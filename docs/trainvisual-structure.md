# trainvisual.top 结构分析（第三方车站/配属可视化站）

> 探测时间：2026-09-15
> 探测方式：**读它自己的前端 JS 拿端点清单 + 少量抽样请求验形**（未做路径爆破、未批量抓取）
> 性质：**只读的结构分析**，用于评估"能不能接、值不值得接"。**本项目未接入、未落库、未批量留存其数据。**

---

## 〇、合规前提（先看这一段）

该站 `robots.txt`（Cloudflare 托管）写明：

```
User-agent: *
Content-Signal: search=yes,ai-train=no,use=reference
```

并对以下 UA 全部 `Disallow: /`：
`Amazonbot`、`Applebot-Extended`、`Bytespider`、`CCBot`、**`ClaudeBot`**、`CloudflareBrowserRenderingCrawler`、`Google-Extended`、`GPTBot`、`meta-externalagent`。

解读与本项目的立场：

1. `use=reference` 允许"引用/参考"用途，`ai-train=no` 禁止用于训练 —— 本文属**reference** 性质，
   且只记录**结构与端点**，不含其数据本身；
2. 但 `ClaudeBot` 被显式 Disallow —— AI agent 身份不在受欢迎之列；
3. **结论：不要把它当作可自由接入的上游**。真要用，先看它页面上的"反馈交流"入口联系站长取得许可；
   在拿到许可前，本文只作为**能力对标与设计参考**，不产生任何代码依赖。

> 与 `docs/source-expansion.md` 的合规红线一致：**未经许可的第三方聚合站不进自动调用链**。

---

## 一、摘要

`trainvisual.top` 是一个面向车迷的「列车运行可视化 + 动车组配属 + 车站大屏」工具站，
服务端渲染、**全部 API 免鉴权 GET JSON**，没有 API Key、没有签名、没有登录。

| 项 | 值 |
|---|---|
| 应用框架 | **Flask**（404 为 Werkzeug 默认页） |
| 前置 | Cloudflare（`server: cloudflare`，含托管 content-signal） |
| 前端 | 原生 HTML/JS，**无框架**；CSS 内联进 HTML（首页 964 KB） |
| 地图 | Leaflet 1.9.4（走 `unpkg.com` CDN） |
| 静态资源 | `/static/gen/common.css`(333B)、`/static/gen/packed.js`(20KB)、`/static/model_images/*.avif`（车型图，供可视化动画用） |
| 鉴权 | **无** |
| 分页 | **无**（多处一次返回全量） |
| 版本/元数据 | 无 API 版本号、无 OpenAPI、无 sitemap |

---

## 二、页面地图

| 路由 | 功能 |
|---|---|
| `/` | 首页：时刻表查询（站站 / 车站 / 车次）+ 配属·交路入口 + **76 条线路**的可视化入口 |
| `/map` | 地图可视化（线路几何 + 车站定位 + 在线列车） |
| `/train_diagram` | 列车运行图（按线路画时空图） |
| `/diagram_query` | 交路查询（车次当日套跑序列 + 交路图） |
| `/train_set` | 动车组查询系统（**22 项**查询功能，见 §4） |
| `/station-statistics` | 车站统计（检票口 / 通达范围 / 站台占用 / 停站车次排名） |
| `/station_screen/<站名>` | 车站大屏 |
| `/train_screen/<车次>` | 车次屏 |
| `/visualization/<线路名>` | 列车位置动画（实时 / 模拟双模式，可调 1秒=1/2/3分钟） |

---

## 三、API 全景（全部 `GET`，JSON）

### 3.1 车站 / 车次查询

| 端点 | 返回 |
|---|---|
| `/api/station_query?station_name=` | 该站当日全部到发车次 |
| `/api/station_to_station?start_station=&end_station=` | `{has_direct, direct_results[], has_transfer, transfer_results[]}`，含 `duration_minutes` |
| `/api/train_query?train_number=` | 车次停站序列（含检票口） |
| `/api/search_lines?keyword=` | 线路名搜索 |
| `/api/search_stations?keyword=` | 车站名搜索 |
| `/api/station_lines?station_name=` | ❌ **404**（前端在调，后端已无此路由，见 §6.1） |

### 3.2 车站屏类（与本项目 `station.screen` 最接近的一块）

| 端点 | 返回 |
|---|---|
| `/api/station_departures/<站名>` | `{departures[]}`：`train_number / start_station / end_station / start_time / status / wicket` |
| `/api/station_arrivals/<站名>` | `{arrivals[]}`，同构 |
| `/api/stations/wickets?station=` | 该站检票口清单 |
| `/api/stations/wicket-trains?station=` | 车次 ↔ 检票口 映射 |
| `/api/stations/model-trains?station=` | 车站车型车次 |
| `/api/stations/platform_occupation?station=` | `{mode, current_time, occupation:{"站台号":[车次…]}}` **站台占用时间线** |
| `/api/stations/reachability?station=` | 通达范围：可达站 + `shortest_time` + 经纬度 |
| `/api/stations/train-counts` | 各站停靠车次数排名（全局） |
| `/api/stations/suggestions?station=` | 站名补全 |

`status` 实测取值含 `"停止检票"`；`platform_occupation` 的 `status` 含 `"departed"`。

### 3.3 运行图 / 可视化

| 端点 | 返回 |
|---|---|
| `/api/train_diagram/<线路名>` | `{line_name, stations[], trains[{direction, schedule[{station_name, mileage, arrival_time, departure_time, stop_order}]}]}` |
| `/api/map/lines` | 线路清单（`name / line_color / station_count / desc`） |
| `/api/map/route-points?line_name=` | 线路几何点串（`lat/lng/distance/type/point_index`） |
| `/api/map/station-location?station=` | 经纬度 + 所属线路 |
| `/api/map/trains-schedules` | ⚠️ **单响应 15 MB**：全线路站序 + 里程，无分页 |
| `/api/trains/<线路名>` | **实时位置**：`{current_mileage, current_station, next_station, direction, progress, model_image_path}` |
| `/api/diagram_query/<车次>` | 该车次当日交路序列（套跑各段） |
| `/api/circulation_image/<车次>` | 交路图 → 实测 **502** |
| `/api/schedule/<线路名>` | ❌ **404**（`packed.js` 在调，后端无此路由，见 §6.1） |

### 3.4 配属 / 出勤 —— `/api/train_set/*`（最大的一块，100+ 端点）

数据是**四维交叉**：路局 → 动车所 → 车型 → 制造商，外加**日期维**（出勤率、担当历史）。

| 类别 | 端点 |
|---|---|
| 维度字典 | `all_bureaus` · `all_depots` · `all_models` · `all_manufacturers` · `available_dates` |
| 计数排名 | `bureau_count` · `depot_count` · `all_model_count` · `manufacturer_count`（及各维度的 `*_count`） |
| 单点查询 | `train_set_no`（车组号） · `full_link`（完整车组号如 `CR400BF-5033`） · `model` · `bureau` · `depot` · `manufacturer` · `train_code` |
| **联动矩阵** | `X_by_Y` / `X_by_Y_and_Z` / `X_by_Y_Z_W` —— 四个维度的所有排列组合，约 40 个端点，纯为页面上四个级联下拉框服务 |
| 出勤率 | `attendance/{bureau\|depot\|depot_bureau}?date=` → `{active_count, total_count, attendance_rate}` |
| 历史担当 | `full_link_history?full_link=` → 按日期的历史车次记录 |
| ❌ 死接口 | `history`（404；后端实际叫 `full_link_history`，见 §6.1） |

---

## 四、数据模型

```
路局 bureau ──┬── 动车所 depot ──┬── 车组 train_set_no ── 车型 model ── 制造商 manufacturer
              │                  │
              └──────────────────┴── 日期 date ── 出勤 attendance_rate / 担当历史 full_link_history
```

- 车组的完整标识是 `full_link`：`{model}-{train_set_no}`，例 `CR400BF-5033`；
- 单条配属记录字段：`bureau_name / depot_name / manufacturer_name / model_name / train_set_no`；
- 出勤是"日期 × (路局|动车所)"的聚合，`available_dates` 给出可查出勤的日期清单。

---

## 五、实测抽样（用于判断数据规模，非数据采集）

单次抽样、每条只请求一次，仅记录**形状与规模**：

| 端点 | 规模 |
|---|---|
| `/api/station_query`（北京南） | 504 条到发 |
| `/api/station_departures`（北京南） | 250 条 |
| `/api/station_arrivals`（北京南） | 250 条 |
| `/api/stations/wicket-trains`（北京南） | 511 条 |
| `/api/stations/wickets`（北京南） | 16 个检票口 |
| `/api/stations/reachability`（北京南） | 275 个可达站 / 20 条线路 |
| `/api/stations/train-counts` | 1738 个站 |
| `/api/train_diagram/京沪高铁` | 23 站 / 1265 车 / 834 KB |
| `/api/trains/京沪高铁` | 212 列在途 |
| `/api/map/lines` | 47 条线路 |
| `/api/map/trains-schedules` | **15,174,136 B（15 MB）** |
| `/api/train_set/all_bureaus` | 20 个路局 |
| `/api/train_set/depot_count` | 73 个动车所 |
| `/api/train_set/all_model_count` | 64 个车型（首位 CRH2A = 491 组） |
| `/api/train_set/model?model_name=CR400BF` | 146 组配属明细 |
| `/api/train_set/available_dates` | 27 个可查日期 |
| `/api/train_set/full_link_history` | 217 条历史担当 |

---

## 六、工程观察

### 6.1 三处"前端调了、后端没有"的死接口

`/api/station_lines`、`/api/train_set/history`、`/api/schedule/<线路名>` 在页面 JS 里都有 `fetch`，
但直接请求返回 Werkzeug 404。说明前后端有过一次不同步改动（前端改名前，后端改了名后）。
**对我们自己的启示**：这正是"调用必然失败的端点"那一类问题，
本项目的对应防线是"未启用工具不入计划"，值得对照检查我们的前端是否也存在调了不存在的接口。

### 6.2 状态是"钟点算出来的"，不是推送

`platform_occupation` 带 `mode:"real"` 与 `current_time`；`station_departures` 我请求时（服务器时刻 14:44）
列表首条是 14:46 的车且 `status:"停止检票"` —— 即**列表窗口围绕当前时刻**，而非从 00:00 起算。

判断：它是**当日计划表 + 按服务器钟点计算出的状态**，不是逐秒/推送式实时。
引用其 `status`/`wicket` 时必须标注"该站自算，未经车站现场核实"。

### 6.3 无分页的重端点

`/api/map/trains-schedules` 一次 15 MB，`/api/train_diagram/京沪高铁` 834 KB。前者对移动端很不友好。
**对本项目的启示**：我们自己的 `station.screen` 已经做了"工具侧过滤 + 截断如实登记"，
这正是这类接口该有的处理方式（见 `docs/12306-station-screen-api.md` §七）。

### 6.4 未验证项（不要当成结论）

- `wicket`（检票口）与 `status` 的**准确性未经车站现场对表** —— 12306 官方接口不提供检票口，
  没有权威源可交叉验证；
- 配属数据的**更新频率**未知（`available_dates` 只说明可查哪些日期，不说明数据截至何时）；
- 未验证其"实时位置"（`/api/trains/`）的时延与误差。

---

## 七、与本项目的关系（能力对标）

### 它有、我们没有

| 能力 | 说明 |
|---|---|
| **检票口**（`wicket`） | 12306 官方接口不提供；这是它的独占项 |
| **站台占用时间线** | 我们只给"某车在几站台"，它给"某站台全天被谁占" |
| **动车组配属** | 路局/动车所/制造商/车型的四维查询 + 出勤率 |
| **通达范围** | 某站可达站 + 最短用时 |
| **运行图 / 线路几何点** | 时空图与地图折线数据 |
| **交路图**（图片） | 端点实测 502，当前不可用 |

### 我们有、它没有（或我们更权威）

| 能力 | 说明 |
|---|---|
| 12306 官方**实时余票** | 它完全不涉及售票数据 |
| 官方**站台号** | 我们 `station.screen` 的 `platform_no` 来自 12306 官方 |
| **具体车组号** | 我们走 rail.re（它只到车型/配属，不接当日实际担当）|
| **径路里程**（黄河网口径） | 我们 `rail.line` / `rail.line_stations` 有权威口径 |

> 结论：**两者是互补而非替代**。但鉴于 §〇 的合规前提，**不建议接入**；
> 更合理的用法是把它当"能力对标样本"——它证明了「检票口 / 站台占用 / 配属」这三类数据
> 在车迷侧有真实需求，值得本项目去找**有授权的数据源**来补（列入 `docs/source-expansion.md` 的评估队列）。

---

## 八、若要接入，前置条件（当前均未满足）

1. **取得站长许可**（robots 对 AI agent 的 Disallow + `ai-train=no`）；
2. `wicket` / `status` 的**准确性对表**（至少与若干车站现场核对）；
3. 为 15 MB 级端点设计分页/缓存策略，绝不可放进单次问答链路；
4. 明确**数据权属与再分发条款**（本项目回答会附来源链接，属再分发行为）。

**在 1–4 全部满足前，本项目的代码与计划中不引入该源。**

---

## 九、探测方法（可复现）

```bash
# 1) 取首页与其内联脚本（它把 JS 内联在 HTML 里）
curl -sSL -A "Mozilla/5.0" https://trainvisual.top/ -o index.html

# 2) 从内联 <script> 中提取端点（不做路径爆破）
python3 - <<'PY'
import re
h = open('index.html', encoding='utf-8').read()
for s in re.findall(r'<script[^>]*>(.*?)</script>', h, re.S):
    for m in sorted(set(re.findall(r'/api/[a-zA-Z0-9_/\-]+', s))):
        print(m)
PY

# 3) 各子页面同法处理，并用 fetch( 上下文还原参数名
# 4) 抽样验形（每条只打一次，勿批量）
curl -sS -A "Mozilla/5.0" "https://trainvisual.top/api/station_departures/北京南" | head -c 400
```

同理可用的两个落点：`/static/gen/packed.js`（可视化页的 `packed` 脚本）、各子页面的内联脚本。
