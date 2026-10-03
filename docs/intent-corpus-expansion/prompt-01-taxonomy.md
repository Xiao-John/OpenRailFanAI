# A：内部分类建构提示词

请作为 OpenRailFanAI Main 后端的分类设计 agent，产出内部操作分类候选。任务是设计与举证，不实施，也不移植 `referss/rail-decision-tree-bundle.tar.gz`。

先读 AGENTS.md、docs/ui-design-boundaries.md、docs/backend-parallel-development-boundaries.md 和 docs/intent-corpus-expansion/CONTRACT.md。随后可只读 docs/plan-main-intent-decision-tree.md，以及 backend/app/pipeline/{intent,schemas,extract,planner,fastpath,retrieve,orchestrator}.py、backend/app/display_result.py 和 backend/app/tools/ 中相关工具定义。不要读取语料生成批次、保留集、密钥或私有日志；不访问网络。

只写 `.ai/intent-corpus-v1/A/` 下的新文件；已有结果则另建 A-run2，不覆盖。不修改共同契约、代码、外部协议或其他 agent 产物。

完成这些工作：

1. 审查契约的暂定操作表：必要拆分、可合并项、容易重叠项、未覆盖的合理诉求。分类数由证据决定，不凑目标数量。
2. 为每个候选操作写定义、纳入/排除条件、必要实体、可选过滤、事实口径、歧义触发、映射到既有八个 Intent 的规则。区分对象类型、操作、问题性质和执行能力。
3. 建模多诉求/多对象/比较/否定/更正；允许一条输入保留多个任务。不要为适配单一 intent 丢信息，也不新增对外标签。
4. 核对实际工具能力，逐项给源码路径及函数依据，明确支持、部分支持、缺失或需验证。语法上可识别不等于已有可执行数据源。
5. 给至少 30 组自然问法边界对照，每组 2–4 条，说明唯一变化因素与标签变化。不要只列关键词。
6. 建立分类决策表：守卫、对象/任务候选、冲突和必要槽位如何共同决定 plan/defer。不得以“先命中关键词”或未经校准的分数作为消歧依据。

输出：

- `taxonomy-proposal.md`：定义表、优先关系、争议、代码依据与能力缺口。
- `taxonomy-delta.json`：保留/拆分/合并/新增建议，与 CONTRACT v1 操作的映射；每项带 reason、examples、external_intent、support_evidence。
- `boundary-cases.jsonl`：边界样本，采用 CONTRACT 的候选格式，ID=A-0001…，每条注明 synthetic。
- `summary.md`：最值得接受的变更、尚待统一的口径及产物列表。

使用 v1 标签产生边界样本；新操作建议放在 delta，不直接改其他生成批次的 schema。无法唯一映射的样本用 null 与 ambiguities。不得宣称分类已被接受或能力已实现。

结束时只给产物路径、候选操作数、边界组数和最关键的待定项。不要部署、提交 Git 或触发其他 agent。
