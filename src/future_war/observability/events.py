"""稳定事件码注册表（工作包 5，M14）。

每个决策点/异常一个固定 code（方案 §4.1/§4.4）。报告协议与诊断字典
（工作包 8）直接引用这些码，因此：

- 码一旦发布**只增不改、不删除**（稳定性约定同 config/README.md）；
  tests/test_structured_log.py::test_registry_code_set_is_stable 钉死码集。
- `EventCode` 为类型化入口（str-mixin Enum，`.value` 即日志行打印的字符串）；
  `EVENT_REGISTRY` / `describe()` 供诊断字典与工具脚本查人类可读描述。
- 每个码的默认 `Tag` 与 `LogLevel` 固化在 `EventSpec` 中，日志器据此渲染
  `[TAG]` 列并做级别过滤，调用方无需（也无法）传错标签。

码族：I 初始化 / E 经济 / N 寻路 / B 建造 / C 战斗 / T 任务 / R 寻宝 /
L LLM / S 沙盒 / O 对手 / D 摘要 / X 错误与异常（ERROR/ANOMALY 标签）。
方案 §4.4 引用的诊断码（E-01/E-04/N-02/N-03/C-01/C-02/C-05/B-02/T-03/L-01）
与本文本语义一一对齐。另含日志词汇：Tag / LogLevel / Phase / phase_of。
仅用标准库。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Final, Mapping


class Tag(str, Enum):
    """固定标签（方案 §4.1）：日志行 `[TAG]` 位置。"""

    INIT = "INIT"
    ECON = "ECON"
    NAV = "NAV"
    BUILD = "BUILD"
    COMBAT = "COMBAT"
    TASK = "TASK"
    TREASURE = "TREASURE"
    LLM = "LLM"
    SANDBOX = "SANDBOX"
    OPP = "OPP"
    ERROR = "ERROR"
    ANOMALY = "ANOMALY"
    DIGEST = "DIGEST"
    METRIC = "METRIC"  # 机器可读指标行（方案 §4.2，工作包 6）

    def bracket(self) -> str:
        """日志行内带方括号的形态，如 `[ECON]`。"""
        return f"[{self.value}]"


class LogLevel(str, Enum):
    """四级日志（方案 §4.1），rank 为粗→细序：DIGEST(0) 最粗，TRACE(3) 最细。

    `log.level` 取一档作为**上限**：rank <= 上限的事件才输出（如 EVENT →
    输出 DIGEST+EVENT、隐藏 DECISION/TRACE）。TRACE 另受 `log.trace_enabled`
    独立放行；ERROR/ANOMALY 标签恒输出（见 structured_log）。
    """

    DIGEST = "DIGEST"
    EVENT = "EVENT"
    DECISION = "DECISION"
    TRACE = "TRACE"

    @property
    def rank(self) -> int:
        return _RANK[self]


_RANK: Final[dict[LogLevel, int]] = {
    LogLevel.DIGEST: 0,
    LogLevel.EVENT: 1,
    LogLevel.DECISION: 2,
    LogLevel.TRACE: 3,
}


class Phase(str, Enum):
    """昼夜相位（日志第 2 列）：D 白天 / N 夜晚 / - 非回合内。"""

    DAY = "D"
    NIGHT = "N"
    NONE = "-"


DAY_ROUNDS: Final = 70  # 白天 70 回合（任务书 §4.2）
DAY_LENGTH: Final = 130  # 一整天 70 + 60 回合


def phase_of(round_no: int) -> Phase:
    """回合号 → 昼夜相位（白天 70 + 夜晚 60 = 130 回合循环，任务书 §4.2）。"""
    return Phase.DAY if (round_no - 1) % DAY_LENGTH < DAY_ROUNDS else Phase.NIGHT


@dataclass(frozen=True, slots=True)
class EventSpec:
    """一个事件码的稳定属性：默认标签、默认级别、人类可读描述。"""

    tag: Tag
    level: LogLevel
    description: str


class EventCode(str, Enum):
    """稳定事件码（方案 §4.1）。`.value` 即日志行打印的字符串，如 `E-01`。

    成员名 = 码串中 `-` 换成 `_`（`E-01` → `EventCode.E_01`），类型化引用。
    """

    I_01 = "I-01"
    I_02 = "I-02"
    I_03 = "I-03"
    I_04 = "I-04"

    E_01 = "E-01"
    E_02 = "E-02"
    E_03 = "E-03"
    E_04 = "E-04"
    E_05 = "E-05"
    E_06 = "E-06"

    N_01 = "N-01"
    N_02 = "N-02"
    N_03 = "N-03"
    N_04 = "N-04"

    B_01 = "B-01"
    B_02 = "B-02"
    B_03 = "B-03"
    B_04 = "B-04"

    C_01 = "C-01"
    C_02 = "C-02"
    C_03 = "C-03"
    C_04 = "C-04"
    C_05 = "C-05"
    C_06 = "C-06"

    T_01 = "T-01"
    T_02 = "T-02"
    T_03 = "T-03"
    T_04 = "T-04"

    R_01 = "R-01"
    R_02 = "R-02"
    R_03 = "R-03"
    R_04 = "R-04"

    L_01 = "L-01"
    L_02 = "L-02"
    L_03 = "L-03"
    L_04 = "L-04"

    S_01 = "S-01"
    S_02 = "S-02"
    S_03 = "S-03"
    S_04 = "S-04"

    O_01 = "O-01"
    O_02 = "O-02"
    O_03 = "O-03"

    D_01 = "D-01"

    M_01 = "M-01"

    X_01 = "X-01"
    X_02 = "X-02"
    X_03 = "X-03"
    X_04 = "X-04"
    X_05 = "X-05"
    X_06 = "X-06"
    X_99 = "X-99"


_SPECS: Final[dict[str, EventSpec]] = {
    # I — 初始化（进程/对局生命周期）
    EventCode.I_01.value: EventSpec(Tag.INIT, LogLevel.EVENT, "bot startup: commit/config-hash/profile stamp"),
    EventCode.I_02.value: EventSpec(Tag.INIT, LogLevel.EVENT, "config loaded (merged defaults/profile/env)"),
    EventCode.I_03.value: EventSpec(Tag.INIT, LogLevel.EVENT, "match started"),
    EventCode.I_04.value: EventSpec(Tag.INIT, LogLevel.EVENT, "match ended (final score summary)"),
    # E — 经济
    EventCode.E_01.value: EventSpec(Tag.ECON, LogLevel.EVENT, "worker collected ore from a mine"),
    EventCode.E_02.value: EventSpec(Tag.ECON, LogLevel.EVENT, "ore sold to vendor"),
    EventCode.E_03.value: EventSpec(Tag.ECON, LogLevel.EVENT, "item purchased from weapon shop"),
    EventCode.E_04.value: EventSpec(Tag.ECON, LogLevel.EVENT, "purchase/upgrade deferred: insufficient gold (§4.4)"),
    EventCode.E_05.value: EventSpec(Tag.ECON, LogLevel.TRACE, "mining route assigned to workers"),
    EventCode.E_06.value: EventSpec(Tag.ECON, LogLevel.TRACE, "vendor price change observed"),
    # N — 寻路
    EventCode.N_01.value: EventSpec(Tag.NAV, LogLevel.TRACE, "path planned"),
    EventCode.N_02.value: EventSpec(Tag.NAV, LogLevel.EVENT, "target unreachable / worker stuck (§4.4)"),
    EventCode.N_03.value: EventSpec(Tag.NAV, LogLevel.TRACE, "collision avoided by replanning (§4.4)"),
    EventCode.N_04.value: EventSpec(Tag.NAV, LogLevel.TRACE, "periodic replan triggered (nav.replan_interval)"),
    # B — 建造
    EventCode.B_01.value: EventSpec(Tag.BUILD, LogLevel.EVENT, "building placed (weapon or wall)"),
    EventCode.B_02.value: EventSpec(Tag.BUILD, LogLevel.EVENT, "build blocked: no legal cell or resource (§4.4)"),
    EventCode.B_03.value: EventSpec(Tag.BUILD, LogLevel.EVENT, "upgrade coupon used"),
    EventCode.B_04.value: EventSpec(Tag.BUILD, LogLevel.DECISION, "layout plan computed"),
    # C — 战斗
    EventCode.C_01.value: EventSpec(Tag.COMBAT, LogLevel.DECISION, "weapon idle: no valid target (§4.4)"),
    EventCode.C_02.value: EventSpec(Tag.COMBAT, LogLevel.EVENT, "weapon has no controller in range (§4.4)"),
    EventCode.C_03.value: EventSpec(Tag.COMBAT, LogLevel.EVENT, "weapon fired at target"),
    EventCode.C_04.value: EventSpec(Tag.COMBAT, LogLevel.EVENT, "robot destroyed"),
    EventCode.C_05.value: EventSpec(Tag.COMBAT, LogLevel.EVENT, "our base damaged (§4.4)"),
    EventCode.C_06.value: EventSpec(Tag.COMBAT, LogLevel.TRACE, "overkill avoided: target reassigned"),
    # T — 任务
    EventCode.T_01.value: EventSpec(Tag.TASK, LogLevel.EVENT, "task accepted"),
    EventCode.T_02.value: EventSpec(Tag.TASK, LogLevel.EVENT, "answer submitted"),
    EventCode.T_03.value: EventSpec(Tag.TASK, LogLevel.EVENT, "task abandoned / timeout risk (§4.4)"),
    EventCode.T_04.value: EventSpec(Tag.TASK, LogLevel.EVENT, "task completed with reward"),
    # R — 寻宝
    EventCode.R_01.value: EventSpec(Tag.TREASURE, LogLevel.TRACE, "folk legend clue recorded"),
    EventCode.R_02.value: EventSpec(Tag.TREASURE, LogLevel.EVENT, "treasure summon attempted"),
    EventCode.R_03.value: EventSpec(Tag.TREASURE, LogLevel.EVENT, "treasure summon failed (wrong place/time/item)"),
    EventCode.R_04.value: EventSpec(Tag.TREASURE, LogLevel.EVENT, "treasure found"),
    # L — LLM
    EventCode.L_01.value: EventSpec(Tag.LLM, LogLevel.EVENT, "llm quota exceeded (errorCode 5, §4.4)"),
    EventCode.L_02.value: EventSpec(Tag.LLM, LogLevel.EVENT, "prompt sent"),
    EventCode.L_03.value: EventSpec(Tag.LLM, LogLevel.TRACE, "llm response parsed"),
    EventCode.L_04.value: EventSpec(Tag.LLM, LogLevel.EVENT, "llm failed: fallback applied"),
    # S — 沙盒
    EventCode.S_01.value: EventSpec(Tag.SANDBOX, LogLevel.TRACE, "sandbox command sent"),
    EventCode.S_02.value: EventSpec(Tag.SANDBOX, LogLevel.EVENT, "sandbox command timed out ([TIMEOUT])"),
    EventCode.S_03.value: EventSpec(Tag.SANDBOX, LogLevel.EVENT, "sandbox output truncated ([TRUNCATED])"),
    EventCode.S_04.value: EventSpec(Tag.SANDBOX, LogLevel.TRACE, "sandbox result parsed"),
    # O — 对手
    EventCode.O_01.value: EventSpec(Tag.OPP, LogLevel.TRACE, "enemy unit observed"),
    EventCode.O_02.value: EventSpec(Tag.OPP, LogLevel.DECISION, "opponent intent inferred"),
    EventCode.O_03.value: EventSpec(Tag.OPP, LogLevel.EVENT, "threat detected: possible base rush"),
    # D — 摘要
    EventCode.D_01.value: EventSpec(Tag.DIGEST, LogLevel.DIGEST, "day/match digest line"),
    # M — 机器可读指标（方案 §4.2：每回合一行，供内部 LLM 直接归纳）
    EventCode.M_01.value: EventSpec(
        Tag.METRIC, LogLevel.DIGEST, "per-round machine-readable metric line"
    ),
    # X — 错误与异常（恒输出，任务书 §八）
    EventCode.X_01.value: EventSpec(Tag.ERROR, LogLevel.EVENT, "internal error caught (process kept alive)"),
    EventCode.X_02.value: EventSpec(Tag.ERROR, LogLevel.EVENT, "malformed judge request"),
    EventCode.X_03.value: EventSpec(Tag.ERROR, LogLevel.EVENT, "invalid action rejected by judge"),
    EventCode.X_04.value: EventSpec(Tag.ERROR, LogLevel.EVENT, "response near 5s budget"),
    EventCode.X_05.value: EventSpec(Tag.ANOMALY, LogLevel.EVENT, "world-state anomaly detected"),
    EventCode.X_06.value: EventSpec(Tag.ERROR, LogLevel.EVENT, "logging degraded (write failed)"),
    EventCode.X_99.value: EventSpec(Tag.ERROR, LogLevel.EVENT, "unclassified error"),
}

EVENT_REGISTRY: Final[Mapping[str, EventSpec]] = MappingProxyType(_SPECS)


def describe(code: EventCode | str) -> str:
    """事件码 → 人类可读描述（诊断字典/工具脚本用）。未知码抛 KeyError。"""
    key = code.value if isinstance(code, EventCode) else code
    return EVENT_REGISTRY[key].description
