#!/usr/bin/env bash
# 编译 NPU 可行性探测程序，产物放进 jniLibs（与 libllamaserver.so 同一套路）。
#
# 为什么产物要叫 `libnpuprobe.so`：
#   Android 10+ 的 W^X 只允许 `nativeLibraryDir`（只读）里的文件被执行，
#   而只有被 AGP 当作**原生库**打包的文件才会落到那里 —— 所以可执行文件
#   必须以 `lib<name>.so` 命名。同一个坑见 scripts/android/build-llama.sh 顶部。
#
# 它**不依赖 Hexagon SDK**：只 dlopen/dlsym，所以本机（没装 Docker/SDK）就能编。
#
# 用法：bash scripts/android/build-npu-probe.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BUILD_DIR="$ROOT/.android-build"
SDK="$BUILD_DIR/sdk"
SRC="$ROOT/scripts/android/npu-probe/npu_probe.c"
OUT_DIR="$ROOT/android/app/src/lm/jniLibs/arm64-v8a"
OUT_SO="$OUT_DIR/libnpuprobe.so"

NDK_DIR="$(ls -d "$SDK"/ndk/* 2>/dev/null | sort -V | tail -1 || true)"
[ -n "$NDK_DIR" ] || { echo "[错误] 找不到 NDK。先跑：bash scripts/android/setup-toolchain.sh" >&2; exit 1; }

CC="$(ls "$NDK_DIR"/toolchains/llvm/prebuilt/*/bin/aarch64-linux-android24-clang 2>/dev/null | head -1)"
[ -n "$CC" ] || { echo "[错误] 找不到 NDK clang（${NDK_DIR}）" >&2; exit 1; }

echo "NDK = $(basename "$NDK_DIR")"
echo "CC  = $CC"
mkdir -p "$OUT_DIR"

# -fPIE/-pie：Android 5.0+ 只允许 PIE 可执行文件（否则 exec 直接被拒）
"$CC" -O2 -fPIE -pie -Wall -Wextra -o "$OUT_SO" "$SRC" -ldl

STRIP="$(ls "$NDK_DIR"/toolchains/llvm/prebuilt/*/bin/llvm-strip 2>/dev/null | head -1)"
[ -n "$STRIP" ] && "$STRIP" --strip-unneeded "$OUT_SO"

echo
echo "==> 产物：${OUT_SO#"$ROOT/"}"
ls -l "$OUT_SO" | awk '{printf "    大小  %.0f KB\n", $5/1024}'
echo "    架构  $(file -b "$OUT_SO" | cut -d, -f1-2)"
READELF="$(ls "$NDK_DIR"/toolchains/llvm/prebuilt/*/bin/llvm-readelf 2>/dev/null | head -1)"
[ -n "$READELF" ] && echo "    依赖  $("$READELF" -d "$OUT_SO" | grep NEEDED | sed 's/.*\[\(.*\)\]/\1/' | tr '\n' ' ')"
echo
echo "下一步：bash scripts/android/build.sh --lm -PincludeDict=true assembleRelease"
