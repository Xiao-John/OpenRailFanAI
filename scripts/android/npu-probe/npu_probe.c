/*
 * NPU 可行性探测：**我们这个进程到底能不能碰到 Hexagon DSP？**
 *
 * 为什么要有它：
 *   调研确认 llama.cpp 上游有官方 Hexagon 后端（`ggml-hexagon`），官方 Docker 镜像
 *   公开可拉、不需要 Qualcomm 账号。但整件事卡在一个**无法从任何文档推断**的问题上：
 *   DSP 授权只对"正常打包启动的进程"有效 —— 而我们的推理跑在 Chaquopy 起出来的
 *   Python **子进程**里。这个进程结构认不认，只有实测能回答。
 *
 * 为什么不用 Hexagon SDK 编：
 *   SDK 头（domain.h / remote.h / dspqueue.h）本机没有，装 Docker 是几 GB 的事。
 *   而 llama.cpp 的 `htp-drv.cpp` 本来就是**运行时 dlopen + dlsym** 取这些函数的，
 *   所以照抄它的函数签名（逐条对着 htp-drv.cpp:35-76 抄的），不带 SDK 头也能编能调。
 *
 * --- 第一版实测结果与它暴露的问题（lm34，8 Elite Gen 5）---
 *   · `stat /dev/fastrpc-cdsp` → **EACCES(13)**（不是 ENOENT）⇒ 节点存在，是 SELinux 拒绝
 *   · `dlopen("libcdsprpc.so")` → not found
 *   两个失败各自可能有好几种原因，**光看错误码分不清**，所以第二版把"为什么"打出来：
 *     - 到底是哪个 SELinux 域被拒（决定"换进程结构有没有用"）
 *     - 库到底是不在、还是不可读、还是没注册成 public library
 *       （按**绝对路径** dlopen 能把这三者分开：ENOENT / EACCES / 成功）
 *     - vendor 的 public libraries 清单里到底有没有它
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

typedef int remote_handle64;

/* 签名逐条照抄 ggml/src/ggml-hexagon/htp-drv.cpp（不是凭印象写的） */
typedef int (*remote_handle64_open_t)(const char *name, remote_handle64 *ph);
typedef int (*remote_handle64_close_t)(remote_handle64 h);

#define DEV_CDSP "/dev/fastrpc-cdsp"

/* AEE 错误码按**低字节**判读：AOSP 版 AEEStdErr.h 里 AEE_EOFFSET 是 0，
 * Qualcomm SDK 里是 0x80000400 —— 只有低字节跨版本稳定。
 * llama.cpp 自己也是这么比的（htp-drv.cpp:412 的 `err & 0xff`）。 */
static const char *aee_name(int err) {
    switch (err & 0xff) {
        case 0x00: return "AEE_SUCCESS —— **成功**";
        case 0x06: return "AEE_EUNABLETOLOAD（加载不了，多半是 skel 不存在）";
        case 0x15: return "AEE_EPRIVLEVEL —— **权限不足（被授权层拦下）**";
        case 0x27: return "AEE_ENOSUCH（无此名字/端口）";
        case 0x45: return "AEE_ENOSUCHFILE —— **过了授权层，只是文件不存在**";
        case 0x6c: return "AEE_EUNSUPPORTEDAPI（本设备不支持该 API）";
        default:   return "（未知，按低字节对照 AEEStdErr.h 判读）";
    }
}

/* 想探的路径。分开列是为了把"不存在 / 不可读 / 不可stat"三种情况区分开 ——
 * 它们指向完全不同的原因，而第一版只探了一个节点，分不出来。 */
static const char *PATHS[] = {
    "/dev/fastrpc-cdsp",
    "/dev/fastrpc-cdsp-secure",
    "/dev/fastrpc-sdsp",
    "/dev/adsprpc-smd",
    "/vendor/lib64/libcdsprpc.so",
    "/vendor/lib64/libadsprpc.so",
    "/vendor/etc/public.libraries.txt",
    "/vendor/etc/public.libraries-qti.txt",
};

/* dlopen 的名字/路径。**按绝对路径**试是关键：
 *   ENOENT → 文件根本不在那儿
 *   EACCES → 文件在，但 SELinux 不让我们读 vendor 库
 *   成功   → 文件可读，问题只在"没注册成 public library"（那是可修的） */
static const char *LIBS[] = {
    "libcdsprpc.so",
    "/vendor/lib64/libcdsprpc.so",
    "libadsprpc.so",
    "/vendor/lib64/libadsprpc.so",
};

/* 3 个 URI：一个必然不存在的当**基线**，两个是我们真要用的。
 * 判据是"失败的方式"而不是"成不成" —— 报"文件不存在"说明已经过了授权层。 */
static const char *URIS[][2] = {
    {"基线（必然不存在）",
     "file:///libnpu_probe_definitely_missing.so?htp_iface_skel_handle_invoke&_modver=1.0&_dom=cdsp&_session=0"},
    {"我们要用的 skel 名",
     "file:///libggml-htp-v81.so?htp_iface_skel_handle_invoke&_modver=1.0&_dom=cdsp&_session=0"},
};

/* 把文件的头部几十行打印出来（用于 public.libraries*.txt）。读不到就报原因。 */
static void dump_lines(const char *path, const char *needle) {
    FILE *f = fopen(path, "r");
    if (!f) {
        printf("      读不到（errno=%d %s）\n", errno, strerror(errno));
        return;
    }
    char line[512];
    int shown = 0, total = 0;
    while (fgets(line, sizeof(line), f)) {
        total++;
        if (needle && !strstr(line, needle)) continue;
        if (shown++ < 12) printf("      %s", line);
    }
    if (!needle) printf("      （共 %d 行）\n", total);
    else if (!shown) printf("      **清单里没有含 '%s' 的条目** ← 这就能解释 dlopen 失败\n", needle);
    fclose(f);
}

static int run(void) {
    printf("=== NPU 可行性探测 v2（FastRPC / Hexagon DSP）===\n");
    /* 版本号由 Python 侧通过环境变量带进来：**没有它就无法判断"manifest 里到底有没有
     * 那条声明"** —— 第一版就卡在这个歧义上（lm33 是坏包、lm34 才有声明）。 */
    const char *ver = getenv("RFA_VERSION");
    printf("应用版本    %s\n", ver && *ver ? ver : "（未提供）");
    printf("pid         %d\n", (int) getpid());
    printf("uid/gid     %d/%d\n", (int) getuid(), (int) getgid());

    /* ---- 0. SELinux 域：决定"换个进程结构有没有用" ----
     * 子进程 fork 自 App 进程，域通常不变；打印出来就能确认，
     * 而不是靠"应该是一样的"去推断。 */
    {
        char dom[256] = {0};
        int fd = open("/proc/self/attr/current", O_RDONLY);
        if (fd >= 0) {
            ssize_t n = read(fd, dom, sizeof(dom) - 1);
            close(fd);
            if (n > 0) {
                dom[n] = 0;
                for (ssize_t i = 0; i < n; i++) if (dom[i] == '\n') dom[i] = 0;
                printf("SELinux 域  %s\n", dom);
            }
        } else {
            printf("SELinux 域  （读不到 /proc/self/attr/current，errno=%d）\n", errno);
        }
        char cmd[256] = {0};
        fd = open("/proc/self/cmdline", O_RDONLY);
        if (fd >= 0) {
            ssize_t n = read(fd, cmd, sizeof(cmd) - 1);
            close(fd);
            for (ssize_t i = 0; i < n - 1; i++) if (cmd[i] == '\0') cmd[i] = ' ';
            printf("进程名      %s\n", cmd);
        }
    }

    /* ---- 1. 路径可达性（把"不存在"和"不可读"分开）---- */
    printf("\n--- 1) 路径可达性 ---\n");
    for (unsigned i = 0; i < sizeof(PATHS) / sizeof(PATHS[0]); i++) {
        struct stat st;
        if (stat(PATHS[i], &st) != 0) {
            const char *why = errno == ENOENT ? "不存在"
                            : errno == EACCES ? "**SELinux 拒绝 getattr**"
                            : "其它";
            printf("  %-34s stat 失败 errno=%-2d %-28s (%s)\n",
                   PATHS[i], errno, why, strerror(errno));
        } else {
            printf("  %-34s stat OK  mode=%06o size=%ld\n",
                   PATHS[i], (unsigned) (st.st_mode & 07777), (long) st.st_size);
        }
    }

    /* ---- 2. 驱动库：按名字 vs 按绝对路径 ----
     * 这一节是第二版的核心：把"库不在 / 不可读 / 没注册成 public library"三者分开。 */
    printf("\n--- 2) dlopen ---\n");
    void *h = NULL;
    for (unsigned i = 0; i < sizeof(LIBS) / sizeof(LIBS[0]); i++) {
        dlerror();
        void *p = dlopen(LIBS[i], RTLD_NOW);
        if (p) {
            printf("  %-34s **OK** handle=%p\n", LIBS[i], p);
            if (!h) h = p;
        } else {
            const char *e = dlerror();
            int en = errno;
            printf("  %-34s 失败 errno=%-2d %s\n", LIBS[i], en, e ? e : "(无 dlerror)");
        }
    }

    /* ---- 3. vendor 的 public library 清单 ----
     * `<uses-native-library>` 只对**登记在册的** vendor 库生效。
     * 若清单里根本没有 cdsprpc，那么无论 manifest 怎么写都 dlopen 不到 ——
     * 那是一个**确定性的、可判读的**结论，比"not found"有用得多。 */
    printf("\n--- 3) vendor public libraries 清单 ---\n");
    const char *lists[] = {"/vendor/etc/public.libraries.txt",
                           "/vendor/etc/public.libraries-qti.txt"};
    for (unsigned i = 0; i < sizeof(lists) / sizeof(lists[0]); i++) {
        printf("  %s\n", lists[i]);
        dump_lines(lists[i], "rpc");
    }

    if (!h) {
        printf("\n=== 结论 ===\n");
        printf("拿不到 FastRPC 用户态库。请对照上面 2) 与 3)：\n");
        printf("  · 若按**绝对路径**也报 ENOENT    → 这机器上就没有这个库\n");
        printf("  · 若按绝对路径报 EACCES          → 库在，但 SELinux 不让读 vendor 库\n");
        printf("  · 若按绝对路径 OK、按名字才失败  → 只是没注册成 public library\n");
        printf("  · 第 3 节清单里没有 cdsprpc      → 同上，且这是确定性的\n");
        printf("再对照 0) 的 SELinux 域与 1) 的 /dev 节点结果综合判断。\n");
        return 1;
    }

    /* ---- 4. 后端需要的符号 ---- */
    printf("\n--- 4) dlsym（llama.cpp 的 htp-drv 初始化时要求的那些）---\n");
    static const char *syms[] = {
        "remote_handle64_open", "remote_handle64_close", "remote_handle64_invoke",
        "remote_handle_control", "remote_handle64_control", "remote_session_control",
        "fastrpc_mmap", "fastrpc_munmap", "rpcmem_alloc", "rpcmem_free", "rpcmem_to_fd",
    };
    int missing = 0;
    for (unsigned i = 0; i < sizeof(syms) / sizeof(syms[0]); i++) {
        void *p = dlsym(h, syms[i]);
        printf("  %-24s %s\n", syms[i], p ? "OK" : "**缺**");
        if (!p) missing++;
    }
    printf("  → 缺失 %d / %u\n", missing, (unsigned) (sizeof(syms) / sizeof(syms[0])));

    /* ---- 5. 真考验：去 DSP 上开一个会话 ---- */
    printf("\n--- 5) remote_handle64_open（授权层的真考验）---\n");
    remote_handle64_open_t p_open = (remote_handle64_open_t) dlsym(h, "remote_handle64_open");
    remote_handle64_close_t p_close = (remote_handle64_close_t) dlsym(h, "remote_handle64_close");
    if (!p_open) {
        printf("拿不到 remote_handle64_open，无法继续。\n");
        return 1;
    }
    int verdict_priv = 0, verdict_past = 0;
    for (unsigned i = 0; i < sizeof(URIS) / sizeof(URIS[0]); i++) {
        remote_handle64 hd = -1;
        int err = p_open(URIS[i][1], &hd);
        printf("  [%s]\n    返回 0x%08x (低字节 0x%02x)  %s\n",
               URIS[i][0], (unsigned) err, (unsigned) (err & 0xff), aee_name(err));
        if (!err && hd >= 0) {
            printf("    **会话已建立！handle=%d —— 这一档真的能跑 NPU**\n", (int) hd);
            if (p_close) p_close(hd);
            verdict_past = 1;
        } else if ((err & 0xff) == 0x15) {
            verdict_priv = 1;
        } else if ((err & 0xff) == 0x45 || (err & 0xff) == 0x27 || (err & 0xff) == 0x06) {
            verdict_past = 1;   /* 报"文件问题"而不是"权限问题" ⇒ 授权层已经过了 */
        }
    }

    printf("\n=== 结论 ===\n");
    if (verdict_past && !verdict_priv) {
        printf("**过了授权层**：报的是『文件不存在』这一类错误，而不是『权限不足』。\n"
               "意思是 —— 只要把我们自己编的 libggml-htp-vNN.so 打进包，\n"
               "这个进程结构就有资格在 DSP 上开会话。**NPU 这条路值得往下走。**\n");
    } else if (verdict_priv) {
        printf("**被授权拦下**：出现 AEE_EPRIVLEVEL。当前进程结构拿不到 DSP。\n");
    } else {
        printf("**不确定**：没有任何一档报出可判读的错误码，需人工看原始返回值。\n");
    }
    return 0;
}

int main(void) {
    int rc = run();
    /* **EOF 标记是硬要求，不是装饰**：这段输出会被复制粘贴、导出成文件、再经聊天工具传出来，
     * **任何一环都可能截断** —— 而"被截断的报告"与"探针本来就没跑完"在文本上**看不出区别**，
     * 据此分析就会得出错误结论（真实教训：一份 vivo 诊断缺了 Java 侧那半，看文本却完全正常）。
     * 有这一行，收件方一眼就能确认收全了没有。
     * 放在 main 里包住 run()，是为了让**所有 return 路径**都能打印到。 */
    printf("\n--- EOF · npu_probe 输出结束（exit=%d）---\n", rc);
    return rc;
}
