"""回合日志写入器：JSONL 落盘（工作包 4 的写侧，M15）。

设计目标（.omo/plans/future-war-bot.md §四 + 工作包 4）：

- 每回合落盘一个 JSON 对象（JSONL），键：roundNo / request / response / result，
  与判题器一轮交互一一对应，供离线回放与摘要重算。
- 落盘不阻塞 5s 响应预算（docs/任务书.md §八）：单行序列化 + 缓冲写；
  flush 策略可配（默认行刷 flush_every=1，可加大为批量），绝不 fsync。
- 任何写失败（磁盘满、目录被删、不可序列化）只降级并告警，绝不抛给调用方
  ——进程崩溃=直接判负（任务书 §八）。

读侧（损坏行检测与确定性回放）见 replay.py。仅用标准库。
"""

from __future__ import annotations

import json
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Final, TextIO, TypedDict, TypeAlias

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]

DEFAULT_LOG_DIR: Final = "logs"
DEFAULT_FLUSH_EVERY: Final = 1
LOG_DIR_ENV: Final = "FUTURE_WAR_LOG_DIR"


class RoundRecord(TypedDict):
    """单回合落盘记录（JSONL 的一行）。request/response/result 保存原始 dict。"""

    roundNo: int
    request: dict[str, JsonValue]
    response: dict[str, JsonValue]
    result: dict[str, JsonValue]


def resolve_log_dir(log_dir: str | Path | None = None) -> Path:
    """解析日志目录：显式参数 > 环境变量 FUTURE_WAR_LOG_DIR > 默认 logs/。"""
    if log_dir is None:
        log_dir = os.environ.get(LOG_DIR_ENV, DEFAULT_LOG_DIR)
    return Path(log_dir)


class RoundLogger:
    """每回合 JSONL 落盘器：线程安全、缓冲写可配、写失败降级绝不抛异常。

    record() 接受一个 RoundRecord（四键契约），返回是否成功；任何
    IO/序列化失败都会把本实例置为 unhealthy 并只向 stderr 告警一次，
    调用方无需也不应处理异常（热路径保护，任务书 §八）。
    """

    def __init__(
        self,
        log_dir: str | Path | None = None,
        *,
        flush_every: int | None = DEFAULT_FLUSH_EVERY,
        match_name: str | None = None,
    ) -> None:
        self._log_dir = resolve_log_dir(log_dir)
        self._flush_every = flush_every
        self._lock = threading.Lock()
        self._pending = 0
        self._healthy = False
        self._warned = False
        self._file: TextIO | None = None
        self._path: Path | None = None
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            self._path = self._new_log_path(match_name)
            self._file = self._path.open("a", encoding="utf-8")
            self._healthy = True
        except OSError as exc:
            self._report(f"logging disabled: {exc}")

    @property
    def log_path(self) -> Path | None:
        """当前日志文件路径；写功能不可用时为 None。"""
        return self._path

    @property
    def healthy(self) -> bool:
        """写路径是否可用。False 时 record() 恒为 no-op 并返回 False。"""
        return self._healthy

    def record(self, record: RoundRecord) -> bool:
        """追加一行回合记录；返回是否成功。绝不抛异常（IO/序列化失败均降级）。"""
        if not self._healthy or self._file is None:
            return False
        try:
            line = json.dumps(record, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            self._mark_broken(f"record dropped (unserializable): {exc}")
            return False
        with self._lock:
            if not self._healthy or self._file is None:
                return False
            try:
                self._file.write(line + "\n")
                self._pending += 1
                if self._flush_every is not None and self._pending >= self._flush_every:
                    self._file.flush()
                    self._pending = 0
            except (OSError, ValueError) as exc:
                self._mark_broken(f"record dropped (write failed): {exc}")
                return False
        return True

    def flush(self) -> None:
        """立即落盘缓冲（不做 fsync）。失败只降级，不抛异常。"""
        with self._lock:
            if self._healthy and self._file is not None:
                try:
                    self._file.flush()
                    self._pending = 0
                except (OSError, ValueError) as exc:
                    self._mark_broken(f"flush failed: {exc}")

    def close(self) -> None:
        """刷缓冲并关闭文件；幂等，之后 record() 恒为 no-op。"""
        with self._lock:
            if self._file is not None:
                try:
                    self._file.flush()
                except (OSError, ValueError):
                    pass
                self._file.close()
                self._file = None
        self._healthy = False

    def __enter__(self) -> RoundLogger:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _new_log_path(self, match_name: str | None) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        stem = f"{match_name or 'match'}_{stamp}_{os.getpid()}"
        candidate = self._log_dir / f"{stem}.jsonl"
        suffix = 2
        while candidate.exists():
            candidate = self._log_dir / f"{stem}_{suffix}.jsonl"
            suffix += 1
        return candidate

    def _mark_broken(self, message: str) -> None:
        self._healthy = False
        self._report(message)

    def _report(self, message: str) -> None:
        if self._warned:
            return
        self._warned = True
        print(f"[future-war] WARN observability: {message}", file=sys.stderr, flush=True)
