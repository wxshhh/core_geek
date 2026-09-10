"""武器攻击结算与伤害应用（任务书 §4.4/§4.5.4/§4.7）。

结算顺序（§4.4）：武器攻击 > 机器人移动；所有伤害**回合末统一结算**。

- 攻击瞄准的是**机器人移动前的位置**：本模块在机器人移动前解析命中目标，
  伤害事件入队 ``world.pending_damage``，由 engine 在回合末统一应用。
- 命中语义（§4.5.4）：
  - 加特林：每颗子弹沿自身落点弹道飞行，命中弹道上最近的一台机器人即消耗
    （伤害 10/颗）。围墙/建筑**不阻挡弹道**（墙后武器输出的前提，方案 §3.2）。
  - 电磁狙击炮：能量 = 10×等级，沿弹道对沿途机器人依次造成
    min(剩余能量, 该机器人当前血量)，能量耗尽或到达终点即止。
  - 火箭：导弹不被阻挡；每枚导弹中心 20 伤害、周围 8 格溅射为中心一半；
    多枚落点重叠伤害叠加；发射后 3 回合冷却（§4.5.1）。
- 简化（见 sim/README.md）：加特林/电磁炮只对**机器人**生效；火箭可命中
  任意单位（机器人/敌方角色/敌方建筑/围墙/基地）；溅射不打己方单位。
"""

from __future__ import annotations

from dataclasses import dataclass

from future_war.models import RoleCommand
from future_war.sim.rules import (
    Cell,
    GATLING_CONE_DEG,
    ROCKET_COOLDOWN,
    ROCKET_SPLASH_RATIO,
    WEAPON_ATTACK,
    cone_ok,
    day_of_round,
    in_bounds,
    is_day_round,
    neighbors8,
    ray_cells,
    weapon_range_at,
)
from future_war.sim.world import DamageEvent, Unit, World


@dataclass(frozen=True, slots=True)
class AttackIntent:
    """一条已通过结构+规则校验的攻击意图。"""

    team: str
    weapon_uid: int
    controller_uid: int
    weapon_type: str
    level: int
    targets: tuple[Cell, ...]


def queue_attack(
    world: World, team: str, weapon: Unit, cmd: RoleCommand
) -> AttackIntent | None:
    """校验攻击指令（§4.4/§4.5.4）；合法返回意图，非法返回 None。"""
    if not weapon.is_weapon:
        return None
    if not _is_night(world):
        return None
    if weapon.cooldown > 0:
        return None
    controller = _controller_of(world, team, cmd)
    if controller is None:
        return None
    targets = tuple((p.x, p.y) for p in cmd.targetPos)
    if not targets or not all(in_bounds(x, y) for x, y in targets):
        return None
    if not _target_count_ok(weapon.kind, weapon.level, len(targets)):
        return None
    if not _range_ok(weapon, targets):
        return None
    if weapon.kind == "gatling" and not cone_ok((weapon.x, weapon.y), targets, GATLING_CONE_DEG):
        return None
    return AttackIntent(
        team=team,
        weapon_uid=weapon.uid,
        controller_uid=controller.uid,
        weapon_type=weapon.kind,
        level=weapon.level,
        targets=targets,
    )


def _is_night(world: World) -> bool:
    return not is_day_round(world.round_no)


def _controller_of(world: World, team: str, cmd: RoleCommand) -> Unit | None:
    """操控角色：须为己方存活角色且在武器周围 1 格内（§4.4）。"""
    if cmd.controllerId is None:
        return None
    try:
        uid = int(cmd.controllerId)
    except ValueError:
        return None
    unit = world.teams[team].units.get(uid)
    if unit is None or not unit.alive or not unit.is_role:
        return None
    return unit


def _target_count_ok(kind: str, level: int, count: int) -> bool:
    """目标位置数与武器等级一致（接口 §2.2；电磁炮恒为 1）。"""
    expected = level if kind in ("gatling", "rocket") else 1
    return count == expected


def _range_ok(weapon: Unit, targets: tuple[Cell, ...]) -> bool:
    limit = weapon_range_at(weapon.kind, weapon.level)
    if limit is None:  # 火箭 L3 全图
        return True
    return all(
        max(abs(weapon.x - x), abs(weapon.y - y)) <= limit for x, y in targets
    )


def resolve_weapon_attacks(world: World, attacks: list[AttackIntent]) -> set[int]:
    """解析攻击命中（移动前位置），伤害入队；返回「有命中」的武器 ID 集合。

    一个角色一回合只能操控一座武器（接口 §2.2）：同一 controller 出现于
    多条攻击时全部判无效（确定性：按意图列表顺序去重）。
    """
    seen_controllers: dict[int, list[int]] = {}
    for atk in attacks:
        seen_controllers.setdefault(atk.controller_uid, []).append(atk.weapon_uid)
    conflict_weapons = {
        uid for uids in seen_controllers.values() if len(uids) > 1 for uid in uids
    }
    hit_weapons: set[int] = set()
    for atk in attacks:
        if atk.weapon_uid in conflict_weapons:
            continue
        weapon = world.teams[atk.team].units.get(atk.weapon_uid)
        if weapon is None or not weapon.alive:
            continue
        hit = _resolve_one(world, atk, weapon)
        if hit:
            hit_weapons.add(atk.weapon_uid)
            weapon.cooldown = ROCKET_COOLDOWN if weapon.kind == "rocket" else 0
    return hit_weapons


def _resolve_one(world: World, atk: AttackIntent, weapon: Unit) -> bool:
    """单条攻击的命中解析；返回是否有命中。"""
    match atk.weapon_type:
        case "gatling":
            return _gatling(world, atk, weapon)
        case "railgun":
            return _railgun(world, atk, weapon)
        case "rocket":
            return _rocket(world, atk, weapon)
        case unreachable:
            raise AssertionError(f"unknown weapon type {unreachable!r}")


def _gatling(world: World, atk: AttackIntent, weapon: Unit) -> bool:
    """加特林：每目标一颗子弹，命中弹道最近机器人，伤害 10（§4.5.4）。"""
    hit = False
    for tx, ty in atk.targets:
        for x, y in ray_cells(weapon.x, weapon.y, tx, ty)[1:]:
            rob = world.robot_at(x, y)
            if rob is None:
                continue
            world.pending_damage.append(
                DamageEvent(source_team=atk.team, amount=WEAPON_ATTACK["gatling"], robot_rid=rob.rid)
            )
            hit = True
            break  # 最近的一台机器人即消耗
    return hit


def _railgun(world: World, atk: AttackIntent, weapon: Unit) -> bool:
    """电磁狙击炮：能量沿弹道穿透，逐一扣除机器人血量（§4.5.4）。"""
    energy = WEAPON_ATTACK["railgun"] * atk.level
    hit = False
    tx, ty = atk.targets[0]
    for x, y in ray_cells(weapon.x, weapon.y, tx, ty)[1:]:
        rob = world.robot_at(x, y)
        if rob is None:
            continue
        amount = min(energy, rob.hp)
        world.pending_damage.append(
            DamageEvent(source_team=atk.team, amount=amount, robot_rid=rob.rid)
        )
        hit = True
        energy -= amount
        if energy <= 0:
            break  # 能量耗尽即止
    return hit


def _rocket(world: World, atk: AttackIntent, weapon: Unit) -> bool:
    """火箭：每目标一枚导弹，中心 20、8 邻格溅射 10，落点重叠叠加（§4.5.4）。"""
    hit = False
    for tx, ty in atk.targets:
        center = WEAPON_ATTACK["rocket"]
        splash = int(round(center * ROCKET_SPLASH_RATIO))
        for index, (x, y) in enumerate([(tx, ty), *neighbors8(tx, ty)]):
            amount = center if index == 0 else splash
            hit |= _damage_cell(world, atk.team, x, y, amount)
    return hit


def _damage_cell(world: World, team: str, x: int, y: int, amount: int) -> bool:
    """对 (x,y) 上的机器人或敌方单位造成伤害；返回是否命中。"""
    rob = world.robot_at(x, y)
    if rob is not None:
        world.pending_damage.append(
            DamageEvent(source_team=team, amount=amount, robot_rid=rob.rid)
        )
        return True
    blocker = world.any_unit_at(x, y)
    if blocker is not None and blocker[0] != team:  # 溅射不打己方（简化）
        enemy_team, unit = blocker
        world.pending_damage.append(
            DamageEvent(source_team=team, amount=amount, unit_ref=(enemy_team, unit.uid))
        )
        return True
    return False


def apply_damage(world: World) -> None:
    """回合末统一结算全部待定伤害与死亡（§4.4「伤害本回合结束后统一结算」）。

    - 机器人死亡 → 击杀方 kills 计数（§六 score2），机器人移除。
    - 角色死亡 → 记录 death_day（次日复活，§4.5.2）。
    - 武器/围墙被毁 → 移除；基地被毁 → 记录 base_destroy_day。
    """
    credited: set[int] = set()
    for ev in world.pending_damage:
        if ev.robot_rid is not None:
            rob = world.robots.get(ev.robot_rid)
            if rob is None or rob.hp <= 0:
                continue
            rob.hp -= ev.amount
            if rob.hp <= 0 and rob.rid not in credited:
                credited.add(rob.rid)
                world.teams[ev.source_team].kills[rob.kind] += 1
                world.events.append(f"kill robot={rob.rid} type={rob.kind} by={ev.source_team}")
        else:
            if ev.unit_ref is None:
                raise AssertionError("damage event without target")
            team_name, uid = ev.unit_ref
            unit = world.teams[team_name].units.get(uid)
            if unit is not None and unit.alive:
                unit.hp -= ev.amount
    world.pending_damage.clear()
    for rid in sorted(world.robots):
        if world.robots[rid].hp <= 0:
            del world.robots[rid]
    for team_name in ("challenger", "defender"):
        ts = world.teams[team_name]
        for uid in sorted(ts.units):
            unit = ts.units[uid]
            if not unit.alive or unit.hp > 0:
                continue
            match unit.kind:
                case "station":
                    unit.alive = False
                    if ts.base_destroy_day == 0:
                        ts.base_destroy_day = day_of_round(world.round_no)
                    world.events.append(f"base_destroyed team={team_name} hp=0")
                case "pioneer" | "worker":
                    unit.alive = False
                    unit.death_day = day_of_round(world.round_no)
                    world.events.append(f"role_died team={team_name} uid={uid}")
                case "gatling" | "railgun" | "rocket" | "wall":
                    del ts.units[uid]
                case unreachable:
                    raise AssertionError(f"unknown unit kind {unreachable!r}")
