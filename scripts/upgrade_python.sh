#!/usr/bin/env bash
# RailFanAI Python 升级脚本：从 3.9 → 3.12，重建虚拟环境
#
# 安全约定（M11.1 加固）：
#   旧虚拟环境先**改名保留**（.venv.bak.<pid>），任一步失败即自动回滚；
#   早期实现直接 `rm -rf .venv`，一旦 pip 安装失败就既没有新环境也没有旧环境。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
VENV="$BACKEND/.venv"
BACKUP="$BACKEND/.venv.bak.$$"
PYTHON_BIN="$(brew --prefix python@3.12 2>/dev/null)/bin/python3.12"

if [ ! -x "$PYTHON_BIN" ]; then
    echo "❌ Python 3.12 未安装。请先运行: brew install python@3.12"
    exit 1
fi

echo "==> Python: $($PYTHON_BIN --version)"

_restore() {
    # 失败回滚：把旧环境放回去，保证"至少还有一个可用环境"
    if [ -d "$BACKUP" ] && [ ! -d "$VENV" ]; then
        mv "$BACKUP" "$VENV"
        echo "⚠️  升级失败，已回滚到原有虚拟环境：${VENV}"
    fi
}
trap _restore EXIT

if [ -d "$VENV" ]; then
    echo "==> 保留旧虚拟环境为 $(basename "$BACKUP")（成功后自动删除）"
    mv "$VENV" "$BACKUP"
fi

echo "==> 重建虚拟环境..."
cd "$BACKEND"
"$PYTHON_BIN" -m venv .venv

echo "==> 安装依赖..."
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt -q

echo "==> 预热离线/静态数据（站点库 + 车次目录）..."
bash "$ROOT/scripts/prewarm.sh" --force

echo "==> 运行测试..."
bash tests/run_all.sh

# 全部通过 → 升级成功，删除旧环境备份并解除回滚
trap - EXIT
if [ -d "$BACKUP" ]; then
    rm -rf "$BACKUP"
    echo "==> 已删除旧虚拟环境备份"
fi

echo ""
echo "✅ Python 升级完成。启动: cd backend && PYTHONPATH=. .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000"
