# 本地小模型（8G 内存也能跑）

给"不想买 API Key / 机器只有 8G 内存"的用户一条**零成本、可离线**的路。
本文只讲本地模型这一条路；云端多供应商见 [`run.md`](run.md)。

```bash
bash scripts/setup_local_model.sh --write   # 装 Ollama → 拉模型 → 冒烟 → 写 .env
```

## 0. 一句话结论

| 问题 | 结论 |
|---|---|
| 用哪个模型 | **Qwen3.5-2B（Q4_K_M）** —— 本分支只推进这一个 |
| 为什么不是 Qwen3-1.7B | 已被 Qwen3.5 小尺寸系列换代（2026-03 开源），同尺寸更强、上下文更长、KV 更省 |
| 本地模型负责什么 | **只当"决策器"**：意图分类 + 槽位抽取，输出几十个 token |
| 答案生成怎么办 | 继续用云端模型；没有云端时走已有的**确定性规则排版**（`LLM_FALLBACK_RENDER`） |
| 能不能完全离线 | 不能（12306/rail.re 数据本身要联网）。离线说的是**不再依赖付费 API** |
| 实测能不能用 | 意图 **68.1%** / 槽位 **63.6%**（云端对照 82.3% / 70.5%，全量语料 137 条）—— 见 §5 |

**别把本地小模型当"助手"用。** 实测：它当决策器勉强合格（见 §5），
当生成器写一段通顺的中文铁路分析则明显不如云端 —— 而"生成"恰好是它最贵、
最慢的一步（输出几百个 token）。**分工**才是对的用法。

## 1. 为什么是 Qwen3.5-2B

选它的**决定性理由不是"多聪明"，而是 8G 内存这个硬约束下的 KV cache 开销**。

| 模型 | 参数结构 | KV/Token | 协议 |
|---|---|---|---|
| **Qwen3.5-2B** | 24 层，**仅 6 层全注意力**（`full_attention_interval: 4`），2 KV heads、head_dim 256 | **12 KiB** | Apache-2.0 |
| MiniCPM5-2B（对比项） | 2.52B，42 层**全**注意力，2 KV heads、head_dim 128 | 42 KiB | Apache-2.0 |

算法（按各家 `config.json` 算，非实测）：
`2(K+V) × 全注意力层数 × KV 头数 × head_dim × 2 字节`。
Qwen3.5-2B 是 `2×6×2×256×2 = 12 KiB`；MiniCPM5-2B 是 `2×42×2×128×2 = 42 KiB`。
Qwen3.5 只对 1/4 的层做全注意力，其余是线性注意力（状态大小固定、不随序列增长）——
这是它长上下文便宜 3.5 倍的**结构性原因，不是调参能追上的差距**。

来源：Qwen3.5-2B [config.json](https://modelscope.cn/api/v1/models/Qwen/Qwen3.5-2B/repo?Revision=master&FilePath=config.json)、
MiniCPM5-2B [config.json](https://modelscope.cn/api/v1/models/OpenBMB/MiniCPM5-2B/repo?Revision=master&FilePath=config.json)。

**为什么不选 MiniCPM5-2B**（它 Agent 榜单更亮）：

1. **KV 贵 3.5 倍**，上下文一开大内存就吃紧（见 §2）。
2. **Ollama 下原生工具调用是坏的**：传 `tools` 时 XML tool call 永远解析不出来，
   [issue #18483](https://github.com/ollama/ollama/issues/18483) 至今 open，
   官方推荐后端是 SGLang 而不是 Ollama。本项目只用 JSON 输出、不碰 `tools`，所以不受影响，
   但"在 Ollama 上跑得稳不稳"这件事上它已经有一次前科。
3. 榜单分数其实是同一档：AA Intelligence Index 给 MiniCPM5-2B 15、Qwen3.5-2B 16
   （[来源](https://artificialanalysis.ai/articles/openbmb-releases-minicpm5-2b)），
   但**两者是不同版本的 index，不能直接相减**。MiniCPM5 的强项偏 Agent/工具调用，
   而我们的任务是"输出一个 JSON 对象"，用不上那部分优势。

> ⚠️ 引用小模型分数务必带榜单版本。同一模型 AA v4.1.1 报 23、v4.2 报 15、v4.3 报 14，
> 而厂商宣传用的是 23。

## 2. 8G 内存预算（Qwen3.5-2B）

| 上下文 | 权重 | KV cache | 合计 |
|---|---|---|---|
| 8k | 1.81 GiB | 0.09 GiB | **1.90 GiB** |
| 16k（脚本默认） | 1.81 GiB | 0.19 GiB | **2.00 GiB** |
| 32k | 1.81 GiB | 0.38 GiB | **2.19 GiB** |

权重取 Ollama `qwen3.5:2b-q4_K_M` 的模型层 1855 MiB。**注意这个数字反直觉地大**：
Qwen3.5-2B 是原生多模态，Ollama 标签把视觉塔一起打包了；纯文本 GGUF（unsloth 的
Q4_K_M）只有 1221 MiB，走 llama.cpp 时 8k 总计约 **1.3 GiB**，比走 Ollama 更省。
再加运行时约 0.3 GiB —— 8G 机器上跑服务端 + 浏览器 + 系统，余量是够的。

但有四条实测警告：

1. **"参数量小 ≠ 内存小"**：论文实测 Qwen2.5-1.5B 在 CPU 上占 **7,960 MiB**、单次 30.8 秒
   （[arXiv 2609.07370](https://arxiv.org/abs/2609.07370)）。那是 PyTorch 路线的开销，
   换成量化推理会低得多，但这句提醒要记住。
2. **Ollama runner 内存泄漏**：匿名内存随请求线性增长 5–12 MiB 且不释放，
   `KEEP_ALIVE=-1` 常驻 5.5 小时可涨到 **9.35 GiB**，而且 **RSS 看不到**（被换出到 swap）
   —— [issue #18106](https://github.com/ollama/ollama/issues/18106)。长期跑要设 `keep_alive`。
3. **上下文别贪**：4G 内存机器上跑 2B 会 swap，速度掉 5–10 倍；8G 是实际下限。
4. **KV cache 可以量化**：`OLLAMA_KV_CACHE_TYPE=q8_0` 能把 KV 再砍一半
   （brew 装完的提示里就建议这个），代价是极小的精度损失。

## 3. 必做的三个设置

`scripts/setup_local_model.sh --write` 会把这些写进 `.env`：

| 变量 | 值 | 为什么 |
|---|---|---|
| `LLM_PROVIDER` / `LLM_MODEL` | `ollama` / 本地模型名 | 走本机 11434，不需要 Key |
| `LLM_STRUCTURED_JSON_SCHEMA` | **`true`** | **最关键的开关**，见下 |
| `LLM_STRUCTURED_NO_THINK` | `true` | 决策任务的思考 token 纯是延迟。**对本地模型是致命的**，见 §3.4 |
| `LLM_CONTEXT_TOKENS` | 与本地 `num_ctx` 一致 | 对不齐时超出部分**被静默丢弃**，比报错难查 |
| （无需配置） | 派生模型的 Modelfile | 对话模板 + 停止符 + num_ctx，见 §3.3 |

### 3.1 `LLM_STRUCTURED_JSON_SCHEMA=true` 是必选项，不是优化项

它把**完整 JSON Schema** 下发给上游做**约束解码**（语法掩码），而不只是说一句
"请给我一个 JSON 对象"。差别有多大：

- 1000 条 CPU 小模型原始响应里，**只有 5 条**能被 `json.loads` 直接解析
  （[arXiv 2609.07370](https://arxiv.org/abs/2609.07370)）；
- 另一组对照：好好请求合法率 **4%** → 重试 5 次 **23%** → **约束解码 100%**，
  且每条可用记录的 token 花费少 19 倍（[来源](https://sesen.ai/blog/structured-output-llm-constrained-decoding)）。

**结论：小模型能不能当决策器，主要不取决于模型多聪明，而取决于有没有约束解码。**

⚠️ 两个实现细节：
- 本项目**不传 `strict`**：`strict` 要求 `additionalProperties:false` 且所有字段必填，
  而我们的 schema 用 draft-07 的 `["string","null"]` 写法，本地服务全都吃，传了反而被拒。
- 上游不支持时阶梯会自动丢掉 `response_format` 重试（不会整轮失败），
  但**每请求白付一次失败往返** —— 所以默认是关的，用本地模型才打开。

⚠️ **`llama-cpp-python` 自带的 server 不支持 `response_format: json_schema`**
（只认 `text` / `json_object`，且返回 **HTTP 500** —— 本项目阶梯只对 400/422 降级，
所以开着这个开关打它会直接失败）。要用约束解码请走 **Ollama** 或 **llama.cpp 的
`llama-server`**（原生支持 `json_schema`）。

### 3.2 上下文长度要对齐

Ollama 上 `qwen3.5:2b-q4_K_M` 的默认 `num_ctx` 只有 **4096**。本项目注入的
事实块（车站大屏、逐站时刻、Markdown 表格）经常远超 4k，**超出的部分会被静默丢掉**
—— 表现为"模型答得头头是道但少了一半数据"，比报错难查得多。
`setup_local_model.sh` 因此用 Modelfile 派生一个 `num_ctx=16384` 的本地模型，
而不是直接用线上 tag。

### 3.3 必须派生模型来修**对话模板与停止符**（最容易踩的坑）

**直接用 `ollama pull` 下来的 tag 是不可用的。** 实测它的 template 就是裸拼接：

```
$ ollama show --template qwen3.5:2b-q4_K_M
{{ .Prompt }}
```

GGUF 里其实**自带**一份完整的 Qwen 官方模板（`tokenizer.chat_template`，我解出来看过），
但 Ollama 这个 tag 没有用上它，也**没有定义任何停止符**。后果不是"效果差一点"，
而是模型**根本不知道轮次边界**：

- 生成到 2600+ token 也不会自己停（没有 `<|im_end|>` 停止符）；
- 该被吞掉的思考内容混在正文里；
- 实测走 OpenAI 兼容端点时，**响应正文是空字符串**，内容全跑到了 `reasoning` 字段里。

`setup_local_model.sh` 里的 Modelfile 按 GGUF 官方模板重写了文本分支，
并补上停止符。要点两条：

- assistant 轮开头要写入**空的思考块** `<think>\n\n</think>\n\n` ——
  这正是官方模板里 `enable_thinking` 为假时的写法；
- `PARAMETER stop "<|im_end|>"` / `"<|im_start|>"` 必须有，否则答完会继续编。

### 3.4 关思考：字段名各家不同，且**不认时是静默忽略**

这是本地化里最阴的一个坑。同一个提示词、同一个 `max_tokens=400`，只换关思考的字段：

| 下发字段 | 结果 |
|---|---|
| `{"enable_thinking": false}` | **被忽略**：400 token 全是思考，正文为空 |
| `{"think": false}` | OpenAI 兼容层同样忽略（只有原生 `/api/chat` 认） |
| `{"reasoning_effort": "none"}` | **生效**：44 token / **0.9 秒**，正文就是那个 JSON |

所以本项目把这件事做成了**供应商级数据**（`Provider.no_think_body`）：
`ollama` 预设自带 `reasoning_effort: none`，用户只要 `LLM_PROVIDER=ollama` 就自动正确，
不需要知道这个字段。上游不认时阶梯会把它丢掉重试（不会整轮失败）。

⚠️ 另一条连带修复：以前只有**合并调用**显式传了 `no_think`，兜底的
`intent.classify` / `extract.fill` 走的是默认 `False` —— 于是"关思考"这条配置
只管住了主路径，**一退回兜底路径就又开始思考**。云端只是白烧钱，
本地小模型则是整轮 60 秒超时。现在 `chat_structured` 不传就沿用配置，全部路径一致。

## 4. 实测：延迟的大头是**提示词**，不是模型

在本机（Apple M5，`llama-cpp-python` 的 **CPU 参考构建**，4 线程）测 planner 的真实提示词：

| 提示词 | prompt tokens | 耗时 | completion |
|---|---|---|---|
| 完整 planner 提示词（指令 + 判定要点 + schema） | 1072 | **19.8s** | 38 tok |
| 同一任务、精简提示词 | 78 | **4.6s** | 60 tok |

**prompt 是 13.7 倍，耗时是 4.3 倍**，而输出两边都只有几十个 token。
外推到真实调用（`_merged_prompt` + system + schema ≈ 2800 tok）：**单次决策 ~54 秒**，
其中 95% 以上花在 prefill。

**prompt 就是延迟本身** —— 而修好之后它几乎不再计入：

1. **本地模型别用云端那套啰嗦提示词**（→ `LLM_STRUCTURED_COMPACT_PROMPT`）。
2. **把变量放到提示词末尾**（已做）。原先"本次用户输入"夹在静态指令中间，
   导致**前缀缓存必然失效**；挪到最后之后，可缓存前缀 = 指令 + 判定要点 + 模板，
   每次真正要算的只剩几十个 token。
3. **关掉思考**（见 §3.4）。这一条比前两条加起来还重要：不关时单次决策
   60 秒超时，关掉后 **0.9 秒**。
4. 上表的绝对耗时**不是真机体验**：那是 PyPI 的 `llama-cpp-python` **CPU 参考构建**
   （轮子没带 Metal/Accelerate）。Ollama 发布的是 ARM 优化构建，
   实测同一台机器上决策 p50 = **1.0 秒**、解码约 46 tok/s。
   请把上表当**相对量级**看。

**前缀缓存是真的在生效**（Ollama 日志里的 `prompt eval time`）：
```
冷启动第一次：prompt eval 2570 tokens / 1583 ms
后续请求：    prompt eval   11 tokens /   99 ms
```
两个不同问题的**公共前缀 2649 字符**（静态模板全命中），见
`tests/test_slm_hardening.py::test_variable_part_always_goes_last`。

## 5. 精度实测（本项目语料）

工具：`scripts/bench_planner_model.py` —— 拿 `tests/corpus/intent_corpus.jsonl` 里
**标注"必须交回模型"**的用例真跑一遍，量意图/性质/槽位准确率、输出可用率与延迟。

```bash
# 先跑云端基线，再跑本地模型，最后对比
backend/.venv/bin/python scripts/bench_planner_model.py --provider deepseek --model <模型> --out /tmp/cloud.json
backend/.venv/bin/python scripts/bench_planner_model.py --provider ollama --model <本地模型> --out /tmp/slm.json
backend/.venv/bin/python scripts/bench_planner_model.py --compare /tmp/cloud.json /tmp/slm.json
```

> **先跑基线再换模型**：没有对照的绝对分数说明不了任何事。

### 5.1 实测结果（**全量语料 137 条**，两边跑同一批用例）

测试条件（**必须一起看**）：Qwen3.5-2B Q4_K_M（Ollama 派生模型）、Apple M5、
串行、`--routes llm,either,fastpath`（语料全量 192 条里除 phrasings 外的全部路由用例）。

| 配置 | 意图准确率 | 槽位准确率 | 硬失败 | 假接管(红线) |
|---|---|---|---|---|
| **云端 dsv4f（对照组，必跑）** | **93/113 (82.3%)** | 62/88 (70.5%) | 0 | 0 |
| 本地 Qwen3.5-2B（本地模型档） | **77/113 (68.1%)** | 56/88 (63.6%) | 0 | 0 |

分意图看差距集中在哪：

| intent | 云端 | 本地 |
|---|---|---|
| general（53 条） | 52/53 (98.1%) | **50/53 (94.3%)** |
| schedule（9 条） | 9/9 (100%) | 7/9 (77.8%) |
| ticket（12 条） | 9/12 (75.0%) | 7/12 (58.3%) |
| station（8 条） | 6/8 (75.0%) | **2/8 (25.0%)** |
| rail_line（6 条） | 5/6 (83.3%) | 3/6 (50.0%) |
| news（11 条） | 6/11 (54.5%) | 6/11 (54.5%) |
| **emu_routing（13 条）** | 5/13 (38.5%) | **1/13 (7.7%)** |

四点结论：

1. **本地模型达到云端约 83% 的水平**（意图 68.1% vs 82.3%，槽位 63.6% vs 70.5%）。
   作为"没有 Key 时的兜底"是站得住的。
2. **差距几乎全在 `emu_routing`**（交路/担当车组/车型）：本地 1/13，云端也只有 5/13。
   这一类**连云端都判不准**，说明它是任务本身的难点（问法里常常没有"交路"这种字面信号），
   不是"小模型特有的短板"。`station` 同理（25% vs 75%）。
3. **问题性质（realtime/knowledge/mixed）两边都不高**，本地约五成 —— 这个字段只影响
   "回答时允不允许用模型自身知识"，判错的代价远小于意图判错。
4. **⚠️ 小样本会骗人**：同一套代码在 30 条分层抽样上量出的是"意图 52.6% vs 89.5%"，
   在 137 条全量上是"68.1% vs 82.3%"。19 条里每对/错一条就是 ±5%，
   而且我**正是拿那个小样本调的模板**——这属于典型的过拟合风险。
   **调模型/提示词时请跑全量**（`--routes llm,either,fastpath`），别信几十条的结论。

### 5.2 本地模型档到底做了什么（按实测收益排序）

| 改动 | 效果 |
|---|---|
| 1. 「模板 + 算例」代替 JSON Schema 原文 | 意图 21% → 58%、槽位 0% → 50%（30 条样本） |
| 2. **修好对话模板与停止符**（见 §3.3） | 从"不停生成 / 整轮超时"变成可用 |
| 3. **关掉思考**（`reasoning_effort`，见 §3.4） | 决策从 60s 超时 → **0.9s** |
| 4. 变量拼到提示词末尾（前缀缓存） | 后续请求 prefill 从 ~2600 token 降到几十 |
| 5. 给 emu_routing/station 补算例 | 意图 52.6% → 73.7%（30 条样本），`emu_routing` 0/4 → 2/4 |

**这五条里只有第 1 条是"提示词工程"，2–4 条都是"让本地推理真的按预期工作"。**
换句话说：本地化的主要工作量不在调模型，在把推理服务的坑填平。

### 5.3 端到端：整条链路真的跑得起来

规划与生成**都用本地 2B 模型**（`llm={"provider":"ollama","model":"railfan-slm"}`），
走完整编排（`orchestrator.run_stream`）：

```
问题：为什么高铁要叫复兴号？
  intent=general   planner=llm-merged   degraded=False   answer_done=True   truncated=False
  思考 0 字 / 答案 768 字
  工具：web.search: ok
```

它自己**规划出了 web.search**（`planner=llm-merged`，不是快路径兜的），
再把检索到的网页事实组织成 768 字的分点中文回答并带来源编号 ——
格式、接地、来源标注都对。**唯一的代价是慢**：这一轮 92 秒
（其中大头是 768 字的生成 + 联网检索，不是决策）。

所以结论是"**能用，但慢**"，而不是"跑不起来"。
真要日常用，建议**本地决策 + 云端生成**：决策只需几十个 token、1 秒；
生成要几百个 token，本地 2B 会很吃力，而那正是云端最擅长、也最便宜的部分。

### 5.3 根因：小模型会把 JSON Schema **照抄回来**

第一轮 19 条**全部**走了兜底路径（合并调用被判定输出非法），槽位 20 个全空。
打印模型原话就很清楚了：

```
输入：明天北京到上海还有票吗？
输出：{"type": "object", "properties": {"intent": "查询余票", "question_type": "realtime",
       "location": {}, "target": "G1", "time": {}, "direction": "北京→上海", "extra": {}}}
```

它把 **schema 骨架当成了答案模板**：`type`/`properties` 原样保留，
`intent` 填了中文描述而不是枚举值 `ticket`。换成"模板 + 两个算例"（其中一个必须是
**省略句**，用来钉住多轮继承）后，同一批用例立刻正常。

**这是"小模型不行"与"我们的提示词不适合小模型"的区别。** 云端模型能从 schema
描述里推断出"要生成实例"，2B 模型不会 —— 所以 `LLM_STRUCTURED_COMPACT_PROMPT`
不是可选的调优，而是本地化的前置条件。

> 顺带一提：本次实测中**约束解码并没有被用到**（0 次格式失败）——
> 提示词修好之后，小模型的 JSON 是合法的，错的是**语义**。
> 约束解码解决的是"输出不是 JSON"，不是"判错意图"，两者不能互相替代。
> 但格式失败一旦发生就是硬失败（整轮降级），所以 `LLM_STRUCTURED_JSON_SCHEMA`
> 仍然建议打开。


## 6. 已知的坑

### 6.1 Ollama 的线上 tag 默认 `num_ctx=4096`
见 §3.2。必须派生一个更大的模型，别直接用 `ollama pull` 下来的 tag。

### 6.2 小模型会"格式对、值瞎填"
本项目决策层对**输出非法 intent** 已有一层保护：合并调用返回的 `intent` 不在枚举里时，
自动回退到"意图 + 槽位"两次更简单的调用（见 `planner.decide`）。
实测小模型最典型的两种失败是**把 JSON 写成了散文**（→ `LLMOutputInvalid` → 回退）
和**字段名对但取值自创**（→ 枚举校验 → 回退）。

### 6.3 思考模式必须关，且"关的写法"因服务而异
Qwen3.5-2B 的思考倾向很重（AA 跑完一次评测用了约 390M 输出 token，是同族最多的）。
决策任务只需要几十个 token 的 JSON，**务必关掉**（`LLM_STRUCTURED_NO_THINK=true`，
Ollama 上由预设自动换成 `reasoning_effort: none`，见 §3.4）。
不关的后果在本地是"整轮 60 秒超时 + 正文为空"，不是"慢一点"。

### 6.4 偶发的一次 60 秒超时
全量语料实测里仍会看到个别请求 p95 到 60s（客户端 `LLM_TIMEOUT_S` 默认 60）。
现象是 SDK 自动重试一次后成功，结果不受影响，但那一轮体验很差。
本地推理给一个更大的超时（或调小 `max_tokens`）会稳妥些 —— 这一条目前**没有根治**，
先如实记在这里。

## 7. 与"确定性快路径 / 规则排版"的分工

这三层是互补的，本地模型只占中间一层：

| 层 | 覆盖 | 成本 |
|---|---|---|
| 确定性快路径（正则 + 站点库） | 单轮问法 **68/90 (76%)**，多轮 11/23 | **0 次模型调用** |
| 本地小模型（决策器） | 快路径吃不下的那部分 | 1 次结构化调用（几十 token） |
| 确定性规则排版（`LLM_FALLBACK_RENDER`） | 模型不可用但检索已完成时 | 0 次模型调用 |

也就是说：**本地模型只需要负责 1/4 的问法**，而且每个只有几十个 token 的输出。
这也是"2B 模型够用"的底气来源 —— 快路径已经把最容易的部分拿走了。

## 8. 相关文件

| 文件 | 作用 |
|---|---|
| `scripts/setup_local_model.sh` | 一键：装 Ollama → 拉模型 → 派生 num_ctx → 冒烟 → 写 `.env` |
| `scripts/bench_planner_model.py` | 拿语料基准化 planner 用的模型（含两份结果对比） |
| `backend/tests/test_slm_hardening.py` | 本地小模型加固回归（JSON 宽容解析 / 输出不可用分层 / 约束解码开关） |
| `backend/app/llm/client.py` | `LLMOutputInvalid`、约束解码下发、参数降级阶梯 |
