# 文档索引

文档分为**实现**、**部署**与**候选计划**；候选计划不代表已经实现或已授权执行。

| 文档 | 内容 |
|---|---|
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
| [`android.md`](android.md) | **部署**：Android 一体化版本（Python 随包分发 + 系统 WebView）的构建、签名、体积与限制 |
| [`ui-design-boundaries.md`](ui-design-boundaries.md) | **实现约束**：应用 UI 与设备示意/设计标注的边界，以及 Edge CSS 诊断和视觉验收流程 |
| [`native-acceptance-evidence.md`](native-acceptance-evidence.md) | **验收协议**：原生运行归档、同批测试证据与逐场景采集基准 |
| [`backend-parallel-development-boundaries.md`](backend-parallel-development-boundaries.md) | **并行约束**：Main 后端优化期间冻结的接口、展示协议、前端文件及协作边界 |
| [`plan-kotlin-business-engine.md`](plan-kotlin-business-engine.md) | **候选计划，未启动**：Python 业务逻辑与现有 MCP 工具的 Kotlin/KMP 重构评估、上游依据及决策门槛 |

> 代码即文档：`backend/app/` 里每个模块与关键函数都有注释说明"为什么这么做"
> （尤其是踩过坑的地方）；测试用例同时充当行为规格。
