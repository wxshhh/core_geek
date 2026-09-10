"""跨回合历史累积器（工作包 9，方案 M2）：只增不减的历史 + 异步待决状态机。

WorldModel 每回合把观察委托给本器追加历史；snapshot() 生成不可变 HistoryState。
去重策略（文档化约定）：
- 新闻：与上一条完全相同时跳过（同一天新闻每回合重复下发）；
- 价格：与同矿种上一次记录价相同时跳过（便于峰值检测）；
- 敌方观测：与上一条完全相同时跳过（仅基地/围墙全局可见时高频重复）；
- 机器人波次：每回合有机器人即记录（波次时间线）。
"""

from __future__ import annotations

from collections.abc import Sequence

from future_war.models import Error, ErrorCode, RobotRole, Role, ShopItem, enum_to_str
from future_war.core.world_state import (
    EnemyObservation,
    HistoryState,
    NewsRecord,
    PendingKind,
    PendingRecord,
    PriceRecord,
    RobotCountRecord,
    TaskEvent,
    TaskEventKind,
    TreasureRecord,
)

_ROBOT_KINDS = ("smallRobot", "middleRobot", "largeRobot", "bossRobot")


class HistoryRecorder:
    """历史与待决状态的唯一持有者（列表内部可变，快照对外不可变）。"""

    def __init__(self) -> None:
        self._news: list[NewsRecord] = []
        self._prices: list[PriceRecord] = []
        self._robot_counts: list[RobotCountRecord] = []
        self._enemy_obs: list[EnemyObservation] = []
        self._task_events: list[TaskEvent] = []
        self._treasure: list[TreasureRecord] = []
        self._pending: list[PendingRecord] = []
        self._task_active = False

    # ------------------------------------------------------------ 追加

    def record_news(self, round_no: int, official: str, folk: str) -> None:
        if not official and not folk:
            return
        if self._news and (self._news[-1].official, self._news[-1].folk) == (
            official,
            folk,
        ):
            return
        self._news.append(NewsRecord(round_no, official, folk))

    def record_prices(self, round_no: int, items: Sequence[ShopItem]) -> None:
        for item in items:
            if self._last_price(item.name) != item.price:
                self._prices.append(PriceRecord(round_no, item.name, item.price))

    def record_robots(self, round_no: int, robots: tuple[RobotRole, ...]) -> None:
        if not robots:
            return
        counts = {kind: 0 for kind in _ROBOT_KINDS}
        for robot in robots:
            kind = enum_to_str(robot.roleType)
            if kind in counts:
                counts[kind] += 1
        self._robot_counts.append(
            RobotCountRecord(
                round=round_no,
                small=counts["smallRobot"],
                middle=counts["middleRobot"],
                large=counts["largeRobot"],
                boss=counts["bossRobot"],
                total=len(robots),
            )
        )

    def record_enemy_observation(self, round_no: int, roles: tuple[Role, ...]) -> None:
        if roles and (not self._enemy_obs or self._enemy_obs[-1].roles != roles):
            self._enemy_obs.append(EnemyObservation(round_no, roles))

    def record_task_lifecycle(
        self,
        round_no: int,
        phase_task: str,
        errors: Sequence[Error],
        treasure_result: int,
    ) -> None:
        """任务生命周期：phaseTask 空↔非空迁移、错误码 1/2、宝藏探测结果。"""
        if phase_task and not self._task_active:
            self._task_events.append(
                TaskEvent(round_no, TaskEventKind.ACCEPTED, detail=phase_task[:64])
            )
            self._task_active = True
        elif not phase_task and self._task_active:
            self._task_events.append(TaskEvent(round_no, TaskEventKind.ENDED))
            self._task_active = False
        for error in errors:
            if error.errorCode == ErrorCode.TASK_TIMEOUT:
                self._task_events.append(
                    TaskEvent(round_no, TaskEventKind.TIMEOUT, detail=error.description)
                )
            elif error.errorCode == ErrorCode.WRONG_ANSWER:
                self._task_events.append(
                    TaskEvent(round_no, TaskEventKind.WRONG_ANSWER, detail=error.description)
                )
        if treasure_result:
            self._treasure.append(TreasureRecord(round_no, treasure_result))

    # ------------------------------------------------------------ 待决

    def add_pending(self, round_no: int, kind: PendingKind | str, payload: str) -> None:
        self._pending.append(PendingRecord(round_no, kind, payload))

    def resolve_pending(self, kind: PendingKind | str, response: str) -> None:
        """最新一条未应答的待决记录 ← 本回合判题器应答（接口 §1.1 异步往返）。"""
        for index in range(len(self._pending) - 1, -1, -1):
            record = self._pending[index]
            if record.kind == kind and not record.resolved:
                self._pending[index] = PendingRecord(
                    record.round, record.kind, record.payload, response, True
                )
                return

    # ------------------------------------------------------------ 快照

    def snapshot(self) -> HistoryState:
        return HistoryState(
            news=tuple(self._news),
            prices=tuple(self._prices),
            robot_counts=tuple(self._robot_counts),
            enemy_observations=tuple(self._enemy_obs),
            task_events=tuple(self._task_events),
            treasure_results=tuple(self._treasure),
            pending=tuple(self._pending),
        )

    def _last_price(self, ore: str) -> int | None:
        for record in reversed(self._prices):
            if record.ore == ore:
                return record.price
        return None
