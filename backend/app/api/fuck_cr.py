"""Preparation contract for the Main client's one-click compatibility button.

The new business rules are not implemented. These endpoints expose that fact;
they must not mutate process settings or pretend shadow observation is repair.
"""
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, StrictBool

from app.fuck_cr import Mode, selected_mode

router = APIRouter(prefix='/fuck-cr', tags=['fuck-cr'])


class CompatibilityStatus(BaseModel):
    schema_version: Literal[1] = 1
    feature: Literal['fuck_cr'] = 'fuck_cr'
    button_label: str = '一键FUCK_CR'
    mode: Literal['legacy', 'shadow']
    enabled: bool = False
    new_rules_ready: bool = False
    can_enable: bool = False
    message: str = '新版车次兼容规则尚未就绪。'


class ActivationRequest(BaseModel):
    enabled: StrictBool

    class Config:
        extra = 'forbid'


class ActivationResult(BaseModel):
    requested_enabled: bool
    changed: bool = False
    status: CompatibilityStatus


def compatibility_status() -> CompatibilityStatus:
    # Keep readiness closed until actual business routing and provider checks
    # exist. A parser, environment flag or frontend preference is not readiness.
    mode = selected_mode()
    if mode not in (Mode.LEGACY, Mode.SHADOW):
        raise RuntimeError('Unsupported compatibility mode')
    return CompatibilityStatus(mode=mode.value)


@router.get('/status', response_model=CompatibilityStatus)
async def status():
    return compatibility_status()


@router.post('/activation', response_model=ActivationResult)
async def activation(request: ActivationRequest):
    current = compatibility_status()
    if request.enabled:
        raise HTTPException(status_code=409, detail={
            'code': 'fuck_cr_not_ready',
            'message': current.message,
            'status': current.dict(),
        })
    # New rules are already disabled. Idempotent acknowledgement only: shadow
    # observation, environment files, cache keys and other users are untouched.
    return ActivationResult(requested_enabled=False, status=current)
