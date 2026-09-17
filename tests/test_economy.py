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
from future_war.strategy.economy import EconomyState, _wall_candidates  # noqa: E402


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


def _shop(x: int, y: int) -> dict[str, Any]:
    return {"pos": _pos(x, y), "neutralType": "weaponShop"}


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
    """Given 金币不足 25，When 规划经济，Then 不建造（退化为保底移动，绝不静默发呆）。

    为什么不再断言「空指令集」：线上事故里「两个工人完全不移动」就是空指令集造成的
    —— 判题器只让没指令的单位原地不动，下一回合也没人会来修正。现在保底分支保证
    任何工人至少有一条指令。
    """
    view = _view([STATION, _role(10010, "worker", 23, 23, 220)], gold=10)
    commands = plan_economy(view)
    assert not any(c.name for c in commands.values()), "金币不足不该发出建造"
    assert 10010 in commands, "没有建造目标也必须退化出一条具体指令"


def test_no_command_when_nothing_available() -> None:
    """Given 无矿无小贩且金币不足，When 规划经济，Then 仍产出保底移动（不再零指令）。

    回归线上事故：工人既不能建造也不能移动时静默返回 ``(None, None)``，真机表现就是
    整局站着不动。保底口径 = 没有矿就走向任意可达空地。
    """
    view = _view([STATION, _role(10010, "worker", 5, 5, 220)], gold=0)
    commands = plan_economy(view)
    assert 10010 in commands
    cmd = commands[10010]
    assert enum_to_str(cmd.action) == "move"
    assert chebyshev(cmd.targetPos[0], Pos(5, 5)) == 1


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


def test_stone_is_reserved_for_walls_before_selling() -> None:
    """Given 有墙要修且背包石头刚够储备，When 贩卖，Then 留石头、改卖铜。

    回归：旧实现按「石头占地方又便宜」优先卖石头，把修墙的料也卖掉，于是
    「墙 0/12 + 金币 0」两头空。
    """
    worker = _role(
        10010, "worker", 5, 5, 220, backpack=["stone", "stone", "copper", "copper", "copper"]
    )
    view = _view([STATION, worker], zones=[_vendor(6, 5)])
    cmd = plan_economy(view)[10010]
    assert cmd.name == "copper", "石头在储备量内不该卖"
    assert cmd.num == 3


def test_surplus_stone_beyond_reserve_is_sold() -> None:
    """Given 石头超过储备量，When 贩卖，Then 只卖多出来的那部分（储备量按配置）。

    这条钉的是**旧行为**（``build.wall_first=false``）：储备量由 ``economy.stone_reserve``
    决定，超出部分换钱。墙优先模式改用按人头的囤石目标，见
    ``test_wall_first_mode_keeps_mining_stone_for_the_whole_team``。
    """
    worker = _role(10010, "worker", 5, 5, 220, backpack=["stone"] * 8)
    view = _view([STATION, worker], zones=[_vendor(6, 5)])
    for reserve, expected in ((2, 6), (4, 4), (6, 2)):
        config = _config(economy={"stone_reserve": reserve}, build={"wall_first": False})
        cmd = plan_economy(view, config)[10010]
        assert cmd.name == "stone"
        assert cmd.num == expected, f"储备 {reserve} 时应卖 {expected}，实际 {cmd.num}"


def test_sell_trip_waits_for_a_full_batch() -> None:
    """Given 只背了 1 块铜、未到成批量，When 规划，Then 不专程跑小贩，就地继续挖。"""
    worker = _role(10010, "worker", 5, 5, 220, backpack=["copper"])
    view = _view([STATION, worker], zones=[_vendor(30, 5), _mine(6, 5, "copper")])
    cmd = plan_economy(view)[10010]
    assert enum_to_str(cmd.action) == "collect", "一趟只换 1 金币不值得跑，先挖矿"


def _shop_view(*, gold: int, with_wall: bool, with_weapon: bool = True):
    """3 座武器已建满 → 有空闲工人可派去采购；采购者紧贴武器商店 (7,5)。"""
    roles = [_role(10013, "station", 20, 20, 1500, level=1)]
    if with_weapon:
        roles += [
            _role(10040, "railgun", 22, 20, 1000, level=1, attackPower=10, attackRange=6),
            _role(10041, "rocket", 24, 20, 1000, level=1, attackPower=20, attackRange=10),
            _role(10042, "gatling", 26, 20, 1000, level=1, attackPower=10, attackRange=3),
        ]
    if with_wall:
        roles.append(_role(10043, "wall", 19, 20, 1000, level=1))
    roles.append(_role(10010, "worker", 20, 18, 220))  # 离蓝区最近 → 建造者
    roles.append(_role(10011, "worker", 7, 6, 220))  # 紧贴商店 → 采购者
    return _view(roles, zones=[_vendor(30, 30), _shop(7, 5)], gold=gold)


def test_buys_cheap_wall_voucher_before_expensive_weapon_voucher() -> None:
    """Given 有 L1 武器与 L1 围墙、金币 25，When 采购，Then 买 20 金的围墙券。

    回归：旧实现只买 100 金的 WeaponUpgradeVoucher1，而真机金币峰值只有 45~60
    → 永远买不了任何东西、武器永远 level1，夜里被推平。
    """
    commands = plan_economy(_shop_view(gold=25, with_wall=True))
    buys = [c for c in commands.values() if enum_to_str(c.action) == "buy"]
    assert buys, f"应采购，实际指令 {[enum_to_str(c.action) for c in commands.values()]}"
    assert buys[0].name == "WallUpgradeVoucher1", buys[0].name


def test_no_voucher_when_no_matching_target() -> None:
    """Given 场上一座围墙都没有，When 只有 25 金，Then 不买围墙券（没有升级目标）。"""
    commands = plan_economy(_shop_view(gold=25, with_wall=False))
    assert not any(enum_to_str(c.action) == "buy" for c in commands.values())


def test_buys_weapon_voucher_once_gold_allows() -> None:
    """Given 金币 120（够 100 金武器券），When 围墙券不在候选或没有围墙目标，Then 买武器券。"""
    commands = plan_economy(_shop_view(gold=120, with_wall=False))
    buys = [c for c in commands.values() if enum_to_str(c.action) == "buy"]
    assert buys, "应采购武器升级券"
    assert buys[0].name == "WeaponUpgradeVoucher1", buys[0].name


def test_wall_trip_waits_for_a_stone_batch() -> None:
    """Given 背包只有 1 块石头，When 规划，Then 不跑墙线、继续采矿攒料。

    回归线上观察：工人「采一次石头就建一次墙」，来回跑把一整天耗光。原因是只要
    有 1 块石头、且没有待卖货，就被判定可以去墙线 —— 于是永远在挖→跑→砌→挖
    的循环里。改成攒够 ``build.wall_stone_batch``（默认 3）块才动身，一次到位连砌。
    """
    one = _role(10010, "worker", 5, 5, 220, backpack=["stone"])
    view1 = _view([STATION, one], zones=[_mine(6, 5)])
    cmd1 = plan_economy(view1)[10010]
    assert enum_to_str(cmd1.action) == "collect", f"只背 1 块不该跑墙线，实际 {cmd1.action}"

    three = _role(10010, "worker", 5, 5, 220, backpack=["stone"] * 3)
    view3 = _view([STATION, three], zones=[_mine(6, 5)])
    cmd3 = plan_economy(view3)[10010]
    assert enum_to_str(cmd3.action) == "move", f"攒够 3 块应动身去墙线，实际 {cmd3.action}"


def _wall_first_view(
    *,
    roles: list[dict[str, Any]],
    zones: list[dict[str, Any]],
    gold: int = 0,
) -> Any:
    """墙优先模式的公共视图构造：默认配置里 ``build.wall_first`` 已为 true。"""
    return _view(roles, zones=zones, gold=gold)


def test_wall_first_mode_keeps_mining_stone_for_the_whole_team() -> None:
    """Given 墙优先模式、武器已插满且还有 12 堵墙没修，When 队里两名工人都有铜/铁矿
    可采，Then 两人都继续采石（不因为「备够就变现」转去挖铜铁）。

    回归线上实测：墙能建但**太慢**。旧逻辑备够 ``economy.stone_reserve``（4 块）
    就转去挖铜/铁换钱，于是砌墙只剩一个人、还常常断料。墙优先模式把目标量提到
    ``wall_stone_batch × 工人数``（3×2=6），全队持续采石直到囤够。
    """
    roles = [
        _role(10013, "station", 20, 20, 1500, level=1),
        _role(10040, "railgun", 22, 22, 1000, level=1, attackPower=10, attackRange=6),
        _role(10041, "rocket", 24, 22, 1000, level=1, attackPower=20, attackRange=10),
        _role(10042, "gatling", 26, 22, 1000, level=1, attackPower=10, attackRange=3),
        _role(10010, "worker", 19, 21, 220),  # 贴着石矿、且不挨着黄区（不会顺路砌墙）
        _role(10012, "worker", 21, 19, 220),
    ]
    # 矿区都贴着工人：按旧规则（need_stone=False）会挑产值更高的铜/铁，
    # 墙优先模式必须改挑石矿 —— 这条测试即证伪「备够就变现」。
    zones = [
        _mine(18, 18, "copper"),
        _mine(22, 18, "iron"),
        _mine(20, 20, "stone"),
    ]
    view = _wall_first_view(roles=roles, zones=zones, gold=0)
    commands = plan_economy(view)

    collected = {
        uid: view.mine_at(cmd.targetPos[0])
        for uid, cmd in commands.items()
        if enum_to_str(cmd.action) == "collect" and cmd.targetPos
    }
    assert len(collected) == 2, f"两名工人都该去采石，实际 {collected}"
    assert set(collected.values()) == {"stone"}, f"墙优先模式不该转采铜铁：{collected}"


def test_wall_first_mode_sends_a_worker_to_the_wall_line() -> None:
    """Given 武器已插满且两名工人都攒够一批石头，When 规划经济，
    Then 至少有一名工人动身去墙线（另一人留下继续采石，避免两人同时在路上）。"""
    roles = [
        _role(10013, "station", 20, 20, 1500, level=1),
        _role(10040, "railgun", 22, 22, 1000, level=1, attackPower=10, attackRange=6),
        _role(10041, "rocket", 24, 22, 1000, level=1, attackPower=20, attackRange=10),
        _role(10042, "gatling", 26, 22, 1000, level=1, attackPower=10, attackRange=3),
        _role(10010, "worker", 10, 10, 220, backpack=["stone"] * 3),
        _role(10012, "worker", 30, 30, 220, backpack=["stone"] * 3),
    ]
    zones = [_mine(9, 9, "stone"), _mine(31, 31, "stone")]
    view = _wall_first_view(roles=roles, zones=zones, gold=0)
    commands = plan_economy(view)

    moves = [c for c in commands.values() if enum_to_str(c.action) == "move"]
    assert moves, f"攒够一批就该去墙线，实际指令 {[enum_to_str(c.action) for c in commands.values()]}"


def test_wall_first_mode_buys_wall_voucher_before_weapons_are_maxed() -> None:
    """Given 墙优先模式、只有 1 座武器但已有 L1 围墙与 25 金币，When 规划经济，
    Then 允许派人去买 20 金围墙券（用户取舍：墙 > 武器升级），且另一人仍在推进墙线。"""
    roles = [
        _role(10013, "station", 20, 20, 1500, level=1),
        _role(10040, "railgun", 22, 22, 1000, level=1, attackPower=10, attackRange=6),
        _role(10043, "wall", 19, 20, 1000, level=1),
        _role(10010, "worker", 20, 18, 220),  # 离蓝格最近 → 建造者
        _role(10011, "worker", 7, 6, 220, backpack=["stone"] * 3),  # 攒够一批 → 推进墙线
    ]
    view = _wall_first_view(roles=roles, zones=[_vendor(30, 30), _shop(7, 5)], gold=25)
    commands = plan_economy(view)

    buys = [c for c in commands.values() if enum_to_str(c.action) == "buy"]
    actions = [enum_to_str(c.action) for c in commands.values()]
    assert buys, f"武器未满也该买围墙券，实际 {actions}"
    assert buys[0].name == "WallUpgradeVoucher1", buys[0].name


def test_breach_cell_is_rebuilt_before_normal_ring_order() -> None:
    """Given 记忆里 (20,18) 是我们砌过的墙位、但现在那里没有墙（夜里被打掉），
    When 规划经济，Then 该破口排在正常「方环由内向外」候选之前，工人直接去重建。

    回归用户需求：夜晚墙被攻破后白天要**先补破口**。破口是全场唯一被敌人用行动
    证明过能打通的位置（机器人夜里沿同一条路再来），按纯环序铺墙会先去砌别的格。
    """
    from future_war.strategy.economy import _wall_candidates

    roles = [
        _role(10013, "station", 20, 20, 1500, level=1),
        _role(10010, "worker", 20, 17, 220, backpack=["stone"] * 3),
    ]
    view = _view(roles)
    plain = EconomyState()  # 无记忆的对照
    plain.wall_dir = (1, 0)  # 来袭方向朝东 → 破口 (20,18) 在北面（侧面），环序里靠后
    state = EconomyState(wall_memory={(20, 18)})
    state.wall_dir = (1, 0)

    # 对照：没有记忆时 (20,18) 只是普通候选，第一个要砌的不是它
    assert Pos(20, 18) in _wall_candidates(view, plain, set())
    assert _wall_candidates(view, plain, set())[0] != Pos(20, 18)
    # 有记忆 → 破口被排到最前
    assert _wall_candidates(view, state, set())[0] == Pos(20, 18)

    cmd = plan_economy(view, None, state)[10010]
    assert enum_to_str(cmd.action) == "build"
    assert cmd.name == "wall"
    assert cmd.targetPos == (Pos(20, 18),)
    assert any("wall_fix=breach(1)" in note for note in state.notes), state.notes


def test_breach_on_back_side_rearms_threat_dir() -> None:
    """Given 锁定的来袭方向是东、而破口出现在西侧（我们故意不建墙的背面），
    When 规划经济，Then 用破口位置覆盖来袭方向，并记一条 breach_rearm 诊断。

    锁定的方向判错就会让背面永久敞开（旧实现的死结）。破口是比机器人质心更硬的
    证据；同时要求不会每回合抖动 —— 改完方向后该格变成正面，不再触发。
    """
    from future_war.strategy.builder import wall_side_tier

    roles = [
        _role(10013, "station", 20, 20, 1500, level=1),
        _role(10010, "worker", 19, 20, 220, backpack=["stone"] * 3),
    ]
    view = _view(roles)
    state = EconomyState(wall_memory={(18, 20)})
    state.wall_dir = (1, 0)
    # 前提：在旧方向下 (18,20) 确实被判为背面（tier=None，不建）
    assert wall_side_tier(view, Pos(18, 20), (1, 0)) is None

    plan_economy(view, None, state)
    assert state.wall_dir == (-1, 0), "背面破口必须推翻来袭方向"
    assert state.threat_rearms == 1
    assert any(note.startswith("breach_rearm=") for note in state.notes), state.notes

    plan_economy(view, None, state)  # 再来一回合：方向已指向破口，不该再改
    assert state.wall_dir == (-1, 0)
    assert state.threat_rearms == 1


def test_gap_cell_is_detected_without_wall_memory() -> None:
    """Given 进程重启后没有任何墙位记忆，但墙线上 (23,20) 的左右两侧
    (23,19)/(23,21) 都已是己方围墙，When 规划经济，Then 该格被当作缺口优先重建。"""
    roles = [
        _role(10013, "station", 20, 20, 1500, level=1),
        _role(40001, "wall", 23, 19, 1000, level=1),
        _role(40002, "wall", 23, 21, 1000, level=1),
        _role(10010, "worker", 22, 20, 220, backpack=["stone"] * 3),
    ]
    view = _view(roles, gold=0)
    state = EconomyState()  # 零记忆
    state.wall_dir = (1, 0)

    cmd = plan_economy(view, None, state)[10010]
    assert enum_to_str(cmd.action) == "build"
    assert cmd.targetPos == (Pos(23, 20),), "两侧都有墙的空格 = 缺口，优先补"
    assert any("wall_fix=breach(0)|gap(1)" in note for note in state.notes), state.notes


def test_damaged_wall_worker_yields_turn_to_wall_fixer() -> None:
    """Given 工人背包里有修复包且身旁围墙残血（未毁），When 规划整回合，
    Then 该角色发出 use WallFixer（残血 → 用维修包；已毁才是花 1 石头重建）。

    经济指令永远先占住角色（planner 用 setdefault 合并），所以经济必须主动让路，
    否则修复包会一直躺在背包里 —— 与升级券「use 被吞」是同一个坑。
    """
    from future_war.strategy.planner import plan_turn

    roles = [
        _role(10013, "station", 20, 20, 1500, level=1),
        _role(40003, "wall", 20, 22, 300, level=1),
        _role(10010, "worker", 20, 21, 220, backpack=["WallFixer"]),
    ]
    view = _view(roles)

    cmd = plan_turn(view, None).commands[10010]
    assert enum_to_str(cmd.action) == "use"
    assert cmd.name == "WallFixer"
    assert cmd.targetPos == (Pos(20, 22),)


def test_shopper_is_sent_to_buy_wall_fixer_when_wall_damaged() -> None:
    """Given 墙优先模式、一面墙残血、队里没有修复包、只有 10 金（买不起任何升级券），
    When 规划经济，Then 留一人朝武器商店走 —— 到店即买 10 金修复包。

    旧实现里没有可买的券就没人去商店（修复包 10 金却整局没人买过），白天这条
    「修被打残的墙」的路就永远缺工具。
    """
    roles = [
        _role(10013, "station", 20, 20, 1500, level=1),
        _role(10040, "railgun", 22, 22, 1000, level=1, attackPower=10, attackRange=6),
        _role(10041, "rocket", 24, 22, 1000, level=1, attackPower=20, attackRange=10),
        _role(10042, "gatling", 26, 22, 1000, level=1, attackPower=10, attackRange=3),
        _role(40003, "wall", 20, 22, 300, level=1),  # 残血：夜里挨过打
        _role(10010, "worker", 19, 20, 220, backpack=["stone"] * 3),
        _role(10012, "worker", 30, 30, 220, backpack=[]),
    ]
    view = _wall_first_view(roles=roles, zones=[_shop(32, 32), _mine(18, 18)], gold=10)
    commands = plan_economy(view)

    moves = [c for c in commands.values() if enum_to_str(c.action) == "move"]
    assert any(
        chebyshev(c.targetPos[0], Pos(32, 32)) == 1 for c in moves if c.targetPos
    ), f"该有人专程去商店买修复包，实际 {[enum_to_str(c.action) for c in commands.values()]}"


def test_two_workers_always_get_a_command_without_any_mine() -> None:
    """Given 地图上一个矿都没有、金币为 0，When 规划经济，Then 两个工人各有具体指令。

    线上事故回归：工人整回合零指令 = 判题器让它原地不动，现场就是「两个工人完全
    不移动」。修法不是继续加策略，而是把「静默什么都不做」变成保底动作。
    """
    roles = [_role(10013, "station", 20, 20, 1500, level=1),
             _role(10010, "worker", 5, 5, 220),
             _role(10012, "worker", 30, 30, 220)]
    commands = plan_economy(_view(roles, gold=0))
    assert 10010 in commands and 10012 in commands, commands


def test_idle_worker_falls_back_to_a_free_step_without_station() -> None:
    """Given 连基地都没有（可建造区为空）且没有矿，When 规划经济，
    Then 工人仍退化为朝可达空地走一步（最后一档保底）。"""
    view = _view([_role(10010, "worker", 5, 5, 220)], gold=0)
    commands = plan_economy(view)
    assert 10010 in commands, "连空地目标都没有时必须退化为相邻空格"
    cmd = commands[10010]
    assert enum_to_str(cmd.action) == "move"
    assert chebyshev(cmd.targetPos[0], Pos(5, 5)) == 1


def test_trapped_worker_is_reported_as_idle_in_notes() -> None:
    """Given 工人四周被围墙堵死且无矿可采，When 规划经济，
    Then D-02 notes 给出 ``idle=<id>:trapped``（谁没有指令、为什么）。

    这是本次事故要求的诊断字段：真机日志一眼指认，不用再猜。
    """
    from future_war.strategy.economy import EconomyState

    roles = [_role(10010, "worker", 5, 5, 220)]
    for index, (dx, dy) in enumerate(
        [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)]
    ):
        roles.append(_role(40000 + index, "wall", 5 + dx, 5 + dy, 1000, level=1))
    state = EconomyState()
    commands = plan_economy(_view(roles, gold=0), None, state)
    assert 10010 not in commands, "被围死时确实无步可走"
    assert any("idle=10010:trapped" in note for note in state.notes), state.notes


def test_idle_worker_rescued_toward_reachable_mine_is_logged() -> None:
    """Given 工人本回合既不能建造也没有可用步（无矿、无基地），
    When 规划经济，Then notes 给出 ``rescue=<id>``（保底生效的证据）。"""
    from future_war.strategy.economy import EconomyState

    state = EconomyState()
    plan_economy(_view([_role(10010, "worker", 5, 5, 220)], gold=0), None, state)
    assert any("rescue=10010" in note for note in state.notes), state.notes


def test_wall_target_is_direction_and_progress_invariant() -> None:
    """Given 同一张地图、来袭方向从东改成西、且已经建好 7 堵墙，
    When 取围墙目标数（分母），Then 结果完全相等（方向与施工进度都不影响它）。

    回归线上事故（issue #2 B 项）：旧分母 = ``len(wall_line(...))``，而 ``wall_line``
    既排除**已建成的墙**、又按来袭方向只取三面，于是日志里同时出现
    ``walls=7/6 (done)``（分母被自己砌的墙吃掉了）和 ``walls=5/8``（方向一改分母就跳），
    施工被误判「完工」而停工。
    """
    from future_war.strategy.economy import EconomyState, _wall_target

    roles = [_role(10013, "station", 20, 20, 1500, level=1),
             _role(10010, "worker", 20, 17, 220, backpack=["stone"] * 3)]
    view = _view(roles)

    east = EconomyState(); east.wall_dir = (1, 0)
    west = EconomyState(); west.wall_dir = (-1, 0)
    assert _wall_target(view, east) == _wall_target(view, west), "分母不得随来袭方向变化"

    # 沿墙线先砌 7 堵墙（用户日志里 ``7/6`` 的那一刻）→ 分母仍不变
    line = _wall_candidates(view, east, set())
    assert len(line) >= 7, line
    built = [
        _role(40000 + index, "wall", cell.x, cell.y, 1000, level=1)
        for index, cell in enumerate(line[:7])
    ]
    progressed = _view(roles + built)
    assert _wall_target(progressed, east) == _wall_target(view, east), "砌墙不得让分母缩水"
    assert not any("done" in note for note in _wall_plan_notes(progressed, east)), (
        "砌了 7 堵墙还不该判完工（分母是固定 12）"
    )


def _wall_plan_notes(view, state):
    """跑一次 ``plan_economy`` 并取出本回合 D-02 notes（测试内小工具）。"""
    plan_economy(view, None, state)
    return state.notes


def test_wall_target_is_capped_by_config_wall_max() -> None:
    """Given ``build.wall_max = 5``，When 取围墙目标数，Then 分母封顶到 5（运维旋钮生效）。"""
    from future_war.strategy.economy import EconomyState, _wall_target

    view = _view([_role(10013, "station", 20, 20, 1500, level=1)])
    config = _config(build={"wall_max": 5})
    assert _wall_target(view, EconomyState(), config) == 5


def test_wall_done_when_no_candidate_cell_remains() -> None:
    """Given 推断黄区里每一格都已被判非法（再无候选），但墙数远小于分母，
    When 判断围墙是否收工，Then ``build.wall_done_when_no_cell=true`` 时收工、
    置 false 时仍认为没收工（保留对比实验的能力）。

    为什么要有这条：分母固定成四面总数（12）后，如果我们按 U 形只砌了三面、
    黄区里再无候选，旧写法会永远认为「没建满」→ ``wall_first`` 的采石管道永不关闭
    → 全队只采石不贩卖、金币锁死。
    """
    from future_war.strategy.economy import EconomyState, _wall_done

    view = _view([_role(10013, "station", 20, 20, 1500, level=1)])
    failed = {(c.x, c.y) for c in view.yellow_build_cells()}
    assert _wall_done(view, EconomyState(failed_build_cells=failed), None) is True, (
        "无候选格 → 收工"
    )
    off = _config(build={"wall_done_when_no_cell": False})
    assert _wall_done(view, EconomyState(failed_build_cells=set(failed)), off) is False, (
        "关掉该键则只在建满分母时收工"
    )


def test_continuity_prefers_cells_next_to_verified_wall() -> None:
    """Given (20,18) 是**已建成过**的墙位（真机证明过那一片能建），
    When 排候选，Then 与它相邻的候选格被提到最前（连续性优先）。

    回归线上事故（issue #2 C 项）：同一圈连续多格 ``result_false``、换到另一组相邻格
    才建成 —— 说明我们推断的黄区与真机有偏差，而真机的可建造区是**连通**的。沿已验证
    格铺开的命中率远高于按环盲扫，能显著减少白跑一趟的失败建造。
    """
    from future_war.strategy.economy import EconomyState

    roles = [_role(10013, "station", 20, 20, 1500, level=1),
             _role(40000, "wall", 20, 18, 1000, level=1)]  # 只此一堵：不构成「缺口」
    view = _view(roles)
    plain = EconomyState(); plain.wall_dir = (1, 0)
    state = EconomyState(); state.wall_dir = (1, 0)
    plan_economy(view, None, state)  # 跑一回合：活墙被登记为「已验证格」

    assert state.verified_wall_cells == {(20, 18)}, state.verified_wall_cells
    first_plain = _wall_candidates(view, plain, set())[0]
    first_verified = _wall_candidates(view, state, set())[0]
    assert chebyshev(first_plain, Pos(20, 18)) > 1, "前提：纯环序并不会先挑它的邻格"
    assert chebyshev(first_verified, Pos(20, 18)) == 1, (
        f"相邻候选应排最前，实际 {first_verified}"
    )

    # 关掉该键 → 回退纯环序（运维可对比）
    off = _config(build={"wall_continuity_first": False})
    assert _wall_candidates(view, state, set(), off)[0] == first_plain

    # 黑名单照旧压过连续性：被证伪的格不出现在候选里
    assert Pos(20, 18) not in _wall_candidates(view, state, {(20, 18)})


def test_no_mobile_units_reports_resource_gap_in_notes() -> None:
    """Given 场上只剩基地、没有任何可移动单位（夜里单位阵亡的窗口），
    When 规划经济，Then 不发任何指令，且 D-02 记下 ``mobile=w0,p0``。

    这是 issue #2 D 项的核实结论：第 2 天前 ~20 帧 ``orders=`` 空、``issued=none``
    的**资源性空窗** —— 单位阵亡后要等「次日白天开始后 20 回合」才在基地复活
    （§4.5.2），这段窗口里根本没人可调度，属预期行为、不改逻辑；加这行诊断是为了
    下一局一眼区分「没人可用」与「有人却不干活」。
    """
    from future_war.strategy.economy import EconomyState

    state = EconomyState()
    commands = plan_economy(_view([STATION]), None, state)
    assert commands == {}, "没有可移动单位时确实没有任何指令"
    assert any("mobile=w0,p0" in note for note in state.notes), state.notes
    assert "orders=" in state.notes, state.notes


def test_planner_drops_structural_illegal_command_and_reports_it() -> None:
    """Given 某模块产出结构非法指令（``use DizzyWeapon`` 缺 ``targetPos``），
    When 规划整回合，Then 该指令被丢弃、并以 ``illegal_dropped=`` 记入 D-02。

    判据与判题器共用 ``core/command_contract``（任务书 §八：字段缺失即「指令错误」，
    累计 5 次停止调度该队）。真机代价极高，所以宁可本地丢一条 + 大声记一行日志。
    """
    from future_war.models import RoleCommand
    from future_war.strategy.planner import _drop_structural_illegal

    commands = {
        10010: RoleCommand(action="use", name="DizzyWeapon"),  # 缺 targetPos → 非法
        10012: RoleCommand(action="move", targetPos=(Pos(5, 5),)),  # 合法
    }
    notes = _drop_structural_illegal(commands)
    assert set(commands) == {10012}, commands
    assert notes and notes[0].startswith("illegal_dropped=10010:"), notes


def test_planner_reports_judge_error_codes_in_notes() -> None:
    """Given 判题器本轮回了 ``errorCode=4``（指令错误）与 2（答案错误），
    When 规划整回合，Then D-02 notes 给出 ``errs=4:…,2:…``（错误码 + 描述原文）。

    2026-09-17 的事故就是卡在「只看得见 ``errors=1`` 的计数」：分不清是烧配额的
    指令错误（4）还是纯任务侧失分的答案错误（2）。把码与描述打出来，下一局不必
    再靠推测。
    """
    from future_war.strategy.planner import plan_turn

    data = _request([STATION, _role(10010, "worker", 5, 5, 220)], zones=[_mine(6, 5)])
    data["roundNo"] = 71  # 夜晚：走防御分支
    data["errors"] = [
        {"errorCode": 4, "description": "bad command"},
        {"errorCode": 2, "description": "wrong answer"},
    ]
    view = WorldModel().apply_round(parse_request(data))
    notes = plan_turn(view, None).notes
    assert any(note.startswith("errs=4:bad command,2:wrong answer") for note in notes), notes


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
