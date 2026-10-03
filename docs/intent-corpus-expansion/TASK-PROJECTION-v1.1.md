# 任务切分与投影补充规则 v1.1

日期：2026-10-01。状态：后端设计与重标口径；未修改运行源码、API、原契约 v1 或原候选。用于解决 R-run2 的 Q4/Q11 及相关表示分歧，不宣称已裁定其余全部开放问题。

依据：`.ai/intent-corpus-v1/R-run2/quality-report.md`、`open-questions.md`，以及 S/M/C 输入和两份标签。未读取 H 保留集内容。

## 1. 先分清三个层次

**任务是用户要求得到的操作结果，查询单元是该操作下的一份完整对象绑定，工具调用是实现步骤。三者不能混着计数。**

| 层次 | 定义 | 例子 |
| --- | --- | --- |
| task | 同一种 operation、同一种结果关系的请求组 | “查两趟车的经停站”是一项 train.stops 任务 |
| query_unit | 一份带角色、时间、地点和筛选条件的完整绑定 | G1/明天和 G2/后天是两个查询单元 |
| execution_step | 为得到结果需要的工具或模型步骤 | 一次任务可能调用时刻、车组等多个工具；它们不是新增用户任务 |

task 的分组键为 `(operation, relation)`。同一请求中相同键的独立单位合并为一项任务；不同日期、站点或筛选条件写在各自 query_unit 中，不据此新增 operation。多段行程的先后关系、明确依赖和比较关系必须保留，不能因合并请求组而丢失。

relation 使用 independent / compare / connection / sequence；必要的任务依赖另用 depends_on。比较两个车型是一项 knowledge.compare，不拆成两个 knowledge.explain；检查 G1→G2 接续是一项 journey.transfer，不增加两个“查时刻”语义任务。未来工具规划可以派生查时刻步骤，但它不计入用户任务集合。

**新的查询单元表示是评测内部字段，不能直接发送到聊天 API。** 当前 tasks/objects/filters 的原始数据全部保留，重标另存 v1.1。v1.1 是角色绑定的收敛口径；不自动将旧 objects 数组解释成主对象集合。

## 2. 何时拆任务，何时只加单元

按以下顺序判定：

1. 列出原文明确要求的操作结果。只有提到的背景、实体、解释性修饰不产生任务。
2. 按暂定 operation 表确定操作。不要根据准备调用几种工具反推任务数量。
3. 相同操作、相同 relation 合并为一个 task；每份不同绑定形成 query_unit。
4. 同一绑定下要求多个同类结果字段，只增加 requested_fields。没有相同含义的重复请求可去重，保留全部证据来源。
5. 不同操作分别保留 task，即使 Intent 相同、同一工具能提供两种结果，也不能合成一项。
6. 操作明确但对象缺失时仍保留 task，所需角色为 null，标缺失或歧义；不能删掉未能绑定的诉求。

| 输入 | task / 单元 | 说明 |
| --- | --- | --- |
| G1、G2 明天经停哪些站？ | 1 / 2 | 同一 train.stops，两个 train 绑定 |
| G1 明天和后天几点开？ | 1 / 2 | 同一 train.timetable，两个日期绑定 |
| G1 图定几点开、几点到？ | 1 / 1 | requested_fields 包含 departure_time、arrival_time |
| G1 到南京南和上海虹桥的图定时刻？ | 1 / 2 | 同一 train，两份 station 绑定，不能只取南京南 |
| G1 经停哪些站，二等座多少钱？ | 2 / 各 1 | train.stops + ticket.fare；票价对象/区间不足仍保留任务 |
| 北京到上海有票吗，多少钱？ | 2 / 各 1 | ticket.availability + ticket.fare，两个任务同属 ticket |
| G1 现在到哪了，晚点没有？ | 1 / 1 | train.actual_status，requested_fields=position、delay |
| CR400AF 和 CR400BF 有什么区别？ | 1 / 1 | knowledge.compare，成对 operands，不能只取第一车型 |
| G1 接 G2 在南京南能换乘吗？ | 1 / 1 | journey.transfer；绑定 arriving_train/departing_train/interchange_station；方向不明确时不要自定顺序 |
| 沪苏湖高铁开通了吗？ | 1 / 1 | news.update；没有额外要求历史介绍，不新增 knowledge.explain |
| 机位想拍 CR400AF | 1 / 1 | photo.spot，车型是拍摄目标，不能自动新增 emu.routing |
| 最近调图了吗，我常坐那趟改点吗？ | 2 / 各 1 | news.update + train.timetable；第二项车次未知，不能吞入“调图影响” |

如果原文明确提出完整中转方案，journey.transfer 是主操作；若同时明确要求票价、机位等独立结果，保留那些额外任务。仅有北京→拉萨→成都→北京行程背景，不足以自动添加“中转方案”诉求。多段行程要完整保存，不将后两段省略成北京→拉萨。

## 3. 对象必须有角色，日期必须绑定对象

旧 `objects=["G1","济南西"]` 把车次与地点放在同一个无类型列表，是差异来源之一。v1.1 用 bindings 表示角色；不把日期放进 objects。

常用角色：train、model、emu_no、station、line、origin、destination、location、photo_target、left_operand、right_operand、arriving_train、departing_train、interchange_station。每个值都需要本次/用户历史/动作字段的证据；尚未确定的角色为 null。区间保留 origin/destination 两个方向角色，不能作为两个互不相关车站。

日期是 query_unit 的作用域。支持以下三种明确表达：

- “G1 明天，G2 后天”：两份绑定，禁止交叉成四种组合。
- “G1 和 G2，明天、后天都查”：原文明确全组合才得到四份绑定。
- “G1/G2 在明天后天查一下”：未明确配对还是全组合时记录歧义，不按两个列表做笛卡尔积。

比较、接续及行程序列需要保留左右/到达与出发/先后角色。只有语义确实对称时才能将对象排序；不能对 origin/destination 或换乘车次随意排序。

例：M-0062“G1234 在 11月5日、G2345 在 11月6日分别几点到济南西？”的任务结构应为：

```json
{
  "operation": "train.timetable",
  "intent": "schedule",
  "question_type": "realtime",
  "relation": "independent",
  "query_units": [
    {
      "bindings": {"train": "G1234", "station": "济南西"},
      "date": {"state": "explicit", "raw": "11月5日", "resolved": "2026-11-05"},
      "requested_fields": ["arrival_time"],
      "constraints": {},
      "time_basis": "scheduled"
    },
    {
      "bindings": {"train": "G2345", "station": "济南西"},
      "date": {"state": "explicit", "raw": "11月6日", "resolved": "2026-11-06"},
      "requested_fields": ["arrival_time"],
      "constraints": {},
      "time_basis": "scheduled"
    }
  ]
}
```

例子按 clock=2026-10-01 解释日期。requested_fields 只记录明确需求，不凭空补“担当、票价”等；不确定细分字段放 pending_fields，由分类维护者给稳定编码后再计算相应一致率。

slot_sources 继续使用真实输入证据。完整重标还应增加 unit_sources，键与 query_unit 绑定字段对应。display_action 的证据要绑定到 `/trains/0`、`/date` 等具体字段路径，不能只证明某段文字出现在动作 JSON 中。

## 4. 投影是独立步骤

任务集合确认后，才计算当前 Intent/QuestionType/五个 Slots 的兼容投影。它是当前单值接口能承载的信息，不是完整任务表示。部分投影不能冒充完整执行计划。

### intent 与 question_type

- 所有明确任务映射到同一 Intent：保留该值，不因为多对象或多任务就填 null。
- 明确任务映射到不同 Intent：投影 intent=null，不取第一个、票数最多或所谓主要任务。仍在各 task 保留真实 Intent。
- 如果存在 operation/intent 未决且可能改变投影，intent=null。
- 全部 realtime → realtime；全部 knowledge → knowledge；明确同时有两者或任一任务本身 mixed → mixed。
- 未决任务可能改变整体性质时为 null；已明确存在知识与事实两部分时 mixed 已成立，不因另一项未决而取消它。

### projection.status

| 值 | 固定定义 |
| --- | --- |
| unique | 已明确一项任务、一份绑定，并能通过既有槽位角色映射表达关键对象 |
| multi | 语义明确，但存在多个任务、多个绑定或关系型多角色，不能压成一份完整单值投影 |
| unresolved | 有意图、必要对象、对应关系或时间作用域未决；仍保留已确定的任务及可安全投影的字段 |

已知数据源缺能力，不等于语义 unresolved。缺少可执行来源可保持 unique/multi，执行策略另记 defer/undetermined。

### 五个单值槽位

先为每个 query_unit 按操作角色生成候选槽位，再取安全公共值：

- target：车次/车型/车组/线路等该操作主对象。多值为 null，禁止 `"G1、G2"`、`"G1 与 G2"`；不把车次放进 location。
- location：该操作明确的站点/地点。多站点或角色不清时为 null；单个共同的济南西可保留。
- direction：只表示唯一、明确的铁路 origin→destination；不能写“北京→拉萨（含后续行程）”。
- time：只有同一日期/时段作用域明确适用于全部投影单元时保留原话。对象分别对应不同日期时为 null，不选第一日、最后一日或取 clock 默认。
- extra：仅承载可证的共同偏好/限制，如二等座。不能承载第二个任务、第二车次日期、全部任务摘要或比较/接续对象。

公共值需要来源与作用域都成立：只出现在第一项任务的 location 不能被强行提升到全部任务。某槽位不适用于其他操作时，原任务仍保留其值，对整体投影则置 null。实体字符串相同但角色不同，也不能合并。

同一日期有不同原话且可确定等价时，保留证据优先级为动作字段、本次明确绝对日期、本次相对日期、明确用户历史；同优先级取最早原文片段。无法证明等价时不合并。此规则只统一标注表示，不改变现有时间解析实现。

M-0062 的投影因此为：status=multi、intent=schedule、question_type=realtime，location=济南西，target/time/direction/extra=null；两组车次日期仍完整保留在 query_units。

同日两趟车的合法 `train_schedule_batch` 动作保留原 trains/date 载荷，Slots.target=null；不能为满足单值槽位删掉一趟车。projection=multi 不意味着动作载荷有歧义，也不证明当前执行层已经完全限制查询范围。

### 首期执行策略

projection 不单独决定 eligibility。自然语言多任务、多绑定和关系型任务在首期仍保守交给现有模型决策，不自动扩成确定性新能力。合法动作的路由与校验按现有动作约定另评估；未知/非法动作不臆造新失败协议。语义缺失需要具体澄清，模型不能补出用户未提供的唯一对象。

纯知识/混合/实际状态/资讯等依旧遵循 v1 的首期保守策略。不要通过更改任务数量、把第二项藏进 extra 来获得 eligible。

## 5. 指标按层计算，保留实质内容

0.187 是包含 filters/fact_basis 编码的完整签名一致率，不能直接读作语义正确率。排除这两项后的 132 条也仍混有粒度和对象角色约定差异，应按本规则重标后复算，不能自动认定 132 条都是实质误解。

但 **filters 不是全都噪声**：席别、车种、时段及它们对哪一对象生效，属于真实请求语义；date/dates、seat 字符串/数组等只是表示差异。先规范键和值，再比较内容。**fact_basis 也不能整体删除**：模型知识/网络获取等来源策略可单列，实际/图定是请求语义，必须在 operation/time_basis 中保留。

建议分开报告：

1. task group：operation、relation 和依赖关系的一致率；不受对象分组数量的旧表示影响。
2. query unit：展开绑定后的原子请求 multiset exact match、precision/recall；匹配角色、日期、requested_fields、时间口径和规范化限制条件。禁止只比较操作标签集合。
3. projection：intent、question_type、status、各槽位的值与来源分别比较。
4. policy：eligibility/reason 单列，不纳入语义任务签名。
5. 自由说明：rationale、must_not、clarification 作语义审计，不做字符串相等准确率。

独立查询单元的排列可忽略；方向角色、日期配对、comparison/sequence/depends_on 关系不能忽略。去重只针对完整相同的原子请求，不能先转成 set(operation) 掩盖漏对象。比较/接续等关系型请求按完整关系节点比较，不拆成独立对象查询再假称一致。

## 6. 如何处理已生成数据

不覆盖 S/M/C/B/R/C-run2，不修改原报告数字。本补充定义的是 v1.1，旧数据保留其契约版本、输入哈希和原标注。

先用下列已暴露的校准样本确认分组与投影规则：S-0018、S-0037、S-0038、S-0043、S-0074、S-0085、M-0062、M-0063、M-0075、C-0040、C-0047、C-0054、C-0089、C-0096。这是来自开发池的校准清单，不是新的独立保留集。

重标使用同一输入、两份新的独立标签和新目录，记录哪些变化是规范化、哪些是判断改正。132 条差异优先人工/独立复标，剩余条目也要检查投影一致性与旧双方同错，不能仅迁移“争议样本”。角色与日期绑定不得根据旧 objects/filters 盲目自动合并。

仍未裁定的分类、过滤键、事实来源、时间锚点、动作恢复问题继续保留 pending_fields，不为了赶冻结把它们清空。只有输入本身有问题才建议排除；一致率低不是排除依据。

下一步使用 [v1.1 重标提示词](prompt-08-relabel-v1.1.md)。本轮不自动重新分发 agent、不触碰 H、不生成或冻结新黄金集。
