"""多轮槽位/意图继承回归（**全部无网络**）。

背景（实测，见 `tests/corpus/intent_corpus.jsonl` 的 24 条带 `history` 用例）
--------------------------------------------------------------------------
单轮问法的快路径接管率 74.4%，**多轮只有 13.0%**。漏掉的几乎全是**省略句**：
「这车呢 / 那明天呢 / 那上海虹桥呢 / 改成上海呢 / 刚才那趟经停哪些站」——
句子里没有任何可检索的字面信号，全靠上文消解。它们被交回 LLM，于是每次多付
一次 1.2–9.3s 的决策往返（快路径的全部意义就在省掉这一趟）。

本套件钉住三件事：
1. **该继承的继承**：槽位（车次/站名/线路/时间）与意图都能从上文补全，且
   区间替换方向正确（「改成上海呢」换终点、「那从南京走呢」换起点）；
2. **该交出的交出**（红线，比 1 更重要）：
   - 纯指代（「这车呢」）指向哪个对象、问的是什么都有歧义 → 交回模型；
   - 时间口径冲突（「那下午还有吗」承接「明天上午」）→ 交回模型，
     **绝不能**因为 `_time_phrase` 对裸时段退回"今天"就把明天的问句答成今天的；
   - 上文解析出的区间两端不是真实站名（`parse_od` 会从「那趟车现在跑到哪了」
     抠出 ('那趟车现在跑','哪了')）→ 不继承；
   - 没有 history → 行为与本改动前完全一致。
3. **继承是可见的**：`matched` 里要标出哪几个槽位承自上文（否则误继承会被
   当成"模型判错"来排查）。

顺带修掉两个被这次改动照出来的既有缺陷（各有独立用例）：
- `_line_in_text`：线路名前只要有 ≥2 个汉字就取不到线路名（"北京到上海走京沪线要多久？"
  直接 NO_SLOT 交回 LLM）；
- `parse_od`：目的地残留动词 → "北京到上海坐高铁要多长时间？" 解析成 `北京→上海坐`。

运行：cd backend && PYTHONPATH=. .venv/bin/python tests/test_multiturn_slots.py
"""
from __future__ import annotations

import asyncio

from app.od import parse_od
from app.pipeline.fastpath import _line_in_text, plan_with_reason


def _U(content: str) -> dict:
    return {"role": "user", "content": content}


def _A(content: str) -> dict:
    return {"role": "assistant", "content": content}


def _plan(msg: str, history=None):
    return asyncio.run(plan_with_reason(msg, history))


def _taken(msg: str, history=None):
    """返回 (intent, slots dict)；未接管则断言失败。"""
    fp, defer = _plan(msg, history)
    assert fp is not None, f"{msg!r} 期望被接管，实际交出：{defer.reason if defer else '?'}"
    return fp.intent, fp.slots.non_empty(), fp


def _deferred(msg: str, history=None) -> str:
    """断言交出，返回原因码。"""
    fp, defer = _plan(msg, history)
    assert fp is None, f"{msg!r} 不该接管，实际 {fp.intent} {fp.slots.non_empty()}"
    assert defer is not None, f"{msg!r} 交出时必须给出原因码"
    return defer.reason


# ---------------------------------------------------------------- 1) 该继承的继承
def test_slot_inheritance_fills_missing_train():
    """问法命中但缺车次 → 从上一轮用户消息补全（语料 intent_multiturn-0004）。"""
    hist = [_U("G1几点到上海虹桥？"), _A("G1次13:28到上海虹桥。")]
    intent, slots, fp = _taken("刚才那趟经停哪些站", hist)
    assert intent == "schedule", intent
    assert slots.get("target") == "G1", slots
    # 「那趟车经停哪些站」（语料 0031）同样路径
    intent2, slots2, _ = _taken("那趟车经停哪些站", [_U("G1今天由哪组动车组担当？"), _A("由CR400AF-5054担当。")])
    assert (intent2, slots2.get("target")) == ("schedule", "G1"), (intent2, slots2)
    print(f"[PASS] 缺车次的上文继承 -> {slots}")


def test_od_endpoint_replacement():
    """本次只提到一个站名时，替换的是**起点还是终点**必须判对。

    判据是「从X」→ 起点，否则 → 终点。实测语料：
      intent_multiturn-0008「改成上海呢」承接「北京南到济南西还有票吗？」→ 北京南→上海
      intent_multiturn-0006「那从南京走呢」承接「G35全程几个小时？」→ 起点换成南京
    """
    intent, slots, _ = _taken("改成上海呢", [_U("北京南到济南西还有票吗？"), _A("有票。")])
    assert intent == "ticket", intent
    assert slots.get("direction") == "北京南→上海", f"终点没被替换：{slots}"

    intent2, slots2, _ = _taken("那从南京走呢", [_U("G35全程几个小时？"), _A("约4小时18分。")])
    assert slots2.get("target") == "G35", f"车次没继承：{slots2}"
    assert slots2.get("location") == "南京", f"「从南京」的站名没进 location：{slots2}"

    # 末尾站名同样替换终点（语料 0013）
    _, slots3, _ = _taken("那上海虹桥呢", [_U("北京南到济南西还有票吗？"), _A("有票。")])
    assert slots3.get("direction") == "北京南→上海虹桥", slots3
    print(f"[PASS] 区间端点替换：换终点={slots['direction']} 换起点={slots2['location']}")


def test_intent_inheritance_for_ellipsis():
    """没匹配到任何问法、但带着新槽位的省略句 → 意图取自上文。

    「那明天呢」（语料 0019）：车次继承 G1234、时间换成明天；
    「那京沪线呢」（0010）：线路换成京沪线；
    「那走京广高速线呢」（0022）：线路名 + 区间都来自上文。
    """
    intent, slots, _ = _taken("那明天呢", [_U("G1234经停哪些站？"), _A("停靠济南西、南京南。")])
    assert (intent, slots.get("target"), slots.get("time")) == ("schedule", "G1234", "明天"), slots

    intent2, slots2, _ = _taken("那京沪线呢", [_U("京沪高速线多少公里？"), _A("约1318公里。")])
    assert (intent2, slots2.get("target")) == ("rail_line", "京沪线"), (intent2, slots2)

    intent3, slots3, _ = _taken("那走京广高速线呢", [_U("北京到广州走哪条线？"), _A("可经京广高速线。")])
    assert (intent3, slots3.get("target"), slots3.get("direction")) == \
        ("rail_line", "京广高速线", "北京→广州"), (intent3, slots3)
    print(f"[PASS] 省略句意图继承 -> {slots} / {slots2} / {slots3}")


def test_inherited_slots_are_marked_in_log():
    """哪几个槽位承自上文必须在 matched 里可见（否则误继承无从排查）。"""
    _, _, fp = _taken("那从南京走呢", [_U("G35全程几个小时？"), _A("约4小时18分。")])
    joined = "、".join(fp.matched)
    assert "承上文" in joined, f"日志没标出继承来源：{fp.matched}"
    assert "G35" in joined, fp.matched
    assert "承接上文" in fp.reason, fp.reason
    print(f"[PASS] 继承可诊断：{fp.matched}")


# ---------------------------------------------------------------- 2) 该交出的交出
def test_bare_pronoun_still_defers():
    """「这车呢」这类**纯指代**必须继续交回模型。

    语料里同一句「这车呢」承接不同上文时期望并不相同（0002 指车次、0007 指车组），
    作者自己标的就是 `route=either` —— 指的哪个对象、问的是什么都有歧义，
    规则出手就是猜。这也是本次改动唯一"故意不接管"的一类。
    """
    for msg in ("这车呢", "那这车呢", "那趟车呢", "那这趟呢", "这几个站都能下车吗"):
        reason = _deferred(msg, [_U("G1234经停哪些站？"), _A("停靠济南西。")])
        assert reason in ("UNKNOWN_FAMILY", "NO_SLOT"), (msg, reason)
    print("[PASS] 纯指代/无新槽位的省略句仍然交回模型（不猜）")


def test_time_conflict_defers():
    """时间口径冲突 → 交回模型，**绝不能**把明天的问句答成今天的。

    实测：「那下午还有吗」承接「G1明天上午还有二等座吗？」——本次只能确定"下午"，
    日期只能取上文的"明天"；而 `_time_phrase("那下午还有吗")` 对裸时段会退回 **"今天"**，
    一旦直接覆盖，用户拿到的就是**今天下午**的余票（看起来正常的错答案）。
    """
    reason = _deferred("那下午还有吗", [_U("G1明天上午还有二等座吗？"), _A("余票充足。")])
    assert reason in ("UNKNOWN_FAMILY", "NO_SLOT"), reason
    print("[PASS] 时间口径冲突 → 交回模型（不做「今天/明天」的错误覆盖）")


def test_junk_od_not_inherited():
    """`parse_od` 的误判不得被继承：站点库校验挡住了它。

    `parse_od("那趟车现在跑到哪了")` → ('那趟车现在跑', '哪了')，两端都不是站名。
    """
    assert parse_od("那趟车现在跑到哪了") is not None, "前提变了：该句不再被 parse_od 误判"
    reason = _deferred("明天还有票吗", [_U("那趟车现在跑到哪了"), _A("")])
    assert reason == "NO_SLOT", f"继承到了垃圾区间：{reason}"
    print("[PASS] 两端非真实站名的「区间」不会被继承")


def test_only_user_turns_are_slot_source():
    """槽位来源只认**用户消息**，不拿助手回答里的实体顶替。

    实测语料：问「G1今天由哪组动车组担当？」的答句里是 CR400AF-5054，
    而用户接着说「那是哪个局的车」问的是车组本身 —— 用回答文本继承会张冠李戴。
    """
    hist = [_U("几点的车"), _A("G1次09:00从北京南开，由CR400AF-5054担当。")]
    reason = _deferred("刚才那趟经停哪些站", hist)
    assert reason == "NO_SLOT", f"从助手回答里继承了车次：{reason}"
    print("[PASS] 助手回答不作为槽位来源（用户说的才算）")


def test_no_history_means_no_change():
    """没有 history 时行为必须与本改动前完全一致（单轮不受影响）。"""
    # 无 history 与 空 history 必须完全同结果
    for msg in ("G1经停哪些站？", "明天北京到上海还有票吗？", "京沪线的所有车站",
                "这车呢", "那明天呢", "改成上海呢", "换乘车站的编号怎么看？"):
        a, da = _plan(msg, None)
        b, db = _plan(msg, [])
        assert (a is None) == (b is None), msg
        if a is not None:
            assert (a.intent, a.slots.non_empty()) == (b.intent, b.slots.non_empty()), msg
        else:
            assert da.reason == db.reason, msg

    # 无关历史也不得改变结论（「你好」里没有任何槽位可继承）
    for msg in ("G1经停哪些站？", "明天北京到上海还有票吗？", "这车呢"):
        a, _ = _plan(msg, None)
        b, _ = _plan(msg, [_U("你好"), _A("你好，请问要查什么？")])
        if a is None:
            assert b is None, f"{msg!r} 被无关历史改变了结论"
        else:
            assert b is not None and (b.intent, b.slots.non_empty()) == (a.intent, a.slots.non_empty()), msg
    print("[PASS] 无 history / 无关 history → 行为不变")


def test_single_hop_inheritance_only():
    """只继承一轮：链式省略（「那明天呢」→「那后天呢」）的口径交回模型。"""
    reason = _deferred("那后天呢", [_U("那明天呢"), _A("")])
    assert reason in ("UNKNOWN_FAMILY", "NO_SLOT"), reason
    print("[PASS] 继承深度固定为 1 轮（不做套娃式传递）")


# ---------------------------------------------------------------- 3) 顺带修掉的既有缺陷
def test_line_name_with_leading_chars():
    """线路名前带字也要能取到（`_line_in_text` 的匹配推进方式修好了）。

    原实现（贪心→懒惰）在候选校验失败后从**匹配末尾**继续，于是
    「那京沪线呢 / 查一下京沪线 / 北京到上海走京沪线要多久？」全部取不到线路名 ——
    最后那句是极自然的问法，实测直接 NO_SLOT 交回 LLM。
    """
    for text, want in (
        ("京沪线有多少公里", "京沪线"),
        ("那京沪线呢", "京沪线"),
        ("查一下京沪线", "京沪线"),
        ("北京到上海走京沪线要多久？", "京沪线"),
        ("那走京广高速线呢", "京广高速线"),
        ("陇海线沿线有哪些车站", "陇海线"),
        ("京沪铁路沿线有哪些车站", "京沪线"),
    ):
        assert _line_in_text(text) == want, f"{text!r} -> {_line_in_text(text)!r}，期望 {want!r}"
    # 泛指词仍不得误判
    for text in ("随便说点什么", "中国铁路有多少公里"):
        assert _line_in_text(text) == "", f"{text!r} 误判为线路：{_line_in_text(text)!r}"
    print("[PASS] 线路名识别不再受前缀字数影响，泛指词仍被挡掉")


def test_od_tail_verb_trimmed():
    """目的地不得残留运输方式前的动词。

    实测：「北京到上海坐高铁要多长时间？」剥掉句末"要多长时间"后剩"上海坐高铁"，
    在"高铁"处截断会留下"坐" → 终点成了不存在的 **"上海坐"**，
    而该问句今天会真的被接管成 rail_line 并带 `direction=北京→上海坐` 去查。
    """
    assert parse_od("北京到上海坐高铁要多长时间？") == ("北京", "上海"), parse_od("北京到上海坐高铁要多长时间？")
    assert parse_od("北京到上海乘高铁") == ("北京", "上海"), parse_od("北京到上海乘高铁")
    # 不能误伤：站名里的字不在截断词里
    assert parse_od("成都东到重庆西卧铺还有吗") == ("成都东", "重庆西"), parse_od("成都东到重庆西卧铺还有吗")
    _, slots, _ = _taken("北京到上海坐高铁要多长时间？")
    assert slots.get("direction") == "北京→上海", f"终点仍带动词：{slots}"
    print(f"[PASS] 目的地残留动词已剔除 -> {slots.get('direction')}")


def main():
    test_slot_inheritance_fills_missing_train()
    test_od_endpoint_replacement()
    test_intent_inheritance_for_ellipsis()
    test_inherited_slots_are_marked_in_log()
    test_bare_pronoun_still_defers()
    test_time_conflict_defers()
    test_junk_od_not_inherited()
    test_only_user_turns_are_slot_source()
    test_no_history_means_no_change()
    test_single_hop_inheritance_only()
    test_line_name_with_leading_chars()
    test_od_tail_verb_trimmed()
    print("\n多轮槽位/意图继承测试全部通过 ✔")


if __name__ == "__main__":
    main()
