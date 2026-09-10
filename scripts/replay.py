"""回放 CLI：对已记录的对局日志重放并输出规范摘要（工作包 4）。

用法：
    python3 scripts/replay.py logs/match_xxx.jsonl       # 输出规范摘要到 stdout
    python3 scripts/replay.py LOG --verify               # 两次回放并逐字节比对
    python3 scripts/replay.py LOG --strict               # 首条损坏行即报错退出
    python3 scripts/replay.py LOG -o summary.json        # 摘要写入文件

退出码：0 干净回放；1 存在损坏行或 --verify 不一致；2 用法/文件错误。
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Sequence

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from future_war.observability.replay import (  # noqa: E402
    LogParseError,
    replay_match,
    verify_replay,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="replay.py",
        description="回放《未来战争》对局日志并输出规范摘要（确定性，可 diff）。",
    )
    parser.add_argument("match_log", help="JSONL 对局日志路径")
    parser.add_argument("--strict", action="store_true", help="首条损坏行即报错退出")
    parser.add_argument(
        "--verify", action="store_true", help="两次回放并逐字节比对（验证可复现性）"
    )
    parser.add_argument("-o", "--output", help="将摘要写入该文件而非 stdout")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = Path(args.match_log)
    if not path.is_file():
        print(f"[replay] ERROR log not found: {path}", file=sys.stderr)
        return 2
    try:
        outcome = replay_match(path, strict=args.strict)
    except LogParseError as exc:
        # --strict 首错即抛：定位到行号
        print(f"[replay] ERROR line {exc.line}: {exc.reason}", file=sys.stderr)
        return 1
    for corruption in outcome.corruptions:
        print(
            f"[replay] WARN line {corruption['line']}: {corruption['reason']}",
            file=sys.stderr,
        )
    canonical = outcome.canonical
    if args.verify:
        ok, canonical = verify_replay(path)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        print(f"[replay] verify: {'OK' if ok else 'FAIL'} sha256={digest}", file=sys.stderr)
        if not ok:
            return 1
    if args.output:
        Path(args.output).write_text(canonical, encoding="utf-8")
        print(f"[replay] summary written to {args.output}", file=sys.stderr)
    else:
        print(canonical)
    return 1 if outcome.corruptions else 0


if __name__ == "__main__":
    raise SystemExit(main())
