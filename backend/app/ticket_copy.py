"""Shared Main ticket wording; data and status semantics stay at their sources."""

FARE_REFERENCE = "票价可作为购票参考，具体以购票页面为准。"
AVAILABILITY_SNAPSHOT = "余票为查询时快照，不能保证购票时仍然有票；请以 12306 购票页面显示为准。"
AVAILABLE_TRAIN_COUNT = "查询时有票的车次数量"

# 席别名称的唯一来源。上游 12306 以英文键返回席别，凡**用户可见**文案都必须过
# `seat_label()`；机器可读字段（如 seat_counts 的键）保持原始键，不改口径。
# 取值是 fare_result 与 ticket_answer 两处历史副本的并集，二者原本一致。
SEAT_NAMES = {
    "business": "商务座", "business_class": "商务座", "special_class": "特等座",
    "first_class": "一等座", "second_class": "二等座", "advanced_soft_sleeper": "高级软卧",
    "soft_sleeper": "软卧", "moving_sleeper": "动卧", "hard_sleeper": "硬卧",
    "soft_seat": "软座", "hard_seat": "硬座", "no_seat": "无座", "standing": "无座",
}


def seat_label(key) -> str:
    """上游席别键 → 用户可见名称；未知键原样返回，不猜测、不丢弃。"""
    text = str(key)
    return SEAT_NAMES.get(text, text)


def join_clause(head: str, tail: str) -> str:
    """把 `tail` 接到 `head` 之后，且不产生「。；」这类连写。

    - `tail` 为空 → 返回规范化后的 `head`（末尾不会新增分号）；
    - `head` 为空（或只有前导分号）→ 返回去掉前导分号的 `tail`；
    - 两端的**前导**「；」都会被丢掉，调用方不必自己 `lstrip`；
    - `head` 已以句末标点（。！？；）收尾 → 直接续写；
    - 其余 → 补一个「；」。

    注意：本函数只按标点决定分隔符，不做其它改写；`head` 已有的尾随分号会保留。
    """
    text = (head or "").strip().lstrip("；").strip()
    rest = (tail or "").lstrip("；")
    if not rest:
        return text
    if not text:
        return rest
    return text + rest if text[-1] in "。！？；" else text + "；" + rest


def join_clauses(parts) -> str:
    """按出现顺序去重后逐个拼接（旧 `"；".join(dict.fromkeys(parts))` 的安全版）。

    与旧写法的区别只有两点：跳过空串；段与段之间不会拼出「。；」。
    """
    out = ""
    for part in dict.fromkeys(p for p in parts if p):
        out = join_clause(out, str(part))
    return out
