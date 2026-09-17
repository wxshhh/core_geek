"""基础防御（工作包 12，方案 M6）：夜晚武器操控与目标选择。

夜晚为每座可用武器分配 1 名操控角色（站在武器周围 1 格，§4.4），攻击射程内
最近的、以我方为目标的机器人；无操控者的武器则派最近角色前往。空闲角色撤回
基地待命。白天不发攻击（§4.4 攻击仅夜晚可用）。

一个角色一回合只能操控一座武器；攻击指令以**武器 id** 为键、``controllerId``
为操控角色（接口 §2.2）。``targetPos`` 的**个数**由武器等级决定：加特林/火箭
L2/L3 按等级发 N 个目标位置，其余（含全部 L1）恒发 1 个 —— 详见
:func:`target_slots`。移动统一走 ``core.nav.resolve_moves``。仅用标准库。
"""

from __future__ import annotations

from typing import Final

from future_war.config import Config
from future_war.models import Action, Pos, RobotRole, Role, RoleCommand, enum_to_str
from future_war.core.nav import resolve_moves
from future_war.core.world_map import chebyshev
from future_war.core.world_view import WorldView
from future_war.strategy.builder import (
    assign_controllers,
    shelter_cells,
    staging_cell,
)

_CONTROLLER_RANGE: Final = 1
DAY_LENGTH: Final = 130  # 一整天 70 白天 + 60 夜晚（任务书 §4.2）
DAY_ROUNDS: Final = 70
# 夜晚前若干回合：无操控者的武器也要派人过去（而不是等敌人进射程才动身）。
# 机器人从地图边缘走到基地需要十几回合，正好够角色跑到操控位。
STAGING_NIGHT_ROUNDS: Final = 25
# 接口 §2.2 注：**加特林炮台 / 火箭发射台**的 ``targetPos`` 可传多个目标位置，
# 个数 = 当前武器等级；电磁狙击炮只能传 1 个。武器等级上限是 3（任务书 §4.5.1），
# 这里把槽位数也封顶，避免非法/超界的等级值把目标数拉爆。
_MULTI_TARGET_KINDS: Final = frozenset({"gatling", "rocket"})
_MAX_TARGET_SLOTS: Final = 3


def target_slots(weapon: Role, candidate_count: int) -> int:
    """这座武器本回合要发几个目标位置（接口 §2.2 的 ``targetPos`` 语义）。

    ``candidate_count`` = 射程内**可供选择**的目标数（调用方已按优先级排好序）。

    为什么按等级发：现在场上武器都是 **level 1**，我们恒发 1 个目标位置，所以文档
    这条注从没暴露；一旦买了升级券（用户策略「后续先升级城墙」，武器也会跟着升），
    加特林/火箭 L2 要 2 个、L3 要 3 个位置才符合文档。形状不对可能直接被判非法，
    那是「开局就被扣异常」的致命代价。

    为什么 L1 必须逐字节不变：我们无法在本地验证判题器对形状的实际校验，因此
    **level <= 1 一律 1 个**（railgun 亦同）—— 即便我对文档的理解有偏差，也绝不动
    今天的线上行为。

    为什么目标不足等级时退回 1 个：文档只规定了「目标位置数 = 等级」，没说目标不够
    时能否少发。发一个长度 2 的数组给 L3 武器是**文档里既没说合法、也没说非法**的
    灰色形状，风险远大于收益；因此返回值只有两种：**恰好 1 个**，或**恰好等于等级**。
    """
    if candidate_count <= 1:
        return 1
    if enum_to_str(weapon.roleType) not in _MULTI_TARGET_KINDS:
        return 1
    level = weapon.level if isinstance(weapon.level, int) else 1
    if level <= 1 or candidate_count < level:
        return 1
    return min(level, _MAX_TARGET_SLOTS)


def attack_positions(weapon: Role, ranked: list[Pos]) -> tuple[Pos, ...]:
    """按 `target_slots` 裁出 ``targetPos``：``ranked`` 已按本模块的目标优先级排好。

    单一入口，进攻/防御两条火力线共用，``level==1`` 时恒等于 ``ranked[:1]``
    （= 旧实现的 ``(target.pos,)``）。
    """
    return tuple(ranked[: target_slots(weapon, len(ranked))])


def plan_defense(
    view: WorldView, config: Config | None = None
) -> dict[int, RoleCommand]:
    """夜晚为武器分配操控者并攻击；白天返回空指令。

    夜晚前 ``combat.staging_night_rounds`` 回合内，**没有操控者的武器也会派人过去**
    （白天不再提前 30 回合就位，见 ``economy.dusk_return``）。理由是用户实测：机器人
    从刷出到摸到基地要走十几回合，这段时间足够角色从工地跑到操控位；把就位放在
    白天等于白白停掉 30 个回合的施工。
    """
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
    staging = _in_night_window(view, config)
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
            # 无操控者：夜间前段无条件派人去操控位；过了窗口只在射程内已有敌人时才动身
            target = assign_controllers(view, (weapon,)).get(weapon.id)
            chosen = (
                next((r for r in healthy if r.id == target and r.id not in assigned), None)
                if staging and target is not None
                else None
            )
            if chosen is not None:
                goals[chosen.id] = staging_cell(view, weapon) or weapon.pos
                assigned.add(chosen.id)
                continue
            if not _in_range(robots, weapon):
                continue
            free = _nearest_within(healthy, weapon.pos, None, assigned)
            if free is not None:
                goals[free.id] = weapon.pos
                assigned.add(free.id)
            continue
        assigned.add(controller.id)
        armed.append((weapon, controller))
    for weapon, controller, targets in _select_targets(armed, robots, config):
        commands[weapon.id] = RoleCommand(
            action=Action.ATTACK,
            controllerId=str(controller.id),
            targetPos=tuple(target.pos for target in targets),
        )
    _send_home(view, mobile, assigned, goals, config)
    for uid, step in resolve_moves(view, goals).items():
        if step is not None:
            commands[uid] = RoleCommand(action=Action.MOVE, targetPos=(step,))
    return commands


def _in_night_window(view: WorldView, config: Config | None) -> bool:
    """是否处于「夜晚前段」就位窗口（``combat.staging_night_rounds``）。"""
    window = _int_flag(config, "combat.staging_night_rounds", STAGING_NIGHT_ROUNDS)
    if window <= 0:
        return False
    night_round = (view.round_no - 1) % DAY_LENGTH - DAY_ROUNDS + 1
    return night_round <= window


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
    """未分配操控位的角色躲进「墙内」安全位（贴着基地、围墙之后的那一侧）。"""
    shelters = shelter_cells(view)
    if not shelters:
        return
    inside = set(shelters)
    hurt = retreating_roles(view, config)
    for role in mobile:
        if role.id in assigned:
            continue
        if role.id in hurt or role.pos not in inside:
            target = min(shelters, key=lambda c: (chebyshev(role.pos, c), c.x, c.y))
            if role.pos != target:
                goals[role.id] = target


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


def _int_flag(config: Config | None, key: str, default: int) -> int:
    value = config.get(key) if config is not None else None
    return value if isinstance(value, int) and not isinstance(value, bool) else default


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
) -> list[tuple[Role, Role, tuple[RobotRole, ...]]]:
    """按优先级选目标，并避免多武器对同一目标溢出伤害（combat.overkill_avoidance）。

    返回每座武器**本回合的全部目标**（元组）：L1 与电磁狙击炮恒为 1 个（与旧实现
    逐字节一致），加特林/火箭 L2/L3 按等级取射程内优先级最高的 N 个（见
    :func:`target_slots`）。多目标时按同一把优先级排序取前 N 个，并把伤害记到每个
    被选中的目标上，后续武器仍然能看到「谁已经挨了多少」。
    """
    order = _priority_order(config)
    claimed: dict[int, int] = {}
    selections: list[tuple[Role, Role, tuple[RobotRole, ...]]] = []
    for weapon, controller in armed:
        in_range = [
            r for r in robots if chebyshev(r.pos, weapon.pos) <= weapon.attackRange
        ]
        if not in_range:
            continue
        fresh = [r for r in in_range if r.health - claimed.get(r.id, 0) > 0]
        # 与旧实现同一个 key 与同一个候选池（``fresh or in_range``）：slots == 1 时
        # ``sorted(...)[:1]`` 就是旧的 ``min(...)``，逐元素相同。slots 按**可用**候选
        # 数算（而非原始射程内数量）：可用目标不足等级时只发 1 个，绝不发出
        # 「长度介于 1 与等级之间」的灰色形状（见 ``target_slots``）。
        ranked = sorted(
            fresh or in_range,
            key=lambda r: (_priority_key(r, order), chebyshev(r.pos, weapon.pos), r.id),
        )
        targets = tuple(ranked[: target_slots(weapon, len(ranked))])
        for target in targets:
            claimed[target.id] = claimed.get(target.id, 0) + weapon.attackPower
        selections.append((weapon, controller, targets))
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
