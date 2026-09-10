"""Bot 策略入口（工作包 13）：把判题 Request 变成 Response。

``StrategyBot`` 持有跨回合 ``WorldModel``，每回合合并请求 → 规划 → 返回指令。
既可直接被本地模拟器驱动（进程内），也可被真实 HTTP 服务调用。仅用标准库。
"""

from __future__ import annotations

from future_war.config import Config
from future_war.models import Request, Response
from future_war.core.world import WorldModel
from future_war.strategy.economy import EconomyState
from future_war.strategy.opponent import OpponentModel
from future_war.strategy.planner import plan_turn
from future_war.strategy.treasure import TreasureState


class StrategyBot:
    """跨回合驻留的策略 Bot（长期进程内单例）。"""

    def __init__(self, config: Config | None = None) -> None:
        self._model = WorldModel()
        self._config = config
        self._economy = EconomyState()
        self._opponent = OpponentModel()
        self._treasure = TreasureState()

    def __call__(self, request: Request) -> Response:
        view = self._model.apply_round(request)
        self._opponent.observe(view)
        return Response(
            roleCommandMap=plan_turn(view, self._config, self._economy, self._treasure)
        )
