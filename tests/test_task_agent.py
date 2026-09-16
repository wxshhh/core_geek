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
from future_war.strategy import SkillLibrary, StrategyBot, TaskState, plan_task  # noqa: E402


def _request(
    *,
    pioneer_pos=(14, 14),
    phase_task="",
    last_cmd="",
    round_no=1,
    llm_resp="",
    zones=None,
):
    return {
        "roundNo": round_no,
        "mapInfo": {
            "width": 41,
            "height": 32,
            "zones": zones
            if zones is not None
            else [{"pos": {"x": 14, "y": 14}, "neutralType": "challengerTaskPoint1"}],
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
        "llmResp": llm_resp,
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


def test_no_task_accepted_late_in_day() -> None:
    """Given 白天接近夜晚（剩余不足余量），When 规划，Then 不接任务（避免跨夜超时）。"""
    action = plan_task(_view(pioneer_pos=(14, 15), round_no=68), _config(), TaskState())
    assert action.commands == {}


def test_task_accepted_early_in_day() -> None:
    """Given 白天回合充裕，When 规划，Then 接任务。"""
    action = plan_task(_view(pioneer_pos=(14, 15), round_no=1), _config(), TaskState())
    assert enum_to_str(action.commands[10011].action) == "acceptTask"


def test_timeout_result_is_tolerated() -> None:
    """Given 沙盒超时，When 规划，Then 不提交答案且不崩溃。"""
    state = TaskState()
    plan_task(_view(phase_task="查询北京天气"), _config(), state)
    action = plan_task(
        _view(phase_task="查询北京天气", last_cmd="[TIMEOUT]\npartial", round_no=2),
        _config(),
        state,
    )
    assert all(
        enum_to_str(c.action) != "submitAnswer" for c in action.commands.values()
    )


def test_bot_emits_prompt_for_active_task() -> None:
    """Given 已领任务，When StrategyBot 处理，Then Response 含 prompt 与 executeCmd。"""
    response = StrategyBot()(parse_request(_request(phase_task="查询北京天气")))
    assert response.prompt
    assert response.executeCmd


def test_pioneer_returns_home_at_dusk_instead_of_tasking() -> None:
    """Given 黄昏就位阶段（显式配置 dusk_return=40），When 规划任务，Then 不再接/做任务。

    默认 ``economy.dusk_return`` 已改为 70（白天干满），所以要测这个分支必须显式配。
    """
    from future_war.strategy.task_agent import plan_task

    view = _view(pioneer_pos=(14, 14), round_no=45)
    state = TaskState()
    state.observations = ["already explored"]
    config = Config(
        data={"economy": {"dusk_return": 40}}, profile="t", commit="c", config_hash="h"
    )
    action = plan_task(view, config, state)
    assert action.commands == {}
    assert action.execute_cmd == ""
    # **只暂停、不清空**：旧实现在这里 _reset，跨天任务每晚归零 → 永远做不完
    assert state.observations == ["already explored"]


def test_llm_response_is_submitted_as_task_answer() -> None:
    """Given 已领任务且上回合问过 LLM，When 收到 llmResp，Then 提交该答案。

    回归真机现象：反复 acceptTask 却从不 submitAnswer（任务分 0）。旧实现只从沙盒
    输出里找 ``ANSWER:`` 标记，**llmResp 根本没被读回**，所以永远没有可提交的答案。
    """
    from future_war.strategy.task_agent import plan_task

    state = TaskState()
    # 第 1 回合：已领任务（phaseTask 非空）→ 发出 prompt + 沙盒命令
    first = plan_task(_view(phase_task="求 6*7", round_no=1), None, state)
    assert first.prompt, "应先向 LLM 提问"
    assert state.pending_prompt is True
    # 第 2 回合：judge 回传 llmResp → 直接提交这个答案
    second = plan_task(_view(phase_task="求 6*7", round_no=2, llm_resp="42"), None, state)
    assert second.commands, f"应提交答案，实际 {second.commands}"
    command = next(iter(second.commands.values()))
    assert enum_to_str(command.action) == "submitAnswer"
    assert command.taskAnswer == "42"


def test_llm_next_reply_is_not_submitted_as_answer() -> None:
    """Given LLM 只回 NEXT（信息不够），When 规划，Then 不提交、继续推进。"""
    from future_war.strategy.task_agent import plan_task

    state = TaskState()
    plan_task(_view(phase_task="某任务", round_no=1), None, state)
    action = plan_task(_view(phase_task="某任务", round_no=2, llm_resp="NEXT"), None, state)
    assert action.commands == {}, "NEXT 不该被当成答案提交"


def test_pioneer_returns_home_at_night() -> None:
    """Given 夜晚，When 规划任务，Then 不产出任务指令（夜里要操控武器）。"""
    from future_war.strategy.task_agent import plan_task

    view = _view(pioneer_pos=(14, 14), round_no=85)
    assert plan_task(view, None, TaskState()).commands == {}


def test_accept_task_is_sent_once_and_not_every_round() -> None:
    """Given 开拓者已站在任务点旁、phaseTask 一直是空，When 连续规划 3 回合，
    Then 只在第 1 回合发 acceptTask，之后是原地等待（``task_stall=accept-sent``）。

    回归线上现象：旧实现每回合都重发 acceptTask。判题器要等领取生效才回
    ``phaseTask``，重复领取一旦被判成「重新领取/非法」，任务计时与状态就被反复
    重置 —— 开拓者永远等不到任务描述，也就永远不可能 submitAnswer。
    """
    from future_war.strategy.task_agent import plan_task

    state = TaskState()
    first = plan_task(_view(pioneer_pos=(13, 13), round_no=1), None, state)
    assert enum_to_str(first.commands[10011].action) == "acceptTask"
    for round_no in (2, 3):
        again = plan_task(_view(pioneer_pos=(13, 13), round_no=round_no), None, state)
        assert again.commands == {}, f"第 {round_no} 回合不该重发 acceptTask"
        assert again.stall == "accept-sent"
    # 有限重试窗口：防止第一次领取丢包后永久卡住
    retry = plan_task(_view(pioneer_pos=(13, 13), round_no=5), None, state)
    assert enum_to_str(retry.commands[10011].action) == "acceptTask"


def test_pioneer_switches_to_a_reachable_task_point() -> None:
    """Given 最近的任务点四周被矿占满（一步都走不进去）、另一个任务点可达，
    When 规划任务，Then 仍产出一条朝远点移动的合法指令（不是零指令）。

    这是真机「开拓者走到任务点后杵着」的确切分支：任务点是阻挡格，``plan_move``
    只能给「最优接近」；最近点周围一圈全被挡时它会停在切比雪夫 2 格外，之后
    每回合 ``plan_move`` 都返回 ``None`` → 整回合零指令 → 永远不 acceptTask。
    旧实现只认最近的那个任务点，于是永久卡死；现在会挨个试到能走过去的那个。
    """
    from future_war.core import chebyshev
    from future_war.core.nav import plan_move
    from future_war.models import Pos
    from future_war.strategy.task_agent import plan_task

    zones = [
        {"pos": {"x": 14, "y": 14}, "neutralType": "challengerTaskPoint1"},
        {"pos": {"x": 20, "y": 20}, "neutralType": "challengerTaskPoint1"},
    ]
    for dx, dy in ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)):
        zones.append({"pos": {"x": 14 + dx, "y": 14 + dy}, "neutralType": "stone"})
    view = _view(pioneer_pos=(12, 12), zones=zones)
    assert plan_move(view, 10011, Pos(14, 14)) is None, "前提：最近的任务点走不进去"

    action = plan_task(view, None, TaskState())
    assert action.commands, "最近点不可达也必须改朝可达的任务点走，不能零指令"
    command = action.commands[10011]
    assert enum_to_str(command.action) == "move"
    assert chebyshev(command.targetPos[0], Pos(12, 12)) == 1
    assert action.stall == ""


def test_pioneer_reports_stall_when_no_task_point_is_reachable() -> None:
    """Given 所有任务点四周都被挡死，When 规划任务，
    Then 给出 ``stall=no-route``（planner 会翻成 notes 里的 ``task_stall=no-route``）。"""
    from future_war.strategy.task_agent import plan_task

    zones = [{"pos": {"x": 14, "y": 14}, "neutralType": "challengerTaskPoint1"}]
    for dx, dy in ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)):
        zones.append({"pos": {"x": 14 + dx, "y": 14 + dy}, "neutralType": "stone"})
    action = plan_task(_view(pioneer_pos=(12, 12), zones=zones), None, TaskState())
    assert action.commands == {}
    assert action.stall == "no-route"


def test_stale_answer_is_not_submitted_to_the_next_task() -> None:
    """Given 上一题已经提交过答案，When 同一开拓者接下一题（judge 还没回 llmResp），
    Then 绝不把上一题的答案当成新任务的答案立刻交出去。

    回归真机「从不提交（正确）答案」：``_reset`` 只清 observations/signature，
    却把 ``llm_answer``/``pending_prompt`` 留了下来 —— 于是新任务开局第一条观测
    就是**上一题的 llmResp**，每题都会瞬间提交同一个陈旧答案（errorCode 2）。
    """
    from future_war.strategy.task_agent import plan_task

    state = TaskState()
    plan_task(_view(phase_task="求 6*7", round_no=1), None, state)
    submitted = plan_task(
        _view(phase_task="求 6*7", round_no=2, llm_resp="42"), None, state
    )
    assert enum_to_str(submitted.commands[10011].action) == "submitAnswer"
    # 任务结束（phaseTask 恢复为空）→ 第二天/下一个任务点重新领取
    plan_task(_view(phase_task="", round_no=3), None, state)
    # 新任务：judge 还没回 llmResp，陈旧的 "42" 绝不能当答案
    fresh = plan_task(_view(phase_task="翻译 hello", round_no=4), None, state)
    assert all(
        enum_to_str(c.action) != "submitAnswer" for c in fresh.commands.values()
    ), f"陈旧答案被当成本题答案提交: {fresh.commands}"
    assert fresh.execute_cmd, "新任务应当重新发沙盒命令"


def test_active_task_answer_is_submitted_even_at_night() -> None:
    """Given 任务仍然活跃且答案已经到手，When 进入夜晚，Then 照样提交答案。

    ``submitAnswer`` 没有昼夜限制；任务一超时/离点就作废（0 分），把已经拿到的
    答案留到第二天再交等于白干。
    """
    from future_war.strategy.task_agent import plan_task

    state = TaskState()
    plan_task(_view(phase_task="求 6*7", round_no=1), None, state)
    action = plan_task(
        _view(phase_task="求 6*7", round_no=85, llm_resp="42"), None, state
    )
    assert enum_to_str(action.commands[10011].action) == "submitAnswer"
    assert action.commands[10011].taskAnswer == "42"


def test_planner_notes_include_task_stall() -> None:
    """Given 任务点全被挡死，When 走完整 plan_turn，Then notes 含 ``task_stall=no-route``。"""
    from future_war.strategy.planner import plan_turn

    zones = [{"pos": {"x": 14, "y": 14}, "neutralType": "challengerTaskPoint1"}]
    for dx, dy in ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)):
        zones.append({"pos": {"x": 14 + dx, "y": 14 + dy}, "neutralType": "stone"})
    plan = plan_turn(_view(pioneer_pos=(12, 12), zones=zones), None)
    assert any(note == "task_stall=no-route" for note in plan.notes), plan.notes


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
