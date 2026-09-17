# RailFanAI — 项目现状与系统总览

> 实现总览：架构、工具清单、关键设计约束与代码入口。数字以仓库代码与测试为准。

## 一、项目定位

中国铁路爱好者领域的 **RAG / Agent 对话助手**：用户输入一句自然语言，系统按三层流水线 **意图分类 → 关键信息抽取 → 数据检索（真实数据源） → 回答生成** 处理。数据能力覆盖车次担当车组（rail.re）、两站余票与车次经停（12306）、径路与里程（黄河铁路网）、车站当日到发（12306 车站大屏）、站点/拍车点检索；参数类问题走网络搜索 + 模型知识并标注不确定性。

> **使用声明**：本项目**可自由使用（含商业用途，代码许可见仓库 `LICENSE`）**，按「现状」提供、**不提供任何担保**，因使用产生的后果由使用者自行承担；**不得滥用**（高频/并发抓取、绕过限流与反爬、整表转载或转售第三方数据）；数据来源与抓取纪律见 `docs/datasources.md`，完整免责声明见应用内「免责声明」页（`#/doc/disclaimer`）。

## 二、技术栈与运行前提

| 项 | 值 |
|---|---|
| 后端 | **Python 3.12**（要求 ≥ 3.10）、FastAPI、**pydantic v1**（`from pydantic import BaseSettings`，**刻意不用 v2**：Android 一体化把 Python 运行时随 APK 分发，v2 依赖的 `pydantic-core` 是 Rust 扩展，Chaquopy 上没有可用轮子；见 `backend/app/config.py` 开头） |
| LLM / 数据源库 | OpenAI 兼容（`AsyncOpenAI`），**多供应商 + 双 API 方言**：默认供应商沿用 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`，另可用 `LLM_PROVIDER` 选 11 家内置目录之一，或用 `LLM_PROVIDERS` / `LLM_PROVIDERS_FILE` 添加自定义供应商；`api=auto` 时先发 `/chat/completions`，遇 404/405 自动改发 `/responses` 并缓存结论（两种方言的响应结构与 usage 字段名不同，已在 `app/llm/client.py` 抹平）；用户可在界面「设置」里用自己的 Key（BYOK），随请求下发、服务端不落库。`LLM_MOCK=true` 走确定性本地 mock；依赖 `mcp-server-12306`（12306 实时）、`httpx[http2]`、`brotli`（浏览器级请求头所需）、`pypinyin`（站名同音纠错） |
| 前端 | 原生 HTML + JS（无构建工具），由 FastAPI 静态托管，SSE 流式；**移动优先三端自适应**，支持多对话并存；**深浅色默认跟随系统**（设置页「外观」可选 跟随系统/浅色/深色；Android 上要配合应用主题的 `isLightTheme`，见 `docs/android.md`） |
| 网络前提 | **中国境内出口**（12306 与 rail.re 均仅境内可达） |

一键启动（细节见 `docs/run.md`）：

```bash
bash scripts/setup.sh    # 建 venv → 装依赖 → 跑测试自检 → 启动并打开浏览器
cd backend && PYTHONPATH=. .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000   # 或手动启动
```
若系统 Python 为 3.9：`brew install python@3.12 && bash scripts/upgrade_python.sh`。

## 三、目录与代码量

实测（排除 `backend/.venv/`）**约 15513 行**：`backend/app/` 7872、`backend/tests/` 5068、`frontend/` 2074（`index.html` 342 + `main.js`/`pages.js`/`store.js` 1650 + `tests` 81）、`scripts/` + `tests/run_all.sh` 499；统计口径 `find <dir> -name '*.py' -o ... | xargs wc -l`（前端含 `tests/`）。

```
backend/app/
  main.py / config.py      # 入口（/health、挂 /api、静态托管前端）/ pydantic v1 读根目录 .env
  dates.py / od.py         # 日期归一化（今天/明天/9月14日 → YYYY-MM-DD）/ 起讫站解析（"北京到上海"）
  context.py / models.py   # 多轮上下文裁剪（最近 6 条 · 单条 800 字 · 合计 3000 字）/ ChatRequest·PipelineResult
  api/chat.py              # POST /api/chat + /api/chat/stream(SSE) + GET /api/sessions
  llm/client.py / _mock.py # chat / chat_with_reasoning / chat_structured / stream_completion / 确定性 mock
  data/train_db.py         # 离线车次目录（仅起止站推断兜底，非实时）
  pipeline/                # intent 意图 / extract 槽位 / retrieve 路由 / generate 生成 / schemas / orchestrator 编排
  tools/                   # 16 个工具（见 §四）
backend/tests/             # 测试套件（套件清单见 §六，命令见 docs/run.md）
frontend/                  # index.html + src/{main,store,pages}.js + tests/
docs/                      # README(索引) / EXPL(本文件) / run / datasources
scripts/                   # setup.sh（一键安装启动） / prewarm.sh（预热离线数据） / upgrade_python.sh
```

## 四、工具清单（16 个，全部已注册并验证）

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
| `web.search` / `web.fetch` / `cnrail.map` | Bing 中国（主）+ 百度（兜底）/ 任意 URL / cnrail.geogv.org | 通用搜索（无需 API Key）；**命中后还会抓前几条结果的网页正文**（`WEB_SEARCH_FETCH_TOP_N`，因为引擎摘要只有 300 字、百度甚至没有摘要）/ 抓取标题与正文摘要 / 生成地图外链（**仅拼 URL，不抓数据**） | 无 |
| `railre` / `jprailfan` | rail.re 页面 / 黄河铁路网 | 页面摘要（**已降级**：主站为 SPA，`/{站名}` 恒 404；交路数据请用 `emu.routing`）/ 客里表·电报码 | 站点可达 |
| `freight.95306` / `kmrail.freight` / `sytlj.ticket` | 95306.cn / kmrail.cn / kyfw.sytlj.com | 货运与路局查询（站点可达性受限，已优雅降级） | 站点可达 |

> 关键设计：实时能力统一走 `mcp-server-12306`。`t12306.search_tickets` 是**备选路径**，需自备 `T12306_BASE` 反代，未配置时停用、**路由不主动调用**。

## 五、三层流水线与关键设计约束

1. **编排**：`orchestrator.run_stream()`（流式 SSE）/ `run()`（块式 JSON）。
2. **检索**：`retrieve.py` 按 intent 路由到工具；失败不影响整体，记录 note 交由生成层如实说明。
3. **生成**：`generate.py` 注入"事实块"，每条事实自带**来源**与**时效说明**，避免把一个工具的陈旧数据警告错误套用到另一个工具的实时数据上。
4. **用量记录**：`llm/client.py` 运行级累加 tokens + latency；流式 usage 按**增量**计（chunk 为累计值）；统计状态用 `contextvars.ContextVar` 隔离并发请求。

**数据完整性契约（`tools/base.py`）**：`ToolResult` 携带 `total`（命中总数）/ `shown`（实际下发）/ `truncated`（`shown < total`），**截断必须如实登记并显式标注省略条数，禁止"截断冒充缺失"**。表格类事实走结构化投影渲染（字段投影 + 行数上限 `FACT_TABLE_MAX_ROWS=40` + 显式省略标注 + 分布统计），取代字符硬截。

**分类型作答策略（`generate.answer_policy()`）**：

| 类型 | 准入的知识来源 | 强制约束 |
|---|---|---|
| `realtime`（默认） | **仅检索事实** | 不得用模型记忆补充时刻/余票/担当/里程 |
| `knowledge` | 检索事实 + **模型知识** | 模型知识须标注"（据模型知识，未检索确认）"；编号/数值给不确定度 |
| `mixed` | 两者分段使用 | 实时部分闭卷、知识部分标注，分开陈述 |

- 判定与意图分类**同一次调用**产出；**未知/缺省一律回退 `realtime`（从严）**；知识型问题检索改走 `web.search`
  （实时工具对其不适用，例如"样车车组号"查 `emu.routing` 必然落空）；前端对知识型/混合型回答显示"含模型知识，请核实"徽标。

**口径与红线**：

- `rail.line`（`shrtroute`）= **两站间最短径路 / 高铁口径**：`北京→上海 = 1320 km / 17 条线路 / 18 站`。
- `rail.line_stations`（`desgroute`）= **指定径路 / 既有线口径**：`京沪线 = 57 站`、`北京→上海 全程 1463 km`。**两个口径分别回答不同问题，不可混用。**
- `station.screen` 只给车底**型号**（`CR400BF-S`），不是车组号（`CR400BFA-5159`），**不能取代 `emu.routing`**。
- `station.screen` 返回空数组是三义歧义（超窗口 / 电报码非法 / 确实无车），**不得断言"该站当日无车"**。
- `cnrail.map` 是"伪接口"：只做 URL 拼接，不抓数据（真实地图数据在 `railmap.geogv.org`）；离线车次目录为 2022 年数据（12306 停更的 `train_list.js`），仅用于**起止站推断**与模糊搜索，返回时必须标注"非实时"。
- 数据源（rail.re / 12306 / 黄河铁路网）不可达时如实说明缺口并给方向性建议，**不编造数据**；Python 要求 **≥ 3.10**：macOS 上 3.9 的 LibreSSL TLS 指纹会被 12306 反爬拦截；代码保留 `from __future__ import annotations` 以兼容 PEP 604 写法。

## 六、测试

`bash backend/tests/run_all.sh` 运行 `backend/tests/` 下全部套件（**跑完全部套件再汇总**，任一套失败则退出码为 1）。

| 类别 | 套件与覆盖要点 |
|---|---|
| 基础 / 流水线 | `test_dates` `test_od` `test_context` `test_policy` `test_pipeline` `test_integration_fullchain` `test_api` `test_perf_fastpath` `test_phrasings`：日期归一化（大后天/下周X/非法回落）、起讫站解析、上下文裁剪、作答策略（knowledge 放宽 + **未知回退 realtime**）、三层流水线（假 LLM）+ 降级 + SSE 事件序列、整链 prompt 注入、HTTP 多轮/422/**提前中断**/按类型分流、确定性快路径/合并调用/投机预取 |
| 工具与数据 | `test_tools` `test_emu_routing` `test_train_stops` `test_station_screen` `test_rail_line_stations` `test_dict_mileage` `test_station_quality`：16 工具逐个调用、rail.re 交路、车次经停（**权威 train_no 纠正离线目录**、D06 红线、余票不可用时仍给经停）、车站大屏（方向判定、`----`→None、空结果三义歧义、车底后缀=**定员**）、按线路名查站序、本地字典、站序排序与 pypinyin 同音纠错（太安→泰安） |
| 回归 | `test_regressions` `test_product_fixes` `test_r1_fixes` `test_r1_fixes2`：脱敏、非对象 JSON、流关闭、SSRF、体积上限、搜索相关性、交路一致性、日志净化；车迷测试集 17 项；R1 的 9 项 + 第 2 批 10 项（D06 次日值隔离、跨日期/自造印证禁令、完整性契约、反推禁令） |
| 语义与治理 | `test_routing` `test_orchestrator_semantics` `test_cost_governance` `test_hardening` `test_config_docs` `test_frontend_store`：mock 路由不伪造起讫站、块式/流式故障语义一致与恰好一次 `done`、历史只注入一次 + 工具并发保序、径路多候选/里程口径/缓存 TTL、配置与文档一致性；前端数据层 node 直跑（22 项：多对话 CRUD、消息上限、思考/日志截断、主题持久化） |

- **无网络套件**（断网/CI 可跑，前端套件需 node）：`test_regressions` `test_routing` `test_orchestrator_semantics` `test_cost_governance` `test_hardening` `test_config_docs` `test_product_fixes` `test_r1_fixes` `test_r1_fixes2`
  `test_station_quality` `test_rail_line_stations` `test_dict_mileage` `test_perf_fastpath` `test_frontend_store`；其余依赖真实 LLM 或境内数据源。
- 真实模型联调通过（SiliconFlow / DeepSeek-V4-Flash）；意图分类 6 用例 × 3 次 = **18 次判定 100% 一致**（单次仍可能漂移，`test_api` 语义断言用有限重试，结构断言仍为硬断言）。

## 七、代码导读（关键文件入口）

**核心链路**（`backend/app/`）：`pipeline/orchestrator.py`（编排，流式/块式）、`pipeline/generate.py`（**`answer_policy()` 分类型策略**）、`pipeline/retrieve.py`（按意图/类型路由）、`api/chat.py`（SSE + **客户端断开即停止生成**）、`llm/client.py`（LLM 封装、多轮 messages、用量记录）。

**数据源工具（重点）**：`tools/emu_routing.py`（rail.re 交路）、`tools/rail_line.py`（径路解析，含 800KB 页面抗噪定位）、`tools/_rt12306.py`（12306 实时共享助手）、`tools/_http.py`（浏览器级请求头 + `format_error()`）、`tools/registry.py`（工具注册表）。

**前端**（`frontend/`）：`src/main.js`（hash 路由 `#/c/<id>` `#/doc/<key>`、SSE 消费、对话列表、AbortController、编辑重发）、`src/store.js`（对话/主题持久化 + 容量上限；持久化后端可切换：浏览器用 localStorage，Android 用原生桥写的应用私有文件）、`src/native.js`（原生桥的 JS 契约，未接桥时全部安全退化）、`src/theme.js`（外观三档：跟随系统/浅色/深色；单独成模块是为了能脱离浏览器用 node 跑测试）、`src/markdown.js`（回答正文的轻量 Markdown 渲染，含 GFM 表格；单独成模块是为了能脱离浏览器用 node 跑测试）、`src/throttle.js`（流式渲染的合并/限频）、`src/pages.js`（帮助/免责/联系静态页 + 设置页：供应商 BYOK、模型探测、外观、关于卡片）、`index.html`（移动优先三端自适应 UI）、`tests/*.test.mjs`（node 直跑前端单测：数据层 / Markdown / 节流 / 主题）。

**配套文档**：`docs/datasources.md`（数据源端点、参数、字段与踩坑）、`docs/run.md`（安装、配置、启动、测试、数据准备）。
