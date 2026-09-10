"""推理类任务（工作包 19，方案 M11）：官方新闻 → 价格/可采性预测。

官方消息为**自由文本**（任务书 §5.1），只做**方向性**预测（规则未给量级）：

- 出现「停工/塌方/停产/停采」等停采词且点名某矿 → 该矿短期不可采、价格上行；
- ``unavailable_days`` 由文本中的「N 天」估计，缺省 2 天。

预测结果供经济模块调整（预囤涨价矿、避开停采矿）。新闻/价格波动在本地模拟器
中未实现（sim/README stub），故本模块以样例文本单测覆盖。仅用标准库。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from future_war.core.world_view import WorldView

_ORE_KEYWORDS: Final = (
    ("铁矿", "iron"),
    ("石矿", "stone"),
    ("铜矿", "copper"),
    ("铁", "iron"),
    ("铜", "copper"),
    ("石", "stone"),
)
_STOP_WORDS: Final = ("停工", "塌方", "停产", "停采", "无法采集", "关闭")
_DAYS_RE: Final = re.compile(r"(\d+)\s*天")
_DEFAULT_UNAVAILABLE_DAYS: Final = 2


@dataclass(frozen=True, slots=True)
class NewsEffect:
    """一条新闻对某矿的影响（方向性）。"""

    ore: str
    direction: str  # "up" / "flat"
    unavailable_days: int


def parse_official_news(text: str) -> tuple[NewsEffect, ...]:
    """把官方新闻解析为各矿的方向性影响；无停采信息返回空。"""
    if not text or "无重大新闻" in text:
        return ()
    if not any(word in text for word in _STOP_WORDS):
        return ()
    days = _estimate_days(text)
    effects: list[NewsEffect] = []
    for keyword, ore in _ORE_KEYWORDS:
        if keyword in text and ore not in {e.ore for e in effects}:
            effects.append(NewsEffect(ore=ore, direction="up", unavailable_days=days))
    return tuple(effects)


def unavailable_ores(text: str) -> frozenset[str]:
    """当前新闻中短期不可采的矿种集合。"""
    return frozenset(effect.ore for effect in parse_official_news(text))


def forecast_directions(view: WorldView) -> dict[str, str]:
    """基于累计新闻给出各矿价格方向（"up"/"flat"）。"""
    directions: dict[str, str] = {}
    for effect in parse_official_news(view.official_news_text()):
        directions[effect.ore] = effect.direction
    return directions


def _estimate_days(text: str) -> int:
    match = _DAYS_RE.search(text)
    if match is None:
        return _DEFAULT_UNAVAILABLE_DAYS
    return max(1, int(match.group(1)))
