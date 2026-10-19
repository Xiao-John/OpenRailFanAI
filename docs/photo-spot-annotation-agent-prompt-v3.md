# 机位后续标注 prompt v3

沿用 `photo-spot-annotation-agent-prompt-v2.md`，增加 `photo-spot-annotation-design-v3.md` 两项规则。

明确废弃铁路设施用 railway_remains，保留原文时效；静态车辆、运营铁路分别使用已有类别。“像废线”“车少”不得推废弃。

规范实体必须在冻结目录。原文名称不改写；经审别名只允许外置映射表明确批准的 occurrence_id 使用。新别名、新语境或目录缺实体均保持 stated/未决，列出证据和候选映射，不自动推广或造 ID。

交付新增批次的 annotations、city-bindings、entity-aliases、修改记录与验证报告。原批标签、来源、目录不改；校验通过不等于机位当前开放、安全或系统准确率。
