"""对战平台启动入口：``python main3.py <port>``。

平台在项目根目录运行本文件，并把端口作为第一个参数传入；本文件启动 HTTP 服务
监听 ``0.0.0.0:<port>``（等价于 ``bash run.sh <port>``）。启动/导入失败会写入
``logs/run.log``，便于平台不显示 stdout/stderr 时定位问题。
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


_log(f"main3.py start argv={sys.argv[1:]!r} python={sys.version.split()[0]} cwd={os.getcwd()!r}")
_bootstrap()

try:
    from future_war.server import main  # noqa: E402  — 必须在 _bootstrap 之后导入
except Exception:  # noqa: BLE001 — 记录完整导入错误后原样抛出
    _log("IMPORT FAILED:\n" + traceback.format_exc())
    raise


def _entry_argv() -> list[str]:
    """读取第一个参数作为 port（缺省交给 server 用 8080）。"""
    port = sys.argv[1] if len(sys.argv) > 1 else None
    return [port] if port else []


if __name__ == "__main__":
    _log(f"calling server.main port={sys.argv[1] if len(sys.argv) > 1 else '<default>'}")
    raise SystemExit(main(_entry_argv()))
