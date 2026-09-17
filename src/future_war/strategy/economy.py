"""白天经济（工作包 11/15，方案 M7）：建造 / 采集 / 贩卖 / 升级 + 黄昏就位。

第 1 天的目标只有一个：**夜晚能打**。工人决策按固定优先级执行：

1. **建武器**（金币 ≥25 且武器 <3）：武器是全部的攻防手段，必须第 1 天建满。
   候选格用 ``builder.standable_cells`` 过滤 —— 推断内环里贴着基地的格子常常
   整圈都是障碍（基地本体/出生点），工人永远无法与之相邻，只会原地绕圈。
2. **建围墙**（黄区、背包有石头）：围墙阻挡机器人推进（任务书 §4.7.3「机器人
   攻击阻挡其移动的单位」），为武器争取输出回合。没有石头就先去采石头。
   ``build.wall_first``（默认开）把围墙提为**全队主线**：只要还有墙没修完，
   两个工人都反复「采石 → 砌墙」，不再因为「备够 4 块」就转去挖铜铁换钱
   （线上实测的瓶颈是**砌墙速度**，不是金币）；攒批仍然保留（一次到位连砌），
   但同一时刻只放一名工人离开矿区去墙线，另一名继续采石，杜绝两人同时在路上
   的空窗。

   **墙优先只占白天前段**（``build.wall_first_until_round``，默认 40）：白天第
   ``< 40`` 回合保持上面的全队采石；第 ``>= 40`` 回合退出该模式，回到普通经济
   （按 ``_plan_sell``/``sell_batch`` 采铜/铁并成批卖给小贩，把金币攒起来买券）。
   线上事故 issue #2（2026-09-17）就是全天墙优先：全队一直采石 → 没人采铜铁 →
   卖不出钱 → 连 20 金的围墙券都买不起 → 武器/围墙永远 L1 → 夜里单位成批阵亡。
   退出模式只影响「要不要**主动**采石」（``_needs_stone`` 在窗口外一律返回否，
   不再拿 ``economy.stone_reserve`` 当采石理由 —— 否则工人会被反复从铜矿拉回
   石矿，收入起不来），**顺路砌墙（相邻即建）与破口/缺口优先重建照旧**
   （见 ``_wall_first_active``）；窗口按天重开，次日白天第 1 回合重新生效。

   **白天修/重建被打掉的墙**（``build.wall_breach_first``，默认开）：夜里墙被攻破
   后，白天先把破口补回来 —— 破口是全场唯一**被敌人用行动证明过**能打通的位置，
   机器人夜里会沿同一条路再来。为此 ``EconomyState.wall_memory`` 记住我们砌过的
   墙位，任何「记过但当前已不是墙」且未被证伪/未被占据的格 = 破口，排在候选最前
   （压过正常「由内向外」的环序）；进程重启丢掉记忆时，退化为「墙线上两侧已有 ≥
   ``build.wall_gap_min_walls`` 面墙」的缺口识别。若破口出现在**我们故意不建墙的
   背面**，说明来袭方向判错了 → ``build.wall_breach_rearm_threat`` 允许用破口位置
   覆盖锁定的 ``wall_dir``（只在一种明确矛盾时才改，不会每回合抖动）。残血未毁的
   墙交给 ``consumables`` 的修复包（``consumables.wall_fixer_reserve`` 让它不受
   100 金应急金限制），已毁的用 1 石头重砌。
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
from future_war.core.nav import plan_move, resolve_moves
from future_war.core.world_map import chebyshev, in_bounds
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
    wall_side_tier,
)
from future_war.strategy.consumables import needs_wall_fixer, wall_fixer_target

WEAPON_COST: Final = 25
WALL_COST: Final = 1
SELL_THRESHOLD: Final = 5  # 背着这么多矿石才值得专门跑一趟小贩
STONE_RESERVE: Final = 4  # 手里常备的修墙石头（其余石头可以卖）
DUSK_RETURN: Final = 70  # 70 = 白天不提前就位（交给夜晚回位）
DAY_LENGTH: Final = 130
WALL_MAX: Final = 12
# 与 config 默认值保持一致：config=None 时（测试/模拟器直连）也走同一套参数
WALL_PROBE_BUDGET: Final = 12
WALL_STONE_BATCH: Final = 3  # 背包攒够这么多石头才值得跑一趟墙线（一次到位连砌）
WALL_PROBE_FROM: Final = 0  # 0 = 全天可铺墙（黄区已按配图确定，无需攒额度探路）
WALL_FIRST: Final = True  # 与 config 默认值一致：墙优先（全队持续采石 + 两人都能砌墙）
# 墙优先只占白天**前段**：白天第几回合（0 起，同 ``_wall_window`` 口径）之前是墙优先。
# 线上事故 issue #2：全天墙优先 → 全队一直采石、没人采铜铁 → 卖不出钱 → 20 金围墙券
# 买不起 → 武器/围墙永远 L1 → 夜里成批阵亡。用户策略是「第一夜先活下来」而不是
# 「一整天只砌墙」：前 40 回合全力铺墙，之后转回普通经济赚钱买券。
WALL_FIRST_UNTIL_ROUND: Final = 40
# 与 config 默认值一致（三处必须同值：default.json / config_defaults.py / 这里）
WALL_BREACH_FIRST: Final = True  # 破口（墙被攻破的格）优先重建
WALL_BREACH_REARM_THREAT: Final = True  # 破口落在背面 → 允许推翻锁定的来袭方向
WALL_GAP_MIN_WALLS: Final = 2  # 无记忆时：相邻已建围墙数 ≥ 此值即视为缺口
WALL_CONTINUITY_FIRST: Final = True  # 优先在与「已建成的墙」相邻的格上接着铺
WALL_DONE_WHEN_NO_CELL: Final = True  # 再也找不出候选格 → 围墙工程收工
WALL_HP_RATIO: Final = 0.4  # 与 config 的 consumables.wall_hp_ratio 同值（残血判定）
MAX_WEAPONS: Final = 3
_WEAPON_ORDER: Final = ("rocket", "railgun", "gatling")
# 八邻域：判定「缺口」时数一数这格旁边已经砌了几面墙（与 builder._ADJACENT 同义）
_NEIGHBORS: Final = tuple(
    (dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (dx, dy) != (0, 0)
)
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
    wall_dir: tuple[int, int] | None = None  # 来袭方向（首次判定后锁定，可被背面破口推翻）
    notes: list[str] = field(default_factory=list)  # 本回合决策摘要（每回合重置，供日志）
    build_failures: list[tuple[int, int, str]] = field(default_factory=list)
    wall_refuted_dist: set[int] = field(default_factory=set)  # 整圈都非法的「到基地距离」
    # 我们砌过墙的格（跨回合记忆）。判题器只给「当前快照」，墙被打掉后就再无痕迹，
    # 所以必须自己记：「记过 + 现在不是墙」= 破口，是敌人**用行动证明过**能打通的
    # 位置。机器人夜里走同一条路，优先补这里比按环形顺序铺更值。
    wall_memory: set[Cell] = field(default_factory=set)
    # 「建墙成功过」的格（含当前立着的墙）。真机的可建造区是**连通**的，所以沿
    # 已验证格向邻格铺开的命中率远高于按环盲扫：线上事故里同一圈连续多格
    # ``result_false``、换到另一组相邻格才成功，正是「我们的黄区与真机有偏差」的
    # 表现。候选排序用它做「连续性优先」（``build.wall_continuity_first``）。
    verified_wall_cells: set[Cell] = field(default_factory=set)
    threat_rearms: int = 0  # 因背面破口推翻来袭方向的次数（诊断用）


def plan_economy(
    view: WorldView, config: Config | None = None, state: EconomyState | None = None
) -> dict[int, RoleCommand]:
    """为所有工人产出本回合经济指令（建造/采集/贩卖/升级 + 移动）。"""
    state = state if state is not None else EconomyState()
    state.notes = []
    state.build_failures = []
    _digest_feedback(view, state)
    _rearm_threat_dir(view, state, config)
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
    stalls: dict[int, str] = {}  # 工人 id → 本回合拿不到指令的原因（供 D-02 idle= 诊断）
    for worker in workers:
        cmd, goal = _plan_worker(
            view,
            config,
            worker,
            orders.get(worker.id, "econ"),
            max_weapons,
            state,
            assignment.get(worker.id),
            stalls,
        )
        if cmd is not None:
            commands[worker.id] = cmd
        elif goal is not None:
            goals[worker.id] = goal
    for uid, step in resolve_moves(view, goals).items():
        if step is not None:
            commands[uid] = RoleCommand(action=Action.MOVE, targetPos=(step,))
    # 「有移动目标但解析不出合法步」= 本回合同样一条指令都发不出去（真机表现：
    # 工人永远站着不动），必须记下原因并走保底，不能静默丢弃。
    for uid in goals:
        if uid not in commands:
            stalls[uid] = "no-step"
    _rescue_idle(view, workers, commands, stalls)
    _record_build_attempts(commands, state)
    state.notes.extend(
        _diagnose(view, config, state, workers, orders, max_weapons, commands, stalls)
    )
    return commands


def _rescue_idle(
    view: WorldView,
    workers: list[Role],
    commands: dict[int, RoleCommand],
    stalls: dict[int, str],
) -> None:
    """保底：本回合拿不到任何指令的工人，退化为「走向最近的可达矿 / 可达空地」。

    为什么必须兜住（线上事故根因）：工人整回合零指令 = 判题器只能让它原地不动，
    而没有任何模块会在下一回合修正它 —— 现场就是「两个工人完全不移动」。宁可走
    一步看起来没用的路，也绝不允许静默什么都不做。

    两个例外（``yield-*``）：修复包与升级券必须由 ``plan_consumables`` /
    ``plan_upgrades`` 发出 ``use``，而 planner 是 ``setdefault`` 合并 —— 这里一旦
    给同一角色补一条 move，就把 ``use`` 挤掉了，修复包与券将永远用不出去。
    """
    pending = {
        worker.id: stalls.get(worker.id, "no-cmd")
        for worker in workers
        if worker.id not in commands and not stalls.get(worker.id, "").startswith("yield-")
    }
    for worker in workers:  # 已有指令的工人不是 idle，原因无需再上报
        if worker.id in commands:
            stalls.pop(worker.id, None)
    if not pending:
        return
    fallback: dict[int, Pos] = {}
    for worker in workers:
        if worker.id not in pending:
            continue
        cmd, goal = _idle_fallback(view, worker)
        if cmd is not None:
            commands[worker.id] = cmd
            stalls[worker.id] = "rescued"
        elif goal is not None:
            fallback[worker.id] = goal
    for uid, step in resolve_moves(view, fallback).items():
        if step is not None:
            commands[uid] = RoleCommand(action=Action.MOVE, targetPos=(step,))
            stalls[uid] = "rescued"
    for uid in pending:  # 连一步都走不出去（被彻底围死）→ 如实上报 trapped
        if stalls.get(uid) != "rescued":
            stalls[uid] = "trapped"


def _idle_fallback(
    view: WorldView, worker: Role
) -> tuple[RoleCommand | None, Pos | None]:
    """保底动作：最近的**可达**矿（已在旁边就直接采），没有矿则走向可达空地。

    顺序刻意如此：矿是工人唯一的生产手段，能靠近矿就靠近；实在没有矿（或全图矿都
    被堵死）时也要动起来 —— 走向可建造区里任意可达空地。都被围死才返回
    ``(None, None)``，由 ``_diagnose`` 记成 ``idle=<id>:trapped``。
    """
    mines = sorted(
        (zone.pos for zone in view.mines()),
        key=lambda m: (chebyshev(worker.pos, m), m.x, m.y),
    )
    for mine in mines:
        if chebyshev(worker.pos, mine) <= 1:
            return RoleCommand(action=Action.COLLECT, targetPos=(mine,)), None
        if plan_move(view, worker.id, mine) is not None:
            return None, mine
    cells = standable_cells(view, view.yellow_build_cells() | view.blue_build_cells())
    for cell in sorted(cells, key=lambda c: (chebyshev(worker.pos, c), c.x, c.y)):
        if plan_move(view, worker.id, cell) is not None:
            return None, cell
    step = _free_neighbor(view, worker)
    return (None, step) if step is not None else (None, None)


def _free_neighbor(view: WorldView, worker: Role) -> Pos | None:
    """任意一个可达的相邻空格（四周全被堵死 → ``None``）。"""
    blocked = view.obstacles() - {worker.pos}
    width, height = view.static_map.width, view.static_map.height
    free: list[Pos] = []
    for dx, dy in _NEIGHBORS:
        cell = Pos(worker.pos.x + dx, worker.pos.y + dy)
        if in_bounds(cell, width, height) and cell not in blocked:
            free.append(cell)
    return min(free, key=lambda c: (c.x, c.y)) if free else None


def _diagnose(
    view: WorldView,
    config: Config | None,
    state: EconomyState,
    workers: list[Role],
    orders: dict[int, str],
    max_weapons: int,
    commands: dict[int, RoleCommand],
    stalls: dict[int, str] | None = None,
) -> list[str]:
    """本回合「为什么建/没建」的一句话摘要（真机只能看控制台，必须自解释）。

    每回合最多几条短语，直接进 ``D-02`` 行；不含搜索，只读当前快照 + 状态，
    因此可以放心每回合调用。

    ``idle=`` 是这次事故的直接答案：**谁本回合没有任何指令、为什么**。真机日志里
    一眼就能指认，不必再靠猜（``no-mine`` = 全图没有可用矿、``no-step`` = 有目标但
    解析不出合法步、``trapped`` = 四周被堵死、``yield-*`` = 刻意让路给消耗品/升级券）。
    """
    weapons = len(view.own_weapons())
    walls = len(view.own_walls())
    stone = sum(w.backpack.count("stone") for w in workers)
    notes = [
        f"gold={view.gold()}",
        f"weapons={weapons}/{max_weapons}",
        f"walls={walls}/{_wall_target(view, state, config)}",
        f"stone={stone}",
        f"probes={state.wall_probes}",
        # 存活的可移动单位数（w=工人 / p=开拓者）。直接回答「为什么这回合 orders 是空的、
        # issued=none」：2026-09-17 线上事故里第 2 天前 ~20 帧没有任何指令，看上去像是
        # 规划器全体发呆，实际是**夜里 4 个单位阵亡、复活要等次日白天第 20 回合**（角色
        # 复活规则 §4.5.2），这段窗口内根本没人可调度。看到 `mobile=w0,p0` 即可一眼定性，
        # 不必再靠推测；这里只读快照，不改任何行为。
        f"mobile=w{len(view.own_workers())},p{len(view.own_pioneer())}",
        # 背包构成：直接回答「为什么只采石头不采铜铁」
        "bag=" + ",".join(
            f"{ore}{sum(w.backpack.count(ore) for w in workers)}"
            for ore in ("stone", "copper", "iron")
        ),
        # 推断出的可建造区规模与包围盒：与真机真实区域对比是否偏移
        "yellow=" + _zone_box(view.yellow_build_cells()),
        "blue=" + _zone_box(view.blue_build_cells()),
        # 围墙闸门状态：直接回答「r15~r44 为什么 issued=none」
        "wall_gate=" + _wall_gate(view, config, state),
        # 修 vs 重建：breach=被打掉、要花 1 石头重砌的格；gap=无记忆时靠「两侧有墙」
        # 认出的缺口；repair=残血未毁、该用修复包的墙。线上一眼看出该走哪条路。
        "wall_fix=" + _wall_fix_note(view, state, config),
        "mine=" + _mine_note(view, config, state, workers, orders),
    ]
    if state.wall_refuted_dist:
        notes.append(f"refuted_dist={sorted(state.wall_refuted_dist)}")
    if state.build_failures:
        notes.append(
            "fail="
            + "+".join(f"({x},{y}):{why}" for x, y, why in state.build_failures[:6])
        )
    # 下一个要试的墙格 + 它到基地的距离（对照上面 yellow 的包围盒即可看出是否出区）
    pending = _wall_candidates(view, state, state.failed_build_cells, config)
    if pending:
        base_cells = tuple(view.base_cells())
        cell = pending[0]
        dist = min(chebyshev(cell, b) for b in base_cells) if base_cells else -1
        in_yellow = cell in view.yellow_build_cells()
        notes.append(f"wall_next=({cell.x},{cell.y}) dist={dist} in_yellow={int(in_yellow)}")
    blocked: list[str] = []
    if weapons < max_weapons:
        if view.gold() < WEAPON_COST:
            blocked.append("weapon:gold")
        elif not _weapon_cells_free(view, state):
            blocked.append("weapon:no-cell")
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
    stalled = _stall_note(stalls or {})
    if stalled:
        notes.append(stalled)
    return notes


def _stall_note(stalls: dict[int, str]) -> str:
    """把「谁没有指令、为什么」拼成 ``idle=<id>:<原因>``（无则空串）。

    刻意让路（``yield-*``，等消耗品/升级券落地）与保底成功（``rescued``）分开列：
    前者不是故障，后者是这次修复生效的证据，真机上一眼能区分。
    """
    idle = [f"{uid}:{why}" for uid, why in sorted(stalls.items())
            if not why.startswith(("yield-", "rescued"))]
    yields = [f"{uid}:{why[6:]}" for uid, why in sorted(stalls.items())
              if why.startswith("yield-")]
    rescued = [str(uid) for uid, why in sorted(stalls.items()) if why == "rescued"]
    parts = []
    if idle:
        parts.append("idle=" + ",".join(idle))
    if yields:
        parts.append("yield=" + ",".join(yields))
    if rescued:
        parts.append("rescue=" + ",".join(rescued))
    return " ".join(parts)


def _weapon_cells_free(view: WorldView, state: EconomyState) -> bool:
    """蓝区里是否还剩「工人站得进去且没失败过」的候选格（空 → 不可能再建武器）。"""
    free = frozenset(c for c in view.blue_build_cells() if not _occupied(view, c))
    return bool(_free_cells(standable_cells(view, free), state.failed_build_cells))


def _any_weapon_cell_free(view: WorldView) -> bool:
    """蓝区是否还剩任何可建造格（不看金币，也不用黑名单状态）。

    ``build.day1_max_weapons`` 之外的兜底：蓝区推断出错时（一格都站不进去），
    「等武器建满」等于永远不动手砌墙 —— 线上「墙 0/12」的一半原因就在这里。
    """
    free = frozenset(c for c in view.blue_build_cells() if not _occupied(view, c))
    return bool(standable_cells(view, free))


def _wall_candidates(
    view: WorldView,
    state: EconomyState,
    failed: set[Cell],
    config: Config | None = None,
) -> tuple[Pos, ...]:
    """围墙候选：跳过硬性失败格，也跳过「整圈都非法」的那一环。

    为什么按「环」跳过：真机实测 12 次探路**全部失败**却都落在同一圈上，把当天额度
    烧光也没摸到真实黄区。既然同一圈连错 2 次，这一圈大概率整圈不是黄区，应该立刻
    往外推进，而不是把剩余额度都花在它身上。

    优先级（``build.wall_breach_first``，默认开）：

    1. **破口**（记忆里砌过、现在不是墙）—— 敌人已经证明这条路走得通，先堵它；
    2. **缺口**（墙线上两侧已有 ≥ 2 面墙的空格）—— 进程重启丢了记忆时的兜底；
    3. 其余照旧按「方环由内向外 + 面分档」。

    这样即使破口落在背面（``wall_line`` 因 ``tier is None`` 不收录它），也会被排到
    最前面重建 —— 旧实现只按环序铺，背面破口永远轮不到。
    """
    base_cells = tuple(view.base_cells())
    line = [
        c
        for c in wall_line(
            view, None, None, _occupied_and_failed(view, failed), _threat_dir(view, state)
        )
        if not _occupied(view, c)
        and (
            not base_cells
            or min(chebyshev(c, b) for b in base_cells) not in state.wall_refuted_dist
        )
    ]
    ordered = _prioritized_line(view, state, line, config)
    return tuple(ordered)[: _wall_target(view, state, config) + len(failed)]


def _prioritized_line(
    view: WorldView,
    state: EconomyState,
    line: list[Pos],
    config: Config | None,
) -> list[Pos]:
    """把「破口 → 缺口 → 已验证格邻格 → 正常环序」排成一条候选序列（去重，保序）。"""
    if not _flag(config, "build.wall_breach_first", WALL_BREACH_FIRST):
        return list(line)
    ordered: list[Pos] = []
    seen: set[Pos] = set()
    for cell in (
        *_breach_cells(view, state),
        *_gap_cells(view, state, line, config),
        *_continuity_cells(state, line, config),
        *line,
    ):
        if cell in seen:
            continue
        seen.add(cell)
        ordered.append(cell)
    return ordered


def _continuity_cells(
    state: EconomyState, line: list[Pos], config: Config | None
) -> tuple[Pos, ...]:
    """与「已验证格」相邻（切比雪夫 1）的候选格，按墙线原序返回。

    为什么值这个优先级（线上事故根因）：我们推断的黄区与真机仍有偏差，按方环扫会
    在整圈非法格上连续撞 ``result_false``；而真机的可建造区是**连通**的，只要有一
    格建成，它的邻格几乎必然也是合法建造区。于是「沿已验证格铺开」的命中率远高于
    「按环盲扫」，能在不牺牲正确性的前提下显著减少失败建造（每次失败都白跑一趟）。
    ``line`` 本身已按「由内向外 + 面分档」排好，这里只做稳定筛选，不重排。
    """
    if not _flag(config, "build.wall_continuity_first", WALL_CONTINUITY_FIRST):
        return ()
    verified = frozenset(state.verified_wall_cells)
    if not verified:
        return ()
    return tuple(
        cell
        for cell in line
        if any(
            (cell.x + dx, cell.y + dy) in verified for dx, dy in _NEIGHBORS
        )
    )


def _base_dist(view: WorldView, pos: Pos) -> int:
    """到己方基地块的切比雪夫距离（无基地 → 0）。"""
    base_cells = tuple(view.base_cells())
    if not base_cells:
        return 0
    return min(chebyshev(pos, cell) for cell in base_cells)


def _breach_cells(view: WorldView, state: EconomyState) -> tuple[Pos, ...]:
    """破口 = 我们砌过、现在已不是墙、且仍可施工的格（按到基地距离升序）。

    三个排除各有理由：**已被证伪**（建过又失败，说明推断黄区变了）不再浪费石头；
    **已被占据**（原地又插了武器/站着敌人）不能重砌；**不在黄区**则根本建不了。
    剩下的就是「被打掉的墙」，重建成本 1 石头、收益是挡住机器人一夜的推进。
    """
    live = {wall.pos for wall in view.own_walls()}
    yellow = view.yellow_build_cells()
    holes = []
    for cell in sorted(state.wall_memory):
        pos = Pos(*cell)
        if pos in live or cell in state.failed_build_cells:
            continue
        if pos not in yellow or _occupied(view, pos):
            continue
        holes.append(pos)
    return tuple(sorted(holes, key=lambda c: (_base_dist(view, c), c.x, c.y)))


def _gap_cells(
    view: WorldView,
    state: EconomyState,
    line: list[Pos],
    config: Config | None,
) -> tuple[Pos, ...]:
    """无记忆时的兜底缺口：墙线上「两侧已有 ≥ N 面墙」的空格。

    为什么要有这条：``wall_memory`` 只活在进程内，重启/换进程后记忆为空，破口就退化
    成一个普通的环序候选。但破口在几何上有明显特征 —— 左右（八邻域）至少两面墙已
    经立着，中间空一格。用它就能在零记忆下认出「这里本该有墙」。

    **计数口径**（线上 ``wall_fix=gap(N)`` 从不归零的原因）：只统计「真的还能建」的格
    —— 已被己方/敌方单位占据、已进失败黑名单（判题器回过 false 的非法格）、或落在
    ``wall_refuted_dist`` 那一环的格，都**永远建不上**，却会被判题器的快照一直显示为
    「两侧有墙的空格」。旧写法把它们一律计入 → ``gap(N)`` 永久 > 0，日志读起来像
    「还有 N 个缺口没补」，实际一个都补不了（纯统计口径问题，不涉及建造决策）。
    这里复用 :func:`_occupied_and_failed` 与 ``_wall_candidates`` 同一套过滤，
    保证「缺口的数字」与「候选格里还能建的格」口径一致。

    排序用途不受影响：``_prioritized_line`` 传进来的 ``line`` 本就已按同一套规则过滤
    过，这些判断只是幂等的重复。
    """
    minimum = max(2, _int_config(config, "build.wall_gap_min_walls", WALL_GAP_MIN_WALLS))
    live = {wall.pos for wall in view.own_walls()}
    blocked = _occupied_and_failed(view, state.failed_build_cells)
    base_cells = tuple(view.base_cells())
    gaps = []
    for cell in line:
        if cell in blocked or _occupied(view, cell):
            continue  # 有单位占着 / 已证伪：建不上，不该算作「还没补的缺口」
        if base_cells and min(chebyshev(cell, b) for b in base_cells) in state.wall_refuted_dist:
            continue  # 整圈已被证伪，搜索本来就会跳过它
        built = sum(1 for dx, dy in _NEIGHBORS if Pos(cell.x + dx, cell.y + dy) in live)
        if built >= minimum:
            gaps.append(cell)
    return tuple(gaps)


def _damaged_walls(view: WorldView, config: Config | None) -> int:
    """残血（未毁）围墙数：这些该用修复包（10 金回满），而不是花石头重砌。

    阈值取 ``consumables.wall_hp_ratio``（与消耗品模块同源）—— 两边必须用同一个判定，
    否则会出现「日志说要修、消耗品却不修」的错觉。
    """
    ratio = _float_config(config, "consumables.wall_hp_ratio", WALL_HP_RATIO)
    return sum(1 for wall in view.own_walls() if wall.health <= 1000 * ratio)


def _wall_fix_note(view: WorldView, state: EconomyState, config: Config | None) -> str:
    """D-02 的 ``wall_fix=breach(N)|gap(G)|repair(M)`` 字段（见 ``_diagnose``）。

    ``gap`` 只统计**还能补的**缺口，两条口径修正（都只影响这行诊断，不影响建造）：

    1. 建不上的格不计入（:func:`_gap_cells` 里按候选格同一套规则过滤）—— 被单位
       占着的格、已进失败黑名单的非法格、整圈被证伪的格，判题器快照永远显示成
       「两侧有墙的空格」，旧写法把它们一直算作缺口；
    2. **围墙已收工就归零** —— 建满 ``build.wall_max``（线上是 12/12）后，墙线上
       剩下的可建格再也不会被砌，却仍满足「两侧 ≥2 面墙」而被永久计入。这就是线上
       ``wall_fix=gap(1)`` 在墙明明砌完之后还挂着的直接原因。
    """
    line = list(wall_line(view, None, None, None, _threat_dir(view, state)))
    gaps = () if _wall_done(view, state, config) else _gap_cells(view, state, line, config)
    return (
        f"breach({len(_breach_cells(view, state))})"
        f"|gap({len(gaps)})"
        f"|repair({_damaged_walls(view, config)})"
    )


def _zone_box(cells: frozenset[Pos]) -> str:
    """可建造区的规模与包围盒：`n个 x[lo-hi] y[lo-hi]`（无则 `none`）。

    真机上把这条与真实区域一比，就知道推断是否整体偏移（而不是一格格猜）。
    """
    if not cells:
        return "none"
    xs = [c.x for c in cells]
    ys = [c.y for c in cells]
    return f"{len(cells)}个 x[{min(xs)}-{max(xs)}] y[{min(ys)}-{max(ys)}]"


def _wall_gate(view: WorldView, config: Config | None, state: EconomyState) -> str:
    """围墙当前卡在哪一步：直接回答「为什么这回合 issued=none」。

    前缀带上墙优先开关与全队囤石目标，线上一眼能看出「速度慢」时是模式没生效、
    还是石头没囤够（``keep``）。``first=on/off`` 是**时段判定**的结果：过了
    ``build.wall_first_until_round``（默认 40）或墙已收工都是 ``off`` —— 这正是
    「金币为什么开始流动」的第一个可观测证据。

    两种收工要分开显示：``done`` = 建满了分母；``done:no_cell`` = 分母没建满但推断
    黄区里再也找不出候选格（``build.wall_done_when_no_cell`` 生效）。混成一个
    ``done`` 会让人误以为墙砌够了。
    """
    mode = "on" if _wall_first_active(view, state, config) else "off"
    limit = _int_config(config, "build.wall_first_until_round", WALL_FIRST_UNTIL_ROUND)
    prefix = f"first={mode},until={limit},keep={_stone_reserve(view, state, config)}:"
    if not _flag(config, "build.wall_enabled", True):
        return prefix + "disabled"
    if len(view.own_walls()) >= _wall_target(view, state, config):
        return prefix + "done"
    if _wall_done(view, state, config):
        return prefix + "done:no_cell"
    if not state.wall_confirmed:
        if not _wall_ready(view, state, config):
            budget = _int_config(config, "build.wall_probe_budget", WALL_PROBE_BUDGET)
            return prefix + f"probe_denied({state.wall_probes}/{budget})"
        if not _wall_window(view, config):
            start = _int_config(config, "build.wall_probe_from", WALL_PROBE_FROM)
            now = _day_round(view)
            return prefix + f"wait_window(r{now}<r{start})"
    if not _wall_line_free(view, state, config):
        return prefix + "line_empty"
    return prefix + "ok"


def _mine_note(
    view: WorldView,
    config: Config | None,
    state: EconomyState,
    workers: list[Role],
    orders: dict[int, str],
) -> str:
    """工人**实际**要去的矿种与坐标：回答「为什么只采石头不采铜铁」。

    必须复用真实判定（``_needs_stone`` + 各自职责），否则日志会骗人 ——
    我第一版这里硬编码 ``need_stone=True``，于是永远显示石矿。
    """
    bits = []
    for worker in workers:
        order = orders.get(worker.id, "econ")
        need_stone = _needs_stone(view, state, config, order, worker)
        mine = _preferred_mine(view, worker, order, None, need_stone)
        kind = view.mine_at(mine) if mine is not None else None
        bits.append(
            f"{worker.id}:{kind or '-'}@{mine.x},{mine.y}"
            f"(need_stone={int(need_stone)})"
            if mine is not None
            else f"{worker.id}:-"
        )
    return ",".join(bits) or "-"


def _wall_line_free(view: WorldView, state: EconomyState, config: Config | None = None) -> bool:
    """围墙防线里是否还剩可施工的格（空 → 不可能再建墙）。

    这里直接问「候选序列是否为空」而不是重新过滤一遍 ``wall_line``：破口可能落在
    背面（不在 ``wall_line`` 里），旧写法会误报 ``line_empty``，让线上日志看起来
    「没活可干」，实际还有一整面墙要补。
    """
    return bool(_wall_candidates(view, state, state.failed_build_cells, config))


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

    墙优先模式（``build.wall_first``）下这条规矩只在**武器没插满之前**成立：
    武器约 3~5 回合就能插满、且只有 3 座（用户策略），插满后两个工人全部并入
    「采石 → 砌墙」管道（见 ``_plan_worker``）—— 围墙上限 12 堵、每堵 1 石头，
    多一个人手就是多一倍砌墙速度。这条管道只在白天前段开着
    （``build.wall_first_until_round``）：过了时间点或墙已收工，``_stone_pipeline``
    即关闭，分工自动退回「建造者 + 经济」的普通形态。
    """
    if not workers:
        return {}
    orders: dict[int, str] = {w.id: "econ" for w in workers}
    builder = state.builder_id
    if builder is None:
        cells = standable_cells(view, view.blue_build_cells())
        builder = _nearest_worker(workers, cells).id if cells else workers[0].id
        state.builder_id = builder
    if (
        _weapons_done(view, config, max_weapons)
        and _stone_pipeline(view, state, config, "build", workers[0])
        and not needs_wall_fixer(view, config)
    ):
        # 武器已插满 + 墙管道可用：两人都是「建造者」，谁都可能被派去砌墙。
        # 注意必须同时看管道是否真的开着（有石矿、还有墙要修），否则无矿场景下
        # 全队会一起发呆、连券都不去买（回归 test_buys_*_voucher）。
        # 「有残血墙且队里没修复包」时**不**全队并入墙线：必须留一个人去商店买包
        # （修复包 10 金，到店即买），否则白天修墙这条线永远缺工具。
        return {w.id: "build" for w in workers}
    orders[builder] = "build"
    shop = _shopper(view, workers, orders[builder], max_weapons, state, config)
    if shop is not None:
        orders[shop.id] = "shop"
    return orders


def _weapons_done(view: WorldView, config: Config | None, max_weapons: int) -> bool:
    """武器阶段是否结束：3 座插满，或蓝区已无格可建（推断错误时不该永远等武器）。

    用户策略是「先把 3 座武器插满 → 之后两个工人全部进墙管道」，因此这是
    「谁去砌墙」的总闸门。**金币不足不算结束**：那时更该去挖石头把墙砌起来
    （用户取舍：第一夜靠墙活下来），而不是全队干等金币。
    """
    if len(view.own_weapons()) >= max_weapons:
        return True
    return not _any_weapon_cell_free(view)


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
    if _wall_done(view, state, config):
        return False
    if state.wall_confirmed:
        return True
    return state.wall_probes < _int_config(config, "build.wall_probe_budget", WALL_PROBE_BUDGET)


def _day_round(view: WorldView) -> int:
    """白天第几回合（0 起）：与 ``_wall_window`` **同一口径**（``round_no`` 按天取模）。

    一天 130 回合（白天 70 + 夜晚 60，任务书 §4.2），所以 ``(round_no - 1) % 130``
    在白天是 0..69、夜里是 70..129；两个时间闸门必须用同一个换算，否则「第 40 回合」
    在日志和判定里会各说各话。
    """
    return (view.round_no - 1) % DAY_LENGTH


def _wall_first_active(view: WorldView, state: EconomyState, config: Config | None) -> bool:
    """当前是否处于「墙优先」时段（``build.wall_first`` + 时间窗 + 未收工）。

    这是墙优先的**唯一总闸门**，``_needs_stone`` / ``_stone_pipeline`` /
    ``_stone_reserve`` 三处共用，保证「要不要优先采石」只有一套口径。

    三种返回 False 的情形：
    1. 开关关掉（``build.wall_first=false``，运维回退旧行为）；
    2. **围墙已收工**（沿用 ``_wall_done``：建满分母，或推断黄区再无候选格）——
       与回合无关，砌完了当然立刻停止采石；
    3. 白天已过 ``build.wall_first_until_round``（默认 40）。线上事故 issue #2 的根因
       就是全天墙优先：全队一直采石 → 没人挖铜铁 → 卖不出钱 → 20 金围墙券买不起 →
       武器/围墙永远 L1。前段铺墙、后段转收入，金币才会流动起来。
    """
    if not _flag(config, "build.wall_first", WALL_FIRST):
        return False
    if _wall_done(view, state, config):
        return False
    if not view.is_day():
        return False
    limit = _int_config(config, "build.wall_first_until_round", WALL_FIRST_UNTIL_ROUND)
    return _day_round(view) < limit


def _stone_quota(view: WorldView, state: EconomyState, config: Config | None) -> int:
    """墙优先模式下**全队**要持续囤到的石头量：``wall_stone_batch × 工人数``。

    目标量按人头算：两个工人各要能带满一批（各 3 块 → 全队 6 块）才同时开工；
    光备 ``economy.stone_reserve``（4 块）只够一个人走一趟，另一个就断料了。
    同时**不低于** ``economy.stone_reserve``（运维旋钮仍然生效，留出的石头只多不少），
    上限是「剩余墙数」（一堵墙 1 块石头），别囤超过剩下的工程量。
    """
    batch = _int_config(config, "build.wall_stone_batch", WALL_STONE_BATCH)
    hands = max(1, len(view.own_workers()))
    reserve = max(0, _int_config(config, "economy.stone_reserve", STONE_RESERVE))
    remaining = _wall_remaining(view, state, config)
    return min(remaining, max(batch * hands, reserve))


def _stone_reserve(view: WorldView, state: EconomyState, config: Config | None) -> int:
    """手里要留住的石头数（其余可以卖）：够修完剩下的墙，且不超过 ``economy.stone_reserve``。

    留一点就够：真正的瓶颈是「不知道黄区在哪」，不是石头不够；囤 12 块只会把
    白天的产出全锁在背包里（用户真机实测：金币 0、白天无产出）。

    墙优先模式（``build.wall_first``）下换成 ``_stone_quota``：全队每个工人都要
    能各带一批，否则总有一人在等料（线上实测「墙建得太慢」的直接原因）。

    墙优先**按时段生效**（``_wall_first_active``）：白天第
    ``build.wall_first_until_round`` 回合起退出该模式，留石量随之回到
    ``economy.stone_reserve`` 口径。窗口外的这个 ``keep`` **只决定「顺路砌墙时
    手里留几块」**，不再意味着「不够就去采」—— 采石的总闸门在 ``_needs_stone``，
    它在窗口外一律返回否（否则工人会被从铜矿反复拉回石矿，金币起不来）。
    """
    if not _flag(config, "build.wall_enabled", True):
        return 0  # 不修墙就不必留石头，全部可以换钱
    if _wall_first_active(view, state, config):
        return _stone_quota(view, state, config)
    remaining = _wall_remaining(view, state, config)
    cap = _int_config(config, "economy.stone_reserve", STONE_RESERVE)
    return min(remaining, max(0, cap))


def _wall_batch_held(worker: Role, config: Config | None) -> bool:
    """这名工人是否已攒够一整批石头（够 → 值得专程跑一趟墙线连砌）。"""
    batch = _int_config(config, "build.wall_stone_batch", WALL_STONE_BATCH)
    return worker.backpack.count("stone") >= max(1, batch)


def _stone_pipeline(
    view: WorldView, state: EconomyState, config: Config | None, order: str, worker: Role
) -> bool:
    """这名工人本回合是否走「采石 → 砌墙」管道（墙优先模式的核心闸门）。

    条件（缺一不可）：墙优先模式开、这名工人有资格施工（``order``）、
    场上还有墙要修、武器阶段已结束（先把 3 座武器插满，用户策略）、
    而且**场上真有矿可采**（没矿时不能为了囤石头放弃贩卖，否则金币与石头双输）。

    墙优先按时段生效（``_wall_first_active``）：白天第
    ``build.wall_first_until_round`` 回合起这条管道关闭，全队转回普通经济
    （采铜/铁 → 成批卖给小贩 → 攒钱买券），破口与顺路砌墙不受影响。
    """
    if not _wall_first_active(view, state, config):
        return False
    if order not in ("build", "econ", "shop"):
        return False
    if not _flag(config, "build.wall_enabled", True):
        return False
    if _wall_done(view, state, config):
        return False
    if not view.mines("stone"):
        return False  # 没石矿 → 这条管道走不通，交回原有优先级
    if not _weapons_done(
        view, config, _int_config(config, "build.day1_max_weapons", MAX_WEAPONS)
    ):
        return False
    # 今天已把探路额度烧光（还不确定黄区在哪）→ 别再无限采石，回去做能变现的
    # 铜/铁（墙优先不等于「把整支队伍耗在一条还没验证过的施工线上」）。
    return _wall_ready(view, state, config)


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
    builder: str,
    max_weapons: int,
    state: EconomyState,
    config: Config | None = None,
) -> Role | None:
    """武器建满后，指派离武器商店最近的**非建造者**去采购升级券。

    建造者不参与采购：否则它一去一回，武器/围墙的施工就停摆，另一名工人又没
    被授权铺墙（旧实现两名工人偶尔会抢同一格围墙）。

    墙优先模式（``build.wall_first``）下放宽武器闸门：只要场上已有可升级的
    **围墙**、且同队还有另一名工人在推进墙线，就允许武器未满时派一人去买
    20 金的围墙券（用户取舍：**墙 > 武器升级**）。不抢全队人手是硬条件 ——
    两个工人同时走掉，墙与石头就都停了；所以只在「除采购者外还有别人在墙线上」
    时才放行，否则宁可先砌墙、回头再买券。

    另一条放行理由：**墙被打残、队里又没有修复包**（``consumables.needs_wall_fixer``）。
    修复包只要 10 金，是全场最便宜的止损；但消耗品模块只会在「人已经站在商店旁」
    时买，而旧实现里没人会为它专程跑一趟 —— 于是残血墙永远等不到修复包（线上金币
    常年在 10~20，20 金的围墙券也买不起，商店这条线整局没人去）。
    """
    if len(view.own_weapons()) < max_weapons and not _flag(
        config, "build.wall_first", WALL_FIRST
    ):
        return None
    voucher = affordable_voucher(view, view.gold(), config)
    need_fixer = needs_wall_fixer(view, config)
    if voucher is None and not need_fixer:
        return None  # 既没有「买得起又用得到」的券，也没有残血墙要修 → 别派人去商店
    if voucher is not None and not _wall_upgrade_voucher(voucher):
        # 武器/基地券：本来就要求武器建满，闸门见上
        if len(view.own_weapons()) < max_weapons:
            return None
    # 注意：**不能**用「防线还没铺完」挡住采购。围墙目标 12 堵本来就常修不满，
    # 旧判断等于永久占住采卖工人 → 武器永远停在 level1（真机实测正是如此：
    # 基地在夜里被推平，而金币攒着没处花）。升到 level2/3 直接翻倍夜里的输出，
    # 收益高于再多砌两堵墙。铺墙时段（build.wall_probe_from）之后工人自然会回去铺。
    shop = view.weapon_shop_pos()
    candidates = [w for w in workers if w.id != builder]
    if shop is None or not candidates:
        return None
    # 错峰闸门只在「另一个人真的在砌墙」时才成立：若墙管道本身没开（武器还没插满
    # 且金币不够、或场上没矿），派谁去买券都不会抽走墙线上的人手。
    if (
        candidates
        and _flag(config, "build.wall_first", WALL_FIRST)
        and _stone_pipeline(view, state, config, "build", candidates[0])
        and not _spare_wall_hand(view, state, config, candidates)
    ):
        return None  # 采购会抽走墙线上最后一个人手 → 先砌墙，券下次再买
    return min(candidates, key=lambda w: (chebyshev(w.pos, shop), w.id))


def _wall_upgrade_voucher(voucher: str) -> bool:
    """该券是否用于升级**围墙**（墙优先模式下唯一允许插队采购的种类）。"""
    from future_war.strategy.builder import _VOUCHERS  # 局部导入：避免模块级耦合

    entry = _VOUCHERS.get(voucher)
    return entry is not None and entry[0] == "wall"


def _spare_wall_hand(
    view: WorldView,
    state: EconomyState,
    config: Config | None,
    candidates: list[Role],
) -> bool:
    """除采购者外是否还有人手在推进墙线（有 → 可以抽一个人去买券）。

    判定「在推进」= 该工人已经在候选墙格旁（下一回合就能砌），或手里攒够了
    一整批石头（马上会去墙线）。两人规模下，这条就是「不许两人同时离开工地」。
    """
    batch = _int_config(config, "build.wall_stone_batch", WALL_STONE_BATCH)
    line = frozenset(_wall_candidates(view, state, state.failed_build_cells, config))
    return any(
        w.backpack.count("stone") >= batch
        or any(chebyshev(w.pos, cell) == 1 for cell in line)
        for w in candidates
    )


def _plan_worker(
    view: WorldView,
    config: Config | None,
    worker: Role,
    order: str,
    max_weapons: int,
    state: EconomyState,
    mine: Pos | None,
    stalls: dict[int, str] | None = None,
) -> tuple[RoleCommand | None, Pos | None]:
    """按职责产出（指令 | 移动目标）。

    优先级：建造武器 → 用券 → **顺路砌墙（零行程）** → 采购 → 贩卖 →
    墙优先模式下「攒够一批 + 错峰」就专程跑墙线 → 专门跑墙位（旧闸门）→ 采集。

    墙优先模式（``build.wall_first``，用户策略「第一夜先活下去、全力砌墙」）：
    武器插满后**两个工人**都走这条管道 —— 攒批仍然保留（一次到位连砌，避免
    「采一块砌一块」的来回空转），但同一时刻最多放一个人离开矿区去墙线：
    谁先攒够一批谁去砌，另一个接着采石。线上实测的瓶颈是**砌墙速度**，不是金币，
    所以在**白天前段**（``build.wall_first_until_round``，默认第 40 回合之前）
    石头永不「备够就变现」。过了这个时间点或墙已收工，``_stone_pipeline`` 关闭，
    回到「采铜/铁 → 成批贩卖 → 买券」的普通经济（金币锁死的根因，见 issue #2）。

    ``(None, None)`` = 本回合既没有指令也没有移动目标，**只可能**来自这几条分支
    （``stalls`` 会被写成原因，plan_economy 据此做保底与 D-02 诊断）：

    1. ``yield-fixer``：手上有修复包且身旁有残血墙 → 让路给 ``plan_consumables``
       的 ``use WallFixer``（经济指令会盖掉它，必须主动让路）；
    2. ``yield-voucher``：手上有券且目标建筑就在旁边 → 让路给 ``plan_upgrades``；
    3. ``no-mine``：走到最后一步仍拿不到矿 —— ``_preferred_mine`` 返回 ``None``
       （全图没有矿，或 ``_assign_mines`` 没给出兜底矿）；
    4. （在 plan_economy 里补记）``no-step``：有移动目标但 ``resolve_moves``
       解析不出合法步（目标不可达且已到最近点 / 被队友占了唯一落脚点）。

    1、2 是**刻意**让路；3、4 是真正的死路，plan_economy 会用 ``_rescue_idle``
    兜住 —— 真机上「两个工人完全不移动」就是 3/4 静默返回的结果。
    """
    if order == "build":
        plan = _plan_build_weapon(view, config, worker, max_weapons, state.failed_build_cells)
        if plan is not None:
            return plan
        # 武器建满 / 金币不足 / 无可用格 → 建造者转去铺墙，别闲着
    if _fixer_repair_first(view, config, worker):
        # 手里有修复包且身旁就是残血围墙：本回合什么都不发，让 plan_consumables 的
        # `use WallFixer` 落地。经济指令永远先占住角色（planner 用 setdefault 合并），
        # 不主动让路的话修复包会一直躺在背包里 —— 与升级券「use 被吞」是同一个坑。
        return _yield(stalls, worker, "yield-fixer")
    action, target = voucher_trip(view, worker, config)
    if action == "use":
        # 本回合什么都不发，让 plan_upgrades 的 use 指令落地（否则被经济指令盖掉）
        return _yield(stalls, worker, "yield-voucher")
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
    if _stone_pipeline(view, state, config, order, worker):
        # 谁攒够一批谁就去墙线；若同队已经有人在墙线上推进，本轮留在矿里接着采，
        # 保证「任意时刻至少有一个工人在推进墙」而不是两人同时在路上。
        if _wall_batch_held(worker, config) and not _line_already_held(
            view, state, config, worker
        ):
            plan = _plan_build_wall(view, config, worker, state, adjacent_only=False)
            if plan is not None:
                return plan
        return _collect_or_stall(
            stalls,
            worker,
            _plan_collect(
                worker, _preferred_mine(view, worker, order, mine, need_stone=True)
            ),
        )
    # 专门跑墙位：已确认过合法墙位（值得专程铺线），或已进入「铺墙时段」
    # （``build.wall_probe_from``，默认白天第 45 回合起）。上午留给经济：挖矿→贩卖→
    # 买券，下午石头也攒下了，再专心试推断出来的黄区。
    # 只在「手上没有铜铁（正在攒的那批货已脱手）」时才专程跑墙位：否则挖一格就
    # 被墙位拉走，永远攒不满一趟的货量，金币也就永远上不去（真机实测金币卡死在 45）。
    if (
        wall_ok
        and _cargo(worker) == 0
        and _wall_batch_held(worker, config)  # 攒够一批再去，别采一块跑一趟
        and (state.wall_confirmed or _wall_window(view, config))
    ):
        plan = _plan_build_wall(view, config, worker, state, adjacent_only=False)
        if plan is not None:
            return plan
    need_stone = _needs_stone(view, state, config, order, worker)
    return _collect_or_stall(
        stalls,
        worker,
        _plan_collect(worker, _preferred_mine(view, worker, order, mine, need_stone)),
    )


def _yield(
    stalls: dict[int, str] | None, worker: Role, reason: str
) -> tuple[None, None]:
    """记录「刻意让路」并返回 ``(None, None)``（另一模块会替这名工人发指令）。"""
    if stalls is not None:
        stalls[worker.id] = reason
    return None, None


def _collect_or_stall(
    stalls: dict[int, str] | None,
    worker: Role,
    plan: tuple[RoleCommand | None, Pos | None],
) -> tuple[RoleCommand | None, Pos | None]:
    """采集结果为空（没有可用矿）时记下 ``no-mine``，交给保底分支处理。"""
    if plan == (None, None) and stalls is not None:
        stalls[worker.id] = "no-mine"
    return plan


def _fixer_repair_first(view: WorldView, config: Config | None, worker: Role) -> bool:
    """这名工人本回合是否该让路给「用修复包修墙」（见 ``_plan_worker`` 调用处）。"""
    if "WallFixer" not in worker.backpack:
        return False
    return wall_fixer_target(view, worker, config) is not None


def _line_already_held(
    view: WorldView, state: EconomyState, config: Config | None, worker: Role
) -> bool:
    """除 ``worker`` 外是否已有人在墙线上推进（在 → 本人先别走，继续采石）。

    错峰闸门：两名工人同时背上石头往墙线跑，矿区就空了，回来时同样一起空手 ——
    砌墙速度反而下降。让「已经在墙格旁」或「已攒够一批且更近墙线」的同伴先去，
    另一个守住采石产能。只要没人推进，本人立刻动身（不会互相等到天荒地老）。
    """
    batch = _int_config(config, "build.wall_stone_batch", WALL_STONE_BATCH)
    pending = [
        w
        for w in view.own_workers()
        if w.id != worker.id and w.backpack.count("stone") >= max(1, batch)
    ]
    if not pending:
        return False
    line = frozenset(_wall_candidates(view, state, state.failed_build_cells, config))
    if not line:
        return False

    def distance(w: Role) -> int:
        return min(chebyshev(w.pos, cell) for cell in line)

    # 同伴比我更接近墙线（或同距但 id 更小）→ 让它先把这批砌完，我接着采。
    # 同距用 id 破平局：否则两人互相「让路」，谁也不去砌墙。
    return min((distance(w), w.id) for w in pending) < (distance(worker), worker.id)


def _needs_stone(
    view: WorldView, state: EconomyState, config: Config | None, order: str, worker: Role
) -> bool:
    """「石头不够、值得专门去采石」是否成立（False = 本回合按铜 > 铁 > 石挑矿）。

    **只有 ``_wall_first_active`` 为真（墙优先窗口内）才允许拿「石头未达储备」当
    理由去采石**。窗口外（白天第 >= ``build.wall_first_until_round`` 回合）一律
    返回 False，直接落到 ``_preferred_mine`` 的 copper > iron 优先级，去挖能变现
    的矿、按 ``_plan_sell``/``economy.sell_batch`` 成批卖给小贩。

    为什么窗口外必须一刀切：只把 ``_stone_reserve`` 调小是不够的 —— 全队石头只要
    低于 ``economy.stone_reserve``（默认 4），``_needs_stone`` 就又把工人从铜矿拉
    回石矿，采到的石头又几乎全被留下修墙、卖不出去，于是「下午归收入」名存实亡
    （模拟器 seed 1 实测：D1 末 gold 1、全队石头长期在储备线以下）。退出窗口 = 停止
    **主动**采石，但**顺路砌墙**（``_plan_build_wall(adjacent_only=True)``）与
    **破口/缺口优先重建**都在本函数之外，照旧生效。

    窗口外不会真的「一块石头都没有」：``_preferred_mine`` 的兜底优先级仍含 stone，
    铜矿/铁矿都不可用时照采不误；墙上已有的石头也仍能顺路砌掉。次日白天第 1 回合
    ``_day_round`` 归零、``_wall_first_active`` 重新为真，墙优先窗口按天重开。

    ``build.wall_first=false``（运维回退开关）保留旧口径：石头未达
    ``economy.stone_reserve`` 就继续采石，一行旧行为都不动。
    """
    if not _flag(config, "build.wall_enabled", True):
        return False
    if order not in ("build", "econ", "shop"):
        return False
    if not _flag(config, "build.wall_first", WALL_FIRST):
        # 回退路径：建造者手上还有武器要建就先专心攒金币，别去挖石头。
        if order == "build" and len(view.own_weapons()) < _int_config(
            config, "build.day1_max_weapons", MAX_WEAPONS
        ):
            return False
        # **按全队存石量**判断，而不是各人手里的量：储备是「全队备够」的概念，
        # 否则 2 个工人各囤 4 块（共 8 块）才罢休 —— 真机实测就是「只采石头、
        # 一整天没有铜铁收入」。全队够了就让所有人转去挖铜/铁换钱。
        total_stone = sum(w.backpack.count("stone") for w in view.own_workers())
        return total_stone < _stone_reserve(view, state, config)
    if not _wall_first_active(view, state, config):
        return False  # 窗口外：不主动采石，去挖铜铁变现（顺路砌墙/补破口不受影响）
    # 墙优先窗口内：武器阶段结束（插满 3 座 / 蓝区无格）后全队持续采石，直到囤够
    # 「每名工人一批」。有人攒满一批却不能动身时（比如同伴正在墙线），其余人
    # 继续采石 —— 始终有料可用。
    max_weapons = _int_config(config, "build.day1_max_weapons", MAX_WEAPONS)
    if not _weapons_done(view, config, max_weapons):
        return False
    total_stone = sum(w.backpack.count("stone") for w in view.own_workers())
    if total_stone < _stone_quota(view, state, config):
        return True
    batch = _int_config(config, "build.wall_stone_batch", WALL_STONE_BATCH)
    return not any(w.backpack.count("stone") >= max(1, batch) for w in view.own_workers())


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
    line = _wall_candidates(view, state, failed, config)
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


def _wall_target(view: WorldView, state: EconomyState, config: Config | None = None) -> int:
    """本局围墙**目标数（分母）**：方向无关、且施工过程中恒定。

    线上事故（2026-09-17）的根因就在这里：旧实现拿
    ``len(wall_line(view, ..., threat_dir))`` 当分母，而 ``wall_line`` 有**两个**
    会让分母缩水的副作用 ——

    1. 它排除**已经建好的围墙格**：每砌一堵墙，分母就少 1，于是出现日志里的
       ``walls=7/6 (done)``（分子 7 > 分母 6 = 分母被施工自己吃掉了）→ 闸门误判
       「完工」→ 直接停工；
    2. 它按来袭方向只围三面：``build.wall_breach_rearm_threat`` 一改 ``wall_dir``
       （背面破口推翻方向），U 形换面，分母在 6/8/12 之间跳（日志里
       ``7/6(done)`` 与 ``walls=5/8`` 同时出现），已建好的墙在新分母下「不算数」。

    现在分母 = 推断黄区**四面**的应建格总数（``threat_dir=None`` → 不按方向裁剪；
    ``include_built=True`` → 不算已建墙），上限 ``build.wall_max``。方向从此只决定
    **建造顺序**（``wall_line`` 的 tier 排序），不决定分母。

    数值上：线上几何的黄区是距离 2 环（2×2 基地 → 20 格），封顶 ``wall_max=12``，
    因此分母稳定为 **12**（旧实现是 6~8 的浮动值）。
    """
    line = wall_line(view, None, None, None, None, include_built=True)
    cap = max(0, _int_config(config, "build.wall_max", WALL_MAX))
    return min(len(line), cap)


def _wall_done(view: WorldView, state: EconomyState, config: Config | None = None) -> bool:
    """围墙工程是否收工：**建满目标数**，或推断黄区里再也找不出候选格。

    为什么需要后半句（``build.wall_done_when_no_cell``，默认开）：分母现在是固定的
    四面总数（12），而我们是按 U 形先砌来袭面的；一旦三面全部砌完、黄区里再无候选
    （``line_empty``），旧写法会一直认为「还没建满」→ ``_stone_pipeline`` 永远开着
    → 全队在 ``build.wall_first`` 下不停采石、永不贩卖，金币被彻底锁死。把「没格可
    砌」也算完工，闸门才会关闭、经济才会回到变现阶段。

    反过来也成立：夜里墙被打掉后该格重新变成候选 → 立刻不算完工 → 破口被优先重建。
    """
    if len(view.own_walls()) >= _wall_target(view, state, config):
        return True
    if not _flag(config, "build.wall_done_when_no_cell", WALL_DONE_WHEN_NO_CELL):
        return False
    return not _wall_candidates(view, state, state.failed_build_cells, config)


def _wall_remaining(view: WorldView, state: EconomyState, config: Config | None) -> int:
    """还剩几堵墙要砌（收工后为 0）。石头配额/储备都按它算，施工一停就放行卖矿。"""
    if _wall_done(view, state, config):
        return 0
    return max(0, _wall_target(view, state, config) - len(view.own_walls()))


def _threat_dir(view: WorldView, state: EconomyState) -> tuple[int, int] | None:
    """来袭方向（八方向之一），**首次判定后锁定**（可被背面破口推翻）。

    用户实测：机器人浪潮从基地的**一侧**刷出，因此围墙只围三面（来袭面 + 两侧），
    背面不建 —— 既省石头，也不会把自己人围死。

    判定顺序：① 场上以我方为目标的机器人质心（真机第一晚后即有观测）；
    ② 敌方基地方向（基地位置全局可见，且通常就是机器人来袭方向）；
    ③ 暂无信息 → None（退化为四面全建）。

    「锁定」的代价：一旦判错，背面永远不建墙（旧实现的死结）。修正入口见
    :func:`_rearm_threat_dir` —— 只有「破口出现在背面」这种明确矛盾才允许改写。
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


def _rearm_threat_dir(
    view: WorldView, state: EconomyState, config: Config | None
) -> None:
    """背面出现破口 → 推翻锁定的来袭方向（``build.wall_breach_rearm_threat``）。

    为什么必须允许改写：锁定的方向若判错，我们**故意不建**的那一面就是敞开的，敌人
    从那里打进来、打掉我们早先建的墙 —— 这正是「破口落在背面」的含义。此时破口本身
    就是最硬的证据（比机器人质心更可信，它证明敌人真的从那一侧来了）。

    为什么不会每回合抖动：只有 ``wall_side_tier(...) is None``（当前方向下的背面）才
    触发；改完方向后这一格就变成正面（tier 0），不会再次触发。同一回合内只改一次。
    """
    if not _flag(config, "build.wall_breach_rearm_threat", WALL_BREACH_REARM_THREAT):
        return
    if state.wall_dir is None:  # 还没锁定过方向 → 无需推翻（本来就四面全建）
        return
    base = base_center(view)
    if base is None or not view.base_cells():
        return
    for pos in _breach_cells(view, state):
        if wall_side_tier(view, pos, state.wall_dir) is not None:
            continue  # 破口在正面/侧面：方向判断没问题
        dx = (pos.x > base.x) - (pos.x < base.x)
        dy = (pos.y > base.y) - (pos.y < base.y)
        if (dx, dy) == (0, 0):
            continue
        state.wall_dir = (dx, dy)
        state.threat_rearms += 1
        state.notes.append(f"breach_rearm=({dx},{dy})@({pos.x},{pos.y})")
        return


# ------------------------------------------------------------------ 采卖 / 升级


def _plan_shopping(
    view: WorldView, worker: Role, config: Config | None = None
) -> tuple[RoleCommand | None, Pos | None] | None:
    """前往武器商店买券（到店即买）：按性价比挑，20 金围墙券优先于 100 金武器券。

    买不起任何券时如果**有残血围墙且队里没修复包**，照样朝商店走 —— 修复包只要
    10 金，是这条采购线上唯一真正买得起的止损手段（旧实现没有券就不去商店，
    于是残血墙永远等不到修复包）。到店后不在这里下单：``consumables`` 会在紧贴
    商店时发 ``buy``，两处各发一次就变成重复购买。
    """
    shop = view.weapon_shop_pos()
    if shop is None:
        return None
    voucher = affordable_voucher(view, view.gold(), config)
    if voucher is None:
        if not needs_wall_fixer(view, config):
            return None
        if chebyshev(worker.pos, shop) <= 1:
            return None  # 已到店：让 plan_consumables 下单买修复包
        return None, shop
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
    """上回合建造目标没变成建筑 → 该格非法，拉黑；同时维护「墙位记忆」。

    判题器对非法建造只回 ``false``，而非法的最常见原因就是**可建造区推断错了**；
    直接看「目标格上现在有没有对应建筑」比读动作结果更可靠（结果位还可能被
    同回合的移动结算覆盖）。不修正推断，工人会在同一个非法格上无限重试。

    墙位记忆为什么两路都记（本回合确认 + 每回合同步活墙）：判题器只给当前快照，
    墙被打掉后快照里没有任何痕迹，必须自己留档。同步活墙是廉价的自我修复 —— 记忆
    意外丢失（重启/异常）时也能在下一次扫描里重新认识现有围墙。

    「已验证格」（``verified_wall_cells``）与墙位记忆同源但用途不同：它回答的是
    「**哪些格被真机证明过能建墙**」，供候选排序做连续性优先（见
    :func:`_continuity_cells`）。活着的围墙必然建成过，所以两路都收。
    """
    live = [(wall.pos.x, wall.pos.y) for wall in view.own_walls()]
    state.wall_memory.update(live)
    state.verified_wall_cells.update(live)
    base_cells = tuple(view.base_cells())
    for uid, (cell, is_wall) in list(state.pending_build.items()):
        pos = Pos(*cell)
        if view.action_ok(uid) is False or not _has_building(view, pos, is_wall):
            state.failed_build_cells.add(cell)
            reason = "result_false" if view.action_ok(uid) is False else "no_building"
            state.build_failures.append((cell[0], cell[1], reason))
            if is_wall:
                state.wall_probes += 1
                # 同距离累计 2 次失败 → 认定这一整圈都非法，把搜索推到外一圈
                dist = min(chebyshev(pos, c) for c in base_cells) if base_cells else 0
                if sum(1 for x, y, _ in state.build_failures
                       if base_cells and min(chebyshev(Pos(x, y), c) for c in base_cells) == dist) >= 2:
                    state.wall_refuted_dist.add(dist)
        elif is_wall:
            state.wall_confirmed = True  # 找到过合法墙位 → 放开施工
            state.wall_memory.add(cell)  # 这块墙是我们砌的：日后不在了就是破口
            state.verified_wall_cells.add(cell)  # 真机证明过这格能建 → 沿其邻格铺开
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


def _float_config(config: Config | None, key: str, default: float) -> float:
    """读浮点旋钮；缺失/类型不对就回退代码兜底常量（与 config 默认值同值）。"""
    value = config.get(key) if config is not None else None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return default
