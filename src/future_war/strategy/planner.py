"""回合规划器（工作包 13/21/22，方案 M4）：把世界快照编排成角色指令。

白天走经济（工人采矿/贩卖/建造）+ 开拓者任务/寻宝；夜晚走防御（武器操控/攻击）。
产出 ``TurnPlan``（指令 + 可选 LLM prompt / 沙盒命令），供 Bot 组装 Response。
仅用标准库。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from future_war.config import Config
from future_war.models import Pos, RoleCommand, enum_to_str
from future_war.core.nav import resolve_moves
from future_war.core.world_view import WorldView
from future_war.strategy.builder import plan_upgrades
from future_war.strategy.combat import plan_defense
from future_war.strategy.consumables import ConsumableState, plan_consumables
from future_war.strategy.economy import EconomyState, plan_economy
from future_war.strategy.offense import OffenseState, plan_offense
from future_war.strategy.task_agent import TaskState, plan_task
from future_war.strategy.treasure import TreasureState, plan_treasure


@dataclass(frozen=True, slots=True)
class TurnPlan:
    """一回合产出：角色指令 + 可选的 LLM prompt / 沙盒命令（接口 §2.1）。"""

    commands: dict[int, RoleCommand] = field(default_factory=dict)
    prompt: str = ""
    execute_cmd: str = ""
    notes: tuple[str, ...] = ()  # 决策摘要（供日志 D-02；不进协议响应）


def _resolve_global_moves(
    view: WorldView, commands: dict[int, RoleCommand]
) -> None:
    """回合末对**全部模块**产出的 move 做一次全局同步解析（就地改写 commands）。

    为什么必须放在这里：``economy`` / ``combat`` 只在各自模块内部用
    ``core.nav.resolve_moves`` 防抢格/防互换，而 ``task_agent`` / ``treasure``
    只有单单位的 ``plan_move``。于是「开拓者去任务点」与「工人去矿点」这两条
    各自都合法的步，合并后可能指向同一空格或构成互换 —— 判题器统一结算时
    这类 move 全部非法（线上 issue #1：连续整帧 ``ok:0,fail:N``，开拓者到不了
    任务点、采矿工人到不了矿点，0 堵墙）。这里用一次全局解析把最后一道关补上：
    后解析者避开先解析者的目标格，跨模块的争抢/互换在**发出前**就被消掉。

    只动 move：其余动作（build/collect/sell/attack/acceptTask…）原样保留，
    优先级与合并顺序都不受影响（原地待命永远优于发一条注定非法的 move）。
    """
    goals: dict[int, Pos] = {}
    for uid, command in commands.items():
        if enum_to_str(command.action) == "move" and command.targetPos:
            # resolve_moves 的 goals 语义是「目标格」：传已算好的 next step 也成立
            # （该格可用即返回该格本身，不可用则返回朝它靠拢的合法一步）。
            goals[uid] = command.targetPos[0]
    # 只有一条 move 时不可能跨模块争抢：单条指令本就被 plan_move / resolve_moves
    # 校验过是合法步，这里省下一次 BFS（每帧 ~3ms）。
    if len(goals) < 2:
        return
    for uid, step in resolve_moves(view, goals).items():
        command = commands.get(uid)
        if command is None:  # 非移动单位：resolve_moves 不认，保持原指令
            continue
        if step is None:
            del commands[uid]  # 无合法步 → 移除 move，原地待命
        else:
            commands[uid] = replace(command, targetPos=(step,))


def plan_turn(
    view: WorldView,
    config: Config | None = None,
    economy_state: EconomyState | None = None,
    treasure_state: TreasureState | None = None,
    task_state: TaskState | None = None,
    offense_state: OffenseState | None = None,
    consumable_state: ConsumableState | None = None,
) -> TurnPlan:
    """按昼夜选择行为模块，返回本回合 TurnPlan。

    合并优先级（后者只在角色还空着时补位）：经济/防御 → 消耗品（含保命回血）
    → 升级券 → 任务/寻宝。保命回血排在任务之前，是因为角色阵亡 = 20 回合无操控
    （§4.5.2），比多做一轮任务重要。

    所有模块的指令合完后统一走一次 :func:`_resolve_global_moves`：move 是唯一
    需要**跨模块**协商的动作，否则两个模块各自合法的步会争抢同一空格。
    """
    if view.is_night():
        commands = plan_defense(view, config)
        commands.update(
            plan_offense(view, config, frozenset(commands), offense_state)
        )
        _resolve_global_moves(view, commands)
        attacks = sum(
            1 for c in commands.values() if enum_to_str(c.action) == "attack"
        )
        return TurnPlan(
            commands=commands,
            notes=(f"night weapons={len(view.own_weapons())} attacks={attacks}",),
        )
    commands = plan_economy(view, config, economy_state)
    notes: list[str] = list(economy_state.notes) if economy_state is not None else []
    for uid, command in plan_consumables(view, config, consumable_state).items():
        commands.setdefault(uid, command)  # 经济指令优先：不能为了喝药停下建造
    for uid, command in plan_upgrades(view, config).items():
        commands.setdefault(uid, command)
    task = plan_task(view, config, task_state)
    commands.update(task.commands)
    # 回答「接任务后 phase 为什么判空、为何反复 acceptTask」：每条都带上 phaseTask
    # 的字符数（0 = 判题器没给任务描述）与本回合发出的任务动作
    phase_text = view.phase_task()
    task_actions = "+".join(
        sorted(enum_to_str(c.action) for c in task.commands.values())
    )
    notes.append(
        f"phaseTask_len={len(phase_text)} task_cmd={task_actions or '-'}"
        f" prompt={int(bool(task.prompt))} cmd={int(bool(task.execute_cmd))}"
    )
    if not task.commands:
        treasure = plan_treasure(view, config, treasure_state)
        commands.update(treasure)
        if treasure:
            notes.append(f"treasure={len(treasure)}")
    _resolve_global_moves(view, commands)
    return TurnPlan(
        commands=commands,
        prompt=task.prompt,
        execute_cmd=task.execute_cmd,
        notes=tuple(notes),
    )
