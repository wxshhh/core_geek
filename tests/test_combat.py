"""工作包 12 测试：基础防御（夜晚武器操控）。

Given/When/Then 风格；运行方式（二选一）：
    python3 -m pytest tests/ -q
    python3 tests/test_combat.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from future_war.models import Pos, enum_to_str, parse_request  # noqa: E402
from future_war.core import WorldModel, chebyshev  # noqa: E402
from future_war.strategy import plan_defense  # noqa: E402

NIGHT_ROUND = 85  # 白天 70 → 85 落在夜晚（任务书 §4.2）
DAY_ROUND = 1


def _pos(x: int, y: int) -> dict[str, int]:
    return {"x": x, "y": y}


def _role(
    role_id: int,
    role_type: str,
    x: int,
    y: int,
    health: int = 100,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "id": role_id,
        "pos": _pos(x, y),
        "roleType": role_type,
        "health": health,
        **extra,
    }


def _robot(
    robot_id: int,
    robot_type: str,
    x: int,
    y: int,
    target: str = "challenger",
    health: int = 40,
) -> dict[str, Any]:
    return {
        "id": robot_id,
        "pos": _pos(x, y),
        "roleType": robot_type,
        "health": health,
        "abnormalState": "",
        "targetTeam": target,
    }


def _request(
    round_no: int,
    roles: list[dict[str, Any]],
    robots: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "roundNo": round_no,
        "mapInfo": {"width": 41, "height": 32, "zones": []},
        "teamOur": {
            "type": "challenger",
            "teamId": "t",
            "teamName": "t",
            "goldNum": 0,
            "totalScore": 0,
            "roles": roles,
        },
        "teamEnemy": {"roles": []},
        "robot": {"roles": robots or []},
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


def _view(round_no, roles, robots=None):
    return WorldModel().apply_round(parse_request(_request(round_no, roles, robots)))


STATION = _role(10013, "station", 20, 20, 1500, level=1)


def _rocket(x=21, y=20, cooldown=0, attack_range=10):
    return _role(
        10040, "rocket", x, y, 1000, level=1, attackPower=20, attackRange=attack_range,
        cooldown=cooldown,
    )


def test_night_weapon_attacks_robot_in_range() -> None:
    """Given 夜晚、武器有操控者且机器人在射程内，When 规划防御，Then 发出 attack。"""
    roles = [STATION, _rocket(), _role(10010, "worker", 22, 20, 220)]
    view = _view(NIGHT_ROUND, roles, [_robot(30001, "smallRobot", 25, 20)])
    cmd = plan_defense(view)[10040]
    assert enum_to_str(cmd.action) == "attack"
    assert cmd.controllerId == "10010"
    assert cmd.targetPos == (Pos(25, 20),)


def test_day_has_no_attack() -> None:
    """Given 白天，When 规划防御，Then 不产生任何指令（攻击仅夜晚，§4.4）。"""
    roles = [STATION, _rocket(), _role(10010, "worker", 22, 20, 220)]
    view = _view(DAY_ROUND, roles, [_robot(30001, "smallRobot", 25, 20)])
    assert plan_defense(view) == {}


def test_weapon_on_cooldown_does_not_attack() -> None:
    """Given 武器冷却中，When 规划防御，Then 不发出攻击。"""
    roles = [STATION, _rocket(cooldown=3), _role(10010, "worker", 22, 20, 220)]
    view = _view(NIGHT_ROUND, roles, [_robot(30001, "smallRobot", 25, 20)])
    assert 10040 not in plan_defense(view)


def test_robot_out_of_range_no_attack() -> None:
    """Given 机器人超出武器射程，When 规划防御，Then 不开火。"""
    roles = [STATION, _rocket(attack_range=3), _role(10010, "worker", 22, 20, 220)]
    view = _view(NIGHT_ROUND, roles, [_robot(30001, "smallRobot", 30, 20)])
    assert 10040 not in plan_defense(view)


def test_role_moves_toward_unmanned_weapon() -> None:
    """Given 武器无操控者在旁，When 规划防御，Then 派最近角色朝武器移动。"""
    roles = [STATION, _rocket(), _role(10010, "worker", 25, 20, 220)]
    view = _view(NIGHT_ROUND, roles, [_robot(30001, "smallRobot", 35, 20)])
    commands = plan_defense(view)
    assert enum_to_str(commands[10010].action) == "move"
    step = commands[10010].targetPos[0]
    assert chebyshev(step, Pos(25, 20)) == 1
    assert chebyshev(step, Pos(21, 20)) < chebyshev(Pos(25, 20), Pos(21, 20))


def test_two_weapons_do_not_share_controller() -> None:
    """Given 两座武器，When 规划防御，Then 各自分配不同操控者。"""
    roles = [
        STATION,
        _rocket(21, 20),
        _role(10030, "railgun", 21, 22, 1000, level=1, attackPower=10, attackRange=6),
        _role(10010, "worker", 22, 20, 220),
        _role(10012, "worker", 22, 22, 220),
    ]
    view = _view(
        NIGHT_ROUND,
        roles,
        [_robot(30001, "smallRobot", 25, 20), _robot(30002, "smallRobot", 25, 22)],
    )
    commands = plan_defense(view)
    controllers = [
        c.controllerId for c in commands.values() if enum_to_str(c.action) == "attack"
    ]
    assert len(controllers) == len(set(controllers))
    assert set(controllers) == {"10010", "10012"}


def test_idle_role_returns_toward_base() -> None:
    """Given 空闲角色远离基地，When 规划防御，Then 朝基地移动。"""
    roles = [STATION, _role(10010, "worker", 35, 30, 220)]
    view = _view(NIGHT_ROUND, roles, [])
    commands = plan_defense(view)
    assert enum_to_str(commands[10010].action) == "move"


def test_target_priority_prefers_boss_over_nearer_small() -> None:
    """Given BOSS 与更近的小型机都在射程内，When 选目标，Then 优先 BOSS。"""
    roles = [STATION, _rocket(), _role(10010, "worker", 22, 20, 220)]
    robots = [
        _robot(30001, "smallRobot", 24, 20),
        _robot(30002, "bossRobot", 28, 20, health=800),
    ]
    view = _view(NIGHT_ROUND, roles, robots)
    assert plan_defense(view)[10040].targetPos == (Pos(28, 20),)


def test_overkill_avoidance_assigns_distinct_targets() -> None:
    """Given 两武器可击杀两机器人，When 选目标，Then 各打不同目标（防溢出）。"""
    roles = [
        STATION,
        _role(10030, "railgun", 21, 20, 1000, level=1, attackPower=20, attackRange=10),
        _role(10040, "rocket", 21, 22, 1000, level=1, attackPower=20, attackRange=10),
        _role(10010, "worker", 22, 20, 220),
        _role(10012, "worker", 22, 22, 220),
    ]
    robots = [
        _robot(30001, "smallRobot", 25, 20, health=20),
        _robot(30002, "smallRobot", 25, 22, health=20),
    ]
    view = _view(NIGHT_ROUND, roles, robots)
    targets = [
        c.targetPos[0]
        for c in plan_defense(view).values()
        if enum_to_str(c.action) == "attack"
    ]
    assert len(targets) == 2
    assert len(set(targets)) == 2


# ------------------------------------------- 目标位置数按武器等级（接口 §2.2 注）


def _multi_target_view(*, kind: str, level: int, robots: list[dict]) -> Any:
    """一座武器 + 一名操控角色：射程内目标数由 ``robots`` 决定。

    武器放在 (21,20)、操控者在 (22,20)，机器人沿 x 轴排开 —— 目标排序只看
    （优先级, 距离, id），所以期望值可以逐格写死。
    """
    roles = [
        STATION,
        _role(10040, kind, 21, 20, 1000, level=level, attackPower=20, attackRange=10),
        _role(10010, "worker", 22, 20, 220),
    ]
    return _view(NIGHT_ROUND, roles, robots)


def test_level_one_weapon_sends_exactly_one_target_position() -> None:
    """Given 火箭 L1、射程内有两个机器人，When 规划防御，
    Then ``targetPos`` 仍然只有 **1** 个位置（与升级前的线上行为逐字节一致）。

    硬约束：即便我对接口文档的理解有偏差，L1 必须完全不动 —— 真机上武器现在
    全是 L1，任何形状变化都会直接变成判题器的指令非法。
    """
    robots = [_robot(30001, "smallRobot", 24, 20), _robot(30002, "smallRobot", 25, 20)]
    cmd = plan_defense(_multi_target_view(kind="rocket", level=1, robots=robots))[10040]
    assert cmd.targetPos == (Pos(24, 20),), cmd.targetPos


def test_level_two_gatling_sends_one_position_per_level() -> None:
    """Given 加特林 L2、射程内有两个机器人，When 规划防御，
    Then ``targetPos`` 有 **2** 个位置（文档：位置数 = 当前武器等级数）。

    线上武器全是 L1 时这条从没暴露；一旦买券升级，形状不对就可能被判非法。
    选目标仍走既有优先级（优先级 → 距离 → id）。
    """
    robots = [_robot(30001, "smallRobot", 24, 20), _robot(30002, "smallRobot", 25, 20)]
    cmd = plan_defense(_multi_target_view(kind="gatling", level=2, robots=robots))[10040]
    assert cmd.targetPos == (Pos(24, 20), Pos(25, 20)), cmd.targetPos


def test_level_two_weapon_with_one_target_sends_only_one_position() -> None:
    """Given 加特林 L2、射程内**只有 1 个**机器人，When 规划防御，
    Then 只发 1 个位置（目标不够等级数时退回今天的合法形状）。

    文档只规定「位置数 = 等级」，没说目标不足时能否补空位；宁可少打一轮也不发
    可疑数组 —— 与「L1 恒 1」同一条保守原则。
    """
    robots = [_robot(30001, "smallRobot", 24, 20)]
    cmd = plan_defense(_multi_target_view(kind="gatling", level=2, robots=robots))[10040]
    assert cmd.targetPos == (Pos(24, 20),), cmd.targetPos


def test_level_two_railgun_still_sends_one_position() -> None:
    """Given 电磁狙击炮 L2、射程内有两个机器人，When 规划防御，
    Then ``targetPos`` 仍然只有 1 个位置（文档：狙击炮只传 1 个）。

    这是「按等级发放」的例外分支：不能把等级规则套到全部武器上。
    """
    robots = [_robot(30001, "smallRobot", 24, 20), _robot(30002, "smallRobot", 25, 20)]
    cmd = plan_defense(_multi_target_view(kind="railgun", level=2, robots=robots))[10040]
    assert cmd.targetPos == (Pos(24, 20),), cmd.targetPos


def test_level_three_weapon_with_two_targets_sends_one_position() -> None:
    """Given 火箭 L3、射程内只有 2 个机器人，When 规划防御，
    Then 只发 **1** 个位置，绝不发「长度 2 的数组给 L3 武器」这种灰色形状。

    返回值只允许是 1 或「恰好等于等级」：文档既没允许也没禁止中间长度，赌不起。
    """
    robots = [_robot(30001, "smallRobot", 24, 20), _robot(30002, "smallRobot", 25, 20)]
    cmd = plan_defense(_multi_target_view(kind="rocket", level=3, robots=robots))[10040]
    assert cmd.targetPos == (Pos(24, 20),), cmd.targetPos


def test_level_three_gatling_sends_three_positions() -> None:
    """Given 加特林 L3、射程内有 3 个机器人，When 规划防御，Then 发 **3** 个位置。"""
    robots = [
        _robot(30001, "smallRobot", 24, 20),
        _robot(30002, "smallRobot", 25, 20),
        _robot(30003, "smallRobot", 26, 20),
    ]
    cmd = plan_defense(_multi_target_view(kind="gatling", level=3, robots=robots))[10040]
    assert cmd.targetPos == (Pos(24, 20), Pos(25, 20), Pos(26, 20)), cmd.targetPos


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
