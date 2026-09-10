"""模拟器世界状态（任务书 §4.1/§4.5/§4.7 的可变事实源）。

世界状态是整场对局的唯一可变状态容器：每回合原地更新（可变性即设计目的，
避免 1300 回合每回合深拷贝开销）。外部输入（Bot 响应）在 judge 层校验后，
由 engine 以指令形式作用到本容器上。

确定性要求（工作包 3）：所有随机性来自 ``self.rng``（由 seed 构造）；
所有集合迭代要么按插入序（dict），要么排序后迭代；跨进程使用
``random.Random(seed)`` 保证可复现。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from random import Random
from typing import Final

from future_war.sim.layout import ROLE_SPAWNS, MapLayout, initial_mines
from future_war.sim.rules import (
    BUILDING_HP,
    Cell,
    INITIAL_GOLD,
    MINE_CHARGES,
    ROLE_CAP,
    ROLE_HP,
    VISION_RANGE,
    chebyshev,
    in_bounds,
)

STATION_CHALLENGER: Final = 10013
STATION_DEFENDER: Final = 20013


@dataclass(slots=True)  # noqa: MUTABLE_OK — 世界状态：可变性即设计目的
class Unit:
    """一个己方单位：角色（开拓者/工人）或建筑（基地/武器/围墙）。"""

    uid: int
    kind: str  # station/gatling/railgun/rocket/wall/pioneer/worker
    x: int
    y: int
    hp: int
    max_hp: int
    level: int
    cooldown: int
    cap: int
    backpack: list[str]
    alive: bool = True
    death_day: int = 0  # 角色死亡当天（复活计时用，§4.5.2）

    @property
    def is_role(self) -> bool:
        return self.kind in ROLE_HP

    @property
    def is_weapon(self) -> bool:
        return self.kind in ("gatling", "railgun", "rocket")

    @property
    def is_building(self) -> bool:
        return not self.is_role


@dataclass(slots=True)  # noqa: MUTABLE_OK — 世界状态：可变性即设计目的
class Robot:
    """一只机器人（§4.7）。"""

    rid: int
    kind: str  # smallRobot/middleRobot/largeRobot/bossRobot
    x: int
    y: int
    hp: int
    target_team: str  # 攻击目标阵营（§1.5.1）


@dataclass(slots=True)  # noqa: MUTABLE_OK — 世界状态：可变性即设计目的
class Mine:
    """一个矿区（§4.1：每矿可采 10 次后消失、下回合刷新）。"""

    ore: str
    charges: int = MINE_CHARGES


@dataclass(slots=True)  # noqa: MUTABLE_OK — 世界状态：可变性即设计目的
class TeamState:
    """一支队伍的全部可动态状态。"""

    team: str
    gold: int
    units: dict[int, Unit]  # 全部单位（含已死亡角色与已毁建筑记账）
    kills: dict[str, int] = field(default_factory=dict)  # 机器人类型 → 击杀数
    base_destroy_day: int = 0  # 基地被毁当天；0=未毁
    walls_issued: int = 0  # 已分配的墙 ID 计数（40000/41000 起）


@dataclass(slots=True)
class DamageEvent:
    """一条待回合末统一结算的伤害（§4.4：伤害本回合结束后统一结算）。"""

    source_team: str  # 伤害来源阵营（击杀分归属，§六 score2）
    amount: int
    robot_rid: int | None = None  # 目标：机器人
    unit_ref: tuple[str, int] | None = None  # 目标：单位 (team, uid)


class World:
    """对局世界：布局 + 双方状态 + 机器人 + 矿区 + 回合计数器。"""

    def __init__(self, seed: int, layout: MapLayout) -> None:
        self.seed = seed
        self.layout = layout
        self.rng = Random(seed)
        self.round_no = 0
        self.robots: dict[int, Robot] = {}
        self.mines: dict[Cell, Mine] = {}
        self.teams: dict[str, TeamState] = {
            team: self._make_team(team) for team in ("challenger", "defender")
        }
        # 回合结算暂存区（engine 每回合清空）
        self.pending_damage: list[DamageEvent] = []
        self.mine_harvest: dict[Cell, int] = {}  # 本回合各矿被采集人次
        self.respawn_queue: list[str] = []  # 下回合重生的矿石类型
        self.summons: dict[str, dict[str, int]] = {}  # 目标阵营 → 类型 → 追加数
        self.events: list[str] = []  # 本回合结构化事件（judge 消费后清空）
        self.robot_id_next = 30001
        for cell, ore in initial_mines(layout, self.rng).items():
            self.mines[cell] = Mine(ore=ore)

    # ------------------------------------------------------------ 初始化

    def _make_team(self, team: str) -> TeamState:
        offset = 10000 if team == "challenger" else 20000
        spawns = ROLE_SPAWNS[team]
        units: dict[int, Unit] = {}
        units[offset + 10] = self._make_role(offset + 10, "worker", *spawns["worker1"])
        units[offset + 11] = self._make_role(offset + 11, "pioneer", *spawns["pioneer"])
        units[offset + 12] = self._make_role(offset + 12, "worker", *spawns["worker2"])
        bx, by = self.layout.base_pos[team]
        station = Unit(
            uid=offset + 13,
            kind="station",
            x=bx,
            y=by,
            hp=BUILDING_HP["station"][0],
            max_hp=BUILDING_HP["station"][0],
            level=1,
            cooldown=0,
            cap=0,
            backpack=[],
        )
        units[station.uid] = station
        return TeamState(
            team=team,
            gold=INITIAL_GOLD,
            units=units,
            kills={kind: 0 for kind in ("smallRobot", "middleRobot", "largeRobot", "bossRobot")},
        )

    def _make_role(self, uid: int, kind: str, x: int, y: int) -> Unit:
        return Unit(
            uid=uid,
            kind=kind,
            x=x,
            y=y,
            hp=ROLE_HP[kind],
            max_hp=ROLE_HP[kind],
            level=1,
            cooldown=0,
            cap=ROLE_CAP[kind],
            backpack=[],
        )

    # ------------------------------------------------------------ 查询

    def opponent(self, team: str) -> str:
        return "defender" if team == "challenger" else "challenger"

    def alive_units(self, team: str) -> list[Unit]:
        """队伍存活单位（含建筑），按 ID 排序保证确定性。"""
        ts = self.teams[team]
        return sorted((u for u in ts.units.values() if u.alive), key=lambda u: u.uid)

    def base(self, team: str) -> Unit | None:
        """基地单位；不存在（已被移除）返回 None。"""
        for u in self.teams[team].units.values():
            if u.kind == "station":
                return u
        return None

    def base_alive(self, team: str) -> bool:
        base = self.base(team)
        return base is not None and base.alive

    def weapons(self, team: str) -> list[Unit]:
        """队伍存活武器，按 ID 排序。"""
        return [u for u in self.alive_units(team) if u.is_weapon]

    def roles(self, team: str) -> list[Unit]:
        """队伍存活角色（开拓者/工人），按 ID 排序。"""
        return [u for u in self.alive_units(team) if u.is_role]

    # ------------------------------------------------------------ 占用与视野

    def building_at(self, x: int, y: int) -> tuple[str, Unit] | None:
        """(x,y) 上的存活建筑（基地按 2×2 判定）；无则 None。"""
        for team in ("challenger", "defender"):
            for u in self.teams[team].units.values():
                if not u.alive or u.is_role:
                    continue
                if u.kind == "station":
                    if (x, y) in self.layout.base_cells(team):
                        return team, u
                elif (u.x, u.y) == (x, y):
                    return team, u
        return None

    def role_at(self, x: int, y: int) -> tuple[str, Unit] | None:
        """(x,y) 上的存活角色；无则 None。"""
        for team in ("challenger", "defender"):
            for u in self.teams[team].units.values():
                if u.alive and u.is_role and (u.x, u.y) == (x, y):
                    return team, u
        return None

    def any_unit_at(self, x: int, y: int) -> tuple[str, Unit] | None:
        """(x,y) 上的任意存活单位（角色或建筑）；无则 None。"""
        return self.role_at(x, y) or self.building_at(x, y)

    def robot_at(self, x: int, y: int) -> Robot | None:
        for rob in self.robots.values():
            if (rob.x, rob.y) == (x, y):
                return rob
        return None

    def static_blocked(self, x: int, y: int) -> bool:
        """(x,y) 是否被静态障碍占据：建筑/中立单位/任务点/矿区（§4.1）。"""
        if not in_bounds(x, y):
            return True
        if self.building_at(x, y) is not None:
            return True
        if (x, y) in self.layout.fixed_neutrals:
            return True
        if (x, y) in self.mines:
            return True
        return False

    def unit_visible_to(self, team: str, x: int, y: int) -> bool:
        """(x,y) 是否处于 team 任一存活单位视野 4 内（§4.3 全局共享视野）。"""
        for u in self.teams[team].units.values():
            if u.alive and chebyshev(u.x, u.y, x, y) <= VISION_RANGE:
                return True
        return False

    # ------------------------------------------------------------ 指令辅助

    def next_wall_id(self, team: str) -> int:
        """分配下一个墙 ID（挑战者 40000 起、防守者 41000 起，§1.3.1）。"""
        ts = self.teams[team]
        base = 40000 if team == "challenger" else 41000
        uid = base + ts.walls_issued
        ts.walls_issued += 1
        return uid

    def next_robot_id(self) -> int:
        rid = self.robot_id_next
        self.robot_id_next += 1
        return rid
