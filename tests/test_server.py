"""集成测试：脚手架 HTTP 服务。

Given: 在临时端口 (0) 上启动的真实服务（无 mock）
When:  POST 判题器样式的请求体
Then:  HTTP 200 + 合法 Response JSON（含 roleCommandMap），进程存活，可干净关闭

运行方式（二选一）：
    python3 -m pytest tests/ -q
    python3 tests/test_server.py
"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.request
from pathlib import Path
from types import TracebackType
from typing import Final, TypeAlias

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from future_war.server import EMPTY_RESPONSE, create_server, resolve_port

REQUEST_FIXTURE: Final = Path(__file__).resolve().parents[1] / "docs" / "request.txt"

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


class RunningServer:
    """真实服务的线程化包装：绑定临时端口，退出时干净关闭。"""

    def __init__(self) -> None:
        self.server = create_server(0)
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True, name="test-judge-server"
        )

    def __enter__(self) -> RunningServer:
        self.thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    def post(self, body: bytes) -> tuple[int, dict[str, JsonValue]]:
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            status = response.status
            payload = json.loads(response.read().decode("utf-8"))
        return status, payload


def test_post_request_txt_returns_valid_judge_response() -> None:
    """Given docs/request.txt，When POST，Then 200 + 顶层三键齐全且值合法。"""
    with RunningServer() as server:
        status, payload = server.post(REQUEST_FIXTURE.read_bytes())
    assert status == 200
    assert set(payload.keys()) == {"roleCommandMap", "prompt", "executeCmd"}
    assert payload["roleCommandMap"] == {}
    assert payload["prompt"] == ""
    assert payload["executeCmd"] == ""


def test_malformed_body_returns_valid_response_and_server_survives() -> None:
    """Given 畸形 JSON 请求体，When POST，Then 200 合法 Response 且服务仍可服务。"""
    with RunningServer() as server:
        status_bad, payload_bad = server.post(b"not json")
        status_after, payload_after = server.post(REQUEST_FIXTURE.read_bytes())
    assert status_bad == 200
    assert status_after == 200
    assert payload_bad == payload_after
    assert payload_bad["roleCommandMap"] == {}


def test_server_creation_skips_reverse_dns_and_is_instant() -> None:
    """Given 创建服务，When server_bind 执行，Then 无反向 DNS 阻塞（启动 < 5s）。

    回归：HTTPServer.server_bind 的 getfqdn 反向解析在无 DNS 环境阻塞 30s+。
    """
    started = time.monotonic()
    server = create_server(0)
    try:
        elapsed = time.monotonic() - started
    finally:
        server.server_close()
    assert elapsed < 5.0


def test_request_round_trip_is_within_5_second_budget() -> None:
    """Given 服务已启动，When POST+响应，Then 端到端耗时 < 5s（任务书 §八）。"""
    with RunningServer() as server:
        started = time.monotonic()
        status, _ = server.post(REQUEST_FIXTURE.read_bytes())
        elapsed = time.monotonic() - started
    assert status == 200
    assert elapsed < 5.0


def test_resolve_port_defaults_when_absent() -> None:
    """Given 无端口参数，When 解析，Then 返回 8080。"""
    assert resolve_port(None) == 8080


def test_resolve_port_parses_integer_argument() -> None:
    """Given "18080"，When 解析，Then 返回 18080。"""
    assert resolve_port("18080") == 18080


def test_resolve_port_rejects_non_integer() -> None:
    """Given "abc"，When 解析，Then 抛 ValueError（入口层捕获并退出码 2）。"""
    try:
        resolve_port("abc")
    except ValueError:
        return
    raise AssertionError("resolve_port('abc') 未抛出 ValueError")


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
