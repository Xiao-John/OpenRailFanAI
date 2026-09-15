#!/usr/bin/env bash
# 安装 Android 模拟器（用于在无真机的情况下自测 APK）。
#
# 定位：这是**开发/调试**工具，不是交付物的一部分。工具链同样装在 .android-build/ 内，
# 删掉该目录即完全卸载。
#
# 为什么用 default 而不是 aosp_atd（更小更快）：
#   ATD 是面向 CI 的精简镜像，会裁掉部分系统应用；本项目要靠**系统 WebView** 渲染界面，
#   用精简镜像可能得到与真机不一致的结论（"测试通过但真机白屏"）。宁可启动慢一点。
#
# 用法：
#   bash scripts/android/setup-emulator.sh            # 装 emulator + 系统镜像
#   bash scripts/android/setup-emulator.sh --avd      # 顺带创建 AVD
#   bash scripts/android/setup-emulator.sh --check
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BUILD_DIR="$ROOT/.android-build"
SDK="$BUILD_DIR/sdk"
AVD_NAME="${AVD_NAME:-railfan_test}"
API="${ANDROID_API:-35}"
IMAGE="system-images;android-$API;default;arm64-v8a"

export JAVA_HOME="$BUILD_DIR/jdk17/Contents/Home"
export ANDROID_HOME="$SDK"
export ANDROID_SDK_ROOT="$SDK"
# AVD 与 SDK 缓存都落在工作区内（默认会写 $HOME/.android，属于污染用户目录）
export ANDROID_USER_HOME="$BUILD_DIR/android-home"
export ANDROID_AVD_HOME="$ANDROID_USER_HOME/avd"
mkdir -p "$ANDROID_USER_HOME" "$ANDROID_AVD_HOME"

SM="$SDK/cmdline-tools/latest/bin/sdkmanager"
AVDM="$SDK/cmdline-tools/latest/bin/avdmanager"
EMU="$SDK/emulator/emulator"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()  { printf '  \033[32m✓\033[0m %s\n' "$*"; }
die() { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

if [ "${1:-}" = "--check" ]; then
  printf 'emulator : %s\n' "$([ -x "$EMU" ] && "$EMU" -version 2>/dev/null | head -1 || echo '未安装')"
  printf '系统镜像 : %s\n' "$([ -d "$SDK/system-images/android-$API/default/arm64-v8a" ] && echo "$IMAGE" || echo '未安装')"
  printf 'AVD      : %s\n' "$("$EMU" -list-avds 2>/dev/null | grep -x "$AVD_NAME" || echo '未创建')"
  exit 0
fi

[ -x "$SM" ] || die "缺少 cmdline-tools，请先跑 scripts/android/setup-toolchain.sh"

say "安装 emulator 与系统镜像（${IMAGE}，约 1.5GB）"
yes | "$SM" --licenses >/dev/null 2>&1 || true
log="$BUILD_DIR/emulator-install.log"
if ! "$SM" --install "emulator" "$IMAGE" >"$log" 2>&1; then
  tail -20 "$log" >&2
  die "安装失败（完整日志：${log}）"
fi
tail -3 "$log" | sed 's/^/  /'
ok "emulator 与镜像就绪"

if [ "${1:-}" = "--avd" ]; then
  say "创建 AVD：$AVD_NAME"
  if "$EMU" -list-avds 2>/dev/null | grep -qx "$AVD_NAME"; then
    ok "AVD 已存在，跳过"
  else
    # --device 用 pixel_6：分辨率与真机接近，且自带 WebView
    echo no | "$AVDM" create avd -n "$AVD_NAME" -k "$IMAGE" --device "pixel_6" --force
    ok "已创建（AVD_HOME=${ANDROID_AVD_HOME}）"
  fi
fi

say "完成"
cat <<EOF
  启动模拟器并等待就绪：
    bash scripts/android/run-emulator.sh start
  安装并启动 APK（含 logcat）：
    bash scripts/android/run-emulator.sh install
  截图：
    bash scripts/android/run-emulator.sh screenshot /tmp/shot.png
  停止：
    bash scripts/android/run-emulator.sh stop
EOF
