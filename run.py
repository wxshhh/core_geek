"""跨平台启动入口：《未来战争》bot（Windows / macOS / Linux 通用）。

用法::

    python run.py <port> [--profile <name>]

等价于 `bash run.sh <port>`：把仓库 ``src/`` 加入 ``sys.path`` 后启动 HTTP 服务。
**不依赖 PYTHONPATH 或 shell 差异**。启动/导入失败会写入 ``logs/run.log``，
便于平台不显示 stdout/stderr 时定位问题。
"""

from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime, timezone


def _log(message: str) -> None:
    """把启动诊断写入 logs/run.log（写失败则静默）。"""
    try:
        logs = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
        os.makedirs(logs, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(os.path.join(logs, "run.log"), "a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] {message}\n")
    except OSError:
        pass


def _bootstrap() -> None:
    """把仓库 src/ 加入 import 路径（基于本文件位置，与当前工作目录无关）。"""
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
    if src not in sys.path:
        sys.path.insert(0, src)


_log(f"run.py start argv={sys.argv[1:]!r} python={sys.version.split()[0]} cwd={os.getcwd()!r}")
_bootstrap()

try:
    from future_war.server import main  # noqa: E402  — 必须在 _bootstrap 之后导入
except Exception:  # noqa: BLE001 — 记录完整导入错误后原样抛出
    _log("IMPORT FAILED:\n" + traceback.format_exc())
    raise

if __name__ == "__main__":
    _log("calling server.main()")
    raise SystemExit(main())

