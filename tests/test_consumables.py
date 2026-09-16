"""小策略测试：消耗品与保命（`docs/策略设计.md` §4.2 S1–S5）。

Given/When/Then 风格；运行方式（二选一）：
    python3 -m pytest tests/ -q
    python3 tests/test_consumables.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from future_war.config import Config  # noqa: E402
from future_war.models import Pos, enum_to_str, parse_request  # noqa: E402
from future_war.core import WorldModel  # noqa: E402
from future_war.strategy import ConsumableState, plan_consumables  # noqa: E402

DAY = 10
NIGHT = 85
SHOP = [{"pos": {"x": 25, "y": 20}, "neutralType": "weaponShop"}]


def _pos(x: int, y: int) -> dict[str, int]:
    return {"x": x, "y": y}


def _role(
    rid: int,
    rtype: str,
    x: int,
    y: int,
    health: int = 220,
    backpack: list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    role: dict[str, Any] = {
        "id": rid,
        "pos": _pos(x, y),
        "roleType": rtype,
        "health": health,
        "backPackCapability": 100,
        **extra,
    }
    if backpack is not None:
        role["backpack"] = backpack
    return role


def _robot(rid: int, x: int, y: int, health: int = 40) -> dict[str, Any]:
    return {
        "id": rid,
        "pos": _pos(x, y),
        "roleType": "smallRobot",
        "health": health,
        "abnormalState": "",
        "targetTeam": "challenger",
    }


def _view(
    *,
    roles: list[dict[str, Any]],
    robots: list[dict[str, Any]] | None = None,
    gold: int = 0,
    round_no: int = DAY,
    zones: list[dict[str, Any]] | None = None,
):
    data = {
        "roundNo": round_no,
        "mapInfo": {"width": 41, "height": 32, "zones": zones or SHOP},
        "teamOur": {
            "type": "challenger",
            "teamId": "t",
            "teamName": "t",
            "goldNum": gold,
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
    return WorldModel().apply_round(parse_request(data))


def _config(**consumables) -> Config:
    return Config(data={"consumables": consumables}, profile="t", commit="c", config_hash="h")


STATION = _role(10013, "station", 20, 20, 1500, level=1)


# ------------------------------------------------------------------ 使用


def test_uses_medicine_when_hurt() -> None:
    """Given 工人残血且背包有药剂，When 规划消耗品，Then 喝药回血。"""
    worker = _role(10010, "worker", 5, 5, 100, backpack=["Medicine"])
    cmd = plan_consumables(_view(roles=[STATION, worker]))[10010]
    assert enum_to_str(cmd.action) == "use"
    assert cmd.name == "Medicine"


def test_does_not_waste_medicine_when_healthy() -> None:
    """Given 工人满血且背包有药剂，When 规划，Then 不喝（留给真正残血时）。"""
    worker = _role(10010, "worker", 5, 5, 220, backpack=["Medicine"])
    assert plan_consumables(_view(roles=[STATION, worker])) == {}


def test_repairs_damaged_adjacent_wall() -> None:
    """Given 身旁围墙残血且背包有修复包，When 规划，Then 修复该墙。"""
    roles = [
        STATION,
        _role(10010, "worker", 20, 21, 220, backpack=["WallFixer"]),
        _role(40000, "wall", 20, 22, 300, level=1),
    ]
    cmd = plan_consumables(_view(roles=roles))[10010]
    assert enum_to_str(cmd.action) == "use"
    assert cmd.name == "WallFixer"
    assert cmd.targetPos == (Pos(20, 22),)


def test_uses_bomb_on_robot_cluster() -> None:
    """Given 机器人成团且手上有范围炸弹，When 规划，Then 朝集群中心投弹。"""
    worker = _role(10010, "worker", 10, 10, 220, backpack=["Bomb"])
    robots = [_robot(30001, 12, 10), _robot(30002, 12, 11), _robot(30003, 13, 10)]
    cmd = plan_consumables(_view(roles=[STATION, worker], robots=robots))[10010]
    assert enum_to_str(cmd.action) == "use"
    assert cmd.name == "Bomb"
    assert cmd.targetPos[0] in {Pos(12, 10), Pos(12, 11), Pos(13, 10)}


def test_uses_dizzy_on_bigger_cluster() -> None:
    """Given 3 只以上机器人成团且手上有眩晕法宝，When 规划，Then 眩晕该集群。"""
    worker = _role(10010, "worker", 10, 10, 220, backpack=["DizzyWeapon"])
    robots = [_robot(i, 12, 10 + i - 30001) for i in range(30001, 30005)]
    cmd = plan_consumables(
        _view(roles=[STATION, worker], robots=robots),
        _config(dizzy_min_robots=3),
    )[10010]
    assert enum_to_str(cmd.action) == "use"
    assert cmd.name == "DizzyWeapon"


def test_bomb_not_wasted_on_single_robot() -> None:
    """Given 只有 1 只机器人，When 规划，Then 不投弹（100 金打 1 只不值）。"""
    worker = _role(10010, "worker", 10, 10, 220, backpack=["Bomb"])
    robots = [_robot(30001, 12, 10)]
    assert plan_consumables(_view(roles=[STATION, worker], robots=robots)) == {}


def test_no_consumables_at_night() -> None:
    """Given 夜晚，When 规划消耗品，Then 什么都不做（角色要操控武器）。"""
    worker = _role(10010, "worker", 5, 5, 100, backpack=["Medicine"])
    view = _view(roles=[STATION, worker], round_no=NIGHT)
    assert plan_consumables(view) == {}


# ------------------------------------------------------------------ 购买


def test_buys_medicine_when_weapons_done_and_hurt() -> None:
    """Given 武器已满 + 有残血角色 + 余钱，When 工人在商店旁，Then 买药剂。"""
    roles = [
        STATION,
        _role(10040, "rocket", 21, 20, 1000, level=1, attackRange=10),
        _role(10030, "railgun", 22, 20, 1000, level=1, attackRange=6),
        _role(10020, "gatling", 23, 20, 1000, level=1, attackRange=3),
        _role(10010, "worker", 24, 20, 100, backpack=[]),
    ]
    state = ConsumableState()
    commands = plan_consumables(_view(roles=roles, gold=200), None, state)
    assert enum_to_str(commands[10010].action) == "buy"
    assert commands[10010].name == "Medicine"
    assert state.bought.get("Medicine") == 1


def test_does_not_buy_consumables_before_weapons_done() -> None:
    """Given 武器还没建满，When 工人在商店旁，Then 一分钱都不花（先武器）。"""
    roles = [
        STATION,
        _role(10040, "rocket", 21, 20, 1000, level=1, attackRange=10),
        _role(10010, "worker", 24, 20, 100, backpack=[]),
    ]
    assert plan_consumables(_view(roles=roles, gold=500)) == {}


def test_respects_emergency_reserve() -> None:
    """Given 金币只够应急金，When 有残血角色，Then 不买道具。"""
    roles = [
        STATION,
        _role(10040, "rocket", 21, 20, 1000, level=1, attackRange=10),
        _role(10030, "railgun", 22, 20, 1000, level=1, attackRange=6),
        _role(10020, "gatling", 23, 20, 1000, level=1, attackRange=3),
        _role(10010, "worker", 24, 20, 100, backpack=[]),
    ]
    config = Config(
        data={
            "consumables": {},
            "economy": {"emergency_reserve": 200},
        },
        profile="t",
        commit="c",
        config_hash="h",
    )
    assert plan_consumables(_view(roles=roles, gold=200), config) == {}


# ------------------------------------------------------------------ 撤退


def test_retreating_roles_marks_hurt_units() -> None:
    """Given 一个残血与一个健康角色，When 计算撤退名单，Then 只含残血者。"""
    from future_war.strategy.combat import retreating_roles

    roles = [STATION, _role(10010, "worker", 5, 5, 60), _role(10012, "worker", 6, 5, 220)]
    view = _view(roles=roles)
    assert retreating_roles(view, None) == frozenset({10010})


def test_retreat_can_be_disabled() -> None:
    """Given 配置关闭撤退，When 计算，Then 名单为空。"""
    from future_war.strategy.combat import retreating_roles

    roles = [STATION, _role(10010, "worker", 5, 5, 10)]
    view = _view(roles=roles)
    config = Config(data={"offense": {"retreat_hp_ratio": 0.0}}, profile="t", commit="c", config_hash="h")
    assert retreating_roles(view, config) == frozenset()


def test_buys_wall_fixer_before_weapons_done_when_wall_hurt() -> None:
    """Given 武器没建满（只有 1 座）、一面墙残血、队里没有修复包、只有 10 金，
    When 工人在商店旁，Then 买修复包（10 金止损优先于留钱建武器）。

    回归线上实测：修复包与其它道具共用 ``economy.emergency_reserve``（100 金），
    而墙优先模式金币常年 10~20 → 整局没有买过一个修复包，残血墙只能眼睁睁被打掉。
    """
    roles = [
        STATION,
        _role(10040, "rocket", 21, 20, 1000, level=1, attackRange=10),
        _role(40000, "wall", 22, 22, 300, level=1),  # 残血：夜里挨过打
        _role(10010, "worker", 24, 20, 220, backpack=[]),
    ]
    state = ConsumableState()
    commands = plan_consumables(_view(roles=roles, gold=10), None, state)
    assert enum_to_str(commands[10010].action) == "buy"
    assert commands[10010].name == "WallFixer"
    assert state.bought.get("WallFixer") == 1


def test_wall_fixer_reserve_blocks_buying() -> None:
    """Given ``consumables.wall_fixer_reserve`` 抬到 10 金，When 手里正好 10 金且有残血墙，
    Then 不买（预留金把修复包挡在预算之外，运维旋钮生效）。"""
    roles = [
        STATION,
        _role(10040, "rocket", 21, 20, 1000, level=1, attackRange=10),
        _role(40000, "wall", 22, 22, 300, level=1),
        _role(10010, "worker", 24, 20, 220, backpack=[]),
    ]
    config = _config(wall_fixer_reserve=10)
    assert plan_consumables(_view(roles=roles, gold=10), config) == {}


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
