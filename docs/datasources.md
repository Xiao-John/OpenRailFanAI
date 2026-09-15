# 数据源参考清单（RailFanAI v1 / M3.1）

以下为系统检索层已接入或可接入的数据源。每个源实现一个 `Tool`（`app/tools/base.py`），
在 `app/tools/registry.py` 注册，返回统一 `ToolResult`。

## 一、已接入并验证的数据源

| 工具名 | 数据源 | 提供内容 | 实时性 | 前置条件 |
|---|---|---|---|---|
| `station.lookup` | 12306 站点库 | 站名/拼音/电报码互查（3384 站） | 静态（可刷新） | 无 |
| **`station.screen`** | **12306「车站车次大屏」** | **某站当日全部到发车次：到发时刻/站台/终到站/车底型号/担当客运段·车辆段/当日套跑交路** | **实时**（约每分钟刷新，本地 60s 缓存） | 境内网络 + Python ≥ 3.10 |
| `ticket.query` | 12306 leftTicket | 两站间实时余票/车次列表 | **实时** | 境内网络 + Python ≥ 3.10 |
| `train.schedule` | 12306 leftTicket + queryByTrainNo | 车次实时时刻/余票/经停站（仅给车次可自动定位起止站） | **实时** | 境内网络 + Python ≥ 3.10 |
| **`emu.routing`** | **rail.re API** | **车次↔动车组担当车组交路** | **实时（含历史）** | 境内网络 + 浏览器级请求头 |
| **`rail.line`** | **jprailfan 旅客径路查询** | **线路序列 + 车站序列 + 里程（区间/累计）** | 静态（准实时） | 境内网络 |
| `railre` | rail.re 页面 | 车站信息、页面摘要 | best-effort | 同上 |
| `cnrail.map` | cnrail.geogv.org | 站名→铁路地图外链 | 静态外链 | 无 |
| `web.fetch` | 任意 URL | 网页标题与正文摘要 | 实时 | 无 |
| `web.search` | Bing 中国 / 百度 | 通用搜索兜底 | 实时 | 无（境内可访问） |
| `jprailfan` | jprailfan.com 黄河铁路网 | 客里表/电报码/拼音码 | 实时抓取 | 站点可达 |
| `t12306.search_tickets` | 自备 12306 反代 | 余票/时刻（备选路径） | 实时 | 需配置 `T12306_BASE` |

### 1.1 担当车组（交路）—— 核心能力说明

12306 公开接口**不提供**担当车组信息（只返回"高速"等列车大类），
因此"G1 今天由哪组动车组担当"这类车迷核心问题需依赖 rail.re。

**rail.re 交路 API**（`https://api.rail.re`，公开无需鉴权）：

```http
GET /train/{车次}      # 该车次历史担当车组（含日期）
GET /emu/{车组号}      # 该车组历史担当车次（含日期）
```

响应示例（`GET /train/G1`）：

```json
[
  {"date": "2026-09-13 11:24", "emu_no": "CR400BFA5054", "train_no": "G1"},
  {"date": "2026-09-11 11:24", "emu_no": "CR400BFA5159", "train_no": "G1"}
]
```

- `emu_no` 为紧凑格式（`CR400BFA5054`），工具会格式化为 `CR400BFA-5054`
- 日期为最后记录时间，用于判断"今日担当"

**关键前置条件（踩坑记录）**：
1. **必须使用浏览器级请求头**（`User-Agent` + `Accept` + `Accept-Language` + `Accept-Encoding`），
   否则主站 `rail.re` 会直接 `ReadTimeout`。实现见 `app/tools/_http.py:BROWSER_HEADERS`。
2. 主站 `rail.re` 与 API `api.rail.re` 的可达性可能不同——**API 子域通常更宽松**。
3. `Accept-Encoding` 含 `br` 时必须安装 `brotli`，否则响应无法解压（工具已做自动降级）。

### 1.3 铁路线路（径路）—— `rail.line`

12306 与 rail.re **都没有线路概念**（12306 只有车次/时刻，rail.re 只有车次↔车组），
"北京到上海走哪条线""多少公里"这类问题需要专门的径路数据。

**数据源**：黄河铁路网「中国铁路旅客径路查询」

```http
GET https://jprailfan.com/tools/dert/index.php
    ?action=shrtroute&startstat={发站}&endstat={到站}
```

响应为一张表，逐行给出：

| 线路 | 车站全名 | 车站简称 | 车站电报码 | 里程 |
|---|---|---|---|---|
| （起点为空） | 北京 | 北 | -BJP | 0km |
| 京沪线 | 北京南 | 北 | -VNP | 9km/总9km |
| 京沪高速线 | 德州东 | 德 | -DIP | 314km/总323km |

工具归一化为 `{line, station, short, telecode, km, cum_km}`，并汇总出
**线路序列**（去相邻重复）与**总里程**。

实测：`北京→上海` = **1320 km / 17 条线路 / 18 站**（客运运价里程口径）。

**踩坑记录（重要）**：
1. 该页面约 **800KB**（内嵌全国站名列表），须放宽超时（工具用 ≥30s）。
2. 站名列表与径路表结构相似，**必须先用 `车站电报码` 表头定位径路表**，
   再做 `<table>` 标签配平截取；否则会把整张站名表误解析为径路行。
3. 径路行有**恰好 5 列且电报码为 3 位大写字母**，用此可过滤噪声行。

**已知限制**：目前只支持「两站间径路」。**按线路名反查站序**（如"京沪线经过哪些站"）
需要该站的 `desgroute` 多步交互流程，暂未接入，路由会退化为 web 搜索兜底。

### 1.3-b 按线路名查站序 / 指定径路（`rail.line_stations`，2026-09-14 逆向）

**需求**：F06「京沪线经过哪些站」与 F05「北京到上海走**老京沪线**（普速）多少公里」——
`action=shrtroute`（两站间**最短径路**）只能给高铁口径（北京→上海 1320km），
无法按线路名枚举站序，也给不出既有线里程。

**接口**：同一页面的另一个动作 `action=desgroute`（**POST 表单**，多步交互）：

```http
POST https://jprailfan.com/tools/dert/index.php?action=desgroute
# step1 选起始站 → 返回 <select name=r1>：该站的出发线路
#   i=            d0=0            s0=北京            key1=提交
# step2 选线路   → 返回 <select name=s1>：**整条线的站序**（含 (●) 接算站标记）
#   i=1           d0=0            s0=北京            r1=京沪线
# step3 选终点   → 返回"指定径路"结果表（线路/车站/简称/电报码/区间里程/累计里程）
#   i=1           d0=0            s0=北京            r1=京沪线         s1=上海
```

**踩坑记录**：
1. 是 **POST**，且 `i`/`d0` 是跨步骤的隐藏状态（step1 响应里给出 `i=1`，必须回传）；
2. **下拉框嵌在结果表附近**，解析单元格前必须先剥离 `<select>…</select>`，
   否则 `option` 文本会混进"线路/车站"列（实测把整条站序字符串塞进单元格）；
3. 线路名有别名（"老京沪线"/"京沪铁路" → `京沪线`；"京沪高铁" → `京沪高速线`），
   而 `r1` 的取值就是规范线路名，标签形如 `京沪线(北京-上海)` 可解析出端点；
4. 每次要抓 **3 个约 800KB 页面**（页面内嵌全国站名表），因此结果**进程内缓存**
   （`RAIL_LINE_CACHE_TTL_S`，默认 1 小时）——实测首次 19.4s、缓存命中 0.00s。

**实测结果**（2026-09-14）：`京沪线` = **57 站**、`北京→上海 全程 1463 km`（客运运价里程，**既有线口径**）；
与 `shrtroute` 的高铁口径 1320km 形成对照 —— 两个口径**分别回答不同问题，不可混用**。

### 1.3 网页搜索 —— 引擎选型（踩坑记录）

**DuckDuckGo 在中国境内不可达**（`httpcore.ConnectTimeout`），
因此 `web.search` 改用境内可访问的引擎：

| 顺序 | 引擎 | 端点 | 解析方式 |
|---|---|---|---|
| 1（主） | Bing 中国 | `https://cn.bing.com/search?q=` | `li.b_algo` 块 + `h2>a` |
| 2（兜底） | 百度 | `https://www.baidu.com/s?wd=` | `h3>a` |

实测可达性：Bing CN ✅ / 百度 ✅ / 搜狗 ✅ / 360 ✅ / DuckDuckGo ❌。

**另一个坑**：`httpx` 的部分异常（如 `ConnectTimeout`）`str()` 为空串，
直接 `f"抓取失败: {e}"` 会得到空白错误信息。统一用
`app/tools/_http.py:format_error(e)` 格式化（带上异常类型名）。

### 1.2 12306 实时查询（MCP Server 方案）

实时时刻/余票通过 `mcp-server-12306` 库调用官方接口：

```
GET /otn/leftTicket/init          → 获取 JSESSIONID
GET /otn/leftTicket/queryI        → 余票/时刻（302 跳转 queryG）
GET /otn/czxx/queryByTrainNo      → 经停站
```

**关键前置条件**：
- **Python ≥ 3.10**（macOS 上 Python 3.9 使用 LibreSSL 2.8.3，TLS 指纹被 12306 反爬拦截）
- 中国境内网络出口

**为什么不用 `t12306.search_tickets`？**
该工具设计为走用户自备的 `T12306_BASE` 反代，未配置时**恒为停用**。
实际可用的路径是 `mcp-server-12306` 直连官方接口，因此：

| 工具 | 路径 | 状态 |
|---|---|---|
| `ticket.query` | mcp-server-12306 直连 | ✅ **主路径**（无需反代） |
| `train.schedule` | mcp-server-12306 直连 | ✅ **主路径** |
| `t12306.search_tickets` | 自备 `T12306_BASE` 反代 | ⚠️ 仅配置后启用（备选） |

**注意**：12306 对**已发车次**不再列出（当日查询 G1 且已过 06:30 会查不到），
`train.schedule` 会明确提示"可能已发车"，而非静默返回陈旧数据。

### 1.2-b 车次经停站 / 车站时刻表 —— 可用性实测（2026-09-15）

**背景**：实测反馈"模型看不到某车次经停哪些站"。核查结论：**12306 有数据且可用，
问题出在我们的取数路径**。

#### 车次经停站（✅ 可用，实测 0.1–0.2s）

```bash
# ① 车次号 → 官方 train_no + 起讫站（**注意 date 必须是 YYYYMMDD，带横杠会返回空**）
curl 'https://search.12306.cn/search/v1/train/search?keyword=G1&date=20260915'
# → {"data":[{"station_train_code":"G1","from_station":"北京南","to_station":"上海虹桥",
#             "train_no":"24000000G10L","total_num":"7"}], ...}

# ② train_no → 全经停站表（**与日期无关**：过去/今天/未来日期都返回同一份图定表）
curl 'https://kyfw.12306.cn/otn/czxx/queryByTrainNo?train_no=24000000G10L\
&from_station_telecode=VNP&to_station_telecode=AOH&depart_date=2026-09-15'
# → data.data = [{station_name:北京南,station_no:01,start_time:06:30,arrive_time:----,stopover_time:----}, ... 共 7 站]
```

**实测覆盖（12 个常见车次）**：

| 路径 | 可用 |
|---|---|
| **现状**（离线 2022 目录推断起讫站 → 余票接口查该 OD） | **1/12**（仅 K53） |
| **修复路径**（search 取 train_no → queryByTrainNo 直接查经停） | **11/12**（D301 当日无此车次） |

**现状失败根因**：起讫站来自 2022 年离线目录，`G1` 被推断成「北京南→**上海**」，
而实际终点是「**上海虹桥**」→ 余票接口对该 OD 无结果 → 整体降级为"静态归属"，
**且从不调用"按车次直接查经停"的接口**（该路径原先只在"已发车 + 用户明确问经停"时才走）。
`D2284`（离线目录：深圳北→南通，实际：东莞南→南通）同理。

**✅ 修复已落地（2026-09-15）**：`train.schedule` 已改为「search 取权威 train_no/起讫站 → 按 train_no 直查图定表」，
并把经停查询与余票接口**解耦**（问经停时先取图定表，即使余票接口被限流/反爬也能给出完整经停）；
回归套件 `backend/tests/test_train_stops.py`（含真实 12306 联调）。

**关键优势**：`queryByTrainNo` 是**图定数据**，不依赖"该车次是否还在售票"，
因此**已发车车次依然能给出完整经停与图定时刻**（这正好补上 12306 不列已发车次的缺口，
但仍须标注"图定时刻，非当日实际运行时刻"）。

#### 车站时刻表 —— Web 端确实没有，但**微信小程序通道有**（✅ 已接入，见 §1.2-c）

> 下面这张表是「Web/App 通道逐一排除」的过程记录；结论在 §1.2-c：
> 真正可用的端点是 `mobile.12306.cn/wxxcx/wechat/bigScreen/queryTrainByStation`（免登录、免签名）。

| 候选 | 结果 |
|---|---|
| 12306 Web「车次查询」模块 `czxxcx_js.js` | 只暴露 `czxx/queryByTrainNo`（按车次），**无按车站** |
| 12306 Web「站站查询」`zzzcx_js.js` | 只有 `zzzcx/query`（区间余票）与 `zzzcx/middleQuery`（中转换乘） |
| `/otn/staStation/*`、`/otn/stationBoard/*`、`/otn/punctuality/*` 等猜测路径 | 一律返回 23KB 的统一错误页（非真实接口） |
| 12306 App「车站大屏」`mobile.12306.cn/otsmobile/app/mgs/station/queryStationTrain` | 接口存在，但**需 App 签名/设备态**（补全 UA/app_version/device_id 后仍返回 `resultStatus:43007 操作失败`）；**2026-09-14 已改走免鉴权的微信小程序路径，见 §1.2-c** |

**可行替代：OD 聚合法（已实测）** —— 对目标站 S 并发查询 `S → N 个枢纽方向` 的余票，
按车次去重即得该站出发列车列表：

```
北京南 → [上海虹桥,天津,济南西,南京南,杭州东,青岛,合肥南,福州,厦门北,武汉,西安北,郑州东]
12 个方向并发 → 0.5s，去重后 403 条车次记录
```

> ⚠️ **实测运维提示**：短时间内大量查询会触发 12306 对**余票接口**的限流
> （返回"网络可能存在问题，请您重试一下！"）。图定表接口不受影响，
> 这也是把经停查询从余票链路里拆出来的直接原因；经停结果另有 1 小时进程内缓存。

⚠️ 两个必须处理的点：(1) 结果含**同城其他站**的车次（如 G5 实际是北京站始发），
需按"实际出发站"过滤（可用 `search` 的 from_station 校正，但不能逐车查经停，成本过高）；
(2) 覆盖**不完整**，必须如实标注"由 N 个方向聚合，可能遗漏"。

#### 1.2-c 车站大屏（✅ 已接入，免登录；2026-09-14 探测）

§1.2-b 里"按车站查当日到发车次"的缺口，已由 **12306 微信小程序的车站大屏接口**补齐
（完整报告：[`docs/12306-station-screen-api.md`](12306-station-screen-api.md)）：

```bash
# ⚠️ 必须 POST form-body：同一 URL 用 GET 会稳定返回 (M0003)"系统忙，请稍后重试！"
curl -s -X POST \
  -d "train_start_date=20260915&train_station_code=VNP" \
  'https://mobile.12306.cn/wxxcx/wechat/bigScreen/queryTrainByStation'
# → {"status":true,"data":[502 条本站到发记录], ...}
```

**为什么它比"OD 聚合法"更好**：不需要猜 N 个枢纽方向、没有"遗漏"风险、一次请求拿全；
且**多给三类 rail.re 也没有的字段**：

| 增量字段 | 用途 |
|---|---|
| `platform_no` | 站台号（`19A、19B#`）——拍车/接站最关心的信息 |
| `jiaolu_train_style` / `jiaolu_corporation_code` / `jiaolu_dept_train` | 车底**型号** + 担当**客运段** + **车辆段**（普速也有） |
| `jiaolu_train` | 该车底**当日整日套跑交路**（`车次｜始发｜发｜终到｜到`） |
| `update_arrive_time` / `update_start_time` | 实际/预计到发（"正晚点"类问题的唯一依据） |

**三条必须守住的约束**（工具层已固化，见 `station_screen.py` 与回归测试）：

1. **只是"型号"不是车组号**（`CR400BF-S` ≠ `CR400BFA-5159`）——**不能取代 rail.re** 的 `emu.routing`；
2. **空数组是三义歧义**：超窗口 / 电报码非法 / 确实无车，12306 都返回 `status:true` + `[]`，
   不得据此断言"该站当日无车"（窗口实测约 ±7 天）；
3. **必须带过滤再截断**：一次回全天 200–700 条，只取前 N 条会让"下午有哪些高铁"拿到凌晨的车。

**定向路由**：仅在命中"大屏/出发屏/到达屏/检票/晚点"等关键词时调用（`retrieve._SCREEN_RE`），
避免每个车站类问题都多打一次 12306；回答"某站今天有哪些车到发"类问题时它就是主源。


### 1.2-d 12306 免登录通道普查结果（2026-09-15 实测）

> **接入状态**：`getCarDetail`（**官方车组号**）已并入 `emu.routing`（与 rail.re 双源互证）；
> 检票口 / 开行日历 / 编组参数**尚未接入**。
> **数据源政策（2026-09-15 已定）**：12306 官方公开接口按"公开可用"对待（公开接口、多平台在用，
> 无道德负担），仅遵守限流纪律（并发/间隔/TTL 缓存）；**个人/社区站点**仍保持礼遇
> （≥2s 间隔、可识别 UA、本地缓存、标来源、不整表分发）。
>
> **配属能力（车组 → 动车所）已明确暂缓**：12306 无任何局/段/所字段，只有车迷配属库有；
> 因"不为次要功能牺牲原则"本轮不接入，详见 `docs/source-expansion.md` §六。

`mobile.12306.cn/wxxcx/**` 是**精确白名单网关**：不存在的路径返回
`403 Forbidden (openresty)` 纯 HTML，存活端点返回 `200 application/json`
（可当存在性探针用，但盲猜 10 条 0 命中；真实路径来自公开 API 目录）。

| 端点 | 方法 | 能力 | 实测 |
|---|---|---|---|
| `…/trainStyleBatch/getCarDetail` | **GET** | **车组号 + 逐车厢席别（16 节）+ 座位图 PNG** | ✅ G1→`CR400BF-A-5159`、G3→`CR400BF-BS-5277`、D10→`CR200J-6018`；反查 `carCode → carType/车厢` 可用。**仅动车组**（普速返回空） |
| `wechat/main/travelServiceQrcodeTrainInfo` | POST | **检票口 / 候车室 / 出站口**（按沿途各站） | ✅ 当日 G1：北京南 `wicket="16检票口,17检票口,负一层3快速进站厅16、17检票口"`、`waitingRoom="京沪候车区"`。**必须传"今天"**才有实时值 |
| `…/qrCode/getDeptByTrainCode` | POST | 编组技术参数：辆数/定员/车长/**餐车位置**/母婴台/无障碍卫生间/时速 + 客运段 | ✅ G1：`totalCoachNum=16`、`carCapacity=1193`、`cateringLocation=9`、`perHourSpeed=350`、`deptName=上海客运段` |
| `wechat/bigScreen/queryTrainDiagram` | POST | **开行日历**（91 天 `{date,flag}`）+ 担当局 | ✅ G1：91 天 + `上海局` |
| `wechat/bigScreen/getStationAddress`、`main/getLCLimitWaitTime`、`main/getTrainMapLine` 等 | GET/POST | 车站地址与经纬度 / 换乘预留时间 / 逐区间坐标 | ✅ 存活（未细测字段） |

> ⚠️ **判据陷阱**：`getCarDetail` 外层 `status` 可能为 `0` **但 `content.data` 仍有完整数据**；
> 判成败必须看 `content.data`，不能看外层 `status`。实测还遇到过一次间歇性返回空数据，**需要重试**。

**明确无解（均已实测排除）**：正晚点/实际到发（`fact_*` 字段对已完成车次仍为空；
大屏 `update_arrive_time` 只是"计划时刻去掉冒号"）、车站实时站台号（当日 515 条 `platform_no` 全空）、
候补余票（需登录态）、**车组级配属动车所**（各端点只到"段"级：客运段/动车段）、
普速车组号、`kyfw /otn/resources/js/query/train_list.js`（HTTP 200 但内容停在 2022 年）。

## 二、待接入 / 受限的数据源

| 数据源 | 网址 | 提供内容 | 状态 |
|---|---|---|---|
| 营业站服务信息 | `95306.cn` | 货运车站业务状况 | 工具已建，站点抓取受限 |
| 昆铁货运 | `kmrail.cn` | 全路货运办理范围/停限装公告 | 工具已建，站点抓取受限 |
| 列车时刻/余票 | `kyfw.sytlj.com` | 具体余票数量及调图后时刻表 | 工具已建，需微信公众号接口 |
| 12306 微信小程序 | `mobile.12306.cn` | 车组号（`getCarDetail`）、车站大屏、检票口 | **均已实测免登录可用**：车站大屏已接入（§1.2-c）；`getCarDetail`／检票口／开行日历**待接入**（§1.2-d）。原「需微信 3rd token」的判断已证伪 |
| 通用搜索 | — | 站名、机位、资讯 | `web.search` 使用 Bing 中国，百度兜底 |

## 三、实现约定

1. 每个数据源实现一个 `Tool`（见 `app/tools/base.py`），在 `registry.py` 注册。
2. 工具在未配置对应环境变量（如 `T12306_BASE`）时标记为未启用，检索层自动跳过。
3. 返回统一 `ToolResult`（data / text / sources / error / note）。
4. **每条检索事实自带 `note` 说明时效性**，生成层逐条渲染，避免不同工具间
   把"离线缓存（2022）"之类的警告错误套用到实时数据上。
5. 所有工具须注明数据版权与来源，回答末尾附来源链接。

## 四、抓取通用约定

- 统一使用 `app/tools/_http.py:get_text()`，默认带浏览器级请求头。
- 抓取失败一律优雅降级（返回 `ok=False` + `note`），不抛异常穿透到接口层。
- 外部站点不可达时，由生成层如实说明数据缺口，不得编造。（见 `docs/plan.md` 设计原则）
