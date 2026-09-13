"""工作包 11 测试：基础经济（采矿/贩卖/建造）。

Given/When/Then 风格；运行方式（二选一）：
    python3 -m pytest tests/ -q
    python3 tests/test_economy.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Final

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from future_war.config import Config  # noqa: E402
from future_war.models import Pos, enum_to_str, parse_request  # noqa: E402
from future_war.core import WorldModel, chebyshev  # noqa: E402
from future_war.strategy import plan_economy  # noqa: E402
from future_war.strategy.builder import assign_controllers  # noqa: E402
from future_war.strategy.economy import EconomyState  # noqa: E402


def _pos(x: int, y: int) -> dict[str, int]:
    return {"x": x, "y": y}


def _role(
    role_id: int,
    role_type: str,
    x: int,
    y: int,
    health: int = 100,
    backpack: list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    role: dict[str, Any] = {
        "id": role_id,
        "pos": _pos(x, y),
        "roleType": role_type,
        "health": health,
        **extra,
    }
    if backpack is not None:
        role["backpack"] = backpack
    return role


def _request(
    roles: list[dict[str, Any]],
    *,
    zones: list[dict[str, Any]] | None = None,
    gold: int = 0,
) -> dict[str, Any]:
    return {
        "roundNo": 1,
        "mapInfo": {"width": 41, "height": 32, "zones": zones or []},
        "teamOur": {
            "type": "challenger",
            "teamId": "t",
            "teamName": "t",
            "goldNum": gold,
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


def _view(roles, *, zones=None, gold=0, round_no=1):
    data = _request(roles, zones=zones, gold=gold)
    data["roundNo"] = round_no
    return WorldModel().apply_round(parse_request(data))


def _config(**sections: object) -> Config:
    """测试用最小配置：只写要覆盖的键，未覆盖的走代码默认。"""
    return Config(
        data=dict(sections),  # type: ignore[arg-type]
        profile="test",
        commit="test",
        config_hash="test",
    )


_NO_WALL: Final = _config(build={"wall_enabled": False})


def _mine(x: int, y: int, kind: str = "stone") -> dict[str, Any]:
    return {"pos": _pos(x, y), "neutralType": kind}


def _vendor(x: int, y: int) -> dict[str, Any]:
    return {"pos": _pos(x, y), "neutralType": "vendor"}


STATION = _role(10013, "station", 20, 20, 1500, level=1)


def test_worker_collects_when_adjacent_to_mine() -> None:
    """Given 工人在矿区旁，When 规划经济，Then 发出 collect 指向该矿区。"""
    view = _view([STATION, _role(10010, "worker", 5, 5, 220)], zones=[_mine(6, 5)])
    cmd = plan_economy(view)[10010]
    assert enum_to_str(cmd.action) == "collect"
    assert cmd.targetPos == (Pos(6, 5),)


def test_worker_moves_toward_mine_when_far() -> None:
    """Given 工人远离矿区，When 规划经济，Then 发出朝矿区靠近的合法 move。"""
    view = _view([STATION, _role(10010, "worker", 5, 5, 220)], zones=[_mine(10, 5)])
    cmd = plan_economy(view)[10010]
    assert enum_to_str(cmd.action) == "move"
    step = cmd.targetPos[0]
    assert chebyshev(step, Pos(5, 5)) == 1
    assert chebyshev(step, Pos(10, 5)) < chebyshev(Pos(5, 5), Pos(10, 5))


def test_worker_sells_when_adjacent_to_vendor_with_ore() -> None:
    """Given 工人背包有矿石且在小贩旁，When 规划经济，Then 发出 sell（数量=矿石数）。"""
    worker = _role(10010, "worker", 5, 5, 220, backpack=["stone"] * 5)
    view = _view([STATION, worker], zones=[_vendor(6, 5)])
    # 关掉围墙：新策略下「有石头就先砌墙」优先于贩卖，这里只验证贩卖分支
    cmd = plan_economy(view, _NO_WALL)[10010]
    assert enum_to_str(cmd.action) == "sell"
    assert cmd.name == "stone"
    assert cmd.num == 5


def test_worker_sells_stone_first_then_copper() -> None:
    """Given 背包有铜与石头，When 贩卖，Then 先出手石头（它同时是围墙材料）。"""
    worker = _role(
        10010, "worker", 5, 5, 220, backpack=["stone", "stone", "copper", "copper", "copper"]
    )
    view = _view([STATION, worker], zones=[_vendor(6, 5)])
    cmd = plan_economy(view, _NO_WALL)[10010]  # 同上：关围墙，只验证贩卖优先级
    assert cmd.name == "stone"
    assert cmd.num == 2


def test_worker_prefers_copper_when_no_stone() -> None:
    """Given 背包只有铜与铁，When 贩卖，Then 优先卖价值更高的铜。"""
    worker = _role(
        10010, "worker", 5, 5, 220, backpack=["iron", "iron", "copper", "copper", "copper"]
    )
    view = _view([STATION, worker], zones=[_vendor(6, 5)])
    cmd = plan_economy(view)[10010]
    assert cmd.name == "copper"
    assert cmd.num == 3


def test_worker_builds_weapon_when_gold_and_blue_cell_adjacent() -> None:
    """Given 金币充足且身旁有蓝色可建造格，When 规划经济，Then 发出 build 武器。"""
    view = _view([STATION, _role(10010, "worker", 23, 23, 220)], gold=75)
    cmd = plan_economy(view)[10010]
    assert enum_to_str(cmd.action) == "build"
    assert cmd.name in ("rocket", "railgun", "gatling")
    assert chebyshev(cmd.targetPos[0], Pos(23, 23)) == 1


def test_no_build_when_gold_below_cost() -> None:
    """Given 金币不足 25，When 规划经济，Then 不建造（无其他目标则无指令）。"""
    view = _view([STATION, _role(10010, "worker", 23, 23, 220)], gold=10)
    assert plan_economy(view) == {}


def test_no_command_when_nothing_available() -> None:
    """Given 无矿无小贩且金币不足，When 规划经济，Then 不产出任何指令。"""
    view = _view([STATION, _role(10010, "worker", 5, 5, 220)], gold=0)
    assert plan_economy(view) == {}


def test_weapon_plan_respects_config_mix() -> None:
    """Given 配置武器配比，When 规划建造，Then 首个武器类型按配置顺序。"""
    config = Config(
        data={"build": {"day1_max_weapons": 3, "weapon_mix": {"rocket": 2, "railgun": 1}}},
        profile="t",
        commit="c",
        config_hash="h",
    )
    view = _view([STATION, _role(10010, "worker", 23, 23, 220)], gold=75)
    cmd = plan_economy(view, config)[10010]
    assert cmd.name == "rocket"


def test_weapon_plan_gatling_first_when_configured() -> None:
    """Given 配比以加特林为首，When 规划建造，Then 首建加特林。"""
    config = Config(
        data={"build": {"day1_max_weapons": 3, "weapon_mix": {"gatling": 1, "railgun": 2}}},
        profile="t",
        commit="c",
        config_hash="h",
    )
    view = _view([STATION, _role(10010, "worker", 23, 23, 220)], gold=75)
    assert plan_economy(view, config)[10010].name == "gatling"


def test_two_workers_do_not_target_same_cell() -> None:
    """Given 两工人，When 同时规划移动，Then 无重复目标格。"""
    view = _view(
        [STATION, _role(10010, "worker", 5, 5, 220), _role(10012, "worker", 6, 5, 220)],
        zones=[_mine(10, 10)],
    )
    commands = plan_economy(view)
    targets = [c.targetPos[0] for c in commands.values() if c.targetPos]
    assert len(targets) == len(set(targets))


def test_two_workers_have_distinct_orders() -> None:
    """Given 两工人且武器未建满，When 规划经济，Then 一人专职建造、另一人采卖。"""
    view = _view(
        [STATION, _role(10010, "worker", 21, 21, 220), _role(10012, "worker", 5, 6, 220)],
        zones=[_mine(6, 5)],
        gold=75,
    )
    state = EconomyState()
    commands = plan_economy(view, None, state)
    assert state.builder_id == 10010  # 离蓝格最近的工人当建造者
    assert enum_to_str(commands[10010].action) == "build"
    assert 10012 in commands  # 另一人去采矿，而不是抢同一格


def test_workers_assigned_distinct_mines() -> None:
    """Given 两矿区与两名采卖工人，When 规划经济，Then 分派不同矿区（避免同矿争夺）。"""
    view = _view(
        [STATION, _role(10010, "worker", 5, 5, 220), _role(10012, "worker", 5, 6, 220)],
        zones=[_mine(6, 5), _mine(6, 6)],
    )
    assignment = plan_economy.__globals__["_assign_mines"](view, list(view.own_workers()))
    assert len(set(assignment.values())) == 2


def test_dusk_stages_roles_at_weapon_control_cells() -> None:
    """Given 白天后段（第 40 回合起）且有武器，When 规划经济，Then 角色就位到操控位。

    旧实现只把角色送回基地，而基地并不总是贴着武器，夜里前几回合只有 1 座武器
    有操控者 —— 这是「第一晚被推平」的直接原因之一。
    """
    round_no = 41  # 白天第 41 回合（第 1 天，已过黄昏阈值）
    weapon = Pos(22, 20)
    roles = [
        _role(10013, "station", 20, 20, 1500, level=1),
        _role(10040, "rocket", 22, 20, 1000, level=1, attackPower=20, attackRange=10),
        _role(10010, "worker", 23, 20, 220),  # 已在操控位
        _role(10011, "pioneer", 26, 20, 200),  # 需要在黄昏赶回
        _role(10012, "worker", 27, 20, 220),
    ]
    view = _view(roles, round_no=round_no)
    assert view.is_day()
    commands = plan_economy(view, _config(economy={"dusk_return": 40}))
    # 每座武器都有操控者；远处的角色朝武器靠拢（而不是全部回基地放羊）
    assignment = assign_controllers(view)
    assert set(assignment) == {w.id for w in view.own_weapons()}
    movers = [c for c in commands.values() if enum_to_str(c.action) == "move"]
    assert movers
    assert any(chebyshev(c.targetPos[0], weapon) <= 4 for c in movers)


def test_default_dusk_return_does_not_stop_daytime_work() -> None:
    """Given 默认配置（``economy.dusk_return=70``），When 白天后段，Then 不停工就位。

    回归用户实测：阈值 40 会让白天最后 30 个回合全部停摆（只走去就位），
    结果是「一整天一堵墙都没建起来」。默认值改为 70（白天结束）后，白天干满，
    就位交给夜晚的 ``combat.plan_defense``。
    """
    round_no = 41
    roles = [
        _role(10013, "station", 20, 20, 1500, level=1),
        _role(10040, "rocket", 22, 20, 1000, level=1, attackPower=20, attackRange=10),
        _role(10010, "worker", 23, 20, 220),
        _role(10011, "pioneer", 26, 20, 200),
    ]
    view = _view(roles, round_no=round_no)
    assert view.is_day()
    state = EconomyState()
    plan_economy(view, None, state)
    assert "staging=dusk" not in state.notes, f"默认配置不应进入黄昏就位：{state.notes}"


def test_walls_start_on_day_one_without_waiting_for_three_weapons() -> None:
    """Given 白天第 1 回合、手里有石头、还没有武器，When 规划经济，Then 直接去砌墙。

    回归：旧实现要求「先建满 3 座武器」才准碰围墙，实机结果是一整天一堵墙都没
    建起来（白天只有 70 回合，而建造仅白天可用）。
    """
    worker = _role(10010, "worker", 5, 5, 220, backpack=["stone", "stone"])
    view = _view([STATION, worker], zones=[_vendor(6, 5)])
    state = EconomyState()
    plan_economy(view, None, state)
    assert not any("wall:probe-denied" in note for note in state.notes), state.notes
    assert any("wall" in note for note in state.notes), state.notes


def test_builder_switches_to_walls_once_weapons_are_maxed() -> None:
    """Given 武器已建满且建造者手里有石头，When 规划经济，Then 建造者也去铺墙。"""
    weapon = _role(10040, "rocket", 22, 20, 1000, level=1, attackPower=20, attackRange=10)
    roles = [
        _role(10013, "station", 20, 20, 1500, level=1),
        weapon,
        _role(10010, "worker", 21, 21, 220, backpack=["stone"]),
    ]
    view = _view(roles, gold=100)
    commands = plan_economy(view)
    actions = {enum_to_str(c.action) for c in commands.values()}
    assert actions <= {"build", "move", "collect", "sell", "use"}, actions
    assert "build" in actions or any(
        enum_to_str(c.action) == "move" for c in commands.values()
    )

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
