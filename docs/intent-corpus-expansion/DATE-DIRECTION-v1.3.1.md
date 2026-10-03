# date.direction 裁决 v1.3.1

日期：2026-10-01。选择方案 1：维持 v1.3 normal_forms 的字段语义，明确 direction 的种类约束为硬性校验。旧 v1.3 文档、机器表、L13 标签和报告原样保留；本补丁只作用于评测资料，不改变时间解析实现、API、展示或客户端。

本条覆盖 v1.3 对 direction 强制性的未明说明；**不把 normal_forms 中所有 precision/state 组合一并宣布为硬约束**。机器覆盖表见 [date-direction-v1.3.1.json](date-direction-v1.3.1.json)。它按 SHA-256 引用原 v1.3 表，两者一起使用，不复制或重写原表。

## 1. 字段用途与硬性取值

date.direction 只描述宽泛相对时期或现行时期的方向，不是所有日期相对 clock 的过去/未来分类，也不是 Slots.direction 中的地理区间方向。

| date.kind | direction 合法值 |
| --- | --- |
| calendar_day | null |
| calendar_range | null |
| instant | null |
| time_of_day | null |
| none | null |
| unresolved | null |
| relative_period | past / future / unspecified，不能为 null |
| current_period | current |

显式和继承日期都按同一张表执行；state=invalid/ambiguous 也不能放宽 kind/direction 的组合限制。“明天”“昨天”已解析成 calendar_day 后 direction=null，原话、查询日、来源仍保留。“现在晚点吗”为 instant/null；“目前实行的标准”为 current_period/current。

如果已知是相对时期，但方向未能确定，使用 relative_period/unspecified，并保留证据及 pending；不是拿 unresolved kind 隐藏已知的相对时期。完全无法确定时间形态时才使用 unresolved/null。不得为了通过校验把明确的 past/future 请求改成 unspecified。

未来需要研究精确日期相对时钟的位置，可以从 resolved、resolved_range 或 resolved_instant 单独派生分析字段；范围跨越当前时点时也不能简单二分成 past/future。本轮不新增派生字段，不把它纳入标注签名。

## 2. 修复与统计

已确认 calendar_day/calendar_range/instant 等 kind 下的非空 direction，只需在新的标签副本中改为 null，记 representation_migration；不能重算日期、改 kind、删原话、改实体、动筛选或投影来顺便消差。比较者不得静默清空后宣称原标签已合规：先报违规，再报清楚标注为试算的规范化结果。

不合法的 kind/direction 组合记 contract_violation。原来归为 convention_deviation 的历史报告保留，不回写数字。契约违规和真实请求理解差异分别计数；不能把大量相同编码偏离当成大量独立语义误解，也不能把日期错误一起归入表示迁移。

relative_period/current_period 的错误或缺值须看原文再决定，不统一清空、不从日期大小猜测。这些可能是时间诉求漏标，不能跟明确日期的表示迁移一起清零。

## 3. 只读核验结果

输入：L13-A/L13-B labels-v1.3.jsonl。两侧各 300 条，单元总数 A=348、B=342；按 L13-C 原配对口径，共 329 对，5 条单元数不等的记录保持不可比。未读取 H。

- A 有 155 处非相对/非现行 kind 的非空 direction；B 为 0。另有 A 的一处 relative_period/null，需按新硬约束复核，不能自动填值。
- 配对单元原 direction 分歧 146；其中 134 对在 date 子层只差 direction。这里不代表该单元的所有其他语义字段都相等。
- **仅在内存中**对已知非相对/非现行 kind 清空 direction 后，133 对纯日期方向分歧消失，另外 9 对共现方向分歧也消失；仍有 4 对 direction 分歧：C-0078、M-0078、S-0066、S-0077。不能从“134 对只差 direction”推断它们全是日历日期的冗余方向。
- M-0078 两侧均为 ambiguous/relative_period/unresolved，“国庆以后”的 direction 为 A=null、B=future。这一对属于那 134 对，但不能机械置空。按本条输入的“以后”及 clock，**direction=future**；仍保留国庆日/假期结束的锚点歧义及全部其他字段，不填写具体假期日期。A 的这一项另记 source_review 后的 judgment_correction。
- 单纯置空试算后 direction 匹配为 325/329；再按上述单独裁决修正 M-0078，试算为 326/329，原 134 对纯日期方向分歧此时全部消失。剩余 C-0078、S-0066、S-0077 涉及 B 漏标“以后/现在”，继续语义复核。其他字段分歧完全保留。这不是已修复标签的正式成绩，也不是系统准确率。

审计含输入/契约哈希、配对位置、155 项迁移候选、M-0078 的单独裁决和两个步骤的残余差异，保存在独立目录 `.ai/intent-corpus-v1/maintainer-direction-v1.3.1/audit.json`。没有改写两侧标签或 L13-C 产物。

## 4. 可分发的补丁指令

在原 v1.3 补标提示词之后追加本文件和机器覆盖表。先读仓库边界；只写分发者指定的新目录，例如 L13-A-direction-fix、L13-B-direction-fix，已有目录则另起后缀。可读取本侧标签和原始 S/M/C 输入，不读对方标签或 H，不动运行代码、前端、旧标签和报告。

沿用 contract_version=1.3、codebook_version=1.2、constraints_time_version=1.3，另记录 direction_rules_version=1.3.1，以及两份机器表哈希。完成：

1. 全量校验八种 kind 的 direction，而不只改被比较者指出的配对单元。
2. 对禁止非空的 kind，另存标签副本并逐项记录旧值/新值/字段路径；其他字段保持原样。
3. relative_period/current_period 不合规的项核对原文，另记判断改正或未决，不套日期大小自动填值。
4. 比较者在新输出目录验证组合硬约束、输入/规则哈希及迁移边界，再重新计算方向子层。记录分母仍为 300，既有结构不可比项与其他实质差异保留。

这是一项编码纪律裁决，无需为本问题全量重新理解所有操作；也不授权将另一个 agent 的标签整体选为黄金答案。
