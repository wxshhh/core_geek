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
from future_war.models import Action, Pos, Role, RoleCommand, enum_to_str
from future_war.core.world_map import chebyshev
from future_war.core.world_view import WorldView

SUMMON_ORDER: Final = "SmallRobotSummonOrder"
DEFAULT_SUMMON_CAP: Final = 10
SUMMON_RESERVE: Final = 60
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
    target = view.enemy_base_pos()
    if target is None:
        return {}
    commands: dict[int, RoleCommand] = {}
    for weapon, controller in _armed(view, exclude):
        if chebyshev(weapon.pos, target) <= weapon.attackRange:
            commands[weapon.id] = RoleCommand(
                action=Action.ATTACK,
                controllerId=str(controller.id),
                targetPos=(target,),
            )
    return commands


def _plan_role_snipe(
    view: WorldView, config: Config | None, exclude: frozenset[int]
) -> dict[int, RoleCommand]:
    if not _flag(config, "offense.role_snipe_enabled", False):
        return {}
    enemies = list(view.enemy_mobile_units())
    if not enemies:
        return {}
    commands: dict[int, RoleCommand] = {}
    for weapon, controller in _armed(view, exclude):
        in_range = [e for e in enemies if chebyshev(e.pos, weapon.pos) <= weapon.attackRange]
        if in_range:
            target = min(in_range, key=lambda e: (chebyshev(e.pos, weapon.pos), e.id))
            commands[weapon.id] = RoleCommand(
                action=Action.ATTACK,
                controllerId=str(controller.id),
                targetPos=(target.pos,),
            )
    return commands


def _plan_summon(
    view: WorldView, config: Config | None, state: OffenseState | None
) -> dict[int, RoleCommand]:
    if not _flag(config, "offense.summon_harass_enabled", False):
        return {}
    state = state if state is not None else OffenseState()
    state.sync(view)
    cap = _int_flag(config, "offense.summon_daily_cap", DEFAULT_SUMMON_CAP)
    if state.summons_today >= cap or view.gold() < SUMMON_RESERVE:
        return {}
    shop = view.weapon_shop_pos()
    if shop is None:
        return {}
    buyer = next(
        (
            role
            for role in view.own_workers()
            if chebyshev(role.pos, shop) <= 1
        ),
        None,
    )
    if buyer is None:
        return {}
    state.summons_today += 1
    return {
        buyer.id: RoleCommand(action=Action.BUY, name=SUMMON_ORDER, num=1)
    }


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
