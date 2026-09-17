"""rail.re 动车组交路（担当车组）查询工具。

通过 rail.re 公开 API（api.rail.re）查询车次与动车组的对应关系：
- GET {API}/train/{车次}   → 该车次历史担当车组（含日期）
- GET {API}/emu/{车组号}   → 该车组历史担当车次（含日期）

这是 12306 公开接口不提供的信息（12306 只返回"高速"等大类），
用于回答"今天 G1 由哪组 CR400AF/CR400BF 担当"这类车迷核心问题。

一致性与诚实性约定（M11.1 修复）
--------------------------------
1. **输入校验**：车次必须是 `G1/D27/C1234` 形态；车组号必须是 `CR400AF…/CRH2A…` 形态。
   把车次传进车组槽位（或反之）不再静默发请求。
2. **结果必须与查询键一致**：返回记录会按查询键过滤，绝不把"接口顺手返回的其它车次/车组"
   混进结论。
3. **车型前缀 ≠ 单台车组**：`/emu/CR400AF` 会被 api.rail.re 前缀匹配到**多台**车组，
   早期实现把它们合并成"车组 CR400AF 今日担当 G2863…"这一**单一结论**（错误）。
   现在会识别为 series 模式，如实给出"匹配到 N 台车组"并按车组分别列出。
"""
from __future__ import annotations

import json
import re
from urllib.parse import quote
from datetime import date, datetime

import httpx

from app.config import get_settings
from app.dates import normalize_date
from app.tools._http import BROWSER_HEADERS, format_error, get_client
from app.tools import _rt12306 as rt
from app.tools.base import Tool, ToolResult

# 车次号与动车组判定统一来自 _rt12306（含普速 K/T/Z 与纯数字车次），避免两处口径漂移
from app.tools._rt12306 import (  # noqa: E402
    _EMU_TYPE_RE,
    _TRAIN_CODE_RE,
)

# 车组号（紧凑形态）：CR400BFA5054 / CR400AF0207 / CRH2A2034 等
_EMU_NO_RE = re.compile(r"^(CR|CRH)[0-9A-Za-z]+$", re.I)


_FOCUS_UNITS: dict[str, list[str]] = {}


def _fmt_emu_no(raw: str) -> str:
    """把 rail.re 的紧凑车组号格式化为可读形式。

    CR400BFA5054 -> CR400BF-A-5054
    CR400AF0207  -> CR400AF-0207
    CRH2A2034    -> CRH2A-2034
    """
    s = (raw or "").strip().upper()
    m = re.match(r"^(CRH?\d*[A-Z]*?)(\d{3,5}[A-Z]?)$", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return s


def _split_date_time(v: str) -> tuple[str, str]:
    """'2026-09-13 11:24' -> ('2026-09-13', '11:24')"""
    try:
        dt = datetime.strptime(v.strip(), "%Y-%m-%d %H:%M")
        return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")
    except Exception:
        return v.strip(), ""


class EmuRoutingTool(Tool):
    name = "emu.routing"
    description = "查询车次担当车组/车组担当车次（rail.re 交路数据，12306 不提供）"

    async def invoke(self, params: dict) -> ToolResult:
        settings = get_settings()
        base = settings.railre_api_base.rstrip("/")

        train = (params.get("train") or params.get("train_code") or "").strip().upper()
        emu_raw = (
            params.get("emu_no") or params.get("unit") or params.get("car_code") or ""
        ).strip().upper()
        limit = int(params.get("limit") or 20)
        # 目标日期（可选）：支持"今天/明天/2026-09-14"等表述；
        # 未指定时默认聚焦今天（见下方 focus_date 逻辑）
        target_date = normalize_date(params.get("date") or params.get("time"), default_today=False)

        # 未显式给出时，从 target/query 猜测类型
        raw_query = (params.get("query") or params.get("target") or "").strip()
        if not train and not emu_raw and raw_query:
            if re.match(r"^0?[GDC]\d{1,4}", raw_query, re.I):
                train = raw_query.upper()
            else:
                emu_raw = raw_query.upper()

        if not train and not emu_raw:
            return ToolResult(ok=False, error="缺少参数：train（车次）或 emu_no（车组号）")

        # ---- 输入形态校验（避免"车次/车组"串槽位后发出无意义请求）----
        emu_key = re.sub(r"[\s\-]", "", emu_raw)      # 去空格与连字符，得到紧凑形态
        if train:
            if not _TRAIN_CODE_RE.match(train):
                return ToolResult(
                    ok=False,
                    error=f"车次号格式不正确：{train}",
                    note="车次形如 G1 / D27 / C1234 / K53 / 1461；若要查车组请用 emu_no 参数",
                )
            if not _EMU_TYPE_RE.match(train):
                # 普速（K/T/Z/纯数字等）：rail.re 交路库只收录动车组担当，
                # 这里**如实说明数据源边界**，而不是报"格式不正确"或"查询失败"。
                return ToolResult(
                    ok=False,
                    error=f"{train} 为普速/非动车组车次，rail.re 交路库不收录其担当信息",
                    note=(
                        "rail.re 仅覆盖动车组（G/D/C）担当车组；"
                        "普速车次的时刻/经停请用 train.schedule（12306 实时），"
                        "机车担当目前无公开数据源（12306 不公开，rail.re 不收录）"
                    ),
                )
        elif not _EMU_NO_RE.match(emu_key):
            return ToolResult(
                ok=False,
                error=f"车组号格式不正确：{emu_raw}",
                note="车组号形如 CR400AF-0207 / CR400BFA5054；若要查车次请用 train 参数",
            )

        focus_day_hint = target_date or date.today().isoformat()

        if train:
            url = f"{base}/train/{quote(train, safe='')}"
            kind = "train"
            kind_label = "车次"
        else:
            url = f"{base}/emu/{quote(emu_key, safe='')}"
            kind = "emu"
            kind_label = "车组"

        try:
            client = await get_client()
            resp = await client.get(
                url, headers=BROWSER_HEADERS, timeout=settings.http_timeout
            )
            resp.raise_for_status()
            payload = resp.json()
        except httpx.HTTPStatusError as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status == 404:
                # 明确的"不存在"结论（R1 K01：直连 rail.re 对 G99999 返回 404）
                return ToolResult(
                    ok=False,
                    error=f"rail.re 交路库中不存在{kind_label} {train or emu_key}（HTTP 404）",
                    note=(
                        "该车次/车组在 rail.re 交路库中没有记录：可能是车次号有误、"
                        "或不属动车组交路库收录范围（普速另见 train.schedule）"
                    ),
                )
            return ToolResult(
                ok=False,
                error=f"rail.re 交路查询失败：{format_error(e)}",
                note="站点可能不可达；rail.re 需浏览器级请求头与境内网络",
            )
        except json.JSONDecodeError as e:
            return ToolResult(
                ok=False,
                error=f"rail.re 返回非 JSON（可能被反爬拦截）: {format_error(e)}",
                note="请确认网络可达 api.rail.re；必要时在浏览器验证",
            )
        except Exception as e:
            return ToolResult(
                ok=False,
                error=f"rail.re 交路查询失败: {format_error(e)}",
                note="站点可能不可达；rail.re 需浏览器级请求头与境内网络",
            )

        if not isinstance(payload, list) or not payload:
            return ToolResult(
                ok=False,
                error=f"rail.re 未返回 {train or emu_key} 的交路记录",
                note="该车次/车组可能暂无收录记录",
            )

        # ---- 12306 官方车组号（第二来源）：与 rail.re 并行取，互不阻塞 ----
        # 目的：① 让"哪一组车"有**两个独立来源**可交叉印证；② rail.re 不可达时仍能答。
        official = None
        if kind == "train":
            official = await rt.get_car_detail(train, focus_day_hint)

        # 归一化为统一记录结构
        records: list[dict] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            raw_dt = str(item.get("date") or "")
            d, t = _split_date_time(raw_dt)
            emu_item = str(item.get("emu_no") or "").strip().upper()
            records.append({
                "date": d,
                "time": t,
                "emu_no": emu_item,
                "emu_no_display": _fmt_emu_no(emu_item),
                "train_code": str(item.get("train_no") or "").strip().upper(),
                "operator": (item.get("bureau") or item.get("operator") or ""),
            })

        # 去重（同一天同一车组同一车次只保留一条），并按日期倒序
        seen: set[tuple[str, str, str, str]] = set()
        unique: list[dict] = []
        for r in records:
            key = (r["date"], r["time"], r["emu_no"], r["train_code"])
            if key in seen:
                continue
            seen.add(key)
            unique.append(r)
        unique.sort(key=lambda r: (r["date"], r["time"]), reverse=True)

        # ---- 关键修复：结果必须与查询键一致，并区分"单台车组"与"车型前缀" ----
        if kind == "train":
            matched = [r for r in unique if r["train_code"] == train]
            if not matched:
                return ToolResult(
                    ok=False,
                    error=f"rail.re 返回的记录中没有车次 {train}（接口可能按前缀匹配）",
                    note=f"原始记录 {len(unique)} 条，已全部判为不匹配，未采用",
                )
            match_mode = "exact"
            unique = matched
        else:
            exact = [r for r in unique if r["emu_no"] == emu_key]
            if exact:
                match_mode = "exact"
                unique = exact
            else:
                series = [r for r in unique if r["emu_no"].startswith(emu_key)]
                if not series:
                    return ToolResult(
                        ok=False,
                        error=f"rail.re 返回的记录中没有车组 {emu_key}",
                        note=f"原始记录 {len(unique)} 条，已全部判为不匹配，未采用",
                    )
                match_mode = "series"
                unique = series

        today_str = date.today().isoformat()
        # 若显式指定了日期则优先用它，否则默认关注今天
        focus_date = target_date or today_str
        focus_records = [r for r in unique if r["date"] == focus_date]
        shown = (focus_records or unique)[:limit]
        focus_label = "今日" if focus_date == today_str else focus_date
        units = sorted({r["emu_no_display"] for r in unique})

        if kind == "train":
            # ⚠️ rail.re 的 date 是"该条交路记录的记录时刻"，**不是列车到发时刻**。
            # 未标注时模型会把 11:24 当成"今日到达时间"（R1 红线 D06 的第二条泄露路径），
            # 因此这里逐行显式标注，并在 note 中再次说明。
            lines = [
                f"{r['date']} {r['time']}（交路记录时刻，非列车到发时刻） · {r['emu_no_display']}"
                + (f" · {r['operator']}" if r["operator"] else "")
                for r in shown
            ]
            focus_units = sorted({r["emu_no_display"] for r in focus_records})
            _FOCUS_UNITS["v"] = focus_units
            if focus_units:
                head = f"车次 {train}：{focus_label}（{focus_date}）担当车组为 {'、'.join(focus_units)}。"
            else:
                head = (
                    f"车次 {train}：未收录{focus_label}（{focus_date}）记录，"
                    f"以下为最近 {len(shown)} 条历史担当记录。"
                )
            summary = head + "\n" + "\n".join(lines)
        elif match_mode == "series":
            # 车型/系列前缀：如实说明匹配到多台，并按车组分别列出当日担当
            by_unit: dict[str, list[str]] = {}
            for r in focus_records:
                by_unit.setdefault(r["emu_no_display"], []).append(r["train_code"])
            if by_unit:
                body = "\n".join(
                    f"{u}：{', '.join(sorted(set(ts)))}" for u, ts in sorted(by_unit.items())
                )
                head = (
                    f"{emu_key} 是车型/系列前缀，rail.re 匹配到 {len(units)} 台车组"
                    f"（{'、'.join(units[:5])}{' 等' if len(units) > 5 else ''}）；"
                    f"{focus_label}（{focus_date}）各车组担当如下（**不是单台车组**）："
                )
            else:
                body = "\n".join(f"{r['date']} {r['time']} · {r['emu_no_display']} · {r['train_code']}"
                                 for r in shown)
                head = (
                    f"{emu_key} 是车型/系列前缀，rail.re 匹配到 {len(units)} 台车组"
                    f"（{'、'.join(units[:5])}{' 等' if len(units) > 5 else ''}）；"
                    f"未收录{focus_label}（{focus_date}）记录，以下为最近 {len(shown)} 条历史记录："
                )
            summary = head + "\n" + body
        else:
            lines = [
                f"{r['date']} {r['time']}（交路记录时刻，非列车到发时刻） · {r['train_code']}"
                for r in shown
            ]
            running = [r for r in shown if r["date"] == focus_date]
            head = (
                f"车组 {_fmt_emu_no(emu_key)}："
                + (f"{focus_label}（{focus_date}）担当 {', '.join(r['train_code'] for r in running)}。"
                   if running else
                   f"未收录{focus_label}（{focus_date}）记录，以下为最近 {len(shown)} 条历史担当记录。")
            )
            summary = head + "\n" + "\n".join(lines)

        if match_mode == "series":
            note = (
                f"rail.re 交路数据：按车型/系列前缀匹配到 {len(units)} 台车组、"
                f"{len(unique)} 条记录（非单台车组，请按车组号逐台核对）；"
                "记录中的时间均为**交路记录时刻**，不是列车到发时刻"
            )
        else:
            note = (
                f"rail.re 交路数据（共 {len(unique)} 条记录）；"
                "记录中的时间均为**交路记录时刻**，不是列车到发时刻，"
                "列车时刻请查 train.schedule"
            )

        # ---- 官方车组号与 rail.re 的对照（**分歧必须如实呈现，不得静默取其一**）----
        official_car = (official or {}).get("car_code", "")
        if official and kind == "train":
            rail_units = _FOCUS_UNITS.get("v") or units
            rail_norm = {rt.normalize_car_code(u) for u in rail_units}
            agree = rt.normalize_car_code(official_car) in rail_norm if rail_norm else None
            official_line = (
                f"\n12306 官方车组信息：{official_car}"
                f"（车型 {official.get('car_type') or '?'}，"
                f"{official.get('total_coaches') or '?'} 节车厢）"
            )
            if official.get("coaches"):
                official_line += "\n车厢：" + "、".join(
                    c["label"] for c in official["coaches"][:18] if c.get("label"))
            if agree is True:
                official_line += "\n✅ 与 rail.re 交路记录**一致**（同一组车，仅写法差连字符）"
                note += "；12306 官方车组号与 rail.re 一致"
            elif agree is False:
                official_line += (
                    f"\n⚠️ 与 rail.re 记录的 {('、'.join(rail_units))} **不一致**："
                    "两个来源对「今天由哪组担当」给出不同车组，"
                    "请以官方当日实际为准，并留意临时换车"
                )
                note += "；⚠️ 12306 官方车组号与 rail.re **不一致**（已如实标注，未取其一）"
            else:
                official_line += "\n（rail.re 无当日记录，无法比对；官方这一条可作为当日依据）"
                note += "；rail.re 无当日记录，车组号来自 12306 官方"
            summary += official_line
            sources_extra = [rt.CAR_DETAIL_URL]
        else:
            sources_extra = []

        return ToolResult(
            ok=True,
            data={
                "kind": kind,
                "query": train or emu_key,
                "match_mode": match_mode,           # exact（单台/单车次）| series（车型前缀）
                "matched_units": units,
                "unit_count": len(units),
                "today": today_str,
                "focus_date": focus_date,
                "today_records": focus_records,
                "records": unique,
                "count": len(unique),
                "official": official,
                "official_car_code": official_car or None,
            },
            text=summary,
            sources=[url, "https://rail.re", *sources_extra],
            note=note,
        )
