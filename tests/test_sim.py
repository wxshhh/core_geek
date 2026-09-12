"""工作包 3 测试：本地模拟器 + mock 判题器。

覆盖：几何/昼夜、合成地图、初始状态、移动碰撞（§4.5.4）、经济（采集/贩卖/
购买）、建造/拆除、武器攻击（昼夜/冷却/穿透/锥形/溅射）、机器人波次与行为、
队伍异常（§八：非法指令计异常、规则性失败不计）、积分（score1/2/3）、
胜负判定、视野、确定性（同种子两次输出一致）、HTTP Bot 驱动、回放/日志产物。

运行方式（二选一）：
    python3 -m pytest tests/ -q
    python3 tests/test_sim.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from future_war.models import Pos, Request, Response, RoleCommand  # noqa: E402
from future_war.sim import (  # noqa: E402
    HttpBot,
    IdleBot,
    MatchLog,
    ReplayWriter,
    World,
    bot_from_spec,
    build_request,
    compute_team_scores,
    make_layout,
    run_match,
)
from future_war.sim import engine  # noqa: E402
from future_war.sim.judge import TeamDriver  # noqa: E402
from future_war.sim.protocol import parse_commands  # noqa: E402
from future_war.sim.rules import (  # noqa: E402
    chebyshev,
    day_of_round,
    is_day_round,
    is_night_round,
    phase_of_round,
)
from future_war.sim.world import Robot, Unit  # noqa: E402

WALL_HP = 1000


def make_world(seed: int = 42) -> World:
    return World(seed, make_layout())


def place_weapon(world: World, team: str, uid: int, kind: str, x: int, y: int) -> None:
    world.teams[team].units[uid] = Unit(
        uid=uid, kind=kind, x=x, y=y, hp=WALL_HP, max_hp=WALL_HP,
        level=1, cooldown=0, cap=0, backpack=[],
    )


def place_wall(world: World, team: str, uid: int, x: int, y: int) -> None:
    place_weapon(world, team, uid, "wall", x, y)


def place_robot(world: World, rid: int, kind: str, x: int, y: int, target: str = "challenger") -> None:
    world.robots[rid] = Robot(rid=rid, kind=kind, x=x, y=y, hp=40, target_team=target)


def night_round(world: World) -> None:
    """把世界推进到夜晚且非出生回合（round 72）。"""
    world.round_no = 71


def day_round(world: World) -> None:
    world.round_no = 0


def cmd(
    action: str,
    name: str | None = None,
    num: int = 1,
    targetPos: tuple[Pos, ...] = (),
    controllerId: str | None = None,
    taskAnswer: str | None = None,
    item: tuple[str, ...] = (),
) -> RoleCommand:
    return RoleCommand(
        action=action,
        name=name,
        num=num,
        targetPos=targetPos,
        controllerId=controllerId,
        taskAnswer=taskAnswer,
        item=item,
    )


def resolve(
    world: World,
    challenger: dict[int, RoleCommand] | None = None,
    defender: dict[int, RoleCommand] | None = None,
) -> dict[str, dict[int, bool]]:
    engine.begin_round(world)
    commands: dict[str, dict[int, RoleCommand]] = {}
    if challenger:
        commands["challenger"] = challenger
    if defender:
        commands["defender"] = defender
    return engine.resolve_round(world, commands, {})


# ---------------------------------------------------------------- 几何与时间

def test_chebyshev_distance() -> None:
    """Given 两点，When chebyshev，Then max(|dx|,|dy|)。"""
    assert chebyshev(0, 0, 3, 4) == 4
    assert chebyshev(10, 24, 10, 24) == 0


def test_day_night_phase_boundaries() -> None:
    """Given 关键回合，When 相位函数，Then 白天 70 回合/夜晚 60 回合边界正确。"""
    assert day_of_round(1) == 1 and day_of_round(70) == 1
    assert day_of_round(71) == 1 and day_of_round(130) == 1
    assert day_of_round(131) == 2 and day_of_round(1300) == 10
    assert is_day_round(1) and is_day_round(70) and not is_day_round(71)
    assert is_night_round(71) and is_night_round(130) and not is_night_round(131)
    assert phase_of_round(70) == "day" and phase_of_round(71) == "night"


# ---------------------------------------------------------------- 地图与初始状态

def test_layout_zones_and_neutrals() -> None:
    """Given 合成地图，When 查蓝/黄区与固定中立，Then 与 README 布局一致。"""
    layout = make_layout()
    assert layout.base_cells("challenger") == frozenset(
        {(10, 24), (11, 24), (10, 25), (11, 25)}
    )
    assert layout.in_weapon_zone(10, 23) and not layout.in_weapon_zone(10, 17)
    assert layout.in_wall_zone(10, 17) and not layout.in_wall_zone(10, 23)
    assert layout.fixed_neutrals[(20, 16)] == "vendor"
    assert layout.fixed_neutrals[(25, 20)] == "weaponShop"


def test_initial_state_roles_and_gold() -> None:
    """Given 新世界，When 检查初始状态，Then 角色×3/基地×1/金币 75（§4.5.2/§4.5.3）。"""
    world = make_world()
    ts = world.teams["challenger"]
    roles = [u for u in ts.units.values() if u.is_role]
    assert len(roles) == 3 and ts.gold == 75
    assert {u.kind for u in roles} == {"pioneer", "worker"}
    assert {u.max_hp for u in roles} == {200, 220}
    base = world.base("challenger")
    assert base is not None and base.hp == 1500 and base.level == 1


def test_mines_not_in_forbidden_cells() -> None:
    """Given 种子矿区，When 检查位置，Then 不在可建造区/中立/基地/出生格。"""
    world = make_world(7)
    layout = world.layout
    from future_war.sim.layout import mine_forbidden_cells

    forbidden = mine_forbidden_cells(layout)
    for cell in world.mines:
        assert cell not in forbidden
    assert len(world.mines) == 6  # 石头/铁/铜各 2


# ---------------------------------------------------------------- 移动与碰撞（§4.5.4）

def test_move_simple_succeeds() -> None:
    """Given 空闲相邻格，When 移动，Then 角色到达且指令生效。"""
    world = make_world()
    day_round(world)
    worker = world.teams["challenger"].units[10010]
    worker.x, worker.y = 20, 20
    results = resolve(world, challenger={10010: cmd("move", targetPos=(Pos(20, 21),))})
    assert results["challenger"][10010] is True
    assert (worker.x, worker.y) == (20, 21)


def test_move_not_adjacent_is_invalid() -> None:
    """Given 目标距离 2，When 移动，Then 无效且原地不动（§4.5.4「每次移动一格」）。"""
    world = make_world()
    day_round(world)
    worker = world.teams["challenger"].units[10010]
    worker.x, worker.y = 20, 20
    results = resolve(world, challenger={10010: cmd("move", targetPos=(Pos(20, 22),))})
    assert results["challenger"][10010] is False
    assert (worker.x, worker.y) == (20, 20)


def test_move_blocked_by_obstacle_stays() -> None:
    """Given 目标格有围墙，When 移动，Then 仅移动方停留（§4.5.4 与障碍物碰撞）。"""
    world = make_world()
    day_round(world)
    place_wall(world, "challenger", 40000, 20, 21)
    worker = world.teams["challenger"].units[10010]
    worker.x, worker.y = 20, 20
    results = resolve(world, challenger={10010: cmd("move", targetPos=(Pos(20, 21),))})
    assert results["challenger"][10010] is False
    assert (worker.x, worker.y) == (20, 20)


def test_move_target_contention_both_stay() -> None:
    """Given 两名角色同目标，When 移动，Then 双方均停留（§4.5.4 目标点争夺）。"""
    world = make_world()
    day_round(world)
    a = world.teams["challenger"].units[10010]
    b = world.teams["challenger"].units[10012]
    a.x, a.y = 20, 20
    b.x, b.y = 22, 20
    results = resolve(
        world,
        challenger={
            10010: cmd("move", targetPos=(Pos(21, 20),)),
            10012: cmd("move", targetPos=(Pos(21, 20),)),
        },
    )
    assert results["challenger"][10010] is False
    assert results["challenger"][10012] is False
    assert (a.x, a.y) == (20, 20) and (b.x, b.y) == (22, 20)


def test_move_position_swap_both_stay() -> None:
    """Given 两名角色互换位置，When 移动，Then 双方均停留（§4.5.4 位置互换）。"""
    world = make_world()
    day_round(world)
    a = world.teams["challenger"].units[10010]
    b = world.teams["challenger"].units[10012]
    a.x, a.y = 20, 20
    b.x, b.y = 21, 20
    results = resolve(
        world,
        challenger={
            10010: cmd("move", targetPos=(Pos(21, 20),)),
            10012: cmd("move", targetPos=(Pos(20, 20),)),
        },
    )
    assert results["challenger"][10010] is False
    assert results["challenger"][10012] is False


def test_move_blocked_by_stationary_role() -> None:
    """Given 目标被未移动角色占据，When 移动，Then 停留（§4.5.4 目标点受阻）。"""
    world = make_world()
    day_round(world)
    a = world.teams["challenger"].units[10010]
    b = world.teams["challenger"].units[10012]
    a.x, a.y = 20, 20
    b.x, b.y = 20, 21  # 静止角色挡路
    results = resolve(world, challenger={10010: cmd("move", targetPos=(Pos(20, 21),))})
    assert results["challenger"][10010] is False
    assert (a.x, a.y) == (20, 20)


# ---------------------------------------------------------------- 经济（§4.4/§4.6）

def test_collect_ore_and_charges() -> None:
    """Given 工人邻接矿区，When collect，Then 背包 +1 且矿存量 -1。"""
    from future_war.sim.world import Mine

    world = make_world()
    day_round(world)
    world.mines.clear()
    world.mines[(20, 21)] = Mine(ore="stone", charges=10)
    worker = world.teams["challenger"].units[10010]
    worker.x, worker.y = 20, 20
    results = resolve(world, challenger={10010: cmd("collect", targetPos=(Pos(20, 21),))})
    assert results["challenger"][10010] is True
    assert worker.backpack == ["stone"]
    assert world.mines[(20, 21)].charges == 9


def test_collect_shared_mine_depletes_and_respawns() -> None:
    """Given 存量 1 的矿被两工人同采，When 结算，Then 各得 1 且矿下回合重生（§4.1）。"""
    world = make_world()
    day_round(world)
    from future_war.sim.world import Mine

    world.mines.clear()
    world.mines[(20, 21)] = Mine(ore="stone", charges=1)
    a = world.teams["challenger"].units[10010]
    b = world.teams["challenger"].units[10012]
    a.x, a.y = 20, 20
    b.x, b.y = 20, 22
    resolve(
        world,
        challenger={
            10010: cmd("collect", targetPos=(Pos(20, 21),)),
            10012: cmd("collect", targetPos=(Pos(20, 21),)),
        },
    )
    assert (20, 21) not in world.mines  # 采尽消失
    assert a.backpack == ["stone"] and b.backpack == ["stone"]  # 不足平分各得 1
    engine.begin_round(world)  # 下回合刷新
    assert any(m.ore == "stone" and m.charges == 10 for m in world.mines.values())


def test_sell_ore_near_vendor() -> None:
    """Given 工人邻接小贩，When sell，Then 矿石换金币（基础价，§4.6.1）。"""
    world = make_world()
    day_round(world)
    worker = world.teams["challenger"].units[10010]
    worker.x, worker.y = 20, 15  # 小贩 (20,16)
    worker.backpack[:] = ["copper", "copper"]
    before = world.teams["challenger"].gold
    results = resolve(
        world, challenger={10010: cmd("sell", name="copper", num=2)}
    )
    assert results["challenger"][10010] is True
    assert world.teams["challenger"].gold == before + 10
    assert worker.backpack == []


def test_sell_away_from_vendor_invalid() -> None:
    """Given 工人离小贩 2 格，When sell，Then 无效且金币不变。"""
    world = make_world()
    day_round(world)
    worker = world.teams["challenger"].units[10010]
    worker.x, worker.y = 20, 14
    worker.backpack[:] = ["stone"]
    before = world.teams["challenger"].gold
    results = resolve(world, challenger={10010: cmd("sell", name="stone", num=1)})
    assert results["challenger"][10010] is False
    assert world.teams["challenger"].gold == before


def test_buy_medicine_success() -> None:
    """Given 工人邻接武器商店，When buy，Then 扣金入包（§4.6.3）。"""
    world = make_world()
    day_round(world)
    worker = world.teams["challenger"].units[10010]
    worker.x, worker.y = 25, 19  # 武器商店 (25,20)
    before = world.teams["challenger"].gold
    results = resolve(world, challenger={10010: cmd("buy", name="Medicine", num=1)})
    assert results["challenger"][10010] is True
    assert world.teams["challenger"].gold == before - 10
    assert worker.backpack == ["Medicine"]


def test_buy_insufficient_gold_is_invalid_not_exception() -> None:
    """Given 金币不足，When buy，Then 无效且金币不变（§八注：规则性失败）。"""
    world = make_world()
    day_round(world)
    world.teams["challenger"].gold = 5
    worker = world.teams["challenger"].units[10010]
    worker.x, worker.y = 25, 19
    results = resolve(world, challenger={10010: cmd("buy", name="Bomb", num=1)})
    assert results["challenger"][10010] is False
    assert world.teams["challenger"].gold == 5


# ---------------------------------------------------------------- 建造与拆除（§4.5.1）

def test_build_weapon_day_success() -> None:
    """Given 工人白天在蓝区旁，When build，Then 武器建成、扣 25 金、ID 符合固定表。"""
    world = make_world()
    day_round(world)
    worker = world.teams["challenger"].units[10010]
    worker.x, worker.y = 10, 22  # 出生位（蓝区）
    results = resolve(
        world,
        challenger={10010: cmd("build", name="gatling", targetPos=(Pos(11, 23),))},
    )
    assert results["challenger"][10010] is True
    assert world.teams["challenger"].gold == 50
    weapon = world.teams["challenger"].units[10020]  # 加特林固定槽位
    assert weapon.kind == "gatling" and (weapon.x, weapon.y) == (11, 23)
    assert weapon.level == 1 and weapon.hp == 1000


def test_build_weapon_wrong_zone_invalid() -> None:
    """Given 目标为黄区，When build 武器，Then 无效（蓝区只建武器，§4.1）。"""
    world = make_world()
    day_round(world)
    worker = world.teams["challenger"].units[10010]
    worker.x, worker.y = 10, 18  # 黄区
    results = resolve(
        world,
        challenger={10010: cmd("build", name="railgun", targetPos=(Pos(10, 17),))},
    )
    assert results["challenger"][10010] is False
    assert 10030 not in world.teams["challenger"].units


def test_build_wall_consumes_stone() -> None:
    """Given 工人带石头在黄区旁，When build wall，Then 墙建成且石头消耗。"""
    world = make_world()
    day_round(world)
    worker = world.teams["challenger"].units[10010]
    worker.x, worker.y = 10, 18
    worker.backpack[:] = ["stone", "stone"]
    results = resolve(
        world,
        challenger={10010: cmd("build", name="wall", targetPos=(Pos(10, 17),))},
    )
    assert results["challenger"][10010] is True
    assert worker.backpack == ["stone"]
    assert 40000 in world.teams["challenger"].units


def test_build_night_is_invalid() -> None:
    """Given 夜晚，When build，Then 无效（建造仅白天，§4.4）。"""
    world = make_world()
    night_round(world)
    worker = world.teams["challenger"].units[10010]
    worker.x, worker.y = 10, 22
    results = resolve(
        world,
        challenger={10010: cmd("build", name="gatling", targetPos=(Pos(11, 23),))},
    )
    assert results["challenger"][10010] is False


def test_weapon_limit_three_per_team() -> None:
    """Given 已有 3 座武器，When build 第 4 座，Then 无效（§4.5.1 上限 3）。"""
    world = make_world()
    day_round(world)
    place_weapon(world, "challenger", 10020, "gatling", 12, 22)
    place_weapon(world, "challenger", 10030, "railgun", 12, 23)
    place_weapon(world, "challenger", 10040, "rocket", 11, 23)
    worker = world.teams["challenger"].units[10010]
    worker.x, worker.y = 10, 22
    world.teams["challenger"].gold = 100
    results = resolve(
        world,
        challenger={10010: cmd("build", name="gatling", targetPos=(Pos(13, 23),))},
    )
    assert results["challenger"][10010] is False


def test_remove_wall_no_refund() -> None:
    """Given 己方墙在周围 1 格，When remove，Then 墙消失且不返还石头（§4.5.1）。"""
    world = make_world()
    day_round(world)
    place_wall(world, "challenger", 40000, 10, 21)
    worker = world.teams["challenger"].units[10010]
    worker.x, worker.y = 10, 22
    results = resolve(
        world, challenger={10010: cmd("remove", targetPos=(Pos(10, 21),))}
    )
    assert results["challenger"][10010] is True
    assert 40000 not in world.teams["challenger"].units


# ---------------------------------------------------------------- 武器攻击（§4.4/§4.5.4）

def test_attack_day_is_invalid() -> None:
    """Given 白天，When attack，Then 无效（攻击仅黑夜，§4.4）。"""
    world = make_world()
    day_round(world)
    place_weapon(world, "challenger", 10020, "gatling", 10, 20)
    worker = world.teams["challenger"].units[10010]
    worker.x, worker.y = 10, 21
    results = resolve(
        world,
        challenger={
            10020: cmd("attack", controllerId="10010", targetPos=(Pos(10, 23),))
        },
    )
    assert results["challenger"][10020] is False


def test_attack_night_damages_robot() -> None:
    """Given 夜晚+操控者邻接+机器人入射程，When attack，Then 回合末掉血。"""
    world = make_world()
    night_round(world)
    place_weapon(world, "challenger", 10020, "gatling", 10, 20)
    worker = world.teams["challenger"].units[10010]
    worker.x, worker.y = 10, 21
    place_robot(world, 30001, "smallRobot", 10, 23)
    results = resolve(
        world,
        challenger={
            10020: cmd("attack", controllerId="10010", targetPos=(Pos(10, 23),))
        },
    )
    assert results["challenger"][10020] is True
    assert world.robots[30001].hp == 30  # 40 - 10（加特林单颗子弹）


def test_attack_kill_counts_score2() -> None:
    """Given 低血机器人，When attack 击杀，Then kills 计数且 score2 正确（§六）。"""
    world = make_world()
    night_round(world)
    place_weapon(world, "challenger", 10030, "railgun", 10, 20)
    worker = world.teams["challenger"].units[10010]
    worker.x, worker.y = 10, 21
    place_robot(world, 30001, "smallRobot", 10, 23)
    world.robots[30001].hp = 5
    resolve(
        world,
        challenger={
            10030: cmd("attack", controllerId="10010", targetPos=(Pos(10, 23),))
        },
    )
    assert 30001 not in world.robots
    assert world.teams["challenger"].kills["smallRobot"] == 1
    assert compute_team_scores(world)["challenger"].score2 == 1


def test_rocket_cooldown_blocks_refire() -> None:
    """Given 火箭发射后，When 冷却期内再攻击，Then 无效；3 回合后恢复（§4.5.1）。"""
    world = make_world()
    night_round(world)
    place_weapon(world, "challenger", 10040, "rocket", 10, 20)
    worker = world.teams["challenger"].units[10010]
    worker.x, worker.y = 10, 21
    place_robot(world, 30001, "smallRobot", 12, 22)
    first = resolve(
        world,
        challenger={
            10040: cmd("attack", controllerId="10010", targetPos=(Pos(12, 22),))
        },
    )
    assert first["challenger"][10040] is True
    assert world.teams["challenger"].units[10040].cooldown == 3
    world.robots[30001].hp = 40
    second = resolve(
        world,
        challenger={
            10040: cmd("attack", controllerId="10010", targetPos=(Pos(12, 22),))
        },
    )
    assert second["challenger"][10040] is False  # 冷却中
    for _ in range(2):  # 再等 2 回合冷却归零
        engine.begin_round(world)
        engine.resolve_round(world, {}, {})
    assert world.teams["challenger"].units[10040].cooldown == 0


def test_railgun_energy_pierce() -> None:
    """Given 弹道两只机器人，When 电磁炮 L1，Then 能量 10 依次穿透（§4.5.4）。"""
    world = make_world()
    night_round(world)
    place_weapon(world, "challenger", 10030, "railgun", 10, 20)
    worker = world.teams["challenger"].units[10010]
    worker.x, worker.y = 10, 21
    place_robot(world, 30001, "smallRobot", 10, 22)
    place_robot(world, 30002, "middleRobot", 10, 23)
    world.robots[30001].hp = 5
    world.robots[30002].hp = 60
    resolve(
        world,
        challenger={
            10030: cmd("attack", controllerId="10010", targetPos=(Pos(10, 23),))
        },
    )
    assert 30001 not in world.robots  # 先扣 5，能量剩 5
    assert world.robots[30002].hp == 55  # 再扣 5，能量耗尽


def test_gatling_cone_violation_invalid() -> None:
    """Given L2 加特林两个相反方向目标，When attack，Then 整次非法（90° 锥，§4.5.4）。"""
    world = make_world()
    night_round(world)
    place_weapon(world, "challenger", 10020, "gatling", 10, 20)
    world.teams["challenger"].units[10020].level = 2
    worker = world.teams["challenger"].units[10010]
    worker.x, worker.y = 10, 21
    results = resolve(
        world,
        challenger={
            10020: cmd(
                "attack",
                controllerId="10010",
                targetPos=(Pos(8, 20), Pos(12, 20)),
            )
        },
    )
    assert results["challenger"][10020] is False


def test_rocket_splash_damage() -> None:
    """Given 落点邻格有机器人，When 火箭命中，Then 中心 20 + 溅射 10（§4.5.4）。"""
    world = make_world()
    night_round(world)
    place_weapon(world, "challenger", 10040, "rocket", 10, 20)
    worker = world.teams["challenger"].units[10010]
    worker.x, worker.y = 10, 21
    place_robot(world, 30001, "smallRobot", 12, 22)
    place_robot(world, 30002, "smallRobot", 13, 22)
    resolve(
        world,
        challenger={
            10040: cmd("attack", controllerId="10010", targetPos=(Pos(12, 22),))
        },
    )
    assert world.robots[30001].hp == 20  # 中心 20
    assert world.robots[30002].hp == 30  # 溅射 10


def test_one_controller_one_weapon() -> None:
    """Given 同一角色操控两武器，When attack，Then 均无效（接口 §2.2）。"""
    world = make_world()
    night_round(world)
    place_weapon(world, "challenger", 10020, "gatling", 10, 20)
    place_weapon(world, "challenger", 10030, "railgun", 12, 20)
    worker = world.teams["challenger"].units[10010]
    worker.x, worker.y = 11, 21  # 两武器中间
    place_robot(world, 30001, "smallRobot", 11, 23)
    results = resolve(
        world,
        challenger={
            10020: cmd("attack", controllerId="10010", targetPos=(Pos(11, 23),)),
            10030: cmd("attack", controllerId="10010", targetPos=(Pos(11, 23),)),
        },
    )
    assert results["challenger"][10020] is False
    assert results["challenger"][10030] is False


# ---------------------------------------------------------------- 机器人（§4.7）

def test_robot_wave_spawn_and_morning_clear() -> None:
    """Given 夜晚第 1 回合，When begin_round，Then 生成浪潮；次日早第 1 回合清除。"""
    world = make_world()
    world.round_no = 70
    engine.begin_round(world)  # 71：夜晚第 1 回合
    assert len(world.robots) == 6  # 每队 3 只小型
    assert all(r.kind == "smallRobot" for r in world.robots.values())
    world.round_no = 130
    engine.begin_round(world)  # 131：次日早第 1 回合
    assert world.robots == {}


def test_robot_wave_grows_with_day() -> None:
    """Given 第 2 天夜晚，When 生成，Then 数量曲线为 小4+中1（README 自定曲线）。"""
    world = make_world()
    world.round_no = 200
    engine.begin_round(world)  # 201：第 2 天夜晚
    kinds = [r.kind for r in world.robots.values()]
    assert kinds.count("smallRobot") == 8  # 每队 4
    assert kinds.count("middleRobot") == 2  # 每队 1


def test_robot_attacks_blocking_wall() -> None:
    """Given 围墙挡在机器人与基地之间，When 机器人行动，Then 攻击围墙（§4.7.3）。"""
    world = make_world()
    night_round(world)
    pioneer = world.teams["challenger"].units[10011]
    pioneer.x, pioneer.y = 5, 5  # 让出墙位（开拓者出生点 (10,23)）
    place_wall(world, "challenger", 40000, 10, 23)
    place_robot(world, 30001, "smallRobot", 10, 22, target="challenger")
    resolve(world)
    assert world.teams["challenger"].units[40000].hp == WALL_HP - 5


def test_robot_attacks_role_blocking() -> None:
    """Given 角色挡路，When 机器人行动，Then 攻击该角色（§4.7.3）。"""
    world = make_world()
    night_round(world)
    worker = world.teams["challenger"].units[10010]
    worker.x, worker.y = 10, 23
    place_robot(world, 30001, "smallRobot", 10, 22, target="challenger")
    resolve(world)
    assert worker.hp == 220 - 5


# ---------------------------------------------------------------- 异常口径（§八）

class _RecordingBot:
    """记录收到的全部 Request 的回放型 Bot（测试探针）。"""

    def __init__(self, responder: Callable[[Request], Response]) -> None:
        self._responder = responder
        self.requests: list[Request] = []

    def __call__(self, request: Request) -> Response:
        self.requests.append(request)
        return self._responder(request)


class _UnknownActionBot:
    """每回合提交一个不可识别的动作码（§八指令错误）。"""

    def __call__(self, request: Request) -> Response:
        return Response(roleCommandMap={10010: RoleCommand(action="dance")})


def test_unknown_action_counts_as_team_exception() -> None:
    """Given 动作码不可识别，When run_match，Then 计队伍异常且 5 次后停调（§八）。"""
    world = make_world()
    report = run_match(
        world,
        {"challenger": _UnknownActionBot(), "defender": IdleBot()},
        max_rounds=10,
    )
    assert report.teams["challenger"].exceptions == 5
    assert report.teams["challenger"].scheduled is False
    assert report.rounds_played == 10


def test_rule_invalid_command_is_not_exception() -> None:
    """Given 合法但规则失败（黄区建武器），When run_match，Then 异常 0 且结果 false。"""
    world = make_world()
    worker = world.teams["challenger"].units[10010]
    worker.x, worker.y = 10, 18  # 黄区

    def responder(request: Request) -> Response:
        return Response(
            roleCommandMap={
                10010: RoleCommand(action="build", name="gatling", targetPos=(Pos(10, 17),))
            }
        )

    bot = _RecordingBot(responder)
    report = run_match(world, {"challenger": bot, "defender": IdleBot()}, max_rounds=2)
    assert report.teams["challenger"].exceptions == 0
    assert bot.requests[-1].lastRoundRoleActionResults.get(10010) is False


def test_invalid_command_reported_as_error_code_4() -> None:
    """Given 非法指令，When 下回合请求，Then errors 含 errorCode 4（接口 §1.7）。"""
    world = make_world()
    bot = _RecordingBot(_UnknownActionBot())
    run_match(world, {"challenger": bot, "defender": IdleBot()}, max_rounds=2)
    assert bot.requests[1].errors[0].errorCode == 4


def test_missing_targetpos_is_structure_error() -> None:
    """Given move 缺 targetPos，When parse_commands，Then 结构性问题（§八注）。"""
    cmds, problems, dropped = parse_commands(
        {"roleCommandMap": {"10010": {"action": "move"}}}
    )
    assert cmds == {} and 10010 in dropped
    assert any("targetPos" in p for p in problems)


# ---------------------------------------------------------------- 积分与胜负（§六/§七）

def test_score3_survival_days() -> None:
    """Given 基地第 2 天被毁，When 结算，Then score3=10（仅第 1 天，§六）。"""
    world = make_world()
    world.teams["defender"].base_destroy_day = 2
    scores = compute_team_scores(world)
    assert scores["defender"].score3 == 10
    assert scores["challenger"].score3 == 550  # 全程存活 10+20+...+100
    assert scores["challenger"].score1 == 0  # 文档化 stub


def test_match_ends_when_both_bases_destroyed() -> None:
    """Given 双方 idle，When 完整对局，Then 基地双双被毁且比赛结束（§七）。"""
    world = make_world(11)
    report = run_match(
        world,
        {"challenger": IdleBot(), "defender": IdleBot()},
        max_rounds=1300,
    )
    assert report.end_reason == "bases_destroyed"
    assert report.rounds_played < 1300
    assert report.winner in {"challenger", "defender", "draw"}


def test_http_bot_drives_real_server() -> None:
    """Given 真实 HTTP Bot 服务，When HttpBot 驱动数回合，Then 无异常（集成）。"""
    from future_war.server import create_server

    server = create_server(0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        world = make_world(3)
        report = run_match(
            world,
            {"challenger": HttpBot(url), "defender": IdleBot()},
            max_rounds=3,
        )
        assert report.teams["challenger"].exceptions == 0
        assert report.rounds_played == 3
    finally:
        server.shutdown()
        thread.join(timeout=5)


# ---------------------------------------------------------------- 视野（§4.3）

def test_enemy_visibility_range_four() -> None:
    """Given 敌方角色，When 距离 >4 / ≤4，Then 不可见/可见；基地恒可见（§4.3）。"""
    world = make_world()
    driver = TeamDriver(team="challenger", bot=IdleBot())
    enemy = world.teams["defender"].units[20010]
    enemy.x, enemy.y = 20, 8  # 视野外
    req = build_request(world, "challenger", driver)
    enemy_ids = {r.id for r in req.teamEnemy.roles}
    assert 20010 not in enemy_ids
    assert 20013 in enemy_ids  # 敌方基地全局可见
    enemy.x, enemy.y = 10, 20  # 距我方 (10,22) 仅 2 格
    req = build_request(world, "challenger", driver)
    assert 20010 in {r.id for r in req.teamEnemy.roles}


# ---------------------------------------------------------------- 确定性 + 产物

def test_full_scripted_match_is_deterministic() -> None:
    """Given 同种子同 Bot，When 完整对局两次，Then 报告字节一致。"""

    def run(seed: int) -> str:
        world = make_world(seed)
        report = run_match(
            world,
            {
                "challenger": bot_from_spec("scripted:defense"),
                "defender": bot_from_spec("scripted:defense"),
            },
            max_rounds=1300,
        )
        return json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True)

    assert run(7) == run(7)


def test_full_scripted_match_report_shape() -> None:
    """Given 脚本化对局，When run_match，Then 报告含 score1/2/3、击杀、胜负。"""
    world = make_world(7)
    report = run_match(
        world,
        {
            "challenger": bot_from_spec("scripted:defense"),
            "defender": bot_from_spec("idle"),
        },
        max_rounds=1300,
    )
    for team in ("challenger", "defender"):
        outcome = report.teams[team]
        assert outcome.score1 == 0
        assert outcome.total == outcome.score2 + outcome.score3
        assert outcome.kills.keys() == {"smallRobot", "middleRobot", "largeRobot", "bossRobot"}
    assert report.teams["challenger"].kills["smallRobot"] > 0
    assert report.winner == "challenger"


def test_replay_and_log_written_for_every_round() -> None:
    """Given 回放与日志写入器，When 对局，Then 每回合一行 JSONL + 日志含 [SIM] 行。"""
    with tempfile.TemporaryDirectory() as tmp:
        replay_path = Path(tmp) / "match.replay"
        log_path = Path(tmp) / "match.log"
        world = make_world(7)
        replay = ReplayWriter(replay_path)
        match_log = MatchLog(log_path)
        report = run_match(
            world,
            {"challenger": bot_from_spec("scripted:defense"), "defender": IdleBot()},
            max_rounds=100,
            replay=replay,
            match_log=match_log,
        )
        replay.close()
        match_log.close()
        lines = replay_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == report.rounds_played
        first = json.loads(lines[0])
        assert first["roundNo"] == 1
        assert set(first["challenger"]) >= {"request", "response", "result"}
        assert "[SIM]" in log_path.read_text(encoding="utf-8")


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


# ---------------------------------------------------------------- 消耗品（§4.6.3）


def test_bomb_damages_robots_in_three_by_three() -> None:
    """Given 3x3 内有多只机器人，When 工人使用范围炸弹，Then 它们各受 100 伤害。"""
    from future_war.models import Pos, RoleCommand
    from future_war.sim import actions, combat

    world = make_world()
    day_round(world)
    worker = world.teams["challenger"].units[10010]
    worker.x, worker.y = 10, 10
    worker.backpack = ["Bomb"]
    place_robot(world, 30001, "middleRobot", 12, 10)
    place_robot(world, 30002, "middleRobot", 12, 11)
    place_robot(world, 30003, "middleRobot", 20, 20)  # 3x3 之外
    ok = actions.do_use(
        world, "challenger", worker, RoleCommand(action="use", name="Bomb", targetPos=(Pos(12, 10),))
    )
    assert ok
    assert "Bomb" not in worker.backpack
    combat.apply_damage(world)
    assert 30001 not in world.robots  # 60 - 100 < 0 → 死亡
    assert 30002 not in world.robots
    assert world.robots[30003].hp == 40  # 3x3 之外不受影响（place_robot 默认 40 血）


def test_bomb_kills_are_credited_to_the_user() -> None:
    """Given 炸弹击杀，When 结算，Then 计入施放方的击杀分（§六 score2）。"""
    from future_war.models import Pos, RoleCommand
    from future_war.sim import actions, combat

    world = make_world()
    day_round(world)
    worker = world.teams["challenger"].units[10010]
    worker.x, worker.y = 10, 10
    worker.backpack = ["Bomb"]
    place_robot(world, 30001, "smallRobot", 12, 10)
    actions.do_use(
        world, "challenger", worker, RoleCommand(action="use", name="Bomb", targetPos=(Pos(12, 10),))
    )
    combat.apply_damage(world)
    assert world.teams["challenger"].kills["smallRobot"] == 1


def test_dizzy_weapon_stops_robots_for_five_rounds() -> None:
    """Given 机器人被眩晕，When 连续推 5 个夜晚回合，Then 它不动不攻击。"""
    from future_war.models import Pos, RoleCommand
    from future_war.sim import actions, robots as sim_robots

    world = make_world()
    day_round(world)
    worker = world.teams["challenger"].units[10010]
    worker.x, worker.y = 10, 10
    worker.backpack = ["DizzyWeapon"]
    place_robot(world, 30001, "smallRobot", 12, 10)
    start = (world.robots[30001].x, world.robots[30001].y)
    actions.do_use(
        world,
        "challenger",
        worker,
        RoleCommand(action="use", name="DizzyWeapon", targetPos=(Pos(12, 10),)),
    )
    assert world.robots[30001].dizzy_rounds == 5
    for _ in range(5):
        sim_robots.move_robots(world)
        assert (world.robots[30001].x, world.robots[30001].y) == start
    sim_robots.move_robots(world)  # 第 6 回合恢复行动
    assert (world.robots[30001].x, world.robots[30001].y) != start
