"""非战斗指令执行：移动意图与经济/建造/拆除（任务书 §4.4/§4.5.1/§4.6）。

本模块函数只**记录意图或立即结算与位置无关的效果**，返回 bool（该指令
是否生效）。生效判定以**回合开始位置**为准（§4.4：每角色每回合一个动作）。

- 移动不在此处落地：只产出 ``MoveIntent``，由 engine 在机器人移动之后
  统一做碰撞结算（§4.5.4）。
- 采集的矿区扣减是回合级聚合（多工人同采一矿共享 10 次存量，§4.1），
  由 engine 在本模块执行完后统一处理 ``world.mine_harvest``。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from future_war.models import RoleCommand
from future_war.sim.rules import (
    BUILDING_HP,
    Cell,
    MAX_WEAPONS,
    ORE_PRICES,
    ORE_TYPES,
    SUMMON_ORDERS,
    WALL_COST_STONE,
    WEAPON_COST,
    WEAPON_SHOP,
    WEAPON_TYPES,
    adjacent,
    in_bounds,
    is_day_round,
)
from future_war.sim.world import DamageEvent, TeamState, Unit, World

_VENDOR: Final = "vendor"
_WEAPON_SHOP: Final = "weaponShop"

WEAPON_ID_BASES: Final = {"gatling": 20, "railgun": 30, "rocket": 40}

BOMB_DAMAGE: Final = 100  # §4.6.3：范围炸弹 3×3 内 100 伤害
DIZZY_ROUNDS: Final = 5  # §4.6.3：眩晕法宝 3×3 内眩晕 5 回合

BUILDABLE_KINDS: Final = (*WEAPON_TYPES, "wall")


@dataclass(frozen=True, slots=True)
class MoveIntent:
    """一条待碰撞结算的移动意图（起点 = 回合开始位置）。"""

    team: str
    uid: int
    x: int  # 起点
    y: int
    tx: int  # 目标
    ty: int


def team_of_unit(world: World, uid: int) -> str:
    """按单位 ID 反查阵营。"""
    if uid in world.teams["challenger"].units:
        return "challenger"
    return "defender"


def _neutral_cell(world: World, name: str) -> Cell | None:
    """固定中立单位（小贩/武器商店）坐标。"""
    for cell, kind in world.layout.fixed_neutrals.items():
        if kind == name:
            return cell
    return None


def _remove_ores(unit: Unit, name: str, num: int) -> None:
    for _ in range(num):
        unit.backpack.remove(name)


def try_move(world: World, unit: Unit, cmd: RoleCommand) -> MoveIntent | None:
    """产出移动意图；目标不在界内/不相邻返回 None（无效指令，§4.5.4）。"""
    if not unit.is_role:
        return None
    pos = cmd.targetPos[0]
    if not in_bounds(pos.x, pos.y):
        return None
    if not adjacent(unit.x, unit.y, pos.x, pos.y):
        return None
    return MoveIntent(
        team=team_of_unit(world, unit.uid),
        uid=unit.uid,
        x=unit.x,
        y=unit.y,
        tx=pos.x,
        ty=pos.y,
    )


def do_collect(world: World, team: str, unit: Unit, cmd: RoleCommand) -> bool:
    """采集：工人在矿区周围 1 格内，背包 +1 对应矿石（§4.4）。"""
    if unit.kind != "worker":
        return False
    pos = cmd.targetPos[0]
    mine = world.mines.get((pos.x, pos.y))
    if mine is None:
        return False
    if not adjacent(unit.x, unit.y, pos.x, pos.y):
        return False
    if len(unit.backpack) >= unit.cap:
        return False
    unit.backpack.append(mine.ore)
    world.mine_harvest[(pos.x, pos.y)] = world.mine_harvest.get((pos.x, pos.y), 0) + 1
    return True


def do_sell(world: World, team: str, unit: Unit, cmd: RoleCommand) -> bool:
    """贩卖：小贩周围 1 格内卖出矿石换金币（§4.4/§4.6.1）。"""
    ore = cmd.name
    if ore is None or ore not in ORE_TYPES:
        return False
    vendor = _neutral_cell(world, _VENDOR)
    if vendor is None or not adjacent(unit.x, unit.y, *vendor):
        return False
    if unit.backpack.count(ore) < cmd.num:
        return False
    _remove_ores(unit, ore, cmd.num)
    world.teams[team].gold += cmd.num * ORE_PRICES[ore]
    return True


def do_buy(world: World, team: str, unit: Unit, cmd: RoleCommand) -> bool:
    """购买：武器商店周围 1 格内购买商品入背包（§4.4/§4.6.3）。"""
    item = cmd.name
    if item is None or item not in WEAPON_SHOP:
        return False
    shop = _neutral_cell(world, _WEAPON_SHOP)
    if shop is None or not adjacent(unit.x, unit.y, *shop):
        return False
    price = WEAPON_SHOP[item]
    ts = world.teams[team]
    if ts.gold < cmd.num * price:
        return False
    if len(unit.backpack) + cmd.num > unit.cap:
        return False
    ts.gold -= cmd.num * price
    unit.backpack.extend([item] * cmd.num)
    if item in SUMMON_ORDERS:
        target = world.opponent(team)
        world.summons.setdefault(target, {})
        world.summons[target][SUMMON_ORDERS[item]] = (
            world.summons[target].get(SUMMON_ORDERS[item], 0) + cmd.num
        )
    return True


def do_build(world: World, team: str, unit: Unit, cmd: RoleCommand) -> bool:
    """建造：工人白天在周围 1 格内建武器（蓝区，25 金）或围墙（黄区，1 石头）。"""
    if unit.kind != "worker":
        return False
    if not is_day_round(world.round_no):
        return False
    name = cmd.name
    if name is None or name not in BUILDABLE_KINDS:
        return False
    pos = cmd.targetPos[0]
    if not in_bounds(pos.x, pos.y) or not adjacent(unit.x, unit.y, pos.x, pos.y):
        return False
    if name == "wall":
        return _build_wall(world, team, unit, pos.x, pos.y)
    return _build_weapon(world, team, unit, name, pos.x, pos.y)


def _build_wall(world: World, team: str, unit: Unit, x: int, y: int) -> bool:
    """围墙：黄色可建造区，消耗背包石头×1（§4.5.1）。"""
    if not world.layout.in_wall_zone(x, y):
        return False
    if unit.backpack.count("stone") < WALL_COST_STONE:
        return False
    existing = world.building_at(x, y)
    if existing is not None:
        if existing[0] != team or existing[1].kind != "wall":
            return False
        uid = existing[1].uid  # 同格重砌，沿用原 ID（简化，见 README）
    else:
        uid = world.next_wall_id(team)
    _remove_ores(unit, "stone", WALL_COST_STONE)
    world.teams[team].units[uid] = Unit(
        uid=uid,
        kind="wall",
        x=x,
        y=y,
        hp=BUILDING_HP["wall"][0],
        max_hp=BUILDING_HP["wall"][0],
        level=1,
        cooldown=0,
        cap=0,
        backpack=[],
    )
    return True


def _build_weapon(world: World, team: str, unit: Unit, kind: str, x: int, y: int) -> bool:
    """武器工事：蓝色可建造区，25 金，全局最多 3 座（§4.5.1）。"""
    ts = world.teams[team]
    if not world.layout.in_weapon_zone(x, y):
        return False
    if ts.gold < WEAPON_COST:
        return False
    if len([u for u in ts.units.values() if u.alive and u.is_weapon]) >= MAX_WEAPONS:
        return False
    existing = world.building_at(x, y)
    if existing is not None:
        if existing[0] != team or not existing[1].is_weapon:
            return False
        uid = existing[1].uid  # 覆盖同格旧武器，沿用其 ID（简化，见 README）
    else:
        uid = _next_weapon_id(ts, kind)
    ts.gold -= WEAPON_COST
    ts.units[uid] = Unit(
        uid=uid,
        kind=kind,
        x=x,
        y=y,
        hp=BUILDING_HP[kind][0],
        max_hp=BUILDING_HP[kind][0],
        level=1,
        cooldown=0,
        cap=0,
        backpack=[],
    )
    return True


def _next_weapon_id(ts: TeamState, kind: str) -> int:
    """按固定 ID 表（§1.3.1）分配该类型下一个未占用 ID。"""
    prefix = 10000 if ts.team == "challenger" else 20000
    base = prefix + WEAPON_ID_BASES[kind]
    used = set(ts.units)
    for offset in range(3):  # 每类 3 个固定槽位
        candidate = base + offset
        if candidate not in used:
            return candidate
    raise AssertionError(f"no free weapon id for {kind} of {ts.team}")


def do_remove(world: World, team: str, unit: Unit, cmd: RoleCommand) -> bool:
    """拆除：工人在周围 1 格内拆除己方围墙（§4.4；不回收石头，§4.5.1）。"""
    if unit.kind != "worker":
        return False
    pos = cmd.targetPos[0]
    if not adjacent(unit.x, unit.y, pos.x, pos.y):
        return False
    existing = world.building_at(pos.x, pos.y)
    if existing is None or existing[0] != team or existing[1].kind != "wall":
        return False
    del world.teams[team].units[existing[1].uid]
    return True


def do_drop(world: World, team: str, unit: Unit, cmd: RoleCommand) -> bool:
    """丢弃：从背包移除 1 个指定物品（§4.4）。"""
    item = cmd.name
    if not unit.is_role or item is None or item not in unit.backpack:
        return False
    unit.backpack.remove(item)
    return True


def do_use(world: World, team: str, unit: Unit, cmd: RoleCommand) -> bool:
    """使用消耗品：Medicine / WallFixer / Bomb / DizzyWeapon（§4.6.3）。"""
    item = cmd.name
    if not unit.is_role or item is None or item not in unit.backpack:
        return False
    match item:
        case "Medicine":
            unit.backpack.remove(item)
            unit.hp = unit.max_hp
            return True
        case "WallFixer":
            if not cmd.targetPos:
                return False
            pos = cmd.targetPos[0]
            if not adjacent(unit.x, unit.y, pos.x, pos.y):
                return False
            existing = world.building_at(pos.x, pos.y)
            if existing is None or existing[0] != team or existing[1].kind != "wall":
                return False
            unit.backpack.remove(item)
            existing[1].hp = existing[1].max_hp
            return True
        case "Bomb":
            if not cmd.targetPos:
                return False
            pos = cmd.targetPos[0]
            unit.backpack.remove(item)
            _aoe(world, pos, BOMB_DAMAGE, team)
            return True
        case "DizzyWeapon":
            if not cmd.targetPos:
                return False
            pos = cmd.targetPos[0]
            unit.backpack.remove(item)
            _aoe_dizzy(world, pos, DIZZY_ROUNDS)
            return True
        case _:
            return False  # 升级券/召唤令未实现（README）


def _aoe(world: World, center: Pos, damage: int, team: str) -> None:
    """以 center 为中心的 3×3 内机器人受 damage（§4.6.3 范围炸弹）。"""
    for robot in world.robots.values():
        if max(abs(robot.x - center.x), abs(robot.y - center.y)) <= 1:
            world.pending_damage.append(
                DamageEvent(source_team=team, amount=damage, robot_rid=robot.rid)
            )


def _aoe_dizzy(world: World, center: Pos, rounds: int) -> None:
    """以 center 为中心的 3×3 内机器人眩晕 rounds 回合（§4.6.3 眩晕法宝）。"""
    for robot in world.robots.values():
        if max(abs(robot.x - center.x), abs(robot.y - center.y)) <= 1:
            robot.dizzy_rounds = max(robot.dizzy_rounds, rounds)

