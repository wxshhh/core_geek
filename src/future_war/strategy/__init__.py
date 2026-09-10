"""策略层（M4/M6/M7/M8）：经济、战斗、建造等行为模块。

工作包 11 提供基础经济：工人采矿 / 贩卖 / 建造武器。每个模块消费只读
``WorldView``，产出一回合的 ``RoleCommand`` 映射（供规划器合并为 Response）。
"""

from future_war.strategy.economy import plan_economy

__all__ = ["plan_economy"]
