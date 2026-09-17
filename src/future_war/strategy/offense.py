"""进攻策略（工作包 27/28/29，方案 M6/M3）：火箭狙击 / 召唤骚扰 / 角色狙击。

- ``plan_base_snipe``（WP27）：火箭 L3 全图射程且无机器人威胁时，按冷却轰击敌方
  基地（基地全局可见、导弹不被阻挡，任务书 §4.3/§4.5.4）。默认开（施压打法）。
- ``plan_role_snipe``（WP29）：视野内敌方机动单位进入武器射程时优先狙杀，阻断其
  任务/经济；默认关（config）。
- ``plan_summon``（WP28）：余钱买机器人召唤令灌对方下夜，每天上限 10；默认关。

全部为 config 开关，默认不影响基线。仅用标准库。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from future_war.config import Config
from future_war.models import Action, Role, RoleCommand, enum_to_str
from future_war.core.world_map import chebyshev
from future_war.core.world_view import WorldView
from future_war.strategy.combat import attack_positions

SUMMON_ORDER: Final = "SmallRobotSummonOrder"
DEFAULT_SUMMON_CAP: Final = 10
SUMMON_RESERVE: Final = 100  # 召唤不得挤占防守升级的应急金（economy.emergency_reserve）

# 召唤令 → (商品名, 机器人种类, 击杀积分)。《任务书》§4.6.3 + §4.7.2：
# 花金币给**对方下个夜晚**加机器人；这些机器人从对方一侧推进，会撞上我们的
# 武器防线 —— 等于用金币**直接买击杀分**：
#   小型 20 金/1 分 = 20 金/分；中型 30 金/2 分 = **15 金/分（最优）**；
#   大型 100 金/4 分 = 25 金/分；BOSS 200 金/10 分 = 20 金/分（血厚难杀，不推荐）。
# 每天最多 10 张（所以上限 ≈ 150 金换 10 分/天），是积分规则里唯一可主动放大的项。
DEFAULT_SNIPE_HP_RATIO: Final = 0.3  # 只在对方残血时狙杀，避免牺牲清波火力

_SUMMON_ECONOMY: Final = (
    ("MiddleRobotSummonOrder", 30, "middleRobot"),
    ("SmallRobotSummonOrder", 20, "smallRobot"),
    ("BossRobotSummonOrder", 200, "bossRobot"),
    ("LargeRobotSummonOrder", 100, "largeRobot"),
)
_MOBILE_KINDS: Final = frozenset({"pioneer", "worker"})
_CONTROLLER_RANGE: Final = 1


@dataclass
class OffenseState:
    """跨回合进攻状态：当天已用召唤令数。"""

    day: int = 0
    summons_today: int = 0
    bought: dict[int, str] = field(default_factory=dict)

    def sync(self, view: WorldView) -> None:
        if view.day != self.day:
            self.day = view.day
            self.summons_today = 0


def plan_offense(
    view: WorldView,
    config: Config | None = None,
    exclude: frozenset[int] = frozenset(),
    state: OffenseState | None = None,
) -> dict[int, RoleCommand]:
    """进攻指令（夜间武器狙击 + 召唤骚扰）；不含防御目标（由 combat 负责）。"""
    commands: dict[int, RoleCommand] = {}
    commands.update(_plan_base_snipe(view, config, exclude))
    commands.update(_plan_role_snipe(view, config, exclude | set(commands)))
    commands.update(_plan_summon(view, config, state))
    return commands


def _plan_base_snipe(
    view: WorldView, config: Config | None, exclude: frozenset[int]
) -> dict[int, RoleCommand]:
    if not _flag(config, "offense.base_snipe_enabled", True):
        return {}
    if _base_under_threat(view):
        return {}  # 自家吃紧时绝不把武器火力挪去轰敌基地（生存分 >> 击杀分）
    target = view.enemy_base_pos()
    if target is None:
        return {}
    commands: dict[int, RoleCommand] = {}
    for weapon, controller in _armed(view, exclude):
        if chebyshev(weapon.pos, target) <= weapon.attackRange:
            commands[weapon.id] = RoleCommand(
                action=Action.ATTACK,
                controllerId=str(controller.id),
                targetPos=attack_positions(weapon, [target]),
            )
    return commands


def _base_under_threat(view: WorldView) -> bool:
    """基地附近（切比雪夫 ≤4）是否还有瞄准我方的机器人。"""
    base = view.base_pos()
    if base is None:
        return False
    return any(
        chebyshev(robot.pos, base) <= 4 for robot in view.robots_targeting_us()
    )


def _plan_role_snipe(
    view: WorldView, config: Config | None, exclude: frozenset[int]
) -> dict[int, RoleCommand]:
    """狙杀视野内敌方机动单位；默认只在**对方残血**时出手（见 ``role_snipe_hp_ratio``）。

    敌方单位阵亡后要等「次日白天开始后 20 回合」才复活、且背包保留（§4.5.2），
    所以打断一次就等于废掉对手近 20 回合的经济/任务链。默认阈值让这条只在
    「一发能收掉」时才用，避免为了骚扰而牺牲清波火力。

    目标位置个数同样按武器等级（接口 §2.2，见 ``combat.target_slots``）：L1 恒 1 个
    （与旧行为一致），加特林/火箭 L2/L3 在残血目标足够时才发多个。
    """
    if not _flag(config, "offense.role_snipe_enabled", False):
        return {}
    threshold = _ratio_flag(config, "offense.role_snipe_hp_ratio", DEFAULT_SNIPE_HP_RATIO)
    enemies = [
        e for e in view.enemy_mobile_units() if _role_hp_ratio(e) <= threshold
    ]
    if not enemies:
        return {}
    commands: dict[int, RoleCommand] = {}
    for weapon, controller in _armed(view, exclude):
        in_range = [e for e in enemies if chebyshev(e.pos, weapon.pos) <= weapon.attackRange]
        if in_range:
            ranked = sorted(in_range, key=lambda e: (chebyshev(e.pos, weapon.pos), e.id))
            commands[weapon.id] = RoleCommand(
                action=Action.ATTACK,
                controllerId=str(controller.id),
                targetPos=attack_positions(weapon, [e.pos for e in ranked]),
            )
    return commands


def _role_hp_ratio(role: Role) -> float:
    """敌方角色血量比例（上限按 §4.5.2：工人 220 / 开拓者 200）。"""
    max_hp = {"worker": 220, "pioneer": 200}.get(enum_to_str(role.roleType), 200)
    return role.health / max_hp if max_hp else 1.0


def _plan_summon(
    view: WorldView, config: Config | None, state: OffenseState | None
) -> dict[int, RoleCommand]:
    """把「花不完的金币」换成击杀分：买性价比最高的召唤令（详见 ``_SUMMON_ECONOMY``）。

    只在**白天**买（夜晚商店交易同样合法，但夜里角色要操控武器，走开就少一座武器
    开火）；只花掉超出应急金的部分，绝不挤占武器/基地升级。
    """
    if not _flag(config, "offense.summon_harass_enabled", False) or not view.is_day():
        return {}
    state = state if state is not None else OffenseState()
    state.sync(view)
    cap = _int_flag(config, "offense.summon_daily_cap", DEFAULT_SUMMON_CAP)
    if state.summons_today >= cap:
        return {}
    reserve = _int_flag(config, "offense.summon_reserve", SUMMON_RESERVE)
    available = view.gold() - reserve
    if available <= 0:
        return {}
    shop = view.weapon_shop_pos()
    if shop is None:
        return {}
    buyer = next(
        (role for role in view.own_workers() if chebyshev(role.pos, shop) <= 1),
        None,
    )
    if buyer is None:
        return {}
    order = next(
        (name for name, price, _kind in _SUMMON_ECONOMY if price <= available),
        None,
    )
    if order is None:
        return {}
    state.summons_today += 1
    return {buyer.id: RoleCommand(action=Action.BUY, name=order, num=1)}


def _armed(
    view: WorldView, exclude: frozenset[int]
) -> list[tuple[Role, Role]]:
    mobile = list(view.own_workers()) + list(view.own_pioneer())
    pairs: list[tuple[Role, Role]] = []
    used: set[int] = set()
    for weapon in sorted(view.own_weapons(), key=lambda w: w.id):
        if weapon.cooldown > 0 or weapon.id in exclude:
            continue
        controller = next(
            (
                role
                for role in sorted(mobile, key=lambda r: r.id)
                if role.id not in used
                and chebyshev(role.pos, weapon.pos) <= _CONTROLLER_RANGE
            ),
            None,
        )
        if controller is not None:
            used.add(controller.id)
            pairs.append((weapon, controller))
    return pairs


def _flag(config: Config | None, key: str, default: bool) -> bool:
    value = config.get(key) if config is not None else None
    return value if isinstance(value, bool) else default


def _int_flag(config: Config | None, key: str, default: int) -> int:
    value = config.get(key) if config is not None else None
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _ratio_flag(config: Config | None, key: str, default: float) -> float:
    value = config.get(key) if config is not None else None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return default
