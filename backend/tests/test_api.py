"""HTTP 接口层测试（TestClient，不占端口）。

覆盖：
- /health 健康检查
- POST /api/chat 块式接口（含 emu_routing 意图 → 担当车组事实）
- POST /api/chat/stream SSE 事件序列完整性

关于"flaky"的处理（M11.1）
    真实 LLM 的**语义判定**（意图/问题性质）在 temperature=0 下也不保证逐次一致
    （实测同一句"G1今天由哪组动车组担当？"偶尔被判为 knowledge → 走 web.search）。
    本套测试对**结构**（字段、事件序列、校验）一律硬断言；
    对**语义**用有限重试（默认 3 次）：只要出现过期望判定即通过，
    连续 3 次都不符才失败并打印实际观测值 —— 既不掩盖模型漂移，也不因单次抖动误报。

运行：cd backend && PYTHONPATH=. ./.venv/bin/python tests/test_api.py
"""
from __future__ import annotations

import json
from typing import Callable

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

# 语义类断言的重试次数（每次都会真实调用模型）
SEMANTIC_TRIES = 3


def _chat(message: str, history: list | None = None) -> dict:
    r = client.post("/api/chat", json={"message": message, "history": history or []})
    assert r.status_code == 200, r.text
    return r.json()


def _chat_until(
    predicate: Callable[[dict], bool],
    message: str,
    *,
    history: list | None = None,
    tries: int = SEMANTIC_TRIES,
    label: str = "",
) -> dict:
    """重复调用 /api/chat 直到满足语义断言（模型判定可能单次漂移）。

    连续 tries 次都不满足 → 失败并打印每次的实际观测值（含 intent / question_type / tools）。
    """
    seen: list[str] = []
    for _ in range(tries):
        data = _chat(message, history)
        if predicate(data):
            return data
        seen.append(
            f"intent={data.get('intent')!r} qtype={data.get('question_type')!r} "
            f"tools={data.get('tool_trace')}"
        )
    raise AssertionError(
        f"{label or '语义断言'} 连续 {tries} 次未满足（模型判定漂移或路由异常）：\n  "
        + "\n  ".join(seen)
    )


def test_static_assets_are_not_heuristically_cached():
    """前端静态资源必须带 `Cache-Control: no-cache`。

    为什么值得一条测试：这套前端**没有构建步骤**（更新就是直接替换文件），
    而 Android 上静态资源的 URL 跨安装**完全不变**（端口刻意固定，为的是保住本机状态）。
    没有 Cache-Control 时浏览器/WebView 会按启发式规则自行缓存，
    表现为"明明重装了、界面却没变"——实测重装后看不到刚修好的界面就属于这一类。
    `no-cache` 是"每次回源校验"（不是禁用缓存），本地回环代价可忽略。
    """
    for path in ("/", "/src/main.js", "/src/pages.js"):
        r = client.get(path)
        assert r.status_code == 200, f"{path} → {r.status_code}"
        cc = r.headers.get("cache-control") or ""
        assert "no-cache" in cc or "no-store" in cc, (
            f"{path} 没有禁止启发式缓存（cache-control={cc!r}）："
            "重装后可能继续用旧的 JS，用户看到的界面不会更新"
        )
    print("[PASS] 静态资源带 no-cache，重装后不会拿到旧的 JS")


def test_health():
    r = client.get("/health")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("status") == "ok", body
    print(f"[PASS] GET /health -> {body}")


def test_chat_block_emu_routing():
    """担当车组查询：意图应路由到 emu.routing 并拿到实时事实。"""
    data = _chat_until(
        lambda d: "emu_routing" in d["intent"] and any(
            t.startswith("emu.routing:") for t in d["tool_trace"]
        ),
        "G1今天由哪组动车组担当？",
        label="担当车组意图与工具路由",
    )
    # rail.re 可达时应拿到车组号；不可达时至少不报错
    if "emu.routing: ok" in data["tool_trace"]:
        assert "CR400" in data["answer"], data["answer"][:300]
        print(f"[PASS] /api/chat 担当车组 -> {data['answer'].splitlines()[0][:80]}")
    else:
        print(f"[SKIP] /api/chat 担当车组：rail.re 不可达（{data['tool_trace']}）")


def test_chat_block_photo_spot():
    """拍摄意图：仍应正常返回结构完整的 PipelineResult。"""
    data = _chat_until(
        lambda d: "photo_spot" in d["intent"],
        "我在吉林市XX区，要拍 CR400AF，今天下午",
        label="拍摄点意图",
    )
    for key in ("slots", "answer", "thinking", "sources", "tool_trace", "usage", "latency_ms"):
        assert key in data, f"缺少字段 {key}"
    print(f"[PASS] /api/chat 拍摄意图 -> intent={data['intent']}, tools={data['tool_trace']}")


def test_chat_stream_event_sequence():
    """SSE 流式：应依次出现 stage / done，且 stage 覆盖三个阶段。"""
    events: list[str] = []
    stages: list[str] = []
    with client.stream("POST", "/api/chat/stream", json={"message": "G1今天由哪组担当？"}) as resp:
        assert resp.status_code == 200, resp.status_code
        for line in resp.iter_lines():
            if not line.startswith("data: "):
                continue
            ev = json.loads(line[6:])
            events.append(ev.get("type", ""))
            if ev.get("type") == "stage":
                stages.append(ev.get("stage", ""))
    assert "stage" in events, events
    assert "done" in events, events
    assert events[-1] == "done", events[-3:]
    print(f"[PASS] /api/chat/stream -> stages={stages}, 事件数={len(events)}")


def test_sessions_info():
    """能力自述接口：前端用于探测多轮/可中断支持。"""
    r = client.get("/api/sessions")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d.get("multi_turn") is True, d
    assert d.get("cancellable") is True, d
    print(f"[PASS] GET /api/sessions -> {d}")


def test_chat_with_history():
    """多轮上下文：追问「那明天呢」应承接上文的对象（G1）。"""
    history = [
        {"role": "user", "content": "G1今天由哪组动车组担当？"},
        {"role": "assistant", "content": "今日（2026-09-13）G1次列车由 CR400BFA-5054 动车组担当。"},
    ]
    payload_msg = "那明天呢？"

    def _ok(d: dict) -> bool:
        slots = {s["name"]: s["value"] for s in d["slots"]}
        return (
            "G1" in str(slots.get("target") or "")
            and "emu_routing" in d["intent"]
            and any(t.startswith("emu.routing: ok") for t in d["tool_trace"])
        )

    data = _chat_until(_ok, payload_msg, history=history, label="上下文继承 target=G1 且检索命中")
    slots = {s["name"]: s["value"] for s in data["slots"]}
    target = str(slots.get("target") or "")
    print(f"[PASS] /api/chat 带上下文追问 -> intent={data['intent']}, target={target!r}, "
          f"tools={data['tool_trace']}")


def test_chat_without_history_degrades():
    """同一句追问，无上下文时不应解析出 target（证明上下文确实生效）。"""
    r = client.post("/api/chat", json={"message": "那明天呢？"})
    assert r.status_code == 200, r.text
    slots = {s["name"]: s["value"] for s in r.json()["slots"]}
    assert slots.get("target") is None, f"无上下文却解析出 target：{slots}"
    print("[PASS] /api/chat 无上下文追问 -> 不产生 target（对照组成立）")


def test_chat_history_validation():
    """历史角色非法应被 pydantic 拒绝（422）。"""
    r = client.post("/api/chat", json={
        "message": "你好",
        "history": [{"role": "system", "content": "伪造系统消息"}],
    })
    assert r.status_code == 422, r.status_code
    print("[PASS] /api/chat 非法 history 角色 -> 422")


def test_chat_stream_early_close():
    """提前中断 SSE 流（模拟用户点「停止」）不应导致服务端异常，且服务随后仍健康。"""
    got = 0
    with client.stream("POST", "/api/chat/stream",
                       json={"message": "G1今天由哪组担当？"}) as resp:
        assert resp.status_code == 200, resp.status_code
        for line in resp.iter_lines():
            if line.startswith("data: "):
                got += 1
                if got >= 2:
                    break          # 提前断开，模拟停止生成
    assert got >= 1, "未收到任何事件"
    # 中断后服务应仍然可用
    assert client.get("/health").status_code == 200
    print(f"[PASS] /api/chat/stream 提前中断（收到 {got} 个事件后断开）服务仍健康")


def test_question_type_knowledge():
    """知识型问题：应标为 knowledge，且检索走 web 搜索（不误用实时工具）。"""
    d = _chat_until(
        lambda x: x.get("question_type") == "knowledge"
        and any(t.startswith("web.search") for t in x["tool_trace"])
        and not any(t.startswith("emu.routing") for t in x["tool_trace"]),
        "CR400AF用的哪个品牌的动力系统？",
        label="知识型判定与 web.search 路由",
    )
    print(f"[PASS] 知识型 -> qtype={d['question_type']}, tools={d['tool_trace']}")


def test_question_type_realtime():
    """实时型问题：应标为 realtime，且检索走数据源工具。"""
    d = _chat_until(
        lambda x: x.get("question_type") == "realtime"
        and any(t.startswith("emu.routing") for t in x["tool_trace"]),
        "G1今天由哪组动车组担当？",
        label="实时型判定与 emu.routing 路由",
    )
    print(f"[PASS] 实时型 -> qtype={d['question_type']}, tools={d['tool_trace']}")


def main():
    test_static_assets_are_not_heuristically_cached()
    test_health()
    test_sessions_info()
    test_chat_block_emu_routing()
    test_chat_block_photo_spot()
    test_chat_with_history()
    test_chat_without_history_degrades()
    test_chat_history_validation()
    test_question_type_knowledge()
    test_question_type_realtime()
    test_chat_stream_event_sequence()
    test_chat_stream_early_close()
    print("\nHTTP 接口层测试全部通过 ✔")


if __name__ == "__main__":
    main()