#!/usr/bin/env bash
# 本地小模型（SLM）一键接入 —— 面向"不想买 API Key / 机器只有 8G 内存"的用户。
#
# 做什么：
#   1) 确认 Ollama 已安装并在跑（缺则按平台给安装命令；macOS 可直接装）
#   2) 拉取推荐模型 Qwen3.5-2B（Q4_K_M，8G 内存的首选；理由见 docs/local-model.md）
#   3) 用 Modelfile 派生一个**上下文拉到 16k** 的本地模型
#      —— 官方线上 tag 的默认 num_ctx 只有 4096，事实块一长就会被静默截断
#   4) 冒烟：按本项目真实用法走一次**结构化 JSON 调用**，确认能被解析
#   5) 打印（或写入 .env）需要设的环境变量
#
# 为什么不是 Qwen3-1.7B：它已被 **Qwen3.5-2B**（2026-03 开源）换代 —— 同尺寸更强、
#   上下文更长，且**只对 1/4 的层做全注意力**，KV cache 是 12 KiB/token，
#   比全注意力的 MiniCPM5-2B（42 KiB/token）便宜 3.5 倍。8G 内存是硬约束时，
#   这一点比"Agent 榜单高几分"重要。完整选型与实测见 docs/local-model.md。
#
# 用法（在仓库根执行）：
#   bash scripts/setup_local_model.sh              # 装好并冒烟，只打印变量
#   bash scripts/setup_local_model.sh --write      # 同时写进 .env（会先备份）
#   bash scripts/setup_local_model.sh --bench      # 冒烟后再跑语料基准
#   bash scripts/setup_local_model.sh --target=android   # 手机端甜点位：只打印配置，不装任何东西
#   ANDROID_CTX=16384 bash scripts/setup_local_model.sh --target=android   # 手机端要更大上下文
#   MODEL=minicpm5-2b:2b bash scripts/setup_local_model.sh   # 换成别的模型
#   CTX=32768 bash scripts/setup_local_model.sh    # 更大的上下文（更吃内存）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
PY="$ROOT/backend/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3 || true)"

MODEL="${MODEL:-qwen3.5:2b-q4_K_M}"   # 8G 首选；官方 tag 自带 num_ctx=4096
CTX="${CTX:-16384}"                   # 上下文；线上 tag 默认只有 4096
LOCAL_NAME="${LOCAL_NAME:-railfan-slm}"
HOST="${OLLAMA_HOST_URL:-http://127.0.0.1:11434}"
WRITE=0
BENCH=0
TARGET="${TARGET:-host}"
for arg in "$@"; do
  case "$arg" in
    --write) WRITE=1 ;;
    --bench) BENCH=1 ;;
    --target) echo "[错误] --target 需要带值：--target=android"; exit 1 ;;
    --target=*) TARGET="${arg#--target=}" ;;
    -h|--help) sed -n '2,31p' "$0"; exit 0 ;;
    *) echo "[错误] 未知参数：${arg}（可用：--write / --bench / --target=android）"; exit 1 ;;
  esac
done
case "$TARGET" in
  host|android) ;;
  *) echo "[错误] 未知 target：${TARGET}（可用：host / android）"; exit 1 ;;
esac

# 把「KEY=VALUE 行」合并进 .env：已存在的键**原位替换**，缺失的追加到末尾；先备份。
write_env_lines() {  # $1 = 每行一个 KEY=VALUE 的文件
  if [ -f "$ENV_FILE" ]; then
    cp "$ENV_FILE" "$ENV_FILE.bak.$(date +%Y%m%d%H%M%S)"
    echo "    （已备份原 .env）"
  fi
  "$PY" - "$ENV_FILE" "$1" <<'PYEOF'
import sys
from pathlib import Path

path, kvfile = Path(sys.argv[1]), Path(sys.argv[2])
wanted: dict[str, str] = {}
for ln in kvfile.read_text(encoding="utf-8").splitlines():
    ln = ln.strip()
    if ln and not ln.startswith("#") and "=" in ln:
        k, v = ln.split("=", 1)
        wanted[k.strip()] = v.strip()
lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
seen, out = set(), []
for ln in lines:
    key = ln.split("=", 1)[0].strip() if "=" in ln and not ln.lstrip().startswith("#") else None
    if key in wanted:
        out.append(f"{key}={wanted[key]}")
        seen.add(key)
    else:
        out.append(ln)
if out and out[-1].strip():
    out.append("")
out.append("# ---- 本地小模型（scripts/setup_local_model.sh 写入）----")
for k, v in wanted.items():
    if k not in seen:
        out.append(f"{k}={v}")
path.write_text("\n".join(out) + "\n", encoding="utf-8")
print(f"    已写入 {path}")
PYEOF
}

# ---- 手机端（--target=android）：只输出配置，不在本机装/拉任何东西 ----
# 每个参数都有实测依据，见 docs/local-model.md「手机端甜点位」一节。
if [ "$TARGET" = "android" ]; then
  DEV_MODEL="${ANDROID_MODEL:-Qwen3.5-4B-Q4_K_M.gguf}"
  DEV_CTX="${ANDROID_CTX:-8192}"   # 手机上 8192 够用（检索事实 ~1000 token + 历史 + 正文）
  echo "==> 目标设备：8 Elite Gen 5 / 16 GB —— 甜点位配置"
  echo
  echo "    ★ 用 4B，不用 2B（192 条语料实测）"
  echo "        意图 81.4% vs 68.1% ｜ 槽位 73.9% vs 64.8% ｜ 云端对照 82.3% / 70.5%"
  echo "      → 4B 意图追平云端、槽位反超；2B 掉 13 个点，'完全免费'只有 4B 撑得住"
  echo "      → 规划是 prefill 主导（~1400 token 进 / ~44 token 出），4B 只比 2B 慢 1.4×"
  echo
  echo "    ★ 用 llama-server，不用 Ollama"
  echo "        同权重同机器实测 38.0 vs 33.0 tok/s（+15%），且 --cache-reuse 等开关 Ollama 不暴露"
  echo
  echo "    ★ 关闭推测解码（实测三种配置**全部负收益**）"
  echo "        ngram-simple     −39%（接受率仅 20%：答案是'续写'不是'复制'）"
  echo "        0.8B 草稿·GPU    −57%（接受率 84% 却更慢：验证开销吃掉全部收益）"
  echo "        0.8B 草稿·CPU    −71%"
  echo
  echo "    ★ 输出上限 192 token"
  echo "        实测 4B 的合格答案用 143–183 token；放到 224 会把 11s 目标顶到 12.7s"
  echo
  echo "==> 在手机上拉起推理服务（模型先 adb push 到 /data/local/tmp/）："
  cat <<EOF
llama-server \\
  -m /data/local/tmp/${DEV_MODEL} \\
  -c ${DEV_CTX} -ngl 99 -fa on -t 4 -tb 6 -b 2048 -ub 512 \\
  --cache-reuse 256 --jinja --host 127.0.0.1 --port 8081
EOF
  echo
  echo "    -ngl 99 = 全部层卸载到 Adreno（最大单项提速）"
  echo "    -c ${DEV_CTX} 必须与下面的 LLM_CONTEXT_TOKENS 一致（对不齐会**静默丢弃**上下文）"
  echo
  echo "==> 把下面几行写进 .env（或用 --write 让脚本代劳）："
  DEV_KV="$(mktemp)"
  cat >"$DEV_KV" <<EOF
LLM_PROVIDER=ondevice
LLM_PROVIDERS={"ondevice":{"type":"openai","base_url":"http://127.0.0.1:8081/v1","api_key":"local","model":"${DEV_MODEL%.gguf}","no_think_body":{"reasoning_effort":"none"}}}
LLM_CONTEXT_TOKENS=${DEV_CTX}
LLM_MAX_TOKENS=192
LLM_TIMEOUT_S=90
LLM_STRUCTURED_JSON_SCHEMA=true
LLM_STRUCTURED_COMPACT_PROMPT=true
LLM_STRUCTURED_NO_THINK=true
LLM_FALLBACK_RENDER=true
FACT_MAX_ENTRIES=8
WEB_SEARCH_FETCH_CHARS=1000
EOF
  cat "$DEV_KV"
  echo
  echo "    模型名取自 LLM_PROVIDERS 的 model 字段。provider 由 LLM_PROVIDERS 定义时"
  echo "    source != request，因此**不需要**打开 LLM_ALLOW_PRIVATE_BASE_URL 这个全局口子。"
  echo "    JSON_SCHEMA 与 COMPACT_PROMPT 两个都开才是最优组合：schema 走 response_format"
  echo "    做约束解码，正文里就不存在 schema 可被照抄（2B 上实测会被整段抄回来）。"
  if [ "$WRITE" = "1" ]; then
    write_env_lines "$DEV_KV"
  fi
  rm -f "$DEV_KV"
  echo
  echo "    时间账（设备端估算）：prefill 1.2s + decode 192/21 ≈ 9.1s = 约 10.3s，进 11s 目标"
  echo "    未验：192 上限在真实问题分布下的截断率（要跑真实问题，不是几个案例）"
  exit 0
fi

echo "==> [1/5] 检查 Ollama"
if ! command -v ollama >/dev/null 2>&1; then
  case "$(uname -s)" in
    Darwin)
      echo "    未安装。正在用 Homebrew 安装（首次约几百 MB）…"
      HOMEBREW_NO_AUTO_UPDATE=1 brew install ollama
      ;;
    Linux)
      echo "    未安装。请执行：  curl -fsSL https://ollama.com/install.sh | sh"
      echo "    （装完重新运行本脚本）"
      exit 1
      ;;
    *)
      echo "    未安装。请到 https://ollama.com/download 下载对应平台安装包后重试。"
      exit 1
      ;;
  esac
fi
echo "    $(ollama --version 2>/dev/null | head -1)"

echo "==> [2/5] 确认服务在跑（${HOST}）"
if ! curl -fsS --max-time 3 "$HOST/api/version" >/dev/null 2>&1; then
  echo "    服务未响应，后台拉起 ollama serve …"
  nohup ollama serve >"$ROOT/.ollama.log" 2>&1 &
  for _ in $(seq 1 30); do
    sleep 1
    if curl -fsS --max-time 2 "$HOST/api/version" >/dev/null 2>&1; then break; fi
  done
fi
if ! curl -fsS --max-time 3 "$HOST/api/version" >/dev/null 2>&1; then
  echo "    [错误] 服务起不来，看日志：$ROOT/.ollama.log"
  exit 1
fi
echo "    $HOST 就绪"

echo "==> [3/5] 拉取模型 ${MODEL}（约 1.5 GB，可断点续传）"
ollama pull "$MODEL"

echo "==> [4/5] 派生 ${LOCAL_NAME}（num_ctx=${CTX} + 正确的对话模板与停止符）"
# 为什么要派生，而不是直接用 pull 下来的 tag —— 三件事都必须靠 Modelfile 修：
#
# 1) **对话模板**：线上 tag 的 template 是 `{{ .Prompt }}`（裸拼接），GGUF 里自带的
#    Qwen 模板没有被用上。后果不是"效果差一点"，而是**模型根本不知道轮次边界**：
#    实测表现为不停生成（跑到 2600+ token 也不会自己停）、把思考写在正文外面。
#    下面这份模板按 GGUF 里的官方模板重写（只保留文本分支，去掉 tools/vision），
#    并在 assistant 开头写入**空的思考块** —— 官方模板里这就是"不思考"的写法。
#
# 2) **停止符**：线上 tag 没定义任何 stop。缺了它，模型答完会继续编下一轮，
#    白白烧掉整个输出预算。
#
# 3) **num_ctx**：线上 tag 默认只有 4096，本项目注入的事实块（车站大屏/逐站时刻/
#    表格）经常远超 4k，超出的部分会被**静默丢弃** —— 表现为"模型答得头头是道
#    但少了一半数据"，比报错难查得多。
TMP_MF="$(mktemp)"
cat >"$TMP_MF" <<EOF
FROM $MODEL
PARAMETER num_ctx $CTX
PARAMETER temperature 0
PARAMETER stop "<|im_end|>"
PARAMETER stop "<|im_start|>"
TEMPLATE """{{- if .Messages }}
{{- range \$i, \$_ := .Messages }}
{{- \$last := eq (len (slice \$.Messages \$i)) 1 }}
{{- if \$last }}
{{- if ne .Role "assistant" }}
<|im_start|>{{ .Role }}
{{ .Content }}<|im_end|>
<|im_start|>assistant
<think>

</think>

{{- else }}
<|im_start|>{{ .Role }}
{{ .Content }}
{{- end }}
{{- else }}
<|im_start|>{{ .Role }}
{{ .Content }}<|im_end|>
{{- end }}
{{- end }}
{{- else }}
<|im_start|>assistant
<think>

</think>

{{- end }}"""
EOF
ollama create "$LOCAL_NAME" -f "$TMP_MF" >/dev/null
rm -f "$TMP_MF"
echo "    已创建 $LOCAL_NAME"

echo "==> [5/5] 冒烟：本项目真实用法（结构化 JSON 调用）"
SMOKE_OUT="$(mktemp)"
if curl -fsS --max-time 180 "$HOST/v1/chat/completions" \
    -H 'Content-Type: application/json' \
    -d "$(cat <<JSON
{
  "model": "$LOCAL_NAME",
  "temperature": 0,
  "response_format": {"type": "json_object"},
  "reasoning_effort": "none",
  "messages": [
    {"role": "system", "content": "你是 RailFanAI 的结构化解析器，只输出满足要求的 JSON。"},
    {"role": "user", "content": "把这句话解析成 JSON：intent（ticket/schedule/station/rail_line/emu_routing/photo_spot/news/general 之一）、question_type（realtime/knowledge/mixed）、target（车次号，没有填 null）。\n\n输入：明天北京到上海还有票吗？"}
  ]
}
JSON
)" >"$SMOKE_OUT" 2>&1; then
  "$PY" - "$SMOKE_OUT" "$ROOT" <<'PYEOF'
import json, sys
raw = open(sys.argv[1], encoding="utf-8").read()
try:
    body = json.loads(raw)
    content = body["choices"][0]["message"]["content"]
except Exception as e:                                    # noqa: BLE001
    print(f"    [警告] 响应不是预期的 OpenAI 结构：{type(e).__name__}: {e}")
    print("    " + raw[:400].replace("\n", " "))
    sys.exit(0)
print(f"    模型原文：{content.strip()[:220]}")
# 用仓库里同一条解析路径判定"能不能用"，别用裸 json.loads 自欺
sys.path.insert(0, sys.argv[2] + "/backend")
try:
    from app.llm.client import _parse_json_object
    obj = _parse_json_object(content)
    print(f"    ✔ 能被本项目解析：{json.dumps(obj, ensure_ascii=False)}")
    if obj.get("intent") != "ticket":
        print(f"    ⚠ 意图判成了 {obj.get('intent')!r}（期望 ticket）—— 见 docs/local-model.md 的实测准确率")
except Exception as e:                                    # noqa: BLE001
    print(f"    ✘ 本项目解析路径不接受这条输出：{e}")
    print("      这说明该模型不适合当决策器，或需要调参/换量化档位。")
PYEOF
else
  echo "    [错误] 请求失败，响应：$(head -c 300 "$SMOKE_OUT")"
  rm -f "$SMOKE_OUT"
  exit 1
fi
rm -f "$SMOKE_OUT"

echo
echo "==> 把下面几行写进仓库根目录的 .env（或用 --write 让脚本代劳）："
cat <<EOF
LLM_PROVIDER=ollama
LLM_MODEL=$LOCAL_NAME
LLM_CONTEXT_TOKENS=$CTX
LLM_STRUCTURED_JSON_SCHEMA=true
LLM_STRUCTURED_COMPACT_PROMPT=true
LLM_STRUCTURED_NO_THINK=true
LLM_FALLBACK_RENDER=true
EOF

if [ "$WRITE" = "1" ]; then
  HOST_KV="$(mktemp)"
  cat >"$HOST_KV" <<EOF
LLM_PROVIDER=ollama
LLM_MODEL=$LOCAL_NAME
LLM_CONTEXT_TOKENS=$CTX
LLM_STRUCTURED_JSON_SCHEMA=true
LLM_STRUCTURED_COMPACT_PROMPT=true
LLM_STRUCTURED_NO_THINK=true
LLM_FALLBACK_RENDER=true
EOF
  write_env_lines "$HOST_KV"
  rm -f "$HOST_KV"
fi

if [ "$BENCH" = "1" ]; then
  echo
  echo "==> 跑语料基准（79 条 route=llm 用例；串行，约几分钟）"
  PYTHONPATH="$ROOT/backend" "$PY" "$ROOT/scripts/bench_planner_model.py" \
    --provider ollama --model "$LOCAL_NAME" --out "/tmp/bench-slm.json"
  echo "    与云端基线对比：加上 --compare /tmp/bench-cloud.json /tmp/bench-slm.json"
fi
