# Main 余票查询交接问题修复

日期：2026-10-04（Asia/Shanghai）。已修复并合入当前共享源码；独立工作树和可回退补丁保留。**16 个专项用例、10 套相关回归通过离线验证。没有访问真实余票接口，也没有重启服务或打包安装客户端，不能据此宣称线上或设备端已恢复。**

依据：[原交接报告](/Users/xylo/Documents/OpenRailFanAI/docs/backend-ticket-query-bug-20261004.md)。原句“查一下 2026-10-05 G1 的余票”缺实际乘车区间；此前调用 train.schedule，产生时刻卡后，重复事实保护可能将补区间正文一并删除。

## 修复后的行为

| 请求或状态 | 后端行为 |
| --- | --- |
| 原句仅有日期、G1 和余票 | 不调用时刻、交路或站点工具；正文明确要求补充实际出发站、到达站 |
| 草稿仍含 `[出发站]`、`[到达站]` 等占位 | 返回区间澄清，不向查询接口发送占位站名 |
| 2026-10-05 G1 北京南到上海虹桥余票 | ticket.query 收到日期、区间、train=G1；不追加时刻查询 |
| 原话还含下午、二等座等筛选 | 正式查询与预取带相同的时段、席别及车种条件 |
| 区间接口同时有 G1/G2 | 使用工具已有的车次过滤，只输出请求的 G1；离线固定数据验证通过 |
| 席别为 `无`、`0`、`候补` | 原样表达该席别状态，不以时刻或票价替代 |
| 席别为 `--`、空值或未返回 | 显示未提供/缺少数据，不推定无票 |
| 无该区间/车次记录、接口不可用 | 正文保留工具实际原因及备注，不断言停运，不伪造余票成功 |
| 截断、低余量变化 | 保留命中/展示数、截断、采样、变动说明及来源 |
| 同时列出多个车次 | 可见说明当前需一次指定一个车次及乘车区间；不默选第一趟，不扩张为批量能力 |

票务回执由实际 ToolResult 的席别数据或失败信息排版，块式和流式共用正文。内部 `direct_answer` 提示只用于 Main 纯实时余票交付，不进入 API 或 done 新字段。正常业务澄清及失败原因说明交付完成后，answer_done 表示正文完成；工具是否成功仍如实体现在 tool_trace。不会因不调用生成模型而伪装模型故障或标记降级。

## 修改范围与兼容性

修改 5 个已有文件，新增 2 个文件：

- [retrieve.py](/Users/xylo/Documents/OpenRailFanAI/backend/app/pipeline/retrieve.py)：Main 余票缺区间澄清；完整区间按车次及筛选条件查询；保留可见失败回执。
- [prefetch.py](/Users/xylo/Documents/OpenRailFanAI/backend/app/pipeline/prefetch.py)：先识别余票；缺区间或服务仍需指代解析时不预取；有明确区间时仅预取同参余票。
- [ticket_answer.py](/Users/xylo/Documents/OpenRailFanAI/backend/app/pipeline/ticket_answer.py)：新增票务正文投影，不生成铁路事实。
- [generate.py](/Users/xylo/Documents/OpenRailFanAI/backend/app/pipeline/generate.py)、[orchestrator.py](/Users/xylo/Documents/OpenRailFanAI/backend/app/pipeline/orchestrator.py)：交付已验证的票务回执；保留其他回答路径及结构化事实保护。
- [专项回归](/Users/xylo/Documents/OpenRailFanAI/backend/tests/test_ticket_query_delivery.py)：新增载荷、正文、车次过滤、区间隔离、席别状态、失败、预取、取消及 Main/LM 验证。
- [既有产品回归](/Users/xylo/Documents/OpenRailFanAI/backend/tests/test_product_fixes.py)：将 Main“车次余票缺区间应查时刻”的旧断言改为可见澄清；LM 旧路由另由专项用例验证。

API 路径、ChatRequest/PipelineResult 字段、SSE 事件、display schema_version:1 及展示结果种类未改。票价仍独立调用 ticket.price；合法结构化动作优先级不变；LM 保持原检索和模型生成路径。已有未来交路过滤、日期修订完整保留。本次未改前端、Android、LM 专属源码、正式验收、版本、启动配置或供应商配置。

实施使用 `/Users/xylo/.codex/worktrees/ticket-query-fix/OpenRailFanAI`，从原目录当前后端字节复制后修改；没有使用较旧提交覆盖未提交优化。合入前逐文件检查原始哈希，7 个交付文件与已测工作树逐字节相同；其余捕获的后端 Python 文件无变化。共享 UI 边界文档在本轮有同期更新，已重新阅读并保留，没有回退它。未提交或暂存其他人的修改。

## 验证条件与证据

专项最终 **16/16** 通过；直接在合入后的原目录源码另跑一遍，同样通过。初版 15 个专项用例运行修复前源码，7 个失败、4 个错误，保留原始日志；其中包含缺区间仍查时刻、G1 查询出现 G2 等明确复现。后续增加了数字车次预取/指代不猜测用例及表格结束空行检查。

通过的 10 套相关回归：test_product_fixes、test_routing、test_orchestrator_semantics、test_cost_governance、test_r1_fixes、test_r1_fixes2、test_output_guard、test_future_routing_guard、test_multiturn_slots、test_perf_fastpath。这是选定回归范围，不表示项目全部测试通过。

所有最终检查使用清洁环境，禁用 dotenv，阻断网络、密钥读取和子进程。最终防护触碰计数均为零。涉及真实边界而旧夹具不完整的套件作了明确适配：

- test_perf_fastpath、test_output_guard 使用失败型工具边界；真实决策、编排与输出保护仍运行，不验证真实铁路数据源。
- test_r1_fixes 的额外官方车组查询用缺失结果替身；test_r1_fixes2 的搜索正文抓取用失败结果替身。各套原有固定输入和断言保留，不改变生产实现来让检查通过。
- 站点资源、本地铁路字典和已知列车目录复制到隔离环境，避免因缺少本地资源制造无关失败。没有重新下载数据。

初次隔离运行发现第三方组件读取 dotenv 的尝试，防护在读取前阻断，随后完善运行器的禁用覆盖；另有旧夹具漏模拟的网络/DNS尝试同样被阻断。失败日志和诊断保留，未将这些运行计为通过。独立只读复审发现成功表格与说明之间缺空行，已修正并增加断言。没有实际读取密钥或执行线上查询。

证据位于 [.ai/ticket-query-fix/2026-10-04-v1](/Users/xylo/Documents/OpenRailFanAI/.ai/ticket-query-fix/2026-10-04-v1/validation-summary.json)：

- [验证汇总](/Users/xylo/Documents/OpenRailFanAI/.ai/ticket-query-fix/2026-10-04-v1/validation-summary.json)
- [专项最终日志](/Users/xylo/Documents/OpenRailFanAI/.ai/ticket-query-fix/2026-10-04-v1/fixed-ticket.log)、[合入后验证](/Users/xylo/Documents/OpenRailFanAI/.ai/ticket-query-fix/2026-10-04-v1/applied-ticket.log)、[修复前复现](/Users/xylo/Documents/OpenRailFanAI/.ai/ticket-query-fix/2026-10-04-v1/before-fix-ticket.log)
- [本次专用补丁](/Users/xylo/Documents/OpenRailFanAI/.ai/ticket-query-fix/2026-10-04-v1/ticket-only.patch)、[合入文件哈希](/Users/xylo/Documents/OpenRailFanAI/.ai/ticket-query-fix/2026-10-04-v1/applied-files.json)、[离线运行器](/Users/xylo/Documents/OpenRailFanAI/.ai/ticket-query-fix/2026-10-04-v1/run_offline.py)

## 剩余范围

真实 12306 可达性、实际日期下的数据覆盖、客户端安装包与在线端到端操作尚未验证；本轮没有停止前端正在使用的后端，也没有重启它。设备上的已打包 Python 不会仅因共享源码修改自动更新，需前端在其构建/联调流程中使用新后端。

指定席别过滤后空集仍如实表述为“无符合筛选条件的车次”，不能据此区分席别售罄与席别数据缺失。同时问票价和余票仍走原票价优先分支；混合知识请求继续原模型生成路径，未纳入本轮直接交付验收。历史指代、任务框架和整体决策树裁决仍按此前复核报告推进，本次不宣称它们完成。
