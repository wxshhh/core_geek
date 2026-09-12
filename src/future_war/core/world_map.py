"""静态地图：中立区域分类 + 可建造区推断（工作包 9，方案 M2）。

接口不下发可建造区（蓝=仅武器/黄=仅围墙，任务书 §4.1），只能从几何推断：
以己方基地 2x2 块（基地 pos 为左上角，接口 §1.3.1 注）为中心，到块内任意格
的切比雪夫距离 ≤ blue_radius 的候选为蓝区、≤ yellow_radius 且非蓝区的为黄区。
排除：己方/敌方基地格、全部中立区域格（矿区/小贩/武器商店/任务点——§4.1
矿区不会刷新在可建造区，二者互斥）、越界格、建造反馈证伪格（refuted）。

推断仅为估算：真实建造合法性以判题器反馈为准（WorldModel.record_build_attempt
提供反馈闭环证伪）。推断对相同输入完全确定 → 跨回合稳定。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

from future_war.config import Config
from future_war.models import MapInfo, NeutralType, Pos, TeamType, Zone, enum_to_str

_BASE_OFFSETS: tuple[tuple[int, int], ...] = ((0, 0), (1, 0), (0, 1), (1, 1))


def chebyshev(a: Pos, b: Pos) -> int:
    """切比雪夫距离（任务书 §4.5.4）。"""
    return max(abs(a.x - b.x), abs(a.y - b.y))


def in_bounds(pos: Pos, width: int, height: int) -> bool:
    """坐标是否在地图内（任务书 §4.1：41x32，原点左下）。"""
    return 0 <= pos.x < width and 0 <= pos.y < height


def base_cells(top_left: Pos) -> frozenset[Pos]:
    """基地 2x2 占用格（接口 §1.3.1 注：pos 为左上角）。"""
    return frozenset(Pos(top_left.x + dx, top_left.y + dy) for dx, dy in _BASE_OFFSETS)


class BuildableKind(str, Enum):
    """可建造区类型（任务书 §4.1）。"""

    WEAPON = "weapon"
    WALL = "wall"


@dataclass(frozen=True, slots=True)
class BuildableInference:
    """可建造区推断参数（config world.inference.*）。"""

    blue_radius: int = 3
    yellow_radius: int = 6
    refuted_weapon: frozenset[Pos] = frozenset()
    refuted_wall: frozenset[Pos] = frozenset()

    def with_refuted(
        self, weapon: frozenset[Pos], wall: frozenset[Pos]
    ) -> "BuildableInference":
        return BuildableInference(self.blue_radius, self.yellow_radius, weapon, wall)


def inference_from_config(
    config: Config | None,
    blue_radius: int | None = None,
    yellow_radius: int | None = None,
) -> BuildableInference:
    """推断参数装配：显式参数 > 配置（world.inference.*）> 内置默认。"""
    default = BuildableInference()
    if config is None:
        return BuildableInference(
            blue_radius=default.blue_radius if blue_radius is None else blue_radius,
            yellow_radius=default.yellow_radius if yellow_radius is None else yellow_radius,
        )
    blue = (
        blue_radius
        if blue_radius is not None
        else _pick_int(config.get("world.inference.blue_radius"), default.blue_radius)
    )
    yellow = (
        yellow_radius
        if yellow_radius is not None
        else _pick_int(config.get("world.inference.yellow_radius"), default.yellow_radius)
    )
    return BuildableInference(blue_radius=blue, yellow_radius=yellow)


def _pick_int(value: object, default: int) -> int:
    """配置值 → 半径 int；非法类型/非法字符串回退默认（parse-don't-validate）。"""
    if isinstance(value, (int, float, bool)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


@dataclass(frozen=True, slots=True)
class ZoneIndex:
    """中立区域分类结果（任务书 §4.1/§4.6）。"""

    vendor_pos: Pos | None
    weapon_shop_pos: Pos | None
    own_task_points: tuple[Pos, ...]
    enemy_task_points: tuple[Pos, ...]
    obstacles: frozenset[Pos]  # 全部中立区域格，均阻挡角色移动（§4.1）


def classify_zones(zones: Sequence[Zone], own_team: TeamType | str) -> ZoneIndex:
    """中立区域分类：小贩/武器商店/己方与敌方任务点/全部阻挡格。"""
    vendor_pos: Pos | None = None
    weapon_shop_pos: Pos | None = None
    own_tasks: list[Pos] = []
    enemy_tasks: list[Pos] = []
    obstacles: set[Pos] = set()
    own_prefix = "challenger" if own_team == TeamType.CHALLENGER else "defender"
    for zone in zones:
        kind = enum_to_str(zone.neutralType)
        obstacles.add(zone.pos)
        if kind == NeutralType.VENDOR:
            vendor_pos = zone.pos
        elif kind == NeutralType.WEAPON_SHOP:
            weapon_shop_pos = zone.pos
        elif kind.startswith(f"{own_prefix}TaskPoint"):
            own_tasks.append(zone.pos)
        elif "TaskPoint" in kind:
            enemy_tasks.append(zone.pos)
    return ZoneIndex(
        vendor_pos, weapon_shop_pos, tuple(own_tasks), tuple(enemy_tasks), frozenset(obstacles)
    )


def infer_buildable_cells(
    map_info: MapInfo,
    own_base: Pos | None,
    zone_index: ZoneIndex,
    enemy_base: Pos | None,
    inference: BuildableInference,
) -> tuple[frozenset[Pos], frozenset[Pos]]:
    """推断蓝区（武器）与黄区（围墙）格集合（详见模块 docstring）。

    Smell-2 豁免说明：map_info/own_base/enemy_base/zone_index 是四个互不隶属的
    独立几何事实，inference 为可调配置束；强行分组只会引入一次性包装类型。
    """
    if own_base is None:
        return frozenset(), frozenset()
    blocked = set(zone_index.obstacles)
    blocked.update(base_cells(own_base))
    if enemy_base is not None:
        blocked.update(base_cells(enemy_base))
    weapon_blocked = blocked | set(inference.refuted_weapon)
    wall_blocked = blocked | set(inference.refuted_wall)
    blue = _field(map_info, own_base, 1, inference.blue_radius, weapon_blocked)
    yellow = _field(
        map_info, own_base, 1, inference.yellow_radius, wall_blocked
    ) - blue
    return frozenset(blue), frozenset(yellow)


def _field(
    map_info: MapInfo,
    own_base: Pos,
    min_dist: int,
    max_dist: int,
    excluded: set[Pos],
) -> set[Pos]:
    """到基地块切比雪夫距离 ∈ [min_dist, max_dist] 的未排除格。

    任务书没有蓝/黄区域坐标表，只给了「基地附近有蓝色（武器）与黄色（围墙）
    可建造区域」这一几何事实（§4.1 + 配图）。蓝区取距离 1..``blue_radius``、
    黄区取距离 1..``yellow_radius``，两者相减（先蓝后黄）。相比只有一圈的
    「环形」，区间式把基地紧邻的内圈也纳入 —— 那正是最适合建武器的一圈
    （操控者能在基地内就位，夜晚第 1 回合即可开火）。

    区间内仍可能有误判格，判题器的 ``lastRoundRoleActionResults`` 反馈会通过
    ``refuted_*`` 逐一剔除（见 ``apply_build_feedback``）。
    """
    block = base_cells(own_base)
    cells: set[Pos] = set()
    for x in range(map_info.width):
        for y in range(map_info.height):
            pos = Pos(x, y)
            if pos in block or pos in excluded:
                continue
            distance = min(chebyshev(pos, cell) for cell in block)
            if min_dist <= distance <= max_dist:
                cells.add(pos)
    return cells


@dataclass(frozen=True, slots=True)
class StaticMap:
    """静态地图：几何 + 可建造推断 + 固定中立点 + 静态阻挡格。"""

    width: int
    height: int
    own_team: TeamType | str
    own_base: Pos | None
    enemy_base: Pos | None
    blue: frozenset[Pos]
    yellow: frozenset[Pos]
    vendor_pos: Pos | None
    weapon_shop_pos: Pos | None
    own_task_points: tuple[Pos, ...]
    enemy_task_points: tuple[Pos, ...]
    static_obstacles: frozenset[Pos]

    @classmethod
    def empty(cls) -> "StaticMap":
        """空地图（首回合异常降级用）。"""
        return cls(0, 0, "", None, None, frozenset(), frozenset(), None, None, (), (), frozenset())

    def in_bounds(self, pos: Pos) -> bool:
        return in_bounds(pos, self.width, self.height)

    def can_build(self, pos: Pos, kind: BuildableKind | str) -> bool:
        """推断的建造合法性（估算；判题器反馈见 WorldModel.record_build_attempt）。"""
        if not self.in_bounds(pos):
            return False
        return pos in (self.blue if kind == BuildableKind.WEAPON else self.yellow)

    def own_base_cells(self) -> frozenset[Pos]:
        return base_cells(self.own_base) if self.own_base is not None else frozenset()

    def enemy_base_cells(self) -> frozenset[Pos]:
        return base_cells(self.enemy_base) if self.enemy_base is not None else frozenset()


def build_static_map(
    map_info: MapInfo,
    own_team: TeamType | str,
    own_base: Pos | None,
    enemy_base: Pos | None,
    *,
    inference: BuildableInference,
) -> StaticMap:
    """组装静态地图：区域分类 + 可建造推断 + 静态阻挡格。

    Smell-2 豁免说明：map_info/own_team/own_base/enemy_base 是互不隶属的独立
    几何事实，inference 为配置束；分组只会制造一次性包装类型。
    """
    zone_index = classify_zones(map_info.zones, own_team)
    blue, yellow = infer_buildable_cells(map_info, own_base, zone_index, enemy_base, inference)
    obstacles = set(zone_index.obstacles)
    if own_base is not None:
        obstacles.update(base_cells(own_base))
    if enemy_base is not None:
        obstacles.update(base_cells(enemy_base))
    return StaticMap(
        width=map_info.width,
        height=map_info.height,
        own_team=own_team,
        own_base=own_base,
        enemy_base=enemy_base,
        blue=blue,
        yellow=yellow,
        vendor_pos=zone_index.vendor_pos,
        weapon_shop_pos=zone_index.weapon_shop_pos,
        own_task_points=zone_index.own_task_points,
        enemy_task_points=zone_index.enemy_task_points,
        static_obstacles=frozenset(obstacles),
    )


@dataclass(frozen=True, slots=True)
class BuildAttempt:
    """一次建造尝试（规划器记账，等待判题器下回合合法性反馈）。"""

    round: int
    pos: Pos
    kind: BuildableKind
    role_id: int


def apply_build_feedback(
    attempts: Sequence[BuildAttempt],
    round_no: int,
    results: Mapping[int, bool],
) -> tuple[list[BuildAttempt], frozenset[Pos], frozenset[Pos]]:
    """上回合建造合法性反馈 → (保留的未决尝试, 新证伪武器格, 新证伪围墙格)。

    上回合尝试非法（False）→ 对应 kind 的候选格证伪；合法/无反馈 → 丢弃
    （尝试仅对上回合有效）；未到期尝试原样保留。
    """
    kept: list[BuildAttempt] = []
    refuted_weapon: set[Pos] = set()
    refuted_wall: set[Pos] = set()
    for attempt in attempts:
        if attempt.round != round_no - 1:
            kept.append(attempt)
            continue
        if results.get(attempt.role_id) is False:
            target = refuted_weapon if attempt.kind == BuildableKind.WEAPON else refuted_wall
            target.add(attempt.pos)
    return kept, frozenset(refuted_weapon), frozenset(refuted_wall)
