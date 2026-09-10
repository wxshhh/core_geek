"""脚本化演示 Bot（供本地模拟器驱动；真实策略属于后续工作包）。

- ``IdleBot``：任何回合都返回空指令（保底对手）。
- ``ScriptedBot``：朴素防御——白天在基地旁尝试建满 3 座武器（火箭×1 +
  电磁炮×2）、工人采矿、囤够后去小贩贩卖；夜晚为每座可用武器就近分配
  操控角色并攻击射程内最近机器人，空闲角色撤向基地。

接口不传蓝/黄可建造区坐标（§1.2 无此字段），Bot 只能通过
``lastRoundRoleActionResults`` 得知建造是否生效——本 Bot 把失败候选格记入
黑名单逐步试出合法建造位，同时演示了结果回读用法。
"""

from __future__ import annotations

from typing import Final

from future_war.models import Pos, Request, Response, Role, RoleCommand
from future_war.sim.rules import (
    ORE_PRICES,
    ORE_TYPES,
    chebyshev,
    in_bounds,
    is_day_round,
    round_in_day,
)

MINE_TYPES: Final = frozenset(ORE_TYPES)
WEAPON_TYPES: Final = frozenset({"gatling", "railgun", "rocket"})
WEAPON_PLAN: Final = ("rocket", "railgun", "railgun")  # 方案 §3.4 推荐配比
SELL_THRESHOLD: Final = 5  # 背包矿石达到该数量才去贩卖
VENDOR_TYPE: Final = "vendor"
DUSK_RETURN: Final = 40  # 白天第 40 回合起工人撤回基地（夜防准备）
BASE_HOLD_RANGE: Final = 2  # 夜晚距基地 ≤2 的角色原地待命（充当操控者）

class IdleBot:
    """空策略 Bot：永远返回空指令。"""

    def __call__(self, request: Request) -> Response:
        return Response()


class ScriptedBot:
    """朴素防御 Bot（演示用，无寻路记忆，仅 1 步贪心）。"""

    def __init__(self) -> None:
        self._failed_build_cells: set[tuple[int, int]] = set()
        self._pending_build: dict[int, tuple[int, int]] = {}  # worker uid → 候选格

    def __call__(self, request: Request) -> Response:
        cmds: dict[int, RoleCommand] = {}
        self._digest_results(request)
        roles = [r for r in request.teamOur.roles if r.roleType in ("worker", "pioneer")]
        weapons = [r for r in request.teamOur.roles if r.roleType in WEAPON_TYPES]
        stations = [r for r in request.teamOur.roles if r.roleType == "station"]
        base = stations[0].pos if stations else Pos(0, 0)
        occupied = self._occupied(request)
        if is_day_round(request.roundNo):
            self._day_commands(request, cmds, roles, weapons, base, occupied)
        else:
            self._night_commands(request, cmds, roles, weapons, base, occupied)
        return Response(roleCommandMap=cmds)

    # ------------------------------------------------------------ 回合内逻辑

    def _day_commands(
        self,
        request: Request,
        cmds: dict[int, RoleCommand],
        roles: list[Role],
        weapons: list[Role],
        base: Pos,
        occupied: set[tuple[int, int]],
    ) -> None:
        vendor = self._find_zone(request, VENDOR_TYPE)
        mines = [z.pos for z in request.mapInfo.zones if z.neutralType in MINE_TYPES]
        for worker in [r for r in roles if r.roleType == "worker"]:
            if round_in_day(request.roundNo) >= DUSK_RETURN:
                self._issue_move(cmds, worker, base, occupied)
                continue
            if len(weapons) < 3 and request.teamOur.goldNum >= 25:
                cell = self._pick_build_cell(worker, base, occupied)
                if cell is not None:
                    kind = WEAPON_PLAN[min(len(weapons), len(WEAPON_PLAN) - 1)]
                    cmds[worker.id] = RoleCommand(
                        action="build", name=kind, targetPos=(Pos(*cell),)
                    )
                    self._pending_build[worker.id] = cell
                    continue
            ore = self._best_ore(worker)
            if ore is not None and len(worker.backpack) >= SELL_THRESHOLD:
                if vendor is not None:
                    if chebyshev(worker.pos.x, worker.pos.y, vendor.x, vendor.y) == 1:
                        cmds[worker.id] = RoleCommand(
                            action="sell", name=ore, num=worker.backpack.count(ore)
                        )
                        continue
                    if self._issue_move(cmds, worker, vendor, occupied):
                        continue
            mine = self._nearest(mines, worker.pos)
            if mine is not None:
                if chebyshev(worker.pos.x, worker.pos.y, mine.x, mine.y) == 1:
                    cmds[worker.id] = RoleCommand(
                        action="collect", targetPos=(Pos(mine.x, mine.y),)
                    )
                    continue
                self._issue_move(cmds, worker, mine, occupied)

    def _night_commands(
        self,
        request: Request,
        cmds: dict[int, RoleCommand],
        roles: list[Role],
        weapons: list[Role],
        base: Pos,
        occupied: set[tuple[int, int]],
    ) -> None:
        robots = [r.pos for r in request.robot.roles]
        assigned: set[int] = set()  # 已占用（含操控者与回防途中的角色）
        for weapon in sorted(weapons, key=lambda w: w.id):
            if weapon.cooldown > 0:
                continue
            controller = self._nearest_role_within(roles, weapon.pos, 1, exclude=assigned)
            if controller is None:
                free = self._nearest_role_within(roles, weapon.pos, None, exclude=assigned)
                if free is not None and self._issue_move(cmds, free, weapon.pos, occupied):
                    assigned.add(free.id)
                continue
            target = self._nearest_within(robots, weapon.pos, weapon.attackRange)
            if target is not None:
                cmds[weapon.id] = RoleCommand(
                    action="attack",
                    controllerId=str(controller.id),
                    targetPos=(Pos(*target),),
                )
            assigned.add(controller.id)
        for role in roles:
            if role.id in assigned:
                continue
            if chebyshev(role.pos.x, role.pos.y, base.x, base.y) <= BASE_HOLD_RANGE:
                continue  # 就地待命，充当操控者
            self._issue_move(cmds, role, base, occupied)

    # ------------------------------------------------------------ 辅助

    def _issue_move(
        self,
        cmds: dict[int, RoleCommand],
        role: Role,
        goal: Pos,
        occupied: set[tuple[int, int]],
    ) -> bool:
        """朝 goal 贪心走 1 步并发出 move 指令；无路可走返回 False。"""
        step = self._step_toward(role, goal, occupied)
        if step is None:
            return False
        cmds[role.id] = RoleCommand(action="move", targetPos=(Pos(*step),))
        return True

    def _digest_results(self, request: Request) -> None:
        """读上回合结果：建造失败候选格进黑名单。"""
        results = request.lastRoundRoleActionResults
        for uid, cell in list(self._pending_build.items()):
            if results.get(uid) is False:
                self._failed_build_cells.add(cell)
            self._pending_build.pop(uid)

    def _occupied(self, request: Request) -> set[tuple[int, int]]:
        occupied: set[tuple[int, int]] = {(r.pos.x, r.pos.y) for r in request.teamOur.roles}
        occupied.update((r.pos.x, r.pos.y) for r in request.teamEnemy.roles)
        occupied.update((z.pos.x, z.pos.y) for z in request.mapInfo.zones)
        occupied.update((r.pos.x, r.pos.y) for r in request.robot.roles)
        return occupied

    def _pick_build_cell(
        self, worker: Role, base: Pos, occupied: set[tuple[int, int]]
    ) -> tuple[int, int] | None:
        """基地附近螺旋候选格中找第一个未被占用/未失败的格子。"""
        base_cells = {(0, 0), (1, 0), (0, 1), (1, 1)}
        candidates = [
            (base.x + dx, base.y + dy)
            for dx in range(-2, 3)
            for dy in range(-2, 3)
            if (dx, dy) not in base_cells
        ]
        candidates.sort(
            key=lambda cell: (
                max(abs(cell[0] - base.x), abs(cell[1] - base.y)),
                cell[1],
                cell[0],
            )
        )
        for cell in candidates:
            if not in_bounds(*cell):
                continue
            if cell in occupied or cell in self._failed_build_cells:
                continue
            if chebyshev(worker.pos.x, worker.pos.y, *cell) != 1:
                continue
            return cell
        return None

    def _best_ore(self, worker: Role) -> str | None:
        present = [ore for ore in ORE_TYPES if ore in worker.backpack]
        if not present:
            return None
        return max(present, key=lambda ore: ORE_PRICES[ore])

    def _step_toward(
        self, role: Role, goal: Pos, occupied: set[tuple[int, int]]
    ) -> tuple[int, int] | None:
        """1 步贪心：朝目标走，避开障碍。"""
        neighbors = [
            (role.pos.x + dx, role.pos.y + dy)
            for dx in (-1, 0, 1)
            for dy in (-1, 0, 1)
            if (dx, dy) != (0, 0)
        ]
        neighbors.sort(
            key=lambda cell: (max(abs(cell[0] - goal.x), abs(cell[1] - goal.y)), cell)
        )
        for cell in neighbors:
            if in_bounds(*cell) and cell not in occupied:
                return cell
        return None

    @staticmethod
    def _find_zone(request: Request, kind: str) -> Pos | None:
        for zone in request.mapInfo.zones:
            if zone.neutralType == kind:
                return zone.pos
        return None

    @staticmethod
    def _nearest(cells: list[Pos], origin: Pos) -> Pos | None:
        if not cells:
            return None
        return min(cells, key=lambda c: chebyshev(c.x, c.y, origin.x, origin.y))

    def _nearest_role_within(
        self,
        roles: list[Role],
        origin: Pos,
        distance: int | None,
        exclude: set[int] | None = None,
    ) -> Role | None:
        candidates = [r for r in roles if r.id not in (exclude or set())]
        if distance is not None:
            candidates = [
                r
                for r in candidates
                if chebyshev(r.pos.x, r.pos.y, origin.x, origin.y) <= distance
            ]
        if not candidates:
            return None
        return min(candidates, key=lambda r: chebyshev(r.pos.x, r.pos.y, origin.x, origin.y))

    @staticmethod
    def _nearest_within(
        cells: list[Pos], origin: Pos, distance: int
    ) -> tuple[int, int] | None:
        inside = [
            (c.x, c.y)
            for c in cells
            if chebyshev(c.x, c.y, origin.x, origin.y) <= distance
        ]
        if not inside:
            return None
        return min(inside, key=lambda cell: chebyshev(*cell, origin.x, origin.y))

