# 排障工作手册（可执行方法论）

> 定位：**遇到问题怎么查**，以及**怎么把"查出来的结论"变成不会再犯的机制**。
> 本文件只收"可执行"的东西：具体命令、判据、以及每条方法论的出处（哪次事故）。
> 状态标记：`✅ 已在用` / `⏳ 待研判`（已写成方法但**尚未实施**，需先定自检强度）。
>
> 相关：`docs/EXPL.md`（现状）｜`docs/datasources.md`（数据源与抓取纪律）｜
> `docs/source-expansion.md`（待接入评估与合规红线）｜`docs/perf-plan.md`（延迟诊断）

---

## 一、总原则（三次事故换来的）

| 原则 | 出处（真实事故） |
|---|---|
| **1. 不许静默降级**：任何"取不到/判断不了"都必须出现在文本、日志或数据字段里 | 起讫站由 2022 离线目录推断错（G1 上海 vs 上海虹桥）→ 整链路降级成"静态归属"，用户只看到一句"北京南→上海"，**没有任何地方提示数据其实没取到** |
| **2. 不许把推测写成结论**：未知就写"未知"，并给出验证方式 | 多次调研中"接口不可用"的结论若不写明验证方式，后人会重复踩 |
| **3. 判据要看内容，不看状态码** | `getCarDetail` 外层 `status=0` 时 `content.data` 仍有完整数据；`bigScreen` 参数写错时返回 `200 + status:true + data:[]`（静默失败）；高德 POI 返回 200 但 body 是阿里云惩罚页 |
| **4. 被 `except Exception` 吞掉的错误必须留痕** | `retrieve.py` 少一句 `import rt` → `NameError` 被吞 → 站名匹配静默退回正则（把"换乘车站"切成"换乘车"），**症状与原因相隔很远** |
| **5. 时间/日期的口径要写在字段旁边** | D06 红线：把"次日图定 11:24"当成"今天到达时刻"；`update_arrive_time` 其实只是计划时刻去掉冒号 |
| **6. 单元正确 ≠ 集成正确** | 三个"前端在调、后端已改名"的死接口（trainvisual 的 `/api/station_lines` 等）——这类问题单测发现不了 |

---

## 二、五类常见故障的排查路径

### A. "工具说查到了，但答案是错的"

1. **先看 `tool_trace` 与 `process_logs`**：`[决策]` 行会写明意图/槽位/耗时与决策来源
   （`快路径` / `合并调用` / `两次调用`），`[数据检索]` 行给出工具名与耗时。
   ```bash
   curl -s -X POST localhost:8000/api/chat -H 'Content-Type: application/json' \
     -d '{"message":"G1今天由哪组担当？"}' | python3 -m json.tool | tail -40
   ```
2. **直接打工具，绕开编排**（把"模型答错"与"数据错"分开）：
   ```bash
   cd backend && PYTHONPATH=. .venv/bin/python -c "
   import asyncio, logging; logging.disable(logging.CRITICAL)
   from app.tools.registry import invoke_by_name
   r = asyncio.run(invoke_by_name('train.schedule', {'train':'G1','date':'今天','include_reference':True}))
   print(r.ok, r.error); print(r.text[:400]); print('DATA:', r.data)"
   ```
3. **看完整性契约**：`ToolResult.total / shown / truncated / filters / fetched_at / integrity_line()`。
   **截断冒充全集**是本项目历史上最反复的坑。
4. **交叉核对第二个来源**：车组号有 rail.re + 12306 官方；里程有 GTFS + 黄河里程表。
   两个源不一致时**如实呈现分歧**，不要静默取一个。

### B. "取不到数据"（外部接口/反爬/限流）

| 症状 | 判据 | 处理 |
|---|---|---|
| `M0003 系统忙` | 12306 对**余票**接口限流；也可能是**方法/参数错** | 退避重试；改查图定表（`queryByTrainNo` 不依赖售票状态） |
| `status:true + data:[]` | **静默失败**：参数名写错（`bigScreen` 要 `train_station_code`，不是 `station_telecode`） | 核对参数名；用「网关 403 存在性探针」确认路径是否存在 |
| `403 Forbidden (openresty)` 纯 HTML | `mobile.12306.cn/wxxcx/` 是**精确白名单网关**，路径不存在 | 换路径；**别盲猜**（实测盲猜 10 条 0 命中） |
| `HTTP 000` | 境内网络层阻断（`github.com`/`raw.githubusercontent.com`/OSM 官方） | 走镜像：`ghfast.top` / `cdn.jsdelivr.net` / `api.github.com` |
| 返回非 JSON 二进制 | `cnrail.geogv.org/api/search` 是自定义编码 | 改用它公开的矢量瓦片 |
| Overpass `406` / 超时 | **UA 被拒**：浏览器 UA 被拒、`curl/8.7.1` 通过 | 用非浏览器 UA + `maps.mail.ru` 镜像 + 退避 |

### C. "答案对但很慢"（延迟）

**先分段量，再改**（实测结论：慢的往往不是你以为的那一段）：
```bash
cd backend && PYTHONPATH=. .venv/bin/python -u /tmp/e2e_after.py   # 逐问句打印 决策/检索/生成
```
- 决策层：`[决策]` 行给了毫秒数。**3.8–11.6s 曾是两次 LLM 往返**，现已优化到 5–19ms（快路径）；
- 若决策又变慢 → 检查 `FASTPATH_ENABLED`、是否走到 `llm-merged`（知识型问题正常）；
- 检索层：看 `[数据检索]` 毫秒；工具冷启动差异极大（`rail.line_stations` 冷启动 ~19s，
  本地字典类 <30ms）；
- 生成层：**当前瓶颈（占 80–95%）**，由答案长度与 provider 抖动决定（同 prompt 相邻两次可差 3–8 倍）。

### D. "某个数据源新增/失效了"

固定四步（`docs/source-expansion.md` 的调研就是这么做的）：
1. **读它自己的前端 JS** 提端点（`grep -oE '/api/[a-zA-Z0-9_/-]+'`），**不爆破路径**；
2. `fetch(` 上下文还原参数名（只看参数，不批量取数）；
3. **每端点抽样 1 次**验形（记形状与规模，不落库）；
4. **读 `robots.txt` 与页面声明**（许可、署名要求、`Disallow`、`ai-train=no`）。
   ⚠️ 注意**主机可能不同**：小铁查查前端 `Allow: /`，但其 `api.` 主机是 `Disallow: /`。

### E. "测试挂了，但代码看起来对"

1. **先判"是回归还是行为变更"**：本次改动是否有意改了这个行为？有意 → 改断言并写明理由；无意 → 找根因。
2. **检查测试注入点是否还成立**（架构迁移最常见）：
   `test_pipeline` / `test_orchestrator_semantics` 在决策层收敛到
   `planner` 后，注入点从 `orchestrator.intent` 移到 `planner` 接缝，并显式关闭快路径。
3. **静默失败会让断言"看起来过了"**：注意 `except Exception: pass`（见总原则 4）。

---

## 三、可执行方法论清单

### 已固化（✅ 已在用）

| 方法 | 命令 / 位置 |
|---|---|
| 全量回归 + 汇总 | `bash backend/tests/run_all.sh`（失败不中断，结尾给汇总） |
| 逐问句分段耗时 | `/tmp/e2e_after.py`（决策/检索/生成三段 + planner 来源） |
| 本地字典体检 | `python3 scripts/mirror_dict.py --stats` |
| 数据源探测纪律 | `docs/source-expansion.md` §一二（限流、UA、镜像、许可） |
| 文档-配置一致性 | `test_config_docs`（`.env.example` 与 `Settings` 双向校验） |

### 待研判（⏳ 已写成方法，**暂不实施**）

> 说明：方法论先整理进本文件，**自检强度待后续研判**，不急着上。

| 方法 | 做法 | 需要研判的点 |
|---|---|---|
| **前端-后端接口路径一致性自检** | 扫描 `frontend/src/*.js` 里所有 `fetch("<path>")`，逐个核对 FastAPI 路由表；不匹配即失败 | 强度：硬失败还是仅告警？要不要覆盖 `#` 路由与静态资源？误报如何豁免？ |
| **`except Exception` 审计** | 静态扫描全部 `except ...: pass`，要求至少 `_log.debug/warning` | 强度：是否允许白名单？现有 7 处如何逐个定性？ |
| **数据源存活巡检** | 定期对关键端点数一次（12306/rail.re/jprailfan/GTFS release），记录可用性曲线 | 频率？失败阈值？结果放哪儿（`docs/` 还是仅日志）？ |
| **快路径误判率统计** | 用 `planner` 字段统计 `deterministic` 占比与"快路径与 LLM 结论不一致"的样本 | 是否做影子对照（同时跑两条路径）？成本与延迟代价？ |
| **答案长度/延迟看板** | 记录生成耗时与答案长度分布，超阈值提示短答策略 | 阈值怎么定？是否需要用户可见的"正在生成"提示？ |

---

## 四、日志与字段速查（排障时先看这些）

| 位置 | 字段 | 用途 |
|---|---|---|
| `process_logs` | `[决策] <来源>：意图 · 问题性质 · 槽位 · Nms` | 判断走的是快路径还是 LLM、耗时、槽位是否符合预期 |
| `process_logs` | `[数据检索] 工具：[...] · Nms` | 哪些工具被调用、成功/失败 |
| `process_logs` | `[回答生成] ... Nms` | 生成耗时 |
| `done` 事件 / `PipelineResult` | `planner` | `deterministic` / `llm-merged` / `llm-legacy` |
| `ToolResult` | `total / shown / truncated / filters / fetched_at` | **是否截断、是否为筛选子集** |
| `ToolResult.note` | 时效与口径 | "图定/实际""本地快照""自算未核实"等必须在回答里转述 |
| `railfan.*` logger | `railfan.planner` / `railfan.tools.*` | 快路径命中、预取放弃、工具降级原因 |

---

## 五、写"下一次不再查"的三种收尾

1. **补回归**：任何修好的缺陷都要有一条会失败的断言（本项目已 27 套 / 301 项）。
2. **写进文档**：口径、陷阱、验证方式 → 对应文档（数据源陷阱进 `datasources.md`，
   待接入与合规进 `source-expansion.md`，性能进 `perf-plan.md`）。
3. **留痕而不是消音**：确需吞异常时，至少 `_log.debug/warning` + 注明"为什么可以吞"。
