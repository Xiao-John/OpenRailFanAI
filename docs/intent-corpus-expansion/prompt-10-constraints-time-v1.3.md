# v1.3：限制条件和时间状态补标

先读 AGENTS.md、docs/ui-design-boundaries.md、docs/backend-parallel-development-boundaries.md；随后读 CONTRACT.md、TASK-PROJECTION-v1.1.md、CODEBOOK-v1.2.md、codebook-v1.2.json、CONSTRAINTS-TIME-v1.3.md 和 constraints-time-v1.3.json。仅 v1.3 明确处理的键、时间、对应案例条款覆盖旧约定；其余争议保持 pending。本任务不授权自动联系其他 agent。

分发者指定标注者或比较者，并给一个未使用的新输出目录（例如 L13-A/L13-B/L13-C；已有则加运行后缀）。只写自己的新目录；不改源码、测试、前端、Android、LM、验收、共同契约、旧标签/报告和黄金集。不读 H/.env/密钥/私有日志，不联网、不调用分类器或铁路接口。

## 标注者

读取指定的全部 300 条 S/M/C inputs.jsonl。可以读取分发者指定的一份本侧 L12 标签用于保留旧版本及迁移记录，不能读对方标签、比较报告或用旧标签替代原文判断。沿用原 18 条开发校准分层；本轮是补标，不称为新的盲标或泛化评测。

逐条重新核对：

1. 每个条件对哪个完整 query_unit 生效；否定、偏好、选择逻辑和分别求结果都不能丢。
2. constraints 只用机器表九种受控键及规定类型、值。时间移 date；实体移 bindings；自由 topic/criterion 按主题/择优维度编码。未知项保留原话、候选、来源及 pending，不新增键/other。
3. 迁出 constraints 的人物、概念、区段、引用和行程证据仍然保留。不能删掉它们制造完整单元一致；结构尚未冻结时标明整体不可比维度。
4. date.state 表来源与真正的解释未决；kind/precision 表时间形态/精度。“最近/以后”明确但宽泛时 explicit+vague；“现在”按谓词分 instant/current_period，不能统一写今天或 omitted。
5. 日期使用本条 clock；保留历史来源、时间片段来源、具体钟点、时段条件和对象对应。invalid/ambiguous 不得经默认值清空；多个确定日期不改 ambiguous。
6. 单位字段补齐后重新计算投影和语义状态，但不擅自裁定未冻结操作、行程段序、身份确认和动作恢复政策。

输出 labels-v1.3.jsonl，contract_version="1.3"、codebook_version="1.2"、constraints_time_version="1.3"，记录两份机器表 SHA-256。附 migration.jsonl（id、字段路径、旧值、新值、原文证据、representation_migration/judgment_correction/unresolved）。缺对象的 query_unit 不删除，操作未决的编码问题也不靠空值回避。

另交 summary.md 与 validation.json：全量 ID/JSON 校验、九键/值/类型、时间字段互斥、enum_filter 合法性、时间来源/作用域、迁出信息保留、未决项和覆盖。输出时间对象固定十个键；time_filters/条件枚举集合排序，方向、证据、关系与有意义段序不能排序抹平。

## 比较者

分发者指定两份 v1.3 标签后，核对输入及两份编码表哈希，再独立校验。原始证据文本不做字符串准确率；受控 topic/criterion 和真实实体/关系不能省略。时间先比 state/kind/precision，再比查询日、范围、时点、时段条件与来源/作用域，不能只比较 resolved 是否相同。

输出 comparison.md、agreement.json、contract_violations.jsonl、open-questions.md。按原 18 条校准/282 条其余开发记录分层；报告全量分母与可比覆盖，禁止删除违规/不可比记录升分。行程引用等未冻结维度单列，constraints 迁空不是完整语义等价。裁决建议另存，不覆盖任一标签，不冻结黄金集，不声称系统性能提高。
