"""可观测性：回合日志落盘与确定性回放（工作包 4，M15）。

- RoundLogger：每回合 (roundNo, request, response, result) 一行 JSONL 落盘；
  缓冲写可配（默认行刷），任何写失败降级不崩溃（任务书 §八）。
- replay_match / verify_replay：确定性回放——同一日志两次回放字节一致。
- load_rounds / canonical_summary：损坏行检测（按行号）与规范摘要重算。

实现分布：logger.py（写侧）、replay.py（读侧，公开命名空间）。
工作包 5（结构化日志）与 6（指标行）将在本子包继续扩展。
"""

from future_war.observability.replay import (
    CorruptedLine,
    LogParseError,
    ReplayOutcome,
    RoundLogger,
    RoundRecord,
    canonical_summary,
    load_rounds,
    render_summary,
    replay_match,
    resolve_log_dir,
    verify_replay,
)

__all__ = [
    "CorruptedLine",
    "LogParseError",
    "ReplayOutcome",
    "RoundLogger",
    "RoundRecord",
    "canonical_summary",
    "load_rounds",
    "render_summary",
    "replay_match",
    "resolve_log_dir",
    "verify_replay",
]
