"""自检脚本：在目标机器（判题/对战平台）上验证 bot 能否启动并正确响应。

用法::

    python3 scripts/selfcheck.py

依次检查：Python 版本、包导入、服务启动、请求/响应。全通过退出码 0，
否则非 0 并打印失败原因——用于快速定位「对战没数据」的根因。
"""

from __future__ import annotations

import json
import os
import platform
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

_results: list[bool] = []


def _check(ok: bool, message: str) -> None:
    _results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {message}")


def _run_server_check() -> None:
    from future_war.config import load_config
    from future_war.observability.round_metrics import RoundObserver
    from future_war.server import create_server
    from future_war.strategy import StrategyBot

    with tempfile.TemporaryDirectory() as tmp:
        config = load_config()
        observer = RoundObserver.from_config(config, log_dir=tmp, match_name="selfcheck")
        server = create_server(0, observer, StrategyBot(config))
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            body = (ROOT / "docs" / "request.txt").read_bytes()
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/", data=body, method="POST"
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = json.loads(response.read())
            _check(response.status == 200, "HTTP 200")
            _check(isinstance(payload.get("roleCommandMap"), dict), "roleCommandMap 存在")
            _check(bool(payload.get("roleCommandMap")), f"返回非空指令（{len(payload['roleCommandMap'])} 条）")
        finally:
            server.shutdown()
            server.server_close()
            observer.close()


def main() -> int:
    print(f"Python : {sys.version.split()[0]}")
    print(f"平台   : {platform.platform()}")
    print(f"cwd    : {os.getcwd()}")
    print(f"仓库根 : {ROOT}")
    print("-" * 48)
    _check(sys.version_info >= (3, 10), "Python >= 3.10")
    try:
        import future_war.server  # noqa: F401
        import future_war.strategy  # noqa: F401
        _check(True, "import future_war.* 成功")
    except Exception as exc:  # noqa: BLE001 — 自检就是要暴露导入错误
        _check(False, f"import future_war.* 失败：{exc!r}")
        print("SELFCHECK FAILED")
        return 1
    try:
        _run_server_check()
    except Exception as exc:  # noqa: BLE001 — 自检就是要暴露运行错误
        _check(False, f"服务启动/请求失败：{exc!r}")
    print("-" * 48)
    ok = all(_results)
    print("SELFCHECK", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
