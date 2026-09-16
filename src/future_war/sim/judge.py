"""mock 判题器：驱动 Bot、统计异常、编排回合、产出分数报告（工作包 3）。

- 每回合为双方各构造一份 ``models.Request``（request_view，复用工作包 2 类型）。
- 响应异常（§八三类）每回合至多计 1 次；累计 5 次后不再调度该队代码。
- 规则性失败（碰撞/落点无目标等）只标记该指令无效，不计队伍异常（§八注）。
- 对局记录（ReplayWriter）与人类可读日志（MatchLog）为可选产物（writers）。

确定性：双方调度顺序固定（challenger → defender），所有随机性来自
``world.rng``，回合记录键序稳定；同 seed + 同 Bot = 输出逐字节一致。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from future_war.models import (
    Error,
    ErrorCode,
    Request,
    RoleCommand,
)
from future_war.sim import engine
from future_war.sim.protocol import (
    BotError,
    BotFn,
    JsonValue,
    normalize_response,
    parse_commands,
    warn,
)
from future_war.sim.request_view import build_request
from future_war.sim.rules import (
    MAX_ROUNDS,
    MAX_TEAM_EXCEPTIONS,
    day_of_round,
    phase_of_round,
)
from future_war.sim.score import (
    MatchReport,
    TeamOutcome,
    TeamScore,
    compute_team_scores,
    decide_winner,
)
from future_war.sim.world import World
from future_war.sim.writers import MatchLog, ReplayWriter

TEAM_ORDER: tuple[str, ...] = ("challenger", "defender")


@dataclass(slots=True)  # noqa: MUTABLE_OK — 判题器账本：每回合更新即设计目的
class TeamDriver:
    """一队的调度账本：异常计数、调度开关、上回合指令结果、待发错误。"""

    team: str
    bot: BotFn
    scheduled: bool = True
    exceptions: int = 0
    last_results: dict[int, bool] = field(default_factory=dict)
    pending_errors: list[Error] = field(default_factory=list)


def run_match(
    world: World,
    bots: dict[str, BotFn],
    *,
    max_rounds: int = MAX_ROUNDS,
    match_log: MatchLog | None = None,
    replay: ReplayWriter | None = None,
) -> MatchReport:
    """驱动整场对局直到结束，返回分数报告。

    结束条件（§七）：1300 回合耗尽 / 双方基地均被摧毁 / 双方异常各达 5 次。
    """
    drivers: dict[str, TeamDriver] = {
        team: TeamDriver(team=team, bot=bots[team]) for team in TEAM_ORDER
    }
    end_reason = "max_rounds"
    while True:
        if world.round_no >= max_rounds:
            end_reason = "max_rounds"
            break
        if not world.base_alive("challenger") and not world.base_alive("defender"):
            end_reason = "bases_destroyed"
            break
        if all(not driver.scheduled for driver in drivers.values()):
            end_reason = "both_teams_exceptions"
            break
        engine.begin_round(world)
        commands: dict[str, dict[int, RoleCommand]] = {}
        invalid_ids: dict[str, set[int]] = {}
        round_record: dict[str, JsonValue] = {"roundNo": world.round_no}
        for team in TEAM_ORDER:
            driver = drivers[team]
            if driver.scheduled:
                request = build_request(world, team, driver)
                payload, cmds, problems, dropped = _ask_bot(driver.bot, request)
                if problems:
                    driver.exceptions += 1
                    driver.pending_errors = [
                        Error(
                            errorCode=ErrorCode.INVALID_COMMAND.value,
                            description="; ".join(problems),
                        )
                    ]
                    if driver.exceptions >= MAX_TEAM_EXCEPTIONS:
                        driver.scheduled = False
                        warn(
                            f"team {team} reached {MAX_TEAM_EXCEPTIONS} "
                            "exceptions; scheduling stopped"
                        )
                else:
                    driver.pending_errors = []
                commands[team] = cmds
                invalid_ids[team] = dropped
                round_record[team] = _team_record(request.to_dict(), payload, problems)
            else:
                round_record[team] = {"request": None, "response": None, "result": {}}
        results = engine.resolve_round(world, commands, invalid_ids)
        for team in TEAM_ORDER:
            drivers[team].last_results = dict(results[team])
            record = round_record[team]
            if isinstance(record, dict):
                record["result"] = {
                    "actionResults": {str(uid): ok for uid, ok in results[team].items()},
                    "errors": [e.to_dict() for e in drivers[team].pending_errors],
                }
        if match_log is not None:
            _log_round(match_log, world, drivers)
        if replay is not None:
            replay.record_round(round_record)
    return _build_report(world, drivers, end_reason)


def _ask_bot(
    bot: BotFn, request: Request
) -> tuple[dict[str, JsonValue], dict[int, RoleCommand], list[str], set[int]]:
    """询问 Bot 并解析响应；返回 (响应对象, 合法指令, 问题清单, 被丢弃 uid)。"""
    try:  # noqa: BROAD_EXCEPT_OK — 判题器边界：Bot 任何异常都计队伍异常，绝不外溢
        payload = normalize_response(bot(request))
    except BotError as exc:
        return {}, {}, [str(exc.reason)], set()
    except Exception as exc:  # noqa: BROAD_EXCEPT_OK — 进程内 Bot 崩溃=异常响应（§八）
        return {}, {}, [f"bot raised {type(exc).__name__}: {exc}"], set()
    try:
        cmds, problems, dropped = parse_commands(payload)
    except BotError as exc:
        return payload, {}, [str(exc.reason)], set()
    return payload, cmds, problems, dropped


def _team_record(
    request: dict[str, JsonValue], payload: dict[str, JsonValue], problems: list[str]
) -> dict[str, JsonValue]:
    """单队单回合的回放记录片段（有异常时 response 记录为空对象）。"""
    return {
        "request": request,
        "response": payload if not problems else {},
        "problems": problems,
        "result": {},
    }


def _log_round(match_log: MatchLog, world: World, drivers: dict[str, TeamDriver]) -> None:
    """把本回合结构化事件与双方状态写成单行日志。"""
    phase = phase_of_round(world.round_no)
    prefix = f"r={world.round_no:04d} {phase}"
    for event in world.events:
        match_log.line(f"{prefix} [EVENT] {event}")
    world.events.clear()
    for team in TEAM_ORDER:
        ts = world.teams[team]
        base = world.base(team)
        base_hp = max(0, base.hp) if base is not None and base.alive else 0
        robots_targeting = sum(1 for r in world.robots.values() if r.target_team == team)
        match_log.line(
            f"{prefix} [SIM] team={team} day={day_of_round(world.round_no)} "
            f"gold={ts.gold} kills={sum(ts.kills.values())} base={base_hp} "
            f"weapons={len(world.weapons(team))} robots={robots_targeting} "
            f"exceptions={drivers[team].exceptions}"
        )


def _build_report(
    world: World, drivers: dict[str, TeamDriver], end_reason: str
) -> MatchReport:
    scores: dict[str, TeamScore] = compute_team_scores(world)
    outcomes: dict[str, TeamOutcome] = {}
    for team in TEAM_ORDER:
        ts = world.teams[team]
        base = world.base(team)
        outcomes[team] = TeamOutcome(
            score1=scores[team].score1,
            score2=scores[team].score2,
            score3=scores[team].score3,
            total=scores[team].total,
            kills=dict(ts.kills),
            gold=ts.gold,
            base_hp=max(0, base.hp) if base is not None and base.alive else 0,
            base_alive=world.base_alive(team),
            base_destroy_day=ts.base_destroy_day,
            exceptions=drivers[team].exceptions,
            scheduled=drivers[team].scheduled,
        )
    return MatchReport(
        seed=world.seed,
        rounds_played=world.round_no,
        end_reason=end_reason,
        teams=outcomes,
        winner=decide_winner(world, scores),
    )
