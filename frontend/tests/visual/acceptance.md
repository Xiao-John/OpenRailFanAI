# Main 移动端逐项验收（2026-09-29）

设计基准：`desi1.png`、`desi2.png`。页面取自 Microsoft Edge，390×844 CSS viewport、DPR 1；九张原始 PNG、DOM 矩形和操作记录见 `screenshots/verification.json`。`measurement-map.json` 给出每个目标的原图框、比例、原图矩形及页面选择器；`page-comparisons/` 是原图和页面并列图。两张设计图对阅读输入栏高度给出不一致的值（51.41 与 42.95 CSS px），该项单列为 `design_conflict`，页面固定输入栏按完整手机图保持 51px。

校准后的主要布局：71/71 项通过 2px 容差，另有上述 1 项设计冲突。逐元素文本、占位符和无障碍标签：109/109 项通过完整文本比较。图标轮廓：`icon-comparisons/measurements.json` 的 32 项中 1 项通过、31 项超过 1px；每项有原图裁切与 Edge 图标叠加图。这一图标结果保留为未通过，不由布局通过推断图标通过。

| 原任务 | 原验收条件对应证据 | 结论 |
|---|---|---|
| T1 | `projection-samples.json` 同一工具数据重复投影相等；实时成功保留 07:10/09:20 和逐站时间，当日不可用清空到发时间；`backend/app/display_result.py` 不读取模型答案；此前接受的 FT1 SSE 分支证据保留 | 通过当前确定性投影核对 |
| T2 | `backend/app/pipeline/generate.py` 有事实表格抑制约束，`orchestrator.py` 缓冲结构化分支；缺不同实际模型输出样例 | 未验收，缺真实模型回答样例 |
| T3 | `verification.json` `train_schedule` 的标题、摘要、线路表、信息、来源和操作共 13 项边界通过；`train_schedule.png` | 通过布局条件 |
| T4 | `verification.json` `emu_routing` 的记录、时间语义、来源、批量入口共 12 项边界通过；车次入口操作记录；`emu_routing.png` | 通过组件条件 |
| T5 | `query_loading.png` 记录活动 SSE 和可见停止按钮；点击后请求为 `aborted`、用户消息保留；`verification.json` `query_loading stop` | 通过 |
| T6 | `batch_partial.png` 两项成功一项失败；重试载荷仅失败车次，成功行保留；`verification.json` `batch_partial retry failed rows` | 通过固定状态交互 |
| T7 | `routing_empty.png` 显示日期；日期变更和最近记录分别发送结构化动作；`verification.json` `routing_empty change date and recent records` | 通过 |
| T8 | `connection_error.png`、展开错误详情、原消息重试、设置页 API Key 字段错误且不暴露密钥；`verification.json` 两条 `connection_error` 操作 | 通过固定错误夹具 |
| T9 | `query_details.png` 和 `verification.json` `request details metadata` 核对本次夹具的来源、日期、时间口径、5.8 秒与 1,555 Token；未覆盖所有结构化结果种类 | 部分通过，其他结果种类的请求元数据未逐一验收 |
| T10 | 先前接受的 FT2 阅读证据保留；本次 `verification.json` 记录滚动锚点偏差 0px、提示可见、点击后到底部、追问保留草稿且未发送 | 通过 |
| T11 | `history.png`；`verification.json` 操作记录覆盖搜索、日期组、新建、选择、重命名、删除、取消 | 通过固定会话交互 |
| T12 | 九状态 71/71 项主要容器及按钮边界通过；固定栏使用完整手机图高度，1 项设计冲突单列；`measurement-map.json` | 通过可比较的页面几何项 |
| T13 | `icon-comparisons/measurements.json` 和逐图标叠加 PNG：1/32 项在 1px 内 | 未通过；31 项轮廓或中心超差 |
| T14 | `design-copy.json` 绑定具体元素，`verification.json` 九状态 109/109 完整文本比较通过 | 通过固定状态文案 |
| T15 | `docs/EXPL.md` 与现有 Main 协议、状态流和本验收结果同步；不变更 LM 说明 | 通过文档核对 |
| T16 | 九张 390px Edge 实际 PNG 与九份 `page-comparisons/` 并列图；71 项布局通过，图标 31 项未过 | 未通过全部视觉验收 |

这些结论仅覆盖表内列明的证据。布局、文案、交互及图标分别计量，未把图标超差或真实模型输出缺证据写作通过。
