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

import re
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


def test_interrupted_answer_is_visible():
    """没有前台服务时，被系统回收的中途回答必须"看得见"，不能只剩一个空气泡。

    本项目**刻意不申请保活权限**（产品决策），所以进程被回收是难免的。既然避免不了，
    就要保证后果是可见的：流式途中定期落盘 + 打 `meta.streaming` 标记 + 重开时如实提示。
    """
    js = (REPO_ROOT / "frontend/src/main.js").read_text(encoding="utf-8")
    assert 'meta: { streaming: true }' in js, (
        "生成开始时没有打上 streaming 标记：进程被杀后无法判断这条回答是否写完"
    )
    assert "persistThrottled" in js, (
        "流式途中没有定期落盘：正文只在结束时写一次，中途被杀会留下一个空气泡"
    )
    assert "delete assistant.meta.streaming" in js, (
        "正常结束后没有摘掉 streaming 标记：会把完整回答误报成被打断"
    )
    assert "上次回答在生成中被系统中断" in js, "被打断的回答没有如实提示"

    # 关键回归：提示**不能**只看 meta.streaming。生成开始时那条消息的标记本来就是
    # true，而它在流式开始时就渲染好了 —— 只判断标记的话，每生成一次都会立刻挂上
    # "被中断"的提示，直到下次重渲染（切换对话）才消失。
    # 用户实测原话："无条件附带……切换一下对话再切回来，这个提示就会消失。"
    assert "state.live = { convId, index: aIndex }" in js, (
        "流式开始时没有登记「哪一条正在生成」—— 无法区分「活着」与「上次被打断」"
    )
    assert "state.live = null" in js, "生成结束后没有清掉「正在生成」的登记"
    assert "if (meta.streaming && !live)" in js, (
        "中断提示又变成只看 meta.streaming 了：正在生成的那条会被误报为被打断"
    )
    print("[PASS] 中断的回答会留下已生成部分并如实标注，且正常结束/生成中都不会误报")


def test_cdp_tool_bypasses_system_proxy():
    """调试工具连的是回环端口，必须显式绕开系统代理。

    实测踩过：macOS 的系统代理开着时，urllib 会把 127.0.0.1 的 CDP 端口也塞进代理，
    代理回 502 Bad Gateway，整个调试通道突然不可用（而 websockets 那处早就写了
    proxy=None）。两处必须一起绕，否则换网络就复发。
    """
    cdp = (REPO_ROOT / "scripts/android/cdp.py").read_text(encoding="utf-8")
    assert "ProxyHandler({})" in cdp, "fetch_targets 没有绕开系统代理（回环会被代理成 502）"
    assert "proxy=None" in cdp, "websockets 连接没有绕开系统代理"
    print("[PASS] cdp.py 两处都绕开了系统代理，回环调试不会被代理劫持")


def test_only_tables_scroll_horizontally():
    """页面不许横向滚动，**只有表格**可以（用户明确要求）。

    理由：横拖会把内容拽出安全区，而竖排阅读本来就不需要它；
    车次表天然六七列，是唯一真正需要横向滚动的场景，所以给它单独一个滚动容器。
    """
    html = (REPO_ROOT / "frontend/index.html").read_text(encoding="utf-8")
    assert "html, body { height:100%; overflow-x:hidden" in html, (
        "页面级横向滚动没有被禁止（html/body 缺 overflow-x:hidden）"
    )
    assert "overflow-x:auto" in html, "表格的横向滚动容器丢了"
    # 表格之外不该再有横向滚动容器
    import re
    scrolls = re.findall(r"([.#][\w-]+[^{]*)\{[^}]*overflow-x:\s*(?:auto|scroll)", html)
    for sel in scrolls:
        assert "table-wrap" in sel, f"{sel.strip()} 也开了横向滚动：除表格之外都不该有"
    # 代码块要折行，不能横向滚
    m = re.search(r"\.md pre\.code \{([^}]*)\}", html)
    assert m and "pre-wrap" in m.group(1) and "overflow-x:hidden" in m.group(1), (
        "代码块还在横向滚动/不折行：规矩是只有表格可以横拖"
    )
    # 气泡要能收缩，否则内部不可断行内容会把整页撑宽
    assert ".bubble { min-width:0;" in html, "气泡缺 min-width:0，会被内容撑得比容器宽"
    print("[PASS] 页面禁止横向滚动，只有表格自带滚动容器，代码块折行")


def test_streaming_render_is_throttled():
    """流式正文渲染必须**合并 + 限频**，并且收尾时要能取消。

    为什么：原先每个 delta 都 `renderMarkdown(整篇) + innerHTML`，一次回答上百个 delta
    就把整篇 Markdown 重新解析、整棵子树重新排版上百次（代价随回答长度增长，O(n²)）——
    桌面端看不出来，移动 WebView 上就是滚动卡顿、键盘跟随迟滞。

    最后那条断言同样是回归项：若收尾渲染之后还有一次排队中的渲染，它会把已经摘掉的
    流式光标重新贴回去（回答写完了光标还在闪）。所以 `finally` 里必须先 `.cancel()`。
    """
    js = (REPO_ROOT / "frontend/src/main.js").read_text(encoding="utf-8")
    assert 'from "./throttle.js"' in js, "main.js 没有引入节流器"
    assert "renderAnswer()" in js and "renderAnswer.cancel()" in js, (
        "delta 没有走节流渲染，或收尾时没有取消挂起的那一次"
    )
    assert "function rafThrottle" not in js, "节流器应留在 throttle.js 才可脱离浏览器测试"
    branch = js.split('case "answer":', 1)[1].split("break;", 1)[0]
    assert "renderAnswer()" in branch, "answer 分支没有走节流渲染"
    assert "innerHTML" not in branch, (
        "answer 分支又直接写 innerHTML 了：等于每个 delta 全量渲染一次"
    )

    # 收尾渲染的判据必须是"有没有正文"，不能是"DOM 里有没有 caret"。
    # 渲染被节流到下一帧，而很快的回答（mock / 命中缓存）可能在第一帧之前就流完，
    # 那时 caret 没来得及画出来 —— 按 caret 判断就会整段回答不显示（模拟器实测踩过）。
    assert 'refs.ans.querySelector(".caret")' not in js, (
        "收尾渲染又用 caret 当判据了：快回答会整段不显示（正文空白，只剩意图与日志）"
    )
    assert "if (answerRaw) {" in js, "收尾没有按「有正文就渲染」来兜底"

    node = shutil.which("node")
    if not node:
        print("[SKIP] 未安装 node，跳过节流器单测")
        return
    harness = REPO_ROOT / "frontend" / "tests" / "throttle.test.mjs"
    assert harness.exists(), f"缺少测试脚本：{harness}"
    proc = subprocess.run(
        [node, str(harness)], capture_output=True, text=True, timeout=120, cwd=str(REPO_ROOT)
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    for line in out.splitlines():
        if line.startswith(("[PASS]", "[FAIL]", "结果")):
            print(line)
    assert proc.returncode == 0, f"节流器测试失败：\n{out}"
    print("[PASS] 流式渲染已合并限频，且收尾 cancel() 不会重贴光标")


def test_theme_follows_system():
    """深浅色必须**默认跟随系统**，并且首屏不能闪。

    为什么钉这么细：这套逻辑在两处各有一份实现 —— `src/theme.js`（运行时）与
    `index.html` 里的内联引导脚本（首屏）。模块脚本是 defer 的，等它跑完才应用主题，
    系统是浅色时会先黑一下再变白，所以内联那份不能删；但两份实现**必须一致**，
    否则"跟随系统"在首屏与之后的行为会不一样（且只在某些账号下复现）。
    """
    html = (REPO_ROOT / "frontend/index.html").read_text(encoding="utf-8")
    store_js = (REPO_ROOT / "frontend/src/store.js").read_text(encoding="utf-8")
    main_js = (REPO_ROOT / "frontend/src/main.js").read_text(encoding="utf-8")
    pages_js = (REPO_ROOT / "frontend/src/pages.js").read_text(encoding="utf-8")
    theme_js = (REPO_ROOT / "frontend/src/theme.js").read_text(encoding="utf-8")

    assert (REPO_ROOT / "frontend/src/theme.js").exists(), "主题逻辑应独立成模块才可脱离浏览器测试"
    assert re.search(r'<meta\s+name="color-scheme"\s+content="light dark"\s*/?>', html), (
        "缺 color-scheme 声明：WebView 会自行对深色做算法变暗（叠起来是一片死黑），"
        "滚动条/下拉框也不会跟着配色"
    )
    assert "color-scheme: dark" in html and "color-scheme: light" in html, (
        "两套令牌都要声明 color-scheme"
    )

    # 内联引导脚本：必须在 <body> 之前、且不能靠模块脚本
    head = html.split("<body", 1)[0]
    assert 'document.documentElement.setAttribute("data-theme"' in head, (
        "首屏之前没有定下主题：系统浅色时会先黑一下再变白"
    )
    assert "prefers-color-scheme: light" in head, "内联脚本没有读系统偏好"

    # 两份实现不许漂移：模式取值、默认模式、主题色
    mods = re.search(r"var MODES = \[([^\]]+)\]", head)
    colors = re.search(r"var COLORS = \{([^}]+)\}", head)
    assert mods and colors, "内联脚本里找不到 MODES / COLORS（改动请同步更新本测试）"
    inline_modes = re.findall(r'"([a-z]+)"', mods.group(1))
    inline_colors = dict(re.findall(r"(\w+):\s*\"(#[0-9a-fA-F]+)\"", colors.group(1)))
    assert inline_modes == ["auto", "light", "dark"], inline_modes
    assert 'export const THEME_MODES = ["auto", "light", "dark"];' in theme_js, (
        "theme.js 的模式全集与 index.html 不一致"
    )
    for name, value in inline_colors.items():
        assert f'{name}: "{value}"' in theme_js, (
            f"主题色 {name}={value} 在 theme.js 里对不上（状态栏颜色会与页面不符）"
        )

    # store：默认 auto + 启动时挂监听
    assert "return normalizeMode(db.getItem(LS_THEME));" in store_js, (
        "store.theme() 又写死默认值了：应经过 normalizeMode（默认 auto = 跟随系统）"
    )
    assert "watchSystemTheme(" in store_js, "没有监听系统深浅色切换（改系统设置后不会实时跟随）"
    assert "initTheme()" in main_js and "setTheme(store.theme())" not in main_js, (
        "启动应调用 store.initTheme()（应用 + 挂监听），而不是只应用一次"
    )

    # 老版本每次启动都会把 "dark" 自动写进偏好（而当时界面上根本没有主题入口），
    # 不做一次性迁移的话，"跟随系统"对升级用户永远不生效 —— 表现为"新装的人浅色、
    # 升级的人还是黑的"。内联脚本与 store.js 用的是同一个标记键，必须对齐。
    assert "migrateTheme(db)" in store_js, "initTheme() 里没有做主题偏好的一次性迁移"
    marker = "railfan_theme_v2"
    assert f'LS_THEME_MIGRATED = "{marker}"' in store_js, "store.js 的迁移标记键变了"
    assert f'"{marker}"' in head, "内联首帧脚本没有用同一个迁移标记键（升级后第一帧会先黑一下）"

    # 设置页要给得出三档，否则"跟随系统"这个默认态无处回去
    for label in ("跟随系统", "浅色", "深色"):
        assert label in pages_js, f"设置页缺少「{label}」这一档"

    node = shutil.which("node")
    if not node:
        print("[SKIP] 未安装 node，跳过主题逻辑单测")
        return
    harness = REPO_ROOT / "frontend" / "tests" / "theme.test.mjs"
    assert harness.exists(), f"缺少测试脚本：{harness}"
    proc = subprocess.run(
        [node, str(harness)], capture_output=True, text=True, timeout=120, cwd=str(REPO_ROOT)
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    for line in out.splitlines():
        if line.startswith(("[FAIL]", "结果")):
            print(line)
    assert proc.returncode == 0, f"主题逻辑测试失败：\n{out}"
    print("[PASS] 外观：默认跟随系统（首屏内联 + 运行时两处一致），显式选择优先，系统切换实时跟随")


def _rel_luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    parts = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    chans = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in parts]
    return 0.2126 * chans[0] + 0.7152 * chans[1] + 0.0722 * chans[2]


def _contrast(fg: str, bg: str) -> float:
    l1, l2 = _rel_luminance(fg), _rel_luminance(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def _theme_tokens(block: str) -> dict:
    return dict(re.findall(r"--([a-z-]+):\s*(#[0-9a-fA-F]{6})", block))


def test_theme_tokens_meet_contrast():
    """两套主题的**文字色**都要达到 WCAG AA（对比度 ≥ 4.5）。

    为什么要测这个：深色那套的令牌直接沿用没问题，但浅色不是把颜色反过来就算完 ——
    实测发现 `--accent`（原 `#3b82f6`）在浅色底上只有 **3.44**，而它在本项目里是**大量用作
    文字色**的（`.intent` / `.stats` / `.fold summary` / `.badge`，字号 11–12px）；
    `--danger`（`#ef4444`）更低到 3.52，于是"出错了"这条提示反而最难读。
    这类问题肉眼看着"还行"，所以必须用数字卡住，否则以后加令牌时还会重犯。
    """
    html = (REPO_ROOT / "frontend/index.html").read_text(encoding="utf-8")
    dark_block = re.search(r":root \{(.*?)\n    \}", html, re.S)
    light_block = re.search(r'html\[data-theme="light"\] \{(.*?)\n    \}', html, re.S)
    assert dark_block and light_block, "两套主题的令牌块没找全"

    # 只查**文字色**。排除项各有理由：
    #   *-bg / *-line / hover —— 底与描边，不是文字
    #   accent-dim —— 只用作 .btn.primary:hover 的**背景**（深色下 #2563eb 本身就只有 3.66）
    #   ok —— .prov-dot.ok 是状态圆点，按非文本标准 3:1 判（3.08 达标）
    #   panel / on-accent / user-bg —— 底或铺在底上的字，由各自的组合决定
    text_tokens = ("fg", "mut", "accent", "danger", "warn", "purple", "code-fg")
    for label, block in (("深色", dark_block.group(1)), ("浅色", light_block.group(1))):
        tokens = _theme_tokens(block)
        bg = tokens.get("bg")
        assert bg, f"{label}主题没有 --bg"
        for name in text_tokens:
            color = tokens.get(name)
            assert color, f"{label}主题没有定义 --{name}（浅色那套必须逐个覆盖，不能靠继承）"
            ratio = _contrast(color, bg)
            assert ratio >= 4.5, (
                f"{label}主题 --{name}={color} 在 --bg={bg} 上对比度只有 {ratio:.2f}（要求 ≥4.5）"
            )
        print(f"[PASS] {label}主题文字令牌对比度均 ≥4.5（最低 "
              f"{min(_contrast(tokens[n], bg) for n in text_tokens):.2f}）")


def main():
    test_markdown_tables_render()
    test_renderer_is_a_separate_module()
    test_answer_layout_and_focus()
    test_interrupted_answer_is_visible()
    test_streaming_render_is_throttled()
    test_theme_follows_system()
    test_theme_tokens_meet_contrast()
    test_cdp_tool_bypasses_system_proxy()
    test_only_tables_scroll_horizontally()
    print("\n前端渲染测试全部通过 ✔")


if __name__ == "__main__":
    main()
