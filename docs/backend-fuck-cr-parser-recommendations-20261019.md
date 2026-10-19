# 新列车车次解析器：评审与改进建议

日期：2026-10-19。对象：`backend/app/fuck_cr/parser.py`（下称"新解析器"）。
性质：**只读评审与建议**。本轮未修改该解析器，也未修改 `shadow` 与生产分流；所有实测都在离线进程内完成。

## 0. 结论

新解析器是 0.1.29 里最值得保留的资产：版本显式、token 不可变、保留原文字符偏移、未知字头不截号、候选域与业务准入分开——方向正确，**不建议重写**。

但它目前是"语法候选器"，不是"可直接用于准入的解析器"。上线前建议先补齐五件事：

1. **状态一致性**：`0X1` 这类带前导零的 token 仍是 `candidate`，与 `_domain()` 的 `unknown` 判断矛盾。
2. **候选强度**：裸数字、序数、分式都被当成同级候选；影子差异会被弱候选污染。
3. **身份规范化**：`G0001` 与 `G1` 是两个 `codes`，会让影子把"写法差异"记成"解析差异"。
4. **号段证据**：`X55001` 是 `unknown`，`B9999`/`DH9999` 却仍是 `freight_candidate`；候选域没有号段匹配状态。
5. **尾号链**：`Z264/265/266` 的 265/266 不知道属于 Z264；`Z264-265` 直接判为 opaque。

这五件事都不改变"识别/准入分离"的大方向，只是把候选结果变得可解释、可聚合、可回归。**在这些完成之前，不要让 `codes` 直接进入业务准入，也不要让新解析器替换 `extract_train_code`。**

## 1. 它现在是什么，不是什么

| 是 | 不是 |
|---|---|
| 带版本的语法候选器 | 车次存在性/可售性判断 |
| 保留原文、偏移、未知字头、歧义尾链 | 逐趟新旧号映射 |
| 区分候选域（客运/货运/路用/未知） | 供应商能力（12306/rail.re 是否支持） |
| 显式 `legacy` / `reform_2026` 语法档案 | 按日期自动切换、自动启用 |
| 可离线、无网络、无状态 | 历史数据解释层、缓存版本层 |

解析器 docstring 已写明"candidate syntax never establishes a real train"（`parser.py:1-6`、`:101-106`）。这条契约应继续保持。

## 2. 实测边界（2026-10-19，离线）

命令见附录 A。关键输出：

| 输入 | `codes` | 状态/域/原因 | 判断 |
|---|---|---|---|
| `0X1` | `('0X1',)` | `candidate / unknown / syntax_only` | **状态不一致**：`_domain()` 已因 `zero` 返回 unknown |
| `G0001` | `('G0001',)` | `candidate / passenger / syntax_only` | 与 `G1` 是两个身份 |
| `第1次` | `('1',)` | `candidate / unknown / numeric_syntax_only` | 序数被当车次 |
| `1` / `1461` | `('1',)` / `('1461',)` | `candidate / unknown / numeric_syntax_only` | 强弱未区分 |
| `1/2` / `3/4` | `('1','2')` / `('3','4')` | 两个 candidate | 分式被当数字列表 |
| `Z264/265/266` | `('Z264',)` | 265/266 `ambiguous / linked_numeric_suffix` | 尾号不知道属于谁 |
| `Z264-265` | `()` | `opaque_invalid_token` | 连字符尾号整串被拒 |
| `X55001` | `('X55001',)` | `candidate / unknown / syntax_only` | 试运转号段无专门域 |
| `B9999` / `DH9999` | 各自 | `candidate / freight / syntax_only` | 已超出资料号段仍是货运 |
| `L123` | `('L123',)` | `candidate / unknown / legacy_only` | 历史字头保留，域未定 |
| `AB123次` | `()` | `unknown_prefix` | 正确：不截成 B123 |
| `2026年10月19日` | `()` | `date_or_clock` | 正确：日期被屏蔽 |
| `CR400AF-0001` | `()` | `rolling_stock` | 正确：车型被屏蔽 |
| `G1次列车` | `('G1',)` | `candidate / passenger` | 正确 |
| `1461、1462` | `('1461','1462')` | 两个 candidate | 正确（列表） |
| `1461.5` | `()` | `date_or_clock` | **可疑**：`_DATES` 把 4 位年份写成任意数字，`1461.5` 被当成日期 |

这些输出已经足以说明：解析器的"识别"能力是好的，问题集中在**状态、强度、身份、域证据、链结构**五个表示层。

## 3. P0：接入影子/生产前建议先做

### P0-1 前导零必须影响 status

**现状**：`_CODE` 的 `zero` 组在 `parser.py:134` 被捕获；`_domain()` 在 `:81-82` 因 `zero=True` 返回 `UNKNOWN`；但 `status` 在 `:140` 已先置为 `CANDIDATE`，之后没有任何回改。于是 `parse("0X1")` 的 `codes` 包含 `0X1`。

**建议**：

- `zero=True` 时置 `Status.AMBIGUOUS`（或新增 `LEADING_ZERO`，如果希望统计上区分），理由 `leading_zero`；
- `codes` 只收 `CANDIDATE`，自然排除；
- 对 `0G1`、`0X1`、`00G1` 加回归；`00G1` 目前会落到 `opaque_invalid_token`，是否也要给 `leading_zero` 由产品决定，但不要"有时 candidate、有时 rejected"。

**验收**：`parse("0X1").codes == ()`，且 token 仍保留原文/偏移；`parse("G1").codes == ("G1",)` 不受影响。

### P0-2 区分强/弱候选，处理序数与分式

**现状**：

- `_NUMERIC_LIST`（`parser.py:41`）允许"单个 1–5 位数字"，并在 `:117` 参与 `numeric_list` 判断；于是 `parse("1")`、`parse("1461")` 都是 candidate（docstring 说 standalone/list 是允许的，所以这更像"强度未标注"而不是纯 bug）。
- 序数：`parse("第1次")` 因 `after` 匹配 `\s*次`（`:158`）成为 candidate。
- 分式：`parse("1/2")`、`parse("3/4")` 因 `/` 是列表分隔符（`:41`）成为两个 candidate。
- `parse("2次")` 也是 candidate；`2次` 可能是"两趟/第二次"，不一定是车次 2。

**建议**：引入 `strength: STRONG | WEAK | NONE`（或 `numeric_context` 枚举），而不是把"候选/不候选"做成二值：

- **强**：有 `次`/`车次`/`列车` 紧邻，或明确的列举（至少两个 4–5 位数字），或字母前缀完整 token；
- **弱**：单个裸数字、1 位数字、`2次` 这类无明确车次语境的；
- **非候选**：`第N次`（序数）、`编号N`（已有 `numeric_non_train_context`）、`1/2` 这类小数字分式。

`codes` 可以继续只收强候选，弱候选进 `weak_codes` 或 `tokens(status=AMBIGUOUS)`，影子单独计数。这样不会为了"少报"而丢掉 `1461` 这类真实用户写法，也不会让 `第1次`/`1/2` 污染差异。

**验收**：`第1次 → ()`、`1/2 → ()`（或标 ambiguous 且不进 codes）、`1461 → candidate(weak 或 strong 按产品定义)`、`1461、1462 → 两个 strong`、`2次 → weak/ambiguous`。

### P0-3 定义 canonical identity

**现状**：`_CODE` 允许 `number` 有前导零（`G0001` → `number='0001'`），`normalized` 原样保留，`codes` 得到 `G0001`。`G0001` 与 `G1` 在影子比较中是两个值。

**建议**：

- `Token` 保留 `raw`、`normalized`（展示用）与新的 `canonical`（身份用：前缀大写 + 去前导零数字 + 后缀大写）；
- `codes` 用 `canonical`；`tokens` 仍保留原文，`start/end` 可回切；
- 明确"补零是否等于同车"：建议 `G0001` 与 `G1` **身份相同**（canonical 相同），但在 token 上标 `padded=True`，便于未来解释"用户写的是 G0001"；
- 若产品认为补零应视为不同/歧义，则 `status=AMBIGUOUS`，不要静默合并。

**验收**：`parse("G0001").codes == ("G1",)`（若采纳合并）；`parse("G0001").tokens[0].raw == "G0001"`；影子不再因补零记 different。

### P0-4 候选域要带号段证据

**现状**：

- `X55001`（`X55001–X55998` 试运转，资料标 C 级）→ `domain=unknown`，`reason=syntax_only`；不是错，但不可解释。
- `B9999`、`DH9999` 超出 `FUCK_CR/data/train-code-2026.json` 列出的号段（B 最大到 B9998，DH 最大到 DH1998），仍是 `freight_candidate`。候选语法可以宽松，但不能让"域"看起来像"已核验"。

**建议**：

- `Token` 增加 `range_match: True | False | UNKNOWN`，以及 `range_basis`（如 `B2001-B2998`、`X55001-X55998`）；
- `X/G/T/K/D 55001–55998` 标 `work_candidate` + `confidence=C`（依据是 C 级转述，不当 A 用）；若资料未确认，则保留 `unknown` 但给 `reason=trial_range_unconfirmed`，不要继续 `syntax_only`；
- `B/DH` 落在列出的号段内才 `range_match=True`；超出标 `False`，域仍可 `freight_candidate`，但准入层必须看 `range_match`；
- 五位纯数字同理：只对 71001–77998 / 80001–84998 / 85001–85998 标 `range_match=True`，其余 `UNKNOWN/False`。

**验收**：`B9999.range_match == False`、`X55001.domain == work_candidate`（或 `unknown` + `trial_range_unconfirmed`）、`71001.range_match == True`。**这些字段只服务解释/准入，不改变 `codes` 的语法候选语义。**

### P0-5 尾号链要有 head 指针

**现状**：

- `Z264/265/266` → `Z264(candidate)`、`265/266(ambiguous, linked_numeric_suffix)`；但 265/266 没有指向 Z264 的字段。
- `Z264-265` 被 `_LEX`（`parser.py:33`）当成一个 token，`_CODE` 不匹配，整串 `opaque_invalid_token`；而 `Z264/265` 是支持的。

**建议**：

- `Token` 增加 `chain_head: str | None`（head 的 canonical）或 `chain_id`；对 `/` 与 `-` 两种尾号链都填；
- 允许 `Z264-265` 进入与 `Z264/265` 相同的链式表示；但**先**做车型/日期屏蔽，确保 `CR400AF-0001`、`2026-10-19` 不会被误切；
- `ParseResult` 增加 `chains`，把 head 与 tails 组织起来；`codes` 仍只含 head（当前行为），不要自动展开成 Z265/Z266 两个"真实车次"。

**验收**：`parse("Z264/265/266").tokens[1].chain_head == "Z264"`；`parse("Z264-265")` 与斜杠版本结构一致；`parse("CR400AF-0001").codes == ()`；`parse("2026-10-19").codes == ()`。

### P0-6 把 reason 变成可枚举的结构

**现状**：`reason` 是自由字符串（`syntax_only`、`numeric_syntax_only`、`linked_numeric_suffix`、`unknown_prefix`、`legacy_only`、`service_number`……）。测试和影子指标都依赖它；文案一改就会漂移，指标也无法稳定聚合。

**建议**：

- 定义 `Reason` 枚举（内部码）+ 中文说明表；`Token.reason` 用枚举值，展示层再翻译；
- 为每个 token 增加 `source`（`syntax`/`legacy`/`excluded`）与 `confidence`（A/B/C/UNKNOWN）；不要让"是否官方确认"只存在于文档；
- `ParseResult` 增加只读属性：`candidates`、`ambiguous`、`rejected`、`unknown_prefix`、`legacy_only`、`by_domain`、`chains`。

**验收**：`reason` 的所有取值都在枚举中；新增 `test_fuck_cr_parser` 断言"每个 token 的 reason ∈ 枚举"。

## 4. P1：影子观测的口径

### P1-1 用集合比较，不用列表

`shadow.observe` 目前是 `preview == legacy_codes`（`shadow.py:28`）。顺序不同会记成 different。建议 `set(preview) == set(legacy_codes)`，并把 `unknown`/`leading_zero` 作为独立维度记录——"新旧号码集合一样但未知字头不同"和"完全一致"不是一回事。

### P1-2 增加低基数、固定标签的细分计数

现在只有一个 `same/different`（`metrics.py:26-33`）。建议在同一固定标签下增加：

```text
calls / same / different
unknown_prefix
leading_zero
legacy_only
weak_numeric
linked_suffix
date_or_model_excluded
by_domain: passenger / freight / work / unknown
```

只存计数，不存号码、原文或用户文本；这与现有 metrics 的脱敏契约一致。不要把这些做成高基数 label。

### P1-3 扩大观察入口，但不要改路由

当前只观察 `service_dispatch.train_codes`（`service_dispatch.py:47`）。在启用任何新规则前，至少覆盖 `train.schedule`、`ticket.query`、`emu.routing`、`service_batch`。注意：**观察入口扩大不等于业务启用**，仍然只记计数。

### P1-4 记录"候选 → 准入"的差值

只看语法 `codes` 无法发现 T/K 的语义变化。建议在影子层加一个固定计数：解析器认为 `candidate` 且 `legacy` 准入通过/拒绝的组合数（不记具体车次）。这能回答"新解析器多识别出来的号码，是否真的能进现有服务"。

## 5. P2：测试矩阵与不变量

### 5.1 建议固定为回归的输入

| 类别 | 输入 |
|---|---|
| 前导零 | `0X1`、`0G1`、`G0001` |
| 序数/分式 | `第1次`、`第1461次`、`1/2`、`3/4` |
| 弱数字 | `1`、`2次`、`1461`、`9999` |
| 强数字 | `1461、1462`、`1461次`、`车次1461` |
| 号段 | `X55001`、`X55998`、`B1`、`B198`、`B199`、`B9999`、`DH998`、`DH1999`、`71001`、`77998`、`77999` |
| 尾号链 | `Z264/265/266`、`Z264-265`、`G1/2` |
| 排除 | `2026`、`2026年10月19日`、`2026-10-19`、`12306`、`95306`、`CR400AF-0001`、`HXD3D-0001` |
| 未知字头 | `AB123次`、`TJ123`、`0X1` |
| 全角/大小写 | `Ｘ１次`、`g1`、`G1次列车` |
| 边界 | ``、`G1/G2`、`1461.5`、`1461号`、`编号1461` |

### 5.2 建议的不变量

1. `codes ⊆ {t.status is CANDIDATE}`，且按首次出现去重；
2. 每个 token 的 `text[start:end] == raw`（含全角归一化后的偏移一致性）；
3. `version` 只由调用方决定，`parse()` 不读当前日期；
4. 未知字头绝不产出尾部数字（`AB123 → ()`，不是 `B123`）；
5. 日期/车型排除优先于号码识别；
6. 同一输入重复解析结果相等（幂等）；
7. `canonical` 相同的 token，其 `codes` 只出现一次。

### 5.3 关于 `1461.5`

`_DATES`（`parser.py:37-39`）的年份段是 `\d{4}`，没有限制 19/20 开头；因此 `1461.5` 会被当成"1461 年 5 月"而屏蔽。建议把年份限制为 `(?:19|20)\d{2}`，或在日期匹配后要求月份 1–12、且不吞掉"4 位车次 + 小数"的形态。这是低优先级，但测试里应钉住。

## 6. 建议的 Token / ParseResult 形状（示意，不是实现）

```text
Token:
  raw, normalized, canonical
  start, end
  prefix, number, suffix
  version
  status          # candidate / weak / ambiguous / unknown_prefix / rejected / leading_zero
  domain          # passenger / freight / work / unknown
  strength        # strong / weak / none
  range_match     # true / false / unknown
  range_basis     # e.g. "B2001-B2998" or null
  chain_head      # canonical of head, or null
  reason          # enum
  source          # syntax / legacy / excluded
  confidence      # A / B / C / unknown
  leading_zero, padded

ParseResult:
  version
  tokens
  candidates / weak / ambiguous / rejected / unknown_prefix
  chains
  by_domain
```

所有字段都是不可变值；`codes` 由 `canonical` 去重得到。不要在这个结构里放"是否可售/是否有交路"——那是准入层的事。

## 7. 与生产/迁移的边界

- **不要直接替换 `extract_train_code`**。它返回单个号码，新解析器返回 token 列表；需要先定义"取哪个候选、什么状态才能查"的适配层。
- **生产解析的截号护栏要单独交付**：`X1次列车 → 1` 在 0.1.29 发布包里仍是现网缺陷；工作区已有左右边界的修复（未提交、未发布，实测 `X1次列车`/`V6001次`/`DH1次`/`AB123次` 已返回 `None`），但不要因此把新解析器当成生产替代品。护栏仍应与新解析器的表示层分开提交、分开发布。
- **不要把 `codes` 当准入名单**。`Domain` 是候选提示；是否可售、是否有 rail.re 交路、是否属于客运，必须由准入层和供应商证据判断。
- **不要按日期自动切换** `legacy/reform_2026`；版本必须显式，默认 `legacy`。
- **不要生成逐趟新旧对照**；字头级映射已经足够，逐趟表等官方发布。
- **不要为了让影子"一致"而抹掉未知字头**；`AB123` 保持 unknown，比截成 `B123` 有价值。
- **不要移除历史字头**（`L` 等）；`legacy_only` 的目的就是保留它们，而不是强制退休。

## 8. 建议的实施顺序

| 顺序 | 内容 | 完成判据 |
|---|---|---|
| 1 | P0-1 前导零状态 + P0-3 canonical | `0X1` 不进 codes；`G0001` 与 `G1` 身份一致或明确歧义 |
| 2 | P0-2 强弱候选 / 序数 / 分式 | `第1次`、`1/2` 不进 codes；`1461` 保留为弱候选 |
| 3 | P0-5 尾号链 + P0-6 reason 枚举 | `Z264-265` 与 `Z264/265` 结构一致；所有 reason 可枚举 |
| 4 | P0-4 号段证据 | `B9999`/`X55001` 的 `range_match`/域可解释 |
| 5 | P1 影子口径 | 新旧集合比较 + 细分计数 + 4 入口 |
| 6 | 适配层与准入 | 不改变默认 `legacy`；`reform_2026` 仍需官方证据 |

每一步都应单独提交、单独回归；不要把"解析器表示层"和"语义/准入层"放在一个提交里。

## 附录 A：复现命令

```bash
cd /Users/xylo/Documents/OpenRailFanAI/backend
PYTHONPATH=. .venv/bin/python - <<'EOF'
from app.fuck_cr.parser import parse
cases = ["0X1","G0001","第1次","1","1461","1/2","2次","1461、1462",
         "Z264/265/266","Z264-265","G1/2","X55001","B9999","DH9999",
         "1461.5","2026年10月19日","CR400AF-0001","AB123次"]
for s in cases:
    r = parse(s)
    print(f"{s!r:18} codes={r.codes!r:24} "
          + " | ".join(f"{t.normalized}:{t.status.value}/{t.domain.value}/{t.reason}"
                       for t in r.tokens))
EOF
```

当前输出（2026-10-19）与 §2 表格一致；修改解析器后应逐项对照。

## 附录 B：相关文件

- 解析器：`backend/app/fuck_cr/parser.py`
- 影子：`backend/app/fuck_cr/shadow.py`
- 计数：`backend/app/metrics.py`
- 接入点：`backend/app/pipeline/service_dispatch.py:47`
- 机读字头表：`FUCK_CR/data/train-code-2026.json`
- 现有测试：`backend/tests/test_fuck_cr_parser.py`（10 项）、`test_fuck_cr.py`（7 项）
- 生产解析缺陷（独立）：`backend/app/tools/_rt12306.py:232-254`；0.1.29 仍受影响，工作区已有未提交的 lookbehind 修复；背景见 `docs/independent-review-20261019.md` §3 B-1
