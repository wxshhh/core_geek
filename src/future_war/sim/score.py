"""积分结算与胜负判定（任务书 §六/§七）。

- ``score1`` 任务完成积分：任务系统未实现 → 恒 0（文档化 stub，见 README）。
- ``score2`` 作战击杀积分：Σ(机器人击杀数 × 机器人积分)，机器人积分见 §4.7.2。
- ``score3`` 生存天数积分：Σ_{day=1..10} 10×day×存活系数；存活系数 = 1
  （基地未被摧毁）、= 0（基地被摧毁当天及之后）。
- 胜负（§七）：基地先后被毁 → 先毁者负；同回合被毁且积分相同 → 平局；
  否则积分高者胜。
"""

from __future__ import annotations

from dataclasses import dataclass

from future_war.sim.protocol import JsonValue
from future_war.sim.rules import MAX_DAYS, ROBOT_STATS
from future_war.sim.world import World


@dataclass(frozen=True, slots=True)
class TeamScore:
    """一支队伍的积分分解（§六）。"""

    score1: int
    score2: int
    score3: int
    total: int


@dataclass(frozen=True, slots=True)
class TeamOutcome:
    """一支队伍的比赛结果（供分数报告）。"""

    score1: int
    score2: int
    score3: int
    total: int
    kills: dict[str, int]  # 机器人类型 → 击杀数
    gold: int
    base_hp: int
    base_alive: bool
    base_destroy_day: int
    exceptions: int
    scheduled: bool

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "score1": self.score1,
            "score2": self.score2,
            "score3": self.score3,
            "total": self.total,
            "kills": self.kills,
            "gold": self.gold,
            "baseHp": self.base_hp,
            "baseAlive": self.base_alive,
            "baseDestroyDay": self.base_destroy_day,
            "exceptions": self.exceptions,
            "scheduled": self.scheduled,
        }


@dataclass(frozen=True, slots=True)
class MatchReport:
    """一场模拟对局的最终分数报告（工作包 3 验收产物）。"""

    seed: int
    rounds_played: int
    end_reason: str
    teams: dict[str, TeamOutcome]
    winner: str

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "seed": self.seed,
            "roundsPlayed": self.rounds_played,
            "endReason": self.end_reason,
            "teams": {team: outcome.to_dict() for team, outcome in self.teams.items()},
            "winner": self.winner,
        }


def compute_team_scores(world: World) -> dict[str, TeamScore]:
    """按 §六 计算双方积分（score1 恒 0 为文档化 stub）。"""
    out: dict[str, TeamScore] = {}
    for team in ("challenger", "defender"):
        ts = world.teams[team]
        score1 = 0  # stub：任务系统未实现
        score2 = sum(
            count * ROBOT_STATS[kind]["score"] for kind, count in ts.kills.items()
        )
        destroy_day = ts.base_destroy_day
        score3 = sum(
            10 * day
            for day in range(1, MAX_DAYS + 1)
            if destroy_day == 0 or day < destroy_day
        )
        out[team] = TeamScore(
            score1=score1, score2=score2, score3=score3, total=score1 + score2 + score3
        )
    return out


def decide_winner(world: World, scores: dict[str, TeamScore]) -> str:
    """§七 半场胜负判定。"""
    challenger_day = world.teams["challenger"].base_destroy_day
    defender_day = world.teams["defender"].base_destroy_day
    total_c = scores["challenger"].total
    total_d = scores["defender"].total
    if challenger_day and defender_day:
        if challenger_day < defender_day:
            return "defender"
        if defender_day < challenger_day:
            return "challenger"
        # 同一回合被毁：积分相同为平局，否则积分高者胜
    if total_c == total_d:
        return "draw"
    return "challenger" if total_c > total_d else "defender"
