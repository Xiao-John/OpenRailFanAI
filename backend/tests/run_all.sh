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

# 用哪个解释器跑（可用环境变量覆盖）。默认仓库自带 venv；
# Android 一体化版本需要验证"纯 Python 依赖集 + pydantic v1"下的行为，
# 这时指向另一个 venv 即可跑同一套测试，例如：
#   PYTHON=$PWD/../.android-build/venv-nodeps/bin/python bash tests/run_all.sh
PYTHON="${PYTHON:-.venv/bin/python}"
[ -x "$PYTHON" ] || { echo "找不到解释器：$PYTHON" >&2; exit 1; }
echo "解释器：$PYTHON ($("$PYTHON" -c 'import sys; print(sys.version.split()[0])'))"

SUITES=(
  test_fuck_cr_api
  test_agent_tools
  test_agent_documents
  test_agent_images
  test_agent_input_routes
  test_llm_image_transport
  # 基础能力
  test_dates
  test_dates_without_tzdata
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
  test_metrics
  # 机位本地库（方案 A；纯离线：闸门 + 查表路径 + 选路）
  test_photo_spots
  test_backend_performance
  test_rt_performance
  test_llm_pool
  test_pipeline_performance
  test_local_inference
  test_output_guard
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
  # 决策层：闭合知识叶子 + 站名匹配缓存（全部无网络）
  test_closed_knowledge_tree
  test_decision_station_matching
  # 未来日期不查交路（产品红线：不据历史记录推断未来担当）
  test_future_routing_guard
  # 静态资源交付（gzip 压缩 + 版本戳分级缓存；全部无网络）
  test_static_assets
  # LLM 多供应商 / 双 API 方言（chat.completions 与 responses，含自动探测与参数降级；全部无网络）
  test_llm_providers
  # Android 一体化约束（依赖版本锁定 / 兼容层 / 工程接线；全部无网络）
  test_android
  # 前端数据层（多对话逻辑，需 node）
  test_frontend_store
  # 「用户说法 → 内部值」探针语料（时间/时段/席别/车种；全部无网络）
  test_phrasings
  # A 类规模化语料回归（读 tests/corpus/*.jsonl，机器生成 + 校验冻结；全部无网络）
  test_corpus
  # 前端渲染（Markdown 表格等，需 node）
  test_frontend_render
  # 确定性规则回复（mock / 模型不可用兜底：prompt 解析 + 规则排版；全部无网络）
  test_mock_render
  # 多轮槽位/意图继承（省略句承接上文；含"该交出的必须交出"红线；全部无网络）
  test_multiturn_slots
  # 提示词缓存用量观测（流式累计 usage、缺失字段、命中率口径；全部无网络）
  test_prompt_cache_metrics
  # 本地小模型加固（输出不可用 ≠ 服务不可用：JSON 宽容解析 + 决策层回退；全部无网络）
  test_slm_hardening
  # Main 票价与余票交付、文案、日期及成对结果回归（2026-10-06）
  test_ticket_copy
  test_ticket_query_delivery
  test_fare_availability
  test_ticket_pair
  test_ticket_delivery_dedup
  test_executed_fare
  test_ticket_snapshot
  test_fuck_cr
  test_fuck_cr_parser
  test_fare_contract
  test_github_updates
  test_photo_annotations
  test_photo_dictionary_updates
  test_structured_sse_visibility
  test_routing_projection_coupling
  test_service_batch
  test_incident_service_delivery
  test_review_fixes
  test_multidate_services
)

# 默认离线；--live 仅运行真实接口/模型套件，--all 合并执行。
MODE="${1:---offline}"
case "$MODE" in --offline|--live|--all|--list) ;; *) echo "用法：$0 [--offline|--live|--all|--list]" >&2; exit 2 ;; esac
LIVE_ONLY=" test_tools test_emu_routing test_api "
MIXED=" test_train_stops test_station_screen "
if [ "$MODE" = "--list" ]; then
  for suite in "${SUITES[@]}"; do
    case "$LIVE_ONLY" in *" $suite "*) echo "live $suite" ;; *) echo "offline $suite" ;; esac
  done
  echo "mixed（离线时仅跑本地断言）:$MIXED"
  exit 0
fi
NODE="${NODE:-$(command -v node || true)}"
NODE_READY=false
if [ -n "$NODE" ] && "$NODE" --version >/dev/null 2>&1; then
  NODE_READY=true
  export PATH="$(dirname "$NODE"):$PATH"
fi
FAILED=()
SKIPPED=()
PARTIAL=()
PASSED=0
TOTAL=0
LOG_DIR="$(mktemp -d "${TMPDIR:-/tmp}/railfan-tests.XXXXXX")"
trap 'rm -rf "$LOG_DIR"' EXIT
for suite in "${SUITES[@]}"; do
  IS_LIVE=false
  case "$LIVE_ONLY" in *" $suite "*) IS_LIVE=true ;; esac
  if [ "$MODE" = "--offline" ] && [ "$IS_LIVE" = true ]; then continue; fi
  if [ "$MODE" = "--live" ] && [ "$IS_LIVE" = false ]; then
    case "$MIXED" in *" $suite "*) ;; *) continue ;; esac
  fi
  TOTAL=$((TOTAL + 1))
  echo "──────── ${suite} ────────"
  case "$suite" in
    test_frontend_store|test_frontend_render)
      if [ "$NODE_READY" != true ]; then
        echo "[SKIP] 没有可运行的 Node；可用 NODE=/绝对路径/node 指定"
        SKIPPED+=("$suite:Node不可用")
        continue
      fi ;;
  esac
  if RAILFAN_TEST_MODE="${MODE#--}" PYTHONPATH=. "$PYTHON" "tests/${suite}.py" >"$LOG_DIR/$suite.log" 2>&1; then
    cat "$LOG_DIR/$suite.log"
    if grep -Eq '\[SKIP\]|OK \(skipped=' "$LOG_DIR/$suite.log"; then
      PARTIAL+=("$suite")
    else
      PASSED=$((PASSED + 1))
    fi
  else
    cat "$LOG_DIR/$suite.log"
    FAILED+=("${suite}")
    echo ">>> ${suite} 失败（继续运行后续套件；不把断言失败改为跳过）"
  fi
done
echo
echo "════════════════ 测试汇总 ════════════════"
echo "模式：${MODE}；套件：${TOTAL}；完整通过：${PASSED}；含跳过：${#PARTIAL[@]}；环境跳过：${#SKIPPED[@]}；失败：${#FAILED[@]}"
[ "${#PARTIAL[@]}" -eq 0 ] || echo "含跳过（不能视为完整通过）：${PARTIAL[*]}"
[ "${#SKIPPED[@]}" -eq 0 ] || echo "环境跳过：${SKIPPED[*]}"
if [ "${#FAILED[@]}" -gt 0 ]; then
  echo "失败：${FAILED[*]}"
  exit 1
fi
echo "=== 所选范围执行完成；跳过项目未获验收 ==="
