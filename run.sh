#!/usr/bin/env bash
# 《未来战争》v1.0 bot 启动脚本：bash run.sh <port> [--profile <name>]
# 监听 0.0.0.0:<port>；port 缺省 8080（docs/接口文档.md 样例：bash run.sh port）
#
# 跨平台：自动探测可用的 Python（venv / python3 / python / py），
# 然后委托 run.py（不用 PYTHONPATH，避免 Windows 路径/命令名差异）。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

# 启动诊断写入 logs/run.log（平台可能不显示 stdout/stderr）
LOG_DIR="$ROOT_DIR/logs"
mkdir -p "$LOG_DIR" 2>/dev/null || true
log_line() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" >> "$LOG_DIR/run.log" 2>/dev/null || true; }
log_line "run.sh start args=$*"

find_python() {
    local candidate
    for candidate in \
        "$ROOT_DIR/.venv/Scripts/python.exe" \
        "$ROOT_DIR/.venv/bin/python" \
        "$ROOT_DIR/venv/Scripts/python.exe" \
        "$ROOT_DIR/venv/bin/python"; do
        if [ -x "$candidate" ]; then
            printf '%s' "$candidate"
            return 0
        fi
    done
    local name
    for name in python3 python py; do
        if command -v "$name" >/dev/null 2>&1; then
            printf '%s' "$name"
            return 0
        fi
    done
    return 1
}

if ! PYTHON="$(find_python)"; then
    log_line "ERROR: 未找到 Python（需 Python 3.10+）"
    echo "[future-war] ERROR: 未找到 Python（需 Python 3.10+）" >&2
    exit 1
fi

log_line "python=$PYTHON"
exec "$PYTHON" "$ROOT_DIR/run.py" "$@"
