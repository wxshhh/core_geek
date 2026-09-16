"""地图布局（任务书 §4.1 的合成版）。

任务书只给了地图尺寸与一张图片，没有坐标表；接口样例 docs/request.txt 给出了
基地、小贩、武器商店、任务点的坐标。本模块以该样例为锚点构造一张**文档化的
合成地图**（机器人出生点为自定，见 sim/README.md），全部坐标为纯数据、确定性，
不含随机性（矿区随机部分见 mine 相关函数，随机源由调用方注入）。

可建造区**不再自定矩形，而是按任务书配图的几何规则算出**：基地是 2×2 绿色块，
紧贴基地的一圈（到基地块切比雪夫距离 1）是蓝色（仅武器），再外一圈（距离 2）
是黄色（仅围墙）。这里刻意不复用生产代码 ``core/world_map._field``：模拟器的
价值就在于独立复现「判题器眼中的真地图」，共用一份实现会让几何写错时两边一起
错、回归测试失去裁判意义（``world.inference.blue_radius=1 / yellow_radius=2``
与本文的 1/2 环保持一致）。
"""

from __future__ import annotations

from dataclasses import dataclass
from random import Random
from typing import Final

from future_war.sim.rules import HEIGHT, WIDTH, Cell, in_bounds

TEAM_NAMES: Final = ("challenger", "defender")


@dataclass(frozen=True, slots=True)
class MapLayout:
    """静态地图布局：基地/可建造区/固定中立单位/机器人出生候选格。"""

    width: int
    height: int
    base_pos: dict[str, Cell]  # 基地左上角坐标（接口 §1.3.1）
    weapon_zone: frozenset[Cell]  # 蓝色：只能建武器
    wall_zone: frozenset[Cell]  # 黄色：只能建围墙
    fixed_neutrals: dict[Cell, str]  # 固定中立单位（小贩/武器商店/任务点）
    spawn_cells: dict[str, tuple[Cell, ...]]  # 目标阵营 → 机器人出生候选格

    def base_cells(self, team: str) -> frozenset[Cell]:
        """基地占用的 2×2 格子（§1.3.1 注）。"""
        bx, by = self.base_pos[team]
        return frozenset({(bx, by), (bx + 1, by), (bx, by + 1), (bx + 1, by + 1)})

    def in_weapon_zone(self, x: int, y: int) -> bool:
        return (x, y) in self.weapon_zone

    def in_wall_zone(self, x: int, y: int) -> bool:
        return (x, y) in self.wall_zone


# 双方角色出生格（合成设定，见 README）：都落在自家蓝区（距离 1 环）内。
# 几何修正后挑战者一侧整体下移一格：原 (10,22)/(11,22) 在新几何里属于黄区
# （距离 2），角色若出生在黄区就无法在开局当晚贴基地建武器。
ROLE_SPAWNS: Final = {
    "challenger": {
        "worker1": (9, 23),
        "pioneer": (10, 23),
        "worker2": (11, 23),
    },
    "defender": {
        "worker1": (30, 12),
        "pioneer": (29, 10),
        "worker2": (29, 11),
    },
}


def _base_block(base_pos: Cell) -> frozenset[Cell]:
    """基地 2×2 占用块（左上角 base_pos，§1.3.1 注）。"""
    bx, by = base_pos
    return frozenset({(bx, by), (bx + 1, by), (bx, by + 1), (bx + 1, by + 1)})


def _ring(base_pos: Cell, distance: int) -> frozenset[Cell]:
    """到基地块切比雪夫距离**恰为** distance 的一圈格（不含基地块本身）。

    只在基地块四周 distance 宽的包围盒内枚举，避免全图扫描；距离按「到块内
    任一格的最小切比雪夫距离」定义，块是 2×2 所以外圈必然是完整一环。
    """
    block = _base_block(base_pos)
    bx, by = base_pos
    cells: set[Cell] = set()
    for x in range(bx - distance, bx + 2 + distance):
        for y in range(by - distance, by + 2 + distance):
            if (x, y) in block or not in_bounds(x, y):
                continue
            if min(max(abs(x - cx), abs(y - cy)) for cx, cy in block) == distance:
                cells.add((x, y))
    return frozenset(cells)


def make_layout() -> MapLayout:
    """构造合成地图（确定性；坐标锚点对齐 docs/request.txt 样例）。"""
    # 基地左上角（样例：挑战者 (10,24)、防守者 (30,10)）
    base_pos = {"challenger": (10, 24), "defender": (30, 10)}
    fixed_neutrals = {
        (20, 16): "vendor",
        (25, 20): "weaponShop",
        (14, 14): "challengerTaskPoint1",
        (17, 17): "challengerTaskPoint2",
        (16, 17): "challengerTaskPoint2",  # 任务点2 占两格（§4.6.2）
        (23, 14): "defenderTaskPoint1",
        (26, 17): "defenderTaskPoint2",
        (27, 17): "defenderTaskPoint2",
    }
    # 中立格与双方基地块都不可建造（§4.1：矿区/中立区不在可建造区，二者互斥；
    # 生产侧 core/world_map.infer_buildable_cells 同样把它们排除）。几何上这
    # 些格当前落在环外，显式减去是为了让「环 ∩ 非中立」成为实现不变式。
    blocked = set(fixed_neutrals) | _base_block(base_pos["challenger"]) | _base_block(
        base_pos["defender"]
    )
    # 蓝色可建造区（只能建武器）：紧贴基地的一圈 = 距离 1 环
    weapon_zone = frozenset(
        cell for team in TEAM_NAMES for cell in _ring(base_pos[team], 1)
    ) - frozenset(blocked)
    # 黄色可建造区（只能建围墙）：蓝区外侧的下一环 = 距离 2 环
    wall_zone = frozenset(
        cell for team in TEAM_NAMES for cell in _ring(base_pos[team], 2)
    ) - frozenset(blocked)

    # 机器人出生候选格：目标基地朝向的地图边缘（合成设定，见 README）
    spawn_cells = {
        "challenger": tuple((x, 31) for x in range(4, 17))
        + tuple((0, y) for y in range(20, 29)),
        "defender": tuple((x, 0) for x in range(24, 37))
        + tuple((40, y) for y in range(4, 13)),
    }
    return MapLayout(
        width=WIDTH,
        height=HEIGHT,
        base_pos=base_pos,
        weapon_zone=weapon_zone,
        wall_zone=wall_zone,
        fixed_neutrals=fixed_neutrals,
        spawn_cells=spawn_cells,
    )


def mine_forbidden_cells(layout: MapLayout) -> frozenset[Cell]:
    """矿区禁止出现的格子：可建造区、固定中立单位、基地格、角色出生格（§4.1）。"""
    forbidden = set(layout.weapon_zone) | set(layout.wall_zone)
    forbidden |= set(layout.fixed_neutrals)
    for team in TEAM_NAMES:
        forbidden |= layout.base_cells(team)
        forbidden |= set(ROLE_SPAWNS[team].values())
    return frozenset(forbidden)


def initial_mines(layout: MapLayout, rng: Random) -> dict[Cell, str]:
    """初始矿区：石头/铁/铜各 2 个，随机选址（避开禁区，§4.1）。"""
    forbidden = mine_forbidden_cells(layout)
    mines: dict[Cell, str] = {}
    occupied: set[Cell] = set()
    for ore in ("stone", "stone", "iron", "iron", "copper", "copper"):
        cell = _free_cell(layout, rng, forbidden | occupied)
        mines[cell] = ore
        occupied.add(cell)
    return mines


def respawn_mine_cell(layout: MapLayout, rng: Random, occupied: set[Cell]) -> Cell:
    """枯竭矿区重生位置：随机禁区外空格（下回合刷新，§4.1）。"""
    return _free_cell(layout, rng, mine_forbidden_cells(layout) | occupied)


def _free_cell(layout: MapLayout, rng: Random, forbidden: set[Cell]) -> Cell:
    """拒绝采样一个不在 forbidden 内的合法格子。"""
    while True:
        cell = (rng.randrange(layout.width), rng.randrange(layout.height))
        if in_bounds(*cell) and cell not in forbidden:
            return cell
