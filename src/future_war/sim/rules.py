"""模拟器规则常量与几何工具（任务书 §4.2/§4.5/§4.7 的可扩展子集）。

本模块只含纯数据与纯函数（无任何可变状态），供 world/engine/judge 共用。
所有数值引用 docs/任务书.md 相应章节；简化与偏离在 sim/README.md 集中说明，
本模块用 ``SIMPLIFIED:`` 注释就近标注非官方出处。

坐标约定（任务书 §4.1）：原点左下角，(0,0) 起，x 右 y 上；地图 41×32。
距离统一用切比雪夫距离（任务书 §4.5.4）。
"""

from __future__ import annotations

import math
from typing import Final, Sequence, TypeAlias

Cell: TypeAlias = tuple[int, int]

# ---------------------------------------------------------------- 地图与时间（§4.1/§4.2）

WIDTH: Final = 41
HEIGHT: Final = 32
MAX_ROUNDS: Final = 1300  # 10 天
DAY_ROUNDS: Final = 70
NIGHT_ROUNDS: Final = 60
ROUNDS_PER_DAY: Final = DAY_ROUNDS + NIGHT_ROUNDS
MAX_DAYS: Final = 10

# ---------------------------------------------------------------- 视野（§4.3）

VISION_RANGE: Final = 4  # 己方全部单位视野距离，全局共享

# ---------------------------------------------------------------- 经济（§4.5.3/§4.6.1）

INITIAL_GOLD: Final = 75
WEAPON_COST: Final = 25  # 三种武器统一造价（§4.5.1）
MAX_WEAPONS: Final = 3  # 武器工事全局同时最多 3 座
WALL_COST_STONE: Final = 1  # 围墙消耗石头×1
MINE_CHARGES: Final = 10  # 每个矿可采 10 次（§4.1）

ORE_TYPES: Final = ("stone", "iron", "copper")
ORE_PRICES: Final = {"stone": 1, "iron": 3, "copper": 5}

# 武器商店商品与价格（接口 §1.1 weaponShopList 样例）
WEAPON_SHOP: Final = {
    "WeaponUpgradeVoucher1": 100,
    "WeaponUpgradeVoucher2": 150,
    "WallUpgradeVoucher1": 20,
    "WallUpgradeVoucher2": 30,
    "StationUpgradeVoucher1": 100,
    "StationUpgradeVoucher2": 150,
    "WallFixer": 10,
    "Medicine": 10,
    "DizzyWeapon": 100,
    "Bomb": 100,
    "SmallRobotSummonOrder": 20,
    "MiddleRobotSummonOrder": 30,
    "LargeRobotSummonOrder": 100,
    "BossRobotSummonOrder": 200,
    "AcientTablet": 15,
    "StarSand": 15,
    "FlameBreath": 15,
    "FrostPotion": 15,
    "ThornAmulet": 15,
    "IronWhistle": 15,
}

# 召唤令 → 对方下夜追加的机器人类型（§4.6.3）
SUMMON_ORDERS: Final = {
    "SmallRobotSummonOrder": "smallRobot",
    "MiddleRobotSummonOrder": "middleRobot",
    "LargeRobotSummonOrder": "largeRobot",
    "BossRobotSummonOrder": "bossRobot",
}

# ---------------------------------------------------------------- 单位属性（§4.5.1/§4.5.2）

ROLE_KINDS: Final = ("pioneer", "worker")
ROLE_HP: Final = {"pioneer": 200, "worker": 220}
ROLE_CAP: Final = {"pioneer": 40, "worker": 100}

WEAPON_TYPES: Final = ("gatling", "railgun", "rocket")
BUILDABLE_NAMES: Final = (*WEAPON_TYPES, "wall")

# 血量按等级 1/2/3（§4.5.1）
BUILDING_HP: Final = {
    "station": (1500, 3000, 4500),
    "gatling": (1000, 1500, 2000),
    "railgun": (1000, 1500, 2000),
    "rocket": (1000, 1500, 2000),
    "wall": (1000, 1500, 2000),
}

# 单目标伤害基数（§4.5.1：加特林 10×等级目标数、电磁炮 10×等级能量、火箭 20×等级导弹数）
WEAPON_ATTACK: Final = {"gatling": 10, "railgun": 10, "rocket": 20}

# 攻击距离按等级 1/2/3（§4.5.1）；None 表示全图
WEAPON_RANGE: Final = {
    "gatling": (3, 5, 7),
    "railgun": (6, 8, 10),
    "rocket": (10, 15, None),
}

ROCKET_COOLDOWN: Final = 3  # 火箭发射后 3 回合冷却（§4.5.1）
ROCKET_SPLASH_RATIO: Final = 0.5  # 溅射伤害为中心伤害一半（§4.5.4）

RESPAWN_DELAY_ROUNDS: Final = 20  # 角色阵亡后次日白天开始后 20 回合复活（§4.5.2）

GATLING_CONE_DEG: Final = 90.0  # 加特林多目标须落在同一 90° 锥形内（§4.5.4）

# ---------------------------------------------------------------- 机器人（§4.7.2）

ROBOT_TYPES: Final = ("smallRobot", "middleRobot", "largeRobot", "bossRobot")

ROBOT_STATS: Final = {
    "smallRobot": {"atk": 5, "range": 3, "hp": 40, "score": 1},
    "middleRobot": {"atk": 10, "range": 3, "hp": 60, "score": 2},
    "largeRobot": {"atk": 20, "range": 3, "hp": 500, "score": 4},
    "bossRobot": {"atk": 40, "range": 3, "hp": 800, "score": 10},
}

# 队伍异常上限（§八：累计 5 次后不再调度）
MAX_TEAM_EXCEPTIONS: Final = 5

# ---------------------------------------------------------------- 几何与时间纯函数


def chebyshev(ax: int, ay: int, bx: int, by: int) -> int:
    """切比雪夫距离（§4.5.4）。"""
    return max(abs(ax - bx), abs(ay - by))


def in_bounds(x: int, y: int) -> bool:
    """坐标是否在 41×32 地图内。"""
    return 0 <= x < WIDTH and 0 <= y < HEIGHT


def adjacent(ax: int, ay: int, bx: int, by: int) -> bool:
    """两点是否相邻（切比雪夫距离为 1，即 8 邻域）。"""
    return chebyshev(ax, ay, bx, by) == 1


def neighbors8(x: int, y: int) -> list[Cell]:
    """8 邻域（含界外过滤）。"""
    cells: list[Cell] = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            if in_bounds(x + dx, y + dy):
                cells.append((x + dx, y + dy))
    return cells


def day_of_round(round_no: int) -> int:
    """回合 → 天数（1..10）。白天 70 + 夜晚 60 = 130 回合/天（§4.2）。"""
    return (round_no - 1) // ROUNDS_PER_DAY + 1


def round_in_day(round_no: int) -> int:
    """当天内的回合序号（0..129；0..69 为白天，70..129 为夜晚）。"""
    return (round_no - 1) % ROUNDS_PER_DAY


def is_day_round(round_no: int) -> bool:
    """是否白天回合（§4.2）。"""
    return round_in_day(round_no) < DAY_ROUNDS


def is_night_round(round_no: int) -> bool:
    """是否夜晚回合（§4.2）。"""
    return not is_day_round(round_no)


def phase_of_round(round_no: int) -> str:
    """返回 "day"/"night"（供日志与请求构造）。"""
    return "day" if is_day_round(round_no) else "night"


def night_first_round_of(day: int) -> int:
    """第 day 天夜晚第一个回合号（§4.7.3 机器人出现时点）。"""
    return (day - 1) * ROUNDS_PER_DAY + DAY_ROUNDS + 1


def respawn_round_of(death_day: int) -> int:
    """死亡于第 death_day 天的角色复活回合（次日白天开始后 20 回合，§4.5.2）。"""
    return death_day * ROUNDS_PER_DAY + RESPAWN_DELAY_ROUNDS + 1


def ray_cells(x0: int, y0: int, x1: int, y1: int) -> list[Cell]:
    """Bresenham 直线（含两端点），按从起点到终点的顺序。

    用于加特林弹道与电磁炮能量穿透的路径判定（§4.5.4「攻击路径」）。
    """
    cells: list[Cell] = [(x0, y0)]
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    x, y = x0, y0
    while (x, y) != (x1, y1):
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x += sx
        if e2 <= dx:
            err += dx
            y += sy
        cells.append((x, y))
    return cells


def cone_ok(center: Cell, targets: Sequence[Cell], max_angle_deg: float = GATLING_CONE_DEG) -> bool:
    """所有目标是否落在以 center 为顶点的同一 ≤max_angle_deg 扇形内（§4.5.4）。

    判定：各目标相对 center 的方向角（atan2 欧氏角）中，补角（最大空隙）
    ≥ 360° - max_angle 时，全部方向可被一个 ≤max_angle 的扇形覆盖。
    """
    if len(targets) < 2:
        return True
    angles = sorted(math.atan2(ty - center[1], tx - center[0]) for tx, ty in targets)
    max_gap = 0.0
    for i, angle in enumerate(angles):
        next_angle = angles[(i + 1) % len(angles)]
        gap = next_angle - angle
        if i == len(angles) - 1:
            gap += 2.0 * math.pi
        max_gap = max(max_gap, gap)
    return 2.0 * math.pi - max_gap <= math.radians(max_angle_deg) + 1e-9


def weapon_range_at(kind: str, level: int) -> int | None:
    """武器当前等级的攻击距离；None = 全图（火箭 L3）。"""
    return WEAPON_RANGE[kind][level - 1]
