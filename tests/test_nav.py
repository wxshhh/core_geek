"""工作包 10 测试：寻路与碰撞规避（.omo/plans/future-war-bot.md 工作包 10）。

Given/When/Then 风格；运行方式（二选一）：
    python3 -m pytest tests/ -q
    python3 tests/test_nav.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from future_war.models import Pos, enum_to_str, parse_request  # noqa: E402
from future_war.core import (  # noqa: E402
    WorldModel,
    chebyshev,
    find_approach_path,
    next_step,
    plan_move,
    resolve_moves,
)
from future_war.strategy import (  # noqa: E402
    TaskState,
    TreasureState,
    plan_turn,
)
from future_war.strategy.economy import EconomyState  # noqa: E402


def _pos(x: int, y: int) -> dict[str, int]:
    return {"x": x, "y": y}


def _role(
    role_id: int, role_type: str, x: int, y: int, health: int = 100, **extra: Any
) -> dict[str, Any]:
    return {
        "id": role_id,
        "pos": _pos(x, y),
        "roleType": role_type,
        "health": health,
        **extra,
    }


def _request(
    round_no: int,
    roles: list[dict[str, Any]],
    zones: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "roundNo": round_no,
        "mapInfo": {"width": 41, "height": 32, "zones": zones or []},
        "teamOur": {
            "type": "challenger",
            "teamId": "t",
            "teamName": "t",
            "goldNum": 75,
            "totalScore": 0,
            "roles": roles,
        },
        "teamEnemy": {"roles": []},
        "robot": {"roles": []},
        "phaseTask": "",
        "lastRoundRoleActionResults": {},
        "lastSummonTreasureResult": 0,
        "llmResp": "",
        "worldNews": {"officialNews": "", "folkLegends": ""},
        "lastCmdResult": "",
        "vendorShopList": [],
        "weaponShopList": [],
        "errors": [],
    }


def _view(roles: list[dict[str, Any]], zones: list[dict[str, Any]] | None = None):
    return WorldModel().apply_round(parse_request(_request(1, roles, zones)))


def _assert_legal_step(step: Pos, start: Pos, blocked: set[Pos] | frozenset[Pos]) -> None:
    assert chebyshev(step, start) == 1, f"step {step} not adjacent to {start}"
    assert step not in blocked, f"step {step} enters a blocked cell"
    assert 0 <= step.x < 41 and 0 <= step.y < 32, f"step {step} out of bounds"


# ---------------------------------------------------------------- primitives


def test_find_approach_path_is_legal_and_adjacent() -> None:
    """Given 一堵部分墙，When 规划路径，Then 每一步 8 邻接且不进入阻挡格。"""
    blocked = {Pos(x, 5) for x in range(0, 30)}
    start, goal = Pos(5, 0), Pos(35, 10)
    path = find_approach_path(blocked, 41, 32, start, goal)
    assert path, "expected a path around the wall"
    current = start
    for step in path:
        _assert_legal_step(step, current, blocked)
        current = step
    assert chebyshev(path[-1], goal) < chebyshev(start, goal)


def test_next_step_returns_none_when_enclosed() -> None:
    """Given 起点被 8 邻居围死，When 规划一步，Then 返回 None（原地）。"""
    start = Pos(5, 5)
    blocked = {
        Pos(start.x + dx, start.y + dy)
        for dx in (-1, 0, 1)
        for dy in (-1, 0, 1)
        if (dx, dy) != (0, 0)
    }
    assert next_step(blocked, 41, 32, start, Pos(20, 20)) is None


def test_next_step_never_enters_blocked_goal() -> None:
    """Given 目标格被阻挡，When 规划一步，Then 不踏入目标格而取最优接近。"""
    start, goal = Pos(5, 5), Pos(9, 5)
    blocked = {goal}
    step = next_step(blocked, 41, 32, start, goal)
    assert step is not None
    assert step != goal
    _assert_legal_step(step, start, blocked)


def test_find_approach_path_clamps_out_of_bounds_goal() -> None:
    """Given 目标越界，When 规划路径，Then 钳制到界内且不崩溃。"""
    path = find_approach_path(frozenset(), 41, 32, Pos(5, 5), Pos(100, 100))
    assert path
    for step in path:
        assert 0 <= step.x < 41 and 0 <= step.y < 32


# ---------------------------------------------------------------- plan_move


def test_plan_move_unknown_or_immobile_unit_returns_none() -> None:
    """Given 未知 id 或建筑，When 规划移动，Then 返回 None。"""
    view = _view([_role(10013, "station", 10, 24, 1500, level=1)])
    assert plan_move(view, 99999, Pos(5, 5)) is None
    assert plan_move(view, 10013, Pos(5, 5)) is None


def test_plan_move_returns_legal_adjacent_step() -> None:
    """Given 工人，When 朝远处目标规划，Then 返回界内、8 邻接、非阻挡的下一步。"""
    roles = [
        _role(10013, "station", 10, 24, 1500, level=1),
        _role(10010, "worker", 12, 24, 220, backPackCapability=100),
    ]
    view = _view(roles)
    worker = view.own_workers()[0]
    step = plan_move(view, worker.id, Pos(5, 5))
    assert step is not None
    _assert_legal_step(step, worker.pos, view.obstacles() - {worker.pos})


# ---------------------------------------------------------------- resolve_moves


def test_resolve_moves_no_duplicate_targets_and_legal() -> None:
    """Given 两工人同目标，When 同步解析，Then 无重复目标格且每步合法。"""
    roles = [
        _role(10013, "station", 20, 20, 1500, level=1),
        _role(10010, "worker", 5, 5, 220),
        _role(10012, "worker", 6, 5, 220),
    ]
    view = _view(roles)
    resolved = resolve_moves(view, {10010: Pos(7, 5), 10012: Pos(7, 5)})
    assert set(resolved) == {10010, 10012}
    targets = [p for p in resolved.values() if p is not None]
    assert len(targets) == len(set(targets)), "two units target the same cell"
    for uid, step in resolved.items():
        if step is not None:
            assert view.in_bounds(step)


def test_resolve_moves_avoids_position_swap() -> None:
    """Given 两工人想互换位置，When 同步解析，Then 不产生互换（§4.5.4 ③）。"""
    roles = [
        _role(10013, "station", 20, 20, 1500, level=1),
        _role(10010, "worker", 5, 5, 220),
        _role(10012, "worker", 6, 5, 220),
    ]
    view = _view(roles)
    resolved = resolve_moves(view, {10010: Pos(6, 5), 10012: Pos(5, 5)})
    swapped = resolved.get(10010) == Pos(6, 5) and resolved.get(10012) == Pos(5, 5)
    assert not swapped, "position swap must not happen"


def test_resolve_moves_is_deterministic() -> None:
    """Given 相同输入，When 两次解析，Then 结果完全一致。"""
    roles = [
        _role(10013, "station", 20, 20, 1500, level=1),
        _role(10010, "worker", 5, 5, 220),
        _role(10012, "worker", 6, 5, 220),
    ]
    goals = {10010: Pos(9, 9), 10012: Pos(9, 10)}
    first = resolve_moves(_view(roles), goals)
    second = resolve_moves(_view(roles), goals)
    assert first == second


def test_resolve_moves_ignores_buildings_and_unknown_ids() -> None:
    """Given 目标含建筑/未知 id，When 解析，Then 仅返回可移动角色。"""
    roles = [
        _role(10013, "station", 20, 20, 1500, level=1),
        _role(10010, "worker", 5, 5, 220),
    ]
    view = _view(roles)
    resolved = resolve_moves(view, {10013: Pos(6, 5), 10010: Pos(6, 5), 99999: Pos(1, 1)})
    assert set(resolved) == {10010}


# ------------------------------------------------- 跨模块全局解析（plan_turn）


def _move_targets(plan) -> dict[int, Pos]:
    """从 TurnPlan 里取 {角色 id: move 目标格}（其他动作忽略）。"""
    return {
        uid: command.targetPos[0]
        for uid, command in plan.commands.items()
        if enum_to_str(command.action) == "move" and command.targetPos
    }


def test_plan_turn_resolves_cross_module_move_conflict() -> None:
    """Given 任务模块的开拓者与经济模块的工人本轮被指派到同一空格，
    When plan_turn 汇总全部指令，Then 无重复目标格、无位置互换，每条 move 都合法。

    这是线上 issue #1 的根因回归：``task_agent`` 用单单位 ``plan_move``，
    不知道 ``economy`` 的工人本轮要去哪 —— 两个模块各自都「合法」的步，
    合并后却是同一条争抢指令，判题器统一结算时全部非法（开拓者到不了任务点、
    工人到不了矿点），于是长时间 ``ok:0,fail:N``。

    修复后：争抢格只归一个角色，另一个宁可不发 move（原地待命），
    也绝不发一条注定非法的 move。
    """
    zones = [
        {"pos": _pos(17, 11), "neutralType": "challengerTaskPoint1"},
        {"pos": _pos(6, 5), "neutralType": "stone"},
    ]
    roles = [
        _role(10013, "station", 20, 20, 1500, level=1),
        _role(10011, "pioneer", 17, 23, 200, backPackCapability=40, backpack=[]),
        _role(10010, "worker", 15, 23, 220, backPackCapability=40, backpack=[]),
    ]
    view = _view(roles, zones)
    plan = plan_turn(view, None, EconomyState(), TreasureState(), TaskState())
    moves = _move_targets(plan)
    starts = {role.id: role.pos for role in view.own_roles()}

    # 修复前：两个模块各自算出的步都是 (16, 22)（同一空格）→ 判题器判非法
    assert moves, f"两个角色都在赶路，不该一条 move 都没有: {plan.commands}"
    assert set(moves) <= {10010, 10011}, f"只应涉及这两条移动指令: {moves}"
    targets = list(moves.values())
    assert len(targets) == len(set(targets)), f"不同角色被指派到同一格: {moves}"
    # 争抢输家：整条 move 被移除（或换成别的合法指令），不留非法 move
    for uid in (10010, 10011):
        command = plan.commands.get(uid)
        if command is not None and uid not in moves:
            assert enum_to_str(command.action) != "move", f"{uid} 留下了非法 move"
    for uid, step in moves.items():
        _assert_legal_step(step, starts[uid], view.obstacles() - {starts[uid]})
    for a, step_a in moves.items():
        for b, step_b in moves.items():
            swapped = step_a == starts[b] and step_b == starts[a]
            assert not swapped, f"{a} 与 {b} 互换位置: {moves}"


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
