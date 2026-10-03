# S：单轮业务语料提示词

请生成 OpenRailFanAI 新决策树的独立单轮语义候选。你负责批次 S，目标 100 条；语义质量优先，不能为凑数重复模板。

先读 AGENTS.md、docs/ui-design-boundaries.md、docs/backend-parallel-development-boundaries.md、docs/intent-corpus-expansion/CONTRACT.md 和本提示词。除此之外不要读代码、旧 SPEC、已有语料、测试、模型输出、其他批次或旧决策树；不要联网。语义标签必须根据契约和中文语义，不根据当前实现会不会通过。

只写 `.ai/intent-corpus-v1/S/`；已有结果时另建 S-run2。不得修改任何原文件或冻结语料。

覆盖主诉求明确的自然问法：

- 经停/站序、图定时刻和历时，各自区别。
- 车次查担当、车型/车组查交路、配属，各自对象区别。
- 线路沿线站点、铁路径路、里程、单站档案、大屏。
- 余票与票价，结合席别、车种、日期、宽时段等原文偏好。
- 拍车、资讯、车型知识及比较，以及少量无法归类的真实合理问法。

100 条中建议至少 20 条知识/资讯/实际运行口径对照；至少 15 组最小对照（每组 2–4 条，计入总数）。不得强制所有类别拥有相同条数，也不规定 eligible 比例。普通样本 history=[]、display_action=null；本批不专门生成多轮。

使用 CONTRACT 的完整候选格式，ID=S-0001…；保留日期原话与规范日期、全部关键槽位及 slot_sources。不要把没提供的对象改成某个具体站，不验证或编造时刻、价格、席位等事实答案。疑难项标 ambiguous/undetermined 并写具体原因。

至少半数样本有不同句式或任务骨架，不能只替换 G1/G2 和北京/上海。family_id 标同骨架，contrast_group 标最小对照。语料的来源全部是 synthetic。

输出 `candidates.jsonl`、去除所有标签/笔记/分组信息的 `inputs.jsonl`（只保留 id/message/history/display_action/clock）、`manifest.json` 和 `summary.md`。检查 JSON 可解析、ID 唯一、五个槽位键齐全、非空槽位都有输入证据；不得运行当前分类器来“筛选正确样本”。manifest 记录 SHA-256、条数、family/contrast 组数、operation/eligibility/status 分布与疑难数。

结束只报告文件路径、条数、组数和未达到的覆盖目标。产物始终是待复核候选，不自动进入黄金集。
