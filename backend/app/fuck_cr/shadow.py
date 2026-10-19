"""Syntax-only candidates. Never changes service admission or train semantics."""
from app.fuck_cr import Mode, selected_mode
from app.fuck_cr.parser import parse


def candidates(text: str, legacy_codes: list[str]) -> tuple[list[str], list[str]]:
    """Add intact future tokens; unknown prefixes remain opaque, never digits.

    This is permissive candidate syntax, not an official range validator or a
    claim that any service exists. Dates/models are masked by the caller.
    """
    result = parse(text or '')
    # Retain the helper's call signature, but never seed the new result from the
    # old parser: doing so hides old false positives and creates false agreement.
    return list(result.codes), list(dict.fromkeys(t.normalized for t in result.unknown))


def observe(text: str, legacy_codes: list[str], *, exclude_spans=()) -> None:
    if selected_mode() is not Mode.SHADOW:
        return
    if exclude_spans:
        masked = list(text)
        for start, end in exclude_spans:
            masked[start:end] = ' ' * (end - start)
        text = ''.join(masked)
    preview, unknown = candidates(text, legacy_codes)
    from app.metrics import record_rule_comparison
    record_rule_comparison('fuck_cr.syntax', same=not unknown and preview == legacy_codes)
