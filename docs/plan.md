# RailFanAI v1 设计与实施计划

## 目标
面向中国铁路迷的 RAG / Agent 助手。输入一句自然语言，输出有依据的回答并附数据来源。

典型案例：
> "我在吉林市XX区，要拍 CR400AF，今天下午"
> 意图：拍摄点推荐（photo_spot）；槽位：地点=吉林市XX区，目标=CR400AF（复兴号动车组），时间=今天下午。

## 三层流水线（核心设计）
| 步 | 模块 | 说明 |
|---|---|---|
| 1 意图分类 | `pipeline/intent.py` | LLM 结构化输出归类（拍摄点/时刻/车站/余票/资讯/闲聊） |
| 2 信息抽取 | `pipeline/extract.py` | 槽位填充：地点、目标、时间、方向、车次等 |
| 3 检索+生成 | `pipeline/retrieve.py` + `generate.py` | Agent 工具循环按需取数 → LLM 生成回答 + 来源 |

## 数据源工具（retrieve 层可调用）
| 工具 | 来源 | 用途 |
|---|---|---|
| `t12306.search_tickets` | 12306 官方查询（参考 Parse12306） | 余票/时刻 |
| `railre` | rail.re（原 moerail.ml） | 车站 / 动车组交路 |
| `cnrail.map` | cnrail.geogv.org | 铁路地图 / 站名定位 |
| `web.search` / `web.fetch` | 通用搜索/抓取（兜底） | 其余资讯、站名、机位等 |

## 会话与持久化
- v1：会话状态内存存储；真·RAG 向量库（embedding + 检索）后置。

## v1 里程碑
- [x] M0 项目骨架与配置
- [x] M1 LLM 客户端（OpenAI 兼容 chat / chat_structured）接入并验证
- [x] M2 意图分类 + 槽位抽取（`tests/test_pipeline.py` 假 LLM 端到端验证）
- [x] M3 数据源工具实现并注册（T12306 / railre / cnrail / web.fetch）——`tests/test_tools.py` 验证
- [x] M3.1 检索层按意图路由调用工具（`retrieve.py` 的 `_plan`）
- [x] M4a 检索层真实调用已注册工具并收集来源（`retrieve.py`）
- [x] M4b 生成层完整引用检索数据注入 prompt（`generate.py`；`tests/test_integration_fullchain.py` 整链验证）
- [x] M5 真实 LLM Key 整链联调（SiliconFlow/deepseek-ai/DeepSeek-V4-Flash）
  - 真实连通：模型回复"正常"
  - 真实整链：示例句"我在吉林市XX区，要拍 CR400AF，今天下午" → intent=photo_spot、槽位{location/target/time}、cnrail 地图来源收录、生成层诚实说明数据缺口（railre/12306 未接通时不编造数据）
  - HTTP 冒烟：schedule 意图示例正确分类、t12306 未配置如实报失败、模型按检索事实回答并附来源
- [x] M6a 无 Key 端到端整链演示（`LLM_MOCK=true`，文档见 `docs/run.md`）
- [x] M6b 真实 LLM 整链正式演示交付（含 LLM 客户端 HTTP 状态码中文诊断，401/400/404/429 分别给出可操作提示）
- [x] M7 真实接口配置（M3.1 收口）——"担当车组/交路"问题彻底解决
  - [x] M7a Python 3.9 → 3.12 升级（解决 macOS LibreSSL TLS 指纹被 12306 反爬拦截）
  - [x] M7b 12306 站点库接入（3384 站，`station.lookup`；含"吉林市XX区"占位符多级降级）
  - [x] M7c 12306 实时时刻/余票/经停站接入（`train.schedule`，经 `mcp-server-12306`）
  - [x] **M7d rail.re 交路 API 接入（`emu.routing`）**——车次↔担当车组互查，填补 12306 不公开的数据
  - [x] M7e 日期归一化（`app/dates.py`：今天/明天/9月14日 → YYYY-MM-DD）
  - [x] M7f 新增 `emu_routing` 意图 + 生成层按条注入来源与时效说明（避免数据时效串味）
  - [x] M7g 浏览器级请求头（`_http.py:BROWSER_HEADERS`）+ brotli 自动降级
  - [x] M7h 测试扩展至 5 套（`test_dates` / `test_pipeline` / `test_tools` / `test_emu_routing` / `test_integration_fullchain`）
  - [x] M7i **12306 主路径重构**：新增 `ticket.query`（直连官方接口，无需反代），
        路由不再调用恒停用的 `t12306.search_tickets`
  - [x] M7j `train.schedule` 仅给车次时**自动推断起止站**后查实时数据；已发车次给出解释
  - [x] M7k `app/od.py` 起讫站解析（"北京到上海"/"从北京去上海"→ 余票路由）
  - [x] M7l 生成层注入当前日期，避免 LLM 自行推算"明天"出错
  - [x] M7m 测试扩展至 7 套（新增 `test_od` / `test_api`）
- [x] **M8 AI 体验优化**（用户提出时称 "M7"；因 M7 已用于真实接口配置，此处记为 M8）
  - [x] M8a **多轮上下文**：`ChatRequest.history` + `app/context.py` 裁剪；
        意图/抽取/生成三层均接入（"那明天呢"可继承上文的 G1）
  - [x] M8b **暂停输出**：前端 AbortController 中断 fetch；服务端每事件前检查
        `request.is_disconnected()`，断开即停止并释放 LLM 流（省 token）
  - [x] M8c **编辑重发 / 重新生成**：用户消息可编辑后重发（丢弃其后消息）；
        助手消息可重新生成；均携带正确截断的 history
  - [x] M8d 前端重构为 DeepSeek 风格：多行输入框（Enter 发送 / Shift+Enter 换行 /
        中文输入法组合态处理）、发送键生成中变「■ 停止」、消息 hover 操作条
        （复制/编辑/重新生成）、新对话、ESC 停止、上下文轮数提示
  - [x] M8e 车次号归一化：兼容上下文继承出的 "G1次列车"（`extract_train_code`）
  - [x] M8f 生成层增加 [对话历史] 区块，并明确"历史结论不得当作本次检索事实"
  - [x] M8g 测试扩展至 8 套（新增 `test_context`；`test_api` 增加多轮/对照组/
        非法角色/提前中断用例）
- [x] **M9 铁路线路（径路）接口**
  - [x] M9a 逆向黄河铁路网「旅客径路查询」`?action=shrtroute` 接口
  - [x] M9b 新增 `rail.line` 工具：线路序列 + 车站序列 + 区间/累计里程
  - [x] M9c 新增 `rail_line` 意图，路由至 `rail.line`（"走哪条线/多少公里/径路"）
  - [x] M9d 解析器抗噪：定位径路表 + 5 列/3 位电报码双重过滤（页面内嵌 800KB 站名表）
  - [x] M9e 测试覆盖（正常/缺参/发到站相同）
- [x] **M10 分类型作答策略（放宽知识型问题的 prompt 限制）**
  - [x] M10a 意图分类新增 `question_type`（realtime / knowledge / mixed），
        并对齐 few-shot 示例保证判定稳定（实测 18 次分类 100% 一致）
  - [x] M10b 生成层按类型切换策略（`generate.answer_policy`）：
        - realtime：严格闭卷，只依据检索事实
        - knowledge：**允许结合模型知识**，但强制标注"（据模型知识，未检索确认）"
          并对编号/数值类给出不确定度
        - mixed：实时部分闭卷、知识部分可放开，分别标注
        未知/缺省一律回退 realtime（从严），避免放宽被误用于实时数据
  - [x] M10c 检索策略同步调整：知识型问题改走 `web.search` 取据
        （避免"样车车组号"误查 `emu.routing` 这类实时工具而落空）
  - [x] M10d `PipelineResult` 与 SSE `done` 事件携带 `question_type`；
        前端显示"知识型/混合型"徽标与"含模型知识，请核实"提示
  - [x] M10e 测试扩展至 9 套（新增 `test_policy`；`test_api` 增加
        知识型/实时型路由分流用例）

## 真实数据能力现状（M7 验收）

| 查询能力 | 状态 | 工具 |
|---|---|---|
| 站名/拼音/电报码互查 | ✅ 实时 | `station.lookup` |
| 两站间实时余票 | ✅ 实时 | `ticket.query` |
| 车次实时时刻/余票/经停站 | ✅ 实时 | `train.schedule` |
| **车次↔担当车组（交路）** | ✅ **实时** | **`emu.routing`** |
| **两站间线路径路 + 里程** | ✅ | **`rail.line`** |
| 车型↔担当车次 | ✅ 实时 | `emu.routing` |
| 铁路地图外链 | ✅ | `cnrail.map` |
| 网页兜底检索 | ✅ | `web.search`（Bing 中国） |

**实测样例**（2026-09-13）：
```
Q: G1今天由哪组动车组担当？
A: 今日（2026-09-13）G1次列车由 CR400BFA-5054 动车组担当。
   （数据来源：rail.re 交路数据，时效为今日，共计30条记录）
```

---

## 🧪 产品实测（96 条车迷测试集）

| 轮次 | 日期 | 结果 | 报告 |
|---|---|---|---|
| 第 1 轮 | 2026-09-14 | 通过 54（56.8%）/ 部分 20 / 不通过 21；**0 条一票否决**；识别出 5 个系统性缺陷 | [`test-report-2026-09-14.md`](test-report-2026-09-14.md) |
| — | — | 5 个缺陷 + 次级问题**已修复**并有回归测试 | [`test-round1-analysis.md`](test-round1-analysis.md) |

> 复验方式（无网络回归 + 真实模式复跑受影响用例）见分析文档 §9。

---

## 🚧 上线前必做清单（Pre-launch Checklist）

> **何时使用**：真正部署 / 对外提供服务之前，逐项核对。
> 本清单是「暂缓项」的登记处 —— 开发期有意放宽，上线前必须收口。

| # | 项 | 状态 | 说明 / 依据 |
|---|---|---|---|
| 1 | **LLM API Key 改为环境变量注入并轮换** | ⏳ 待办 | **已决定推迟到上线阶段**。执行步骤见 [`docs/keysetsug.md`](keysetsug.md) |
| 2 | **收紧 CORS** | ⏳ 待办 | 当前 `allow_origins=["*"]`（`main.py`），改为白名单 |
| 3 | **入口限流** | ⏳ 待办 | 当前无限流，任何人可调用并消耗 token；对外部署时在反向代理层收敛 |
| 4 | **日志脱敏** | ⏳ 待办 | 避免 Key 等敏感配置进日志 |
| 5 | **HTTPS + 域名** | ⏳ 待办 | 对外提供服务时置于 HTTPS 之后 |

> 完成一项就把 ⏳ 改成 ✅，并在此记录日期与执行人。

## 环境
- 复制 `.env.example` → 根目录 `.env`，填 `LLM_BASE_URL` / `LLM_API_KEY`（OpenAI 兼容）。
- 后端启动：`cd backend && uvicorn app.main:app --reload`。
- 前端：`frontend/index.html`（模块脚本，可由后端静态托管或任意静态服务器/DevServer 打开；跨域已由后端 CORS 放开）。
