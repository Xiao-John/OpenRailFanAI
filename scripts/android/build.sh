#!/usr/bin/env bash
# 构建 Android 一体化 APK。
#
# 前置：先跑过 scripts/android/setup-toolchain.sh（工具链装在工作区内，自包含）。
#
# 用法：
#   bash scripts/android/build.sh                 # 默认 assembleDebug
#   bash scripts/android/build.sh assembleRelease # 出正式包（需签名，见 docs/android.md）
#   bash scripts/android/build.sh -PincludeDict=true assembleRelease
#   bash scripts/android/build.sh --lm -PincludeDict=true assembleDebug   # 本地模型版（封测轨）
#   bash scripts/android/build.sh clean
#
# 构建成功后会把产物按版本号归置到 dist/android/OpenRailFanAI-<VERSION>-arm64-<类型>.apk。
# 版本号唯一来源是仓库根的 VERSION（同一条号也决定 Android 的 versionName/versionCode）。
#
# `--lm` 是**独立的封测轨**：版本标识来自 VERSION.lm（一个自增数字），产物命名为
#   OpenRailFanAI-lm<N>-arm64-<类型>.apk，Android versionName=lm<N>、versionCode=100000+N。
# 它与 VERSION 完全解耦 —— 改 VERSION 会让正式构建跟着跳号；沿用同一个号又会在
# dist/ 里留下"同名不同内容"的包。构建成功后 VERSION.lm 自动 +1。
#
# 说明：参数**原样转交** Gradle（-P 开关与任务可混用）。早期版本只取第一个参数当任务，
#      会把 `-PincludeDict=true` 误当成任务名 —— 已在文档里给出组合用法的场景必须支持。
#      所有 Gradle/JDK/SDK 路径都指向 .android-build/，不读系统环境，
#      因此在没装 Android Studio 的机器上也能构建，且不污染用户目录。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BUILD_DIR="$ROOT/.android-build"
GRADLE="$BUILD_DIR/gradle-8.11.1/bin/gradle"

# `--lm` / `--internal` 是本脚本自己的开关，必须在转交 Gradle 前摘掉（Gradle 不认识它们）。
LM=0
INTERNAL=0
GRADLE_ARGS=()
for a in "$@"; do
  case "$a" in
    --lm) LM=1 ;;
    --internal) LM=1; INTERNAL=1 ;;
    *) GRADLE_ARGS+=("$a") ;;
  esac
done
if [ "${#GRADLE_ARGS[@]}" -gt 0 ]; then
  set -- "${GRADLE_ARGS[@]}"
else
  set --
fi

if [ "$#" -eq 0 ]; then
  set -- assembleDebug
fi

[ -x "$GRADLE" ] || { echo "缺少 Gradle：${GRADLE}（见 scripts/android/setup-toolchain.sh）" >&2; exit 1; }
[ -d "$BUILD_DIR/sdk/platforms/android-35" ] || { echo "缺少 Android SDK 平台 35，请先跑 setup-toolchain.sh" >&2; exit 1; }

export JAVA_HOME="$BUILD_DIR/jdk17/Contents/Home"
export ANDROID_HOME="$BUILD_DIR/sdk"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export ANDROID_USER_HOME="$BUILD_DIR/android-home"
# 缓存与守护进程都落在工作区内（沙箱只允许写工作区）
export GRADLE_USER_HOME="$BUILD_DIR/gradle-home"
# Chaquopy 需要与 App 同主次版本的本机 Python 来生成部分产物
export CHAQUOPY_BUILD_PYTHON="${CHAQUOPY_BUILD_PYTHON:-$ROOT/backend/.venv/bin/python3.12}"
mkdir -p "$GRADLE_USER_HOME" "$ANDROID_USER_HOME"

APP_VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION" 2>/dev/null || true)"
if [ -z "$APP_VERSION" ]; then
  echo "缺少或为空的 VERSION 文件：版本号是唯一真相，不能靠临时编一个" >&2
  exit 1
fi

# LM 轨（本地模型版封测）：版本标识来自 VERSION.lm，与 VERSION 无关。
LM_FILE="$ROOT/VERSION.lm"
ARTIFACT_LABEL="$APP_VERSION"
if [ "$LM" = "1" ]; then
  # **内部调试轨从 lm1000 起**（`--internal`），计数放在独立文件里。两条理由：
  #   1) **防混淆**：lm1..lmN 是封测轨，lm1000+ 是内部调试轨，一眼看得出手上是哪种包；
  #   2) **可直接覆盖安装**：versionCode = 100000 + N ⇒ lm1000 = **101000**，
  #      永远高于封测轨（100000 + 小数字），同包名能直接升级。
  #      ⚠️ 代价是**不可逆**：装过 lm1000 之后，再装 lm37 会被系统拒绝（版本号更低），
  #      要回封测轨必须先卸载。这是内部调试版的既定取舍。
  if [ "$INTERNAL" = "1" ]; then
    LM_FILE="$ROOT/VERSION.lminternal"
    [ -f "$LM_FILE" ] || echo "1000" >"$LM_FILE"    # 首次自动播种在 lm1000
    set -- "$@" "-PinternalDebug=true"
  fi
  LM_N="$(tr -dc '0-9' < "$LM_FILE" 2>/dev/null || true)"
  if [ -z "$LM_N" ]; then
    echo "[错误] 缺少或非法的 ${LM_FILE}（应只含一个数字）；这条轨靠这个数字彼此区分" >&2
    exit 1
  fi
  ARTIFACT_LABEL="${LM_PREFIX:-lm}$LM_N"
  set -- "$@" "-PlmLabel=$ARTIFACT_LABEL"
fi

echo "JAVA_HOME   = $JAVA_HOME"
echo "ANDROID_HOME= $ANDROID_HOME"
echo "Python      = $CHAQUOPY_BUILD_PYTHON"
echo "版本        = $ARTIFACT_LABEL$([ "$LM" = "1" ] && echo "（LM 轨，正式线仍为 ${APP_VERSION}）")"
echo "Gradle 参数 = $*"
echo

BUILD_STARTED_AT="$(mktemp)"          # 作为"本次构建"的新鲜度基准
cd "$ROOT/android"
"$GRADLE" --no-daemon --console=plain "$@"
STATUS=$?
[ "$STATUS" -eq 0 ] || exit "$STATUS"

# 把产物按**版本号**归置到 dist/android/。
# 以前所有构建都叫 OpenRailFanAI-0.1.1-arm64-*.apk，内容各不相同却同名 ——
# "我手上这个文件是哪一版"从文件名上看不出来，只能靠我口头说明。
# 现在文件名直接带版本，Android 自身的"应用信息"里也是同一个号。
#
# 只归置**本次真的重新构建过**的产物：Gradle 会对没变化的类型报 UP-TO-DATE，
# 那种 APK 还是旧内容，按新版本号复制过去就是"同名不同内容"——正是要消灭的东西。
mkdir -p "$ROOT/dist/android"
PLACED=0
for TYPE in debug release; do
  SRC="$ROOT/android/app/build/outputs/apk/$TYPE/app-$TYPE.apk"
  DST="$ROOT/dist/android/OpenRailFanAI-$ARTIFACT_LABEL-arm64-$TYPE.apk"
  if [ ! -f "$SRC" ]; then
    continue
  fi
  if [ "$SRC" -nt "$BUILD_STARTED_AT" ]; then
    cp "$SRC" "$DST"
    echo "已归置：dist/android/OpenRailFanAI-$ARTIFACT_LABEL-arm64-$TYPE.apk"
    PLACED=1
  else
    echo "跳过 $TYPE 产物：本次未重新构建（跑 build.sh assemble$TYPE 可刷新）"
  fi
done

# 只有**真的产出了 lm<N> 包**才推进计数：构建失败或全部 UP-TO-DATE 时不跳号，
# 否则数字会飘 —— 而"靠数字彼此区分"正是这条轨的全部意义。
if [ "$LM" = "1" ] && [ "$PLACED" = "1" ]; then
  echo "$((LM_N + 1))" >"$LM_FILE"
  # 报**真实写进去的那个文件**：内部轨写 VERSION.lminternal，写死 "VERSION.lm"
  # 会让人以为两条轨共用一个计数器（实测出的第一版就是这样，差点被骗）。
  NEXT_FLAG="--lm"; [ "$INTERNAL" = "1" ] && NEXT_FLAG="--internal"
  echo "下次 ${NEXT_FLAG} 将构建 ${LM_PREFIX:-lm}$((LM_N + 1))（已写入 ${LM_FILE#"$ROOT/"}）"
fi
