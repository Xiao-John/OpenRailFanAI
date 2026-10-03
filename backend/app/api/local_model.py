"""设备端本地模型路由：状态查询 / 一键下载（带进度）/ 删除 / 启停。

为什么需要它：模型 0.5–2.6 GB，**不可能打进 APK**。此前只能靠 `adb push`，
封测用户根本用不了。有了这几个端点，设置页就能给一个"一键下载并启用"的按钮。

安全
    下载地址来自服务端固定的 `MODEL_CHOICES`，**不接受请求体里的 URL**
    —— 否则这就是一条现成的 SSRF + SSRF-后的任意大文件落盘通道。
    删除只作用于模型目录下的 `*.gguf`，不接受任意路径。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import local_inference

_log = logging.getLogger("railfan.api.local_model")

router = APIRouter(tags=["local-model"])


class DownloadRequest(BaseModel):
    choice: str = Field(..., description="MODEL_CHOICES 里的 id")


@router.get("/local-model")
async def get_status() -> dict:
    """本地模型的完整状态：是否含二进制、有没有模型、服务是否就绪、下载进度、可选目录。"""
    return local_inference.status()


@router.post("/local-model/download")
async def start_download(req: DownloadRequest) -> dict:
    """一键下载（后台进行）。进度用 GET /api/local-model 轮询。"""
    try:
        local_inference.start_download(req.choice)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return local_inference.status()


@router.get("/local-model/diagnostics")
async def diagnostics() -> dict:
    """打包好的排障信息（状态 + llama-server 日志 + 应用日志 + 环境）。

    给界面上那个「导出诊断」按钮用。真机上没有 adb，应用私有目录读不到，
    **没有这个就没有远程排障的可能**。
    """
    return {"text": local_inference.diagnostics()}


@router.post("/local-model/log/clear")
async def clear_logs() -> dict:
    """清空历史日志（跨次追加的日志混着几十次启动记录，清掉后复现一次才有可读性）。"""
    return local_inference.clear_logs()


@router.post("/local-model/export")
async def export_diagnostics() -> dict:
    """把诊断**写成文件**并返回路径 —— 比分享长文本可靠得多。"""
    try:
        return local_inference.export_diagnostics()
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"导出失败：{e}") from e


@router.get("/local-model/npu-probe")
async def npu_probe() -> dict:
    """跑一次 NPU 可行性探针（能不能在 DSP 上开会话）。

    为什么做成端点而不是只在诊断导出里：这是**一次决定性的判定**，
    用户点一下就该看到结论，而不是"导出文件 → 找地方打开 → 翻到某一段"。
    输出**原样返回**，不做二次解释 —— 探针的原始返回值本身就是证据。
    """
    return {
        "binary": local_inference.probe_binary(),
        # 两半一起返回（原生 + Java 缓存那份），末尾带 EOF。
        # 只给原生那半会让人得出"设备不支持"的错误结论 —— 见 npu_probe_report()。
        "text": local_inference.npu_probe_report(),
    }


@router.get("/local-model/log")
async def server_log(lines: int = 120) -> dict:
    """llama-server 的日志尾部。

    为什么要有这个端点：设备上那份日志在**应用私有目录**里，非调试包用 adb 读不到，
    而"本地模型起没起来、到底跑在什么后端"恰恰只看得到这一处。
    实测为此瞎猜过一轮（以为在用 GPU，其实在跑 CPU）。**能远程看到日志比什么都省事。**
    """
    return {
        "path": local_inference.log_path(),
        "tail": local_inference.log_tail(max(10, min(lines, 1000))),
    }


@router.post("/local-model/cancel")
async def cancel_download() -> dict:
    local_inference.cancel_download()
    return local_inference.status()


class StartRequest(BaseModel):
    ngl: int | None = Field(None, ge=0, le=999,
                            description="GPU 卸载层数；0=纯 CPU。给了就重启并记住。")
    threads: int | None = Field(None, ge=1, le=32,
                                description="CPU 线程数。设备 8 核，默认只用了 4 —— 值得试。")
    ctx: int | None = Field(None, ge=512, le=65536, description="上下文长度。")
    extra_args: str | None = Field(
        None, max_length=2000,
        description="**原始 llama-server 参数**（内部调试轨用），如 `-fa on --device HTP0`。"
                    "追加在命令行最后（llama.cpp 后者覆盖前者，所以它说了算）。"
                    "走参数列表而非 shell，因此没有命令注入面。")


@router.post("/local-model/start")
async def start_server(req: StartRequest | None = None) -> dict:
    """手动启用；带参数时改配置并重启（**用来隔离变量、找性能上限**）。"""
    if req is not None:
        vals = {k: v for k, v in (("ngl", req.ngl), ("threads", req.threads),
                                  ("ctx", req.ctx), ("extra_args", req.extra_args))
                if v is not None}
        if vals:
            return local_inference.set_tune(**vals)
    local_inference.start(wait=True)
    return local_inference.status()


@router.post("/local-model/stop")
async def stop_server() -> dict:
    local_inference.stop()
    return local_inference.status()


class DeleteRequest(BaseModel):
    name: str | None = Field(None, description="要删除的模型文件名（来自 /api/local-model 的 models）")
    all: bool = Field(False, description="显式全删（回收空间）。与 name 二选一，都不给则 400")


class SelectRequest(BaseModel):
    name: str = Field(..., description="要切换到的模型文件名（必须来自 models 列表）")


@router.post("/local-model/select")
async def select_model(req: SelectRequest) -> dict:
    """切换到另一个**已下载**的模型（多档共存）。

    为什么要这个端点：对比 2B / 4B 此前只能"删掉再下载"（0.5–2.6 GB），
    一次对照十几分钟 —— 等于把对比评测变成做不了的事。
    校验在 `local_inference.select_model` 里：名字必须出现在 `list_models()` 中，
    **不接受任意路径**（否则就是一条任意文件删除/读取通道）。
    """
    try:
        return local_inference.select_model(req.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/local-model/delete")
async def delete_model(req: DeleteRequest | None = None) -> dict:
    """删除模型。**必须显式**：要么给 `name` 删一个，要么 `all=true` 全删。

    旧版没有参数、一删就删光 —— 多档共存之后那等于"删一个模型顺手清掉另外几个 GB"，
    所以这里改成必须显式，且名字要经 `_safe_model_name` 收成纯文件名。
    """
    r = req or DeleteRequest()
    try:
        return local_inference.delete_model(r.name, all_models=r.all)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
