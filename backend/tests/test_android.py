"""Android 一体化版本的约束回归测试（无网络）。

为什么值得单独用测试钉住：
    "能在 Android 上跑"依赖若干**容易在后续改动中静默失效**的约束 ——
    pydantic 必须是 v1、FastAPI 必须 <0.126、依赖闭包必须全纯 Python、
    依赖兼容层必须真的起作用。这些一旦被破坏，本机测试仍然全绿，
    只有到真机构建时才炸（或更糟：构建成功但运行期 ImportError）。
    所以把约束写成断言，改动越界时立刻失败。

运行：cd backend && PYTHONPATH=. .venv/bin/python tests/test_android.py
"""
from __future__ import annotations

import os
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ANDROID_DIR = REPO_ROOT / "android"
BACKEND_REQ = REPO_ROOT / "backend/requirements.txt"
ANDROID_REQ = ANDROID_DIR / "requirements.txt"

# 在 Android 上**装不出来**的包（原生扩展，且 Chaquopy 仓库里没有），
# 一旦出现在 Android 依赖清单里，构建会失败或运行期 ImportError。
FORBIDDEN_IN_ANDROID = {
    "jiter",            # Rust；openai 的依赖，已用替身
    "pydantic-core",    # Rust；pydantic v2 的核心，已锁 v1
    "pydantic_core",
    "uvloop",           # uvicorn[standard]
    "httptools",        # uvicorn[standard]
    "watchfiles",       # uvicorn[standard]
    "websockets",       # uvicorn[standard]
    "cryptography",     # 仅 MCP SDK 链路需要
    "cffi",
    "rpds-py",
    "brotli",           # 缺失时 _http.py 自动降级 gzip/deflate
    "pydantic-settings",  # 要求 pydantic>=2，与 Android 的 v1 冲突
}


def _req_names(path: Path) -> set[str]:
    """解析 requirements 文件里的包名（忽略注释与空行）。"""
    names = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        m = re.match(r"([A-Za-z0-9._-]+)", line)
        if m:
            names.add(m.group(1).lower().replace("_", "-"))
    return names


# ---------------------------------------------------------------- 依赖约束
def test_android_requirements_are_pure_python():
    names = _req_names(ANDROID_REQ)
    bad = names & FORBIDDEN_IN_ANDROID
    assert not bad, (
        f"android/requirements.txt 含 Android 装不出来的包：{sorted(bad)}\n"
        "→ 这些是原生扩展（或要求 pydantic v2），必须从清单移除并改用 _compat 替身"
    )
    # 关键依赖必须显式在列（--no-deps 安装，漏一个就是运行期 ImportError）
    for must in ("fastapi", "pydantic", "uvicorn", "openai", "httpx", "httpx2",
                 "mcp-server-12306", "h2", "pypinyin", "aiofiles", "pytz"):
        assert must in names, f"android/requirements.txt 缺少 {must}（--no-deps 下必须列全）"
    print(f"[PASS] Android 依赖清单 {len(names)} 项，无原生扩展、关键项齐全")


def test_android_requirements_are_pinned():
    """全部锁死版本：避免上游大版本漂移让"某个时间点起就构建不出来"。"""
    unpinned = []
    for line in ANDROID_REQ.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        if "==" not in line:
            unpinned.append(line)
    assert not unpinned, f"以下依赖未锁定版本：{unpinned}"
    print("[PASS] Android 依赖全部锁定版本")


def test_backend_requirements_keep_android_constraints():
    """服务端依赖必须保留两条 Android 硬约束（否则 Android 直接构建不出来）。"""
    text = BACKEND_REQ.read_text(encoding="utf-8")
    assert re.search(r"pydantic[^\n]*<\s*2", text), (
        "backend/requirements.txt 未把 pydantic 限制在 <2 —— pydantic v2 依赖 "
        "pydantic-core(Rust)，Android 无轮子。"
    )
    assert re.search(r"fastapi[^\n]*<\s*0\.126", text), (
        "backend/requirements.txt 未把 fastapi 限制在 <0.126 —— "
        "FastAPI 0.126.0 起移除了 pydantic v1 支持（fastapi/types.py 引入 IncOpt/IncEx）。"
    )
    print("[PASS] 服务端依赖保留了 pydantic<2 与 fastapi<0.126 两条 Android 约束")


def test_installed_versions_satisfy_android_constraints():
    """当前环境实际装到的版本也要满足约束（防止只在文件里锁了、环境没跟上）。"""
    import fastapi
    import pydantic

    major = int(pydantic.VERSION.split(".")[0])
    assert major == 1, f"pydantic 实际为 {pydantic.VERSION}，Android 要求 v1"
    fver = tuple(int(x) for x in re.findall(r"\d+", fastapi.__version__)[:2])
    assert fver < (0, 126), f"fastapi 实际为 {fastapi.__version__}，Android 要求 <0.126"
    print(f"[PASS] 运行环境版本合规：pydantic {pydantic.VERSION} / fastapi {fastapi.__version__}")


# ---------------------------------------------------------------- 兼容层
def test_compat_shims_are_functional():
    """替身必须真的可用 —— 它们是 Android 能跑起来的前提。"""
    from app import _compat

    enabled = _compat.install(force=True)
    assert "jiter" in enabled and "pydantic_settings" in enabled, (
        f"force 模式下应启用两个替身，实际 {enabled}"
    )

    import jiter

    assert jiter.from_json(b'{"a": 1}') == {"a": 1}
    assert jiter.from_json('{"a": [1, 2]}') == {"a": [1, 2]}
    # partial 模式**必须报错而不是猜**：它只被 openai 流式 .parsed 用，
    # 硬凑会返回错误结果（比报错危险得多）
    try:
        jiter.from_json(b'{"a":', partial_mode=True)
    except NotImplementedError as e:
        assert "partial_mode" in str(e)
    else:
        raise AssertionError("jiter 替身的 partial_mode 未按约定报错")
    print("[PASS] jiter 替身：完整解析可用，partial_mode 明确报错")


def test_pydantic_settings_shim_translates_config():
    """替身要把 v2 的 model_config 真正翻译到 v1 的 Config，而不是被静默忽略。"""
    from app import _compat

    _compat.install(force=True)
    import pydantic_settings

    # pydantic v1 的 env 来源按**字段名**匹配环境变量（大小写不敏感），
    # 字段是 probe 就必须是 PROBE —— 写成 RAILFAN_COMPAT_PROBE 是匹配不上的。
    os.environ["PROBE"] = "9123"

    class Probe(pydantic_settings.BaseSettings):
        probe: int = 8000
        host: str = "0.0.0.0"
        model_config = pydantic_settings.SettingsConfigDict(
            env_file=None, extra="ignore", case_sensitive=False
        )

    s = Probe()
    # case_sensitive=False 生效（小写字段名命中大写环境变量），extra="ignore" 生效（多余环境变量不报错）
    assert s.probe == 9123, f"model_config 未生效：probe={s.probe}（期望读到大写的环境变量）"
    # model_config 本身不能被当成字段（v1 会把无标注可变默认值推导成字段）
    assert set(Probe.__fields__) == {"probe", "host"}, (
        f"替身引入了多余字段：{sorted(Probe.__fields__)}（model_config 不该成为字段）"
    )
    assert Probe.model_validate({}).host == "0.0.0.0", "model_validate 别名不可用"
    assert Probe().dict()["host"] == "0.0.0.0"
    print("[PASS] pydantic_settings 替身：model_config→Config 翻译生效，且不引入多余字段")


def test_compat_does_not_override_real_packages():
    """真包存在时**不得**启用替身（否则会静默用上假实现）。

    显式构造"真包存在"的场景，而不是依赖本机装没装 —— 否则该用例的结论
    会随运行环境漂移（Android 依赖环境里两个真包都不存在）。
    """
    import sys
    import types as _types

    from app import _compat

    saved = {n: sys.modules.get(n) for n in ("jiter", "pydantic_settings")}
    try:
        real_jiter = _types.ModuleType("jiter")      # 无 __railfan_shim__ 标记 → 视为真包
        sys.modules["jiter"] = real_jiter
        sys.modules.pop("pydantic_settings", None)   # 这个视为缺失

        enabled = _compat.install()
        assert "jiter" not in enabled, "真包存在时不应替换成替身"
        assert sys.modules["jiter"] is real_jiter, "真包被替身覆盖了"
        assert "pydantic_settings" in enabled, "缺包时应启用替身"
    finally:
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod
        _compat.install(force=True)                  # 还原为替身状态，避免影响后续用例
    print("[PASS] 兼容层只在真包缺失时启用，不影子覆盖真包")


# ---------------------------------------------------------------- 工程接线
def test_android_project_wiring():
    """关键接线点必须存在，否则构建会静默产出"能装但白屏"的包。"""
    gradle = (ANDROID_DIR / "app/build.gradle.kts").read_text(encoding="utf-8")
    manifest = (ANDROID_DIR / "app/src/main/AndroidManifest.xml").read_text(encoding="utf-8")
    activity = ANDROID_DIR / "app/src/main/java/org/openrailfanai/app/MainActivity.java"
    server_py = ANDROID_DIR / "app/src/main/python/server.py"

    for path in (activity, server_py, ANDROID_DIR / "requirements.txt",
                 ANDROID_DIR / "app/src/main/res/xml/network_security_config.xml"):
        assert path.exists(), f"缺少文件：{path}"

    # Chaquopy 必须用 --no-deps（否则 pydantic-settings/jiter 会把依赖解析搞失败）
    assert 'options("--no-deps")' in gradle, "build.gradle.kts 未使用 --no-deps 安装依赖"
    assert "../requirements.txt" in gradle or "requirements.txt" in gradle, "未引用 requirements.txt"
    # 依赖清单、后端源码、前端资源都要接进来
    assert "python-staging" in gradle and "srcDir(" in gradle, "未把后端源码接入 Python 源码集"
    assert "staged-assets" in gradle and "assets.srcDir" in gradle, "未把前端资源接入 assets"
    # 字典可选开关
    assert "includeDict" in gradle, "缺少 includeDict 开关"

    # 明文流量必须只对本机回环放行，而不是全放开
    assert "networkSecurityConfig" in manifest, "未引用网络安全配置"
    assert 'android:usesCleartextTraffic="false"' in manifest, (
        "不该用 usesCleartextTraffic=true（那会对全网放行明文）"
    )

    act = activity.read_text(encoding="utf-8")
    # WebView 必须是系统自带的那个（不引入 web 组件框架）
    assert "android.webkit.WebView" in act, "未使用系统 WebView"
    assert "setJavaScriptEnabled(true)" in act and "setDomStorageEnabled(true)" in act, (
        "前端依赖 JS 与 localStorage，两项都必须开启"
    )
    assert "onServerReady" in act and "onStartupFailed" in act, "缺少 Python→Java 回调"

    srv = server_py.read_text(encoding="utf-8")
    assert "FRONTEND_DIR" in srv, "启动器未设置 FRONTEND_DIR（打包后前端目录推导不成立，会 404）"
    assert "127.0.0.1" in srv, "后端必须只监听回环地址"

    print("[PASS] Android 工程接线：--no-deps / 源码与资源接入 / 回环明文 / 系统 WebView")


def test_frontend_dir_setting_is_honoured():
    """FRONTEND_DIR 必须真的被 main.py 使用（打包场景的唯一出路）。"""
    from app.config import Settings

    assert "frontend_dir" in Settings.__fields__, "Settings 缺少 frontend_dir 字段"
    main_py = (REPO_ROOT / "backend/app/main.py").read_text(encoding="utf-8")
    assert "settings.frontend_dir" in main_py, "main.py 未读取 frontend_dir 配置"
    # 目录不存在时要告警而不是静默 404
    assert "前端静态目录不存在" in main_py, "目录缺失时应显式告警（否则表现为难排查的主页 404）"
    print("[PASS] FRONTEND_DIR 已接入配置与静态托管，且缺失时有明确告警")


def test_env_example_covers_frontend_dir():
    """.env.example 必须包含 FRONTEND_DIR（打包用户照示例配置才知道有这个开关）。"""
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"^FRONTEND_DIR\s*=", text, re.M), ".env.example 缺少 FRONTEND_DIR"
    print("[PASS] .env.example 已包含 FRONTEND_DIR")


def test_shell_scripts_avoid_cjk_variable_capture():
    """shell 脚本里 `$VAR` 后**紧跟非 ASCII 字符**会踩坑，必须写成 `${VAR}`。

    原因：`echo "…$VAR（说明）"` 里，bash 会把全角括号的首字节吞进变量名，
    于是报 `VAR<乱码>: unbound variable` —— 而这个错误**只有执行到那一行才会出现**，
    `bash -n` 也查不出来（语法本身是合法的）。本项目已因此踩坑三次
    （setup-toolchain.sh / setup.sh / setup-emulator.sh），所以固化成测试。

    另外顺带做一遍 `bash -n` 语法检查：脚本是交付路径的一部分，语法错误不该等到用户才发现。
    """
    import re
    import subprocess

    bad: list[str] = []
    scripts = sorted(
        p for p in REPO_ROOT.rglob("*.sh")
        if ".android-build" not in p.parts and "node_modules" not in p.parts
    )
    assert scripts, "没有找到任何 shell 脚本（检查方式可能已失效）"

    cjk_after_var = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)(?=[^\x00-\x7f])")
    for p in scripts:
        text = p.read_text(encoding="utf-8", errors="replace")
        for name in cjk_after_var.findall(text):
            bad.append(f"{p.relative_to(REPO_ROOT)}: ${name} 后紧跟非 ASCII 字符 → 应写成 ${{{name}}}")
        # bash -n：语法检查（能查出括号/引号不配等问题）
        proc = subprocess.run(["bash", "-n", str(p)], capture_output=True, text=True)
        if proc.returncode != 0:
            bad.append(f"{p.relative_to(REPO_ROOT)}: bash -n 失败：{proc.stderr.strip()[:200]}")

    assert not bad, "shell 脚本问题：\n  " + "\n  ".join(bad)
    print(f"[PASS] {len(scripts)} 个 shell 脚本：无「变量后紧跟中文」隐患，且 bash -n 全部通过")


def test_apk_assets_satisfy_index_html_references():
    """构建产物里的前端资源必须能满足 index.html 的全部本地引用。

    这条针对一个真实事故：最早的解包实现把目录结构**拍平**了
    （`webapp/src/main.js` 被写成 `webapp/main.js`），于是 index.html 里
    `./src/main.js` 全部 404 —— 页面只剩静态骨架，用户看到的是"白屏"。
    静态资源 404 不会触发 WebView 的 onReceivedError，所以从应用侧完全看不出来。

    APK 不存在时跳过（本地没构建过 Android 时不该判失败）。
    """
    import zipfile

    apks = sorted((REPO_ROOT / "android/app/build/outputs/apk").rglob("*.apk"))
    if not apks:
        print("[SKIP] 未找到已构建的 APK，跳过资源引用检查（构建后会自动纳入）")
        return

    apk = apks[-1]
    with zipfile.ZipFile(apk) as z:
        names = set(z.namelist())
        index_names = [n for n in names if n.endswith("assets/webapp/index.html")]
        assert index_names, f"{apk.name} 里没有 assets/webapp/index.html"
        html = z.read(index_names[0]).decode("utf-8", "replace")

    # index.html 里引用的本地资源（相对路径）
    refs = re.findall(r'(?:src|href)="(\./[^"]+)"', html)
    assert refs, "index.html 里没有解析到任何本地资源引用（检查方式可能已失效）"
    missing = []
    for ref in refs:
        rel = ref[2:]                                  # 去掉 ./
        want = f"assets/webapp/{rel}"
        if want not in names:
            missing.append(f"{ref}（APK 内应有 {want}）")
    assert not missing, (
        "APK 内的前端资源与 index.html 的引用不一致：\n  " + "\n  ".join(missing)
        + "\n→ 检查 MainActivity 的 assets 解包是否保持了目录结构（曾因拍平导致 ./src/*.js 全部 404）"
    )
    print(f"[PASS] APK({apk.name}) 内前端资源满足 index.html 的 {len(refs)} 个本地引用")


def test_mcp_package_kept_out_of_normal_resolution():
    """mcp-server-12306 不得出现在常规依赖解析集合里。

    它的元数据依赖 pydantic-settings（要求 pydantic>=2），与本项目的 pydantic<2
    不可同时满足。放进同一个集合会让 pip 长时间回溯、最终 ResolutionImpossible ——
    真实现象就是 `setup.sh` 第一步像"卡死"（当时还叠加了 -q，屏幕上什么都没有，
    用户完全看不出原因）。必须用 --no-deps 单独安装，见 requirements-nodeps.txt。
    """
    normal = _req_names(BACKEND_REQ)
    assert "mcp-server-12306" not in normal, (
        "backend/requirements.txt 又出现了 mcp-server-12306：它与 pydantic<2 冲突，"
        "请放 requirements-nodeps.txt 并用 --no-deps 安装"
    )
    nodeps = REPO_ROOT / "backend/requirements-nodeps.txt"
    assert nodeps.exists(), "缺少 backend/requirements-nodeps.txt"
    assert "mcp-server-12306" in _req_names(nodeps), "requirements-nodeps.txt 里没有该包"

    # 它的真实运行时依赖必须在常规清单里显式列出（--no-deps 不会自动带进来）
    for dep in ("httpx2", "aiofiles", "pytz"):
        assert dep in normal, (
            f"backend/requirements.txt 缺少 {dep}：它是 mcp-server-12306 的真实运行时依赖，"
            "用 --no-deps 安装后必须由常规清单显式提供"
        )

    # setup.sh 必须两步走，并把 pip 源做成可切换（默认清华，可 PIP_INDEX= 关掉）
    setup = (REPO_ROOT / "scripts/setup.sh").read_text(encoding="utf-8")
    assert "--no-deps -r requirements-nodeps.txt" in setup, "setup.sh 未用 --no-deps 安装该包"
    assert "PIP_INDEX" in setup and "tuna.tsinghua" in setup, "setup.sh 未提供清华 pip 源"
    # 真实的 pip 调用行不得带 -q（注释里提到 -q 不算，注释本身是解释这条规矩的）
    bad_q = [
        ln.strip() for ln in setup.splitlines()
        if not ln.strip().startswith("#")
        and ("_pip " in ln or "pip install" in ln)
        and " -q" in ln
    ]
    assert not bad_q, (
        f"setup.sh 的 pip 调用带了 -q：{bad_q}\n"
        "→ 这几步是最容易「看起来卡死」的地方，必须让用户看到 pip 在下载还是求解"
    )
    print("[PASS] mcp-server-12306 已移出常规解析集合，运行时依赖显式在列；setup.sh 两步安装 + 可切换镜像")


def test_python_callbacks_use_attribute_access():
    """Python 调 Java 回调必须用属性调用，不能用 `callAttr`。

    真机事故：`activity.callAttr("onServerReady", port)` —— `callAttr` 是
    **Java 侧调用 Python 对象**（PyObject）的 API；Java 对象进到 Python 里是
    Chaquopy 的 `JavaObject`，要像普通属性那样调用。用错之后**每个回调**都抛
    `AttributeError: 'MainActivity' object has no attribute 'callAttr'` 并被吞掉：
    后端其实已正常监听、自检也通过，界面却永远停在"调用 server.serve()…"。
    这个错误在桌面上完全测不出来，只有真机 logcat 才看得到。
    """
    srv = (ANDROID_DIR / "app/src/main/python/server.py").read_text(encoding="utf-8")
    # 用 AST 找**真实的属性访问**，而不是搜文本 —— 否则文档字符串里
    # "曾经误写成 activity.callAttr(...)" 这样的解释性文字会被误判。
    import ast

    offenders = [
        node.attr
        for node in ast.walk(ast.parse(srv))
        if isinstance(node, ast.Attribute) and node.attr == "callAttr"
    ]
    assert not offenders, (
        "server.py 仍在用 callAttr 调用 Java（应改为 getattr(activity, method)(...)）："
        "callAttr 是 Java 侧调用 Python 对象的 API，Java 对象在 Python 侧只能按属性调用"
    )
    assert "getattr(activity, method)" in srv, (
        "未用 getattr(activity, method)(...) 调用 Java 回调 —— Java 对象在 Python 侧是 "
        "JavaObject，只能按属性调用"
    )
    print("[PASS] Java 回调使用属性调用（不再误用 callAttr）")


def test_android_sets_tls_ca_bundle():
    """Android 必须把 httpx2 的信任库指向**文件形式**的 CA 证书束。

    真机事故：`httpx2`（openai SDK 与 mcp-server-12306 都用它）默认走 truststore
    读平台信任库，而 Chaquopy 的 Python 在 Android 上读不到系统 CA，于是所有 HTTPS
    请求都报 `CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`。
    修法是让 httpx2 采用 SSL_CERT_FILE（它会优先读该变量）。
    """
    srv = (ANDROID_DIR / "app/src/main/python/server.py").read_text(encoding="utf-8")
    assert "_ensure_ca_bundle" in srv, "缺少 CA 证书束准备逻辑"
    assert "SSL_CERT_FILE" in srv, "未设置 SSL_CERT_FILE（httpx2 不会改用文件证书束）"
    # 必须校验候选是不是真实文件：Chaquopy 下 certifi 的路径可能落在 .imy 归档里
    assert "os.path.isfile" in srv, (
        "未校验候选证书束是否为真实文件 —— Chaquopy 下 certifi.where() 可能是归档内路径，"
        "直接交给 ssl.create_default_context(cafile=...) 依然会失败"
    )
    serve_body = srv.split("def serve(", 1)[1]
    assert "_ensure_ca_bundle()" in serve_body, "serve() 未调用 _ensure_ca_bundle()"
    assert serve_body.index("_ensure_ca_bundle()") < serve_body.index("import app"), (
        "必须在导入后端之前准备好信任库（导入过程本身可能就会创建 HTTP 客户端）"
    )
    print("[PASS] Android 启动时把 httpx2 信任库指向真实的 CA 证书束文件")


def test_llm_diagnostics_include_cause_chain():
    """LLM 失败日志必须能看出**根本原因**（含因果链）。

    真机排障时只看到 `APIConnectionError: Connection error.` 完全无法定位，
    真实原因（SSLCertVerificationError）被包在里面。
    """
    text = (REPO_ROOT / "backend/app/llm/client.py").read_text(encoding="utf-8")
    assert "_cause_chain" in text, "缺少因果链辅助函数"
    assert "exc_info=True" in text, "失败日志未带 exc_info（拿不到完整堆栈）"
    print("[PASS] LLM 失败日志含因果链与堆栈（能定位 SSL/DNS/超时等真实原因）")


def test_settings_row_gets_edit_callback():
    """供应商行必须通过**回调**拿到编辑入口，不能在模块级函数里引用闭包内函数。

    真机事故：`providerRow` 定义在模块作用域，却直接调用 `openEditor(entry)` ——
    而 `openEditor` 定义在 `renderSettingsPage` 内部，两者作用域不同，
    于是点「编辑」抛 `ReferenceError: openEditor is not defined`，按钮完全没反应
    （文件里定义了也没用）。这类错误静态看代码很难发现，靠页面上的错误横幅才立刻暴露。
    """
    pages = (REPO_ROOT / "frontend/src/pages.js").read_text(encoding="utf-8")
    # 模块级函数 providerRow 的签名要带 onEdit 回调
    assert "function providerRow(entry, rerender, onEdit)" in pages, (
        "providerRow 未通过参数接收编辑回调（会引用不到闭包内的 openEditor）"
    )
    assert "() => onEdit(entry)" in pages, "编辑按钮未使用传入的回调"
    # 且不能再出现对 openEditor 的直接调用（只能作为回调传递）
    direct = [ln.strip() for ln in pages.splitlines()
              if "openEditor(" in ln and "function openEditor" not in ln and "openEditor)" not in ln]
    assert not direct, f"仍存在对 openEditor 的直接调用：{direct}"
    print("[PASS] 供应商行的编辑入口通过回调传递（作用域正确）")


def test_android_uses_stable_local_port():
    """本地端口必须在重启后保持稳定。

    WebView 的 localStorage 按「源」隔离，而源包含端口。端口每次启动都变的话，
    每次启动都是一个"新用户"—— 用户的对话、供应商配置、记住的 Key 全部丢失。
    真机实测过：58213 下存的数据在 43657 下读不到。
    """
    srv = (ANDROID_DIR / "app/src/main/python/server.py").read_text(encoding="utf-8")
    assert "_stable_port" in srv, "缺少稳定端口逻辑"
    assert ".local_port" in srv, "未把上次端口记录下来，重启后无法复用"
    serve_body = srv.split("def serve(", 1)[1]
    assert "_stable_port(" in serve_body, "serve() 未使用稳定端口"
    print("[PASS] 本地端口在重启后复用（保住 WebView 的 localStorage）")


def test_frontend_api_calls_have_api_prefix():
    """前端调用后端必须带 `/api` 前缀 —— 否则静默 404。

    真机事故：设置页写的是 `api("/providers")`，而 `api()` 只做 `API_BASE + path`，
    于是请求打到 `/providers`（404）。界面只显示"服务端不可达"，看代码也看不出问题，
    只有在 CDP 的 Network 域里才看到真实 URL 少了 `/api`。
    现在 `api()` 会自动补前缀，且调用处也必须写全 —— 两层都钉住。
    """
    pages = (REPO_ROOT / "frontend/src/pages.js").read_text(encoding="utf-8")
    assert 'path.startsWith("/api/") ? path : "/api" + path' in pages, (
        "api() 未做 /api 前缀兜底：漏写前缀会静默 404"
    )
    bad = [
        ln.strip() for ln in pages.splitlines()
        if 'api("' in ln and '/api/' not in ln and not ln.strip().startswith("//")
    ]
    assert not bad, f"pages.js 存在未带 /api 前缀的调用：{bad}"
    print("[PASS] 前端 API 调用带 /api 前缀（且 api() 有兜底）")


def test_server_selfcheck_covers_entry_script():
    """启动自检必须包含入口脚本 —— 它是"白屏"类故障的唯一自动防线。"""
    srv = (ANDROID_DIR / "app/src/main/python/server.py").read_text(encoding="utf-8")
    assert '"/src/main.js"' in srv, (
        "Android 启动自检未检查 /src/main.js：静态资源 404 不会触发 WebView 报错，"
        "只能靠这个自检发现"
    )
    assert "_self_check" in srv and "onStartupFailed" in srv, "自检结论未回传给界面"
    print("[PASS] 启动自检覆盖首页与入口脚本，失败时回传可读结论")


def test_frontend_reports_boot_errors():
    """前端必须把"模块加载失败/抛错"显示在页面上（真机拿不到控制台）。"""
    html = (REPO_ROOT / "frontend/index.html").read_text(encoding="utf-8")
    assert 'id="boot-error"' in html, "index.html 缺少错误横幅容器"
    assert "unhandledrejection" in html and "addEventListener(\"error\"" in html, (
        "缺少全局错误捕获：模块脚本 404 或抛错时页面会静默变成白屏"
    )
    # 引导条（未配置模型时）
    assert 'id="llm-notice"' in html, "index.html 缺少未配置模型的引导条"
    main_js = (REPO_ROOT / "frontend/src/main.js").read_text(encoding="utf-8")
    assert "renderLlmNotice" in main_js, "main.js 未实现引导逻辑"
    assert "llm_ready" in main_js, "引导逻辑未依据服务端的 llm_ready"
    print("[PASS] 前端错误可见化 + 未配置模型的引导条已接线")


def test_apk_contains_build_stamp():
    """APK 里要带上"这是哪一次构建"的标记，并显示在设置 → 关于。

    为什么需要：Android 上静态资源的 URL 跨安装不变，出问题后只能靠"重新下载"，
    而用户无从判断重装到底生效了没有 —— 反复出现"我装了但界面没变"的扯皮。
    有了这一行就能直接对照："你那儿显示的是 X，我说的是 Y"。
    """
    gradle = (ANDROID_DIR / "app/build.gradle.kts").read_text(encoding="utf-8")
    assert '"build.json"' in gradle, "打包时没有写入 build.json（构建标记）"
    assert "gitShortHead" in gradle, "构建标记里没有提交号，无法定位到具体代码"
    assert "-dirty" in gradle, (
        "工作区有未提交改动时没有标出来：标记会说成是某次干净提交的产物，实际上不是"
    )
    pages = (REPO_ROOT / "frontend/src/pages.js").read_text(encoding="utf-8")
    assert 'kv("构建"' in pages, "「关于」卡片没有显示构建标记"
    assert 'fetch("build.json"' in pages, "没有去读 build.json"

    import zipfile

    apks = sorted((ANDROID_DIR / "app/build/outputs/apk").rglob("*.apk"))
    if not apks:
        print("[SKIP] 未找到已构建的 APK，跳过构建标记的产物检查")
        return
    # 逐个检查**所有**已构建的 APK：debug 与 release 都要有，不能只让其中一个带上
    # （只取 sorted()[-1] 会挑到 release 或 debug，取决于路径排序，容易漏掉另一个。
    #  实测第一次跑就抓到了"release 包是加标记之前构建的"这种情况。）
    stamps = []
    for apk in apks:
        with zipfile.ZipFile(apk) as z:
            names = [n for n in z.namelist() if n.endswith("assets/webapp/build.json")]
            assert names, f"{apk.name} 里没有 assets/webapp/build.json（请重新构建该产物）"
            stamps.append(f"{apk.name}: " + z.read(names[0]).decode("utf-8").strip())
    assert all("commit" in s and "builtAt" in s for s in stamps), f"构建标记内容不完整：{stamps}"
    print("[PASS] 每个已构建的 APK 都带构建标记：" + "；".join(stamps))


def test_dict_extraction_does_not_wipe_shared_data_dir():
    """解包字典时不得清空目标目录 —— 它的目标是 filesDir，与 webapp/ 共用。

    这是一次**只在 release 包上出现**的事故：`extractAssets` 为清理旧版本残留会先
    `deleteContents(targetDir)`，而字典被解包到 filesDir 本身，于是刚解包好的
    `webapp/` 被连带删掉 —— 后端启动后 `/` 与 `/src/main.js` 全 404，界面只剩
    `{"detail":"Not Found"}`。Java 侧的自检还认为"index.html：有"（它在删之前查的）。

    为什么开发时看不见：debug 包默认**不带字典**，`am.list("dict")` 为空会直接返回，
    压根走不到删除那一步。也就是说"打不打包字典"这个开关决定了程序能不能跑。
    """
    src = (ANDROID_DIR / "app/src/main/java/org/openrailfanai/app/MainActivity.java"
           ).read_text(encoding="utf-8")
    calls = dict(re.findall(
        r"extractAssets\(\s*ASSET_(\w+)\s*,\s*\w+\s*(?:,\s*(true|false))?\s*\)", src))
    assert "WEBAPP" in calls and "DICT" in calls, (
        f"没解析到 webapp/dict 两处解包调用（找到 {sorted(calls)}），检查方式可能已失效"
    )
    assert calls["DICT"] == "false", (
        "字典解包又会清空 filesDir：它的目标目录与 webapp/ 共用，"
        "清空会把刚解包好的前端一起删掉（release 包主页 404）→ 传 cleanTarget=false"
    )
    assert calls["WEBAPP"] in ("", "true"), (
        "webapp 的目标目录归它独占，仍需清空以免旧版残留污染"
    )
    assert re.search(r"if \(cleanTarget\)\s*\{\s*deleteContents\(", src), (
        "deleteContents 没有被 cleanTarget 保护住"
    )
    print("[PASS] 字典解包不清空共用的数据目录，前端不会被连带删除")


def test_android_points_dict_db_path_at_extracted_copy():
    """Android 上必须把 DICT_DB_PATH 显式指到解包出来的 dict.db。

    后端默认值是相对路径 `data/dict.db`，而 `app/data/dict.py` 把相对路径解析到
    **代码目录**（不是 CWD）——APK 里那是只读打包区，永远找不到。而 Java 侧把字典
    解包在数据目录根下。两边对不上时 `-PincludeDict=true` 打了字典也等于没打，
    且**完全静默**：用户只会看到工具回"本地字典尚未构建"，无从判断是谁的问题。
    """
    gradle = (ANDROID_DIR / "app/build.gradle.kts").read_text(encoding="utf-8")
    assert 'File(dstDir, "dict.db")' in gradle, (
        "打包字典的产物文件名变了，解包与 DICT_DB_PATH 的约定需要同步更新"
    )
    srv = (ANDROID_DIR / "app/src/main/python/server.py").read_text(encoding="utf-8")
    assert 'os.environ.setdefault("DICT_DB_PATH"' in srv, (
        "server.py 没有设置 DICT_DB_PATH：字典会被解包到数据目录，"
        "但后端仍去只读的代码目录找，等于没打包"
    )
    assert 'os.path.join(data_dir, "dict.db")' in srv, (
        "DICT_DB_PATH 必须指向 Java 解包出来的 <data_dir>/dict.db"
    )
    assert "dict_available" in srv, "启动日志没有如实报告本地字典是否可用"
    print("[PASS] DICT_DB_PATH 指向解包出来的 <data_dir>/dict.db，且启动时如实报告可用性")


def _main_java() -> str:
    return (ANDROID_DIR / "app/src/main/java/org/openrailfanai/app/MainActivity.java"
            ).read_text(encoding="utf-8")


def test_native_bridge_contract_matches_java():
    """前端用到的每个桥方法，Java 侧都必须实现，且都带 @JavascriptInterface。

    为什么需要这条：`addJavascriptInterface` 是**按方法名反射**暴露的 ——
    名字写错既不编译报错、也不抛异常，前端拿到的只是 undefined，表现为
    "这个功能点了没反应"。这类错只有装到真机上才看得出来，代价很高，
    所以在这里用静态检查钉住两边的契约。

    另外顺带钉住这个桥赖以安全的前提：外链一律交给系统浏览器，
    WebView 永远不会停在第三方页面上（否则陌生页面就能调到本桥）。
    """
    js = (REPO_ROOT / "frontend/src/native.js").read_text(encoding="utf-8")
    java = _main_java()

    used = sorted(set(re.findall(r'(?:call|has)\("(\w+)"', js)))
    assert used, "没从 native.js 解析出任何桥方法名（检查方式可能已失效）"
    missing = [n for n in used
               if not re.search(r"@JavascriptInterface\s+public\s+[\w<>\[\]]+\s+" + n + r"\s*\(", java)]
    assert not missing, (
        "前端调用了 Java 侧不存在（或没加 @JavascriptInterface）的桥方法："
        + "、".join(missing)
        + "\n→ 反射不到时不会报错，只会「点了没反应」，前端拿到的是 undefined"
    )
    assert 'addJavascriptInterface(new RailBridge(), "RailNative")' in java, (
        "WebView 没有注册原生桥：前端 window.RailNative 会是 undefined"
    )
    # 安全前提：外链必须离开 WebView，否则第三方页面也能调到这个桥
    assert "openExternally(request.getUrl().toString())" in java, (
        "外链没有交给系统浏览器 —— 一旦 WebView 内能打开第三方页面，"
        "注入的 RailNative 就能被陌生页面调用"
    )
    print(f"[PASS] 原生桥契约一致（{len(used)} 个方法），且外链离开 WebView 的安全前提仍在")


def test_byok_keys_use_android_keystore():
    """BYOK 的 Key 在 Android 上必须走系统密钥库，而不是明文落盘。

    WebView 的 localStorage / 应用私有文件对**本机其他应用**都是不可读的，
    但对"拿到设备或备份的人"不是。Key 是用户的真金白银，值得用 Keystore 加密：
    加密密钥本身永远不出密钥库，密文被拷走也解不开。
    """
    java = _main_java()
    assert '"AndroidKeyStore"' in java, "没有使用 Android Keystore"
    assert "AES/GCM/NoPadding" in java, "应使用 AES-GCM（带认证的加密，能发现密文被篡改）"
    assert "setUserAuthenticationRequired(true)" not in java, (
        "Key 不应要求每次解锁都验指纹/密码：发送消息时需要静默取用"
    )
    # 密钥库被重置（刷机/清数据）后旧密文解不开，必须如实返回空而不是崩掉设置页
    assert "密钥库已重置" in java, "缺少密钥库重置后的降级处理"
    # 明文文档里不应再出现 Key：store.js 侧已断言，这里确认落盘文件是分开的两个
    assert '"state.json"' in java and '"secrets.json"' in java, (
        "状态与密钥应分文件存放（密钥单独一个文件，便于整体删除与加密）"
    )
    print("[PASS] BYOK Key 走 Android Keystore（AES-GCM），且密钥库重置时有降级处理")


def test_native_state_write_is_atomic():
    """状态落盘必须是"写临时文件 + 改名"，不能直接覆写。

    应用被系统在写到一半时回收（移动端很常见），直接覆写会留下半个 JSON：
    下次启动解析失败，用户看到的是"我的对话全没了"。先写 .tmp、刷盘、再原子改名，
    最坏情况也只是丢掉最后一次改动。
    """
    java = _main_java()
    assert "renameTo(" in java, "状态落盘没有用 renameTo 做原子替换"
    assert 'f.getName() + ".tmp"' in java, "没有先写临时文件"
    assert "out.getFD().sync()" in java, "临时文件没有 fsync：改名成功但数据可能还在页缓存里"
    print("[PASS] 状态落盘为原子写（tmp + fsync + rename），进程被杀不会留下半个 JSON")


def test_theme_declares_no_actionbar():
    """必须显式声明 NoActionBar 主题 —— 否则顶部约 275px 会整个点不动。

    这是一次真机才会暴露、而且**症状极具欺骗性**的缺陷：清单里不声明主题时
    （targetSdk 35），平台会给一个带 ActionBar 的默认主题。这条 ActionBar 在本应用里
    看不见（没有标题、透明），但它确实存在，并以 ActionBarOverlayLayout 的叠加模式
    **盖在 WebView 之上**，吃掉顶部约 275px 的全部触摸。

    表现：界面完全正常，顶栏的「☰」和侧栏的「＋ 新对话」也画在那里，但怎么点都没反应。
    用户的原话是"创建新对话的功能怎么没了"。
    （那 275px = 挖孔安全区 128 + ActionBar 高度 147，与实测的触摸死区边界完全吻合；
    排查依据是 `dumpsys activity top` 视图层级里的 ActionBarContainer 0,0-1080,275。）

    注意：只给顶栏加 env(safe-area-inset-top) 解决不了这个问题 —— 那只解决"看起来
    被状态栏压住"的观感，不解决"摸不到"。
    """
    manifest = (ANDROID_DIR / "app/src/main/AndroidManifest.xml").read_text(encoding="utf-8")
    assert 'android:theme="@style/AppTheme"' in manifest, (
        "清单没有声明主题：平台会给带 ActionBar 的默认主题，"
        "它会在 WebView 之上吃掉顶部约 275px 的触摸（顶栏按钮点不动）"
    )
    styles = ANDROID_DIR / "app/src/main/res/values/styles.xml"
    assert styles.exists(), "缺少 res/values/styles.xml（AppTheme 定义在这里）"
    text = styles.read_text(encoding="utf-8")
    assert "NoActionBar" in text, (
        "AppTheme 必须继承 NoActionBar 主题，否则顶部触摸死区会回来"
    )
    print("[PASS] 主题显式声明为 NoActionBar，顶部触摸死区不会复现")


def test_app_canvas_is_inset_from_system_bars():
    """整块界面必须按 window insets 收进安全区，不能铺到状态栏/手势条底下。

    为什么必须做在 Java 侧、而不能再只靠 CSS 的 env(safe-area-inset-*)：
    Android 15（API 35）对 targetSdk 35 的应用**强制边到边**，内容会铺到状态栏与手势条
    底下；而 WebView 的 env(safe-area-inset-*) 在 Android 上**只反映挖孔** —— 没有挖孔的
    机器会得到 0，于是按钮又贴回状态栏（用户报的正是「＋ 新对话」贴着系统栏）。

    另外底部要把输入法 insets 一并算上：边到边模式下 `adjustResize` **不再自动压缩窗口**
    （实测键盘弹起时窗口 frame 仍是 0,0-1080,2400），底部输入框会被键盘整个盖住。
    """
    java = _main_java()
    assert "setOnApplyWindowInsetsListener" in java, (
        "根视图没有处理 window insets：边到边模式下界面会铺到状态栏/手势条底下"
    )
    assert "WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout()" in java, (
        "安全区应同时取系统栏与挖孔"
    )
    assert "WindowInsets.Type.ime()" in java, (
        "底部未计入输入法 insets：键盘弹起时输入框会被盖住"
        "（边到边下 adjustResize 不再自动压缩窗口）"
    )
    assert "Build.VERSION_CODES.R" in java, (
        "WindowInsets.Type 是 API 30+ 的接口，必须有版本判断；"
        "低版本本来就不是边到边，由 CSS 的 env() 兜底"
    )
    css = (REPO_ROOT / "frontend/index.html").read_text(encoding="utf-8")
    assert "env(safe-area-inset-top, 0px)" in css, "CSS 侧的老机型兜底被删掉了"
    print("[PASS] 界面按 window insets（系统栏+挖孔+输入法）收进安全区，CSS 兜底仍在")


def test_version_is_single_sourced():
    """版本号只能有一个来源：仓库根的 VERSION。

    背景：同一个 `0.1.1` 曾经对应过好几个**内容不同**的包，用户无法判断自己装的是哪一版，
    只能靠口头说"请重新下载"；而界面上还同时写死过 `v0.5`/`v0.6` 两个互不相干的号。
    所以这里钉住四件事：VERSION 存在且成格式、gradle 从它读、前端不写死、后端能报出来。
    """
    ver_file = REPO_ROOT / "VERSION"
    assert ver_file.exists(), "缺少仓库根的 VERSION 文件（版本号的唯一来源）"
    ver = ver_file.read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"\d+\.\d+\.\d+", ver), f"VERSION 内容不是 x.y.z 形式：{ver!r}"

    gradle = (ANDROID_DIR / "app/build.gradle.kts").read_text(encoding="utf-8")
    assert 'repoRoot.resolve("VERSION")' in gradle, "gradle 没有从 VERSION 读版本"
    assert not re.search(r'versionName\s*=\s*"', gradle), (
        "gradle 里又出现写死的 versionName —— 版本号必须来自 VERSION"
    )
    assert not re.search(r"versionCode\s*=\s*\d+", gradle), (
        "gradle 里又出现写死的 versionCode —— 应由 VERSION 推导"
    )
    assert '"version":"$appVersion"' in gradle, "build.json 里没带上版本号"

    # 扫描前先去掉注释：注释里提到历史版本号是有价值的说明，不该被判为"写死"。
    html = re.sub(r"<!--.*?-->", "", (REPO_ROOT / "frontend/index.html").read_text(encoding="utf-8"), flags=re.S)
    pages = re.sub(r"^\s*//.*$", "", (REPO_ROOT / "frontend/src/pages.js").read_text(encoding="utf-8"), flags=re.M)
    for name, blob in (("index.html", html), ("pages.js", pages)):
        bad = re.findall(r'v0\.\d+[^"\s<]*', blob)
        assert not bad, f"{name} 里又写死了版本号：{bad}（应改为运行时读取）"

    main_py = (REPO_ROOT / "backend/app/main.py").read_text(encoding="utf-8")
    assert '"/api/version"' in main_py, "后端没有 /api/version（桌面/自建部署靠它显示版本）"
    print(f"[PASS] 版本单一来源 VERSION={ver}：gradle / 前端 / 后端三处都从它取，无写死")


def test_version_bump_updates_artifact_name():
    """构建脚本要把产物按版本号命名，否则"我手上是哪个文件"又要靠嘴说。"""
    sh = (REPO_ROOT / "scripts/android/build.sh").read_text(encoding="utf-8")
    assert "dist/android/OpenRailFanAI-$APP_VERSION-arm64-$TYPE.apk" in sh, (
        "构建脚本没有按版本号命名产物"
    )
    assert "-nt " in sh and "BUILD_STARTED_AT" in sh, (
        "构建脚本没有跳过「本次未重新构建」的旧产物："
        "把旧内容的 APK 按新版本号复制过去，就成了同名不同内容"
    )
    print("[PASS] 产物按版本号命名，且只归置本次真正重建过的")


def main():
    test_android_requirements_are_pure_python()
    test_android_requirements_are_pinned()
    test_backend_requirements_keep_android_constraints()
    test_installed_versions_satisfy_android_constraints()
    test_compat_shims_are_functional()
    test_pydantic_settings_shim_translates_config()
    test_compat_does_not_override_real_packages()
    test_android_project_wiring()
    test_frontend_dir_setting_is_honoured()
    test_env_example_covers_frontend_dir()
    test_mcp_package_kept_out_of_normal_resolution()
    test_shell_scripts_avoid_cjk_variable_capture()
    test_apk_assets_satisfy_index_html_references()
    test_dict_extraction_does_not_wipe_shared_data_dir()
    test_android_points_dict_db_path_at_extracted_copy()
    test_native_bridge_contract_matches_java()
    test_byok_keys_use_android_keystore()
    test_native_state_write_is_atomic()
    test_theme_declares_no_actionbar()
    test_apk_contains_build_stamp()
    test_version_is_single_sourced()
    test_version_bump_updates_artifact_name()
    test_app_canvas_is_inset_from_system_bars()
    test_python_callbacks_use_attribute_access()
    test_android_sets_tls_ca_bundle()
    test_llm_diagnostics_include_cause_chain()
    test_settings_row_gets_edit_callback()
    test_android_uses_stable_local_port()
    test_frontend_api_calls_have_api_prefix()
    test_server_selfcheck_covers_entry_script()
    test_frontend_reports_boot_errors()
    print("\nAndroid 一体化约束测试全部通过 ✔")


if __name__ == "__main__":
    main()
