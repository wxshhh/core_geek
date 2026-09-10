"""对局产物写入器：回放 JSONL 与人类可读日志（工作包 3 产物）。

两类产物都遵循「写入失败只告警、绝不崩溃」的原则（进程崩溃即判负，
任务书 §八 精神的统一约束）。均为可选（None 即不产出）。

- ``ReplayWriter``：每行一个 JSON 对象 ``{roundNo, challenger:{request,
  response, problems, result}, defender:{...}}``，供离线核对与确定性 diff。
- ``MatchLog``：单行结构化事件日志（[EVENT]/[SIM]），供人工快速定位。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TextIO

from future_war.sim.protocol import JsonValue, warn


class MatchLog:
    """人类可读对局日志写入器；写入失败只告警（绝不崩溃）。"""

    def __init__(self, path: str | Path | None) -> None:
        self._file: TextIO | None = None
        if path is None:
            return
        target = Path(path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            self._file = target.open("w", encoding="utf-8")
        except OSError as exc:
            warn(f"cannot open match log {target}: {exc}")

    def line(self, text: str) -> None:
        if self._file is None:
            return
        try:
            self._file.write(text + "\n")
        except OSError:
            pass

    def close(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            except OSError:
                pass
            self._file = None


class ReplayWriter:
    """对局回放 JSONL 写入器（每行 {roundNo, challenger:{request,response,...}, ...}）。"""

    def __init__(self, path: str | Path) -> None:
        self._file: TextIO | None = None
        target = Path(path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            self._file = target.open("w", encoding="utf-8")
        except OSError as exc:
            warn(f"cannot open replay file {target}: {exc}")

    def record_round(self, record: dict[str, JsonValue]) -> None:
        if self._file is None:
            return
        try:
            self._file.write(json.dumps(record, ensure_ascii=False) + "\n")
        except (OSError, TypeError):
            pass

    def close(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            except OSError:
                pass
            self._file = None
