"""可观测性：回合日志落盘与确定性回放（工作包 4）+ 结构化事件流（工作包 5）。

- RoundLogger：每回合 (roundNo, request, response, result) 一行 JSONL 落盘；
  缓冲写可配（默认行刷），任何写失败降级不崩溃（任务书 §八）。
- replay_match / verify_replay：确定性回放——同一日志两次回放字节一致。
- load_rounds / canonical_summary：损坏行检测（按行号）与规范摘要重算。
- EventCode / EVENT_REGISTRY / describe：稳定事件码注册表（方案 §4.1/§4.4），
  供诊断字典（工作包 8）与工具脚本查人类可读描述。
- StructuredLogger / format_line / phase_of：单行契约格式
  `<round> <phase> <TAG> <CODE> <message> <k=v ...>` 的事件流；级别上限
  （log.level）与 TRACE 开关（log.trace_enabled）可配，ERROR/ANOMALY 恒输出；
  线程安全、缓冲写、写失败降级；进程级默认实例见 configure/get/reset_logger。

实现分布：logger.py（写侧，含共享命名 helper new_log_path）、replay.py（读侧）、
events.py（事件码注册表）、structured_log.py（结构化事件流）。
工作包 6（每回合 [METRIC] 指标行）在本子包继续扩展。
"""

from future_war.observability.events import (
    EVENT_REGISTRY,
    EventCode,
    EventSpec,
    LogLevel,
    Tag,
    describe,
)
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
from future_war.observability.round_metrics import RoundObserver, metric_fields
from future_war.observability.structured_log import (
    Phase,
    StructuredLogger,
    format_line,
    phase_of,
)

_global_logger: StructuredLogger | None = None


def configure_logger(logger: StructuredLogger) -> StructuredLogger:
    """设置进程级默认实例（server.py 启动时调用一次）。"""
    global _global_logger
    _global_logger = logger
    return logger


def get_logger() -> StructuredLogger:
    """返回进程级默认实例；未配置时惰性创建默认配置实例。"""
    global _global_logger
    if _global_logger is None:
        _global_logger = StructuredLogger()
    return _global_logger


def reset_logger() -> None:
    """重置进程级默认实例（测试钩子）。"""
    global _global_logger
    _global_logger = None

__all__ = [
    "CorruptedLine",
    "EVENT_REGISTRY",
    "EventCode",
    "EventSpec",
    "LogParseError",
    "Phase",
    "ReplayOutcome",
    "RoundLogger",
    "RoundObserver",
    "RoundRecord",
    "StructuredLogger",
    "Tag",
    "canonical_summary",
    "configure_logger",
    "describe",
    "format_line",
    "get_logger",
    "load_rounds",
    "metric_fields",
    "phase_of",
    "render_summary",
    "replay_match",
    "reset_logger",
    "resolve_log_dir",
    "verify_replay",
]
