# 协作说明（社区版）

> 面向想给本项目提改动的开发者。所有命令都在**仓库根目录**或注明的目录下执行。

---

## 一、先把项目跑起来

```bash
bash scripts/setup.sh        # 建 venv → 装依赖 → 跑测试自检 → 启动（推荐）
```

手动方式与全部配置项说明见 [`run.md`](run.md)。要求 **Python ≥ 3.10（推荐 3.12）**；
实时数据源（12306、rail.re）需要**中国境内网络出口**，否则相关工具会如实降级报缺口。

## 二、跑测试（提交前必做）

```bash
bash backend/tests/run_all.sh               # 全部套件：跑完再汇总，任一套失败则退出码 1
cd backend && bash tests/run_all.sh         # 等价写法

# 单个套件（在 backend/ 下）
PYTHONPATH=. .venv/bin/python tests/test_pipeline.py    # 三层流水线（假 LLM，无网络）
PYTHONPATH=. .venv/bin/python tests/test_tools.py       # 16 个工具逐个调用（需境内网络）
```

- `docs/run.md` §6 列出了全部套件、各自覆盖范围与是否需要网络。
- 大部分套件**无网络**，可在 CI/断网环境运行；少数依赖真实 LLM 或境内数据源。
- 前端数据层单测需要 `node`：`frontend/tests/store.test.mjs`（由 `test_frontend_store` 驱动）。
- 硬性要求：**禁止"零断言即绿"**，也禁止用 SKIP 假装通过——不能跑的用例要显式说明跳过原因。

## 三、代码约定（沿用仓库既有风格）

| 项 | 约定 |
|---|---|
| 语言/版本 | Python ≥ 3.10；文件首行 `from __future__ import annotations` |
| 注释与文档 | 模块 docstring 与关键注释**用中文说明"为什么"**（尤其是实测踩过的坑与对应事故编号）；测试文件 docstring 写明"运行命令 + 覆盖范围" |
| 配置 | 一律走 `backend/app/config.py`（pydantic-settings），字段同步更新 `.env.example`——`test_config_docs.py` 会校验二者一致与文档路径存在 |
| LLM 调用 | 统一走 `backend/app/llm/client.py`（含 `LLM_MOCK=true` 的确定性 mock）；不要在业务层直连 SDK |
| 新增数据源工具 | 在 `backend/app/tools/` 实现工具类，在 `registry.py` 注册，并补一个可离线运行的测试 |
| 工具返回 | 必须使用统一的 `ToolResult`，并如实填写数据完整性字段（`total` / `shown` / `truncated` / `filters` / `fetched_at`）——**"截断"不能表述成"没有"** |
| 前端 | 原生 HTML + JS，无构建工具（由后端静态托管）；不引入打包器 |
| 脚本 | `scripts/` 下的脚本保持可重入、失败有明确提示 |

## 四、文档约定

- 文档索引：[`README.md`](README.md)，新增文档请一并登记。
- 改接口 / 数据模型前，先改对应文档（`datasources.md`、`12306-station-screen-api.md` 等）再改代码。
- 缺陷的根因与修复对应关系写入 `test-round1-analysis.md` / `ragval/run/fix-record.md`，并附回归测试位置。
- **数字不自相矛盾**：测试套数、配置项、工具数等会被测试或脚本核对，不要手写与仓库不符的数字。

## 五、数据源接入的合规要求（硬性）

接入任何新数据源之前，先读 [`source-expansion.md`](source-expansion.md) §四「合规红线」。摘要：

1. **不绕过任何限制**：不绕登录、不绕付费墙、不绕验证码；12306 不做批量抓取。
2. **遵守 robots 与站点声明**：明确禁止 AI agent 抓取的站点不接入。
3. **限速与缓存**：他人站点一律低频（个人小站 ≤1 req/s）+ 本地缓存 + 可识别 UA，失败即退避。
4. **标注来源与时间戳**：每条下发数据带来源与采样时刻；社区数据冲突时如实呈现分歧。
5. **不整表分发**：里程表、配属表等第三方数据只做本地检索使用，不整表对外分发。
6. **许可风险先评估**：无 LICENSE / 标注"不得商用" / 含照片著作权的数据源，默认不接入或仅本地使用。

## 六、提交改动

- 一次改动尽量只解决一件事；行为变化请附回归测试。
- 提交说明写清**改了什么 + 为什么**；涉及数据源口径变化时引用实测证据（响应片段、字段名、时间）。
- 发现文档与实现不一致时，以**实现为准**并同步修文档，不要只改一边。
