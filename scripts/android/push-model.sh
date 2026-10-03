#!/usr/bin/env bash
# 把 GGUF 模型推进 LM 轨的 App，供设备端本地推理使用。
#
# 为什么要专门写个脚本，而不是给一句 adb push：
#   实测（Android 15 arm64 模拟器）`adb push` 到外部应用目录
#       /sdcard/Android/data/<pkg>/files/models/
#   会**成功**，但目录属主是 shell，App 侧 `ls` 直接 `Permission denied`
#   （emulated storage 的合成权限问题，不是 SELinux）——
#   现象是"文件明明在那儿，App 却说没找到模型"，极难排查。
#   走 `run-as` 写**内部** filesDir 则完全没这个问题，所以统一走这条。
#
# 前置：LM 轨 App 必须已经装过一次并启动过（内部目录才会存在）；
#       且必须是 **debug 包** —— `run-as` 只对 debuggable 应用有效。
#
# 用法（在仓库根执行）：
#   bash scripts/android/push-model.sh /path/to/Qwen3.5-2B-Q4_K_M.gguf
#   PKG=org.openrailfanai.app bash scripts/android/push-model.sh model.gguf   # 指定包名
#   bash scripts/android/push-model.sh --list                                 # 看设备上已有哪些模型
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ADB="$ROOT/.android-build/sdk/platform-tools/adb"
PKG="${PKG:-org.openrailfanai.app.debug}"

[ -x "$ADB" ] || { echo "[错误] 找不到 adb：${ADB}（先跑 scripts/android/setup-toolchain.sh）" >&2; exit 1; }
[ -n "$("$ADB" devices | sed -n '2p')" ] || { echo "[错误] 没有连接的设备/模拟器" >&2; exit 1; }

if [ "${1:-}" = "--list" ]; then
  echo "设备 $PKG 内的模型："
  # shellcheck disable=SC2016  # 单引号是给远端 sh 用的，不能本地展开
  "$ADB" shell run-as "$PKG" ls -l files/models/ 2>&1 || echo "  （目录不存在——App 还没启动过？）"
  exit 0
fi

SRC="${1:-}"
[ -n "$SRC" ] || { echo "用法：bash scripts/android/push-model.sh <模型.gguf>  或 --list" >&2; exit 1; }
[ -f "$SRC" ] || { echo "[错误] 文件不存在：$SRC" >&2; exit 1; }
case "$SRC" in
  *.gguf) ;;
  *) echo "[警告] $SRC 不是 .gguf，本地推理只认 .gguf" >&2 ;;
esac

# 先确认包已安装且可 run-as（不可调试的包 run-as 会失败，早报比晚报好）
if ! "$ADB" shell run-as "$PKG" true >/dev/null 2>&1; then
  echo "[错误] 无法以 $PKG 的身份执行（run-as 失败）。三种可能：" >&2
  echo "        1) 包没装；2) 装的是 release 包（run-as 只对 debuggable 有效）；" >&2
  echo "        3) 包名不对（LM 轨是 ${PKG}，正式线是 org.openrailfanai.app）" >&2
  exit 1
fi

SIZE_MB=$(ls -l "$SRC" | awk '{printf "%.0f", $5/1048576}')
echo "==> 推送 $(basename "$SRC")（${SIZE_MB} MB）"
# 两跳：先到 /data/local/tmp（adb 能写），再用 run-as 归位（App 属主）
TMP="/data/local/tmp/$(basename "$SRC")"
"$ADB" push "$SRC" "$TMP"
"$ADB" shell run-as "$PKG" mkdir -p files/models
"$ADB" shell run-as "$PKG" cp "$TMP" files/models/
"$ADB" shell rm -f "$TMP"

echo "==> 设备上的模型："
# shellcheck disable=SC2016
"$ADB" shell run-as "$PKG" ls -l files/models/
echo
echo "重启 App 即可生效（本地推理在启动时发现模型并拉起 llama-server）："
echo "    $ADB shell am force-stop $PKG && $ADB shell am start -n $PKG/org.openrailfanai.app.MainActivity"
