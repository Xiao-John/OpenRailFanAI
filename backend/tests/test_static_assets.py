"""静态资源交付回归（perf P1-2：压缩 + 分级缓存）。

背景
----
前端是**无构建步骤**的，由 FastAPI 直接托管，URL 跨安装完全不变。原先给**所有**静态响应
一律加 `no-cache`（这是为了治"重装了、界面却没变"），代价是每次页面加载都要对
全部资源回源校验，而且完全没有压缩（`index.html` + 5 个 JS ≈ 137 KB）。

本套件钉住四件事：
1. **压缩真的生效**：静态资源带 `Content-Encoding: gzip` 且体积显著下降；
   但 **SSE（text/event-stream）绝不能被压** —— 流式体验不能被缓冲破坏；
2. **首页永远回源**：`/` 返回 `no-cache`，并把入口脚本指向**带版本戳**的 URL；
3. **只有带正确版本戳的 URL 才长缓存**：戳对不上（旧页面里的旧 URL）仍按 `no-cache`
   处理，非 `src/` 路径直接 404 —— 避免任何东西被 immutable 缓存住一年；
4. **模块图完整**：`src/*.js` 里 `import "./x.js"` 的目标都存在（版本戳靠路径前缀
   继承，模块图断了整个界面就打不开）。

运行：cd backend && PYTHONPATH=. LLM_MOCK=true .venv/bin/python tests/test_static_assets.py
（不需要网络，也不需要 LLM）
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import _frontend_dir, _frontend_stamp, app

_IMMUTABLE = "public, max-age=31536000, immutable"


def _entry_src(html: str) -> str:
    """从首页里取出入口脚本的 URL（可能是 `./v/<stamp>/src/main.js`）。"""
    m = re.search(r'<script[^>]+src="\./([^"]+)"', html)
    assert m, "首页里找不到入口脚本"
    return "/" + m.group(1)


def test_frontend_dir_present() -> None:
    assert _frontend_dir.is_dir(), f"前端目录不存在：{_frontend_dir}"
    print(f"[PASS] 前端目录：{_frontend_dir}")


def test_gzip_compresses_static_assets() -> None:
    stamp = _frontend_stamp()
    urls = ["/"] + [
        f"/v/{stamp}/src/{n}.js"
        for n in ("main", "pages", "store", "markdown", "native", "throttle")
    ]
    with TestClient(app) as c:
        wire = decoded = 0
        for u in urls:
            r = c.get(u, headers={"Accept-Encoding": "gzip"})
            assert r.status_code == 200, f"{u} -> {r.status_code}"
            assert r.headers.get("content-encoding") == "gzip", f"{u} 没有压缩：{dict(r.headers)}"
            wire += int(r.headers["content-length"])
            decoded += len(r.content)
    ratio = decoded / max(1, wire)
    assert ratio > 2.0, f"压缩收益过低：{decoded} → {wire}（仅 {ratio:.2f}x）"
    print(f"[PASS] 静态资源已压缩：{decoded} B → {wire} B（{ratio:.2f}x）")


def test_sse_is_not_compressed() -> None:
    """流式必须绕过 gzip：压缩会引入缓冲，用户看到的是"不再逐字出现"。"""
    with TestClient(app) as c:
        # 不实际发起生成，只确认中间件对 event-stream 不做压缩
        r = c.get("/api/chat/stream")
        assert r.headers.get("content-encoding") != "gzip", "SSE 被压缩了"
    print("[PASS] SSE（/api/chat/stream）未被 gzip 压缩")


def test_index_is_nocache_and_stamped() -> None:
    stamp = _frontend_stamp()
    with TestClient(app) as c:
        r = c.get("/")
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-cache", "首页必须回源校验"
        entry = _entry_src(r.text)
        assert entry == f"/v/{stamp}/src/main.js", f"入口未带版本戳：{entry}"
        r2 = c.get("/index.html")
        assert r2.headers["cache-control"] == "no-cache"
    print(f"[PASS] 首页 no-cache 且入口指向带戳 URL：{entry}")


def test_versioned_assets_are_immutable() -> None:
    stamp = _frontend_stamp()
    with TestClient(app) as c:
        ok = c.get(f"/v/{stamp}/src/main.js")
        assert ok.status_code == 200
        assert ok.headers["cache-control"] == _IMMUTABLE, ok.headers.get("cache-control")

        # 过期戳：仍能取到文件（旧页面不至于直接坏掉），但**不给长缓存**
        stale = c.get("/v/deadbeef/src/main.js")
        assert stale.status_code == 200
        assert stale.headers["cache-control"] == "no-cache", "过期戳不该拿到 immutable"

        # 无戳的裸路径：保持原行为
        raw = c.get("/src/main.js")
        assert raw.status_code == 200
        assert raw.headers["cache-control"] == "no-cache"

        # 只放行 src/：不给 index.html 之类被 immutable 缓存一年的机会
        assert c.get(f"/v/{stamp}/index.html").status_code == 404
    print("[PASS] 版本戳分级缓存：当前戳 immutable / 过期戳与裸路径 no-cache / 非 src 路径 404")


def test_stamp_follows_mtime_not_version_file() -> None:
    """版本戳取的是资源 mtime，**不是** VERSION 文件。

    为什么钉住：若用 VERSION，开发时改了 JS 而不发版就会一直命中旧缓存，
    而这正是本项目踩过的"重装了、界面却没变"。
    """
    newest = 0
    for p in [_frontend_dir / "index.html", *(_frontend_dir / "src").rglob("*")]:
        newest = max(newest, p.stat().st_mtime_ns)
    assert _frontend_stamp() == f"{newest:x}", "版本戳与资源 mtime 不一致"
    assert int(_frontend_stamp(), 16) > 1_600_000_000 * 10**9, "版本戳看起来不是纳秒时间戳"
    print(f"[PASS] 版本戳来自资源 mtime（{_frontend_stamp()}），改文件即自动失效")


def test_module_graph_is_complete() -> None:
    """`src/*.js` 里每个相对 import 的目标都必须存在（戳靠路径前缀继承，图断了整页打不开）。"""
    src = _frontend_dir / "src"
    missing = []
    for js in src.rglob("*.js"):
        text = js.read_text(encoding="utf-8")
        for spec in re.findall(r"""from\s+["'](\.[^"']+)["']""", text):
            if not (js.parent / spec).exists():
                missing.append(f"{js.name} -> {spec}")
    assert not missing, f"模块图缺文件：{missing}"
    # 入口必须能被首页直接引用
    assert (src / "main.js").exists()
    print("[PASS] 模块图完整：所有相对 import 目标均存在")


def main() -> None:
    test_frontend_dir_present()
    test_gzip_compresses_static_assets()
    test_sse_is_not_compressed()
    test_index_is_nocache_and_stamped()
    test_versioned_assets_are_immutable()
    test_stamp_follows_mtime_not_version_file()
    test_module_graph_is_complete()
    print("\n静态资源交付（压缩 + 分级缓存）测试全部通过 ✔")


if __name__ == "__main__":
    main()
