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
import socket
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from types import TracebackType
from typing import Final, TypeAlias

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from future_war.config import Config
from future_war.models import Response
from future_war.observability.events import phase_of
from future_war.observability.round_metrics import RoundObserver
from future_war.server import (
    EMPTY_RESPONSE,
    JudgeRequestHandler,
    SLOW_ROUND_MS_DEFAULT,
    _command_summary,
    _result_summary,
    create_server,
    resolve_port,
    resolve_slow_round_ms,
)

REQUEST_FIXTURE: Final = Path(__file__).resolve().parents[1] / "docs" / "request.txt"
FIXTURE_ROUND_NO: Final = 85  # docs/request.txt 的 roundNo

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


class _SlowBot:
    """睡眠指定秒数后返回空指令的假 Bot（用于制造「回合超时」）。"""

    def __init__(self, seconds: float) -> None:
        self._seconds = seconds

    def __call__(self, request: object) -> Response:
        time.sleep(self._seconds)
        return Response(roleCommandMap={}, prompt="", executeCmd="")


class RunningServer:
    """真实服务的线程化包装：绑定临时端口，退出时干净关闭。"""

    def __init__(
        self,
        observer: RoundObserver | None = None,
        bot: object | None = None,
        *,
        slow_round_ms: int = SLOW_ROUND_MS_DEFAULT,
    ) -> None:
        self.server = create_server(
            0, observer, bot, slow_round_ms=slow_round_ms  # type: ignore[arg-type]
        )
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


# ---------------------------------------------------------------- X-04 超时留证


def _observer(tmp: str) -> RoundObserver:
    config = Config(
        data={"log": {"level": "EVENT", "trace_enabled": False}},
        profile="test",
        commit="testcommit",
        config_hash="testhash",
    )
    return RoundObserver.from_config(config, log_dir=tmp, match_name="slow")


def _structured_lines(observer: RoundObserver) -> str:
    observer.flush()
    path = observer.structured.log_path
    assert path is not None
    return path.read_text(encoding="utf-8")


def test_slow_round_emits_x04_with_round_no_and_elapsed_ms() -> None:
    """Given 一回合耗时超阈值，When POST，Then 结构化日志记一条带回合号的 X-04。"""
    with tempfile.TemporaryDirectory() as tmp:
        observer = _observer(tmp)
        try:
            with RunningServer(observer, _SlowBot(0.05), slow_round_ms=10) as server:
                status, payload = server.post(REQUEST_FIXTURE.read_bytes())
            text = _structured_lines(observer)
        finally:
            observer.close()
    assert status == 200
    assert payload == EMPTY_RESPONSE
    assert "[ERROR] X-04" in text, f"未记录 X-04：\n{text}"
    line = next(line for line in text.splitlines() if "X-04" in line)
    expected = f"{FIXTURE_ROUND_NO:04d} {phase_of(FIXTURE_ROUND_NO).value} "
    assert line.startswith(expected), f"回合号/相位不对：{line}"
    assert "thresholdMs=10" in line
    assert "elapsedMs=" in line


def test_normal_round_does_not_emit_x04() -> None:
    """Given 一回合远快于阈值，When POST，Then 不记 X-04（避免噪声）。"""
    with tempfile.TemporaryDirectory() as tmp:
        observer = _observer(tmp)
        try:
            with RunningServer(observer) as server:
                status, _ = server.post(REQUEST_FIXTURE.read_bytes())
            text = _structured_lines(observer)
        finally:
            observer.close()
    assert status == 200
    assert "X-04" not in text, f"不该记录 X-04：\n{text}"


def test_slow_round_threshold_disabled_by_non_positive_config() -> None:
    """Given slow_round_ms=0，When 极慢回合，Then 不记 X-04（告警可关闭）。"""
    with tempfile.TemporaryDirectory() as tmp:
        observer = _observer(tmp)
        try:
            with RunningServer(observer, _SlowBot(0.02), slow_round_ms=0) as server:
                server.post(REQUEST_FIXTURE.read_bytes())
            text = _structured_lines(observer)
        finally:
            observer.close()
    assert "X-04" not in text, f"阈值 0 应关闭告警：\n{text}"


class _ShortSocketTimeout:
    """临时把 handler 的 socket 读超时调小，测试不必真等 5s（退出时还原）。"""

    def __init__(self, seconds: float) -> None:
        self._seconds = seconds
        self._original = JudgeRequestHandler.timeout

    def __enter__(self) -> None:
        JudgeRequestHandler.timeout = self._seconds

    def __exit__(self, *exc_info: object) -> None:
        JudgeRequestHandler.timeout = self._original


def _read_until_close(sock: socket.socket) -> bytes:
    """读完连接上的全部字节（服务端回包后即关连接）。"""
    chunk_list: list[bytes] = []
    sock.settimeout(5)
    while True:
        try:
            chunk = sock.recv(4096)
        except OSError:
            break
        if not chunk:
            break
        chunk_list.append(chunk)
    return b"".join(chunk_list)


def test_request_line_timeout_gets_legal_response_and_x04() -> None:
    """Given 连上却不发请求行，When 读超时，Then 回合法空 Response 且记 X-04。

    回归：stdlib 的 handle_one_request 会静默吞掉 TimeoutError 并直接关连接，
    判题器既收不到响应、日志里也没有任何痕迹。
    """
    with tempfile.TemporaryDirectory() as tmp:
        observer = _observer(tmp)
        try:
            with _ShortSocketTimeout(0.2):
                with RunningServer(observer) as server:
                    sock = socket.create_connection(("127.0.0.1", server.port), timeout=5)
                    try:
                        raw = _read_until_close(sock)
                    finally:
                        sock.close()
            text = _structured_lines(observer)
        finally:
            observer.close()
    assert b"200 OK" in raw, f"未收到合法 HTTP 响应：{raw!r}"
    assert b'"roleCommandMap"' in raw, f"响应体不是合法 Response：{raw!r}"
    assert "[ERROR] X-04" in text, f"未记录 X-04：\n{text}"
    # 请求行都没读到 → 无回合号可用，round 与 phase 列渲染为 "-"
    line = next(line for line in text.splitlines() if "X-04" in line)
    assert line.startswith("- - [ERROR] X-04 "), f"无回合号场景格式不对：{line}"
    assert "elapsedMs=" in line


def test_body_read_timeout_gets_legal_response_and_x04() -> None:
    """Given 声明 Content-Length 却不发请求体，When 读超时，Then 同样回包 + X-04。"""
    with tempfile.TemporaryDirectory() as tmp:
        observer = _observer(tmp)
        try:
            with _ShortSocketTimeout(0.2):
                with RunningServer(observer) as server:
                    sock = socket.create_connection(("127.0.0.1", server.port), timeout=5)
                    try:
                        sock.sendall(b"POST / HTTP/1.1\r\nContent-Length: 100\r\n\r\n")
                        raw = _read_until_close(sock)
                    finally:
                        sock.close()
            text = _structured_lines(observer)
        finally:
            observer.close()
    assert b"200 OK" in raw, f"未收到合法 HTTP 响应：{raw!r}"
    assert b'"roleCommandMap"' in raw, f"响应体不是合法 Response：{raw!r}"
    assert "[ERROR] X-04" in text, f"未记录 X-04：\n{text}"


def test_client_close_without_request_emits_no_x04() -> None:
    """Given 连上后什么都不发就关闭，When 连接结束，Then 不误报 X-04。"""
    with tempfile.TemporaryDirectory() as tmp:
        observer = _observer(tmp)
        try:
            with RunningServer(observer) as server:
                sock = socket.create_connection(("127.0.0.1", server.port), timeout=5)
                sock.close()
                time.sleep(0.2)  # 等服务端走完 handle()/finish()
            text = _structured_lines(observer)
        finally:
            observer.close()
    assert "X-04" not in text, f"对端正常关闭不该记 X-04：\n{text}"


def test_resolve_slow_round_ms_reads_config_or_falls_back() -> None:
    """Given 各种 server.slow_round_ms 取值，When 解析，Then 合法值生效、其余回退默认。"""
    def config_with(value: object) -> Config:
        data: dict[str, object] = {} if value is None else {"server": {"slow_round_ms": value}}
        return Config(data=data, profile="t", commit="c", config_hash="h")  # type: ignore[arg-type]

    assert resolve_slow_round_ms(None) == SLOW_ROUND_MS_DEFAULT
    assert resolve_slow_round_ms(config_with(None)) == SLOW_ROUND_MS_DEFAULT
    assert resolve_slow_round_ms(config_with(1200)) == 1200
    assert resolve_slow_round_ms(config_with(4500.0)) == 4500
    # 非正数 = 显式关闭告警（保留原值，由 _warn_if_slow 判定为关；不是回退默认）
    assert resolve_slow_round_ms(config_with(0)) == 0
    assert resolve_slow_round_ms(config_with(-5)) == -5
    # 非法类型回退默认（JSON true 不是阈值）
    assert resolve_slow_round_ms(config_with(True)) == SLOW_ROUND_MS_DEFAULT
    assert resolve_slow_round_ms(config_with("fast")) == SLOW_ROUND_MS_DEFAULT


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


# ---------------------------------------------------------------- D-02 指令追踪


def test_round_emits_d02_trace_with_commands_and_results() -> None:
    """Given 一个回合，When POST，Then stderr/日志里有 D-02：规划摘要 + 指令 + 上回合结果。"""
    body = json.dumps(
        {
            "roundNo": 12,
            "mapInfo": {"width": 41, "height": 32, "zones": []},
            "teamOur": {
                "type": "challenger",
                "teamId": "t",
                "teamName": "t",
                "goldNum": 30,
                "totalScore": 0,
                "roles": [
                    {"id": 10013, "pos": {"x": 20, "y": 20}, "roleType": "station", "health": 1500},
                    {"id": 10010, "pos": {"x": 5, "y": 5}, "roleType": "worker", "health": 220},
                ],
            },
            "teamEnemy": {"roles": []},
            "robot": {"roles": []},
            "phaseTask": "",
            "lastRoundRoleActionResults": {"10010": False, "10011": True},
            "lastSummonTreasureResult": 0,
            "llmResp": "",
            "worldNews": {"officialNews": "", "folkLegends": ""},
            "lastCmdResult": "",
            "vendorShopList": [],
            "weaponShopList": [],
            "errors": [],
        }
    ).encode("utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        observer = _observer(tmp)
        try:
            with RunningServer(observer) as server:
                status, payload = server.post(body)
            text = _structured_lines(observer)
        finally:
            observer.close()
    assert status == 200
    assert "roleCommandMap" in payload
    assert "[DIGEST] D-02" in text, f"未记录 D-02：\n{text}"
    line = next(line for line in text.splitlines() if "D-02" in line)
    assert line.startswith("0012 D [DIGEST] D-02 "), line
    assert "results=ok:1,fail:1" in line, f"上回合结果摘要不对：{line}"
    assert "cmds=" in line


def test_command_summary_lists_build_targets() -> None:
    """Given 含建造的指令集，When 摘要，Then 列出建造类型与目标格。"""
    summary = _command_summary(
        {
            1: {"action": "build", "name": "wall", "targetPos": [{"x": 9, "y": 23}]},
            2: {"action": "move", "targetPos": [{"x": 3, "y": 4}]},
        }
    )
    assert summary == "build:1,move:1+wall@(9,23)", summary
    assert _command_summary({}) == "none"
    assert _command_summary(None) == "none"
    assert _result_summary({"1": True, "2": False, "3": "x"}) == "ok:1,fail:1,other:1"
    assert _result_summary({}) == "none"

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
