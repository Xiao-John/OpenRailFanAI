"""User-authorized holistic review, independent from historical pixel fidelity."""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

CHECKS = (
    "design_direction", "typography_and_readability", "spacing_and_hierarchy",
    "recognizable_controls", "no_control_overlap", "complete_core_content",
)


def evaluate_harmony(state: str, image: Path, policy: dict, review: dict,
                     hard_failures: list, hard_unmeasured: list) -> dict:
    """Never convert missing review or missing functional evidence into a pass."""
    missing = list(hard_unmeasured)
    failures = list(hard_failures)
    policy = policy if isinstance(policy, dict) else {}
    review = review if isinstance(review, dict) else {}
    states = review.get("states")
    item = states.get(state, {}) if isinstance(states, dict) else {}
    item = item if isinstance(item, dict) else {}
    actual_sha = hashlib.sha256(image.read_bytes()).hexdigest()
    if policy.get("schema_version") != 1 or policy.get("mode") != "harmony_first":
        missing.append("整体协调验收策略无效。")
    if review.get("schema_version") != 1 or review.get("policy_id") != policy.get("id"):
        missing.append("评审未绑定当前用户授权的验收策略。")
    if not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip():
        missing.append("缺少整体视觉评审者。")
    try:
        if datetime.fromisoformat(review["reviewed_at"]).tzinfo is None:
            raise ValueError("review timestamp needs timezone")
    except (KeyError, TypeError, ValueError):
        missing.append("缺少有效的视觉评审时间。")
    if item.get("image_sha256") != actual_sha:
        missing.append("整体视觉评审与本次实际截图哈希不一致。")
    checks = item.get("checks")
    checks = checks if isinstance(checks, dict) else {}
    for key in CHECKS:
        value = checks.get(key)
        if value is False:
            failures.append(f"整体视觉检查未通过：{key}")
        elif value is not True:
            missing.append(f"整体视觉检查缺失：{key}")
    notes = item.get("notes")
    if (not isinstance(notes, list) or not notes
            or not all(isinstance(note, str) and note.strip() for note in notes)):
        missing.append("缺少该状态的具体整体评审记录。")
    return {"status": "failed" if failures else "unmeasured" if missing else "passed",
            "policy_id": policy.get("id"), "reviewer": review.get("reviewer"),
            "reviewed_at": review.get("reviewed_at"), "actual_sha256": actual_sha,
            "checks": checks, "notes": item.get("notes", []),
            "hard_failures": failures, "unmeasured": missing,
            "scope": "整体协调与核心交互验收；不代表旧版逐像素门槛通过。"}
