"""每回合日志完整性与机器可读指标行（工作包 6，M14）。

RoundObserver 组合两个已有写入器，保证每个判题回合都留下：

1. 一行 JSONL 事实记录 ``(roundNo, request, response, result)`` —— RoundLogger
   （工作包 4），供确定性回放；
2. 一行 ``[METRIC] M-01`` 机器可读指标 —— StructuredLogger（工作包 5），
   供内部 LLM 直接归纳（方案 §4.2）。

任何解析/写失败都降级为事件行并继续，绝不抛出（任务书 §八）；开关来自
配置（``features.replay_enabled`` / ``features.metric_line_enabled``）。仅用标准库。
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from future_war.config import Config
from future_war.observability.events import EventCode, Phase, phase_of
from future_war.observability.logger import RoundLogger, RoundRecord
from future_war.observability.structured_log import StructuredLogger

_STATION_TYPE: Final = "station"
_TRUTHY: Final = frozenset({"1", "true", "yes", "on"})


def _as_int(value: object) -> int | None:
    """int（排除 bool）原样返回，否则 None。"""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _flag(value: object, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).lower() in _TRUTHY


def metric_fields(request: Mapping[str, Any]) -> dict[str, object]:
    """从判题请求推导指标字段；不可推导的字段用 None（渲染为 ``-``）。"""
    team = request.get("teamOur")
    team = team if isinstance(team, Mapping) else {}
    roles = team.get("roles")
    roles = roles if isinstance(roles, list) else []
    base_hp = next(
        (
            _as_int(role.get("health"))
            for role in roles
            if isinstance(role, Mapping) and role.get("roleType") == _STATION_TYPE
        ),
        None,
    )
    errors = request.get("errors")
    return {
        "gold": _as_int(team.get("goldNum")),
        "kills": None,
        "score": _as_int(team.get("totalScore")),
        "baseHP": base_hp,
        "rolesAlive": len(roles),
        "errors": len(errors) if isinstance(errors, list) else 0,
    }


class RoundObserver:
    """每个判题回合的 JSONL 事实记录 + ``[METRIC]`` 指标行。"""

    def __init__(
        self,
        round_logger: RoundLogger | None,
        structured: StructuredLogger,
        *,
        metric_enabled: bool = True,
    ) -> None:
        self._round_logger = round_logger
        self._structured = structured
        self._metric_enabled = metric_enabled

    @classmethod
    def from_config(
        cls,
        config: Config,
        *,
        log_dir: str | None = None,
        match_name: str | None = None,
    ) -> RoundObserver:
        """按 Config 构造：读 features.replay_enabled / features.metric_line_enabled。

        另读 ``log.echo_stderr``（默认 **true**）：真机上拿不到 ``logs/`` 目录，
        平台捕获的 stdout/stderr 是唯一可见通道，所以默认把事件流镜像到 stderr。
        """
        replay_enabled = _flag(config.get("features.replay_enabled", True), default=True)
        metric_enabled = _flag(
            config.get("features.metric_line_enabled", True), default=True
        )
        echo_stderr = _flag(config.get("log.echo_stderr", True), default=True)
        structured = StructuredLogger.from_config(
            config, log_dir=log_dir, match_name=match_name, echo_stderr=echo_stderr
        )
        round_logger = (
            RoundLogger(log_dir, match_name=match_name) if replay_enabled else None
        )
        return cls(round_logger, structured, metric_enabled=metric_enabled)

    @property
    def structured(self) -> StructuredLogger:
        return self._structured

    @property
    def round_log_path(self) -> Path | None:
        """JSONL 事实记录文件路径；replay 关闭时为 None。"""
        return self._round_logger.log_path if self._round_logger is not None else None

    def observe(
        self,
        round_no: int,
        request: Mapping[str, Any],
        response: Mapping[str, Any],
        result: Mapping[str, Any] | None = None,
    ) -> None:
        """记录一个回合：JSONL + 指标行；任何失败都降级，不抛出。"""
        if self._round_logger is not None:
            self._round_logger.record(
                RoundRecord(
                    roundNo=round_no,
                    request=dict(request),
                    response=dict(response),
                    result=dict(result or {}),
                )
            )
        if self._metric_enabled:
            self._structured.emit(
                EventCode.M_01,
                "metric",
                round_no=round_no,
                phase=phase_of(round_no),
                **metric_fields(request),
            )

    def emit(
        self,
        code: EventCode,
        message: str,
        *,
        round_no: int | None = None,
        phase: Phase = Phase.NONE,
        **fields: object,
    ) -> None:
        """透传一行结构化事件（异常/错误路径用）。"""
        self._structured.emit(code, message, round_no=round_no, phase=phase, **fields)

    def flush(self) -> None:
        if self._round_logger is not None:
            self._round_logger.flush()
        self._structured.flush()

    def close(self) -> None:
        if self._round_logger is not None:
            self._round_logger.close()
        self._structured.close()
