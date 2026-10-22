# 文档索引

这是精选索引，不是 `docs/` 的全量文档清单。历史交接与验证记录仍保留在目录中。

文档分为**实现**、**部署**与**候选计划**；候选计划不代表已经实现或已授权执行。

| 文档 | 内容 |
|---|---|
| [`release-0.1.32.md`](release-0.1.32.md) | **发布说明**：PaddleOCR、本次查询内续查、思考开关及侧滑/滚动修正 |
| [`agent-ocr-thinking-update-20261022.md`](agent-ocr-thinking-update-20261022.md) | **Main 更新**：PaddleOCR、可续查工具循环、思考开关、侧滑与输入区跟随；编译通过，待真实使用验收 |
| [`release-0.1.31.md`](release-0.1.31.md) | **发布说明**：开关整行点击、圆角按压层裁切、贴底时输入区同步上推 |
| [`agent-handoff-20261021.md`](agent-handoff-20261021.md) | **交接**：0.1.30 后改动、开关修复、方角按压层审计与未决项 |
| [`release-automation.md`](release-automation.md) | **发布工具**：一份Markdown驱动版本、构建、签名核验、源码推送与Release上传 |
| [`release-0.1.30.md`](release-0.1.30.md) | **发布说明**：个人记忆、统一侧栏、模型标题及评审修复 |
| [`sidebar-and-model-titles-20261021.md`](sidebar-and-model-titles-20261021.md) | **Main 界面**：统一左侧栏、右滑打开、固定设置与模型会话标题 |
| [`personal-memory-mvp-20261021.md`](personal-memory-mvp-20261021.md) | **Main 试用实现**：默认关闭的本机记忆、消息保存入口、当前模型按需总结与隐私边界 |
| [`independent-review-fixes-20261019.md`](independent-review-fixes-20261019.md) | **修复**：独立评审问题、测试分层、部署边界与暂缓项目 |
| [`release-0.1.29.md`](release-0.1.29.md) | **发布**：多平台配置向导、图片文档输入、车次规则预备入口与源码同步 |
| [`frontend-inputs-fuck-cr-integration-20261019.md`](frontend-inputs-fuck-cr-integration-20261019.md) | **客户端**：可见附件入口、兼容状态按钮、17项定向检查与打包记录 |
| [`frontend-multi-provider-wizard-20261019.md`](frontend-multi-provider-wizard-20261019.md) | **客户端**：八平台配置向导、进度与密钥隔离 |
| [`frontend-provider-setup-wizard-design-20261014.md`](frontend-provider-setup-wizard-design-20261014.md) | **设计**：服务商选择至首次连接的引导流程与验证边界 |
| [`frontend-siliconflow-wizard-implementation-20261014.md`](frontend-siliconflow-wizard-implementation-20261014.md) | **客户端**：首批硅基流动向导与 Custom Tabs |
| [`backend-image-document-input-handoff-20261018.md`](backend-image-document-input-handoff-20261018.md) | **协议**：图片、文档、OCR、能力声明及回退契约 |
| [`backend-agent-tools-handoff-20261018.md`](backend-agent-tools-handoff-20261018.md) | **协议**：资料整理、文件草稿与安全边界 |
| [`backend-fuck-cr-button-handoff-20261019.md`](backend-fuck-cr-button-handoff-20261019.md) | **协议**：兼容准备状态、禁用开启与409处理 |
| [`backend-train-code-reform-preparation-20261017.md`](backend-train-code-reform-preparation-20261017.md) | **迁移准备**：版本边界、影子观察与尚未开放的新版业务规则 |
| [`backend-ticket-shared-rows-20261014.md`](backend-ticket-shared-rows-20261014.md) | **后端**：票价/余票同请求原始记录共享、隔离与真实测量边界 |
| [`backend-prompt-cache-measurement-20261014.md`](backend-prompt-cache-measurement-20261014.md) | **观测**：提示缓存命中与性能测量边界 |
| [`backend-retrieval-audit-20261014.md`](backend-retrieval-audit-20261014.md) | **审计**：检索调用与数据获取成本 |
| [`backend-decision-speed-20261013.md`](backend-decision-speed-20261013.md) | **后端**：确定性决策、站名匹配与分流验证 |
| [`backend-provider-wizard-handoff-20261014.md`](backend-provider-wizard-handoff-20261014.md) | **协作**：向导与后端诊断边界 |
| [`independent-review-20261019.md`](independent-review-20261019.md) | **历史评审快照**：提交前的发布、解析器与测试问题；后续发布修复见0.1.29记录 |
| [`dictionary-progress-integration-20261006.md`](dictionary-progress-integration-20261006.md) | **词典实时进度**：SSE阶段、真实下载字节/百分比和导入计数，未打包 |
| [`main-fare-availability-integration-20261006.md`](main-fare-availability-integration-20261006.md) | **票价与余票合并卡片**：席别并集、独立状态/来源/采样时刻及旧历史兼容，未打包 |
| [`main-ticket-delivery-dedup-integration-20261006.md`](main-ticket-delivery-dedup-integration-20261006.md) | **单渠道交付接入**：探测后端能力并声明合并卡片能力，兼容旧后端，未打包 |
| [`release-0.1.19.md`](release-0.1.19.md) | **发布记录**：执行价卡片、更新入口、随包词典和新安装图标 |
| [`main-frontend-changes-0.1.17.md`](main-frontend-changes-0.1.17.md) | **前端更改与交接**：回复区、Token、会话隔离及票价卡片；现有APK未包含10月5日后续修复 |
| [`backend-fare-card-handoff-20261005.md`](backend-fare-card-handoff-20261005.md) | **票价卡片交接**：当前纯客户端展示及后续结构化票价候选契约，不干预正在进行的后端修改 |
| [`main-structured-fare-integration-20261006.md`](main-structured-fare-integration-20261006.md) | **结构化票价接入**：版本1票价卡片、严格正文去重及混合多日期结果保留，纳入0.1.19 |
| [`main-updates-integration-20261006.md`](main-updates-integration-20261006.md) | **客户端更新接入**：软件检查、校验安装、GTFS独立更新及正式版随包词典，纳入0.1.19 |
| [`EXPL.md`](EXPL.md) | **实现**：架构、三层流水线、工具清单、数据完整性契约、测试与运行状态 |
| [`datasources.md`](datasources.md) | **实现**：各数据源的接入方式、接口形态、关键约束与踩坑记录 |
| [`run.md`](run.md) | **部署与运行**：安装、启动、配置密钥、接口示例、测试与数据准备 |
| [`local-model.md`](local-model.md) | **部署**：本地小模型（8G 内存 / 零 API 成本）—— 选型对比、内存预算、必做设置、实测数据与已知的坑 |
| [`backend-photo-spot-handoff.md`](backend-photo-spot-handoff.md) | **实现**：机位本地库的交接（为什么不实时查、怎么建库、闸门规则与踩过的坑） |
| [`android.md`](android.md) | **部署**：Android 一体化版本（Python 随包分发 + 系统 WebView）的构建、签名、体积与限制 |
| [`ui-design-boundaries.md`](ui-design-boundaries.md) | **实现约束**：应用 UI 与设备示意/设计标注的边界，以及 Edge CSS 诊断和视觉验收流程 |
| [`native-acceptance-evidence.md`](native-acceptance-evidence.md) | **验收协议**：原生运行归档、同批测试证据与逐场景采集基准 |
| [`backend-parallel-development-boundaries.md`](backend-parallel-development-boundaries.md) | **并行约束**：Main 后端优化期间冻结的接口、展示协议、前端文件及协作边界 |
| [`plan-kotlin-business-engine.md`](plan-kotlin-business-engine.md) | **候选计划，未启动**：Python 业务逻辑与现有 MCP 工具的 Kotlin/KMP 重构评估、上游依据及决策门槛 |

> 车次编排改革资料单独放在 [`../FUCK_CR/`](../FUCK_CR/README.md)（新字头表、过渡期、代码影响与信源登记）。

> 代码即文档：`backend/app/` 里每个模块与关键函数都有注释说明"为什么这么做"
> （尤其是踩过坑的地方）；测试用例同时充当行为规格。
