# Android 一体化版本

把 OpenRailFanAI 做成**一个自包含的 Android 应用**：Python 后端随包分发、在设备内运行，
UI 用**系统自带**的 `android.webkit.WebView` 承载既有前端 —— 不引入 Capacitor / Cordova /
React Native 之类的 Web 组件框架，也不依赖任何远程服务端。

## 形态与启动流程

```
APK
├── assets/webapp/…        前端静态资源（仓库 frontend/ 的副本）
├── assets/dict/dict.db    可选：本地数据字典（-PincludeDict=true 时才打）
├── assets/chaquopy/…      CPython 3.12 标准库 + 依赖（纯 Python）
└── lib/arm64-v8a/…        libpython3.12.so、OpenSSL、SQLite 等

启动：
  MainActivity
   1) 把 assets/webapp 与 assets/dict 解包到应用私有目录（带版本标记，升级后强制重解）
   2) 后台线程启动 Python：server.serve(activity, webapp_dir, data_dir)
   3) Python 侧在 127.0.0.1 的**空闲端口**上以 uvicorn 启动后端 app.main:app
   4) Python 侧**自检**：回环 GET `/` 与 `/src/main.js`，确认前端真的被托管
   5) 回调 onServerReady(port, 自检结论) → WebView 加载 http://127.0.0.1:port/
```

**为什么走本地 HTTP 而不是 `file://` 或 `WebViewAssetLoader`**：前端要用 `fetch` + SSE 调
`/api/*`，需要真实 origin；同源加载还让前端的相对路径（`window.__API_BASE__` 为空串）
无需任何改动即可工作 —— 与桌面/服务器部署完全同一份前端代码。

明文流量只对 `127.0.0.1` / `localhost` 放行（`res/xml/network_security_config.xml`），
没有使用 `android:usesCleartextTraffic="true"`（那会对全网放行）。

## 依赖改造（Android 能跑起来的前提）

Android 上没有 Rust 扩展的预编译轮子，因此下列依赖必须处理，否则装都装不上：

| 包 | 问题 | 处理 |
|---|---|---|
| `pydantic` | v2 依赖 `pydantic-core`（Rust），无 Android 轮子 | 全局锁 **v1**（`1.10.24+` 提供 `py3-none-any` 纯 Python 轮子） |
| `fastapi` | **0.126.0 起移除 pydantic v1 支持**（`fastapi/types.py` 开始 import 仅 v2 存在的 `pydantic.main.IncEx`） | 锁 `>=0.125,<0.126` |
| `pydantic-settings` | 要求 `pydantic>=2`，与上面的 v1 冲突 | 不安装；由 `backend/app/_compat.py` 提供替身 |
| `jiter` | `openai` 的依赖，Rust 扩展 | 不安装；由 `_compat.py` 提供替身 |
| `mcp` 及其依赖链 | 只服务于"把 12306 当 MCP 服务器跑"这条链路；本项目只 import 其 `services`/`utils` | `--no-deps` 安装 `mcp-server-12306`，整条 SDK 链不带 |
| `uvicorn[standard]` | `uvloop`/`httptools`/`watchfiles`/`websockets` 均是原生扩展 | 装不带 extras 的 `uvicorn` |
| `brotli` | 原生扩展 | 不装；`app/tools/_http.py` 已写明缺失时降级 gzip/deflate |

**兼容层（`backend/app/_compat.py`）的设计约束**：

1. **只在真包缺失时启用** —— 桌面/服务端若装有真包，行为完全不变；
2. 不把替身放成顶层 `jiter.py` 去"影子覆盖"真包，而是显式注册进 `sys.modules`；
3. 能力缺失时**报错而不是猜** —— 例如 `jiter` 的 `partial_mode` 直接抛
   `NotImplementedError`，因为它只被 openai 流式助手的 `.parsed` 增量解析使用，
   用 `json.loads` 硬凑会返回错误结果，比报错危险得多。

依赖闭包由 `android/requirements.txt` **显式锁定**（全部纯 Python），
构建时用 `options("--no-deps")` 安装 —— 这也是为什么该文件必须列全传递依赖。

## 构建

```bash
# 1) 一次性：把 JDK 17 + Android SDK + Gradle 装进工作区（自包含，不写系统目录、不需要 sudo）
bash scripts/android/setup-toolchain.sh

# 2) 出包
bash scripts/android/build.sh                    # debug 包（用调试密钥，可直接 adb install）
bash scripts/android/build.sh assembleRelease    # release 包（需先做签名，见下）
bash scripts/android/build.sh -PincludeDict=true assembleRelease   # 带上 14MB 本地字典
```

产物：`android/app/build/outputs/apk/<buildType>/`。

工具链全部落在 `.android-build/`（已 gitignore），删掉该目录即完全卸载。

### 签名（发布正式包必做）

```bash
bash scripts/android/make-keystore.sh            # 生成密钥库，只需一次
bash scripts/android/build.sh assembleRelease
```

密钥库与口令在 `.android-build/keystore/` 与 `android/keystore.properties`（均 gitignore，权限 600）。
**丢失密钥库后，已发布的包将永远无法升级**（签名不一致会被系统拒绝安装），请自行备份。

## 体积

以 arm64-v8a、不带本地字典为例，APK 约 **25MB**，构成：

| 组成 | 体积 |
|---|---|
| `libpython3.12.so` | 6.5 MB |
| CPython 标准库（`stdlib-common.imy`） | 4.2 MB |
| Python 依赖（`requirements-common.imy`） | 3.4 MB |
| OpenSSL（Chaquopy 为 `ssl`/`hashlib` 附带） | 4.2 MB |
| SQLite / 引导件 / cacert 等 | ~2 MB |
| 前端与代码 | < 1 MB |

轻量化手段：只打 `arm64-v8a`（原生库体积减半）、不引入 AndroidX/Kotlin/Compose、
不用 Web 组件框架、依赖表只留纯 Python 必需项、本地字典默认不打。

要继续变小：
- 用 Chaquopy 的 `exclude` 裁掉用不到的标准库模块；
- 若不需要 HTTPS 校验则去掉 OpenSSL（**不推荐**：12306 与 LLM 均需 TLS）；
- 按 ABI 出多包（App Bundle）而不是单包。

## 排障设计（白屏必须能自证原因）

真机排障拿不到 logcat，所以**任何失败都必须显示在屏幕上**，而不是留一片白：

| 层 | 手段 |
|---|---|
| Java | 启动过程做成**可见日志**（应用目录 / 解包文件数 / index.html 与 src/main.js 是否存在 / Python 启动 / 后端端口），失败时保留面板并显示原因与「重试」按钮 |
| WebView | `onReceivedError`（主框架加载失败）与 `onReceivedHttpError`（**静态资源 404 走这条**）都显示出来 |
| 前端 | `index.html` 内联脚本在任何模块之前注册 `error` / `unhandledrejection` 捕获，横幅显示"资源加载失败：<URL>"或脚本错误行号 |
| Python | 起服务后自检 `/` 与 `/src/main.js`；不通过则把结论 + 最近日志回传给界面 |
| Python | 启动**逐阶段回报**（选端口 / 准备 TLS / 导入 app / 导入 uvicorn / 起服务 / 自检），卡住时最后一条就是答案 |
| WebView | 页面加载完成后用 JS 把**实际渲染结果**读回日志（标题、消息区子节点数、错误横幅、引导条是否显示、正文摘录）——`onPageFinished` 只说明文档加载完，不代表渲染正确 |

### 真机（模拟器）自测

没有实体机也能完整验证，工具链同样装在 `.android-build/` 内：

```bash
bash scripts/android/setup-emulator.sh --avd     # 装 emulator + 系统镜像并建 AVD（约 1.7GB）
bash scripts/android/run-emulator.sh start      # headless 启动（约 6s 就绪）
bash scripts/android/run-emulator.sh install    # 装 APK 并前台抓 logcat
bash scripts/android/run-emulator.sh ui         # dump 界面文本
bash scripts/android/run-emulator.sh screenshot /tmp/shot.png
```

再配合端口转发就能直接调设备内的后端（验证整链而无需手点界面）：

```bash
adb forward tcp:<设备内端口> tcp:<设备内端口>
curl -s http://127.0.0.1:<端口>/api/providers
curl -s -XPOST http://127.0.0.1:<端口>/api/chat -H 'Content-Type: application/json' \
  -d '{"message":"G1今天由哪组动车组担当？","api_key":"...","base_url":"...","model":"..."}'
```

> 其中的 `/src/main.js` 检查是有来历的：静态资源 404 **不会**触发 WebView 的
> `onReceivedError`，只会让页面悄悄少掉全部脚本（看起来就是白屏）。首版在 assets
> 解包时把目录结构拍平（`webapp/src/main.js` → `webapp/main.js`），正是这个失败模式。
> `backend/tests/test_android.py` 现在会直接比对 APK 内资源与 index.html 的引用，
> 在构建产物层面挡住同类问题。

## 未配置模型时的引导

社区版不内置任何 API Key。前端启动后按 `/api/providers` 的 `llm_ready` / `mock` 决定是否显示引导条：
未配置 → 「尚未配置模型 API…去配置」直接跳设置页；Mock 模式 → 明确说明回答来自本地确定性规则而非真实模型。

## 已知限制

- **已在 Android 15 arm64 模拟器上实测通过**：启动各阶段、前端渲染（DOM 探针核对到完整界面文本）、
  设备内真实问答（12306 + rail.re + LLM 全链，返回正确担当车组）。仍未验证的是**实体机**上的
  长时间后台回收行为、以及不同厂商 WebView 版本的兼容性。
- **本地字典默认不打**，因此 `rail.mileage`、车站档案、离线时刻这类依赖字典的工具
  会如实报告不可用；需要完整功能请用 `-PincludeDict=true` 构建。
- 后端跑在应用进程内，**没有前台 Service**：切到后台久了可能被系统回收，
  回到前台需重新冷启动（表现为重新加载页面）。
- 目录访问等需要凭据的第三方数据源在移动网络下的可用性未做专门适配。

## 首次使用

应用不内置任何 API Key（社区版定位，也避免把 Key 随包分发被反编译提取）。
首次打开请在界面「⚙️ 模型」里选择供应商并填入**你自己的** Key（BYOK），
或先用 `LLM_MOCK=true` 方式体验整链。填写方式与桌面版完全一致，见 `docs/run.md`。

## 故障排查

| 现象 | 可能原因 |
|---|---|
| 卡在"正在启动本地服务…" | Python 侧启动失败，界面会显示 traceback；多为依赖缺失或数据目录不可写 |
| 白屏但已进入应用 | 前端静态资源未解包成功（检查 `filesDir/webapp/index.html`）或 `FRONTEND_DIR` 未生效 |
| 「未配置 LLM」 | 未在设置页填 Key；或 Key 被清空（未勾选"记住 Key"时重启会丢） |
| 实时查询全部失败 | 设备网络不通，或 12306 触发风控（与桌面版相同） |
| `includeDict=true` 报错 | `backend/data/dict.db` 不存在，先跑 `scripts/mirror_dict.py` |
