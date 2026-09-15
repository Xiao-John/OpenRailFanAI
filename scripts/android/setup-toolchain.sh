#!/usr/bin/env bash
# 安装 Android 构建工具链到**工作区内**（.android-build/），不写系统目录、不需要 sudo。
#
# 为什么自包含：DSH 文件沙箱只允许写工作区，且这样也不污染用户系统 ——
# 删掉 .android-build/ 即完全卸载。构建产物与 Gradle 缓存同样落在工作区内。
#
# 用法：
#   bash scripts/android/setup-toolchain.sh            # 装 JDK17 + Android SDK
#   bash scripts/android/setup-toolchain.sh --check    # 只检查现状
#
# 环境变量：
#   ANDROID_SETUP_PROXY  下载 JDK 用的代理（Temurin 通过 github.com 分发，
#                        境内直连常被拦；本机实测需 http://127.0.0.1:7897）
#   ANDROID_API          目标平台，默认 35
#   ANDROID_BUILD_TOOLS  build-tools 版本，默认 35.0.0
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BUILD_DIR="$ROOT/.android-build"
JDK_DIR="$BUILD_DIR/jdk17"
SDK_DIR="$BUILD_DIR/sdk"
DL_DIR="$BUILD_DIR/downloads"

ANDROID_API="${ANDROID_API:-35}"
ANDROID_BUILD_TOOLS="${ANDROID_BUILD_TOOLS:-35.0.0}"
CMDLINE_BUILD="${CMDLINE_BUILD:-13114758}"
PROXY="${ANDROID_SETUP_PROXY:-}"
JDK_URL="https://api.adoptium.net/v3/binary/latest/17/ga/mac/aarch64/jdk/hotspot/normal/eclipse"
CMDLINE_URL="https://dl.google.com/android/repository/commandlinetools-mac-${CMDLINE_BUILD}_latest.zip"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()  { printf '  \033[32m✓\033[0m %s\n' "$*"; }
die() { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

# ---- 现状检查 ----
sdkmanager_bin="$SDK_DIR/cmdline-tools/latest/bin/sdkmanager"
if [ "${1:-}" = "--check" ]; then
  printf '工作区      : %s\n' "$ROOT"
  printf 'JDK17       : %s\n' "$([ -x "$JDK_DIR/Contents/Home/bin/java" ] && "$JDK_DIR/Contents/Home/bin/java" -version 2>&1 | head -1 || echo '未安装')"
  printf 'Android SDK : %s\n' "$([ -x "$sdkmanager_bin" ] && echo "$SDK_DIR" || echo '未安装')"
  printf 'Gradle      : %s\n' "$([ -x "$BUILD_DIR/gradle-8.11.1/bin/gradle" ] && echo "$BUILD_DIR/gradle-8.11.1" || echo '未安装')"
  # sdkmanager 是 Java 程序：必须先把 JAVA_HOME 指到工作区内的 JDK17，
  # 否则会去用系统里的旧 JDK 并报 "This tool requires JDK 17 or later"
  if [ -x "$JDK_DIR/Contents/Home/bin/java" ]; then
    export JAVA_HOME="$JDK_DIR/Contents/Home"
  fi
  [ -x "$sdkmanager_bin" ] && "$sdkmanager_bin" --list_installed 2>/dev/null | sed 's/^/  /' | head -20
  exit 0
fi

mkdir -p "$BUILD_DIR" "$DL_DIR"

# ---- 1) JDK 17 ----
if [ -x "$JDK_DIR/Contents/Home/bin/java" ]; then
  ok "JDK 17 已存在，跳过下载"
else
  say "下载 JDK 17（Temurin）"
  tarball="$DL_DIR/jdk17.tar.gz"
  # Temurin 走 github.com 分发；境内直连常被拦，故支持代理
  curl_args=(-fL --retry 3 --retry-delay 2 -o "$tarball")
  [ -n "$PROXY" ] && curl_args+=(-x "$PROXY") && ok "使用代理 $PROXY"
  curl "${curl_args[@]}" "$JDK_URL" || die "JDK 下载失败（可设 ANDROID_SETUP_PROXY=http://127.0.0.1:7897）"
  ok "已下载 $(du -h "$tarball" | cut -f1)"
  mkdir -p "$JDK_DIR"
  tar -xzf "$tarball" -C "$JDK_DIR" --strip-components=1
  "$JDK_DIR/Contents/Home/bin/java" -version 2>&1 | head -1 | sed 's/^/  /'
  ok "JDK 17 就绪（$(du -sh "$JDK_DIR" | cut -f1)）"
fi
export JAVA_HOME="$JDK_DIR/Contents/Home"

# ---- 2) Android cmdline-tools ----
if [ -x "$sdkmanager_bin" ]; then
  ok "cmdline-tools 已存在，跳过下载"
else
  say "下载 Android cmdline-tools"
  zip="$DL_DIR/cmdline-tools.zip"
  curl -fL --retry 3 -o "$zip" "$CMDLINE_URL" || die "cmdline-tools 下载失败"
  mkdir -p "$SDK_DIR/cmdline-tools"
  rm -rf "$SDK_DIR/cmdline-tools/latest" "$SDK_DIR/cmdline-tools/tmp"
  # zip 内层目录名是 cmdline-tools/，sdkmanager 要求最终落在 cmdline-tools/latest/
  unzip -q "$zip" -d "$SDK_DIR/cmdline-tools/tmp"
  mv "$SDK_DIR/cmdline-tools/tmp/cmdline-tools" "$SDK_DIR/cmdline-tools/latest"
  rmdir "$SDK_DIR/cmdline-tools/tmp"
  ok "cmdline-tools 就绪"
fi
export ANDROID_HOME="$SDK_DIR"
export ANDROID_SDK_ROOT="$SDK_DIR"
# sdkmanager 默认把下载缓存与许可证写进 $HOME/.android。工作区外的写入会被文件沙箱拒绝，
# 表现为 "IO exception while downloading manifest" + NoSuchFileException —— 极易误判为网络故障。
export ANDROID_USER_HOME="$BUILD_DIR/android-home"
export ANDROID_PREFS_ROOT="$ANDROID_USER_HOME"     # 旧版 cmdline-tools 用的变量名
mkdir -p "$ANDROID_USER_HOME"

# ---- 3) Gradle ----
# 必须一并安装：build.sh 直接用这里的 Gradle（仓库刻意不带 wrapper，避免往 git 塞二进制）。
# services.gradle.org 会 302 到 github.com 的 release —— 境内直连通常被拦，走代理又极慢
# （实测 46KB/s，130MB 要 47 分钟），所以优先国内镜像（实测 11MB/s）。
GRADLE_VERSION="${GRADLE_VERSION:-8.11.1}"
GRADLE_DIR="$BUILD_DIR/gradle-$GRADLE_VERSION"
if [ -x "$GRADLE_DIR/bin/gradle" ]; then
  ok "Gradle $GRADLE_VERSION 已存在，跳过下载"
else
  say "下载 Gradle $GRADLE_VERSION"
  gz="$DL_DIR/gradle.zip"
  got=""
  for url in \
    "https://mirrors.cloud.tencent.com/gradle/gradle-$GRADLE_VERSION-bin.zip" \
    "https://mirrors.huaweicloud.com/gradle/gradle-$GRADLE_VERSION-bin.zip" \
    "https://services.gradle.org/distributions/gradle-$GRADLE_VERSION-bin.zip"
  do
    printf '  尝试 %s\n' "$url"
    extra=()
    case "$url" in *services.gradle.org*) [ -n "$PROXY" ] && extra=(-x "$PROXY");; esac
    if curl -fL --retry 2 --connect-timeout 20 "${extra[@]}" -o "$gz" "$url" 2>/dev/null; then
      got="$url"; break
    fi
  done
  [ -n "$got" ] || die "Gradle 下载失败（三个源都不可用）"
  ok "已下载（$(du -h "$gz" | cut -f1)）"
  rm -rf "$GRADLE_DIR"
  unzip -q "$gz" -d "$BUILD_DIR" || die "Gradle 解压失败"
  [ -x "$GRADLE_DIR/bin/gradle" ] || die "解压后未找到 $GRADLE_DIR/bin/gradle"
fi

# ---- 4) SDK 组件 ----
say "安装 SDK 组件（platform-tools / platforms;android-$ANDROID_API / build-tools;${ANDROID_BUILD_TOOLS}）"
# 许可证必须显式接受，否则构建会在下载阶段失败
yes | "$sdkmanager_bin" --licenses > "$BUILD_DIR/licenses.log" 2>&1 || true
# 注意：不要把 sdkmanager 输出接进 `| tail` 而不管退出码 —— 管道会掩盖失败（本项目刚踩过）
install_log="$BUILD_DIR/sdkmanager-install.log"
if ! "$sdkmanager_bin" --install \
      "platform-tools" \
      "platforms;android-$ANDROID_API" \
      "build-tools;$ANDROID_BUILD_TOOLS" > "$install_log" 2>&1; then
  tail -20 "$install_log" >&2
  die "SDK 组件安装失败（完整日志：${install_log}）"
fi
tail -3 "$install_log" | sed 's/^/  /'

# ---- 4) 自检 ----
say "自检"
"$sdkmanager_bin" --list_installed 2>/dev/null | sed 's/^/  /' | head -12
[ -d "$SDK_DIR/platforms/android-$ANDROID_API" ] || die "platforms;android-$ANDROID_API 未安装成功"
[ -d "$SDK_DIR/build-tools/$ANDROID_BUILD_TOOLS" ] || die "build-tools;$ANDROID_BUILD_TOOLS 未安装成功"
ok "工具链就绪，总占用 $(du -sh "$BUILD_DIR" | cut -f1)"
cat <<EOF

后续构建请先导入环境（或直接用 scripts/android/build.sh）：
  export JAVA_HOME="$JAVA_HOME"
  export ANDROID_HOME="$ANDROID_HOME"
  export GRADLE_USER_HOME="$BUILD_DIR/gradle-home"     # 缓存也留在工作区内
  # Gradle 本体：$BUILD_DIR/gradle-8.11.1/bin/gradle
EOF
