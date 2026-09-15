# 文档索引（docs/）

> 先读哪个？**想了解现状 → `EXPL.md`；想跑起来 → `run.md`；想参与开发 → `CONTRIBUTING.md`；想知道开发历程与后续方向 → `plan.md`。**

---

## 一、按用途分类

### 现状与规划

| 文档 | 内容 | 何时读 |
|---|---|---|
| [`EXPL.md`](EXPL.md) | **项目现状权威说明**（能力、代码结构、工具清单、测试、设计要点、已知风险、技术债） | 接手项目、通读代码前 |
| [`plan.md`](plan.md) | 里程碑 M0–M10 与「上线前必做清单」 | 想知道开发历程与待办 |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | **社区版协作说明**：环境、测试命令、代码风格、数据源接入的合规要求 | 准备提交改动 |

### 运行与运维

| 文档 | 内容 |
|---|---|
| [`run.md`](run.md) | 安装、启动、联调、测试、前端交互说明 |
| [`keysetsug.md`](keysetsug.md) | API Key 安全方案（环境变量注入/direnv/Keychain）与部署前收口步骤 |
| [`datasources.md`](datasources.md) | 各数据源的**逆向过程与踩坑记录**（12306 反爬、rail.re API、搜索引擎选型、径路解析） |
| [`source-expansion.md`](source-expansion.md) | 数据源扩展评估：待接入源、硬约束、合规红线 |
| [`troubleshooting.md`](troubleshooting.md) | **排障手册**：五类故障排查路径、可执行方法论清单（含 ⏳ 待研判项）、日志字段速查 |
| [`perf-plan.md`](perf-plan.md) | **响应延迟诊断与优化方案**：逐阶段实测、P0 已实施结果与后续 P1/P2 项 |
| [`12306-station-screen-api.md`](12306-station-screen-api.md) | **12306「车站大屏」端点探测报告**（免登录 POST 端点、字段字典、可查窗口、空结果三义歧义）+ 本项目 `station.screen` 的接入实现 |
| [`trainvisual-structure.md`](trainvisual-structure.md) | **第三方站 trainvisual.top 结构分析**：页面/API 全景、配属数据模型、能力对标。**含合规前提（robots 对 AI agent 的 Disallow），当前不建议接入** |

### 质量与验证

| 文档 | 内容 |
|---|---|
| [`testset.md`](testset.md) | **车迷测试集（96 条）**：非技术视角的黑盒验收题库与记录表 |
| [`ragval/README.md`](ragval/README.md) · [`ragval/testset.txt`](ragval/testset.txt) | **RAG 验证工作流（Agent 交叉验证版）**：机器可读测试集（50 条）+ 格式声明 + 评分算法 + 多角色 Agent 流程（参考 RAGAS/TruLens/DeepEval/多判官一致性） |
| [`ragval/run/report.md`](ragval/run/report.md) | **R1 验证报告**：通过率 76%、1 条红线（D06 陈旧数据冒充今日）、κ=0.66、9 项缺陷与测试集校准项（证据 `ragval/run/*.jsonl`） |
| [`ragval/run/fix-record.md`](ragval/run/fix-record.md) | **R1 → R2 修复记录**：D06 红线（次日值冒充今日）结构性隔离 + 截断三层治理（工具侧过滤 / 完整性契约 / 表格渲染器） |
| [`ragval/run/data-capability-proposals.md`](ragval/run/data-capability-proposals.md) | **数据能力缺口方案**：F05 老京沪线里程口径 / F06 按线路名反查站序 / H03 京沪达速 —— 三组可选方案与建议 |
| [`test-report-2026-09-14.md`](test-report-2026-09-14.md) | 第 1 轮执行结果报告（通过率 56.8%、5 个系统性缺陷、原始证据索引） |
| [`test-round1-analysis.md`](test-round1-analysis.md) | **第 1 轮问题的逐条归因与解决方案**（代码级根因 + 已修/待修 + 复验步骤） |
| [`_run_harness.py`](_run_harness.py) · [`_results.jsonl`](_results.jsonl) | 执行器与 99 条原始问答证据（可复现） |

> `test-round1-analysis.md` 与 `ragval/run/*` 中的"证据/原始报告"是调试记录，不是对外接口契约。

---

## 二、代码位置速查

```
backend/app/
  main.py           入口（/health、路由挂载、静态托管前端）
  config.py         全部配置（与 .env.example 一一对应，由 test_config_docs 保证）
  dates.py / od.py  日期归一化、起讫站解析
  context.py        多轮上下文裁剪
  models.py         请求/响应与流水线结果模型
  api/              chat（含 SSE）
  pipeline/         三层流水线：intent → extract → retrieve → generate（orchestrator 编排）
  llm/              LLM 客户端封装（含确定性 mock）
  tools/            16 个数据源工具 + registry（数量以 `registry.list_enabled()` 为准）
  data/             离线车次目录 + 本地数据字典（里程/车站档案/离线时刻）
backend/tests/      测试套件（由 tests/run_all.sh 汇总执行）
frontend/           原生 HTML + JS（无构建工具，由后端静态托管；移动优先，多对话）
scripts/            setup.sh（一键安装启动）、prewarm.sh（预热离线数据）、upgrade_python.sh
```

---

## 三、约定

1. **接口与数据模型先行**：改接口/数据模型前，先改 `datasources.md`、`12306-station-screen-api.md` 等对应文档，再改代码。
2. **缺陷要留痕**：根因与修复对应关系写入 `test-round1-analysis.md` / `ragval/run/fix-record.md`，并附回归测试位置。
3. **测试分层**：无网络套件（可在 CI/断网运行）与真实联调套件分开；禁止"零断言/SKIP 即绿"。
4. **数字不自相矛盾**：测试套数、配置项、工具数等由测试或脚本核对（`test_config_docs.py` 已覆盖配置项与文档路径）。
5. **数据源合规**：接入任何新数据源前，先读 [`source-expansion.md`](source-expansion.md) §四「合规红线」。
