#!/usr/bin/env bash
# Unix 便捷入口：委托给跨平台的 Python 打包脚本。
# Windows 请直接运行： python scripts\package.py
#
#   bash scripts/package.sh            # 用 VERSION 文件里的版本
#   bash scripts/package.sh 0.2.0      # 临时覆盖版本
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$HERE/package.py" "$@"

