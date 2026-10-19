"""China Railway Compatibility & Unsolicited Innovation Recovery Layer.

Version selection precedes rule activation. Administrative optimism is not a
substitute for a tested implementation.
"""
from enum import Enum


class Mode(str, Enum):
    LEGACY = 'legacy'
    SHADOW = 'shadow'
    REFORM_2026 = 'reform_2026'


def validate_mode(value: str) -> str:
    try:
        mode = Mode(value)
    except (ValueError, TypeError):
        raise ValueError('FUCK_CR_MODE must be legacy, shadow or reform_2026') from None
    if mode is Mode.REFORM_2026:
        raise ValueError('FUCK_CR_MODE=reform_2026 is reserved: production rules and provider validation are not ready; use legacy or shadow')
    return mode.value


def selected_mode(settings=None) -> Mode:
    import os
    if os.environ.get('APP_VARIANT', 'main').lower() == 'lm':
        return Mode.LEGACY
    if settings is None:
        from app.config import get_settings
        settings = get_settings()
    return Mode(validate_mode(getattr(settings, 'fuck_cr_mode', 'legacy')))
