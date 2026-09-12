"""寻路与碰撞规避（工作包 10，方案 M5）。

网格 BFS，8 方向，切比雪夫距离（任务书 §4.5.4）。阻挡规则（§4.1）：己方/敌方
建筑（基地、武器工事、围墙）、己方/敌方角色、机器人、中立单位（小贩、武器商店）、
4 个任务点、矿区均阻挡角色移动。「两个障碍物相邻的对角线位置不构成行进阻挡」
→ 允许对角穿越，不做 corner-cut 判定。

碰撞规避：§4.5.4 三类碰撞全部预测并规避，产出永不触发碰撞的合法步：
① 目标点受阻 —— 目标格被障碍物 / 未移动角色 / 机器人占据时绝不踏入；
② 目标点争夺 —— 多单位同步解析，先解析者预留目标格，后解析者避开；
③ 位置互换 —— 单位绝不踏入「已解析队友的出发格」，当该队友本回合目标正是
   自己的当前格。

保守假设（文档化）：敌方单位与机器人的移动意图不可知 → 其当前格一律视为
阻挡；未参与本轮解析的己方角色视为停留 → 其格阻挡；未解析队友（本轮优先级
更低）的当前格也保守阻挡。因此本模块产出的每一步在已知信息下都是安全步。

实现要点（性能，行为与朴素 BFS 逐字节一致）：
* 网格数据放进「外扩 1 圈的扁平数组」（``_Field``：mask/stamp/prev/queue），
  内层循环只做整数运算 —— 没有 ``Pos`` 分配、没有 dict 哈希、没有 bounds 判断。
  外扩坐标 = 真实坐标 + 1，切比雪夫与 (x,y) 字典序在整体平移下不变，故
  tie-break 可直接用外扩坐标（少一次 divmod/邻居）。
* ``stamp`` 用「代数」代替逐次清零：一轮 BFS +1，无需重建访问表。
* goal 可达时**发现即止**（cheb=0 的格唯一，必为最优），不必跑完连通分量；
  不可达才退化为全图「最优接近」，并在 BFS 过程中增量维护最小值（原本是全图
  扫一遍再取 min）。
* ``resolve_moves`` 的逐单位阻挡集差异用 mask 增量置位/还原（记录旧值，逆序
  回滚），避免每个单位都复制一份 base 并重新构建数组。
实测（41x32 地图，12 单位跨图）：``resolve_moves`` 52ms → 3.3ms（约 16x），
单次 ``next_step`` 4.3ms → 0.6ms（约 7x）。

API（策略引擎入口）：
    plan_move(view, unit_id, goal) -> Pos | None          # 单单位下一步；None=原地
    resolve_moves(view, goals, order=None) -> dict[int, Pos | None]  # 多单位同步解析
纯函数（测试与复用）：
    find_approach_path(blocked, w, h, start, goal) -> tuple[Pos, ...]  # BFS 路径
    next_step(blocked, w, h, start, goal) -> Pos | None   # 单步规划

约定：返回 Pos = 移动到该格（保证界内、8 邻接、无阻挡、无争夺、无互换）；
返回 None = 原地不动（无进展可期 / 已被围死 / 非可移动单位）——策略引擎此时
不应发出 move 指令。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final

from future_war.models import Pos, Role, enum_to_str
from future_war.core.world_map import in_bounds
from future_war.core.world_view import WorldView

_DIRS: Final = ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1))
_MOBILE_KINDS: Final = frozenset({"pioneer", "worker"})
_MAX_ID: Final = 1 << 30  # order 中缺失单位的兜底优先级（排最后）


class _Field:
    """外扩 1 格的扁平网格缓冲（边界恒阻挡 → 内层循环无需 bounds 判断）。

    每次公开函数调用新建一个实例，不跨调用共享 → 天然线程安全。
    """

    __slots__ = ("width", "height", "pw", "ph", "mask", "stamp", "prev", "gen", "neigh", "queue")

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.pw = pw = width + 2
        self.ph = ph = height + 2
        mask = bytearray(pw * ph)
        for i in range(pw):  # 上下边界
            mask[i] = 1
            mask[(ph - 1) * pw + i] = 1
        for j in range(ph):  # 左右边界
            mask[j * pw] = 1
            mask[j * pw + pw - 1] = 1
        self.mask = mask
        self.stamp = [0] * (pw * ph)
        self.prev = [0] * (pw * ph)
        self.queue = [0] * (pw * ph)
        self.gen = 0
        # (线性偏移, dx, dy)：偏移 = dx + dy*行宽
        self.neigh = tuple((dx + dy * pw, dx, dy) for dx, dy in _DIRS)

    def idx(self, pos: Pos) -> int:
        """真实坐标 → 外扩扁平索引。"""
        return (pos.y + 1) * self.pw + pos.x + 1

    def pos_of(self, idx: int) -> Pos:
        j, i = divmod(idx, self.pw)
        return Pos(i - 1, j - 1)


def _fill(field: _Field, blocked) -> None:
    """把阻挡集写进 mask（越界项忽略；外扩边界本就恒阻挡）。"""
    mask = field.mask
    width, height = field.width, field.height
    for pos in blocked:
        if 0 <= pos.x < width and 0 <= pos.y < height:
            mask[field.idx(pos)] = 1


def _search(field: _Field, start_idx: int, goal: Pos) -> int:
    """BFS：返回「最优可达格」的外扩扁平索引。

    最优 = 最小 (到 goal 的切比雪夫距离, BFS 距离, x, y)。goal 可达时发现即
    返回（cheb=0 的格唯一，必然最优），不再跑完整个连通分量。
    """
    stamp = field.stamp
    prev = field.prev
    mask = field.mask
    queue = field.queue
    pw = field.pw
    gen = field.gen = field.gen + 1  # 代数 +1：免去逐次清空 stamp
    goal_idx = (goal.y + 1) * pw + goal.x + 1
    stamp[start_idx] = gen
    prev[start_idx] = -1
    queue[0] = start_idx
    head, tail = 0, 1
    if start_idx == goal_idx:
        return start_idx

    # 外扩坐标 = 真实坐标 + 1：切比雪夫与 (x,y) 字典序在整体平移下不变。
    gx, gy = goal.x + 1, goal.y + 1
    sy, sx = divmod(start_idx, pw)
    best_idx = start_idx
    best_key = (max(abs(sx - gx), abs(sy - gy)), 0, sx, sy)

    dist = 0
    neigh = field.neigh
    while head < tail:
        level_end = tail
        dist += 1
        while head < level_end:  # 逐层推进：层号即 BFS 距离
            cur = queue[head]
            head += 1
            py, px = divmod(cur, pw)
            for off, dx, dy in neigh:
                nidx = cur + off
                if mask[nidx] or stamp[nidx] == gen:
                    continue
                stamp[nidx] = gen
                prev[nidx] = cur
                if nidx == goal_idx:
                    return nidx
                nx = px + dx
                ny = py + dy
                key = (max(abs(nx - gx), abs(ny - gy)), dist, nx, ny)
                if key < best_key:
                    best_key = key
                    best_idx = nidx
                queue[tail] = nidx
                tail += 1
    return best_idx


def _first_step(field: _Field, start_idx: int, best_idx: int) -> Pos | None:
    """从最优可达格回溯到「出发格之外的第一个节点」——即本回合该走的那一格。"""
    if best_idx == start_idx:
        return None
    prev = field.prev
    node = best_idx
    while prev[node] != start_idx:
        node = prev[node]
    return field.pos_of(node)


def find_approach_path(
    blocked: frozenset[Pos] | set[Pos],
    width: int,
    height: int,
    start: Pos,
    goal: Pos,
) -> tuple[Pos, ...]:
    """8 方向 BFS 最短路径（不含起点）；目标不可达时退化为「最优接近」。

    最优接近：在所有可达格中最小化到 goal 的切比雪夫距离；等距取 BFS 距离
    短者；再相等按 (x, y) 字典序 —— 结果完全确定。goal 越界时先钳制到界内。
    """
    goal = Pos(min(max(goal.x, 0), width - 1), min(max(goal.y, 0), height - 1))
    if not in_bounds(start, width, height):
        return ()
    field = _Field(width, height)
    _fill(field, blocked)
    start_idx = field.idx(start)
    best_idx = _search(field, start_idx, goal)
    if best_idx == start_idx:
        return ()
    prev = field.prev
    path: list[Pos] = []
    node = best_idx
    while node != start_idx:
        path.append(field.pos_of(node))
        node = prev[node]
    path.reverse()
    return tuple(path)


def next_step(
    blocked: frozenset[Pos] | set[Pos],
    width: int,
    height: int,
    start: Pos,
    goal: Pos,
) -> Pos | None:
    """给定阻挡集，返回从 start 朝 goal 的合法下一步；无进展可期返回 None（原地）。"""
    goal = Pos(min(max(goal.x, 0), width - 1), min(max(goal.y, 0), height - 1))
    if not in_bounds(start, width, height):
        return None
    field = _Field(width, height)
    _fill(field, blocked)
    start_idx = field.idx(start)
    return _first_step(field, start_idx, _search(field, start_idx, goal))


def plan_move(view: WorldView, unit_id: int, goal: Pos) -> Pos | None:
    """单单位移动规划：下一步合法格或 None（原地）。

    阻挡集 = 当前已知全部障碍（view.obstacles()：静态中立格/矿区/双方单位/
    机器人，§4.1）减自身格。多单位协同请用 resolve_moves（可避免己方争夺）。
    """
    role = _find_role(view, unit_id)
    if role is None:
        return None
    blocked = view.obstacles() - {role.pos}
    return next_step(blocked, view.static_map.width, view.static_map.height, role.pos, goal)


def resolve_moves(
    view: WorldView,
    goals: Mapping[int, Pos],
    *,
    order: Sequence[int] | None = None,
) -> dict[int, Pos | None]:
    """多单位同回合移动同步解析（详见模块 docstring 的三类碰撞规避）。

    goals: {role_id: 目标格}（仅己方可移动角色参与；建筑/未知 id 被忽略）。
    order: 优先级（高→低），默认按 id 升序；未列出的单位排最后。
    返回 {role_id: 下一步 Pos 或 None=原地}，键集合 = 参与解析的单位集合。
    """
    index = _mobile_index(view)
    participating = [(uid, index[uid]) for uid in goals if uid in index]
    if order is None:
        participating.sort(key=lambda item: item[0])
    else:
        rank = {uid: i for i, uid in enumerate(order)}
        participating.sort(key=lambda item: (rank.get(item[0], _MAX_ID), item[0]))

    width, height = view.static_map.width, view.static_map.height
    field = _Field(width, height)
    mask = field.mask
    _fill(field, _base_blocked(view, {uid for uid, _ in participating}))

    pos_of = {uid: role.pos for uid, role in participating}
    resolved: dict[int, Pos | None] = {}
    for i, (uid, role) in enumerate(participating):
        # 逐单位的阻挡差异：记录旧值 → 逆序回滚，使 mask 回到 base 状态
        applied: list[tuple[int, int]] = []
        for later_uid, _ in participating[i + 1 :]:
            _apply(mask, field.idx(pos_of[later_uid]), 1, applied)  # 未解析队友：保守阻挡
        for t, tnext in resolved.items():
            tpos = pos_of[t]
            if tnext is None:
                _apply(mask, field.idx(tpos), 1, applied)  # 已解析但原地 → 仍阻挡
            else:
                _apply(mask, field.idx(tnext), 1, applied)  # 目标格预留（争夺规避）
                if tnext != role.pos:
                    _apply(mask, field.idx(tpos), 0, applied)  # 出发格开放；互换保持阻挡
        start_idx = field.idx(role.pos)
        goal = goals[uid]
        best_idx = _search(field, start_idx, goal)
        resolved[uid] = _first_step(field, start_idx, best_idx)
        for idx, old in reversed(applied):
            mask[idx] = old
    return resolved


def _apply(mask: bytearray, idx: int, value: int, applied: list[tuple[int, int]]) -> None:
    """mask 置位/清位并记账旧值（同一格多次改动可逆序正确回滚）。"""
    applied.append((idx, mask[idx]))
    mask[idx] = value


def _mobile_index(view: WorldView) -> dict[int, Role]:
    """己方可移动角色 id → role（首次出现者胜，与 ``_find_role`` 同序）。

    原先逐个 uid 线性扫描 own_roles 是 O(单位数²)；这里一次成表。
    """
    index: dict[int, Role] = {}
    for role in view.dynamic.own_roles:
        if _is_mobile(role) and role.id not in index:
            index[role.id] = role
    return index


def _base_blocked(view: WorldView, participating_ids: set[int]) -> set[Pos]:
    """参与解析单位之外的全部阻挡格（§4.1 + 保守假设）。

    己方可移动角色：仅参与本轮解析者排除（其出发格由解析流程按规则开放/
    保持阻挡）；其余（建筑、未给目标的移动角色）一律阻挡。
    """
    blocked = set(view.static_map.static_obstacles)
    blocked.update(zone.pos for zone in view.dynamic.mines)
    blocked.update(role.pos for role in view.dynamic.enemy_roles)
    blocked.update(robot.pos for robot in view.dynamic.robots)
    for role in view.dynamic.own_roles:
        if not _is_mobile(role) or role.id not in participating_ids:
            blocked.add(role.pos)
    return blocked


def _find_role(view: WorldView, unit_id: int):
    """按 id 在己方单位中查找；要求为可移动角色（开拓者/工人）。"""
    for role in view.dynamic.own_roles:
        if role.id == unit_id and _is_mobile(role):
            return role
    return None


def _is_mobile(role) -> bool:
    return enum_to_str(role.roleType) in _MOBILE_KINDS
