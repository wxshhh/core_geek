"""工作包 18 测试：自对弈调参框架。

Given/When/Then 风格；运行方式（二选一）：
    python3 -m pytest tests/ -q
    python3 tests/test_self_play.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from future_war.config import load_config  # noqa: E402
from future_war.sim.score import MatchReport, TeamOutcome  # noqa: E402

_spec = importlib.util.spec_from_file_location("self_play", _ROOT / "scripts" / "self_play.py")
self_play = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(self_play)


def _outcome(total: int) -> TeamOutcome:
    return TeamOutcome(
        score1=0,
        score2=0,
        score3=total,
        total=total,
        kills={},
        gold=0,
        base_hp=1500,
        base_alive=True,
        base_destroy_day=0,
        exceptions=0,
        scheduled=True,
    )


def _report(winner: str, challenger_total: int, defender_total: int) -> MatchReport:
    return MatchReport(
        seed=0,
        rounds_played=1,
        end_reason="max_rounds",
        teams={
            "challenger": _outcome(challenger_total),
            "defender": _outcome(defender_total),
        },
        winner=winner,
    )


def test_summarize_counts_outcomes_and_averages() -> None:
    """Given 三局（胜/负/平），When 汇总，Then 计数与平均分正确。"""
    reports = [
        _report("challenger", 300, 100),
        _report("defender", 50, 200),
        _report("draw", 100, 100),
    ]
    summary = self_play.summarize(reports)
    assert summary["games"] == 3
    assert summary["wins"] == 1
    assert summary["losses"] == 1
    assert summary["draws"] == 1
    assert summary["winRate"] == round(1 / 3, 4)
    assert summary["avgScore"] == round((300 + 50 + 100) / 3, 2)


def test_summarize_handles_empty() -> None:
    """Given 无对局，When 汇总，Then 指标为零且不崩溃。"""
    summary = self_play.summarize([])
    assert summary["games"] == 0
    assert summary["winRate"] == 0.0
    assert summary["avgScore"] == 0.0


def test_run_batch_produces_one_report_per_seed() -> None:
    """Given 两个 seed 与短对局，When 批量跑，Then 每 seed 一份报告。"""
    reports = self_play.run_batch([0, 1], "idle", load_config(), max_rounds=150)
    assert len(reports) == 2
    assert all(isinstance(r, MatchReport) for r in reports)
    assert all("challenger" in r.teams for r in reports)


def main() -> int:
    """零依赖测试运行器：执行全部 test_* 函数并报告。"""
    test_funcs = [
        obj
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    failed = 0
    for test in test_funcs:
        try:
            test()
        except Exception as exc:  # noqa: BROAD_EXCEPT_OK — 运行器顶层边界：收集失败并继续
            failed += 1
            print(f"FAIL {test.__name__}: {exc!r}", file=sys.stderr)
        else:
            print(f"PASS {test.__name__}")
    print(f"{len(test_funcs) - failed}/{len(test_funcs)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())


# ------------------------------------------------------------------ 端到端防线回归


def _play(seed: int, rounds: int, config):
    """跑指定回合数的 StrategyBot vs ScriptedBot，返回过程快照。"""
    from future_war.sim import World, make_layout, engine, ScriptedBot
    from future_war.sim.judge import TEAM_ORDER, TeamDriver, build_request
    from future_war.sim.protocol import normalize_response, parse_commands
    from future_war.strategy import StrategyBot

    world = World(seed=seed, layout=make_layout())
    bots = {"challenger": StrategyBot(config), "defender": ScriptedBot()}
    drivers = {t: TeamDriver(team=t, bot=bots[t]) for t in TEAM_ORDER}
    snapshots = []
    while world.round_no < rounds:
        engine.begin_round(world)
        commands = {}
        for team in TEAM_ORDER:
            d = drivers[team]
            payload = normalize_response(d.bot(build_request(world, team, d)))
            cmds, _, _ = parse_commands(payload)
            commands[team] = cmds
        results = engine.resolve_round(world, commands, {t: set() for t in TEAM_ORDER})
        for team in TEAM_ORDER:
            drivers[team].last_results = dict(results[team])
        base = world.base("challenger")
        snapshots.append(
            {
                "round": world.round_no,
                "weapons": len(world.weapons("challenger")),
                "base_hp": max(0, base.hp) if base and base.alive else 0,
                "commands": commands["challenger"],
            }
        )
    return snapshots


def test_first_night_has_three_weapons_and_low_base_damage() -> None:
    """Given 第 1 天白天，When 跑到第一晚结束，Then 3 座武器就位且基地几乎不掉血。

    回归点：旧实现在第 1 天只能建成 2 座武器、夜里只有 1 座有人操控，
    第一晚基地就被刷出的机器人推平（用户实测现象）。
    """
    snapshots = _play(seed=0, rounds=130, config=load_config())
    day_one = [s for s in snapshots if s["round"] <= 70]
    assert day_one[-1]["weapons"] == 3, "第 1 天结束前必须建满 3 座武器"
    first_night = [s for s in snapshots if 70 < s["round"] <= 130]
    assert first_night
    base_hp = first_night[-1]["base_hp"]
    assert base_hp >= 1400, f"第一晚基地掉血过多: {base_hp}"


def test_first_night_three_weapons_fire() -> None:
    """Given 第一晚，When 机器人进入射程，Then 3 座武器都在同一回合开火。"""
    snapshots = _play(seed=0, rounds=130, config=load_config())
    best = 0
    for snap in snapshots:
        if snap["round"] <= 70:
            continue
        firing = {
            uid
            for uid, cmd in snap["commands"].items()
            if str(cmd.action) == "attack"
        }
        best = max(best, len(firing))
    assert best >= 3, f"第一晚最多只有 {best} 座武器同时开火"
