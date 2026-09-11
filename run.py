"""跨平台启动入口：《未来战争》bot（Windows / macOS / Linux 通用）。

用法::

    python run.py <port> [--profile <name>]

等价于 `bash run.sh <port>`：把仓库 ``src/`` 加入 ``sys.path`` 后启动 HTTP 服务。
**不依赖 PYTHONPATH 或 shell 差异**（Windows 上尤其重要——避免 `python3` 缺失、
POSIX 路径不被 Windows Python 识别等问题）。
"""

from __future__ import annotations

import os
import sys


def _bootstrap() -> None:
    """把仓库 src/ 加入 import 路径（基于本文件位置，与当前工作目录无关）。"""
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
    if src not in sys.path:
        sys.path.insert(0, src)


_bootstrap()

from future_war.server import main  # noqa: E402  — 必须在 _bootstrap 之后导入

if __name__ == "__main__":
    raise SystemExit(main())
