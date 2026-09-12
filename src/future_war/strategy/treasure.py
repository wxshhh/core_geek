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
from future_war.models import Action, Pos, Role, RoleCommand
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
TASK_ITEM_COST: Final = 15  # §4.6.3：任务用品单价 15 金


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
    if _must_return_home(view, config):
        return {}  # 黄昏/夜晚必须回防操控武器，而不是跑去地图另一头挖宝
    state = state if state is not None else TreasureState()
    state.last_result = view.last_treasure_result()
    clues = parse_clues(view.folk_legend_text())
    if clues.direction is None or state.probes >= _probe_cap(config):
        return {}
    pioneer = view.own_pioneer()
    if not pioneer:
        return {}
    unit = pioneer[0]
    items = _task_items(unit)
    if len(items) < clues.item_count:
        # 物品不够：先补货。经济工人在同一回合只能买一件（一条指令），所以
        # 由开拓者自己顺路买 —— 但绝不在任务期间走开（离开任务点 = 任务结束，§五）。
        buying = _plan_item_purchase(view, unit, clues.item_count)
        if buying is not None:
            return buying
        # 买不起也不要去：跑到祭坛也开不了，白占开拓者的白天（还要回防）。
        if view.gold() < TASK_ITEM_COST:
            return {}
    target = candidate_cell(view, clues.direction)
    if target is None:
        return {}
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


def _plan_item_purchase(
    view: WorldView, unit: Role, needed: int
) -> dict[int, RoleCommand] | None:
    """补买任务用品：到商店旁 → 买；否则朝商店移动。金币不足则放弃（不浪费时间）。"""
    if view.phase_task():
        return None  # 任务进行中：离开任务点即失败（§五）
    shop = view.weapon_shop_pos()
    if shop is None:
        return None
    missing = needed - len(_task_items(unit))
    if missing <= 0:
        return None
    if view.gold() < TASK_ITEM_COST:
        return None
    if chebyshev(unit.pos, shop) <= 1:
        item = _cheapest_item(view)
        if item is None:
            return None  # 清单还没下发：不要发一条注定失败的 buy
        return {unit.id: RoleCommand(action=Action.BUY, name=item, num=1)}
    step = plan_move(view, unit.id, shop)
    if step is None:
        return None
    return {unit.id: RoleCommand(action=Action.MOVE, targetPos=(step,))}


def _cheapest_item(view: WorldView) -> str | None:
    """从武器商店清单里挑最便宜的任务用品；没有可用报价返回 None。

    真机清单随地图变化（§4.6.3「任务用品列表不固定」），所以只能按当轮报价挑，
    绝不能硬编码某一种物品名。
    """
    quoted = [(price, name) for name, price in _shop_items(view) if name in TASK_ITEMS]
    if not quoted:
        return None
    return min(quoted)[1]


def _shop_items(view: WorldView) -> tuple[tuple[str, int], ...]:
    """当前商店报价（``WorldView`` 只存了名称→价格，这里按固定顺序还原）。"""
    names = (
        "AcientTablet", "StarSand", "FlameBreath",
        "FrostPotion", "ThornAmulet", "IronWhistle",
    )
    return tuple(
        (name, price)
        for name in names
        if (price := view.weapon_price(name)) is not None
    )


def _parse_count(raw: str) -> int:
    if raw.isdigit():
        return max(1, int(raw))
    return _CN_NUM.get(raw, DEFAULT_ITEM_COUNT)


def _task_items(unit) -> list[str]:
    return [item for item in unit.backpack if item in TASK_ITEMS]


def _enabled(config: Config | None) -> bool:
    value = config.get("tasks.treasure_enabled") if config is not None else None
    return value is not False


def _must_return_home(view: WorldView, config: Config | None) -> bool:
    """是否必须回防：夜晚，或白天已进入黄昏就位阶段。"""
    if view.is_night():
        return True
    threshold = config.get("economy.dusk_return") if config is not None else None
    if not isinstance(threshold, int) or isinstance(threshold, bool):
        threshold = 40
    return (view.round_no - 1) % 130 >= threshold


def _probe_cap(config: Config | None) -> int:
    value = config.get("tasks.treasure_probe_cap") if config is not None else None
    return value if isinstance(value, int) and not isinstance(value, bool) else 4
