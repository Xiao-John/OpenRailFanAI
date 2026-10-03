#!/usr/bin/env bash
# 交叉编译 llama.cpp 的 llama-server，供 Android 一体化包在**设备上**跑本地推理。
#
# 为什么要它：`scripts/setup_local_model.sh --target=android` 只产出"配置"。
# 设备上真正把 token 算出来的是这个二进制。没有它，所谓"本地模型版"就只是个标签。
#
# 产物：android/app/src/lm/jniLibs/arm64-v8a/libllamaserver.so
#
# 为什么**故意叫 `lib*.so`**：这不是笔误，是 Android 的硬要求。
#   Android 10+ 禁止从可写目录 exec 二进制（W^X）；唯一允许 exec 的位置是只读的
#   `nativeLibraryDir`，而只有被 AGP 当作**原生库**打包的文件才会落到那里。
#   所以可执行文件必须以 `lib<name>.so` 命名并放进 jniLibs。
#   配套地，`build.gradle.kts` 在 LM 轨上把 `useLegacyPackaging` 置 true ——
#   否则原生库只在 APK 内 mmap、不落地成真实文件，照样 exec 不了。
#   这两处是**一对**，改一个必须改另一个。
#
# 为什么第一版只编 CPU（不开 Vulkan/OpenCL）：
#   1) 它能跑在**每一台**设备上，而 Vulkan 后端要额外的头文件与 shader 编译工具链；
#   2) 跨设备封测要的是**可比性** —— 大家跑的是同一套 kernel，差异才反映设备本身；
#   3) 端到端链路（子进程 / 端口 / 供应商接线）先用最简单的后端打通，
#      免得把"接线错了"和"GPU 后端编错了"两件事搅在一起排障。
#   Adreno 后端（`-ngl`）作为下一步，见 docs/android.md。
#
# 用法（在仓库根执行）：
#   bash scripts/android/build-llama.sh              # 克隆/复用源码并编译
#   LLAMA_REF=b9982 bash scripts/android/build-llama.sh   # 指定 llama.cpp 版本
#   bash scripts/android/build-llama.sh --clean      # 删掉源码与构建目录重来
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BUILD_DIR="$ROOT/.android-build"
SDK="$BUILD_DIR/sdk"
OUT_DIR="$ROOT/android/app/src/lm/jniLibs/arm64-v8a"
OUT_SO="$OUT_DIR/libllamaserver.so"

NDK_DIR="$(ls -d "$SDK"/ndk/* 2>/dev/null | sort -V | tail -1 || true)"
CMAKE_BIN="$(ls -d "$SDK"/cmake/*/bin 2>/dev/null | sort -V | tail -1 || true)"
SRC_DIR="$BUILD_DIR/llama.cpp"
# **构建目录按模式分开**：CMake 会缓存上次的配置，`--vulkan` 与 `--cpu-only` 共用一个目录时，
# 后一次会直接沿用前一次的缓存 —— 实测 `--cpu-only` 报出来的是"libvulkan 已链接"，
# 也就是**打着 CPU 版旗号产出了 Vulkan 版二进制**。这种"配置撒谎"比构建失败更坏。
WORK_DIR=""
LLAMA_REPO="${LLAMA_REPO:-https://github.com/ggml-org/llama.cpp.git}"
LLAMA_REF="${LLAMA_REF:-master}"

# 默认**带 Vulkan**（GPU 卸载）。真机实测：纯 CPU 的 2B 在 8 Elite Gen 5 上
# 一次回答要 49 秒，而 Qualcomm 自家优化栈在同一颗 SoC 上是 38.6 tok/s ——
# 差的那 2.5–4 倍就是"用没用 GPU"。所以 CPU-only 从"第一版默认"降级为**排障选项**。
VULKAN=1
for a in "$@"; do
  case "$a" in
    --clean)
      echo "==> 清理源码与构建目录"
      rm -rf "$SRC_DIR" "$BUILD_DIR"/llama-build-arm64* "$OUT_SO"
      exit 0 ;;
    --cpu-only) VULKAN=0 ;;
    --vulkan)   VULKAN=1 ;;
    -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
    *) echo "[错误] 未知参数：${a}（可用：--clean / --vulkan / --cpu-only）" >&2; exit 1 ;;
  esac
done

WORK_DIR="$BUILD_DIR/llama-build-arm64$([ "$VULKAN" = "1" ] || echo "-cpu")"

[ -n "$NDK_DIR" ] || { echo "[错误] 找不到 NDK。先跑：bash scripts/android/setup-toolchain.sh 或 sdkmanager 'ndk;27.2.12479018'" >&2; exit 1; }
[ -n "$CMAKE_BIN" ] || { echo "[错误] 找不到 SDK 里的 cmake。sdkmanager 'cmake;3.31.6'" >&2; exit 1; }

echo "NDK   = $(basename "$NDK_DIR")"
echo "CMAKE = $CMAKE_BIN/cmake"
echo "源码  = ${SRC_DIR}（ref=${LLAMA_REF}）"
echo

export PATH="$CMAKE_BIN:$PATH"          # 让 cmake 找到同目录的 ninja

if [ ! -d "$SRC_DIR/.git" ]; then
  echo "==> 克隆 llama.cpp"
  # --depth 1 不带分支：既快又能配合 LLAMA_REF 检出指定 tag
  git clone --depth 1 "$LLAMA_REPO" "$SRC_DIR"
fi
echo "==> 检出 $LLAMA_REF"
git -C "$SRC_DIR" fetch --depth 1 origin "$LLAMA_REF" 2>/dev/null || true
git -C "$SRC_DIR" checkout -q FETCH_HEAD 2>/dev/null || git -C "$SRC_DIR" checkout -q "$LLAMA_REF"
echo "    提交：$(git -C "$SRC_DIR" rev-parse --short HEAD)"

GLSLC="$(command -v glslc || true)"
if [ "$VULKAN" = "1" ] && [ -z "$GLSLC" ]; then
  echo "[错误] 要编 Vulkan 后端但没有 glslc。先装：brew install shaderc" >&2
  echo "        （也可以 --cpu-only 编一个纯 CPU 版排障用）" >&2
  exit 1
fi

# SPIRV-Headers：llama.cpp 的 ggml-vulkan 里有 `find_package(SPIRV-Headers CONFIG REQUIRED)`，
# 但**整个 CMakeLists 再没引用过它的 target** —— 是个残留依赖，只要求"能 find 到"。
# 而 brew 的 spirv-headers **只装头文件、不装 CMake config**，于是默认必然 configure 失败。
#
# 这里按需生成一份最小 config（写进构建目录，不污染 brew 前缀、不进仓库）。
# 它是安全的：如果真的需要那些头文件，编译阶段会立刻因为找不到头而失败 —— **不会静默出错**。
SPIRV_ARGS=()
if [ "$VULKAN" = "1" ]; then
  SPIRV_PREFIX="$(brew --prefix spirv-headers 2>/dev/null || true)"
  if [ -n "$SPIRV_PREFIX" ] && [ -d "$SPIRV_PREFIX/include/spirv" ]; then
    if ! find "$SPIRV_PREFIX" -name "SPIRV-HeadersConfig.cmake" 2>/dev/null | grep -q .; then
      SPIRV_DIR="$BUILD_DIR/cmake/SPIRV-Headers"
      mkdir -p "$SPIRV_DIR"
      cat > "$SPIRV_DIR/SPIRV-HeadersConfig.cmake" <<EOF
# 由 scripts/android/build-llama.sh 生成（见该脚本里的说明）
set(SPIRV-Headers_FOUND TRUE)
set(SPIRV-Headers_INCLUDE_DIR "$SPIRV_PREFIX/include")
if (NOT TARGET SPIRV-Headers::SPIRV-Headers)
  add_library(SPIRV-Headers::SPIRV-Headers INTERFACE IMPORTED)
  set_target_properties(SPIRV-Headers::SPIRV-Headers PROPERTIES
    INTERFACE_INCLUDE_DIRECTORIES "$SPIRV_PREFIX/include")
endif()
EOF
      echo "    SPIRV-Headers = ${SPIRV_PREFIX}（brew 未提供 config，已生成最小配置）"
    else
      SPIRV_DIR="$SPIRV_PREFIX"
    fi
    SPIRV_ARGS=("-DSPIRV-Headers_DIR=$SPIRV_DIR")
  else
    echo "[错误] 要编 Vulkan 但找不到 spirv-headers。先装：brew install spirv-headers" >&2
    exit 1
  fi
fi

# vulkan.hpp（C++ 绑定）：**NDK 只带 C 头 `vulkan.h`，不带 C++ 绑定**，
# 而 ggml-vulkan 的 ggml-vulkan-types.h 直接 include <vulkan/vulkan.hpp>。
# 不能把 brew 的 include 目录整个塞进去 —— 它的 vulkan.h 是 1.4，会盖掉 NDK 的平台版
# （1.3.275），可能引用设备 loader 上不存在的命令。所以做一个**只放 .hpp 的垫层**：
# 编译器在垫层里找不到 vulkan.h，自然继续去 sysroot 拿 NDK 的那份。
# ---- 主机头文件垫层 ----
#
# 交叉编译 Vulkan 后端要三样 NDK **不提供**的东西，全部只放头文件、不碰 NDK 的平台头：
#   1. vulkan.hpp（C++ 绑定）—— NDK 只带 C 头 vulkan.h
#   2. vk_video/*            —— vulkan_core.h 会 include 它，是**同级的另一个目录**
#   3. spirv/unified1/*.hpp  —— ggml-vulkan-types.h 直接 include，来自 SPIRV-Headers
#
# 三条都是实测撞出来的：
#   只链 vulkan.hpp → `VkGpaSessionCreateInfoAMD` unknown（1.4 的 hpp 配 1.3 的 h 不一致）
#   只链 vulkan/    → 'vk_video/...' file not found
#   只 find_package → 'spirv/unified1/spirv.hpp' file not found
#     （llama.cpp 只 find_package(SPIRV-Headers) 而**从不链接它的 target**，
#      上游那份 include 目录接不上，所以直接把头放进垫层最省事）
#
# **Vulkan 的 h 与 hpp 必须整份取同版本**（1.4）：Vulkan 头带 VK_HEADER_VERSION_COMPLETE
# 一致性检查，混版本必然报一屏 "too many errors"。代价是头 1.4 配设备的 1.3 loader ——
# llama.cpp 运行时用 vkGetInstanceProcAddr 取函数，取不到是空指针、并会读 apiVersion 降级，
# 最坏回落 CPU，而 `local_inference.detect_backend()` 会把实际后端暴露出来，不会静默。
SHIM="$BUILD_DIR/host-headers-shim"
VULKAN_CPP_ARGS=()
if [ "$VULKAN" = "1" ]; then
  VH_PREFIX="$(brew --prefix vulkan-headers 2>/dev/null || true)"
  SP_PREFIX="$(brew --prefix spirv-headers 2>/dev/null || true)"
  if [ -z "$VH_PREFIX" ] || [ ! -d "$VH_PREFIX/include/vulkan" ]; then
    echo "[错误] 要编 Vulkan 但缺 vulkan-headers。先装：brew install vulkan-headers" >&2
    exit 1
  fi
  if [ -z "$SP_PREFIX" ] || [ ! -d "$SP_PREFIX/include/spirv" ]; then
    echo "[错误] 要编 Vulkan 但缺 spirv-headers。先装：brew install spirv-headers" >&2
    exit 1
  fi
  rm -rf "$SHIM"; mkdir -p "$SHIM"
  for sub in "${VH_PREFIX}"/include/*/; do
    ln -sfn "${sub%/}" "$SHIM/$(basename "${sub%/}")"
  done
  ln -sfn "${SP_PREFIX}/include/spirv" "$SHIM/spirv"
  VULKAN_CPP_ARGS=("-DCMAKE_CXX_FLAGS=-I$SHIM")
  echo "    主机头垫层    ${SHIM}（vulkan + vk_video + spirv）"
fi

VULKAN_ARGS=()
if [ "$VULKAN" = "1" ]; then
  # 两个都必须显式给：
  #   GGML_VULKAN=ON                  —— 默认是 OFF，不开就等于没编 GPU 后端
  #   Vulkan_GLSLC_EXECUTABLE=<host>  —— glslc 是**主机**程序，而 Android 工具链默认
  #     只在 target root 里找程序；不显式指定的话 find_package 会找不到。
  # NDK 自带 vulkan/vulkan.h 与 libvulkan.so stub，运行时靠系统的 libvulkan.so。
  VULKAN_ARGS=(-DGGML_VULKAN=ON "-DVulkan_GLSLC_EXECUTABLE=$GLSLC")
  echo "==> 配置（arm64-v8a / android-24 / Vulkan 已开）"
  echo "    glslc = $GLSLC"
else
  echo "==> 配置（arm64-v8a / android-24 / **CPU-only**，仅排障用）"
fi
# 最低 API 级别：Vulkan 版必须 28（Android 9），CPU 版仍是 24。
#
# 为什么不能都定 24：NDK 各 API 级别的 libvulkan.so stub 里，
# `vkGetPhysicalDeviceFeatures2`（Vulkan **1.1** 引入）到 **api-28 才有**：
#     api-24: 无   api-26: 无   api-28: 有   api-29: 有
# 而 llama.cpp **直接链接 libvulkan.so**（不用 volk 之类的运行时动态加载），
# 所以链接期就要求这个符号存在，定 24 会以 `undefined symbol` 失败（实测）。
#
# 代价要说清楚：Vulkan 版在 Android 8 及以下**根本加载不起来**（不是慢，是起不来）。
# 判断是可接受的 —— 本地推理的靶子是 8G+ 内存的机型，那些基本都在 Android 10 以上；
# 真要覆盖老机型就用 `--cpu-only` 单独编一份（那份仍是 android-24）。
if [ "$VULKAN" = "1" ]; then
  ANDROID_API=28
else
  ANDROID_API=24
fi
echo "    最低 API       android-$ANDROID_API"

cmake -S "$SRC_DIR" -B "$WORK_DIR" -G Ninja \
  -DCMAKE_TOOLCHAIN_FILE="$NDK_DIR/build/cmake/android.toolchain.cmake" \
  -DANDROID_ABI=arm64-v8a \
  -DANDROID_PLATFORM="android-$ANDROID_API" \
  -DCMAKE_BUILD_TYPE=Release \
  -DLLAMA_BUILD_SERVER=ON \
  -DLLAMA_BUILD_TESTS=OFF \
  -DLLAMA_BUILD_EXAMPLES=OFF \
  -DLLAMA_CURL=OFF \
  -DGGML_OPENMP=OFF \
  -DBUILD_SHARED_LIBS=OFF \
  -DCMAKE_EXE_LINKER_FLAGS=-Wl,--gc-sections \
  "${VULKAN_ARGS[@]+"${VULKAN_ARGS[@]}"}" \
  "${SPIRV_ARGS[@]+"${SPIRV_ARGS[@]}"}" \
  "${VULKAN_CPP_ARGS[@]+"${VULKAN_CPP_ARGS[@]}"}" \
  2>&1 | tail -16

echo "==> 编译 llama-server（llama/ggml 全静态，只对 bionic 动态）"
cmake --build "$WORK_DIR" --target llama-server -j "$(sysctl -n hw.ncpu 2>/dev/null || nproc)"

BUILT="$(find "$WORK_DIR" -name 'llama-server' -type f -perm -u+x | head -1)"
[ -n "${BUILT}" ] || { echo "[错误] 没有产出 llama-server" >&2; exit 1; }

mkdir -p "$OUT_DIR"
cp "$BUILT" "$OUT_SO"
chmod 755 "$OUT_SO"
RAW_MB=$(ls -l "$OUT_SO" | awk '{printf "%.1f", $5/1048576}')

# **必须 strip**：Release 构建仍然带着完整调试符号，实测未 strip 是 **168.5 MB**，
# strip 后 12.8 MB —— 十几倍的差距，直接决定这个包能不能发出去。
STRIP="$(ls "$NDK_DIR"/toolchains/llvm/prebuilt/*/bin/llvm-strip 2>/dev/null | head -1)"
if [ -n "$STRIP" ]; then
  "$STRIP" --strip-unneeded "$OUT_SO"
else
  echo "[警告] 没找到 llvm-strip，二进制会带着调试符号（体积可能是十几倍）" >&2
fi

READELF="$(ls "$NDK_DIR"/toolchains/llvm/prebuilt/*/bin/llvm-readelf 2>/dev/null | head -1)"
echo
echo "==> 产物：${OUT_SO#"$ROOT/"}"
ls -l "$OUT_SO" | awk -v raw="$RAW_MB" '{printf "    大小        %.1f MB（strip 前 %s MB）\n", $5/1048576, raw}'
echo "    架构        $(file -b "$OUT_SO" | cut -d, -f1-2)"
if [ -n "$READELF" ]; then
  # 只应依赖 bionic 自带的三个库。出现别的（libc++_shared / libomp / libcurl…）
  # 说明链接配置跑偏了，装到手机上会以 "library not found" 起不来。
  echo "    动态依赖    $("$READELF" -d "$OUT_SO" | grep NEEDED | sed 's/.*\[\(.*\)\]/\1/' | tr '\n' ' ')"
  # 判定后端看 **NEEDED 里有没有 libvulkan.so**，不要 grep 符号名：
  # strip 之后符号表没了，第一版用 `strings | grep ggml_vulkan` 判，编出来了也报"无"。
  if "$READELF" -d "$OUT_SO" | grep -q "libvulkan.so"; then
    echo "    GPU 后端    ✔ libvulkan 已链接（运行时用 -ngl 99 卸载到 GPU）"
  else
    echo "    GPU 后端    ✘ 无（CPU-only 版，真机上会慢 2.5–4 倍）"
  fi
  echo "                （libm/libdl/libc 由 Android 提供；libvulkan 也由系统提供，Android 9+）"
fi
echo
echo "下一步：bash scripts/android/build.sh --lm -PincludeDict=true assembleDebug"
