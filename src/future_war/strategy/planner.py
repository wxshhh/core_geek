"""回合规划器（工作包 13/21/22，方案 M4）：把世界快照编排成角色指令。

白天走经济（工人采矿/贩卖/建造）+ 开拓者任务/寻宝；夜晚走防御（武器操控/攻击）。
产出 ``TurnPlan``（指令 + 可选 LLM prompt / 沙盒命令），供 Bot 组装 Response。
仅用标准库。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Final

from future_war.config import Config
from future_war.models import Pos, RoleCommand, enum_to_str
from future_war.core.command_contract import structural_error
from future_war.core.nav import resolve_moves
from future_war.core.world_map import chebyshev, in_bounds
from future_war.core.world_view import WorldView
from future_war.strategy.builder import plan_upgrades
from future_war.strategy.combat import plan_defense
from future_war.strategy.consumables import ConsumableState, plan_consumables
from future_war.strategy.economy import EconomyState, plan_economy
from future_war.strategy.offense import OffenseState, plan_offense
from future_war.strategy.task_agent import TaskState, plan_task
from future_war.strategy.treasure import TreasureState, plan_treasure

_DIRS: Final = ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1))
_MOBILE_KINDS: Final = frozenset({"pioneer", "worker"})


@dataclass(frozen=True, slots=True)
class TurnPlan:
    """一回合产出：角色指令 + 可选的 LLM prompt / 沙盒命令（接口 §2.1）。"""

    commands: dict[int, RoleCommand] = field(default_factory=dict)
    prompt: str = ""
    execute_cmd: str = ""
    notes: tuple[str, ...] = ()  # 决策摘要（供日志 D-02；不进协议响应）


def _resolve_global_moves(
    view: WorldView, commands: dict[int, RoleCommand]
) -> tuple[str, ...]:
    """回合末对**全部模块**产出的 move 做一次全局同步解析（就地改写 commands）。

    为什么必须放在这里：``economy`` / ``combat`` 只在各自模块内部用
    ``core.nav.resolve_moves`` 防抢格/防互换，而 ``task_agent`` / ``treasure``
    只有单单位的 ``plan_move``。于是「开拓者去任务点」与「工人去矿点」这两条
    各自都合法的步，合并后可能指向同一空格或构成互换 —— 判题器统一结算时
    这类 move 全部非法（线上 issue #1：连续整帧 ``ok:0,fail:N``，开拓者到不了
    任务点、采矿工人到不了矿点，0 堵墙）。这里用一次全局解析把最后一道关补上：
    后解析者避开先解析者的目标格，跨模块的争抢/互换在**发出前**就被消掉。

    解析不出步（``step is None``）时**不再整条删除 move**：删掉 = 该单位整回合零
    指令，判题器只能让它原地不动，真机表现就是「工人/开拓者站着不动」。这里改用
    :func:`_fallback_step` 保底 —— 朝目标横移一格（不抢格、不互换）也比什么都不发
    强；只有四周被彻底堵死才删（并记 ``idle=<id>:move-resolve-failed``）。

    返回本回合的移动解析诊断（进 D-02 行）：``move_fallback=<ids>``=触发保底、
    ``idle=<id>:move-resolve-failed``=连保底步都没有。
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
        return ()
    resolved = resolve_moves(view, goals)
    taken: set[Pos] = set()
    fallback: list[int] = []
    dropped: list[int] = []
    for uid in sorted(goals):  # 与 resolve_moves 默认优先级（id 升序）一致
        command = commands.get(uid)
        if command is None:  # 非移动单位：resolve_moves 不认，保持原指令
            continue
        step = resolved.get(uid)
        if step is not None:
            commands[uid] = replace(command, targetPos=(step,))
            taken.add(step)
            continue
        alt = _fallback_step(view, uid, goals[uid], taken)
        if alt is None:
            del commands[uid]  # 确实无路可走（被围死）→ 原地待命
            dropped.append(uid)
        else:
            commands[uid] = replace(command, targetPos=(alt,))
            taken.add(alt)
            fallback.append(uid)
    notes: list[str] = []
    if fallback:
        notes.append("move_fallback=" + ",".join(str(uid) for uid in fallback))
    if dropped:
        notes.append(
            "idle=" + ",".join(f"{uid}:move-resolve-failed" for uid in dropped)
        )
    return tuple(notes)


def _fallback_step(
    view: WorldView, uid: int, goal: Pos, taken: set[Pos]
) -> Pos | None:
    """解析失败的 move 的保底步：朝目标挪一格，且保证不抢格、不互换。

    为什么不是直接 ``plan_move``：``plan_move`` 只看**当前**阻挡，不知道队友本回合
    的意图，会把刚好被占的目标格再发一次（判题器判定双方 move 全非法，正是本函数
    要修的那个坑）。这里把「全部己方角色当前格 + 已定案目标格」一起当作阻挡：
    踩人不可能、互换也不可能（要互换就必须踏入别人的当前格）。
    返回 ``None`` = 连相邻空格都没有（被彻底围死）。
    """
    role = next(
        (
            item
            for item in view.dynamic.own_roles
            if item.id == uid and enum_to_str(item.roleType) in _MOBILE_KINDS
        ),
        None,
    )
    if role is None:
        return None
    blocked = view.obstacles()  # 含己方全部角色当前格（也就含 role 自己）
    width, height = view.static_map.width, view.static_map.height
    free = [
        cell
        for cell in (
            Pos(role.pos.x + dx, role.pos.y + dy) for dx, dy in _DIRS
        )
        if in_bounds(cell, width, height) and cell not in blocked and cell not in taken
    ]
    if not free:
        return None
    return min(free, key=lambda cell: (chebyshev(cell, goal), cell.x, cell.y))


def _drop_structural_illegal(commands: dict[int, RoleCommand]) -> tuple[str, ...]:
    """发送前自检：丢掉结构非法的指令，并留下 ``illegal_dropped=`` 诊断。

    为什么必须有这道闸（任务书 §八）：**单队异常累计 5 次就停止调度该队**，而
    「指令错误」＝ 字段缺失/动作码不可识别 —— 一条缺 ``targetPos`` 的 ``move`` 就
    能烧掉一次配额，代价远大于少发一条指令。判据与判题器口径共用
    ``core/command_contract``（与 ``sim.commands`` 同表），所以这里拦得住的，
    真机也一定会判非法。

    注意**只**拦结构问题：对非黄区格 ``build``、金币不足 ``buy`` 之类属于
    「指令执行失败」，判题器只标记该条无效、不计异常，绝不能在这里误删
    （否则我们会静默失去整条施工线）。
    """
    dropped: list[str] = []
    for uid, command in list(commands.items()):
        reason = structural_error(command)
        if reason is None:
            continue
        del commands[uid]
        dropped.append(f"{uid}:{reason}")
    if not dropped:
        return ()
    return ("illegal_dropped=" + "+".join(dropped),)


def _error_notes(view: WorldView) -> tuple[str, ...]:
    """把判题器本轮的 ``errors`` 原文压成一行（无错误则空）。

    为什么要在 ``D-02`` 里复述：真机上唯一可见通道是平台捕获的 stderr，而
    ``M-01`` 只给「本轮几条错误」的**计数**。2026-09-17 的线上事故正是被这个
    计数卡住 —— 只能看到 ``errors=1``，分不清是 errorCode 4（指令错误，会烧掉
    5 次配额）还是 errorCode 2（答案错误，纯任务侧失分），排查全靠猜。这里连
    ``errorCode`` 与描述一起打印，下一局直接指认。
    """
    items = []
    for error in view.errors():
        description = " ".join(str(getattr(error, "description", "")).split())[:48]
        items.append(
            f"{getattr(error, 'errorCode', '?')}:{description}" if description
            else str(getattr(error, "errorCode", "?"))
        )
    if not items:
        return ()
    return ("errs=" + ",".join(items),)


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
        illegal_notes = _drop_structural_illegal(commands)
        move_notes = _resolve_global_moves(view, commands)
        attacks = sum(
            1 for c in commands.values() if enum_to_str(c.action) == "attack"
        )
        return TurnPlan(
            commands=commands,
            notes=(
                f"night weapons={len(view.own_weapons())} attacks={attacks}",
                *illegal_notes,
                *move_notes,
                *_error_notes(view),
            ),
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
    # 任务侧为什么停着（不接受/不提交/原地等 phaseTask）：真机日志直接指认
    if task.stall:
        notes.append(f"task_stall={task.stall}")
    if not task.commands:
        treasure = plan_treasure(view, config, treasure_state)
        commands.update(treasure)
        if treasure:
            notes.append(f"treasure={len(treasure)}")
    notes.extend(_drop_structural_illegal(commands))
    notes.extend(_resolve_global_moves(view, commands))
    notes.extend(_error_notes(view))
    return TurnPlan(
        commands=commands,
        prompt=task.prompt,
        execute_cmd=task.execute_cmd,
        notes=tuple(notes),
    )
