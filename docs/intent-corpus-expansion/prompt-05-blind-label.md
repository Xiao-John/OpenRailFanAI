# B：独立标注提示词

你是独立语义标注 agent，必须使用没有生成者历史的新上下文。任务是重新标注指定 inputs.jsonl，不是复述生成者答案。

先读 AGENTS.md、docs/ui-design-boundaries.md、docs/backend-parallel-development-boundaries.md 和 docs/intent-corpus-expansion/CONTRACT.md。随后只读取分发者明确指定的 inputs.jsonl；默认是 S/M/C 批次的输入文件。不要读 candidates、manifest、summary、其他标签、实现、测试、旧语料、taxonomy 提案或保留集。若你已看过这些候选的标签，应说明不满足盲标条件，请分发者使用另一份新上下文，不能声称盲标。

只写 `.ai/intent-corpus-v1/B/`，已有结果另建 B-run2；不改原文件，不访问网络。

对每条输入独立产出：

```json
{"id":"S-0001","expect":{},"rationale":"独立判断依据","label_status":"candidate","contract_version":"v1"}
```

expect 必须完整使用 CONTRACT 定义的结构，包括所有 tasks、projection、slot_sources、time、eligibility、reason、missing_slots、ambiguities、must_not、clarification。不得以空 expect 完成交付；上例只示意外层结构。

按中文语义和共同契约标注：语义状态与执行政策分开；未知数据源能力不凭空认定支持；多任务/对象不省略；本次值优先，历史来源明确；助手正文不可充当事实来源；不将知识问题固定分派到实时铁路工具。没有唯一答案时给部分确定信息、候选解释和具体疑问。

产出 `labels.jsonl` 和 `summary.md`。检查每个输入 ID 恰好一个标签，无遗漏/多余/重复，JSON 和来源索引合法。不要执行当前分类器，也不要要求看生成者标签来“纠正自己”。summary 写输入文件哈希、独立标注条数、模糊项及契约中难以一致执行的定义。

结果交给裁决 agent 比较；本任务不修改标签标准、裁决最终正确性或冻结黄金集。结束只报告文件路径、条数与疑难数量。
