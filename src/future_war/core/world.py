"""世界模型与跨回合状态存储（工作包 9，方案 M2）。

把每回合 Request 合并进长期驻留状态（进程跨 1300 回合存活，方案 §二）：
- 静态：可建造区推断与中立区域分类（world_map）；
- 动态：己方/敌方/机器人/金币/价格当前快照（world_state.DynamicState）；
- 历史：新闻、价格序列、机器人波次、敌方观测、任务事件、宝藏探测、
  LLM/沙盒待决（只增不减，world_history.HistoryRecorder）；
- 健壮性：字段缺失回退（world_merge）并记录 fallback。

apply_round 任何异常被捕获 → X-01 + 上一状态降级视图，绝不抛异常（任务书 §八）。
"""

from __future__ import annotations

from future_war.config import Config
from future_war.models import (
    Error,
    MapInfo,
    PlayerTask,
    Pos,
    Request,
    RobotRole,
    TeamType,
    Zone,
    enum_to_str,
)
from future_war.observability import EventCode, StructuredLogger, phase_of
from future_war.core.world_history import HistoryRecorder
from future_war.core.world_map import (
    BuildAttempt,
    BuildableKind,
    StaticMap,
    apply_build_feedback,
    build_static_map,
    inference_from_config,
)
from future_war.core.world_merge import FieldCache, has_field, merge_round
from future_war.core.world_state import DynamicState, PendingKind, day_of
from future_war.core.world_view import WorldView

_MINE_KINDS = ("stone", "iron", "copper")


class WorldModel:
    """跨回合世界状态（服务线程单线程调用，方案 M2）。"""

    def __init__(
        self,
        *,
        config: Config | None = None,
        logger: StructuredLogger | None = None,
        blue_radius: int | None = None,
        yellow_radius: int | None = None,
    ) -> None:
        self._logger = logger
        self._inference = inference_from_config(config, blue_radius, yellow_radius)
        self._history = HistoryRecorder()
        self._cache = FieldCache()
        self._round = 0
        self._robots: tuple[RobotRole, ...] = ()
        self._static: StaticMap | None = None
        self._vendor_prices: dict[str, int] = {}
        self._weapon_prices: dict[str, int] = {}
        self._attempts: list[BuildAttempt] = []
        self._refuted_weapon: set[Pos] = set()
        self._refuted_wall: set[Pos] = set()
        self._player_tasks: tuple[PlayerTask, ...] = ()
        self._phase_task = ""
        self._errors: tuple[Error, ...] = ()
        self._action_results: dict[int, bool] = {}
        self._llm_resp = ""
        self._last_cmd = ""
        self._last_treasure = 0
        self._last_fallbacks: tuple[str, ...] = ()

    @property
    def round_no(self) -> int:
        """已处理的最后一个回合号（未处理任何回合时为 0）。"""
        return self._round

    def apply_round(self, request: Request) -> WorldView:
        """合并一回合 Request 并返回当前快照；绝不抛异常（任务书 §八）。"""
        try:
            return self._apply(request)
        except Exception as exc:  # noqa: BLE001 — 边界兜底：进程存活优先
            self._emit(
                EventCode.X_01,
                f"world model internal error: {exc!r}",
                round_no=request.roundNo,
            )
            return self._snapshot(
                request.roundNo, (f"internal_error:{type(exc).__name__}",)
            )

    def view(self) -> WorldView:
        """当前回合快照（与最近一次 apply_round 返回值等价）。"""
        return self._snapshot(self._round, self._last_fallbacks)

    def note_response_sent(self, *, prompt: str = "", execute_cmd: str = "") -> None:
        """记录本回合发出的异步请求；下回合 llmResp/lastCmdResult 将应答。

        规划器在 apply_round 之后调用（prompt/executeCmd 属响应内容，接口 §2.1）。
        """
        if prompt:
            self._history.add_pending(self._round, PendingKind.LLM, prompt)
        if execute_cmd:
            self._history.add_pending(self._round, PendingKind.SANDBOX, execute_cmd)

    def record_build_attempt(
        self, pos: Pos, kind: BuildableKind | str, role_id: int
    ) -> None:
        """记录一次建造尝试；下回合判题器反馈非法时证伪该候选格。"""
        resolved = kind if isinstance(kind, BuildableKind) else BuildableKind(kind)
        self._attempts.append(BuildAttempt(self._round, pos, resolved, role_id))

    # ------------------------------------------------------------ 内部

    def _apply(self, request: Request) -> WorldView:
        round_no = request.roundNo
        raw = request.raw or request.to_dict()
        fallbacks: list[str] = []

        previous_team = self._cache.team
        merge_round(self._cache, request, fallbacks)
        if previous_team is not None and self._cache.team != previous_team:
            self._refuted_weapon.clear()  # 换边后证伪集随旧地图失效
            self._refuted_wall.clear()

        self._apply_build_feedback(request, round_no)
        self._rebuild_static_map(request, self._cache.team)

        if has_field(raw, "vendorShopList"):
            for item in request.vendorShopList:
                self._vendor_prices[item.name] = item.price
            self._history.record_prices(round_no, request.vendorShopList)
        else:
            fallbacks.append("vendorShopList missing → previous prices")

        if has_field(raw, "weaponShopList"):
            self._weapon_prices = {i.name: i.price for i in request.weaponShopList}
        else:
            fallbacks.append("weaponShopList missing → previous prices")

        if has_field(raw, "robot", "roles"):
            self._robots = request.robot.roles
            self._history.record_robots(round_no, self._robots)
        else:
            fallbacks.append("robot.roles missing → previous")

        news = request.worldNews
        self._history.record_news(round_no, news.officialNews, news.folkLegends)
        self._history.record_enemy_observation(round_no, self._cache.enemy_roles)
        self._history.record_task_lifecycle(
            round_no, request.phaseTask, request.errors, request.lastSummonTreasureResult
        )
        if request.llmResp:
            self._history.resolve_pending(PendingKind.LLM, request.llmResp)
        if request.lastCmdResult:
            self._history.resolve_pending(PendingKind.SANDBOX, request.lastCmdResult)

        self._player_tasks = request.teamOur.playerTasks
        self._phase_task = request.phaseTask
        self._errors = request.errors
        self._action_results = dict(request.lastRoundRoleActionResults)
        self._llm_resp = request.llmResp
        self._last_cmd = request.lastCmdResult
        self._last_treasure = request.lastSummonTreasureResult
        self._last_fallbacks = tuple(fallbacks)
        self._round = round_no

        if fallbacks:
            self._emit(
                EventCode.X_05,
                "world state fallback applied",
                round_no=round_no,
                fields=",".join(fallbacks),
            )
        return self._snapshot(round_no, self._last_fallbacks)

    def _apply_build_feedback(self, request: Request, round_no: int) -> None:
        """上回合建造尝试的合法性反馈 → 证伪可建造候选格（估算修正闭环）。"""
        self._attempts, refuted_weapon, refuted_wall = apply_build_feedback(
            self._attempts, round_no, request.lastRoundRoleActionResults
        )
        self._refuted_weapon.update(refuted_weapon)
        self._refuted_wall.update(refuted_wall)

    def _rebuild_static_map(self, request: Request, team: TeamType | str | None) -> None:
        inference = self._inference.with_refuted(
            frozenset(self._refuted_weapon), frozenset(self._refuted_wall)
        )
        self._static = build_static_map(
            MapInfo(request.mapInfo.width, request.mapInfo.height, self._cache.zones),
            team or "",
            self._cache.own_base,
            self._cache.enemy_base,
            inference=inference,
        )

    def _snapshot(self, round_no: int, fallbacks: tuple[str, ...]) -> WorldView:
        static = self._static if self._static is not None else StaticMap.empty()
        dynamic = DynamicState(
            round=round_no,
            phase=phase_of(round_no),
            day=day_of(round_no),
            team=self._cache.team or "",
            own_roles=self._cache.own_roles,
            enemy_roles=self._cache.enemy_roles,
            robots=self._robots,
            gold=self._cache.gold,
            total_score=self._cache.score,
            player_tasks=self._player_tasks,
            phase_task=self._phase_task,
            errors=self._errors,
            action_results=dict(self._action_results),
            llm_resp=self._llm_resp,
            last_cmd_result=self._last_cmd,
            last_treasure_result=self._last_treasure,
            mines=tuple(z for z in self._cache.zones if enum_to_str(z.neutralType) in _MINE_KINDS),
            vendor_prices=dict(self._vendor_prices),
            weapon_prices=dict(self._weapon_prices),
            fallbacks=fallbacks,
        )
        return WorldView(static, dynamic, self._history.snapshot())

    def _emit(self, code: EventCode, message: str, *, round_no: int, **fields: object) -> None:
        if self._logger is None:
            return
        self._logger.emit(
            code, message, round_no=round_no, phase=phase_of(round_no), **fields
        )
