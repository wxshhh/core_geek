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

_ANSWER_MARKER: Final = "ANSWER:"
_EXPLORE_CMD: Final = "echo explore"


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


@dataclass(frozen=True, slots=True)
class TaskAction:
    """一回合任务产出：角色指令 + 可选的 LLM prompt / 沙盒命令。"""

    commands: dict[int, RoleCommand]
    prompt: str = ""
    execute_cmd: str = ""


def plan_task(
    view: WorldView, config: Config | None = None, state: TaskState | None = None
) -> TaskAction:
    """推进自进化任务：领取 / 沙盒探索 / 提交答案。"""
    if not _enabled(config):
        return TaskAction({})
    state = state if state is not None else TaskState()
    pioneer = view.own_pioneer()
    if not pioneer:
        return TaskAction({})
    unit = pioneer[0]
    phase = view.phase_task()

    if state.signature is not None and not phase:
        state.skills.record(state.signature, tuple(state.commands_sent))
        _reset(state)

    if not phase:
        return _seek_task(view, unit)

    if state.signature is None:
        state.signature = _signature(phase)
        state.commands_sent = []
        state.observations = []

    _digest_result(view, state)
    answer = _answer_from(state.observations)
    if answer is not None:
        return TaskAction(
            {unit.id: RoleCommand(action=Action.SUBMIT_ANSWER, taskAnswer=answer)}
        )
    if state.pending_cmd is None:
        command = _next_command(state)
        state.pending_cmd = command
        state.commands_sent.append(command)
        return TaskAction({}, prompt=_prompt(phase, state), execute_cmd=command)
    return TaskAction({})


def _seek_task(view: WorldView, unit) -> TaskAction:
    points = view.own_task_points()
    if not points:
        return TaskAction({})
    target = min(points, key=lambda p: (chebyshev(unit.pos, p), p.x, p.y))
    if chebyshev(unit.pos, target) <= 1:
        return TaskAction({unit.id: RoleCommand(action=Action.ACCEPT_TASK)})
    step = plan_move(view, unit.id, target)
    if step is None:
        return TaskAction({})
    return TaskAction({unit.id: RoleCommand(action=Action.MOVE, targetPos=(step,))})


def _digest_result(view: WorldView, state: TaskState) -> None:
    if state.pending_cmd is None:
        return
    result = view.last_cmd_result()
    if result:
        state.observations.append(result)
    state.pending_cmd = None


def _answer_from(observations: list[str]) -> str | None:
    for observation in observations:
        if _ANSWER_MARKER in observation:
            tail = observation.split(_ANSWER_MARKER, 1)[1].strip()
            return tail.splitlines()[0].strip() if tail else None
    return None


def _next_command(state: TaskState) -> str:
    sop = state.skills.recall(state.signature or "")
    index = len(state.observations)
    return sop[index] if index < len(sop) else _EXPLORE_CMD


def _prompt(phase: str, state: TaskState) -> str:
    observed = " | ".join(state.observations)
    return f"task={phase}\nobserved={observed}\nnext={_next_command(state)}"


def _signature(phase: str) -> str:
    return " ".join(phase.split())[:40]


def _reset(state: TaskState) -> None:
    state.signature = None
    state.pending_cmd = None
    state.commands_sent = []
    state.observations = []


def _enabled(config: Config | None) -> bool:
    value = config.get("tasks.self_evolution_enabled") if config is not None else None
    return value is not False
