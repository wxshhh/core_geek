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

from collections import deque
from collections.abc import Mapping, Sequence
from typing import Final

from future_war.models import Pos, enum_to_str
from future_war.core.world_map import chebyshev, in_bounds
from future_war.core.world_view import WorldView

_DIRS: Final = ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1))
_MOBILE_KINDS: Final = frozenset({"pioneer", "worker"})
_MAX_ID: Final = 1 << 30  # order 中缺失单位的兜底优先级（排最后）


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
    dist: dict[Pos, int] = {start: 0}
    prev: dict[Pos, Pos | None] = {start: None}
    queue: deque[Pos] = deque([start])
    while queue:
        cur = queue.popleft()
        for dx, dy in _DIRS:
            nxt = Pos(cur.x + dx, cur.y + dy)
            if not in_bounds(nxt, width, height) or nxt in blocked or nxt in dist:
                continue
            dist[nxt] = dist[cur] + 1
            prev[nxt] = cur
            queue.append(nxt)
    best = min(dist, key=lambda p: (chebyshev(p, goal), dist[p], p.x, p.y))
    if best == start:
        return ()
    path: list[Pos] = []
    node: Pos | None = best
    while node is not None and node != start:
        path.append(node)
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
    path = find_approach_path(blocked, width, height, start, goal)
    return path[0] if path else None


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
    own_roles = view.dynamic.own_roles
    participating = [
        (uid, role)
        for uid, role in ((uid, _find_role(view, uid)) for uid in goals)
        if role is not None
    ]
    if order is None:
        participating.sort(key=lambda item: item[0])
    else:
        rank = {uid: i for i, uid in enumerate(order)}
        participating.sort(key=lambda item: (rank.get(item[0], _MAX_ID), item[0]))

    base = _base_blocked(view, {uid for uid, _ in participating})
    pos_of = {uid: role.pos for uid, role in participating}
    width, height = view.static_map.width, view.static_map.height
    resolved: dict[int, Pos | None] = {}
    for i, (uid, role) in enumerate(participating):
        blocked = set(base)
        for later_uid, _ in participating[i + 1 :]:
            blocked.add(pos_of[later_uid])  # 未解析队友：保守阻挡其当前格
        for t, tnext in resolved.items():
            tpos = pos_of[t]
            if tnext is None:
                blocked.add(tpos)  # 已解析但原地 → 仍阻挡
            else:
                blocked.add(tnext)  # 目标格预留（争夺规避）
                if tnext != role.pos:
                    blocked.discard(tpos)  # 出发格开放（跟随）；互换情形保持阻挡
        resolved[uid] = next_step(blocked, width, height, role.pos, goals[uid])
    return resolved


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
