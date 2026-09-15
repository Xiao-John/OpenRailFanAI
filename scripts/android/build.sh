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

echo "JAVA_HOME   = $JAVA_HOME"
echo "ANDROID_HOME= $ANDROID_HOME"
echo "Python      = $CHAQUOPY_BUILD_PYTHON"
echo "Gradle 参数 = $*"
echo

cd "$ROOT/android"
exec "$GRADLE" --no-daemon --console=plain "$@"
