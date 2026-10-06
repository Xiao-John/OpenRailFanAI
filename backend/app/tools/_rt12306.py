"""12306 实时查询共享助手（基于 mcp-server-12306）。

集中封装实时查询所需的三件事，供 train.schedule / ticket.query 等工具复用：
1. 站点数据初始化与站名→电报码解析
2. 余票/时刻查询（返回已归一化的车次列表）
3. 车次定位（缺起止站时，用离线车次目录自动推断 origin→destination）

注意：12306 反爬要求 Python ≥ 3.10 + 境内网络；
外部异常统一转换为 Realtime12306Error，由工具层降级处理。
"""
from __future__ import annotations

import json
import re
import os
import inspect
import time
from copy import deepcopy
from contextvars import ContextVar
from functools import wraps

from app.metrics import record_cache, record_operation
from app.query_cache import QueryCache, query_scope

from mcp_12306.services.ticket_service import (
    get_train_route_stations_validated,
    query_ticket_price_validated,
    query_tickets_validated,
    search_stations_validated,
)

from app.dates import normalize_date

_loaded = False


def _main() -> bool:
    return os.environ.get("APP_VARIANT", "main").lower() != "lm"


def _scoped_key(*parts):
    # Scope is a digest; no credentials or query values reach metrics/logs.
    return (query_scope(), *parts) if _main() else tuple(parts)


def _cache_get(cache: dict, key, ttl: float, name: str):
    hit = cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        if _main():
            record_cache(name, "hit")
            return deepcopy(hit[1])
        return hit[1]
    if _main():
        if hit is not None:
            cache.pop(key, None)
            record_cache(name, "expired")
        record_cache(name, "miss")
    return None


def _cache_put(cache: dict, key, value, ttl: float, capacity: int, name: str):
    now = time.time()
    if not _main():
        if len(cache) >= capacity:
            cache.clear()
        cache[key] = (now, value)
        return
    for old_key, (fetched, _) in list(cache.items()):
        if now - fetched >= ttl:
            cache.pop(old_key, None)
            record_cache(name, "expired")
    # Evict only the oldest snapshot, rather than every valid entry at capacity.
    while key not in cache and len(cache) >= capacity:
        oldest = min(cache, key=lambda candidate: cache[candidate][0])
        cache.pop(oldest)
        record_cache(name, "evicted")
    cache[key] = (now, deepcopy(value))


_QUERY_FLIGHTS: list[QueryCache] = []
_MCP_OPERATION: ContextVar[str | None] = ContextVar("rt_mcp_operation", default=None)


def _install_mcp_observer() -> None:
    """Observe the dependency's existing init/retry lifecycle without replacing it.

    The installed mcp-server-12306 calls its factory for every retry. A once-installed
    factory wrapper returns the original client outside this task-local context.
    The proxy delegates TLS/proxy/cookies/close and never stores URLs or arguments.
    """
    from mcp_12306.services import ticket_service as ts

    factory = ts.create_12306_client
    if getattr(factory, "_rt_observed", False):
        return

    class ObservedClient:
        def __init__(self, manager, operation):
            self.manager = manager
            self.client = manager
            self.operation = operation

        async def __aenter__(self):
            self.client = await self.manager.__aenter__()
            return self

        async def __aexit__(self, *args):
            return await self.manager.__aexit__(*args)

        def __getattr__(self, name):
            return getattr(self.client, name)

        async def get(self, url, *args, **kwargs):
            phase = "init" if str(url) == str(ts.HTTP_URLS["init"]) else "query"
            return await _operation(self.operation + "." + phase,
                self.client.get(url, *args, **kwargs),
                success=lambda response: response.status_code < 400)

    @wraps(factory)
    def observed_factory(*args, **kwargs):
        client = factory(*args, **kwargs)
        operation = _MCP_OPERATION.get()
        return ObservedClient(client, operation) if operation else client

    observed_factory._rt_observed = True
    ts.create_12306_client = observed_factory


def _shared_query(name: str):
    cache = QueryCache("rt." + name + ".flight", capacity=128)
    _QUERY_FLIGHTS.append(cache)

    def decorate(fn):
        signature = inspect.signature(fn)
        @wraps(fn)
        async def call(*args, **kwargs):
            if not _main():
                return await fn(*args, **kwargs)
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            canonical = dict(bound.arguments)
            if "train_date" in canonical:
                canonical["train_date"] = normalize_date(canonical["train_date"])
            for field in ("train_code", "from_code", "to_code", "station_code"):
                if field in canonical:
                    canonical[field] = str(canonical[field] or "").strip().upper()
            key = (query_scope(), tuple(sorted(canonical.items())))
            return await cache.get(key, lambda: fn(*args, **kwargs), ttl=0)
        return call
    return decorate


async def close_query_flights() -> None:
    """Release only this module's shared queries at application shutdown."""
    for cache in _QUERY_FLIGHTS:
        await cache.close()


async def _operation(name: str, awaitable, *, success=lambda value: True):
    token = None
    if _main() and name.endswith(".mcp"):
        _install_mcp_observer()
        token = _MCP_OPERATION.set(name[:-4])
    started = time.perf_counter()
    ok = False
    try:
        value = await awaitable
        ok = bool(success(value))
        return value
    finally:
        if token is not None:
            _MCP_OPERATION.reset(token)
        if _main():
            record_operation("rt." + name, (time.perf_counter() - started) * 1000, ok)

# 行政/方位后缀，站名通常不含这些词（用于"吉林市船营区"→"吉林市船营"→"吉林市"→"吉林"的多级降级）
_ADMIN_SUFFIX_RE = re.compile(r"(市|省|自治区|特别行政区|地区|自治州|县|区|新区|城区|主城区|市区|街道|镇|乡)$")
_PLACEHOLDER_RE = re.compile(r"(XX+|某某|某X?|××+)", re.I)

# 车次号（2026-09-14 放开普速）：
#   动车组/城际：G/D/C；普速：K/T/Z/Y/S/L/B 等；以及纯数字车次（1461、K507 的对偶写法）
#   注意：纯数字排除"年份形态"（19xx/20xx），否则 "2026" 会被当成车次
# 位数放宽到 5：像 G99999 这种"不存在的车次"应交给数据源判定（rail.re 直接 404），
# 而不是被本地正则挡在门外、退化去搜网页（R1 缺陷 P0-3 K01）
# 字母前缀放宽到 5 位（让 G99999 这类"不存在的车次"由数据源判定 404）；
# 纯数字仍限 4 位（12306 这种 5 位数字不是车次号，实测会被误判）
_TRAIN_CODE_RE = re.compile(r"^(?:0?[GDCTZKYSLBN]\d{1,5}[A-Z]?|\d{1,4})$", re.I)
_YEAR_LIKE_RE = re.compile(r"^(?:19|20)\d{2}$")
# 仅动车组（rail.re 交路库的覆盖范围）
_EMU_TYPE_RE = re.compile(r"^0?[GDC]\d{1,5}[A-Z]?$", re.I)


class Realtime12306Error(RuntimeError):
    """12306 实时接口不可用（网络/反爬/数据异常）。"""


async def ensure_loaded() -> None:
    """加载 12306 站点库（mcp-server-12306 需显式加载）。"""
    global _loaded
    if _loaded:
        return
    from mcp_12306.services import ticket_service as ts
    await ts.station_service.load_stations()
    _loaded = True


def is_train_code(v: str | None) -> bool:
    """是否为车次号（含普速 K/T/Z 与纯数字车次；年份形态不算）。"""
    if not v:
        return False
    s = str(v).strip()
    if _YEAR_LIKE_RE.match(s):
        return False
    return bool(_TRAIN_CODE_RE.match(s))


def is_emu_train_code(v: str | None) -> bool:
    """是否动车组/城际车次（G/D/C）——只有这类在 rail.re 交路库里有数据。"""
    return bool(v) and bool(_EMU_TYPE_RE.match(str(v).strip()))


# 车次后缀噪声：G1次列车 / G1次 / G1 列车 / 车次G1 ...
# 注意：不能用 \b —— 中文"次"在 Unicode 下也是词字符，会导致 "G1次列车" 匹配失败。
# 改用否定断言：车次号后不能紧跟 ASCII 字母/数字。
# 自由文本扫描只用"字母前缀"形态：纯数字在文本里极易误命中（年份、里程、编号）
_TRAIN_CODE_IN_TEXT_RE = re.compile(r"(0?[GDCTZKYSLBN]\d{1,4}[A-Z]?)(?![A-Za-z0-9])", re.I)
# 纯数字车次只在后接"次"时才算（"1461次列车"），避免年份/编号误命中
_PURE_DIGIT_TRAIN_RE = re.compile(r"(?<!\d)(\d{1,4})(?=次(?:列车|车)?)")
_TRAIN_NOISE_RE = re.compile(r"(次列车|次|列车|车次|高铁|动车|列车组)$")


def extract_train_code(v: str | None) -> str | None:
    """从表述中提取规范车次号。

    "G1次列车" -> "G1"；"K53" -> "K53"；"1461次列车" -> "1461"；"CR400AF" -> None
    用于兼容 LLM 抽取出的带后缀写法（如上下文继承得到的 "G1次列车"）与普速车次。
    """
    if not v:
        return None
    s = str(v).strip()
    if _TRAIN_CODE_RE.match(s) and not _YEAR_LIKE_RE.match(s):
        return s.upper()
    m = _TRAIN_CODE_IN_TEXT_RE.search(s)
    if m:
        return m.group(1).upper()
    m = _PURE_DIGIT_TRAIN_RE.search(s)     # 纯数字须后接"次"才认（"1461次列车"）
    if m:
        return m.group(1).upper()
    return None


def strip_train_noise(v: str | None) -> str:
    """去掉车次表述的常见后缀，保留主干（供展示/其他解析使用）。"""
    s = str(v or "").strip()
    return _TRAIN_NOISE_RE.sub("", s).strip()


def parse_mcp_result(result: list) -> dict:
    """mcp 返回 [{type:text,text:"{...}"}] → dict。"""
    for item in result:
        text = item.get("text", "")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            continue
    return {"success": False, "error": "无法解析 MCP 返回数据"}


def station_candidates(keyword: str) -> list[str]:
    """站名候选：原词 → 截断占位符 → **逐级**剥离行政后缀（直到不再变化）。

    2026-09-14 修复：原实现只剥两轮，导致
    `'吉林市船营区' → ['吉林市船营区', '吉林市船营']` 就停了，落不到 "吉林市" / "吉林"，
    使 README 旗舰句（吉林市船营区拍 CR400AF）在站点查询这一步就失败。
    现在持续剥离（区→市 等），并对每个中间形态都生成候选。
    """
    out: list[str] = []

    def add(v: str) -> None:
        v = (v or "").strip()
        if v and v not in out:
            out.append(v)

    add(keyword)
    cut = _PLACEHOLDER_RE.split(keyword)[0].strip()
    add(cut)

    for base in (keyword, cut):
        cur = base
        while True:
            nxt = _ADMIN_SUFFIX_RE.sub("", cur).strip()
            if not nxt or nxt == cur or len(nxt) < 2:
                break
            add(nxt)
            cur = nxt

    # 残尾处理：'吉林市船营' 这类"市/省 + 区县名残尾"再截到行政区级别
    # （否则永远落不到 '吉林市' / '吉林'）
    for base in list(out):
        for marker in ("市", "省", "自治区", "自治州", "地区"):
            idx = base.find(marker)
            if idx > 0:
                add(base[: idx + len(marker)])      # 吉林市
                add(base[:idx])                     # 吉林
    return out


# 向后兼容的私有别名
_candidates = station_candidates


async def resolve_station_code(name: str) -> tuple[str, str] | None:
    """站名 → (电报码, 规范站名)。支持占位符与行政后缀降级。"""
    await ensure_loaded()
    name = (name or "").strip()
    if not name:
        return None

    for candidate in station_candidates(name):
        data = parse_mcp_result(
            await search_stations_validated({"query": candidate, "limit": 3})
        )
        if data.get("success") and data.get("stations"):
            st = data["stations"][0]
            return st["code"], st["name"]

    # 已是三字电报码
    upper = name.upper()
    if upper.isalpha() and len(upper) == 3:
        return upper, name
    return None


@_shared_query("tickets")
async def query_tickets(from_code: str, to_code: str, train_date: str) -> list[dict]:
    """实时余票/时刻查询，返回归一化车次列表。

    抛出 Realtime12306Error 表示 12306 不可用。
    """
    await ensure_loaded()
    date_str = normalize_date(train_date)
    async def fetch():
        return parse_mcp_result(await query_tickets_validated({
            "from_station": from_code,
            "to_station": to_code,
            "train_date": date_str,
        }))
    data = await _operation("tickets.mcp", fetch(), success=lambda result: result.get("success"))
    if not data.get("success"):
        raise Realtime12306Error(
            data.get("error") or str(data.get("errors") or "12306 查询失败")
        )
    return data.get("trains", []) or []


@_shared_query("prices")
async def query_ticket_prices(
    from_station: str, to_station: str, train_date: str, train_code: str = ""
) -> dict:
    """查询 12306 票价；返回标准化 MCP 数据，不包含余票状态。"""
    await ensure_loaded()
    date_str = normalize_date(train_date)
    async def fetch():
        return parse_mcp_result(await query_ticket_price_validated({
            "from_station": from_station,
            "to_station": to_station,
            "train_date": date_str,
            "train_code": train_code,
            "purpose_codes": "ADULT",
        }))
    if _main():
        from app.tools.executed_fare import query
        data = await query(from_station, to_station, date_str, train_code)
    else:
        data = await _operation("prices.mcp", fetch(), success=lambda result: result.get("success"))
    if not data.get("success"):
        raise Realtime12306Error(data.get("error") or str(data.get("errors") or "12306 票价查询失败"))
    if _main():
        origin = await resolve_station_code(from_station)
        destination = await resolve_station_code(to_station)
        if not origin or not destination:
            raise Realtime12306Error("无法确认票价查询的实际出发站和到达站")
        # The official endpoint may return extended destinations (e.g. a
        # Zhengding request also contains Shijiazhuang). Never relabel them.
        rows = data.get("data") or []
        exact = [row for row in rows if isinstance(row, dict)
                 and row.get("from_station") == origin[1]
                 and row.get("to_station") == destination[1]
                 and (not train_code or str(row.get("train_code") or "").upper() == train_code.upper())]
        data = {**data, "data": exact, "from_station": origin[1], "to_station": destination[1]}
        if "count" in data:
            data["count"] = len(exact)
    return data


# 余票列表**原始行**缓存：(from, to, date) → (取数时刻, 行表)
# 只有原始行才带 12306 的**内部编号**（`train_no`，形如 39000G236801）；
# MCP 的归一化层只留下车次号，把内部编号丢了 —— 而"同一次车在交路不同分段用不同车次号"
# （G2365/G2368、D2238/D2235、Z184/Z181…）唯一可靠的判据就是这个内部编号。
_RAW_ROWS_CACHE: dict[tuple[str, ...], tuple[float, list[dict]]] = {}
_RAW_ROWS_TTL_S = 300
_RAW_ROWS_CACHE_MAX = 200

_LEFT_TICKET_INIT = "https://kyfw.12306.cn/otn/leftTicket/init"
_LEFT_TICKET_QUERY = "https://kyfw.12306.cn/otn/leftTicket/queryI"
# 车次号形态（用于在原始行里认列，防止 12306 改字段顺序后静默取错）
_TRAIN_CODE_FIELD_RE = re.compile(r"^0?[A-Z]?\d{1,4}[A-Z]?$", re.I)


@_shared_query("raw_rows")
async def query_ticket_rows(
    from_code: str, to_code: str, train_date: str
) -> list[dict]:
    """直连 12306 余票接口，返回**保留内部编号**的行列表。

    每行：`{train_no(内部编号), train_code(车次号), from_code, to_code, start_time, arrive_time}`。

    与 `query_tickets` 的区别只有一个：内部编号 `train_no`。它是"车底/交路"级别的唯一键 ——
    同一次车在交路不同分段挂不同车次号（上行/下行、分段开行），内部编号却**完全相同**；
    实测 G2365/G2368 同为 `39000G236801`、D2238/D2235 同为 `77000D223802`、
    Z184/Z181 同为 `330000Z1840X`。因此这是"同车不同号"判定的权威依据。

    失败一律返回 `[]`（调用方按"拿不到别名信息"降级），**不抛异常**。
    """
    code_a = str(from_code or "").strip().upper()
    code_b = str(to_code or "").strip().upper()
    date_str = normalize_date(train_date)
    if not code_a or not code_b or not date_str:
        return []

    key = _scoped_key(code_a, code_b, date_str)
    hit = _cache_get(_RAW_ROWS_CACHE, key, _RAW_ROWS_TTL_S, "rt.raw_rows")
    if hit is not None:
        return hit

    from app.tools._http import BROWSER_HEADERS, get_client

    headers = dict(BROWSER_HEADERS)
    headers["Referer"] = _LEFT_TICKET_INIT
    rows: list[dict] = []
    try:
        client = await get_client()
        # 余票接口需要 init 种下的会话（Cookie），缺了会 302/空结果
        await _operation("raw.init", client.get(_LEFT_TICKET_INIT, headers=headers, timeout=15),
                         success=lambda response: response.status_code < 400)
        async def fetch_rows():
            resp = await client.get(
                _LEFT_TICKET_QUERY,
                headers=headers,
                params={
                    "leftTicketDTO.train_date": date_str,
                    "leftTicketDTO.from_station": code_a,
                    "leftTicketDTO.to_station": code_b,
                    "purpose_codes": "ADULT",
                },
                timeout=15,
            )
            resp.raise_for_status()
            payload = resp.json()
            result = (payload.get("data") or {}).get("result")
            if _main() and (payload.get("status") is False or not isinstance(result, list)):
                raise Realtime12306Error("12306 原始余票行查询未返回有效数据")
            return result or []
        result = await _operation("raw.query", fetch_rows())
    except Exception:  # noqa: BLE001 —— 增强路径失败不得影响主流程
        return []

    for raw in result:
        parts = str(raw).split("|")
        if len(parts) < 10:
            continue
        # 12306 原始行里"预订"标记紧跟内部编号与车次号；字段顺序变过，
        # 因此先在 "预订" 之后确认车次号形态，认不出再退回固定列（实测 2/3 列）。
        idx = None
        for i, cell in enumerate(parts):
            if cell.strip() == "预订" and i + 2 < len(parts):
                if _TRAIN_CODE_FIELD_RE.match(parts[i + 2].strip()):
                    idx = i + 1
                break
        if idx is None:
            if not _TRAIN_CODE_FIELD_RE.match(parts[3].strip()):
                continue
            idx = 2
        rows.append({
            "train_no": parts[idx].strip(),
            "train_code": parts[idx + 1].strip().upper(),
            "from_code": parts[6].strip(),
            "to_code": parts[7].strip(),
            "start_time": parts[8].strip(),
            "arrive_time": parts[9].strip(),
        })

    _cache_put(_RAW_ROWS_CACHE, key, rows, _RAW_ROWS_TTL_S, _RAW_ROWS_CACHE_MAX, "rt.raw_rows")
    return rows


async def query_route_stations(
    train_no: str, from_code: str, to_code: str, train_date: str
) -> list[dict]:
    """实时经停站查询（失败返回空列表，不抛异常）。"""
    await ensure_loaded()
    try:
        async def fetch():
            return parse_mcp_result(await get_train_route_stations_validated({
                "train_no": train_no,
                "from_station": from_code,
                "to_station": to_code,
                "train_date": normalize_date(train_date),
            }))
        data = await _operation("stops.mcp", fetch(), success=lambda result: result.get("success"))
        if data.get("success"):
            return data.get("stations", []) or []
    except Exception:
        pass
    return []


# ---------- 权威车次身份（train_no + 起讫站）----------
#
# 2026-09-15 增补。为什么需要它：
# 旧实现用 **2022 年停更的离线车次目录**推断起讫站，再拿这个 OD 去查余票。
# G1 在离线目录里被判成「北京南→上海」，实际终点是「上海虹桥」，
# 于是余票接口对该 OD 无结果 → 整条链路降级成"静态归属"，**一个经停站都拿不到**。
# 实测 12 个常见车次：旧路径仅 1/12 可用。
#
# 12306 官方车次搜索接口可直接给出权威 train_no 与起讫站（~0.1s）：
#   https://search.12306.cn/search/v1/train/search?keyword=G1&date=20260915
# ⚠️ date 必须是 **YYYYMMDD**（带横杠返回空数组，这是最容易踩的坑）。

_SEARCH_URL = "https://search.12306.cn/search/v1/train/search"
# (车次, 日期) → (train_no, from_station, to_station)；train_no 在调图时可能变化，故带 TTL
_TRAIN_ID_CACHE: dict[tuple[str, ...], tuple[float, tuple[str, str, str]]] = {}
_TRAIN_ID_TTL_S = 3600
_TRAIN_ID_CACHE_MAX = 500


def _search_date(date_str: str) -> str:
    return str(date_str or "").replace("-", "")


@_shared_query("identity")
async def search_train_identity(train_code: str, train_date: str) -> tuple[str, str, str] | None:
    """车次号 → (train_no, 起始站名, 终到站名)；查不到返回 None。

    结果按 (车次, 日期) 缓存 1 小时：同一会话里连续问同一趟车不会重复打 12306。
    """
    code = (train_code or "").strip().upper()
    date_str = normalize_date(train_date)
    if not code or not date_str:
        return None
    key = _scoped_key(code, date_str)
    hit = _cache_get(_TRAIN_ID_CACHE, key, _TRAIN_ID_TTL_S, "rt.identity")
    if hit is not None:
        return hit

    from app.tools._http import BROWSER_HEADERS, get_client

    headers = dict(BROWSER_HEADERS)
    headers.update({
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.12306.cn/index/",
    })
    try:
        client = await get_client()
        async def fetch_identity():
            resp = await client.get(
                _SEARCH_URL,
                headers=headers,
                params={"keyword": code, "date": _search_date(date_str)},
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()
        payload = await _operation("identity.query", fetch_identity(),
                                   success=lambda result: isinstance(result.get("data"), list))
    except Exception:
        return None

    for row in payload.get("data") or []:
        # keyword 是前缀匹配（搜 G1 会带回 G10/G13…）→ 必须精确比对车次号
        if str(row.get("station_train_code", "")).upper() != code:
            continue
        train_no = str(row.get("train_no") or "").strip()
        if not train_no:
            continue
        value = (train_no, str(row.get("from_station") or ""), str(row.get("to_station") or ""))
        _cache_put(_TRAIN_ID_CACHE, key, value, _TRAIN_ID_TTL_S, _TRAIN_ID_CACHE_MAX, "rt.identity")
        return value
    return None


async def resolve_train_identity(
    train_code: str, train_date: str
) -> dict | None:
    """车次号 → {train_no, from_station, to_station, from_code, to_code}（车站码解析失败则为空串）。"""
    ident = await search_train_identity(train_code, train_date)
    if not ident:
        return None
    train_no, from_name, to_name = ident
    out = {
        "train_no": train_no,
        "from_station": from_name,
        "to_station": to_name,
        "from_code": "",
        "to_code": "",
    }
    for name, key_code in ((from_name, "from_code"), (to_name, "to_code")):
        if not name:
            continue
        try:
            res = await resolve_station_code(name)
        except Exception:
            res = None
        if res:
            out[key_code] = res[0]
    return out


# 经停表缓存：(train_no, date) → (取数时刻, 站序表)
# 图定表一天内不会变；缓存可显著降低 12306 压力（余票接口对高频访问会限流）
_STOPS_CACHE: dict[tuple[str, ...], tuple[float, list[dict]]] = {}
_STOPS_TTL_S = 3600
_STOPS_CACHE_MAX = 300


@_shared_query("stops")
async def query_stops_by_train_no(
    train_no: str, from_code: str, to_code: str, train_date: str
) -> list[dict]:
    """按 train_no 查**全经停表**（12306 图定表；已发车车次同样有数据）。

    与 `query_route_stations` 的区别：那个是"按 OD 找车次再取站序"的旁路，
    这个是直接用权威 train_no 查，**不依赖该车次是否还在售票**（实测 0.1–0.2s，
    且对过去/今天/未来日期返回同一份图定表）。因此"问经停"不该被余票接口的
    限流/反爬牵连。
    """
    if not train_no:
        return []
    parts = (str(train_no), normalize_date(train_date))
    key = _scoped_key(*parts, from_code, to_code) if _main() else parts
    hit = _cache_get(_STOPS_CACHE, key, _STOPS_TTL_S, "rt.stops")
    if hit is not None:
        return hit
    stops = await query_route_stations(train_no, from_code, to_code, train_date)
    if stops:
        _cache_put(_STOPS_CACHE, key, stops, _STOPS_TTL_S, _STOPS_CACHE_MAX, "rt.stops")
    return stops




# ---------- 车站大屏（12306 官方 bigScreen 接口）----------
#
# 端点与逐字段探测见 docs/datasources.md。三个必须守住的点：
#   1) **必须 POST form-body**：同一 URL 用 GET 恒返回
#      {"status":false,"errorMsg":"系统忙，请稍后重试！(M0003)"}，极易被误判成接口故障；
#   2) 无需 Cookie / Referer / 签名（实测裸请求即可，UA 可有可无）；
#   3) 超出可查日期窗口时返回 status:true + []，与"该站当日确实没有车"**无法区分** ——
#      必须由调用方显式处理，否则会对用户谎报"该站当日无到发车次"。

SCREEN_URL = "https://mobile.12306.cn/wxxcx/wechat/bigScreen/queryTrainByStation"
# 实测可查范围约为今日 ±1 周（窗口边缘还可能出现部分数据），超出即返回空数组。
# 见 docs/datasources.md §5.1
SCREEN_WINDOW_DAYS = 7

# (电报码, 日期) → (取数时刻, 原始行列表)
# 大屏数据约每分钟刷新一次（响应自带 base_datetime）。TTL 取 **10 分钟**：
# 该接口一次 0.5–2s、单站 200–700 条，同一会话里连续追问（"那下午呢""那到达呢"）
# 若按分钟级 TTL 会反复打 12306；10 分钟内复用同一份快照对"今天有哪些车"这类
# 问题完全够用（列车时刻不会在 10 分钟内变，站台/晚点变化由 note 里的快照时间兜底）。
_SCREEN_CACHE: dict[tuple[str, ...], tuple[float, list[dict]]] = {}
_SCREEN_TTL_S = 600
_SCREEN_CACHE_MAX = 40


def _hhmm(v) -> str | None:
    """'06:30' → '06:30'；'----'/'' → None。"""
    s = str(v or "").strip()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", s)
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


def _hhmm_from_compact(v) -> str | None:
    """'0630' → '06:30'；'----'/'' → None（update_arrive_time 是紧凑写法）。"""
    s = str(v or "").strip()
    m = re.fullmatch(r"(\d{2})(\d{2})", s)
    return f"{m.group(1)}:{m.group(2)}" if m else None


def parse_jiaolu(raw) -> list[dict]:
    """解析 `jiaolu_train`：该车底**当日整日套跑交路**。

    形如 `G1|北京南|06:30|上海虹桥|11:24#G1956/7|上海虹桥|11:44|太原南|21:26#`，
    段间用 `#` 连接，字段用 `|` 分隔。
    """
    out: list[dict] = []
    for part in str(raw or "").split("#"):
        cols = [c.strip() for c in part.split("|")]
        if len(cols) < 5 or not cols[0]:
            continue
        out.append({
            "train": cols[0], "from_station": cols[1], "depart_time": cols[2],
            "to_station": cols[3], "arrive_time": cols[4],
        })
    return out


# `train_style` 的尾部数字是**定员（座位数）**，不是车组号 —— 实测 119/121 行与
# `train_limit` 完全相等（CR400BF-BS_1346 ↔ train_limit=1346；CRH2E_642 ↔ 642）。
# 把定员并进"型号"会被误读成车组号（本项目的探测文档曾据此误判过一次），故必须拆开。
# 反例 `CR200J_16`（train_limit=918）是动力集中动车组的编组辆数写法 → 保留原文。
_STYLE_SUFFIX_RE = re.compile(r"^(?P<model>.+?)_(?P<num>\d+)$")


def split_train_style(raw, train_limit=None) -> tuple[str, int | None]:
    """`CR400BF-BS_1346` → ("CR400BF-BS", 1346)；后缀非定员时返回 (原文, 定员或 None)。"""
    text = str(raw or "").strip()
    if not text:
        return "", (_safe_int(train_limit) if str(train_limit or "").strip().isdigit() else None)
    m = _STYLE_SUFFIX_RE.match(text)
    limit = str(train_limit or "").strip()
    if not m:
        return text, (int(limit) if limit.isdigit() else None)
    num = int(m.group("num"))
    if limit.isdigit() and int(limit) == num:
        return m.group("model"), num
    return text, (int(limit) if limit.isdigit() else None)


def normalize_screen_row(row: dict, station_code: str) -> dict:
    """把大屏原始行归一化成紧凑结构（只保留可作答/可展示的字段）。

    方向判据（实测，见文档 §3）：
      本站始发 `start_station_telecode == 本站` → departure（此时 arrive_time 为 '----'）
      本站终到 `end_station_telecode == 本站`   → arrival
      过路     两者都不等于本站                 → through
    """
    code = str(station_code or "").strip().upper()
    start_code = str(row.get("start_station_telecode") or "").strip().upper()
    end_code = str(row.get("end_station_telecode") or "").strip().upper()
    if start_code == code:
        direction = "departure"
    elif end_code == code:
        direction = "arrival"
    else:
        direction = "through"

    _own_model, _capacity = split_train_style(row.get("train_style"), row.get("train_limit"))

    return {
        "train": str(row.get("station_train_code") or "").strip(),
        "train_no": str(row.get("train_no") or "").strip(),
        "from_station": str(row.get("start_station_name") or "").strip(),
        "to_station": str(row.get("end_station_name") or "").strip(),
        "arrive_time": _hhmm(row.get("arrive_time")),
        "depart_time": _hhmm(row.get("start_time")),
        # 实际/预计到发（未更新时为 None）——"正晚点"类问题的唯一依据
        "actual_arrive": _hhmm_from_compact(row.get("update_arrive_time")),
        "actual_depart": _hhmm_from_compact(row.get("update_start_time")),
        "platform": str(row.get("platform_no") or "").strip().rstrip("#"),
        "station_no": str(row.get("station_no") or "").strip(),
        "direction": direction,
        "train_class": str(row.get("train_class_name") or "").strip(),
        "train_type": str(row.get("train_type_name") or "").strip(),   # 直通/局管
        # 担当（12306 官方口径；只到"型号"，拿不到单组车号）
        # 车底：`jiaolu_train_style` 是**交路链**上的型号；`train_style` 是**本车底**标识
        # （型号 + 定员）。两者实测有 217/483 行不一致，故都保留、不静默二选一：
        #   rolling_stock      主展示值（优先交路型号，退回本车底型号）
        #   rolling_stock_own  本车底型号（仅在与主值不同时给出，便于交叉核对）
        #   capacity            定员（座位数）—— 来自 train_limit，与 train_style 后缀互证
        "rolling_stock": str(row.get("jiaolu_train_style") or "").strip()
                         or _own_model,
        "rolling_stock_own": _own_model if _own_model and _own_model != str(
            row.get("jiaolu_train_style") or "").strip() else "",
        "capacity": _capacity,
        "corporation": str(row.get("jiaolu_corporation_code") or "").strip(),   # 客运段
        "depot": str(row.get("jiaolu_dept_train") or "").strip(),               # 车辆段
        "bureau": str(row.get("bureau_code") or "").strip(),
        "route": parse_jiaolu(row.get("jiaolu_train")),
        "run_time": str(row.get("running_time") or "").strip(),
        "distance": str(row.get("distance") or "").strip(),
        "stopover_min": _safe_int(row.get("stopover_time")),
        "train_date": str(row.get("station_train_date") or "").strip(),
        "snapshot_at": str(row.get("base_datetime") or "").strip(),
    }


async def _fetch_station_screen(station_code: str, date_str: str) -> list[dict]:
    """POST 车站大屏接口，返回原始行列表；失败抛 Realtime12306Error。"""
    from app.tools._http import BROWSER_HEADERS, get_client

    headers = dict(BROWSER_HEADERS)
    headers["Content-Type"] = "application/x-www-form-urlencoded"
    try:
        client = await get_client()
        resp = await client.post(
            SCREEN_URL,
            headers=headers,
            # ⚠️ 必须 POST：改成 GET 会稳定返回 (M0003)"系统忙"
            data={
                "train_start_date": _search_date(date_str),
                "train_station_code": station_code,
            },
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:  # noqa: BLE001 —— 统一归一化为可读错误
        raise Realtime12306Error(
            f"12306 车站大屏接口不可达（{type(e).__name__}）"
        ) from e

    if not payload.get("status"):
        raise Realtime12306Error(
            str(payload.get("errorMsg") or "12306 车站大屏返回 status=false")
        )
    return list(payload.get("data") or [])


@_shared_query("screen")
async def query_station_screen_rows(station_code: str, train_date: str) -> list[dict]:
    """车站大屏**原始行**（带 10 分钟缓存）。

    注意空结果的语义：`status:true` + `[]` 既可能是"该站当日无到发车次"，
    也可能是"查询日期超出可查窗口"或"电报码不存在"，三者无法区分 ——
    调用方必须显式处理（见 `screen_window_hint()`）。
    """
    code = str(station_code or "").strip().upper()
    date_str = normalize_date(train_date)
    if not code or not date_str:
        return []

    key = _scoped_key(code, date_str)
    hit = _cache_get(_SCREEN_CACHE, key, _SCREEN_TTL_S, "rt.screen")
    if hit is not None:
        return hit

    rows = await _operation("screen.query", _fetch_station_screen(code, date_str))
    _cache_put(_SCREEN_CACHE, key, rows, _SCREEN_TTL_S, _SCREEN_CACHE_MAX, "rt.screen")
    return rows


async def query_station_screen(station_code: str, train_date: str) -> list[dict]:
    """车站大屏（归一化后的到发记录列表）。"""
    rows = await query_station_screen_rows(station_code, train_date)
    return [normalize_screen_row(r, station_code) for r in rows]


def screen_window_hint(date_str: str) -> str:
    """查询日期明显落在可查窗口外时，返回一句"别把空结果说成没车"的提醒（否则空串）。"""
    from datetime import date as _date

    try:
        target = _date.fromisoformat(normalize_date(date_str))
    except Exception:  # noqa: BLE001
        return ""
    days = (target - _date.today()).days
    if abs(days) <= SCREEN_WINDOW_DAYS:
        return ""
    return (
        f"查询日期距今日 {days:+d} 天，已超出车站大屏实测可查范围（约 ±{SCREEN_WINDOW_DAYS} 天）；"
        "12306 对「超出窗口」「电报码不存在」与「该站当日确实无车」同样返回空数组，"
        "三者无法区分 —— 不得据此断言该站当日没有到发车次"
    )


# ---------- 官方车组号（12306「车厢信息」接口 getCarDetail）----------
#
# 这是我们旗舰能力「今天 G1 由哪组担当」的**第二个独立来源**（第一个是 rail.re）。
# 实测（2026-09-15）：
#   G1 → carCode=CR400BF-A-5159（rail.re 写作 CR400BFA-5159，同一组，少一个连字符）
#   G3 → CR400BF-BS-5277（rail.re: CR400BFBS-5277）
# 两个坑（都实测踩过）：
#   1) **必须用 GET + 完整查询串**（POST form 会返回 `content.errorCode=10000 系统错误`）；
#   2) 外层 `status` **不可作成败判据** —— 实测 status=0 时 content.data 仍有完整数据，
#      且有**间歇性**返回空 data（需要重试）。
CAR_DETAIL_URL = ("https://mobile.12306.cn/wxxcx/openplatform-inner/miniprogram/wifiapps/"
                  "appFrontEnd/v2/lounge/open-smooth-common/trainStyleBatch/getCarDetail")
_CAR_DETAIL_CACHE: dict[tuple[str, ...], tuple[float, dict]] = {}
_CAR_DETAIL_TTL_S = 3600
_CAR_DETAIL_CACHE_MAX = 200


@_shared_query("car_detail")
async def get_car_detail(train_code: str, train_date: str) -> dict | None:
    """车次 → 12306 官方车组号与车厢明细；取不到返回 None（调用方决定降级）。

    返回：{car_code, car_type, coaches: [{index, label}], total_coaches, source}
    """
    code = str(train_code or "").strip().upper()
    day = normalize_date(train_date or "")
    if not code or not day:
        return None
    key = _scoped_key(code, day)
    hit = _cache_get(_CAR_DETAIL_CACHE, key, _CAR_DETAIL_TTL_S, "rt.car_detail")
    if hit is not None:
        return hit or None

    from app.tools._http import BROWSER_HEADERS, get_client

    headers = dict(BROWSER_HEADERS)
    headers["Accept"] = "application/json, text/plain, */*"
    headers["Referer"] = "https://mobile.12306.cn/"

    async def _once() -> dict | None:
        client = await get_client()
        resp = await client.get(CAR_DETAIL_URL, headers=headers, params={
            "carCode": "", "trainCode": code,
            "runningDay": day.replace("-", ""), "reqType": "form",
        }, timeout=12)
        resp.raise_for_status()
        payload = resp.json()
        # ⚠️ 判据看 content.data，而不是外层 status（见上方注释 2）
        data = ((payload.get("content") or {}).get("data") or {})
        car_code = str(data.get("carCode") or "").strip()
        if not car_code:
            return None
        coaches = []
        for i, item in enumerate(data.get("coachPicList") or []):
            coaches.append({"index": item.get("picOrder", i),
                            "label": str(item.get("pictureName") or "").strip()})
        return {
            "car_code": car_code,
            "car_type": str(data.get("carType") or "").strip(),
            "train_style": str(data.get("trainStyle") or "").strip(),
            "coaches": coaches,
            "total_coaches": len(coaches),
            "source": "12306 官方（getCarDetail）",
        }

    result = None
    for attempt in range(2):      # 实测有间歇性空返回 → 重试一次
        try:
            result = await _operation("car_detail.query", _once(), success=bool)
        except Exception:  # noqa: BLE001 —— 官方通道失败不影响 rail.re 主路径
            result = None
        if result:
            break
        if attempt == 0:
            import asyncio as _asyncio

            await _asyncio.sleep(0.6)

    # Missing/failed responses must not pin a one-hour negative result in Main.
    if result or not _main():
        _cache_put(_CAR_DETAIL_CACHE, key, result or {}, _CAR_DETAIL_TTL_S,
                   _CAR_DETAIL_CACHE_MAX, "rt.car_detail")
    return result


def normalize_car_code(raw: str) -> str:
    """车组号归一化（用于**跨来源比较**）：去掉连字符与空格、转大写。

    rail.re 写 `CR400BFA-5159`，12306 官方写 `CR400BF-A-5159` —— 实为同一组车，
    直接字符串比较会误判成"分歧"，故比较前必须归一化。
    """
    return re.sub(r"[\s\-]", "", str(raw or "")).upper()


def infer_endpoints_from_offline(train_code: str) -> tuple[str, str] | None:
    """缺起止站时，用离线车次目录推断 origin→destination 站名。

    仅用于定位，不作为实时数据返回。
    """
    try:
        from app.data.train_db import TrainDB, get_train_db

        tdb: TrainDB = get_train_db()
        tdb._ensure_loaded()
        info = tdb.lookup(train_code)
        if info and info.get("from_station") and info.get("to_station"):
            return info["from_station"], info["to_station"]
    except Exception:
        pass
    return None


_ALL_STATIONS_CACHE: dict[str, dict] = {}


def all_stations() -> dict[str, dict]:
    """全部站点索引：站名 → {code, city, pinyin, py_short, num}（**结果缓存，只读**）。

    用途：mcp 的 `search_stations_validated` **硬上限 10 条**（实测 limit=80 仍只回 10），
    导致"列出北京/苏州的主要车站"这类需求拿到的是"房山东、后吕村"而漏掉北京丰台、清河
    （R1 E08）。本地索引让我们能按 `city` 同城归组、按 `num`（12306 站序≈重要度）自行排序。

    **为什么要缓存**（perf P2-1）：这是"包内静态站点表"的只读投影，`ensure_loaded()` 之后
    不再变化，却被热路径反复取用 —— 每次重建 3384 条字典实测 **1.6 ms**，
    按 §7.7 的统计一次问答要取 2–4 次。缓存后命中成本可忽略。

    **未加载时返回空字典且不缓存**（与 `station_name_set` 同一条纪律）：否则会把
    "调用方忘了先 `ensure_loaded()`"固化成永久空表，表现为"站名一律查不到"的静默退化 ——
    这个坑在 `fastpath._station_in_text` 的 docstring 里已经踩过一次。

    调用方**不得修改**返回值（会污染全局缓存）。
    """
    global _ALL_STATIONS_CACHE
    if _ALL_STATIONS_CACHE:
        return _ALL_STATIONS_CACHE
    try:
        from mcp_12306.services import ticket_service as ts

        raw = getattr(ts.station_service, "stations", None) or []
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, dict] = {}
    for st in raw:
        name = getattr(st, "name", None)
        if not name:
            continue
        out[str(name)] = {
            "code": str(getattr(st, "code", "") or ""),
            "city": str(getattr(st, "city", "") or ""),
            "pinyin": str(getattr(st, "pinyin", "") or ""),
            "py_short": str(getattr(st, "py_short", "") or ""),
            "num": _safe_int(getattr(st, "num", None)),
        }
    if out:
        _ALL_STATIONS_CACHE = out
    return out


_STATION_NAME_SET: set[str] = set()


def station_name_set() -> set[str]:
    """全部站名的**集合**（判"这是不是一个真实站名"用；结果缓存）。

    为什么单独给一个缓存的集合：`all_stations()` 每次调用都要重建 3384 条字典，
    而"逐候选站名判真伪"是要**反复**查的（起讫站解析、多轮继承的区间校验），
    一次问答里会被调几十次。

    **库未加载时返回空集且不缓存** —— 否则"调用方忘了先 `ensure_loaded()`"会被
    永久固化成一个空集合，表现为"站名一律判假"的静默退化（这个坑
    `fastpath._station_in_text` 的 docstring 里已经踩过一次）。
    """
    global _STATION_NAME_SET
    if _STATION_NAME_SET:
        return _STATION_NAME_SET
    names = {n for n in all_stations() if n}
    if names:
        _STATION_NAME_SET = names
    return names


def _safe_int(v) -> int:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return 10**9          # 无站序的排到最后


def all_station_names() -> dict[str, str]:
    """站点名 → 电报码（仅名称与码，供"错别字纠正"这类建议使用）。

    数据来自 mcp 站点库；未加载或加载失败时返回空 dict（调用方自行降级）。
    """
    try:
        from mcp_12306.services import ticket_service as ts

        stations = getattr(ts.station_service, "stations", None) or []
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, str] = {}
    for st in stations:
        try:
            name = st.get("name") if isinstance(st, dict) else getattr(st, "name", None)
            code = st.get("code") if isinstance(st, dict) else getattr(st, "code", None)
        except Exception:  # noqa: BLE001
            continue
        if name and code:
            out[str(name)] = str(code)
    return out
