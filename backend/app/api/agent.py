"""Optional app/file actions, kept separate from the railway chat contract."""
from fastapi import APIRouter, HTTPException
from app.agent import AgentRequest, run
from app.llm.client import LLMUnavailable

router=APIRouter()

@router.post('/agent/run')
async def agent_run(req: AgentRequest):
    try:
        return await run(req)
    except (ValueError,TypeError) as exc:
        raise HTTPException(422,'文件内容或工具参数无效，请检查格式、大小及日程时间。') from exc
    except LLMUnavailable as exc:
        raise HTTPException(503,'文件处理模型暂不可用；原文件未改动。') from exc
