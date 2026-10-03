"""设备端本地推理的**无网络**回归测试。

钉住三件容易静默失效的事：
  1. **不碰全局 SSRF 开关** —— 本地推理注册成"配置级"供应商（source != "request"），
     所以 `guard_request_base_url` 直接早返回。若哪天有人改成让前端下发 base_url，
     这个测试会立刻失败：那条路要打开 `LLM_ALLOW_PRIVATE_BASE_URL` 才能用。
  2. **下载地址不可由请求指定** —— 否则这就是一条现成的 SSRF + 任意大文件落盘通道。
  3. **模型查找的双位置兜底** —— 外部目录被 shell 建过而属主不对时（实测踩过），
     内部目录必须能兜住，否则表现为"文件明明在那儿却说没找到"。

运行：cd backend && PYTHONPATH=. .venv/bin/python tests/test_local_inference.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient

from app import local_inference as li

REPO_ROOT = Path(__file__).resolve().parents[2]
os.environ["APP_VARIANT"] = "lm"
from app.llm import providers as providers_mod


@contextmanager
def _env(**kw):
    """临时设环境变量并**还原**（模块级状态在进程内是共享的，测试之间会互相污染）。"""
    old = {k: os.environ.get(k) for k in kw}
    for k, v in kw.items():
        # 传 None 表示"把它从环境里摘掉"，**不能**写成 str(None)="None"
        # —— 那会变成一个名为 "None" 的相对目录，测试会莫名其妙地"通过"
        # （第一版就踩了：candidate_dirs() 拿到 "None" 并把它建了出来）。
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = str(v)
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@contextmanager
def _dirs(models_dir: str, data_dir: str):
    old = (li._models_dir, li._data_dir, li._native_lib_dir)
    li._models_dir, li._data_dir, li._native_lib_dir = models_dir, data_dir, ""
    try:
        yield
    finally:
        li._models_dir, li._data_dir, li._native_lib_dir = old


@contextmanager
def _no_spawn():
    """把 `start()` 换成桩，避免单测真的去拉起 llama-server。

    为什么必须：删掉"当前使用"的模型会触发"自动切到下一个并重启"，
    而测试里的 .gguf 是几十字节的假文件 —— 真拉起来只会让一个进程在后台
    加载一个必然失败的文件，测试输出里还会混进一行"启动本地推理："，
    看起来像测试真的起了服务。
    """
    old = li.start
    calls: list[dict] = []
    li.start = lambda **kw: (calls.append(kw), dict(li._status))[1]  # type: ignore[assignment]
    try:
        yield calls
    finally:
        li.start = old               # type: ignore[assignment]


@contextmanager
def _status(**kw):
    old = dict(li._status)
    li._status.update(kw)
    try:
        yield
    finally:
        li._status.clear()
        li._status.update(old)


def test_choices_are_fixed_and_have_real_sizes():
    """可下载目录必须服务端固定，且带**实测**体积（前端要拿它算进度与空间）。"""
    assert li.MODEL_CHOICES, "没有可下载的模型目录"
    for c in li.MODEL_CHOICES:
        for key in ("id", "label", "note", "size_mb", "gguf_name", "urls"):
            assert key in c, f"模型条目缺字段 {key}：{c}"
        assert c["gguf_name"].endswith(".gguf"), f"不是 GGUF：{c['gguf_name']}"
        assert c["size_mb"] > 0, f"{c['id']} 的 size_mb 必须为正（前端靠它显示进度）"
        assert all(u.startswith("https://") for u in c["urls"]), f"{c['id']} 有非 https 下载源"
    print(f"[PASS] 可下载模型目录固定为服务端常量（{len(li.MODEL_CHOICES)} 档，均带实测体积）")


def test_download_rejects_unknown_choice_and_arbitrary_url():
    """端点只接受目录里的 id —— 不接受请求体里的 URL。"""
    with tempfile.TemporaryDirectory() as tmp, _env(LOCAL_MODEL_DIR=tmp):
        client = TestClient(_app())
        r = client.post("/api/local-model/download", json={"choice": "not-a-model"})
        assert r.status_code == 400, f"未知模型应 400，实际 {r.status_code}"
        assert "未知的模型" in r.json()["detail"]

        # 关键：没有任何字段能让请求把下载指到别处
        import inspect

        from app.api import local_model as api_mod

        src = inspect.getsource(api_mod)
        assert "url" not in api_mod.DownloadRequest.__fields__, (
            "DownloadRequest 出现了 url 字段 —— 那就是一条 SSRF + 任意大文件落盘通道"
        )
        assert "MODEL_CHOICES" in src, "下载源不再取自服务端常量"
    print("[PASS] 下载只接受固定目录里的 id：不接受请求体 URL（无 SSRF 面）")


def test_device_provider_is_config_level_not_request_level():
    """本地推理必须以**配置级**供应商出现，否则会被 SSRF 守卫拦下。

    这是整个"一键启用"能不能成立的关键：如果按请求级下 base_url，
    生产环境（APP_ENV=production）下 `guard_request_base_url` 会 400 ——
    除非把 `LLM_ALLOW_PRIVATE_BASE_URL` 这个**全局**口子打开，
    为一个已知的固定本机端口开全局后门，代价远大于收益。
    """
    p = providers_mod.provider_from_dict(li.PROVIDER_ID, {
        "label": li.PROVIDER_LABEL, "base_url": "http://127.0.0.1:8081/v1",
        "api_key": "local", "model": "m",
    }, source="config")
    assert p.source != "request", "设备端供应商被标成了请求级"
    # 生产环境下也必须放行
    providers_mod.guard_request_base_url(p, settings=_FakeSettings(production=True))

    # 反向对照：请求级 + 内网地址必须被拒（守卫本身没坏）
    req = providers_mod.provider_from_dict("x", {
        "base_url": "http://127.0.0.1:8081/v1", "model": "m",
    }, source="request")
    try:
        providers_mod.guard_request_base_url(req, settings=_FakeSettings(production=True))
    except ValueError:
        pass
    else:
        raise AssertionError("请求级内网地址没被拦下 —— SSRF 守卫失效了")
    print("[PASS] 设备端供应商是配置级（生产环境也放行），请求级内网地址仍被拦下")


class _FakeSettings:
    """只提供被测路径真正读到的字段（`load_providers` 会读那几个供应商来源）。"""

    def __init__(self, production: bool):
        self.llm_allow_private_base_url = False
        self.is_production = production
        self.llm_providers_file = ""
        self.llm_providers = ""
        self.llm_api_key = ""
        self.llm_model = ""
        self.llm_structured_model = ""
        self.llm_provider = ""


def test_provider_item_only_when_ready():
    """未就绪时不得把设备端供应商塞进列表（否则用户选中一个连不上的地址）。"""
    with _status(state="idle", base_url=None, model=None):
        assert li.provider_item() is None, "idle 时不该注册供应商"
    with _status(state="unavailable", base_url=None):
        assert li.provider_item() is None
    with _status(state="starting", base_url="http://127.0.0.1:8081/v1", model="m.gguf"):
        item = li.provider_item()
        assert item and item[0] == li.PROVIDER_ID, "starting 时就该能被选中（页面先打开再等就绪）"
        assert item[1]["no_think_body"] == {"reasoning_effort": "none"}, (
            "本地推理必须按供应商关思考：llama-server 只认 reasoning_effort"
        )
        # 超时必须按供应商放宽：设备端实测 6096 token prefill 要 156s，
        # 用云端的全局默认 60s 会 100% 超时并静默降级成规则排版。
        assert item[1]["timeout_s"] >= 300, (
            f"本地推理的超时太短（{item[1]['timeout_s']}s）—— 设备端 prefill 就要上百秒"
        )
        # 真正生效的路径：构造出的 Provider 必须带上它
        p = providers_mod.provider_from_dict(item[0], item[1], source="config")
        assert p.timeout_s == item[1]["timeout_s"], "timeout_s 没被 provider_from_dict 接住"

        # 更关键的一条：**生成路径也必须被强制关思考**。
        # 全局开关在进程启动时就冻结了，而设备端模型是运行期才装上的 ——
        # 只靠 LLM_GENERATION_NO_THINK 的话，"下载完自动启用"这条路会带着思考跑，
        # 实测结果是思考吃光预算、正文一个字都没有。
        assert p.always_no_think is True, "设备端供应商没有强制关思考"
        from app.llm import client as llm_client

        kw = llm_client._kwargs_for(
            "chat_completions", p, messages=[{"role": "user", "content": "x"}],
            model="m", temperature=0.7, max_tokens=100, json_mode=False,
            no_think=False,            # 故意让调用方说"不要关" —— 供应商级仍应压过它
            dropped=set(),
        )
        assert kw.get("extra_body") == {"reasoning_effort": "none"}, (
            f"生成路径没被强制关思考，extra_body={kw.get('extra_body')} —— 本地生成会返回空正文"
        )
        # 字段级合并（LLM_PROVIDERS 覆盖）时不能把它丢掉
        merged = providers_mod._provider_to_dict(p)
        assert merged.get("timeout_s"), "_provider_to_dict 丢了 timeout_s（覆盖合并会重置它）"
    with _status(state="idle", base_url=None):
        providers = providers_mod.load_providers(_FakeSettings(production=False))
        assert li.PROVIDER_ID not in providers, "未就绪却出现在供应商列表里"
    print("[PASS] 设备端供应商只在 starting/ready 时注册；关思考字段按供应商下发")


def test_model_lookup_prefers_external_then_internal():
    """外部目录优先，内部目录兜底 —— 兜底那条是实测踩出来的。"""
    with tempfile.TemporaryDirectory() as ext, tempfile.TemporaryDirectory() as inner:
        with _env(LOCAL_MODEL_DIR=None), _dirs(ext, inner):
            assert li.find_model() is None, "两个目录都空时应返回 None"
            # 只有内部目录有
            Path(inner, "models").mkdir()
            (Path(inner, "models", "a.gguf").write_bytes(b"x"))
            assert li.find_model().endswith("a.gguf"), "内部目录兜底失效"
            # 外部也有时应优先外部
            Path(ext, "b.gguf").write_bytes(b"x")
            assert li.find_model().endswith("b.gguf"), "外部目录应优先"
    # 只设 LOCAL_MODEL_DIR、没跑过 setup() 的场景（**下载流程正是它**）：
    # 早期 find_model 只认 _models_dir/_data_dir，于是"刚下完却报没找到模型"。实测踩到。
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "c.gguf").write_bytes(b"x")
        with _env(LOCAL_MODEL_DIR=tmp):
            old_dirs = (li._models_dir, li._data_dir)
            li._models_dir, li._data_dir = "", ""
            try:
                found = li.find_model()
            finally:
                li._models_dir, li._data_dir = old_dirs
            assert found and found.endswith("c.gguf"), (
                "只设了 LOCAL_MODEL_DIR 时找不到模型 —— 下载后自动启用会因此失败"
            )
    print("[PASS] 模型查找：外部目录优先 → 内部 filesDir 兜底 → LOCAL_MODEL_DIR 兜底")


def test_writable_dir_skips_unwritable_candidate():
    """外部目录写不进去时必须回落到下一个候选 —— 实测这个坑咬过两次。

    场景：`/sdcard/Android/data/<pkg>/files/models` 若被 `adb shell mkdir` 建过，
    属主是 shell，App 一律 Permission denied。第一次表现为"文件在那儿却说没找到模型"，
    第二次表现为"下载跑到写文件才失败"。所以必须**探测**而不是**假定**。
    """
    with tempfile.TemporaryDirectory() as tmp:
        ext = os.path.join(tmp, "ext")          # 造一个只读目录冒充"不可写的外部目录"
        os.makedirs(ext)
        os.chmod(ext, 0o500)
        inner = os.path.join(tmp, "inner")
        try:
            with _env(LOCAL_MODEL_DIR=None), _dirs(ext, inner):
                li._writable_cache = ""
                got = li.writable_dir()
                assert got == os.path.join(inner, "models"), (
                    f"外部目录不可写时没有回落到内部目录，实际选了 {got}"
                )
                assert li.models_dir() == got, "models_dir() 与 writable_dir() 不一致"
        finally:
            os.chmod(ext, 0o700)
    print("[PASS] 可写目录是探测出来的：外部不可写 → 回落内部 filesDir")


def test_echoed_base_url_does_not_downgrade_provider():
    """客户端把**同一个** base_url 原样回传，不该把配置级供应商降级成请求级。

    实测事故：前端选中预设时会同时下发 id 与 base_url（为了支持中转站覆盖地址），
    于是设备端本地模型的地址被回传 → 服务端判成 `source="request"`
    → 生产环境 SSRF 守卫拒绝 127.0.0.1 → 生成整条降级到规则排版。
    用户看到的是"模型明明就绪、问了却答得像没接上模型"，而原因只在日志里留一行。

    真正的地址覆盖（中转站）仍必须按请求级处理并过守卫 —— 那是这条规则的另一半，
    少了它就是把 SSRF 口子重新打开。
    """
    import app.llm.providers as pm

    dev = pm.provider_from_dict(
        "ondevice",
        {"base_url": "http://127.0.0.1:8081/v1", "model": "m", "api_key": "local"},
        source="config",
    )
    orig = pm.load_providers
    pm.load_providers = lambda settings=None: {"ondevice": dev}
    prod = _FakeSettings(production=True)
    try:
        # 1) 原样回传 → 仍是配置级，生产环境放行
        p = pm.resolve_provider("ondevice", {"base_url": "http://127.0.0.1:8081/v1",
                                             "model": "m", "api_key": "local"})
        assert p.source == "config", f"回传相同地址被降级成了 {p.source}"
        pm.guard_request_base_url(p, settings=prod)      # 不该抛
        # 末尾斜杠差异也算同一个地址
        p_slash = pm.resolve_provider("ondevice", {"base_url": "http://127.0.0.1:8081/v1/"})
        assert p_slash.source == "config", "末尾斜杠差异被当成地址覆盖了"

        # 2) 真的换地址 → 请求级，且生产环境必须被拦
        p2 = pm.resolve_provider("ondevice", {"base_url": "http://10.0.0.5:9000/v1"})
        assert p2.source == "request", "换了地址却没转成请求级 —— SSRF 口子被打开了"
        try:
            pm.guard_request_base_url(p2, settings=prod)
        except ValueError:
            pass
        else:
            raise AssertionError("请求级内网地址没被拦下")
    finally:
        pm.load_providers = orig
    print("[PASS] 回传相同 base_url 不降级（生产放行）；真改地址仍按请求级拦截")


def test_frontend_does_not_send_local_base_url():
    """前端给设备端模型条目存的字段里**不能有 base_url，也不能有 model**。"""
    js = (REPO_ROOT / "android" / "lm-webapp" / "src" / "pages.js").read_text(encoding="utf-8")
    blk = js.split("本地模型（一键下载并启用）", 1)[1].split("function localModelCard", 1)[1]
    blk = blk.split("store.upsertLlmEntry", 1)[1].split("});", 1)[0]
    assert "base_url" not in blk, (
        "设备端模型的条目里带了 base_url —— 前端 llmSpec() 会把它下发，"
        "服务端会降级成请求级并撞 SSRF 守卫（实测事故）"
    )
    # `model` 与 base_url **是同一个病根的第二次发作**：第一版漏删了它，
    # 而它会随着用户在设置页换档（2B↔4B）而过期 —— 换完之后请求里带的是旧模型名。
    assert "model" not in blk, (
        "设备端模型的条目里带了 model —— 换档之后它会过期，"
        "请求会带着旧模型名打到新模型上（与 SSRF 事故同源）"
    )
    main_js = (REPO_ROOT / "android" / "lm-webapp" / "src" / "main.js").read_text(encoding="utf-8")
    assert '"ondevice"' in main_js, "ondevice 不在免 Key 名单里 —— 界面会一直显示未配置"
    # 双保险：即使本机存着旧版本写下的 model，llmSpec() 也**不许**下发它。
    assert "SERVER_OWNED_PROVIDERS" in main_js and "ondevice" in main_js, (
        "llmSpec() 缺少『服务端自有供应商』的黑名单 —— 旧数据里存的 model 又会被回传"
    )
    print("[PASS] 前端设备端条目不带 base_url / model，且 llmSpec 对服务端自有的供应商不下发覆盖")


def test_download_validation_catches_truncation_and_bad_magic():
    """落盘校验必须同时挡住"少给了字节"和"内容不对"。

    实测教训：GB 级下载在移动网络上会中途超时，只按大小判断是不够的 ——
    续传拼接错位时大小可能正好对，但开头不是 GGUF。
    """
    with tempfile.TemporaryDirectory() as tmp:
        good = os.path.join(tmp, "good.gguf")
        with open(good, "wb") as f:
            f.write(b"GGUF" + b"\0" * (1024 * 1024 - 4))
        li._validate_downloaded(good, 1)          # 不该抛

        short = os.path.join(tmp, "short.gguf")
        with open(short, "wb") as f:
            f.write(b"GGUF" + b"\0" * 100)       # 只有 104 字节
        try:
            li._validate_downloaded(short, 1)
        except RuntimeError as e:
            assert "不完整" in str(e), f"截断的报错不清晰：{e}"
        else:
            raise AssertionError("截断的下载没被拦下")

        bad = os.path.join(tmp, "bad.gguf")
        with open(bad, "wb") as f:
            f.write(b"<htm" + b"\0" * (1024 * 1024 - 4))   # 大小够，但开头不是 GGUF
        try:
            li._validate_downloaded(bad, 1)
        except RuntimeError as e:
            assert "不是 GGUF" in str(e), f"坏魔数的报错不清晰：{e}"
        else:
            raise AssertionError("大小对但内容不对的下载没被拦下")
    print("[PASS] 下载校验：截断（大小）与坏内容（魔法数）都被拦下")


def test_delete_only_touches_gguf():
    """删除只动模型目录下的 *.gguf，不能误删同目录的其它东西。"""
    with tempfile.TemporaryDirectory() as ext, tempfile.TemporaryDirectory() as inner:
        with _dirs(ext, inner):
            keep = Path(ext, "llama-server.log")
            keep.write_bytes(b"log")
            (Path(ext, "m.gguf")).write_bytes(b"x")
            (Path(ext, "m.gguf.part")).write_bytes(b"x")
            li.delete_model(all_models=True)
            assert not list(Path(ext).glob("*.gguf")), "gguf 没被删掉"
            assert not list(Path(ext).glob("*.gguf.part")), "半截文件没被清掉"
            assert keep.exists(), "删模型把同目录的日志文件也删了"
    print("[PASS] 删除模型只动 *.gguf / *.gguf.part，不碰同目录其它文件")


def test_delete_requires_explicit_scope():
    """**不给名字也不给 all_models 必须被拒绝**。

    多档共存之后，"无参数 = 删光"是一条会顺手清掉用户几个 GB 的默认行为，
    最坏的情况是某个调用方只想删一个却漏传了参数 —— 那时用户看到的
    是"我删了 2B，4B 也没了"。所以这条默认必须是不做事并报错。
    """
    with tempfile.TemporaryDirectory() as ext, tempfile.TemporaryDirectory() as inner:
        with _dirs(ext, inner):
            (Path(ext, "a.gguf")).write_bytes(b"x")
            try:
                li.delete_model()
            except ValueError as e:
                assert "全删" in str(e), f"拒绝的理由不清楚：{e}"
            else:
                raise AssertionError("无参调用 delete_model() 竟然被允许了（会误删全部模型）")
            assert Path(ext, "a.gguf").exists(), "被拒绝的调用居然动了文件"
    print("[PASS] delete_model() 无参调用被拒且不动任何文件")


def test_delete_one_keeps_the_other():
    """只删指定的那一个，**不碰**另一个模型。"""
    with tempfile.TemporaryDirectory() as ext, tempfile.TemporaryDirectory() as inner:
        with _dirs(ext, inner), _no_spawn():
            (Path(ext, "Qwen3.5-2B-Q4_K_M.gguf")).write_bytes(b"x" * 10)
            (Path(ext, "Qwen3.5-4B-Q4_K_M.gguf")).write_bytes(b"x" * 20)

            names = [m["name"] for m in li.list_models()]
            assert "Qwen3.5-4B-Q4_K_M.gguf" in names, f"列举没看到 4B：{names}"

            li.delete_model("Qwen3.5-2B-Q4_K_M.gguf")
            assert not Path(ext, "Qwen3.5-2B-Q4_K_M.gguf").exists(), "该删的没删掉"
            assert Path(ext, "Qwen3.5-4B-Q4_K_M.gguf").exists(), "删 2B 把 4B 也删了"
    print("[PASS] 按名字删除只影响目标模型，另一个模型完好")


def test_select_model_persists_and_drives_find_model():
    """选中一个模型后：`find_model()` 听它的，而且**重启进程也还在**。

    这两条缺一不可 —— 只落内存的话，App 被系统回收重启后又回到"字母序第一个"，
    用户看到的是"我明明选过 4B，怎么又是 2B"，而且会被当成随机行为。
    """
    with tempfile.TemporaryDirectory() as ext, tempfile.TemporaryDirectory() as inner:
        with _dirs(ext, inner):
            (Path(ext, "Qwen3.5-2B-Q4_K_M.gguf")).write_bytes(b"x" * 10)
            (Path(ext, "Qwen3.5-4B-Q4_K_M.gguf")).write_bytes(b"x" * 20)

            # 没选过 → 字母序第一个（**向后兼容**，与加入多档之前完全一致）
            assert li.find_model().endswith("Qwen3.5-2B-Q4_K_M.gguf"), \
                "没选过时的默认行为变了（应为字母序第一个）"

            li._select_save("Qwen3.5-4B-Q4_K_M.gguf")
            assert li.selected_model() == "Qwen3.5-4B-Q4_K_M.gguf"
            assert li.find_model().endswith("Qwen3.5-4B-Q4_K_M.gguf"), "选中没生效"

            # 模拟进程重启：把内存里的覆盖清掉，只留盘上的文件
            li._tune_override.clear()
            assert li.find_model().endswith("Qwen3.5-4B-Q4_K_M.gguf"), \
                "选中的模型没有落盘（重启后会退回字母序第一个）"

            # 选中的文件被删掉了 → 必须优雅回退，而不是返回一个不存在的路径
            os.remove(Path(ext, "Qwen3.5-4B-Q4_K_M.gguf"))
            assert li.selected_model() is None, "选中的文件已不存在，selected_model() 仍报它"
            assert li.find_model().endswith("Qwen3.5-2B-Q4_K_M.gguf"), \
                "选中的文件消失后没有回退到剩下的模型"
    print("[PASS] 选中模型会落盘并驱动 find_model()；文件消失后能优雅回退")


def test_model_name_is_a_security_boundary():
    """名字里的路径必须被拒绝 —— 否则删模型就是个任意文件删除漏洞。"""
    for bad in ("../evil.gguf", "/tmp/evil.gguf", "sub/evil.gguf", "", "noext", "a.gguf/../b"):
        try:
            li._safe_model_name(bad)
        except ValueError:
            continue
        raise AssertionError(f"非法模型名被接受了：{bad!r}")
    assert li._safe_model_name("Qwen3.5-2B-Q4_K_M.gguf") == "Qwen3.5-2B-Q4_K_M.gguf"
    print("[PASS] 模型名收成纯文件名：路径穿越一律拒绝")


def test_select_unknown_name_does_nothing():
    """切到一个不在列表里的名字 → 报错，且**不得**删改任何文件。"""
    with tempfile.TemporaryDirectory() as ext, tempfile.TemporaryDirectory() as inner:
        with _dirs(ext, inner):
            (Path(ext, "a.gguf")).write_bytes(b"x")
            try:
                li.select_model("evil.gguf")
            except ValueError as e:
                assert "不在已下载列表" in str(e), f"报错不清楚：{e}"
            else:
                raise AssertionError("切到一个不存在的模型竟然成功了")
            assert Path(ext, "a.gguf").exists(), "失败的切换动了文件"
    print("[PASS] select 未知模型被拒且不改动文件系统")


def test_status_survives_no_binary_and_no_dir():
    """没二进制、目录不存在时，status() 也要给出一份可渲染的答案（不能抛）。"""
    with tempfile.TemporaryDirectory() as tmp:
        with _env(LOCAL_LLAMA_BINARY=None), _dirs(os.path.join(tmp, "nope"), os.path.join(tmp, "nope2")):
            old = li._native_lib_dir
            li._native_lib_dir = ""
            import shutil as _sh

            saved = _sh.which
            _sh.which = lambda *a, **kw: None      # 假装 PATH 上也没有 llama-server
            try:
                st = li.status()
            finally:
                _sh.which = saved
                li._native_lib_dir = old
            for key in ("state", "binary_present", "choices", "download", "models_dir", "free_mb",
                        "active_model", "uptime_s", "models"):
                assert key in st, f"status() 缺字段 {key}"
            assert st["binary_present"] is False
            # 每个可选档位都要带 gguf_name：前端靠它把"已下载的档位"从下载列表里剔掉，
            # 否则用户会为同一个文件重复下载一遍。
            for c in st["choices"]:
                assert c.get("gguf_name", "").endswith(".gguf"), \
                    f"choices 缺 gguf_name，前端无法判断该档是否已下载：{c}"
            assert all(m["name"].endswith(".gguf") for m in st["models"])
    print("[PASS] 无二进制/无目录时 status() 仍返回可渲染结构（不抛异常，含多档字段）")


def test_npu_probe_is_optional_and_never_throws():
    """NPU 探针是**增强项**：本包没有它时也只能返回一句可读的说明，不能抛。

    为什么钉这条：探针只在 lm 轨的包里才有，而 `diagnostics()` 会无条件调用它。
    要是它因为找不到文件就抛异常，整个「导出诊断」——也就是排障时唯一的出路——
    会跟着一起坏掉。**排障工具本身不能成为新的故障点。**
    """
    with tempfile.TemporaryDirectory() as ext, tempfile.TemporaryDirectory() as inner:
        with _dirs(ext, inner):
            assert li.probe_binary() is None, "测试目录里不该有探针"
            txt = li.probe_npu()
            assert "不含 NPU 探针" in txt, f"缺探针时的说明不可读：{txt}"
            # diagnostics() 必须照样能跑完（含探针那一节）
            d = li.diagnostics()
            assert "NPU 可行性探测" in d, "诊断里少了 NPU 探针一节"
            assert "libnpuprobe" in d, "诊断里没写出探针路径"

            # 有一个可执行的探针时，要走 subprocess 拿它的真实输出（这里用 shell 脚本代替）。
            # 走 LOCAL_NPU_PROBE 环境变量指定 —— 探针在**设备上**是放在 nativeLibraryDir 的，
            # 桌面跑测试时用这个变量指过去（与 LOCAL_LLAMA_BINARY 是同一个套路）。
            fake = Path(ext, li.NPU_PROBE_NAME)
            fake.write_text("#!/bin/sh\necho PROBE_OK\n")
            fake.chmod(0o755)
            with _env(LOCAL_NPU_PROBE=str(fake)):
                assert li.probe_binary() == str(fake), "环境变量指定的探针没被认出来"
                assert "PROBE_OK" in li.probe_npu(), "探针输出没被原样带回来"
    print("[PASS] NPU 探针缺失时只是一句说明（不抛），存在时原样带回它的输出")


def test_sibling_dirs_are_readable_but_never_writable():
    """兄弟包的模型目录：**能认出来**，但**绝不作为下载目标**。

    这是"内部调试轨用独立包名"能成立的前提 —— 调试版与封测版是两个应用，
    看不到对方 Android/data 下的模型，而一份 2B/4B 是 1.2–2.6 GB。
    但反过来，绝不能把下载落进别人的目录里（拿到「所有文件访问权限」时确实写得进去），
    那会把两个应用的数据搅在一起。两条一起钉住。
    """
    with tempfile.TemporaryDirectory() as mine, tempfile.TemporaryDirectory() as other:
        other_models = os.path.join(other, "models")
        os.makedirs(other_models)
        Path(other_models, "Qwen3.5-4B-Q4_K_M.gguf").write_bytes(b"x" * 32)

        with _dirs(mine, tempfile.mkdtemp()):
            old = li._sibling_dirs
            li._sibling_dirs = [other_models]
            try:
                # 1) 认得出
                assert other_models in li.readable_dirs(), "兄弟目录没进可读列表"
                assert other_models not in li.candidate_dirs(), "兄弟目录混进了可写列表"
                found = li.find_model()
                assert found and found.startswith(other_models), \
                    f"没能认出兄弟包里的模型：{found}"
                names = [m["name"] for m in li.list_models()]
                assert "Qwen3.5-4B-Q4_K_M.gguf" in names, f"列举里没有兄弟包的模型：{names}"

                # 2) 但**下载目标**必须还是自己的目录
                w = li.writable_dir()
                assert not w.startswith(other), \
                    f"下载目标落到了别人的目录里：{w}（应当只用自己的目录）"
            finally:
                li._sibling_dirs = old
    print("[PASS] 兄弟包的模型可原地认出来，但绝不作为下载目标")


def test_internal_permission_is_injected_not_declared():
    """「所有文件访问权限」必须是**构建时注入**，源 manifest 里不能有。

    为什么用这种别扭的做法：三条更"正统"的路都实测排除过（见 build.gradle.kts 末尾的长注释）——
    `manifest.srcFile()` 是替换不是追加（会产出没有桌面入口的包）、占位符不作用于 tools: 属性、
    product flavor 会让 assembleRelease 这个任务名消失。
    注入的好处是**正式轨与封测轨从构造上就碰不到它**（不是"应该不会"）。
    这里做静态护栏：源 manifest 保持干净 + 注入逻辑确实存在且被 internalDebug 包着。
    """
    mf = (REPO_ROOT / "android" / "app" / "src" / "main" / "AndroidManifest.xml").read_text(
        encoding="utf-8")
    assert "MANAGE_EXTERNAL_STORAGE" not in mf, (
        "源 manifest 里直接声明了 MANAGE_EXTERNAL_STORAGE —— 正式包会跟着多一条"
        "「从未申请过」的特殊权限。它必须由 build.gradle.kts 在 internal 构建时注入。"
    )
    gradle = (REPO_ROOT / "android" / "app" / "build.gradle.kts").read_text(encoding="utf-8")
    assert "MANAGE_EXTERNAL_STORAGE" in gradle, "注入逻辑没了 —— 内部调试轨会拿不到文件访问权限"
    assert "processReleaseMainManifest" in gradle and "internalDebug" in gradle, \
        "注入逻辑没有挂在 internalDebug 上，有污染正式包的风险"
    print("[PASS] MANAGE_EXTERNAL_STORAGE 只由 internal 构建注入，源 manifest 干净")


def _app():
    from app.main import app

    return app


def main() -> None:
    test_choices_are_fixed_and_have_real_sizes()
    test_download_rejects_unknown_choice_and_arbitrary_url()
    test_device_provider_is_config_level_not_request_level()
    test_provider_item_only_when_ready()
    test_model_lookup_prefers_external_then_internal()
    test_writable_dir_skips_unwritable_candidate()
    test_echoed_base_url_does_not_downgrade_provider()
    test_frontend_does_not_send_local_base_url()
    test_download_validation_catches_truncation_and_bad_magic()
    test_delete_only_touches_gguf()
    test_delete_requires_explicit_scope()
    test_delete_one_keeps_the_other()
    test_select_model_persists_and_drives_find_model()
    test_model_name_is_a_security_boundary()
    test_select_unknown_name_does_nothing()
    test_status_survives_no_binary_and_no_dir()
    test_npu_probe_is_optional_and_never_throws()
    test_sibling_dirs_are_readable_but_never_writable()
    test_internal_permission_is_injected_not_declared()
    print("\n设备端本地推理测试全部通过 ✔")


if __name__ == "__main__":
    sys.exit(main())
