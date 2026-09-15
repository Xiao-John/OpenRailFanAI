"""配置与文档一致性测试（**无网络**，M11.1 审计修复回归）。

对应 `审计报告（历史）` P2：`.env.example` 只覆盖了部分配置项
（审计时 26/35），新人照示例配环境会漏项，且示例里可能出现拼写错误或已删除的键。

规则：
1. `Settings` 的**每个字段**都必须在 `.env.example` 中出现（`FIELD_NAME=` 形式）
2. `.env.example` 里**不得出现** `Settings` 不认识的键（防拼写错误/残留键）
3. 其他文档类一致性：README / run.md 中的关键命令指向真实存在的文件

运行：cd backend && PYTHONPATH=. .venv/bin/python tests/test_config_docs.py
"""
from __future__ import annotations

import re
from pathlib import Path

from app.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = REPO_ROOT / ".env.example"

# 允许不出现在 .env.example 的字段（一般是不该鼓励手工配置的）—— 当前为空，保持严格
ALLOW_MISSING: set[str] = set()


def _env_keys() -> set[str]:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    return set(re.findall(r"^([A-Z][A-Z0-9_]*)\s*=", text, re.M))


def test_env_example_covers_all_settings():
    fields = {name.upper() for name in Settings.__fields__}
    keys = _env_keys()
    missing = fields - keys - ALLOW_MISSING
    assert not missing, (
        f".env.example 缺少 {len(missing)} 个配置项：{sorted(missing)}\n"
        "→ 新增配置项时必须同步写入 .env.example（否则新人照示例配置会漏项）"
    )
    print(f"[PASS] .env.example 覆盖全部 {len(fields)} 个配置项")


def test_env_example_has_no_unknown_keys():
    fields = {name.upper() for name in Settings.__fields__}
    unknown = _env_keys() - fields
    assert not unknown, (
        f".env.example 存在 Settings 不认识的键：{sorted(unknown)}（拼写错误或已删除的配置）"
    )
    print("[PASS] .env.example 无未知/拼错的键")


def test_docs_reference_existing_files():
    """README/run.md 里引用的脚本与文档路径必须真实存在（防文档漂移）。"""
    checked = 0
    for doc in ("README.md", "docs/run.md"):
        text = (REPO_ROOT / doc).read_text(encoding="utf-8")
        for ref in set(re.findall(r"(?:bash\s+)?(scripts/[\w./-]+\.sh|docs/[\w./-]+\.md)", text)):
            assert (REPO_ROOT / ref).exists(), f"{doc} 引用了不存在的路径：{ref}"
            checked += 1
    print(f"[PASS] 文档引用的 {checked} 个脚本/文档路径均存在")


def test_env_example_keys_have_no_duplicates():
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    keys = re.findall(r"^([A-Z][A-Z0-9_]*)\s*=", text, re.M)
    dupes = {k for k in keys if keys.count(k) > 1}
    assert not dupes, f".env.example 存在重复键：{sorted(dupes)}"
    print(f"[PASS] .env.example 无重复键（共 {len(keys)} 个）")


def main():
    test_env_example_covers_all_settings()
    test_env_example_has_no_unknown_keys()
    test_env_example_keys_have_no_duplicates()
    test_docs_reference_existing_files()
    print("\n配置与文档一致性测试全部通过 ✔")


if __name__ == "__main__":
    main()
