# 本地小模型（8G 内存也能跑）

给"不想买 API Key / 机器只有 8G 内存"的用户一条**零成本、可离线**的路。
本文只讲本地模型这一条路；云端多供应商见 [`run.md`](run.md)。

```bash
bash scripts/setup_local_model.sh --write   # 装 Ollama → 拉模型 → 冒烟 → 写 .env
```

## 0. 一句话结论

| 问题 | 结论 |
|---|---|
| 用哪个模型 | **Qwen3.5-2B（Q4_K_M）** —— 8G 内存 + 只当决策器的场景 |
| 为什么不是 Qwen3-1.7B | 已被 Qwen3.5 小尺寸系列换代（2026-03 开源），同尺寸更强、上下文更长、KV 更省 |
| 本地模型负责什么 | **只当"决策器"**：意图分类 + 槽位抽取，输出几十个 token |
| 答案生成怎么办 | 继续用云端模型；没有云端时走已有的**确定性规则排版**（`LLM_FALLBACK_RENDER`） |
| 能不能完全离线 | 不能（12306/rail.re 数据本身要联网）。离线说的是**不再依赖付费 API** |
| 实测能不能用 | 意图 **68.1%** / 槽位 **63.6%**（云端对照 82.3% / 70.5%，全量语料 137 条）—— 见 §5 |

> **要连"生成"也本地（API 花费恒为 0）、且设备是 16G 旗舰？那这一页的模型档位要改。**
> 结论是 **Qwen3.5-4B**：192 条语料下意图 **81.4%**（追平云端 82.3%）、槽位 **73.9%**（反超云端 70.5%），
> 而 2B 掉 13 个点。完整参数、量化阶梯与全部实测见 **§8 手机端甜点位**。

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

## 8. 手机端甜点位（8 Elite Gen 5 / 16 GB，**生成也本地**）

上面 §1–§7 是"**8G 内存 + 本地只当决策器**"的场景。如果要求**连生成也本地、API 花费恒为 0**，
结论会变：内存不再是约束，**解码速度**成为唯一约束，而模型档位要往上走。

一句话：**4B 才是"免费又不掉效果"的档位；2B 是保险，不是甜点。**

### 8.1 为什么不是 2B（192 条语料，同一批用例）

| | **4B** | 2B | 云端 dsv4f |
|---|---|---|---|
| 意图准确率 | **81.4%**（92/113） | 68.1%（77/113） | 82.3% |
| 问题性质 | **85.8%** | 79.6% | — |
| 槽位准确率 | **73.9%** | 64.8% | 70.5% |
| 假接管（红线） | 0 | 0 | 0 |
| p50 延迟（M5） | 2.83 s | 2.03 s | — |

**4B 意图追平云端、槽位反超云端。** 分意图看，4B 在**每一个类别**上都赢：

| 意图 | 4B | 2B |
|---|---|---|
| emu_routing | **53.8%** | **7.7%** |
| schedule | 100% | 77.8% |
| ticket | 75.0% | 58.3% |
| general | 100% | 94.3% |
| station | 50.0% | 25.0% |
| news / rail_line / photo_spot | 54.5 / 50.0 / 100 | 54.5 / 50.0 / 100 |

延迟代价比想象的小：**4B 只比 2B 慢 1.4×，不是 2.2×** —— 因为规划是
**prefill 主导**（~1400 token 进 / ~44 token 出），模型大小对它的影响远小于长文本生成。

> ⚠️ 早期用 **4 条生成案例**得出过"4B 没赢、2B 够用"的结论，被 192 条语料推翻。
> 小样本上的结论不要写进方案。

### 8.2 解码是**纯带宽受限**的（实测比值）

| 模型 | 权重 | M5 decode（Ollama） | 相对 |
|---|---|---|---|
| 2B Q4_K_M | 1.19 GB | 72.5 tok/s | 1.000× |
| 4B Q4_K_M | 2.55 GB | 33.0 tok/s | **0.455×** |

权重比 1.19/2.55 = **0.467**，速度比 **0.455** —— 两者相等。
所以**速度可以按权重字节数直接外推**，不需要猜架构。

外推锚点用 Qualcomm 公开的真机数：[Qwen3.5-2B / q4_0 / Snapdragon 8 Elite / ctx 512 = 38.6 tok/s](https://huggingface.co/qualcomm/Qwen3.5-2B/commit/bd885dfceb1a1f45564aef4cd69e183085ea9a24)。
→ 8 Elite Gen 5 上 **2B ≈ 42–50 tok/s、4B ≈ 19–23 tok/s**（Gen 5 的内存带宽提升是厂商口径，未核实）。

### 8.3 量化：**不要选 Q4_0**

4B 的实测文件大小：

| 量化 | GiB | 相对 Q4_K_M |
|---|---|---|
| Q3_K_M | 2.14 | 0.837× |
| **IQ4_XS** | **2.31** | **0.904×** |
| Q4_0 | 2.41 | 0.942× |
| **Q4_K_M** | **2.55** | 1.000× |

- **Q4_0 只小 6%，而 IQ4_XS 比它更小且质量更好** —— Q4_0 没有胜算。
- 常被引用的"Q4_0 有 ARM 重排 kernel（`Q4_0_4x4/8x8`）"是**CPU 侧**的优化；
  用 `-ngl 99` 全卸载到 Adreno 时这个理由不成立。
- 2B 上更极端：Q4_0（1.21 GB）比 Q4_K_M（1.19 GB）**还大**。
- 默认选 **Q4_K_M**；要抠那 10% 的速度选 **IQ4_XS**，不要选 Q4_0。

### 8.4 运行时：llama-server 比 Ollama 快 15%

| 运行时 | 4B Q4_K_M decode（M5，同权重同提示词） |
|---|---|
| Ollama | 33.0 tok/s |
| **llama-server** | **38.0 tok/s** |

**换运行时是零成本、零质量损失的 15%。** 而且 `--cache-reuse`、`--reasoning-budget`
这些开关 Ollama 根本不暴露。

### 8.5 推测解码：三种配置**全部负收益**（实测，撤回）

| 配置 | 提取型-余票 | 提取型-站序 | 叙述型-知识 | 平均 | 相对基线 |
|---|---|---|---|---|---|
| 基线（无推测） | 37.6 | 38.4 | 38.0 | **38.0 tok/s** | 1.00× |
| `--spec-type ngram-simple` | 29.1 | 22.4 | 17.7 | 23.1 | **−39%** |
| 0.8B 草稿（草稿在 GPU） | 19.6 | 17.1 | 12.2 | 16.3 | **−57%** |
| 0.8B 草稿（草稿在 CPU） | 11.1 | 11.6 | 10.1 | 10.9 | **−71%** |

两个关键读数：

1. **ngram 的接受率只有 18–21%**，即使在"逐字引用车次号"的用例上。
   原因是答案是**续写**而不是**复制**：提示词里是 `- [ticket_query] G1 次 北京南→上海虹桥 09:01 开`，
   模型写的是 `G1 次列车 09:00 开` —— 车次号一样，但前后 token 不一样，
   12-token 的查找上下文匹配不上。
2. **草稿模型的接受率高达 43–84%，但速度掉一半。** 那 84% 是最有价值的一条数据：
   它说明瓶颈不在"猜得准不准"，而在**每一步验证的开销**。推测解码省的是权重读取次数，
   但批处理验证的开销把省下的全吃回去还倒欠。

> 结论：**关闭推测解码**。换到 Adreno 方向理论上可能不同，但要爬出 40% 以上的坑，不值得拿方案去赌。

### 8.6 参数与时间账

```bash
llama-server -m Qwen3.5-4B-Q4_K_M.gguf \
  -c 8192 -ngl 99 -fa on -t 4 -tb 6 -b 2048 -ub 512 \
  --cache-reuse 256 --jinja --host 127.0.0.1 --port 8081
```

```bash
LLM_PROVIDER=ondevice
LLM_PROVIDERS={"ondevice":{"type":"openai","base_url":"http://127.0.0.1:8081/v1","api_key":"local","model":"Qwen3.5-4B-Q4_K_M","no_think_body":{"reasoning_effort":"none"}}}
LLM_CONTEXT_TOKENS=8192
LLM_MAX_TOKENS=192
LLM_TIMEOUT_S=90
LLM_STRUCTURED_JSON_SCHEMA=true
LLM_STRUCTURED_COMPACT_PROMPT=true
LLM_STRUCTURED_NO_THINK=true
LLM_FALLBACK_RENDER=true
FACT_MAX_ENTRIES=8
WEB_SEARCH_FETCH_CHARS=1000
```

一键产出上面这份配置：`bash scripts/setup_local_model.sh --target=android`

**输出上限为什么是 192**：实测 4B 的合格答案用 **143–183** token；
放到 224 会把目标顶到 12.7 s，192 落在 **10.3 s**（prefill 1.2 s + decode 192/21 ≈ 9.1 s）。

**`FACT_MAX_ENTRIES=8` 为什么必要**：实测给 4B 灌 15 条候选车次时，它一边列出
"G1 二等座余 12 张"，一边在结论里写"明天上午没有票"——自相矛盾。
根因**不是"没思考"**（那是加思考预算的思路，会吃掉本就紧张的 token），
而是**候选条数超出了小模型的注意力容量**。收敛条数同时降 prefill，是少见的双赢。
被丢弃的条数会**显式写进 prompt** 并禁止全集表述（见 `test_cost_governance.py`
的 `test_fact_injection_entry_limit`）。

**整体体验的形状**（不是"每次都等 14 秒"）：

| 层 | 时间 | 覆盖 |
|---|---|---|
| 确定性快路径（0 次模型调用） | **<0.5 s** | 语料 41% / 实测单轮 68% |
| 规划（4B，不思考） | 3–4 s | 剩余 |
| 生成（4B） | 8–10 s | 全部 |

### 8.7 还没验的

- **192 上限在真实问题分布下的截断率** —— 需要跑真实问题集，4 条案例说明不了。
- **8 Elite Gen 5 的真实带宽**：上面所有设备端数字都是从 8 Elite（上一代）+ 厂商口径外推的。
  必须在真机上量一次 `decode tok/s` 与常驻 footprint。
- **Adreno 上推测解码是否仍为负收益** —— 本机结论是 M5/Metal。

## 9. 设备端运行时管理（多档共存 / 调参 / 发热）

### 9.1 多档共存：换模型不再需要"删掉再下载"

调试时最常用的动作是**同一台设备上对比 2B / 4B**。此前做不到：

| 旧行为 | 后果 |
|---|---|
| `find_model()` 取字母序第一个 | 2B 永远排在 4B 前面，推两个进去也只会用 2B |
| `delete_model()` 一次删光全部 `*.gguf` | 想换档只能"删掉再下"（0.5–2.6 GB） |
| 没有"选中哪个"的概念 | 对比一次十几分钟，等于把对比评测变成做不了的事 |

现在：

- `list_models()` 列出全部已下载档位（**按体积降序**，大模型排前面），每项带
  `size_mb / dir / active / warning`；
- 选中状态落盘在 `data_dir/.selected_model`（**只存文件名**，不存绝对路径 ——
  目录可能变）。`find_model()` 的优先级是
  `LOCAL_MODEL_PATH` 环境变量 → 选中的 → 字母序第一个。
  **没选过时的行为与加入多档之前完全一致**，不会因为这次改动换掉任何人正在用的模型；
- `POST /api/local-model/select {"name": "..."}` 切换（`stop()` → 落盘 → `start(wait=True)`）；
- `POST /api/local-model/delete {"name": "..."}` 只删那一个；
  `{"all": true}` 才全删。**两样都不给则 400** —— 多档之后"无参 = 删光"是一条
  会顺手清掉用户好几个 GB 的默认行为。

**安全边界**：`name` 一律经 `_safe_model_name()` 收成纯文件名（拒绝 `../`、绝对路径、
非 `.gguf`），且必须出现在 `list_models()` 里 —— 否则这就是一条任意文件删除通道。
删掉"当前使用"的那一档时会自动切到剩下体积最大的一个并重启，不会把用户留在
"没有模型、服务也停了"却以为只是清了一档的状态。

### 9.2 运行时可调参数（`ngl` / `threads` / `ctx`）

三个都能在设置页改、**都会落盘**（`data_dir/.tune.json`）。落盘不是洁癖：
第一版只放内存，实测被咬 —— 用户设成 `ngl=0`、服务也确实用 0 重启了，但 App 随后
被系统回收重开，覆盖值没了又回到默认，于是"我明明设成 0 了"和"诊断里显示 99"**同时为真**。

| 参数 | 默认 | 说明 |
|---|---|---|
| `ngl` | **0（纯 CPU）** | GPU 卸载层数。真机对照见 §8 / `docs/pending-fixes.md` P3.8：**在这台机器上是亏的**（decode −24%）。注意 2B 只有 24 层，`-ngl 32` 已经等于全卸载，"部分卸载更慢"是个误解 |
| `threads` | 4 | 设备是 2 大核 + 6 性能核共 8 核，默认**只用了一半**。decode 有效带宽仅 ~18 GB/s（机器约 68–77 GB/s），说明既没打满带宽也没打满算力 —— 这一项嫌疑最大，**值得实测** |
| `ctx` | 8192 | 必须与 `LLM_CONTEXT_TOKENS` 一致，否则超出的上下文被静默丢弃 |

每次启动都会往 `llama-server.log` 写一条带参数的分隔行
（`=========== 启动 <时间> ngl=<N> ctx=<C> t=<T> ===========`）。
**这是硬要求**：那份日志是跨次累积的，而 llama.cpp 自己不记录启动参数 ——
实测为此卡了一整轮（同一份日志里有 38.9 与 5.23 tok/s 两条 prefill，却分不清各是什么配置）。

### 9.3 发热：常驻进程必须被说出来

llama-server 是**常驻进程**。只要设备上下过模型，App 每次启动都会把它拉起来
（`setup()` → `start(wait=False)`），之后它一直占着内存与 CPU 线程。手机没有风扇，
散热全靠机身，表现是**机身持续温热 + 掉电明显变快**，而用户不会把这两件事联系起来 ——
他只会觉得"这个 App 让手机发烫"，然后去别的地方找原因。

因此有两处提醒，都是**可操作的**而不是装饰文案：

1. **设置页卡片**：只要服务在跑就显示一条说明；连续运行 **≥10 分钟**后转成警告色
   并直接给一个「立即停用」按钮。
2. **首页引导条**：在**回到前台时**（`visibilitychange`）刷新状态，若已运行 ≥10 分钟
   就提示一次。为什么必须挂在"回到前台"而不是启动时：App 一启动就把它拉起来了，
   所以"刚启动"永远显示运行 0 分钟，提醒永远不会触发；而用户切回来的时候
   它可能已经跑了半小时。

`status()` 里的 `uptime_s` 就是为这条提醒服务的（子进程拉起时刻到现在的秒数）。
停用（`POST /api/local-model/stop`）**不删文件**：模型留在手机上不占运行资源，
下次要用点一下「启用」即可（设备端加载 1–3.5 s）。真正腾空间才需要删除。

### 9.4 用量计数：流式必须**主动索取** usage

界面底部的「累计 Token」曾长期显示 0，两个原因叠在一起：

1. **流式从来没要过 usage。** OpenAI 兼容规范下流式响应**默认不下发** usage
   （末块只有一个空的 `choices`），必须显式传 `stream_options: {"include_usage": true}`。
   那个参数此前只出现在"可失败参数"的降级表里，**没有任何地方真的下发它**。
   现在由 `_kwargs_for(stream=True)` 下发，上游不认时走既有的降级阶梯丢弃重试；
   个别网关用 5xx 拒绝、阶梯认不出来时，可用 `LLM_STREAM_USAGE=false` 关掉。
2. **它是个全局计数器。** `store.totalTokens()` 现在改成**按当前对话的消息求和**：
   新对话自然归零，删消息 / 编辑重发之后数字跟着变（重算而非累加，天然自愈）。
   按对话求和的前提正是第 1 条 —— 每条消息的 `meta.usage` 得有真数。

## 10. 相关文件

| 文件 | 作用 |
|---|---|
| `scripts/setup_local_model.sh` | 一键：装 Ollama → 拉模型 → 派生 num_ctx → 冒烟 → 写 `.env`；`--target=android` 只打印手机端甜点位配置 |
| `scripts/bench_planner_model.py` | 拿语料基准化 planner 用的模型（含两份结果对比） |
| `backend/app/local_inference.py` | 设备端推理：下载 / 多档选中 / 参数落盘 / 诊断导出 / 发热 uptime |
| `backend/app/api/local_model.py` | 上述能力的 HTTP 面（含 `select` 与需要显式范围的 `delete`） |
| `backend/tests/test_local_inference.py` | 无网络回归：SSRF 面、下载校验、多档选中与删除的边界 |
| `backend/tests/test_slm_hardening.py` | 本地小模型加固回归（JSON 宽容解析 / 输出不可用分层 / 约束解码开关） |
| `backend/tests/test_cost_governance.py` | 含 `fact_max_entries` 注入条数上限的回归（默认不变 + 裁剪必须声明） |
| `backend/app/llm/client.py` | `LLMOutputInvalid`、约束解码下发、参数降级阶梯、流式 usage 索取 |
| `backend/app/pipeline/generate.py` | 生成层 prompt 组装、事实注入与条数上限 |
