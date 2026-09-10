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
    armed: list[tuple[Role, Role]] = []
    for weapon in sorted(view.own_weapons(), key=lambda w: w.id):
        if weapon.cooldown > 0:
            continue
        controller = _nearest_within(mobile, weapon.pos, _CONTROLLER_RANGE, assigned)
        if controller is None:
            free = _nearest_within(mobile, weapon.pos, None, assigned)
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
    _send_home(view, mobile, assigned, goals)
    for uid, step in resolve_moves(view, goals).items():
        if step is not None:
            commands[uid] = RoleCommand(action=Action.MOVE, targetPos=(step,))
    return commands


def _send_home(
    view: WorldView, mobile: list[Role], assigned: set[int], goals: dict[int, Pos]
) -> None:
    base = view.base_pos()
    if base is None:
        return
    for role in mobile:
        if role.id in assigned:
            continue
        if chebyshev(role.pos, base) > BASE_HOLD_RANGE:
            goals[role.id] = base


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
