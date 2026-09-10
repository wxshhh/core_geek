"""LLM 管理器（工作包 23/25，方案 M12）：配额 + 异步关联 + 免费窗口。

接口 §1.7：每游戏日 3 次 LLM，超出报 errorCode 5；**自进化任务期间不限次且不
计入**。``prompt`` 本回合发出、``llmResp`` 下回合返回（异步一回合往返，§2.1），
故本模块以状态机关联「已发出 / 待响应」。

免费窗口利用（工作包 25）：``phase_task`` 非空即免费，期间发出的请求不计数。
仅用标准库。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from future_war.config import Config
from future_war.core.world_view import WorldView

DEFAULT_QUOTA: Final = 3


@dataclass
class LLMManager:
    """跨回合 LLM 配额与异步请求状态。"""

    daily_quota: int = DEFAULT_QUOTA
    day: int = 0
    used: int = 0
    pending: str | None = None
    last_response: str = ""

    @classmethod
    def from_config(cls, config: Config | None) -> LLMManager:
        value = config.get("llm.daily_quota") if config is not None else None
        quota = value if isinstance(value, int) and not isinstance(value, bool) else DEFAULT_QUOTA
        return cls(daily_quota=max(0, quota))

    def sync(self, view: WorldView) -> None:
        """每回合开始：跨日重置配额，并关联上回合发出的 prompt 与 llmResp。"""
        if view.day != self.day:
            self.day = view.day
            self.used = 0
        if self.pending is not None:
            response = view.llm_resp()
            if response:
                self.last_response = response
                self.pending = None

    def free_window(self, view: WorldView) -> bool:
        """自进化任务期间 LLM 免费（§1.7）。"""
        return bool(view.phase_task())

    def remaining(self, view: WorldView) -> int:
        return max(0, self.daily_quota - self.used)

    def can_call(self, view: WorldView) -> bool:
        return self.free_window(view) or self.remaining(view) > 0

    def note_sent(self, prompt: str, view: WorldView) -> bool:
        """登记一次 prompt 发送；配额不足返回 False 且不发送。"""
        if not prompt or not self.can_call(view):
            return False
        if not self.free_window(view):
            self.used += 1
        self.pending = prompt
        return True
