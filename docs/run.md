# RailFanAI · 运行 / 联调 / 演示

## 0. 环境要求

| 依赖 | 要求 | 原因 |
|---|---|---|
| **Python** | **≥ 3.10**（推荐 3.12） | 12306 反爬会拦截 Python 3.9 / macOS LibreSSL 的 TLS 指纹 |
| 网络 | 中国境内出口 | 12306 实时接口与 rail.re 仅境内可达 |
| 浏览器级请求头 | 已内置（`app/tools/_http.py`） | rail.re 对裸 UA 直接 `ReadTimeout` |

- 检查：`python3 --version`（需 ≥ 3.10）
- 若为 3.9：`brew install python@3.12 && bash scripts/upgrade_python.sh`（升级 + 重建 venv + 跑测试）

## 1. 一键配置 + 启动（推荐）

在仓库根目录执行 `bash scripts/setup.sh`：创建虚拟环境 → 安装依赖 → 生成 `.env`（已存在则保留）→ 跑一遍全部测试自检 → 启动 `http://127.0.0.1:8000` 并**自动打开对话页面**。`.env` 未配置 `LLM_API_KEY`（含占位值）则自动启用 `LLM_MOCK=true`，已配置真实 Key 则走真实模型整链。停止：`kill <输出中的 PID>`（日志 `.setup-uvicorn.log`）。可选：`PORT=8001 bash scripts/setup.sh` 换端口 · `SKIP_TESTS=1 bash scripts/setup.sh` 跳过测试 · `bash scripts/setup.sh --check-key` 仅检查配置不启动 · 首次配置 LLM Key 可用 `--api-key <你的Key>`（或交互式粘贴/环境变量 `LLM_API_KEY=...`/手改 `.env`，四种任选；写入后 `.env` 权限收紧为 600）。

**预热离线数据（可选）**：`bash scripts/prewarm.sh`（站点库 + 离线车次目录，缺失才下载；`--force` 强制重下，如升级 Python 后）。离线车次目录是**非实时兜底数据**，只用于"仅给车次号"时推断起止站；未预热时相关查询**如实提示未就绪**，不会在请求路径里同步下载。

## 2. 安装依赖

```bash
cd backend && python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt
```
依赖含 `mcp-server-12306`（12306 实时查询）、`httpx[http2]`、`brotli`（浏览器级请求头所需）。

## 3. 配置环境变量

复制模板：`cp .env.example .env`（模板在仓库根目录；配置读根目录 `.env`）。

| 项 | 默认 / 说明 |
|---|---|
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | OpenAI 兼容端点与模型；配 `LLM_MOCK=false` 走真实模型 |
| `LLM_STRUCTURED_MODEL` / `LLM_MOCK` | 意图·抽取用更强模型（留空沿用 `LLM_MODEL`）/ `true` 走确定性本地 mock（无 Key 演示与 CI） |
| `T12306_BASE` | 自备 12306 反代；**留空则 `t12306.search_tickets` 停用**（路由不主动调用） |
| `RAILRE_API_BASE` / `RAILRE_BASE` / `JPRAILFAN_BASE` / `CNRAIL_BASE` | rail.re 交路 API / rail.re 主站 / 黄河铁路网 / cnrail 地图 |
| `FREIGHT_95306_BASE` / `KMRAIL_BASE` / `SYTLJ_BASE` | 95306 / 昆铁货运 / 沈阳局余票（长期不可用，失败如实说明原因） |
| `HOST` / `PORT` / `APP_ENV` | `127.0.0.1` / `8000` / `dev`·`production`（production 校验必须显式配置的密钥，缺失拒绝启动） |
| `ENABLE_API_DOCS` / `API_BASE` | 是否暴露 `/docs`·`/redoc`·`/openapi.json`（生产应关闭或置于鉴权/内网后）/ 前端代理目标 |
| 治理阈值 | `MAX_MESSAGE_CHARS=2000`、`TOOL_CONCURRENCY=3`、`FACT_TEXT_MAX_CHARS=4000`（代码字段 `fact_text_max_chars`）、`FACT_TABLE_MAX_ROWS=40`、`STATION_LIST_LIMIT=12`、`STATION_SCREEN_LIMIT=15`、`RAIL_LINE_CACHE_TTL_S=3600`（大屏一次回全天 200–700 条，必须裁剪后再进 prompt）、`HTTP_TIMEOUT=12.0`、`HTTP_MAX_BYTES=2000000` |
| 性能与本地字典 | `FASTPATH_ENABLED=true`（确定性快路径，出问题置 false 回退纯 LLM）、`LLM_STRUCTURED_NO_THINK=true`、`DICT_DB_PATH=data/dict.db`、`DICT_GTFS_MAX_AGE_DAYS=5`、`DICT_SITE_MIN_INTERVAL_S=2.0`（个人站点间隔下限，勿调小） |
| 其他 | `TRAIN_CACHE_TTL_DAYS=30`、`TICKET_PRESALE_DAYS=15`、`HUB_PROBE_PAIRS=北京:上海,北京:广州,北京:哈尔滨,上海:广州` |

> ⚠️ **密钥**：`.env` 含真实 Key（已在 `.gitignore`），仅本地保存、不进库、不随包分发；**部署公网前必须改为环境变量注入并轮换 Key**。当前 `main.py` 为开发默认：CORS `allow_origins=["*"]`（`allow_credentials=False`）、无鉴权（`/health` 返回 `auth: "disabled"`）、无限流；对外提供访问应在反向代理层收敛入口并加限流、改白名单。

## 4. 启动后端

```bash
cd backend && LLM_MOCK=false .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```
前端页面 `http://127.0.0.1:8000/`（FastAPI 托管 `frontend/`）· API 文档 `/docs` · 健康检查 `/health`。

## 5. 调用示例

```bash
curl -s -X POST http://127.0.0.1:8000/api/chat -H "Content-Type: application/json" \
  -d '{"message":"我在吉林市XX区，要拍 CR400AF，今天下午"}'

curl -s -X POST http://127.0.0.1:8000/api/chat -H "Content-Type: application/json" \
  -d '{"message":"G1今天由哪组动车组担当？"}'
```
返回包含 `intent`（意图分类）、`slots`（关键槽位）、`answer`（生成回答）、**`thinking`（模型思考内容 think，前端渲染为可折叠块）**、`sources`（数据来源）、`tool_trace`（工具调用与结果）。另：仅给车次（无起止站）时 `train.schedule` 从车次目录**自动推断**起止站再查实时数据；区间表述（"北京到上海"）由 `app/od.py` 解析；12306 对**已发车次**不再列出，工具提示"可能已发车"而非返回陈旧数据。索引见 `docs/README.md`。

## 6. 测试

```bash
bash backend/tests/run_all.sh                    # 仓库根目录执行；跑完全部套件再汇总，任一失败退出码 1
cd backend && PYTHONPATH=. .venv/bin/python tests/test_tools.py    # 也可在 backend/ 下逐个跑
```
套件清单、覆盖要点与**无网络套件列表**（断网/CI 可跑，`test_frontend_store` 需 node）见 `docs/EXPL.md` §六；其余套件依赖真实 LLM 或境内数据源（`test_tools`、`test_emu_routing` 需境内网络，`test_api` 需真实 LLM，其对**语义判定**采用有限重试——模型偶发把"G1担当"判成知识型，结构断言仍为硬断言）。

## 6.1 本地数据字典（里程 / 车站档案 / 离线时刻）

`scripts/setup.sh` 会自动构建；也可单独跑：

```bash
python3 scripts/mirror_dict.py --all      # GTFS 周更快照（经镜像）+ 黄河铁路网线路汇总表
python3 scripts/mirror_dict.py --stats    # 查看本地库现状
```
落库位置 `backend/data/dict.db`（gitignored）。构建后 `rail.mileage` 提供两站里程 / 线路逐站里程 / 车站档案，**毫秒级**（本地命中，未命中才抓一次并回填缓存）；`train.schedule` 在 12306 不可用时用离线时刻（图定站序 + 累计里程）兜底，并标注"本地快照、非实际运行"。两个来源都是"字典类"数据（变更慢）且都是**他人站点**：只做低频抓取 + 本地缓存 + 标注来源，**不整表对外分发**。

## 7. 前端交互与上下文治理

多对话并存（「☰」列表，`#/` 新建，hover「✎」重命名 /「🗑」删除，标题默认取首条用户消息，各对话独立 `messages[]`/`history` 存 localStorage）· 多轮追问（前端回传 `history`）· 暂停输出（「■」或 `ESC`，`AbortController`，保留已生成内容并标记"已停止"）· 编辑重发（丢弃该消息之后的内容）/ 重新生成 / 复制 · `Enter` 发送、`Shift+Enter` 换行（中文输入法组合态已处理）· 深/浅色跟随系统并记忆 · 帮助/关于/免责页路由 `#/doc/<key>`（免责声明 `#/doc/disclaimer` 正文已填写，其余为【待补充】占位）。移动优先三端自适应：默认抽屉式侧栏（`☰` 呼出、遮罩点击关闭）+ 底部输入区带 `env(safe-area-inset-bottom)` 安全区，`≥768px` 加宽留白，`≥1024px` 侧栏常驻。上下文裁剪在服务端 `app/context.py`：**最近 6 条 / 单条 ≤800 字 / 合计 ≤3000 字**；前端 `frontend/src/store.js` 上限 **对话 ≤100 段 / 每段 ≤300 条消息 / 思考内容 ≤4000 字 / 工具日志 ≤60 条**。

## 8. 常见故障与处置

| 现象 | 处置 |
|---|---|
| 12306 / rail.re 查询失败或超时 | 确认**中国境内出口**；rail.re 需浏览器级请求头（已内置）；确认 Python ≥ 3.10（3.9 的 LibreSSL TLS 指纹被反爬拦截） |
| 12306 返回"网络可能存在问题，请您重试一下！" | 余票接口被限流：降低并发/频率（`TOOL_CONCURRENCY=3`）；图定表接口不受影响 |
| 抓取莫名失败（尤其报 502 且目标像是本地/内网地址） | 机器上开着**系统代理**时，请求会被塞进代理：本项目的抓取客户端已显式 `trust_env=False`（不继承环境/系统代理），这既是避免代理绕过 SSRF 地址校验，也是为了让抓取行为可预期；若确需代理，请在 `app/tools/_http.py` 中显式配置 |
| 某车次查不到（尤其已过发车时刻） | 12306 不列已发车次，工具提示"可能已发车"；日期超 `TICKET_PRESALE_DAYS=15` 时说明中会点明可能超预售期 |
| 401 / 鉴权失败 | `.env` 仍是占位 Key（如 `your-api-key-here`，占位值不算已配置）：改 `LLM_MOCK=true` 或填真实 Key |
| 站点库 / 离线车次目录未就绪 | 跑 `bash scripts/prewarm.sh`（启动时不在请求路径下载） |
| 端口被占用 | `PORT=8001 bash scripts/setup.sh`，或改 uvicorn `--port` |
| 依赖安装失败 / LibreSSL 报错 | 系统 Python 为 3.9：`brew install python@3.12 && bash scripts/upgrade_python.sh` |
| 货运/路局工具无结果 | 站点可达性受限，已优雅降级并如实说明原因 |

> 三层流水线在 `app/pipeline/`：`intent.py` 意图、`extract.py` 槽位、`retrieve.py` 按意图路由 `app/tools/` 数据源工具、`generate.py` 生成，编排入口 `orchestrator.py`；意图枚举 `photo_spot` / `schedule` / **`emu_routing`** / `station` / `ticket` / `news` / `general`。实时能力统一走 `mcp-server-12306`（`ticket.query` / `train.schedule`）。作答策略与数据源细节见 `docs/EXPL.md` §五、`docs/datasources.md`。`LLM_MOCK=true` 仅用于演示/CI，生产应关闭并配置真实 OpenAI 兼容 Key。
