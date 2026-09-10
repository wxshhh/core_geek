"""地图布局（任务书 §4.1 的合成版）。

任务书只给了地图尺寸与一张图片，没有坐标表；接口样例 docs/request.txt 给出了
基地、小贩、武器商店、任务点的坐标。本模块以该样例为锚点构造一张**文档化的
合成地图**（蓝/黄可建造区与机器人出生点为自定，见 sim/README.md），全部坐标
为纯数据、确定性，不含随机性（矿区随机部分见 mine 相关函数，随机源由调用方注入）。
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


# 双方角色出生格（合成设定，见 README）：都落在自家蓝区内、紧贴基地。
ROLE_SPAWNS: Final = {
    "challenger": {
        "worker1": (10, 22),
        "pioneer": (10, 23),
        "worker2": (11, 22),
    },
    "defender": {
        "worker1": (30, 12),
        "pioneer": (29, 10),
        "worker2": (29, 11),
    },
}


def _cells_in(x0: int, x1: int, y0: int, y1: int) -> frozenset[Cell]:
    return frozenset((x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1))


def make_layout() -> MapLayout:
    """构造合成地图（确定性；坐标锚点对齐 docs/request.txt 样例）。"""
    # 基地左上角（样例：挑战者 (10,24)、防守者 (30,10)）
    base_pos = {"challenger": (10, 24), "defender": (30, 10)}
    # 蓝色可建造区（只能建武器）：紧贴基地一侧
    weapon_zone = (
        _cells_in(10, 13, 20, 23)  # 挑战者：基地正下方
        | _cells_in(27, 29, 8, 11)  # 防守者：基地正左侧
    )
    # 黄色可建造区（只能建围墙）：蓝色区外侧的一层防线带
    wall_zone = (
        _cells_in(10, 15, 17, 19)  # 挑战者
        | _cells_in(24, 29, 5, 7)  # 防守者
    )
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
