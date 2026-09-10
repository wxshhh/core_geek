"""沙盒执行器（工作包 24/26，方案 M13）：解析 ``lastCmdResult``。

接口 §1.1/§2.1 约定 ``lastCmdResult`` 格式：

- ``"[exitCode:N]\\n<输出>"`` —— 正常执行，N 为退出码；
- ``"[TIMEOUT]\\n<部分输出>"`` —— 沙盒命令超时（不计队伍异常）；
- ``"[JUDGER_ERROR]\\n<原因>"`` —— 判题器侧异常（不计队伍异常）；
- 输出超过 64KB 时末尾追加 ``"[TRUNCATED]"``。

解析失败一律降级为「无结果」，绝不抛异常（任务书 §八）。仅用标准库。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

_EXIT_RE: Final = re.compile(r"^\[exitCode:(-?\d+)\]\s*\n?")
_TIMEOUT_MARK: Final = "[TIMEOUT]"
_JUDGER_MARK: Final = "[JUDGER_ERROR]"
_TRUNCATED_MARK: Final = "[TRUNCATED]"


@dataclass(frozen=True, slots=True)
class CmdResult:
    """一次沙盒命令的结果（解析后）。"""

    exit_code: int | None
    output: str
    timed_out: bool
    judger_error: bool
    truncated: bool

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.judger_error


def parse_cmd_result(text: str) -> CmdResult:
    """把 ``lastCmdResult`` 原文解析为结构化结果；空/异常输入降级为无结果。"""
    if not text:
        return CmdResult(None, "", False, False, False)
    truncated = _TRUNCATED_MARK in text
    body = text.replace(_TRUNCATED_MARK, "")
    if body.startswith(_TIMEOUT_MARK):
        return CmdResult(None, _tail(body, _TIMEOUT_MARK), True, False, truncated)
    if body.startswith(_JUDGER_MARK):
        return CmdResult(None, _tail(body, _JUDGER_MARK), False, True, truncated)
    match = _EXIT_RE.match(body)
    if match is not None:
        return CmdResult(
            int(match.group(1)), body[match.end():].strip(), False, False, truncated
        )
    return CmdResult(None, body.strip(), False, False, truncated)


def _tail(text: str, marker: str) -> str:
    return text[len(marker):].strip()
