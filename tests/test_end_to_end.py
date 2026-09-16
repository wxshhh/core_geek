"""工作包 13 测试：端到端存活基线（世界模型+寻路+经济+防御 整合）。

Given/When/Then 风格；运行方式（二选一）：
    python3 -m pytest tests/ -q
    python3 tests/test_end_to_end.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from future_war.config import Config  # noqa: E402
from future_war.observability.round_metrics import RoundObserver  # noqa: E402
from future_war.server import create_server  # noqa: E402
from future_war.sim import IdleBot, World, make_layout, run_match  # noqa: E402
from future_war.strategy import StrategyBot  # noqa: E402

REQUEST_FIXTURE = SRC_DIR.parent / "docs" / "request.txt"


def _run(seed: int = 42):
    world = World(seed=seed, layout=make_layout())
    return run_match(world, {"challenger": StrategyBot(), "defender": IdleBot()})


def test_strategy_bot_survives_at_least_three_days() -> None:
    """Given 策略 Bot vs 空 Bot，When 跑整场，Then 基地存活 ≥3 天。"""
    outcome = _run().teams["challenger"]
    survived_days = outcome.base_destroy_day or 10
    assert survived_days >= 3, f"base destroyed on day {outcome.base_destroy_day}"


def test_strategy_bot_has_no_team_exceptions() -> None:
    """Given 策略 Bot，When 跑整场，Then 不产生队伍异常（指令结构始终合法）。"""
    outcome = _run().teams["challenger"]
    assert outcome.exceptions == 0


def test_strategy_bot_kills_robots() -> None:
    """Given 夜晚机器人浪潮，When 策略 Bot 防守，Then 至少击杀若干机器人。"""
    outcome = _run().teams["challenger"]
    assert sum(outcome.kills.values()) > 0


def test_match_is_deterministic() -> None:
    """Given 相同 seed 与策略，When 跑两次，Then 双方总分一致。"""
    first = _run().teams["challenger"].total
    second = _run().teams["challenger"].total
    assert first == second


def test_strategy_beats_idle_opponent() -> None:
    """Given 策略 Bot vs 空 Bot，When 比较总分，Then 策略方分数更高。"""
    report = _run()
    assert report.teams["challenger"].total > report.teams["defender"].total


def _post(port: int, body: bytes) -> tuple[int, bytes]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/", data=body, method="POST"
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, response.read()


def test_server_plays_via_strategy_bot() -> None:
    """Given 真实服务挂了策略 Bot，When POST 样例请求，Then 返回非空合法指令。"""
    with tempfile.TemporaryDirectory() as tmp:
        config = Config(
            data={
                "log": {"level": "EVENT", "trace_enabled": False},
                "features": {"replay_enabled": True, "metric_line_enabled": True},
            },
            profile="test",
            commit="c",
            config_hash="h",
        )
        observer = RoundObserver.from_config(config, log_dir=tmp, match_name="e2e")
        server = create_server(0, observer, StrategyBot(config))
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            status, body = _post(port, REQUEST_FIXTURE.read_bytes())
            assert status == 200
            payload = json.loads(body)
            assert isinstance(payload["roleCommandMap"], dict)
            assert payload["roleCommandMap"], "strategy bot produced no commands"
        finally:
            server.shutdown()
            server.server_close()
            observer.close()


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
