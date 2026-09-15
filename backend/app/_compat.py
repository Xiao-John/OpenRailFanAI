"""Android 专用兼容层：为两个"在 Android 上装不出来、但我们其实用不到"的依赖提供替身。

背景（为什么需要这一层）
    Android 上没有 Rust 扩展的预编译轮子，因此这些包无法安装：
      - `jiter`            —— openai SDK 的依赖，用于**流式助手的 `.parsed`**
      - `pydantic-settings` —— mcp-server-12306 的 `utils/config.py` 用它读 4 个环境变量
    而这两个包提供的功能，本项目的代码路径**一个都没用到**：
      - 我们用 `client.chat.completions.create(stream=True)` 直接迭代原始 chunk，
        从不使用 `.stream()` 助手的 `.parsed`（openai 只有两处调用 from_json，
        都由 `is_given(self._rich_response_format)` / tool_call `strict` 保护）。
      - mcp_12306 只在模块级取一次 `settings.log_level` 配日志级别。

设计约束（重要）
    1. **只在真包缺失时启用** —— 桌面/服务端环境照常使用真包，行为零变化；
    2. 不把替身文件放成顶层 `jiter.py` 那样去"影子覆盖"真包，而是显式注册进
       `sys.modules`，避免哪天真的装上了 jiter 却静默用到替身；
    3. 替身缺失能力时**报错而不是猜** —— 例如 jiter 的 partial 模式直接抛错，
       绝不返回可能错误的解析结果。

调用时机：必须在 import openai / mcp_12306 **之前**调用 `install()`。
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types

__all__ = ["install", "enabled_modules"]


def _available(name: str) -> bool:
    """真包是否可用（不实际导入，避免副作用）。"""
    if name in sys.modules:
        return not getattr(sys.modules[name], "__railfan_shim__", False)
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


# ---------------------------------------------------------------- jiter
def _make_jiter() -> types.ModuleType:
    mod = types.ModuleType("jiter")
    mod.__railfan_shim__ = True  # type: ignore[attr-defined]
    mod.__doc__ = "纯 Python 替身：仅实现完整 JSON 解析（Android 上无 Rust 扩展可用）"

    class JiterError(Exception):
        pass

    def from_json(data, partial_mode: bool = False, **kwargs):  # noqa: ANN001, ARG001
        """把 JSON 解析成 Python 对象。

        partial_mode 在替身里**故意不支持**：它是给流式 `.parsed` 增量解析用的，
        需要真正的增量 JSON 解析器；用 json.loads 硬凑会返回错误结果，
        比直接报错危险得多（上层会把它当成模型输出的"部分解析结果"）。
        """
        if partial_mode:
            raise NotImplementedError(
                "jiter 的 partial_mode 在当前构建（Android/无 Rust 扩展）中不可用。"
                "该能力仅被 openai SDK 的流式 .parsed / 工具调用增量解析使用，"
                "本项目未使用这条路径。若确需使用，请改用非 Android 构建。"
            )
        if isinstance(data, (bytes, bytearray, memoryview)):
            data = bytes(data).decode("utf-8")
        return json.loads(data)

    mod.from_json = from_json
    mod.JiterError = JiterError
    return mod


# ---------------------------------------------------------------- pydantic_settings
# pydantic v2 的 SettingsConfigDict 键 → pydantic v1 Config 属性
_CONFIG_KEY_MAP = {
    "env_file": "env_file",
    "env_file_encoding": "env_file_encoding",
    "env_prefix": "env_prefix",
    "env_nested_delimiter": "env_nested_delimiter",
    "case_sensitive": "case_sensitive",
    "extra": "extra",
}


def _make_pydantic_settings() -> types.ModuleType:
    """基于 pydantic v1 的 BaseSettings 实现 v2 风格的 API 表面。

    关键点：翻译 `model_config` 必须在**元类**里做，不能放在 `__init_subclass__`。
    pydantic v1 的 ModelMetaclass 在创建类时就读取 `namespace['Config']`，而
    `__init_subclass__` 是 `type.__new__` 期间才被调用的 —— 到那时再改 `cls.Config`
    已经晚了，配置会被**静默忽略**（类照样能构造，行为却与预期不符，最难排查的一类问题）。
    """
    from pydantic import BaseSettings as _V1BaseSettings
    from pydantic.main import ModelMetaclass as _V1ModelMetaclass

    mod = types.ModuleType("pydantic_settings")
    mod.__railfan_shim__ = True  # type: ignore[attr-defined]
    mod.__doc__ = "纯 Python 替身：把 pydantic v2 的 settings API 映射到 pydantic v1"

    class SettingsConfigDict(dict):
        """v2 里写在 `model_config = SettingsConfigDict(...)` 的配置载体。"""

    class _ShimMetaclass(_V1ModelMetaclass):
        def __new__(mcs, name, bases, namespace, **kwargs):  # noqa: ANN001
            # 必须先把 model_config 从 namespace 里**取出来**：
            # pydantic v1 会把无类型标注的可变默认值（dict/list/set）推导成字段，
            # 留着它就会凭空多出一个叫 model_config 的字段。
            model_config = namespace.pop("model_config", None)
            if model_config:
                base_cfg = namespace.get("Config") or _V1BaseSettings.Config
                attrs = {}
                for key, value in dict(model_config).items():
                    v1_key = _CONFIG_KEY_MAP.get(key)
                    if v1_key:
                        # v1 的 env_file 接受 str 或序列；list 需转成 tuple
                        attrs[v1_key] = tuple(value) if isinstance(value, list) else value
                # 写进 namespace，确保 pydantic 的元类能读到（而不是只挂到 cls 上）
                namespace["Config"] = type("Config", (base_cfg,), attrs)
            cls = super().__new__(mcs, name, bases, namespace, **kwargs)
            if model_config is not None:
                # 类建好之后再挂回去：仅作为可读回属性，不参与字段推导
                setattr(cls, "model_config", model_config)
            return cls

    class BaseSettings(_V1BaseSettings, metaclass=_ShimMetaclass):
        # v2 别名，供仍按 v2 写法调用的第三方包使用
        @classmethod
        def model_validate(cls, obj, **kwargs):  # noqa: ANN001, ARG003
            return cls.parse_obj(obj)

        def model_dump(self, **kwargs):  # noqa: ANN003, ARG002
            return self.dict()

    mod.SettingsConfigDict = SettingsConfigDict
    mod.BaseSettings = BaseSettings
    return mod


_SHIMS = {"jiter": _make_jiter, "pydantic_settings": _make_pydantic_settings}

# 已启用的替身（供自检/日志展示）
enabled_modules: list[str] = []


def install(*, force: bool = False) -> list[str]:
    """按需注册替身，返回本次启用的模块名列表。

    force=True 时无视真包是否存在（仅供测试模拟 Android 环境）。
    """
    enabled: list[str] = []
    for name, builder in _SHIMS.items():
        if not force and _available(name):
            continue
        sys.modules[name] = builder()
        enabled.append(name)
    if enabled:
        enabled_modules[:] = enabled
    return enabled
