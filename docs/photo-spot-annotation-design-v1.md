# 机位结构化标注方案 v1（待试标校准）

日期：2026-10-07。范围：现有 `photo_spot_doc` 文档库。

当前库能按地点、标题和正文检索攻略，但没有独立机位及可验证的筛选字段。方案是在原文旁增加标注层，保留现有检索作为兜底。本文提出新的标注方案；不修改原入库闸门，也不将旧文档中的“不做结构化抽取”改写成已经实现。

## 1. 数据分三层

| 层 | 一条记录表示什么 | 用途 |
| --- | --- | --- |
| 文档 `document` | 一个 URL 的原始页面快照 | 保存全文、来源、内容哈希和时间 |
| 机位出现 `occurrence` | 某篇文档明确描述的一个拍摄位置 | 标注、绑定证据；一篇攻略可有多个 |
| 合并机位 `spot` | 经复核确认属于同一位置的一组出现记录 | 跨文档去重、结构化检索 |

先完成 occurrence，最后再合并 spot。不同文章同名不自动合并；同一文章内不同取景位置也不因属于同一公园而合并。

切分规则：

- 明确列出的不同位置分别建 occurrence；同一位置的时段、季节、取景方向作为该位置的属性。
- “桥北侧”和“桥南侧”若原文分别描述站位，拆成两个；只有方向描述、没有独立站位时不拆。
- 路线、站名、列车经过地不直接生成机位。交通指引中的地铁站是到达方式，不自动成为拍摄目标。
- 一段描述两个站位但属性无法绑定时，拆出位置，相关属性留待复核；不能给两个位置各复制一套属性。
- 泛称“沿线很多机位”、只有作品展示、缺失上下文的指代，记录未解析片段，不生成确定机位。

## 2. 输入和证据

导出时用 SQLite 一致性快照，禁止直接改运行词典。每份源记录固定：`doc_id`、`url`、`title`、`scope`、`published`、`fetched_at`、`text_basis`、`text`、`text_sha256`。

- `doc_id = "doc_" + SHA256(url 的原始 UTF-8 字节)`，使用完整摘要。
- `text` 优先使用非空 `page_text`，否则使用非空 `snippet`；`text_basis` 分别为 `body`、`snippet`。两者都空时为 `none`，不能仅据标题生成机位。
- 文本按导出值原样保存，不先压空白、改换行或截断。全文哈希使用 SHA256；不用已有前 4000 字指纹代替全文版本。
- 标题可以作为辅助证据，不能替代正文中的位置描述。
- 每个非未知属性必须附证据：`source_field`（`text` 或 `title`）、`start`、`end`、`quote`。偏移按 Unicode 码点、从 0 起、右端不包含；必须满足 `source[start:end] == quote`。
- 点位本身也须有位置证据。上下文补充须增加另一段证据，不能用模型常识补齐。
- `published` 缺失就保留 null；`fetched_at` 是抓取时间，不代表攻略仍然有效。

## 3. 固定标注格式

每篇文档输出一条 JSONL；外层只允许以下键：

| 键 | 类型及含义 |
| --- | --- |
| `schema_version` | 整数 `1` |
| `doc_id`、`text_sha256` | 与输入一致 |
| `document_status` | `annotated / no_explicit_spot / insufficient_text / needs_review` |
| `occurrences` | occurrence 数组，可为空 |
| `unresolved` | 未解析片段数组，每项为 `reason_code`、`evidence`、`note` |

每个 occurrence 只允许：

| 键 | 类型及含义 |
| --- | --- |
| `occurrence_id` | `doc_id + ":" + text_sha256 + ":" + 主位置证据 start`；一个位置合并其多段证据 |
| `name_raw` | 原文名称；未命名则 null，不自行创造正式名称 |
| `location_evidence` | 非空证据数组，至少一段来自 `text` |
| `claims` | 属性断言数组，结构见下 |
| `pending_fields` | 未解决字段数组，每项为 `field`、`reason_code`、`evidence`、`note` |
| `review_status` | `unreviewed / accepted / needs_review / rejected`；初标只能前者或 `needs_review` |

claim 只允许：`field`、`state`、`value`、`group_id`、`evidence`、`valid_time_raw`。

- `state` 固定为 `stated / resolved / unknown / conflicting`。
- `stated`：原文直接陈述；`resolved`：仅用于 `rail_relation`，通过提供的本地实体表唯一规范化，证据须含原名，value 中保留实体表版本和实体 ID。
- `unknown`、`conflicting` 的 `value` 必须为 null；冲突的两种说法分别保留为 stated claim，再添加 conflicting 标记，不能择一消除。
- `group_id` 为字符串或 null。同一组取景条件共用 ID；例如“夏季上午拍下行、冬季下午拍上行”分两组，禁止拆散成任意季节和时段的组合。
- `valid_time_raw` 为原文有效期或观察时间字符串，否则 null。不要从抓取日期推断有效期。
- `stated`、`resolved` 的 `evidence` 必须非空；`unknown` 可以为空。一个字段可有多条 claim，不用自由文本扩充字段名。
- 没有提及的字段只记一条 unknown，不记入 pending；有歧义、绑定不清或无法规范化时才记 pending。

## 4. 字段与编码表

所有 occurrence 都必须覆盖下表字段；关系类和分组条件允许重复 claim。除表内明确保留原文的字段外，不新增分类。

| field | value 编码 |
| --- | --- |
| `location` | 对象，固定键 `province/city/district/address/landmark`，各为原文字符串或 null；行政归属只能来自原文明确绑定，不能借文章 scope 猜测；离线地理映射可留待后续另建规范化层 |
| `point_type` | `park / bridge / roadside / station_platform / viewing_platform / museum / other`；没有足够依据为 unknown |
| `view_target` | `operating_railway / yard_or_depot / static_rolling_stock / mixed`；不把静态火车公园当成运营线拍车点 |
| `rail_relation` | 对象，固定键 `entity_type/entity_raw/entity_id/catalog_version/relation`；类型 `line / station / yard / depot`，关系 `photographed / nearby / access / mention`；后三种不能自动变成 photographed |
| `coordinates` | 对象，固定键 `lat/lon/crs/precision_raw`；仅提取原文给出的坐标。crs 为 `WGS84 / GCJ02 / BD09 / unknown`；未知坐标系不能用于距离过滤，禁止地名猜坐标或站点坐标代替机位坐标 |
| `season` | `spring / summer / autumn / winter / all_year`；“全年”必须原文明说，未提季节不等于全年 |
| `time_of_day` | `dawn / morning / noon / afternoon / dusk / night / any`；明确钟点只存 `time_window`，不靠钟点自动归类；“任意”须原文明说 |
| `time_window` | 对象，固定键 `start/end/timezone/qualifier`；明确时分标准化为 HH:MM，未给时区则 null，qualifier 为原文限定或 null；跨午夜按 end 小于 start 表示 |
| `light` | `front / side / back / variable`；仅标注明确的顺光、侧光、逆光或变化，不计算太阳方位 |
| `camera_direction` | `N / NE / E / SE / S / SW / W / NW`；指相机朝向，不把“铁路北侧”当成朝北 |
| `train_direction_raw` | 原文字符串，例如“上行”；不换算东、西，也不套用别处线路方向 |
| `access_mode` | `walk / bicycle / car / bus / metro / rail`；保留每种方式独立证据 |
| `access_instruction_raw` | 原文字符串，包含距离及路径；不据此产生坐标 |
| `access_status` | `open / conditional / closed / prohibited`；均为“来源所述状态”，不是当前许可认证。公共地名、作者曾到访不等于 open |
| `fee_raw` | 原文字符串；明确免费才写“免费” |
| `hazard` | `track_intrusion / restricted_area / road_traffic / height_or_edge / other`；无记载为 unknown，不能标成安全 |
| `equipment_raw` | 原文字符串，例如焦段、梯子要求；不自行推荐装备 |

`location` 的每个非 null 子键都要有相应证据；无法唯一绑定到点位的归属保留 null，并登记 `location` pending。`coordinates` 的 lat/lon 必须成对、在合法范围内；不能只有半个坐标。

统一 reason_code：`ambiguous_reference / unclear_binding / entity_unresolved / insufficient_location / conflicting_sources / unsupported_value / insufficient_text`。note 用于解释，不参与分类和检索。遇到编码表未覆盖情况写 unsupported_value，集中提案，不自增枚举。

## 5. 跨文档合并与冲突

先生成 `merge_candidates.jsonl`，固定键：`left_occurrence_id/right_occurrence_id/decision/evidence/note`。decision 为 `same / different / uncertain`。此处 evidence 引用 occurrence ID 和其证据区间。

同名加同城市只够形成候选。确认 same 需要可定位的共同站位描述或相容的明确坐标，并核对桥侧、站位等差异。没有充分信息一律 uncertain。不能按正文相似度或模型相似度直接合并。

复核接受 same 后，维护稳定的 `spot_id` 和成员表；首次可分配 UUID，后续保留旧 ID，不能因新增来源重算整个 spot_id。不同来源属性冲突逐条保留；更多重复转载不算多份独立证据。新版原文变动使旧标注失效，应按 doc_id + text_sha256 重新标注。

## 6. 检索和入库方向

后续实现可增加独立的 `photo_spot_annotation_document`、`photo_spot_occurrence`、`photo_spot_claim`、`photo_spot`、`photo_spot_member` 表，以及已复核关系和筛选字段索引。原 `photo_spot_doc` 不删不替换，本轮只输出标注文件，不直接建这些表。

确定性检索仅使用 accepted occurrence 的无冲突属性：

- 多条件按 AND 筛选；同一字段多个候选值按 OR。取景条件必须来自相容的 group，不能跨组拼凑。
- unknown、conflicting、待复核均不能满足硬筛选，包括“排除禁入”“免费”等否定条件。不得用缺少禁入记录证明可以进入。
- 地点与线路关系分别过滤；“经某站到达”不能满足“拍摄某站”。未唯一规范化的实体只参与原文关键词检索，不参与实体 ID 精确过滤。
- 距离检索只使用明确坐标系、能可靠转换的坐标；目前不承诺坐标覆盖率。
- 未能满足筛选时，明确返回“符合条件的已标注机位不足”，可另外提供原文线索；不放宽条件后仍称符合。
- 每条结果保留来源、证据、来源时间和未知字段。机位标注尚未接入现有在线工具或客户端协议。

## 7. 试标与验收

1. 导出全库清单及统计，按输入哈希锁定批次；不假定始终是 810 篇。
2. 选 60 篇试标，覆盖单点、盘点多点、交通指引、静态火车公园、位置模糊、属性冲突和正文不足。正文不足若库中不存在，用独立校验夹具测试，不混入真实语料。
3. 其中 20 篇做独立双标，第二次不能读取第一次结果。只有一个 agent 时可交付待双标文件，不伪称独立复核。
4. 裁决点位切分、属性绑定和字段编码；形成变更清单，校准后冻结 v1.0。未经裁决不能进入全量自动接受阶段。
5. 冻结后按 doc_id 的固定顺序每批 50 篇，保留输入、结果、校验报告和失败清单；不覆盖旧批次。

硬校验：JSON 结构、固定键及枚举、证据偏移、文本哈希、ID 唯一、坐标范围、unknown/null、point 必须有正文位置证据，全部通过。每篇输入必须有输出，即使不能提取也不能消失。试标点位全部人工或独立复核；全量每批抽查至少 10 篇，并复核全部 pending、冲突和合并候选。

分别报告文档完成率、候选/接受机位数、字段有证据率、未知率、点位切分一致率、字段与绑定一致率、未决数量。不用“JSON 合法率”代替语义准确率，也不用双标一致率当系统准确率。有证据率以出现记录为分母，各字段单独计算；待复核不能计入接受数量。

双标比较先按同篇原文的位置证据和指代关系对齐 occurrence，保存对齐表；不能仅因主证据 start 不同就算成两个不同机位。先报告未匹配、拆分与合并差异，再对已对齐机位计算字段一致率。小批复核通过后，用另留的 20 篇盲测集检查枚举和切分规则是否稳定；未通过则修订方案，保留旧版输出。

先验证能否可靠回答“某地某线路的下午顺光机位”，再决定是否扩大标注。不要为了覆盖率补全缺失事实。
