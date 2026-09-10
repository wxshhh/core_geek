"""策略层（M4/M6/M7/M8）：经济、战斗、建造等行为模块。

工作包 11 基础经济（``plan_economy``）、工作包 12 基础防御（``plan_defense``）。
每个模块消费只读 ``WorldView``，产出一回合的 ``RoleCommand`` 映射，供规划器
合并为 Response。仅用标准库。
"""

from future_war.strategy.combat import plan_defense
from future_war.strategy.economy import plan_economy

__all__ = ["plan_defense", "plan_economy"]
