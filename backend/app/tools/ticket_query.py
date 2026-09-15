"""余票查询工具（实时，基于 12306）。

直接通过 mcp-server-12306 查询 12306 官方余票接口，
不依赖 T12306_BASE 反代（那是备选路径）。

用法：
- 必须提供出发站与到达站（中文名/拼音/电报码均可）
- 日期支持"今天/明天/9月14日/2026-09-14"等表述
"""
from __future__ import annotations

import re

from app.config import get_settings
from app.dates import date_note, normalize_date
from app.tools import _rt12306 as rt
from app.tools._rt12306 import Realtime12306Error
from app.tools._http import format_error
from app.tools.base import Tool, ToolResult


def _seat_summary(seats: dict, limit: int = 4) -> str:
    if not seats:
        return "席别信息缺失"
    parts = [f"{k}={v}" for k, v in seats.items() if v and v not in ("无", "--")]
    return " ".join(parts[:limit]) or "无余票"



def _now_iso() -> str:
    """数据采样时刻（UTC ISO8601）——低余量数据存在分钟级漂移，必须能标注采样时间。"""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _presale_hint(date_str: str) -> str:
    """若查询日期距今超过 12306 预售期，返回一句可操作说明（否则空串）。

    2026-09-14 修复 C08/L03：此前"超预售期查不到"被模型转述成"工具调用失败"，
    把"数据本身不存在"说成"系统坏了"，会误导用户去反复重试。
    """
    from datetime import date as _date

    try:
        target = _date.fromisoformat(date_str)
    except Exception:  # noqa: BLE001
        return ""
    days = (target - _date.today()).days
    limit = get_settings().ticket_presale_days
    if days > limit:
        return (
            f"该日期距今 {days} 天，超出 12306 预售期（当前约 {limit} 天），"
            "属正常查不到而非系统故障，请在开售后再查"
        )
    return ""



# ---- 时段/席别/车种过滤（2026-09-14：把"集合运算"从模型侧挪到工具侧）----

def _to_minutes(hhmm: str) -> int | None:
    """'07:12' → 432（分钟）；非法返回 None。"""
    m = re.match(r"^(\d{1,2}):(\d{2})$", (hhmm or "").strip())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 24 or mi > 59:
        return None
    return h * 60 + mi


_PERIODS = (
    ("凌晨(00-06)", 0, 360), ("上午(06-12)", 360, 720),
    ("下午(12-17)", 720, 1020), ("晚上(17-24)", 1020, 1440),
)


def _apply_filters(trains: list[dict], params: dict) -> tuple[list[dict], dict]:
    """按 after_time/before_time/seat/train_type 过滤，返回 (结果, 已应用条件说明)。"""
    out = trains
    filters: dict = {}

    after = _to_minutes(str(params.get("after_time") or ""))
    before = _to_minutes(str(params.get("before_time") or ""))
    if after is not None:
        filters["发车时刻≥"] = str(params.get("after_time"))
    if before is not None:
        filters["发车时刻<"] = str(params.get("before_time"))

    seat_key = str(params.get("seat") or "").strip().lower()
    if seat_key:
        filters["席别"] = seat_key

    ttype = str(params.get("train_type") or "").strip().upper()
    prefixes = tuple(x.strip() for x in ttype.split(",") if x.strip())
    if prefixes:
        filters["车种"] = "/".join(prefixes)

    if after is not None or before is not None:
        kept = []
        for t in out:
            cur = _to_minutes(str(t.get("start_time") or ""))
            if cur is None:
                continue                      # 时刻未知的不参与时段筛选
            if after is not None and cur < after:
                continue
            if before is not None and cur >= before:
                continue
            kept.append(t)
        out = kept

    if seat_key:
        out = [
            t for t in out
            if any(seat_key in str(k).lower() and str(v) not in ("", "无", "--", "0")
                   for k, v in (t.get("seats") or {}).items())
        ]

    if prefixes:
        out = [t for t in out if str(t.get("train_no", "")).upper().startswith(prefixes)]

    # 按发车时刻排序（同一时段内更好读，也让"前 N 条"更有意义）
    out = sorted(out, key=lambda t: _to_minutes(str(t.get("start_time") or "")) or 0)
    return out, filters


def _period_counts(trains: list[dict]) -> dict:
    """按时段分组计数（供"晚上还有吗"这类问题在明细被裁时仍能回答）。"""
    counts: dict = {}
    for name, lo, hi in _PERIODS:
        counts[name] = sum(
            1 for t in trains
            if (_to_minutes(str(t.get("start_time") or "")) or -1) >= lo
            and (_to_minutes(str(t.get("start_time") or "")) or -1) < hi
        )
    return counts


def _seat_counts(trains: list[dict], limit: int = 8) -> dict:
    """按席别统计"有票"趟数（键名取自 12306 返回，做截断展示）。"""
    counts: dict = {}
    for t in trains:
        for k, v in (t.get("seats") or {}).items():
            if str(v) in ("", "无", "--", "0"):
                continue
            counts[k] = counts.get(k, 0) + 1
    return dict(sorted(counts.items(), key=lambda x: -x[1])[:limit])


def _is_low_count(v) -> bool:
    """是否为"低余量"（数字且 ≤3）——这类数据分钟级就会变，值得二次校验。"""
    try:
        return int(str(v).strip()) <= 3
    except (TypeError, ValueError):
        return False


def _low_seat_hits(trains: list[dict]) -> list[tuple[str, str, str]]:
    out = []
    for t in trains:
        for k, v in (t.get("seats") or {}).items():
            if _is_low_count(v):
                out.append((str(t.get("train_no", "")), str(k), str(v)))
    return out


class TicketQueryTool(Tool):
    name = "ticket.query"
    description = "查询两站间实时余票与车次（12306 官方接口，无需反代）"

    async def invoke(self, params: dict) -> ToolResult:
        from_raw = (params.get("from_station") or params.get("from") or "").strip()
        to_raw = (params.get("to_station") or params.get("to") or "").strip()
        raw_date = params.get("date") or params.get("time")
        date_str = normalize_date(raw_date)
        # 用户给了时间表述但无法识别 → 如实说明已按今天查询，绝不静默替换
        date_warn = date_note(raw_date, date_str)
        train_filter = (params.get("train") or "").strip().upper()
        limit = int(params.get("limit") or 10)
        # 过滤条件由检索层从用户原话解析后传入（如"晚上"→after_time=17:00、"二等座"→seat=second_class）
        want_filters = {
            "after_time": params.get("after_time") or "",
            "before_time": params.get("before_time") or "",
            "seat": params.get("seat") or "",
            "train_type": params.get("train_type") or "",
        }

        if not from_raw or not to_raw:
            return ToolResult(
                ok=False,
                error="缺少参数：from_station / to_station（出发站与到达站）",
                note="例如：from_station=北京, to_station=上海, date=明天",
            )

        try:
            from_res = await rt.resolve_station_code(from_raw)
            to_res = await rt.resolve_station_code(to_raw)
            if not from_res:
                return ToolResult(ok=False, error=f"无法识别出发站：{from_raw}")
            if not to_res:
                return ToolResult(ok=False, error=f"无法识别到达站：{to_raw}")

            from_code, from_name = from_res
            to_code, to_name = to_res
            trains = await rt.query_tickets(from_code, to_code, date_str)
        except Realtime12306Error as e:
            presale = _presale_hint(date_str)
            return ToolResult(
                ok=False,
                error=f"12306 余票查询失败：{e}",
                note=("需境内网络 + Python ≥ 3.10；可稍后重试"
                      + (f"；另：{presale}" if presale else "")),
            )
        except Exception as e:  # noqa: BLE001
            presale = _presale_hint(date_str)
            return ToolResult(
                ok=False,
                error=f"12306 余票查询异常：{format_error(e)}",
                note=("需境内网络 + Python ≥ 3.10；可稍后重试"
                      + (f"；另：{presale}" if presale else "")),
            )

        total_all = len(trains)

        if train_filter:
            trains = [
                t for t in trains
                if str(t.get("train_no", "")).upper() == train_filter
            ]

        # ---- 集合运算在工具侧完成：按时段/席别/车种过滤（不把全集丢给模型）----
        trains, applied = _apply_filters(trains, want_filters)

        if not trains:
            presale = _presale_hint(date_str)
            if applied:
                return ToolResult(
                    ok=False,
                    error=(
                        f"{from_name}→{to_name}（{date_str}）该区间共 {total_all} 趟，"
                        "但**没有符合筛选条件的车次**（" 
                        + "、".join(f"{k}{v}" for k, v in applied.items()) + "）"
                    ),
                    note=(
                        "数据本身存在，只是不满足本次筛选条件；"
                        "如需可放宽条件（如换时段或换席别）后重查"
                        + (f"；{presale}" if presale else "")
                    ),
                    total=total_all, shown=0, truncated=False, filters=applied,
                )
            return ToolResult(
                ok=False,
                error=f"{from_name}→{to_name}（{date_str}）未查询到"
                      + (f"车次 {train_filter}" if train_filter else "余票数据"),
                note=(
                    "12306 实时接口正常，但该区间/车次无数据"
                    + (f"；{presale}" if presale else "")
                ),
                total=total_all, shown=0,
            )

        matched = len(trains)
        shown = trains[:limit]

        # ---- 低余量二次校验（R1 P2-8）----
        # "余 1 张"这类低余量在分钟级就会变（R1 实测 G4 一等座 1 张 → 4 分钟后为无）。
        # 只在**确有低余量**时多查一次并比对，把"数据在变"如实告诉用户，而不是给一个假精确的旧值。
        verify_note = ""
        low_hits = _low_seat_hits(shown)
        if low_hits:
            try:
                again = await rt.query_tickets(from_code, to_code, date_str)
                index = {str(t.get("train_no", "")).upper(): t for t in again}
                changes = []
                for t in shown:
                    other = index.get(str(t.get("train_no", "")).upper())
                    if not other:
                        continue
                    for k, v in (t.get("seats") or {}).items():
                        ov = (other.get("seats") or {}).get(k)
                        if ov is not None and str(ov) != str(v) and _is_low_count(v):
                            changes.append(f"{t.get('train_no')} {k}: {v}→{ov}")
                if changes:
                    verify_note = (
                        "；**低余量数据在两次采样间发生变化**（余票实时变动）："
                        + "；".join(changes[:5])
                        + "，请以 12306 实时显示为准"
                    )
                else:
                    verify_note = "；低余量席别已二次校验（两次采样一致，仍可能随时变化）"
            except Exception as e:  # noqa: BLE001 —— 校验失败不影响主结果
                verify_note = f"；低余量二次校验未完成（{format_error(e)}）"
        lines = [
            f"{t.get('train_no','')} {t.get('from_station','')}→{t.get('to_station','')} "
            f"{t.get('start_time','')}-{t.get('arrive_time','')} "
            f"历时{t.get('duration','')} | {_seat_summary(t.get('seats', {}))}"
            for t in shown
        ]
        period_counts = _period_counts(trains)
        seat_counts = _seat_counts(trains)
        truncated = matched > len(shown)

        # 摘要行把"命中/展示/分布/过滤"说清楚：即使明细被裁，模型也能答"存在性/计数"问题
        head = (
            f"{from_name}→{to_name}（{date_str}）：区间共 {total_all} 趟"
            + (f"，按条件筛选后 {matched} 趟" if applied else "")
            + f"，本次展示 {len(shown)} 趟"
            + ("（**已截断**）" if truncated else "")
            + ("；过滤条件：" + "、".join(f"{k}{v}" for k, v in applied.items()) if applied else "")
        )
        dist = "；".join(f"{name} {cnt} 趟" for name, cnt in period_counts.items() if cnt)
        seat_line = "；".join(f"{k}={v} 趟有票" for k, v in seat_counts.items())

        return ToolResult(
            ok=True,
            data={
                "from_station": from_name,
                "to_station": to_name,
                "from_code": from_code,
                "to_code": to_code,
                "train_date": date_str,
                "count": matched,
                "count_all": total_all,
                "trains": shown,          # 只下发展示过的明细，避免下游误当全集
                "trains_shown": len(shown),
                "period_counts": period_counts,
                "seat_counts": seat_counts,
                "applied_filters": applied,
            },
            text=(
                head + "\n"
                + (f"发车时段分布：{dist}\n" if dist else "")
                + (f"有票席别统计：{seat_line}\n" if seat_line else "")
                + "明细：\n" + "\n".join(lines)
            ),
            sources=["https://kyfw.12306.cn/otn/leftTicket/queryI"],
            note=(f"12306 实时余票（{date_str}）" + (f"；{date_warn}" if date_warn else "") + verify_note),
            total=matched, shown=len(shown), truncated=truncated,
            filters=applied, fetched_at=_now_iso(),
        )