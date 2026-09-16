"""前端 Markdown 渲染测试（无网络，需要 node）。

为什么值得一条独立测试：**"模型输出的表格无法被渲染"是用户实测反馈的缺陷**，
而车次/余票这类回答基本都以表格形式给出 —— 渲染不出来，整条回答就只剩一坨竖线。
渲染规则是纯字符串处理，不需要浏览器，用 node 直接跑最快、也最容易覆盖边界情况
（代码块里的表格示例、单独一行 `---`、单元格里的 HTML 转义等）。

- 运行 `frontend/tests/markdown.test.mjs`
- 未安装 node 时给出明确跳过提示（不判失败）

运行：cd backend && PYTHONPATH=. .venv/bin/python tests/test_frontend_render.py
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = REPO_ROOT / "frontend" / "tests" / "markdown.test.mjs"
MODULE = REPO_ROOT / "frontend" / "src" / "markdown.js"


def test_markdown_tables_render():
    node = shutil.which("node")
    if not node:
        print("[SKIP] 未安装 node，跳过前端渲染测试（建议安装 node 以纳入 CI）")
        return
    assert MODULE.exists(), (
        f"缺少 {MODULE}：Markdown 渲染被拆成独立模块才能脱离浏览器测试"
    )
    assert HARNESS.exists(), f"缺少测试脚本：{HARNESS}"
    proc = subprocess.run(
        [node, str(HARNESS)], capture_output=True, text=True, timeout=120, cwd=str(REPO_ROOT)
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    for line in out.splitlines():
        if line.startswith(("[PASS]", "[FAIL]", "结果")):
            print(line)
    assert proc.returncode == 0, f"Markdown 渲染测试失败：\n{out}"
    print("[PASS] Markdown：表格/对齐/单元格行内语法正确，代码块与转义未被破坏")


def test_renderer_is_a_separate_module():
    """渲染逻辑必须在 main.js 之外，且 main.js 是从它 import 的。

    这条不是洁癖：main.js 在模块顶层就会碰 DOM，node 里 import 不了；渲染规则一旦
    写在里面，就只能靠"起个浏览器点一遍"来验证，而表格恰恰是最需要回归测试的部分。
    """
    main_js = (REPO_ROOT / "frontend/src/main.js").read_text(encoding="utf-8")
    assert 'from "./markdown.js"' in main_js, "main.js 没有从 markdown.js 引入渲染函数"
    assert "function renderMarkdown" not in main_js, (
        "main.js 里又出现了 renderMarkdown 的实现：渲染逻辑应留在 markdown.js 才可测"
    )
    print("[PASS] 渲染逻辑独立成模块，main.js 只做引入")


def test_answer_layout_and_focus():
    """回答气泡里的元素顺序与"生成完不要弹键盘"是用户明确提的需求，钉住它们。

    顺序：流程日志 → 思考过程 → **正文** → 统计 → **数据来源**
    （用户在实测反馈里要求把日志与思考放到正文上方，来源留在下方。）

    焦点：生成结束后**不能**无条件聚焦输入框 —— 手机上这会立刻弹出软键盘，
    把刚生成的回答顶走半屏。判据是 `(hover: hover)`（是否真有指针设备），
    桌面端保聚焦以便接着打下一句。
    """
    js = (REPO_ROOT / "frontend/src/main.js").read_text(encoding="utf-8")
    i_logs = js.index('流程日志（意图 / 抽取 / 检索 / 生成）')
    i_think = js.index('思考过程（think）')
    i_ans = js.index('const ans = el("div", "md")')
    i_src = js.index('"数据来源："')
    assert i_logs < i_ans, "流程日志又跑到正文下面了"
    assert i_think < i_ans, "思考过程又跑到正文下面了"
    assert i_src > i_ans, "数据来源必须在正文之后（它是结论的出处）"
    assert 'matchMedia("(hover: hover)")' in js, (
        "生成结束后又无条件聚焦输入框了：手机会每次弹软键盘把回答顶走"
    )
    print("[PASS] 气泡顺序为 流程日志→思考→正文→来源，且触屏设备不再自动弹键盘")


def main():
    test_markdown_tables_render()
    test_renderer_is_a_separate_module()
    test_answer_layout_and_focus()
    print("\n前端渲染测试全部通过 ✔")


if __name__ == "__main__":
    main()
