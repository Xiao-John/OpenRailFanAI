# RailFanAI · 中国铁路车迷助手

面向铁路迷（RailFan）的 RAG / Agent 助手。输入一句自然语言，比如：

> 我在吉林市XX区，要拍 CR400AF，今天下午

系统按三层流水线处理：

1. **意图分类层** —— 识别用户意图（拍摄点推荐 / 时刻查询 / **车组交路** / 车站信息 / 余票查询…）
2. **信息抽取层** —— 提取关键槽位（地点、车次/车型、时间、方向…）
3. **检索生成层** —— Agent 工具循环，按需调用网络数据源（12306、rail.re、cnrail 等），生成回答并附数据来源

## 真实数据能力

| 查询能力 | 状态 | 数据源 |
|---|---|---|
| 站名/拼音/电报码互查（3384 站） | ✅ 实时 | 12306 站点库 |
| 两站间实时余票 | ✅ 实时 | 12306 leftTicket |
| 车次实时时刻 / 余票 / 经停站 | ✅ 实时 | 12306 leftTicket + queryByTrainNo |
| **车站当日全部到发车次（"车站大屏"）** | ✅ **实时** | **12306 微信小程序 bigScreen**（含站台/车底型号/客运段/套跑交路） |
| **车次 ↔ 担当车组（交路）** | ✅ **实时** | **rail.re API** |
| 车型 ↔ 担当车次 | ✅ 实时 | rail.re API |
| **两站间线路径路 + 里程** | ✅ | 黄河铁路网「旅客径路查询」 |
| 按线路名查站序 / 指定径路里程 | ✅ | 黄河铁路网「指定径路查询」 |
| 两站里程 / 车站档案（本地字典） | ✅ | GTFS 周更快照 + 客运里程表（本地缓存） |
| 铁路地图外链 | ✅ | cnrail.geogv.org |
| 网页兜底检索 | ✅ | Bing 中国 / 百度 |

## 对话体验

- **多轮上下文** —— 支持追问（"那明天呢"承接上文的对象）
- **多对话并存** —— 对话列表内新建/切换/重命名/删除，各自独立上下文，存本机
- **暂停输出** —— 生成中可随时停止，保留已生成内容
- **编辑重发 / 重新生成** —— 消息可修改后重发，回答可重新生成
- **流式渲染** —— 阶段进度（意图→抽取→检索）→ think → 回答逐字输出
- **分类型作答** —— 实时型严格依据检索事实；知识型（车型参数/样车编号等）
  允许结合模型知识，但会标注「据模型知识，未检索确认」
- **移动优先三端自适应** —— 抽屉式会话栏、底部输入区安全区、深浅色主题跟随系统

> 「担当车组」指**具体车组号**（如 CR400BFA-5159）——这是 12306 公开接口不提供的数据
> （12306 只给到"车型"这一级），本系统通过 rail.re 交路 API 补齐，可回答
> "今天 G1 由哪组动车组担当"这类车迷核心问题。
> 而 12306 车站大屏（`station.screen`）给出的是**车底型号 + 担当客运段/车辆段 + 当日套跑交路**
> （且覆盖普速），两者互补：前者答"哪一组"，后者答"什么型号、谁在用、今天还跑哪些车次"。

## 环境要求

- **Python ≥ 3.10**（推荐 3.12）——macOS 上 Python 3.9 的 LibreSSL TLS 指纹会被 12306 反爬拦截
- 中国境内网络出口（12306 实时接口与 rail.re 仅境内可达）
- 可选：`node`（仅用于跑前端数据层单测）

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.12 + FastAPI + pydantic v2 |
| AI 编排 | LLM Agent（OpenAI 兼容接口，DeepSeek / SiliconFlow 等均可；也支持 `LLM_MOCK=true` 确定性本地 mock） |
| 数据源 | 12306 官方接口（经 mcp-server-12306）+ rail.re 交路 API + 黄河铁路网 + cnrail + 网页抓取 |
| 前端 | 原生 HTML + JS（无构建工具，由后端静态托管，SSE 流式） |
| 检索 | v1 工具调用按需取数；真·RAG 向量库后置 |

## 目录结构

```
RailFanAI/
├── backend/            # FastAPI 后端
│   ├── app/
│   │   ├── main.py     # 应用入口（/health、路由挂载、静态托管前端）
│   │   ├── config.py   # 环境配置（OpenAI 兼容端点、Key）
│   │   ├── api/        # HTTP 路由（/api/chat、/api/chat/stream）
│   │   ├── pipeline/   # 三层流水线（意图/抽取/检索生成）
│   │   ├── tools/      # 数据源工具（12306、rail.re、黄河铁路网、web…）
│   │   ├── llm/        # LLM 客户端封装（含 mock）
│   │   └── data/       # 离线车次目录 + 本地数据字典
│   └── tests/          # 测试套件（run_all.sh 汇总执行）
├── frontend/           # 对话前端（index.html + src/）
├── docs/               # 设计与数据源文档（索引见 docs/README.md）
├── scripts/            # setup.sh / prewarm.sh / upgrade_python.sh
└── .env / .env.example # 环境变量
```

## 快速开始

### 推荐：一键脚本（自动选 Python ≥3.10、装依赖、跑测试、启动并打开页面）

```bash
bash scripts/setup.sh                 # 在仓库根目录执行
PORT=8001 bash scripts/setup.sh       # 换端口
SKIP_TESTS=1 bash scripts/setup.sh    # 跳过测试自检，快速启动
```

启动后直接打开 `http://127.0.0.1:8000/` 即可对话，**无需注册或登录**。

### 手动步骤（注意 Python 版本与执行目录）

```bash
# 1. 后端依赖（必须 Python ≥3.10：3.9 的 TLS 指纹会被 12306 反爬拦截）
python3.12 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt

# 2. 配置环境变量（.env 放在**仓库根目录**，不是 backend/）
cp .env.example .env
# 真实模型：编辑 .env 填入 LLM_BASE_URL / LLM_API_KEY（OpenAI 兼容）
# 多家混用/自备 Key：设 LLM_PROVIDER 选内置供应商（deepseek/openai/zhipu/ollama…），
#   或用 LLM_PROVIDERS / LLM_PROVIDERS_FILE 添加自定义供应商；也可启动后在界面「⚙️ 模型」里直接选
# 无 Key 演示：直接设 LLM_MOCK=true（确定性本地 mock 跑通整链）

# 3. 启动（从 backend/ 目录启动，配置会自动向上查找根目录 .env）
cd backend && LLM_MOCK=true PYTHONPATH=. .venv/bin/uvicorn app.main:app --reload
# 打开 http://127.0.0.1:8000/ 使用对话界面；开发环境下 /docs 查看 API
```

> ⚠️ `python3 -m venv` 在部分机器上仍是 3.9（`python3 --version` 确认一下）；
> 若为 3.9，请先 `brew install python@3.12 && bash scripts/upgrade_python.sh`。

## 测试

```bash
# 一键运行全部套件（跑完全部套件再汇总，任一套失败则退出码为 1）
bash backend/tests/run_all.sh          # 在仓库根目录执行，或 cd backend && bash tests/run_all.sh

# 单个套件示例（在 backend/ 下）
PYTHONPATH=. .venv/bin/python tests/test_pipeline.py    # 三层流水线（假 LLM，无网络）
PYTHONPATH=. .venv/bin/python tests/test_tools.py       # 16 个工具逐个调用（需境内网络）
```

其中大部分套件**完全无网络**（可在 CI/断网环境运行），少数依赖真实 LLM 或境内数据源；
前端数据层单测由 `frontend/tests/store.test.mjs` 提供（需 node）。详见 [docs/run.md](docs/run.md) §6。

## 文档

| 文档 | 内容 |
|---|---|
| [`docs/EXPL.md`](docs/EXPL.md) | **实现**：架构、三层流水线、工具清单、数据完整性契约、测试与运行状态 |
| [`docs/datasources.md`](docs/datasources.md) | **实现**：各数据源的接入方式、关键约束与踩坑记录 |
| [`docs/run.md`](docs/run.md) | **部署与运行**：安装、启动、配置密钥、接口示例、测试与数据准备 |
| [`docs/README.md`](docs/README.md) | 索引 |

> 代码即文档：模块与关键函数都注释了"为什么这么做"；测试用例同时充当行为规格。
## 常用脚本

```bash
bash scripts/setup.sh          # 一键安装 + 预热 + 自检 + 启动（推荐）
bash scripts/prewarm.sh        # 仅预热离线数据（站点库/车次目录）
python3 scripts/mirror_dict.py --all   # 构建本地数据字典（里程/车站档案/离线时刻）
bash scripts/upgrade_python.sh # 升级到 Python 3.12 并重建环境（失败自动回滚）
bash backend/tests/run_all.sh  # 全部测试套件（跑完再汇总）
```

## 数据源与合规

本项目只调用**公开可访问**的接口，不绕过任何登录、验证码或付费机制；对他人站点只做低频抓取 +
本地缓存 + 标注来源，**不整表对外分发**。接入新数据源前请先读
[`docs/datasources.md`](docs/datasources.md) 结尾的「抓取纪律」。
