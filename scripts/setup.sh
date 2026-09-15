#!/usr/bin/env bash
# RailFanAI 一键快速配置环境并启动对话页面。
#
# 作用：
#   1) 选择 Python >= 3.10（首选 3.12）并创建虚拟环境、安装依赖
#   2) 确保根目录 .env 存在（无则从 .env.example 复制）
#   3) 一键跑通全部测试（22 套：日期/OD/上下文/策略/流水线/工具/交路/整链/HTTP/账户/迁移/回归/路由/编排语义/成本/加固/配置/产品修复/R1修复/路由兜底/站点质量）
#   4) 启动后端 http://127.0.0.1:8000，并自动打开对话页面
#
# 说明：
#   - 若 .env 已配置 LLM_API_KEY -> 使用真实模型整链联调
#   - 若无 Key                  -> 自动改用 LLM_MOCK=true（无 Key 也可整链演示）
#   - 实时数据源（12306 / rail.re）需中国境内网络，不可达时会如实降级
#   - 端口被占用时会提示，可先用 PORT=8001 bash scripts/setup.sh 换端口
#   - 依赖：python3(>=3.10)、venv、网络（pip 安装）；可自动打开浏览器
#   - 离线数据预热见 scripts/prewarm.sh；文档索引见 docs/README.md
#
# 用法：
#   bash scripts/setup.sh                 # 在仓库根目录下运行
#   PORT=8001 bash scripts/setup.sh       # 自定义端口
#   SKIP_TESTS=1 bash scripts/setup.sh    # 跳过测试自检（快速启动）
#
# —— 导出版本特有说明 ——
#   **本仓库不含任何密钥**（.env 不入库）。首次运行时会引导你导入 LLM Key，
#   共有四种方式（交互输入 / --api-key 参数 / LLM_API_KEY 环境变量 / 直接编辑 .env）。
#   无 Key 也能启动并整链演示（自动 LLM_MOCK=true）；要连真实模型必须导入 Key。
#   仅检查配置而不启动：bash scripts/setup.sh --check-key
#   特别注意：**不要**把含密钥的 .env 提交或随包分发（脚本会做一次兜底扫描提醒）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
ENV_FILE="$ROOT/.env"
LOG_FILE="$ROOT/.setup-uvicorn.log"
PORT="${PORT:-8000}"
SKIP_TESTS="${SKIP_TESTS:-0}"
UVID=""                       # 预初始化，避免 set -u 误报

echo "==> [1/5] 创建虚拟环境并安装依赖"
# 选择 Python：优先 3.12/3.11/3.10，回退系统 python3（需 >= 3.10）
# 3.9 的 TLS 指纹会被 12306 反爬拦截，故强制要求 >= 3.10
PYTHON_BIN=""
for candidate in \
  "$(brew --prefix python@3.12 2>/dev/null)/bin/python3.12" \
  "$(brew --prefix python@3.11 2>/dev/null)/bin/python3.11" \
  "$(brew --prefix python@3.10 2>/dev/null)/bin/python3.10" \
  "$(command -v python3.12 2>/dev/null)" \
  "$(command -v python3.11 2>/dev/null)" \
  "$(command -v python3.10 2>/dev/null)" \
  "$(command -v python3 2>/dev/null)"; do
  if [ -x "$candidate" ]; then PYTHON_BIN="$candidate"; break; fi
done

if [ -z "$PYTHON_BIN" ]; then
  echo "    [错误] 未找到 Python。请先安装 Python >= 3.10（brew install python@3.12）"
  exit 1
fi

PY_VER="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PY_OK="$("$PYTHON_BIN" -c 'import sys; print(1 if sys.version_info >= (3,10) else 0)')"
if [ "$PY_OK" != "1" ]; then
  echo "    [错误] 需要 Python >= 3.10（当前 ${PY_VER}）。"
  echo "       12306 反爬会拦截 3.9 的 TLS 指纹；请运行: brew install python@3.12 && bash scripts/upgrade_python.sh"
  exit 1
fi
echo "    使用 Python $PY_VER ($PYTHON_BIN)"

"$PYTHON_BIN" -m venv "$BACKEND/.venv"
cd "$BACKEND"
"$BACKEND/.venv/bin/python" -m pip install --upgrade pip -q
"$BACKEND/.venv/bin/python" -m pip install -q -r requirements.txt
echo "    依赖安装完成"

echo "==> [1.5/5] 预热离线/静态数据（兜底数据，避免首个请求触发下载）"
bash "$ROOT/scripts/prewarm.sh" || echo "    [警告] 预热未完全成功；相关查询会如实提示数据未就绪"

echo "==> [1.6/5] 构建本地数据字典（里程/车站档案/离线时刻；失败不阻塞启动）"
# 为什么要本地化：里程与车站档案此前是能力空白；GTFS（周更）与黄河铁路网客运里程表
# 都是"字典类"数据，本地化后毫秒级可查，且把请求路径从个人站点上摘下来（见 docs/source-expansion.md）。
# 失败不阻塞：缺字典时 rail.mileage 会明确提示如何构建，train.schedule 仍走 12306 实时链路。
PYTHONPATH=backend "$BACKEND/.venv/bin/python" "$ROOT/scripts/mirror_dict.py" --all \
  || echo "    [警告] 本地字典未构建成功；rail.mileage 会提示先运行 scripts/mirror_dict.py --all"

echo "==> [2/5] 确保 .env 存在"
if [ ! -f "$ENV_FILE" ]; then
  cp "$ROOT/.env.example" "$ENV_FILE"
  echo "    已从 .env.example 生成 ${ENV_FILE}（导出包不含任何密钥，下一步会引导导入）"
else
  echo "    $ENV_FILE 已存在，保留现有配置"
fi

# ---------------------------------------------------------------------------
# [2.5/5] 导入 LLM Key（**仅导出版本包含此段**）
#
# 为什么导出包需要这一步：仓库分发的版本**不包含任何密钥**（.env 被 gitignore，
# 历史上也从未提交过）。没有 Key 时服务仍能启动并整链演示（自动 LLM_MOCK=true），
# 但要连真实模型就必须先导入 Key。
#
# 四种导入方式（任选其一）：
#   1) 交互：直接运行本脚本，在这里粘贴（输入不回显、不写进 shell 历史）
#   2) 参数：bash scripts/setup.sh --api-key <你的Key>
#   3) 环境变量：LLM_API_KEY=<你的Key> bash scripts/setup.sh
#   4) 手工：编辑根目录 .env
# 额外：bash scripts/setup.sh --check-key   # 只检查是否已配置，不启动服务
# ---------------------------------------------------------------------------
_write_env_key() {
  # $1 = key。用「先删后写」保证幂等，不产生重复行；.env 权限收紧到 600。
  local key="$1" tmp
  tmp="$(mktemp)"
  grep -v -E '^[[:space:]]*LLM_API_KEY=' "$ENV_FILE" >"$tmp" || true
  printf 'LLM_API_KEY=%s\n' "$key" >>"$tmp"
  mv "$tmp" "$ENV_FILE"
  chmod 600 "$ENV_FILE" 2>/dev/null || true
}

_key_status() {
  # 输出 "ok" / "missing"；占位符视为未配置
  local k
  k="$(grep -E '^[[:space:]]*LLM_API_KEY=' "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '[:space:]' || true)"
  if [ -z "$k" ] || [ "$k" = "your-api-key-here" ]; then echo "missing"; else echo "ok"; fi
}

_scan_stray_keys() {
  # 兜底：确认仓库内没有把 Key 写进会被提交的文件
  local hits
  hits="$(grep -rlE 'sk-[A-Za-z0-9_-]{16,}' --exclude-dir=.venv --exclude-dir=.git \
            --exclude-dir=__pycache__ --exclude-dir=data "$ROOT" 2>/dev/null | head -5 || true)"
  if [ -n "$hits" ]; then
    echo "    [警告] 在以下文件中发现疑似 API Key 明文，请勿提交/分发这些文件："
    printf '        %s\n' $hits
  fi
}

# ---- 命令行参数解析（--check-key / --api-key <你的Key>）----
_CLI_KEY=""
_CHECK_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --check-key) _CHECK_ONLY=1; shift ;;
    --api-key)   _CLI_KEY="${2:-}"; shift 2 ;;
    --api-key=*) _CLI_KEY="${1#--api-key=}"; shift ;;
    *) shift ;;
  esac
done

if [ -n "$_CLI_KEY" ]; then
  _write_env_key "$_CLI_KEY"
  echo "    已写入 LLM_API_KEY（来自 --api-key）；${ENV_FILE} 权限已收紧为 600"
fi

if [ "$_CHECK_ONLY" = "1" ]; then
  _scan_stray_keys
  if [ "$(_key_status)" = "ok" ]; then
    echo "[OK] LLM_API_KEY 已配置（真实模型可用）"
  else
    echo "[提示] 尚未配置 LLM_API_KEY —— 服务仍可启动，但会走 LLM_MOCK=true（确定性演示）"
    echo "       导入方式见本脚本 [2.5/5] 段注释，或查看 docs/run.md §1"
  fi
  exit 0
fi

if [ "$(_key_status)" = "ok" ]; then
  echo "    LLM_API_KEY 已配置（来自 .env 或环境变量）"
else
  echo
  echo "    未检测到 LLM_API_KEY。可任选一种方式导入（按 Enter 跳过，稍后也能补）："
  echo "      · 直接粘贴 Key（输入不回显）"
  echo "      · 或先跳过：无 Key 时可整链演示（自动 LLM_MOCK=true），但要真实模型必须导入"
  if [ -t 0 ]; then
    printf '    LLM_API_KEY: '
    read -rs _INPUT_KEY || true
    echo
    if [ -n "${_INPUT_KEY:-}" ]; then
      _write_env_key "$_INPUT_KEY"
      echo "    已写入 ${ENV_FILE}（权限 600）"
    else
      echo "    已跳过；稍后可：bash scripts/setup.sh --api-key <你的Key>  或编辑 .env"
    fi
  else
    echo "    （非交互终端：跳过输入。可用 bash scripts/setup.sh --api-key <你的Key>）"
  fi
fi

_scan_stray_keys


echo "==> [3/5] 运行全部测试（自检，27 套；跑完再汇总）"
if [ "$SKIP_TESTS" = "1" ]; then
  echo "    SKIP_TESTS=1，已跳过"
else
  cd "$BACKEND"
  bash tests/run_all.sh
fi

echo "==> [4/5] 启动后端 http://127.0.0.1:$PORT"

# 端口占用检查：避免"静默启动失败 + 健康检查误判成功"
if command -v lsof >/dev/null 2>&1; then
  OCCUPIED="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | head -1 || true)"
  if [ -n "$OCCUPIED" ]; then
    echo "    [警告] 端口 ${PORT} 已被占用（PID ${OCCUPIED}）。"
    echo "       该端口上可能已有一个 RailFanAI 实例（内容可能为旧代码）。"
    if curl -sf --max-time 3 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
      echo "       检测到 /health 可用 —— 直接复用现有服务，不再另起进程。"
      echo
      echo "[OK] 服务已在运行：http://127.0.0.1:${PORT}  (PID=${OCCUPIED})"
      echo "   如需重启加载新代码：kill ${OCCUPIED} && bash scripts/setup.sh"
      if command -v open >/dev/null 2>&1; then open "http://127.0.0.1:$PORT/"; fi
      exit 0
    fi
    echo "       如需换端口：PORT=$((PORT + 1)) bash scripts/setup.sh"
    exit 1
  fi
else
  echo "    （未找到 lsof，跳过端口占用检查）"
fi

# 无 Key -> 自动走 Mock（保证能整链演示）
# 记录命令行/环境传入的端口（.env 里可能也有 PORT，稍后会覆盖它）
_REQUESTED_PORT="$PORT"

set -a; source "$ENV_FILE" 2>/dev/null; set +a
# .env 通常含 PORT=8000，会覆盖外部传入值 —— 若调用方显式指定了 PORT，以调用方为准
PORT="$_REQUESTED_PORT"
export PORT

# 同时检查系统环境变量（优先级高于 .env）
if [ -n "${LLM_API_KEY:-}" ] && [ "$LLM_API_KEY" != "your-api-key-here" ]; then
  export LLM_MOCK="false"
else
  export LLM_MOCK="true"
  echo "    未检测到 LLM_API_KEY，已自动启用 LLM_MOCK=true"
fi
cd "$BACKEND"
PYTHONPATH=. "$BACKEND/.venv/bin/uvicorn" app.main:app --host 127.0.0.1 --port "$PORT" >"$LOG_FILE" 2>&1 &
UVID=$!

echo "==> [5/5] 等待服务就绪并打开页面 http://127.0.0.1:$PORT/"
READY=0
for i in $(seq 1 30); do
  # 进程若已退出（如启动报错），立即失败而不是空等到超时
  if ! kill -0 "$UVID" 2>/dev/null; then
    echo "    [错误] 进程已退出，请查看日志：$LOG_FILE"
    tail -n 20 "$LOG_FILE" 2>/dev/null || true
    exit 1
  fi
  if curl -sf --max-time 3 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
    READY=1
    break
  fi
  sleep 1
done

if [ "$READY" != "1" ]; then
  echo "    [错误] 服务未就绪，请查看 $LOG_FILE"
  tail -n 20 "$LOG_FILE" 2>/dev/null || true
  kill "$UVID" 2>/dev/null || true
  exit 1
fi
echo "    服务已就绪"

if command -v open >/dev/null 2>&1; then
  open "http://127.0.0.1:$PORT/"
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "http://127.0.0.1:$PORT/"
else
  echo "    请手动打开：http://127.0.0.1:$PORT/"
fi

echo
echo "[OK] 完成。服务运行中：http://127.0.0.1:$PORT  (PID=$UVID)"
echo "   停止：kill $UVID    日志：$LOG_FILE"