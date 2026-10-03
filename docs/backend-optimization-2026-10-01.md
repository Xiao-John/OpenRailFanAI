# 后端优化交付与性能复核 — 2026-10-01

八项后端内部优化已合入并验证，冻结的客户端契约保持兼容。

本次范围是 [9 月 30 日性能报告](backend-performance-2026-09-30.md)第 8 节的八项优化。保留原报告作为历史测量，本文记录实现、验证和剩余限制。所有实现先在独立工作树验证，合入前核对既有文件哈希，保护原工作区的未提交修改。

## 八项优化

| 优先级 | 完成内容 | 证据 |
| --- | --- | --- |
| P0 回归入口 | 修复 retrieve 替身的 display_action 参数；Main 排除本地模型供应商、LM 保留既有目录；metrics 测试实际执行；四个新专项加入 run_all。普通输出和结构化缓冲分别断言，流水线用固定检索替身避免 mock 模型仍访问网络。 | test_cost_governance、test_orchestrator_semantics、test_llm_providers、test_metrics、test_pipeline、test_mock_render |
| P0 指标口径 | 保留 requests.completed/failed/cancelled；新增互斥 outcomes.successful/partial/degraded/failed/cancelled，区分工具失败、模型失败、降级和截断。模型逻辑调用、实际 HTTP 尝试、首次上游内容/公开文本/卡片交付分别计时。缺失 usage 不漏记调用，SDK 重试不重复计逻辑调用。p95 使用 nearest-rank。 | test_backend_performance、test_pipeline_performance、test_llm_pool；真实指标见下文 |
| P1 结构化生成 | 仅 Main 的纯 train.schedule / emu.routing 实时查询缩短辅助提示和输出：默认上限最多 256 token，关闭辅助推理，保留完整检索事实、历史隔离、来源、日期及参考时刻边界。显式请求预算仍优先，窗口耗尽不把 64 上限抬到 256。含搜索/地图等复合事实、知识/混合问题及 LM 保留原提示和预算。 | test_pipeline_performance、test_llm_pool、受控提示比较、真实云端采样 |
| P1 铁路子请求 | 为原始查询与 MCP 的 init/query、身份定位、大屏、车组详情、票价等添加固定名称耗时/失败统计；区分 HTTP 成功与业务成功，缓存记录 hit/miss/joined/expired/evicted。保留初始化、Cookie、TLS、代理与既有重试；失败查询不缓存成空白成功。 | test_rt_performance；mcp-server-12306 0.5.0.post20260822 入口已核对 |
| P1 模型连接池 | Main 按完整供应商、Key、Headers、模型/API、超时与请求预算哈希隔离 SDK 客户端；每循环最多保留 16 个池条目，活跃借用不淘汰，溢出客户端最终归还即关闭。流取消、打开失败、途中失败、提前停止及服务退出均释放资源。 | test_llm_pool；真实采样池 miss=1、hit=24 |
| P2 决策/预取 | 确定性决策未命中后，在云端决策开始时启动最多一个高置信查询；使用规范日期、完整过滤参数匹配计划；结构化 display_action 不启动无关预取。未使用、计划失败、检索退出和取消均清理。 | test_pipeline_performance 实际检索只消费一次，证明工具已在决策返回前运行 |
| P2 查询合并/缓存 | Main 同配置同日期同参数请求合并；等待者独立取消，最后一个离开才停止上游；结果和输入快照隔离。每工具缓存/在途表有界，逐项淘汰。最终余票快照 TTL 15 秒、时刻表 30 秒，保留采样时间及复用说明；其余工具只合并在途请求，RT 既有 TTL 不扩大。并存事件循环分别保存状态、分别关闭。 | test_backend_performance、test_rt_performance；50 等待者/1000 查询专项 |
| P2 异步 DNS | 抓取及 Main 模型/设置探测的同步地址校验移入工作线程；仍使用原守卫逐请求检查公网/私网策略，不缓存校验结果、不改变代理约定。 | 慢 DNS 时循环仍能推进，私网解析仍拒绝；test_backend_performance、test_llm_pool |

## 云端与测量条件

已接入 SiliconFlow；本次沿用既有配置，无需替换 Key。默认模型及结构化模型均为 `deepseek-ai/DeepSeek-V4-Flash`，服务地址 `https://api.siliconflow.cn/v1`，API 配置 auto、实际 chat_completions，mock=false。配置全局 max_tokens=4096/context_tokens=32000，工具并发=3、确定性快路径开启。Key 不写入报告、补丁或遥测。

每轮是 20 次串行请求：五个核心场景各三次，另加五个扩展场景各一次。脚本复制当前源码和只读字典到临时目录，使用独立端口、数据库及进程；只读取原配置，不复制 .env、不停止其他服务。Python 3.12.14、macOS ARM64。遥测只保存固定问题标识、计数与耗时，服务器诊断日志按配置密钥脱敏并限制权限。

- 优化前：2026-09-30 23:33–23:39，端口 54177，20/20 无错误或截断。
- 中间优化版本：2026-09-30 23:44–23:49，端口 55198。发现拍车复合场景两次被错误套用 256-token 上限；这轮作为缺陷发现证据，不能视作最终验收。
- 预算修正后：2026-10-01 11:02–11:05，端口 50839。20 次均返回 done，19 次成功、1 次云端生成中断并明确降级；无长度截断。拍车 3 次输出 1122/887/375 token，均完整结束。

三轮源码快照哈希与测量结束校验均一致。云端采样之后仅补充并存循环的查询/共享抓取 HTTP 生命周期和嵌套输入快照隔离，生成、展示投影与 API 请求模型不再改动；这些补充用离线并存循环测试验证，见最终合入证据。

## 端到端结果

以下是秒数中位值，核心场景 n=3；扩展场景 n=1。大屏最终中位数包含一次降级请求，不能作为纯成功时延指标。

| 场景 | 每轮样本数 | 优化前 | 中间版本 | 预算修正后 | 修正后错误数 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 担当/交路 | 3 | 10.60 | 8.61 | 6.35 | 0 |
| 余票 | 3 | 17.64 | 20.23 | 8.76 | 0 |
| 车站大屏 | 3 | 16.29 | 10.64 | 15.36 | 1 |
| 知识问答 | 3 | 17.70 | 11.18 | 15.76 | 0 |
| 拍车地点 | 3 | 9.94 | 10.13 | 14.41 | 0 |
| 单车时刻表 | 1 | 12.89 | 24.57 | 1.77 | 0 |
| 批量时刻表 | 1 | 31.80 | 15.39 | 4.96 | 0 |
| 票价 | 1 | 8.41 | 7.65 | 3.57 | 0 |
| 里程 | 1 | 3.38 | 6.19 | 4.34 | 0 |
| 多轮追问 | 1 | 15.40 | 13.87 | 6.82 | 0 |

这些是观察值，不能等同于优化的因果收益。两次晚间采样较接近，但仍有上游波动；晨间复测跨了日期，今天/明天解析出的查询日、实际事实和缓存状态也变化。票价、里程和批量只有单样本，不能估计尾延迟或稳定容量。没有宣称所有场景提速。

可以直接验证的生成改进：相同模拟事实/配置下，纯结构化提示从 2165 降到 1173 字符（减少 45.8%）；知识、混合、拍车复合提示与原版本完全一致。夜间纯结构化六次请求的输入 token 从 15141 降到 11667，输出从 1812 降到 838。晨间相应输入/输出为 11923/925，但事实不同，不能作为严格成本对照。整个晨间轮总用量 66260 token（输入 57837、输出 8423），25 次模型调用；相较优化前总量 65750，并未证明全系统总成本下降，也未减少本轮逻辑调用数。

预算修正后 health 探针 403 次、失败 0，p50 3.34ms/p95 5.47ms，进程 RSS 峰值 149.83MiB。优化前 669 次、失败 0，p50 3.45ms/p95 5.44ms，RSS 峰值 150.42MiB。这是短时单进程观测，不能证明长期内存稳定或多用户吞吐上限。

## 指标与资源语义

晨间 requests.completed=20 表示流程正常交付结束事件；outcomes.successful=19、degraded=1 才表示业务结果。LLM 逻辑调用 25 次、failed=1、cancelled=0。大屏的一次 `APIConnectionError` 发生在已返回 HTTP 200 的流体读取阶段，保留 error、answer_done=false、degraded=true，未伪造成功、未自动重发已开始生成的回答。

`llm.http_attempt` 观察 SDK 物理 HTTP 尝试至响应头，25 次均收到成功头；这不与后续流体读取失败矛盾。无响应头的传输失败在下一次重试或最终退出时结算，耗时可能包含重试退避；不将它解释为纯网络 RTT。`llm.first_content` 包含首次思考或正文，`first_public_answer` 是客户端可见正文，`card_delivery` 是卡片交付时间；结构化缓冲下不能用上游首 token 代替卡片可用时间。

RT init/query 是保留的客户端 get 操作时间，重定向链包含在 query 操作中；外围 `.mcp` 统计业务成功，不能把这些不同层的 count 相加当作去重网络请求数。中间轮 tickets.mcp 12 次中有 3 次业务失败，工具通过既有回退仍成功，初始化 p50 2597ms；晨间相同初始化 p50 292.7ms。初始化逻辑未跳过，这个差异显示外部接口波动，不能归因于缓存或删除初始化。

所有计数从进程启动累积；延迟窗口为最近 512 个样本，多 worker 需外部聚合。缓存键和连接池标识是完整配置的哈希，不出现在指标；不缓存模型输出。缓存失败不长时间钉住空结果，余票快照仍保留工具采样时间。缓存/在途表达到上限时不会无限扩张；突发溢出的独立查询仍由调用者取消释放，此上限不代表应用全局并发限额。

离线并发专项：50 个同键等待者只调用一次受控上游，取消 20 个后另外 30 个正常完成；返回对象互不共享可变列表。1000 个不同查询后结果缓存保持 128 条，关闭后无在途查询。这验证合并、取消和有界行为，未用真实铁路或云端做 50 人压测。

## 验证、纪律与复现

已有 30 项后端套件全部通过；预算修正后重跑 19 项重点套件全部通过。生命周期补充后，后端性能专项增至 11 组；RT 专项 9 例、模型池专项 9 组、流水线专项 9 组均通过。真实两个同时运行的 asyncio 循环分别关闭查询和抓取客户端，另一循环仍可复用查询、缓存和 HTTP 客户端；嵌套参数快照、h2 缺失降级及 LM 原生命周期均有直接断言。原工作区合入后，这四项及另外六项相关回归共 10 个套件全部通过，六个 API 的契约复验也通过，见合入证据。

运行新专项（backend 目录，使用本项目 Python 环境）：

```bash
PYTHONDONTWRITEBYTECODE=1 LLM_MOCK=true PYTHONPATH=. .venv/bin/python tests/test_backend_performance.py
PYTHONDONTWRITEBYTECODE=1 LLM_MOCK=true PYTHONPATH=. .venv/bin/python tests/test_rt_performance.py
PYTHONDONTWRITEBYTECODE=1 LLM_MOCK=true PYTHONPATH=. .venv/bin/python tests/test_llm_pool.py
PYTHONDONTWRITEBYTECODE=1 LLM_MOCK=true PYTHONPATH=. .venv/bin/python tests/test_pipeline_performance.py
```

真实云端复测（读取现有配置，会产生模型调用）：

```bash
PYTHONDONTWRITEBYTECODE=1 backend/.venv/bin/python scripts/bench_backend_performance.py --rounds 3
```

脚本也支持 `--source-root` / `--config-root`，源码与配置可以分属工作树和原目录。`tests/run_all.sh` 已补充四个新专项。完整旧测试入口含外网、客户端和 LM 测试，本任务未将其整包标为通过。结构化 SSE 既有测试会写前端诊断夹具，已在独立工作树运行；未复制该夹具回当前前端，亦未将它新增到共同 run_all 入口。

聊天 API、ChatRequest/PipelineResult、展示投影源文件、schema_version=1 及所有卡片字段保持原状；设置路由只将原地址校验异步化。六个 API 的离线 TestClient 联调通过；结构化 display_action 的批量部分失败及仅重试指定车次、参考时刻、隐藏未确认时刻、查询日与历史记录隔离、块式/流式相同事实的展示投影一致性均有直接断言。普通问答流式增量、结构化铁路查询缓冲、重复事实抑制、用户停止和错误交付保持语义。块式入口没有因此增加 display_action 能力。

仅合入明确允许的后端实现/测试、基准脚本与本说明；没有复制工作树中的 Android、前端、版本、LM 专属实现、设计或验收文件。原工作区这些范围存在其他任务的同期修改，它们不属于本次后端补丁，合入时逐文件保护。未触碰正在运行的设备或前端服务。模型配置和 .env 未改动。

验证限制：发现旧 R1 测试漏模拟车型详情旁路，原重点复验及代理后续回归日志出现了 12306 公网 getCarDetail 请求；因此这几轮旧套件不能宣称全程离线。保持旧测试源文件未改动，最终离线重跑在运行器中补固定替身并阻断真实出站：hardening/product/R1 第一批均零出站尝试；R1 第二批使用两次固定公网 DNS 夹具，仍有两次假文章 HTTP 尝试被阻断，真实 DNS/socket 连接为零。第二批验证的是原测试的降级断言，不代表文章抓取成功；四套退出码均为 0。结果和阻断计数单独归档。新性能专项使用受控上游，云端测量则明确为真实请求。

仍需单独安排的工作：真实多用户吞吐/限额压测、持续运行的缓存/RSS 观测、广泛业务准确性及时效审计、Android 实机端到端计时和视觉验收。本次没有把它们写成通过。云端服务本身仍可能中断，铁路上游延迟仍波动；MCP 工厂入口的观测适配需要在依赖升级时复核。

证据均位于独立目录：

- [优化前样本](../.ai/backend-optimization/before/samples.json)、[中间样本](../.ai/backend-optimization/after/samples.json)、[预算修正后样本](../.ai/backend-optimization/after-final/samples.json)与各目录 manifest/telemetry/metrics。
- [30 项回归](../.ai/backend-optimization/regressions/results.json)、[19 项重点复验](../.ai/backend-optimization/regressions-final/results.json)。
- [冻结 API 与展示契约联调](../.ai/backend-optimization/contract-smoke.json)。
- [相同事实提示对比](../.ai/backend-optimization/prompt-controlled.json)、[50 等待者及缓存上限](../.ai/backend-optimization/concurrency.json)。
- [最终合入清单与受保护文件哈希](../.ai/backend-optimization/application-result.json)、[当前工作区复验](../.ai/backend-optimization/applied-tests/results.json)、[八项完成审计](../.ai/backend-optimization/completion-audit.json)。
- [生命周期及旧回归出站阻断验证](../.ai/backend-optimization/lifecycle-validation/)、[诊断隐私审计](../.ai/backend-optimization/privacy-audit.json)、[测量进程清理](../.ai/backend-optimization/measurement-cleanup.json)。
