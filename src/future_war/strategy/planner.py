"""回合规划器（工作包 13/21/22，方案 M4）：把世界快照编排成角色指令。

白天走经济（工人采矿/贩卖/建造）+ 开拓者任务/寻宝；夜晚走防御（武器操控/攻击）。
产出 ``TurnPlan``（指令 + 可选 LLM prompt / 沙盒命令），供 Bot 组装 Response。
仅用标准库。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from future_war.config import Config
from future_war.models import RoleCommand
from future_war.core.world_view import WorldView
from future_war.strategy.combat import plan_defense
from future_war.strategy.economy import EconomyState, plan_economy
from future_war.strategy.offense import OffenseState, plan_offense
from future_war.strategy.task_agent import TaskState, plan_task
from future_war.strategy.treasure import TreasureState, plan_treasure


@dataclass(frozen=True, slots=True)
class TurnPlan:
    """一回合产出：角色指令 + 可选的 LLM prompt / 沙盒命令（接口 §2.1）。"""

    commands: dict[int, RoleCommand] = field(default_factory=dict)
    prompt: str = ""
    execute_cmd: str = ""


def plan_turn(
    view: WorldView,
    config: Config | None = None,
    economy_state: EconomyState | None = None,
    treasure_state: TreasureState | None = None,
    task_state: TaskState | None = None,
    offense_state: OffenseState | None = None,
) -> TurnPlan:
    """按昼夜选择行为模块，返回本回合 TurnPlan。"""
    if view.is_night():
        commands = plan_defense(view, config)
        commands.update(
            plan_offense(view, config, frozenset(commands), offense_state)
        )
        return TurnPlan(commands=commands)
    commands = plan_economy(view, config, economy_state)
    task = plan_task(view, config, task_state)
    commands.update(task.commands)
    if not task.commands:
        commands.update(plan_treasure(view, config, treasure_state))
    return TurnPlan(commands=commands, prompt=task.prompt, execute_cmd=task.execute_cmd)
