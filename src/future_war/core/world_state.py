"""世界状态：当前回合快照与跨回合历史记录（工作包 9，方案 M2 纯数据层）。

全部为 frozen/slots dataclass。历史元组只增不减（进程长期驻留跨 1300 回合）；
DynamicState 每回合重建为不可变快照，旧快照不受后续回合影响。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from future_war.models import Error, PlayerTask, RobotRole, Role, TeamType, Zone
from future_war.observability.events import DAY_LENGTH, Phase


def day_of(round_no: int) -> int:
    """回合号 → 第几天（1 起；白天 70 + 夜晚 60 = 130 回合/天，任务书 §4.2）。"""
    return (round_no - 1) // DAY_LENGTH + 1


@dataclass(frozen=True, slots=True)
class NewsRecord:
    """一条世界消息（官方消息 + 民间传闻，接口 §1.6）。"""

    round: int
    official: str
    folk: str


@dataclass(frozen=True, slots=True)
class PriceRecord:
    """小贩收购价的一次观测（接口 §1.1 vendorShopList）。"""

    round: int
    ore: str
    price: int


@dataclass(frozen=True, slots=True)
class RobotCountRecord:
    """一轮机器人波次观测（接口 §1.5，全图可见）。"""

    round: int
    small: int
    middle: int
    large: int
    boss: int
    total: int


@dataclass(frozen=True, slots=True)
class EnemyObservation:
    """敌方可见单位的一次观测（视野 4 + 基地/围墙全局可见，任务书 §4.3）。"""

    round: int
    roles: tuple[Role, ...]


class TaskEventKind(str, Enum):
    """任务生命周期事件类型（任务书 §五/§5）。"""

    ACCEPTED = "accepted"
    ENDED = "ended"
    WRONG_ANSWER = "wrong_answer"
    TIMEOUT = "timeout"


@dataclass(frozen=True, slots=True)
class TaskEvent:
    """任务相关事件：接取/结束/答案错误/超时（接口 §1.1/§1.7）。"""

    round: int
    kind: TaskEventKind | str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class TreasureRecord:
    """一次宝藏探测结果（接口 §1.1 lastSummonTreasureResult）。"""

    round: int
    result: int


class PendingKind(str, Enum):
    """异步往返的待决类型（接口 §1.1/§2.1）。"""

    LLM = "llm"
    SANDBOX = "sandbox"


@dataclass(frozen=True, slots=True)
class PendingRecord:
    """一条已发出、待判题器下回合应答的异步请求。"""

    round: int
    kind: PendingKind | str
    payload: str
    response: str = ""
    resolved: bool = False


@dataclass(frozen=True, slots=True)
class DynamicState:
    """当前回合动态状态快照（每回合重建）。"""

    round: int
    phase: Phase
    day: int
    team: TeamType | str
    own_roles: tuple[Role, ...]
    enemy_roles: tuple[Role, ...]
    robots: tuple[RobotRole, ...]
    gold: int
    total_score: int
    player_tasks: tuple[PlayerTask, ...]
    phase_task: str
    errors: tuple[Error, ...]
    action_results: Mapping[int, bool]
    llm_resp: str
    last_cmd_result: str
    last_treasure_result: int
    mines: tuple[Zone, ...]
    vendor_prices: Mapping[str, int]
    weapon_prices: Mapping[str, int]
    fallbacks: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class HistoryState:
    """跨回合累积历史（只增不减）。"""

    news: tuple[NewsRecord, ...] = ()
    prices: tuple[PriceRecord, ...] = ()
    robot_counts: tuple[RobotCountRecord, ...] = ()
    enemy_observations: tuple[EnemyObservation, ...] = ()
    task_events: tuple[TaskEvent, ...] = ()
    treasure_results: tuple[TreasureRecord, ...] = ()
    pending: tuple[PendingRecord, ...] = ()
