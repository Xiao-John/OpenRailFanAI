#!/usr/bin/env bash
# 生成 release 签名密钥库（只需做一次；之后所有正式包都用它签名）。
#
# 为什么必须自己签名：release 包不能像 debug 包那样用自动生成的调试密钥，
# 否则无法升级安装（签名不一致会被系统拒绝），也无法上架任何应用商店。
#
# 产物（**都不进仓库**，.gitignore 已覆盖）：
#   .android-build/keystore/railfanai.jks   密钥库本体
#   android/keystore.properties             构建时读取的配置（含口令）
#
# ⚠️ 密钥库一旦丢失，已发布的包将**永远无法升级**（签名不同）。请自行备份。
#
# 用法：
#   bash scripts/android/make-keystore.sh                 # 交互式输入口令
#   STORE_PASS=xxx KEY_PASS=xxx bash scripts/android/make-keystore.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
KEYTOOL="$ROOT/.android-build/jdk17/Contents/Home/bin/keytool"
OUT_DIR="$ROOT/.android-build/keystore"
STORE="$OUT_DIR/railfanai.jks"
PROPS="$ROOT/android/keystore.properties"
ALIAS="${KEY_ALIAS:-railfanai}"

[ -x "$KEYTOOL" ] || { echo "找不到 keytool，请先跑 scripts/android/setup-toolchain.sh" >&2; exit 1; }

if [ -f "$STORE" ]; then
  echo "密钥库已存在：$STORE"
  echo "如需重建，请先自行备份并删除它（重建会导致已发布包无法升级）。"
  exit 0
fi

mkdir -p "$OUT_DIR"

if [ -z "${STORE_PASS:-}" ]; then
  read -r -s -p "请输入密钥库口令（至少 6 位）: " STORE_PASS; echo
  read -r -s -p "请再次输入: " STORE_PASS2; echo
  [ "$STORE_PASS" = "$STORE_PASS2" ] || { echo "两次输入不一致" >&2; exit 1; }
fi
KEY_PASS="${KEY_PASS:-$STORE_PASS}"

"$KEYTOOL" -genkeypair -v \
  -keystore "$STORE" \
  -alias "$ALIAS" \
  -keyalg RSA -keysize 4096 -validity 10000 \
  -storepass "$STORE_PASS" -keypass "$KEY_PASS" \
  -dname "CN=OpenRailFanAI, OU=Community, O=OpenRailFanAI, L=, ST=, C=CN"

cat > "$PROPS" <<EOF
# 由 scripts/android/make-keystore.sh 生成 —— **含口令，已在 .gitignore 中**
storeFile=$STORE
storePassword=$STORE_PASS
keyAlias=$ALIAS
keyPassword=$KEY_PASS
EOF
chmod 600 "$PROPS" "$STORE"

echo
echo "✓ 密钥库：$STORE"
echo "✓ 配置  ：${PROPS}（权限 600）"
echo
echo "请务必自行备份这两个文件：丢失后将无法为已发布的包发布升级版本。"
