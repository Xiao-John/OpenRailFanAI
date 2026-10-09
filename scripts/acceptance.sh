#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if [ "${1:-}" = "--photo-cards" ]; then
  exec backend/.venv/bin/python scripts/android/accept-photo-cards.py "${@:2}"
fi

if [ "${1:-}" = "--ergonomics-walkthrough" ]; then
  exec backend/.venv/bin/python scripts/android/review-live-ergonomics.py
fi

if [ "${1:-}" = "--routing-walkthrough" ]; then
  exec backend/.venv/bin/python scripts/android/review-live-routing.py
fi

if [ "${1:-}" = "--review-main-compose" ]; then
  exec backend/.venv/bin/python scripts/android/run-native-acceptance.py --review-run "${2:?缺少正式运行编号}"
fi

if [ "${1:-}" = "--main-compose" ]; then
  exec backend/.venv/bin/python scripts/android/run-native-acceptance.py
fi

node frontend/tests/visual/capture.mjs
backend/.venv/bin/python frontend/tests/visual/compare-icons.py
backend/.venv/bin/python frontend/tests/visual/build-acceptance.py
