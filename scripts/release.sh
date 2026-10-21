#!/usr/bin/env bash
# 一份发布说明文件驱动构建、提交、推送及 GitHub Release。
set -euo pipefail
release_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
release_python="${PYTHON:-$release_root/backend/.venv/bin/python}"
exec "$release_python" "$release_root/scripts/release/publish.py" "$@"
