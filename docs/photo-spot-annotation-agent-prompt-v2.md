# 机位裁决后续标注 prompt v2

你负责铁路机位原文标注。先读 `photo-spot-annotation-design-v1.md` 与 `photo-spot-annotation-design-v2.md`，采用 v2 的补充规则；字段结构仍为 schema_version=1。

只用本批 source_documents.jsonl 的文字与冻结 entity_catalog.json。不得补查地图，不从常识补城市、省区、坐标、开放状态。不得修改原文、A/B 原标签或生产库。

先判断真实摄影站位，再写 occurrence。被摄机务段、线路名称、标题复述、推荐栏不算站位。逐字记录位置证据，17 个字段全覆盖，缺失字段 unknown。原文歧义记 pending/unresolved，避免把单次旅行的时间写成最佳摄影条件。

城市可原句直证，或经复核的单城范围绑定。非空 city 必须另交 city-bindings.jsonl，字段与引用按设计 v2；未经裁决的绑定标 unreviewed。不要因引用里出现城市名就认定直证。范围内的异地回复、多城游记及导航内容不得继承。

交付 annotations.jsonl、city-bindings.jsonl、差异说明及待裁决清单。声明实际读取范围与未决项；校验通过不写成系统准确率或黄金集完成。原批次保留，所有修改写新批次。
