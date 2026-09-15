"""RailFanAI 后端包。

此处**立即**安装依赖兼容层：它必须在任何可能 import `openai` / `mcp_12306`
的模块之前生效，而 app 包的 __init__ 是最早的执行点。

为什么兼容层放在后端包里而不是 Android 工程里：
    需要替身的两个包（jiter、pydantic-settings）在服务端与 Android 上**都不再安装**
    —— pydantic 锁 v1 后，要求 pydantic>=2 的 pydantic-settings 就无法共存，
    而 jiter 是 Rust 扩展、Android 无轮子。所以它是两个环境共用的，
    不该藏在某个平台目录下；放在这里也让服务端的全量测试能覆盖到它。
"""
from app import _compat as _compat

_compat.install()
