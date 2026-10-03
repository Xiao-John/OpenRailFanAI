"""设备端本地推理：模型下载 / 生命周期管理 / 接成 LLM 供应商。

为什么这个模块在 `backend/app/` 而不是 Android 的 python 目录：
    下载与启停要由 **HTTP API** 驱动（前端那个"一键下载"按钮），所以它必须能被
    FastAPI 后端 import。放在 Android 侧的话桌面版永远用不了，且 API 层够不着。

为什么是**子进程**而不是进程内 JNI：
  1. 后端 pipeline **零改动** —— llama-server 自带 OpenAI 兼容接口，
     我们只是把供应商指到 127.0.0.1，与桌面接 Ollama 是同一条路；
  2. 崩溃隔离：小模型 + 手机上 OOM 是常态，子进程被杀不会带走整个 App；
  3. 排障可见：llama-server 自己的日志（加载耗时、上下文、KV 大小）能原样拿到。

为什么二进制叫 `libllamaserver.so`：见 `scripts/android/build-llama.sh` 顶部
（Android 10+ 的 W^X：只有 nativeLibraryDir 允许 exec，而只有 lib*.so 会落到那里）。
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request

# 供应商 id：前端按 **id** 选中它（而不是下发 base_url），
# 这样就不会走"请求级供应商"那条被 SSRF 守卫拦下的路（详见 providers.guard_request_base_url）。
PROVIDER_ID = "ondevice"
PROVIDER_LABEL = "设备端本地模型"

BINARY_NAME = "libllamaserver.so"

# NPU 可行性探针（scripts/android/npu-probe/npu_probe.c → build-npu-probe.sh）。
#
# 它回答的是**唯一无法从文档推断**的那个问题：我们的推理跑在 Chaquopy 起出来的
# Python **子进程**里，而 Hexagon DSP 的授权只对"正常打包启动的进程"有效 ——
# 这个进程结构认不认，没有任何文档能回答。
# 判据见 C 源码顶部注释：被 "权限不足" 拦下 vs 只是 "文件不存在"（后者说明已经过了授权层）。
NPU_PROBE_NAME = "libnpuprobe.so"

# 设备端默认参数（依据见 docs/local-model.md §8）：
#   -c 8192 与 LLM_CONTEXT_TOKENS 必须一致，否则超出的上下文会被静默丢弃；
#   -t 4    手机上 2+6 核，用满性能核即可，开更多只会互相抢带宽；
#   -b/-ub  prefill 是本地推理的延迟大头，批大一点更划算。
DEFAULT_CTX = 8192
DEFAULT_THREADS = 4
DEFAULT_BATCH = 2048
DEFAULT_UBATCH = 512

# 卸载到 GPU 的层数。**默认 0 = 纯 CPU**，理由见下。
#
# 原本默认 99（全卸载），直觉是"GPU 一定更快"。**真机实测推翻了它**：
# 8 Elite Gen 5 / 16G、Qwen3.5-2B、同一次请求（580 token 的 prefill）——
#     ngl=0（CPU）  38.91 tok/s
#     ngl=99（GPU）  5.23 tok/s      ← **慢 7.4 倍**
# 而且 GPU 那条路径还会以 SIGSEGV 收场。两条线索合起来就是用户报的
# "lm19 以后比之前卡顿，不符合常理"：不是某一版引入的，是**从有 Vulkan 二进制起
# `-ngl 99` 就一直在生效**。
#
# 结论：**在手机上 GPU 卸载不一定更快**。prefill 是计算密集 + 驱动路径不成熟，
# 完全可能远慢于 CPU。所以默认取保守值 0，想要 GPU 的用户在设置页显式打开
# （那个下拉会落盘，重启后仍生效）。
#
# 注意：这个结论只对**这颗 SoC + 这个驱动 + 这个模型**成立，别的组合要重新测。
DEFAULT_NGL = 0

# 运行时覆盖：用来**隔离变量** —— 崩了就设 0 试纯 CPU，
# 一步就能判断是 Vulkan 后端的锅还是别的原因。不重装、不重新下载模型。
#
# **必须落盘**：第一版只放内存，实测被咬了 —— 用户设成 0、服务也确实用 0 重启了，
# 但 App 随后重启（崩溃后重开 / 被 LMK 回收），新进程里覆盖值没了，
# `setup()` 又按默认的 99 起来。于是"我明明设成 0 了"和"诊断里显示 99"**同时为真**，
# 排查时看起来像用户没操作 —— 这种"设置悄悄失效"最难查。
_TUNE_FILE = ".tune.json"
# 可运行时调、且**会落盘**的推理参数。默认值就是 DEFAULT_*。
_TUNE_KEYS = ("ngl", "threads", "ctx", "extra_args")
_tune_override: dict = {}
_ngl_override: int | None = None

# 当前"选中"的模型文件名（多档共存时用）。
#
# **必须落盘**：否则每次重启 App 又回到"字母序第一个"，"选过"等于没选 ——
# 而多档共存这件事本身就是为调试服务的，调试必然要反复重启 App。
# 只存**文件名**不存绝对路径：模型目录可能变（外部目录被清、改走内部目录）。
_SELECTED_FILE = ".selected_model"


_TUNE_DEFAULTS = {"ngl": None, "threads": None, "ctx": None}   # None = 用 DEFAULT_*


def _tune_load() -> dict:
    """读落盘的调优值（进程重启后仍在）。

    **为什么必须落盘**：第一版只放内存，实测被咬 —— 用户设成 0、服务也确实用 0 重启了，
    但 App 随后重启（崩溃后重开/被 LMK 回收），覆盖值没了又回到默认。
    "设置悄悄失效"会让排查看起来像用户没操作。
    """
    if not _data_dir:
        return {}
    try:
        import json as _json

        with open(os.path.join(_data_dir, _TUNE_FILE), encoding="utf-8") as f:
            d = _json.load(f)
        out = {k: int(d[k]) for k in _TUNE_KEYS if k != "extra_args" and isinstance(d.get(k), int)}
        if isinstance(d.get("extra_args"), str):
            out["extra_args"] = d["extra_args"]
        return out
    except (OSError, ValueError, TypeError):
        return {}


def _tune_save(d: dict) -> None:
    if not _data_dir:
        return
    try:
        import json as _json

        with open(os.path.join(_data_dir, _TUNE_FILE), "w", encoding="utf-8") as f:
            _json.dump(d, f)
    except OSError as e:
        _logger().warning("调优参数写入失败（重启后会回到默认）：%s", e)


def current_tune() -> dict:
    """当前生效的推理参数（内存覆盖 > 落盘 > 环境变量 > 默认）。"""
    on_disk = _tune_load()
    env_map = {"ngl": "LOCAL_MODEL_NGL", "threads": "LOCAL_MODEL_THREADS",
               "ctx": "LOCAL_MODEL_CTX"}
    defaults = {"ngl": DEFAULT_NGL, "threads": DEFAULT_THREADS, "ctx": DEFAULT_CTX}
    out: dict = {}
    for k in _TUNE_KEYS:
        if k == "extra_args":
            # **原始参数**：原样透传，不做 int 转换（见 set_tune 的说明）
            out[k] = str(_tune_override.get(k, on_disk.get(k, "")) or "")
            continue
        v = _tune_override.get(k, on_disk.get(k))
        if v is None:
            try:
                v = int(os.environ.get(env_map[k], str(defaults[k])))
            except (KeyError, ValueError):
                v = defaults[k]
        out[k] = v
    return out


def current_ngl() -> int:
    return int(current_tune()["ngl"])


def _ngl_from_disk() -> int | None:
    return _tune_load().get("ngl")

# 单次 socket 读的超时。停顿时长超过它就算本次失败，交给外层带 Range 重试 ——
# 所以不必设得很大，设大了只是让用户更晚看到"卡住了"。
READ_TIMEOUT_S = 60.0

# 起服务到能用的上限。权重在手机上冷加载通常 3–10s（4B 更久），给足余量但仍要有界。
HEALTH_TIMEOUT_S = 300.0

# ---- 可下载的模型目录 ----
# 用 ModelScope 而不是 HuggingFace：国内直连实测 ~8 MB/s，HF 往往要走代理。
# size_mb 是**实测文件大小**，用来给用户"要下多久/占多少空间"的预期，不是估算。
MODEL_CHOICES: list[dict] = [
    # ⚠️ **不提供 0.8B 档位**（2026-09-21 实测后撤下）。
    #
    # 同一条真实生成提示词（2073 字）下的四方对照：
    #     0.8B   输出 1200 token（撞上限不会停）· 重复率 97% · 同一片段重复 68 次 · **编造站名**
    #     2B     205 token · 重复 0% · 内容正确
    #     4B     234 token · 重复 0% · 内容正确
    #     云端   119 token · 重复 0% · 内容正确
    # 0.8B 把杭州/宁波/绍兴/湖州/嘉兴当成京沪线上的站编了出来（检索事实里只有 6 站），
    # 然后陷进自己编的序列里出不来。
    #
    # 这不是"判断力弱一点"，是**会编造数据且不会自己停** —— 用户看不出哪句是真的，
    # 属于最坏的一类失败。**设备端的可用性下限是 2B。**
    {
        "id": "qwen3.5-2b-q4km",
        "label": "Qwen3.5-2B（约 1.2 GB）",
        "note": "8G 内存的推荐档；速度与质量折中（**设备端的可用性下限**）",
        "size_mb": 1219,
        "gguf_name": "Qwen3.5-2B-Q4_K_M.gguf",
        "urls": [
            "https://modelscope.cn/api/v1/models/unsloth/Qwen3.5-2B-GGUF/repo"
            "?Revision=master&FilePath=Qwen3.5-2B-Q4_K_M.gguf",
        ],
    },
    {
        "id": "qwen3.5-4b-q4km",
        "label": "Qwen3.5-4B（约 2.6 GB）",
        "note": "16G 旗舰推荐档；意图准确率追平云端，但更慢更费电",
        "size_mb": 2612,
        "gguf_name": "Qwen3.5-4B-Q4_K_M.gguf",
        "urls": [
            "https://modelscope.cn/api/v1/models/unsloth/Qwen3.5-4B-GGUF/repo"
            "?Revision=master&FilePath=Qwen3.5-4B-Q4_K_M.gguf",
        ],
    },
]

_lock = threading.Lock()
_status: dict = {"state": "idle", "detail": "未启用", "base_url": None, "model": None}
_proc: subprocess.Popen | None = None
# 子进程拉起时刻（epoch 秒）。用来回答"它在后台已经跑了多久" —— 手机上
# 本地推理常驻是**持续发热 + 掉电**的，用户有权知道自己已经开着它多久了。
_started_at: float = 0.0
_models_dir: str = ""
_data_dir: str = ""
_native_lib_dir: str = ""
# 兄弟包（其它 RailFanAI 安装）的模型目录，**只读**。见 readable_dirs()。
_sibling_dirs: list[str] = []
_log = None
_log_path: str = ""

# 下载进度（供前端轮询）
_download: dict = {
    "state": "idle",     # idle | downloading | done | failed | cancelled
    "choice": None,
    "received": 0,
    "total": 0,
    "detail": "",
}
_cancel = threading.Event()


def _logger():
    if _log is not None:
        return _log
    import logging

    return logging.getLogger("railfan.local_inference")


# ---------------------------------------------------------------- 路径与探测

def _binary() -> str | None:
    cands = []
    if os.environ.get("LOCAL_LLAMA_BINARY"):
        cands.append(os.environ["LOCAL_LLAMA_BINARY"])
    if _native_lib_dir:
        cands.append(os.path.join(_native_lib_dir, BINARY_NAME))
    # 桌面兜底：PATH 上的 llama-server（brew 装的那个）
    for name in ("llama-server",):
        p = shutil.which(name)
        if p:
            cands.append(p)
    for p in cands:
        if p and os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def candidate_dirs() -> list[str]:
    """模型可能所在的全部目录，**顺序即优先级**。"""

    def _default_cache() -> str:
        return os.path.expanduser("~/.cache/openrailfanai/models")

    out: list[str] = []
    env_dir = os.environ.get("LOCAL_MODEL_DIR")
    if env_dir:
        out.append(env_dir)
    #   1) 外部应用目录（Java 侧传进来的 models_dir）—— 直观、可被文件管理器看到；
    #   2) **内部** filesDir/models —— 兜底，理由见 `writable_dir()`。
    if _models_dir:
        out.append(_models_dir)
    if _data_dir:
        out.append(os.path.join(_data_dir, "models"))
    if not out:
        out.append(_default_cache())
    return out


def readable_dirs() -> list[str]:
    """**可读**的模型目录 = 可写的那些 + 兄弟包的（只读）。

    为什么把这两件事分开：内部调试轨的包名带 `.internal`，与封测轨属于**两个应用**，
    看不到对方 `Android/data/<包名>/files/models` 里已下好的模型
    （Android 11+ 禁止跨包读 Android/data）。把兄弟目录加进来，调试版就能**原地用上**
    封测版下的 2B/4B —— 不拷贝、不重下几 GB。这是"内部版用独立包名"能成立的前提。

    但它们**绝不能进 `writable_dir()`**：拿到「所有文件访问权限」时确实写得进去，
    那会把下载落进别人的目录，把两个应用的数据搅在一起。
    """
    return candidate_dirs() + [d for d in _sibling_dirs if d and os.path.isdir(d)]


_writable_cache: str = ""

def writable_dir() -> str:
    """返回**真的能写**的模型目录（下载目标）。逐候选做写测试，取第一个成功的。

    为什么不能直接假定"外部应用目录一定可写"：实测（Android 15 emulator）里，
    只要那个目录曾被 `adb shell mkdir` 创建过，属主就是 `shell`，
    App 侧一律 `Permission denied`（emulated storage 的合成权限，不是 SELinux）。
    现象是**下载跑到写文件那一步才失败**，而报错里只有一个路径 —— 极难联想到
    "目录是被谁建的"。同一个坑在一个会话里咬了两次（先是找不到模型、后是下载失败），
    所以这里做成**探测**而不是**假定**。
    """
    global _writable_cache
    if _writable_cache and os.path.isdir(_writable_cache):
        return _writable_cache
    errs: list[str] = []
    for d in candidate_dirs():
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, ".write-probe")
            with open(probe, "w") as f:
                f.write("ok")
            os.remove(probe)
            _writable_cache = d
            return d
        except OSError as e:
            errs.append(f"{d}（{e.strerror or e}）")
    raise OSError("没有可写的模型目录：" + "；".join(errs))


def models_dir() -> str:
    """对外展示/算剩余空间用的模型目录（与 `writable_dir()` 一致）。"""
    try:
        return writable_dir()
    except OSError:
        return candidate_dirs()[0]


def _safe_model_name(name: str) -> str:
    """把外部传进来的模型名收成**纯文件名**。

    **这是一条安全边界**：`select_model` / `delete_model` 会拿它去拼路径，
    放任 `../` 或绝对路径进来就是一个任意文件删除漏洞。
    只接受 basename，且必须以 `.gguf` 结尾。
    """
    base = os.path.basename(str(name or "").strip())
    if not base or base != str(name or "").strip():
        raise ValueError(f"非法的模型名：{name!r}")
    if not base.lower().endswith(".gguf"):
        raise ValueError(f"不是模型文件：{base}")
    return base


def _selected_path() -> str:
    return os.path.join(_data_dir, _SELECTED_FILE) if _data_dir else ""


def selected_model() -> str | None:
    """落盘的"当前选中"文件名；没选过或文件已不在 → None。"""
    p = _selected_path()
    if not p or not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            name = f.read().strip()
    except OSError:
        return None
    if not name:
        return None
    for d in readable_dirs():
        if d and os.path.isfile(os.path.join(d, name)):
            return name
    return None                      # 选中的文件被删了 → 视为没选，自动回退到旧的字母序逻辑


def _select_save(name: str) -> None:
    p = _selected_path()
    if not p:
        return
    try:
        with open(p, "w", encoding="utf-8") as f:
            f.write(name)
    except OSError as e:
        _logger().warning("选中模型写入失败（重启后会回到字母序第一个）：%s", e)


def find_model() -> str | None:
    """当前生效的模型文件。

    优先级（**顺序就是契约**）：
      1. `LOCAL_MODEL_PATH` 环境变量 —— 桌面冒烟用，永远最高；
      2. 用户选中的那个（`select_model` 落的盘）；
      3. 都没选过 → 退回"字母序第一个"。

    第 3 条是**向后兼容**的关键：不选的时候行为与加入多档之前**完全一致**，
    不会因为这次改动换掉任何人正在用的模型。
    """
    if os.environ.get("LOCAL_MODEL_PATH"):
        p = os.environ["LOCAL_MODEL_PATH"]
        return p if os.path.isfile(p) else None
    sel = selected_model()
    if sel:
        for d in readable_dirs():
            if d and os.path.isfile(os.path.join(d, sel)):
                return os.path.join(d, sel)
    for d in readable_dirs():
        if d and os.path.isdir(d):
            files = sorted(glob.glob(os.path.join(d, "*.gguf")))
            if files:
                return files[0]
    return None


# ---------------------------------------------------------------- 子进程生命周期

def _wait_healthy(port: int, proc: subprocess.Popen, log) -> bool:
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.monotonic() + HEALTH_TIMEOUT_S
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            log.error("llama-server 已退出（code=%s），详见 %s", proc.returncode, _log_path)
            return False
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass                      # 还没起来，继续等
        time.sleep(0.5)
    log.error("llama-server 在 %.0fs 内没就绪", HEALTH_TIMEOUT_S)
    return False


def _clean_extra_args(raw: str) -> str:
    """清洗用户填的**原始 llama-server 参数**。

    这是内部调试轨要的"自由"：不必等我们为每个 llama.cpp 开关加一个下拉，
    直接写 `-fa on -ctk q8_0 -ctv q8_0 --device HTP0` 就能试。

    为什么还要"清洗"而不是原样透传：
      · **只做分词，不做执行** —— 走 `shlex.split` 后交给 `subprocess` 的参数列表，
        **绝不经过 shell**，所以 `;`、`&&`、反引号这些没有执行语义（不是靠过滤挡住的）；
      · 拒绝含换行的输入：多行只会让日志里那条"启动参数"变得没法读，
        而且真实用途也不需要它；
      · 长度设上限：这是给排障用的输入框，不是配置文件。
    其余一律放行 —— **加了白名单就等于把"自由"又收回去了**，与这个功能的初衷相悖。
    """
    text = (raw or "").replace("\n", " ").strip()
    if "\n" in text or "\r" in text:
        text = text.replace("\r", " ")
    if len(text) > 2000:
        raise ValueError("原始参数过长（上限 2000 字符）")
    return text


def extra_args_list() -> list[str]:
    """把 `extra_args` 分词成参数列表（**不经过 shell**）。"""
    import shlex

    raw = str(current_tune().get("extra_args") or "").strip()
    if not raw:
        return []
    try:
        return shlex.split(raw)
    except ValueError as e:
        # 引号没配对之类：**不能静默丢弃** —— 那会让用户以为参数生效了。
        _logger().warning("原始参数解析失败，本次忽略：%s（%s）", raw, e)
        return []


def set_tune(**values: int) -> dict:
    """改推理参数并**重启**服务；改过的值会落盘，进程重启后仍生效。

    可调项：
      `ngl`     —— GPU 卸载层数（0 = 纯 CPU）
      `threads` —— CPU 线程数。**这一项最值得试**：设备是 2 大核 + 6 性能核共 8 核，
                   而我们一直写死 4 —— 只用了**一半**。decode 实测有效带宽仅 ~18 GB/s
                   （机器约 68–77 GB/s），说明既没打满带宽也没打满算力，线程数嫌疑最大。
      `ctx`     —— 上下文长度（越小 KV 越省，也越少内存压力）
    """
    global _ngl_override
    cur = _tune_load()
    for k, v in values.items():
        if k not in _TUNE_KEYS or v is None:
            continue
        if k == "extra_args":
            cur[k] = _clean_extra_args(str(v))
            continue
        cur[k] = max(0, min(int(v), 999 if k == "ngl" else 65536))
    _tune_override.clear()
    _tune_override.update(cur)
    _ngl_override = cur.get("ngl")
    _tune_save(cur)
    _logger().info("推理参数改为 %s，重启本地推理", cur)
    stop()
    return start(wait=True)


def set_ngl(value: int) -> dict:
    """兼容入口：只改 GPU 卸载层数。"""
    return set_tune(ngl=value)


def select_model(name: str) -> dict:
    """切换到指定的已下载模型：落盘选中 → 重启服务 → 返回新状态。

    **同步等待**（`start(wait=True)`）：设备端加载 1–3.5s，前端那一下能拿到结论，
    否则用户点了"使用"却看到状态还是旧模型，只能靠轮询猜。

    ⚠️ 切换会 **`stop()` 掉正在跑的那个进程** —— 如果此刻有一次生成在飞，
    那次会遇到连接中断。这是用户主动切换的代价，可接受，但不要让它表现成
    "答到一半莫名其妙断了"（前端在切换期间显示"正在切换…"）。
    """
    want = _safe_model_name(name)
    found = next((m for m in list_models() if m["name"] == want), None)
    if found is None:
        # **必须校验必须来自 list_models()**：否则就是一个任意文件读取/删除入口。
        raise ValueError(f"模型不在已下载列表里：{want}")
    _select_save(want)
    _logger().info("切换到本地模型 %s（%d MB）", want, found["size_mb"])
    stop()
    return start(wait=True)


def start(*, wait: bool = False) -> dict:
    """拉起 llama-server。已在运行则直接返回当前状态。

    `wait=True` 时同步等健康检查（下载完成后的"一键启用"用这个，让前端能立刻看到结论）；
    否则起后台线程等待（App 启动时用这个，别让用户对着启动页干等）。
    """
    global _proc, _started_at
    log = _logger()
    with _lock:
        if _proc is not None and _proc.poll() is None:
            return dict(_status)

    binary = _binary()
    if not binary:
        _status.update(state="unavailable", detail=f"没找到推理二进制（{BINARY_NAME}）")
        return dict(_status)
    model = find_model()
    if not model:
        _status.update(state="unavailable", detail="没找到模型文件（先在下面下载一个）")
        return dict(_status)

    port = int(os.environ.get("LOCAL_MODEL_PORT", "8081"))
    tune = current_tune()
    ctx, threads = tune["ctx"], tune["threads"]
    ngl = current_ngl()
    cmd = [
        binary,
        "-m", model,
        "-c", str(ctx),
        "-t", str(threads),
        "-b", str(DEFAULT_BATCH),
        "-ub", str(DEFAULT_UBATCH),
        "-ngl", str(ngl),           # GPU 卸载层数：设备端最大的一根提速杠杆，见 DEFAULT_NGL
        # ⚠️ 这个开关对本模型**实际是空操作**，留着只是为了将来换模型时能用。
        # 实测日志：`cache_reuse is not supported by this context, it will be disabled`
        # 原因在源码里（server-context.cpp）：它依赖 memory shift，而
        # `llama_memory_can_shift()` 对 Qwen3.5 这类**混合注意力**（线性+全注意力）返回 false
        # —— 线性注意力的循环状态没有"移位"的概念。
        #
        # **别把它当成"前缀复用"**：跨请求复用公共前缀是另一套机制，不需要 cache_reuse，
        # 而且确实在工作（早先实测 cold prefill 2570 token/1583ms → 后续 11 token/99ms）。
        # 我一度把两者混为一谈，这里写清楚免得后人再错。
        "--cache-reuse", "256",
        "--jinja",
        "--no-webui",
        "--host", "127.0.0.1",
        "--port", str(port),
    ]
    # 原始参数**追加在最后**：llama.cpp 的 CLI 是后者覆盖前者，放最后才"说了算"，
    # 否则用户填的 `-t 8` 会被前面的 `-t 4` 盖掉 —— 那是很隐蔽的"设了没生效"。
    extra = extra_args_list()
    if extra:
        cmd += extra
        log.info("附加了 %d 个原始参数：%s", len(extra), " ".join(extra))
    log.info("启动本地推理：%s", " ".join(cmd))

    logf = None
    try:
        if _data_dir or _models_dir:
            global _log_path
            _log_path = os.path.join(_data_dir or _models_dir, "llama-server.log")
            # **每次启动先写一条带参数的分隔标记。**
            # 为什么必须：这份日志是**跨次累积**的，而日志本身**不记录启动参数** ——
            # 于是"哪一次跑得快、哪一次跑得慢"根本无法与配置对应（实测为此卡了一轮：
            # 同一份日志里有 38.9 tok/s 和 5.23 tok/s 两条 prefill，却看不出各是什么设置）。
            with open(_log_path, "a", encoding="utf-8") as _mf:
                _mf.write(f"\n=========== 启动 {time.strftime('%Y-%m-%d %H:%M:%S')} "
                          f"ngl={ngl} ctx={ctx} t={threads} ===========\n")
            logf = open(_log_path, "ab", buffering=0)
        _proc = subprocess.Popen(cmd, stdout=logf or subprocess.DEVNULL,
                                 stderr=logf or subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        _started_at = time.time()      # 供"后台常驻了多久"的发热提醒用，见 status().uptime_s
    except OSError as e:
        _status.update(state="failed", detail=f"拉起失败：{e}")
        log.error("本地推理拉起失败：%s", e, exc_info=True)
        return dict(_status)

    _status.update(state="starting", detail="等待模型加载…",
                   base_url=f"http://127.0.0.1:{port}/v1",
                   model=os.path.basename(model))
    _apply_env()

    def _finish() -> None:
        t0 = time.monotonic()
        ok = _wait_healthy(port, _proc, log)
        if ok:
            _status.update(state="ready", detail=f"就绪（{time.monotonic() - t0:.1f}s）")
            log.info("本地推理就绪：%s", _status["detail"])
        else:
            _status.update(state="failed", detail="健康检查超时或进程退出")
        if logf:
            try:
                logf.close()
            except Exception:  # noqa: BLE001
                pass

    if wait:
        _finish()
    else:
        threading.Thread(target=_finish, name="llama-health", daemon=True).start()
    return dict(_status)


def stop() -> None:
    """停止子进程（进程退出 / 用户手动停用）。"""
    global _proc, _started_at
    if _proc and _proc.poll() is None:
        try:
            _proc.terminate()
            _proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            try:
                _proc.kill()
            except Exception:  # noqa: BLE001
                pass
    _proc = None
    _started_at = 0.0
    _status.update(state="idle", detail="已停用")


# ---------------------------------------------------------------- 供应商接线

def _apply_env() -> None:
    """把 LLM 相关环境变量设好。

    只在**进程早期**（settings 还没被读）有效 —— `get_settings()` 带 `@lru_cache`，
    之后再改环境变量不会生效。运行期启用走 `provider_item()` + 前端按 id 选中。
    """
    os.environ.setdefault("LLM_PROVIDER", PROVIDER_ID)
    os.environ.setdefault("LLM_CONTEXT_TOKENS", str(int(os.environ.get("LOCAL_MODEL_CTX", DEFAULT_CTX))))
    os.environ.setdefault("LLM_STRUCTURED_JSON_SCHEMA", "true")
    os.environ.setdefault("LLM_STRUCTURED_COMPACT_PROMPT", "true")
    os.environ.setdefault("LLM_STRUCTURED_NO_THINK", "true")
    # **生成路径也必须关思考**（与结构化那条是两件事）。实测不关的话，
    # 思考会把输出预算吃光 —— 正文一个字都没有，整条生成链路不可用。
    os.environ.setdefault("LLM_GENERATION_NO_THINK", "true")
    # 生成提示词也用精简版：设备端模型尺度小，长提示词会过载（实测 0.8B 因此退化成复读），
    # 且 prefill 是设备端唯一的延迟瓶颈 —— 这一条同时治质量与延迟。
    os.environ.setdefault("LLM_GENERATION_COMPACT_PROMPT", "true")
    os.environ.setdefault("FACT_MAX_ENTRIES", "8")


def provider_item() -> tuple[str, dict] | None:
    """本地推理就绪时返回 (id, 配置)，否则 None。

    由 `providers.load_providers` 合并进注册表。**用 id 而不是 base_url**：
    provider 来自配置时 `source != "request"`，`guard_request_base_url` 直接早返回，
    因此**不需要**打开 `LLM_ALLOW_PRIVATE_BASE_URL` 这个全局口子。
    """
    st = dict(_status)
    if st.get("state") not in ("ready", "starting"):
        return None
    base_url = st.get("base_url")
    if not base_url:
        return None
    return PROVIDER_ID, {
        "label": PROVIDER_LABEL,
        "base_url": base_url,
        "api_key": "local",              # 本地服务不校验，但要占位串满足 SDK
        "model": st.get("model") or "local-model",
        # 关思考的字段名按供应商下发：llama-server 认这个（见 config.llm_generation_no_think）
        "no_think_body": {"reasoning_effort": "none"},
        # **超时必须放宽**：设备端慢在 prefill 上。实测 6096 token 提示词
        # prefill 就要 156s，全局默认 60s 会让本地生成 100% 超时，
        # 且降级成规则排版 —— 用户看到的是"模型不可用"，根本猜不到是超时。
        "timeout_s": float(os.environ.get("LOCAL_MODEL_TIMEOUT_S", "600")),
        # 全局开关在进程启动时就冻结了，而本供应商是**运行期**才装上的 ——
        # 所以"永远关思考"必须跟供应商走。否则"下载完自动启用"这条路会带着思考跑，
        # 实测正文直接是空的。见 providers.Provider.always_no_think。
        "always_no_think": True,
    }


# ---------------------------------------------------------------- 下载

def download_state() -> dict:
    return dict(_download)


def _emit(**kw) -> None:
    _download.update(kw)


def _download_from(url: str, part: str, choice: dict) -> None:
    """从单个源拉取，**从 part 已有的字节继续**（HTTP Range）。

    为什么必须支持续传：模型 0.5–2.6 GB，走移动网络必然遇到停顿/切换网络/瞬时超时。
    一次性下载的写法（第一版）实测**下到 342/507 MB 超时整包作废**，
    用户白等十分钟然后从零重来 —— 这不是"边角情况"，是常态。
    """
    have = os.path.getsize(part) if os.path.exists(part) else 0
    headers = {"User-Agent": "OpenRailFanAI/local-model"}
    if have:
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=READ_TIMEOUT_S) as r:
        # 不是所有源都支持 Range：返回 200 表示"从头给"，那就必须把已下的丢掉重来，
        # 否则拼出来的文件中间会重复一段 —— 那种损坏是**静默**的（模型能加载但乱答）。
        if have and r.status != 206:
            _logger().info("该源不支持断点续传（返回 %s），从头重新下载", r.status)
            have = 0
            try:
                os.remove(part)
            except OSError:
                pass
        length = int(r.headers.get("Content-Length") or 0)
        expected = choice["size_mb"] * 1024 * 1024
        total = (length + have) if length else expected
        _emit(total=total, received=have, detail=f"来自 {url.split('/')[2]}")
        got = have
        with open(part, "ab" if have else "wb") as f:
            while True:
                if _cancel.is_set():
                    raise _Cancelled()
                chunk = r.read(262144)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                _emit(received=got)
    _validate_downloaded(part, choice["size_mb"])


def _validate_downloaded(part: str, expected_mb: int) -> None:
    """落盘后校验大小与 GGUF 魔数。

    为什么两道都要：
      · **大小**：提前关闭/被截断的连接不报错、只少给字节；
      · **魔数**：大小对不代表内容对 —— 续传拼接错位、中间被代理插了错误页，
        都会让开头不是 `GGUF`。这种文件 llama-server 只会说"加载失败"，
        指不到"是下载坏了"，用户会去怀疑模型、量化、内存，全错方向。
    """
    size = os.path.getsize(part) if os.path.exists(part) else 0
    if size < expected_mb * 1024 * 1024 * 0.98:
        raise RuntimeError(f"下载不完整：{size / 1048576:.0f} MB < 预期 {expected_mb} MB")
    with open(part, "rb") as f:
        magic = f.read(4)
    if magic != b"GGUF":
        raise RuntimeError(f"下载的文件不是 GGUF（开头是 {magic!r}）")


def _download_one(choice: dict, dest: str) -> None:
    """下载到 dest.part，再原子改名 —— 断在半路不会留下一个"看起来能用"的坏文件。

    每个源失败都**保留 .part** 并重试：续传让"重试"的代价从"整包重来"降到"接着下"。
    """
    part = dest + ".part"
    last_err = ""
    attempts = 3
    for url in choice["urls"]:
        for attempt in range(attempts):
            if _cancel.is_set():
                raise _Cancelled()
            try:
                _download_from(url, part, choice)
                os.replace(part, dest)
                return
            except _Cancelled:
                raise
            except Exception as e:  # noqa: BLE001
                last_err = f"{type(e).__name__}: {e}"
                _logger().warning("下载中断（%s 第 %d/%d 次）：%s", url.split("/")[2],
                                  attempt + 1, attempts, last_err)
                if attempt < attempts - 1:
                    time.sleep(2 * (attempt + 1))     # 退避，别把对端当 DDoS
    raise RuntimeError(f"下载失败（已重试 {attempts} 次）：{last_err}")


class _Cancelled(Exception):
    pass


def start_download(choice_id: str) -> dict:
    """**一键下载**：后台线程拉模型，进度写进 `_download` 供前端轮询；完成后自动拉起服务。"""
    choice = next((c for c in MODEL_CHOICES if c["id"] == choice_id), None)
    if choice is None:
        raise ValueError(f"未知的模型：{choice_id}")
    if _download["state"] == "downloading":
        raise ValueError("已有下载在进行中")

    # 目标目录**先探测再下**：不假定"外部应用目录一定可写"（见 writable_dir 注释）。
    # 探测放在这里而不是等写文件时报错，是因为下载跑到一半才失败，用户已经白等了。
    try:
        target_dir = writable_dir()
    except OSError as e:
        raise ValueError(f"没有可写的模型目录：{e}") from e
    dest = os.path.join(target_dir, choice["gguf_name"])

    # 空间不够就别开始 —— 下到一半失败比一开始就说清楚糟得多
    free = shutil.disk_usage(target_dir).free
    need = choice["size_mb"] * 1024 * 1024 * 1.15
    if free < need:
        raise ValueError(
            f"剩余空间不足：需要约 {need / 1073741824:.1f} GB，可用 {free / 1073741824:.1f} GB"
        )

    _cancel.clear()
    _emit(state="downloading", choice=choice_id, received=0,
          total=choice["size_mb"] * 1024 * 1024, detail="")

    def _run() -> None:
        try:
            _download_one(choice, dest)
            _emit(state="done", detail=f"已保存 {os.path.basename(dest)}")
            _logger().info("模型下载完成：%s", dest)
            start(wait=True)          # 下完自动启用 —— 这才叫"一键"
        except _Cancelled:
            _emit(state="cancelled", detail="已取消")
            try:
                os.path.exists(dest + ".part") and os.remove(dest + ".part")
            except OSError:
                pass
        except Exception as e:  # noqa: BLE001
            _emit(state="failed", detail=f"{type(e).__name__}: {e}")
            _logger().error("模型下载失败：%s", e, exc_info=True)

    threading.Thread(target=_run, name="model-download", daemon=True).start()
    return download_state()


def cancel_download() -> dict:
    _cancel.set()
    return download_state()


def delete_model(name: str | None = None, *, all_models: bool = False) -> dict:
    """删除已下载的模型。

    两种模式，**必须显式区分**：
      · `name="xxx.gguf"` —— 只删这一个。若正被使用，先 `stop()`；
        删完若还有别的模型，**自动切到剩下体积最大的那个并重启**（否则会把用户
        留在一个"没有模型、服务也停了"的状态，而他还以为只是清了一档）。
      · `all_models=True` —— 全删（回收空间用），同时停服务。
        不给名字也不给 `all_models` 一律**拒绝**：这是为了防止调用方"想删一个"
        却因为漏传参数把用户几个 GB 的模型全清了。

    只动模型目录下的 `*.gguf` / `*.gguf.part`，且名字经 `_safe_model_name` 收成纯文件名
    —— 不接受任意路径。
    """
    if name is None and not all_models:
        raise ValueError("必须指定要删除的模型名，或显式传 all_models=True 全删")

    if name is not None:
        want = _safe_model_name(name)
        known = {m["name"] for m in list_models()}
        if want not in known and not (want + ".part"):
            # 允许删 .part 残留（下载中断留下的大文件，用户看不见但要占几百 MB）
            in_parts = any(
                os.path.isfile(os.path.join(d, want + ".part"))
                for d in candidate_dirs() if d
            )
            if not in_parts:
                raise ValueError(f"模型不在已下载列表里：{want}")
        was_active = (os.path.basename(find_model() or "") == want)
        if was_active:
            stop()
        removed = _remove_files([want, want + ".part"])
        _logger().info("删除本地模型 %s（%s），共 %d 个文件", want,
                       "原为当前使用" if was_active else "非当前使用", len(removed))
        if was_active:
            # 清掉选中记录，让 find_model() 回退到剩下最大的那个
            try:
                p = _selected_path()
                if p and os.path.isfile(p):
                    os.remove(p)
            except OSError:
                pass
            rest = list_models()
            if rest:
                _select_save(rest[0]["name"])
                start(wait=False)      # 后台重启，别让删一个模型变成一次卡界面
            else:
                _status.update(state="unavailable", detail="模型已删除")
        return {"removed": removed, **status()}

    stop()
    removed = []
    for d in {models_dir(), os.path.join(_data_dir, "models") if _data_dir else ""}:
        if not d or not os.path.isdir(d):
            continue
        for p in glob.glob(os.path.join(d, "*.gguf")) + glob.glob(os.path.join(d, "*.gguf.part")):
            try:
                os.remove(p)
                removed.append(p)
            except OSError as e:
                _logger().warning("删除 %s 失败：%s", p, e)
    try:
        p = _selected_path()
        if p and os.path.isfile(p):
            os.remove(p)
    except OSError:
        pass
    _status.update(state="unavailable", detail="模型已删除")
    return {"removed": removed, **status()}


def _remove_files(names: list[str]) -> list[str]:
    """在各候选目录里按文件名删文件，返回真正删掉的路径。"""
    removed: list[str] = []
    for d in candidate_dirs():
        if not d or not os.path.isdir(d):
            continue
        for n in names:
            p = os.path.join(d, n)
            if os.path.isfile(p):
                try:
                    os.remove(p)
                    removed.append(p)
                except OSError as e:
                    _logger().warning("删除 %s 失败：%s", p, e)
    return removed


# ---------------------------------------------------------------- 状态 / 启动钩子

# 已知不可用/危险的档位：撤档之后，**已经下载过**的人仍然会用上它，
# 所以要在界面上直说，而不是只在可选列表里消失。
_KNOWN_BAD = [
    ("0.8B", "该档位实测会编造数据并陷入无限重复（同一段重复 68 次），已停止提供。"
              "请删除后改用 2B 或 4B。"),
]


def _model_warning(name: str) -> str:
    for tag, msg in _KNOWN_BAD:
        if tag.lower() in (name or "").lower():
            return msg
    return ""


def _mem_report() -> list[str]:
    """内存占用快照：系统 + 推理进程。

    为什么要有：用户报过"占用异常高"，但**当时我们没有任何量化手段**——
    只能靠感觉。这里直接给出可对比的数字。
    Android 上 /proc 是可读的，不需要 root。
    """
    out: list[str] = []
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            info = {}
            for ln in f:
                k, _, v = ln.partition(":")
                info[k.strip()] = v.strip()
        for k in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree"):
            if k in info:
                out.append(f"  {k:14s} {info[k]}")
    except OSError as e:
        out.append(f"  （读 /proc/meminfo 失败：{e}）")

    def _rss(pid: int, tag: str) -> None:
        try:
            with open(f"/proc/{pid}/status", encoding="utf-8") as f:
                for ln in f:
                    if ln.startswith(("VmRSS", "VmHWM")):
                        out.append(f"  {tag} {ln.strip()}")
        except OSError:
            out.append(f"  {tag} （读不到 /proc/{pid}/status）")

    _rss(os.getpid(), "本进程")
    if _proc is not None and _proc.poll() is None:
        _rss(_proc.pid, "llama-server")
    else:
        out.append("  llama-server （未在运行）")
    return out


def _app_version() -> str:
    """从解包出来的 webapp/build.json 读版本（Android 侧由 MainActivity 解包到 data_dir）。"""
    import json as _json

    for base in (_data_dir, _models_dir):
        if not base:
            continue
        bj = os.path.join(base, "webapp", "build.json")
        if os.path.isfile(bj):
            try:
                with open(bj, encoding="utf-8") as f:
                    d = _json.load(f)
                return f"{d.get('version')} · {d.get('commit')} · {d.get('builtAt')}"
            except Exception:  # noqa: BLE001
                return "（build.json 解析失败）"
    return "未知（找不到 webapp/build.json）"


def probe_binary() -> str | None:
    """NPU 探针可执行文件（与 llama-server 同一套 W^X 逻辑，放在 nativeLibraryDir）。"""
    cands = []
    if os.environ.get("LOCAL_NPU_PROBE"):
        cands.append(os.environ["LOCAL_NPU_PROBE"])
    if _native_lib_dir:
        cands.append(os.path.join(_native_lib_dir, NPU_PROBE_NAME))
    for p in cands:
        if p and os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def probe_npu(timeout: float = 25.0) -> str:
    """跑一次 NPU 可行性探针，返回它的原始输出。

    **为什么要在真机上跑而不是"查资料"**：DSP 授权只对"正常打包启动的进程"有效，
    而我们的推理是 Chaquopy 从 Python 里 `subprocess` 起出来的**子进程** ——
    这个进程结构认不认，官方文档、issue、PR 里都没有答案。
    探针就是把这个问号变成一行可读结论。

    怎么读结果（判据写死在 C 源码注释里，这里重复一遍免得两边漂移）：
      · 报 `AEE_EPRIVLEVEL`(0x15)       → **被授权拦下**，这条路当前走不通
      · 报 `AEE_ENOSUCHFILE`(0x45) 等   → **已经过了授权层**，只是 skel 没打包进来
                                          ⇒ 只要把我们编的 libggml-htp-vNN.so 打进去就能用
    返回原始文本而不是解析后的布尔值：**探针的原始输出本身就是证据**，
    导出诊断时要能原样看到它，不要经过我们这层的二次解释。
    """
    binary = probe_binary()
    if not binary:
        return f"（本包不含 NPU 探针 {NPU_PROBE_NAME}；先跑 scripts/android/build-npu-probe.sh）"
    try:
        # 版本号通过环境变量带进去：**没有它就无法判断"这个包里到底有没有那条
        # manifest 声明"** —— 第一次实测就卡在这个歧义上（坏包 lm33 与 lm34 的
        # 探针输出长得一模一样，但一个必然假阴性，另一个才有意义）。
        env = dict(os.environ)
        env["RFA_VERSION"] = _app_version()
        r = subprocess.run([binary], capture_output=True, text=True, timeout=timeout, env=env)
        out = (r.stdout or "") + (r.stderr or "")
        if r.returncode != 0:
            out += f"\n（探针退出码 {r.returncode}）"
        return out.strip() or "（无输出）"
    except subprocess.TimeoutExpired:
        return f"（探针 {timeout:.0f}s 内没结束 —— 多半卡在 FastRPC 上，这本身也是个答案）"
    except Exception as e:  # noqa: BLE001
        return f"（执行失败：{type(e).__name__}: {e}）"


def java_npu_report() -> str:
    """Java 侧探测的报告（由 `RailBridge.probeNpuJava()` 缓存到 filesDir 下的文件）。

    **为什么必须读文件而不是"顺便调一下 Java"**：Python 侧够不着 RailBridge ——
    `diagnostics()` 只能读文件。而 Java 那半边才是决定性的（`loadLibrary` 成不成 +
    `open` 设备节点成不成）。不接这一条，导出的报告就**只有原生那半**：
    实测 vivo 那份诊断正是如此 —— 决定性数据缺失，而文本看上去毫无异常，极难发现。

    没有缓存文件时返回一句**可读说明**（不是空串）：让"报告缺了一截"这件事
    在报告里**看得见**，而不是让人以为探针就这么多内容。
    """
    for base in (_data_dir, _models_dir):
        if not base:
            continue
        p = os.path.join(base, "npu-java-report.txt")
        if os.path.isfile(p):
            try:
                with open(p, encoding="utf-8") as f:
                    return f.read().strip()
            except OSError as e:
                return f"（读取 Java 侧 NPU 报告失败：{e}）"
    return ("（还没有 Java 侧 NPU 报告 —— 请在设置页点一次「探测 NPU」。"
            "点过之后导出的诊断才会包含决定性的那一半。）")


def npu_probe_report() -> str:
    """**两半合在一起**的 NPU 报告（原生 + Java），末尾带 EOF 标记。

    为什么要合：单看任一半都会得出错误结论 ——
      · 只有原生那半：`dlopen` 必然失败（独立进程拿到的是裸 `(default)` namespace），
        看起来像"设备不支持"，其实是测量方式的假阴性；
      · 只有 Java 那半：拿不到设备枚举、SELinux 域、vendor 清单这些上下文。
    两半一起看才判得了。
    """
    return (probe_npu()
            + "\n\n"
            + java_npu_report()
            + "\n\n--- EOF · NPU 报告结束 ---")


def list_devices() -> str:
    """跑一次 `llama-server --list-devices`，把设备枚举结果原样带回来。

    为什么值得单独做：`-ngl 99` 到底有没有生效，日志里**可能一句话都不留**
    （实测既没有 `offloaded N/M layers`，也没有 `no usable GPU found` 警告）——
    于是"崩在 GPU 还是 CPU"这个最基本的问题都答不上来。
    这个子命令会把 llama.cpp **实际看到的设备**直接列出来，一次说清。
    """
    binary = _binary()
    if not binary:
        return "（没有推理二进制）"
    try:
        r = subprocess.run([binary, "--list-devices"], capture_output=True,
                           text=True, timeout=20)
        out = (r.stdout or "") + (r.stderr or "")
        return out.strip() or "（无输出）"
    except Exception as e:  # noqa: BLE001
        return f"（执行失败：{type(e).__name__}: {e}）"


def clear_logs() -> dict:
    """清空 llama-server 日志与应用日志缓冲。

    为什么要能清空：llama-server.log 是**跨次追加**的，一份导出里混着几十次启动的记录
    （实测为此卡了一轮：同一份日志里有 38.9 与 5.23 tok/s 两条 prefill，却分不清各是什么配置）。
    清空后复现一次，日志里就只有这一次，读起来才有意义。

    截断是安全的：子进程以 **O_APPEND** 打开该文件，截断后下一次写自然回到偏移 0，
    不会留下空洞。
    """
    freed = 0
    if _log_path and os.path.isfile(_log_path):
        try:
            freed = os.path.getsize(_log_path)
            with open(_log_path, "w", encoding="utf-8"):
                pass
        except OSError as e:
            _logger().warning("清空 llama-server 日志失败：%s", e)
    app_cleared = False
    try:
        import server  # type: ignore[import-not-found]  # Android 入口模块

        server._LOG_BUFFER.clear()
        app_cleared = True
    except Exception:  # noqa: BLE001
        pass
    _logger().info("已清空日志（llama-server %d 字节，应用缓冲 %s）", freed,
                   "已清" if app_cleared else "不可用")
    return {"freed_bytes": freed, "app_buffer_cleared": app_cleared}


def _export_dir() -> str:
    """诊断文件的落盘目录。

    **优先放外部应用目录**（`/sdcard/Android/data/<pkg>/files/diagnostics`）：
    写自己的外部目录**不需要任何权限**，而且 `adb pull` 能直接取走、
    文件管理器也看得见 —— 比"分享长文本"可用得多（实测分享面板对长文本限制很多）。
    """
    bases: list[str] = []
    if _models_dir:
        bases.append(os.path.dirname(_models_dir))   # Java 传进来的是 <外部根>/models
    if _data_dir:
        bases.append(_data_dir)
    for b in bases:
        try:
            d = os.path.join(b, "diagnostics")
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, ".write-probe")
            with open(probe, "w", encoding="utf-8") as f:
                f.write("x")
            os.remove(probe)
            return d
        except OSError:
            continue
    return ""


def export_diagnostics() -> dict:
    """把诊断写成**文件**并返回路径（供前端展示/复制，用户可用 adb 或文件管理器取走）。"""
    d = _export_dir()
    if not d:
        raise OSError("找不到可写的导出目录")
    name = f"railfanai-diag-{time.strftime('%Y%m%d-%H%M%S')}.txt"
    path = os.path.join(d, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(diagnostics())
    return {"path": path, "name": name, "size": os.path.getsize(path)}


def diagnostics() -> str:
    """一次性打包排障需要的东西 —— 给"导出日志"按钮用。

    为什么需要它：真机上**没有 adb**，应用私有目录里的 llama-server 日志读不出来，
    而"模型起没起来、跑在什么后端、为什么崩"全都只在这几处。
    实测为此瞎猜过一整轮（以为在用 GPU，其实在跑 CPU；以为已就绪，其实进程早死了）。
    **让人能把日志交出来，比什么都省事。**
    """
    import platform as _pf

    lines: list[str] = []
    w = lines.append
    w("=== RailFanAI 本地模型诊断 ===")
    w(f"时间        {time.strftime('%Y-%m-%d %H:%M:%S')}")
    # 版本必须有：日志是**跨版本累积**的（升级不删内部目录），
    # 没有版本号就分不清哪段日志是哪一版留下的。
    # 注意路径：webapp 是解包到 **data_dir** 下的（`getFilesDir()/webapp`），
    # 不在 Python 代码旁边 —— 第一版按 `__file__/../..` 找，永远返回"未知"（实测）。
    w(f"应用版本    {_app_version()}")
    w(f"Python      {_pf.python_version()} / {_pf.platform()}")
    w(f"CPU 核数    {os.cpu_count()}")
    w(f"模型目录    {models_dir()}")
    w(f"日志文件    {_log_path or '（未设置）'}")
    src = "运行时设置(已落盘)" if _ngl_from_disk() is not None else "默认/环境变量"
    w(f"ngl 来源    {src}")
    w("")
    w("--- 状态 ---")
    try:
        st = status()
        for k in ("state", "detail", "backend", "ngl", "model", "model_warning",
                  "binary_present", "base_url"):
            w(f"{k:14s} {st.get(k)}")
    except Exception as e:  # noqa: BLE001
        w(f"（status() 失败：{e}）")
    w("")
    w("--- 内存占用 ---")
    lines.extend(_mem_report())
    w("")
    w("--- llama.cpp 看到的设备（--list-devices）---")
    w(list_devices())
    w("")
    w("--- NPU 可行性探测（FastRPC / Hexagon DSP）---")
    w(f"探针        {probe_binary() or '（本包不含）'}")
    # 报告完整性写在**最前面**：缺了 Java 那半的报告看起来毫无异常，会被当成
    # "设备不支持"（实测在 vivo 上连踩两次），所以必须在读到细节之前就先看见。
    w("完整性      原生探针 + Java 侧报告" if "Java 侧探测" in java_npu_report()
      else "⚠️ **只有原生探针那半** —— Java 侧报告缺失（先在设置页点一次「探测 NPU」）")
    # 两半一起给（原生 + Java）。只给原生那半会让人得出"设备不支持"的错结论。
    w(npu_probe_report())
    w("")
    w("--- 已下载的模型 ---")
    try:
        for m in list_models():
            w(f"  {m['name']}  {m['size_mb']} MB  dir={m['dir']}")
    except Exception as e:  # noqa: BLE001
        w(f"（列举失败：{e}）")
    w("")
    w("--- llama-server 日志（尾部 200 行）---")
    w(log_tail(200))
    w("")
    w("--- 应用日志（尾部 120 行）---")
    # Android 入口模块里的环形缓冲。桌面环境没有这个模块，拿不到就算了。
    try:
        import server  # type: ignore[import-not-found]

        w(server.recent_logs(120))
    except Exception:  # noqa: BLE001
        w("（非 Android 环境，或取不到应用日志）")
    body = "\n".join(lines)
    # **EOF 标记是硬要求，不是装饰**：这份文件会被分享、复制、经聊天工具转手，
    # **每一环都可能截断** —— 而"被截断的报告"与"报告本来就到这儿"在文本上
    # **完全看不出区别**。实测踩到过：一份 vivo 诊断缺了决定性的一节，看起来却毫无异常。
    # 带上行数与字节数，收件方就能核对（数字是**正文**的，不含这一行自身）。
    return (body
            + f"\n\n--- EOF · 诊断结束 · 正文 {len(lines)} 行 / "
              f"{len(body.encode('utf-8'))} 字节 ---\n")


def list_models() -> list[dict]:
    """已下载的全部模型（跨候选目录），按体积降序。

    **多档共存是调试刚需**：对比 2B/4B 现在只能"删掉再下载"，
    而 `find_model()` 取的是字母序第一个 —— 推两个进去永远选 2B。
    （完整方案见 docs/plan-local-model-multi.md）
    """
    seen: set[str] = set()
    out: list[dict] = []
    active = os.path.basename(find_model() or "")
    for d in readable_dirs():
        if not d or not os.path.isdir(d):
            continue
        for path in glob.glob(os.path.join(d, "*.gguf")):
            name = os.path.basename(path)
            if name in seen:
                continue
            seen.add(name)
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            out.append({
                "name": name,
                "size_mb": round(size / 1048576),
                "dir": d,
                "active": name == active,
                "warning": _model_warning(name),
            })
    out.sort(key=lambda x: -x["size_mb"])
    return out


def log_path() -> str:
    """llama-server 日志文件路径（供排障端点读取）。"""
    return _log_path


def log_tail(lines: int = 120) -> str:
    """日志尾部若干行。文件不存在时返回一句可读的说明，不抛。"""
    if not _log_path or not os.path.isfile(_log_path):
        return f"（日志不存在：{_log_path or '未设置'}）"
    try:
        with open(_log_path, "r", encoding="utf-8", errors="ignore") as f:
            return "".join(f.readlines()[-lines:])
    except OSError as e:
        return f"（读取失败：{e}）"


def detect_backend() -> str:
    """从 llama-server 自己的日志里读出**实际**跑在什么后端上。

    为什么必须暴露这个：我们刚刚因为"没看这个"误判了半天 —— 真机上跑的是纯 CPU 版
    二进制、Adreno 全程闲置，而界面上只看得到"已就绪"。
    **"我以为在用 GPU" 是这一整轮最贵的错误，所以它必须是可见的。**

    解析的是 llama.cpp **真实**的日志原文（从源码里核对过，不是猜的）：
        llama_model_load: using device Vulkan0 (Adreno (TM) 840) (Vulkan) - 8192 MiB free
        load_tensors: offloaded 25/25 layers to GPU
        ggml_vulkan: No devices found.

    注意 `ggml_vulkan: Found N Vulkan devices` 是 **DEBUG 级**（默认不输出），
    所以不能靠它判断 —— 第一版就是照它写的，结果永远返回"未知"。
    """
    if not _log_path or not os.path.isfile(_log_path):
        return "未知（日志不可读）"
    try:
        with open(_log_path, "r", encoding="utf-8", errors="ignore") as f:
            text = "".join(f.readlines()[-500:])
    except OSError:
        return "未知（日志不可读）"

    # 这条是**决定性**的：llama-server 自己说的，比任何间接推断都硬。
    # 实测模拟器（swiftshader 软件 Vulkan）就是这条 —— 二进制带了 Vulkan 后端，
    # 但运行时找不到可用设备，于是 `-ngl 99` 被忽略、全部落回 CPU。
    # **先区分"二进制没编进去"和"这台机器没有 GPU"** —— 两者在日志里都表现为
    # "no usable GPU found"，但原因和该采取的动作完全不同：
    #   · 没编进去 → `-ngl` 是空操作，换台机器也一样，得换二进制（重新交叉编译）
    #   · 机器没有 → 换台有 Adreno Vulkan 的设备就有效
    # 实测踩到：当前打进包的 `libllamaserver.so` 是 CPU-only 版（9.8 MB），
    # 而界面一直报"找不到可用 GPU" —— 看起来像设备的锅，其实是包的问题。
    if "compiled without GPU support" in text:
        return "CPU（**本包编译时未包含 GPU 后端**，-ngl 是空操作 —— 需重新交叉编译）"
    if "no usable GPU found" in text:
        if "using device" in text:
            return "GPU（有警告但已有设备在用，详见日志）"
        return "CPU（llama.cpp 报告找不到可用 GPU；-ngl 被忽略）"
    if "No devices found" in text and "using device" not in text:
        return "CPU（未发现任何 GPU 设备）"

    # 设备名**本身可能带括号**（实测真机是 "Adreno (TM) 840"）：
    # 第一版用 `[^)]*` 会在第一个 ")" 就停住，于是永远匹配不上、设备名丢失。
    # 用贪婪 `.*` 让它回退到最后一个 "(后端名)"。
    dev = re.search(r"using device\s+(\S+)\s+\((.*)\)\s+\((\w+)\)", text)
    off = re.search(r"offloaded\s+(\d+)/(\d+)\s+layers to GPU", text)

    parts: list[str] = []
    if dev:
        parts.append(f"{dev.group(3)} · {dev.group(1)}（{dev.group(2)}）")
    if off:
        cur, total = int(off.group(1)), int(off.group(2))
        parts.append(f"{cur}/{total} 层在 GPU" if cur else f"0/{total} 层在 GPU（全在 CPU）")
    if parts:
        return " · ".join(parts)
    if "ggml_vulkan" in text:
        return "GPU（Vulkan 已加载，未取到设备名）"
    return "未知（日志里没有后端信息）"


def _recheck_process() -> None:
    """把"进程其实已经死了"如实反映到状态里。

    为什么必须做：`_status` 只在启动/健康检查时写。进程**之后**崩掉（实测：Vulkan 版
    在首次推理时挂掉），状态会一直停在 "ready"，于是界面显示"已就绪"，
    而每次请求都拿到 connection refused —— 用户看到的是一句自相矛盾的
    "LLM 尚未配置或不可用"，完全指不到真正的原因。**状态撒谎比状态缺失更坏。**
    """
    global _proc, _started_at
    if _proc is None:
        return
    code = _proc.poll()
    if code is None:
        return
    if _status.get("state") in ("ready", "starting"):
        _status.update(
            state="failed",
            detail=f"推理进程已退出（code={code}）——详见日志；可在设置页重新「启用」",
        )
        _logger().error("llama-server 已退出，退出码 %s（状态已如实更新）", code)
    _proc = None
    _started_at = 0.0


def status() -> dict:
    """给前端的完整状态（含可下载目录与磁盘余量）。"""
    _recheck_process()
    model = find_model()
    size = os.path.getsize(model) if model else 0
    try:
        free = shutil.disk_usage(models_dir()).free
    except OSError:
        free = 0
    return {
        "state": _status.get("state"),
        "detail": _status.get("detail"),
        "model": os.path.basename(model) if model else None,
        # 与 `model` 同义，但**名字说清了语义**：这是当前真正生效的那个。
        # 多档共存时前端靠它标"使用中"，不靠"列表里的 active 标记"去猜。
        "active_model": os.path.basename(model) if model else None,
        "model_warning": _model_warning(os.path.basename(model) if model else ""),
        "models": list_models(),
        "model_size_mb": round(size / 1048576) if size else 0,
        "models_dir": models_dir(),
        "binary_present": bool(_binary()),
        "backend": detect_backend(),          # GPU 还是 CPU —— 必须可见，见 detect_backend 注释
        "ngl": current_ngl(),
        "tune": current_tune(),
        "extra_args": str(current_tune().get("extra_args") or ""),
        # 已经连续跑了多久。手机上本地推理常驻 = 持续发热 + 掉电，
        # 界面据此在超过阈值时给出可操作的提醒（不是装饰性文案）。
        "uptime_s": round(time.time() - _started_at) if _started_at else 0,
        "free_mb": round(free / 1048576),
        "provider_id": PROVIDER_ID,
        "provider_label": PROVIDER_LABEL,
        "base_url": _status.get("base_url"),
        "server_detail": _status.get("detail"),
        "download": download_state(),
        # `gguf_name` 必须一起给：前端靠它判断"这一档是不是已经下过了"，
        # 从而把已下载的档位从"再下载一档"里去掉。少了它前端只能显示全部档位，
        # 用户会为同一个文件重复下载一遍（白等十几分钟，还覆盖掉正在用的那个）。
        "choices": [
            {k: c[k] for k in ("id", "label", "note", "size_mb", "gguf_name")}
            for c in MODEL_CHOICES
        ],
    }


def setup(*, native_lib_dir: str = "", models_dir: str = "", data_dir: str = "",
          sibling_dirs: str = "", log=None) -> dict:
    """App 启动时调用：有二进制且有模型就拉起来，并把供应商环境变量设好。

    **必须在 `import app.main` 之前调用**（配置在 import 时读取）。
    任何一步不满足都只是"不启用"，绝不抛异常打断启动：本地模型是增强项，
    缺了它云端路径和规则排版都还在。
    """
    global _native_lib_dir, _models_dir, _data_dir, _sibling_dirs, _log
    _native_lib_dir = native_lib_dir or ""
    _models_dir = models_dir or ""
    _data_dir = data_dir or ""
    # 兄弟包的模型目录（`: `分隔，由 Java 侧给出）。**只读**：见 readable_dirs()。
    _sibling_dirs = [d for d in (sibling_dirs or "").split(":") if d.strip()]
    _log = log

    if os.environ.get("LOCAL_MODEL_ENABLED", "1").lower() in ("0", "false", "no"):
        _status.update(state="disabled", detail="LOCAL_MODEL_ENABLED=0")
        return status()
    if not _binary():
        _status.update(state="unavailable", detail=f"本包不含推理二进制（{BINARY_NAME}）")
        _logger().info("未启用本地推理：%s", _status["detail"])
        return status()
    if not find_model():
        _status.update(state="unavailable", detail="尚未下载模型")
        _logger().info("未启用本地推理：尚未下载模型（可在设置页一键下载）")
        return status()
    return start(wait=False)
