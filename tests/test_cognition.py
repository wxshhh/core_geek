"""工作包 23/24 测试：LLM 管理器（配额/异步/免费窗口）与沙盒结果解析。

Given/When/Then 风格；运行方式（二选一）：
    python3 -m pytest tests/ -q
    python3 tests/test_cognition.py
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from future_war.models import parse_request  # noqa: E402
from future_war.core import WorldModel  # noqa: E402
from future_war.strategy import LLMManager, parse_cmd_result  # noqa: E402


def _view(round_no=1, phase_task="", llm_resp=""):
    data = {
        "roundNo": round_no,
        "mapInfo": {"width": 41, "height": 32, "zones": []},
        "teamOur": {
            "type": "challenger",
            "teamId": "t",
            "teamName": "t",
            "goldNum": 0,
            "totalScore": 0,
            "roles": [],
        },
        "teamEnemy": {"roles": []},
        "robot": {"roles": []},
        "phaseTask": phase_task,
        "lastRoundRoleActionResults": {},
        "lastSummonTreasureResult": 0,
        "llmResp": llm_resp,
        "worldNews": {"officialNews": "", "folkLegends": ""},
        "lastCmdResult": "",
        "vendorShopList": [],
        "weaponShopList": [],
        "errors": [],
    }
    return WorldModel().apply_round(parse_request(data))


# ---------------------------------------------------------------- LLM manager


def test_quota_decrements_per_call() -> None:
    """Given 日配额 3，When 连发 3 次，Then 计数到 3 且第 4 次被拒。"""
    manager = LLMManager(daily_quota=3)
    view = _view(round_no=1)
    manager.sync(view)
    assert [manager.note_sent(f"p{i}", view) for i in range(3)] == [True, True, True]
    assert manager.used == 3
    assert manager.note_sent("p4", view) is False


def test_quota_resets_next_day() -> None:
    """Given 当天配额用尽，When 进入下一天，Then 配额重置。"""
    manager = LLMManager(daily_quota=3)
    day1 = _view(round_no=1)
    manager.sync(day1)
    for i in range(3):
        manager.note_sent(f"p{i}", day1)
    assert manager.can_call(day1) is False
    day2 = _view(round_no=131)  # 第 2 天
    manager.sync(day2)
    assert manager.can_call(day2) is True
    assert manager.used == 0


def test_free_window_unlimited_during_task() -> None:
    """Given 自进化任务进行中，When 已超配额，Then 仍可调用且不计数。"""
    manager = LLMManager(daily_quota=3)
    view = _view(round_no=1, phase_task="查询天气")
    manager.sync(view)
    for i in range(10):
        assert manager.note_sent(f"p{i}", view) is True
    assert manager.used == 0


def test_response_correlated_to_pending() -> None:
    """Given 已发 prompt，When 下回合返回 llmResp，Then 关联并清空待决。"""
    manager = LLMManager(daily_quota=3)
    sent = _view(round_no=1)
    manager.sync(sent)
    manager.note_sent("ask", sent)
    assert manager.pending == "ask"
    replied = _view(round_no=2, llm_resp="answer")
    manager.sync(replied)
    assert manager.last_response == "answer"
    assert manager.pending is None


# ---------------------------------------------------------------- sandbox


def test_parse_exit_code_result() -> None:
    """Given [exitCode:0] 结果，When 解析，Then 退出码 0 且 ok。"""
    result = parse_cmd_result("[exitCode:0]\nhello")
    assert result.exit_code == 0
    assert result.output == "hello"
    assert result.ok is True


def test_parse_nonzero_exit_not_ok() -> None:
    """Given 非零退出码，When 解析，Then ok 为假。"""
    result = parse_cmd_result("[exitCode:2]\nboom")
    assert result.exit_code == 2
    assert result.ok is False


def test_parse_timeout() -> None:
    """Given [TIMEOUT] 结果，When 解析，Then timed_out 为真。"""
    result = parse_cmd_result("[TIMEOUT]\npartial")
    assert result.timed_out is True
    assert result.output == "partial"


def test_parse_judger_error() -> None:
    """Given [JUDGER_ERROR] 结果，When 解析，Then judger_error 为真。"""
    result = parse_cmd_result("[JUDGER_ERROR]\nreason")
    assert result.judger_error is True
    assert result.output == "reason"


def test_parse_truncated_suffix() -> None:
    """Given 末尾 [TRUNCATED]，When 解析，Then truncated 为真且输出保留。"""
    result = parse_cmd_result("[exitCode:0]\nbig output\n[TRUNCATED]")
    assert result.truncated is True
    assert result.exit_code == 0


def test_parse_empty_is_safe() -> None:
    """Given 空字符串，When 解析，Then 返回空结果且不崩溃。"""
    result = parse_cmd_result("")
    assert result.exit_code is None
    assert result.output == ""
    assert result.ok is False


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
