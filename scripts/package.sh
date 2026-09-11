#!/usr/bin/env bash
# 打包脚本：把项目压缩为 dist/future-war-bot-<version>.tar.gz
#
# 版本统一在仓库根目录的 VERSION 文件管理（单一真源）；也可用参数覆盖：
#   bash scripts/package.sh            # 用 VERSION 文件里的版本
#   bash scripts/package.sh 0.2.0      # 临时覆盖版本
#
# 产物：dist/future-war-bot-<version>.tar.gz + 同名 .sha256 校验文件。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

VERSION_FILE="VERSION"
FILE_VERSION="$(tr -d '[:space:]' < "$VERSION_FILE" 2>/dev/null || true)"
VERSION="${1:-${FILE_VERSION:-0.0.0}}"
NAME="future-war-bot-${VERSION}"
DIST_DIR="$ROOT_DIR/dist"
ARCHIVE="$DIST_DIR/${NAME}.tar.gz"
COMMIT="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
BUILT_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

mkdir -p "$DIST_DIR"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/$NAME"

# 复制项目文件到暂存目录（排除开发/运行/工具产物）
rsync -a \
  --exclude='.git/' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='*.pyo' \
  --exclude='.pytest_cache/' \
  --exclude='.venv/' \
  --exclude='venv/' \
  --exclude='logs/' \
  --exclude='dist/' \
  --exclude='*.egg-info/' \
  --exclude='uv.lock' \
  --exclude='.DS_Store' \
  --exclude='.omo/evidence/' \
  --exclude='.omo/run-continuation/' \
  --exclude='.omo/boulder.json' \
  "$ROOT_DIR/" "$STAGE/$NAME/"

cat > "$STAGE/$NAME/BUILD_INFO.txt" <<EOF
name:    $NAME
version: $VERSION
commit:  $COMMIT
built:   $BUILT_AT
EOF

tar -czf "$ARCHIVE" -C "$STAGE" "$NAME"

( cd "$DIST_DIR" && shasum -a 256 "${NAME}.tar.gz" > "${NAME}.tar.gz.sha256" 2>/dev/null \
  || sha256sum "${NAME}.tar.gz" > "${NAME}.tar.gz.sha256" )

echo "打包完成："
echo "  版本：  $VERSION (源：$VERSION_FILE)"
echo "  提交：  $COMMIT"
echo "  压缩包：$ARCHIVE"
echo "  大小：  $(du -h "$ARCHIVE" | cut -f1)"
echo "  校验：  $ARCHIVE.sha256"
