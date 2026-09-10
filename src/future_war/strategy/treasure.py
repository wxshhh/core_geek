"""长上下文寻宝（工作包 20，方案 M10）：累积民间传闻 → 推断宝藏三要素。

民间传闻为自由文本（任务书 §5.2），跨天累积后推断**地点/物品/开启时间**。
本模块做保守的规则解析与探测：

- ``parse_clues``：抽取方位（东/西/南/北）、所需任务用品数量、是否含时间线索；
- ``candidate_cell``：把方位映射到地图对应一侧的候选格（地点未知，只能猜侧）；
- ``plan_treasure``：开拓者携够任务用品且到达候选格 → ``summonTreasure``；否则朝
  候选格移动。用 ``lastSummonTreasureResult``（2=地点/时间错，3=物品错）反馈修正，
  并受 ``tasks.treasure_probe_cap`` 限制探测次数（合法召唤即消耗物品，§2.3）。

宝藏机制在本地模拟器中未实现（sim/README stub），故以合成视图单测覆盖。仅用标准库。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from future_war.config import Config
from future_war.models import Action, Pos, RoleCommand
from future_war.core.nav import plan_move
from future_war.core.world_map import chebyshev
from future_war.core.world_view import WorldView

TASK_ITEMS: Final = frozenset(
    {
        "AcientTablet",
        "StarSand",
        "FlameBreath",
        "FrostPotion",
        "ThornAmulet",
        "IronWhistle",
    }
)
_DIRECTIONS: Final = (("西", "west"), ("东", "east"), ("南", "south"), ("北", "north"))
_ITEM_RE: Final = re.compile(r"([一二三四五六七八九十\d]+)\s*[钥钥匙个件]")
_CN_NUM: Final = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}
_TIMING_WORDS: Final = ("水位", "月", "夜", "时", "晨", "黄昏")
DEFAULT_ITEM_COUNT: Final = 1


@dataclass(frozen=True, slots=True)
class TreasureClues:
    """从传闻中解析出的宝藏线索（方位/物品数/时间线索）。"""

    direction: str | None
    item_count: int
    timing_hint: bool


@dataclass
class TreasureState:
    """跨回合寻宝状态：已探测次数 + 上次结果码。"""

    probes: int = 0
    last_result: int = 0


def parse_clues(text: str) -> TreasureClues:
    """解析民间传闻：方位、所需物品数、是否含时间线索。"""
    direction = next((name for kw, name in _DIRECTIONS if kw in text), None)
    count = DEFAULT_ITEM_COUNT
    match = _ITEM_RE.search(text)
    if match is not None:
        count = _parse_count(match.group(1))
    timing = any(word in text for word in _TIMING_WORDS)
    return TreasureClues(direction=direction, item_count=count, timing_hint=timing)


def candidate_cell(view: WorldView, direction: str) -> Pos | None:
    """把方位映射到地图对应一侧的候选格（地图中部靠该侧）。"""
    if direction not in {"west", "east", "south", "north"}:
        return None
    width = view.static_map.width
    height = view.static_map.height
    if direction == "west":
        return Pos(1, height // 2)
    if direction == "east":
        return Pos(width - 2, height // 2)
    if direction == "south":
        return Pos(width // 2, 1)
    return Pos(width // 2, height - 2)


def plan_treasure(
    view: WorldView, config: Config | None = None, state: TreasureState | None = None
) -> dict[int, RoleCommand]:
    """开拓者的寻宝指令：够物品且到位则召唤，否则朝候选格移动。"""
    if not _enabled(config):
        return {}
    state = state if state is not None else TreasureState()
    state.last_result = view.last_treasure_result()
    clues = parse_clues(view.folk_legend_text())
    if clues.direction is None or state.probes >= _probe_cap(config):
        return {}
    pioneer = view.own_pioneer()
    if not pioneer:
        return {}
    unit = pioneer[0]
    target = candidate_cell(view, clues.direction)
    if target is None:
        return {}
    items = _task_items(unit)
    if chebyshev(unit.pos, target) <= 1 and len(items) >= clues.item_count:
        state.probes += 1
        return {
            unit.id: RoleCommand(
                action=Action.SUMMON_TREASURE,
                targetPos=(target,),
                item=tuple(items[: clues.item_count]),
            )
        }
    step = plan_move(view, unit.id, target)
    if step is None:
        return {}
    return {unit.id: RoleCommand(action=Action.MOVE, targetPos=(step,))}


def _parse_count(raw: str) -> int:
    if raw.isdigit():
        return max(1, int(raw))
    return _CN_NUM.get(raw, DEFAULT_ITEM_COUNT)


def _task_items(unit) -> list[str]:
    return [item for item in unit.backpack if item in TASK_ITEMS]


def _enabled(config: Config | None) -> bool:
    value = config.get("tasks.treasure_enabled") if config is not None else None
    return value is not False


def _probe_cap(config: Config | None) -> int:
    value = config.get("tasks.treasure_probe_cap") if config is not None else None
    return value if isinstance(value, int) and not isinstance(value, bool) else 4
