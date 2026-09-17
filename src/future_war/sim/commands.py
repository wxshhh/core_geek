"""指令结构性校验（任务书 §八「指令错误」口径）。

把一条指令区分为两类失败：

- **队伍异常**（结构性问题，计 1 次队伍异常、errorCode 4）：动作码不可识别、
  必填字段缺失或无法识别（如 move/attack 缺 targetPos、use 眩晕/炸弹缺
  targetPos，§八注）。由 ``validate_structure`` 以 ``CommandError`` 报出。
- **指令执行失败**（规则性问题，仅该条指令无效、不计异常）：移动碰撞、
  落点无目标、金币不足等，由 engine 返回 False 处理。

表与判据本身放在 ``core/command_contract.py``：它同时被生产侧（planner 发送前
自检，避免把一条结构非法指令送到真机上白烧一次队伍异常配额）与这里复用，两边
共用一份表才不会漂移。本模块只负责把它包成本模拟器/测试用的异常类型。
"""

from __future__ import annotations

from future_war.core.command_contract import (  # noqa: F401 — 兼容旧导入路径
    REQUIRED_FIELDS,
    USE_NEEDS_TARGET,
    structural_error,
)
from future_war.models import RoleCommand


class CommandError(Exception):
    """结构性指令错误（队伍异常口径，§八）。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def validate_structure(cmd: RoleCommand) -> None:
    """校验指令结构；结构性问题抛 CommandError（= 队伍异常）。"""
    reason = structural_error(cmd)
    if reason is not None:
        raise CommandError(reason)
