# 问题清单（调试期间记录 / 已修与未修都在这里）

> 这里记的是**已知问题**，每条都写了根因与验证方式，不是"感觉不对"。
> **已在 lm31 修掉：P1、P4、P5；P2 / P3 / P3.7 更早已修。**
> 仍未解决：**P3.5（设备端 SIGSEGV）**。

---

## P1. 累计 Token 跨对话累积（且这个数本身不可信）——**已修（lm31）**

**现象**：底部「累计 Token」是所有对话加在一起的历史总和，新开对话也不归零。

**根因有两个，必须一起修**：

1. `frontend/src/store.js:16` `LS_TOTAL = "railfan_total_tokens"` 是 **localStorage 里的全局计数器**，
   `main.js:867` 在 `done` 事件里 `bumpTotal(ev.usage.total_tokens)` 累加 —— 与对话无关。
2. **更根本**：流式调用**从来没要过 usage**。
   `backend/app/llm/client.py` 里 `stream_options` 只出现在"可失败参数"降级表（`_PARAM_ALIASES`），
   **从未真正下发**。OpenAI 兼容的流式默认不回 usage（要 `stream_options: {"include_usage": true}`），
   所以 `ev.usage` 基本恒为 0 —— 截图里"本次 token used: 0（输入 0 / 输出 0）"正是这个。

**改法**：

- 后端：`chat_completions` 方言的流式请求带上 `stream_options={"include_usage": True}`；
  上游不认时**必须能降级丢弃**（该参数已在降级表里，链路是通的，只是没人发）。
- 前端：把 `totalTokens()` 改成**从当前对话的消息里求和**：
  ```js
  totalTokens() {
    const conv = this.current();
    return (conv?.messages || []).reduce(
      (s, m) => s + ((m.meta && m.meta.usage && m.meta.usage.total_tokens) || 0), 0);
  }
  ```
  这样顺带自愈：删消息、编辑重发之后数字都对，也不需要 `bumpTotal` 了。

**验收**：新开对话后计数归零；回答里能看到非 0 的输入/输出 token；重发生成后计数不重复累加。

**已修（lm31）**：
- 后端：`_kwargs_for(..., stream=True)` 现在真的下发
  `stream_options={"include_usage": True}`（此前那个键只躺在降级表里，没人发）。
  上游不认时走既有阶梯丢弃重试；阶梯认不出来（网关回 5xx 而非 400/422）时，
  可用新的配置项 `LLM_STREAM_USAGE=false` 逃生。
- 前端：`store.totalTokens()` 改成**按当前对话的消息求和**（重算，不是累加），
  `bumpTotal` 与全局键 `railfan_total_tokens` 一并删除（迁移时顺手清掉死键）。
- 气泡里的用量现在拆开显示「输入 N / 输出 M」—— 只有总数时看不出瓶颈是 prefill 还是 decode。
- 回归：`backend/tests/test_llm_providers.py::test_stream_requests_usage`（含"非流式不该带"
  与"开关能关"两条反向断言）、`frontend/tests/store.test.mjs` 里的四段对话语义断言
  （求和 / 多轮累加 / 新对话归零 / 截断后减少 / 缺 usage 不污染合计）。

---

## P2. 真机上导不出日志 → 远程排障无路可走

**现象**：设备上没有 adb，`llama-server.log` 在**应用私有目录**，读不出来；
出问题时只能靠猜。

**已在 lm20 修**：

- 新增 `GET /api/local-model/diagnostics`：一次打包状态（含 `backend`）+ llama-server 日志尾部
  + 应用日志环形缓冲 + 环境信息
- 设置页「本地模型」卡片加了**「导出诊断」按钮**：走系统分享面板（`RailNative.shareText`），
  失败则退回复制到剪贴板
- 卡片上直接显示 `backend`（实际跑在 GPU 还是 CPU）与模型警告

---

## P3. 状态会撒谎：进程已死却仍报「已就绪」

**现象**（与 P2 同一次调试中撞到）：界面显示本地模型「已就绪」，
但每次提问都返回"LLM 尚未配置或不可用 / 网络连接错误"，用户完全看不出是进程死了。

**根因**：`local_inference._status` 只在**启动与健康检查时**写。
进程**之后**崩掉（Vulkan 版在首次推理时挂掉，是当前的头号怀疑对象）状态不会更新。

**已在 lm20 修**：`status()` 每次先 `_recheck_process()`，
发现子进程已退出就如实改成 `failed` 并带上退出码。

**注意**：这只让状态**如实**，不代表进程不再崩。崩溃本身要看 P2 导出的日志。

---

## P3.5. 设备端 SIGSEGV（**未解决，正在隔离**）

**现象**（lm19/lm20，8 Elite Gen 5 真机）：

```
selfchecker: checker "remold.libllamaserver.so" detects sig11
llama-server 已退出，退出码 -11
```

崩在**请求刚开始处理**的那一刻：

```
slot launch_slot_: id 3 | task 0 | processing task, is_child = 0
selfchecker: ... detects sig11
```

**已排除的**：
- 不是超时（是信号杀死，退出码为负）
- 不是连接配置（供应商解析正确、地址正确）

**重点疑点**：llama-server 日志里**既没有 `load_tensors: offloaded N/M layers to GPU`，
也没有 `no usable GPU found` 警告**。按 llama.cpp 的逻辑这两者必有其一 ——
"设备被枚举到但卸载没发生"本身就不正常，指向 `-ngl 99` 这条路径。

**日志里的关键矛盾（必须消掉）**：按 llama.cpp 的逻辑，`-ngl 99` 只有两种结果 ——
`load_tensors: offloaded N/M layers to GPU` 或 `warning: no usable GPU found`。
**这两句一句都没出现**，于是"到底崩在 GPU 还是 CPU"根本无法判断。
现有两种互斥解释，不消掉就可能花几天修一个不存在的问题。

**已做的隔离手段（lm21）**：`-ngl` 改为**运行时可切**（`POST /api/local-model/start {"ngl": 0}`），
设置页加了「GPU 卸载层数」下拉 + 「应用并重启」。切到 0 若立刻稳定 ⇒ 就是 GPU 后端的锅。

**lm22 补充**：诊断信息里加了 `llama-server --list-devices` 的原始输出 ——
它直接列出 llama.cpp **实际看到的设备**，一次说清"Vulkan/Adreno 到底可不可见"。
另外修了版本号读取的路径 bug（webapp 解包在 `data_dir` 下，不在 Python 代码旁边，
第一版按 `__file__/../..` 找，永远返回"未知"）。

**顺带记一个坑**：`llama-server.log` 是**跨版本累积**的（升级不删应用内部目录），
所以同一份日志里混着不同版本留下的记录。已在诊断信息里补上**应用版本号**，
否则根本分不清哪段日志是哪一版写的 —— 这次就卡在这上面。

## P3.6. 已知的真实性能（供后续对照）

真机 8 Elite Gen 5，**2B Q4_K_M，纯 CPU**：

| | 实测 |
|---|---|
| prefill | **38.9 tok/s**（1096 token 用 28.2 s） |
| decode | **19.2 tok/s**（367 token 用 19.1 s） |
| 一次完整回答 | **47.2 s** |

4B 同一台机器：prefill **6.75 tok/s**（875 token 要 100 s）—— 用户实测">90 秒放弃"属实。

对照 Qualcomm 自家优化栈（2B q4_0）的 38.6 tok/s：我们**纯 CPU 的 decode 是它的一半**。
这就是"开 GPU 值不值"的量化依据。

## P3.7. 文件导出：私有目录对用户**不可达**（已修）

**现象**：把诊断写成文件放在 App 外部私有目录
(`/sdcard/Android/data/<pkg>/files/diagnostics/`)，用户**根本找不到**。

**根因**：Android 11+ 把 `/sdcard/Android/data/` 对文件管理器屏蔽了。
文件写得出来、`adb pull` 也取得到，但**用户没有 adb 时无路可走**。
选这条路径时只考虑了"不需要权限"，没考虑"用户能不能找到" —— 这是设计失误。

**已修（lm28）**：改走 **SAF（`ACTION_CREATE_DOCUMENT`）** ——
系统弹「保存到…」，用户自己选位置（下载/文档/网盘），**不需要任何存储权限**，
落点是用户知道的地方。

- Java：`RailBridge.saveTextFile(name, text)` + `onActivityResult` 写盘
  （`@JavascriptInterface` 跑在 JavaBridge 线程，`startActivityForResult` 必须 `runOnUiThread`）
- 前端：`native.saveTextFile()` 为主路径；网页端/老包退化为"写服务端目录 + 复制路径"，
  再退化为"整段文本进剪贴板"

## P3.8. 真机性能对照与结论（2026-09-21，8 Elite Gen 5 / 2B）

**同一问题、同一模型、同一机器，只改 `ngl`（对照干净）**：

| ngl | prefill | decode |
|---|---|---|
| **0（CPU）** | **26.12** tok/s | **14.98** tok/s |
| 32 | 25.70 | 14.99 |
| 99 | 22.80 | **11.40** |

**结论：GPU 卸载在这台机器上是亏的** —— decode 慢 24%、prefill 慢 13%。

⚠️ **本文件早先写的"GPU 慢 7.4 倍"是错的**：那是拿两份**配置不可比**的旧日志算的
（当时日志不记录 ngl）。已改为上表的对照数据。

**"部分卸载更慢"是个误解**：Qwen3.5-2B **只有 24 层**，`-ngl 32` 已经取满 =
和 `-ngl 99` 是同一种配置。真正的部分卸载要 `-ngl 12` 这种。

**为什么全卸载反而慢**（手机上的常态，不是异常）：
1. 每 token 都要跨 CPU↔GPU 同步一次（命令缓冲 + fence 等待），小模型摊不薄这笔固定开销；
2. **llama.cpp 的 CPU Q4 内核是手写 NEON 优化的，而 Adreno 上的 Vulkan compute 没有协同矩阵**——
   等于把快路径换成慢路径；
3. prefill 本该是 GPU 强项，但按层拆开后 CPU 段与 GPU 段串行。

### "高通 38.6 tok/s 是怎么测的"

公开口径：`GENIEX_LLAMACPP | q4_0 | Snapdragon 8 Elite | 512 | 38.6`。
对比我们：`llama.cpp | Q4_K_M | Snappy 8 Elite Gen 5 | ctx 8192 | decode 14.98`。

差异按可能性排序：
1. **多半测的是 NPU（Hexagon）** —— 高通整套卖点就是 NPU 推理；而本项目已确认
   **第三方 App 基本拿不到 NPU**（NNAPI 废弃、厂商栈封闭）。
2. 上下文 512 vs 8192（decode 上影响有限）。
3. 自家 GenieX 栈的额外 kernel 优化。

**"换成 q4_0 能快一截"这个假设是错的**：查过源码，ARM 上 **Q4_0 与 Q4_K 都有重排内核**
（`ggml_gemv_q4_0_4x4_q8_0` 与 `ggml_gemv_q4_K_8x8_q8_K`），Q4_K_M 已走优化路径。

### 还没试的真正旋钮

按百亿分之一秒成本排序，**threads 嫌疑最大**：

- decode 14.98 tok/s × 1.2 GB ≈ **有效带宽 18 GB/s**，而机器约 **68–77 GB/s**
  → 效率仅 ~24%，**既没打满带宽也没打满算力**。
- 设备是 **2 大核 + 6 性能核 = 8 核**，而我们一直写死 `-t 4`，**只用了 4 个**。

lm29 起 `ngl / threads / ctx` 都可运行时调且落盘，设置页有三个下拉。

## P4. 本地模型不支持多档共存 ——**已修（lm31）**

方案见 `docs/plan-local-model-multi.md`（该文是施工图，实现已按它落地）。

现状一句话（**修之前**）：`find_model()` 取字母序第一个、`delete_model()` 删全部，
所以"对比 2B/4B"只能**删掉再下载**（508MB–2.6GB），把对比评测变成做不了的事。

**已修（lm31）**：
- `list_models()` 按体积降序列出全部档位；选中状态落盘 `data_dir/.selected_model`；
  `find_model()` 优先级 = `LOCAL_MODEL_PATH` → 选中 → 字母序第一个
  （**没选过时行为完全不变**，向后兼容是硬要求）；
- `POST /api/local-model/select` 切换；`POST /api/local-model/delete {"name": ...}`
  只删一个，`{"all": true}` 才全删，**两样都不给则 400**；
- 安全：`_safe_model_name()` 收成纯文件名（拒 `../`、绝对路径、非 `.gguf`），
  且必须出现在 `list_models()` 里 —— 否则就是一条任意文件删除通道；
- 删掉"当前使用"的那一档会自动切到剩下体积最大的一个并重启，
  不会把用户留在"没有模型、服务也停了"却以为只是清了一档的状态；
- 设置页改成一张列表（使用中 / 使用 / 删除 各自独立），并保留"再下载一档"入口。

---

## P5. 前端条目里存的 `model` 会过期 ——**已修（lm31）**

「设为当前使用」时往 `store.upsertLlmEntry` 写了 `model: d.model`，
而 `main.js:llmSpec()` 会把它作为**请求级覆盖**下发。

**换了本地模型之后，前端存的还是旧模型名** → 请求带着旧名字打到新模型上。

这与之前那次 SSRF 事故（存了 `base_url` → provider 被降级成请求级 → 撞守卫）
**是同一个病根**：把服务端自己的事实回传给服务端。
当时只删了 `base_url` 与 `key`，**漏了 `model`**。

**改法**：条目只留 `{id, label}`；`model` 由服务端的 `provider_item()` 提供。
随 P4 一起改，并补进回归测试。

**已修（lm31）**：两处一起改，缺一不可 ——
1. `pages.js` 的「设为当前使用」不再写 `model`（只写 `id` + `label`）；
2. `main.js` 新增 `SERVER_OWNED_PROVIDERS = ["ondevice"]`：`llmSpec()` 对**服务端自有的**
   供应商**只下发 id**，地址、模型名、Key 一概不传。
   第 2 条是给**已经存着旧数据**的人兜底 —— 光改写入点不解决存量。
   回归：`test_local_inference.py::test_frontend_does_not_send_local_base_url`
   现在同时断言 `base_url` 与 `model` 都不在条目里，且黑名单存在。

---

## P7. 装完没有桌面图标、安装器只显示包名 ——**已修（lm34）**

**现象**（用户实测，lm33）：更新时安装器显示的是包名而不是应用名，装完**桌面上找不到图标**。

**根因**：为了给 NPU 加 `<uses-native-library android:name="libcdsprpc.so">`，
当时想「只对 LM 轨加、别碰正式包」，于是给 LM 轨写了一份附加 manifest 并用
`manifest.srcFile("src/lm/AndroidManifest.xml")` 挂上去 —— **误以为它是"追加并合并"**。

`AndroidSourceSet.manifest.srcFile()` 实际是**替换**：整个 `src/main/AndroidManifest.xml`
被顶掉。产出包的后果（`aapt2 dump badging` 对比 lm31 vs lm33）：

| | lm31 | lm33（坏） |
|---|---|---|
| `application-label` | `RailFanAI` | **空** |
| `application icon` | `res/r2.xml` | **空** |
| `launchable-activity` | `MainActivity` | **没有** |

label 为空 ⇒ 安装器只能显示包名；没有 LAUNCHER 入口 ⇒ 桌面没有图标。

**最坏的地方是它静默**：`BUILD SUCCESSFUL`、零警告。只有装到设备上才看得出来。

**已修（lm34）**：
- 删掉 `src/lm/AndroidManifest.xml` 与那行 `manifest.srcFile(...)`；
- 声明直接进 **`src/main/AndroidManifest.xml`** 的 `<application>` 内
  （`required="false"`，非骁龙设备上是惰性的，无行为代价）；
- 回归测试 `test_android.py::test_launcher_entry_and_label_survive`：静态断言主 manifest
  保住 LAUNCHER / label / icon、FastRPC 声明在 `<application>` 内、且代码里不再出现
  `manifest.srcFile(`（判据先剥注释 —— 第一版把自己注释里的那个词当成了违规，是个假失败）。

**教训（可迁移）**：`BUILD SUCCESSFUL` ≠ 产物是对的。凡是动 manifest / 打包方式，
**必须 dump 产物的实际 manifest**，不能只看构建结果。这次就是靠 `aapt2 dump badging`
一眼看出三个字段全空。

**副作用，需要知悉**：`<uses-native-library>` 现在在 main manifest 里，所以**正式发布包
下次构建会多这一行**（惰性：不改权限、不改可安装性、不改任何行为）。磁盘上现存的
`OpenRailFanAI-0.1.6-arm64-release.apk`（9月17日）**未被触碰**。若要做到"只在 LM 轨声明"，
正确做法是引入真正的 product flavor（`assembleRelease` 会变成 `assemble<Flavor>Release`，
`build.sh` 要跟着改）——**不是** `manifest.srcFile()`。

---

## P6. 本地模型是常驻进程，用户不知道它在发热 ——**已实现（lm31）**

不是 bug，是被用户明确点出来的**产品缺失**：

> "还需要提醒用户卸载模型，否则后台一直开着会造成严重发热。"

llama-server 是常驻进程 —— 只要设备上下过模型，App 每次启动都会把它拉起来，
之后一直占着内存与 CPU 线程。手机没有风扇，散热全靠机身，表现是持续温热 + 掉电变快，
而用户不会把这两件事联系起来。

**已实现**：
- `status()` 新增 `uptime_s`（子进程拉起时刻到现在）；
- 设置页卡片：服务在跑就给一条说明；**≥10 分钟**转警告色 + 一个「立即停用」按钮；
- 首页引导条：在**回到前台**（`visibilitychange`）时刷新状态并提示。
  为什么不是启动时 —— App 一启动就把它拉起来了，"刚启动"永远显示 0 分钟，提醒永远不触发；
  而用户切回来时它可能已经跑了半小时；
- 「停用」不删文件（模型不占运行资源），要腾空间才需要「删除全部模型」（两步确认 + 服务端要求显式 `all=true`）。
