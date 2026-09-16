"""自对弈调参 CLI（工作包 18，方案 §2.5）：批量跑对局并输出可比较指标。

用法::

    python3 scripts/self_play.py --seeds 5 --opponent idle
    python3 scripts/self_play.py --seeds 10 --opponent scripted --profile aggressive
    python3 scripts/self_play.py --seeds 3 --max-rounds 300 --json

在多种子下跑 StrategyBot vs 对手，输出胜/负/平、胜率与平均总分，用于比较策略
变体（武器配比/建造顺序/推家阈值）。仅用标准库。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from future_war.config import load_config  # noqa: E402
from future_war.sim import IdleBot, ScriptedBot, World, make_layout, run_match  # noqa: E402
from future_war.sim.score import MatchReport  # noqa: E402
from future_war.strategy import StrategyBot  # noqa: E402

_OPPONENTS = {"idle": IdleBot, "scripted": ScriptedBot}


def summarize(reports: Sequence[MatchReport]) -> dict[str, Any]:
    """把多局报告汇总为可比较指标（胜率/平均分/各局总分）。"""
    games = len(reports)
    wins = sum(1 for r in reports if r.winner == "challenger")
    losses = sum(1 for r in reports if r.winner == "defender")
    draws = sum(1 for r in reports if r.winner == "draw")
    scores = [r.teams["challenger"].total for r in reports]
    return {
        "games": games,
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "winRate": round(wins / games, 4) if games else 0.0,
        "avgScore": round(sum(scores) / games, 2) if games else 0.0,
        "scores": scores,
    }


def run_batch(
    seeds: Sequence[int],
    opponent: str,
    config: Any,
    max_rounds: int,
) -> list[MatchReport]:
    """对每个 seed 跑一局 StrategyBot vs 指定对手，返回报告列表。"""
    reports: list[MatchReport] = []
    for seed in seeds:
        world = World(seed=seed, layout=make_layout())
        report = run_match(
            world,
            {"challenger": StrategyBot(config), "defender": _OPPONENTS[opponent]()},
            max_rounds=max_rounds,
        )
        reports.append(report)
    return reports


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="《未来战争》自对弈调参")
    parser.add_argument("--seeds", type=int, default=5, help="对局数量（seed 0..N-1）")
    parser.add_argument("--seed-start", type=int, default=0, help="起始 seed")
    parser.add_argument("--opponent", choices=sorted(_OPPONENTS), default="idle")
    parser.add_argument("--profile", default=None, help="config profile（如 aggressive）")
    parser.add_argument("--max-rounds", type=int, default=1300)
    parser.add_argument("--json", action="store_true", help="输出 JSON 而非文本表")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(profile=args.profile)
    seeds = list(range(args.seed_start, args.seed_start + args.seeds))
    reports = run_batch(seeds, args.opponent, config, args.max_rounds)
    summary = summarize(reports)
    summary["opponent"] = args.opponent
    summary["profile"] = config.profile
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(
            f"games={summary['games']} wins={summary['wins']} "
            f"losses={summary['losses']} draws={summary['draws']} "
            f"winRate={summary['winRate']} avgScore={summary['avgScore']} "
            f"opponent={summary['opponent']} profile={summary['profile']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
