# 行程引用、段序与非法动作日期 v1.4

日期：2026-10-02。用途：解除 DISPATCH-NOTES.md §7.5 的行程结构与非法动作日期映射规则依赖。依据 C-0096/C-0008 原输入、L13-A-final、L13-B-final3 和既有冻结条款。未读取 H。

本文件与 [机器表](itinerary-action-date-v1.4.json) 优先于旧文件中的对应未决说明；只冻结本文明确列出的字段。v1.2 结果码/口径、v1.3 九键约束和 v1.3.1 方向硬表不变。旧冻结文件、标签、报告、分发记录原样保留。**这是评测契约，不是新增聊天请求、展示协议或执行能力。**

## 1. 行程、查询任务、工具步骤分开

多段行程保存在 expect.itineraries。行程是用户的城市/地点序列，不是自动新增的 journey.transfer 任务，也不是已验证的铁路线或直达列车方案。

| 字段 | 冻结结构 |
| --- | --- |
| expect.itineraries | 数组；没有明确多段行程时为 [] |
| itinerary.id | 按该输入首次提及顺序编号 I1、I2…，在记录内唯一 |
| itinerary.legs | 有序数组；每段包含 leg_id、leg_index、origin、destination、date |
| leg_id / leg_index | I1:L0 / 0 开始的连续整数；段 ID 在记录内唯一 |
| itinerary.sequence_order | leg_id 数组，恰好覆盖 legs，顺序与原文相同 |
| itinerary.stays | 停留数组；项包含 after_leg、before_leg、location、amount、unit、approximate、quote |
| query_unit.itinerary_ref | 非行程单位为 null；否则含 itinerary_id、scope、leg_ids、candidate_leg_sets 四键 |

stays 的 amount 为正数，unit=day/week，approximate 为布尔值；after_leg/before_leg 必须引用相邻且有效的段。它记录原文停留要求，不是允许直接把到达时间加上精确天数的执行指令。未知端点为 null，并保存来源/疑点；不得把城市自动替换成铁路车站。

itinerary_ref.scope：

- leg：leg_ids 恰一个有效段，candidate_leg_sets=[]；单位绑定与这段一致。
- all_legs：leg_ids 为该行程完整的 sequence_order，candidate_leg_sets=[]；仅用于用户明确要求整体结果的复合请求，不代替分别查每段。
- unresolved：leg_ids=[]，candidate_leg_sets 保留可证的候选段集合；每个候选内按行程顺序排列，不能选第一个当作已解析。

源证据仍通过 unit_sources 和原有证据资料保存。新结构中的来源需指到具体字段/段，不能仅引用整句就假称全部角色已确认。引用本身不等于实体已选择：查经停的 train 可以仍为 null；明确城市区间不代表存在某条直达服务。

## 2. 分组、段序与单位展开

先识别用户要求的结果，再绑定行程。**对明确覆盖多段的班次、经停、票价、余票请求，每段产生一个完整 query_unit**，共同约束复制到对应单位；不得把所有段压成无角色列表，也不产生段间笛卡尔积。未知服务仍保留该段、train=null 和未选择服务说明。

只有原文明示整体总值/整体方案等复合结果时才用 all_legs 单位；不能用这个例外把“各段分别多少”并成全程一个价格。范围本身未确认时使用一个 unresolved 引用节点，不自动展开所有候选。

主行程的班次/到达时间请求可用 train.timetable + relation=sequence；经停/票价/余票等各段独立结果用 independent，段间顺序由 itinerary 保存。relation 是用户结果关系，不是“这项任务出现在第几句”。不同 operation 仍分别建 task。

任务级 sequence_order 不再用作段序真值：A 的整数任务序号和 B 的 1-based 段序数组都移到上述统一行程结构。query_unit 中旧 leg_index 可迁入引用所指段，完成后移除冗余副本；不能保留相互矛盾的双份段序。明确的比较/接续关系仍遵循 v1.1，不因行程序列丢失左右/到达出发角色。

depends_on 只表示实际语义依赖，不能根据任务列表次序制造依赖，也不能把工具查询步骤新增为用户任务。选择具体服务前，不允许将候选服务的第一项写成已确认 train。缺少服务或实际数据可保持未决/defer，不能伪造实体值来获得完整计划。

## 3. 时间背景不自动成为每项查询的日期

每段 date 仍为 v1.3 的十键对象；单位的查询日期按它实际绑定的段或明确时间要求填写。**行程先后、停留关系、出发窗口、具体运行日是不同信息。**

- “下个月从 A 去 B”：该出发段的窗口可解析为下一自然月；不自动推定全程都在该月结束。
- “到 B 大概玩一周，然后去 C”：停留存 stays，后一段的时间为事件锚定的 relative_period/vague/future；保留约一周原话，不按 clock+7 天生成日期。锚点是上一段到达，而不是请求时钟。
- 只说“再回 A”：保留段序；没有独立日期表达时 date=omitted/none/none，不能因顺序明确编造日期。
- 段内的经停、票价、余票诉求绑定该段日期条件；同一次输入中的证据仍为 current，不因为字段间引用就伪装成 history_user。
- 泛指“线路上有哪些机位”“某车型是否跑这条线”，如果没有拍摄/运行日期要求，date=omitted。旅行日期背景通过 itinerary_ref 保留，不当作检索日期。明确说“下个月还能去这个机位吗”或“这次行程当天担当什么”则按实际时间要求绑定；不是 photo/emu 操作永远不带日期。

日期/单位的 temporal anchor 候选和 scope 候选有多个解释时仍记录 ambiguity。字段结构冻结不使原文缺失的信息自动唯一。整体 expect.time 只填写能代表全部任务的时间；不能从第一段或旅行背景提升一个日期到所有任务。

## 4. C-0096 的结构与日期裁决

仍保留六项明确诉求，不新增 journey.transfer，不把 emu.routing 改成 emu.assignment。

I1 的段序固定为：L0 北京→拉萨；L1 拉萨→成都；L2 成都→北京。城市粒度不改。约一周停留位于 L0 到达后、L1 出发前，location=拉萨、amount=1、unit=week、approximate=true。

| 段 | date 判定 | 证据/限制 |
| --- | --- | --- |
| I1:L0 | explicit/calendar_range/range，2026-11-01…2026-11-30 | raw/window_raw=下个月；使用输入 clock=2026-10-01，不能用编写本文件当天 |
| I1:L1 | explicit/relative_period/vague，direction=future，所有 resolved 字段 null | raw/window_raw=大概玩一周；事件锚点为 L0 到达，通过 stays 连接，不定具体出发日 |
| I1:L2 | omitted/none/none，所有日期结果 null | 只确认后续返回顺序，不确认日期 |

查询结构如下，合计 **6 tasks / 14 query_units**：

| operation / relation | 单位数 | 完整绑定及字段 |
| --- | --- | --- |
| train.timetable / sequence | 3 | 每段 origin+destination，train=null 表示待找服务；arrival_time、train_list |
| train.stops / independent | 3 | 每段 origin+destination、train=null；stop_sequence；不得猜服务 |
| ticket.fare / independent | 3 | 每段 origin+destination、train=null；fare |
| ticket.availability / independent | 3 | 每段 origin+destination、train=null；availability；seat=sleeper 的受控筛选不变 |
| photo.spot / independent | 1 | line=null；spot_options；unresolved 行程引用 |
| emu.routing / independent | 1 | model=CR400BF、line=null；route_membership；同样的 unresolved 引用 |

最后两项的候选范围沿用既有校准说明：`[I1:L0]` 与 `[I1:L0,I1:L1,I1:L2]`。保留“这条线路/这条线”的原文来源，**不得自动选第一段或整个行程**。两项 date 都为 omitted/none/none；旅行出发窗口仍能从 I1 引用追溯。该裁决解除旧 [4,0]/[5,0] 日期表示依赖，不宣称线路范围已澄清。

两侧都须修正：A 需要迁移段序、把次要结果关系改为 independent、停止给所有段复制十一月；B 需要按明确各段展开三个下游请求，并去除机位/车型查询自动继承的十一月。不能直接宣布 A 或 B 整份标签正确。

expect.status=ambiguous，projection.status=unresolved、intent=null、question_type=realtime，五槽位均 null，首期 defer。线路指代、实际服务选择、后续具体日期和卧铺问法的剩余语义疑点仍保留。timetable/stops 的 time_basis 为 scheduled，其他四项为 not_applicable；票务价格和可售状态仍分任务。

整体 expect.time 采用 state=explicit（输入确有明确时间条件），但 raw/resolved_date/window_raw 均为 null，因为没有共同适用于全部请求的唯一时间短语；各段原话和解析结果留在行程及单位中。不能把这个摘要的 explicit 解读为所有任务都在十一月。

## 5. 非法动作日期值的映射

仅适用于**已确认非法**且来源是已声明单日日期的动作字段，例如合法识别的动作类型下 `/date`。动作类型与消息冲突，不改变该字段的声明类型；但本规则不裁定运行时如何恢复冲突动作。

| 情况 | kind / precision | 其余要求 |
| --- | --- | --- |
| 单日日期字段存在、值非法或不可解析 | calendar_day / unresolved | state=invalid、direction=null；全部 resolved 字段 null |
| 值非法，且既无声明时间类型也无法辨别形态 | unresolved / unresolved | state=invalid；保存原值、来源和原因，不假称无日期 |
| 未提供字段，或声明允许的空值 | 不由本规则判 invalid | 沿用 omitted 等既有判据；不能把空值一概当非法 |

这里 kind 表示字段的已知时间语义，precision 表示无法得到有效精度。**invalid 动作单日字段的 calendar_day/unresolved 优先于通用 normal_forms 的 calendar_day/day。** kind=none 不能用来掩盖非法输入；state 也不能自动改成 omitted。此补丁不顺带重新裁定所有自然语言非法日期的精度。

C-0008 `/display_action/date=not_a_date` 的 date 十键固定为：

```json
{
  "state": "invalid",
  "kind": "calendar_day",
  "precision": "unresolved",
  "direction": null,
  "raw": "not_a_date",
  "resolved": null,
  "resolved_range": null,
  "resolved_instant": null,
  "window_raw": null,
  "time_filters": []
}
```

raw 来自动作字段，不要求它出现在用户 message；来源应为 source=display_action、history_index=null、quote=not_a_date、path=/date（相对 display_action）。原值是诊断证据，不是可信日期。expect.time 同步为 state=invalid、raw=not_a_date、resolved_date=null、window_raw=null；projection.slots.time 仍为 null，不能展示为已识别查询日。

自然语言任务仍保留 train.timetable、train=G1、timetable/scheduled；不得利用冲突的 emu_routing 动作把消息改为另一操作。动作执行/恢复、HTTP/SSE 错误行为仍未在本评测补丁中改变；eligibility 等策略不因字段映射完成而自动变 eligible。

## 6. 执行边界与验收

使用 [v1.4 分发提示词](prompt-11-itinerary-action-date-v1.4.md)。新产物另起目录，保留全部原输入与标签；不得修改 DISPATCH-NOTES.md、旧冻结文件、backend、前端或黄金集。

先验证：行程/段 ID 唯一连续、引用有效、段序有来源、绑定与段吻合、未决范围未被选定、日期不越权传播、非法动作 raw 可回指。再比较语义单位，以 operation/relation + 行程段/引用角色对齐，不继续只按旧索引配对。A 14/B 8 的结构不可比只有在两侧按新规则补齐后才能解除。

九键和值词表不变，但展开后单位数量和条件副本数量会变；旧的 Δ=0 和 340 配对分母不能直接延用。重新计算分母和可比覆盖，18 条校准/282 条其余开发记录分层。不从分母删除未知服务或未决引用；冻结结构后的真实 ambiguity 是合法标注状态，不再列为“等待结构规则文件”。

raw 粒度、部分范围、跨轮时段等其他残余条目仍按已有明确条款复核或单列待裁决；本文件不宣称所有标签、政策和运行目标已经完成。
