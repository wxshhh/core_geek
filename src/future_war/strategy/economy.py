"""白天经济（工作包 11/15，方案 M7）：建造 / 采集 / 贩卖 / 升级 + 黄昏就位。

第 1 天的目标只有一个：**夜晚能打**。工人决策按固定优先级执行：

1. **建武器**（金币 ≥25 且武器 <3）：武器是全部的攻防手段，必须第 1 天建满。
   候选格用 ``builder.standable_cells`` 过滤 —— 推断内环里贴着基地的格子常常
   整圈都是障碍（基地本体/出生点），工人永远无法与之相邻，只会原地绕圈。
2. **建围墙**（黄区、背包有石头）：围墙阻挡机器人推进（任务书 §4.7.3「机器人
   攻击阻挡其移动的单位」），为武器争取输出回合。没有石头就先去采石头。
3. **买/用升级券**：金库充裕时把金币换成火力（1 级 → 2 级武器伤害翻倍）。
4. **采集 → 贩卖**：石头既是围墙材料又能卖钱，其余按 copper > iron > stone。

黄昏（白天第 ``economy.dusk_return`` 回合起）停止施工，进入**就位**阶段：按
``builder.assign_controllers`` 的一对一分配把每个移动角色送到武器的操控位，
保证夜晚第 1 回合就有 3 座武器开火。旧实现只把角色送回基地，而基地并不总是
贴着武器，夜里前几回合只有 1 座武器有操控者。

移动统一交给 ``core.nav.resolve_moves`` 做多单位无碰撞解析。仅用标准库。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from future_war.config import Config
from future_war.models import Action, Pos, Role, RoleCommand, enum_to_str
from future_war.core.nav import resolve_moves
from future_war.core.world_map import chebyshev
from future_war.core.world_view import WorldView
from future_war.strategy.builder import (
    assign_controllers,
    base_center,
    preferred_weapon_cells,
    staging_cell,
    standable_cells,
    wall_line,
)

WEAPON_COST: Final = 25
WALL_COST: Final = 1
VOUCHER_COST: Final = 100
VOUCHER: Final = "WeaponUpgradeVoucher1"
SELL_THRESHOLD: Final = 5
DUSK_RETURN: Final = 40
DAY_LENGTH: Final = 130
WALL_MAX: Final = 12
_WEAPON_ORDER: Final = ("rocket", "railgun", "gatling")
_DEFAULT_PLAN: Final = ("rocket", "railgun", "railgun")
_MINE_KINDS: Final = frozenset({"stone", "iron", "copper"})
_ORE_VALUE: Final = {"stone": 1, "iron": 3, "copper": 5}
Cell = tuple[int, int]


@dataclass
class EconomyState:
    """跨回合经济状态：建造失败候选格黑名单 + 当期建造角色分配。"""

    failed_build_cells: set[Cell] = field(default_factory=set)
    pending_build: dict[int, tuple[Cell, bool]] = field(default_factory=dict)
    builder_id: int | None = None
    wall_probes: int = 0  # 当天已消耗的「探路」建造次数（每天重置）
    wall_probe_day: int = 0
    wall_confirmed: bool = False  # 是否已确认过一处合法围墙位
    wall_dir: tuple[int, int] | None = None  # 来袭方向（首次判定后锁定）


def plan_economy(
    view: WorldView, config: Config | None = None, state: EconomyState | None = None
) -> dict[int, RoleCommand]:
    """为所有工人产出本回合经济指令（建造/采集/贩卖/升级 + 移动）。"""
    state = state if state is not None else EconomyState()
    _digest_feedback(view, state)
    if not view.is_day():
        return {}
    max_weapons = _int_config(config, "build.day1_max_weapons", 3)
    workers = list(view.own_workers())
    if _staging(view, config):
        state.builder_id = None
        return _plan_staging(view)
    if state.builder_id is not None and state.builder_id not in {w.id for w in workers}:
        state.builder_id = None
    orders = _work_orders(view, workers, max_weapons, state)
    assignment = _assign_mines(view, workers)
    commands: dict[int, RoleCommand] = {}
    goals: dict[int, Pos] = {}
    for worker in workers:
        cmd, goal = _plan_worker(
            view,
            config,
            worker,
            orders.get(worker.id, "econ"),
            max_weapons,
            state,
            assignment.get(worker.id),
        )
        if cmd is not None:
            commands[worker.id] = cmd
        elif goal is not None:
            goals[worker.id] = goal
    for uid, step in resolve_moves(view, goals).items():
        if step is not None:
            commands[uid] = RoleCommand(action=Action.MOVE, targetPos=(step,))
    _record_build_attempts(commands, state)
    return commands


def _record_build_attempts(
    commands: dict[int, RoleCommand], state: EconomyState
) -> None:
    """记账本回合的建造目标：下回合用 ``lastRoundRoleActionResults`` 证伪非法格。

    判题器对非法建造只回 ``false``，而非法的最常见原因就是**可建造区推断错了**。
    不记账就永远不会修正推断，工人会在同一个非法格上无限重试（实测第 1 天卡死）。
    """
    for uid, command in commands.items():
        name = command.name
        if name not in ("gatling", "railgun", "rocket", "wall") or not command.targetPos:
            continue
        pos = command.targetPos[0]
        state.pending_build[uid] = ((pos.x, pos.y), name == "wall")


# ------------------------------------------------------------------ 角色分工


def _work_orders(
    view: WorldView,
    workers: list[Role],
    max_weapons: int,
    state: EconomyState,
) -> dict[int, str]:
    """分工：1 名「建造者」（先武器后围墙）+ 其余「经济」（采卖）。

    只让一名工人施工：另一名从第 1 天起就持续采矿/贩卖，否则武器建满前没人
    攒钱、也没人备石头（旧实现的围墙分支永远排不上队）。建造者锁定到当前
    活着的工人，避免两名工人同时朝同一格跑。
    """
    if not workers:
        return {}
    orders: dict[int, str] = {w.id: "econ" for w in workers}
    builder = state.builder_id
    if builder is None:
        cells = standable_cells(view, view.blue_build_cells())
        builder = _nearest_worker(workers, cells).id if cells else workers[0].id
        state.builder_id = builder
    orders[builder] = "build"
    shop = _shopper(view, workers, max_weapons, orders[builder], state)
    if shop is not None:
        orders[shop.id] = "shop"
    return orders


def _wall_ready(view: WorldView, state: EconomyState, config: Config | None) -> bool:
    """是否准许动用石头修墙。

    可建造区是**推断**出来的，开局我们并不知道黄区在哪；盲目让工人拿着宝贵的
    石头去撞非法的格子，会把第 1 天全部烧光、连一座武器都建不齐。因此：
    已经确认过合法墙位 → 正常施工；否则只在「武器已满 + 还没有围墙」时放行
    有限次探路（默认 3 次），探明后即转为常规铺设。
    """
    if not _flag(config, "build.wall_enabled", True):
        return False
    if len(view.own_walls()) >= _wall_target(view, state):
        return False
    if state.wall_probe_day != view.day:
        state.wall_probe_day = view.day
        state.wall_probes = 0  # 探路额度**每天**重置，否则用完一次就永久放弃围墙
    if state.wall_confirmed:
        return True
    if len(view.own_weapons()) < _int_config(config, "build.day1_max_weapons", 3):
        return False
    return state.wall_probes < _int_config(config, "build.wall_probe_budget", 6)


def _flag(config: Config | None, key: str, default: bool) -> bool:
    value = config.get(key) if config is not None else None
    return value if isinstance(value, bool) else default


def _nearest_worker(workers: list[Role], cells: frozenset[Pos]) -> Role:
    """到候选格集合最近（切比雪夫）的工人，同距按 id。"""

    def distance(worker: Role) -> tuple[int, int]:
        return (min(chebyshev(worker.pos, c) for c in cells), worker.id)

    return min(workers, key=distance)


def _shopper(
    view: WorldView,
    workers: list[Role],
    max_weapons: int,
    builder: str,
    state: EconomyState,
) -> Role | None:
    """武器建满、防线铺完且金币够买券时，指派离武器商店最近的**非建造者**采购。

    建造者不参与采购：否则它一去一回，武器/围墙的施工就停摆，另一名工人又没
    被授权铺墙（旧实现两名工人偶尔会抢同一格围墙）。
    """
    if len(view.own_weapons()) < max_weapons or view.gold() < VOUCHER_COST:
        return None
    if 0 < len(view.own_walls()) < _wall_target(view, state):
        return None  # 防线还没铺完，先让工人施工
    shop = view.weapon_shop_pos()
    candidates = [w for w in workers if w.id != builder]
    if shop is None or not candidates:
        return None
    return min(candidates, key=lambda w: (chebyshev(w.pos, shop), w.id))


def _plan_worker(
    view: WorldView,
    config: Config | None,
    worker: Role,
    order: str,
    max_weapons: int,
    state: EconomyState,
    mine: Pos | None,
) -> tuple[RoleCommand | None, Pos | None]:
    """按职责产出（指令 | 移动目标）。

    优先级：建造武器（``build``）→ 围墙（``econ``，缺石头时先采石）→ 买券
    （``shop``）→ 贩卖（背包够阈值）→ 采集。围墙只交给采卖工人，专职建造者
    专心把武器建满：两人都去铺墙会让第 1 天既没武器也没钱。
    """
    if order == "build":
        plan = _plan_build_weapon(view, config, worker, max_weapons, state.failed_build_cells)
        if plan is not None:
            return plan
    elif order in ("econ", "shop") and _wall_ready(view, state, config):
        plan = _plan_build_wall(view, config, worker, state)
        if plan is not None:
            return plan
    if order == "shop":
        plan = _plan_shopping(view, worker)
        if plan is not None:
            return plan
    if order in ("econ", "shop") or _ore_count(worker) >= SELL_THRESHOLD:
        sold = _plan_sell(view, worker)
        if sold is not None:
            return sold
    need_stone = _needs_stone(view, state, config, order)
    return _plan_collect(worker, _preferred_mine(view, worker, order, mine, need_stone))


def _needs_stone(
    view: WorldView, state: EconomyState, config: Config | None, order: str
) -> bool:
    """当前是否真的缺石头（缺 → 优先采石；不缺 → 专心挖铜铁换钱）。"""
    if order not in ("econ", "shop") or not _flag(config, "build.wall_enabled", True):
        return False
    if len(view.own_walls()) >= _wall_target(view, state):
        return False
    return True


def _preferred_mine(
    view: WorldView, worker: Role, order: str, mine: Pos | None, need_stone: bool
) -> Pos | None:
    """选矿：需要石头修墙时优先石矿，否则按产值 copper > iron > stone 挑最近的。

    一个背包格子的产值差异极大（铜 5 金 / 铁 3 金 / 石 1 金），而往返小贩的路
    是固定成本 —— 背包里装满石头去卖是净亏损，必须优先高价值矿。
    """
    priority = ("stone",) if need_stone else ("copper", "iron", "stone")
    for kind in priority:
        cells = [zone.pos for zone in view.mines(kind)]
        if cells:
            return min(cells, key=lambda m: (chebyshev(worker.pos, m), m.x, m.y))
    return mine


# ------------------------------------------------------------------ 建造


def _plan_build_weapon(
    view: WorldView,
    config: Config | None,
    worker: Role,
    max_weapons: int,
    failed: set[Cell],
) -> tuple[RoleCommand | None, Pos | None] | None:
    if len(view.own_weapons()) >= max_weapons or view.gold() < WEAPON_COST:
        return None
    kind = _weapon_plan(config, len(view.own_weapons()))
    free = [c for c in view.blue_build_cells() if not _occupied(view, c)]
    cells = _free_cells(standable_cells(view, frozenset(free)), failed)
    if not cells:
        return None
    rank = {cell: i for i, cell in enumerate(preferred_weapon_cells(view))}
    cell = _adjacent_cell(worker, cells, rank)
    if cell is not None:
        return RoleCommand(action=Action.BUILD, name=kind, targetPos=(cell,)), None
    goal = _nearest_cell(worker, cells, rank)
    return (None, goal) if goal is not None else None


def _plan_build_wall(
    view: WorldView, config: Config | None, worker: Role, state: EconomyState
) -> tuple[RoleCommand | None, Pos | None] | None:
    """背包有石头就补防线（来袭面 + 两侧的 U 形）；没石头交给采集分支。"""
    failed = state.failed_build_cells
    if worker.backpack.count("stone") < WALL_COST:
        return None
    line = tuple(
        c
        for c in wall_line(
            view,
            None,
            None,
            _occupied_and_failed(view, failed),
            _threat_dir(view, state),
        )
        if not _occupied(view, c)
    )[: _wall_target(view, state) + len(failed)]
    if not line:
        return None
    cells = frozenset(line)
    rank = {cell: i for i, cell in enumerate(line)}
    cell = _adjacent_cell(worker, cells, rank)
    if cell is not None:
        return RoleCommand(action=Action.BUILD, name="wall", targetPos=(cell,)), None
    return None, _nearest_cell(worker, cells, rank)


def _wall_target(view: WorldView, state: EconomyState) -> int:
    """本局围墙目标数：U 形防线长度，上限 ``build.wall_max``（默认 12）。"""
    return min(len(wall_line(view, None, None, None, _threat_dir(view, state))), WALL_MAX)


def _threat_dir(view: WorldView, state: EconomyState) -> tuple[int, int] | None:
    """来袭方向（八方向之一），**首次判定后锁定**。

    用户实测：机器人浪潮从基地的**一侧**刷出，因此围墙只围三面（来袭面 + 两侧），
    背面不建 —— 既省石头，也不会把自己人围死。

    判定顺序：① 场上以我方为目标的机器人质心（真机第一晚后即有观测）；
    ② 敌方基地方向（基地位置全局可见，且通常就是机器人来袭方向）；
    ③ 暂无信息 → None（退化为四面全建）。
    """
    if state.wall_dir is not None:
        return state.wall_dir
    base = base_center(view)
    if base is None:
        return None
    robots = list(view.robots_targeting_us())
    if robots:
        center = Pos(
            round(sum(r.pos.x for r in robots) / len(robots)),
            round(sum(r.pos.y for r in robots) / len(robots)),
        )
    else:
        center = view.enemy_base_pos()
    if center is None:
        return None
    dx = (center.x > base.x) - (center.x < base.x)
    dy = (center.y > base.y) - (center.y < base.y)
    if (dx, dy) == (0, 0):
        return None
    state.wall_dir = (dx, dy)
    return state.wall_dir


# ------------------------------------------------------------------ 采卖 / 升级


def _plan_shopping(
    view: WorldView, worker: Role
) -> tuple[RoleCommand | None, Pos | None] | None:
    """前往武器商店买武器升级券（到店即买）。"""
    if view.gold() < VOUCHER_COST:
        return None
    shop = view.weapon_shop_pos()
    if shop is None:
        return None
    if chebyshev(worker.pos, shop) <= 1:
        return RoleCommand(action=Action.BUY, name=VOUCHER, num=1), None
    return None, shop


def _plan_sell(view: WorldView, worker: Role) -> tuple[RoleCommand | None, Pos | None] | None:
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


def _plan_collect(worker: Role, mine: Pos | None) -> tuple[RoleCommand | None, Pos | None]:
    if mine is None:
        return None, None
    if chebyshev(worker.pos, mine) <= 1:
        return RoleCommand(action=Action.COLLECT, targetPos=(mine,)), None
    return None, mine


# ------------------------------------------------------------------ 黄昏就位


def _staging(view: WorldView, config: Config | None) -> bool:
    """是否进入黄昏就位阶段：白天第 ``economy.dusk_return`` 回合起。"""
    threshold = _int_config(config, "economy.dusk_return", DUSK_RETURN)
    return view.is_day() and (view.round_no - 1) % DAY_LENGTH >= threshold


def _plan_staging(view: WorldView) -> dict[int, RoleCommand]:
    """把每个移动角色送到所属武器的操控位（就位后本回合原地待命）。

    残血角色（≤ ``consumables.retreat_hp_ratio``）不派去当操控者：先回基地，
    让健康角色顶上 —— 夜里再挨一下就是 20 回合无人操控（§4.5.2）。
    """
    weapons = tuple(sorted(view.own_weapons(), key=lambda w: w.id))
    mobile = list(view.own_workers()) + list(view.own_pioneer())
    hurt = _hurt_ids(view)
    if not weapons:
        return _send_home(view, mobile, {})
    healthy = [r for r in mobile if r.id not in hurt]
    assignment = assign_controllers(view, weapons) if healthy else {}
    by_id = {r.id: r for r in healthy}
    goals: dict[int, Pos] = {}
    for weapon in weapons:
        uid = assignment.get(weapon.id)
        role = by_id.get(uid) if uid is not None else None
        if role is None or chebyshev(role.pos, weapon.pos) <= 1:
            continue  # 已在操控位：原地待命，夜晚直接开火
        cell = staging_cell(view, weapon)
        if cell is not None:
            goals[role.id] = cell
    return _send_home(view, mobile, goals)


def _hurt_ids(view: WorldView) -> frozenset[int]:
    """残血角色 id（撤退阈值见 combat.RETREAT_HP_RATIO）。"""
    from future_war.strategy.combat import retreating_roles

    return retreating_roles(view, None)


def _send_home(
    view: WorldView, mobile: list[Role], goals: dict[int, Pos]
) -> dict[int, RoleCommand]:
    """未分配操控位的角色撤回基地附近（≤2 格内原地待命）。"""
    base = view.base_pos()
    if base is not None:
        for role in mobile:
            if role.id in goals:
                continue
            if chebyshev(role.pos, base) > 2:
                goals[role.id] = base
    commands: dict[int, RoleCommand] = {}
    for uid, step in resolve_moves(view, goals).items():
        if step is not None:
            commands[uid] = RoleCommand(action=Action.MOVE, targetPos=(step,))
    return commands


# ------------------------------------------------------------------ 辅助


def _digest_feedback(view: WorldView, state: EconomyState) -> None:
    """上回合建造目标没变成建筑 → 该格非法，拉黑。

    判题器对非法建造只回 ``false``，而非法的最常见原因就是**可建造区推断错了**；
    直接看「目标格上现在有没有对应建筑」比读动作结果更可靠（结果位还可能被
    同回合的移动结算覆盖）。不修正推断，工人会在同一个非法格上无限重试。
    """
    for uid, (cell, is_wall) in list(state.pending_build.items()):
        pos = Pos(*cell)
        if view.action_ok(uid) is False or not _has_building(view, pos, is_wall):
            state.failed_build_cells.add(cell)
            if is_wall:
                state.wall_probes += 1
        elif is_wall:
            state.wall_confirmed = True  # 找到过合法墙位 → 放开施工
        state.pending_build.pop(uid, None)


def _has_building(view: WorldView, pos: Pos, is_wall: bool) -> bool:
    """该格是否已有对应类别的建筑（围墙单独判定，不与武器混淆）。"""
    for role in view.own_roles():
        if role.pos != pos:
            continue
        kind = enum_to_str(role.roleType)
        if (kind == "wall") == is_wall and kind != "station":
            return True
    return False


def _occupied_and_failed(view: WorldView, failed: set[Cell]) -> set[Pos]:
    """已建建筑格 + 施工失败格：都不再作为围墙候选（避免在非法格上无限重试）。"""
    occupied = {role.pos for role in view.own_roles()}
    occupied.update(role.pos for role in view.enemy_roles())
    occupied.update(Pos(x, y) for x, y in failed)
    return occupied


def _occupied(view: WorldView, pos: Pos) -> bool:
    """该格是否已有己方/敌方建筑（同格重砌会覆盖旧武器，等于白花钱）。"""
    return any(role.pos == pos for role in view.own_roles()) or any(
        role.pos == pos for role in view.enemy_roles()
    )


def _free_cells(cells: frozenset[Pos], failed: set[Cell]) -> frozenset[Pos]:
    return frozenset(c for c in cells if (c.x, c.y) not in failed)


def _adjacent_cell(
    worker: Role,
    cells: frozenset[Pos],
    rank: dict[Pos, int],
) -> Pos | None:
    valid = [cell for cell in cells if chebyshev(worker.pos, cell) == 1]
    if not valid:
        return None
    return min(valid, key=lambda c: (rank.get(c, 1 << 30), c.x, c.y))


def _nearest_cell(
    worker: Role, cells: frozenset[Pos], rank: dict[Pos, int]
) -> Pos | None:
    if not cells:
        return None
    return min(
        cells,
        key=lambda c: (chebyshev(worker.pos, c), rank.get(c, 1 << 30), c.x, c.y),
    )


def _assign_mines(view: WorldView, workers: list[Role]) -> dict[int, Pos]:
    """给每个工人分配互不相同的最近矿区（矿少时允许共享），避免同矿争夺。"""
    mines = [zone.pos for zone in view.mines()]
    if not mines:
        return {}
    available = list(mines)
    assignment: dict[int, Pos] = {}
    for worker in sorted(workers, key=lambda w: w.id):
        if not available:
            available = list(mines)
        nearest = min(available, key=lambda m: (chebyshev(worker.pos, m), m.x, m.y))
        assignment[worker.id] = nearest
        available.remove(nearest)
    return assignment


def _best_ore(worker: Role) -> str | None:
    present = [item for item in worker.backpack if item in _MINE_KINDS]
    if not present:
        return None
    if "stone" in present:
        return "stone"  # 石头既卖钱又是围墙材料，优先脱手
    return max(present, key=lambda item: _ORE_VALUE[item])


def _ore_count(worker: Role) -> int:
    return sum(1 for item in worker.backpack if item in _MINE_KINDS)


def _weapon_plan(config: Config | None, index: int) -> str:
    """第 ``index`` 座武器（0 起）的类型。"""
    plan = _DEFAULT_PLAN
    if config is not None:
        mix = config.get("build.weapon_mix")
        if isinstance(mix, dict):
            built: list[str] = []
            for kind, count in mix.items():
                if kind in _WEAPON_ORDER and isinstance(count, int) and not isinstance(count, bool):
                    built.extend([kind] * count)
            if built:
                plan = tuple(built)
    return plan[min(index, len(plan) - 1)]


def _int_config(config: Config | None, key: str, default: int) -> int:
    value = config.get(key) if config is not None else None
    return value if isinstance(value, int) and not isinstance(value, bool) else default
