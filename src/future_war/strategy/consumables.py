"""消耗品与保命策略（小策略 S1–S5，`docs/策略设计.md` §4.2）。

任务书 §4.6.3 的消耗品大多有明确性价比，但**必须在正确的时机用**才有意义。
本模块只做「该花就花、花了立刻见效」的决策，不抢武器/基地升级的钱：

| 物品 | 价格 | 触发条件 | 依据 |
| --- | --- | --- | --- |
| 生命药剂 Medicine | 10 | 角色血量 ≤ ``consumables.medicine_hp_ratio``（默认 0.5），且有金币 | §4.5.2 阵亡 = 20 回合无操控 |
| 围墙修复包 WallFixer | 10 | 身旁围墙血 ≤ ``consumables.wall_hp_ratio``（默认 0.4） | 比新砌（1 石头）或围墙升级券（20 金）更省 |
| 范围炸弹 Bomb | 100 | 目标 3×3 内 ≥ ``consumables.bomb_min_robots``（默认 2）只机器人 | §4.6.3：3×3 内 100 伤害，当回合减员 |
| 眩晕法宝 DizzyWeapon | 100 | 目标 3×3 内 ≥ ``consumables.dizzy_min_robots``（默认 3）只机器人 | §4.6.3：眩晕 5 回合，期间它们不动不打，我方白打 5 回合 |

购买只发生在**白天**（夜晚角色要操控武器，走开一座武器就少一轮输出），
且保留 ``economy.emergency_reserve`` 应急金。仅用标准库。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from future_war.config import Config
from future_war.models import Action, Pos, RobotRole, Role, RoleCommand
from future_war.core.world_map import chebyshev
from future_war.core.world_view import WorldView

MEDICINE: Final = "Medicine"
WALL_FIXER: Final = "WallFixer"
BOMB: Final = "Bomb"
DIZZY: Final = "DizzyWeapon"

MEDICINE_COST: Final = 10
WALL_FIXER_COST: Final = 10
BOMB_COST: Final = 100
DIZZY_COST: Final = 100

MEDICINE_HP_RATIO: Final = 0.5
WALL_HP_RATIO: Final = 0.4
BOMB_MIN_ROBOTS: Final = 2
DIZZY_MIN_ROBOTS: Final = 3
DEFAULT_RESERVE: Final = 100
_AOE_RADIUS: Final = 1  # 「3x3」= 切比雪夫距离 ≤1（§4.6.3）


@dataclass
class ConsumableState:
    """跨回合消耗品状态：仅在购买成功后才允许使用（避免拿库存里的任务用品误判）。

    背包里出现药剂 ≠ 我们买的（也可能是捡到/别人给的），但用掉它一定不亏：
    血量低才用、围墙残血才修，因此这里只做计数，不做归属校验。
    """

    bought: dict[str, int] = field(default_factory=dict)

    def note_bought(self, item: str) -> None:
        self.bought[item] = self.bought.get(item, 0) + 1


def plan_consumables(
    view: WorldView,
    config: Config | None = None,
    state: ConsumableState | None = None,
) -> dict[int, RoleCommand]:
    """产出本回合的「用消耗品 + 买消耗品」指令（防空时返回空）。"""
    if view.is_night():
        return {}
    state = state if state is not None else ConsumableState()
    commands: dict[int, RoleCommand] = {}
    for unit in _mobile(view):
        command = _use_command(view, config, unit)
        if command is not None:
            commands[unit.id] = command
    commands.update(_buy_commands(view, config, state))
    return commands


def _use_command(
    view: WorldView, config: Config | None, unit: Role
) -> RoleCommand | None:
    """用掉手里的消耗品：修墙 → 回血 → 炸弹 → 眩晕（顺序=收益从确定到不确定）。"""
    backpack = set(unit.backpack)
    if WALL_FIXER in backpack:
        target = _damaged_wall(view, unit, config)
        if target is not None:
            return RoleCommand(action=Action.USE, name=WALL_FIXER, targetPos=(target,))
    if MEDICINE in backpack and _hurt(unit, config):
        return RoleCommand(action=Action.USE, name=MEDICINE)
    robots = list(view.robots_targeting_us())
    if not robots:
        return None
    if BOMB in backpack:
        target = _aoe_target(unit, robots, _int_knob(config, "consumables.bomb_min_robots", BOMB_MIN_ROBOTS))
        if target is not None:
            return RoleCommand(action=Action.USE, name=BOMB, targetPos=(target,))
    if DIZZY in backpack:
        target = _aoe_target(unit, robots, _int_knob(config, "consumables.dizzy_min_robots", DIZZY_MIN_ROBOTS))
        if target is not None:
            return RoleCommand(action=Action.USE, name=DIZZY, targetPos=(target,))
    return None


def _buy_commands(
    view: WorldView, config: Config | None, state: ConsumableState
) -> dict[int, RoleCommand]:
    """武器建满且防线铺完后，把余钱换成救命道具（只花超出应急金的部分）。"""
    max_weapons = _int_knob(config, "build.day1_max_weapons", 3)
    if len(view.own_weapons()) < max_weapons:
        return {}
    reserve = _int_knob(config, "economy.emergency_reserve", DEFAULT_RESERVE)
    available = view.gold() - reserve
    if available < MEDICINE_COST:
        return {}
    shop = view.weapon_shop_pos()
    if shop is None:
        return {}
    buyer = next(
        (r for r in view.own_workers() if chebyshev(r.pos, shop) <= 1), None
    )
    if buyer is None:
        return {}
    if _count(view, MEDICINE) == 0 and any(_hurt(r, config) for r in _mobile(view)):
        return _buy(buyer, MEDICINE, state)
    if _count(view, WALL_FIXER) == 0 and view.own_walls() and available >= WALL_FIXER_COST:
        return _buy(buyer, WALL_FIXER, state)
    if _count(view, BOMB) == 0 and available >= BOMB_COST:
        return _buy(buyer, BOMB, state)
    if _count(view, DIZZY) == 0 and available >= DIZZY_COST:
        return _buy(buyer, DIZZY, state)
    return {}


def _buy(buyer: Role, item: str, state: ConsumableState) -> dict[int, RoleCommand]:
    state.note_bought(item)
    return {buyer.id: RoleCommand(action=Action.BUY, name=item, num=1)}


# ------------------------------------------------------------------ 判定


def _mobile(view: WorldView) -> list[Role]:
    return list(view.own_workers()) + list(view.own_pioneer())


def _count(view: WorldView, item: str) -> int:
    return sum(r.backpack.count(item) for r in _mobile(view))


def _hurt(unit: Role, config: Config | None) -> bool:
    ratio = _float_knob(config, "consumables.medicine_hp_ratio", MEDICINE_HP_RATIO)
    max_hp = _max_hp(unit)
    return max_hp > 0 and unit.health <= max_hp * ratio


def _max_hp(unit: Role) -> int:
    """角色满血（工人 220 / 开拓者 200，§4.5.2；未知类型按 200 估）。"""
    return {"worker": 220, "pioneer": 200}.get(str(unit.roleType), 200)


def _damaged_wall(view: WorldView, unit: Role, config: Config | None) -> Pos | None:
    ratio = _float_knob(config, "consumables.wall_hp_ratio", WALL_HP_RATIO)
    candidates = [
        wall
        for wall in view.own_walls()
        if chebyshev(wall.pos, unit.pos) <= 1 and wall.health <= 1000 * ratio
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda w: (w.health, w.pos.x, w.pos.y)).pos


def _aoe_target(unit: Role, robots: list[RobotRole], minimum: int) -> Pos | None:
    """找一个「以它为中心的 3×3 挤满机器人」的落点（只考虑手边够得着的）。

    落点取机器人自身所在格：机器人是朝基地走的，聚在基地附近时 3×3 覆盖最多。
    """
    if minimum <= 1:
        minimum = 1
    best: tuple[int, int, int, int] | None = None
    for robot in robots:
        reach = chebyshev(robot.pos, unit.pos)
        if reach > 6:  # 够不着就别浪费道具（真机无使用距离限制，但仍要靠近防守点）
            continue
        covered = sum(
            1 for other in robots if chebyshev(other.pos, robot.pos) <= _AOE_RADIUS
        )
        if covered < minimum:
            continue
        key = (-covered, reach, robot.pos.x, robot.pos.y)
        if best is None or key < best:
            best = key
    if best is None:
        return None
    return Pos(best[2], best[3])


def _int_knob(config: Config | None, key: str, default: int) -> int:
    value = config.get(key) if config is not None else None
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _float_knob(config: Config | None, key: str, default: float) -> float:
    value = config.get(key) if config is not None else None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return default
