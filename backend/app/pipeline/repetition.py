"""生成输出的重复循环检测。

为什么需要这一层：**实测 0.8B 在真实生成提示词下会把一段 17 个站名重复 68 次**，
一路吐到 `n_predict` 上限（1200 token），中间还编造了不属于该线路的车站。
2B / 4B / 云端在同一条提示词下零重复 —— 但"现在不循环"不等于"永远不循环"，
**产品正确性不该依赖模型自律**，所以这层兜底必须在，且与模型无关。

检测口径刻意保守：只认**连续重复 ≥3 次**的块（默认块长 ≥12 字）。
正常的排比、表格、逐条列举不会命中；命中就是真的退化了。

`README` 之外的使用约定：检测到就**在循环起点截断**并如实告知用户，
不要静默丢弃 —— 用户看到的应该是一句"后面重复了，已截断"，而不是一段莫名其妙中断的回答。
"""
from __future__ import annotations

# 默认参数（依据实测：0.8B 那次循环块约 51 字、重复 68 次；正常回答里最长重复片段出现 1 次）
MIN_BLOCK = 12          # 块长下限：比这更短的重复（如"的的的"）不是我们要抓的退化
MAX_BLOCK = 160         # 块长上限：再长就不像"卡住"，像是正常复述
REPEATS = 3             # 连续重复多少次才算循环
SCAN_WINDOW = 2000      # 只扫尾部这么多个字符


def detect(text: str, *, min_block: int = MIN_BLOCK, max_block: int = MAX_BLOCK,
           repeats: int = REPEATS) -> int | None:
    """返回循环起点（应在此处截断），没检测到返回 None。

    只看**尾部**：循环一定长在结尾，而全文扫描在长回答上纯属浪费。
    """
    n = len(text)
    if n < min_block * repeats:
        return None
    window = text[-max(max_block * (repeats + 1), 600):]
    m = len(window)
    upper = min(max_block, m // repeats)
    for blk in range(min_block, upper + 1):
        tail = window[m - blk:]
        # 从最小的块长开始试：退化时周期通常很短，早返回能省掉大部分比较
        if not all(window[m - blk * (i + 1): m - blk * i] == tail
                   for i in range(1, repeats)):
            continue
        start = m - blk * repeats
        # 继续往前扩，把更早的重复也一并去掉（否则会留下残留的重复头）
        while start - blk >= 0 and window[start - blk:start] == tail:
            start -= blk
        return n - m + start
    return None


def cut(text: str, **kw) -> tuple[str, bool]:
    """返回 (截断后的文本, 是否发生截断)。截断时会**如实附一句说明**。"""
    at = detect(text, **kw)
    if at is None:
        return text, False
    kept = text[:at].rstrip()
    return (kept + "\n\n（后文出现连续重复，已在此截断。）"), True
