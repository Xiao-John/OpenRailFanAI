# v1.4：行程引用与非法动作日期收敛

分发者指定角色和未使用的输出目录，例如 L14-A/L14-B/L14-C；存在则另加后缀。本提示词不授权自动联系其他 agent。先读 AGENTS.md、docs/ui-design-boundaries.md、docs/backend-parallel-development-boundaries.md；随后读本目录 CONTRACT.md、TASK-PROJECTION-v1.1.md、CODEBOOK-v1.2.md/codebook-v1.2.json、CONSTRAINTS-TIME-v1.3.md/constraints-time-v1.3.json、DATE-DIRECTION-v1.3.1.md/date-direction-v1.3.1.json、ITINERARY-ACTION-DATE-v1.4.md/itinerary-action-date-v1.4.json。

本文处理的结构和非法动作日期条款以 v1.4 优先；不是将所有未决项清空。只写自己的新目录，不改旧标签/报告/契约/分发记录、backend、原冻结语料、前端、Android、LM、验收及配置。不读 H/.env/密钥/私有日志，不联网、不调用现有分类器决定语义。

## 标注者

分发者指定本侧最新标签（建议 A=L13-A-final、B=L13-B-final3）与 S/M/C 原输入。可以读取自己的旧产物，不能读取对方标签；这是开发补标，不是新的盲标或泛化评测。

全量检查 300 条，只修改新规则明确涉及的项：

1. 多段行程使用 expect.itineraries；段序从 0 开始、ID 可追溯。query_unit.itinerary_ref 四键完整，未知范围保存候选。不要把任务出现序号当行程段序。
2. 各段班次/经停/票价/余票请求形成完整绑定单元；分别求结果不能压成一个 any_of/all_legs。查经停服务未选时 train=null，不猜第一班车。
3. C-0096 按机器表六项操作/十四单元及段日期裁决；下游三组 relation=independent，段序在 itinerary。机位和车型线路范围仍未知，date=omitted，行程背景通过引用保留。
4. C-0008 单元和 expect.time 按非法动作单日字段裁决，raw 从 /date 精确取 not_a_date，补动作来源。precision=unresolved 不得被旧通用 normal_form 自动改回 day；不能清成 omitted 或猜今天。
5. 仅移除已经由本文件解决的“结构规则未冻结”类 pending；实际指代、服务、日期、卧铺含义以及动作恢复政策仍保留相应语义/策略疑点。字段映射完成不代表能确定性执行。
6. 重新计算涉及项投影，不扩大到无关样本。解释每处跨版本变化：representation_migration、judgment_correction、unresolved；B 单元展开必须记录原单元到新段单位的对应，不假称零漂移。

输出 labels-v1.4.jsonl，contract_version="1.4"，codebook_version="1.2"、constraints_time_version="1.3"、direction_rules_version="1.3.1"、itinerary_action_date_version="1.4"，记录四份机器表 SHA-256。新数据中 expect.itineraries 无行程时为 []、query_unit.itinerary_ref 无引用时为 null，其他主字段与来源继续保留。

另输出 migration.jsonl、summary.md、validation.json：输入哈希、ID 覆盖、行程/段数与引用、操作单位数、日期作用域/十键/硬约束、非法动作证据、九键和值不变、变更范围及仍未决项。不能沿用上轮 340 对或 Δ=0；结构展开后重算数量/配对。

## 比较者

分发者指定两侧新标签后，核对 300 ID 和全部输入/规则哈希。独立验证引用及来源、C-0096 的 6/14 结构、每段日期、两个 unresolved 范围节点和 C-0008 日期对象；不能以单元数终于相同替代完整绑定核验。

按 operation/relation、itinerary/leg、角色/范围及实质条件对齐；保留行程顺序、候选集合及日期配对，不按任务/单位旧下标强制认作相同。报告 structural_match、query_units、constraints、date、projection、policy、未决语义与覆盖；非法动作日期新条件规则优先于旧通用 precision 模板。

产物为 comparison.md、agreement.json、contract_violations.jsonl、open-questions.md 和 adjudication-proposal.jsonl。18 校准/282 其余分层，违规/未决记录留在全量分母。不改原比较报告，不冻结黄金集，不宣称系统准确率或运行性能提高。只有实际完成校验后才能说规则依赖已解除；其他真正 ambiguity 继续保留。
