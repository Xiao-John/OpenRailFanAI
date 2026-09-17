"""离线列车目录（车次 → 内部编号 / 起讫站）。

**定位**：这是一个**静态兜底数据源**，不是实时接口。
数据来自 12306 的 `train_list.js`（该文件 CDN 已停更于 2022-09），
用途仅两处：
1. `_rt12306.infer_endpoints_from_offline()` —— 用户只给车次号时，
   推断出起讫站再去做**实时**查询；
2. `train.schedule` 在实时不可达时的降级展示（**会明确标注"非实时"**）。

> 实时数据请走 `app/tools/_rt12306.py`（基于 `mcp-server-12306`）。
> 本模块**不含**任何实时 HTTP 客户端。
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path


from app.config import get_settings
from app.tools._http import get_client

_log = logging.getLogger("railfan.data")

_TRAIN_CACHE = Path(__file__).resolve().parent / ".train_cache.json"
_TRAIN_URL = "https://kyfw.12306.cn/otn/resources/js/query/train_list.js"


async def _download_raw(url: str, timeout: float = 15) -> str:
    client = await get_client()
    resp = await client.get(
        url, headers={"User-Agent": "RailFanAI/0.3"}, timeout=timeout
    )
    resp.raise_for_status()
    return resp.text


def _parse_train_list(text: str) -> dict[str, dict]:
    """解析 train_list.js。

    形如 `{"2022-08-29": {"G": [{"station_train_code":"G1(北京南-上海)","train_no":"24000000G100"}]}}`
    → `{"G1": {train_code, train_no, from_station, to_station, train_type, sample_date}}`
    """
    m = re.search(r"=\s*(\{.*\})", text, re.S)
    if not m:
        raise ValueError("无法提取列车数据")
    raw = json.loads(m.group(1))
    trains: dict[str, dict] = {}
    for date_key, date_data in raw.items():
        for train_type, train_list in date_data.items():
            for entry in train_list:
                code = entry.get("station_train_code", "")
                train_no = entry.get("train_no", "")
                if not code or not train_no:
                    continue
                m2 = re.match(r"^([A-Z]+\d+)\s*\((.+?)-(.+?)\)$", code)
                if m2:
                    code = m2.group(1)
                    from_st = m2.group(2)
                    to_st = m2.group(3)
                else:
                    from_st = ""
                    to_st = ""
                # 只保留首次出现的日期（最早的快照）
                if code not in trains:
                    trains[code] = {
                        "train_code": code,
                        "train_no": train_no,
                        "from_station": from_st,
                        "to_station": to_st,
                        "train_type": train_type,
                        "sample_date": date_key,
                    }
    return trains


def cache_age_days() -> float | None:
    """缓存文件的年龄（天）；不存在返回 None。"""
    if not _TRAIN_CACHE.exists():
        return None
    return (time.time() - _TRAIN_CACHE.stat().st_mtime) / 86400.0


def is_cache_stale() -> bool:
    """缓存是否已超过 TTL（缺失也算 stale）。"""
    age = cache_age_days()
    if age is None:
        return True
    return age > max(1, get_settings().train_cache_ttl_days)


def _read_cache() -> dict[str, dict]:
    """读取缓存；文件损坏时给出**明确可操作**的错误（而不是抛 JSONDecodeError）。"""
    try:
        return json.loads(_TRAIN_CACHE.read_text("utf-8"))
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"离线车次目录缓存已损坏（{_TRAIN_CACHE.name}）：{e}；"
            "可删除该文件后重新预热（见 scripts/upgrade_python.sh）"
        ) from e


async def build_train_cache(force: bool = False) -> dict[str, dict]:
    """下载并构建/复用本地车次缓存。

    行为约定（M11.1 加固）：
    - 缓存存在且未超 TTL → 直接复用（默认 TTL 30 天，可用 `TRAIN_CACHE_TTL_DAYS` 调整）
    - 超期或 `force=True` → 重新下载；**下载/解析失败时回退到旧缓存**（不让兜底数据凭空消失）
    - 写入为**原子操作**（临时文件 + rename），避免中断留下半个 JSON
    """
    if not force and _TRAIN_CACHE.exists() and not is_cache_stale():
        return _read_cache()

    try:
        raw = await _download_raw(_TRAIN_URL, timeout=45)
        trains = _parse_train_list(raw)
    except Exception as e:  # noqa: BLE001
        if _TRAIN_CACHE.exists():
            age = cache_age_days() or 0
            _log.warning("车次缓存刷新失败（%s），继续使用 %.0f 天前的旧缓存", e, age)
            return _read_cache()
        raise

    _TRAIN_CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _TRAIN_CACHE.with_suffix(f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(trains, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(_TRAIN_CACHE)          # 原子替换
    _log.info("列车缓存已构建：%d 条车次", len(trains))
    return trains


class TrainDB:
    """离线车次目录查询（只读，需先确保缓存存在）。"""

    def __init__(self) -> None:
        self._trains: dict[str, dict] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        if _TRAIN_CACHE.exists():
            self._trains = _read_cache()
            if is_cache_stale():
                _log.warning(
                    "离线车次目录已过期（%.0f 天，TTL %d 天），建议重新预热",
                    cache_age_days() or 0, get_settings().train_cache_ttl_days,
                )
        else:
            raise RuntimeError("列车缓存未构建，请先调用 build_train_cache()")
        self._loaded = True

    def lookup(self, train_code: str) -> dict | None:
        """按车次精确查询（如 G1、D27）。"""
        self._ensure_loaded()
        return self._trains.get(train_code.upper().strip())

    def search(self, keyword: str, limit: int = 20) -> list[dict]:
        """按车次/起讫站模糊搜索。"""
        self._ensure_loaded()
        kw = keyword.upper()
        results: list[dict] = []
        for t in self._trains.values():
            if len(results) >= limit:
                break
            if (kw in t["train_code"].upper()
                    or kw in t.get("from_station", "")
                    or kw in t.get("to_station", "")):
                results.append(t)
        return results

    @property
    def train_count(self) -> int:
        self._ensure_loaded()
        return len(self._trains)


_train_db: TrainDB | None = None


def get_train_db() -> TrainDB:
    global _train_db
    if _train_db is None:
        _train_db = TrainDB()
    return _train_db
