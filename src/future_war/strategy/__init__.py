"""策略层（M4/M6/M7/M8）：经济、战斗、规划与 Bot 入口。

工作包 11 基础经济（``plan_economy``）、工作包 12 基础防御（``plan_defense``）、
工作包 13 回合规划与 Bot 入口（``plan_turn`` / ``StrategyBot``）。每个模块消费
只读 ``WorldView``，产出一回合的 ``RoleCommand`` 映射。仅用标准库。
"""

from future_war.strategy.bot import StrategyBot
from future_war.strategy.combat import plan_defense
from future_war.strategy.economy import plan_economy
from future_war.strategy.planner import plan_turn

__all__ = ["StrategyBot", "plan_defense", "plan_economy", "plan_turn"]
