#!/usr/bin/env bash
# 《未来战争》v1.0 bot 启动脚本：bash run.sh <port>
# 监听 0.0.0.0:<port>；port 缺省 8080（docs/接口文档.md 样例：bash run.sh port）
set -euo pipefail

PORT="${1:-8080}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 优先使用仓库内虚拟环境，否则回退系统 python3（本项目运行时仅依赖标准库）
if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
    PYTHON="$ROOT_DIR/.venv/bin/python"
elif [ -x "$ROOT_DIR/venv/bin/python" ]; then
    PYTHON="$ROOT_DIR/venv/bin/python"
else
    PYTHON="python3"
fi

export PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:${PYTHONPATH}}"
exec "$PYTHON" -m future_war.server "$PORT"
