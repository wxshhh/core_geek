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
且保留 ``economy.emergency_reserve`` 应急金 —— **修复包除外**：墙已被打残说明防线正在
崩，它只用 ``consumables.wall_fixer_reserve``（默认 0）这一道单独的预留，否则在金币
常年 10~20 的墙优先模式里根本买不起（线上实测：整局没买过一个修复包）。另外
``economy._shopper`` 会在「有残血墙且队里没包」时派一个人专程去商店买。仅用标准库。
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
# 与 config 默认值一致：买修复包时单独留的应急金（0 = 不受 economy.emergency_reserve
# 的 100 金约束）。墙被打残是**明确矛盾**，10 金立刻止损比留着钱等武器划算得多；
# 旧实现共用 100 金预留，而墙优先模式的金币常年在 10~20 → 修复包整局买不到。
WALL_FIXER_RESERVE: Final = 0
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
        target = wall_fixer_target(view, unit, config)
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
    """把余钱换成救命道具（残血围墙优先；其余只花超出应急金的部分）。

    为什么给修复包单开一条路：旧实现把「武器建满 3 座」和 ``economy.emergency_reserve``
    （默认 100 金）当成所有消耗品的共同闸门，而墙优先模式下金币常年在 10~20 —— 结果
    是**整局没人买过修复包**（买包逻辑存在，但两个条件永远同时不成立）。现在只要
    「有残血围墙 + 队伍里没有修复包 + 买得起（单独预留 ``consumables.wall_fixer_reserve``，
    默认 0）」，武器没满也照样派人去买：10 金换一堵墙回满，是夜里最便宜的止损。
    """
    max_weapons = _int_knob(config, "build.day1_max_weapons", 3)
    urgent_wall = needs_wall_fixer(view, config)
    if len(view.own_weapons()) < max_weapons and not urgent_wall:
        return {}  # 武器没建满且墙没被打残 → 一分钱都不花（先武器）
    shop = view.weapon_shop_pos()
    if shop is None:
        return {}
    buyer = next(
        (r for r in view.own_workers() if chebyshev(r.pos, shop) <= 1), None
    )
    if buyer is None:
        return {}
    if urgent_wall:
        return _buy(buyer, WALL_FIXER, state)
    reserve = _int_knob(config, "economy.emergency_reserve", DEFAULT_RESERVE)
    available = view.gold() - reserve
    if available < MEDICINE_COST:
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


def _wall_hurt(wall: Role, config: Config | None) -> bool:
    """这面墙是否已残血到该用修复包（阈值 ``consumables.wall_hp_ratio``）。"""
    ratio = _float_knob(config, "consumables.wall_hp_ratio", WALL_HP_RATIO)
    return wall.health <= 1000 * ratio


def _fixer_reserve(config: Config | None) -> int:
    """买修复包时要留的应急金（``consumables.wall_fixer_reserve``，默认 0）。"""
    return max(0, _int_knob(config, "consumables.wall_fixer_reserve", WALL_FIXER_RESERVE))


def needs_wall_fixer(view: WorldView, config: Config | None = None) -> bool:
    """是否该去**买**一个修复包：有残血围墙、队里还没有包、且买得起。

    公开给经济模块用（``economy._shopper`` 要为它专程派一个人去商店）。买与用必须
    共用同一套判定，否则会出现「经济以为有人会买、消耗品却因为预留金不足而不买」。
    """
    if not view.own_walls():
        return False
    if _count(view, WALL_FIXER) > 0:
        return False  # 队里已有包：先用掉，别重复买（10 金也是钱）
    if not any(_wall_hurt(wall, config) for wall in view.own_walls()):
        return False
    return view.gold() - _fixer_reserve(config) >= WALL_FIXER_COST


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


def wall_fixer_target(
    view: WorldView, unit: Role, config: Config | None = None
) -> Pos | None:
    """身旁最该修的那面残血墙（无 → ``None``）。

    公开入口：经济模块要让路（``economy._fixer_repair_first``），判定必须与这里
    完全一致，否则会出现「经济以为消耗品会用、消耗品却找不到目标」而白站一回合。
    """
    candidates = [
        wall
        for wall in view.own_walls()
        if chebyshev(wall.pos, unit.pos) <= 1 and _wall_hurt(wall, config)
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
