"""Bot 规格注册表：把 CLI 字符串解析成可调用的 Bot（工作包 3 演示用）。

支持：``scripted:defense`` / ``scripted:idle`` / ``idle`` / ``http:URL``。
HTTP Bot（真实服务集成）见 protocol.HttpBot。
"""

from __future__ import annotations

from typing import Final

from future_war.sim.bots import IdleBot, ScriptedBot
from future_war.sim.protocol import BotFn, HttpBot


class BotSpecError(Exception):
    """run_sim CLI 的 Bot 规格错误（用法错误，退出码 2）。"""

    def __init__(self, spec: str, hint: str) -> None:
        super().__init__(f"unknown bot spec {spec!r} ({hint})")
        self.spec = spec
        self.hint = hint


SCRIPTED_BOTS: Final = {"idle": IdleBot, "defense": ScriptedBot}


def bot_from_spec(spec: str) -> BotFn:
    """按规格构造 Bot：scripted:defense / scripted:idle / idle / http:URL。"""
    kind, _, arg = spec.partition(":")
    match kind:
        case "scripted":
            bot_class = SCRIPTED_BOTS.get(arg or "defense", ScriptedBot)
            return bot_class()
        case "idle":
            return IdleBot()
        case "http":
            if not arg:
                raise BotSpecError(spec, "http bot spec needs a url: http:http://127.0.0.1:8080")
            return HttpBot(arg)
        case unreachable:
            raise BotSpecError(spec, "use scripted:defense | scripted:idle | idle | http:URL")
