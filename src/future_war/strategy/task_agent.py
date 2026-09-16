"""自进化任务 Agent（工作包 21，方案 M9）：沙盒 + LLM 循环 + 技能库。

开拓者到任务点领取自进化类任务后，按「规划 → 沙盒执行 → 读结果 → 再规划」
循环推进，最终提交答案。核心是**自进化**：把成功的命令序列抽象为 SOP 存入
``SkillLibrary``，同类任务（按任务文本签名）下次直接复用，加速完成。

沙盒/LLM 均为异步一回合往返（接口 §2.1/§1.7）：本回合发出 ``executeCmd``/``prompt``，
下回合从 ``lastCmdResult``/``llmResp`` 读回。任务系统在本地模拟器中未实现
（sim/README stub），故以合成视图 + 脚本化沙盒结果单测覆盖。仅用标准库。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from future_war.config import Config
from future_war.models import Action, Pos, RoleCommand
from future_war.core.nav import plan_move
from future_war.core.world_map import chebyshev
from future_war.core.world_view import WorldView
from future_war.strategy.sandbox import parse_cmd_result

_ANSWER_MARKER: Final = "ANSWER:"
# LLM 明确表示「还缺信息」时不要当答案提交（提交错误答案会拉低通过率）
_NON_ANSWERS: Final = frozenset({"NEXT", "CONTINUE", "NEED_MORE", "UNKNOWN", "继续", "未知"})
_EXPLORE_CMD: Final = "echo explore"
_DAY_ROUNDS: Final = 70
_DAY_LENGTH: Final = 130
# acceptTask 的重发窗口（回合）：判题器要等任务生效才回 phaseTask，旧实现**每回合**
# 重发领取请求；重复领取一旦被判成「重新领取/非法」，任务计时与状态就被反复重置，
# phaseTask 永远等不到。保留这个窗口只为防丢包：几回合后仍没任务描述就再试一次。
_ACCEPT_RETRY_ROUNDS: Final = 4


@dataclass
class SkillLibrary:
    """任务签名 → 成功命令序列（SOP）的可复用技能库。"""

    skills: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def record(self, signature: str, commands: tuple[str, ...]) -> None:
        if commands:
            self.skills[signature] = tuple(commands)

    def recall(self, signature: str) -> tuple[str, ...]:
        return self.skills.get(signature, ())


@dataclass
class TaskState:
    """跨回合任务状态：签名、已发命令、观测、技能库。"""

    skills: SkillLibrary = field(default_factory=SkillLibrary)
    signature: str | None = None
    pending_cmd: str | None = None
    commands_sent: list[str] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    pending_prompt: bool = False  # 上回合发过 prompt → 本回合认领 llmResp
    llm_answer: str = ""  # 最近一次 LLM 回复原文（答案的候选来源）
    accept_point: tuple[int, int] | None = None  # 上次 acceptTask 指向的任务点
    accept_round: int = -1  # 上次 acceptTask 的回合号（去重用的窗口起点）


@dataclass(frozen=True, slots=True)
class TaskAction:
    """一回合任务产出：角色指令 + 可选的 LLM prompt / 沙盒命令。"""

    commands: dict[int, RoleCommand]
    prompt: str = ""
    execute_cmd: str = ""
    stall: str = ""  # 本回合任务侧为什么没推进（进 planner notes 的 task_stall=）


def plan_task(
    view: WorldView, config: Config | None = None, state: TaskState | None = None
) -> TaskAction:
    """推进自进化任务：领取 / 沙盒探索 / 提交答案。

    ``TaskAction.stall`` 说明本回合为什么没推进（进 planner notes 的
    ``task_stall=``）：``disabled``（配置关闭）/ ``no-pioneer``（开拓者不在场）/
    ``return-home``（夜/黄昏暂停）/ ``late-in-day``（余量不足）/ ``no-task-point``
    （本地图没有己方任务点）/ ``accept-sent``（已发过领取，等 phaseTask）/
    ``no-route``（任务点周围一圈全被挡，一步都走不过去）/ ``awaiting-cmd-result``
    （沙盒命令还没回结果，本回合等）。
    """
    if not _enabled(config):
        return TaskAction({}, stall="disabled")
    state = state if state is not None else TaskState()
    pioneer = view.own_pioneer()
    if not pioneer:
        return TaskAction({}, stall="no-pioneer")
    unit = pioneer[0]
    phase = view.phase_task()

    if _must_return_home(view, config):
        # 黄昏/夜晚：任务再香也不如多一座武器开火，所以**不动**（开拓者回去当操控者）。
        # 但只暂停、**不重置**：旧实现在这里 _reset，把 observations/signature 全清空，
        # 于是任何需要跨天推进的任务每晚归零、第二天从头再来 —— 真机表现就是
        # 「反复 acceptTask，永远 submitAnswer」。
        # 例外：已经拿到答案的活跃任务照交 —— submitAnswer 没有昼夜限制，而任务一超时
        # 就作废（0 分），把答案留到第二天再交等于白干。
        return _suspend(view, state, unit, phase)

    if state.signature is not None and not phase:
        state.skills.record(state.signature, tuple(state.commands_sent))
        _reset(state)

    if not phase:
        return _seek_task(view, unit, config, state)

    if state.signature is None:
        state.signature = _signature(phase)
        state.commands_sent = []
        state.observations = []

    _digest_result(view, state)
    answer = _answer_from(state.observations, state.llm_answer)
    if answer is not None:
        return TaskAction(
            {unit.id: RoleCommand(action=Action.SUBMIT_ANSWER, taskAnswer=answer)}
        )
    if state.pending_cmd is None:
        command = _next_command(state)
        state.pending_cmd = command
        state.commands_sent.append(command)
        state.pending_prompt = True
        return TaskAction({}, prompt=_prompt(phase, state), execute_cmd=command)
    return TaskAction({}, stall="awaiting-cmd-result")


def _suspend(
    view: WorldView, state: TaskState, unit, phase: str
) -> TaskAction:
    """夜/黄昏：暂停任务探索；但若是活跃任务且已有答案，先把答案交掉。"""
    if state.signature is None or not phase:
        return TaskAction({}, stall="return-home")
    _digest_result(view, state)
    answer = _answer_from(state.observations, state.llm_answer)
    if answer is None:
        return TaskAction({}, stall="return-home")
    return TaskAction(
        {unit.id: RoleCommand(action=Action.SUBMIT_ANSWER, taskAnswer=answer)}
    )


def _seek_task(view: WorldView, unit, config: Config | None, state: TaskState) -> TaskAction:
    """去任务点领取任务：已在旁边就 acceptTask，否则朝**可走过去的**任务点移动。

    为什么要逐个任务点试（而不是只看最近的那个）：任务点本身是**阻挡格**，
    ``plan_move`` 的 goal 是不可达格时只给「最优接近」—— 当某个任务点周围一圈
    全被占（矿/队友/墙），开拓者会停在切比雪夫 2 格外再也走不进一格，从此每回合
    都返回空指令：这就是真机「开拓者走到任务点后杵着」的确切分支。换一个能走过去的
    任务点即可绕过；一个都走不过去时给出 ``no-route`` 诊断，不再静默发呆。
    """
    if not _enough_time(view, config):
        return TaskAction({}, stall="late-in-day")
    points = view.own_task_points()
    if not points:
        return TaskAction({}, stall="no-task-point")
    ordered = sorted(points, key=lambda p: (chebyshev(unit.pos, p), p.x, p.y))
    for target in ordered:
        if chebyshev(unit.pos, target) <= 1:
            return _accept_task(view, state, unit, target)
    for target in ordered:
        step = plan_move(view, unit.id, target)
        if step is not None:
            return TaskAction({unit.id: RoleCommand(action=Action.MOVE, targetPos=(step,))})
    return TaskAction({}, stall="no-route")


def _accept_task(view: WorldView, state: TaskState, unit, target) -> TaskAction:
    """在任务点身旁发 ``acceptTask``；同一任务点**不每回合重发**。

    真机上判题器要等领取生效才回 ``phaseTask``，而旧实现每回合都发一次领取请求。
    重复领取一旦被判成「重新领取 / 非法」，任务计时与状态就被反复重置，phaseTask
    永远等不到 —— 开拓者就杵在任务点旁边、从不 submitAnswer。保留
    ``_ACCEPT_RETRY_ROUNDS`` 窗口只为防丢包：几回合后仍没有任务描述就再试一次。
    """
    key = (target.x, target.y)
    if state.accept_point == key and 0 <= view.round_no - state.accept_round < _ACCEPT_RETRY_ROUNDS:
        return TaskAction({}, stall="accept-sent")
    state.accept_point = key
    state.accept_round = view.round_no
    return TaskAction({unit.id: RoleCommand(action=Action.ACCEPT_TASK)})


def _digest_result(view: WorldView, state: TaskState) -> None:
    """认领上一回合的两种异步回复：``llmResp``（LLM 答案）与 ``lastCmdResult``（沙盒）。"""
    if state.pending_prompt:
        response = view.llm_resp().strip()
        if response:
            state.llm_answer = response
            state.observations.append(response)
        state.pending_prompt = False
    if state.pending_cmd is None:
        return
    result = parse_cmd_result(view.last_cmd_result())
    if result.output:
        state.observations.append(result.output)
    state.pending_cmd = None


def _answer_from(observations: list[str], llm_answer: str = "") -> str | None:
    """从沙盒输出与 LLM 回复里取答案。

    优先显式 ``ANSWER:`` 标记（沙盒侧约定），否则退回 LLM 回复的首个可用行。
    旧实现只认标记、且只看沙盒输出，而 LLM 回复根本没被读回 —— 于是**永远没有
    可提交的答案**，真机上表现为「反复 acceptTask 却从不 submitAnswer」（0 分）。
    """
    for text in (*observations, llm_answer):
        if _ANSWER_MARKER in text:
            tail = text.split(_ANSWER_MARKER, 1)[1].strip()
            line = tail.splitlines()[0].strip() if tail else ""
            if line:
                return line
    return _first_answer_line(llm_answer)


def _first_answer_line(text: str) -> str | None:
    """LLM 回复里取最终答案：跳过空行与「还需要更多信息」之类的应答。"""
    for raw in text.splitlines():
        line = raw.strip().strip('"').strip()
        if not line:
            continue
        if line.upper().rstrip(":：") in _NON_ANSWERS:
            return None
        return line
    return None


def _next_command(state: TaskState) -> str:
    sop = state.skills.recall(state.signature or "")
    index = len(state.observations)
    return sop[index] if index < len(sop) else _EXPLORE_CMD


def _prompt(phase: str, state: TaskState) -> str:
    observed = " | ".join(state.observations)
    return (
        f"task={phase}\nobserved={observed}\nnext={_next_command(state)}\n"
        "只输出最终答案本身（单行、不要解释）；信息还不够就只输出 NEXT"
    )


def _signature(phase: str) -> str:
    return " ".join(phase.split())[:40]


def _reset(state: TaskState) -> None:
    """任务结束后的清场。

    必须连 ``pending_prompt`` / ``llm_answer`` 一起清：它们属于**上一题**。
    旧实现只清 observations/signature，于是上一题的 llmResp 会被当成本题的第一条
    观测 —— 真机表现就是「每题开局立刻交上一题答案」（判题器回 errorCode 2）或
    永远卡在旧答案上不再推进。
    """
    state.signature = None
    state.pending_cmd = None
    state.pending_prompt = False
    state.commands_sent = []
    state.observations = []
    state.llm_answer = ""
    state.accept_point = None  # 上一题已结束 → 允许重新领取
    state.accept_round = -1


def _enabled(config: Config | None) -> bool:
    value = config.get("tasks.self_evolution_enabled") if config is not None else None
    return value is not False


def _must_return_home(view: WorldView, config: Config | None) -> bool:
    """是否必须回防：夜晚，或白天已进入黄昏就位阶段。"""
    if view.is_night():
        return True
    threshold = _int_config(config, "economy.dusk_return", 70)
    return (view.round_no - 1) % _DAY_LENGTH >= threshold


def _int_config(config: Config | None, key: str, default: int) -> int:
    value = config.get(key) if config is not None else None
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _enough_time(view: WorldView, config: Config | None) -> bool:
    """白天剩余回合是否足以完成一个任务（避免跨夜超时，§4.4 T-03）。"""
    if not view.is_day():
        return False
    remaining = _DAY_ROUNDS - (view.round_no - 1) % _DAY_LENGTH
    return remaining > _margin(config)


def _margin(config: Config | None) -> int:
    value = config.get("tasks.timeout_margin_rounds") if config is not None else None
    return value if isinstance(value, int) and not isinstance(value, bool) else 10
