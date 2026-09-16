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
HARNESS_NATIVE = REPO_ROOT / "frontend" / "tests" / "store.native.test.mjs"


def _run_node_harness(node: str, harness: Path, what: str) -> None:
    assert harness.exists(), f"缺少测试脚本：{harness}"
    proc = subprocess.run(
        [node, str(harness)], capture_output=True, text=True, timeout=120, cwd=str(REPO_ROOT)
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    for line in out.splitlines():
        if line.startswith("[PASS]") or line.startswith("[FAIL]") or line.startswith("结果"):
            print(line)
    assert proc.returncode == 0, f"{what}失败：\n{out}"


def test_store_logic():
    node = shutil.which("node")
    if not node:
        print("[SKIP] 未安装 node，跳过前端数据层测试（建议安装 node 以纳入 CI）")
        return
    _run_node_harness(node, HARNESS, "store.js 逻辑测试")
    print("[PASS] 前端数据层（多对话/重命名/删除/搜索/持久化/容量保护）逻辑正确")


def test_store_survives_origin_change():
    """接了原生桥时，状态必须与 WebView 的「源」无关 —— 换源后数据仍在。

    这条钉的是一个真实的数据丢失形状：localStorage 按 scheme://host:port 隔离，
    而 Android 版的后端端口会变（上次那个被别的 App 占了就换）。端口一变，
    对话/供应商配置/记住的 Key 会全部读不到，用户看到的是"数据没了"。
    所以必须走原生侧的应用私有文件，并且 Key 要走系统密钥库而不是明文。

    测试用"清空 localStorage + 丢弃模块缓存重新 import"来模拟那次换源。
    """
    node = shutil.which("node")
    if not node:
        print("[SKIP] 未安装 node，跳过原生桥数据层测试")
        return
    _run_node_harness(node, HARNESS_NATIVE, "原生桥数据层测试")
    print("[PASS] 换源后对话/主题/配置仍在，且 API Key 只以密文存在于密钥库")


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
    test_store_survives_origin_change()
    test_frontend_has_no_account_ui()
    print("\n前端数据层测试全部通过 ✔")


if __name__ == "__main__":
    main()
