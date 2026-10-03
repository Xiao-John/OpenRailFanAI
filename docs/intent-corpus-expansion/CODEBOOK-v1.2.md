# 请求字段与时间口径编码表 v1.2

日期：2026-10-01。用途：开发池重标和比较。它补充 CONTRACT.md 与 TASK-PROJECTION-v1.1.md；仅在本文明确规定的编码、关系单元和案例裁决处优先。22 个 operation、八个外部 Intent 与首期保守策略继续沿用。

机器枚举及逐字段定义见 [codebook-v1.2.json](codebook-v1.2.json)，案例见 [CALIBRATION-DECISIONS-v1.2.md](CALIBRATION-DECISIONS-v1.2.md)，分发见 [prompt-09-relabel-v1.2.md](prompt-09-relabel-v1.2.md)。不得原地改动这些冻结文件；下一轮变更另起版本。

**全部字段属于意图评测资料。这里的 query_unit.time_basis 不是展示协议的 time_basis。** 现有展示协议的 reference、stations_only 和 schema_version=1 保持既有含义。本轮没有 API、客户端、后端运行代码或黄金语料变更。

## 1. requested_fields：操作内的有限枚举

含义是用户希望得到的结果字段，不是输入实体、检索工具字段或模型准备补充的信息。按 operation 的允许表选择；相同字符串在不同 operation 中仍由各自定义解释。

- 数组去重并按字符串排序。枚举以机器文件为准；不允许自行发明同义码。
- operation 明确且请求了结果时，字段不得为空。缺对象仍保留结果字段，不能以 train=null 为由删除 arrival_time。
- 只有泛指结果时使用该操作的 generic_field，例如“看时刻表”用 timetable，“有哪些经停站”用 stop_sequence。**generic_field 不是每个查询自动附加的字段**；“几点到”仅用 arrival_time，不再加 timetable。
- 多项具体结果逐项编码；泛指与具体请求确实并存时才同时保留。未能确定细分结果时保留可确认字段，并在 pending_fields 保存原话、候选和原因；不得把未知码偷偷塞进数组。
- operation 本身未决，或尚不能判断是否请求结果时，可以暂为空，必须有 pending_fields；这类样本不作为已冻结的完整语义标签。
- 日期、席别、车种、摄影目标、知识主题不是输出字段。它们留在 date、bindings 或 constraints。查卧铺余票使用 availability + constraints.seat=卧铺，不创造 sleeper_availability；同时问价格另建 ticket.fare。

主要编码如下；每个码的完整定义在机器文件中。

| operation | requested_fields 允许码 |
| --- | --- |
| train.stops | stop_sequence, stop_membership, stopover_time |
| train.timetable | timetable, train_list, departure_time, arrival_time, departure_station, arrival_station, schedule_change |
| train.duration | duration, remaining_duration |
| train.operating_status | operating_status, operating_days, operating_frequency |
| train.actual_status | actual_status, position, delay, actual_arrival_time, actual_departure_time, eta, remaining_duration |
| emu.assignment | assignment, model, emu_no, coupled_status, formation, operator_bureau |
| emu.routing | routing, assigned_trains, route_membership |
| emu.affiliation | affiliation, operator_bureau, depot |
| rail.route | route, endpoints |
| rail.mileage | mileage, segment_count, mileage_basis |
| rail.stations | station_list, station_membership |
| station.profile | profile, telegraph_code, station_grade, platform_count, floor_layout, transfer_facility, transfer_rule, distance_to_city, identity_check |
| station.screen | screen, train_list, departure_time, arrival_time, gate, platform, boarding_start_time, delay_notice |
| ticket.availability | availability, remaining_count, waitlist_status |
| ticket.fare | fare |
| photo.spot | spot_options, shooting_suitability, best_time_window, emu_types, spot_status |
| news.update | news_list, opening_status, timetable_change, announcement, future_service_plan, service_change |
| knowledge.explain | explanation, specification, naming, history, capacity, freight_types, identity_check, mileage_calculation_basis, model_type, operational_speed |
| knowledge.compare | comparison |
| transport.nonrail | route_options, departure_station, fare, availability, duration, comparison |
| journey.transfer | transfer_plan, transfer_feasibility, transfer_required, transfer_count, interchange_station, fastest_option, full_itinerary |
| general.chat | reply |

### 只允许有条件的旧码迁移

机器文件的 legacy_aliases 仅提供候选对应，每条都带 operation 和适用条件；它们不是自动重标器。迁移仍须核对输入和完整绑定。

可以确认的例子：train.stops 的 stop_list→stop_sequence；rail.mileage 的 length→mileage；photo.spot 的 photo_spots→spot_options。它们不能跨 operation 使用。

下列组合**不是等价别名**：

| 两种结果 | 必须保留的区别 |
| --- | --- |
| stop_membership / stop_sequence | 是否停某站 / 完整经停站序 |
| station_membership / station_list | 某站是否在线上 / 沿线站列表 |
| segment_count / mileage | 段数 / 距离 |
| emu_no / assignment / operator_bureau | 车组号 / 泛指担当信息 / 运营局 |
| shooting_suitability / spot_options | 某地是否适合 / 候选机位列表 |
| endpoints / route | 起终点 / 径路 |
| availability / fare | 可售状态 / 价格，属于两个 operation |
| timetable / arrival_time / departure_time | 泛指时刻表 / 到点 / 开点 |

status_update、assignment、route 等旧码如有多种解释，必须按原文重新选择；不能用经验映射掩盖判断差异。B 留空而 A 填字段也不直接算“同义码”：须确认原文确实要求该结果。

## 2. time_basis：只描述列车运行时刻或历时的口径

该维度回答“请求的是图定还是实际运行的时刻/历时”，不回答“数据从哪里拿”“查询哪一天”或“事实是否变化频繁”。可售状态、价格、配属、交路、开行状态等事实可能实时变化，仍然不属于到发时刻口径。

| 固定码 | 定义 |
| --- | --- |
| scheduled | 图定、公布或计划时刻/历时，包括图定经停站表 |
| actual | 实际运行时刻、位置、正晚点、实际历时或预计实际到达 |
| mixed | 同一完整单元明确要求上述两种口径；不能因“检索+解释”而填写 |
| unspecified | 请求涉及运行时刻/历时，但所需口径还不能确定 |
| not_applicable | 请求结果不涉及上述时刻/历时口径；不是缺数据或未知对象 |

operation 的 generic_time_basis 在机器文件中冻结：train.timetable、train.stops、train.duration 默认 scheduled；train.actual_status 固定 actual；station.screen 在未明确口径时为 unspecified；journey.transfer 泛指方案/可行性为 not_applicable，明确要求到发时刻或历时口径时另按证据填写；其余操作为 not_applicable。

“现在几点到、实际到了没、晚点没有”应按实际状态诉求判定 operation；不能套 train.timetable 的默认值。相反，“明天图定几点到”中的明天并不使 scheduled 变成 actual。用户明确请求实际历时的 train.duration 可填 actual。

| 请求 | operation / 字段 | time_basis | 其他维度 |
| --- | --- | --- | --- |
| 明天 G1 图定几点到南京南 | train.timetable / arrival_time | scheduled | date 表示明天；fact_basis=scheduled_reference |
| G1 现在晚点吗 | train.actual_status / delay | actual | fact_basis=actual_operation |
| 明天有卧铺吗 | ticket.availability / availability | not_applicable | seat=卧铺；需要事实检索，不代表 actual |
| G1 二等座多少钱 | ticket.fare / fare | not_applicable | fact_basis=retrieved_fact |
| CR400BF 跑这条线吗 | emu.routing / route_membership | not_applicable | 车型与线路仍需完整绑定 |
| 南京南到发屏现在列哪些车 | station.screen / train_list | unspecified | 不能据“现在”宣称屏上全是实际到发时刻 |
| G1 下月开不开 | train.operating_status / operating_status | not_applicable | 日期作用域独立保存 |

reference、retrieved_fact、model_knowledge、unresolved **均不是 v1.2 的 time_basis 编码**。reference 不能无条件映射为 retrieved_fact；先判断它原来想表达参考图定还是一般事实。票务的 actual 要改为 not_applicable，这是维度纠正，不是同义替换。旧 unresolved 也不能统一改为 unspecified：票务缺车次仍应 not_applicable。

fact_basis 沿用 v1 枚举，单列比较；unit_sources 描述实体/日期的输入证据，date 描述查询日。未知对象不自动使时刻口径或检索需求未知。未取到数据不修改用户原本请求的口径。

## 3. query_unit 是完整关系，不能按实体个数切分

同一单元内须同时保留：区间 origin/destination；比较 left_operand/right_operand；接续 arriving_train/departing_train/interchange_station。不能分别拆成只有一个角色的几个单元再用“角色值集合相同”宣称等价。

独立的问题才拆单元。“G10 到南京南和上海虹桥分别几点”有两份 train+station 绑定；“北京到上海”只有一份 origin+destination 绑定。共同实体要复制到每份独立单元，而不是另建“共同实体单元”。

车次在某站的到点/开点使用 train+station。只有查询区间端点或明确始发/终到角色才使用 origin/destination；“从北京南开”本身不能证明该站是全程始发站。

接续方向未知时，保留一份未决的关系节点：

```json
{
  "bindings": {
    "arriving_train": null,
    "departing_train": null,
    "interchange_station": "南京南站"
  },
  "date": {"state": "omitted", "raw": null, "resolved": null},
  "requested_fields": ["transfer_feasibility"],
  "constraints": {"unassigned_trains": ["G1", "G2"]},
  "time_basis": "not_applicable"
}
```

constraints.unassigned_trains 是本轮固定的未分配角色证据键，值按出现顺序保留，但**顺序不意味着到达/出发角色**。其 unit_sources 须分别引用两个车次。expect.status=ambiguous、projection.status=unresolved；方向有可靠证据后才能分配。这不是两个独立换乘请求。

行程序列的每段 origin+destination 也在同一单元，段间顺序另记录。不同请求若引用同一三段行程，要保留引用或完整上下文，不能投影为第一段。未确认指代范围时保留候选范围，不强行展开全组合。

## 4. 日期与质量门槛

日期必须按每条输入自己的 clock.now 和 timezone 解析；不能使用运行当天或全局示例时钟。当前“明天”覆盖历史“今天”，同时可以从同一话题继承站点。date.raw 保留具体证据片段，resolved 只填可确定的日期。

明确月区间不是单个日期：单日 resolved=null，并记录 window_raw；可确认的范围另附 resolved_range（start/end，闭区间，ISO 日期）。“下个月”的范围按样本时钟确定；不能擅定月初出发或七天后的准确日期。宽泛“最近”的边界继续 pending，不伪装成已冻结的范围。

对比前先执行以下硬检查：operation/字段枚举、非空字段要求、time_basis 枚举、角色完整性、对象日期绑定、来源引用、clock 日期、ID 覆盖及 JSON 结构。违反枚举或关系单元规则记为 contract_violation，不当成一般字符串差异，也不删除样本改善分数。校验失败时报告覆盖和违规，不能声称完成可比的 L2 评估。

v1.1 的 0.179 和 0.214 仅保存为历史表示一致率，**不用于系统正确率、A/B 谁更准确或决策树质量的结论**。冻结重标后仍报告标注一致率，而非系统准确率；系统准确率需要裁决后的独立评测。

新的比较分开报告：任务组；完整绑定与日期；requested_fields；time_basis；constraints；整体单元；投影；策略。constraints、分类和证据规范尚有未决项，必须公布不可比项与覆盖分母，不能靠删除实质限制或将角色展平成集合来生成“净化后的质量分”。尤其不能把 operation 一致率当作完整任务保留率。

先修复两侧，再重新比较全量 300 条。B 的 50 条拆分案例是优先修复队列，A 也要全量检查；禁止直接选择 A 为黄金答案。不得读取 H 输入或根据开发池重标后的升分宣称泛化提升。
