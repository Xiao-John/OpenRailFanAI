# 机位标注裁决口径 v2（试行）

本版用于 20261008 裁决批次。沿用 `photo-spot-annotation-design-v1.md` 的字段、证据偏移与 schema_version=1；补充下列规则。未接入生产检索，也不代表黄金集验收。

1. **侧别身份**：对齐索引使用 `(side, occurrence_id)`。两侧 ID 相同仍是两条标注记录。程序锚点配对不等于语义裁决通过；共用段落不证明同一位置。
2. **城市绑定**：原句明确连接城市与站位可用 direct。单城提问或推荐范围可用 document_scope，但必须逐项复核回复是否在该范围内。多城游记、异地回复、导航、相关推荐、scope 入库字段不得继承城市。省、区仍需独立证据。
3. **外置绑定表**：非空 location.city 必须登记 occurrence_id、claim_index、field、binding_mode、city_evidence_refs、position_evidence_refs、scope_region_refs、review_status。引用包含 source_field/start/end，来自同文档同哈希。城市与位置证据必须同时在 claim.evidence 中；scope 模式另存真实适用正文区间。只有 reviewed_ok 可用于后续城市过滤。机器检查引用与范围，语义限定由裁决者核实，不能靠城市字符串出现自动通过。
4. **缺失与疑问**：原文未提城市不填，也不自动产生 pending。归属冲突、指代不明才登记 pending。名称疑问句不能升级成已确认位置名。
5. **拍摄对象与站位**：设施、线路或车辆的照片说明不自动成为摄影者站位。没有实际站位依据的条目转 unresolved；不得从被摄地点推摄机位置。
6. **枚举边界**：地铁与国铁都是 operating_railway，同时出现不算 mixed。废弃铁轨不是 static_rolling_stock；当前枚举不能表达的对象保留 unknown + unsupported_value，不扩充生产字段。
7. **时效与条件**：一次到访的季节、时刻不等于推荐条件。历史启用、许可或封闭不等于当前状态。站台方位不是镜头方位；无遮栏不自动推出危险类型；相邻段落的交通、进出限制不能无依据迁移到站位。
8. **粒度争议**：明确并列的独立地点分别标注；无法判定一处复合描述还是多处的片段暂存 unresolved，记录两侧旧 ID，不造复合站位，也不强行拆分。

本版与 `photo-spot-annotation-agent-prompt-v2.md`、裁决批次中的 `verify_adjudication.py` 配套。复核通过只说明标注对该版本原文有依据，不能证明机位当前开放、安全或坐标准确。
