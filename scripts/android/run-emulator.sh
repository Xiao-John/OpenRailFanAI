#!/usr/bin/env bash
# 驱动 Android 模拟器：启动 / 装包并抓 logcat / 截图 / 停止。
#
# 定位：**开发调试工具**。用途是让"没有真机"也能真正跑一遍 APK ——
# 尤其是看 Python 侧的 logcat（真机排障时用户拿不到，界面又未必显示得全）。
#
# 用法：
#   bash scripts/android/run-emulator.sh start            # headless 启动并等待就绪
#   bash scripts/android/run-emulator.sh install [apk]    # 安装并启动，前台抓 logcat
#   bash scripts/android/run-emulator.sh logs             # 只抓 logcat（Python 关键行）
#   bash scripts/android/run-emulator.sh screenshot out.png
#   bash scripts/android/run-emulator.sh ui               # dump 当前界面的文本（无截图也能看内容）
#   bash scripts/android/run-emulator.sh stop
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BUILD_DIR="$ROOT/.android-build"
SDK="$BUILD_DIR/sdk"
EMU="$SDK/emulator/emulator"
ADB="$SDK/platform-tools/adb"
AVD_NAME="${AVD_NAME:-railfan_test}"
# 应用 id：release 无后缀，debug 带 .debug
APP_ID="${APP_ID:-org.openrailfanai.app}"
DEFAULT_APK="$ROOT/android/app/build/outputs/apk/release/app-release.apk"

export JAVA_HOME="$BUILD_DIR/jdk17/Contents/Home"
export ANDROID_HOME="$SDK"
export ANDROID_SDK_ROOT="$SDK"
export ANDROID_USER_HOME="$BUILD_DIR/android-home"
export ANDROID_AVD_HOME="$ANDROID_USER_HOME/avd"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

booted() { "$ADB" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' | grep -q '^1$'; }

case "${1:-}" in
  start)
    say "启动模拟器（headless）"
    if booted; then echo "  已在运行"; exit 0; fi
    # -no-window：无 GUI 也能跑；-gpu swiftshader_indirect：不依赖宿主机 GPU
    # -no-snapshot：避免复用到脏快照导致"行为与真机不一致"的假象
    nohup "$EMU" -avd "$AVD_NAME" -no-window -no-audio -no-boot-anim \
      -gpu swiftshader_indirect -no-snapshot -port 5554 \
      > "$BUILD_DIR/emulator.log" 2>&1 &
    echo "  已拉起（日志 $BUILD_DIR/emulator.log），等待 boot_completed…"
    "$ADB" wait-for-device
    for i in $(seq 1 120); do
      if booted; then echo "  就绪（约 ${i}s）"; "$ADB" shell input keyevent 82 >/dev/null 2>&1 || true; exit 0; fi
      sleep 1
    done
    echo "  超时未就绪，见 $BUILD_DIR/emulator.log" >&2
    exit 1
    ;;

  install)
    APK="${2:-$DEFAULT_APK}"
    [ -f "$APK" ] || { echo "找不到 APK：$APK" >&2; exit 1; }
    booted || { echo "模拟器未启动，先跑 start" >&2; exit 1; }
    say "安装 $APK"
    # 先卸载：避免版本标记导致跳过解包（真机同理会遇到）
    "$ADB" uninstall "$APP_ID" >/dev/null 2>&1 || true
    "$ADB" install -r "$APK" | tail -2
    say "启动应用并抓 logcat（Ctrl-C 退出）"
    "$ADB" logcat -c
    "$ADB" shell monkey -p "$APP_ID" -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1
    # 只保留有价值的行：Python 侧、我们的 TAG、异常
    "$ADB" logcat -v time | grep --line-buffered -E "RailFanAI|python|Chaquopy|AndroidRuntime|FATAL|Traceback|railfan|uvicorn"
    ;;

  logs)
    "$ADB" logcat -d -v time | grep -E "RailFanAI|python|Chaquopy|AndroidRuntime|FATAL|Traceback|railfan|uvicorn" | tail -${2:-120}
    ;;

  screenshot)
    OUT="${2:-/tmp/railfan-shot.png}"
    "$ADB" exec-out screencap -p > "$OUT"
    echo "  已保存 $OUT"
    ;;

  ui)
    # 把界面上的文本 dump 出来：没有图形界面时也能"看到"屏幕上写了什么
    "$ADB" shell uiautomator dump /sdcard/ui.xml >/dev/null 2>&1 || true
    "$ADB" shell cat /sdcard/ui.xml 2>/dev/null \
      | tr '<' '\n' | grep -oE 'text="[^"]+"' | sed 's/text="//;s/"$//' | grep -v '^$' | head -60
    ;;

  stop)
    "$ADB" emu kill >/dev/null 2>&1 || true
    echo "  已发送关闭信号"
    ;;

  *)
    grep '^#' "$0" | sed 's/^# \{0,1\}//' | head -14
    ;;
esac
