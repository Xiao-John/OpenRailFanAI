# Main 意图决策树实施基线

日期：2026-10-02（Asia/Shanghai）。状态：**基线已建立并验证；新决策树尚未实施或接入。**

11 个相关离线套件通过，冻结语料通过 549 项断言。当前快路径在 192 条冻结意图样本中接管 79 条，在 300 条 L14 开发样本中接管 90 条。518 条完整语义输出在两个独立进程中复现一致。本报告记录当前实现行为与本地开销，不报告系统准确率、云端连通性或整轮问答性能。

## 1. 已固定的源码与数据

| 项目 | 本次基线 |
| --- | --- |
| 原提交 | f256ccbf7f93e1de27eaf0b05f1d4ce2dbbebec3 |
| 原分支 | feat/local-small-model |
| 执行变体 | Main；全新进程环境，未加载用户密钥配置 |
| 独立工作树 | /Users/xylo/.codex/worktrees/intent-tree-baseline/OpenRailFanAI |
| 快照范围 | 130 个允许范围内的文件：当前后端 Python 源码、所选回归、冻结语料、L14 输入/双侧标签/比较资料、规则与边界文档、本地铁路字典与列车缓存 |
| 与提交版本的关系 | 含 20 个已有改动的受跟踪文件、70 个未跟踪文件；其余 40 个与提交版本一致。未提交优化按当前字节状态保留，未退回远端旧状态 |
| 最终归档 | baseline-snapshot-final.tar.gz；约 5.6 MB；130 个成员逐字节验证通过；保留原文件时间与权限 |
| 执行环境 | macOS Darwin 25.6.0 / arm64；Python 3.12.14；PYTHONHASHSEED=0；Asia/Shanghai；包版本另存 |
| 本地资源 | 3384 个站名；dict.db 可用；mcp_12306 站点资源另存并固定哈希 |

最终归档 SHA-256：`33428e62406b72b7f43feb93f5510edd7487e6a966df9d92560adb55f029b659`。

原始捕获归档保留作过程证据；恢复应使用 final 归档及其 final manifest。工作树保留供下一阶段实施使用，不自动清理。完整虚拟环境没有打包，已固定实际包版本与站点资源；复跑前应核对依赖版本，不在本任务中联网安装。

关键文件固定值：

| 文件 | SHA-256 |
| --- | --- |
| 原 SPEC.md | 56664a6451e6260772dbdae926ac4ebd10767042d9bfc21dd0e0729b8ea3b1dd |
| 原 intent_corpus.jsonl | 30f87e162cadf3d694665567ba343823060c8a83edcf9e15146691cde4e1d29a |
| 原 phrasings_corpus.jsonl | 7c30af5f6f06e94ba51228879b834b609b263f76d167fffc54b7b377fae160c1 |
| L14-A 标签 | 18eede1e57a98dc1db512e9465b50aa9fbe6c9c8662c958bf0f5fc0bd37ceb50 |
| L14-B 标签 | 4c90c0c5883ef4241f9e0eb292c7f8e77f52dc87b7aef2b98b05d1d74bcb326d |

130 个文件在捕获、测试后和最终核验时均与原目录及隔离副本的哈希一致。各规则、输入及所选评测程序的完整哈希见 [最终清单](../.ai/intent-tree-baseline/2026-10-02-v1/snapshot-final-manifest.json)。

## 2. 回归与隔离检查

通过的 11 套：test_corpus、test_perf_fastpath、test_pipeline、test_orchestrator_semantics、test_cost_governance、test_pipeline_performance、test_dates、test_od、test_phrasings、test_multiturn_slots、test_structured_sse_visibility。结果、测试输出和耗时逐套保存，未宣称整个项目全部测试通过。

- 测试进程禁用 dotenv 加载，使用明确允许的全新环境，不继承供应商密钥。运行器阻断网络/DNS、子进程、密钥文件读取和隔离范围外文件写入；全部最终检查的阻断计数为零。
- test_perf_fastpath 的编排检查缺少检索替身，本次在 registry.invoke_by_name 注入明确的离线失败，4 次调用均未获取或生成铁路事实。真实 planner、快路径与预取调度保持运行。该适配只证明对应测试目标，不能证明真实工具查询成功。
- 其余集成检查使用测试自身的模型/工具替身。结构化 SSE 检查生成的测试 JSON 仅位于隔离工作树的 frontend/tests/visual 路径，不覆盖原目录夹具，不构成正式视觉验收。
- 冻结语料 549 项断言通过，含 192 条意图、86 条说法；回归测试已有的弱约束仍保留，不能将通过结果读作完整语义正确性。
- 79 个原快路径接管样本另经真实 planner 检查：意图、问题性质、五个槽位与快路径输出一致，planner=deterministic、模型决策回调零次。回放中模型/工具边界触碰零次。

回放工具开发时发生过两次工具自身错误：适配了不存在的 client API、平台信息助手尝试启动子进程被阻断。均已在运行器中修正，失败日志原样保留；没有为此修改生产源码或测试期望。

## 3. 当前快路径行为与热运行开销

每个独立回放进程预加载站点，首轮初始化本地字典，再按样本池循环 10 轮；下表为第二次成功回放的热运行结果。计时只覆盖 fastpath.plan_with_reason，不包含模型、工具或生成。

| 样本池 | 总条数 | 接管 | 交出 | 接管覆盖 | 热调用数 | p50 毫秒 | p95 毫秒 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 原冻结意图回归 | 192 | 79 | 113 | 41.1% | 1920 | 0.252 | 0.580 |
| L14 开发输入 | 300 | 90 | 210 | 30.0% | 3000 | 0.275 | 0.723 |
| 手写快路径/必须交出对照 | 26 | 18 | 8 | 69.2% | 260 | 0.251 | 0.479 |

两次回放的 518 条意图、性质、槽位、命中说明、matched 列表及交出原因完全相同；每次热运行输出也逐项与首次结果比对。去除计时后的语义结果 SHA-256：`f0a8586536e7d366894c39d2b54f06dffd61657ef14ab018546d4b6127f2d982`。

相对日期测试按 Asia/Shanghai 运行；L14 回放中的 app.dates 使用各样本 clock。快路径本身返回时间原话，display_action 没有传入该接口，因此本次没有测动作执行/恢复。初始资源加载及进程 RSS 另存；RSS 包含导入与评测工具，不能当成引擎增量内存。没有作整轮冷启动或云端性能测量。

## 4. 下一阶段的复核信号

L14 的 90 条被接管输入中：

- **23 条**至少一侧标签仍有 pending_fields 或 ambiguities。
- **16 条**至少一侧投影为 multi/unresolved。
- **4 条**输入含 display_action；这里只观察自然语言快路径输出，不判断最终动作是否被扩大或正确执行。

这些集合有重叠。它们是优先复核信号，不是 23/16/4 个已经裁定的错误，也不是黄金策略比较结果。完整 ID 清单见 [复核信号](../.ai/intent-tree-baseline/2026-10-02-v1/review-signals.json)。下一阶段应把这些实际接管信号与原有 35 条单侧未决队列交叉检查，优先处理会改变确定性路由、对象、日期或工具参数的条目。

本次没有归一化标签、裁定 pending、扩增语料或实现树。后续原型应先复现这份完整行为，再列明经裁决的预期差异；不能因为回归全绿或降低模型调用就直接开放正式接管。

## 5. 交付与复跑

产物均位于 `.ai/intent-tree-baseline/2026-10-02-v1/`：

| 产物 | 用途 |
| --- | --- |
| [验证汇总](../.ai/intent-tree-baseline/2026-10-02-v1/verification-summary.json) | 11 套结果、549 项断言、518 条复现与隔离计数 |
| [当前行为及性能](../.ai/intent-tree-baseline/2026-10-02-v1/legacy-baseline.json) | 每池接管/交出、原因和本地计时 |
| [完整语义输出](../.ai/intent-tree-baseline/2026-10-02-v1/legacy-fastpath-semantic-results.jsonl) | 后续树对照的逐条结果；不作真值 |
| [环境与包版本](../.ai/intent-tree-baseline/2026-10-02-v1/runtime.json) | 解释器、模块实际来源、依赖版本与资源哈希 |
| [最终清单](../.ai/intent-tree-baseline/2026-10-02-v1/snapshot-final-manifest.json) / [完整性核验](../.ai/intent-tree-baseline/2026-10-02-v1/integrity.json) | 当前文件、与 HEAD 差异和归档验证 |
| [最终快照归档](../.ai/intent-tree-baseline/2026-10-02-v1/baseline-snapshot-final.tar.gz) | 从记录提交恢复独立环境后覆盖允许范围文件；保留时间信息 |

本次执行入口为仓库根目录下的 `python3 .ai/intent-tree-baseline/2026-10-02-v1/run_baseline.py`，追加 `--replay` 运行行为回放。**再次复跑前**，先将 run_baseline.py、baseline_runner.py 和 snapshot-manifest.json 复制到新的同级版本目录，命令中的目录相应替换；输出随运行器所在目录写入，避免覆盖本次封存结果。运行器固定本次工作树与现有 backend/.venv 解释器，换路径或比较新树时另做版本化适配。依赖核对使用 requirements-observed.txt，源文件先对 final manifest 核验；相对日期回归随运行日变化应另作记录。归档包含运行器，恢复前核对 archive 和文件哈希，不在原共享目录直接解包。

原目录只新增本报告和本任务的产物目录；原后端源码、冻结语料、L14 标签、前端/Android/LM 专属实现、正式验收、启动配置及外部 API/SSE/schema_version:1 均未修改。没有启动服务、联网查询、安装依赖、提交或暂存他人修改。管理工具创建工作树的 Git 元数据属于隔离准备操作。

下一步依 [实施规划](plan-main-intent-decision-tree-next.md) 进入定向裁决与离线兼容原型。黄金集及整体决策树仍未完成；本次仅完成基线里程碑。
