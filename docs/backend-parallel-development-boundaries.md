# Main 后端并行优化边界

记录日期：2026-09-30。

本文用于后端优化与 Android 前端迁移、设计验收同时进行的阶段。
目标是在优化性能和查询可靠性时，让现有 Android 与 WebUI 客户端继续兼容。
这是当前协作阶段的冻结约定，不表示这些接口永远不能升级。

## 一、后端任务不能直接修改的内容

| 范围 | 约束 |
| --- | --- |
| `android/` | 不修改 Compose 界面、客户端数据模型、网络解析、会话存储、设置、系统安全区、原生测试和构建配置。尤其不动 `MainChatScreen.kt`、`ScheduleCard.kt`、`HistoryScreen.kt`、`ChatModels.kt`、`ChatRepository.kt`。这些由前端任务负责。 |
| `frontend/` | 不修改 WebUI、文案、图标、设计目标、测量映射和视觉采集夹具。不能为了适配后端变更而顺带改客户端。 |
| `desi1.png`、`desi2.png`、其他设计资源 | 不替换、重绘或调整设计基准，不引入设备状态栏及设计标注。 |
| `scripts/acceptance.sh`、原生验收消费端及采集工具 | 不修改验收入口、坐标换算、触控规则、容差、截图状态或通过条件。消费端由 H 负责，前端采集由 L 负责。 |
| `acceptance/` | 不覆盖正式索引、对照图和归档，不手工修改状态为通过。后端诊断产物放到独立目录。 |
| `.ai/reports/` 中前端与 H 的既有报告 | 不覆盖他人报告或改变已接受任务的结论。 |
| LM 专属实现和配置 | 不以 Main 优化为由修改 LM 工作范围，不向 Main 重新加入本地模型入口。 |
| 版本、签名、安装包与启动配置 | 不在后端性能任务中顺带升级应用版本、调整签名、修改 Android 打包或客户端连接地址。 |

独立后端任务可以编写自己的说明文档；修改共同架构说明前，先确认没有覆盖前端同期变更。

## 二、冻结的是外部契约，不是整份后端源码

下列文件可能同时包含接口和内部实现。可以优化其内部逻辑，但不能在独立后端任务中
改变客户端可见的路径、字段、类型、状态含义和交互行为。

| 契约来源 | 必须保持稳定的内容 |
| --- | --- |
| `backend/app/api/chat.py` | 聊天路径、HTTP 方法、SSE 编码、取消和错误交付行为。 |
| `backend/app/models.py` | `ChatRequest`、`ChatMessage`、`PipelineResult` 的外部字段及兼容语义。 |
| `backend/app/api/providers.py` | 设置页使用的供应商目录、模型列表和连接测试契约。 |
| `backend/app/display_result.py` | 展示协议版本、结果种类、状态、嵌套字段和数据口径。 |
| `backend/app/pipeline/orchestrator.py` | 对外事件语义、结构化动作入口、块式与流式展示结果的一致性。 |

### 1. API 路径与请求

保持以下现有入口及 HTTP 方法：

- `POST /api/chat`
- `POST /api/chat/stream`
- `GET /api/sessions`
- `GET /api/providers`
- `POST /api/providers/models`
- `POST /api/providers/test`

不得改变 `/api` 前缀、默认连接约定或增加客户端必须提供的登录、账户和计费条件。

聊天请求保持 `message`、`session_id`、`history`、`display_action` 的名称、类型和用途。
历史消息保持 `role`、`content`；不能把本次输入重复拼进历史。
按请求指定的云端参数保持兼容：`provider`、`model`、`api`、`base_url`、`api_key`、
`max_tokens`、`context_tokens`。不得把用户传入的配置静默替换成全局共享配置。

这些是需要保持的现有能力，不表示块式入口已经实现与流式入口完全相同的动作行为；
不要在优化任务中把尚未验证的能力写成已实现。

### 2. SSE 事件及结束结果

保持 UTF-8 JSON 的 `data:` 事件与空行分隔，不改成另一种流式协议。

| 事件 | 保持的字段及含义 |
| --- | --- |
| `stage` | 阶段进度，保留 `stage`、`msg`、`ms` 的用途。 |
| `think` | 普通知识问答允许的思考增量，使用 `delta`。 |
| `answer` | 回答文本增量，使用 `delta`。 |
| `replace` | 使用 `text` 替换已累积的回答，不是追加。 |
| `error` | 保留客户端识别的错误通知及 `message`。 |
| `done` | 结束元数据与展示结果，不因正文为空而删除结果卡片。 |

`done` 保持现有字段及类型，包括 `intent`、`question_type`、`slots`、`sources`、
`tool_trace`、`thinking`、`answer_done`、`degraded`、`truncated`、`truncate_reason`、
`planner`、`error`、`usage`、`latency_ms`、`process_logs`、`display_results`。
用量继续区分 `total_tokens`、`prompt_tokens`、`completion_tokens`；耗时字段保持毫秒单位。

不要求事件数量、文本分块长度或耗时完全不变；冻结的是事件用途和客户端解析方式。
普通问答保持流式输出。结构化铁路查询继续执行现有缓冲与重复事实保护，
不能重新让模型叙述或思考流承担时刻、站点和交路事实展示。

用户停止或连接断开后，继续释放上游请求；不得新增无条件后台续跑、自动重发，
也不得为了补结束事件而伪造成功结果。

### 3. 结构化展示协议

当前协议为 `schema_version: 1`。独立后端优化不得静默升级版本、改名、删字段、
改变数组嵌套或使用客户端不认识的新结果替代旧结果。

| 结果种类 | 保持的关键字段 |
| --- | --- |
| `train_schedule` | `status`、`train_code`、`date`、起终站、顶层到发时刻、`duration`、`stops`、`schedule_type`、`time_basis`、`today_times_available`、`sample_data`、`sources`、失败时的 `error`。 |
| `train_schedule_batch` | `status`、`items`，每项继续保留独立车次、日期和成功或失败状态。 |
| `emu_routing` | `status`、`query`、`focus_date`、`records`、`source`、`time_semantics`、`sample_data`、`sources`。 |
| `empty` | 保留空结果及查询日期语义，适用时保留 `query`、`historical_records`、`sources`。 |
| `error` | 保留失败结果及 `message`，适用时保留 `category`、`tool`。 |

站点字段白名单保持 `station_no`、`station`、`arrive_time`、`start_time`、`stopover_time`；
交路记录保持 `train_code`、`date`、`time`。嵌套版本字段沿用现有投影。
保持 `success`、`partial`、`empty`、`failed` 状态含义，缺失值不伪装为有效时刻。

尤其不能改变以下事实口径：

- 图定或参考时刻不能标称为实际运行时刻、实时位置或正晚点。
- `time_basis` 中 `reference`、`stations_only` 的含义保持稳定。
- 当日时刻不可用且无明确图定参考时，继续隐藏不能确认的时刻。
- 交路记录时间不是列车到发时间；历史记录继续带原始日期。
- 查询日无记录不能用其他日期的数据冒充当日结果，也不能据此断言停运。
- 失败项保留车次和日期；定向重试不能丢掉已成功结果或扩大到无关车次。
- 块式与流式入口对相同工具结果使用统一展示投影，不维护两份不同口径。

### 4. 结构化动作与设置错误

保持客户端提交的 `display_action` 字段及现有动作语义，例如
`kind: train_schedule_batch` 中的 `trains`、`date`，以及更换日期、查看最近记录的现有载荷。
不能通过重新解析按钮文案取代结构化参数，也不能扩大定向重试范围。

保留模型设置、密钥错误、网络错误的现有恢复路径及客户端依赖的状态信息。
不得为了提高成功率把真实错误改成空白成功、伪造数据或隐藏供应商故障。
供应商设置、响应、日志与诊断产物不得回显 API Key。

## 三、可以并行推进的后端优化

在上述契约保持兼容的前提下，可以优化：

- 工具内部的数据获取、规范化、超时、限流和重试策略。
- 查询性能、连接复用、并发、缓存与相同请求合并。
- 意图识别、检索调度及云端模型调用成本。
- 票价等既有查询能力的接口可靠性与结果完整性。
- 脱敏日志、分阶段耗时、失败率统计及后端内部实现整理。

缓存和请求合并必须区分查询日期、车次、动作及适用的供应商配置；不能串用用户密钥、
模型输出或把历史记录缓存作为当日结果。取消一个请求时，不应错误取消其他用户的共享查询。
这些优化不自动授权启动 Python 到 Kotlin 的整体重构；该事项仍按候选计划单独安排。

## 四、并行协作与交付

1. 优先在独立分支或工作树中开展后端工作，保留前端未提交改动。
2. 开始前列出计划修改文件；同一文件有其他任务正在修改时，先明确负责范围。
3. 后端联调使用独立端口，不停止或替换前端正在使用的后端、设备和验收进程。
4. 不把未测的视觉项目标为通过；后端性能结果与前端视觉验收分别记录。
5. 需要协议变化时，先提出字段差异、影响客户端及兼容方案，由 H 排入同步任务；
   不将破坏兼容的修改混入独立性能任务。已有优化授权不等同于已安排客户端配套升级。
6. 交付时说明修改范围、对外契约是否变化、测量条件、效果及剩余问题。
   需要客户端实测时启动本次后端，并明确工作树、端口与配置，避免误连旧版本。

项目已有结构化 SSE 回归和固定场景证据可用于后续核对，但不能因为夹具通过而声称
真实云端查询已经通过。是否运行或新增测试，按具体优化任务约定执行。

## 五、相关约束

- [UI 设计边界](ui-design-boundaries.md)
- [原生验收证据协议](native-acceptance-evidence.md)
- [实现与数据完整性契约](EXPL.md)
- [Kotlin 业务引擎候选计划](plan-kotlin-business-engine.md)
