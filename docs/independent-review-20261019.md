# OpenRailFanAI 独立评审（复核版）

日期：2026-10-19。评审方式：**产品代码只读**（未修改 `backend/app`、`frontend/src`、`android/`、配置、词典或发布产物；`backend/app/fuck_cr/parser.py` 按指示未动）。为了让结论可复核，本轮更新了 4 个测试文件与 `run_all.sh` 清单，见 §9。本文件与这些测试改动是仅有的写入。

复核原因：上一版评审完成时工作区尚未提交；约 19:46–19:52，发布方已把 0.1.29 提交、打 tag、发布到 GitHub，并同步了 `main`。因此 §2 的"发布源与产物脱节"已被解决，本版以 **`40e47f5` / `v0.1.29`** 为基线重写了结论与测试数据。

## 0. 结论

1. **0.1.29 的发布链路已经闭环**：`83794ad` 是干净构建提交，`40e47f5` 是发布核验提交，`main` 与 `origin/main` 都已指向 `40e47f5`；APK 内 `build.json` 为 `83794ad`（无 `-dirty`），GitHub Release、SHA-256、verification JSON 与 tag 一致。上一版的阻断项不再成立。
2. **测试闸门从"不可信"回到"基本可信"**：修正 3 个过期/脆弱的测试断言、补登记 4 个离线套件后，63 套中 **60 套通过**；剩余 3 个失败全部是环境问题（本机 DNS 拦截 `example.com`、本机 Node 动态库损坏），不是产品代码失败。
3. **但本轮发现了一个 0.1.29 新引入的产品回归**：请求内共享售票行后，**空区间**（12306 正常返回 0 条）被报成"12306 余票查询失败"，并丢掉 `availability_status=empty`。单测与 UI 卡片都把"无车次"和"查询失败"当作两种语义，这是明确的行为退化。
4. **车次改革仍是最大的时间风险**：生产解析器仍会把 `X1次列车` 解析成 `1`、`V6001次` 解析成 `6001`、`DH1次` 解析成 `1`、`AB123次` 解析成 `B123`；语义层、交路闸门、过渡期去重全部未动。影子解析器与 409 按钮只是"准备好了开关"，不改变 11 月下旬之后的用户结果。
5. **工作区需要一次"分层清理"，而不是 `git clean`**：`dist/android` 有 76 个 APK / 2.5 GB（其中绝大部分是历史版本），`.android-build` 18 GB，`acceptance` 488 MB，`.ai` 293 MB；`.git` 本身 252 MB（历史里有两枚约 29 MB 的 APK）。这些都不在 Git 工作区里，清理不影响已发布版本，但需要先区分"可删、可归档、不能碰"。
6. **文档与安全面整体仍好于同类项目**：更新包的哈希+签名+URL 锁定、SSRF 共享守卫、DOCX/XXE 防护、Key 脱敏、降级契约都有实现与测试支撑；需要补的是索引口径、匿名面说明和几处边界收敛。

## 1. 评审快照与方法

| 项 | 值 |
|---|---|
| HEAD | `40e47f5`（docs: verify 0.1.29 release, clean build and signed runtime checks） |
| 发布提交 / tag | `83794ad` / `v0.1.29` |
| 分支 | `codex/main-0.1.19` = `main` = `origin/main` = `40e47f5`；工作区干净 |
| `VERSION` | `0.1.29` |
| 0.1.29 APK | `dist/android/OpenRailFanAI-0.1.29-arm64-release.apk`，56,351,221 B；SHA-256 `762ea071ff94a2def490b7e376f4235388541b1d3e8aa7c65fe2132465a75283` |
| APK 构建标记 | `{"version":"0.1.29","commit":"83794ad","builtAt":"2026-10-19T19:46:09+08:00"}`（无 `-dirty`） |
| verification | `docs/verification/release-0.1.29.json`：`source_commit=83794ad`、GitHub 资产摘要一致、模拟器 API 35 安装启动与 health 通过 |
| 测试 | 更新后 63 套：**60 通过 / 3 失败**（失败均为环境，见 §9） |

实际执行：

- `git status/log/tag/branch --contains`、`git diff --check`、`unzip -p` 读取 APK 内 `build.json`；
- 全量 `bash backend/tests/run_all.sh` 两次（改动前 59 套、改动后 63 套）；
- 单独运行全部新增/受影响套件；
- 离线探针：`extract_train_code`、`retrieve._train_code_from_message`、`ticket_snapshot.availability`、`fastpath._station_in_text`（跨 `PYTHONHASHSEED`）、`fuck_cr.parser.parse`；
- `FUCK_CR/check.py`；
- 用 `TestClient` 复现 gzip 响应头与 HTTP 字节数；
- 统计仓库/构建目录体积与历史大对象。

## 2. 上一版阻断项：发布链路（已闭环）

上一版评审时 `HEAD=47dafbb`、`VERSION=0.1.29`、218 项未提交、0.1.29 APK 为 `-dirty`、`main` 落后。现在逐项复核：

| 上一版问题 | 当前状态 |
|---|---|
| 0.1.27–0.1.29 无提交、无 tag、无发布记录 | 已解决：`83794ad` + `v0.1.29` + `release-0.1.29.md` + `verification/release-0.1.29.json` |
| 0.1.29 APK 由 dirty 树构建 | 已解决：`build.json` 为 `83794ad`，与 tag 一致 |
| `main`/`origin/main` 不含发布提交 | 已解决：两者均为 `40e47f5`，且包含 `83794ad` |
| `docs/README.md` 链接未跟踪的 `FUCK_CR/` | 已解决：`FUCK_CR/` 已随 0.1.29 提交，链接有效 |
| `.gitignore` 未忽略 `.ai/`、`acceptance/runs/` | 已解决：发布提交已加入 `/.ai/`、`/acceptance/runs/` 等排除 |
| 0.1.29 无 `.sha256` 伴随文件 | 已解决：`dist/android/...0.1.29...apk.sha256` 存在，内容与 GitHub 资产一致 |

**剩余（不阻断发布）**：`dist/android` 仍保留 76 个历史 APK（含 `_副本`），`.git` 历史里仍有两枚约 29 MB 的 APK 与基线压缩包；这属于本地清理与仓库体积问题，见 §6。

## 3. 阻断级（当前唯一）

### B-1 生产解析仍会把未知字头静默改成另一趟车

这是当前**最确定、也最容易被用户看到**的"现在就会给出错误答案"缺陷。

**实测（2026-10-19，离线）**

| 输入 | `extract_train_code` / `retrieve._train_code_from_message` | 新解析器 `fuck_cr.parser` | 后果 |
|---|---|---|---|
| `X1次列车今天几点开` | **`1`** | `X1` | 查成 1 次车 |
| `V6001次列车经过哪些站` | **`6001`** | `V6001` | 查成 6001 |
| `DH1次今天有吗` | **`1`** | `DH1` | 查成 1 次车 |
| `AB123次列车` | **`B123`** | 未知字头，不产出 | 截掉首字母后当成 B123 |
| `G1次列车` / `1461次列车` / `T109` | `G1` / `1461` / `T109` | 一致 | 正常 |

**根因**

- `_TRAIN_CODE_IN_TEXT_RE`（`backend/app/tools/_rt12306.py:232`）只匹配旧字头集合，且左侧没有"不能紧邻字母/数字"的边界；`_PURE_DIGIT_TRAIN_RE`（同文件 236 行）会把 `X1次` 里的 `1` 当成纯数字车次。
- `extract_train_code` 在整串不匹配后依次降级到这两个正则（`_rt12306.py:249-254`），于是"字母开头但前缀未知"的 token 被截成一段**看起来合法**的数字。
- `retrieve._train_code_from_message` 直接把该结果当作车次补位，进入 `train.schedule` / 余票等工具。

**严重性**：它不报错，而是查询另一趟车；答案可能"看起来正常"（有车次、有时刻），用户无从发现。改革后 `X/V/DH` 会变成真实输入，11 月下旬之后这类错误将从边缘输入变成常用输入。

**建议（最小修复，不依赖新规则）**

1. 给 `_TRAIN_CODE_IN_TEXT_RE`、`_PURE_DIGIT_TRAIN_RE` 加左边界 `(?<![A-Za-z0-9])`；
2. `extract_train_code` 在"字符串以 ASCII 字母开头且整串不匹配已知字头"时直接返回 `None`，不降级到纯数字。

回归钉住：`X1次列车 → None`、`V6001次 → None`、`DH1次 → None`、`AB123次 → None`、`G1次列车 → G1`、`1461次列车 → 1461`、`T109 → T109`。修 shadow 之前先修 legacy；两者不要捆在一个提交里。

## 4. 高优先级

### H-1 【新】售票行共享把"无车次"降级成"查询失败"

0.1.29 的 `ticket_snapshot` 在请求内共享原始售票行，但空结果的处理改变了对外语义。

**复现（离线，`TicketQueryTool`，空 rows）**

```
snapshot ON  | ok=False | error=12306 余票查询失败：未找到该线路的余票 | data=None
snapshot OFF | ok=False | error=北京南→上海虹桥（2026-10-25）未查询到余票数据
             | data={'availability_status': 'empty', 'from_station': '北京南', ...}
```

**根因**

- `ticket_snapshot.availability` 在 `trains` 为空时直接 `raise Realtime12306Error('未找到该线路的余票')`（`app/tools/ticket_snapshot.py:156`）。
- `ticket_query` 的 `except Realtime12306Error` 分支（`app/tools/ticket_query.py:227-235`）把它转成"12306 余票查询失败"，并**丢掉**下方空结果分支（`:259-287`）才有的 `data={"availability_status":"empty",...}` 与 `sampled_at`。
- 批量路径（`service_batch`）同样把这一项写成"查询失败：12306 余票查询失败：未找到…"，而不是"未查询到余票数据"。

**影响**：违反项目自己反复强调的"空结果三义"契约（正常空 / 筛选后空 / 接口失败）。单查询卡片、票价/余票合并卡和生成层都会把"该区间无车次"读成"查询失败"，用户得到不同的提示与不同的可信度判断。数据本身没有错，但**语义错了**。

**建议**：`availability` 对空 rows 返回空 `SnapshotTrains`（带 `fetched_at`），把"是否有车"的判断留给 `ticket_query` 的空结果分支；或抛一个专用 sentinel 由 `ticket_query` 转成 `availability_status=empty`。补一条测试：snapshot 激活 + 空 rows → `data.availability_status == 'empty'`。这条应在 0.1.30 修掉。

### H-2 语义层、交路闸门、过渡期全部未动，距第一批约 5 周

| 触点 | 状态 |
|---|---|
| `_TYPE_KEYWORDS`（`retrieve.py:133`） | 仍是 `高铁→G`、`动车→D`、`特快→T`、`快速→K`、`普速→K,T,Z` |
| `_EMU_TYPE_RE`（`_rt12306.py:196`） | 只认 `G/D/C`；新 `X/T/K` 会被交路闸门拒绝（`retrieve.py:852`、`service_batch.py`、`ticket_pair.py`） |
| 服务准入 | `B/DH/DJ/5 位数字` 未区分"可识别"与"可查客运"；新解析器的候选域没有接入分流 |
| 有效期/历史 | `effective_date` 只在文档里，不在代码 |
| 过渡期双表达 | 没有跨日期的"同车不同号"归一化；现存去重按 train_no |
| 启用路径 | `/api/fuck-cr/activation` 固定 409；`reform_2026` 启动即拒绝；`shadow` 只观察 `service_dispatch.train_codes` 一处 |

**改革后的具体错法**（按现行代码推演）：

- "普速"→ 仍筛 `K,T,Z`；新体系普速是 `Z,N,V`，旧 `K/T` 已改挂 `V/N` → 漏掉大部分普速车。
- "高铁"→ 仍只给 `G`；新 `X/T/K` 也是高速旅客列车 → 漏车。
- "动车"→ 仍只给 `D`；新 `D` 只剩动集，200–250 的动车已改 `K` → 语义反了。
- "特快/快速"→ 旧 T→N、旧 K→V，而新 T=300、新 K=200–250；同一个词在 11/25 前后指向不同字母。
- 新 `X/T/K` 查担当 → `is_emu_train_code` 为假 → 回答"rail.re 不收录"；实际是能力未接入。
- 跨 11/24–11/25 的历史/未来日期 → 同一列车两个号，可能被当成两趟。

**时间表**

| 批次 | 官方口径 | 精确转述（C 级） | 代码应在此前完成 |
|---|---|---|---|
| 第一批 | 2026 年 11 月下旬 | 货运 11-24 18:00 / 客运 11-25 00:00（《上海铁道》转述） | 解析加固、语义分层、准入分流、日期口径 |
| 第二批 | 2026 年 12 月下旬 | 未公布 | 高速字头切换、双表达去重、文案 |

**建议**：不要把"11/25"写成自动开关；用 `FUCK_CR_MODE` + 显式规则版本控制，默认 `legacy`；先做"识别 → 类别解释 → 数据源准入"三层拆分；缓存键加规则版本/业务日期；先修 B-1，再谈语义。

### H-3 机读资料的置信度字段与自身注释矛盾

- `FUCK_CR/data/train-code-2026.json` 的 `effective.confidence` 是 **`"A"`**，但同一对象的 `note` 明说两个精确时点来自 C 级转述；`docs/backend-train-code-reform-preparation-20261017.md` §5 已经指出。
- `check.py` 通过（`客运 11 个，映射 17 条，来源 16 条`），但它只校验结构/引用/覆盖，不校验字段与注释是否自洽。
- 目前没有生产代码读这个 `confidence`，所以不是现行 bug；一旦未来用 `confidence == "A"` 做启用门控，就会把转述日期当成官方日期。
- 建议拆成 `window_confidence`（"11 月下旬"=A）与 `exact_time_confidence`（C），并让 `check.py` 断言"精确时点不得为 A"。

### H-4 测试闸门：60/63，剩余失败全部是环境；但仍有结构性缺口

**本次结果（更新后，63 套）**

- 通过 **60**；失败 3：`test_tools`（本机 DNS 把 `example.com` 解析到 `198.18.1.22`，SSRF 守卫正确拒绝）、`test_frontend_store` / `test_frontend_render`（本机 Homebrew Node 缺 `libsimdjson.33.dylib`）。
- 上一版被误归因为"rail.re 网络"的 `test_api`，复核确认是**测试假设过期**：它用「那明天呢」要求 `emu.routing: ok`，而产品规则明确"未来日期不查交路"（`retrieve.py:856-866`）。把追问改为「那今天呢」后通过。
- `test_static_assets` 是测试假设错误：Starlette 0.50 的流式 gzip 会移除 `content-length`；改用 `r.num_bytes_downloaded` 后通过（实测 237,519 B → 77,371 B，3.07x）。
- `test_android` 有三处过期断言：`rglob("*.apk")` 把 `app-debug-androidTest.apk` 当成应用包；`providerRow` 签名已扩展；manifest 图标已从 `@drawable` 改为 `@mipmap`。均已按"检查意图"而非"精确文本"修正。
- 仍存在网络/环境耦合：8 个套件未接入（`test_github_updates`、`test_photo_annotations`、`test_dates_without_tzdata`、`test_structured_sse_visibility` 等）；网络失败与代码失败在同一份汇总里无法区分。
- 本轮已登记 4 个此前未接入的离线套件（见 §9），`test_hardening` 现在报告 63 套。

**建议**：把 `run_all.sh` 拆成 `--offline` 与 `--live`；发现不到可用 Node 时输出明确 `SKIP`；网络失败显式 `SKIP` 并计入汇总。

### H-5 文档索引与口径（已比上一版改善）

- 0.1.29 已把 13 份最新交接/审计文档补进 `docs/README.md`，`FUCK_CR/` 链接也不再悬空；此前"最新 13 份全缺"的问题已解决。
- 仍有 62 份更早的文档不在索引里。索引本身是"精选"而非全集，这可以接受，但应在页首写明"精选，不是全量清单"，否则读者会以为 `docs/` 只有这些。
- `docs/EXPL.md:131-132` 的"无网络套件"清单仍只列约 16 个，并称"其余依赖真实 LLM 或境内数据源"；实际 `test_metrics`、`test_photo_spots`、`test_backend_performance`、`test_pipeline_performance`、`test_local_inference`、`test_static_assets`、`test_llm_providers`、`test_android`、`test_corpus`、`test_phrasings`、`test_fuck_cr*` 都是离线可跑的。这会误导"断网时该跑哪些"。
- `FUCK_CR/INDEX.md:6` front matter 是 `revision: 4`，`:21` 表格仍写 `revision 3 · 2026-10-17`。
- `backend/app/main.py:60` 的 FastAPI `version="0.1.0"` 与 `VERSION=0.1.29` 不一致（仅 OpenAPI 元数据，但会出现在 `/docs`/客户端生成物里）。

## 5. 中低优先级

### M-1 匿名面与开放指标

- 默认绑定 `127.0.0.1`（`config.py:82`），对本机/Android 内嵌合理；但 `CORS *`（`main.py:73`）且**没有任何认证**。`/api/metrics`（`main.py:118`）公开暴露进程计数、工具名、供应商 id、缓存名（不含用户文本/密钥）。
- 若 `HOST=0.0.0.0` 放到局域网，同网段设备可以使用服务器上配置的 LLM Key 发起对话，并读取指标。当前配置面里没有认证/共享令牌开关。
- 建议：恢复可选共享令牌/反代鉴权，或在文档与启动日志明确"仅限回环"，并让非回环绑定必须显式确认；指标端点至少可关闭或加同一道门。

### M-2 体积与生成式设计资产

- 0.1.29 release APK **56,351,221 B**，0.1.26 为 41,422,428 B，**+36%**；其中 `lib/arm64-v8a/libmlkit_google_ocr_pipeline.so` 单项 11,064,544 B。离线中文 OCR 是明确需求，但应作为产品决策记录。
- release 仍是 `isMinifyEnabled=false` / `isShrinkResources=false`（`build.gradle.kts:148-149`），R8 未启用。
- `NativeDesignPaths.kt` 1,511 行、**1,465 个 `DesignInkLayer`**，从 `desi1.png`/`desi2.png`（2.6 MB）逐像素描出。UI 约束已在 10-03 放宽"逐像素接近"，这批生成物更像历史投资；应评估维护/启动成本，考虑换回矢量图标。

### M-3 隐性开关

- `MAIN_TICKET_SHARED_ROWS_ENABLED` 由 `os.environ` 直接读取（`ticket_snapshot.py:33-35`），不在 `Settings`、不在 `.env.example`，`test_config_docs` 的 67 项覆盖看不到它；`RAILFAN_TESSERACT` 同样只在交接文档出现。它们有文档，但没进入"配置真相"。
- LM 轨把 ML Kit 设为 `compileOnly`，而 `ChatRepository.kt:98` 无条件引用 `ImageTextRecognizer`；只要 LM 路径调用到 `ChatRepository`，就会在无 ML Kit 的 APK 上触发 `NoClassDefFoundError`。当前 LM 走 WebView，未必触发，但这是一条没有编译期保护的耦合。

### M-4 确定性与边界

- `fastpath._station_hits` 在"两个同长度站名"时回退到 `sorted((n for n in names ...), key=len)`，而 `names` 是 `set`（`fastpath.py:336`）。`set` 迭代顺序受 `PYTHONHASHSEED` 影响：实测同一句「北京南和上海南哪个近」在 seed 1/2/4 取 `北京南`、seed 3 取 `上海南`。当前多站场景多数被 `_merge_od` 的歧义门挡住，但 `rule_slots`/`_station_in_text` 仍可能拿到不确定的第一个站。建议按"出现位置、再按长度"显式排序。
- `fuck_cr.parser.parse("0X1")` 返回 `codes=('0X1',)` 且 `status=candidate`，尽管 `_domain()` 对 `zero=True` 返回 `UNKNOWN`。按指示未改影子解析器；建议后续把这类输入归入 `ambiguous/rejected`，否则影子统计会把 `0X1` 当候选。
- `app/data/dict.py:_polite_get` 不捕获 `httpx.ConnectError`；`rail_mileage._station_profile` 对不存在的站名仍去请求 jprailfan，离线时异常直接冒出（本次 `test_dict_mileage` 第一次运行即失败于此，网络恢复后通过）。编排层会兜住，但工具契约要求 `ToolResult(ok=False)`。建议在抓取层收敛传输错误。

### M-5 其他可优化点

- `web_search._fetch_top_pages` 在注入预算为 0 时仍按 `max(400, budget // top_n)` 抓取每页至少 400 字，并在保留 `top_n` 条成功正文的同时最多并发抓 `top_n + 2` 条；全部成功时后两条被丢弃（`web_search.py:300-317`）。这是审计已记录过的过量抓取，仍未收敛。
- `registry.query_scope()` 每次工具调用都对全部 67 项设置做 `dict() + json.dumps + sha256`；请求内可缓存一次。
- `updates/software.check` 只挑"版本号最大"的 release；若该 release 缺 arm64 资产，会报有更新但没有可下载资产，而不会回退到"最新一个带资产的版本"。
- `frontend/src/main.js` 的 `schedule-source` 链接直接 `link.href = sourceUrl`，未像 `renderSources` 那样过滤 `http(s)` 协议；当前 sources 由工具硬编码，风险低，但可统一。

## 6. 工作区是否需要清理

结论：**需要，但应分层处理；不要跑 `git clean -fdx`**。当前工作区是干净的（`git status` 0 项），需要清理的是本地构建/证据/发布产物。

| 目录/文件 | 体积 | 性质 | 建议 |
|---|---:|---|---|
| `.android-build/` | 18 GB | SDK 6.0G + android-home 7.0G + gradle 1.6G + llama 构建 3.2G + jdk 0.3G | **保留** SDK/gradle/jdk（重建成本高）；llama 构建目录若近期不编 LM 轨，可归档后删除 |
| `dist/android/` | 2.5 GB / 76 个 APK | 0.1.4–0.1.29 的历史 debug/release/lm 包，含 `_副本` | **保留** 0.1.29 与 0.1.26（回滚）；其余可归档到外部盘后删除，约释放 2.4 GB |
| `acceptance/` | 488 MB | 101 个 `runs/` 目录 + 正式对照 PNG | `runs/` 已在 `.gitignore`；旧 run 可打包归档；正式 PNG 与 `index.yaml` 不要动 |
| `.ai/` | 293 MB | 审计/基准证据，10 个脚本已跟踪 | 未跟踪部分打包归档；已跟踪的 10 个文件保留 |
| `android/app/build/` | 793 MB | 可重建的 Gradle 产物 | 可删，下次构建重建 |
| `backend/data/dict.db.bak-20261007` | 14 MB | 旧词典备份 | 确认当前 `dict.db` 正常后可删 |
| `.git/` | 252 MB | 历史里有两枚约 29 MB 的 APK、两份 5.6 MB 基线 tar | `git gc` 只能清 12.7 MB garbage，收不回可达对象；不建议为体积改写历史 |
| 根目录 `test-baseline-*.log`、`.setup-uvicorn.log`、`.DS_Store` | <200 KB | 一次性日志/系统文件 | 可直接删 |
| `dist/RailFanAI-export-20260915_副本.tar.gz`、`*.apk_副本` | 数 MB | 手工副本 | 可直接删 |
| `desi1.png` / `desi2.png` | 2.6 MB | **已跟踪的设计原稿** | 保留（UI 边界文档要求原图不变） |

建议先做无损动作：删日志/`.DS_Store`/`_副本`、清 `android/app/build`、归档旧 APK 到外部盘；再做 `.android-build` 与 `.ai`/`acceptance/runs` 的归档。任何删除前先确认 0.1.29 与 0.1.26 的 APK/SHA-256 已另存。

## 7. 车次改革专项：现在到底缺什么

**已具备**：`FUCK_CR/` 资料自洽（`check.py` 通过：11 个客运字头、17 条映射、16 个来源）；独立解析器（10 项单测）；影子观察（7 项）；409/200 预备接口（5 项 API 测试）。

**未具备**（按改革后的用户可见顺序）：

1. 生产解析不再截号（§3 B-1）——最小、最急、可独立上线。
2. 字头 → 类别 → 日期的解释层；`普速/高铁/动车/特快/快速/城际` 要按 `effective_date` 返回不同字母集。
3. 服务准入：`B/DH/DJ/5 位数字` 不进客运余票/时刻；`X/T/K` 能进交路。
4. 过渡期双表达：跨 11/24–11/25 的两个号不重复、不静默替换；历史日期按旧语义。
5. 文案与来源：至少一处解释"车次变了、车没变"；不能把字头级映射写成逐趟对照。
6. 缓存与词典：随包词典、GTFS、公布票价保留快照日期；新号缺失如实缺失。

**门槛（不是日期开关）**：`shadow` 至少覆盖 `train.schedule`、`ticket.query`、`emu.routing`、`service_batch` 四个入口；`reform_2026` 启用需官方全文/可验证接口样本 + 逐字段置信度 + 回滚演练；先修 B-1，再谈默认值。

## 8. 核实为真的项目声明（正面）

| 声明 | 核对结果 |
|---|---|
| 0.1.29 从干净提交构建并发布 | 成立：`build.json`=`83794ad` 无 dirty；tag/Release/verification/SHA 一致 |
| 更新包有 SHA-256 + 签名 + URL 锁定 | 成立：`github.asset_info` 校验文件名/大小/摘要/URL；客户端下载后再校验；`SoftwareInstaller` 验包名/versionCode/签名历史 |
| SSRF 守卫覆盖请求级与配置级 | 成立：`providers.guard_request_base_url` 共用；`test_regressions` 10 类目标 + 真实抓取链路 + `test_llm_providers` |
| DOCX/DOC 解析有边界与 XXE 防护 | 成立：zip 条目/解压总量/单文件上限、禁 `DOCTYPE/ENTITY`、OLE 链与 mini-FAT 有上限 |
| 不泄露 Key | 成立：`/api/providers` 只回 `has_key`；错误文案脱敏；日志无 Key |
| 降级不伪装成成功 | 成立：`degraded=True` + `answer_done=False` + `error` 同时可见 |
| 检索事实/来源/截断契约 | 成立：`test_r1_fixes`、`test_output_guard`、549 项语料断言 |
| 请求内共享售票行 | 机制成立且有 12 项并发/取消/隔离测试；但空结果语义见 H-1 |
| 文件/图片工具链边界 | 成立：25 项 agent 测试、9 项文档解析、10 项图片/12 项输入路由/9 项传输 |
| `FUCK_CR` 资料自洽 | 成立：`check.py` 通过；三条硬规矩在文中一致 |
| 0.1.26/0.1.29 发布记录质量 | 成立：SHA、签名证书、词典、构建提交、模拟器检查、限制说明齐全 |

## 9. 本轮"更新测试"的具体改动

只改了测试，没有改产品代码。`git diff --stat`：4 files, +27/−7。

| 文件 | 改动 | 原因 |
|---|---|---|
| `backend/tests/run_all.sh` | 59 → 63 套 | 登记 `test_closed_knowledge_tree`、`test_decision_station_matching`、`test_future_routing_guard`、`test_prompt_cache_metrics` |
| `backend/tests/test_api.py` | `那明天呢` → `那今天呢`，补 docstring | 原断言要求未来日期 `emu.routing: ok`，与 `retrieve.py:856` 的未来交路红线冲突；这是测试假设过期，不是网络 |
| `backend/tests/test_static_assets.py` | `content-length` → `r.num_bytes_downloaded` | Starlette 0.50 流式 gzip 不提供 `content-length`；用传输层字节数才能同时测两种响应 |
| `backend/tests/test_android.py` | 过滤 `androidTest` APK；`providerRow` 签名只断言前缀；icon 接受 `mipmap/drawable` | 三处都是过期/过窄的静态断言，不是产品缺陷 |

更新后全量：**60/63 通过**；失败 `test_tools`（本机 DNS）与两个前端套件（本机 Node 动态库）。受影响套件单独复跑均通过：`test_api`、`test_static_assets`、`test_android`、4 个新登记套件。

## 10. 未能验证

- **真机**：本轮没有连接设备；0.1.29 的模拟器安装/health 取自 `docs/verification/release-0.1.29.json` 的记录，不是我的独立复测。相册/文件选择器、字体缩放、键盘、系统分享、安装器交互未复测。
- **云端文件/图片端到端**：没有用真实模型跑"上传 → 整理 → 导出"的语义正确率；只验证了离线契约与固定替身。
- **供应商注册/付款/密钥连接**：没有逐家实测。
- **改革后的 12306/rail.re 行为**：新规则尚未实施，无法验证新号真实返回、双表达、历史查询。
- **`FUCK_CR` 外部信源**：核对了库内自洽与代码影响，没有重新逐条核验新华社/《人民铁道》原文。
- **全量 Kotlin/Compose 与前端逐行**：抽样审计（Manifest、更新、OCR、设置、WebView、agent.js、store/theme 契约）。
- **前端测试**：本机 Node 动态库损坏，`test_frontend_store`/`test_frontend_render` 无法执行。

## 11. 建议顺序

| 时间 | 动作 | 完成判据 |
|---|---|---|
| 立即 | 修 H-1（空车次语义）+ 补回归 | snapshot 激活时空 rows → `availability_status=empty` |
| 立即 | 修 B-1（解析截号）单独发 0.1.30 | `X1次列车` 不再产出 `1`；旧语料零回退 |
| 本周 | 测试拆分 `--offline` / `--live`；Node 缺失显式 SKIP | 断网环境仍能得到可解释汇总 |
| 本周 | 清理旧 APK/日志/`_副本`；归档旧 run | 保留 0.1.29/0.1.26 校验材料 |
| 10/24–11/07 | 三层拆分（识别/解释/准入）；`effective_date`；`shadow` 扩到 4 入口；缓存加规则版本 | 新旧对照可观测；新号不误进客运 |
| 11/07–11/17 | 过渡期双表达、跨日期、历史查询；真实 12306 样本回填 | 跨 11/24–25 不重复、不替换 |
| 11/17–11/24 | 冻结默认 `legacy`；测试实例开 `shadow`；回滚演练 | 回滚 5 分钟内完成；无自动按日期切换 |
| 改革后 | 依据官方全文/逐趟表/接口证据再启用 `reform_2026` | 不以发布日期、按钮或解析器能力作为启用依据 |

**需要拍板**：

1. 0.1.30 是否只包含 H-1 + B-1 两个修复（建议：是，单独发版，不夹带语义迁移）。
2. 11/25 是否让新语义默认生效（建议：不；默认 `legacy`，按业务日期与证据逐段启用）。
3. 是否接受 +36% APK 体积换离线中文 OCR（若接受，发布说明写明；若不接受，改按需下载/Play Services）。

---

## 附录 A：关键复现

```bash
# 快照与发布
cd /Users/xylo/Documents/OpenRailFanAI
git rev-parse HEAD; cat VERSION; git status --porcelain | wc -l
unzip -p dist/android/OpenRailFanAI-0.1.29-arm64-release.apk assets/webapp/build.json

# B-1 解析截号
cd backend && PYTHONPATH=. .venv/bin/python - <<'EOF'
from app.pipeline import retrieve
from app.tools._rt12306 import extract_train_code
for s in ["X1次列车","V6001次","DH1次","AB123次","G1次列车","1461次列车"]:
    print(s, "->", extract_train_code(s), "|", retrieve._train_code_from_message(s+"今天几点"))
EOF
# 实测：1 / 6001 / 1 / B123 / G1 / 1461

# H-1 空车次语义
cd backend && PYTHONPATH=. .venv/bin/python - <<'EOF'
import asyncio, os
from unittest.mock import patch, AsyncMock
from app.tools import ticket_snapshot as snap, _rt12306 as rt
from app.tools.ticket_query import TicketQueryTool

@snap.request_scope
async def active():
    with patch.object(rt,'resolve_station_code',AsyncMock(side_effect=[('VNP','北京南'),('AOH','上海虹桥')])), \
         patch.object(snap,'query_rows',AsyncMock(return_value={'rows':[],'fetched_at':'x'})):
        return await TicketQueryTool().invoke({'from_station':'北京南','to_station':'上海虹桥','date':'2026-10-25'})

async def inactive():
    with patch.dict(os.environ,{'MAIN_TICKET_SHARED_ROWS_ENABLED':'false'}), \
         patch.object(rt,'resolve_station_code',AsyncMock(side_effect=[('VNP','北京南'),('AOH','上海虹桥')])), \
         patch.object(rt,'query_tickets',AsyncMock(return_value=[])):
        return await TicketQueryTool().invoke({'from_station':'北京南','to_station':'上海虹桥','date':'2026-10-25'})

for label,coro in [('snapshot ON',active()),('snapshot OFF',inactive())]:
    r=asyncio.run(coro); print(label,'ok=',r.ok,'error=',r.error,'data=',r.data)
EOF

# 站名同长度排序随 PYTHONHASHSEED 变化
cd backend && for s in 1 3; do PYTHONHASHSEED=$s PYTHONPATH=. .venv/bin/python -c \
 "import asyncio;from app.tools import _rt12306 as rt;from app.pipeline import fastpath;asyncio.run(rt.ensure_loaded());print(fastpath._station_in_text('北京南和上海南哪个近'))"; done

# FUCK_CR 自洽
cd FUCK_CR && python3 check.py

# 测试
cd backend && bash tests/run_all.sh
```

## 附录 B：测试结果（更新后，63 套）

| 套件 | 结果 | 归因 |
|---|---|---|
| `test_fuck_cr_api` / `test_fuck_cr` / `test_fuck_cr_parser` | 通过（5 / 7 / 10） | — |
| `test_agent_tools` / `test_agent_documents` / `test_agent_images` / `test_agent_input_routes` / `test_llm_image_transport` | 通过（25 / 9 / 10 / 12 / 9） | — |
| `test_tools` | 失败 | 本机 DNS 把 `example.com` 解析到 `198.18.1.22`，SSRF 守卫正确拒绝；环境 |
| `test_api` | **通过（已修）** | 原断言要求未来日期查交路；改为今天后通过 |
| `test_static_assets` | **通过（已修）** | 用 `num_bytes_downloaded`；实测 3.07x |
| `test_android` | **通过（已修）** | 过滤 androidTest；签名/图标断言放宽到意图 |
| `test_dict_mileage` | 通过 | 本轮 jprailfan 可达；但 `_polite_get` 的传输错误仍未收敛 |
| `test_frontend_store` / `test_frontend_render` | 失败 | 本机 Node 缺 `libsimdjson.33.dylib`；环境 |
| `test_closed_knowledge_tree` / `test_decision_station_matching` / `test_future_routing_guard` / `test_prompt_cache_metrics` | 通过（4 / 2 / 通过 / 5） | 本轮新登记 |
| 其余 44 套 | 通过 | 含 `test_corpus` 549 项、`test_slm_hardening` 68 项、`test_ticket_snapshot` 12 项等 |

未接入的其余套件（未改动）：`test_dates_without_tzdata`（需无 tzdata 环境）、`test_github_updates`、`test_photo_annotations`、`test_photo_dictionary_updates`、`test_structured_sse_visibility`、`test_routing_projection_coupling`、`test_service_batch`、`test_incident_service_delivery`。其中 `test_routing_projection_coupling`、`test_service_batch`、`test_incident_service_delivery` 本轮单独跑过并通过；其余未跑。
