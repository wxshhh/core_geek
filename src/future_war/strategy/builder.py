"""建造规划（工作包 14，方案 M8）：武器布局、围墙防线、操控位与升级券。

接口不传蓝/黄可建造区坐标，``WorldView`` 按基地几何**推断**（工作包 9）。
推断必然不精确（真实区域形状未知），因此本模块的核心原则是：

1. **近基地优先**：所有候选格按「到基地块的切比雪夫距离」升序排列。推断的
   内环几乎必然是真实可建造区（区域通常紧贴基地），越远越可能是误判。旧的
   「前出优先（离基地越远越靠前）」会让工人在第 1 天反复撞非法格，实测第 1 天
   只能建成 2 座武器、且 3 座武器里 2 座落在推断错误的外环。
2. **一次成功**：武器尽量落在「基地块的 8 邻域」——那里必定与基地相邻，操控者
   可以在基地内就位，夜晚第 1 回合即可开火（武器射程 3/6/10 都远大于 1，
   贴基地建造**不会**损失任何射程覆盖）。
3. **围墙成线**：围墙围成一圈阻挡机器人（任务书 §4.7.3「机器人攻击阻挡其移动
   的单位」），为武器争取输出时间；缺石头时才去采。围墙只建最内环，避免把
   己方角色关死。
4. **操控位预置**：白天指定期（``staging``）把移动角色送到各武器的操控位，
   否则夜晚前几个回合武器无人操控 = 不输出（角色每回合只能走 1 格）。

升级券在本地模拟器中不生效（sim/README 明确 stub），故其逻辑以合成视图单测覆盖。
仅用标准库。
"""

from __future__ import annotations

from itertools import permutations
from typing import Final

from future_war.config import Config
from future_war.models import Action, Pos, Role, RoleCommand, enum_to_str
from future_war.core.world_map import chebyshev
from future_war.core.world_view import WorldView

MAX_LEVEL: Final = 3
CONTROL_RANGE: Final = 1  # 操控者须站在武器周围 1 格（任务书 §4.4）
_ADJACENT: Final = tuple(
    (dx, dy)
    for dx in (-1, 0, 1)
    for dy in (-1, 0, 1)
    if (dx, dy) != (0, 0)
)
_WALL_MAX_RADIUS: Final = 6  # 围墙候选方环最大半径
_WEAPON_KINDS: Final = frozenset({"gatling", "railgun", "rocket"})
_MOBILE_KINDS: Final = frozenset({"pioneer", "worker"})

# 升级券单价（任务书 §4.6.3 价目表）——采购要按性价比排序，不能只盯着最贵的
_VOUCHER_PRICE: Final = {
    "WallUpgradeVoucher1": 20,
    "WallUpgradeVoucher2": 30,
    "WeaponUpgradeVoucher1": 100,
    "WeaponUpgradeVoucher2": 150,
    "StationUpgradeVoucher1": 100,
    "StationUpgradeVoucher2": 150,
}

# 同价时的取舍（与 build.upgrade_order 的意图一致：武器 > 基地 > 围墙）
_VOUCHER_CATEGORY_RANK: Final = {"weapon": 0, "station": 1, "wall": 2}

# 升级券 → (目标类别, 起始等级, 目标等级)
_VOUCHERS: Final = {
    "WeaponUpgradeVoucher1": ("weapon", 1, 2),
    "WeaponUpgradeVoucher2": ("weapon", 2, 3),
    "StationUpgradeVoucher1": ("station", 1, 2),
    "StationUpgradeVoucher2": ("station", 2, 3),
    "WallUpgradeVoucher1": ("wall", 1, 2),
    "WallUpgradeVoucher2": ("wall", 2, 3),
}


# ------------------------------------------------------------------ 候选格排序


def _base_cells_of(view: WorldView) -> tuple[Pos, ...]:
    """己方基地 2x2 占用格（接口 §1.3.1 注：pos 为左上角）。"""
    return tuple(view.base_cells())


def _base_distance(pos: Pos, base_cells: tuple[Pos, ...]) -> int:
    """到基地块的切比雪夫距离（0 = 压在基地格上）。"""
    if not base_cells:
        return 0
    return min(chebyshev(pos, cell) for cell in base_cells)


def standable_cells(view: WorldView, cells: frozenset[Pos]) -> frozenset[Pos]:
    """候选格中「工人能站得进去」的那些：至少有一个相邻格可站立。

    推断内环里靠近基地的格子常常整圈都是障碍（基地本体、角色出生点），
    工人根本无法与之相邻，于是永远建不了、只会绕圈。过滤掉它们即可让
    建造者直奔真正可施工的位置。
    """
    blocked = view.obstacles()
    standable: set[Pos] = set()
    for cell in cells:
        for dx, dy in _ADJACENT:
            nxt = Pos(cell.x + dx, cell.y + dy)
            if view.in_bounds(nxt) and nxt not in blocked:
                standable.add(cell)
                break
    return frozenset(standable)


def base_center(view: WorldView) -> Pos | None:
    """基地 2x2 的中心格（无基地时退回左上角）。"""
    base = view.base_pos()
    if base is None:
        return None
    return Pos(base.x, base.y)


def _direction(base: Pos | None, other: Pos | None) -> tuple[int, int] | None:
    """base → other 的八方向符号向量（零向量返回 None）。"""
    if base is None or other is None:
        return None
    dx = (other.x > base.x) - (other.x < base.x)
    dy = (other.y > base.y) - (other.y < base.y)
    return (dx, dy) if (dx, dy) != (0, 0) else None


def preferred_weapon_cells(view: WorldView, config: Config | None = None) -> tuple[Pos, ...]:
    """蓝色可建造格排序：先「基地 8 邻域」、再按到基地的距离升序，同距稳定。

    近基地优先的理由见模块 docstring（推断内环可信度最高；射程不因贴基地而损失；
    操控者可在基地内就位）。config 参数保留以兼容旧调用签名。
    """
    base_cells = _base_cells_of(view)
    base = base_center(view)
    toward = _direction(base, view.enemy_base_pos())
    cells = standable_cells(view, view.blue_build_cells()) or view.blue_build_cells()

    def rank(cell: Pos) -> tuple[int, int, int, int]:
        aim = 0
        if toward is not None and base is not None:
            aim = -(cell.x - base.x) * toward[0] - (cell.y - base.y) * toward[1]
        return (_base_distance(cell, base_cells), aim, cell.x, cell.y)

    return tuple(sorted(cells, key=rank))


def preferred_wall_cells(view: WorldView, config: Config | None = None) -> tuple[Pos, ...]:
    """黄色可建造格排序：最内环优先（到基地距离升序），同距按 (x, y) 稳定。

    最内环的黄格构成紧贴己方区域的**第一道防线**：机器人要摸到基地必须先拆墙，
    而拆墙回合内武器持续输出。只用最内环可避免把己方角色围死、也省石头。
    """
    base_cells = _base_cells_of(view)
    return tuple(
        sorted(
            view.yellow_build_cells(),
            key=lambda c: (_base_distance(c, base_cells), c.x, c.y),
        )
    )


def wall_line(
    view: WorldView,
    limit: int | None = None,
    threat: Pos | None = None,
    exclude: frozenset[Pos] | set[Pos] | None = None,
    threat_dir: tuple[int, int] | None = None,
) -> tuple[Pos, ...]:
    """围墙防线：**只围三面**（来袭面 + 两个相邻面），跳过背面。

    机器人浪潮从基地的一侧来（用户实测），整圈围墙既费石头又把己方角色
    围死。因此按来袭方向把基地四周分成三档：

    - ``tier 0`` 正对来袭方向的那一面（**优先构筑**，先把机器人真正会走的一面堵上）
    - ``tier 1`` 与来袭方向垂直的两面（补成 U 形，防绕后）
    - 背面**不建**（`threat_dir` 未知时退化为四面全建）

    位置按「到基地最近的方环」由内向外铺，因此防线连续；同环内按面分档排序。
    """
    base = view.base_pos()
    if base is None:
        return ()
    yellow = set(view.yellow_build_cells())
    if not yellow:
        return ()
    blocked = {w.pos for w in view.own_walls()} | set(exclude or ())
    block = _base_cells_of(view)
    center = base_center(view)
    direction = _as_direction(threat_dir) or _direction(center, threat)
    candidates: list[tuple[int, int, int, int, int]] = []
    for radius in range(1, _WALL_MAX_RADIUS + 1):
        ring = _square_ring(base, radius)
        order = {cell: index for index, cell in enumerate(ring)}
        for cell in ring:
            if cell not in yellow or cell in blocked:
                continue
            tier = _side_tier(cell, center, block, direction)
            if tier is None:
                continue  # 背面：不建
            # 主序=方环（由内向外），次序=面分档（来袭面优先），再按环上顺序
            # 这样最近的一圈先被围成 U 形，正面（tier 0）永远排在两侧之前。
            candidates.append((radius, tier, order[cell], cell.x, cell.y))
    if not candidates:
        return ()
    ranked = sorted(set(candidates))
    ordered = [Pos(entry[3], entry[4]) for entry in ranked]
    return tuple(ordered if limit is None else ordered[: max(0, limit)])


def _square_ring(center: Pos, radius: int) -> tuple[Pos, ...]:
    """以 ``center`` 为心的切比雪夫半径 ``radius`` 方环（确定性遍历序）。"""
    cells: list[Pos] = []
    for dx in range(-radius, radius + 1):
        if abs(dx) == radius:
            for dy in range(-radius, radius + 1):
                cells.append(Pos(center.x + dx, center.y + dy))
        else:
            cells.append(Pos(center.x + dx, center.y - radius))
            cells.append(Pos(center.x + dx, center.y + radius))
    return tuple(cells)


def _as_direction(value: tuple[int, int] | None) -> tuple[int, int] | None:
    """把 (dx, dy) 归一化为八方向符号向量；零向量返回 None。"""
    if value is None:
        return None
    dx = (value[0] > 0) - (value[0] < 0)
    dy = (value[1] > 0) - (value[1] < 0)
    return (dx, dy) if (dx, dy) != (0, 0) else None


def _side_tier(
    cell: Pos,
    center: Pos | None,
    block: tuple[Pos, ...],
    direction: tuple[int, int] | None,
) -> int | None:
    """墙格属于哪一档：0=来袭面（优先）、1=相邻面（补 U 形）、None=背面（不建）。

    判定用「该格相对基地的偏移向量」与「来袭方向」的**余弦**：

    - ``cos > 0.5``（夹角 < 60°）→ 正对来袭面，优先级最高；
    - ``cos < -0.5``（夹角 > 120°）→ 背面，整段跳过（机器人几乎不会从那里来，
      建墙既费石头又可能把己方角色围死）；
    - 其余 → 与来袭面垂直的两侧，补成 U 形防绕后。

    ``direction`` 未知时一律 0（退化为四面全建）。
    """
    if direction is None or center is None or not block:
        return 0
    dx = cell.x - center.x
    dy = cell.y - center.y
    if dx == 0 and dy == 0:
        return 0  # 压在基地块上（不应出现）
    distance = max(abs(dx), abs(dy))  # 切比雪夫模长（与八方向一致）
    if distance == 0:
        return 0
    cos = (dx * direction[0] + dy * direction[1]) / distance
    if cos > 0.5:
        return 0
    if cos < -0.5:
        return None
    return 1


def _dominant_axis(direction: tuple[int, int]) -> str:
    """来袭方向的主轴（用于文档/调试；正负号已包含在 direction 里）。"""
    if direction[0] and direction[1]:
        return "x" if abs(direction[0]) >= abs(direction[1]) else "y"
    return "x" if direction[0] else "y"


# ------------------------------------------------------------------ 操控位


def control_cells(view: WorldView, weapon: Role) -> tuple[Pos, ...]:
    """武器的合法操控位（周围 1 格的界内空格），按到基地的距离升序。

    排除被单位/中立格/矿区占据的格；按到基地的距离升序 —— 操控位越靠内，
    角色从基地出发越早到位，也越不容易被机器人顺手拍掉。
    """
    base_cells = _base_cells_of(view)
    blocked = view.obstacles()
    cells = [
        Pos(weapon.pos.x + dx, weapon.pos.y + dy)
        for dx, dy in _ADJACENT
        if view.in_bounds(Pos(weapon.pos.x + dx, weapon.pos.y + dy))
        and Pos(weapon.pos.x + dx, weapon.pos.y + dy) not in blocked
    ]
    if not cells:
        return ()
    return tuple(sorted(cells, key=lambda c: (_base_distance(c, base_cells), c.x, c.y)))


def staging_cell(view: WorldView, weapon: Role) -> Pos | None:
    """该武器的推荐待命格：内环操控位；无空格时退回基地左上角。"""
    cells = control_cells(view, weapon)
    if cells:
        return cells[0]
    return view.base_pos()


def shelter_cells(view: WorldView) -> tuple[Pos, ...]:
    """「墙内」待命格：紧贴基地块（切比雪夫距离 1）的可站立空格。

    围墙按来袭面铺成 U 形后，贴着基地的**另一侧**空格就是墙内的安全位：机器人
    得先拆墙才能碰到躲在里面的角色，武器则在墙后继续输出。夜晚不操控武器的角色
    躲进来，比现在「退回基地左上角」更明确 —— 后者可能让角色停在面向机器人的
    那一侧，正好站在墙外挨打。

    距离 1 环内已被围墙/单位占据的格自动排除，所以围墙越铺越满时安全位会自然
    收敛到剩下开口的一侧。返回按 (x, y) 稳定排序。
    """
    base_cells = _base_cells_of(view)
    if not base_cells:
        return ()
    blocked = view.obstacles()
    ring: set[Pos] = set()
    for cell in base_cells:
        for dx, dy in _ADJACENT:
            nxt = Pos(cell.x + dx, cell.y + dy)
            if not view.in_bounds(nxt) or nxt in blocked:
                continue
            if _base_distance(nxt, base_cells) == 1:
                ring.add(nxt)
    return tuple(sorted(ring, key=lambda c: (c.x, c.y)))


def nearest_shelter(view: WorldView, origin: Pos) -> Pos | None:
    """离 ``origin`` 最近的安全位；没有安全位时退回基地左上角。"""
    shelters = shelter_cells(view)
    if not shelters:
        return view.base_pos()
    return min(shelters, key=lambda c: (chebyshev(origin, c), c.x, c.y))


def assign_controllers(
    view: WorldView, weapons: tuple[Role, ...] | None = None
) -> dict[int, int]:
    """武器 → 操控角色的一对一分配（最小化总距离，确定性）。

    仅在「角色数 ≥ 武器数」时做全排列最优匹配（最多 3 座武器 → 6 种排列），
    否则退化为按武器 id 的贪心就近分配。
    """
    if weapons is None:
        weapons = tuple(sorted(view.own_weapons(), key=lambda w: w.id))
    mobile = sorted(
        list(view.own_workers()) + list(view.own_pioneer()), key=lambda r: r.id
    )
    if not weapons or not mobile:
        return {}
    if len(weapons) <= len(mobile) <= 6:
        best_perm: tuple[Role, ...] | None = None
        best_key: tuple[int, tuple[int, ...]] | None = None
        for chosen in permutations(mobile, len(weapons)):
            cost = sum(
                chebyshev(role.pos, weapon.pos) for role, weapon in zip(chosen, weapons)
            )
            key = (cost, tuple(role.id for role in chosen))
            if best_key is None or key < best_key:
                best_key = key
                best_perm = chosen
        if best_perm is not None:
            return {weapon.id: role.id for weapon, role in zip(weapons, best_perm)}
    used: set[int] = set()
    assignment: dict[int, int] = {}
    for weapon in weapons:
        candidates = [
            role
            for role in mobile
            if role.id not in used
        ]
        if not candidates:
            break
        role = min(candidates, key=lambda r: (chebyshev(r.pos, weapon.pos), r.id))
        used.add(role.id)
        assignment[weapon.id] = role.id
    return assignment


# ------------------------------------------------------------------ 升级券


def affordable_voucher(
    view: WorldView, gold: int, config: Config | None = None
) -> str | None:
    """按性价比挑一张「买得起、且买得到用场」的升级券（价格升序）。

    为什么不是只买 ``WeaponUpgradeVoucher1``：它是 100 金，而围墙升级券只要
    **20 金**。用户真机实测里金币峰值只有 45~60，盯着 100 金的券等于永远买不到
    任何东西 —— 武器永远 level1，夜里被推平。先买 20 金的把已有围墙升到 L2，
    同样的钱换到的夜间硬度最高。

    「用得到」= 场上有该类别、且等级正好是券的起始等级的建筑（没有目标就白买）。
    背包放不下时不采购（§4.6.3：背包不足则购买失败）。
    """
    if not _has_room(view):
        return None
    candidates = [
        (price, _VOUCHER_CATEGORY_RANK.get(_VOUCHERS[name][0], 9), name)
        for name, price in _VOUCHER_PRICE.items()
        if price <= gold and _has_target(view, name)
    ]
    if not candidates:
        return None
    return min(candidates)[2]


def _has_target(view: WorldView, voucher: str) -> bool:
    """场上是否有能立刻升级的建筑（类别匹配且等级 == 券的起始等级）。"""
    category, from_level, to_level = _VOUCHERS[voucher]
    if to_level > MAX_LEVEL:
        return False
    return any(
        _matches(building, category) and building.level == from_level
        for building in view.own_roles()
    )


def _has_room(view: WorldView) -> bool:
    """至少有一名可移动角色背包没满（满了买不了，§4.6.3）。"""
    for role in _mobile(view):
        cap = getattr(role, "backPackCapability", 0)
        # 缺省/非法（<=0）视为「未知容量」：不因此拒绝采购，真满了让判题器拒
        if cap <= 0 or len(role.backpack) < cap:
            return True
    return False


def voucher_trip(
    view: WorldView, role: Role, config: Config | None = None
) -> tuple[str, Pos | None]:
    """手里的升级券该怎么办：``("use", None)`` / ``("walk", 目标格)`` / ``("none", None)``。

    为什么需要这一跳：``plan_upgrades`` 只在角色**已经紧贴目标**时才发 ``use``，而
    ``plan_turn`` 是用 ``setdefault`` 合并的 —— 经济指令永远先占住该角色，``use``
    就被静默丢掉了。实测整局 ``buy`` 3~8 次、``use`` **0 次**，武器/围墙一直停在
    level1。所以这里显式区分三种情形，交给调用方让路或先走过去。
    """
    for voucher in sorted(role.backpack):
        if voucher not in _VOUCHERS:
            continue
        category, from_level, _to_level = _VOUCHERS[voucher]
        targets = [
            building
            for building in view.own_roles()
            if _matches(building, category) and building.level == from_level
        ]
        if not targets:
            continue
        nearest = min(targets, key=lambda b: (chebyshev(role.pos, b.pos), b.pos.x, b.pos.y))
        if chebyshev(role.pos, nearest.pos) <= 1:
            return "use", None
        return "walk", nearest.pos
    return "none", None


def upgrade_order(config: Config | None = None) -> tuple[str, ...]:
    """升级优先级（``build.upgrade_order``）；缺失返回空。"""
    value = config.get("build.upgrade_order") if config is not None else None
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return ()


def plan_upgrades(
    view: WorldView, config: Config | None = None
) -> dict[int, RoleCommand]:
    """为持有升级券且身旁有可升级建筑的角色发出 ``use`` 指令。"""
    order = upgrade_order(config)
    commands: dict[int, RoleCommand] = {}
    for role in _mobile(view):
        voucher = _pick_voucher(role, order)
        if voucher is None:
            continue
        target = _upgrade_target(view, role, voucher)
        if target is not None:
            commands[role.id] = RoleCommand(
                action=Action.USE, name=voucher, targetPos=(target,)
            )
    return commands


def _pick_voucher(role: Role, order: tuple[str, ...]) -> str | None:
    owned = [item for item in role.backpack if item in _VOUCHERS]
    if not owned:
        return None
    if not order:
        return min(owned)
    rank = {name: i for i, name in enumerate(order)}
    return min(owned, key=lambda name: (rank.get(name, len(order)), name))


def _upgrade_target(view: WorldView, role: Role, voucher: str) -> Pos | None:
    category, from_level, to_level = _VOUCHERS[voucher]
    candidates = [
        building
        for building in view.own_roles()
        if _matches(building, category)
        and building.level == from_level
        and chebyshev(role.pos, building.pos) <= 1
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda b: (b.pos.x, b.pos.y)).pos


def _matches(building: Role, category: str) -> bool:
    kind = enum_to_str(building.roleType)
    if category == "weapon":
        return kind in _WEAPON_KINDS
    return kind == category


def _mobile(view: WorldView) -> tuple[Role, ...]:
    return tuple(
        r for r in view.own_roles() if enum_to_str(r.roleType) in _MOBILE_KINDS
    )
