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
    affordable_voucher,
    voucher_trip,
    assign_controllers,
    base_center,
    preferred_weapon_cells,
    shelter_cells,
    staging_cell,
    standable_cells,
    wall_line,
)

WEAPON_COST: Final = 25
WALL_COST: Final = 1
SELL_THRESHOLD: Final = 5  # 背着这么多矿石才值得专门跑一趟小贩
STONE_RESERVE: Final = 4  # 手里常备的修墙石头（其余石头可以卖）
DUSK_RETURN: Final = 70  # 70 = 白天不提前就位（交给夜晚回位）
DAY_LENGTH: Final = 130
WALL_MAX: Final = 12
# 与 config 默认值保持一致：config=None 时（测试/模拟器直连）也走同一套参数
WALL_PROBE_BUDGET: Final = 12
WALL_PROBE_FROM: Final = 45  # 白天第几回合起专门铺墙（之前留给经济）
MAX_WEAPONS: Final = 3
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
    notes: list[str] = field(default_factory=list)  # 本回合决策摘要（每回合重置，供日志）


def plan_economy(
    view: WorldView, config: Config | None = None, state: EconomyState | None = None
) -> dict[int, RoleCommand]:
    """为所有工人产出本回合经济指令（建造/采集/贩卖/升级 + 移动）。"""
    state = state if state is not None else EconomyState()
    state.notes = []
    _digest_feedback(view, state)
    _roll_probe_day(view, state)
    if not view.is_day():
        return {}
    max_weapons = _int_config(config, "build.day1_max_weapons", MAX_WEAPONS)
    workers = list(view.own_workers())
    if _staging(view, config):
        state.builder_id = None
        state.notes.append("staging=dusk")
        return _plan_staging(view)
    if state.builder_id is not None and state.builder_id not in {w.id for w in workers}:
        state.builder_id = None
    orders = _work_orders(view, workers, max_weapons, state, config)
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
    state.notes.extend(
        _diagnose(view, config, state, workers, orders, max_weapons, commands)
    )
    return commands


def _diagnose(
    view: WorldView,
    config: Config | None,
    state: EconomyState,
    workers: list[Role],
    orders: dict[int, str],
    max_weapons: int,
    commands: dict[int, RoleCommand],
) -> list[str]:
    """本回合「为什么建/没建」的一句话摘要（真机只能看控制台，必须自解释）。

    每回合最多几条短语，直接进 ``D-02`` 行；不含搜索，只读当前快照 + 状态，
    因此可以放心每回合调用。
    """
    weapons = len(view.own_weapons())
    walls = len(view.own_walls())
    stone = sum(w.backpack.count("stone") for w in workers)
    notes = [
        f"gold={view.gold()}",
        f"weapons={weapons}/{max_weapons}",
        f"walls={walls}/{_wall_target(view, state)}",
        f"stone={stone}",
        f"probes={state.wall_probes}",
    ]
    blocked: list[str] = []
    if weapons < max_weapons:
        if view.gold() < WEAPON_COST:
            blocked.append("weapon:gold")
        elif not _weapon_cells_free(view, state):
            blocked.append("weapon:no-cell")
    if _flag(config, "build.wall_enabled", True) and walls < _wall_target(view, state):
        if not _wall_ready(view, state, config):  # 已只读（跨天重置在 _roll_probe_day）
            blocked.append("wall:probe-denied")
        elif stone < WALL_COST:
            blocked.append("wall:no-stone")
        elif not _wall_line_free(view, state):
            blocked.append("wall:line-empty")
    if blocked:
        notes.append("blocked=" + ",".join(blocked))
    orders_text = ",".join(f"{uid}:{orders[uid]}" for uid in sorted(orders))
    notes.append(f"orders={orders_text}")
    builds = [
        f"{cmd.name}@({cmd.targetPos[0].x},{cmd.targetPos[0].y})"
        for cmd in commands.values()
        if cmd.name and cmd.targetPos
    ]
    notes.append("issued=" + ("+".join(builds) if builds else "none"))
    return notes


def _weapon_cells_free(view: WorldView, state: EconomyState) -> bool:
    """蓝区里是否还剩「工人站得进去且没失败过」的候选格（空 → 不可能再建武器）。"""
    free = frozenset(c for c in view.blue_build_cells() if not _occupied(view, c))
    return bool(_free_cells(standable_cells(view, free), state.failed_build_cells))


def _wall_line_free(view: WorldView, state: EconomyState) -> bool:
    """围墙防线里是否还剩可施工的格（空 → 不可能再建墙）。"""
    return any(
        not _occupied(view, c)
        for c in wall_line(
            view, None, None, _occupied_and_failed(view, state.failed_build_cells),
            _threat_dir(view, state),
        )
    )


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
    config: Config | None = None,
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
    shop = _shopper(view, workers, max_weapons, orders[builder], state, config)
    if shop is not None:
        orders[shop.id] = "shop"
    return orders


def _roll_probe_day(view: WorldView, state: EconomyState) -> None:
    """跨天重置探路额度（每天开工前调用一次，避免只读的判定函数带副作用）。"""
    if state.wall_probe_day != view.day:
        state.wall_probe_day = view.day
        state.wall_probes = 0  # 不重置的话，用完一次就永久放弃围墙


def _wall_window(view: WorldView, config: Config | None) -> bool:
    """是否进入「专门铺墙」时段：白天第 ``build.wall_probe_from`` 回合起。

    用户真机实测的教训：把「专程跑墙位」和经济工作混在同一个优先级里争抢，会让
    工人「挖一格石头 → 跑墙线 → 试错失败 → 再挖一格」无限空转 —— 墙 0/12，金币也
    恒为 0。改成**按时间分片**：上午纯经济（挖矿/贩卖/买券），下午专心铺墙。规则
    简单、可预测，两边都不再互相饿死。
    """
    if not view.is_day():
        return False
    threshold = _int_config(config, "build.wall_probe_from", WALL_PROBE_FROM)
    return (view.round_no - 1) % DAY_LENGTH >= threshold


def _wall_ready(view: WorldView, state: EconomyState, config: Config | None) -> bool:
    """是否准许动用石头修墙。

    可建造区是**推断**出来的，开局我们并不知道黄区在哪；盲目让工人拿着宝贵的
    石头去撞非法的格子，会把第 1 天全部烧光。因此：已经确认过合法墙位 → 正常
    施工；否则只放行「每天有限次」的探路（``build.wall_probe_budget``，默认 6），
    探明后即转为常规铺设。

    旧实现还额外要求「先建满 3 座武器才准碰墙」，实机结果是**一整天一堵墙都没
    建起来**：白天只有 70 回合，建造者要来回走、武器又可能因为推断错误卡住，等
    到条件满足时天已经黑了（而建造仅白天可用）。围墙只要 1 石头、却是把机器人
    挡在墙外挨打的唯一手段，因此改成**与武器并行推进**，代价由每日探路额度兜住。
    """
    if not _flag(config, "build.wall_enabled", True):
        return False
    if len(view.own_walls()) >= _wall_target(view, state):
        return False
    if state.wall_confirmed:
        return True
    return state.wall_probes < _int_config(config, "build.wall_probe_budget", WALL_PROBE_BUDGET)


def _stone_reserve(view: WorldView, state: EconomyState, config: Config | None) -> int:
    """手里要留住的石头数（其余可以卖）：够修完剩下的墙，且不超过 ``economy.stone_reserve``。

    留一点就够：真正的瓶颈是「不知道黄区在哪」，不是石头不够；囤 12 块只会把
    白天的产出全锁在背包里（用户真机实测：金币 0、白天无产出）。
    """
    if not _flag(config, "build.wall_enabled", True):
        return 0  # 不修墙就不必留石头，全部可以换钱
    remaining = max(0, _wall_target(view, state) - len(view.own_walls()))
    cap = _int_config(config, "economy.stone_reserve", STONE_RESERVE)
    return min(remaining, max(0, cap))


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
    config: Config | None = None,
) -> Role | None:
    """武器建满、防线铺完且金币够买券时，指派离武器商店最近的**非建造者**采购。

    建造者不参与采购：否则它一去一回，武器/围墙的施工就停摆，另一名工人又没
    被授权铺墙（旧实现两名工人偶尔会抢同一格围墙）。
    """
    if len(view.own_weapons()) < max_weapons:
        return None
    if affordable_voucher(view, view.gold(), config) is None:
        return None  # 没有「买得起又用得到」的券（20 金围墙券也算）就别派人去商店
    # 注意：**不能**用「防线还没铺完」挡住采购。围墙目标 12 堵本来就常修不满，
    # 旧判断等于永久占住采卖工人 → 武器永远停在 level1（真机实测正是如此：
    # 基地在夜里被推平，而金币攒着没处花）。升到 level2/3 直接翻倍夜里的输出，
    # 收益高于再多砌两堵墙。铺墙时段（build.wall_probe_from）之后工人自然会回去铺。
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

    优先级（用户真机实测后重排，见下）：建造武器 → **顺路砌墙** → **贩卖（有整批
    货就先去小贩）** → 专门跑一趟墙位 → 买券 → 采集。

    为什么把经济排在「专门跑墙位」前面：旧顺序里只要背包有 1 块石头，采卖工人就
    一直往墙线跑，而它为了修墙又总在采石 —— 结果**永远不去小贩**，金币从第 5 回合
    起就是 0，白天再无产出；同时探路把当天额度烧光（`wall:probe-denied`），墙还是
    0/12。现在：

    * **顺路砌墙**（已经在候选格旁边）永远允许 —— 零额外行程成本；
    * 有整批货（``economy.sell_batch``）就**先去卖**，途中不会被墙打断；
    * 专门跑墙位只在**空手**（没有待卖矿石）时做，避免「挖一格 → 跑墙线 → 再挖一格」
      的来回空转；已确认过合法墙位（``wall_confirmed``）则值得专程去铺。
    """
    if order == "build":
        plan = _plan_build_weapon(view, config, worker, max_weapons, state.failed_build_cells)
        if plan is not None:
            return plan
        # 武器建满 / 金币不足 / 无可用格 → 建造者转去铺墙，别闲着
    action, target = voucher_trip(view, worker, config)
    if action == "use":
        # 本回合什么都不发，让 plan_upgrades 的 use 指令落地（否则被经济指令盖掉）
        return None, None
    if action == "walk":
        return None, target  # 先把券送到目标建筑旁
    stone_keep = _stone_reserve(view, state, config)
    wall_ok = order in ("build", "econ", "shop") and _wall_ready(view, state, config)
    if wall_ok:
        plan = _plan_build_wall(view, config, worker, state, adjacent_only=True)
        if plan is not None:
            return plan  # 顺路砌：不花行程
    if order == "shop":
        plan = _plan_shopping(view, worker, config)
        if plan is not None:
            return plan
    sold = _plan_sell(view, worker, stone_keep, config)
    if sold is not None:
        return sold
    # 专门跑墙位：已确认过合法墙位（值得专程铺线），或已进入「铺墙时段」
    # （``build.wall_probe_from``，默认白天第 45 回合起）。上午留给经济：挖矿→贩卖→
    # 买券，下午石头也攒下了，再专心试推断出来的黄区。
    # 只在「手上没有铜铁（正在攒的那批货已脱手）」时才专程跑墙位：否则挖一格就
    # 被墙位拉走，永远攒不满一趟的货量，金币也就永远上不去（真机实测金币卡死在 45）。
    if wall_ok and _cargo(worker) == 0 and (
        state.wall_confirmed or _wall_window(view, config)
    ):
        plan = _plan_build_wall(view, config, worker, state, adjacent_only=False)
        if plan is not None:
            return plan
    need_stone = _needs_stone(view, state, config, order, worker)
    return _plan_collect(worker, _preferred_mine(view, worker, order, mine, need_stone))


def _needs_stone(
    view: WorldView, state: EconomyState, config: Config | None, order: str, worker: Role
) -> bool:
    """背包里的石头是否还没到储备量（没到 → 优先采石；到了 → 专心挖铜铁换钱）。

    旧实现是「只要还有墙没修就永远优先采石」，于是石头一直占着背包、卖不出去，
    金币从开局第 5 回合起恒为 0。改成只看储备量：备够 2 块就转去挖铜/铁换钱。
    """
    if not _flag(config, "build.wall_enabled", True):
        return False
    if order not in ("build", "econ", "shop"):
        return False
    if order == "build" and len(view.own_weapons()) < _int_config(
        config, "build.day1_max_weapons", MAX_WEAPONS
    ):
        return False  # 建造者手上还有武器要建 → 先专心攒金币，别去挖石头
    return worker.backpack.count("stone") < _stone_reserve(view, state, config)


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
    view: WorldView,
    config: Config | None,
    worker: Role,
    state: EconomyState,
    *,
    adjacent_only: bool = False,
) -> tuple[RoleCommand | None, Pos | None] | None:
    """背包有石头就补防线（来袭面 + 两侧的 U 形）；没石头交给采集分支。

    ``adjacent_only=True``：只做「已经在候选格旁边」的零行程建造（顺路砌墙）。
    否则返回走向墙线的移动目标 —— 调用方据此区分「顺路」与「专程跑一趟」。
    """
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
    if adjacent_only:
        return None
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
    view: WorldView, worker: Role, config: Config | None = None
) -> tuple[RoleCommand | None, Pos | None] | None:
    """前往武器商店买券（到店即买）：按性价比挑，20 金围墙券优先于 100 金武器券。"""
    voucher = affordable_voucher(view, view.gold(), config)
    if voucher is None:
        return None
    shop = view.weapon_shop_pos()
    if shop is None:
        return None
    if chebyshev(worker.pos, shop) <= 1:
        return RoleCommand(action=Action.BUY, name=voucher, num=1), None
    return None, shop


def _plan_sell(
    view: WorldView,
    worker: Role,
    stone_keep: int = 0,
    config: Config | None = None,
) -> tuple[RoleCommand | None, Pos | None] | None:
    """卖矿换钱。**石头先留够修墙的量**，其余（含多余石头）都可以出手。

    两个关键约束（真机实测踩过的坑）：

    * 留储备：不留就会把修墙的石头也卖掉，墙永远建不起来；
    * 成批再走：背着 1 块矿就专程跑一趟小贩，一趟十来回合只换 1 金币 —— 旧实现
      对 ``econ`` 工人无条件调用本函数，吞吐极低。现在不足 ``economy.sell_batch``
      就地继续挖，攒够再走（已在摊前则直接卖）。
    """
    choice = _sellable_ore(worker, stone_keep)
    vendor = view.vendor_pos()
    if choice is None or vendor is None:
        return None
    ore, num = choice
    if chebyshev(worker.pos, vendor) <= 1:
        return RoleCommand(action=Action.SELL, name=ore, num=num), None
    batch = _int_config(config, "economy.sell_batch", SELL_THRESHOLD)
    if num < batch and _ore_count(worker) < batch:
        return None  # 还不够一趟的油钱：继续挖
    return None, vendor


def _cargo(worker: Role) -> int:
    """待卖的铜铁数量（石头不算货物 —— 它是修墙材料，备够就停）。"""
    return sum(1 for item in worker.backpack if item in ("copper", "iron"))


def _sellable_ore(worker: Role, stone_keep: int) -> tuple[str, int] | None:
    """可卖的矿石与数量：先出多余的石头（占地方、又便宜），再按价值 copper > iron。"""
    stone = worker.backpack.count("stone")
    if stone > stone_keep:
        return "stone", stone - stone_keep
    for ore in ("copper", "iron"):
        count = worker.backpack.count(ore)
        if count > 0:
            return ore, count
    return None


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
    """未分配操控位的角色躲进「墙内」安全位（贴着基地、围墙之后的那一侧）。"""
    shelters = shelter_cells(view)
    if shelters:
        for role in mobile:
            if role.id in goals or role.pos in set(shelters):
                continue
            target = min(shelters, key=lambda c: (chebyshev(role.pos, c), c.x, c.y))
            if role.pos != target:
                goals[role.id] = target
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
