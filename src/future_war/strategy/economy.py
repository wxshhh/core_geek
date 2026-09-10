"""基础经济（工作包 11，方案 M7）：工人采矿 / 贩卖 / 建造武器。

每个工人每回合的决策优先级：

1. 建造：武器数未满且金币足够、身旁有可建造格 → ``build``；否则朝最近可建造格移动。
2. 贩卖：背包矿石达到阈值且在小贩旁 → ``sell``；否则朝小贩移动。
3. 采集：在矿区旁 → ``collect``；否则朝最近矿区移动。

移动统一交给 ``core.nav.resolve_moves`` 做多单位无碰撞解析；本模块只产出
``RoleCommand``，不为建筑/开拓者决策（战斗与任务由 M6/M9 负责）。仅用标准库。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from future_war.config import Config
from future_war.models import Action, Pos, Role, RoleCommand
from future_war.core.nav import resolve_moves
from future_war.core.world_map import chebyshev
from future_war.core.world_view import WorldView
from future_war.strategy.builder import preferred_weapon_cells

WEAPON_COST: Final = 25
SELL_THRESHOLD: Final = 5
_WEAPON_ORDER: Final = ("rocket", "railgun", "gatling")
_DEFAULT_PLAN: Final = ("rocket", "railgun", "railgun")
_MINE_KINDS: Final = frozenset({"stone", "iron", "copper"})
_ORE_VALUE: Final = {"stone": 1, "iron": 3, "copper": 5}
Cell = tuple[int, int]


@dataclass
class EconomyState:
    """跨回合经济状态：建造失败候选格黑名单 + 待验证建造。"""

    failed_build_cells: set[Cell] = field(default_factory=set)
    pending_build: dict[int, Cell] = field(default_factory=dict)


def plan_economy(
    view: WorldView, config: Config | None = None, state: EconomyState | None = None
) -> dict[int, RoleCommand]:
    """为所有工人产出本回合经济指令（采矿/贩卖/建造 + 移动）。"""
    state = state if state is not None else EconomyState()
    _digest_feedback(view, state)
    max_weapons = _int_config(config, "build.day1_max_weapons", 3)
    plan = _weapon_plan(config)
    commands: dict[int, RoleCommand] = {}
    goals: dict[int, Pos] = {}
    for worker in view.own_workers():
        cmd, goal = _plan_worker(view, worker, max_weapons, plan, state)
        if cmd is not None:
            commands[worker.id] = cmd
        elif goal is not None:
            goals[worker.id] = goal
    for uid, step in resolve_moves(view, goals).items():
        if step is not None:
            commands[uid] = RoleCommand(action=Action.MOVE, targetPos=(step,))
    return commands


def _digest_feedback(view: WorldView, state: EconomyState) -> None:
    """读上回合结果：建造失败的候选格拉黑。"""
    for uid, cell in list(state.pending_build.items()):
        if view.action_ok(uid) is False:
            state.failed_build_cells.add(cell)
        state.pending_build.pop(uid, None)


def _plan_worker(
    view: WorldView,
    worker: Role,
    max_weapons: int,
    plan: tuple[str, ...],
    state: EconomyState,
) -> tuple[RoleCommand | None, Pos | None]:
    build = _plan_build(view, worker, max_weapons, plan, state)
    if build is not None:
        return build
    if _ore_count(worker) >= SELL_THRESHOLD:
        sold = _plan_sell(view, worker)
        if sold is not None:
            return sold
    return _plan_collect(view, worker)


def _plan_build(
    view: WorldView,
    worker: Role,
    max_weapons: int,
    plan: tuple[str, ...],
    state: EconomyState,
) -> tuple[RoleCommand | None, Pos | None] | None:
    if len(view.own_weapons()) >= max_weapons or view.gold() < WEAPON_COST:
        return None
    kind = plan[min(len(view.own_weapons()), len(plan) - 1)]
    cells = view.blue_build_cells()
    rank = {cell: i for i, cell in enumerate(preferred_weapon_cells(view))}
    cell = _adjacent_cell(view, worker, cells, state.failed_build_cells, rank)
    if cell is not None:
        state.pending_build[worker.id] = (cell.x, cell.y)
        return RoleCommand(action=Action.BUILD, name=kind, targetPos=(cell,)), None
    goal = _nearest_cell(worker, cells, state.failed_build_cells, rank)
    return (None, goal) if goal is not None else None


def _plan_sell(
    view: WorldView, worker: Role
) -> tuple[RoleCommand | None, Pos | None] | None:
    ore = _best_ore(worker)
    vendor = view.vendor_pos()
    if ore is None or vendor is None:
        return None
    if chebyshev(worker.pos, vendor) <= 1:
        return (
            RoleCommand(action=Action.SELL, name=ore, num=worker.backpack.count(ore)),
            None,
        )
    return None, vendor


def _plan_collect(view: WorldView, worker: Role) -> tuple[RoleCommand | None, Pos | None]:
    mine = _nearest_mine(view, worker)
    if mine is None:
        return None, None
    if chebyshev(worker.pos, mine) <= 1:
        return RoleCommand(action=Action.COLLECT, targetPos=(mine,)), None
    return None, mine


def _adjacent_cell(
    view: WorldView,
    worker: Role,
    cells: frozenset[Pos],
    failed: set[Cell],
    rank: dict[Pos, int],
) -> Pos | None:
    blocked = view.obstacles()
    candidates = [
        cell
        for cell in cells
        if chebyshev(worker.pos, cell) == 1
        and cell not in blocked
        and (cell.x, cell.y) not in failed
    ]
    return (
        min(candidates, key=lambda c: (rank.get(c, 1 << 30), c.x, c.y))
        if candidates
        else None
    )


def _nearest_cell(
    worker: Role, cells: frozenset[Pos], failed: set[Cell], rank: dict[Pos, int]
) -> Pos | None:
    candidates = [c for c in cells if (c.x, c.y) not in failed]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda c: (chebyshev(worker.pos, c), rank.get(c, 1 << 30), c.x, c.y),
    )


def _nearest_mine(view: WorldView, worker: Role) -> Pos | None:
    mines = [zone.pos for zone in view.mines()]
    if not mines:
        return None
    return min(mines, key=lambda m: (chebyshev(worker.pos, m), m.x, m.y))


def _best_ore(worker: Role) -> str | None:
    present = [item for item in worker.backpack if item in _MINE_KINDS]
    return max(present, key=lambda item: _ORE_VALUE[item]) if present else None


def _ore_count(worker: Role) -> int:
    return sum(1 for item in worker.backpack if item in _MINE_KINDS)


def _weapon_plan(config: Config | None) -> tuple[str, ...]:
    mix = config.get("build.weapon_mix") if config is not None else None
    if not isinstance(mix, dict):
        return _DEFAULT_PLAN
    plan: list[str] = []
    for kind, count in mix.items():
        if kind in _WEAPON_ORDER and isinstance(count, int) and not isinstance(count, bool):
            plan.extend([kind] * count)
    return tuple(plan) or _DEFAULT_PLAN


def _int_config(config: Config | None, key: str, default: int) -> int:
    value = config.get(key) if config is not None else None
    return value if isinstance(value, int) and not isinstance(value, bool) else default
