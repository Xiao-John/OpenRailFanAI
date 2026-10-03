# 决策树语义候选契约 v1

状态：供扩增与分类讨论使用，未接入测试入口。输出里的新字段仅属于评测资料，不是 API/SSE 或客户端协议变更。本文件独立于旧 `backend/tests/corpus/SPEC.md`；不沿用旧词表顺序来生成“语义真值”。

## 1. 所有角色的工作纪律

有仓库访问时先读 `AGENTS.md`、`docs/ui-design-boundaries.md`、`docs/backend-parallel-development-boundaries.md`。本任务没有 UI 实现。其后只读对应提示词允许的资料，只写自己的新产物目录。

不修改运行源码、原冻结语料、共同 SPEC、前端/Android/LM、验收产物、启动配置和他人报告；不读取 `.env`、密钥、私有用户日志；不访问云端模型、铁路接口或抓取网站；不发起构建、安装、服务和真实网络测试。需要事实查证时标为待查，不凭记忆生成铁路事实答案。

合成问法可以使用真实地名、线路名、语法合理的车次或车型；但不得宣称某车次存在、某车组今天担当某车次、某时刻/价格/席位/机位安全性已经验证。任务是标注用户请求，不是写答案。助手历史如含合成答案，必须在 provenance 声明为虚构上下文，且不得拿它充当已确认的工具事实。

## 2. 语义与执行策略分开

- `tasks` 保留用户所有明确诉求：多对象不等于多任务，多任务也不能硬压成一个标签。
- intent 表示当前八类的数据路由，不是提到的名词。车型技术/对比通常映射 general；线路沿线车站映射 rail_line；单站自身映射 station。
- question_type 只能为 realtime/knowledge/mixed。这里 realtime 指依赖具体事实检索，包含相对稳定的线路里程、站档；不能把事实是否常变化当作唯一判据。
- 语义清楚但当前无法安全执行，可以标 `resolved` + `defer`，不得改为含糊问题。例如实际到点请求可以很明确，但不能用图定时刻回答。
- `eligibility` 是新树保守策略的候选期望，不是当前 fastpath 输出，也不是经过测量的置信度。
- 模型不可能补出用户未提供的唯一对象。信息不足时写缺失槽位和澄清点，不标“交给模型即可确定”。不新增客户端澄清协议。

## 3. 暂定内部操作表

以下为 v1 标注锚点，不是新增 Intent、真实工具名或已实现能力。分类 agent 可以提出变更，但生成者不得自行新增 operation。

| operation | 对外 intent | 范围 |
| --- | --- | --- |
| train.stops | schedule | 某车次经停与站序 |
| train.timetable | schedule | 某车次或区间图定时刻、班次 |
| train.duration | schedule | 列车旅程历时 |
| train.operating_status | schedule | 某日开行/停运，不能据无记录断言停运 |
| train.actual_status | schedule | 实际到点、晚点或位置，标注所需事实口径 |
| emu.assignment | emu_routing | 车次 → 担当车型/车组 |
| emu.routing | emu_routing | 车型/车组 → 担当车次、交路 |
| emu.affiliation | emu_routing | 具体车组当前配属 |
| rail.route | rail_line | 铁路区间径路 |
| rail.mileage | rail_line | 铁路线或铁路区间里程 |
| rail.stations | rail_line | 线路沿线车站 |
| station.profile | station | 单站电报码、站档、站台等 |
| station.screen | station | 单站到发屏、车次及检票口 |
| ticket.availability | ticket | 席位是否可售、余票和候补状态 |
| ticket.fare | ticket | 价格；有价格不等于可售 |
| photo.spot | photo_spot | 铁路摄影位置、机位与拍摄建议 |
| news.update | news | 开通、调图、公告等动态 |
| knowledge.explain | general | 车型规格、原理、历史、命名等知识解释 |
| knowledge.compare | general | 技术、谱系、概念对比 |
| transport.nonrail | general | 城市交通、航空、公路及景点问路 |
| journey.transfer | general | 完整中转/接续方案；当前分类和执行边界待建构 |
| general.chat | general | 闲聊或其他明确非查询诉求 |

没有唯一适用操作时，task.operation/intent 可为 null，并在 ambiguities 写候选类别和原因；不能用 general.chat 掩盖铁路领域缺口。无需机械凑齐全部操作。

## 4. JSONL 格式

完整候选一行一个对象，至少有这些字段：

```json
{
  "schema": "intent-semantic-candidate-v1",
  "id": "S-0001",
  "family_id": "S-od-seat-query-01",
  "contrast_group": "S-pair-001",
  "message": "明天北京南到天津南还有二等座吗？",
  "history": [],
  "display_action": null,
  "clock": {"now": "2026-10-01T10:00:00+08:00", "timezone": "Asia/Shanghai"},
  "expect": {
    "status": "resolved",
    "tasks": [
      {
        "operation": "ticket.availability",
        "intent": "ticket",
        "question_type": "realtime",
        "objects": ["北京南→天津南"],
        "filters": {"seat": "二等座"},
        "fact_basis": "retrieved_fact"
      }
    ],
    "projection": {
      "status": "unique",
      "intent": "ticket",
      "question_type": "realtime",
      "slots": {"location": null, "target": null, "time": "明天", "direction": "北京南→天津南", "extra": "二等座"}
    },
    "slot_sources": {
      "time": {"source": "current", "history_index": null, "quote": "明天"},
      "direction": {"source": "current", "history_index": null, "quote": "北京南到天津南"},
      "extra": {"source": "current", "history_index": null, "quote": "二等座"}
    },
    "time": {"state": "explicit", "raw": "明天", "resolved_date": "2026-10-02", "window_raw": null},
    "eligibility": "eligible",
    "reason": "clear_fact",
    "missing_slots": [],
    "ambiguities": [],
    "must_not": ["把票价当作有票", "反转起终点", "使用其他日期"],
    "clarification": null
  },
  "rationale": "区间、日期、席别及余票诉求明确；请求的是可售状态。",
  "provenance": {"kind": "synthetic", "batch": "S", "assistant_history_synthetic": false}
}
```

上例是格式示意，不计入新增语料。实际 JSONL 不能跨行。允许自行添加说明字段，但不能删除或改名上述主字段。

### 字段约定

| 字段 | 约定 |
| --- | --- |
| expect.status | resolved / ambiguous / out_of_scope / invalid_input；unsupported 能力用 reason 表示，不混入语义状态 |
| tasks | 每个明确任务均填写 operation、intent、question_type、objects、filters、fact_basis；多个对象保留数组，不只写第一个 |
| fact_basis | retrieved_fact / scheduled_reference / actual_operation / model_knowledge / unresolved；描述需要的口径，不宣称数据已取到 |
| projection.status | unique / multi / unresolved；multi/unresolved 时 intent 可 null，无法准确表达的单槽位填 null，不丢 tasks 中的信息 |
| projection.question_type | realtime / knowledge / mixed / null；不得填写 either |
| projection.slots | location、target、time、direction、extra 五键齐全，值为字符串或 null；不要把 unresolved 对象填成猜测值 |
| slot_sources | 每个非空投影槽位必须有来源；source=current / history_user / display_action，history_index 对 history 从 0 开始，其他为 null；quote 引用对应输入中的文字或动作字段 |
| expect.time | state=explicit / inherited / omitted / invalid / ambiguous；raw、resolved_date、window_raw 可 null；只有可确定时写 ISO 日期 |
| eligibility | eligible / defer / undetermined；不填 fastpath、llm 或概率 |
| reason | clear_fact / knowledge / mixed / missing_required / entity_ambiguity / context_ambiguity / time_ambiguity / multi_task / multi_object / out_of_scope / unsupported_fact_basis / invalid_input / policy_undetermined |
| missing_slots / ambiguities / must_not | 字符串数组；ambiguous 必须说明真实的不确定点，不能只写“有歧义” |
| clarification | 需要补充信息时给一句具体问题，否则 null；仅评测字段 |

单个纯知识、混合诉求、实际运行状态或资讯请求，首期策略保守标 defer。多个明确任务/对象不代表语义含糊；可标 resolved、保留任务、标 defer 并写 multi_task/multi_object。分类尚无明确政策时标 undetermined/policy_undetermined，不凭空规定接管。

空输入或非法时间样本可以没有可确认 task。其他 ambiguous 样本保留能确定的部分；无法确定的值必须为 null，并写出候选解释。projection.status=multi 允许只有一个共同 intent，但它不能假称现有五个槽位完整表达了全部任务。

objects 使用规范实体或区间，不填事实答案。多个车型、车次、地点、查询日期不可省略：多日期可在 filters.dates 中完整记录，每个任务也可有自己日期。多个明确日期无法投影成一个 resolved_date 时，该字段填 null，time.state 仍可为 explicit/inherited，并在任务 filters 中保留各日期；不要把“有多个明确日期”误标成真正的时间歧义。extra 只记录原文可证的偏好，不填新的铁路知识。

## 5. 实体、时间与上下文

车次/车型大写，站名保留具体站点粒度，区间用起点→终点，线路使用已知规范名。不要把城市“北京”擅自改为“北京南”，不要把车型/组号/日期/时刻的数字当裸车次。源片段是证据；自写了规范值不等于存在性已验证。

history 只包含 role/content，顺序由旧到新，不含本次 message。槽位仅从明确用户消息或 display_action 继承；助手正文不能当成确认数据。指代对象仅存在于助手答案时应记录消解困难；不能假装没有歧义。不得跨不同话题逐槽位拼接。

clock 是评测条件，固定在样本中，不是新增 API 字段。默认使用示例 now；跨午夜样本另定固定 now，但不能给运行时虚构本来未收到的历史时间戳。历史“明天”缺少可靠锚点且跨日含义不明时保留原话、resolved_date=null，记录 time_ambiguity。明确日期、没说日期、只说时段及非法日期要分别记录。

自然语言日期原话保留在 Slots.time；规范化日期仅在 expect.time 或任务 filters 中。相对日期按该样本 clock 判断，不写运行当天。无日期 state=omitted、resolved_date=null，允许执行层有既有默认日期，不把默认值伪装成用户明确提供。

宽时段首批只标 window_raw，不要求生成者制定新的钟点范围。需要验证规范化范围的样本，由复核者依据既有业务口径单独标注；不得凭各 agent 的直觉分别定义“下午”。农历日期、假期区间或节日简称有歧义时不要硬猜公历日。

display_action 可以包含既有 `train_schedule_batch`（trains/date）或 `emu_routing`（query/date）；合法动作优先于按钮文案和历史。非法/未知动作只标注冲突及待定恢复语义，不自行规定新增 HTTP 状态或错误协议。动作专项是未来能力，不宣称当前已绕过模型或已有封闭计划。

## 6. 质量与独立性

所有新增样本注明 synthetic。至少半数不能只替换车次/站名套同一句模板；用自然口语、车迷简写、合理省略和真正不同的任务骨架。错别字不应占主要比例。

每组关键对照只改一个判别因素，例如“有票吗/多少钱”“图定几点到/实际到了吗”。每组 2–4 条；同组标签变化必须解释。模板骨架与其改写共享 family_id，即使实体换了也不能冒充独立样本。

不设接管率/通过率配额，不照现有正则猜结果，不生成答案，不自动冻结语料。分类/原文真正有争议时保持候选及疑难记录；没有问题就不要为凑配额制造歧义。
