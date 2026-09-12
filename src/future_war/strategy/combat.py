"""基础防御（工作包 12，方案 M6）：夜晚武器操控与目标选择。

夜晚为每座可用武器分配 1 名操控角色（站在武器周围 1 格，§4.4），攻击射程内
最近的、以我方为目标的机器人；无操控者的武器则派最近角色前往。空闲角色撤回
基地待命。白天不发攻击（§4.4 攻击仅夜晚可用）。

一个角色一回合只能操控一座武器；攻击指令以**武器 id** 为键、``controllerId``
为操控角色（接口 §2.2）。移动统一走 ``core.nav.resolve_moves``。仅用标准库。
"""

from __future__ import annotations

from typing import Final

from future_war.config import Config
from future_war.models import Action, Pos, RobotRole, Role, RoleCommand, enum_to_str
from future_war.core.nav import resolve_moves
from future_war.core.world_map import chebyshev
from future_war.core.world_view import WorldView
from future_war.strategy.builder import assign_controllers

BASE_HOLD_RANGE: Final = 2
_CONTROLLER_RANGE: Final = 1


def plan_defense(
    view: WorldView, config: Config | None = None
) -> dict[int, RoleCommand]:
    """夜晚为武器分配操控者并攻击；白天返回空指令。"""
    if not view.is_night():
        return {}
    commands: dict[int, RoleCommand] = {}
    goals: dict[int, Pos] = {}
    assigned: set[int] = set()
    mobile = list(view.own_workers()) + list(view.own_pioneer())
    robots = list(view.robots_targeting_us())
    weapons = sorted(view.own_weapons(), key=lambda w: w.id)
    # 优先按「白天就位阶段」的分配配对，保证角色待在正确的武器旁
    preferred = assign_controllers(view, tuple(weapons))
    # 残血角色退回基地保命：角色阵亡 = 20 回合无人操控武器（§4.5.2），
    # 但撤退优先级低于开火，所以已有操控者的武器不受影响。
    hurt = retreating_roles(view, config)
    healthy = [r for r in mobile if r.id not in hurt]
    armed: list[tuple[Role, Role]] = []
    for weapon in weapons:
        if weapon.cooldown > 0:
            continue
        candidates = []
        uid = preferred.get(weapon.id)
        if uid is not None:
            candidates = [r for r in healthy if r.id == uid and r.id not in assigned]
        controller = _pick_ready(candidates, weapon, assigned) or _nearest_within(
            healthy, weapon.pos, _CONTROLLER_RANGE, assigned
        )
        if controller is None:
            # 无操控者：只有射程内有敌人（或该武器本就是某角色的待命目标）才派人过去
            if not _in_range(robots, weapon):
                continue
            free = _nearest_within(healthy, weapon.pos, None, assigned)
            if free is not None:
                goals[free.id] = weapon.pos
                assigned.add(free.id)
            continue
        assigned.add(controller.id)
        armed.append((weapon, controller))
    for weapon, controller, target in _select_targets(armed, robots, config):
        commands[weapon.id] = RoleCommand(
            action=Action.ATTACK,
            controllerId=str(controller.id),
            targetPos=(target.pos,),
        )
    _send_home(view, mobile, assigned, goals, config)
    for uid, step in resolve_moves(view, goals).items():
        if step is not None:
            commands[uid] = RoleCommand(action=Action.MOVE, targetPos=(step,))
    return commands


def _pick_ready(
    candidates: list[Role], weapon: Role, assigned: set[int]
) -> Role | None:
    """从候选角色里挑一个已经站在武器操控范围内的（本回合即可开火）。"""
    ready = [
        role
        for role in candidates
        if role.id not in assigned
        and chebyshev(role.pos, weapon.pos) <= _CONTROLLER_RANGE
    ]
    if not ready:
        return None
    return min(ready, key=lambda r: (chebyshev(r.pos, weapon.pos), r.id))


def _in_range(robots: list[RobotRole], weapon: Role) -> bool:
    return any(chebyshev(r.pos, weapon.pos) <= weapon.attackRange for r in robots)


def _send_home(
    view: WorldView,
    mobile: list[Role],
    assigned: set[int],
    goals: dict[int, Pos],
    config: Config | None = None,
) -> None:
    base = view.base_pos()
    if base is None:
        return
    hurt = retreating_roles(view, config)
    for role in mobile:
        if role.id in assigned:
            continue
        if role.id in hurt or chebyshev(role.pos, base) > BASE_HOLD_RANGE:
            goals[role.id] = base


def retreating_roles(view: WorldView, config: Config | None = None) -> frozenset[int]:
    """残血到「再挨一下就没了」的角色：应退回基地，把操控位让给健康角色。

    设置 ``offense.retreat_hp_ratio=0`` 可关闭该行为。撤退优先级低于操控武器，
    因此不会出现「为了保命把武器丢空」的情况。
    """
    ratio = _ratio_flag(config, "offense.retreat_hp_ratio", RETREAT_HP_RATIO)
    if ratio <= 0:
        return frozenset()
    out = []
    for role in list(view.own_workers()) + list(view.own_pioneer()):
        max_hp = {"worker": 220, "pioneer": 200}.get(enum_to_str(role.roleType), 200)
        if max_hp and role.health <= max_hp * ratio:
            out.append(role.id)
    return frozenset(out)


RETREAT_HP_RATIO: Final = 0.35


def _ratio_flag(config: Config | None, key: str, default: float) -> float:
    value = config.get(key) if config is not None else None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return default


_ROBOT_KINDS: Final = {
    "boss": "bossRobot",
    "large": "largeRobot",
    "medium": "middleRobot",
    "small": "smallRobot",
}
_DEFAULT_PRIORITY: Final = ("bossRobot", "largeRobot", "middleRobot", "smallRobot")


def _select_targets(
    armed: list[tuple[Role, Role]], robots: list[RobotRole], config: Config | None
) -> list[tuple[Role, Role, RobotRole]]:
    """按优先级选目标，并避免多武器对同一目标溢出伤害（combat.overkill_avoidance）。"""
    order = _priority_order(config)
    claimed: dict[int, int] = {}
    selections: list[tuple[Role, Role, RobotRole]] = []
    for weapon, controller in armed:
        in_range = [
            r for r in robots if chebyshev(r.pos, weapon.pos) <= weapon.attackRange
        ]
        if not in_range:
            continue
        fresh = [r for r in in_range if r.health - claimed.get(r.id, 0) > 0]
        target = min(
            fresh or in_range,
            key=lambda r: (_priority_key(r, order), chebyshev(r.pos, weapon.pos), r.id),
        )
        claimed[target.id] = claimed.get(target.id, 0) + weapon.attackPower
        selections.append((weapon, controller, target))
    return selections


def _priority_order(config: Config | None) -> tuple[str, ...]:
    value = config.get("combat.target_priority") if config is not None else None
    if isinstance(value, (list, tuple)):
        mapped = tuple(_ROBOT_KINDS.get(str(v), "") for v in value)
        if mapped and all(mapped):
            return mapped
    return _DEFAULT_PRIORITY


def _priority_key(robot: RobotRole, order: tuple[str, ...]) -> int:
    kind = enum_to_str(robot.roleType)
    return order.index(kind) if kind in order else len(order)


def _nearest_within(
    units: list[Role] | list[RobotRole],
    origin: Pos,
    distance: int | None,
    exclude: set[int] | None = None,
) -> Role | RobotRole | None:
    excluded = exclude or set()
    candidates = [u for u in units if u.id not in excluded]
    if distance is not None:
        candidates = [u for u in candidates if chebyshev(u.pos, origin) <= distance]
    if not candidates:
        return None
    return min(candidates, key=lambda u: (chebyshev(u.pos, origin), u.id))
