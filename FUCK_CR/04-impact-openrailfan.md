---
doc_id: CR-04
title: 对本项目（OpenRailFanAI）的影响与迁移方案
status: active
as_of: 2026-10-18
revision: 3
sources: [S1, S3, S6]
code_baseline: 47dafbb
---

# 04 · 对本项目的影响

> 结论：**解析层已经坏了，语义层整张过期，过渡期还会 duplicated。**
> 本页只做定位与方案，不代表已实施；行号基于 `47dafbb`，改动后需重新核对。

## 1. 三个结论

1. **新字头进不来**：`X`、`V`、`DH` 不被识别；`X1次列车` 会被截成 `1`。
2. **旧语义全错位**：`_TYPE_KEYWORDS` 把"动车"判 D、"特快"判 T、"普速"判 K,T,Z——改革后每一条都反了。
3. **交路闸门误杀**：`is_emu_train_code` 只认 G/D/C，新体系里 X/T/K 都是动车组。

## 2. 实测证据

命令（2026-10-17，工作区 `47dafbb`）：

```bash
PYTHONPATH=backend backend/.venv/bin/python - <<'EOF'
from app.tools._rt12306 import is_train_code, extract_train_code, is_emu_train_code
for s in ["G1","X1","V6001","N101","DH1","71001","T109"]:
    print(s, is_train_code(s), extract_train_code(s), is_emu_train_code(s))
print(extract_train_code("X1次列车"), extract_train_code("V6001次"))
EOF
```

输出：

```
G1      True    G1      True
X1      False   None    False     ← 新最高档
V6001   False   None    False     ← 新普速主力
N101    True    N101    False     ← 解析 OK，但语义层没用到
DH1     False   None    False     ← 新货运动车
71001   False   None    False     ← 5 位路用/大宗货运
T109    True    T109    False     ← 新 T 应为动车组
X1次列车 → 1                       ← 错号，比 None 更危险
V6001次  → 6001
```

## 3. 代码触点

| # | 位置 | 现状 | 风险 | 需要改成 |
|---|---|---|---|---|
| 1 | [`backend/app/tools/_rt12306.py:193`](../backend/app/tools/_rt12306.py#L193) | `_TRAIN_CODE_RE` 前缀集 `[GDCTZKYSLBN]` | 缺 X、V；DH 双字母不匹配 | 前缀集补 `X`、`V`；`DH` 单独分支 |
| 2 | [`backend/app/tools/_rt12306.py:196`](../backend/app/tools/_rt12306.py#L196) | `_EMU_TYPE_RE` 仅 `[GDC]` | X/T/K 被判"非动车组" | 扩为 `[XGTKDC]`（Z/N/V/Y/B 仍非动车组） |
| 3 | [`backend/app/tools/_rt12306.py:232`](../backend/app/tools/_rt12306.py#L232) | `_TRAIN_CODE_IN_TEXT_RE` 同一前缀集 | 自由文本抽不到 X/V | 与 #1 同步 |
| 4 | [`backend/app/tools/_rt12306.py:234`](../backend/app/tools/_rt12306.py#L234) | 纯数字限 1–4 位 | 5 位车次不识别；`X1次` 被截成 `1` | 有字母前缀时禁止降级纯数字；5 位按场景放开 |
| 5 | [`backend/app/tools/_rt12306.py:247`](../backend/app/tools/_rt12306.py#L247) | 整串失败→文本→纯数字 | 前缀失配时产生错号 | 先判断"是否字母开头"，是则不降级 |
| 6 | [`backend/app/pipeline/retrieve.py:133`](../backend/app/pipeline/retrieve.py#L133-L144) | `_TYPE_KEYWORDS` 旧语义 | 动车/特快/快速/普速/高铁全错 | 按速度等级重建（见 §4） |
| 7 | [`backend/app/tools/ticket_query.py:123`](../backend/app/tools/ticket_query.py#L123) | `startswith(prefixes)` 吃 #6 | 普速筛选漏 V/N、误选 K/T | 依赖 #6 的新映射 |
| 8 | [`backend/app/pipeline/retrieve.py:852`](../backend/app/pipeline/retrieve.py#L852) | `emu.routing` 闸门 | 新 X/T/K 被拒 | 由 #2 自动修复 |
| 9 | [`backend/app/pipeline/service_batch.py:101`](../backend/app/pipeline/service_batch.py#L101) | 同上 | 同上 | 由 #2 自动修复 |
| 10 | [`backend/app/pipeline/ticket_pair.py:52`](../backend/app/pipeline/ticket_pair.py#L52) | `is_train_code` 过滤 | 新字头行被丢弃 | 由 #1 修复 |
| 11 | [`backend/tests/test_phrasings.py:177`](../backend/tests/test_phrasings.py#L177-L189) | 用例只有旧字头 | 注释提到 X，但**用例没有 X** | 补 X/V/N 用例 |
| 12 | [`backend/app/pipeline/planner.py:128`](../backend/app/pipeline/planner.py#L128) | 提示词示例 `G1`/`D2238` | 示例仍可用，但语义已变 | 补一句新旧差异说明 |

## 4. 语义层怎么改

现状（[`retrieve.py:133`](../backend/app/pipeline/retrieve.py#L133-L144)）：

```python
(("高铁", "高速动车", "G字头"), "G")
(("动车", "D字头"), "D")
(("特快", "T字头"), "T")
(("快速", "K字头"), "K")
(("普速", "普快", "绿皮", "慢车"), "K,T,Z")
```

问题：全部按"字母=车型"的旧假设。新体系下同一个词可能对应多个字头，且含义随实施日期变化。

建议结构（不要照抄，先定产品语义）：

| 用户词 | 新体系应落到 | 备注 |
|---|---|---|
| 高铁 / 高速动车 | `X,G,T,K` | 350 及以上 / 300 / 200–250 |
| 动车 | `G,T,K,D` | 口语"动车"含动集；纯 D 只剩动集，不能只给 D |
| 特快 | 歧义：旧 `T`→`N`；新 `T`=300 | 过渡期必须反问或新旧并列 |
| 快速 | 歧义：旧 `K`→`V`；新 `K`=200–250 | 同上 |
| 普速 / 绿皮 / 慢车 | `Z,N,V` | 不能再带 K/T |
| 城际 | `C` + `G/T/K`（>200 的部分） | 只给 C 会漏京津/沪宁；需按线路或返回车次判断 |
| 市郊 | `S` | — |

**关键决策**：语义映射要带 `effective_date`。11/25 前后的"K 字头"不是同一个东西；历史日期查询应按旧语义解释。

## 5. 过渡期：双表达

- 12306 在 15 天预售期内按"新车次开行日"切换，同一列车不同日期可能两个号（[S3]）。
- 现有去重与"同车不同号"逻辑没有跨日期双表达的处理；是否会把同一列车算成两趟，**待实测**。
- 静态数据（随包词典、GTFS、缓存）可能长期保留旧号，需要能解释"新旧是同一趟"。

## 6. 迁移方案（建议分四步）

| 阶段 | 内容 | 验收 |
|---|---|---|
| P0 数据 | 把 `data/train-code-2026.json` 定为唯一字头表；代码不散落字母集 | 新增字头只改一处 |
| P1 解析 | 补 X/V/DH、5 位；禁止"字母开头却降级纯数字" | §7 回归全绿 |
| P2 语义 | 按速度等级 + `effective_date` 重建 `_TYPE_KEYWORDS` | "普速"不再返回 K/T |
| P3 过渡 | 新旧号归一化 + 双表达去重 + 回答口径 | 跨 11/25 的查询不重复、不误判 |

P1 可以独立上线（纯增益）；P2/P3 建议在 11 月下旬前完成，否则第一批切换后回答会开始出错。

## 7. 回归用例

| 输入 | 期望 |
|---|---|
| `X1` | `is_train_code=True`，`is_emu_train_code=True` |
| `V6001` | `True`，`False` |
| `N101` | `True`，`False` |
| `DH1` | `True`，`False` |
| `B2001` | `True`，`False`（货运，不查客运交路） |
| `71001次` | 提取为 `71001` |
| `X1次列车` | 提取为 `X1`（**不是** `1`） |
| `V6001次` | 提取为 `V6001`（**不是** `6001`） |
| `T109` | `is_emu_train_code=True`（新体系） |
| `Z184` | `is_emu_train_code=False` |
| `2026` | 不是车次 |
| `1461` | 是车次 |

## 8. 回答口径（可直接复用）

- 第一批："11 月 25 日起普速车次先换：原 T 改 N、原 K 改 V，货运班列的 X 改 B。"
- 第二批："12 月下旬起高速车次换：G 里 300 km/h 的改挂 T，D 里 200–250 的改挂 K，D 只留给动集。"
- X："X 是给未来 350 km/h 以上的复兴号（CR450）预留的，目前还没有 X 字头车次。"
- 安抚："车次变了、车没变——开行区段和时刻按现行运行图，静音车厢、订餐等服务也不变。"

## 9. Definition of Done

- [ ] 解析：§7 全绿；`X1次列车` 不再产出 `1`
- [ ] 语义："普速"筛选返回 Z/N/V；"高铁"能覆盖 X/G/T/K
- [ ] 交路：新 X/T/K 不再被闸门拒；Z/N/V 仍被正确拒
- [ ] 过渡：跨新车次开行日的查询不重复计列车
- [ ] 文案：至少一处能解释新旧对应；涉及 UI 先读 [`docs/ui-design-boundaries.md`](../docs/ui-design-boundaries.md)
- [ ] 数据：`data/train-code-2026.json` 与代码同源

## 10. 不要做

1. 不要按字母硬编码"X=货运"（旧数据会误导）。
2. 不要生成逐趟车新旧对照（官方未发布）。
3. 不要假设 11/25–12 月下旬"T/K 一定不存在"（以 12306 返回为准）。
4. 不要把 `is_emu_train_code` 放宽到 Z/N/V/Y/B（rail.re 确实没有这些车的交路）。

## 11. 已落地的预备开关（2026-10-17）

新增运行兼容层 `backend/app/fuck_cr/`，配置为 `FUCK_CR_MODE`：默认 `legacy`，可选 `shadow`，`reform_2026` 预留但明确拒绝启用。影子模式只记录候选号码集合的计数差异，不改变解析返回值、客运分流或交路闸门。

这不是 §6 的解析/语义迁移完成；§9 的验收项仍全部未勾选。未知字头被截成数字的现有缺陷也尚未修复。产品类别、业务日期和数据源能力的拆分见 [后端预备方案](../docs/backend-train-code-reform-preparation-20261017.md)。补丁说明与回退步骤见 [运行兼容层 README](../backend/app/fuck_cr/README.md)。

## 12. 独立解析器（2026-10-18）

`backend/app/fuck_cr/parser.py` 已提供显式新旧语法档案、不可变原文 token、完整未知字头、候选业务域、原文字符偏移及去重投影。影子观察现用独立结果，不以旧结果播种。旧服务分流保持原样，`reform_2026` 运行模式仍拒绝启用；这不是全项目解析入口迁移完成。

34 条独立边界样本通过，另外覆盖链式折角尾号、货运号段空隙、原文位置、双版本和排除范围等专项。88 项相关测试、既有语料 549 项断言通过；192 条现有输入的新旧影子服务分流相同。上述均为离线证据，不是新体系的供应商联调或系统准确率。
