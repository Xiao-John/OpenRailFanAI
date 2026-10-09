# 机位争议标记与点击说明：前端交接

日期：2026-10-08。后端已接入，前端尚需实现下面的标记与详情交互。本次没有修改 Android、WebUI 或 release。

## 前端需要做什么

**默认只显示一个“待确认”标记，不显示争议原因、原文证据或长段说明。点击标记后请求详情，以现有弹层或底部面板展示。**

已有聊天协议与 SSE 不变。当前机位回答仍使用文字和 sources URL；前端可根据来源 URL 获取说明索引，给对应来源/机位条目附加标记。不要把 marker 作为新聊天消息，也不要重复助手头像。

若使用下述结构化检索入口渲染机位列表，可直接用每个 item 的 issue_markers。标记为空则不显示。多个争议可以汇总成一个“待确认”入口，点击后按相关字段排列；不要默认打开或自动请求所有详情。

## 三个只读入口

### 1. 获取来源标记

`GET /api/photo-spots/documents?url=<完整来源URL>`。使用 URL 查询参数编码，不把来源 URL 拼成路径。

返回 `url/doc_id/status/contract_version/issue_count/issue_markers`。status 为 `reviewed/unreviewed/stale/not_annotated`：

- reviewed 表示文档已做裁决，不表示所有字段确定；仍可能有标记。
- unreviewed 表示尚未语义复核，不当作确定性结构化事实。
- stale 表示原文已变化或索引无法核对，旧标注暂停参与过滤；点击说明可查看原因。
- not_annotated 表示旧词典/未覆盖来源，没有说明索引，不代表没有机位。

marker 形状：

```json
{
  "issue_id": "psi_…",
  "field": "rail_relation",
  "label": "待确认",
  "occurrence_id": "doc_…:全文哈希:位置偏移",
  "applies_to": "occurrence",
  "detail_url": "/api/photo-spots/issues/psi_…"
}
```

applies_to 为 document 时是来源整体问题，不能解释成每个机位都有相同错误。全文变化的标记也可能使用 pss_ ID；ID 都按不透明字符串处理，不解析或自行生成。

### 2. 点击后读取说明

`GET /api/photo-spots/issues/{issue_id}`。

返回 `issue_id/doc_id/url/occurrence_id/field/reason_code/title/note/evidence/source_status`。弹层展示 title 与 note，必要时提供“查看原文依据”折叠区和来源链接；evidence 包含逐字 quote 与来源区间。不要把这些内容写入默认回答正文。

source_status 为 current/stale；current 只表示全文哈希一致，不代表今天可以进入机位。历史交通、开放信息保留原文时效，不展示成当前保证。

点击时显示局部加载状态，失败允许重试。404 表示索引不存在或随词典更新失效，应重新取来源索引并提示“说明已更新”；不清空原回答。优先按当前词典版本缓存，词典更新后清理缓存。

### 3. 按已确认字段检索

`GET /api/photo-spots/search?scope=深圳&city=深圳&point_type=bridge&limit=3`。

参数：scope 为地点/名称关键词；city、point_type、view_target、season、time_of_day 为可选精确过滤。limit 为 1–20。地点与过滤条件都为空或枚举不支持返回 422。

返回 `available/items/total/shown/truncated`。每项含 occurrence_id、name_raw、url、review_status、claims、issue_markers。claims 只包含可用字段及 group_id/valid_time_raw，**不包含争议说明或 evidence**。name_raw 是原文称呼，不是导航点位认证。

支持枚举沿用标注契约，view_target 新增 railway_remains，前端可显示“铁路遗存”。这仅是新增检索接口的数据类别，未更改既有聊天展示种类。季节/时段缺失不等于不适合；未确认字段不会成为过滤命中。不同条件组不拼成一个已确认组合。

available=false 表示词典没有标注表；展示兼容提示或继续已有文字回答，不当成“没有机位”。total/shown/truncated 如实展示，不把前三条当全部结果。

## 已完成的数据与边界

本地 `backend/data/dict.db` 已导入 60 篇标注：20 篇有裁决记录，40 篇未复核。说明索引共有 130 项，包含未复核材料的既有疑问；不能解释成 130 个独立事故或确定错误。已裁决材料的未决字段/片段仍为 34 条。

部分字段有争议不会拖住其他已确认字段。例如笋岗桥的城市、桥类型可用于过滤，但未绑定实体 ID 的铁路名称不能当确定铁路过滤；具体说明点击后查看。标题锚点、区域多个机位及被摄设施不会冒充确认站位。

软件携带完整 dict.db 即可带上 `photo_annotation_doc/photo_annotation_issue`。新的软件词典合并会同步这两表；更新成没有标注索引的较新旧式机位库会清理旧索引。独立 GTFS 更新保持索引。前端打包配置未改，也未生成新安装包。

## 前端验收点

1. 深圳桥类检索仍有结果；有争议的条目默认只显示标记，长说明与证据不可见。
2. 点击标记后才发详情请求；关闭后保留原结果、输入草稿和滚动状态。
3. 多个问题可查看，文档问题与具体机位问题不会混作一条事实。
4. 加载、失败重试、索引 404、词典更新后的缓存失效均可恢复。
5. 未标注旧库、未复核和原文变化状态不解释成“机位不存在”；旧聊天与历史会话仍可读。

后端验证：35 项单元/集成测试、原机位测试与实际应用入口检查通过。前端点击交互尚未实施，不能将这份后端结果当作前端验收通过。
