# 本地小模型（8G 内存也能跑）

给"不想买 API Key / 机器只有 8G 内存"的用户一条**零成本、可离线**的路。
本文只讲本地模型这一条路；云端多供应商见 [`run.md`](run.md)。

```bash
bash scripts/setup_local_model.sh --write   # 装 Ollama → 拉模型 → 冒烟 → 写 .env
```

## 0. 一句话结论

| 问题 | 结论 |
|---|---|
| 用哪个模型 | **Qwen3.5-2B（Q4_K_M）** —— 8G 内存下的首选 |
| 为什么不是 Qwen3-1.7B | 已被 Qwen3.5-2B 换代（2026-03 开源），同尺寸更强、上下文更长、KV 更省 |
| MiniCPM5-2B 呢 | 它 Agent 指标确实更强，但 **KV cache 贵 3.5 倍**；上下文开到 16k 以上时内存明显更差。只跑 4–8k 时两者差不多，那时它也完全可用 |
| 本地模型负责什么 | **只当"决策器"**：意图分类 + 槽位抽取，输出几十个 token |
| 答案生成怎么办 | 继续用云端模型；没有云端时走已有的**确定性规则排版**（`LLM_FALLBACK_RENDER`） |
| 能不能完全离线 | 不能（12306/rail.re 数据本身要联网）。离线说的是**不再依赖付费 API** |

**别把本地小模型当"助手"用。** 这台机器上实测：它当决策器勉强合格（见 §5），
当生成器写一段通顺的中文铁路分析则会明显不如云端 —— 而"生成"恰好是它最贵、
最慢的一步（输出几百个 token）。**分工**才是对的用法。

## 1. 选型对比

| 模型 | 参数 | Ollama Q4 体积 | KV/Token | 协议 | 已知问题 |
|---|---|---|---|---|---|
| **Qwen3.5-2B** | 2.27B，24 层，**仅 6 层全注意力** | 1.81 GiB（含视觉塔）<br>纯文本 GGUF 1.19 GiB | **12 KiB** | Apache-2.0 | 思考倾向重（见 §6.3） |
| MiniCPM5-2B | 2.52B，42 层全注意力 | 1.49 GiB | 42 KiB | Apache-2.0 | Ollama 下原生工具调用解析不出来（[issue #18483](https://github.com/ollama/ollama/issues/18483)，修复 PR 未合并） |
| Qwen3.5-4B | 4B 级 | ~2.5 GiB | 同结构 | Apache-2.0 | 8G 机器上留给系统的余量偏紧 |

KV/Token 的算法（按各家 `config.json` 算，非实测）：
`2(K+V) × 全注意力层数 × KV 头数 × head_dim × 2 字节`。
Qwen3.5-2B 是 `2×6×2×256×2 = 12 KiB`；MiniCPM5-2B 是 `2×42×2×128×2 = 42 KiB`。
Qwen3.5 只对 1/4 的层做全注意力（`full_attention_interval: 4`），其余是线性注意力
（状态大小固定、不随序列增长）—— 这是它长上下文便宜 3.5 倍的**结构性原因**，
不是调参能追上的差距。

来源：Qwen3.5-2B [config.json](https://modelscope.cn/api/v1/models/Qwen/Qwen3.5-2B/repo?Revision=master&FilePath=config.json)、
MiniCPM5-2B [config.json](https://modelscope.cn/api/v1/models/OpenBMB/MiniCPM5-2B/repo?Revision=master&FilePath=config.json)、
[MiniCPM5-2B 官方 GGUF 仓库](https://modelscope.cn/models/openbmb/MiniCPM5-2B-GGUF)。

**⚠️ 权重体积反直觉**：Ollama 上 `qwen3.5:2b-q4_K_M` 的模型层是 **1855 MiB**，
比 MiniCPM5-2B 的 1490 MiB **更大** —— 因为 Qwen3.5-2B 是原生多模态，标签里
把视觉塔一起打包了（纯文本 GGUF 只有 1221 MiB）。所以"谁更省内存"**取决于上下文长度**：

| 上下文 | Qwen3.5-2B | MiniCPM5-2B | 谁省 |
|---|---|---|---|
| 8k | 1.81 + 0.09 = **1.90 GiB** | 1.49 + 0.33 = **1.82 GiB** | 几乎相同（MiniCPM 略省） |
| 16k | 1.81 + 0.19 = **2.00 GiB** | 1.49 + 0.66 = **2.15 GiB** | Qwen 省 0.15 GiB |
| 32k | 1.81 + 0.38 = **2.19 GiB** | 1.49 + 1.31 = **2.80 GiB** | **Qwen 省 0.6 GiB** |

本项目的事实块（车站大屏、逐站时刻、Markdown 表格）让生成阶段经常要 8–16k 上下文，
所以按"够用的上下文"算，**Qwen3.5-2B 更省**；而如果只跑 4–8k，两者基本一样 ——
那种情况下 MiniCPM5-2B 也完全可用（它的 Agent 指标还更强）。

> 若改用 llama.cpp + unsloth 的纯文本 GGUF，Qwen3.5-2B 的权重是 1221 MiB，
> 8k 总计约 1.3 GiB —— 比走 Ollama 标签更省。

**关于"哪个更聪明"**：Artificial Analysis 的 Intelligence Index 给 MiniCPM5-2B 15 分、
Qwen3.5-2B 16 分（[来源](https://artificialanalysis.ai/articles/openbmb-releases-minicpm5-2b)），
但两者是**不同版本的 index，不能直接相减** —— 只能说"同一档次"。
MiniCPM5-2B 的强项偏 Agent/工具调用（GDPval Elo 831）；当"输出一个 JSON"的决策器时，
这点优势不足以抵掉 3.5 倍的 KV 开销。

> ⚠️ 引用小模型分数务必带榜单版本。同一模型 AA v4.1.1 报 23、v4.2 报 15、v4.3 报 14，
> 而厂商宣传用的是 23。

## 2. 8G 内存预算

| 项 | MiniCPM5-2B @16k | Qwen3.5-2B @16k |
|---|---|---|
| 权重（Q4_K_M） | 1.45 GiB | 1.19 GiB |
| KV cache | 672 MiB | 192 MiB |
| 运行时+框架 | ~0.3 GiB | ~0.3 GiB |
| **小计** | **~2.4 GiB** | **~1.7 GiB** |

8G 机器上跑服务端 + 浏览器 + 系统，余量是够的（这是"8G 能跑 2B"的字面依据）。
但有三条实测警告：

1. **论文实测的反面数据**：Qwen2.5-1.5B 在 CPU 上占 **7,960 MiB**、单次 30.8 秒
   （[arXiv 2609.07370](https://arxiv.org/abs/2609.07370)）。那是 PyTorch 路线的开销，
   换成 llama.cpp/Ollama 的量化路线会低得多 —— 但**"参数量小 ≠ 内存小"**这条要记住。
2. **Ollama runner 内存泄漏**：匿名内存随请求线性增长 5–12 MiB 且不释放，
   `KEEP_ALIVE=-1` 常驻 5.5 小时可涨到 **9.35 GiB**，而且 **RSS 看不到**（被换出到 swap）
   —— [issue #18106](https://github.com/ollama/ollama/issues/18106)。长期跑要设 `keep_alive`。
3. **上下文别贪**：4G 内存机器上跑 2B 会 swap，速度掉 5–10 倍；8G 是实际下限。

## 3. 必做的三个设置

`scripts/setup_local_model.sh --write` 会把这些写进 `.env`：

| 变量 | 值 | 为什么 |
|---|---|---|
| `LLM_PROVIDER` / `LLM_MODEL` | `ollama` / 本地模型名 | 走本机 11434，不需要 Key |
| `LLM_STRUCTURED_JSON_SCHEMA` | **`true`** | **最关键的开关**，见下 |
| `LLM_STRUCTURED_NO_THINK` | `true` | 决策任务的思考 token 纯是延迟 |
| `LLM_CONTEXT_TOKENS` | 与本地 `num_ctx` 一致 | 对不齐时超出部分**被静默丢弃**，比报错难查 |

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

Ollama 上 `openbmb/minicpm5-2b:2b` 的默认 `num_ctx` 只有 **4096**。本项目注入的
事实块（车站大屏、逐站时刻、Markdown 表格）经常远超 4k，**超出的部分会被静默丢掉**
—— 表现为"模型答得头头是道但少了一半数据"，比报错难查得多。
`setup_local_model.sh` 因此会用 Modelfile 派生一个 `num_ctx=16384` 的本地模型，
而不是直接用线上 tag。

## 4. 实测：延迟的大头是**提示词**，不是模型

在本机（Apple M5，`llama-cpp-python` 的 **CPU 参考构建**，4 线程）测 planner 的真实提示词：

| 提示词 | prompt tokens | 耗时 | completion |
|---|---|---|---|
| 完整 planner 提示词（指令 + 判定要点 + schema） | 1072 | **19.8s** | 38 tok |
| 同一任务、精简提示词 | 78 | **4.6s** | 60 tok |

**prompt 是 13.7 倍，耗时是 4.3 倍**，而输出两边都只有几十个 token。
外推到真实调用（`_merged_prompt` + system + schema ≈ 2800 tok）：**单次决策 ~54 秒**，
其中 95% 以上花在 prefill。

三条由此推出的工程结论：

1. **本地模型别用云端那套啰嗦提示词**。我们的 schema 描述写得很细（为云端模型的可读性
   服务），在本地是一条昂贵的固定开销。给本地模型配一份**精简 schema** 是最直接的提速手段。
2. **把变量放到提示词末尾**。本项目现在把"本次用户输入"夹在静态指令中间，导致
   **前缀缓存必然失效**。Ollama / `llama-server` 默认会复用公共前缀（`cache_prompt`），
   把用户输入挪到最后就能让每次决策只 prefill 几十个 token。
3. 上面的绝对耗时**不代表真机体验**：PyPI 的 `llama-cpp-python` 轮子没有 Metal/Accelerate
   加速，而 Ollama 发布的是 ARM 优化构建。请把上表当**相对量级**看，不要当基准。

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

### 5.1 实测结果（30 条按意图分层抽样，同一批用例）

测试条件（**必须一起看**）：Qwen3.5-2B Q4_K_M、`llama-cpp-python` 的 **CPU 参考构建**、
Apple M5、串行、`--limit 30`（语料 `route=llm,either`）。

| 配置 | 意图准确率 | 问题性质 | 槽位准确率 | 硬失败 | p50 延迟 |
|---|---|---|---|---|---|
| **云端 dsv4f（对照组，必跑）** | **17/19 (89.5%)** | 89.5% | 12/20 (60.0%) | 0 | 1.1s |
| 本地 Qwen3.5-2B · schema 原文进提示词 | 4/19 (21.1%) | 57.9% | **0/20 (0%)** | 0 | 54.4s |
| 本地 Qwen3.5-2B · **模板 + 算例** | **11/19 (57.9%)** | 57.9% | **10/20 (50.0%)** | 0 | **13.9s** |

三点结论：

1. **只改提示词形态，意图准确率 21% → 58%、槽位 0% → 50%、p50 延迟 54.4s → 13.9s。**
   本地化改造里性价比最高的一步不是换模型，是把"给它一份 schema"换成"给它一个算例"。
2. **但仍然明显落后云端**（意图 58% vs 90%）。放大差距的是 `emu_routing`
   （交路/担当车组，本地 0/4）和 `station`（本地 0/2）—— 这类**领域细分意图**
   靠常识猜不出来，小模型会退回到 `schedule` / `general`。
   槽位抽取反而已接近云端（50% vs 60%，且云端那 8 条里有多条只是
   `西安北` vs `西安北站` 的归一化差异）。
3. **本地模型适合当"没有 Key 时的兜底"，不适合替代云端。** 这也印证了快路径的价值：
   76% 的单轮问法根本不需要模型判断，剩下 24% 才是小模型的战场。

### 5.2 根因：小模型会把 JSON Schema **照抄回来**

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

### 6.3 思考模式是纯延迟，要关
Qwen3.5-2B 的思考倾向很重（AA 跑完一次评测用了约 390M 输出 token，是同族最多的）。
决策任务只需要几十个 token 的 JSON，务必 `LLM_STRUCTURED_NO_THINK=true`。
注意 `enable_thinking` 是**厂商约定**的字段，本地服务不一定认；不认时本项目会自动丢弃该参数。

### 6.4 MiniCPM5-2B 的 Ollama 工具调用是坏的
Ollama 下传 `tools` 时原生 XML tool call 永远解析不出来（`tool_calls: null`，
content 里出现乱码片段），根因是 XML 特殊 token 在 detokenize 时被剥掉，
[issue #18483](https://github.com/ollama/ollama/issues/18483) 至今 open。
**本项目不使用 `tools`**（只要求模型输出 JSON），所以不受影响 —— 但如果将来要接
原生工具调用，MiniCPM5 必须走 SGLang（官方推荐后端）而不是 Ollama。

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
