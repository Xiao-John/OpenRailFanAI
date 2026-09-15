# RailFanAI · 运行 / 联调 / 演示

## 0. 环境要求（重要）

| 依赖 | 要求 | 原因 |
|---|---|---|
| **Python** | **≥ 3.10**（推荐 3.12） | 12306 反爬会拦截 Python 3.9 / macOS LibreSSL 的 TLS 指纹 |
| 网络 | 中国境内出口 | 12306 实时接口与 rail.re 仅境内可达 |
| 浏览器级请求头 | 已内置 | rail.re 对裸 UA 直接 `ReadTimeout` |

检查当前版本：
```bash
python3 --version          # 需要 ≥ 3.10
```

若为 3.9，先升级：
```bash
brew install python@3.12
bash scripts/upgrade_python.sh     # 重建 venv 并跑测试
```

## 1. 一键配置 + 启动（推荐）
在仓库根目录执行：
```bash
bash scripts/setup.sh
```
它会自动：创建虚拟环境 → 安装依赖 → 生成 `.env`(已存在则保留) → 跑一遍全部测试自检 → 启动
`http://127.0.0.1:8000` 并**自动打开对话页面**。若 `.env` 未配置 `LLM_API_KEY` 则自动启用
`LLM_MOCK=true`；已配置 Key 则用真实模型整链。手动停止：`kill <输出中的 PID>`（日志见 `.setup-uvicorn.log`）。

## 1.1 仅预热离线数据（可选）
```bash
bash scripts/prewarm.sh          # 站点库 + 离线车次目录（缺失才下载）
bash scripts/prewarm.sh --force  # 强制重下（升级 Python 后）
```
> 离线车次目录是**非实时兜底数据**，只用于"仅给车次号"时推断起止站；
> 未预热时相关查询会**如实提示未就绪**，不会在请求路径里同步下载。

## 2. 安装依赖
```bash
cd backend
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```
依赖含 `mcp-server-12306`（12306 实时查询）、`httpx[http2]`、`brotli`（浏览器级请求头所需）。

## 3. 配置环境变量
复制模板并在根目录新建 `.env`（仓库根目录）：
```bash
cp .env.example .env
```
需关注两个开关：
- **真实 LLM**：填 `LLM_BASE_URL`（OpenAI 兼容）与 `LLM_API_KEY`，`LLM_MOCK=false`。
- **Mock 演示**（无 Key 也可端到端跑通）：设 `LLM_MOCK=true`。此时意图/抽取/生成走确定性本地 mock，便于整链演示与 CI。

> ⚠️ **安全提示**：`.env` 含真实 Key，务必仅本地保存、不入库。详见 `docs/keysetsug.md`。

## 4. 启动后端
```bash
cd backend
LLM_MOCK=false .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```
- 前端页面：打开 `http://127.0.0.1:8000/`（FastAPI 已托管 `frontend/`）。
- API 文档：`http://127.0.0.1:8000/docs`。
- 健康检查：`http://127.0.0.1:8000/health`。

## 5. 调用示例（三层流水线）

**常规查询：**
```bash
curl -s -X POST http://127.0.0.1:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"我在吉林市XX区，要拍 CR400AF，今天下午"}'
```

**担当车组（交路）查询：**
```bash
curl -s -X POST http://127.0.0.1:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"G1今天由哪组动车组担当？"}'
```

返回包含：`intent`（意图分类）、`slots`（关键槽位）、`answer`（生成回答）、
**`thinking`（模型思考内容 think）**、`sources`（数据来源）、`tool_trace`（工具调用与结果）。
前端会把 `thinking` 渲染为折叠的"思考过程（think）"块。

> 文档总索引见 [`docs/README.md`](README.md)。

## 6. 测试
```bash
cd backend
bash tests/run_all.sh      # 一键运行 backend/tests/ 下全部套件（跑完再汇总）
```
或逐个运行：
```bash
PYTHONPATH=. .venv/bin/python tests/test_dates.py                 # 日期归一化（含"大后天/下周X"与非法日期回归）
PYTHONPATH=. .venv/bin/python tests/test_od.py                    # 起讫站解析
PYTHONPATH=. .venv/bin/python tests/test_context.py               # 多轮上下文裁剪
PYTHONPATH=. .venv/bin/python tests/test_policy.py                # 分类型作答策略
PYTHONPATH=. .venv/bin/python tests/test_pipeline.py              # 三层流水线
PYTHONPATH=. .venv/bin/python tests/test_tools.py                 # 工具层
PYTHONPATH=. .venv/bin/python tests/test_emu_routing.py           # 交路查询
PYTHONPATH=. .venv/bin/python tests/test_integration_fullchain.py # 整链集成
PYTHONPATH=. .venv/bin/python tests/test_api.py                   # HTTP + 多轮 + 中断
PYTHONPATH=. .venv/bin/python tests/test_train_stops.py           # 车次经停站（末项需境内网络）
PYTHONPATH=. .venv/bin/python tests/test_station_screen.py        # 车站大屏（末项需境内网络）
PYTHONPATH=. .venv/bin/python tests/test_dict_mileage.py           # 本地字典：里程/车站档案/离线时刻
PYTHONPATH=. .venv/bin/python tests/test_perf_fastpath.py          # 决策层：快路径/合并调用/预取
PYTHONPATH=. .venv/bin/python tests/test_regressions.py           # 历史缺陷修复回归（无网络）
PYTHONPATH=. .venv/bin/python tests/test_routing.py               # 检索计划与 mock 路由（无网络）
PYTHONPATH=. .venv/bin/python tests/test_orchestrator_semantics.py # 块式/流式故障语义（无网络）
PYTHONPATH=. .venv/bin/python tests/test_cost_governance.py       # 上下文开销治理（无网络）
PYTHONPATH=. .venv/bin/python tests/test_hardening.py             # P2 加固（无网络）
PYTHONPATH=. .venv/bin/python tests/test_config_docs.py           # 配置与文档一致性（无网络）
PYTHONPATH=. .venv/bin/python tests/test_product_fixes.py         # 车迷测试集问题修复回归（无网络）
PYTHONPATH=. .venv/bin/python tests/test_r1_fixes.py               # RAG R1 问题修复回归（无网络）
PYTHONPATH=. .venv/bin/python tests/test_r1_fixes2.py              # RAG R1 第 2 批修复回归（无网络）
PYTHONPATH=. .venv/bin/python tests/test_station_quality.py        # 站点清单质量/同音纠错（无网络）
PYTHONPATH=. .venv/bin/python tests/test_rail_line_stations.py     # 按线路名查站序/里程（无网络）
PYTHONPATH=. .venv/bin/python tests/test_frontend_store.py         # 前端数据层（需 node，无网络）
```

> **可在断网/CI 环境运行的无网络套件**：
> `test_regressions` / `test_routing` / `test_orchestrator_semantics` / `test_cost_governance` /
> `test_hardening` / `test_config_docs` / `test_product_fixes` / `test_r1_fixes` / `test_r1_fixes2` /
> `test_station_quality` / `test_rail_line_stations` / `test_dict_mileage` / `test_perf_fastpath` /
> `test_frontend_store`（无网络，其中前端套件需 node）。
> 其余套件依赖真实 LLM 或境内数据源；`test_api` 对**语义判定**采用有限重试
> （模型偶发把"G1担当"判成知识型），结构断言仍为硬断言。

## 6.1 前端交互（M8 重构）

对话界面参照 DeepSeek 客户端设计，**多对话并存**，不再"新对话覆盖旧对话"：

| 能力 | 用法 |
|---|---|
| **多对话** | 左上角「☰」打开对话列表；每段对话独立 `messages[]`/`history`，切换即切换上下文 |
| **新对话** | 列表顶部「＋ 新对话」（`#/`），旧对话完整保留在 localStorage |
| **重命名 / 删除** | 对话项 hover → 「✎」重命名 / 「🗑」删除（标题默认取首条用户消息） |
| **多轮上下文** | 直接追问即可（如先问"G1今天由哪组担当"，再问"那明天呢"） |
| **暂停输出** | 生成中点「■」或按 `ESC`，保留已生成内容并标记"已停止" |
| **编辑重发** | hover 用户消息 → 「编辑」，改完发送（该消息之后的内容会被丢弃） |
| **重新生成** | hover 助手回答 → 「重新生成」 |
| **复制** | hover 消息 → 「复制」 |
| 输入 | `Enter` 发送，`Shift+Enter` 换行（中文输入法组合态已处理） |

**输入区**：无需登录即可直接对话，页面打开即用，无引导遮罩。

**主题切换**：深/浅色跟随系统并记忆（`localStorage`）。

**静态说明页**：使用帮助 / 关于 / 免责声明等页面（路由 `#/doc/<key>`，正文为【待补充】占位，
由 `frontend/src/pages.js` 统一维护）。

**三端自适应（移动优先）**：默认按手机布局渲染——对话列表为抽屉式侧栏（`☰` 呼出、遮罩点击关闭）、
输入区固定底部并带 `env(safe-area-inset-bottom)` 安全区；`≥768px` 加宽留白，`≥1024px` 侧栏常驻。
深/浅色主题跟随系统并记忆（`localStorage`）。

上下文由前端维护并随每次请求回传；服务端 `app/context.py` 负责裁剪
（最近 6 条 / 单条 ≤800 字 / 合计 ≤3000 字）。
对话数据存 `localStorage`（`frontend/src/store.js`），有上限防止无限增长：
对话 ≤100 段 / 每段 ≤300 条消息 / 思考内容 ≤4000 字 / 工具日志 ≤60 条。

### 6.2 本地数据字典（里程 / 车站档案 / 离线时刻）

`scripts/setup.sh` 会自动构建；也可单独跑：

```bash
python3 scripts/mirror_dict.py --all      # GTFS 周更快照（经镜像）+ 黄河铁路网线路汇总表
python3 scripts/mirror_dict.py --stats    # 查看本地库现状
```

落库位置 `backend/data/dict.db`（gitignored）。构建后：

| 能力 | 说明 |
|---|---|
| 两站里程 / 线路逐站里程 / 车站档案 | `rail.mileage`，**毫秒级**（本地命中；未命中才抓一次并回填缓存） |
| 离线时刻（图定站序 + 累计里程） | `train.schedule` 在 12306 不可用时的兜底（标注"本地快照、非实际运行"） |

> 两个来源都是"字典类"数据（变更慢）且都是**他人站点**：只做低频抓取 + 本地缓存 + 标注来源，
> **不整表对外分发**（合规与礼遇约定见 `docs/source-expansion.md` §四「合规红线」）。

---

## 7. 数据能力与前置条件

| 查询能力 | 工具 | 前置条件 |
|---|---|---|
| 站名/拼音/电报码互查 | `station.lookup` | 无 |
| 两站间实时余票 | `ticket.query` | 境内网络 + Python ≥ 3.10 |
| 车次实时时刻/余票/经停站 | `train.schedule` | 境内网络 + Python ≥ 3.10 |
| **车次↔担当车组（交路）** | **`emu.routing`** | 境内网络（rail.re API） |
| **两站间线路径路 + 里程** | **`rail.line`** | 境内网络（黄河铁路网） |
| **按线路名查站序 / 指定径路里程** | **`rail.line_stations`** | 境内网络（黄河铁路网，首次约 20s，之后走缓存） |
| 铁路地图外链 | `cnrail.map` | 无 |
| 网页兜底检索 | `web.search` | 无（Bing 中国 / 百度） |

实测试例：
```
Q: G1今天由哪组动车组担当？
A: 今日（2026-09-13）G1次列车由 CR400BFA-5054 动车组担当。

Q: 明天北京到上海的高铁余票
A: 从 12306 查询到 2026-09-14 北京→上海共 55 趟，G531 商务座 7 张…

Q: G1明天几点发车，经停哪些站？
A: G1（北京南→上海虹桥）06:30 发车，经停 沧州西→德州东→曲阜东→南京南→苏州北…

Q: 北京到上海走哪条线路？
A: 全程 1320 km，途经 17 条线路：京沪线→京沪高速线→德州东联络线→…
```

> 注：仅给车次（无起止站）时，`train.schedule` 会从车次目录**自动推断**起止站再查实时数据。
> 区间表述（"北京到上海"）由 `app/od.py` 解析，无需用户写成规范格式。
> 12306 对**已发车次**不再列出，此时工具会提示"可能已发车"而非返回陈旧数据。

## 8. 作答策略（M10）

问题会先被判定性质，再决定"能否使用模型自身知识"：

| 类型 | 示例 | 行为 |
|---|---|---|
| **实时型** | "G1今天由哪组担当""明天北京到上海还有票吗" | 严格闭卷，只依据检索事实 |
| **知识型** | "CR400AF用的哪个品牌动力系统""样车车组号是多少" | 允许结合模型知识，但标注"（据模型知识，未检索确认）" |
| **混合型** | 今日是否开行 + 该车型动力系统 | 实时部分闭卷、知识部分标注 |

前端会在"知识型/混合型"回答上显示徽标与"含模型知识，请核实"提示。

## 说明
- 三层流水线在 `app/pipeline/`：意图分类 `intent.py`、槽位抽取 `extract.py`、
  检索 `retrieve.py`（按意图路由 `app/tools/` 的数据源工具）、生成 `generate.py`；编排入口 `orchestrator.py`。
- 意图枚举：`photo_spot` / `schedule` / **`emu_routing`** / `station` / `ticket` / `news` / `general`。
- 实时能力统一走 `mcp-server-12306`（`ticket.query` / `train.schedule`）；
  `t12306.search_tickets` 仅在配置 `T12306_BASE` 反代时作为备选，路由不再主动调用。
- 数据源细节（含 rail.re API 与 12306 反爬踩坑记录）见 `docs/datasources.md`。
- `LLM_MOCK=true` 仅用于演示/CI；生产应关闭并配置真实 OpenAI 兼容 Key。
