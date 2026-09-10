"""回合规划器（工作包 13，方案 M4）：把世界快照编排成三角色指令。

白天走经济（工人采矿/贩卖/建造），夜晚走防御（武器操控/攻击）。后续 Wave 会
在此叠加任务、寻宝、进攻等目标。仅用标准库。
"""

from __future__ import annotations

from future_war.config import Config
from future_war.models import RoleCommand
from future_war.core.world_view import WorldView
from future_war.strategy.combat import plan_defense
from future_war.strategy.economy import EconomyState, plan_economy


def plan_turn(
    view: WorldView,
    config: Config | None = None,
    economy_state: EconomyState | None = None,
) -> dict[int, RoleCommand]:
    """按昼夜选择行为模块，返回本回合全角色指令映射。"""
    if view.is_night():
        return plan_defense(view, config)
    return plan_economy(view, config, economy_state)
