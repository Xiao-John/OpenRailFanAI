# 结构化票价契约交接

日期：2026-10-06。已实现 Main 后端 `ticket_fare`，按用户“实现候选契约”授权追加到现有 `display_results`。块式响应与 SSE `done` 使用同一投影；票价正文继续完整交付。

## 字段

每条实际票价记录独立一个对象，不增加批量包装。最多10个不同车次、10个不同日期，沿用现有并发上限和超限拒绝规则。

| 字段 | 类型与含义 |
| --- | --- |
| `kind` / `schema_version` | `ticket_fare` / `1` |
| `status` | `success`：有效记录且席别金额完整；`partial`：记录归属明确但金额缺失或无效；`empty`：接口成功但无精确记录；`failed`：查询失败、格式无效或归属冲突 |
| `train_code` / `date` | 字符串或null；车次与乘车日期（YYYY-MM-DD）。失败项保留请求归属，不能套用其他日期 |
| `from_station` / `to_station` | 字符串或null；成功/部分结果取每条实际记录的区间，空/失败结果表示请求区间 |
| `start_time` / `arrive_time` / `duration` | 上游字符串或null，不代表实时运行状态 |
| `prices` | 席别数组，每项 `{seat, amount, currency, raw_amount}`；不提供余票字段 |
| `prices[].seat` | 原始席别名 |
| `prices[].amount` | 非负十进制字符串或null，避免二进制浮点运算；缺失不等于0 |
| `prices[].currency` | 当前12306人民币票价固定 `CNY` |
| `prices[].raw_amount` | 原始标量；非有限数及非JSON标量转为字符串，保证标准JSON |
| `sources` | 工具实际提供的来源字符串数组，无来源则空数组 |
| `fetched_at` | 查询返回时采集的UTC ISO时间；没有有效回执则null，不伪造失败采样时间 |
| `error` / `note` | 字符串或null / 字符串；失败原因、数据限制及票价不代表余票的说明 |

`empty`与`failed`的`prices`为空，到发时间与历时为null。格式或日期/车次冲突的记录不能成为成功结果，也不泄漏为其他查询的有效金额。现有票价工具继续过滤邻站扩展记录，投影不把实际行的区间改写为请求区间。

示例：

```json
{"kind":"ticket_fare","schema_version":1,"status":"success","train_code":"G1","date":"2026-10-07","from_station":"北京南","to_station":"上海虹桥","start_time":"06:30","arrive_time":"11:24","duration":"04:54","prices":[{"seat":"二等座","amount":"795.0","currency":"CNY","raw_amount":"795.0"}],"sources":["https://kyfw.12306.cn/otn/leftTicketPrice/queryAllPublicPrice"],"fetched_at":"2026-10-06T01:34:56.179071+00:00","error":null,"note":"票价不代表实时余票；到发时刻为接口区间时刻，不代表实际运行状态。"}
```

示例节选同次真实响应的一个席别，完整响应还有一等座、商务座；不要据此裁剪实际数组。

## 前端接入

前端另行添加种类解析与卡片，不从问题、邻近时刻卡片补齐缺失字段。仅在成功解析相应结构化记录后抑制对应重复正文；未知版本、格式或未接入客户端继续展示原始正文。`failed`展示错误，`empty`展示无精确记录，均不得判断无票或停运。

当前Android源码将未知种类解析为未支持结果，正文仍保留，但可能额外显示格式未支持提示；尚未接入原生结构化票价卡片，未做APK或视觉验收。

## 验证与范围

- 12项新增离线测试：字段/金额/来源、逐行区间、空/失败、缺失金额、非标准JSON数值、归属冲突、LM兼容、旧检索路径及多车次多日期的流块一致。
- 12组既有后端回归通过；离线验证不连接网络、不读私密配置、不调用子进程。
- 独立工作树真实匿名12306核验：10月7/8日，G1北京南→上海虹桥各返回一条记录，三种席别795/1272/2782元；北京→上海虹桥两日均空。逐项核对区间、时刻、金额、来源及采样时间，云模型调用0，私密文件访问0。
- 初次真实核验发现原工具采样时间为空，已修复；初次日志保留，不改写失败证据。
- 只改后端及本任务文档，未修改Android、WebUI、LM专属文件、版本、发布包或前端验收。未重启共享服务、未推送GitHub。

证据：`.ai/backend-fare-contract/2026-10-06-v1/`；独立工作树：`/Users/xylo/.codex/worktrees/backend-fare-contract/OpenRailFanAI`。
