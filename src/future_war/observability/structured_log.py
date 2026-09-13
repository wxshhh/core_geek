"""结构化日志（工作包 5，M14）：单行固定格式的事件流。

格式契约（方案 §4.1，逐字段稳定、可 grep、供你方内部 LLM 归纳）::

    <round> <phase> <TAG> <CODE> <message> <k=v ...>
    0085 D [ECON] E-01 collect worker=10010 target=stone@(4,24) got=1 gold=20
    0130 - [DIGEST] D-01 day=1 gold=45 kills=6 baseHP=1500

- 级别上限：`log.level` ∈ {DIGEST, EVENT, DECISION, TRACE}（rank <= 上限才
  输出，见 events.LogLevel）；`log.trace_enabled` 独立放行 TRACE 行；
  **ERROR/ANOMALY 标签恒输出**——错误必须可见（进程崩溃即判负，任务书 §八）。
- 标签与级别随事件码固化（events.EventSpec）：调用方只传码，杜绝错配。
- 线程安全：单锁短临界区 + 缓冲写（默认行刷，绝不 fsync）；写失败降级告警
  一次、绝不抛给调用方（任务书 §八）；级别过滤发生在任何 I/O 之前。
- 文件命名复用 logger.new_log_path / resolve_log_dir：与 RoundLogger 同目录、
  同 match_name 前缀，后缀 `.log` 区别于 JSONL；echo_stderr 可选镜像到 stderr。
- 与 RoundLogger 互补：JSONL 是机器回放事实源（工作包 4），本事件流是
  人类/LLM 的诊断视图；不重复、可并存。仅用标准库。
"""

from __future__ import annotations

import json
import re
import sys
import threading
from pathlib import Path
from typing import Final, Sequence, TextIO

from future_war.config import Config
from future_war.observability.events import (
    EVENT_REGISTRY,
    EventCode,
    EventSpec,
    LogLevel,
    Phase,
    Tag,
    phase_of,
)
from future_war.observability.logger import new_log_path, resolve_log_dir

DEFAULT_FLUSH_EVERY: Final = 1

# 无需引号包裹的"裸值"（沿用方案 §4.1 样例风格：stone@(4,24)、(4,5)）
_SAFE_VALUE_RE: Final = re.compile(r"[\w@().,+:/\-]+")
_KEY_RE: Final = re.compile(r"[^A-Za-z0-9_]")


def _format_message(message: str) -> str:
    """消息压成单行：所有空白折叠为单空格。"""
    return " ".join(message.split())


def _format_value(value: object) -> str:
    """k=v 值编码：None→`-`；布尔/数字原样；裸安全串原样；其余 JSON 引号包裹。"""
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    try:
        text = str(value)
    except Exception:  # noqa: BROAD_EXCEPT_OK — 字段值不可控，降级为占位符
        return "<unprintable>"
    if text and _SAFE_VALUE_RE.fullmatch(text):
        return text
    try:
        return json.dumps(text, ensure_ascii=False)
    except (TypeError, ValueError):
        return "<unprintable>"


def format_line(
    code: EventCode,
    message: str,
    *,
    round_no: int | None,
    phase: Phase,
    fields: Sequence[tuple[str, object]],
) -> str:
    """渲染一行契约日志（纯函数、确定性；测试与指标行扩展可直接复用）。"""
    spec = EVENT_REGISTRY[code.value]
    round_text = (
        "-" if round_no is None else (f"{round_no:04d}" if 0 <= round_no < 10000 else str(round_no))
    )
    parts = [f"{round_text} {phase.value} {spec.tag.bracket()} {code.value}"]
    text = _format_message(message)
    if text:
        parts.append(text)
    parts.extend(f"{_KEY_RE.sub('_', key)}={_format_value(value)}" for key, value in fields)
    return " ".join(parts)


def _warn(message: str) -> None:
    print(f"[future-war] WARN observability: {message}", file=sys.stderr, flush=True)


def _coerce_level(value: object) -> LogLevel:
    """config 值 → LogLevel；非法值告警回退 EVENT（绝不崩溃）。"""
    if isinstance(value, LogLevel):
        return value
    if isinstance(value, str):
        try:
            return LogLevel(value.upper())
        except ValueError:
            pass
    _warn(f"invalid log.level {value!r}; falling back to EVENT")
    return LogLevel.EVENT


def _coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes", "on"}


class StructuredLogger:
    """结构化事件流写入器：线程安全、缓冲写、写失败降级绝不抛异常。

    emit() 返回是否落盘：级别过滤或写失败返回 False（热路径无需处理）。
    """

    def __init__(
        self,
        log_dir: str | Path | None = None,
        *,
        level: LogLevel | str = LogLevel.EVENT,
        trace_enabled: bool = False,
        match_name: str | None = None,
        flush_every: int | None = DEFAULT_FLUSH_EVERY,
        echo_stderr: bool = False,
    ) -> None:
        self._cap = level if isinstance(level, LogLevel) else LogLevel(level)
        self._trace_enabled = trace_enabled
        self._echo_stderr = echo_stderr
        self._flush_every = flush_every
        self._lock = threading.Lock()
        self._pending = 0
        self._written = 0
        self._healthy = False
        self._warned = False
        self._file: TextIO | None = None
        self._path: Path | None = None
        try:
            directory = resolve_log_dir(log_dir)
            directory.mkdir(parents=True, exist_ok=True)
            self._path = new_log_path(directory, match_name, "log")
            self._file = self._path.open("a", encoding="utf-8")
            self._healthy = True
        except OSError as exc:
            self._report(f"structured logging disabled: {exc}")

    @classmethod
    def from_config(
        cls,
        config: Config,
        *,
        log_dir: str | Path | None = None,
        match_name: str | None = None,
        flush_every: int | None = DEFAULT_FLUSH_EVERY,
        echo_stderr: bool = False,
    ) -> StructuredLogger:
        """按 Config 构造：读 log.level / log.trace_enabled（非法值告警回退）。"""
        level = _coerce_level(config.get("log.level", LogLevel.EVENT.value))
        trace_enabled = _coerce_bool(config.get("log.trace_enabled", False))
        return cls(
            log_dir,
            level=level,
            trace_enabled=trace_enabled,
            match_name=match_name,
            flush_every=flush_every,
            echo_stderr=echo_stderr,
        )

    @property
    def log_path(self) -> Path | None:
        """当前日志文件路径；写功能不可用时为 None。"""
        return self._path

    @property
    def healthy(self) -> bool:
        """写路径是否可用。False 时 emit() 恒为 no-op 并返回 False。"""
        return self._healthy

    @property
    def written_count(self) -> int:
        """实际落盘行数（测试/自检用）。"""
        with self._lock:
            return self._written

    def emit(
        self,
        code: EventCode | str,
        message: str,
        *,
        round_no: int | None = None,
        phase: Phase | str = Phase.NONE,
        **fields: object,
    ) -> bool:
        """输出一行事件。未知码/非法相位抛 ValueError（编程错误）；True=已落盘。"""
        resolved = code if isinstance(code, EventCode) else EventCode(code)
        spec = EVENT_REGISTRY[resolved.value]
        resolved_phase = phase if isinstance(phase, Phase) else Phase(phase)
        if not self._passes(spec):
            return False
        line = format_line(
            resolved,
            message,
            round_no=round_no,
            phase=resolved_phase,
            fields=tuple(fields.items()),
        )
        return self._write(line)

    def flush(self) -> None:
        """立即落盘缓冲（不做 fsync）。失败只降级。"""
        with self._lock:
            if self._healthy and self._file is not None:
                try:
                    self._file.flush()
                    self._pending = 0
                except (OSError, ValueError) as exc:
                    self._mark_broken(f"structured log flush failed: {exc}")

    def close(self) -> None:
        """刷缓冲并关闭；幂等，之后 emit() 恒为 no-op。"""
        with self._lock:
            if self._file is not None:
                try:
                    self._file.flush()
                except (OSError, ValueError):
                    pass
                self._file.close()
                self._file = None
        self._healthy = False

    def __enter__(self) -> StructuredLogger:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _passes(self, spec: EventSpec) -> bool:
        """级别过滤：ERROR/ANOMALY 恒过；TRACE 需 trace_enabled 且上限为 TRACE。"""
        if spec.tag in (Tag.ERROR, Tag.ANOMALY):
            return True
        if spec.level is LogLevel.TRACE:
            return self._trace_enabled and self._cap.rank >= LogLevel.TRACE.rank
        return spec.level.rank <= self._cap.rank

    def _write(self, line: str) -> bool:
        written = False
        if self._healthy and self._file is not None:
            with self._lock:
                if self._healthy and self._file is not None:
                    try:
                        self._file.write(line + "\n")
                        self._pending += 1
                        if self._flush_every is not None and self._pending >= self._flush_every:
                            self._file.flush()
                            self._pending = 0
                        self._written += 1
                        written = True
                    except (OSError, ValueError) as exc:
                        self._mark_broken(f"structured log dropped (write failed): {exc}")
        # 回显与文件健康**解耦**：真机上队友看不到 logs/ 目录，stderr 是唯一通道，
        # 文件写不了（只读盘/沙盒）时更要保证控制台还能看到日志。
        if self._echo_stderr:
            try:
                print(line, file=sys.stderr, flush=True)
            except (OSError, ValueError):
                pass
        return written

    def _mark_broken(self, message: str) -> None:
        self._healthy = False
        self._report(message)

    def _report(self, message: str) -> None:
        if self._warned:
            return
        self._warned = True
        _warn(message)
