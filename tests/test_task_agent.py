"""工作包 21 测试：自进化任务 Agent（沙盒循环 + 技能库）。

Given/When/Then 风格；运行方式（二选一）：
    python3 -m pytest tests/ -q
    python3 tests/test_task_agent.py
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from future_war.config import Config  # noqa: E402
from future_war.models import enum_to_str, parse_request  # noqa: E402
from future_war.core import WorldModel  # noqa: E402
from future_war.strategy import SkillLibrary, TaskState, plan_task  # noqa: E402


def _request(*, pioneer_pos=(14, 14), phase_task="", last_cmd="", round_no=1):
    return {
        "roundNo": round_no,
        "mapInfo": {
            "width": 41,
            "height": 32,
            "zones": [{"pos": {"x": 14, "y": 14}, "neutralType": "challengerTaskPoint1"}],
        },
        "teamOur": {
            "type": "challenger",
            "teamId": "t",
            "teamName": "t",
            "goldNum": 0,
            "totalScore": 0,
            "roles": [
                {
                    "id": 10011,
                    "pos": {"x": pioneer_pos[0], "y": pioneer_pos[1]},
                    "roleType": "pioneer",
                    "health": 200,
                    "backPackCapability": 40,
                    "backpack": [],
                }
            ],
        },
        "teamEnemy": {"roles": []},
        "robot": {"roles": []},
        "phaseTask": phase_task,
        "lastRoundRoleActionResults": {},
        "lastSummonTreasureResult": 0,
        "llmResp": "",
        "worldNews": {"officialNews": "", "folkLegends": ""},
        "lastCmdResult": last_cmd,
        "vendorShopList": [],
        "weaponShopList": [],
        "errors": [],
    }


def _view(**kwargs):
    return WorldModel().apply_round(parse_request(_request(**kwargs)))


def _config(**tasks):
    return Config(data={"tasks": tasks}, profile="t", commit="c", config_hash="h")


def test_accept_task_when_adjacent_to_task_point() -> None:
    """Given 开拓者在任务点旁且未接任务，When 规划，Then 发出 acceptTask。"""
    action = plan_task(_view(pioneer_pos=(14, 15)), _config(), TaskState())
    cmd = action.commands[10011]
    assert enum_to_str(cmd.action) == "acceptTask"


def test_move_toward_task_point_when_far() -> None:
    """Given 开拓者远离任务点，When 规划，Then 朝任务点移动。"""
    action = plan_task(_view(pioneer_pos=(5, 5)), _config(), TaskState())
    assert enum_to_str(action.commands[10011].action) == "move"


def test_sends_execute_cmd_and_prompt_when_task_active() -> None:
    """Given 已领取任务，When 规划，Then 发出沙盒命令与 LLM prompt。"""
    state = TaskState()
    action = plan_task(_view(phase_task="查询北京天气"), _config(), state)
    assert action.execute_cmd
    assert "查询北京天气" in action.prompt
    assert state.pending_cmd == action.execute_cmd


def test_submits_answer_when_result_contains_marker() -> None:
    """Given 沙盒结果含 ANSWER:，When 规划，Then 提交答案。"""
    state = TaskState()
    plan_task(_view(phase_task="查询北京天气"), _config(), state)  # 发出命令
    action = plan_task(
        _view(phase_task="查询北京天气", last_cmd="[exitCode:0]\nANSWER: sunny", round_no=2),
        _config(),
        state,
    )
    cmd = action.commands[10011]
    assert enum_to_str(cmd.action) == "submitAnswer"
    assert cmd.taskAnswer == "sunny"


def test_skill_library_record_and_recall() -> None:
    """Given 技能库，When 记录并查询同一签名，Then 返回 SOP。"""
    library = SkillLibrary()
    library.record("weather", ("curl a", "curl b"))
    assert library.recall("weather") == ("curl a", "curl b")
    assert library.recall("unknown") == ()


def test_skill_reused_on_second_similar_task() -> None:
    """Given 同类任务已完成并入库，When 再次执行，Then 首条命令来自 SOP。"""
    state = TaskState()
    state.skills.record("查询上海天气", ("weather-cmd",))
    action = plan_task(_view(phase_task="查询上海天气"), _config(), state)
    assert action.execute_cmd == "weather-cmd"


def test_disabled_by_config() -> None:
    """Given tasks.self_evolution_enabled=false，When 规划，Then 不产出指令。"""
    action = plan_task(
        _view(pioneer_pos=(14, 15)), _config(self_evolution_enabled=False), TaskState()
    )
    assert action.commands == {}


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
