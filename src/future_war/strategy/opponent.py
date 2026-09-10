"""对手建模与侦察适配（工作包 17，方案 M3）。

跨回合记录**敌方可见单位轨迹**与**机器人出生格**，用于：

- ``threat``：敌方机动单位逼近我方基地 → 推家威胁（对应事件码 O-03 的判定）；
- ``spawn_centroid``：由观测到的机器人出生格估计来袭方向，供布局适配
  （任务书 §4.7.3 未定义出生点，只能观测学习）。

敌方非建筑单位仅在视野 4 内可见（§4.3），故轨迹天然稀疏；本模块只做保守记录。
仅用标准库。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from future_war.models import Pos
from future_war.core.world_map import chebyshev
from future_war.core.world_view import WorldView

Cell = tuple[int, int]
THREAT_RANGE = 6


@dataclass
class OpponentModel:
    """跨回合对手观测：轨迹 + 机器人出生格。"""

    tracks: dict[int, list[Cell]] = field(default_factory=dict)
    spawn_cells: dict[int, set[Cell]] = field(default_factory=dict)

    def observe(self, view: WorldView) -> None:
        """记录本回合敌方机动单位位置与夜晚机器人出生格。"""
        for role in view.enemy_mobile_units():
            self.tracks.setdefault(role.id, []).append((role.pos.x, role.pos.y))
        if view.is_night() and view.robot_total() > 0:
            cells = self.spawn_cells.setdefault(view.day, set())
            for robot in view.robots():
                cells.add((robot.pos.x, robot.pos.y))

    def threat(self, view: WorldView) -> bool:
        """敌方机动单位是否逼近我方基地（推家威胁）。"""
        base = view.base_pos()
        if base is None:
            return False
        return any(
            chebyshev(role.pos, base) <= THREAT_RANGE
            for role in view.enemy_mobile_units()
        )

    def spawn_centroid(self) -> Pos | None:
        """已观测机器人出生格的质心；无观测返回 None。"""
        cells = [cell for night in self.spawn_cells.values() for cell in night]
        if not cells:
            return None
        return Pos(
            round(sum(x for x, _ in cells) / len(cells)),
            round(sum(y for _, y in cells) / len(cells)),
        )

    def observed_nights(self) -> tuple[int, ...]:
        return tuple(sorted(self.spawn_cells))
