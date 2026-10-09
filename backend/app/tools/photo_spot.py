"""机位线索工具（本地库，毫秒级）—— `photo.spot`。

为什么要有这个工具
------------------
机位**没有结构化数据源**（2026-10-06 实测：OSM 全中国涉铁路观景点 17 个、真机位词仅 3 个；
12306 / rail.re / 黄河铁路网 / GTFS 均无拍摄点字段；车迷自制机位站 `chinaonrails.org` 已不可达）。
有效内容只散落在车迷社区的非结构化散文里，且**搜索引擎反爬**使其无法在请求路径上稳定获取。

因此本项目对机位的策略是**离线建库 + 线上零延迟查表**（与 `rail.mileage` 同构）：
- 离线：`scripts/build_photo_spots.py` 低频抓取 + 三道硬闸门过滤，正文原文入库；
- 线上：本工具只读本地 `photo_spot_doc` 表，**不联网**。

如实性纪律（宁可少，不可编）
----------------------------
- 库不存在 / 命中为空 → `ok=False` 且说明"公开攻略稀少或尚未收录"，**绝不编点位**；
- 每条线索都带 `scope`（地点）与来源 URL，正文原文交给生成层阅读（本工具**不**做结构化点位抽取）；
- 库内数据是**社区众包内容**，可能过时或不准，note 里必须如实标注这一点。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from app.tools.base import Tool, ToolResult
from app.data import photo_annotations as annotations

_log = logging.getLogger("railfan.photo_spot")

# 单次下发给生成层的线索条数上限（正文较长，多了会挤占上下文）
DEFAULT_LIMIT = 3
# 每条线索投喂正文的字符上限（攻略正文常上万字；点位描述多在中前部）
PAGE_CHARS = 1800


def _db_path() -> Path:
    from app.data.dict import db_path

    return db_path()


def _table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='photo_spot_doc'"
    ).fetchone()
    return bool(row)


class PhotoSpotTool(Tool):
    name = "photo.spot"
    description = (
        "机位/拍摄点线索（**本地库，不联网**）：按地点（站名/线路名/地标）查已收录的车迷机位攻略，"
        "返回来源链接与正文片段。库未收录时如实说明「公开攻略稀少」，不编造点位"
    )

    async def invoke(self, params: dict) -> ToolResult:
        scope = str(params.get("station") or params.get("location")
                    or params.get("place") or params.get("scope") or "").strip()
        extra = str(params.get("line") or params.get("target") or "").strip()
        limit = int(params.get("limit") or DEFAULT_LIMIT)

        path = _db_path()
        if not path.exists():
            return ToolResult(
                ok=False,
                error="机位本地库尚未构建（未找到 dict.db）",
                note="需先运行 `python3 scripts/build_photo_spots.py` 离线建库；本工具不联网",
            )
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
        except sqlite3.Error as e:
            return ToolResult(ok=False, error=f"机位库打开失败：{type(e).__name__}")

        try:
            if not _table_exists(conn):
                return ToolResult(
                    ok=False,
                    error="机位本地库尚未构建（dict.db 中无 photo_spot_doc 表）",
                    note="需先运行 `python3 scripts/build_photo_spots.py` 离线建库",
                )
            return self._query(conn, scope, extra, limit)
        finally:
            conn.close()

    def _query(self, conn: sqlite3.Connection, scope: str, extra: str,
               limit: int) -> ToolResult:
        if not scope and not extra:
            total = conn.execute("SELECT COUNT(*) FROM photo_spot_doc").fetchone()[0]
            return ToolResult(
                ok=False,
                error="未给出地点（需要站名 / 线路名 / 地标）",
                note=f"本地机位库现有 {total} 篇攻略；请给出具体地点后重查",
                total=total, shown=0,
            )

        # 匹配口径：scope / entities / 标题 / **正文**。
        #
        # 为什么必须匹配正文（2026-10-06 用户洞察）：机位的**具体点位**大量藏在盘点文里 ——
        # 实测"克俭公园"（沈阳的经典拍车点）在任何文档的 scope/标题里都没有，但它出现在
        # 《沈阳约拍圣地｜JK女孩的火车公园梦》《沈阳火车头拍照圣地》的**正文**中
        # （含最佳时段"下午4-6点"与到达方式"皇姑屯地铁站"）。
        # 只匹配 scope 会导致用户问"克俭公园怎么拍车"时查不到 —— 而这正是最该答上的问题。
        # 库规模小（百级），LIKE 扫正文足够；不引入全文索引依赖。
        keys = [k for k in (scope, extra) if k]
        conds, args = [], []
        for k in keys:
            conds.append("(scope = ? OR scope LIKE ? OR entities LIKE ?"
                         " OR title LIKE ? OR page_text LIKE ?)")
            args += [k, f"%{k}%", f"%{k}%", f"%{k}%", f"%{k}%"]
        where = " OR ".join(conds)
        # 排序按**相关度**，不能按正文长度：实测查"上海"时，一篇只顺带提到上海的
        # 《追车记--新成都西环线》因为正文更长而排在南翔编组站机位小结**前面**。
        # 故：scope 精确 > 标题命中 > scope 包含 > 正文命中，最后才比长度。
        annotation_priority = (
            "CASE WHEN EXISTS (SELECT 1 FROM photo_annotation_doc a "
            "WHERE a.url=photo_spot_doc.url AND a.reviewed=1) THEN 0 ELSE 1 END, "
            if annotations.exists(conn) else ""
        )
        rows = conn.execute(
            f"SELECT * FROM photo_spot_doc WHERE {where}"
            f" ORDER BY {annotation_priority}CASE WHEN scope = ? THEN 0"
            "               WHEN title LIKE ? THEN 1"
            "               WHEN scope LIKE ? THEN 2"
            "               ELSE 3 END,"
            "          char_count DESC LIMIT ?",
            (*args, keys[0], f"%{keys[0]}%", f"%{keys[0]}%", max(1, limit)),
        ).fetchall()
        total = len(rows)

        if not rows:
            have = conn.execute("SELECT COUNT(*) FROM photo_spot_doc").fetchone()[0]
            return ToolResult(
                ok=False,
                error=f"本地机位库未收录「{scope or extra}」的车迷攻略",
                note=(
                    f"机位是众包数据、公开攻略稀少：本地库现收录 {have} 篇，"
                    f"其中没有匹配「{scope or extra}」的；"
                    "**不得据此编造点位**，可如实告知该地点暂无可靠机位线索"
                ),
                total=0, shown=0,
                filters={"scope": scope or None, "line": extra or None},
            )

        lines, sources = [], []
        for i, r in enumerate(rows, 1):
            reviewed = annotations.reviewed_text(conn, r['url'])
            body = reviewed if reviewed is not None else (r["page_text"] or "")[:PAGE_CHARS]
            src = r["url"] or ""
            head = f"{i}. {r['title'] or '(无标题)'}"
            scope_label = '原文归类（不代表站位所属）' if reviewed is not None else '地点'
            block = [head, f"   {scope_label}：{r['scope']}（{r['scope_kind']}）｜来源：{src}"]
            if r["entities"] and reviewed is None:
                try:
                    ent = json.loads(r["entities"])
                    # 防御：字段只该是 JSON 数组；若是历史脏数据/裸字符串，
                    # 逐字符展开会把 ["黄","渡"] 这种垃圾喂给模型，故只接受 list。
                    if isinstance(ent, list) and ent:
                        block.append("   涉及：" + "、".join(str(x) for x in ent[:6]))
                except (ValueError, TypeError):
                    pass
            if body:
                block.append("   正文摘录：" + body.replace("\n", " ")[:PAGE_CHARS])
            else:
                block.append("   （仅有标题与来源，正文未取到）")
            lines.append("\n".join(block))
            if src:
                sources.append(src)

        shown = len(rows)
        return ToolResult(
            ok=True,
            data={
                "scope": scope or extra,
                "count": shown,
                "spot_docs": [
                    {"url": r["url"], "title": r["title"], "scope": r["scope"],
                     "source": r["source"], "char_count": r["char_count"],
                     "entities": r["entities"] if annotations.load(conn,r['url']) is None else '[]', "fetched_at": r["fetched_at"],
                     "annotation_index": annotations.document_index(conn, r['url'])}
                    for r in rows
                ],
            },
            text="\n\n".join(lines),
            sources=sources,
            total=shown, shown=shown,
            filters={"scope": scope or None, "line": extra or None},
            note=(
                "机位线索来自**车迷社区攻略**（非官方数据，可能过时或不准）；"
                "以上为**本地库中已收录**的篇目，不代表该地点只有这些机位，"
                "也不代表未收录地点就没有机位。不得据此编造具体点位。"
            ),
        )
