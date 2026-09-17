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


def metric_fields(
    request: Mapping[str, Any], *, previous_score: int | None = None
) -> dict[str, object]:
    """从判题请求推导指标字段；不可推导的字段用 None（渲染为 ``-``）。

    ``kills`` 恒为 None（渲染 ``-``）是**如实说明不可得**，不是待补的 stub，也
    绝不用别的数字糊弄排查：判题请求里没有任何逐单位击杀字段 —— 己方击杀只回
    一个布尔动作结果（``lastRoundRoleActionResults``），敌方 ``roles`` 列表只给
    「当前还站着几个」，既分不清「本轮被打死」与「一直没出现」，也分不清是谁的
    战果。想要真实击杀数，只能等接口补字段，或从 ``teamEnemy.roles`` 的环比
    差值间接估（噪声大，故不写进指标行）。

    可得的替代信息是 ``scoreDelta`` = ``totalScore`` 相对**上一回合**的差值（由
    调用方跨回合持有并传入 ``previous_score``；没有上一回合时为 None）。它至少
    能让内部 LLM 看出「本轮有没有拿到分」，而不是盯着一个恒为 ``-`` 的 kills。

    ``errors`` 只给**条数**，因此额外派生 ``errorCodes`` / ``errorDesc``：判题器
    的 ``errors`` 数组里混着多种错误码（1=任务超时、2=答案错误、4=指令错误、
    5=LLM 超限），而只有 errorCode 4 会消耗「累计 5 次即停止调度该队」的配额。
    2026-09-17 的线上事故就卡在这一步 —— 日志只有 ``errors=1``，无法区分「我们
    发了非法指令」（必须立刻修）与「任务答错了」（纯失分）。码与描述一起打印后，
    下一局可以直接指认，不必再靠推测。
    """
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
    error_list = _error_list(request.get("errors"))
    score = _as_int(team.get("totalScore"))
    return {
        "gold": _as_int(team.get("goldNum")),
        "kills": None,
        "score": score,
        "scoreDelta": (
            None if score is None or previous_score is None else score - previous_score
        ),
        "baseHP": base_hp,
        "rolesAlive": len(roles),
        "errors": len(error_list),
        "errorCodes": _error_codes(error_list),
        "errorDesc": _error_desc(error_list),
    }


def _error_list(raw: object) -> list[object]:
    """``errors`` 字段规范化为列表（缺失/类型不对 → 空列表，绝不抛）。"""
    return list(raw) if isinstance(raw, list) else []


def _error_codes(errors: list[object]) -> str | None:
    """错误码串（如 ``4`` / ``1,2``）；无错误返回 None（渲染 ``-``）。"""
    codes = [
        str(item.get("errorCode"))
        for item in errors
        if isinstance(item, Mapping) and item.get("errorCode") is not None
    ]
    return ",".join(codes) if codes else None


def _error_desc(errors: list[object]) -> str | None:
    """第一条非空描述（压成单行、截断）；无则 None。"""
    for item in errors:
        if not isinstance(item, Mapping):
            continue
        text = item.get("description")
        if isinstance(text, str) and text.strip():
            return " ".join(text.split())[:48]
    return None


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
        # 上一回合的 totalScore：用于 scoreDelta（环比差值）。观察器与比赛同生命周期，
        # 只有真的读到过分数才更新 —— 请求缺字段的回合不能把基线抹成 None。
        self._last_score: int | None = None

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
            fields = metric_fields(request, previous_score=self._last_score)
            self._structured.emit(
                EventCode.M_01,
                "metric",
                round_no=round_no,
                phase=phase_of(round_no),
                **fields,
            )
            score = fields["score"]
            if isinstance(score, int):
                self._last_score = score

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
