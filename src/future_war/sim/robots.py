"""机器人进攻浪潮与行为（任务书 §4.7）。

官方任务书未定义机器人出生位置、数量曲线与寻路规则（方案 §一 Risk 列明），
本模块采用**文档化简化**（详见 sim/README.md）：

- 每夜第 1 回合（``night_first_round_of``）统一生成，次日早第 1 回合统一清除（§4.7.3）。
- 数量曲线（自定）：第 N 天夜晚：小 = 2+N、中 = max(0, N-1)、大 = max(0, N-3)、
  BOSS = 1（N≥6）。召唤令叠加在基础浪潮之上（§4.7.3）。
- 出生：从目标基地一侧的地图边缘候选格中随机选（种子确定）。
- 行为：每夜逐只朝目标基地贪心走一格；下一步目标被单位占据则**攻击该阻挡单位**
  （§4.7.3「攻击阻挡其移动的单位」）；被中立/矿区阻挡则绕行。
- 机器人不互相阻挡（简化）；机器人先于角色移动（§4.4 未规定次序的补充，README）。
"""

from __future__ import annotations

from future_war.sim.rules import (
    Cell,
    ROBOT_STATS,
    ROBOT_TYPES,
    day_of_round,
    in_bounds,
)
from future_war.sim.world import DamageEvent, Robot, Unit, World


def wave_composition(day: int) -> dict[str, int]:
    """第 day 天夜晚的基础机器人数量（自定曲线，见 README）。"""
    return {
        "smallRobot": 2 + day,
        "middleRobot": max(0, day - 1),
        "largeRobot": max(0, day - 3),
        "bossRobot": 1 if day >= 6 else 0,
    }


def spawn_night_wave(world: World) -> None:
    """夜晚第 1 回合：按基础浪潮 + 召唤令生成机器人（§4.7.3）。"""
    day = day_of_round(world.round_no)
    for target_team in ("challenger", "defender"):
        counts = wave_composition(day)
        extra = world.summons.get(target_team, {})
        spawns = world.layout.spawn_cells[target_team]
        for kind in ROBOT_TYPES:
            total = counts[kind] + extra.get(kind, 0)
            for _ in range(total):
                sx, sy = world.rng.choice(spawns)
                rid = world.next_robot_id()
                world.robots[rid] = Robot(
                    rid=rid,
                    kind=kind,
                    x=sx,
                    y=sy,
                    hp=ROBOT_STATS[kind]["hp"],
                    target_team=target_team,
                )
    world.summons.clear()  # 召唤令只作用于下一个夜晚


def clear_robots(world: World) -> None:
    """次日早第 1 回合：残余机器人自动清除（§4.7.3）。"""
    world.robots.clear()


def move_robots(world: World) -> None:
    """逐只机器人行动：贪心寻路 + 攻击阻挡单位（伤害回合末结算）。

    §4.6.3：眩晕法宝命中的机器人眩晕 5 回合，期间**不动也不攻击**
    （结算优先级 > 机器人移动）。
    """
    for rid in sorted(world.robots):
        robot = world.robots[rid]
        if robot.dizzy_rounds > 0:
            robot.dizzy_rounds -= 1
            continue
        _step_robot(world, robot)


def _step_robot(world: World, rob: Robot) -> None:
    bx, by = world.layout.base_pos[rob.target_team]
    for nx, ny in _ordered_steps(rob.x, rob.y, bx, by):
        if not in_bounds(nx, ny):
            continue
        blocker = world.any_unit_at(nx, ny)
        if blocker is not None:
            _attack_blocker(world, rob, blocker[0], blocker[1])
            return
        if (nx, ny) in world.layout.fixed_neutrals or (nx, ny) in world.mines:
            continue  # 中立/矿区阻挡：绕行（§4.1）
        rob.x, rob.y = nx, ny
        return
    # 所有方向被堵：攻击相邻单位（若有），否则原地停留
    for nx, ny in _ordered_steps(rob.x, rob.y, bx, by):
        if not in_bounds(nx, ny):
            continue
        blocker = world.any_unit_at(nx, ny)
        if blocker is not None:
            _attack_blocker(world, rob, blocker[0], blocker[1])
            return


def _attack_blocker(world: World, rob: Robot, team_name: str, unit: Unit) -> None:
    """机器人攻击阻挡其移动的单位（§4.7.3）；伤害回合末统一结算。"""
    world.pending_damage.append(
        DamageEvent(
            source_team="robot",
            amount=ROBOT_STATS[rob.kind]["atk"],
            unit_ref=(team_name, unit.uid),
        )
    )


def _ordered_steps(x: int, y: int, bx: int, by: int) -> list[Cell]:
    """朝向 (bx,by) 的候选步序：主对角线 → 主轴 → 其余按距基地远近排序。"""
    dx = _sign(bx - x)
    dy = _sign(by - y)
    primary: list[Cell] = []
    if dx and dy:
        primary.append((x + dx, y + dy))
    if dx:
        primary.append((x + dx, y))
    if dy:
        primary.append((x, y + dy))
    rest: list[Cell] = []
    for sdx in (-1, 0, 1):
        for sdy in (-1, 0, 1):
            if sdx == 0 and sdy == 0:
                continue
            cell = (x + sdx, y + sdy)
            if cell in primary:
                continue
            rest.append(cell)
    rest.sort(key=lambda cell: max(abs(cell[0] - bx), abs(cell[1] - by)))
    return primary + rest


def _sign(value: int) -> int:
    return (value > 0) - (value < 0)
