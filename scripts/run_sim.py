"""本地模拟对局 CLI（工作包 3）。

用法：
    python3 scripts/run_sim.py --seed 42
    python3 scripts/run_sim.py --seed 42 --challenger scripted:defense --defender idle
    python3 scripts/run_sim.py --seed 7 --challenger http:http://127.0.0.1:18080 --rounds 130
    python3 scripts/run_sim.py --seed 42 --replay .omo/evidence/task-3-future-war-bot.replay \\
        --log .omo/evidence/task-3-future-war-bot.log

输出：分数报告 JSON 到 stdout（--report 可另存文件）；--replay 写对局回放
JSONL；--log 写人类可读日志。同 seed 同 Bot 规格 → stdout 逐字节一致。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from future_war.sim.judge import run_match  # noqa: E402
from future_war.sim.layout import make_layout  # noqa: E402
from future_war.sim.protocol import BotFn  # noqa: E402
from future_war.sim.registry import BotSpecError, bot_from_spec  # noqa: E402
from future_war.sim.rules import MAX_ROUNDS  # noqa: E402
from future_war.sim.world import World  # noqa: E402
from future_war.sim.writers import MatchLog, ReplayWriter  # noqa: E402

DEFAULT_BOTS = {"challenger": "scripted:defense", "defender": "scripted:idle"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_sim.py",
        description="运行一场《未来战争》本地模拟对局（mock 判题器，固定种子可复现）。",
    )
    parser.add_argument("--seed", type=int, default=42, help="随机种子（默认 42）")
    parser.add_argument("--rounds", type=int, default=MAX_ROUNDS, help=f"回合上限（默认 {MAX_ROUNDS}）")
    parser.add_argument(
        "--challenger",
        default=DEFAULT_BOTS["challenger"],
        help="挑战者 Bot 规格：scripted:defense | scripted:idle | idle | http:URL",
    )
    parser.add_argument(
        "--defender",
        default=DEFAULT_BOTS["defender"],
        help="防守者 Bot 规格（同上）",
    )
    parser.add_argument("--replay", help="对局回放 JSONL 输出路径（.replay）")
    parser.add_argument("--log", help="人类可读对局日志输出路径")
    parser.add_argument("--report", help="分数报告 JSON 输出路径（stdout 始终打印）")
    return parser


def resolve_bots(args: argparse.Namespace) -> dict[str, BotFn]:
    try:
        return {
            "challenger": bot_from_spec(args.challenger),
            "defender": bot_from_spec(args.defender),
        }
    except BotSpecError as exc:
        print(f"[run_sim] ERROR {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 1 <= args.rounds <= MAX_ROUNDS:
        print(f"[run_sim] ERROR --rounds must be in [1, {MAX_ROUNDS}]", file=sys.stderr)
        return 2
    bots = resolve_bots(args)
    world = World(seed=args.seed, layout=make_layout())
    match_log = MatchLog(args.log)
    replay = ReplayWriter(args.replay) if args.replay else None
    try:
        report = run_match(
            world,
            bots,
            max_rounds=args.rounds,
            match_log=match_log,
            replay=replay,
        )
    finally:
        match_log.close()
        if replay is not None:
            replay.close()
    text = json.dumps(
        report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True
    )
    print(text)
    if args.report:
        Path(args.report).write_text(text + "\n", encoding="utf-8")
        print(f"[run_sim] report written to {args.report}", file=sys.stderr)
    if args.replay:
        print(f"[run_sim] replay written to {args.replay}", file=sys.stderr)
    if args.log:
        print(f"[run_sim] log written to {args.log}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
