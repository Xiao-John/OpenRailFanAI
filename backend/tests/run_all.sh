#!/usr/bin/env bash
# 一键运行全部测试（三层流水线 + 数据源工具 + 整链集成 + 账户系统 + 审计修复回归）。
#
# 用法：bash tests/run_all.sh   （在 backend/ 下执行）
#
# 行为约定（M11.1 加固）：
# - **跑完全部套件再汇总**：某套失败不会中断后续套件（早期用 `set -e`，
#   首个失败即退出，看不到其它套件的真实状态）
# - 结尾打印通过/失败清单，任一套失败则退出码为 1
set -uo pipefail
cd "$(dirname "$0")/.."

SUITES=(
  # 基础能力
  test_dates
  test_od
  test_context
  test_policy
  test_pipeline
  # 数据源与整链（部分依赖真实网络/LLM）
  test_tools
  test_emu_routing
  test_integration_fullchain
  test_api
  # 审计修复回归（全部无网络）
  test_regressions
  test_routing
  test_orchestrator_semantics
  test_cost_governance
  test_hardening
  test_config_docs
  # 产品实测（96 条车迷测试集）问题修复回归
  test_product_fixes
  # R1（RAG 验证第 1 轮）问题修复回归：D06 红线 + 截断治理
  test_r1_fixes
  test_r1_fixes2
  # 站点清单质量 / 同音纠错 / 思考效率（六条批复第 2/4/5 条）
  test_station_quality
  # 按线路名查站序/里程（F06+F05）
  test_rail_line_stations
  # 车次经停站（2026-09-15：search 权威 train_no + 图定表直查）
  test_train_stops
  # 车站大屏（station.screen：12306 bigScreen 接口 + 空结果三义歧义治理）
  test_station_screen
  # 本地数据字典（里程/车站档案/离线时刻；GTFS + 黄河里程表）
  test_dict_mileage
  # 决策层性能优化（确定性快路径 / 合并调用 / 投机预取）
  test_perf_fastpath
  # LLM 多供应商 / 双 API 方言（chat.completions 与 responses，含自动探测与参数降级；全部无网络）
  test_llm_providers
  # 前端数据层（多对话逻辑，需 node）
  test_frontend_store
)

FAILED=()
PASSED=0

for suite in "${SUITES[@]}"; do
  echo "──────── ${suite} ────────"
  if PYTHONPATH=. .venv/bin/python "tests/${suite}.py"; then
    PASSED=$((PASSED + 1))
  else
    FAILED+=("${suite}")
    echo ">>> ${suite} 失败（继续运行后续套件）"
  fi
done

echo
echo "════════════════ 测试汇总 ════════════════"
echo "通过：${PASSED} / ${#SUITES[@]}"
if [ "${#FAILED[@]}" -gt 0 ]; then
  echo "失败：${FAILED[*]}"
  echo "=== 存在失败套件（见上方各自输出） ==="
  exit 1
fi
echo "=== 全部测试通过 ==="
