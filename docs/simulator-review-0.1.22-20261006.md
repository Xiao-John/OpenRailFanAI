# 模拟器实测 review：Main 0.1.22 功能实现情况

日期：2026-10-06。范围：**Main 正式轨（非 LM）**、`VERSION=0.1.22` 的 Release 包。
方法：真实安装到模拟器、配置真实 BYOK 模型、走真实 12306 / rail.re / GitHub 链路，逐项核对 `docs/` 中声明的功能，而不是只做静态检查。

## 结论摘要

- **0.1.22 是一个能用的包**：可安装、可冷启动、可完成真实端到端查询；`build-0.1.22-local-20261006.md` 自认的"未运行模拟器、真机或端到端测试"在本次被补齐，**未发现阻断级或数据错误级缺陷**。
- 文档中此前标注"仅编译检查 / 未实测"的功能，本次实测**多数成立**：票价与余票合并卡片、执行票价口径、能力声明门控投递、字典更新实时进度、应用内软件更新检查、错误分类。
- 发现 **4 项需要修的缺陷**（1 项中高、2 项中、1 项低）与若干观察项，详见第 3 节。
- **最需要修的一项**：全新安装首次启动时启动日志误报 `本地字典：不可用`，与 `docs/android.md:91-92` 的发布验收最低标准直接冲突；功能本身是好的，坏的是自证手段（首次安装的验收会被误判为失败）。

## 1. 环境与证据

| 项 | 值 |
|---|---|
| APK | `dist/android/OpenRailFanAI-0.1.22-arm64-release.apk`，39,559,176 B，SHA-256 `fc15122469de…d8f1b`（与同名 `.sha256` 一致） |
| 包名 / 版本 | `org.openrailfanai.app`，versionName 0.1.22，versionCode 122（设置页显示 `当前安装版本 0.1.22`） |
| 模拟器 | AVD `railfan_test`（API 35、arm64-v8a、软件渲染），serial `emulator-5554`，`wm size 390x844` / `density 160` |
| 设备内后端 | `127.0.0.1:47485`（经 `adb forward tcp:18080` 访问） |
| 模型 | 设置页 BYOK：硅基流动 SiliconFlow + `.env` 中的真实 Key（未勾选"记住 API Key"） |
| 网络 | 设备可直连 12306、rail.re、api.github.com（宿主机到 github.com 反而不通） |

证据目录：`.ai/review-20261006/`

- `ui/`：60+ 张 390×844 截图 + 逐步 UI 文本转储，其中 `fare-statement-counts.json` 记录说明文案的逐屏出现次数
- `api_*.json`：设备内后端的请求/响应原文（已把 Key 替换为 `<api-key>`）
- `query_battery.py`：7 条真实问句 + 1 条无效 Key 的批量核对脚本
- `ui_walk.py`：adb 驱动的界面走查工具

## 2. 逐项核对结果

### 2.1 安装、启动与自证

| 文档声明 | 实测结果 | 证据 |
|---|---|---|
| 启动自检 `/` 与 `/src/main.js` 均 HTTP 200 | ✅ `自检通过：/ → HTTP 200（4000B）；/src/main.js → HTTP 200（4000B）` | logcat；`server.py:121` 只读 4000B，两个 4000B 是显示上限而非截断 |
| 正式版携带本地词典 | ✅ APK 内含 `assets/dict/dict.db` 14,118,912 B；运行时解压到 `files/bundled-dict/dict.db` 并由后端合并 | `unzip -l`；`BundledDictionary.java:15`；`BackendService.java:124,131` |
| `本地字典：可用`（发布验收最低标准） | ⚠️ **首次启动误报"不可用"**，冷重启后才正确 | 见缺陷 **D1** |
| Main 走原生 Compose、LM 走 WebView | ✅ 安装后直接进 `MainComposeActivity`，界面为 Compose 原生页 | `dumpsys window`；`android.md:590-598` |
| 用户确认的状态栏/设备示意不实现 | ✅ 未见应用绘制模拟系统栏；页面留白保留 | `ui/fare-card-complete.png` |

### 2.2 原生客户端（Compose）

| 功能 | 实测结果 | 证据 |
|---|---|---|
| 空对话示例引导：点击填入草稿、不直接发送 | ✅ 点"查票价"→输入框出现"明天北京南到上海虹桥的 G1 票价"，未发送 | `ui/new-conversation.png`、`ui/schedule-draft.png` |
| 顶栏：设置 / 对话历史 / 新建对话（无返回键） | ✅ 三点均为 48×48dp 可点区 | 各步 UI 转储 |
| 历史抽屉：单行标题、分组、重命名、删除确认 | ✅ 标题单行、`今天` 分组、菜单 `重命名`/`删除对话`、确认文案 `确定删除「…」？该操作不可恢复。` | `ui/history-drawer.png`、`ui/history-rename-dialog.png`、`ui/history-delete-dialog.png` |
| 新建入口复用空白会话，不产生多个空会话 | ✅ 连续点击 `新建对话` 不新增会话，出现 Toast（`MainComposeActivity.kt:270`） | 行为核对 + `ui/new-conv-toast.png` |
| 操作区：复制 / 重新生成 / 分享（每条回复一次、44dp） | ✅ 三条回复各一份；分享调起系统面板且内容含正文+错误详情 | `ui/share-sheet.png` |
| 复制真的写入系统剪贴板 | ✅ 复制后到设置页点"粘贴"→ `已填入密钥。`（剪贴板确实有内容） | `ui/clipboard-paste-probe.png` |
| 状态持久化（对话/供应商）跨冷启动 | ✅ 强杀重启后对话与供应商配置都在 | `ui/`（重启后界面完整） |
| 不记住 API Key = 不落盘 | ✅ 重启后供应商/地址/模型保留，**API Key 字段为空**（`填写 API Key`） | 重启后设置页转储 |
| 主题枚举下拉（展示全部选项，非轮换）+ 深色渲染 | ✅ 展开 `跟随系统/浅色/深色`，选深色整体变深，可还原 | `ui/theme-dropdown.png`、`ui/theme-dark.png` |
| 连接测试就地反馈 | ✅ `测试连接` → `连接成功，可以使用当前模型。`（后端日志 200 OK） | `ui/`（设置页） |
| API Key 遮蔽 | ✅ 字段以 `•••` 显示 | 设置页转储 |
| 回到底部浮动按钮 | ✅ 离开底部时出现，回到底部后消失 | `ui/fare-card-full.png` |
| Token 用量 | ✅ 工具类回复为 `0 Token`（后端 `usage` 确为 0，准确）；累计 `本对话累计 0 Token` 一致 | `api_*_summary.json` |

### 2.3 票务：票价 + 余票

| 文档声明 | 实测结果 | 证据 |
|---|---|---|
| 结构化 `ticket_fare`（schema_version 1）含身份/区间/时刻/历时/席别金额/来源/采样 | ✅ 设备内后端返回完整结构 | `api_fare_single.json` |
| 执行票价口径 `fare_basis=executed` → "实际执行票价" | ✅ 卡头徽标显示 `实际执行票价`，且"绝不回退公布票价"在离线失败时也成立 | `ui/fare-card-complete.png`；`ui/error-card-5s.png` |
| 票价与余票合并卡片（席别并集） | ✅ 表头 `席别/票价/查询时余票`；商务座 2315.0 元 2 张、一等座 1058.0 元 查询时无票、二等座 661.0 元 2 张、无座 661.0 元 查询时无票 | `ui/fare-card-complete.png` |
| 余票状态映射（有/无/候补/未提供） | ✅ `2 张` 与 `查询时无票` 分别渲染，未把缺失解释成无票 | 同上 |
| 余票说明只出现一次（0.1.22 交接的下一步检查项） | ✅ 任意单屏内 `票价可作为购票参考` ×1、`余票为查询时快照` ×1、`票价与余票`（标题）×1 | `ui/fare-statement-counts.json`（11 屏滚动扫描，取单屏最大值） |
| 低余量席别二次校验如实告知变化 | ✅ 出现 `低余量数据在两次采样间发生变化`（并暴露**D2** 文案缺陷） | `ui/final-fare-verify.png` |
| 多车次/多日期成对查询 | ✅ `明天后天大后天…G1 票价` → **3 张** `ticket_fare` 卡，0.91 s | `api_fare_three_dates.json` |
| 能力声明门控投递（声明后无重复正文） | ✅ 带 `client_capabilities`：`answer` 为空 + 卡片；不带：619 字纯文本回执 | `api_fare_single.json` vs 旧客户端对比请求 |
| 查询详情保留两项来源与采样时刻 | ✅ 展开后有 `数据日期/票价状态/票价采样/…` | `ui/fare-details-expanded.png` |

### 2.4 时刻与交路

| 文档声明 | 实测结果 | 证据 |
|---|---|---|
| 时刻卡（含图定口径标注） | ✅ 卡头 `图定` 徽标 + `以下为图定计划时刻，不代表当天实际运行时刻或正晚点状态。` | `ui/schedule-card-full.png` |
| 车次查担当展示实际车组号 + 重联标记 | ✅ `CR400BFS-3158 + CR400BFS-3203` + `重联` | `ui/routing-card.png` |
| 交路时间口径澄清 | ✅ `以下为记录时间，不是列车到发时间。` + 底部 `记录可能不完整，以实际运行情况为准。` | 同上 |
| 交路空结果不推断停运 | ✅ `未找到当天交路记录` / `暂无记录，不能据此判断停运。` | `ui/routing-empty-card.png` |
| 未来日期不查交路、且不谎报失败 | ✅ 经"更换日期"选 2026-10-08：`2026-10-08 是未来日期…未查询交路，这不属于查询失败。` | `ui/routing-after-date-change.png` |
| 日期切换可操作 | ✅ `更换日期` → 平台日期选择器 → 选定后按新日期重查 | `ui/date-picker.png`、`ui/date-picker-day8.png` |
| 卡片内结构化动作（查看该车次时刻） | ✅ 点行内动作直接产出对应时刻卡，无需重新输入 | `ui/routing-row-action-schedule.png` |

### 2.5 字典与更新

| 文档声明 | 实测结果 | 证据 |
|---|---|---|
| 本地字典可读、可答里程 | ✅ `京沪高速线全长 1318.0 公里`（rail.mileage 命中，附来源与口径） | `api_dict_mileage.json` |
| 词典更新检查（只查不装） | ✅ `发现词典版本 gtfs-20261004-052300` 并出现 `更新词典数据` | `ui/dict-check.png` |
| 词典更新实时进度（**文档自认从未实测**） | ✅ 逐阶段显示：`正在下载更新包` + 秒表 + `301.7 KB / 1.9 MB · 下载 15%` + `百分比仅表示下载进度；…`，进度 15%→29%→36%→40%→44%→59% | `ui/dict-apply-4s.png` … `ui/dict-apply-36s.png` |
| 词典更新落地 | ✅ `数据版本` gtfs-20260913-040340 → **gtfs-20261004-052300**，`词典数据已更新。`；站 5,410 / 车次 19,407 / 停站 161,494，与 `backend-dictionary-update-handoff-20261006.md` 的桌面数字**逐项一致** | `ui/dict-apply-t1.png`、`/api/updates/dictionary/local` |
| 软件更新检查 | ✅ `未发现更高正式版本。`（latest 0.1.19 < 当前 0.1.22）；资产名/SHA-256/发布页链接齐全 | `ui/software-check-result.png`、`/api/updates/software?current_version=0.1.22` |

### 2.6 错误分类与降级

| 文档声明 | 实测结果 | 证据 |
|---|---|---|
| 服务类异常不再叫"模型连接失败" | ✅ 断网后交路查询 → 标题 `查询服务暂时异常` | `ui/error-routing-offline.png` |
| 错误详情保留真实原因、可展开 | ✅ `rail.re 交路查询失败: ConnectError: [Errno 7] No address associated with hostname` | `ui/error-details.png` |
| 失败可重试 | ✅ `重试` 按钮重新发起同一提问 | `ui/error-retry-offline.png` |
| 认证失败单独归类 | ✅ 无效 Key 的知识问答返回 `LLM 鉴权失败(HTTP 401)：…API Key 无效或被拒绝`，并给出两种配置方式 | `api_invalid_key_knowledge.json` |
| 票价/余票单项失败互相独立 | ✅ 断网时票价失败仍保留余票项、余票失败仍保留票价项，且明确 `未使用公布票价替代` | `ui/error-card-5s.png` |
| 未知异常不甩锅给密钥 | ✅ 工具类错误显示为服务异常，未出现"更换密钥"类提示 | 同上 |

## 3. 发现的问题

### D1（中高）首次安装启动日志误报 `本地字典：不可用`，与发布验收最低标准冲突

- **现象**：全新安装后首次启动，logcat 输出 `本地字典：不可用（/data/user/0/org.openrailfanai.app/files/dict.db）`；而同一台设备强杀重启后输出 `本地字典：可用（…）`。运行期功能一直是好的（设置页显示数据版本、`/api/updates/dictionary/local` 返回 `available: true`）。
- **根因**：该日志在 uvicorn 启动**之前**求值，而"包内词典合并"发生在 FastAPI lifespan 里。
  `server.py:298-301` 在 `from app.main import app` 之后、`导入 uvicorn` 之前打印字典可用性；真正创建运行库的合并逻辑在 `backend/app/main.py:31-42`（`_lifespan` → `sync_bundled` → `merge`，`backend/app/updates/dictionary.py:119-157`）。
- **影响**：`docs/android.md:91-92` 把 `本地字典：可用` 写进**发布验收最低标准**，首次安装这条永远不达标，验收会被误判为整包失效；排障时也会把"字典没用上"当成结论。功能与自证不一致，属于"证据不真实"一类问题。
- **建议**：把该日志改为在 lifespan 合并之后回报（或首次启动时打印两条：合并前/合并后），并同步修订验收标准的判定时机。

### D2（中）低余量二次校验提示泄漏上游英文席别键

- **现象**：卡片说明出现 `**低余量数据在两次采样间发生变化**（余票实时变动）：G1 business: 3→2，请以 12306 实时显示为准`——"business" 是上游原始席别键，用户可见文案里应显示"商务座"。
- **根因**：`backend/app/tools/ticket_query.py:296` 直接拼接原始键 `k`；同仓库已有现成映射未被使用（`backend/app/pipeline/ticket_answer.py:35`、`backend/app/fare_result.py:97` 的 `"business": "商务座"`）。
- **影响**：中英混排、可读性差；同类变化发生在卧铺等席别时更难理解。
- **建议**：拼接前过一遍席别标签映射；无映射时退回原始键并保持现有诚实口径。

### D3（低）余票文案出现 `。；` 连写

- **现象**：旧客户端（不声明能力）的纯文本回执里出现 `…请以 12306 购票页面显示为准。；查询快照最多复用 15 秒，采样时间 …`。
- **根因**：`backend/app/tools/registry.py:77` 无条件以 `；` 追加 `查询快照最多复用 …`，而 `ticket_copy.py:4` 的 `AVAILABILITY_SNAPSHOT` 本身以 `。` 结尾。
- **影响**：低。Compose 卡片会把说明重排，用户看不到；受影响的是旧客户端/接口消费方的正文。
- **建议**：追加前按 note 末尾标点归一（末尾已是 `。`/`；` 时不再插 `；`）。

### D4（低）余票失败提示前缀重复

- **现象**：断网时卡片显示 `余票查询失败：12306 余票查询失败：网络请求失败 (已重试3次): [Errno 7] No address associated with hostname`。
- **根因**：`FareCard.kt:106` 给 `availability.error` 再加一次 `余票查询失败：` 前缀，而后端该字段本身已以同名前缀开头。
- **影响**：低（口径不假，只是啰嗦）；同时把英文 `Errno 7` 原样呈现给用户。
- **建议**：前端只在错误文本未包含该前缀时补；或后端去掉自述前缀、由展示层统一加。

### D5（低，设计边界）回到底部浮动按钮在部分滚动位置遮挡卡片内容

- **现象**：`ui/fare-card-full.png` 中按钮压住"到达 11:24"的末位；`ui/schedule-card-full.png` 中压住南京南站行的时间。
- **说明**：`ui-design-boundaries.md` 明确接受"覆盖消息区域、不额外占用列表高度"，且内容可滚动后可见，因此不判为硬性失败；但 `ui-acceptance-policy.md` 的"关键内容不可见"需要留意——建议在有内容被遮挡时把按钮做轻微位移或半透明降级。

### 观察项（不判为缺陷）

- **O1**：同一车次同日的余票在两次请求间从 `2 张` 变为 `无`（相隔约 17 秒）。二次采样提示如实标注了变化，属数据源实时性，不是缺陷。
- **O2**：`找机位` 示例仍是可点占位；用户已明确说明该功能未实现，本次 review 不纳入。
- **O3**：交路查询里输入 `CR400AF-5033`，回执与卡片显示 `CR400AF5033`（去连字符）。当前是忠实透传上游键的写法，未见错误。
- **O4**：`/health` 报 `llm_ready:false`、`model:deepseek-chat`，是服务端 `.env` 视角；客户端 BYOK 按请求下发，属既有设计（但保留 WebView 回退入口时会因此显示"未配置模型"引导条，属既有行为）。

## 4. 未验证 / 无法验证项

| 项 | 原因 |
|---|---|
| 应用内**下载并安装**高版本 APK | 当前最高正式版为 0.1.19 < 0.1.22，`update_available=false`，无包可下；未人为降级版本制造场景 |
| 多日期卡片的**原生 UI** 渲染 | 模拟器只有拉丁输入法，adb 无法输入中文问句；已用接口级验证 3 张卡（`api_fare_three_dates.json`） |
| 生成类回复的 Token 显示与"对话累计"增长 | 同上（工具类回复为 0 Token 已核对） |
| 阅读模式"有新内容 · 回到底部" | `回到底部` 已验证；"有新内容"需生成中滚动，未构造到 |
| 保留的 WebView 回退入口（`railfan_web_fallback=true`） | 本次只 review Main 原生路径 |
| LM 轨（本地模型、设备端推理） | 不属 Main 0.1.22 范围；Main 构建期已剔除该模块 |
| 实体机的长时间后台回收、厂商 WebView 兼容 | 无实体机 |

## 5. 与文档/验收记录的差异

1. **`build-0.1.22-local-20261006.md:13`** 自认"未运行模拟器、真机或端到端测试"——本次补齐后结论是**构建与功能成立**，但该文档"携带本地词典"这一条的自证方式有缺陷（D1）。
2. **`docs/android.md:91-92`** 的验收最低标准目前**无法在首次安装时满足**（D1）。
3. **`docs/README.md` 索引滞后**：未收录 `build-0.1.20/0.1.21/0.1.22`、`release-0.1.18`、`main-copy-review-fixes`、`backend-ticket-copy-alignment`、`backend-executed-fare-handoff` 等。
4. **`dist/android/OpenRailFanAI-0.1.21-arm64-release.apk` 无任何文档记录**（39,542,520 B，与 0.1.20 同尺寸）。
5. **`acceptance/index.yaml` 最近记录仍是 2026-10-04**（run `20261004T190918-654df36b`），其后的 Token、合并卡片、能力声明、更新入口、0.1.22 文案修订都**没有进入正式验收索引**；本次为人工走查证据，不能替代 `./scripts/acceptance.sh` 的正式产物。

## 6. 建议下一步

1. 修 **D1**：调整启动自证时机，并复核 `docs/android.md` 的验收标准表述（最高优先，直接影响每次发布的自证可信度）。
2. 修 **D2**：席别标签映射；顺带扫一遍其他"直接拼接上游原始键"的文案点。
3. 合并 **D3/D4** 两个文案小修，并给 `registry.py:77` 补一条标点归一的单测。
4. 用**中文输入可用**的环境（真机或装 IME 的模拟器）补做多日期与生成类回复的原生 UI 走查，然后跑 `./scripts/acceptance.sh --main-compose` 生成与 0.1.22 绑定的正式验收产物。
5. 发布流程中补一条：每个版本发布前把本次这类"首次安装 + 冷重启"两组启动日志同时留档。

---

本报告的所有截图与响应原文均在 `.ai/review-20261006/`，未修改 `backend/`、`android/`、`frontend/` 任何源码。
