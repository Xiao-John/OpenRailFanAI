#!/usr/bin/env bash
# 构建 Android 一体化 APK。
#
# 前置：先跑过 scripts/android/setup-toolchain.sh（工具链装在工作区内，自包含）。
#
# 用法：
#   bash scripts/android/build.sh                 # 默认 assembleDebug
#   bash scripts/android/build.sh assembleRelease # 出正式包（需签名，见 docs/android.md）
#   bash scripts/android/build.sh -PincludeDict=true assembleRelease
#   bash scripts/android/build.sh clean
#
# 构建成功后会把产物按版本号归置到 dist/android/OpenRailFanAI-<VERSION>-arm64-<类型>.apk。
# 版本号唯一来源是仓库根的 VERSION（同一条号也决定 Android 的 versionName/versionCode）。
#
# 说明：参数**原样转交** Gradle（-P 开关与任务可混用）。早期版本只取第一个参数当任务，
#      会把 `-PincludeDict=true` 误当成任务名 —— 已在文档里给出组合用法的场景必须支持。
#      所有 Gradle/JDK/SDK 路径都指向 .android-build/，不读系统环境，
#      因此在没装 Android Studio 的机器上也能构建，且不污染用户目录。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BUILD_DIR="$ROOT/.android-build"
GRADLE="$BUILD_DIR/gradle-8.11.1/bin/gradle"

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

echo "JAVA_HOME   = $JAVA_HOME"
echo "ANDROID_HOME= $ANDROID_HOME"
echo "Python      = $CHAQUOPY_BUILD_PYTHON"
echo "版本        = $APP_VERSION"
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
for TYPE in debug release; do
  SRC="$ROOT/android/app/build/outputs/apk/$TYPE/app-$TYPE.apk"
  DST="$ROOT/dist/android/OpenRailFanAI-$APP_VERSION-arm64-$TYPE.apk"
  if [ ! -f "$SRC" ]; then
    continue
  fi
  if [ "$SRC" -nt "$BUILD_STARTED_AT" ]; then
    cp "$SRC" "$DST"
    echo "已归置：dist/android/OpenRailFanAI-$APP_VERSION-arm64-$TYPE.apk"
  else
    echo "跳过 $TYPE 产物：本次未重新构建（跑 build.sh assemble$TYPE 可刷新）"
  fi
done
