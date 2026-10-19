---
doc_id: CR-06
title: 维护规范
status: active
as_of: 2026-10-17
revision: 1
sources: []
---

# 06 · 维护规范

> 目的：让这个目录在半年后还能被信任。规则比内容重要。

## 1. 目录结构

```
FUCK_CR/
├── README.md                 # 入口：维护者撰写的讽刺版
├── INDEX.md                  # 正经索引：状态、文件、信源等级、硬规矩
├── CHANGELOG.md              # 每次实质修改一行
├── 01-code-system-2026.md    # 新体系（规范级）
├── 02-transition.md          # 时间线与过渡期
├── 03-legacy-and-mapping.md  # 旧版与映射
├── 04-impact-openrailfan.md  # 对本项目的影响
├── 05-sources.md             # 信源登记
├── 06-maintenance.md         # 本文件
├── check.py                  # 一致性校验（stdlib）
└── data/
    ├── train-code-2026.json  # 机读字头表
    └── prefix-map.csv        # 新旧映射（表格）
```

## 2. 文档元数据（front matter）

除 `README.md`（入口文案，不受元数据约束）外，每个 `.md` 开头必须有：

```yaml
---
doc_id: CR-01            # 稳定编号，不复用
title: 2026 版车次编码体系
status: active           # active | draft | superseded
as_of: 2026-10-17        # 内容对应的"事实截止日"，不是修改日
revision: 1              # 实质修改 +1
sources: [S1, S3]        # 正文实际引用到的来源；C 级须在正文标注
---
```

规则：

- `as_of` 是**事实截止日**。只改错别字不动它；补了新事实才更新。
- `revision` 与 `CHANGELOG.md` 必须同步。
- `status: superseded` 时在正文第一行写"已被 CR-xx 取代"。
- 可选 `code_baseline: <commit>`：代码类文档记录行号基线，代码变动后更新。

## 3. 更新触发表

| 触发 | 改哪里 | 注意 |
|---|---|---|
| 官方公布规定全文 | `01` 全表、`05` 增源、`data/*` | 全文与解读冲突时以全文为准 |
| 公布逐趟车对照表 | `03` 新增"逐趟对照"节；`data/` 加映射文件 | 不要再把字头级当逐趟级 |
| 第二批日期确定 | `02` 时间线、`README` 状态表 | 同时更新 `as_of` |
| 12306 接口行为变化 | `02` §4、`04` §5 | 以实测为准，记录抓包日期 |
| 代码已改 | `04` §3 勾选、§9 更新 | 行号会漂，改完重新核对 |
| 发现新字头/新号段 | `01`、`03`、`data/*` | 三处必须同步 |
| 链接失效 | `05` 标 superseded、`CHANGELOG` | 不删 ID |

## 4. 修订流程

1. 改内容 → 2. 同步 `data/` → 3. 跑 `python3 check.py` → 4. `revision + 1`、更新 `as_of` → 5. 在 `CHANGELOG.md` 加一行 → 6. 提交。

涉及代码的改动，另在 `04` 的 §9 勾选，不要只改正文不勾验收项。

## 5. 数据文件规范

### `data/train-code-2026.json`

```jsonc
{
  "schema_version": "1.0",
  "meta": { "as_of": "...", "revision": 1, "sources": ["S1"] },
  "effective": { "...": "..." },
  "passenger": [ { "prefix": "X", "read": "新", "category": "...",
                   "max_speed_kmh": ">350", "ranges": ["X1-X9998"], "note": "..." } ],
  "freight":   [ ... ],
  "work":      [ ... ],
  "legacy_2014": [ ... ],
  "mapping":   [ { "old": "...", "new": "...", "confidence": "A|A-inferred|C" } ]
}
```

- `prefix` 用大写；区间用 `起点-终点`，数字与字母不补零。
- `confidence` 只允许 `A`、`A-inferred`、`C` 三值。
- 新增字段必须向后兼容（旧消费方忽略未知字段）。

### `data/prefix-map.csv`

表头固定：

```csv
old_prefix,old_read,old_meaning,old_speed,new_prefix,new_read,new_meaning,new_speed,confidence,note
```

- 一行一条映射；旧体系没有的（X/DH/B/DJ）`old_*` 留空。
- `confidence` 与 JSON 的 `mapping` 保持一致。
- 含逗号的字段一律加引号（中文逗号不算分隔符，但为统一也加）。

## 6. 一致性校验

```bash
cd FUCK_CR && python3 check.py
```

检查项：

1. JSON 可解析、`schema_version` 存在、字头唯一、区间格式合法。
2. CSV 表头正确、行数与 JSON `mapping` 一致、`confidence` 取值合法。
3. 所有 Markdown 里的 `[S#]` 都能在 `05-sources.md` 找到。
4. 不出现已废弃的旧目录名拼写（`check.py` 会扫全部 Markdown）。
5. `01` 的客运字头集合与 JSON `passenger` 一致（粗查：逐个前缀出现在正文）。

## 7. 新文档模板

````markdown
---
doc_id: CR-07
title: 标题
status: draft
as_of: 2026-10-17
revision: 1
sources: [S1]
---

# 07 · 标题

> 一句话结论。

## 1. 事实

…

## 2. 不确定项

| 事项 | 现状 | 标注 |
|---|---|---|

## 3. 对本项目的影响（如适用）

…
````

## 8. 禁止事项

1. 不写"国铁公告称 11-24 18:00"——那是 S15 转述。
2. 不生成逐趟车新旧对照。
3. 不把 C 级来源当论据：可以登记，但正文必须标"C 级/仅交叉核对"。
4. 不在正文重复 URL（统一放 `05`），避免两处失效。
5. 不为了"看起来完整"而补没来源的数字；宁缺勿造。
6. 不删除旧 `doc_id`/`S#`，只标 superseded。
