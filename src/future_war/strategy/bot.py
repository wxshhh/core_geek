"""Bot 策略入口（工作包 13）：把判题 Request 变成 Response。

``StrategyBot`` 持有跨回合 ``WorldModel``，每回合合并请求 → 规划 → 返回指令。
既可直接被本地模拟器驱动（进程内），也可被真实 HTTP 服务调用。仅用标准库。
"""

from __future__ import annotations

from future_war.config import Config
from future_war.models import Request, Response
from future_war.core.world import WorldModel
from future_war.strategy.consumables import ConsumableState
from future_war.strategy.economy import EconomyState
from future_war.strategy.llm_manager import LLMManager
from future_war.strategy.offense import OffenseState
from future_war.strategy.opponent import OpponentModel
from future_war.strategy.planner import plan_turn
from future_war.strategy.task_agent import TaskState
from future_war.strategy.treasure import TreasureState


class StrategyBot:
    """跨回合驻留的策略 Bot（长期进程内单例）。"""

    def __init__(self, config: Config | None = None) -> None:
        self._model = WorldModel()
        self._config = config
        self._economy = EconomyState()
        self._opponent = OpponentModel()
        self._treasure = TreasureState()
        self._task = TaskState()
        self._offense = OffenseState()
        self._consumables = ConsumableState()
        self._llm = LLMManager.from_config(config)
        self.last_notes: tuple[str, ...] = ()  # 上回合决策摘要（供 server 记 D-02）

    def __call__(self, request: Request) -> Response:
        view = self._model.apply_round(request)
        self._opponent.observe(view)
        self._llm.sync(view)
        plan = plan_turn(
            view,
            self._config,
            self._economy,
            self._treasure,
            self._task,
            self._offense,
            self._consumables,
        )
        self.last_notes = plan.notes
        prompt = plan.prompt
        if prompt and not self._llm.note_sent(prompt, view):
            prompt = ""  # 配额不足则不发送（避免 errorCode 5，§1.7）
        return Response(
            roleCommandMap=plan.commands,
            prompt=prompt,
            executeCmd=plan.execute_cmd,
        )
