# v1.2 开发池案例裁决记录

日期：2026-10-01。直接核对 S/M/C inputs.jsonl 与 L11-A/L11-B labels-v1.1.jsonl。仅记录本次已核实的九条，不宣称审完全部 300 条或原 14 条校准样本；不覆盖任一侧标签或 L11-C 报告，不接触 H。这些是开发校准依据，不是新的盲测成绩。

## 明确裁决

| ID | 输入要求和裁决 | 两侧核对结果 |
| --- | --- | --- |
| S-0043 | train.timetable，2 单元，各自为 G10+南京南、G10+上海虹桥；同日 2026-10-02；arrival_time / scheduled。projection=multi，target=G10、time=10月2号、location=null | **A 已正确绑定**；B 将车次与到站列表拆开，丢失完整绑定。无需改 A 的单元粒度 |
| S-0085 | news.update/timetable_change + train.timetable/schedule_change；第二项 train=null；expect.status=ambiguous，projection=unresolved，intent=null，question_type=realtime。最近的具体窗口保留 pending | **A 的状态判断正确**；B 的 resolved/multi/mixed 和擅加 departure_time 不合适。A 的字段码/time_basis 仍按 v1.2 迁移；第一项 not_applicable，第二项 scheduled |
| M-0062 | 2 单元，G1234+济南西+2026-11-05；G2345+济南西+2026-11-06；arrival_time / scheduled。共同 location=济南西，target/time=null | A 合规；B 两份单元均遗漏 station，不能只在投影层补 location 掩盖绑定缺失 |
| C-0089 | journey.transfer/connection，**1 个完整关系单元**，两个方向角色为 null；保留 G1/G2 未分配证据及 interchange_station=南京南站；transfer_feasibility / not_applicable；ambiguous/unresolved/realtime | **两侧都要修**：A 拆成两个 train 单元且 resolved 与自己的方向歧义说明矛盾；B 拆成三个角色单元，并未经证据指派 G1 到达/G2 出发。原文“和”没有给方向 |
| S-0046 | G7 的两个 fare 单元；2026-10-01、2026-10-03；time_basis=not_applicable；projection.time=null | A 两个日期正确；B 第二单元复制了 10月1号/2026-10-01，虽然 constraints 又写 10月3号，不能靠去重覆盖矛盾 |
| M-0067 | clock=2026-09-30 23:40 +08:00，明天=2026-10-01；G1234+station=北京南；departure_time / scheduled | A 日期正确；B 错写 2026-10-02，且将车次/站点拆开。A 的 origin 角色也应改为站点，不能由“从北京南开”推定全程始发 |
| M-0080 | clock=2026-12-31 23:50 +08:00，当前明天=2027-01-01；从用户历史继承 station=济南西，当前绑定 G2345；departure_time / scheduled | A 日期和继承地点正确，origin 改 station；B 错用示例日期 2026-10-02，并拆开车次/站点 |
| C-0070 | clock=2026-09-30 23:40 +08:00，明天=2026-10-01；保留凌晨0:30；availability / not_applicable；车次或区间未知，ambiguous/unresolved | A 日期和对象未决正确，time_basis 迁移；B 错写 2026-10-02，resolved 也与缺对象不符。可解析日期不意味着“那趟车”已被识别 |

时刻 0:30 是用户原话中的具体钟点；保留其原文，不能把它泛化成整段凌晨。未知车次不会阻止日期独立解析。以上日期都来自输入 clock，不依赖系统当前时间。

## C-0096：保留共同确定项，重新判定两侧标签

原文三段顺序：北京→拉萨、拉萨→成都、成都→北京。均为城市粒度；不得替换成具体车站。约一周停留是原文约束，不是已确定的到发日期或旅行事实。

按 v1.1“工具步骤不是用户任务”与“没有明确中转要求不自动增加中转任务”的规则，当前明确结果有六组：

| operation | requested_fields | 绑定范围 |
| --- | --- | --- |
| train.timetable | arrival_time, train_list | 三段城市区间分别保留完整 origin+destination；relation=sequence，保留段序 |
| train.stops | stop_sequence | 沿途经停诉求；具体服务尚未选择，保留行程上下文，train 未决 |
| ticket.fare | fare | 票价诉求，完整行程引用及所需服务/区间不能丢 |
| ticket.availability | availability | 按票务语境保留卧铺诉求，seat=卧铺；不能合并进 fare。若是否只问设有卧铺仍有疑义，另记候选解释 |
| photo.spot | spot_options | “这条线路”的范围未唯一确认，保留行程与候选范围 |
| emu.routing | route_membership | model=CR400BF；“跑这条线”的线路范围未决，不能变成车次→担当的 emu.assignment |

“看看有没有合适的车次和到达时间”足以确定 train.timetable；**尚不足以新增 journey.transfer**。如果后续用户明确要求中转方案，才按相应操作记录；不能根据检索过程可能查换乘而反推语义任务。relation=sequence 只保存三段先后，不能凭此生成额外工具步骤或推断每段直达。

时间：下个月在 clock=2026-10-01 下是 2026-11-01…2026-11-30；这是出行窗口，单日 resolved=null。后三段的具体日期、约一周停留的锚点和“下个月”是否覆盖全部行程继续保留证据与疑点，不能机械给每段同一天，也不能猜七天后是哪日。

指代：“沿途”“这条线路”“这条线”不能静默指向北京→拉萨。保留整个三段行程上下文；对未确定的引用写范围候选和 null 绑定，不默认全部范围，也不生成未经授权的笛卡尔积。段序可以用 unit 的 leg_index 与任务的 sequence_order 保存；每项都有输入证据，具体结构由重标者明确输出并作为待复核结构项。

projection.intent=null；目前六组都是事实诉求，question_type=realtime，不因旅行建议自动加 knowledge。线路指代和具体服务/时间仍未决，因此 expect.status=ambiguous、projection.status=unresolved、五槽位均为 null；首期 defer。字段口径：train.timetable/train.stops 为 scheduled，其余为 not_applicable。

**B 的三段绑定丢失、fare/availability 合并、emu.assignment 误判可以确认。A 保留三段、拆开票务、使用 emu.routing 更好，但仍有需要改正的地方：journey.transfer 过度推导、把北京→拉萨擅自绑定到 emu.routing、resolved 与未决说明矛盾，以及时间维度混用。不能将整个 A 标签直接确认为黄金答案。** 本记录只裁定明确部分；依赖/行程引用结构及卧铺问法的剩余歧义继续显式复核。

## 对既有报告的解释

L11-C 的 341/420 单元、0.179 单元匹配 F1、0.214 任务内容全等均保留原报告。它们证明两侧结构和编码未统一，不能证明系统只有相应准确率；未重标前也不能给出修复后的预测分数。

其经验等价表中 stop_membership→stop_sequence、segment_count→mileage、operator_bureau→assignment、shooting_suitability→spot_options 等会丢失结果含义，v1.2 不采用。将 time_basis.reference→retrieved_fact 也混了请求口径与来源。按照 v1.2 重新比较时，须将编码迁移与判断改正分开列账，原数字不回写。
