"""回放与规范摘要：读侧（工作包 4，M15）。

设计目标（.omo/plans/future-war-bot.md §四 + 工作包 4）：

- 回放是确定性的：同一日志文件两次回放产出字节一致的规范摘要（§4.2 replay 模式）。
- 损坏/截断行按行号报告，不崩溃；strict 模式首错即抛 LogParseError。
- 写侧（RoundLogger 的 JSONL 落盘）见 logger.py；本模块保持对外命名空间，
  工作包 5（结构化日志）与 6（指标行）在本模块之上扩展。

仅用标准库（json / pathlib / dataclasses），运行时零第三方依赖。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Sequence, TypedDict

from future_war.observability.logger import (
    JsonValue,
    RoundLogger,
    RoundRecord,
    resolve_log_dir,
)

# 损坏原因稳定文案（工作包 5/8 的诊断字典与报告协议引用这些字符串）
REASON_EMPTY_LINE: Final = "empty line"
REASON_INVALID_JSON: Final = "invalid JSON"
REASON_NOT_OBJECT: Final = "not a JSON object"
REASON_MISSING_ROUND_NO: Final = "missing 'roundNo'"
REASON_BAD_ROUND_NO: Final = "'roundNo' is not an integer"
REASON_MISSING_REQUEST: Final = "missing 'request'"
REASON_MISSING_RESPONSE: Final = "missing 'response'"


class LogParseError(Exception):
    """日志行损坏。line 为 1-based 行号（文件打开/解码失败时为 None）。"""

    def __init__(self, reason: str, *, line: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.line = line


class CorruptedLine(TypedDict):
    """一条损坏/无法解析的日志行。"""

    line: int
    reason: str


def load_rounds(
    path: str | Path, *, strict: bool = False
) -> tuple[list[RoundRecord], list[CorruptedLine]]:
    """解析 JSONL 对局日志。返回 (合法记录, 损坏行)。

    strict=True 时首条损坏行即抛 LogParseError（带行号）。
    损坏行判定：空行 / 非法 JSON / 非对象 / 缺 roundNo / 缺 request / 缺 response。
    result 键缺失时补 {}（向后兼容）。
    """
    records: list[RoundRecord] = []
    corruptions: list[CorruptedLine] = []

    def reject(reason: str, line: int) -> None:
        if strict:
            raise LogParseError(reason, line=line)
        corruptions.append(CorruptedLine(line=line, reason=reason))

    source = Path(path)
    try:
        stream = source.open("r", encoding="utf-8")
    except OSError as exc:
        raise LogParseError(f"cannot open log: {exc}") from exc
    lineno = 0
    with stream:
        try:
            for lineno, raw in enumerate(stream, start=1):
                stripped = raw.strip()
                if not stripped:
                    reject(REASON_EMPTY_LINE, lineno)
                    continue
                try:
                    obj: Any = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    reject(f"{REASON_INVALID_JSON}: {exc.msg}", lineno)
                    continue
                if not isinstance(obj, dict):
                    reject(REASON_NOT_OBJECT, lineno)
                    continue
                if not isinstance(obj.get("roundNo"), int):
                    reason = (
                        REASON_MISSING_ROUND_NO
                        if "roundNo" not in obj
                        else REASON_BAD_ROUND_NO
                    )
                    reject(reason, lineno)
                    continue
                for key, reason in (
                    ("request", REASON_MISSING_REQUEST),
                    ("response", REASON_MISSING_RESPONSE),
                ):
                    if not isinstance(obj.get(key), dict):
                        reject(reason, lineno)
                        break
                else:
                    result = obj.get("result", {})
                    records.append(
                        RoundRecord(
                            roundNo=obj["roundNo"],
                            request=obj["request"],
                            response=obj["response"],
                            result=result if isinstance(result, dict) else {},
                        )
                    )
        except UnicodeDecodeError as exc:
            # 流式解码中断后文件位置不可靠：报告该行并停止解析
            reject(f"invalid UTF-8: {exc}", lineno)
    return records, corruptions


def canonical_summary(
    records: Sequence[RoundRecord],
    corruptions: Sequence[CorruptedLine],
    *,
    source: str | Path,
) -> dict[str, JsonValue]:
    """由原始记录重算规范摘要：键序与取值完全确定（同输入 → 同输出）。

    内容：回合总数、首尾回合、缺回合列表、重复回合计数、损坏行清单、
    每回合的指令数与 result.errors 数（供工作包 6 的 LLM 快速定位扩展）。
    """
    ordered = sorted(
        enumerate(records), key=lambda pair: (pair[1]["roundNo"], pair[0])
    )
    seen: dict[int, int] = {}
    per_round: list[dict[str, JsonValue]] = []
    for _, rec in ordered:
        round_no = rec["roundNo"]
        seen[round_no] = seen.get(round_no, 0) + 1
        role_command_map = rec["response"].get("roleCommandMap")
        commands = len(role_command_map) if isinstance(role_command_map, dict) else 0
        errors = rec["result"].get("errors", [])
        error_count = len(errors) if isinstance(errors, list) else 0
        per_round.append({"roundNo": round_no, "commands": commands, "errors": error_count})
    if records:
        first, last = min(seen), max(seen)
        missing = [n for n in range(first, last + 1) if n not in seen]
    else:
        first = last = None
        missing = []
    duplicates = {str(n): seen[n] for n in sorted(seen) if seen[n] > 1}
    return {
        "source": str(source),
        "totalRounds": len(records),
        "firstRound": first,
        "lastRound": last,
        "missingRounds": missing,
        "duplicateRounds": duplicates,
        "corruptedLines": [dict(c) for c in corruptions],
        "perRound": per_round,
    }


def render_summary(summary: dict[str, JsonValue]) -> str:
    """渲染规范摘要：sort_keys + 紧凑分隔符，保证字节级确定性（可 diff）。"""
    return json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class ReplayOutcome:
    """一次回放的产物：规范摘要原文、损坏行清单、合法回合数。"""

    canonical: str
    corruptions: tuple[CorruptedLine, ...]
    round_count: int


def replay_match(path: str | Path, *, strict: bool = False) -> ReplayOutcome:
    """回放一个对局日志：解析 → 重算规范摘要。确定性（同文件两次结果字节一致）。

    strict=True 时首条损坏行即抛 LogParseError（CLI --strict 用）。
    """
    records, corruptions = load_rounds(path, strict=strict)
    summary = canonical_summary(records, corruptions, source=path)
    return ReplayOutcome(
        canonical=render_summary(summary),
        corruptions=tuple(corruptions),
        round_count=len(records),
    )


def verify_replay(path: str | Path) -> tuple[bool, str]:
    """两次回放并逐字节比对，验证可复现性。返回 (一致, canonical)。"""
    first = replay_match(path).canonical
    second = replay_match(path).canonical
    return first == second, first
