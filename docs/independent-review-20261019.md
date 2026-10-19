# OpenRailFanAI 独立评审

日期：2026-10-19。评审方式：**只读**（未修改任何源码、配置、词典、客户端或发布产物；本文件是唯一新增文件）。
评审时工作区正被另一个进程写入（19:36 仍有构建与文档落盘），因此所有结论都绑定下面的快照。

## 0. 结论

1. **产品方向与工程底子是本项目最强的资产**：工具契约、数据完整性、降级语义、更新校验、SSRF/XXE 防护、语料回归，这些不是"看起来做了"，而是有测试和注释支撑的。
2. **但当前仓库处于"不可发布"状态**：0.1.26 之后的所有工作在本地未提交，0.1.29 的 APK 已在 `dist/android/` 生成，构建标记是 `47dafbb-dirty`；0.1.27–0.1.29 没有发布记录、没有 verification、没有 tag。发布产物与可追溯源码已经脱节。
3. **车次改革的风险不在"不知道"，而在"还没动手"**：最危险的现存缺陷（`X1次列车` 被解析成 `1`，即静默变成另一趟车）在 10-17 就已被自己记录，但生产解析器一行未改；语义层、交路闸门、过渡期去重也全部未动。影子解析器和 409 按钮证明设计想清楚了，但不改变 11 月下旬之后的用户结果。
4. **测试闸门目前不是可靠闸门**：本机 59 套中 52 套通过；7 个失败里 5 个是环境/网络，2 个是测试自身的缺陷；另有 3 个新增离线套件根本没有接进 `run_all.sh`。因此"全绿"既不能由当前环境证明，也不能由当前清单保证。
5. **文档的"深度"很好，"索引与口径"在漂移**：最新的 13 份交接/审计文档没有进索引；`docs/README.md` 甚至链接到一个未提交的 `FUCK_CR/`；`EXPL.md` 的"无网络套件"清单已明显过期。

## 1. 评审快照与方法

| 项 | 值 |
|---|---|
| HEAD | `47dafbb00c49d8990b1d4c75dd39e8ad7e277061`（2026-10-09，0.1.26 发布记录） |
| `VERSION` | `0.1.29`（工作区改动；HEAD 内为 `0.1.26`） |
| 工作区 | 218 项：173 个未跟踪条目 + 45 个已修改文件；代码/文档类未提交约 5.8k 行（不含二进制与 acceptance 索引） |
| 最新产物 | `dist/android/OpenRailFanAI-0.1.29-arm64-release.apk`，56,351,225 B，2026-10-19 19:36 |
| APK 构建标记 | `{"version":"0.1.29","commit":"47dafbb-dirty","builtAt":"2026-10-19T19:34:44+08:00"}` |
| 已发布 | GitHub tag 最新为 `v0.1.26`；0.1.27/0.1.28/0.1.29 无 tag、无 Release 记录、无 verification JSON |
| 测试 | `backend/tests/run_all.sh`：59 套，52 通过 / 7 失败（逐项分类见附录 B） |
| 并发写入 | 评审期间另一进程在 19:36 生成 `docs/frontend-inputs-fuck-cr-integration-20261019.md` 与 0.1.29 APK；本评审不据此判断该进程的正确性 |

实际执行的检查（全部只读或使用临时目录）：

- `git status/log/tag/branch --contains`、`git diff --check`；
- `python -m compileall backend/app`；
- 全量 `bash backend/tests/run_all.sh`（59 套）；
- 单独补跑 3 个未接入的新套件；
- 离线调用 `parse_od`、`extract_train_code`、`fuck_cr.parser.parse`、`service_dispatch.train_codes`、`retrieve.retrieve` 做边界探针；
- 用 `TestClient` 复现 gzip/`content-length` 行为；
- `FUCK_CR/check.py`；
- 读取 APK 内的 `assets/webapp/build.json` 与 native 库清单。

---

## 2. 阻断级

### B-1 发布源与发布产物脱节，0.1.29 不可复现

**证据**

- `HEAD` 仍是 0.1.26 的发布记录（`47dafbb`），`VERSION` 已是 0.1.29；`git status` 218 项，核心新功能全部未提交：
  - 后端：`backend/app/agent.py`、`agent_documents.py`、`agent_images.py`、`api/agent.py`、`api/fuck_cr.py`、`fuck_cr/`、`pipeline/decision_tree.py`、`tools/ticket_snapshot.py`；
  - Android：`AppAgent.kt`、`ImageTextRecognizer.kt`、`FuckCrSettings.kt`、`ProviderSetupWizard.kt` 等 8 个主代码文件（另有 2 个 androidTest）；
  - 前端：`agent.js`、`switch.js` 与 `agent.test.mjs`；
  - 测试：`test_agent_*`、`test_fuck_cr*`、`test_ticket_snapshot.py` 等。
- 0.1.29 APK 的 `build.json` 自己写着 `47dafbb-dirty`。项目自己的规则要求"只在工作区干净时构建待发布的包"（`docs/android.md:139`）。
- 0.1.27/0.1.28/0.1.29 均无 `docs/release-*.md`、无 `docs/verification/*.json`、无 `.sha256` 伴随文件（0.1.29 目录下只有 APK）。
- 发布 tag `v0.1.24`/`v0.1.25`/`v0.1.26` **只存在于 `codex/main-0.1.19` 分支**（含其远端），`main` / `origin/main` 不包含这些提交；`origin/main` 停在 `f4bec3e`，落后 HEAD **29 个提交**，不含 0.1.19 起的任何发布提交。
- `docs/README.md:26` 链接 `../FUCK_CR/README.md`，但 `FUCK_CR/` 整个目录未跟踪 → 在 GitHub 上该链接 404。
- `.gitignore` 没有忽略 `.ai/`（293 MB）与 `acceptance/runs/`（488 MB），导致 `git status` 长期被 200+ 条噪声淹没，进一步降低了"提交干净"的可操作性。

**影响**

- 任何"这个 APK 对应哪份源码"的问题都无法回答；回滚、复现 bug、对照测试都失去基准。
- 一旦机器故障或误执行 `git clean -fdx`，约 5.8k 行源代码、`FUCK_CR/` 研究资料和整套证据目录会一起消失。
- 更新器从 GitHub Releases 取包，但 Release 所对应的源码不在默认分支；外部贡献者/审计者 clone 下来看到的是旧代码。
- 继续在这个树上升版本，会把"dirty 构建"变成惯例，而项目已经在文档里明确禁止这一点。

**建议**

1. 立刻冻结写操作，把现有工作按可回滚的边界提交（建议 6 个提交：`agent/file tools`、`image/document input`、`provider wizard`、`fuck_cr` 预备层、`metrics/prompt-cache`、`ticket snapshot`，最后单独一个 `Release 0.1.29`）。
2. 在干净树上重新构建 0.1.29，核对 `build.json` 不含 `-dirty`，补 `.sha256`、`release-0.1.29.md`、`verification/release-0.1.29.json`，再打 tag、发 Release。
3. 把 `codex/main-0.1.19` 合回 `main` 并推送，保证默认分支能复现所有已发布 tag。
4. 将 `.ai/`、`acceptance/runs/` 加入 `.gitignore`（或明确纳入版本管理，二选一，但不能继续悬空）。
5. 在完成 1–3 之前，**不要发布** 当前 `dist/android/OpenRailFanAI-0.1.29-arm64-release.apk`。

### B-2 车次迁移：生产解析仍会把未知字头静默改成另一趟车

这是当前**最确定、也最容易被用户看到**的"现在就会给出错误答案"缺陷，是改革前必须先修的那一个。

**实测（2026-10-19，离线，`PYTHONPATH=backend`）**

| 输入 | 生产 `extract_train_code` | 新解析器 `fuck_cr.parser` | 后果 |
|---|---|---|---|
| `X1` | `None` | `X1` | 生产不识别 |
| `X1次列车` | **`1`** | `X1` | **查成 1 次车** |
| `V6001` | `None` | `V6001` | 生产不识别 |
| `V6001次` | **`6001`** | `V6001` | **查成 6001** |
| `DH1次` | **`1`** | `DH1` | 查成 1 次车 |
| `AB123次` | **`B123`** | 未知字头，不产出 | 截掉首字母后当成 B123 |
| `B2001` | `B2001` | `B2001`（货运候选） | 语义上仍会被当客运 |
| `71001次` | `None` | `71001`（货运候选） | 生产不识别 |

**根因**

- `_TRAIN_CODE_IN_TEXT_RE`（`backend/app/tools/_rt12306.py:232`）只匹配旧字头集合，且没有"左侧不能紧邻字母/数字"的边界；`_PURE_DIGIT_TRAIN_RE`（同文件 236 行）会把 `X1次` 里的 `1` 当成纯数字车次。
- `extract_train_code` 在整串不匹配后依次降级到这两个正则（`_rt12306.py:249-254`），于是"字母开头但前缀未知"的 token 被截成一段**看起来合法**的数字。
- 这条结果会进入 `retrieve` 的车次归一化（`backend/app/pipeline/retrieve.py:402`），并参与 `_is_train_code`/槽位兜底；`service_dispatch.train_codes` 虽然返回空（不会批量误发），但单车路径仍会带着错误号码去查。

**严重性**

- 它不报错，而是查询另一趟车；答案可能"看起来正常"（有车次、有时刻），用户无从发现。
- 改革后 `X/V/DH` 会变成真实输入；11 月下旬之后这类错误将从"边缘输入"变成"常用输入"。
- `FUCK_CR/04` §2 和 `docs/backend-train-code-reform-preparation-20261017.md` 已经完整记录了它，并明确说"解析加固可以独立上线（纯增益）"。**但至今没有修**，同期落地的是更大的新功能。

**建议（不要求等新版规则）**

最小修复只需两步，且不改变任何现行合法车次的解析：

1. 在 `_TRAIN_CODE_IN_TEXT_RE` 与 `_PURE_DIGIT_TRAIN_RE` 前加左边界约束：前面不能是 `[A-Za-z0-9]`；
2. `extract_train_code` 在"字符串以 ASCII 字母开头"且整串不匹配已知字头时，直接返回 `None`，不要降级到纯数字。

回归必须钉住：`X1次列车 → None`（不是 `1`）、`V6001次 → None`、`DH1次 → None`、`AB123次 → None`、`G1次列车 → G1`、`1461次列车 → 1461`、`T109 → T109`。这条修复应当**先于**语义迁移单独发版（0.1.30），不要和新规则启用捆在一起。

---

## 3. 高优先级

### H-1 语义层、交路闸门、过渡期全部未动，距第一批约 5 周

**现状（工作区 2026-10-19）**

| 触点 | 状态 |
|---|---|
| `_TYPE_KEYWORDS`（`retrieve.py:133`） | 仍是 `高铁→G`、`动车→D`、`特快→T`、`快速→K`、`普速→K,T,Z` |
| `_EMU_TYPE_RE`（`_rt12306.py:196`） | 只认 `G/D/C`；新 `X/T/K` 会被交路闸门拒绝（`retrieve.py:852`、`service_batch.py`、`ticket_pair.py`） |
| 服务准入 | `B/DH/DJ/5 位数字` 没有被区分"可识别"与"可查客运"；新解析器已给出候选域，但没有接入分流 |
| 有效期/历史 | 没有"按乘车日期解释字头"的结构；`effective_date` 只存在于文档，不在代码 |
| 过渡期双表达 | 没有跨日期的"同车不同号"归一化；现存去重按 train_no，不能处理新旧号并存 |
| 启用路径 | `/api/fuck-cr/activation` 固定 409；`FUCK_CR_MODE=reform_2026` 启动即拒绝；`shadow` 只观察 `service_dispatch.train_codes` 一处 |

**改革后的具体错法**（不是推测，是按现行代码直接推演）

- 用户说"普速"→ 仍筛 `K,T,Z`；新体系普速是 `Z,N,V`，旧 `K/T` 已改挂 `V/N` → **漏掉大部分普速车**。
- 用户说"高铁"→ 仍只给 `G`；新 `X/T/K` 也是高速旅客列车 → **漏车**。
- 用户说"动车"→ 仍只给 `D`；新 `D` 只剩动集，200–250 的动车已改 `K` → **语义反了**。
- 用户说"特快/快速"→ 旧 T→N、旧 K→V，而新 T=300、新 K=200–250；同一个词在 11/25 前后指向不同字母 → **必须带日期解释**，不能无条件映射。
- 新 `X/T/K` 车次查担当 → `is_emu_train_code` 为假 → 回答"rail.re 不收录"；实际是新规则未接入，**把能力缺口说成数据不存在**。
- 查询跨 11/24–11/25 的历史/未来日期 → 同一列车两个号，可能被当成两趟；现有"同车不同号"逻辑只处理已知别名，不处理行政改号。

**时间表**

| 批次 | 官方口径 | 精确转述（C 级） | 代码应在此前完成 |
|---|---|---|---|
| 第一批 | 2026 年 11 月下旬 | 货运 11-24 18:00 / 客运 11-25 00:00（《上海铁道》转述） | 解析加固、语义分层、准入分流、日期口径 |
| 第二批 | 2026 年 12 月下旬 | 未公布 | 高速字头切换、双表达去重、文案 |

**建议**

- 不要把"11/25"写成代码里的自动开关；用 `FUCK_CR_MODE` 与显式规则版本控制，默认 `legacy`，`shadow` 先跑对照。
- 先做"识别 → 类别解释 → 数据源准入"三层拆分；`is_emu_train_code` 改成"类别解释 + 供应商能力"两个判断，不要在交路闸门里直接放宽。
- 缓存键加"规则版本/业务日期"；新旧号关联只用于解释，不能跨日期合并结果。
- 先修 B-2，再谈语义；两者分开交付、分开回滚。

### H-2 机读资料的置信度字段与自身注释矛盾

- `FUCK_CR/data/train-code-2026.json` 的 `effective.confidence` 是 **`"A"`**，但同一对象的 `note` 明说两个精确时点来自 C 级转述；`docs/backend-train-code-reform-preparation-20261017.md` §5 已经指出这一点。
- `check.py` 仍然通过（`OK: ... 客运 11 个，映射 17 条，来源 16 条`），因为它只校验结构/引用/覆盖，不校验"字段与注释是否自洽"。
- 目前没有生产代码读这个 `confidence`，所以不是现行 bug；但一旦未来用 `confidence == "A"` 做启用门控，就会**把转述日期当成官方日期**。
- 建议拆成 `window_confidence`（"11 月下旬"=A）与 `exact_time_confidence`（C），并在 `check.py` 增加"精确时点不得为 A"的断言。这是低成本、高杠杆的修正。

### H-3 测试闸门：52/59，且"绿"不代表覆盖

**本次全量结果**

- 通过 52 / 59；失败 7 个，分类如下（详见附录 B）：
  - 环境/网络 5 个：`test_tools`（`example.com` 被本机 DNS 解析到 `198.18.1.22`，被 SSRF 守卫正确拒绝）、`test_api`（rail.re 瞬时不可达）、`test_dict_mileage`（jprailfan `ConnectError`）、`test_frontend_store` / `test_frontend_render`（本机 Homebrew Node 缺 `libsimdjson.33.dylib`）。
  - 测试自身缺陷 2 个：
    - `test_static_assets.py:57` 对 gzip 后的 `FileResponse` 断言 `content-length`；Starlette 0.50 对流式 gzip 会移除该头（我用 `TestClient` 复现：`/` 有，`/v/.../src/main.js` 无）。这是测试假设错误，不是缓存失效。
    - `test_android.py:548` 用 `rglob("*.apk")` 遍历所有 APK，把新出现的 `app-debug-androidTest.apk` 也要求带 `assets/webapp/build.json`；androidTest APK 本来就不该有。应只检查 `app-*.apk` 或按 package 过滤。
- **3 个新增离线套件没接进 `run_all.sh`**：`test_closed_knowledge_tree`（4 项）、`test_prompt_cache_metrics`（5 项）、`test_decision_station_matching`（2 项）。我单独跑均通过——也就是说它们有价值，但电池不会替你跑。
- 共 71 个 `test_*.py`，只接 59 个；未接入的 12 个里还有 `test_github_updates`、`test_photo_annotations`、`test_future_routing_guard` 等。
- 网络套件与离线套件混在同一个清单里，失败时无法从汇总判断"是代码坏了还是网络坏了"；本次 5 个环境失败就是例子。
- `test_dict_mileage` 的失败还暴露一个**代码健壮性**问题：`rail_mileage._station_profile` 对不存在的站名仍去请求 jprailfan，`dict._polite_get` 不捕获 `httpx.ConnectError`，异常直接冒出（`app/data/dict.py:198-215`、`app/tools/rail_mileage.py:202`）。编排层会兜住（trace 记为 failed），但工具契约要求 `ToolResult(ok=False)`，离线部署下这条路径不应以异常收场。

**建议**

- 先把 3 个新套件接进清单，并修掉上面 2 个测试缺陷；
- 把 `run_all.sh` 拆成 `--offline`（CI/断网可跑）与 `--live`（需境内网络/模型），网络失败显式 `SKIP` 并计入汇总；
- 前端套件发现不到可用 Node 时，输出明确 `SKIP` 而不是让整批红；
- 给 `rail_mileage` 的抓取路径加传输错误 → `ok=False` 的收敛。

### H-4 文档索引与口径漂移

- `docs/README.md` 只维护了约 20 条精选；2026-10-13 之后的 **13 份**交接/审计文档全部不在索引里：`backend-decision-speed`、`backend-prompt-cache-measurement`、`backend-provider-wizard-handoff`、`backend-retrieval-audit`、`backend-ticket-shared-rows`、`frontend-provider-setup-wizard-design`、`frontend-siliconflow-wizard-implementation`、`backend-train-code-reform-preparation`、`backend-agent-tools-handoff`、`backend-image-document-input-handoff`、`backend-fuck-cr-button-handoff`、`frontend-inputs-fuck-cr-integration`、`frontend-multi-provider-wizard` 等。
- `docs/EXPL.md:131-132` 的"无网络套件"清单只有 15 个左右，并声称"其余依赖真实 LLM 或境内数据源"；实际上 `test_metrics`、`test_photo_spots`、`test_backend_performance`、`test_pipeline_performance`、`test_local_inference`、`test_static_assets`、`test_llm_providers`、`test_android`、`test_corpus`、`test_phrasings`、`test_fuck_cr*` 都是离线可跑的。这会误导"断网时该跑哪些"。
- `FUCK_CR/INDEX.md:6` 的 front matter 是 `revision: 4`，`:21` 的表格仍写 `revision 3 · 2026-10-17`（CHANGELOG 已记 10-18 rev 3/4）。
- `backend/app/main.py:60` 的 FastAPI `version="0.1.0"` 与 `VERSION=0.1.29` 不一致（仅 OpenAPI 元数据，但会出现在 `/docs`/客户端生成物里）。
- 根 `README.md` 的功能表还没有图片/文档导入、文件草稿导出、主题操作这些已经落地的能力。

这些都不影响运行，但会直接影响"独立评审/外部贡献/故障排查"的可信度；修复成本以分钟计。

---

## 4. 中低优先级

### M-1 匿名面与开放指标

- 服务默认绑定 `127.0.0.1`（`config.py:82`），这对本机/Android 内嵌是合理的；但代码同时允许 `CORS *`（`main.py:73`）且**没有任何认证**。`/api/metrics`（`main.py:118`）公开暴露进程级计数、工具名、供应商 id、缓存名（不含用户文本/密钥）。
- 若用 `HOST=0.0.0.0` 放到局域网，任何同网段设备都能使用服务器上配置的 LLM Key 发起对话，并读取指标。当前配置面里没有认证/共享令牌开关。
- 建议二选一：恢复一个可选的共享令牌/反代鉴权，或在文档与启动日志里明确"仅限回环"，并让非回环绑定必须显式确认。指标端点至少应可选关闭或加同一道门。

### M-2 体积与生成式设计资产

- 0.1.29 release APK **56,351,225 B**，0.1.26 为 41,422,428 B，**+36%**；其中 `lib/arm64-v8a/libmlkit_google_ocr_pipeline.so` 单项 11,064,544 B。离线中文 OCR 是明确需求，这个代价可能是值得的，但应作为产品决策记录，而不是构建时顺带发生。
- release 仍是 `isMinifyEnabled = false` / `isShrinkResources = false`（`android/app/build.gradle.kts:148-149`），R8 未启用。
- `NativeDesignPaths.kt` 1,511 行、**1,465 个 `DesignInkLayer`**，是从 `desi1.png`/`desi2.png`（2.6 MB）逐像素描出来的 Compose 路径。UI 约束文档已在 10-03 放宽"逐像素接近"，这批生成物现在更像历史投资而非必要资产；至少应评估维护成本、启动/滚动开销，并考虑换回矢量图标。

### M-3 隐性开关与仓库噪声

- `MAIN_TICKET_SHARED_ROWS_ENABLED` 由 `os.environ` 直接读取（`app/tools/ticket_snapshot.py:30`），不在 `Settings`、不在 `.env.example`，因此 `test_config_docs` 的 67 项覆盖检查看不到它；`RAILFAN_TESSERACT` 同样只在交接文档里出现。它们有文档，但没有进入"配置真相"。
- `.gitignore` 未覆盖 `.ai/` 与 `acceptance/runs/`，与 B-1 的 status 噪声同源。
- LM 轨把 ML Kit 设为 `compileOnly`，但 `ChatRepository.kt:98` 无条件引用 `ImageTextRecognizer`；只要 LM 路径调用到 `ChatRepository`，就会在无 ML Kit 的 APK 上触发 `NoClassDefFoundError`。当前 LM 走 WebView，未必触发，但这是一条没有编译期保护的耦合。

### M-4 确定性与边界

- `fastpath._station_hits` 在"两个同长度站名"时，回退到 `sorted((n for n in names if n in kept), key=len)`，而 `names` 是 `set`（`fastpath.py:336`）。`set` 的迭代顺序受 `PYTHONHASHSEED` 影响，因此 `hits[0]` 可能随进程变化。当前多站场景会被 `_merge_od` 的歧义门挡住（`len(hits) > 1 → 交回模型`），但 `rule_slots`/`_station_in_text` 仍可能拿到不确定的第一个站。建议显式按"出现位置、再按长度"排序。
- `fuck_cr.parser.parse("0X1")` 返回 `codes=('0X1',)` 且 `status=candidate`，尽管 `_domain()` 对 `zero=True` 返回 `UNKNOWN`。`leading_zero` 被记录了，但状态与 `codes` 没跟上；这类输入应进入 `ambiguous/rejected`，否则影子统计会把 `0X1` 当候选。
- `ticket_snapshot` 的 `RequestStore` 依赖 `ContextVar` 与 `retain_scope` 的借用计数，测试覆盖了取消/并发（12 项），但这是全项目最复杂的生命周期之一；建议在真实高并发下再看一次 `store.finish()` 与工具工厂的时序（本次未做压测）。

---

## 5. 车次改革专项：现在到底缺什么

### 5.1 已具备

- 资料层：`FUCK_CR/` 的 01–06、JSON/CSV、`check.py` 自洽（11 个客运字头、17 条映射、16 个来源，本次实跑通过）。
- 独立解析器：`backend/app/fuck_cr/parser.py` 能保留完整 token、原文字符偏移、未知字头、候选域、歧义尾链；单测 10 项通过。
- 影子观察：`shadow.observe` 在 `service_dispatch.train_codes` 上只记计数，不改路由；`test_fuck_cr` 7 项通过。
- 预备接口：`/api/fuck-cr/status` 与 `/api/fuck-cr/activation` 的 409/200/idempotent 语义有 5 项 API 测试；客户端对 404/409/网络失败的处理有 Kotlin 实现。

### 5.2 未具备（按改革后的用户可见顺序）

1. **生产解析不再截号**（B-2）——最小、最急、可独立上线。
2. **字符头 → 类别 → 日期**的解释层；`普速/高铁/动车/特快/快速/城际` 需要按 `effective_date` 返回不同字母集。
3. **服务准入**：`B/DH/DJ/5 位数字` 不能进客运余票/时刻；`X/T/K` 必须能进交路（否则把"未接入"说成"无数据"）。
4. **过渡期双表达**：同一列车跨 11/24–11/25 的两个号，不能重复计数、不能静默替换用户输入；历史日期按旧语义。
5. **文案与来源**：至少一处能解释"车次变了、车没变"；不能把字头级映射写成逐趟对照。
6. **缓存与词典**：随包词典、GTFS、公布票价保留快照日期；新号缺失时如实缺失，不能拿旧号填补。

### 5.3 建议的门槛（不是日期开关）

- `shadow` 至少覆盖 `train.schedule`、`ticket.query`、`emu.routing`、`service_batch` 四个入口，并能在 `/api/metrics` 看到新旧候选差异；当前只有 `service_dispatch`。
- `reform_2026` 的启用必须同时满足：官方全文或可验证的接口样本 + 逐字段置信度整理 + 回滚演练。
- 先在 **11/24 之前**用真实 12306 返回验证"旧号在新日期是否仍可查""新号在旧日期是否误报"，再决定默认值。
- 明确一条红线：**发布日不是开关**；没有数据源证据就不切默认。

---

## 6. 核实为真的项目声明（正面）

独立评审不能只列问题。以下是我实际核对后确认成立的：

| 声明 | 核对结果 |
|---|---|
| 更新包有 SHA-256 + 签名 + URL 锁定 | 成立：`UpdateClient.kt:88-100` 校验 release/asset/URL/大小/哈希；`SoftwareInstaller.kt:16-27` 校验 package、versionCode、signingCertificateHistory |
| SSRF 守卫覆盖请求级与配置级 | 成立：`providers.guard_request_base_url` 共用；`test_regressions` 10 类目标 + 真实抓取链路 + `test_llm_providers` 均通过 |
| DOCX/DOC 解析有边界与 XXE 防护 | 成立：`agent_documents.py` 限制 zip 条目/解压总量/单文件，禁 `DOCTYPE/ENTITY`，OLE 链与 mini-FAT 有上限 |
| 不泄露 Key | 成立：`/api/providers` 只回 `has_key`；错误文案脱敏；日志无 Key；`test_llm_providers` 有专项断言 |
| 降级不伪装成成功 | 成立：`degraded=True` + `answer_done=False` + `error` 同时可见；`test_mock_render` 17 项 |
| 检索事实/来源/截断契约 | 成立：`test_r1_fixes`、`test_output_guard`、549 项语料断言 |
| 请求内共享售票行 | 成立：`test_ticket_snapshot` 12 项覆盖并发、取消、跨请求隔离、低余量二次校验 |
| 文件/图片工具链边界 | 成立：25 项 agent 测试、9 项文档解析、10 项图片/传输；1 MiB 输出、8 MiB 输入、60000 字符、CSV 公式前缀、ICS 校验 |
| `FUCK_CR` 资料自洽 | 成立：`check.py` 通过；三条硬规矩（不编逐趟表、不把转述当公告、不把 X 当现有车）在文中一致 |
| 0.1.26 的发布记录质量 | 成立：SHA-256、签名证书、词典大小、构建提交、模拟器 5 项、限制说明齐全——这正是 0.1.27–29 缺失的模板 |

---

## 7. 未能验证

- **真机**：本次没有连接设备，也没有安装 0.1.29；文档中的"模拟器 17 项/5 项"不能替代真机。相册/文件选择器、字体缩放、键盘、系统分享、安装器交互均未复测。
- **云端文件/图片端到端**：没有用真实模型跑"上传 → 整理 → 导出"的语义正确率；只验证了离线契约与固定替身。
- **供应商注册/付款/密钥连接**：没有逐家实测；本轮只读代码与测试。
- **改革后的 12306/rail.re 行为**：新规则尚未实施，无法验证新号的真实返回、双表达、历史查询。
- **`FUCK_CR` 的外部信源**：我核对了库内自洽与代码影响，没有重新逐条核验新华社/《人民铁道》原文；资料里的 A/B/C 分级沿用作者判断。
- **全量 Kotlin/Compose 与前端逐行**：本次是抽样审计（Manifest、更新、OCR、设置、WebView、agent.js、store/theme 契约），未覆盖每一个 Composable 和每一条 CSS。
- **前端测试**：本机 Node 动态库损坏，`test_frontend_store`/`test_frontend_render` 无法执行；这是环境限制，不是代码结论。

---

## 8. 建议的执行顺序

| 时间 | 动作 | 完成判据 |
|---|---|---|
| 10/19–10/20 | 冻结写入；按 6 个边界提交；合并回 `main`；干净树重建 0.1.29 | `git status` 干净；`build.json` 无 `-dirty`；tag/Release/verification 齐全 |
| 10/20–10/24 | 解析加固单独发 0.1.30；补回归；修 2 个测试缺陷；接 3 个新套件 | `X1次列车` 不再产出 `1`；`run_all.sh` 52→59 的失败可解释 |
| 10/24–11/07 | 三层拆分（识别/解释/准入）；`effective_date`；`shadow` 扩到 4 个入口；缓存加规则版本 | 新旧对照可观测；旧语料零回退；新号不误进客运 |
| 11/07–11/17 | 过渡期双表达、跨日期、历史查询；真实 12306 样本回填 | 跨 11/24–25 不重复、不替换；文案可解释 |
| 11/17–11/24 | 冻结默认 `legacy`；在测试实例开 `shadow`；回滚演练；准备发布说明 | 回滚 5 分钟内完成；无自动按日期切换 |
| 改革后 | 依据官方全文/逐趟表/接口证据再启用 `reform_2026` | 不以发布日期、按钮或解析器能力作为启用依据 |

**需要拍板的三件事**

1. 是否允许从当前 dirty 树发布 0.1.29（我的建议：不允许；先提交再重建）。
2. 11/25 是否要让新语义默认生效（我的建议：不；默认 `legacy`，按业务日期与证据逐段启用）。
3. 是否接受 +36% APK 体积换离线中文 OCR（如果接受，在发布说明里写明；如果不接受，改成按需下载/Play Services 方案）。

---

## 附录 A：关键复现

```bash
# 快照
git rev-parse HEAD; cat VERSION; git status --porcelain | wc -l
unzip -p dist/android/OpenRailFanAI-0.1.29-arm64-release.apk assets/webapp/build.json

# 解析缺陷
cd backend && PYTHONPATH=. .venv/bin/python - <<'EOF'
from app.tools._rt12306 import extract_train_code
for s in ["X1次列车","V6001次","DH1次","AB123次","G1次列车","1461次列车"]:
    print(s, "->", extract_train_code(s))
EOF
# 实测：1 / 6001 / 1 / B123 / G1 / 1461

# 新解析器
PYTHONPATH=. .venv/bin/python -c "from app.fuck_cr.parser import parse; print(parse('X1次、V6001次、DH1次').codes)"

# FUCK_CR 自洽
cd FUCK_CR && python3 check.py
# OK: ... 客运 11 个，映射 17 条，来源 16 条

# 静态 gzip 头（测试假设错误的最小复现）
cd backend && PYTHONPATH=. .venv/bin/python - <<'EOF'
from fastapi.testclient import TestClient
from app.main import app, _frontend_stamp
with TestClient(app) as c:
    for u in ["/", f"/v/{_frontend_stamp()}/src/main.js"]:
        print(u, dict(c.get(u, headers={"Accept-Encoding":"gzip"}).headers))
EOF
```

## 附录 B：测试结果逐项

| 套件 | 结果 | 归因 |
|---|---|---|
| `test_fuck_cr_api` / `test_fuck_cr` / `test_fuck_cr_parser` | 通过（5 / 7 / 10） | — |
| `test_agent_tools` / `test_agent_documents` / `test_agent_images` / `test_agent_input_routes` / `test_llm_image_transport` | 通过（25 / 9 / 10 / 12 / 9） | — |
| `test_dates` `test_od` `test_context` `test_policy` `test_pipeline` | 通过 | — |
| `test_tools` | 失败 | 本机 DNS 把 `example.com` 解析到 `198.18.1.22`，SSRF 守卫正确拒绝；网络环境 |
| `test_emu_routing` / `test_integration_fullchain` | 通过 | — |
| `test_api` | 失败 | rail.re 瞬时不可达，`emu.routing: failed`，语义断言要求 `ok`；网络 |
| `test_regressions` `test_routing` `test_orchestrator_semantics` `test_cost_governance` | 通过 | — |
| `test_metrics` `test_photo_spots` `test_backend_performance` `test_rt_performance` | 通过 | — |
| `test_llm_pool` `test_pipeline_performance` `test_local_inference` `test_output_guard` `test_hardening` | 通过 | — |
| `test_config_docs` | 通过（67 项） | — |
| `test_product_fixes` `test_r1_fixes` `test_r1_fixes2` `test_station_quality` | 通过 | — |
| `test_rail_line_stations` `test_train_stops` `test_station_screen` | 通过 | — |
| `test_dict_mileage` | 失败 | 无网络时 `jprailfan` `ConnectError` 从工具冒出；同时暴露 `_polite_get` 未收敛传输错误 |
| `test_perf_fastpath` | 通过 | — |
| `test_static_assets` | 失败 | 测试对 gzip `FileResponse` 断言 `content-length`（Starlette 0.50 不提供）；测试缺陷 |
| `test_llm_providers` | 通过 | — |
| `test_android` | 失败 | `rglob("*.apk")` 把 `app-debug-androidTest.apk` 当成应用包；测试缺陷 |
| `test_frontend_store` / `test_frontend_render` | 失败 | 本机 Node 缺 `libsimdjson.33.dylib`；环境 |
| `test_phrasings` `test_corpus` `test_mock_render` `test_multiturn_slots` `test_slm_hardening` | 通过（549 / 68 等） | — |
| `test_ticket_*` `test_fare_*` `test_executed_fare` `test_ticket_snapshot` `test_multidate_services` | 通过 | — |

未接入 `run_all.sh` 但单独通过：`test_closed_knowledge_tree`（4）、`test_prompt_cache_metrics`（5）、`test_decision_station_matching`（2）。
