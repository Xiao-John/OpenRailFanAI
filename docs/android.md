# Android 一体化版本

## Main Android Compose 迁移状态

Main Android 已采用 Kotlin 与 Jetpack Compose。`MainActivity` 保留已安装应用的
launcher 组件身份，依据构建资源`main_native_ui`将 Main 转入未导出的
`MainComposeActivity`；原生界面承载聊天、历史及设置。九种状态已按用户确认的
整体协调优先标准验收，见`ui-acceptance-policy.md`与`acceptance/index.yaml`。
LM 的默认入口和独立页面不随此次迁移切换。Python/Chaquopy、FastAPI、铁路工具和
现有请求/SSE/display_results 协议保持复用。

Compose 版本固定为 Kotlin/Compose Compiler Gradle Plugin 2.2.21、Compose BOM 2025.08.00、
Activity Compose 1.10.1；当前 AGP 8.7.3、Gradle 8.11.1、compileSdk 35、minSdk 24、
Java 17、arm64-v8a 与 Chaquopy 17.0.0 保持不变。Compose 页面遵守
[`ui-design-boundaries.md`](ui-design-boundaries.md) 中的应用区域、系统栏排除与原生验收规则。

把 OpenRailFanAI 做成**一个自包含的 Android 应用**：Python 后端随包分发、在设备内运行，
Main UI 使用原生 Compose；LM 及保留的网页回退使用系统自带的`android.webkit.WebView`。
应用仍通过设备内服务调用用户配置的云端模型接口，不需要另行部署应用后端。

## 形态与启动流程

```
APK
├── assets/webapp/…        前端静态资源（仓库 frontend/ 的副本）
├── assets/dict/dict.db    可选：本地数据字典（-PincludeDict=true 时才打）
├── assets/chaquopy/…      CPython 3.12 标准库 + 依赖（纯 Python）
└── lib/arm64-v8a/…        libpython3.12.so、OpenSSL、SQLite 等

启动（MainActivity 按构建资源分流，Main 使用原生客户端连接）：
  MainActivity 或 MainComposeActivity
   1) 启动并绑定进程级 BackendService；Activity 不持有 Python 服务生命周期
   2) Service 解包 assets/webapp 与 assets/dict 到应用私有目录（带版本标记）
   3) Service 后台线程启动 Python：server.serve(host, webapp_dir, data_dir)
   4) Python 在 127.0.0.1 的动态端口以 uvicorn 启动 app.main:app，并运行服务自检
   5) Service 保存就绪端口与状态；旧 Activity 加载 WebView，Compose 通过回环 `/health` 探测
```

**为什么走本地 HTTP 而不是 `file://` 或 `WebViewAssetLoader`**：前端要用 `fetch` + SSE 调
`/api/*`，需要真实 origin；同源加载还让前端的相对路径（`window.__API_BASE__` 为空串）
用于保留的网页入口；Main 原生客户端也使用同一套 HTTP 与 SSE 协议。

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

> ⚠️ **发布包必须真的装到设备上跑一遍，不能用 debug 包代替。** 两者加载的资产不同，
> 差异足以让整包不可用：`-PincludeDict=true` 曾因为解包字典时清空了 `filesDir`
> （它与 `webapp/` 共用），把刚解包好的前端一起删掉 —— 结果是 release 包主页 404、
> 界面只剩 `{"detail":"Not Found"}`，而 debug 包（默认**不带**字典，压根走不到那段代码）
> 一切正常。验收最低标准：启动日志里 `/` 与 `/src/main.js` 都是 **HTTP 200**、
> 「页面自检」能读出完整界面文本、`本地字典：可用`（带字典构建时）。

### 签名（发布正式包必做）

```bash
bash scripts/android/make-keystore.sh            # 生成密钥库，只需一次
bash scripts/android/build.sh assembleRelease
```

密钥库与口令在 `.android-build/keystore/` 与 `android/keystore.properties`（均 gitignore，权限 600）。
**丢失密钥库后，已发布的包将永远无法升级**（签名不一致会被系统拒绝安装），请自行备份。

## 版本号（发新版必读）

**唯一来源是仓库根的 `VERSION` 文件**（纯文本，如 `0.1.2`）。同一条号决定：

| 用途 | 从哪来 |
|---|---|
| Android 的 `versionName` / `versionCode` | 构建时读 `VERSION`；`versionCode = 主*10000 + 次*100 + 修`（保证单调递增） |
| APK 文件名 | `dist/android/OpenRailFanAI-<VERSION>-arm64-<类型>.apk`（构建脚本自动归置） |
| 界面「设置 → 关于 → 版本」 | 打包时写入 `build.json`；桌面/自建部署读 `/api/version` |
| 侧栏版本角标 | 同上 |

> 为什么必须较真：同一个 `0.1.1` 曾经对应过好几个**内容不同**的包，用户无法判断自己装的是
> 哪一版，只能靠口头说明"请重新下载"；而 Android 自身的"应用信息"里也只有这个号。
> 界面上另外写死的 `v0.5`/`v0.6` 已全部移除 —— 同时出现两个号只会更乱。

发新版流程：

```bash
printf '0.1.3\n' > VERSION                    # 1) 升版本（唯一改动点）
bash scripts/android/build.sh -PincludeDict=true assembleDebug assembleRelease
# 2) 产物已自动归置到 dist/android/OpenRailFanAI-0.1.3-arm64-*.apk
```

构建脚本只归置**本次真的重新构建过**的产物：Gradle 对没变化的类型报 UP-TO-DATE，
那种 APK 还是旧内容，按新版本号复制过去就成了"同名不同内容"。
另外「关于」里还有一行**构建**（提交号 + 构建时间，工作区有未提交改动时带 `-dirty`），
用来直接对照"你装的"和"我说的"是不是同一个包。
`-dirty` 不是摆设：发第一个 0.1.2 包时它立刻显示 `8ab447a-dirty`，说明那个包是在提交**之前**
构建的，与 Release 里写的提交号对不上 —— 于是重新在干净树上构建再发布。

发布两条硬规则：

1. **每个版本新建一条 Release**（`v<版本>-debug`），不要在同一条 Release 里替换资产 ——
   GitHub 的资源下载走 CDN，同名替换后旧对象仍会被取到（实测：替换后下载到的还是旧 sha256，
   加个 cache-buster 才是新的）。新版本 = 新 URL，才不会有这个坑。
2. **只在工作区干净时构建待发布的包**，否则构建标记会带 `-dirty`。

### LM 轨（本地模型版封测，与正式版本线解耦）

本地模型版是**内部封测通道**，改动比正式线频繁得多。它**不能占用 `VERSION`**：
改了会让正式构建跟着跳号；沿用同一个号又会在 `dist/` 里留下"同名不同内容"的包
（正是上面那条规则要消灭的东西）。所以它自带前缀 + 自增数字：

```bash
bash scripts/android/build.sh --lm -PincludeDict=true assembleDebug
# → dist/android/OpenRailFanAI-lm1-arm64-debug.apk
#   Android versionName=lm1、versionCode=100001，「关于」里也显示 lm1
#   构建成功后 VERSION.lm 自动 +1，下次就是 lm2
```

| 项 | LM 轨 | 正式线 |
|---|---|---|
| 版本来源 | 仓库根 `VERSION.lm`（只有一个数字） | 仓库根 `VERSION` |
| 产物命名 | `OpenRailFanAI-lm<N>-arm64-<类型>.apk` | `OpenRailFanAI-<VERSION>-arm64-<类型>.apk` |
| `versionName` | `lm<N>` | `<VERSION>` |
| `versionCode` | **`100000 + N`** | `主*10000 + 次*100 + 修` |

Main 构建只打包云端版前端与后端；LM 构建会用 `android/lm-webapp/` 覆盖专用页面与脚本，
并打包设备端推理模块。本地模型 API 仅在 `APP_VARIANT=lm` 时注册。
直接运行桌面后端时默认是 Main；需要联调 LM 后端可显式设置该环境变量。

`versionCode` 取 `100000+N` 而不是 `N` 是**必须的**：测试者手机上已经装了正式线的
debug 包（0.1.6 → 106），用 1/2/3 计数会让 Android 直接拒绝安装"更低版本"的包。

计数只在**真的产出了 lm 包**时才 +1（构建失败或全部 UP-TO-DATE 都不跳号），
否则数字会飘 —— 而"靠数字彼此区分"正是这条轨的全部意义。

`VERSION.lm` 与 `VERSION` 一样是版本真相，**要提交进仓库**。

### 内部调试轨（`--internal`，lm1000 起）

```bash
bash scripts/android/build.sh --internal -PincludeDict=true assembleRelease
```

比封测轨"更自由、更透明"的一档，专门给调试用：

| 项 | 内部调试轨 | 封测轨（`--lm`） |
|---|---|---|
| 版本来源 | `VERSION.lminternal`（**从 1000 起**） | `VERSION.lm`（1 起） |
| 产物命名 | `OpenRailFanAI-lm1000-arm64-release.apk` | `OpenRailFanAI-lm37-…` |
| `versionCode` | `100000 + N` ⇒ **101000 起** | `100000 + N` ⇒ 100037 |
| 包名 | **`org.openrailfanai.app.internal`**（独立） | `org.openrailfanai.app` |
| `debuggable` | **true**（`adb shell run-as` 可进私有目录） | false |
| 附加权限 | `MANAGE_EXTERNAL_STORAGE` | 无 |
| 设置页 | 多一个**原始 llama-server 参数**框 | 无 |

**为什么独立包名而不沿用同一个（否则 versionCode 更高就能直接覆盖升级）**：
同包名一旦装了 lm1000，**再装 lm37 会被系统拒绝**（版本号更低），要回封测轨必须先卸载 ——
而卸载会丢掉全部对话。独立包名让两条轨各自独立递增、并存、随时切换，代价只是多一个图标。

**为什么要 `MANAGE_EXTERNAL_STORAGE`**：两个包名是两个应用，Android 11+ 禁止应用读
**其它包**的 `Android/data` 目录 —— 于是调试版看不到封测版已经下好的 2B/4B（1.2–2.6 GB）。
拿到这个权限后，`local_inference` 会把兄弟包的模型目录加进**只读**候选（`readable_dirs()`），
**原地使用：不拷贝、不重下**。它会主动去系统设置里申请一次；不授权也照常可用，只是退回重下。

⚠️ 这个权限**只由构建时注入**（`build.gradle.kts` 末尾），源 `AndroidManifest.xml` 里**没有**它 ——
正式轨与封测轨的 merged manifest 因此**从构造上**就碰不到。别改成在源文件里声明：
更"正统"的三种写法都实测排除过（`manifest.srcFile()` 是替换不是追加、占位符不作用于
`tools:` 属性、product flavor 会让 `assembleRelease` 这个任务名消失），细节写在 gradle 文件末尾的注释里。

**原始参数框**的意义：llama.cpp 有几十个开关（`-fa` / `-ctk` / `-ctv` / `--device` …），
每加一个下拉都要改后端+前端+出包 —— 而调试要的是**立刻能试**。后端把它 `shlex.split`
后作为**参数列表**交给 subprocess（**不经过 shell**，所以 `;`/`&&` 没有执行语义），
并**追加在命令行末尾**（llama.cpp 后者覆盖前者，用户写的确实说了算）。


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

## 深浅色跟随系统（改主题前必读）

前端是"跟随系统"的三档主题（跟随系统 / 浅色 / 深色，见 `frontend/src/theme.js` 与设置页「外观」）。
在 Android 上它**不是**靠系统夜间模式就直接生效的，有两个坑都实测踩过：

### 1. `prefers-color-scheme` 由**应用主题的 `isLightTheme`** 决定，与系统夜间模式无关

> WebView 始终根据应用主题属性 `isLightTheme` 设置媒体查询 `prefers-color-scheme`
> （即 `light`；`isLightTheme` 为 true 或未指定）… 否则为 `dark`。
> —— [`WebSettings.setAlgorithmicDarkeningAllowed`](https://developer.android.com/reference/android/webkit/WebSettings#setAlgorithmicDarkeningAllowed(boolean))

原来的 `AppTheme` 用的是 `Theme.DeviceDefault.NoActionBar` —— AOSP 里那是**深色**变体
（`isLightTheme=false`），于是页面**永远**收到 `prefers-color-scheme: dark`。
真机实测（模拟器 Android 15）：`cmd uimode night no`、`dumpsys activity` 的
`mCurrentConfig` 里也没有 `night` 限定符，WebView 仍然报 `dark` —— 前端"跟随系统"
因此永远跟随不上，只有写死的深色。

修法（**两套父主题都要改，改一处不算改**）：

| 文件 | 父主题 | 何时生效 |
|---|---|---|
| `res/values/styles.xml` | `Theme.DeviceDefault.Light.NoActionBar` | 浅色 |
| `res/values-night/styles.xml` | `Theme.DeviceDefault.NoActionBar` | 深色 |

用 `values-night` 而不是 `Theme.DeviceDefault.DayNight`：后者要 API 29+，而本应用 `minSdk` 是 24。
两个文件里 `windowBackground`（黑）与 `windowLightStatusBar/windowLightNavigationBar`（false）
**必须一致**：启动日志面板始终是黑底浅字、系统栏区域始终露出窗口底色（Java 侧把内容按
insets 缩进了，不绘制到系统栏底下），所以那里永远是深的，图标必须用浅色 ——
不写 `windowLightStatusBar=false` 的话，Light 父主题默认给深色图标，在黑底上等于看不见。

`onCreate` 里不需要做任何"根据夜间模式选颜色"的逻辑：主题换了，WebView 的媒体查询跟着换。

### 2. 系统实时切换是能跟上的（不需要重启应用）

`AndroidManifest.xml` 里 MainActivity 声明了 `configChanges` 含 `uiMode`，Activity 不会重建；
但实测 Chromium 会随配置变化更新媒体查询，前端 `matchMedia(...).addEventListener("change")`
（`theme.js` 的 `watchSystemTheme`）因此能实时收到 —— 模拟器上不重启应用切换
`cmd uimode night yes|no`，`data-theme` 与页面底色都跟着变。

### 3. 老用户的迁移

旧版本每次启动都会把 `"dark"` **自动写进**偏好（而当时界面上根本没有主题入口），
不清掉的话"跟随系统"对升级用户永远不生效，表现为"新装的人是浅色、升级的人还是黑的"。
`store.migrateTheme()` 用 `railfan_theme_v2` 标记做一次性清理；`index.html` 的首帧内联脚本
用同一个标记键做**只读**判断（首帧阶段不落盘），避免升级后第一次启动先黑一下再变白。

## 原生桥（`window.RailNative`）

WebView 上有三件事纯网页做不好，所以 MainActivity 里挂了一个自己的小桥
（`addJavascriptInterface`，**零依赖**；契约见 `frontend/src/native.js`）：

| 问题 | 纯网页的处境 | 桥的做法 |
|---|---|---|
| 状态持久化 | localStorage 按**源**隔离，而源含端口。后端端口一变（上次那个被别的 App 占了就换），应用在浏览器眼里就是另一个站点 —— 对话/配置/记住的 Key 全部读不到 | 状态写进应用私有目录的 `state.json`，与端口无关 |
| 粘贴 API Key | `navigator.clipboard.readText()` 在 WebView 里不可靠（要权限 + 用户手势） | `readClipboard()` 走系统剪贴板 |
| 分享 | 没有系统分享入口 | `shareText()` 调起系统分享面板 |
| Key 的静态加密 | 明文躺在 localStorage | `securePut/Get` 走 **Android Keystore**（AES-GCM），加密密钥永不出密钥库 |

**为什么不用 Capacitor 之类**：本项目需要的只有上面这几项，自己写百来行就能覆盖；
引一整套运行时还要在构建链里插 `npx cap sync`，而且**解决不了核心那条**（源随端口变化）。

设计要点：

- **同步**。`@JavascriptInterface` 的返回值同步回到 JS，所以 `store.js` 现有的同步读写
  模型一行都没改。也正因为同步会阻塞页面，写盘做了**合并**（250ms 内的多次写入只落一次），
  并在 `visibilitychange`/`pagehide` 时立即补写。
- **原子落盘**：先写 `.tmp`、`fsync`、再 `renameTo`。应用被系统在写到一半时回收
  （移动端很常见）不会留下半个 JSON —— 那会让下次启动读到损坏状态，用户看到"数据全没了"。
- **优雅降级**：桥不存在时（桌面浏览器、node 单测、未接桥的旧包）`native.js` 全部退化为
  安全空实现，`store.js` 退回 localStorage，调用方不需要到处写 `if (android)`。
- **Key 与状态分文件**：`secrets.json` 只放密文，`state.json` 里一个 Key 都不留；
  取消勾选「记住 Key」、删除条目、清空配置时都会**真的删掉**密钥库里的副本。
- 密钥库被重置（刷机/清除数据）后旧密文解不开，此时如实返回空让用户重填，而不是抛异常把设置页带崩。

> ⚠️ **安全前提**：桥对 WebView 里加载的**任何页面**都可见。本项目外链一律交给系统浏览器
> （见 `openExternally`），WebView 永远停在 `127.0.0.1` 上，所以不存在陌生页面调用本桥的路径。
> **将来若允许 WebView 内打开第三方页面，必须先重新评估这个前提。**
> `backend/tests/test_android.py` 会把这条前提和"两边方法名一致"一起钉住
> —— 桥是**按方法名反射**暴露的，名字写错既不报错也不抛异常，只会"点了没反应"。

启动自检的日志里会带 `bridge` 字段（`android` 或 `缺失`）：桥没接上时前端会**静默**退回
localStorage，界面上完全看不出来，所以把它显式打进日志。

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
| WebView | `target="_blank"` 与一切非 `http://127.0.0.1` 的链接**交给系统浏览器**：需要 `setSupportMultipleWindows(true)` + `onCreateWindow` 接管，否则在 WebView 里点外链毫无反应（设置页的"在 GitHub 上打开仓库"就是这条路径） |

### 真机（模拟器）自测

没有实体机也能完整验证，工具链同样装在 `.android-build/` 内：

```bash
bash scripts/android/setup-emulator.sh --avd     # 装 emulator + 系统镜像并建 AVD（约 1.7GB）
bash scripts/android/run-emulator.sh start      # headless 启动（约 6s 就绪）
bash scripts/android/run-emulator.sh install    # 装 APK 并前台抓 logcat
bash scripts/android/run-emulator.sh ui         # dump 界面文本
bash scripts/android/run-emulator.sh screenshot /tmp/shot.png
```

再配合 CDP 在设备上执行任意 JS（debug 构建自动开启 WebView 远程调试）：

```bash
python3 scripts/android/cdp.py --targets                     # 列出可调试页面
python3 scripts/android/cdp.py "document.title"              # 执行任意 JS
python3 scripts/android/cdp.py --eval-file /tmp/probe.js     # 复杂脚本写文件
python3 scripts/android/cdp.py --tap-sel "#menu-btn"         # 真的点一下（按元素中心）
python3 scripts/android/cdp.py --tap 206,400                 # 或按页面坐标点
python3 scripts/android/cdp.py --screenshot /tmp/s.png       # 由渲染器截图
```

> ⚠️ **别用 `adb exec-out screencap` 判断布局**：模拟器软件渲染下它抓的是合成器
> 的最后一帧，可能明显滞后（本项目实测拿到过"引导条只画了一半、页头按钮还没出现"
> 的中间态，据此误判成布局 bug）。`--screenshot` 走渲染器，与 DOM 测量同一时刻，
> 两者交叉验证才可靠。

关于"点击"，有两条血泪教训，都曾导致**完全错误的结论**：

1. **先确认 App 真的在前台。** `adb shell input tap` 只投递给**当前前台窗口**。
   本项目曾出现过程序还活着（CDP 能读到实时 DOM、WebView 一直在渲染）但前台已经
   回到 launcher 的情况 —— 此时所有点击都被 launcher 吃掉，界面上"点什么都没反应"，
   于是被误判成"页头 105px 触摸死区"。真相是：app 在后台 + 点的还是安全区留白。
   判断方法：`adb shell dumpsys window | grep mCurrentFocus`。
   拉回前台要写**完整组件名**，注意 debug 包名带后缀而类名不带：
   `adb shell am start -n org.openrailfanai.app.debug/org.openrailfanai.app.MainActivity`。
   （同一后台状态下，点击外链还会被 Android 15 的 BAL 拦截，日志里是
   `Background activity launch blocked!`，很容易让人以为代码没接上。）

2. **要点击就用 `cdp.py --tap/--tap-sel`，它走渲染器输入管线，顺带做了命中测试。**
   注意 WebView 的 `Input.dispatchTouchEvent` 是**静默失效**的（不回包、不报错、页面
   毫无反应），必须用 `Input.dispatchMouseEvent`，`tap()` 已按此实现。
   验证"能不能点到"不能只看坐标：`document.elementFromPoint(x,y)` 能一眼看出
   那个点上究竟是按钮还是它上面的浮层/遮罩（侧栏展开时 `#menu-btn` 就会被盖住）。

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

## 设备端本地推理（LM 轨）

**API 花费恒为 0** 的那条路：模型在手机上跑，不上云。前提是把 llama.cpp 的
`llama-server` 交叉编译进包 —— 只出配置是没用的，设备上真正算 token 的是这个二进制。

```bash
bash scripts/android/build-llama.sh                              # 交叉编译（约 3–6 分钟）
bash scripts/android/build.sh --lm -PincludeDict=true assembleDebug
```

### 为什么二进制必须叫 `libllamaserver.so`

不是笔误，是 Android 的硬要求，且**与一个 Gradle 开关成对**：

| | 位置 | 作用 |
|---|---|---|
| 命名成 `lib*.so` 放 jniLibs | `scripts/android/build-llama.sh` | 只有被当作**原生库**打包的文件才会落到只读的 `nativeLibraryDir` |
| `useLegacyPackaging = true` | `build.gradle.kts`（**仅 LM 轨**） | 否则原生库只在 APK 内 mmap、**不落地成真实文件**，照样 exec 不了 |

Android 10+ 的 W^X 禁止从可写目录 exec 二进制，**只有 `nativeLibraryDir` 允许**。
这两处改一个必须改另一个，否则表现为"二进制明明打进包了，就是起不来"。

正常版本线不受影响：`useLegacyPackaging` 仍然是 `false`，`src/lm/jniLibs` 也不参与它的构建。

### 模型怎么装上去：两条路

**A. 应用内一键下载（给封测用户，推荐）**

设置页 →「本地模型（免 API Key）」→ 选档 →「一键下载并启用」。
下载带进度、支持**断点续传**（GB 级下载在移动网络上必然遇到停顿，实测第一版
下到 342/507 MB 超时后整包作废），完成后自动拉起 `llama-server`，再点
「设为当前使用」即接入对话。

| 档位 | 体积 | 说明 |
|---|---|---|
| Qwen3.5-0.8B | 508 MB | 最快；判断力弱，适合先跑通链路 |
| Qwen3.5-2B | 1.2 GB | 8G 内存推荐档 |
| Qwen3.5-4B | 2.6 GB | 16G 旗舰推荐档（意图准确率追平云端，但更慢） |

**B. adb 推（给开发机）**

```bash
bash scripts/android/push-model.sh /path/to/Qwen3.5-2B-Q4_K_M.gguf
bash scripts/android/push-model.sh --list
```

**为什么别手敲 `adb push` 到外部应用目录**：实测在 Android 15 上，
`adb push` 到 `/sdcard/Android/data/<pkg>/files/models/` 会**成功**，但目录属主是 `shell`，
App 侧一律 `Permission denied`（emulated storage 的合成权限，不是 SELinux）。
同一个坑在一个会话里咬了两次：先是"文件在那儿却说没找到模型"，后是"下载跑到写文件才失败"。
现在 `writable_dir()` **逐候选做写测试**，写不进去就自动回落到内部 `filesDir/models`。

### 模型怎么放上去

模型 1.2–2.5 GB，**不进 APK**。用脚本推（别手敲 adb push，原因见下）：

```bash
bash scripts/android/push-model.sh /path/to/Qwen3.5-2B-Q4_K_M.gguf
bash scripts/android/push-model.sh --list          # 看设备上已有哪些模型
```

**为什么不直接 `adb push` 到外部应用目录**：实测在 Android 15 上，
`adb push` 到 `/sdcard/Android/data/<pkg>/files/models/` 会**成功**，但目录属主是 `shell`，
App 侧 `ls` 直接 `Permission denied`（emulated storage 的合成权限，不是 SELinux）。
现象是"文件明明在那儿，App 却说没找到模型"——极难排查。
脚本因此统一走 `run-as <pkg> cp` 写内部 `filesDir`（**只对 debug 包有效**，LM 轨正好是 debug）。
`local_model.py` 两个位置都会找，外部目录优先，内部兜底。

启动时 `local_model.py` 自动发现模型并拉起 `llama-server`
（`--host 127.0.0.1 --port 8081`），再把 `LLM_PROVIDERS` 指过去 —— pipeline 零改动。

### 关思考是**必需项**，不是优化

实测（llama-server + Qwen3.5-0.8B，`max_tokens=64`，问"G1 始发站"）：

| 关闭姿势 | 正文 | 思考 | finish |
|---|---|---|---|
| 什么都不传 | **0 字** | 202 字 | `length` |
| **`reasoning_effort: "none"`** | 15 字 | 0 字 | ✅ `stop` |
| `enable_thinking: false` | **0 字** | 216 字 | `length`（**被静默忽略**） |

**不关思考 = 思考吃光输出预算、正文一个字都没有**，整条生成链路不可用。
所以 `local_model.py` 会强制置上 `LLM_GENERATION_NO_THINK=true` 与
`LLM_STRUCTURED_NO_THINK=true`（两者管的是不同路径）。

### 时间与超时：必须按供应商放宽

实测（Android 15 arm64 模拟器、Qwen3.5-0.8B、6096 token 提示词）：

| | 数值 |
|---|---|
| prefill | 6096 tok @ 39 tok/s = **156 s** |
| decode | 173 tok @ 10.6 tok/s = 16 s |
| 合计 | **172 s** |

**设备端慢在 prefill 上，而不是 decode。** 而全局默认 `LLM_TIMEOUT_S=60` 是给云端定的 ——
于是本地生成必然超时，且失败方式是"语言模型当前不可用 → 降级到规则排版"，
**用户完全看不出是超时**。

所以设备端供应商带 `timeout_s = 600`（`Provider.timeout_s`，可按供应商配）。
注意模拟器跑在软件渲染 + HVF 上，比真机慢得多；真机数字必须自己测。

### 生成必须关思考（供应商级）

全局开关 `LLM_GENERATION_NO_THINK` 在进程启动时就冻结了（`get_settings()` 带 lru_cache），
而设备端模型是**运行期**才装上的 —— 用户点"一键下载"之后服务才起来，此时改环境变量已经无效![。]()

所以设备端供应商带 `always_no_think = True`（`Provider.always_no_think`），
**压过调用方的 `no_think=False`**。不这么做的话，"下载完自动启用"这条路会带着思考跑，
而实测不关思考的后果是**正文一个字都没有**。

### 实测（Android 15 arm64 模拟器，Qwen3.5-0.8B）

| 环节 | 结果 |
|---|---|
| 二进制落地 | `lib/arm64-v8a/libllamaserver.so` 13,376,232 B、`-rwxr-xr-x`（真文件，可 exec） |
| 拉起 | App 从 `nativeLibraryDir` 起子进程成功，进程在 App 自己的 UID 下 |
| 模型加载 + 健康检查 | **1.1s** 就绪 |
| 解码速度 | **84.4 tok/s**（注意：模拟器跑在 M5 CPU 上，这个数是**主机**的，不代表手机） |
| 端到端问答 | 快路径 23ms 命中 → `rail.mileage` 检索 → 本地生成，答"京沪高速线全长 1318.0 公里"并附来源，`thinking` 为空（思考确实关掉了） |
| API 花费 | **0** |

仍未验证：**实体机**上的解码速度与常驻内存（模拟器数字不能外推）；Adreno GPU 卸载
（本版是 CPU-only，见 `build-llama.sh` 顶部说明）。

## 生成质量：小模型会退化成复读（2026-09-21 实测）

同一条**真实生成提示词**（2073 字）下的四方对照：

| 模型 | 输出 | 重复率 | 同一片段最多重复 | 内容 |
|---|---|---|---|---|
| **0.8B** | 1200（撞上限，不会自己停） | **97%** | **68 次** | **编造站名** |
| 2B | 205 | 0% | 1 次 | 正确 |
| 4B | 234 | 0% | 1 次 | 正确 |
| 云端 | 119 | 0% | 1 次 | 正确 |

0.8B 把杭州/宁波/绍兴/湖州/嘉兴当成京沪线上的站编了出来（检索事实里只有 6 站），
然后陷进自己编的序列里出不来。**这不是"判断力弱一点"，是会编造数据且不会自己停。**

三条对策，都已落地：

1. **撤掉 0.8B 档位**（`MODEL_CHOICES`）。**设备端的可用性下限是 2B。**
   对已经下载过 0.8B 的人，`status()` 会给出 `model_warning`，不能只是"列表里消失"。
2. **精简生成提示词**（`LLM_GENERATION_COMPACT_PROMPT`，默认关）。
   长提示词不是"信息更多"，对这个小尺度是**过载**：它会去逐条应付读不完的规则，
   最后抓住一条反复复读。换成精简版后**同一个 0.8B** 输出 61 token、重复率 0%。
   1950 → 590 字（降 70%），顺带把设备端 prefill 砍到 ~1/2.5。
3. **重复循环兜底**（`app/pipeline/repetition.py`）。边生成边查（每 128 字一次，单次 0.006 ms），
   检测到就**停流**并把已吐出去的内容用一次 `replace` 事件收回、截断、如实告知。
   兜底不依赖模型自律 —— 2B 现在不循环，不等于以后不会。

截断提示按**原因**分文案：`length`（调大输出上限可解决）与 `repetition`
（调大只会让它重复更久）对用户的意义完全相反，混成一句话会把用户引向错误操作。

## 已知限制

- **已在 Android 15 arm64 模拟器上实测通过**：启动各阶段、前端渲染（DOM 探针核对到完整界面文本）、
  设备内真实问答（12306 + rail.re + LLM 全链，返回正确担当车组）、**原生桥全链**
  （剪贴板往返、Keystore 写读、**强行换端口后对话与配置仍在**）。**release 包（带字典）也已
  全新安装验证**：前后端自检均 HTTP 200、界面完整渲染、`本地字典：可用`。
  仍未验证的是**实体机**上的长时间后台回收行为、以及不同厂商 WebView 版本的兼容性。
- **本地字典默认不打**，因此 `rail.mileage`、车站档案、离线时刻这类依赖字典的工具
  会如实报告不可用；需要完整功能请用 `-PincludeDict=true` 构建（该开关曾整包失效，
  见「构建」下的注意事项）。
- 后端跑在应用进程内，**没有前台 Service —— 这是刻意的产品决策，不是遗漏**：
  不为一个聊天应用去向用户索要保活 / 自启动权限。代价是切到后台久了进程会被系统回收，
  回到前台需重新冷启动（表现为重新加载页面），**正在生成的长回答会被中断**。
  在不申请任何权限的前提下已把损失收到最小：流式途中约每 1.5 秒把已生成的正文落一次盘，
  并给该条消息打上 `meta.streaming` 标记；进程被杀后重新打开，看到的是
  **当时已生成的部分 + 一句如实说明**，而不是一个空气泡。
  要彻底不受影响，只能把生成搬到应用进程之外（瘦客户端 / 远程后端）—— 那等于放弃"一体化"。
- 只打 arm64-v8a（`minSdk 24`）：32 位老机装不上，需要时在 `build.gradle.kts` 的
  `abiFilters` 里追加 `armeabi-v7a`（APK 体积会明显变大）。
- 目录访问等需要凭据的第三方数据源在移动网络下的可用性未做专门适配。

## 首次使用

应用不内置任何 API Key（社区版定位，也避免把 Key 随包分发被反编译提取）。
首次打开请在界面「⚙️ 设置」里选择供应商并填入**你自己的** Key（BYOK），
或先用 `LLM_MOCK=true` 方式体验整链。填写方式与桌面版完全一致，见 `docs/run.md`。

## 故障排查

| 现象 | 可能原因 |
|---|---|
| 界面正常，但顶部（顶栏「☰」「⚙️」、侧栏「＋ 新对话」）**怎么点都没反应** | 平台的默认主题带来了一条看不见的 ActionBar，它叠加在 WebView 之上吃掉顶部约 275px 的触摸。已在 `res/values/styles.xml` 里声明 `NoActionBar` 主题修掉；若又出现，先看清单里 `android:theme` 是否还在（`test_android.py` 有断言钉住）。注意只加 `env(safe-area-inset-top)` 解决不了——那只解决观感，不解决触摸 |
| 卡在"正在启动本地服务…" | Python 侧启动失败，界面会显示 traceback；多为依赖缺失或数据目录不可写 |
| 白屏但已进入应用 | 前端静态资源未解包成功（检查 `filesDir/webapp/index.html`）或 `FRONTEND_DIR` 未生效 |
| 界面上点什么都没反应 | 先用 `dumpsys window \| grep mCurrentFocus` 确认 App 是否真在前台；不在前台时点击全被 launcher 接走（用 CDP 点击则不受影响） |
| 点外链没反应 / 日志报 `Background activity launch blocked!` | 同样是 App 不在前台时 Android 15 拦下了外部 Intent；代码路径本身没问题（日志会有「已在系统浏览器打开」） |
| 「未配置 LLM」 | 未在设置页填 Key；或 Key 被清空（未勾选"记住 Key"时重启会丢）；刷过机/清过应用数据后密钥库被重置，旧 Key 解不开（会如实让你重填） |
| 对话/供应商配置不见了 | 先看启动日志里自检的 `bridge` 字段：显示 `缺失` 说明桥没接上、前端退回了 localStorage —— 那是跑到了没接桥的旧包。正常应为 `android`（数据在 `filesDir/state.json`） |
| 实时查询全部失败 | 设备网络不通，或 12306 触发风控（与桌面版相同） |
| `includeDict=true` 报错 | `backend/data/dict.db` 不存在，先跑 `scripts/mirror_dict.py` |

### Main 原生启动与回退（2026-10-03）

普通桌面入口`MainActivity`在 Main 构建中转入`MainComposeActivity`；反复点击桌面图标复用原生页面。LM 构建的资源开关为 false，保持原 WebView 路径。Main 的原生页只有设备内服务健康检查成功后才允许发送请求。

保留 WebView 用于排障，可使用显式启动意图附加布尔值`railfan_web_fallback=true`。不新增用户界面的切换按钮。正式包的 Compose Activity 仍未导出；调试包的显式自动化入口保持调试专用。

实时铁路查询不包含在可重复的九状态视觉夹具中，需显式传入测试参数`liveRailwayQuery=true`执行`ComposeScheduleQueryTest`。其失败不能被夹具通过替代。

Compose 1.9 自带的静态检查要求 Lint 8.8.2。项目在`gradle.properties`单独固定此检查器版本，AGP仍为8.7.3；没有关闭发布包检查，也没有因此升级应用运行依赖。参见[官方 Compose Runtime 1.9 发布说明](https://developer.android.com/jetpack/androidx/releases/compose-runtime#1.9.0)及[Lifecycle 静态检查兼容说明](https://developer.android.com/jetpack/androidx/releases/lifecycle#2.9.0-rc01)。

Main 启用`desugar_jdk_libs:2.0.3`，为`minSdk 24`提供原生日历及历史分组使用的`java.time`兼容实现；LM 构建不启用此运行依赖。参见[官方 Java API 兼容支持说明](https://developer.android.com/studio/write/java8-support)。当前运行验证设备为 API35，未把编译兼容声明为 API24 真机测试通过。
