# v1.1：任务分组与投影重新标注

请在全新上下文中按统一粒度重标已指定的开发池输入，重点解决任务集合、对象角色、日期绑定与兼容投影。你只标注，不修改代码或裁定整个分类提案。

先读 AGENTS.md、docs/ui-design-boundaries.md、docs/backend-parallel-development-boundaries.md、docs/intent-corpus-expansion/CONTRACT.md 和 TASK-PROJECTION-v1.1.md；两份标注约定冲突时以 v1.1 的任务/投影定义为准，其余未决事项仍留待裁决。随后只读分发者指定的 S/M/C inputs.jsonl，不读原标签、R 报告、实现或 H 保留集。不联网。

分发者须指定输出批次，例如 L11-A 或 L11-B；只写 `.ai/intent-corpus-v1/<输出批次>/` 的新文件，已有目录则另建，不覆盖任何已有产物。两位标注者使用不同上下文，不互看结果。

按每条输入完成：

1. 识别所有明确操作，区分背景/偏好与独立诉求。
2. 相同 operation+relation 归一个 task；不同对象/地点/日期进 query_units，绑定角色与来源。
3. 保留明确对象日期对应、比较/接续/顺序及 depends_on；不得做未授权笛卡尔积，不派生用户没要求的知识解释或工具子任务。
4. 再计算投影：共同 Intent 保留，跨 Intent 为 null；性质按规则汇总；单值槽位只保留公共且同角色的可靠值，多值为 null。
5. 不以单一 target/extra 藏多任务，不拿 clock 或助手正文填缺失对象；清楚但不能执行的请求与真正歧义分开。

输出 `labels-v1.1.jsonl`，每行至少包含：id、contract_version=1.1、expect（保留 v1 主字段）、rationale、pending_fields。expect.tasks 使用 operation/intent/question_type/relation/query_units；每单元包含 bindings、date（state/raw/resolved）、requested_fields、constraints、time_basis；附 unit_sources 逐字段保存原文证据。不要再把 objects 无角色列表作为任务真值。

未定 requested_fields/constraints 编码及真正分类争议在 pending_fields 列明，不自创一堆同义键然后当成已统一。比较/接续中的角色顺序不确定时记录歧义，不强制选方向。原 projection 的五槽位、slot_sources、time、status 与策略字段仍输出，凡涉及其他开放政策写 undetermined/policy_undetermined 或具体 pending_fields，不用任务重分组代替裁定执行能力。

另输出 `summary.md`：输入哈希、条数、任务组数/单元数、歧义数、未决编码/分类/政策清单。检查 ID 覆盖、JSON、槽位五键、来源索引与引用、日期合法性；不得运行现有分类器筛选标签。

校准与盲标分开：若分发者给了带预期的校准样本，它们不计作盲标样本。已有开发池病例可用于统一解释，但不能报告为新的泛化证据。结束只给文件路径、覆盖条数和未决事项。
