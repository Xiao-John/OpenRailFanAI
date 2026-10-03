# 文档索引

文档分为**实现**、**部署**与**候选计划**；候选计划不代表已经实现或已授权执行。

| 文档 | 内容 |
|---|---|
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
