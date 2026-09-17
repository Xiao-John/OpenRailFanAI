# 数据源参考清单

每个数据源实现一个 `Tool`（`app/tools/base.py`），在 `app/tools/registry.py` 注册，统一返回 `ToolResult`。

## 一、已接入并验证的数据源

| 工具名 | 数据源 | 提供内容 | 实时性 | 前置条件 |
|---|---|---|---|---|
| `station.lookup` | 12306 站点库 | 站名/拼音/电报码互查（3384 站） | 静态（可刷新） | 无 |
| **`station.screen`** | **12306「车站车次大屏」** | **某站当日全部到发车次：到发时刻/站台/终到站/车底型号/担当客运段·车辆段/当日套跑交路** | **实时**（约每分钟刷新，本地 60s 缓存） | 境内网络 + Python ≥ 3.10 |
| `ticket.query` | 12306 leftTicket | 两站间实时余票/车次列表 | **实时** | 境内网络 + Python ≥ 3.10 |
| `train.schedule` | 12306 leftTicket + queryByTrainNo | 车次实时时刻/余票/经停站（仅给车次可自动定位起止站） | **实时** | 境内网络 + Python ≥ 3.10 |
| **`emu.routing`** | **rail.re API**（+ 12306 `getCarDetail` 双源互证） | **车次↔动车组担当车组交路** | **实时（含历史）** | 境内网络 + 浏览器级请求头 |
| **`rail.line`** | **jprailfan 旅客径路查询** | **线路序列 + 车站序列 + 里程（区间/累计）** | 静态（准实时） | 境内网络 |
| **`rail.line_stations`** | **jprailfan 指定径路查询** | **按线路名查站序 + 指定径路里程**（既有线口径） | 静态（进程内缓存 1h） | 境内网络 |
| `rail.mileage` | 本地数据字典（GTFS 快照 + 客里表里程） | 两站里程 / 线路逐站里程 / 车站档案（电报码·TMIS·接算站·营业限制） | 静态（本地库，毫秒级） | 无 |
| `railre` / `cnrail.map` | rail.re 页面 / cnrail.geogv.org | 车站信息·页面摘要（best-effort）/ 站名→铁路地图外链 | best-effort / 静态外链 | 同上 / 无 |
| `web.fetch` / `web.search` | 任意 URL / Bing 中国·百度 | 网页标题与正文摘要 / 通用搜索兜底 | 实时 | 无（境内可访问） |
| `jprailfan` | jprailfan.com 黄河铁路网 | 客里表/电报码/拼音码 | 实时抓取 | 站点可达 |
| `t12306.search_tickets` | 自备 12306 反代 | 余票/时刻（备选路径） | 实时 | 需配置 `T12306_BASE` |

## 二、12306 实时查询（MCP Server 方案）

实时时刻/余票通过 `mcp-server-12306` 调用官方接口：`GET /otn/leftTicket/init`（取 JSESSIONID）→ `GET /otn/leftTicket/queryI`（余票/时刻，302 跳转 queryG）→ `GET /otn/czxx/queryByTrainNo`（经停站）。
**前置条件**：**Python ≥ 3.10**（macOS 上 3.9 用 LibreSSL 2.8.3，TLS 指纹被 12306 反爬拦截）+ 中国境内网络出口。

| 工具 | 路径 | 状态 |
|---|---|---|
| `ticket.query` / `train.schedule` | mcp-server-12306 直连官方接口 | ✅ **主路径**（无需反代） |
| `t12306.search_tickets` | 自备 `T12306_BASE` 反代 | ⚠️ 未配置时**恒为停用**，故路由不主动调用 |

- 12306 对**已发车次**不再列出（当日查 G1 且已过发车时刻会查不到），`train.schedule` 明确提示"可能已发车"，不静默返回陈旧数据。
- **限流**：短时间大量查询会触发**余票接口**限流（返回"网络可能存在问题，请您重试一下！"）；图定表接口不受影响——这也是把经停查询从余票链路里拆出来的直接原因；经停结果另有 1 小时进程内缓存。

## 三、车次经停站（`train.schedule`）

```bash
curl 'https://search.12306.cn/search/v1/train/search?keyword=G1&date=20260915'   # ① date 必须是 YYYYMMDD，带横杠会返回空
# → {"data":[{"station_train_code":"G1","from_station":"北京南","to_station":"上海虹桥","train_no":"24000000G10L","total_num":"7"}], ...}
curl 'https://kyfw.12306.cn/otn/czxx/queryByTrainNo?train_no=24000000G10L&from_station_telecode=VNP&to_station_telecode=AOH&depart_date=2026-09-15'   # ② 与日期无关
# → data.data = [{station_name:北京南,station_no:01,start_time:06:30,arrive_time:----,stopover_time:----}, ... 共 7 站]
```

- 取数路径：**`search` 取权威 `train_no`/起讫站 → 按 `train_no` 直查图定表**，且经停查询与余票接口**解耦**（余票被限流/反爬时仍能给出完整经停）。实测 12 个常见车次：旧路径（离线 2022 目录推断起讫站 → 余票接口查该 OD）**1/12**，修复路径 **11/12**（D301 当日无此车次）。
- **必须**用 `search` 的权威起讫站纠正离线目录：目录把 `G1` 推断成「北京南→**上海**」（实际终点**上海虹桥**），余票接口对该 OD 无结果会整体降级；`D2284` 同理（目录 深圳北→南通，实际 东莞南→南通）。
- `queryByTrainNo` 是**图定数据**，不依赖"该车次是否还在售票"：**已发车车次仍能给出完整经停与图定时刻**（补上 12306 不列已发车次的缺口），但**必须标注"图定时刻，非当日实际运行时刻"**。回归套件 `backend/tests/test_train_stops.py`（含真实 12306 联调）。

## 四、车站大屏（`station.screen`）

```bash
curl -s -X POST -d "train_start_date=20260915&train_station_code=VNP" 'https://mobile.12306.cn/wxxcx/wechat/bigScreen/queryTrainByStation'
# ⚠️ 必须 POST form-body：同一 URL 用 GET 会稳定返回 (M0003)"系统忙，请稍后重试！"
# → {"status":true,"data":[502 条本站到发记录], ...}
```

接口参数只有两个：`train_start_date`（`YYYYMMDD`）与 **`train_station_code`**（12306 电报码，如北京南=`VNP`，**不是站名**）；工具入参为 `station`（站名或电报码）、`date`、`direction`、`limit`（默认 `STATION_SCREEN_LIMIT=15`）。免登录、免签名、一次请求拿全本站当日到发——优于早期"OD 聚合法"（并发查 N 个枢纽方向再按车次去重，覆盖不完整且含同城其他站车次）。

| 增量字段（rail.re 没有的） | 用途 |
|---|---|
| `platform_no` | 站台号（`19A、19B#`）——拍车/接站最关心的信息 |
| `jiaolu_train_style` / `jiaolu_corporation_code` / `jiaolu_dept_train` | 车底**型号** + 担当**客运段** + **车辆段**（普速也有） |
| `jiaolu_train` | 该车底**当日整日套跑交路**（`车次｜始发｜发｜终到｜到`） |
| `update_arrive_time` / `update_start_time` | 实际/预计到发（"正晚点"类问题的唯一依据） |

**三条必须守住的约束**（工具层已固化，见 `backend/app/tools/station_screen.py` 与回归测试）：① **只是"型号"不是车组号**（`CR400BF-S` ≠ `CR400BFA-5159`），**不能取代 rail.re** 的 `emu.routing`；② **空数组是三义歧义**（超窗口 / 电报码非法 / 确实无车，12306 都返回 `status:true` + `[]`），**不得据此断言"该站当日无车"**（窗口实测约 ±7 天）；③ **必须带过滤再截断**：一次回全天 200–700 条，只取前 N 条会让"下午有哪些高铁"拿到凌晨的车。
**定向路由**：仅在命中"大屏/出发屏/到达屏/检票/晚点"等关键词时调用（`app/pipeline/fastpath.py:_SCREEN_RE`，计划补全在 `retrieve.py`），避免每个车站类问题都多打一次 12306；回答"某站今天有哪些车到发"时它就是主源。

## 五、`mobile.12306.cn/wxxcx/**` 免登录通道

`mobile.12306.cn/wxxcx/**` 是**精确白名单网关**：不存在的路径返回 `403 Forbidden (openresty)` 纯 HTML，存活端点返回 `200 application/json`（可当存在性探针，但盲猜 10 条 0 命中；真实路径来自公开 API 目录）。
**端点与能力**：`…/trainStyleBatch/getCarDetail`（**GET**，**车组号 + 逐车厢席别（16 节）+ 座位图 PNG**，**仅动车组**、普速返回空，反查 `carCode → carType/车厢` 可用；**已并入 `emu.routing`**，与 rail.re 双源互证）；`wechat/main/travelServiceQrcodeTrainInfo`（POST，**检票口/候车室/出站口**，按沿途各站，**必须传"今天"**才有实时值）；`…/qrCode/getDeptByTrainCode`（POST，编组参数：辆数/定员/车长/**餐车位置**/母婴台/无障碍卫生间/时速 + 客运段）；`wechat/bigScreen/queryTrainDiagram`（POST，**开行日历** 91 天 `{date,flag}` + 担当局）；`wechat/bigScreen/getStationAddress`、`main/getLCLimitWaitTime`、`main/getTrainMapLine`（车站地址与经纬度 / 换乘预留时间 / 逐区间坐标）。**除 `getCarDetail` 外均为待接入**。
**判据陷阱**：`getCarDetail` 外层 `status` 可能为 `0` **但 `content.data` 仍有完整数据**——判成败**必须看 `content.data`**，不能看外层 `status`；实测还遇到过一次间歇性返回空数据，**需要重试**。
**明确无解（均已实测排除）**：正晚点/实际到发（`fact_*` 字段对已完成车次仍为空；大屏 `update_arrive_time` 只是"计划时刻去掉冒号"）、车站实时站台号（当日 515 条 `platform_no` 全空）、候补余票（需登录态）、**车组级配属动车所**（各端点只到"段"级）、普速车组号、`kyfw /otn/resources/js/query/train_list.js`（HTTP 200 但内容停在 2022 年）。

## 六、rail.re 交路 API（`emu.routing`）

12306 公开接口**不提供**担当车组信息（只返回"高速"等列车大类），"G1 今天由哪组动车组担当"必须依赖 rail.re。`https://api.rail.re` 公开无需鉴权：`GET /train/{车次}`（该车次历史担当车组，含日期）、`GET /emu/{车组号}`（该车组历史担当车次，含日期）。

```json
// GET /train/G1
[{"date": "2026-09-13 11:24", "emu_no": "CR400BFA5054", "train_no": "G1"},
 {"date": "2026-09-11 11:24", "emu_no": "CR400BFA5159", "train_no": "G1"}]
```

`emu_no` 为紧凑格式（`CR400BFA5054`），工具会格式化为 `CR400BFA-5054`；`date` 是**最后记录时间**，用于判断"今日担当"。
**踩坑记录**：① **必须使用浏览器级请求头**（`User-Agent` + `Accept` + `Accept-Language` + `Accept-Encoding`），否则主站 `rail.re` 直接 `ReadTimeout`（实现见 `app/tools/_http.py:BROWSER_HEADERS`）；② 主站 `rail.re` 与 API `api.rail.re` 可达性可能不同——**API 子域通常更宽松**；③ `Accept-Encoding` 含 `br` 时**必须安装 `brotli`**，否则响应无法解压（工具已做自动降级）。

## 七、铁路径路（`rail.line`）

12306 与 rail.re **都没有线路概念**（前者只有车次/时刻，后者只有车次↔车组），"北京到上海走哪条线""多少公里"需要专门径路数据。数据源：黄河铁路网「中国铁路旅客径路查询」`GET https://jprailfan.com/tools/dert/index.php?action=shrtroute&startstat={发站}&endstat={到站}`。
响应为一张表，逐行给出 `线路 / 车站全名 / 车站简称 / 车站电报码 / 里程`（例：`京沪高速线 | 德州东 | 德 | -DIP | 314km/总323km`），工具归一化为 `{line, station, short, telecode, km, cum_km}`，并汇总**线路序列**（去相邻重复）与**总里程**；实测 `北京→上海` = **1320 km / 17 条线路 / 18 站**（客运运价里程口径）。
**踩坑记录**：① 该页面约 **800KB**（内嵌全国站名列表），**必须放宽超时**（工具用 ≥30s）；② 站名列表与径路表结构相似，**必须先用 `车站电报码` 表头定位径路表**再做 `<table>` 标签配平截取，否则会把整张站名表误解析为径路行；③ 径路行**恰好 5 列且电报码为 3 位大写字母**，用此过滤噪声行。按线路名反查站序需 `desgroute` 多步交互，已由 `rail.line_stations` 覆盖（路由可按需分流）。

## 八、按线路名查站序 / 指定径路（`rail.line_stations`）

`action=shrtroute` 只给两站间**最短径路**（高铁口径），无法按线路名枚举站序、也给不出既有线里程；改用同页面的 `action=desgroute`（**POST 表单**，多步交互）：

```http
POST https://jprailfan.com/tools/dert/index.php?action=desgroute
  i=   d0=0  s0=北京  key1=提交              # step1 选起始站 → 返回 <select name=r1>：该站的出发线路
  i=1  d0=0  s0=北京  r1=京沪线              # step2 选线路 → 返回 <select name=s1>：**整条线的站序**（含 (●) 接算站标记）
  i=1  d0=0  s0=北京  r1=京沪线  s1=上海     # step3 选终点 → 返回"指定径路"结果表（线路/车站/简称/电报码/区间里程/累计里程）
```

**踩坑记录**：① 是 **POST**，且 `i`/`d0` 是跨步骤的隐藏状态（step1 响应里给出 `i=1`，**必须回传**）；② **下拉框嵌在结果表附近**，解析单元格前**必须**先剥离 `<select>…</select>`，否则 `option` 文本会混进"线路/车站"列（实测把整条站序字符串塞进单元格）；③ 线路名有别名（"老京沪线"/"京沪铁路" → `京沪线`；"京沪高铁" → `京沪高速线`），而 `r1` 的取值就是规范线路名，标签形如 `京沪线(北京-上海)` 可解析出端点；④ 每次要抓 **3 个约 800KB 页面**（页面内嵌全国站名表），因此结果**进程内缓存**（`RAIL_LINE_CACHE_TTL_S`，默认 1 小时）——实测首次 19.4s、缓存命中 0.00s。
实测：`京沪线` = **57 站**、`北京→上海 全程 1463 km`（客运运价里程，**既有线口径**），与 `shrtroute` 的高铁口径 1320km 形成对照——**两个口径分别回答不同问题，不可混用**。

## 九、网页搜索与抓取（`web.search` / `web.fetch`）

**DuckDuckGo 在中国境内不可达**（`httpcore.ConnectTimeout`），故 `web.search` 改用境内可访问的引擎：

| 顺序 | 引擎 | 端点 | 解析方式 |
|---|---|---|---|
| 1（主） | Bing 中国 | `https://cn.bing.com/search?q=` | `li.b_algo` 块 + `h2>a` |
| 2（兜底） | 百度 | `https://www.baidu.com/s?wd=` | `h3>a` |

实测可达性：Bing CN ✅ / 百度 ✅ / 搜狗 ✅ / 360 ✅ / DuckDuckGo ❌。**坑**：`httpx` 的部分异常（如 `ConnectTimeout`）`str()` 为空串，直接 `f"抓取失败: {e}"` 会得到空白错误信息——统一用 `app/tools/_http.py:format_error(e)` 格式化（带上异常类型名）。

### 两条引擎的**内容量差异极大**（2026-09-17 实测）

| | 标题 | URL | 摘要 |
|---|---|---|---|
| Bing 中国 | ✅ | ✅ | ✅（上限 300 字） |
| 百度 | ✅ | ✅ | **❌ 恒为空** |

百度那条路等于"只有标题"：实测 `CR400AF 复兴号` 的关键词拉回 **815 KB** 的 SERP、解析出 5 条结果，
**摘要非空的 0 条** —— 百度已换成 CSS-module 哈希类名（`_sc-title_10ku5_63` 这类），
`c-abstract` / `content-right` 这些老标记在当前页面里 0 次出现。所以**不能把百度的结果当作有依据**。

### 命中后读正文（`WEB_SEARCH_FETCH_TOP_N`，默认 2）

正因为摘要最多 300 字、百度连摘要都没有，`web.search` 在选定引擎后会**顺手抓前几条结果的网页正文**
（并发、按页截断、`WEB_SEARCH_FETCH_CHARS` 是上限且会随注入预算动态下调），
正文以 `【网页正文】` 段并入事实块。三条纪律：

1. **抓失败要如实说**：失败原因按 `classify_http_error` 归类后写进 `note`（如 `http：站点返回 HTTP 403`），
   否则模型会把"没读到"当成"页面上没有"；失败**不进正文块**，避免原始错误串污染事实块。
2. **截断要声明**：按字数截断的正文在标题上标"（正文已按字数上限截断）"。
3. **可一键回退**：`WEB_SEARCH_FETCH_TOP_N=0` 退回"只有标题+摘要"的旧行为。

实测代价（同一问句 A/B）：工具耗时 0.40s → 0.64s，事实文本 979 字 → 3840 字。
收益是质的变化——回答里开始出现**页面正文**里的具体参数（编组形式、座位电源、无障碍设施等），
而不是只有一句搜索摘要。另外 `baike.baidu.com` 对任何请求头都回 **403**
（换 UA / 加 Referer / 先取百度 Cookie 均无效，属风控层拦截），所以候选会比保留条数多试 2 条。

## 十、待接入 / 受限的数据源

| 数据源 | 网址 | 提供内容 | 状态 |
|---|---|---|---|
| 营业站服务信息 | `95306.cn` | 货运车站业务状况 | 工具已建，站点抓取受限 |
| 昆铁货运 | `kmrail.cn` | 全路货运办理范围/停限装公告 | 工具已建，站点抓取受限 |
| 列车时刻/余票 | `kyfw.sytlj.com` | 具体余票数量及调图后时刻表 | 工具已建，需微信公众号接口 |
| 12306 微信小程序 | `mobile.12306.cn` | 车组号（`getCarDetail`）、车站大屏、检票口、开行日历、编组参数 | 车站大屏与 `getCarDetail` **已免登录可用**；检票口 / 开行日历 / 编组参数**待接入**（「需微信 3rd token」的判断已证伪） |

## 十一、实现约定与抓取纪律

1. 每个数据源实现一个 `Tool`（`app/tools/base.py`），在 `registry.py` 注册，统一返回 `ToolResult`（data / text / sources / error / note）；未配置对应环境变量（如 `T12306_BASE`）的工具标记为未启用，检索层自动跳过。
2. **每条检索事实自带 `note` 说明时效性**，生成层逐条渲染，避免把"离线缓存（2022）"之类的警告错误套用到实时数据上；所有工具须注明数据版权与来源，回答末尾附来源链接。
3. 抓取统一用 `app/tools/_http.py:get_text()`（默认带浏览器级请求头），失败一律优雅降级（返回 `ok=False` + `note`），不抛异常穿透到接口层。
4. **抓取纪律**：低频抓取 + 本地缓存 + 标注来源，**不整表对外分发**；12306 官方公开接口遵守并发/间隔/TTL 缓存限流纪律，个人/社区站点（黄河铁路网等）额外保持 ≥2s 请求间隔（`DICT_SITE_MIN_INTERVAL_S=2.0`）、可识别 UA。外部站点不可达时由生成层如实说明数据缺口，**不得编造**。
5. **使用声明**：本项目及其数据源接入**可自由使用（含商业用途，代码许可见仓库 `LICENSE`）**，服务按「现状」提供、**不提供任何担保**，因使用产生的后果由使用者自行承担；**不得滥用**——禁止高频/并发抓取、绕过限流或反爬、整表转载与转售第三方数据；第三方数据版权归各原站点（不在本项目代码许可范围内），使用须遵守其条款与 robots 约定。
