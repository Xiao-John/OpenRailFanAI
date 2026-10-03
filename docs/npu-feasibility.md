# Hexagon NPU 到底能不能用（调研 + 源码核验）

> 结论日期：2026-09-21 · 核验对象：本仓库 `.android-build/llama.cpp`
> （`git log -1` = `711f60be` "tests : remove stale comment (#29140)"，2026-09-21）
>
> **本文分两类内容，请严格区分：**
> - 🟢 **我亲自核验过的**（源码/文件/HTTP 都在本机跑过，给了命令与输出）
> - 🟡 **只有二手来源、我没能核验的**（网络受限，明确标出）

---

## 0. 结论

**「第三方 App 拿不到 NPU」这个判断是错的。** 我们的 llama.cpp 检出里**已经有**官方的
Hexagon NPU 后端 `ggml/src/ggml-hexagon/`，它是上游 master 的一部分，有官方文档，
而且**不需要 Qualcomm 账号、不需要 NDA、不需要签名**。

**但代价是明确的，不是"改个开关"：**

| 维度 | 结论 |
|---|---|
| 构建方式 | **必须换用官方 Docker 交叉编译镜像**（本机没装 Docker）。现有脚本是"宿主直接交叉编译"，这条路走不通 —— DSP 侧的 `htp-vXX` 必须在容器里用 Qualcomm 工具链编 |
| 覆盖面 | 只覆盖 Hexagon **v73 及以上**（骁龙 8 Gen 2 起）；中低端机走不到，**CPU 回退不能删** |
| **收益在哪** | **不是"2B 翻倍"**。0.8B 在 NPU 上只有 14.5 tok/s（比 4B 的 21.7 还慢）⇒ 小模型受"每 token 固定开销"支配。**真正被解决的是「4B 在 CPU 上不可用」**：4B 的 prefill 我们从 6.75 tok/s → NPU 的 **135 tok/s（≈20 倍）** |
| 工作量 | 构建链路 + 设备枚举 + 自检 + 回退 + 钉版本 + CI，**周级不是天级** |
| 版本风险 | 该后端 **2026-09 每天都在合 PR**（含 Qwen3.5 的 `GATED_DELTA_NET` 优化）。我们 `LLAMA_REF=master`，必须**钉死一个 ref** |
| 最大不确定性 | 我们跑在 Chaquopy 的 Python 子进程 + WebView 里，**DSP 授权是否认这种进程结构，未知** |

**所以定性是：从"完全不可能"变成"值得排期做一次最小验证"，不是"明天就能上"。**

---

## 1. 🟢 源码核验（我在本机逐条跑过）

### 1.1 后端确实存在，且就在我们构建用的那份源码里

```console
$ ls .android-build/llama.cpp/ggml/src/
ggml-cpu  ggml-hexagon  ggml-vulkan

$ ls .android-build/llama.cpp/ggml/src/ggml-hexagon/
CMakeLists.txt  ggml-hexagon.cpp  htp  htp-drv.cpp  htp-drv.h
htp-opnode.h    libdl.h           libggml-htp.inf

$ ls .android-build/llama.cpp/ggml/src/ggml-hexagon/htp | wc -l
84
```

`htp/` 下是 DSP 侧的内核源码（`flash-attn-ops.c`、`matmul-ops.c`、`binary-ops.c`、
`gated-delta-net-ops.c` …）。**`gated-delta-net-ops.c` 值得单独注意**：它是 Qwen3.5 那类
混合注意力里线性注意力分支的算子 —— 也就是说 NPU 侧对我们正在用的模型结构有专门实现。

**为什么这比"上游有"更重要**：`scripts/android/build-llama.sh` 的 `LLAMA_REF` 默认就是
`master`，所以我们下次构建时这份源码**已经在本地**，不需要额外获取。

### 1.2 CMake 开关与官方文档

```console
$ grep -n "GGML_HEXAGON" .android-build/llama.cpp/ggml/CMakeLists.txt
272:option(GGML_HEXAGON  "ggml: enable Hexagon backend"  OFF)

$ ls .android-build/llama.cpp/docs/backend/
BLIS.md CANN.md CUDA-FEDORA.md ET.md OPENCL.md OPENVINO.md
snapdragon SYCL.md VirtGPU VirtGPU.md zDNN.md ZenDNN.md
```

`docs/backend/snapdragon/` 里有 `README.md / developer.md / linux.md / windows.md /
CMakeUserPresets.json`。主 README 明确写：

> "llama.cpp supports three backends on Snapdragon-based devices: **CPU, Adreno GPU
> (GPUOpenCL), and Hexagon NPU**."
>
> "**Hexagon NPU behaves as a "GPU" device when it comes to `-ngl` and other
> offload-related options.**"

**这句是整件事里最有价值的一句**：`-ngl` 的语义完全一致，也就是说我们已有的
「GPU 卸载层数」这个下拉**不需要改架构**就能指向 NPU。

### 1.3 官方构建方式（**必须 Docker**）

```bash
docker run -it --rm -u $(id -u):$(id -g) --volume $(pwd):/workspace \
    --platform linux/amd64 ghcr.io/snapdragon-toolchain/arm64-android:v0.7

cp docs/backend/snapdragon/CMakeUserPresets.json .
cmake --preset arm64-android-snapdragon-release -B build-snapdragon
cmake --build build-snapdragon
```

预设里已经定好：`ANDROID_ABI=arm64-v8a`、`GGML_HEXAGON=ON`、`GGML_OPENCL=ON`、
`GGML_OPENMP=OFF`、`HEXAGON_SDK_ROOT=/opt/hexagon/6.6.0.0`。

产物：

```
libggml-cpu.so  libggml-opencl.so  libggml-hexagon.so
libggml-htp-v73.so  libggml-htp-v75.so  libggml-htp-v79.so  libggml-htp-v81.so
```

`v81` 正对应 **8 Elite Gen 5**（🟡 型号对应关系来自社区，未独立核验；
但镜像里同时构建 v73/v75/v79/v81 是 🟢 从 README 的构建日志里看到的）。

### 1.4 🟢 工具链镜像是**公开可拉**的（这条我实测了）

没有 Qualcomm 账号也能拿：

```console
$ curl -s "https://ghcr.io/token?scope=repository:snapdragon-toolchain/arm64-android:pull&service=ghcr.io"
→ 返回了 token（前缀 djE6c25hcGRyYWdvbi10...，即 base64 的 "s:snapdragon-t..."）

$ curl ... -H "Authorization: Bearer <token>" \
    "https://ghcr.io/v2/snapdragon-toolchain/arm64-android/manifests/v0.7"
→ HTTP 200
```

匿名 token + manifest 200 ⇒ **镜像公开，不需要登录**。

### 1.5 🟢 `-ngl` 之外还有哪些旋钮（README 原文）

| 环境变量 | 作用 |
|---|---|
| `GGML_HEXAGON_DEVICES` | 选设备/会话：`HTP0:0,HTP0:1`（单 NPU 分层）、`HTP0:0,HTP1:0`（跨物理核张量并行）、`HTP0[0-1]`（行并行分组） |
| `GGML_HEXAGON_VERBOSE=1` | **逐算子日志** —— 用来确认"到底有没有真的跑在 NPU 上"，见 §2 |
| `GGML_HEXAGON_PROFILE=1/2` | 算子级 `usecs`/`cycles`/PMU 计数 |
| `GGML_HEXAGON_OPFILTER=regex` | 把匹配的算子踢回 CPU/GPU（例如 `FLASH_ATTN_EXT`） |
| `GGML_HEXAGON_NHVX` | HVX 硬件线程数 |

单会话虚拟地址空间约 **3.5 GB**，超出部分由后端自动 mmap/unmap；也支持多层虚拟会话分摊。

---

## 2. ⚠️ 我推翻了子 agent 的一条核心建议

子 agent 的结论里有一条被标成"最高优先级、零成本、可能直接翻倍"：

> "我们用的 Q4_K_M 很可能在 Hexagon 后端上静默回落到 CPU。Qualcomm 官方反复说要用 Q4_0 …
> **建议立刻做：模型换 Q4_0**"

**这条是错的，而且是可证伪的。** 两重证据：

### 2.1 🟢 源码：Q4_K 是一等公民

```cpp
// ggml/src/ggml-hexagon/ggml-hexagon.cpp:258
static inline bool ggml_hexagon_is_repack_type(enum ggml_type type) {
    return type == GGML_TYPE_Q4_0 || type == GGML_TYPE_Q4_1 ||
           type == GGML_TYPE_Q8_0 || type == GGML_TYPE_IQ4_NL ||
           type == GGML_TYPE_MXFP4 || type == GGML_TYPE_Q6_K ||
           type == GGML_TYPE_Q4_K;          // ← 有它
}
```

`MUL_MAT` 的支持表里显式列举 `GGML_TYPE_Q4_K` 与 `GGML_TYPE_Q6_K`，
并带 `QK_K`(256) 的块对齐检查；DSP 侧 `htp/matmul-ops.h` 里 Q4_K/Q4_1 走的是
**q8_1** 激活量化（而不是 q8_0），有专属 tiled 布局。

### 2.2 🟢 上游 PR 原文：Q4_K_M 就是这次支持的**目标**

`ggml-org/llama.cpp` **PR #28994**「hexagon: Support for K-Quants Q4_K and Q6_K」，
**2026-09-16 合入**（+818/−61，7 个文件，作者 rjtokenring）。正文原文：

> "Support for Q4_K (reuses Q4_1 infra) and Q6_K (new kernels).
> **This PR enables support for Q4_K_M models** which are typically mixes of Q4_K and Q6_K."

我们的检出是 **2026-09-21**（`711f60be`）—— 也就是说，**K-quant 支持是在我们构建源码的 5 天前才合入的**。

**所以**：子 agent 那条「Q4_K 会静默回落 CPU」在 **2026-09-16 之前**是对的，
在**我们实际会构建的版本上**已经不成立。**不要为了这条去重下模型。**

⚠️ 但这 5 天的新鲜度本身是个风险，要如实记下：
- 该 PR 的自测平台写的是「IQ8（VentunoQ）与 IQ9」，**不含 SM8850 / 8 Elite Gen 5**；
- Q6_K 在 `ggml-hexagon.cpp:6240` 仍标注 `// Q6_K has no fused HVX kernel`
  —— 融合路径不成立时性能会掉，Q4_K_M 里恰好含一部分 Q6_K 张量。
  **掉多少必须实测**，这不能靠推断。

---

## 2.5 🟢 自检锚点（比子 agent 给的更可靠）

子 agent 建议用 `GGML_HEX_VERBOSE=1` 自检。**这个变量名在当前源码里不存在**
（`grep GGML_HEX_VERBOSE` 在整个仓库零命中）。实际名字是 **`GGML_HEXAGON_VERBOSE`**，
而且它只是把日志切成 `GGML_LOG_DEBUG` —— 也就是说**还得额外打开 debug 日志级别才看得到**。
这正是我们在 Vulkan 上吃过的那个亏（`ggml_vulkan: Found N Vulkan devices` 也是 DEBUG 级，
所以我们第一版的 `detect_backend()` 永远返回"未知"）。

**真正可靠的自检锚点是另一行，它是 `GGML_LOG_INFO`、默认就会打印：**

```cpp
// ggml-hexagon.cpp:7376
GGML_LOG_INFO("ggml-hex: Hexagon Arch version v%d, DMA64 %s\n", opt_arch, opt_dma64 ? "enabled" : "disabled");
```

⇒ 只要在 `llama-server.log` 里找 **`ggml-hex: Hexagon Arch version v`**，
出现就是 NPU 后端真的起来了（并直接告诉我们 arch，例如 `v81`），
没出现就是没起来。**一行 INFO、无需任何开关、无歧义** —— 这才该是我们的判据。

`status()` 里现成的 `detect_backend()` 解析的是核心层的
`using device <名> (<描述>) (<后端>)`，Hexagon 设备在那里的描述是 `Hexagon`，
所以**大概率不需要改**就能显示成"Hexagon · HTP0（Hexagon）"，
但要在真机上确认（🟡 未验证）。

---

## 3. 🟢 性能：真机数字（代理开了之后核到了原始出处）

### 3.1 数据来源可信

`ggml-org/llama.cpp` **PR #21705**「hexagon: improved Op queuing, buffer and cache management」，
2026-04-10 合入（+1764/−2587，34 个文件），作者 **max-krasnyansky**
（后端原作者）。正文原文：

> "Tested on Galaxy S24U, Galaxy S25+, **Galaxy S26+** with Android, X-Elite with Windows, and X2-Elite with Windows and Linux. Here are some numbers with S26+."

Galaxy S26+ 就是 **8 Elite Gen 5**（SM8850）—— 与我们手上的机器同代。

### 3.2 数字（decode / eval）

| 模型（全部 Q4_0） | 体积量级 | eval tok/s |
|---|---|---|
| qwen3-0.6b | ~0.4 GB | **57.7 – 84.6** |
| qwen3.5-**08b** | ~0.5 GB | **14.26 – 15.14** |
| gemma-4-e2b | ~1.5 GB | **23.5 – 27.8** |
| qwen3-**4b** | ~2.3 GB | **18.88 – 21.73** |
| OLMoE-7B（MoE） | ~4 GB | **44.2 – 50.6** |

### 3.3 ⚠️ 把这张表换算成"我们能拿到多少"，结论**不是简单的翻倍**

我们的现状：**2B Q4_K_M，纯 CPU，decode 14.98 tok/s**（有效带宽 ≈ 18 GB/s）。

按同一算法换算 NPU 侧：

| | 有效带宽 |
|---|---|
| NPU · qwen3.5-0.8B（~0.5 GB @ 14.5 tok/s） | **≈ 7 GB/s** |
| NPU · qwen3-4B（~2.3 GB @ 21.7 tok/s） | **≈ 50 GB/s** |
| **我们 · 2B Q4_K_M CPU（1.19 GB @ 15.0 tok/s）** | **≈ 18 GB/s** |

**这张表里最反直觉、也最重要的一点**：0.8B 在 NPU 上只有 14.5 tok/s，
和 4B 的 21.7 tok/s 相比**模型小了 5 倍、速度反而更慢**。
这说明**小模型在 NPU 上是"每 token 固定开销"受限的**（主机↔DSP 同步、dQ 往返），
不是带宽受限 —— 模型越小，这笔固定开销摊得越薄。

**对我们的含义（这是推断，必须实测确认）：**

- **2B 大概率只有 1.2–1.4 倍**，落在 18–20 tok/s 附近。因为 2B 正好卡在
  "固定开销还没被摊薄、带宽又没吃满"的那段。
- **4B 才是真正的受益者**：4B 在 NPU 上 18.9–21.7 tok/s，而我们在 CPU 上测 4B **prefill 只有
  6.75 tok/s（875 token 要 100 秒，用户实测">90 秒放弃"）**。NPU 这条路的 prefill 是
  **135–137 tok/s**（同表 prompt eval）—— **约 20 倍**。
- **所以"上 NPU"解决的问题是「4B 在 CPU 上不可用」，不是「2B 不够快」。**
  这个定位比子 agent 说的"翻倍"要具体得多，也更有价值。

⚠️ 上表 prompt eval 用的是 **204 token** 的短提示词，而我们真实提示词常在 1000–6000 token。
prefill 通常随长度近似线性，但**长上下文下的表现未经验证**。

### 3.4 🟢 未 root 零售真机上确实跑起来了

`ggml-org/llama.cpp` **issue #27677**（2026-08-24，**仍 open**）原文：

> 标题：`Misc. bug: Hexagon session fails when /vendor/lib64 is on LD_LIBRARY_PATH (Termux, SM8850)`
> "Built in `ghcr.io/snapdragon-toolchain/arm64-android:v0.7`, preset `arm64-android-snapdragon-release`"
> "**Device is a OnePlus 15 (CPH27…**"（SM8850 / 8 Elite Gen 5）
> "I run llama.cpp from Termux instead of the adb scripts. If `/vendor/lib64` is on
> `LD_LIBRARY_PATH`, the Hexagon backend can't open a session. **Take it off and it works.**"

这条同时说明两件事：
1. **未 root 的零售 8 Elite Gen 5 上，Hexagon 后端是能开会话的** —— 不是纸面能力；
2. 有一个具体的坑：**OpenCL 与 Hexagon 抢 `LD_LIBRARY_PATH`**（`libggml-opencl.so` 硬依赖
   `/vendor/lib64/libOpenCL.so`）。我们要同时打 Vulkan/OpenCL 与 Hexagon 时必须注意
   （我们目前用的是 Vulkan，不是 OpenCL，但同一类问题）。

## 4. 🟡 仍未核验（代理通了也没解决）

| 说法 | 状态 |
|---|---|
| 8 Elite Gen 5 = Hexagon **v81** | 🟡 未直接核到权威映射。但后端会**自己打印** `Hexagon Arch version vNN`（§2.5），真机一跑就知道，不必猜 |
| `snapdragon-toolchain/hexagon-sdk` 的 release 可匿名下载 | 🟡 `releases` 下载在我这里超时；仓库本身存在（2026-09-17 更新，但**只有 6 star、无 license 字段**）。**不过这条不重要** —— Docker 镜像里已经带了 Hexagon SDK（§1.3 预设里的 `HEXAGON_SDK_ROOT=/opt/hexagon/6.6.0.0`） |
| GenieX Android SDK（Maven Central、AAR 82 MB、minSdk 27、仅 SM8750/SM8850） | 🟡 未核验。**而且它违背本项目「无私有 SDK」的定位，不作为路线** |
| Qualcomm 那条 `GENIEX_LLAMACPP … 38.599606` 的列含义 | 🟡 一直没核到。**不要再引用它当基准** |
| MLC-LLM 用不用 NPU | 🟡 子 agent 自己也**不采信**那篇来源；无一手证据。不作为依据 |

## 4.5 🟢 一个影响排期的现实：这个后端**每天在变**

代理通了之后能看到，2026-09 这个后端是**高频合入**的：

```
#29197  merged 2026-09-21  hexagon: overhaul of buffer and DMA handling for 64bit mappings
#29116  merged 2026-09-19  hexagon: enable I32 GET_ROWS
#29114  merged 2026-09-19  hexagon: add support for GEGLU_QUICK
#29113  merged 2026-09-19  hexagon: enable support for TOP_K op
#28994  merged 2026-09-16  hexagon: Support for K-Quants Q4_K and Q6_K
#28589  merged 2026-09-12  hexagon: support for multi-device model split (row-split)
#29199  open             hexagon: new HMX-optimized GATED_DELTA_NET   ← Qwen3.5 线性注意力，正在做
#29123  open             hexagon: add q5_k quant type support
#29052  open             ci: add Hexagon NPU backend build for Windows Arm64
```

两面看：
- **好**：有人长期投入（看起来就是 Qualcomm），算子覆盖在快速补齐，
  而且 **`GATED_DELTA_NET` 正在被 HMX 优化** —— 那正是 Qwen3.5 混合注意力的关键算子，
  说明我们的模型结构在路线图上。
- **坏**：版本漂移快。我们的 `build-llama.sh` 用的是 `LLAMA_REF=master`，
  意味着**每次构建拿到的后端都不同**，性能与稳定性会跟着抖。
  真要上，必须**钉住一个 ref**（就像现在对 Vulkan 的做法一样）。

## 5. 对项目的具体影响（方案骨架，**未实施**）

如果要做，改动面比想象中小，因为后端在上游、`-ngl` 语义与 GPU 一致：

1. **构建**：`scripts/android/build-llama.sh` 增加第三种模式（现在只有 Vulkan / `--cpu-only`）。
   需要 Docker；产物多出 `libggml-hexagon.so` + `libggml-htp-v7{3,5,9}1.so`（🟡 约 +4.5 MB）。
   **注意**：现有脚本是"宿主直接交叉编译"，这条路走不通 —— Hexagon 侧的 `htp-vXX`
   是**在容器里**用 Qualcomm 工具链编 DSP ELF 的。
2. **打包**：新增的 `.so` 放进 `jniLibs`（AGP 会自动打进去）。
   `libcdsprpc.so` **不能打包**（系统库，打包会破坏 DSP 握手）——需要 manifest 里的
   `uses-native-library` 声明（🟡 具体写法未核验）。
3. **运行时**：`local_inference.py` 的 `ngl` 下拉已经够用；需要新增的是
   - 设备枚举（`--list-devices` 现在会多出 `HTP0`），
   - **`GGML_HEXAGON_VERBOSE=1` 自检**：启动后从日志里找 `ggml-hex: Hexagon Arch version vNN`
     与 `libggml-htp-vNN.so` 会话行；**找不到就说明回落到了 CPU** —— 这条必须是可见的，
     否则又是一个"以为在用 NPU"（我们已经在 Vulkan 上吃过一次这个亏，见 `pending-fixes.md` P3）；
   - CPU 回退：v73 以下 / 非骁龙机型必须还能跑。
4. **不确定性最大的点**：DSP 授权（`/dev/fastrpc-cdsp`）只对"正常打包启动的进程"有效。
   我们的推理跑在 **Chaquopy 的 Python 子进程**里、界面在 WebView 里 ——
   这个进程结构是否被认可，**没有任何二手来源能回答，只能真机实测**。
   这也是我认为**必须先做一个最小验证包**、而不是直接排全量开发的原因。

---

## 5.5 🟢 APK 打包：三个已知坑，我们已经踩掉一个

`ggml-hexagon` 是"原生库"，但它要被**设备上的 FastRPC 加载**，所以有几条 Android 打包
特有的硬要求。三条都有第三方实证（`llama.rn` / `primer` / `whisper-htp-android`）：

| 坑 | 后果 | 我们的状态 |
|---|---|---|
| **manifest 少 `<uses-native-library android:name="libcdsprpc.so">`** | targetSdk ≥ 31 的 vendor library isolation 会**把 `/vendor/lib64` 藏起来**，进程连 `libcdsprpc.so` 都 dlopen 不到 | **已处理（lm33）**：加在 `android/app/src/lm/AndroidManifest.xml`，**只对 LM 轨生效**（正式包不受影响，已核验） |
| `jniLibs.useLegacyPackaging` 没开 | 原生库只留在 APK 内被 mmap、**不落地成真实文件**，而 `ADSP_LIBRARY_PATH` 需要真实路径 | **本来就有**（`useLegacyPackaging = (lmLabel != null)`），当初为 `libllamaserver.so` 的可执行权限加的，正好是同一个需求 |
| DSP skel 与 host stub 的 **Hexagon arch 必须严格匹配** | 加载失败，且报错指向不明 | 待做（要编 v81 时才会遇到） |

⚠️ 关于第一条，有一个**很容易静默失效**的细节：官方文档写明
`<uses-native-library>` 的 "contained in" 是 **`<application>`**，不是 `<manifest>`。
放错层级**不报错、也不生效**。已在 lm33 的编译后 manifest 里核到它确实在 `<application>` 内。

**这意味着我的探针在 lm32 上会给出假阴性**（`dlopen("libcdsprpc.so")` 失败，
但原因是 namespace 而不是 DSP 授权）。**所以 lm32 作废，请用 lm33。**

### 已有的真实先例

- **`mybigday/llama.rn`**（React Native 绑定，ChatterUI 在用）把 Hexagon 列为正式特性，
  App 侧只要 manifest 加那一行 + 传 `devices: ['HTP0']`；
  **ChatterUI v0.9.0 的 release notes 已上线 "Added NPU and OpenCL acceleration for Snapdragon 8 devices"**。
  → 说明"普通未 root 的商店 App 用上 Hexagon"不是纸面方案。
- 注意 `primer`（RedMagic 11 Pro / SM8850）走的是 **QNN/Genie**，不是 `ggml-hexagon`，
  但它在 APK 上踩到的坑与上面这张表**完全一致**，可以互相印证。

### 预期要压住（别被官方数字带走）

- 官方 README 的 1B Q4_0 → **prefill 169 t/s / decode 51.5 t/s**；
- 但 issue **#18139**「Hexagon backend cannot achieve the same performance as QNN SDK」
  里用户实测只有 **~0.5 TOPS**（宣传是 45 TOPS），该 issue 以 `not_planned` 关闭；
- 后端仍标 **experimental**，open issue 里有 `dspqueue_read failed`、HMX 在 8 Elite 上乱码、
  `ubatch>=32` prefill 乱码、`FLASH_ATTN_EXT` 非确定性等一批稳定性问题。

**结论：把它当作"4B 能不能用"的解法去验证，而不是当作"跑满 NPU"的解法。**

## 5.6 真机实测记录（8 Elite Gen 5，lm35 的探针 v2）

| 检查 | 结果 | 读法 |
|---|---|---|
| SELinux 域 | `u:r:untrusted_app:s0:c160,...` | 子进程**继承 App 的域** ⇒ **"换到主进程"这个变量不成立** |
| `/dev/fastrpc-cdsp` | `stat` **EACCES(13)** | 节点**存在**，被拒绝（不是"没有"） |
| `/dev/fastrpc-cdsp-secure` | `stat` **EACCES(13)** | 同上 |
| `/dev/fastrpc-sdsp`、`/dev/adsprpc-smd` | ENOENT | 这台机器上没有 |
| `/vendor/lib64/libcdsprpc.so` | stat OK，`0644`，612672 字节 | **库在，且世界可读** |
| `/vendor/etc/public.libraries.txt` | 292 字节，**含 `libcdsprpc.so`** | **"没注册成 public library"这个解释被排除** |
| `dlopen("libcdsprpc.so")` / 绝对路径 | `is not accessible for the namespace "(default)"` | ↓ |

**linker 原文把 namespace 打了出来，这才是关键证据**：

```
default_library_paths="/system/lib64:/system_ext/lib64"
permitted_paths="/system/lib64/drm:...:/vendor/framework:/vendor/app:/vendor/priv-app:..."
                                                        ↑ **没有 /vendor/lib64**
```

**⚠️ 这里的 "not found" 很可能是"测量方式的假阴性"，不是设备的结论。**
`<uses-native-library>` 的授权加在 **App 的 classloader namespace** 上，
而探针是 `exec()` 出来的**独立可执行文件**，拿到的是裸的 `(default)` namespace ——
那条声明对它不生效。**换个地方 dlopen，结论可能完全不同。**

→ 补了 **Java 侧探针**（`RailBridge.probeNpuJava()`，跑在 App 主进程、走 classloader
namespace，即"普通 App 真正会用到的那条路"）。两种探测**必须一起看**：

| Java `loadLibrary("cdsprpc")` | Java 打开 `/dev/fastrpc-cdsp` | 结论 |
|---|---|---|
| 成功 | 成功 | **NPU 可用** —— 原生探针结构要改（改从 JNI/进程内调用，不要 exec 独立二进制） |
| 成功 | 被拒 | **本机硬性堵死**：库能加载，但没人能开 DSP 设备节点 |
| 失败 | — | namespace 理论不成立，堵在更早一层 |

**已可确定的一点**：SELinux 域是**进程级**的，Java 与原生探针同域 ⇒ 第二行
**大概率会成立**。若真如此，这一条**不是打包能解决的**，是平台策略。

**另注**：结果与调研来源对不上（issue #27677 是一加 15、primer 是 RedMagic 11 Pro）
⇒ "第三方能不能用 NPU" **是机型相关的，没有统一答案**。

## 5.7 最终结论（lm36 双向探测，8 Elite Gen 5）

**Java 侧（App 主进程 / classloader namespace）—— 这才是决定性的一半：**

```
loadLibrary("cdsprpc")               → **成功**
load("/vendor/lib64/libcdsprpc.so")  → **成功**

/dev/fastrpc-cdsp         exists=true canRead=true  → open 失败：EACCES (Permission denied)
/dev/fastrpc-cdsp-secure  exists=true canRead=false → open 失败：EACCES (Permission denied)
```

### 结论：**这台机器上 NPU 不可达，且不是我们代码或打包的问题。**

两件事被分别钉死：

1. **`<uses-native-library>` 的授权确实生效了** —— Java 侧加载成功，说明
   "原生探针 dlopen 失败"是**测量方式的假阴性**（独立 `exec()` 出来的进程拿到的是
   裸的 `(default)` namespace，声明对它不生效）。**我那条 namespace 推断是对的。**
2. **但 DSP 设备节点打不开** —— 库只是用户态包装，真正 `open/ioctl` 设备节点的是它。
   Java 用 `O_RDONLY`、原生探针用 `O_RDWR`，**两条都被 EACCES 拒**。

⇒ 即使把 `ggml-hexagon` 编出来、把 `libggml-htp-v81.so` 打进包，**也开不了会话**。

### 一个没解释干净的点（如实记下）

原生探针 `stat /dev/fastrpc-cdsp` 拿到 **EACCES**，而 Java 的 `File.exists()` 返回 **true**。
两者本该一致。最可能的解释是 `access(F_OK)` 不需要 SELinux 的 `getattr` 权限，而 `stat()` 需要
—— 但**我没有验证这一点**。不过它不影响结论：`open` 被拒是两条路共同的、且是真正要紧的那一步。

### 对产品的含义

调研里的先例（issue #27677 一加 15、primer 的 RedMagic 11 Pro）说明**别的机型可以**。
所以 "第三方能不能用 NPU" **是机型/ROM 相关的策略问题，没有统一答案**。

**用户那句"没见哪个主流 app 用"现在有了具体机制**：在这类机器上，第三方 App
不是不想用 —— 是**根本打不开那个设备节点**。NPU 对第三方开放与否，取决于厂商的
SELinux 策略，而不是 Android SDK 里有没有接口。

### 建议

- **在没有第二台可测机型之前，停掉 NPU 这条线。** 2–4 周的工程投入换不来一台
  打不开 DSP 的设备上的任何收益。
- 探针（`探测 NPU` 按钮）**保留**：13 KB，是唯一能在新机型上快速给出答案的东西。
  换一台骁龙 8 Gen 2 以上的机器跑一次，若 `open` 成功，这条线立刻重新成立。
- `<uses-native-library>` 声明保留（惰性、无害），其它机型上它是必要条件。

## 6. 建议的下一步（按性价比排序）

**第 0 步（已实现，见下）：确认我们这个进程结构到底拿不拿得到 DSP。**

这是整件事唯一"无法从文档推断、只能实测"的环节 —— 推理跑在 **Chaquopy 起出来的
Python 子进程**里，而 DSP 授权只对"正常打包启动的进程"有效。已做成一个探针：

| 文件 | 作用 |
|---|---|
| `scripts/android/npu-probe/npu_probe.c` | 探针本体。只用 `dlopen`/`dlsym`，**不需要 Hexagon SDK**（函数签名逐条照抄 `htp-drv.cpp:35-76`），所以本机没有 Docker/SDK 也能编 |
| `scripts/android/build-npu-probe.sh` | 编成 `android/app/src/lm/jniLibs/arm64-v8a/libnpuprobe.so`（11 KB，只依赖 libdl/libc） |
| `backend/app/local_inference.py` 的 `probe_npu()` | 以 **Python 子进程**的方式拉起它 —— 也就是复现我们真实的进程结构 |
| 设置页「探测 NPU」按钮 / `GET /api/local-model/npu-probe` | 点一下看结果；原始输出直接铺在卡片里（可复制、可截图） |
| `android/app/src/lm/AndroidManifest.xml` | 声明 `libcdsprpc.so`（**前提条件**，见 §5.5）—— 没有它，探针第 2 步会假阴性 |

探针查四件事，**逐条独立**（一条失败不会掩盖另一条）：

1. `/dev/fastrpc-cdsp` 能不能 `stat` + `open(O_RDWR)` → errno
2. `dlopen("libcdsprpc.so")` → 成败 + `dlerror`
3. `dlsym` 后端要求的 11 个符号各自有没有
4. `remote_handle64_open()` 打开 3 个 URI（一个必然不存在的当基线、一个我们真要用的
   skel 名、一个固件可能预装的 QNN skel）

**判据是"失败的方式"，不是"成不成"**（写死在 C 源码注释里，两边不要漂移）：

- 报 **`AEE_EPRIVLEVEL`(0x15)** → 被授权拦下，这条路当前走不通；
- 报 **`AEE_ENOSUCHFILE`(0x45) / `ENOSUCH`(0x27) / `EUNABLETOLOAD`(0x06)**
  → **已经过了授权层**，只是那个 skel 没打包进来
  ⇒ 只要把我们自己编的 `libggml-htp-vNN.so` 打进去，就有资格在 DSP 上开会话。

⚠️ 一个必须记住的细节：**错误码按低字节判读**（`err & 0xff`）。
AOSP 版 `AEEStdErr.h` 里 `AEE_EOFFSET` 是 `0`，而 Qualcomm SDK 里是 `0x80000400`
—— 只有低字节是跨版本稳定的，llama.cpp 自己也是这么比的（`htp-drv.cpp:412`）。

**第 1 步**：装 Docker，用官方预设编一份带 Hexagon 的 `llama-server`
（`--preset arm64-android-snapdragon-release`，`GGML_HEXAGON=ON`），只验证三件事：
- `--list-devices` 出不出 `HTP0`；
- 日志里出不出 **`ggml-hex: Hexagon Arch version vNN`**（§2.5，INFO 级，不需要 verbose）；
- 出得来之后，**实测我们的 2B/4B 到底多少 tok/s**（§3.3 的推断必须被实测取代）。

**第 2 步**：同一份包顺带测：
- **Q4_K_M**（我们现成的模型，不要去重下 Q4_0）在 NPU 上到底跑不跑、多快
  —— 顺带把 §2.2 里"Q6_K 无融合内核"的影响量化出来；
- 4B 的 prefill 是否真有 ~135 tok/s（这是 §3.3 里唯一"值得换路线"的理由）。

**第 3 步**：只有前面都过了，才谈「NPU 作为加速层 + CPU 回退」的正式实现，
并且**钉死 llama.cpp ref**（不能再用 `master`）。

**不要做的事**：
- 不要为了"换 Q4_0"提前重下模型（§2 已推翻，Q4_K_M 支持的 PR 就在我们源码里）；
- 不要用 `GGML_HEX_VERBOSE` 做自检（这个变量名不存在，见 §2.5）；
- 不要引入 GenieX / QAIRT（私有 SDK + 80 MB 体积 + minSdk 27，违背项目定位）；
- 不要把 Qualcomm 的 38.6 tok/s 当目标（🟡 口径一直没核实，且多为官方 AI Hub 环境）；
- 不要在验证成功前动 `build-llama.sh` 的默认行为（现有 Vulkan/CPU 两条路是能用的）。
