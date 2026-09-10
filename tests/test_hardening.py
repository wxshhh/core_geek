"""工作包 31 测试：容错与超时加固。

Given/When/Then 风格；运行方式（二选一）：
    python3 -m pytest tests/ -q
    python3 tests/test_hardening.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from future_war.config import Config  # noqa: E402
from future_war.models import ParseError, parse_request  # noqa: E402
from future_war.observability.round_metrics import RoundObserver  # noqa: E402
from future_war.server import create_server  # noqa: E402
from future_war.strategy import StrategyBot  # noqa: E402

MALFORMED_BODIES = [
    b"",
    b"not json",
    b"[]",
    b"{}",
    b'{"roundNo": "x"}',
    b"\x00\xff\xfe",
    b"{" * 2000,
    b'{"roundNo": 1, "mapInfo": null, "teamOur": null}',
]


def _config() -> Config:
    return Config(
        data={
            "log": {"level": "EVENT", "trace_enabled": False},
            "features": {"replay_enabled": False, "metric_line_enabled": False},
        },
        profile="t",
        commit="c",
        config_hash="h",
    )


def _post(port: int, body: bytes, timeout: float = 5.0) -> tuple[int, bytes, float]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/", data=body, method="POST"
    )
    start = time.monotonic()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read()
    return response.status, payload, time.monotonic() - start


def _with_server(fn) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config = _config()
        observer = RoundObserver.from_config(config, log_dir=tmp, match_name="hard")
        server = create_server(0, observer, StrategyBot(config))
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            fn(port)
        finally:
            server.shutdown()
            server.server_close()
            observer.close()


def test_server_returns_valid_json_for_malformed_bodies() -> None:
    """Given 各类畸形请求体，When POST，Then 均返回合法 JSON 且 <5s。"""

    def scenario(port: int) -> None:
        for body in MALFORMED_BODIES:
            status, payload, elapsed = _post(port, body)
            assert status == 200
            assert json.loads(payload)["roleCommandMap"] is not None
            assert elapsed < 5.0

    _with_server(scenario)


def test_server_survives_concurrent_requests() -> None:
    """Given 并发请求，When 同时 POST，Then 全部成功且进程存活。"""
    body = (SRC_DIR.parent / "docs" / "request.txt").read_bytes()
    errors: list[Exception] = []

    def scenario(port: int) -> None:
        def worker() -> None:
            try:
                status, payload, _ = _post(port, body)
                assert status == 200
                json.loads(payload)
            except Exception as exc:  # noqa: BLE001 — 收集并发失败
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(24)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert not errors

    _with_server(scenario)


def test_bot_call_never_raises_on_broken_request() -> None:
    """Given 结构非法/空内容请求，When 解析后调 Bot，Then 仅抛可捕获 ParseError 或正常返回。"""
    bot = StrategyBot()
    for raw in ({}, {"roundNo": 1}, {"roundNo": 1, "mapInfo": {}, "teamOur": {}}):
        try:
            request = parse_request(raw)
        except ParseError:
            continue  # 解析层按设计抛可捕获错误，server 已捕获并降级
        response = bot(request)
        assert response.roleCommandMap is not None


def main() -> int:
    """零依赖测试运行器：执行全部 test_* 函数并报告。"""
    test_funcs = [
        obj
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    failed = 0
    for test in test_funcs:
        try:
            test()
        except Exception as exc:  # noqa: BROAD_EXCEPT_OK — 运行器顶层边界：收集失败并继续
            failed += 1
            print(f"FAIL {test.__name__}: {exc!r}", file=sys.stderr)
        else:
            print(f"PASS {test.__name__}")
    print(f"{len(test_funcs) - failed}/{len(test_funcs)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
