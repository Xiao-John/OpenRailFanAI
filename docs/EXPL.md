# RailFanAI — 项目现状与系统总览

> 本文件是项目的现状权威说明，所有数字与结论均基于对仓库的**实际执行与读取**。
> 最后更新：M10 完成后。

---

## 一、项目定位

中国铁路爱好者领域的 **RAG / Agent 对话助手**。用户输入一句自然语言，系统按三层流水线处理：

**意图分类 → 关键信息抽取 → 数据检索（真实数据源） → 回答生成**

典型问题与真实数据能力：

| 问题 | 数据源 | 状态 |
|---|---|---|
| G1 今天由哪组动车组担当？ | rail.re 交路 API | ✅ 实时 |
| 明天北京到上海的高铁余票 | 12306 leftTicket | ✅ 实时 |
| G1 明天几点发车、经停哪些站 | 12306 leftTicket + queryByTrainNo | ✅ 实时 |
| 北京到上海走哪条线路 / 多少公里 | 黄河铁路网旅客径路查询 | ✅ |
| 吉林市 XX 区能拍 CR400AF 吗 | 站点库 + cnrail 地图 + 车型交路 + 搜索 | ✅ |
| CR400AF 用哪个品牌的动力系统 | 网络搜索 + 模型知识（标注不确定性） | ✅ 知识型 |

LLM 可用真实 OpenAI 兼容接口（SiliconFlow / DeepSeek-V4-Flash）或 `LLM_MOCK=true` 本地确定性 mock。

---

## 二、技术栈与运行前提

| 项 | 值 |
|---|---|
| 后端 | **Python 3.12**（要求 ≥ 3.10，见 §八-13）、FastAPI、pydantic v2、pydantic-settings |
| LLM | OpenAI 兼容（`AsyncOpenAI`）；`LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` |
| 数据源库 | `mcp-server-12306`（12306 实时）、`httpx[http2]`、`brotli`、`pypinyin`（站名同音纠错） |
| 前端 | 原生 HTML + JS（无构建工具），由 FastAPI 静态托管，SSE 流式；**移动优先三端自适应**，支持多对话并存 |
| 网络前提 | **中国境内出口**（12306 与 rail.re 均仅境内可达） |

### 一键启动

```bash
bash scripts/setup.sh          # 建 venv → 装依赖 → 跑测试自检 → 启动并打开浏览器
# 或
cd backend && PYTHONPATH=. .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

若系统 Python 为 3.9：`brew install python@3.12 && bash scripts/upgrade_python.sh`。

---

## 三、目录与代码量

实测（排除 `backend/.venv/`）：**约 15513 行**

| 部分 | 行数 |
|---|---|
| `backend/app/`（含 tests 之外的全部后端） | 7872 |
| `backend/tests/` | 5068 |
| `frontend/`（`index.html` 342 + `main.js`/`pages.js`/`store.js` 1650 + `tests` 81） | 2074 |
| `scripts/` + `tests/run_all.sh` | 499 |

> 统计口径：`find <dir> -name '*.py' -o ... | xargs wc -l`（前端含 `tests/`）。

```
backend/app/
  main.py               # 入口：/health、挂 /api、根路径静态托管前端
  config.py             # pydantic-settings，读根目录 .env
  dates.py              # 日期归一化（今天/明天/9月14日 → YYYY-MM-DD）
  od.py                 # 起讫站解析（"北京到上海" → (北京, 上海)）
  context.py            # 多轮上下文裁剪/格式化（最近 6 条 · 单条 800 字 · 合计 3000 字）
  models.py             # ChatMessage / ChatRequest(含 history) / PipelineResult(含 question_type)
  api/chat.py           # POST /api/chat + /api/chat/stream(SSE) + GET /api/sessions
  llm/client.py         # chat / chat_with_reasoning / chat_structured / stream_completion（支持 history）
  llm/_mock.py          # LLM_MOCK=true 时的确定性 mock
  data/train_db.py      # 离线车次目录（仅起止站推断兜底，非实时）
  pipeline/
    intent.py           # 意图 + question_type 分类
    extract.py          # 槽位抽取（支持上下文继承）
    retrieve.py         # 按意图路由工具（M10 起按 question_type 调整策略）
    generate.py         # prompt 构造 + answer_policy（分类型作答策略）
    schemas.py          # 意图/槽位 JSON Schema
    orchestrator.py     # 编排：run() 块式 / run_stream() 流式
  tools/                # 16 个工具（见 §四）
backend/tests/          # 测试套件（见 §五）
frontend/               # index.html + src/{main,store,pages}.js + tests/
docs/                   # README(索引) / EXPL(本文件) / plan / run / datasources / keysetsug / testset
                        #   / 数据源与协议反查报告 / 测试与验证记录 / CONTRIBUTING
scripts/                # setup.sh（一键安装启动） / prewarm.sh（预热离线数据） / upgrade_python.sh
```

---

## 四、工具清单（16 个，全部已注册并验证）

### 实时数据源

| 工具 | 数据源 | 能力 | 前置条件 |
|---|---|---|---|
| `ticket.query` | 12306 leftTicket | 两站间实时余票/车次列表 | 境内网络 + Py≥3.10 |
| `train.schedule` | 12306 leftTicket + queryByTrainNo | 车次实时时刻/余票/经停站；**仅给车次可自动推断起止站** | 同上 |
| **`station.screen`** | **12306「车站车次大屏」** | **某站当日全部到发车次**：到发时刻/站台/终到站/**车底型号**/担当客运段·车辆段/当日套跑交路 | 同上 |
| `emu.routing` | **rail.re API** | **车次↔担当车组互查**（12306 不公开此数据；只到"型号"够不着的**单组车号**靠它） | 境内网络 + 浏览器级请求头 |
| `station.lookup` | 12306 站点库 | 站名/拼音/电报码互查（3384 站） | 无 |
| `rail.line` | 黄河铁路网旅客径路查询 | **两站间最短径路**：线路序列 + 车站序列 + 里程（高铁口径） | 境内网络 |
| **`rail.line_stations`** | 黄河铁路网「指定径路查询」 | **按线路名查站序 + 指定径路线路口径里程**（如京沪线 57 站 / 北京→上海 1463km） | 境内网络 |
| **`rail.mileage`** | **本地数据字典**（GTFS 周更快照 + 黄河铁路网客运里程表） | **两站里程 / 线路逐站里程 / 车站档案**（电报码·TMIS 编号·接算站·营业限制）；**毫秒级**（本地缓存，未命中才抓一次） | 无（首次抓取需境内网络） |

### 辅助 / 兜底

| 工具 | 说明 |
|---|---|
| `web.search` | Bing 中国（主）+ 百度（兜底），无需 API Key |
| `web.fetch` | 抓取指定 URL 的标题与正文摘要 |
| `cnrail.map` | 生成 cnrail.geogv.org 地图外链（**仅拼 URL，不抓数据**） |
| `railre` | rail.re 页面抓取（**已降级**：主站为 SPA，`/{站名}` 实测恒 404；交路数据请用 `emu.routing`） |
| `jprailfan` | 黄河铁路网客里表/电报码 |
| `freight.95306` / `kmrail.freight` / `sytlj.ticket` | 货运与路局查询（站点可达性受限，已优雅降级） |
| `t12306.search_tickets` | **备选路径**，需自备 `T12306_BASE` 反代；未配置时停用，**路由不主动调用** |

> 关键设计：实时能力统一走 `mcp-server-12306`；`t12306` 是历史遗留的备选分支。

---

## 五、测试与运行状态

`bash backend/tests/run_all.sh` 汇总运行 `backend/tests/` 下的全部套件（跑完全部套件再汇总，任一套失败则退出码为 1）：

| 测试 | 覆盖 | 网络 |
|---|---|---|
| `test_dates` | 日期归一化（今天/明天/大后天/下周X/非法日期回落） | 无 |
| `test_od` | 起讫站解析（"北京到上海"/"从北京去上海"） | 无 |
| `test_context` | 多轮上下文裁剪/格式化（含非法角色过滤） | 无 |
| `test_policy` | 作答策略（knowledge 放宽 + **未知回退 realtime**） | 无 |
| `test_pipeline` | 三层流水线（假 LLM）+ 降级 + SSE 事件序列 | 无 |
| `test_tools` | **16 个工具**逐个调用 | 需境内网络 |
| `test_emu_routing` | rail.re 交路查询（含车型前缀/单台车组区分） | 需境内网络 |
| `test_integration_fullchain` | 意图→槽位→检索→prompt 注入 | 部分 |
| `test_api` | HTTP 层：多轮、对照组、422、**提前中断**、按类型分流（语义断言带有限重试） | 真实 LLM |
| `test_regressions` | **历史缺陷修复回归**：脱敏、非对象 JSON、流关闭、SSRF、体积上限、搜索相关性、交路一致性、日志净化 | **无** |
| `test_routing` | **检索计划与 mock 路由**：不伪造起讫站、车站意图不再调 railre、knowledge/mixed 分流、mock 只按用户输入判定 | **无** |
| `test_orchestrator_semantics` | **块式/流式故障语义一致**：可操作文案、恰好一次 done、`answer_done` 真伪、`tool_trace` 对齐 | **无** |
| `test_cost_governance` | **上下文开销治理**：历史只注入一次、工具并发且保序、单工具异常隔离、消息长度/空串 422 | **无** |
| `test_hardening` | **P2 加固**：径路多候选表/里程口径/站名后缀重试、URL 转义、缓存不进请求路径与 TTL、脚本自检 | **无** |
| `test_config_docs` | **配置与文档一致性**：`.env.example` 覆盖全部配置项、无未知/重复键、文档引用路径存在 | **无** |
| `test_product_fixes` | **车迷测试集第 1 轮问题的修复回归**（17 项）：日期表、普速车次、站名降级、站点事实路由、车次席别、已发车回退、资讯时效… | **无** |
| `test_r1_fixes` | **RAG 验证 R1 问题修复回归**（9 项）：D06 次日值结构性隔离、跨日期/自造印证禁令、工具侧过滤、完整性契约、表格渲染器、错误透传 | **无** |
| `test_r1_fixes2` | **R1 第 2 批修复回归**（10 项）：原话兜底路由、404→"不存在"、枢纽探测、资讯搜两遍、反推禁令、低余量二次校验 | **无** |
| `test_station_quality` | **站点清单质量与同音纠错**：主要车站按 12306 站序排序（含北京丰台/苏州园区）、pypinyin 同音纠正（太安→泰安）、思考简短指令 | **无** |
| `test_rail_line_stations` | **按线路名查站序/里程（F06+F05）**：desgroute 三步流程、指定径路口径、缓存、路由分流 | **无** |
| `test_train_stops` | **车次经停站**（2026-09-15）：权威 train_no 纠正离线目录错判、图定表直查、余票不可用时仍给经停、D06 红线（默认不给时刻）、真实 12306 联调（G1 应 7 站） | 联调项需境内网络 |
| `test_station_screen` | **车站大屏**（2026-09-15）：方向判定、"----"→None、站台去 `#`、出发/到达屏切分、截断与完整性契约、**空结果三义歧义**（不谎报"该站无车"）、POST/GET 差异、车底后缀=**定员**而非车组号、两车型分歧同时呈现 | 联调项需境内网络 |
| `test_dict_mileage` | **本地数据字典**：里程、车站档案、离线时刻 | **无** |
| `test_perf_fastpath` | **决策层性能**：确定性快路径、合并调用、投机预取 | **无** |
| `test_frontend_store` | **前端数据层**（node 跑 `frontend/tests/store.test.mjs`，22 项）：多对话 CRUD、标题派生、消息上限、思考/日志截断、主题持久化 | **无** |

- 真实模型联调通过（SiliconFlow / DeepSeek-V4-Flash）
- 意图分类稳定性：6 用例 × 3 次 = **18 次判定 100% 一致**
  （注：单次调用仍可能漂移，`test_api` 语义断言已改为有限重试）
- 仓库可整体提交：`.env`/`.venv`/SQLite/缓存/日志均已确认被 `.gitignore` 排除（`git check-ignore` 实测）

---

## 六、核心流程与设计要点

1. **编排**：`orchestrator.run_stream()`（流式 SSE）/ `run()`（块式 JSON）。
2. **检索**：`retrieve.py` 按 intent 路由到工具；失败不影响整体，记录 note 交由生成层如实说明。
3. **生成**：`generate.py` 注入"事实块"，每条事实自带**来源**与**时效说明**，避免把一个工具的陈旧数据警告错误套用到另一个工具的实时数据上。
4. **用量记录**：`llm/client.py` 运行级累加 tokens + latency；流式 usage 按**增量**计（chunk 为累计值）；统计状态用 `contextvars.ContextVar` 隔离并发请求。

### 已实现的 AI 体验能力

| 能力 | 实现方式 |
|---|---|
| 多对话并存 | 对话列表（抽屉式侧栏）内新建/切换/重命名/删除，各对话独立 `messages[]`，落 `localStorage`（`store.js`，含容量上限） |
| 多轮上下文 | 前端维护 `messages[]` 回传 `history`；三层均接入；实测"那明天呢"继承上文 G1 |
| 暂停输出 | 前端 `AbortController`；服务端每事件前检查 `request.is_disconnected()`，断开即停止并释放 LLM 流 |
| 编辑重发 / 重新生成 | 丢弃目标消息之后的内容后重跑；history 正确截断 |
| 主题切换 | 深/浅色跟随系统并记忆（`localStorage`） |
| 分类型作答（M10） | 见下表 |

---

## 七、分类型作答策略（M10，重要）

生成层**不再对所有问题一律"闭卷"**，而是按 `question_type` 分流：

| 类型 | 准入的知识来源 | 强制约束 |
|---|---|---|
| `realtime`（默认） | **仅检索事实** | 不得用模型记忆补充时刻/余票/担当/里程 |
| `knowledge` | 检索事实 + **模型知识** | 模型知识须标注"（据模型知识，未检索确认）"；编号/数值给不确定度 |
| `mixed` | 两者分段使用 | 实时部分闭卷、知识部分标注，分开陈述 |

- 判定与意图分类**同一次调用**产出；**未知/缺省一律回退 `realtime`（从严）**
- 知识型问题检索改走 `web.search`（实时工具对其不适用，例如"样车车组号"查 `emu.routing` 必然落空）
- 前端对知识型/混合型回答显示徽标与"含模型知识，请核实"提示

实测对照：
```
"CR400AF用的哪个品牌的动力系统？"  → question_type=knowledge, tools=[web.search]
  答：…牵引变流器由中车时代电气供应（据模型知识，未检索确认）…

"G1今天由哪组动车组担当？"        → question_type=realtime, tools=[emu.routing, train.schedule]
  答：G1 今日由 CR400BFA-5054 担当（来源 rail.re）… 未使用模型记忆补充。
```

---

## 八、已知风险 / 待改进点

### 设计取舍（非缺陷）

1. **数据源受限时的诚实降级**：rail.re / 12306 / 黄河铁路网不可达时，系统如实说明缺口并给方向性建议，**不编造数据**（刻意的产品设计）。
2. **`cnrail.map` 是"伪接口"**：只做 URL 字符串拼接，不抓取任何数据；真实地图数据在 `railmap.geogv.org`（本机环境不可达）。
3. **离线车次目录为 2022 年数据**：`data/.train_cache.json` 源自 12306 停更的 `train_list.js`，仅用于**起止站推断**与模糊搜索，返回时明确标注"非实时"。

### 安全问题

4. **`.env` 含真实 API Key**（已 gitignore，但仍存在于工作区）——
   **已决定：推迟到「部署 / 上线阶段」再改为环境变量注入并轮换**。
   开发期有意保留以便联调；执行步骤见 `docs/keysetsug.md`，
   并已登记在 `docs/plan.md` 的「上线前必做清单」第 1 条。
5. **CORS 全开放**：`main.py` 使用 `allow_origins=["*"]`（`allow_credentials=False`），仅适合开发；对外部署时应改为白名单。
6. **无限流 / 无日志脱敏**：任何能访问端口的人都能调用 LLM（消耗 token）。
   社区版不内置鉴权（`/health` 返回 `auth: "disabled"`），如需对外提供访问，应在反向代理层收敛入口并加限流。
7. **待上线收口的项已集中登记**：见 `docs/plan.md` →「上线前必做清单（Pre-launch Checklist）」
   （含 Key 注入、CORS 收紧、入口限流、HTTPS）。

### 技术债

8. **用量统计准确性**：非流式 `chat_structured` 每跳直接累加 usage；流式按增量。多跳（意图/抽取/生成）合并后的 total 是否等于三跳之和，仍待持续校验。
9. **会话无持久化**：刷新页面即丢失；无跨会话历史（v1 有意为之）。
10. **前端 Markdown 渲染器为自写**：先 HTML 转义再套标签防 XSS，正则覆盖有限，复杂 Markdown 可能渲不全。代码块保护已用 NUL 占位（曾有误伤 bug，已修）。
11. **暂停输出粒度**：服务端在**事件之间**检查断开，若模型单次 chunk 间隔较长，停止最多有 1 个 chunk 延迟。
12. **`rail.line` 只支持两站间径路**：按线路名反查站序（"京沪线经过哪些站"）需该站 `desgroute` 多步交互，已由 `rail.line_stations` 覆盖，路由可按需分流。
13. **Python 版本要求 ≥ 3.10**：macOS 上 3.9 的 LibreSSL TLS 指纹会被 12306 反爬拦截。代码保留 `from __future__ import annotations` 以兼容 PEP 604 写法。
14. **历史质量改进**：开发期累计登记 **80 条缺陷发现**（P0×5 / P1×21 / P2×37 / P3×17），
    其中 **41 项已修复并带回归测试**（含 2 个 P0 数据正确性问题、1 个 P0 前端不可用问题、
    2 个 P0 脚本/文档问题、SSRF 与体积上限、上游错误原文外泄、
    工具并发化与历史去重（开销治理）、URL 转义与缓存治理等）。

---

## 九、代码导读（关键文件入口）

**核心链路**
- `backend/app/pipeline/orchestrator.py` —— 编排（流式/块式）
- `backend/app/pipeline/generate.py` —— **`answer_policy()` 分类型策略**（M10 重点）
- `backend/app/pipeline/retrieve.py` —— 按意图/类型路由
- `backend/app/api/chat.py` —— SSE + **客户端断开即停止生成**
- `backend/app/llm/client.py` —— LLM 封装、多轮 messages、用量记录

**数据源工具（重点）**
- `backend/app/tools/emu_routing.py` —— rail.re 交路（担当车组）
- `backend/app/tools/rail_line.py` —— 径路解析（含 800KB 页面抗噪定位）
- `backend/app/tools/_rt12306.py` —— 12306 实时共享助手
- `backend/app/tools/_http.py` —— 浏览器级请求头 + `format_error()`
- `backend/app/tools/registry.py` —— 工具注册表

**前端**
- `frontend/src/main.js` —— hash 路由（`#/c/<id>` `#/doc/<key>`）、SSE 消费、对话列表、AbortController、编辑重发
- `frontend/src/store.js` —— 对话/主题持久化（localStorage，含容量上限）
- `frontend/src/pages.js` —— 帮助 / 关于 / 免责等静态页面（路由 `#/doc/<key>`，正文为【待补充】占位）
- `frontend/index.html` —— 移动优先三端自适应 UI（抽屉侧栏 + 安全区 + 深/浅色）
- `frontend/tests/store.test.mjs` —— 前端数据层单测（node 直跑，无需构建）

**测试**
- `backend/tests/run_all.sh`（跑完全部套件再汇总；其中 `test_regressions`/`test_routing`/`test_orchestrator_semantics`/`test_cost_governance`/`test_hardening`/`test_config_docs`/`test_product_fixes`/`test_r1_fixes`/`test_r1_fixes2`/`test_station_quality`/`test_rail_line_stations`/`test_dict_mileage`/`test_perf_fastpath`/`test_frontend_store` 无网络）

**配套文档**
- `docs/plan.md` —— 里程碑 M0–M10 + 「上线前必做清单」
- `docs/datasources.md` —— **各数据源逆向过程与踩坑记录**（rail.re API、12306 反爬、搜索引擎选型、径路解析）
- `docs/run.md` —— 运行/联调/交互说明
- `docs/keysetsug.md` —— Key 安全
- `docs/source-expansion.md` —— 数据源扩展评估（待接入源、硬约束、合规红线）
- `docs/troubleshooting.md` —— 排障手册
- `docs/perf-plan.md` —— 响应延迟诊断与优化方案
- `docs/CONTRIBUTING.md` —— 社区版协作说明（测试、代码风格、数据源接入合规要求）
