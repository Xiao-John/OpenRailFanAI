# 限制条件与时间状态补充规则 v1.3

日期：2026-10-01。针对 L12-C 的 Q1、Q2、Q3，并明确 criterion 直接涉及的 Q5，补充 v1/v1.1/v1.2；这些明确条款冲突以本文为准。requested_fields、time_basis 和操作表仍使用冻结的 v1.2，不另造结果码。本文件和 [机器表](constraints-time-v1.3.json) 只作用于评测资料，不修改运行 API、展示协议或客户端。

依据：L12-A/L12-B 的实际标签、L12-C/open-questions.md 及 S/M/C 原始输入。未读 H。旧标签和报告保持原样；本轮不裁定其余全部分类与行程结构问题，也不生成黄金集。

## 1. 冻结键、类型、值和作用域

**需要冻结受控键表，但不能把出现过的 25 种键全部认作筛选条件。** 字段仍放在各 query_unit，禁止把只适用一项请求的条件提升到整个 task。未知键/值保留原文到 pending_fields，申请下一版本，不能塞入 other 自由字符串。

| 受控键 | 类型 | 含义 |
| --- | --- | --- |
| seat | enum_filter | 席别；不包含价格或余票结果 |
| service_type | enum_filter | 明确要求的高铁、城际、动卧、临客等服务类型 |
| station_type | enum_filter | 明确要求的车站类型，如编组站 |
| transport_mode | enum_filter | 非铁路或综合交通问法中的交通方式筛选 |
| direct_only | boolean_filter | 是否要求直达；“直达”不作为车种 |
| topic | enum[] | 结果字段还未覆盖的主题维度；不填实体名或整句改写 |
| criterion | enum[] | 比较/择优维度；不宣称已有评分或结果 |
| service_ordinal | object | index 为从 1 开始的正整数；order_by=departure_time/arrival_time/unspecified |
| unassigned_trains | ordered string[] | v1.2 已冻结的接续方向未分配证据；不是可售/车种筛选 |

enum_filter 固定为 `{any_of:[码], none_of:[码], strength:"required"或"preferred"}`；两个数组都输出，至少一个非空，不能重叠。any_of 是可选类别的并集，none_of 是排除；不把“不要硬卧”改成要求硬卧。boolean_filter 为 `{value:true或false, strength:...}`。“最好直达”用 preferred，不冒充硬条件。

枚举集合去重排序；unassigned_trains 保留提及顺序但不据此分配角色。不同必需条件同时作用，同键选项不隐含必须全部满足。明确要求分别查询两种席别价格或各自可售状态，按 v1.1 分成不同条件单元，不能用一份 any_of 掩盖结果对应。复杂逻辑未能完整表达时保留 pending，不简化成无依据的 AND/OR。

### 受控值的起始范围

- seat：first_class、second_class、business、hard_seat、soft_seat、hard_sleeper、soft_sleeper、sleeper、no_seat。
- service_type：high_speed、intercity、sleeper_emu、temporary_service。
- station_type：marshalling、passenger、freight。
- transport_mode：air、metro、bus、coach、taxi、rail、high_speed_rail。
- criterion：min_duration、min_cost、value_for_money、comfort、technical_advancement、high_train_frequency、convenience。
- topic：机器表给出有限的主题维度及定义，例如 person_relation、crew_handover、switching_mechanism、maintenance_levels、train_numbering、wireless_connectivity、manufacture_origin。它不是完整铁路知识分类。

“卧铺”保持 sleeper，不擅自选硬卧或展开成几个具体席别；“高铁”与“直达”分开；“划算”是 value_for_money，不自动等于最低票价；“最顺”是 convenience，不自动触发 fastest_option。“机票”只有在明确航空票务语境下才支持 air，不能把票种名直接当交通方式码。

topic 仅补充 requested_fields 没表达的维度。例如已经请求 history/naming/specification，不能再以 topic 自由重复“历史/命名/参数”。“绿皮车”等概念留在 bindings.concept；“詹天佑”留在 bindings.related_person；地区/区段留在适用 bindings 中。实体名保持输入证据支持的规范值，不要求建立封闭的人名/概念总表。

S-0052“京张铁路的历史和詹天佑有什么关系”：保留线路、related_person=詹天佑、requested_fields=history、topic=[person_relation]。两侧的“与詹天佑的关系”和“詹天佑”不能作为同一自由文本标签直接比较，也不能删除这个人物关系诉求。

S-0090“哪个更省时间”：criterion=[min_duration]。没有明确要求分别报耗时，不因比较过程可能查耗时自动增加 requested_fields=duration。“哪个更舒服”用 criterion=[comfort]，不在 topic 再抄一遍；车次/车型/交通方式比较对象仍保持成对绑定。

### 25 个旧键如何处置

| 旧键 | v1.3 位置/处理 |
| --- | --- |
| seat、station_type、criterion、service_ordinal、unassigned_trains | 原文复核后迁移到对应受控键和类型 |
| train_type | service_type；“直达”改 direct_only；不能机械搬值 |
| mode | transport_mode；比较两种方式时仍须保留左右对象 |
| topic | 结果码已表达的内容去除重复；实体移 bindings；剩余维度按有限 topic 编码 |
| topic_scope、section、from、direction_from | 按语义移到适用的实体/区段/起点角色，不留在 constraints |
| window、part_of_day、service_scope、time_window、after_time、time_hint、time | 移 date.window_raw/time_filters 或时间主字段；先核对究竟是日期、时段、相对时刻还是排序诉求 |
| reference、reference_scope、candidate_stations、candidate_legs、itinerary_scope、line_scope | 保留未决绑定/引用证据及候选到 pending_fields/原始迁移记录；按后续角色/行程结构裁决，不冒充筛选条件 |

最后一组的结构和 C-0096 段序问题尚未全部冻结。迁出 constraints **不意味着从完整语义评估中删除**；含未决引用关系的单元仍须标为整体不可比/待裁决，并保留全量分母。机器表逐个列出全部 25 个键的处置，但不提供无证据的自动转码器。

## 2. date.state 与时间精度分开

不新增 vague 状态。state 沿用 explicit/inherited/omitted/invalid/ambiguous，并增加独立 kind、precision；resolved=null 本身既不等于 omitted，也不等于 ambiguous。

| state | 固定判据 |
| --- | --- |
| explicit | 当前输入或合法动作明确给出适用于本单元的时间表达；含宽泛表达 |
| inherited | 当前没有新的适用时间表达，从可靠用户历史继承，锚点唯一 |
| omitted | 无当前表达，也没有可安全继承的时间；执行默认值不算用户输入 |
| invalid | 输入时间在已有明确解释下不合法，不能悄悄丢弃或修成默认日期 |
| ambiguous | 时间解释、锚点、对象对应或作用域有多个实质候选，影响所问结果 |

invalid/ambiguous 优先于来源状态；来源继续在 unit_sources 逐字段保留。单元的当前时间片段与继承日期可以同时存在，例如“11月6日”的上下文后问“下午”：state=explicit，但 resolved 的证据来自用户历史，time_filters 的证据来自当前。不能因为 state=explicit 就谎称全部时间字段来自当前。

precision 取 none/day/range/instant/clock_time/vague/unresolved；它描述主要时间作用域的精度，时段条件另外保留。kind 与精度、字段组合见机器表。

| 原话与语境 | state / kind / precision | 规范化结果 |
| --- | --- | --- |
| 最近跑过哪些车次、最近有什么公告 | explicit / relative_period / vague | direction=past，window_raw 保留，resolved/range=null；不默认最近 7 天 |
| 最近准备去旅行 | explicit / relative_period / vague | direction=future；不能只见“最近”就写过去 |
| 以后还会开行吗 | explicit / relative_period / vague | direction=future，终点未定，不编造单日或封闭范围 |
| G1 现在晚点吗、车组现在配属哪局 | explicit / instant / instant | resolved_instant=样本 clock.now；resolved=null，不推定车次服务日 |
| 现在这条线跑哪些车、现在还实行这个标准吗 | explicit / current_period / vague | direction=current；请求现行有效信息，不当成此刻所有在线列车 |
| 下个月 | explicit / calendar_range / range | 单日 null；可唯一确定的月范围填 resolved_range |
| 11月5日下午 | explicit / calendar_day / day | resolved=2026-11-05；下午作为独立时段条件，不擅定钟点边界 |
| 三点那趟 | ambiguous / time_of_day / unresolved | 上午/下午、对象等尚未唯一确定；保留候选，不能默认 15:00 |
| 国庆以后，未明确国庆日还是假期结束 | ambiguous / relative_period / unresolved | 保留两类锚点候选，不猜具体假期日期 |
| 历史“明天”可能跨日且无可靠锚点 | ambiguous / calendar_day / unresolved | 不拿当前时钟重新解析成已确认的历史日期 |

**宽泛但含义明确，采用 explicit+vague；存在竞争解释，才采用 ambiguous。** 需要检索工具的精确边界却没有边界时，可以另外记缺失精度、澄清或 defer；不因此改写时间表达来源。S-0085 的“最近”本身不是 ambiguous 的理由，但“我常坐那趟”未定，整条 expect.status 仍为 ambiguous。

“现在”必须按谓词判断是瞬时快照还是现阶段有效信息。C-0029 询问当前配属，用 instant；S-0083 询问当前开行服务，用 current_period。“现在”不属于遗漏日期，也不自动等于单日车次查询。“实际/图定”的判断仍由 operation/time_basis 完成，不由现在或最近这个词单独决定。

### 固定字段与时段位置

date 固定输出 state、kind、precision、direction、raw、resolved、resolved_range、resolved_instant、window_raw、time_filters；不适用字段为 null，time_filters 为数组。

- resolved 只表示唯一的查询日；resolved_range 表示可确认的闭合日范围（start/end）；resolved_instant 是带时区的观察/查询时点。这三者不能互相冒充。
- window_raw 保留宽泛窗口或时段原话；不得再复制到 constraints.window/time 等键。
- time_filters 保存具体时段条件，每项为 event/op/value/raw。event=departure/arrival/boarding/observation/unspecified；op=eq/gt/gte/lt/lte/part_of_day；钟点用 HH:MM，part_of_day 使用受控时段码。只在原文支持时分配事件角色，不凭操作泛名猜到发方向。
- “15点40分之后”用 gt/15:40；“不早于15点40分”用 gte/15:40；“凌晨0:30”保留 eq/00:30，不能简化成整段凌晨。不明钟点 value=null 并保留 pending，不猜上午/下午。
- morning/afternoon/evening/early_morning/night 仅表示词义类别，不在本轮制定数值起止边界。“早班”未必等同于一个固定上午区间，保留原话和未决解释。

投影 time 可以保留“最近/以后/现在”等原话，只要其作用域适用于全部相关单元；不要求先得到唯一日。S-0085 两项的“最近”作用域尚未确认共同成立，不能强行提升给第二项或整体投影。未知车次也不抹去已解析日期。

expect.time 沿用旧主字段，采用同一 state 判据；resolved_date 只有能代表整体唯一查询日才填写，不能从 instant 裁切出“今天”代替它。不同单元的日期/作用域须各自保留，不以整体字段覆盖。

## 3. 下一轮校验和比较

使用 [v1.3 补标提示词](prompt-10-constraints-time-v1.3.md)。两侧全量复核，逐项留下原始值、规范值、证据和变更类型；不是根据对方标签修改自己的答案。

先验证受控键、类型、枚举、作用域、否定/偏好、时间字段互斥与状态判据，再报告 constraints 和 date 各子层一致率。真实过滤缺失、人物/概念丢失、范围未决不得归成“编码噪声”。未冻结的行程/分类仍显式 pending，完整单元不可比覆盖单列；不可比和违规记录仍保留在全量分母。

这一轮仍是开发池标注收敛，不是系统准确率或决策树性能测试。只改变约定/标签表示不能宣称运行能力提升。
