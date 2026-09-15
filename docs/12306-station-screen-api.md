# 12306「车站大屏」接口探测报告

> 探测时间：2026-09-14 22:0x（境内网络）
> 探测方式：直接对本机可达的 12306 域名发请求，逐参数试错（curl + Python 解析）
> 结论：**已拿到可用的官方接口**，无需登录、无需 Cookie、无签名。

---

## 一、结论摘要

| 项 | 值 |
|---|---|
| 端点 | `https://mobile.12306.cn/wxxcx/wechat/bigScreen/queryTrainByStation` |
| 方法 | **POST（必须是 POST）**，`application/x-www-form-urlencoded` |
| 鉴权 | 无需登录/签名。响应里 `noLogin:"Y"` 只表示"匿名访问"，不影响取数 |
| 入参 | `train_start_date=YYYYMMDD`、`train_station_code=电报码` |
| 出参 | 该站当日**全部到发车次**（含时刻、站台、车底、交路、里程、客运段…） |
| 实测规模 | 北京南 2026-09-15 → 502 条；上海虹桥 683 条；天津 299 条；吉林 168 条 |
| 耗时 | 单次 0.5–2 s |

### 关键踩坑

1. **GET 请求一定失败**。用 GET 会返回 `{"status":false,"errorMsg":"系统忙，请稍后重试！(M0003)"}`，
   参数怎么写都一样 —— 这是最容易误判成"接口挂了"的坑。**换成 POST form-body 立刻出数据。**
2. **不需要任何请求头**。实测去掉 UA、不带 Referer、不带 Cookie 同样返回完整数据
   （带浏览器 UA 亦可）。所以不必伪造微信/App 指纹。
3. 电报码用 12306 口径（北京南 = `VNP`、上海虹桥 = `AOH`、吉林 = `JLL`），
   非法码不报错，返回 `status:true` + 空数组。
4. 日期超出可查范围同样返回 `status:true` + 空数组（**空 ≠ 该站无车次**，要按日期窗口判断）。

### 最小可用复现

```bash
curl -s -X POST \
  -d "train_start_date=20260915&train_station_code=VNP" \
  "https://mobile.12306.cn/wxxcx/wechat/bigScreen/queryTrainByStation"
```

---

## 二、响应结构

顶层：

```json
{
  "noLogin": "Y",
  "data": [ { /* 一行 = 一条"本站到发记录" */ }, ... ],
  "now": "1789395530037",
  "errorCode": "",
  "status": true,
  "errorMsg": ""
}
```

`data[i]` 主要字段（实测全量列出，`VNP/2026-09-15` 样本）：

### 车次与时刻

| 字段 | 含义 | 示例 |
|---|---|---|
| `station_train_code` | 车次号（对外口径） | `G1` |
| `train_no` | 12306 内部车次编号 | `24000000G10L` |
| `station_name` / `station_telecode` | 本站名 / 电报码 | `北京南` / `VNP` |
| `start_station_name` / `start_station_telecode` | 始发站 | `北京南` / `VNP` |
| `end_station_name` / `end_station_telecode` | 终到站 | `上海虹桥` / `AOH` |
| `arrive_time` | **本站到达时刻**；本站为始发站时为 `----` | `09:24` |
| `start_time` | **本站发车时刻**；本站为终到站时与 `arrive_time` 相同 | `06:30` |
| `start_start_time` | 始发站发车时刻 | `21:05` |
| `end_arrive_time` | 终到站到达时刻 | `09:24` |
| `arrive_day_diff` / `start_day_diff` | 相对查询日的天数偏移（跨日车） | `1` |
| `stopover_time` | 本站停站分钟 | `0` |
| `update_arrive_time` | 实际/预计到点（`HHMM`，无则 `----`） | `0924` |
| `update_start_time` | 实际/预计发点（`HHMM`，无则 `----`） | `0600` |
| `running_time` | 全程历时（文案） | `12小时19分` |
| `distance` / `speed` | 全程里程(km) / 均速(km/h) | `1468` / `122` |
| `station_train_date` | 该记录的乘车日期 | `20260915` |
| `start_train_date` | 始发站发车日期（跨日车与上者不同） | `20260914` |
| `base_datetime` | **数据基准时刻**（快照时间，用于判断新鲜度） | `2026-09-14 21:53:39.283` |

### 站台与站序

| 字段 | 含义 | 示例 |
|---|---|---|
| `platform_no` | 站台（A/B 股道用 `、` 分隔，尾部带 `#`）；**仅本站发车车次有值** | `19A、19B#` |
| `station_no` / `display_station_no` | 站序 | `04` |

### 列车属性 / 担当

| 字段 | 含义 | 示例 |
|---|---|---|
| `train_class_code` / `train_class_name` | 列车等级 | `D` / `动车`、`高速` |
| `train_type_code` / `train_type_name` | 直通(2) / 局管(1) | `直通` |
| `service_type` | 服务类型 | `1` / `2` |
| `train_style` | 本车底标识：**型号 + 定员**（后缀是定员，见 §4.x） | `CR400BF-BS_1346` |
| `jiaolu_train_style` | **实际车底型号** | `CR400BF-S` |
| `jiaolu_corporation_code` | **担当客运段** | `上海客运段` |
| `jiaolu_dept_train` | **担当车辆段** | `上海机辆段` |
| `jiaolu_train` | **该车底整日交路**（`车次\|始发\|发\|终到\|到` 用 `#` 连接） | 见下 |
| `bureau_code` / `corporation_code` | 铁路局 | `H` / `H00` |
| `train_limit` | 定员 | `918` |
| `seat_types` | 席别掩码 | `13369344` |
| `running_fig` | 开行图解标识 | `20250105#1#1#20300303` |
| `running_notice_code` / `center_notice_code` | 广播/中心通知码（晚点、变更等） | `20260914#-29#` |
| `time_interval` | 与通知码配套的时间间隔标记 | `1` |
| `price_info` / `local_start_time` / `local_arrive_time` | 实测恒为空 | `` |

`jiaolu_train` 示例（一趟车的**全天套跑交路**，直接可用作"车底运用"答案）：

```
G1806/7|上海虹桥|09:08|郑州东|14:08#
G82/79|郑州东|14:40|上海虹桥|18:43#
G32|上海虹桥|19:25|北京南|23:49#
G1|北京南|06:30|上海虹桥|11:24#
G1956/7|上海虹桥|11:44|太原南|21:26#
G1958/5|太原南|08:49|上海虹桥|17:53#
```

> 这一条对本项目很关键：**12306 官方接口给出了"担当车底型号 + 客运段 + 车辆段 + 当日套跑交路"**，
> 是 rail.re 交路库之外的第二个来源，且覆盖面不只动车组（普速同样返回 `jiaolu_*` 字段）。
>
> ⚠️ 但注意口径差别：`jiaolu_train_style` 是**型号**（`CR400BF-S`），**不是车组号**
> （rail.re 给的 `CR400BFA-5159`）。所以"A01 G1 由哪组车担当"这类要**具体车组号**的问题，
> 本接口只能答到"型号 + 客运段"，**不能取代 rail.re**；它的价值在于
> ①交叉验证担当路局/客运段；②给出当日套跑交路；③**覆盖普速**（rail.re 只收动车组）。

### 4.x ⚠️ `train_style` 的后缀是**定员**，不是车组号（2026-09-15 复核）

`CR400BF-BS_1346` 乍看像"型号_车组号"，**实测不是**：当日北京南 483 条中，凡带 `_数字`
后缀的 121 行里有 **119 行**的后缀与 `train_limit` **完全相等**
（`CR400BF-BS_1346` ↔ 定员 1346；`CRH2E_642` ↔ 定员 642）。因此：

| 字段 | 真实含义 | 例 |
|---|---|---|
| `train_style` | **本车底**：型号 + 定员 | `CR400BF-BS_1346`（1346 是定员，不是车组） |
| `train_limit` | 定员（座位数） | `1346` |
| `jiaolu_train_style` | **交路链**上的型号（无编号） | `CR400BF-S` |

两个字段口径**并不总一致**：当日 483 条中有 **217 行**型号不同
（如 G1：`jiaolu_train_style=CR400BF-S` vs `train_style=CR400AF-A`；
G3/G7/G13 则一致）。因此工具侧**两个值都保留**
（`rolling_stock` 主值 + `rolling_stock_own` 本车底 + `capacity` 定员），
由模型如实呈现分歧，而不是静默二选一。

> **分歧已有定论（2026-09-15 交叉核对）**：用 12306 官方 `getCarDetail`
> （见下方 §4.y）与 rail.re 两个独立来源核对 G1/G3 ——
> `getCarDetail` 给 `CR400BF-A-5159`、rail.re 给 `CR400BFA-5159`（同一组，rail.re 少一个连字符），
> **两者一致**；而大屏 `train_style=CR400AF-A` 与两者都不符。
> 结论：**涉及"哪一组车"时以 `getCarDetail` / rail.re 为准**，大屏 `train_style` 仅供参考。

> 唯一反常例：`CR200J_16` 配的是定员 918 —— 那是动力集中动车组的**编组辆数**写法，
> 工具据此判定"后缀非定员"并保留原文，不误拆。

---

## 三、怎么拼出「出发屏」和「到达屏」

接口一次返回本站**全部到发记录**，出发/到达靠方向字段自行切分。实测 `VNP / 2026-09-15` 共 502 条：

| 类别 | 判据 | 条数 | 用哪个时刻 |
|---|---|---|---|
| 本站始发 | `start_station_telecode == 本站` | 243 | `start_time` |
| 本站终到 | `end_station_telecode == 本站` 且非始发 | 246 | `arrive_time` |
| 过路车 | 两者都不等于本站 | 13 | 到达用 `arrive_time`、发车用 `start_time` |

* **出发屏** = 始发 + 过路 = 256 条 —— 与实测"`platform_no` 非空"的条数（256）**完全吻合**，
  所以 `platform_no != ""` 是"本站有发车作业"的可靠标志。
* **到达屏** = 终到 + 过路 = 259 条 —— 与实测"`arrive_time != "----"`"的条数（259）一致。
* 因此：`arrive_time == "----"` ⟺ 本站为始发站。这一条足以判定方向，不用查站序表。

App 大屏上显示的「候车 / 检票中 / 停止检票」**不在接口里**，是客户端用
`start_time` + 当前时间 + `platform_no` 现算的；接口只给"预计到发时刻"（`update_*`）
和通知码（`*_notice_code`，晚点/变更用）。

---

## 四、同族接口（一并实测）

| 端点 | 方法 | 入参 | 实测结果 |
|---|---|---|---|
| `…/wechat/bigScreen/getStationAddress` | GET | `stationCode=VNP` | ✅ 站名/地址/经纬度/城市码，无需登录 |
| `…/wechat/bigScreen/queryTrainBureau` | GET | `queryDate=YYYYMMDD&trainCode=G1` | ✅ 返回担当路局（G1 → `{"bureau_code":"H","bureau_code_name":"上海"}`） |
| `…/wechat/bigScreen/queryTrainDiagram` | GET/POST | `queryDate=YYYYMMDD&trainCode=G1` | ❌ 实测 GET 返回 `M0003`，未继续试 POST |
| `…/wechat/main/conf` | GET | 无 | ❌ 返回 `"操作失败"`（需小程序侧凭证） |
| `…/wechat/ticketInfo/getStopStation` | — | — | ❌ 403（openresty 直接拒绝） |

`getStationAddress` 返回样例：

```json
{"stationCode":"VNP","cityName":"北京市","cityCode":"010","latitude":"39.86526",
 "stationName":"北京南站","stationAddress":"北京市丰台区永外大街车站路12号","longitute":"116.37852"}
```

---

## 五、限制与注意事项

1. **日期窗口有限、边界不稳定**。实测 2026-09-10 ~ 09-22（相对探测日 ±1 周）返回数据，
   更远（如 2026-09-30、2027-06-01）与更早（2025-01-01）返回**空数组**但 `status:true`。
   窗口边缘还可能出现**部分数据**（09-20 只回 131 条，相邻的 09-19/09-21 都是 500+ 条）。
   即：**"取不到"和"没有车"在响应里无法区分**，必须自己按窗口做前置校验，否则会对用户谎报"该站当日无车"。
2. **只有单日快照**，没有"历史大屏"；`update_*` 只在当日/临近时刻才有实际值。
3. **无分页、无过滤**，一次返回全天 200–700 条，客户端自行裁剪（本项目里注意别整段塞进 LLM 上下文）。
4. **反爬**：本接口当前无签名/无 Cookie，但 12306 对高频访问会限流；实测连续请求正常，
   仍建议加本地 TTL 缓存（参照 `_rt12306.py` 里 `_STOPS_CACHE` 的写法，建议 TTL 30–60 s —— 大屏数据本身约每分钟刷新一次）。
5. **口径**：`train_no` 是内部编号，与 `search.12306.cn` 返回的一致，可与现有
   `resolve_train_identity()` 打通（**本站大屏不需要先查 OD，比 leftTicket 路径更省一次请求**）。

---

## 六、备选方案（非官方，含"检票状态"）

携程/智行的接口直接返回大屏语义状态（`stationWaitingScreens` = 正在检票/候车、
`invalidWaitingScreens` = 停止检票），入参是**中文站名**而非电报码：

```
POST https://m.suanya.com/restapi/soa2/24635/getScreenStationData
  ?_fxpcqlniredt=<20位随机数>&x-traceID=<随机数>-<毫秒时间戳>-<7位随机数>
body: {"stationName":"北京南","screenFlag":0,
       "authentication":{"partnerName":"ZhiXing","source":"","platform":"APP"},
       "head":{"cid":"<同20位>","ctok":"","cver":"1005.006","lang":"01","sid":"8888",
               "syscode":"32","auth":"","xsid":"","extension":[]}}
```

来源：[zboku222/12306 · plugins/station_screen.py](https://github.com/zboku222/12306)。
**未在本机实测**，且属第三方聚合数据，时效与准确性都不如上面那个官方接口 —— 只建议作为
"要显示'正在检票'状态"时的补充，不建议作为主源。

---

## 七、接入本项目的实现（✅ 已完成，2026-09-14）

| 位置 | 内容 |
|---|---|
| `backend/app/tools/_rt12306.py` | `SCREEN_URL` / `normalize_screen_row()` / `query_station_screen_rows()`（POST + 60s TTL 缓存）/ `query_station_screen()` / `parse_jiaolu()` / `screen_window_hint()` |
| `backend/app/tools/station_screen.py` | 新工具 **`station.screen`**（方向切分、时段/车种过滤、完整性契约、空结果三义歧义处理） |
| `backend/app/tools/registry.py` | 注册（现为 16 个工具、全部 enabled） |
| `backend/app/pipeline/retrieve.py` | `_SCREEN_RE` 关键词路由 + `_screen_direction()`：命中"大屏/出发屏/到达屏/检票/晚点"时补一次车站级查询 |
| `backend/app/config.py` | `station_screen_limit`（默认 15 条） |
| `backend/tests/test_station_screen.py` | 离线层（归一化/切分/过滤/截断/空歧义）+ 联调层（POST 必需回归、真实取数） |
| `backend/tests/test_routing.py` | 路由回归：只在命中关键词时调用、时段/车种必须一起下发、knowledge 型不调 |
| `scripts/probe_station_screen.py` | 独立探针（仅标准库，可在 venv 外运行） |

### 实现中固化下来的规则（都来自实测踩坑）

1. **必须 POST**。`test_live_get_vs_post` 把"GET 恒返回 M0003 / POST 成功"锁进回归测试 ——
   若 12306 哪天改了行为，测试直接失败并提示更新本文档。
2. **空结果绝不表述成"该站当日无车"**。此时工具返回 `ok=False`，错误信息明说
   "超出可查窗口 / 电报码不存在 / 确实无车 三者无法区分"，窗口外（±7 天）再补具体原因。
3. **时段与车种过滤必须与截断同时发生**。一次返回全天 200–700 条，只取前 N 条会让
   "今天下午有哪些高铁"拿到凌晨的车（即本项目反复出问题的"截断冒充缺失"）。
   实测"北京南站大屏今天下午有哪些高铁"：全站出发 **259 趟** → 过滤后 **54 趟** → 展示 15 趟，
   并在 `ToolResult` 里如实登记 `total/showed/truncated/filters`。
4. **终到车不发"本站开"**。终到车的 `start_time` 只是 `arrive_time` 的镜像，
   只有 `direction == "through"` 的过路车才在到达屏上标注本站发车时刻（有回归用例）。
5. **只到"车底型号"，拿不到单组车号**（`CR400BF-S` ≠ `CR400BFA-5159`）——
   `station.screen` 与 rail.re 的 `emu.routing` 是互补而非替代关系；
   但它**覆盖普速**、能给**当日整日套跑交路**、能给**站台号**，这三项 rail.re 都没有。

### 常用调用

```bash
cd backend && PYTHONPATH=. .venv/bin/python -c "
import asyncio
from app.tools import registry
r = asyncio.run(registry.invoke_by_name('station.screen',
      {'station': '北京南', 'direction': '出发', 'after_time': '17:00', 'train_type': 'G', 'limit': 8}))
print(r.text); print(r.integrity_line())
"
```

回归测试：

```bash
cd backend && PYTHONPATH=. .venv/bin/python tests/test_station_screen.py
cd backend && PYTHONPATH=. .venv/bin/python tests/test_routing.py
```

---

## 附：探测原始证据

```
POST https://mobile.12306.cn/wxxcx/wechat/bigScreen/queryTrainByStation
     train_start_date=20260915 & train_station_code=VNP
→ 200, status=true, data.length=502
→ data[0] = D10 上海南→北京南 09:24 到, base_datetime=2026-09-14 21:53:39.283
→ 出发作业(platform_no 非空) 256 条, 到达作业(arrive_time 非 '----') 259 条

GET 同一 URL + 同一参数
→ 200, {"noLogin":"Y","now":"…","status":false,"errorMsg":"系统忙，请稍后重试！(M0003)"}

其它站：天津 TJP=299 / 上海虹桥 AOH=683 / 吉林 JLL=168（同为 2026-09-15）
```

### 7.x 列表默认按「当前时刻」对齐（2026-09-15）

真实车站大屏是**围绕此刻**读的：列表从最近的车次开始。此前我们按时刻从 00:00 顺排，
北京南全天 256 趟出发、15 条明细会被凌晨车次占满（下午提问等于没看到有用信息）——
**数据没错但没用**。

现在：**查询日期=今天、且用户未指定时段**时自动对齐：

- 重排为「未发车（升序）在前 → 已发车（降序）在后」，**不丢数据**（总数与截断声明照旧）；
- 文本首行标注「已按**当前时刻 HH:MM**（北京时间）对齐」；
- 已过车次逐条标注「已发车 / 已到达」，避免模型把它们当成"接下来要等的车"；
- 数据字段新增 `aligned_to_now` 与 `now`；`note` 里注明北京时刻口径；
- **用户显式给时段**（`after_time/before_time`）→ 不对齐，按他的条件办（可用 `align_now=false` 显式关闭）。

> 注意：只影响**展示顺序**，不影响筛选与统计；时段分布（上午/下午/晚上各多少趟）
> 始终按全量 `matched` 计算。
