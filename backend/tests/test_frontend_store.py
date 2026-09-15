"""前端本机数据层（store.js）的逻辑测试（无网络，需要 node）。

为什么放在测试套件里：**"新对话会覆盖旧对话"是用户明确反馈的缺陷**，
会话管理是本机纯逻辑，值得用自动化钉住（不依赖浏览器即可验证）。

- 运行 `frontend/tests/store.test.mjs`（用 localStorage 打桩）
- 未安装 node 时给出明确跳过提示（不判失败）

运行：cd backend && PYTHONPATH=. .venv/bin/python tests/test_frontend_store.py
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = REPO_ROOT / "frontend" / "tests" / "store.test.mjs"


def test_store_logic():
    node = shutil.which("node")
    if not node:
        print("[SKIP] 未安装 node，跳过前端数据层测试（建议安装 node 以纳入 CI）")
        return
    assert HARNESS.exists(), f"缺少测试脚本：{HARNESS}"
    proc = subprocess.run(
        [node, str(HARNESS)], capture_output=True, text=True, timeout=120, cwd=str(REPO_ROOT)
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    for line in out.splitlines():
        if line.startswith("[PASS]") or line.startswith("[FAIL]") or line.startswith("结果"):
            print(line)
    assert proc.returncode == 0, f"store.js 逻辑测试失败：\n{out}"
    print("[PASS] 前端数据层（多对话/重命名/删除/搜索/持久化/容量保护）逻辑正确")


def test_frontend_has_no_account_ui():
    """前端**不应**包含账户相关接线（本项目不需要登录）。

    用测试钉住这条约束：一旦有人误把带登录/鉴权的版本拷进来，
    会立刻在这条断言上失败。
    """
    html = (REPO_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    js = (REPO_ROOT / "frontend" / "src" / "main.js").read_text(encoding="utf-8")
    pages = (REPO_ROOT / "frontend" / "src" / "pages.js").read_text(encoding="utf-8")

    for needle, why in [("auth-gate", "index.html 仍含登录引导层"),
                        ("profile-btn", "index.html 仍含个人中心入口"),
                        ("Authorization", "main.js 仍在发鉴权头"),
                        ("isLoggedIn", "main.js 仍含登录判定"),
                        ("/api/me", "前端仍在调账户接口"),
                        ("login-password", "前端仍含管理员登录")]:
        blob = html + js + pages
        assert needle not in blob, f"{why}：{needle}"
    # 多对话能力必须保留（核心体验）
    assert "id=\"conv-list\"" in html and "createConversation" in js or "store.create" in js, "多对话接线丢失"
    print("[PASS] 前端无账户接线（登录层/个人中心/鉴权头均不存在），多对话保留")

def main():
    test_store_logic()
    test_frontend_has_no_account_ui()
    print("\n前端数据层测试全部通过 ✔")


if __name__ == "__main__":
    main()
