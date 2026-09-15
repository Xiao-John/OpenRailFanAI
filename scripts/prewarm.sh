#!/usr/bin/env bash
# 预热离线/静态数据（被 setup.sh 与 upgrade_python.sh 共用）。
#
# 为什么需要：这些是**兜底数据**，不应在用户请求路径里下载
# （见 docs/review/00-improvement-plan.md ✅35）。统一在此预热，避免各脚本各写一份。
#
# 用法：
#   bash scripts/prewarm.sh            # 缺失才下载
#   bash scripts/prewarm.sh --force    # 强制重新下载（如升级 Python 后）
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
PY="$BACKEND/.venv/bin/python"
FORCE="${1:-}"

if [ ! -x "$PY" ]; then
    echo "[prewarm] 未找到虚拟环境：${PY}"
    echo "          请先运行 bash scripts/setup.sh 创建环境"
    exit 1
fi

cd "$BACKEND"

echo "[prewarm] 1/2 12306 站点库（静态数据，站点名/拼音/电报码互查）..."
if ! PYTHONPATH=. "$PY" -c "
import asyncio
from app.tools import _rt12306 as rt
asyncio.run(rt.ensure_loaded())
print('          站点库就绪')
"; then
    echo "[prewarm] ⚠️  站点库预热失败（多为网络问题），站点查询将降级"
fi

echo "[prewarm] 2/2 离线车次目录（非实时兜底数据，仅用于起止站推断）..."
if ! PYTHONPATH=. PREWARM_FORCE="$FORCE" "$PY" -c "
import asyncio, os
from app.data.train_db import build_train_cache, cache_age_days, get_train_db
asyncio.run(build_train_cache(force=os.environ.get('PREWARM_FORCE') == '--force'))
print(f'          车次目录就绪：{get_train_db().train_count} 条（缓存年龄 {cache_age_days():.2f} 天）')
"; then
    echo "[prewarm] ⚠️  车次目录预热失败；相关查询会提示缓存未就绪而非静默降级"
fi

echo "[prewarm] 完成"
