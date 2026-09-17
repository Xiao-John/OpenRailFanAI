# RailFanAI · 性能 review 与优化方案

> **本文档是一次只读 review 的产出**：没有改动任何代码，只做通读、取样测量与方案设计。
> 所有"实测"数字都标注了测量口径与复现命令；所有"预计收益"都是**基于代码事实的推断**，
> 落地时必须按 §5 的验证方法复测，不要当成结论直接引用。

## 0. Review 范围与方法

| 项 | 说明 |
|---|---|
| 范围 | `backend/app/`（46 个文件 · 10465 行）、`frontend/src/`（5 个文件 · 2464 行）、`frontend/index.html`、`docs/`、`scripts/` |
| 方式 | 通读主干路径（`main.py` → `api/chat.py` → `pipeline/*` → `llm/client.py` → `tools/*` → `data/*`）+ 本地微基准 |
| **未做** | **未跑全量测试套件**（30 个套件 · 8018 行）；只跑了 1 个无网络套件 `tests/test_perf_fastpath.py`（全绿，9 项断言） |
| 未覆盖 | `android/`（含 `build/` 生成物）、`dist/`、`.android-build/`、`referss/`、历史日志 `test-baseline-*.log` |
| 机器 | macOS · Python 3.12 · 本地（首次调用含冷启动，未做多轮预热后再取样） |

### 本文出现的实测数字

| 测点 | 实测 | 代码位置 |
|---|---|---|
| 站点库加载（本地静态表，不联网） | **4.9 ms**（首次） | `app/tools/_rt12306.py:47` `ensure_loaded` |
| `all_stations()` 单次重建（3384 站） | **≈1.04 ms** | `app/tools/_rt12306.py:800` |
| `all_station_names()` 单次重建 | **≈0.30 ms** | `app/tools/_rt12306.py:833` |
| 快路径决策（正则 + 站点库） | **≈0.98 ms** | `app/pipeline/fastpath.py` |
| `rail.mileage` 工具（本地字典） | **≈2.0 ms** | `app/tools/rail_mileage.py` |
| `station.lookup` 工具 | **≈1.5 ms** | `app/tools/station_lookup.py` |
| `retrieve()` 本地开销（工具全部假返回） | **≈0.08 ms** | `app/pipeline/retrieve.py:356` |
| `build_prompt()` | **< 0.01 ms** | `app/pipeline/generate.py:132` |
| `dict.stats()`（6 条 `COUNT(*)`，含 13.1 万行表） | **1.9 ms**（首次 7.8 ms） | `app/data/dict.py:269` |
| `search_lines("", 2000)`（`LIKE '%%'` 全表 + 排序） | **1.02 ms**（首次 8.6 ms） | `app/data/dict.py:193` |
| `dict.db` 规模 | 5 386 站 / 14 674 车次 / 131 247 停站 / 758 线路 | 14 MB，只读连接 |

复现（全部离线，不需要 Key）：

```bash
cd backend && PYTHONPATH=. LLM_MOCK=true .venv/bin/python tests/test_perf_fastpath.py
```

结论先行：**这套代码的本地计算几乎不花时间**（毫秒级，甚至在微秒级）。所有可观的延迟都在
**外部往返**（LLM 调用 + 12306 / rail.re / 黄河铁路网抓取）和**串行的阶段边界**上；
所有可观的可用性风险都来自**同步阻塞调用混进 async 路径**。因此下面的方案按这两条主线展开。

---

## 1. 现状总览

### 1.1 请求主链路与阶段耗时归属

```
POST /api/chat/stream                                   api/chat.py:61
 └─ orchestrator.run_stream                             orchestrator.py:47
     ├─ ①planner.decide          快路径≈1 ms / 合并调用 1 次 LLM        planner.py:70
     │    └─ prefetch.start()    决策走 LLM 时并行预取 1 个工具          prefetch.py:78
     ├─ ②retrieve.retrieve       计划内工具 asyncio.gather（≤3 并发）    retrieve.py:356
     └─ ③generate / stream       1 次 LLM 流式生成                       generate.py:231
```

三阶段**严格串行**；能跨阶段重叠的只有 `prefetch` 一处（且只限"决策走 LLM"这条路）。
外部往返次数：快路径命中 ≈ 1 次生成 LLM + N 次数据源；未命中 ≈ 2 次 LLM（或 3 次，回退到两次调用）+ N 次数据源。

### 1.2 已经做得好的（不要在优化中破坏）

这些是项目里已经落地的性能与治理措施，改造时应保持语义不变：

- **确定性快路径**（`pipeline/fastpath.py`）：覆盖约 56–68% 车迷问法，0 次 LLM 调用，实测 ≈1 ms。
- **合并结构化调用**（`planner.py:100`）：把"意图 + 问题性质 + 槽位"合成 1 次往返，替代原来 2 次。
- **投机预取**（`pipeline/prefetch.py`）：只在 LLM 决策路径上、最多 1 个工具、失败静默、同名同参才复用。
- **工具并发**（`retrieve.py:700-720`）：`asyncio.Semaphore(TOOL_CONCURRENCY)` + `gather`，且保持计划顺序输出。
- **已有 TTL 缓存**（`_rt12306.py`）：原始余票行 300 s、车次内部编号 3600 s、经停 3600 s、车站大屏 600 s、车底详情 3600 s；线路径路 `rail_line.py:192` 默认 3600 s。
- **URL 级 SSRF 校验 + 响应体上限**（`_http.py:38,121`）：默认 2 MB 上限，曾有一个 172 MB 页面把进程 RSS 顶到 920 MB。
- **断流即停**（`api/chat.py:76`）：每个事件前 `request.is_disconnected()`，用户点"停止"立即释放 LLM 流。
- **输入与注入预算**（`config.py:82-97`）：单条消息 2000 字、单条事实 4000 字、表格 40 行、大屏 15 条。

---

## 2. 发现的问题（按优先级）

### P0-1 ★ 同步阻塞调用混进异步工具路径 — **可用性缺陷**

| 项 | 内容 |
|---|---|
| 位置 | `app/data/dict.py:152` `_polite_get()`（同步 `httpx.Client(timeout=120)` + `time.sleep(2)`） |
| 被谁调用 | `app/tools/rail_mileage.py:102`（`D.line_stations` → 经 `:154`）、`:202`（`D.station_profile`）、`:84/:107/:211`（`D.search_lines`）——全部在 **`async def invoke`** 内 |
| 触发条件 | 本地字典未命中：按线路名查逐站里程、查车站档案 |
| 后果 | **阻塞整个事件循环最长约 120 s**（`timeout=120` + 2 s 礼貌间隔）。期间该 worker 上所有并发请求、所有进行中的 SSE 流全部停摆 —— 别的用户看到的是"服务卡死"，日志里没有任何异常 |

**为什么这是本次 review 最该先修的一条**：它不是"慢"，而是"一个请求把服务打停"。
`retrieve` 的并发、`prefetch` 的重叠、SSE 的流式体验，全部依赖事件循环不被占用；
一条同步调用把它们一起作废。

**方案**（保持"对个人站点低频礼貌"的约束不变）：
1. `_polite_get` 改 `async def`，用共享 `httpx.AsyncClient`；`time.sleep(wait)` → `await asyncio.sleep(wait)`；
2. 用模块级 `asyncio.Lock` 串行化"礼貌间隔"（并发调用时排队，而不是各自 sleep 后一起打）；
3. `line_stations` / `station_profile` 的调用方改 `await`；
4. `sqlite3` 查询（`D.stats` / `search_lines` / `station_profile` 的读）虽然只有 1–9 ms，同样建议用 `asyncio.to_thread` 包一层，避免"以后数据变大再回来改"；
5. 若短期不想改签名：最低限度也用 `await asyncio.to_thread(D.line_stations, line)` 兜住，能立刻消除阻塞（线程池默认 40 线程，足够）。

**风险**：低。纯 IO 语义变更，不改数据口径。需回归 `tests/test_dict_mileage.py`、`tests/test_rail_line_stations.py`。

---

### P0-2 每次取数都新建 HTTP 客户端 — 无连接池

| 项 | 内容 |
|---|---|
| 位置 | `tools/_http.py:136`、`:257`；`tools/_rt12306.py:255,377,611,729`；`tools/emu_routing.py:139`；`tools/rail_line.py:368`；`tools/web_search.py:167`；`tools/t12306.py:53`；`data/train_db.py:34` —— **7 个模块、10+ 处** |
| 后果 | 每个工具调用都重付 TCP 三次握手 + TLS 握手（境内 12306 / rail.re 上约 100–300 ms 量级）；并发的 3–4 个工具各开各的池子 |
| 附加成本 | `_http.py:123` 的 `assert_public_url()` 每次调用额外做一次 `socket.getaddrinfo`（**阻塞**，且与随后 httpx 自己的解析重复一遍） |

**方案**：
1. 建 `app/tools/_http.py` 内的**共享 AsyncClient**（懒建）：
   `Limits(max_connections=20, max_keepalive_connections=10, keepalive_expiry=90)`、`http2=True`、`trust_env=False`（**语义必须保留**，见 `_http.py:129-135` 的注释：走代理会让 SSRF 校验形同虚设）；
2. 各工具模块改为引用共享 client，不再各自 `async with httpx.AsyncClient(...)`；
3. 在 FastAPI `lifespan` 结束时 `aclose()`，避免"未关闭的 client"告警；
4. DNS：把 `assert_public_url` 的解析结果加一个短 TTL（如 60 s）的小缓存，或按 host 复用同一 client 让 httpx 自己复用连接（连接复用后不再每次解析）。

**预计收益**：单次工具往返省一次握手；**并发场景下省的是"最慢那条"的墙钟时间**，因此对 `photo_spot`（4 个工具）这类计划收益最大。

**风险**：中。共享 client 后 `follow_redirects` / `headers` 的差异需要逐个工具核对（有的工具依赖自定义 UA/Referer，这些应走 per-request `headers=`，不要写进 client 默认值）。

---

### P0-3 12306 每次查询都先打一次 init 种 Cookie

| 项 | 内容 |
|---|---|
| 位置 | `_rt12306.py:243-300` `query_ticket_rows()`：每次缓存未命中都 `await client.get(_LEFT_TICKET_INIT)` 再打 `queryI` |
| 后果 | 每个余票/车次查询多一次完整往返；`train.schedule` + `ticket.query` 同时下发时这笔开销翻倍 |
| 相关 | `_TRAIN_ID_CACHE`（`:341`）的 key 是 `(车次, 日期)`，但 TTL 3600 s 且车次内部编号在**调图时会变**，缓存里没有"跨天失效"的显式语义 |

**方案**：把 Cookie jar 挂在共享 client 上（`cookies=` 复用），`init` 只在"缺 Cookie / 返回 302 空结果"时补打；缓存 key 补上日期维度或让 TTL 在跨天时失效。

**预计收益**：每个余票类查询省 1 次往返。**风险**：中 —— 12306 对 Cookie 与 UA 的一致性敏感，改完必须实跑 `tests/test_tools.py`（需境内网络）。

---

### P1-1 前端流式渲染是 O(n²) 重排

| 项 | 内容 |
|---|---|
| 位置 | `frontend/src/main.js:779` 与 `:845`：每个 `answer` delta 都 `refs.ans.innerHTML = renderMarkdown(answerRaw) + caret` |
| 后果 | 长回答（逐站列出、表格）时，**每个 token 重新解析整篇 Markdown 并整体重排 DOM**；移动端 WebView 上表现为滚动卡顿、键盘跟随迟滞 |
| 相关 | `persistThrottled`（`:704`）已经按 1.5 s 节流落盘 —— 说明团队已经意识到"每次都做全量操作"的代价，但渲染这一侧还没做 |

**方案**：
1. 用 `requestAnimationFrame` 合并渲染（每帧最多一次），delta 只累加到字符串；
2. 流式期间渲染"最后一个未闭合块的尾部 + 已闭合块"，结束时再做一次完整渲染（避免未闭合表格在流式中反复重排）；
3. 给 `scrollBottom()` 加同样的帧节流，避免与渲染互相触发。

**预计收益**：长回答的流式帧率；桌面端感知不明显，Android / 移动浏览器上明显。**风险**：低。

---

### P1-2 静态资源交付未压缩，且对所有资源用 `no-cache`

| 项 | 内容 |
|---|---|
| 位置 | `app/main.py:99-121` `_NoCacheStatic`：给**所有**静态响应加 `Cache-Control: no-cache` |
| 现状体积 | `index.html` 33 KB + `src/main.js` 42 KB + `pages.js` 33 KB + `store.js` 18 KB + `markdown.js` 4.6 KB ≈ **130 KB 未压缩** |
| 缺失 | `main.py` 里**没有** GZip 中间件（`grep -c GZip app/main.py` = 0） |

**关于 `no-cache` 的正确性**：文件里写了理由（无构建步骤、URL 跨安装不变、Android 上端口固定）——
这条理由对 **`index.html`** 成立，必须保留；但对 `src/*.js` 一并施加就过度了。

**方案**：
1. `index.html` 保持 `no-cache`（回源校验，成本可忽略）；
2. `src/*.js` 加 `?v=<VERSION>`（`VERSION` 已是单一来源，见 `main.py:47` 与 `docs/EXPL.md`）后给 `public, max-age=31536000, immutable`；
3. 加 `fastapi.middleware.gzip.GZipMiddleware(minimum_size=1024)`（纯回环场景收益小，但 Android WebView 与局域网部署下明显）；
4. 顺带：`/health` 每次读一次 `VERSION` 文件（`main.py:55`）—— 可忽略，但既然要动静态层，一并缓存在进程内更干净。

**预计收益**：首屏与更新后的首次加载。**风险**：低——唯一要小心的是"改了 JS 但版本号没变"会拿到旧缓存，因此必须让版本号进入构建产物（Android 侧已有这条纪律，见 `docs/android.md`）。

---

### P1-3 预取"打空"不可回收，且并发同参请求不合并

| 项 | 内容 |
|---|---|
| 位置 | `pipeline/prefetch.py`（每次请求新建 `Prefetch`，命中即复用，未命中只能 `cancel`） |
| 现象 | 预取只在**同一次请求**内生效；跨请求的"同一车次/同一区间"重复查询（用户连问、"那明天呢"追问）每次都重新打外部站点 |
| 缺口 | 没有 `(tool, params)` 级别的 **in-flight 去重**（同一瞬间两个请求查同一车次 → 两次外部调用） |

**方案**：
1. 在 `registry.invoke_by_name` 之上加一层进程内 `single-flight`：同 `(tool, params)` 的并发调用共享同一个 `Task`；
2. 加**按工具分类的结果 TTL 缓存**（实时类 15–60 s、字典/交路类长 TTL），key 用规范化后的参数；
3. 复用现成的 `ToolResult` 结构，直接把 `fetched_at` 如实带出（否则"缓存命中"会让用户以为数据是刚刚采的 —— 本项目对"截断/时效如实标注"有明确纪律，缓存必须同样标注）。

**预计收益**：追问场景最明显（"那明天呢" → 第二轮几乎全部命中缓存）。**风险**：中 —— **缓存必须不影响"实时性"的语义**，余票/大屏类 TTL 要短，且要在 `note` / `integrity_line()` 里如实体现采样时刻。

---

### P2-1 `all_stations()` 在热路径上被重建多次

| 项 | 内容 |
|---|---|
| 位置 | `_rt12306.py:800` 每次调用重建 3384 条 dict；调用点：`fastpath.py:128`、`retrieve.py:314`、`station_lookup.py:46,68,107` |
| 实测 | **≈1.04 ms/次**，一次请求调 2–4 次 → 2–4 ms |
| 相关缺陷 | `station_lookup.py:28` 的 `_PY_INDEX_READY` 一旦置真就**永不失效**（站点库更新后拼音索引仍是旧的） |

**方案**：给 `all_stations()` 加"站点库版本号 / 加载序号"失效的进程内缓存（不是裸 `lru_cache` —— 测试会替换站点库）。**预计收益**：每请求 2–4 ms 的确定性下降，并把上面那条陈旧索引问题一并修掉。**风险**：低。

---

### P2-2 工具并发度与超时粒度

| 项 | 内容 |
|---|---|
| 位置 | `config.py:86` `tool_concurrency: int = 3`；`retrieve.py:700` |
| 现象 | 最常见的计划恰好是 3–4 个工具（`photo_spot`：lookup + cnrail + emu/routing + web.search），第 4 个被排到第二批 |
| 缺口 | 所有工具共用 `HTTP_TIMEOUT=12.0` 一把尺子：本地字典该 1 s，12306 该 8 s，个人站点该 20 s |

**方案**：并发度提到 4；按工具声明超时档位（`Tool` 基类加 `timeout_s`，或按 host 分级）。**预计收益**：4 工具计划省一批次。**风险**：低（但要复查 `tests/test_cost_governance.py` 里是否钉死了 3）。

---

### P2-3 生成侧 token 预算与重试放大

| 项 | 内容 |
|---|---|
| 位置 | `llm/client.py`：参数降级阶梯 `_MAX_ATTEMPTS = 5`，每次尝试**重发整个 prompt**；`config.py:88-90` 的三项上限是**按条**（4000 字/条、40 行/表）而不是**总量** |
| 现象 | 工具多时事实块总量无上界，输入 token 抬高首 token 延迟；上游不认某参数时最多 5 次全量重发 |
| 相关 | `llm_context_tokens`（`:63`）已用于把输出预算限制在窗口内，但没有对**输入侧事实总量**做预算 |

**方案**：加"注入事实总字节/总 token 预算"，超出时按工具优先级在**工具边界**上裁剪（而不是在条内二次截断，避免"截断冒充缺失"回归）；重试阶梯保留，但重试前先按已定位的参数裁剪 prompt。**风险**：中 —— 这条与项目的"数据完整性契约"直接冲突，改动必须保持 `total/shown/truncated` 语义。

---

### P2-4 缺少聚合性能观测

| 项 | 内容 |
|---|---|
| 现状 | `done` 事件已透出 `latency_ms` 与分阶段 `stage.ms`（`orchestrator.py:98-113`），`llm_client` 已统计 token 与 LLM 耗时；**但没有聚合出口**，这些数字只活在前端气泡和服务端日志里 |
| 后果 | 项目里"实测 3.8–11.6 s""56–68% 命中率"这类关键数字只能靠注释和一次性脚本维持；每次优化都缺少回归基线 |

**方案**：加只读接口 `/api/metrics`（或 `/api/perf`）：快路径接管率、预取命中/打空率、分阶段 p50/p95、各工具成功率与耗时、缓存命中率、LLM 重试次数。**纯增量，不改请求路径。** **风险**：低 —— 注意别把用户输入/Key 带进指标（项目对日志脱敏已有纪律，见 `api/chat.py:37` `_safe_label`）。

---

## 3. 方案总表

| 编号 | 项 | 类型 | 预计收益 | 改动面 | 风险 | 依赖 |
|---|---|---|---|---|---|---|
| P0-1 | 解除 `dict._polite_get` 的同步阻塞 | **可用性** | 消除最长 120 s 全服务停摆 | `data/dict.py` + `tools/rail_mileage.py` | 低 | — |
| P0-2 | 共享 HTTP 连接池 | 延迟 | 每工具省一次 TLS 握手 | 7 个模块 | 中 | 需核对 per-tool header |
| P0-3 | 12306 会话/Cookie 复用 | 延迟 | 每余票查询省 1 次往返 | `tools/_rt12306.py` | 中 | 需境内网络验证 |
| P1-1 | 前端流式渲染帧节流 | 流畅度 | 长回答不再 O(n²) 重排 | `frontend/src/main.js` | 低 | — |
| P1-2 | 静态资源压缩 + 分级缓存 | 首屏 | 130 KB → 约 30–40 KB | `app/main.py` | 低 | 版本号进产物 |
| P1-3 | 单飞去重 + 结果 TTL 缓存 | 延迟 | 追问场景近乎全命中 | 新增缓存层 | 中 | 必须如实标注时效 |
| P2-1 | `all_stations()` 缓存 | 延迟 | 每请求 2–4 ms | `_rt12306.py` + `station_lookup.py` | 低 | 顺带修陈旧拼音索引 |
| P2-2 | 并发度 4 + 超时分级 | 延迟 | 4 工具计划省一批次 | `config.py` / `retrieve.py` | 低 | 会碰并发相关测试 |
| P2-3 | 注入事实总量预算 | 延迟/成本 | 首 token 更快、输入 token 更少 | `generate.py` / `llm/client.py` | 中 | 不得破坏完整性契约 |
| P2-4 | `/api/metrics` 观测 | 工程 | 让后续优化有基线 | 新增只读路由 | 低 | 注意脱敏 |

---

## 4. 建议的实施顺序

**第一批（低风险、收益确定，建议一次做完再复测）**
P0-1 → P0-2 → P1-1 → P1-2

这一批的共性是：**不改变任何数据口径与作答策略**，只改"怎么算、怎么送"。
P0-1 是唯一的可用性缺陷，应当排在所有优化之前。

**第二批（需要真实网络与工具行为验证）**
P0-3 → P1-3 → P2-1 → P2-2

这一批开始触碰外部站点的会话与缓存语义，必须跑需要境内网络的套件（`tests/test_tools.py` 等），并逐条确认 `ToolResult` 的 `note` / `fetched_at` / `truncated` 仍然如实。

**第三批（需要设计对齐）**
P2-3 → P2-4

P2-3 与"数据完整性契约"（`_truncated` / `total` / `shown`）正面相关，先有 P2-4 的基线再做，否则无法判断"省了 token 但答得更差"。

**贯穿始终的两条纪律**（来自本项目的既有约定，不要因为优化而破）：
1. **不得把"没查到 / 被截断 / 缓存命中"表述成"数据里没有"** —— 任何缓存与裁剪都要把口径写进 `note` / `integrity`；
2. **优化必须可一键回退** —— 新行为一律走配置开关（参照 `FASTPATH_ENABLED` 的先例）。

---

## 5. 验证方法

```bash
# 1) 决策层基线（离线，本次已跑，9 项断言全绿）
cd backend && PYTHONPATH=. LLM_MOCK=true .venv/bin/python tests/test_perf_fastpath.py

# 2) 整链结构（假 LLM，无网络；改 orchestrator/retrieve 后必跑）
cd backend && PYTHONPATH=. LLM_MOCK=true .venv/bin/python tests/test_pipeline.py

# 3) 受影响面（按改动挑，不要每次全量）
cd backend && PYTHONPATH=. .venv/bin/python tests/test_dict_mileage.py        # P0-1 / P2-1
cd backend && PYTHONPATH=. .venv/bin/python tests/test_rail_line_stations.py  # P0-1
cd backend && PYTHONPATH=. .venv/bin/python tests/test_cost_governance.py     # P2-2 / P2-3
cd backend && PYTHONPATH=. .venv/bin/python tests/test_api.py                 # P1-3 / P2-4（含真实 LLM，语义断言带重试）

# 4) 全量（仅在发版前）
bash backend/tests/run_all.sh
```

**测量纪律**：本项目的延迟几乎全在外部往返上，**单机微基准不能代替端到端计时**。
建议做法是先用 P2-4 的指标或 `done` 事件里的 `latency_ms` / `stage.ms` 取**改动前后的同批问句**对照，
再谈"提升了多少"；不要用"省了一次握手"直接换算成"快了 300 ms"。

**端到端取样问句**（覆盖快路径、LLM 决策、多工具并发三条路径）：

| 问句 | 期望路径 |
|---|---|
| `G1今天由哪组动车组担当？` | 快路径 + emu.routing |
| `明天北京到上海的高铁还有票吗？` | 快路径 + ticket.query（可预取命中） |
| `北京南站大屏今天下午有哪些高铁？` | 快路径 + station.screen（大数据量裁剪） |
| `CR400AF 为什么叫复兴号？` | 知识型 → LLM 决策 + web.search |
| `吉林市XX区，要拍 CR400AF，今天下午` | LLM 决策 + 4 工具并发（`photo_spot`） |

### 附：顺带发现的一处文档与代码不一致（非性能问题）

| 项 | 内容 |
|---|---|
| 位置 | `docs/EXPL.md`「二、技术栈与运行前提」表：后端写 **pydantic v2、pydantic-settings** |
| 实际代码 | `backend/app/config.py:16` 是 `from pydantic import BaseSettings`（**pydantic v1**），且该文件开头专门解释了**为什么必须是 v1**：Android 一体化把 Python 运行时随 APK 分发，v2 依赖的 `pydantic-core` 是 Rust 扩展，Chaquopy 上没有可用轮子 |
| 影响 | 不影响运行，但会误导后来者按 v2 的 API 写代码（v1 的 `@validator` / `class Config` 在 v2 下语义不同）。建议改 `EXPL.md` 那一行以代码为准 |

> 同样口径：本文所有行号引用都按**当前工作区**（`git status` 干净）核对过；
> 若后续改动使得行号漂移，以函数名/符号名定位为准，不要以行号为准。

---

## 6. 实施记录（第一批：P0-1 → P0-2 → P1-1 → P1-2）

> 本节由**实施方**在改完后追加，口径与上文一致：写"实测"的都给复现方式，
> 推断与实测分开写。上文 §0–§5 是 review 当时的快照，**不改动**。

| 编号 | 状态 | 落地位置 |
|---|---|---|
| P0-1 | ✅ 已修 | `app/data/dict.py`（`_polite_get`/`line_stations`/`station_profile` 全部 async）+ `app/tools/rail_mileage.py` 两个调用点加 `await` |
| P0-2 | ✅ 已修 | `app/tools/_http.py` 新增共享 `get_client()`/`aclose_client()`；7 个模块 11 处调用点全部改走它；`app/main.py` 的 `lifespan` 收尾 `aclose()` |
| P1-1 | ✅ 已修 | `frontend/src/throttle.js`（新模块，可脱离浏览器用 node 测）+ `frontend/src/main.js` 的 answer 分支改走 `renderAnswer()` |
| P1-2 | ✅ 已修 | `app/main.py`：`GZipMiddleware(minimum_size=1024)` + `/v/<stamp>/...` 分级缓存路由；`docs/EXPL.md` 的 pydantic v1/v2 已按代码改正 |

### 6.1 与上文方案的三处**有意偏离**（不是漏做）

1. **sqlite 读没有用 `asyncio.to_thread` 包**（上文 P0-1 建议 4/5）。
   实测这些查询 1–9 ms（上文 §0 已列），而线程跳转本身要几十微秒量级；对**命中缓存**的
   快路径（整条工具 ≈2 ms）反而是净增延迟。真正的可用性缺陷只有一个：那个
   `httpx.Client(timeout=120)`，已消除。保留了一个 AST 级回归测试
   （`tests/test_dict_mileage.py::test_polite_fetch_never_blocks_the_event_loop`），
   一旦有人把同步 client / `time.sleep` 放回 `data/dict.py` 就会红。
   （本机 `sqlite3.threadsafety == 3`，共享连接跨线程是安全的 —— 若将来数据变大要改，
   这条是前提，不是障碍。）

2. **分级缓存用"路径前缀"而不是给入口加 `?v=<VERSION>`**（上文 P1-2 建议 2）。
   前端是无构建的 **ES 模块图**（`main.js` → `pages.js` → `store.js`…）。`?v=` 只会挂在
   入口那**一个** URL 上，被 `import` 的四个模块拿不到版本号 —— 分级缓存会只对 5 个文件里的
   1 个生效。改成 `/v/<stamp>/src/main.js` 后，模块里的相对导入（`./store.js`）**自动继承**
   前缀，5 个模块一起进长缓存，且**一行 JS 都不用改写**。
   版本戳取的是 `index.html` 与 `src/**` 的**最新 mtime**，不是 `VERSION` 文件 ——
   用 VERSION 会在"改了 JS 但没发版"时直接命中旧缓存，而这正是本项目踩过的
   "重装了、界面却没变"。实测（`tests/test_static_assets.py`）：
   当前戳 → `immutable`，过期戳与裸路径 → `no-cache`，非 `src/` 路径 → 404。

3. **P0-2 的收益比上文估计的更大**。上文按"每工具省一次握手"估算；实际还省在
   2026-09-17 新增的「同车不同号」别名判定上 —— 那条路径一次未命中要额外打
   `leftTicket/init` + `queryI` 两个请求，共享连接池后这笔开销基本消失。

### 6.2 实测（本机，2026-09-17）

| 项 | 结果 | 复现 |
|---|---|---|
| 静态资源压缩 | `index.html` + 5 个 JS：**139 532 B → 52 704 B（2.65x）** | `backend/tests/test_static_assets.py` |
| SSE 未被压缩 | `/api/chat/stream` 无 `Content-Encoding` | 同上 |
| 共享连接池 | 16 个工具全部照常工作（12306 余票/图定、rail.re、Bing、jprailfan…），HTTP/2 生效 | `tests/test_tools.py`（需境内网络） |
| 字典层不再阻塞 | `_polite_get` 等三个入口为协程，模块内无同步 client/sleep | `tests/test_dict_mileage.py` |
| 流式渲染合并 | 50 次 delta → **1 次**渲染；40 ms 内 20 次 → ≤3 次；`cancel()` 后挂起渲染不执行 | `frontend/tests/throttle.test.mjs` |
| 真浏览器加载 | 无头 Chromium 加载首页后，6 个模块**全部经 `/v/<stamp>/` 前缀 200**，且 `/api/providers`、`/api/version` 被 JS 调起（证明模块图与相对导入继承都成立） | 见下 |

真浏览器复现（本机 Edge，无头）：

```bash
cd backend && PYTHONPATH=. .venv/bin/python -m uvicorn app.main:app --port 8018 &
"/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge" \
  --headless=new --disable-gpu --user-data-dir=/tmp/edgeprof \
  --virtual-time-budget=8000 --dump-dom http://127.0.0.1:8018/ > /tmp/dom.html
```

### 6.3 上线**不能**只看本文

- 端到端计时仍未做（上文 §5 的"测量纪律"照旧成立）：本文只给出**单项**实测，
  没有"改动前后同批问句"的对照 —— 回环场景下 P0-2/P1-2 的收益本就是**毫秒级**，
  别把"省了一次握手"换算成"快了 300 ms"。
- **真机（Android/WebView）回归已做**（2026-09-17，0.1.5 包）：release 包**清空应用数据后全新安装**，
  启动日志 `本地字典：可用`、`自检：/ → HTTP 200；/src/main.js → HTTP 200`、`页面自检` 读出完整界面文本；
  再用 `adb forward` 直接打应用内的后端，确认 `/` 是 `no-cache`、带戳入口 `/v/<stamp>/src/main.js` 是
  **`public, max-age=31536000, immutable`**、裸路径仍 `no-cache` —— 分级缓存在系统 WebView 上成立。
  另外 debug 包上跑通两条实网问句（12306 车次 1766ms / 站名+里程 4.4ms），说明共享连接池在 Chaquopy 里同样工作。
- 第二批（P0-3 / P1-3 / P2-1 / P2-2）与第三批（P2-3 / P2-4）**未动**。
