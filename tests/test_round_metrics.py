"""工作包 6 测试：每回合日志完整性与 [METRIC] 指标行。"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from future_war.config import Config  # noqa: E402
from future_war.observability.round_metrics import (  # noqa: E402
    RoundObserver,
    metric_fields,
)
from future_war.server import create_server  # noqa: E402

SAMPLE_REQUEST = {
    "roundNo": 85,
    "teamOur": {
        "goldNum": 20,
        "totalScore": 280,
        "roles": [
            {"id": 10013, "roleType": "station", "health": 1500},
            {"id": 10010, "roleType": "worker", "health": 220},
        ],
    },
    "errors": [{"errorCode": 2, "description": "x"}],
}


def _config(*, replay: bool = True, metric: bool = True) -> Config:
    return Config(
        data={
            "log": {"level": "EVENT", "trace_enabled": False},
            "features": {
                "replay_enabled": replay,
                "metric_line_enabled": metric,
            },
        },
        profile="test",
        commit="testcommit",
        config_hash="testhash",
    )


def _observer(tmp: str, *, replay: bool = True, metric: bool = True) -> RoundObserver:
    return RoundObserver.from_config(
        _config(replay=replay, metric=metric), log_dir=tmp, match_name="t"
    )


def _start(observer: RoundObserver) -> tuple[object, int]:
    server = create_server(0, observer)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, port


def _post(port: int, body: bytes) -> tuple[int, bytes]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/", data=body, method="POST"
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, response.read()


def _stop(server: object, observer: RoundObserver) -> None:
    server.shutdown()  # type: ignore[attr-defined]
    server.server_close()  # type: ignore[attr-defined]
    observer.close()


def test_metric_fields_derivation() -> None:
    fields = metric_fields(SAMPLE_REQUEST)
    assert fields["gold"] == 20
    assert fields["score"] == 280
    assert fields["baseHP"] == 1500
    assert fields["rolesAlive"] == 2
    assert fields["errors"] == 1
    # 判题请求没有击杀字段：如实保持 None（渲染 -），不猜数
    assert fields["kills"] is None
    # 没有上一回合分数时，环比差值同样不可得
    assert fields["scoreDelta"] is None
    assert metric_fields(SAMPLE_REQUEST, previous_score=250)["scoreDelta"] == 30


def test_metric_fields_tolerate_missing_and_wrong_types() -> None:
    assert metric_fields({}) == {
        "gold": None,
        "kills": None,
        "score": None,
        "scoreDelta": None,
        "baseHP": None,
        "rolesAlive": 0,
        "errors": 0,
    }
    assert metric_fields({"teamOur": "nope", "errors": "nope"})["rolesAlive"] == 0


def test_observe_writes_jsonl_and_metric_line() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        observer = _observer(tmp)
        try:
            observer.observe(85, SAMPLE_REQUEST, {"roleCommandMap": {}})
            observer.flush()
            round_path = observer.round_log_path
            metric_path = observer.structured.log_path
            assert round_path is not None and metric_path is not None
            records = [
                json.loads(line)
                for line in round_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            assert len(records) == 1
            assert records[0]["roundNo"] == 85
            assert records[0]["request"]["teamOur"]["goldNum"] == 20
            metrics = [
                line
                for line in metric_path.read_text(encoding="utf-8").splitlines()
                if "[METRIC] M-01" in line
            ]
            assert len(metrics) == 1
        finally:
            observer.close()


def test_metric_line_is_parseable_kv() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        observer = _observer(tmp)
        try:
            observer.observe(85, SAMPLE_REQUEST, {})
            observer.flush()
            metric_path = observer.structured.log_path
            assert metric_path is not None
            line = next(
                line
                for line in metric_path.read_text(encoding="utf-8").splitlines()
                if "[METRIC] M-01" in line
            )
            assert line.startswith("0085 N [METRIC] M-01 metric ")
            pairs = dict(
                token.split("=", 1)
                for token in line.split()[5:]
                if "=" in token
            )
            assert pairs["gold"] == "20"
            assert pairs["score"] == "280"
            assert pairs["scoreDelta"] == "-"  # 首回合没有环比基线
            assert pairs["baseHP"] == "1500"
            assert pairs["kills"] == "-"
        finally:
            observer.close()


def test_metric_score_delta_tracks_total_score_across_rounds() -> None:
    """Given 连续两个回合的 totalScore 变化，When 写指标行，Then scoreDelta 是环比差值。"""
    with tempfile.TemporaryDirectory() as tmp:
        observer = _observer(tmp)
        try:
            observer.observe(85, SAMPLE_REQUEST, {})  # totalScore=280
            raised = {
                **SAMPLE_REQUEST,
                "teamOur": {**SAMPLE_REQUEST["teamOur"], "totalScore": 340},
            }
            observer.observe(86, raised, {})
            observer.flush()
            metric_path = observer.structured.log_path
            assert metric_path is not None
            lines = [
                line
                for line in metric_path.read_text(encoding="utf-8").splitlines()
                if "[METRIC] M-01" in line
            ]
            assert len(lines) == 2
            assert "scoreDelta=-" in lines[0]
            assert "scoreDelta=60" in lines[1]
        finally:
            observer.close()


def test_replay_disabled_skips_jsonl_but_keeps_metric() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        observer = _observer(tmp, replay=False)
        try:
            observer.observe(85, SAMPLE_REQUEST, {})
            observer.flush()
            assert observer.round_log_path is None
            metric_path = observer.structured.log_path
            assert metric_path is not None
            assert "[METRIC] M-01" in metric_path.read_text(encoding="utf-8")
        finally:
            observer.close()


def test_metric_disabled_skips_metric_but_keeps_jsonl() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        observer = _observer(tmp, metric=False)
        try:
            observer.observe(85, SAMPLE_REQUEST, {})
            observer.flush()
            assert observer.round_log_path is not None
            metric_path = observer.structured.log_path
            assert metric_path is not None
            assert "[METRIC] M-01" not in metric_path.read_text(encoding="utf-8")
        finally:
            observer.close()


def test_server_logs_one_record_and_one_metric_per_round() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        observer = _observer(tmp)
        server, port = _start(observer)
        try:
            for _ in range(3):
                status, body = _post(port, json.dumps(SAMPLE_REQUEST).encode())
                assert status == 200
                assert json.loads(body)["roleCommandMap"] == {}
            observer.flush()
            round_path = observer.round_log_path
            metric_path = observer.structured.log_path
            assert round_path is not None and metric_path is not None
            rounds = [
                line
                for line in round_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            metrics = [
                line
                for line in metric_path.read_text(encoding="utf-8").splitlines()
                if "[METRIC] M-01" in line
            ]
            assert len(rounds) == 3
            assert len(metrics) == 3
        finally:
            _stop(server, observer)


def test_server_malformed_body_logs_anomaly_and_stays_alive() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        observer = _observer(tmp)
        server, port = _start(observer)
        try:
            status, body = _post(port, b"not json")
            assert status == 200
            assert json.loads(body)["roleCommandMap"] == {}
            status, _ = _post(port, json.dumps(SAMPLE_REQUEST).encode())
            assert status == 200
            observer.flush()
            metric_path = observer.structured.log_path
            assert metric_path is not None
            text = metric_path.read_text(encoding="utf-8")
            assert "[ERROR] X-02" in text
        finally:
            _stop(server, observer)


def test_server_accepts_trailing_comma_fixture() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        observer = _observer(tmp)
        server, port = _start(observer)
        try:
            body = (_ROOT / "docs" / "request.txt").read_bytes()
            status, _ = _post(port, body)
            assert status == 200
            observer.flush()
            assert observer.round_log_path is not None
            rounds = [
                line
                for line in observer.round_log_path.read_text(
                    encoding="utf-8"
                ).splitlines()
                if line.strip()
            ]
            assert len(rounds) == 1
            assert json.loads(rounds[0])["roundNo"] == 85
        finally:
            _stop(server, observer)


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
