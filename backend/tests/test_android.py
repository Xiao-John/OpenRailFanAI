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
    print("\nAndroid 一体化约束测试全部通过 ✔")


if __name__ == "__main__":
    main()
