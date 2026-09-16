"""指令结构性校验（任务书 §八「指令错误」口径）。

把一条指令区分为两类失败：

- **队伍异常**（结构性问题，计 1 次队伍异常、errorCode 4）：动作码不可识别、
  必填字段缺失或无法识别（如 move/attack 缺 targetPos、use 眩晕/炸弹缺
  targetPos，§八注）。由 ``validate_structure`` 以 ``CommandError`` 报出。
- **指令执行失败**（规则性问题，仅该条指令无效、不计异常）：移动碰撞、
  落点无目标、金币不足等，由 engine 返回 False 处理。

动作码必填字段映射自接口 §2.2/§2.3 的「需指定」说明。
"""

from __future__ import annotations

from typing import Final

from future_war.models import Action, RoleCommand, enum_to_str


class CommandError(Exception):
    """结构性指令错误（队伍异常口径，§八）。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


_ACTION_VALUES: Final = frozenset(a.value for a in Action)

# 各动作的必填字段（接口 §2.3）
REQUIRED_FIELDS: Final = {
    "move": ("targetPos",),
    "attack": ("controllerId", "targetPos"),
    "sell": ("name",),
    "buy": ("name",),
    "build": ("name", "targetPos"),
    "remove": ("targetPos",),
    "acceptTask": (),
    "submitAnswer": ("taskAnswer",),
    "summonTreasure": ("targetPos", "item"),
    "use": ("name",),
    "drop": ("name",),
    "collect": ("targetPos",),
}

# use 动作中「必须指定 targetPos」的物品（§八注：眩晕法宝/范围炸弹）
_USE_NEEDS_TARGET: Final = frozenset({"DizzyWeapon", "Bomb"})


def _field_missing(cmd: RoleCommand, field: str) -> bool:
    """必填字段是否缺失/空（§八注：缺失即无法识别）。"""
    match field:
        case "targetPos":
            return not cmd.targetPos
        case "controllerId":
            return cmd.controllerId is None or cmd.controllerId == ""
        case "name":
            return cmd.name is None or cmd.name == ""
        case "taskAnswer":
            return cmd.taskAnswer is None
        case "item":
            return not cmd.item
        case unreachable:
            raise AssertionError(f"unknown required field {unreachable!r}")


def validate_structure(cmd: RoleCommand) -> None:
    """校验指令结构；结构性问题抛 CommandError（= 队伍异常）。"""
    action = enum_to_str(cmd.action)
    if action not in _ACTION_VALUES:
        raise CommandError(f"unknown action {action!r}")
    missing = [f for f in REQUIRED_FIELDS[action] if _field_missing(cmd, f)]
    if missing:
        raise CommandError(f"{action} missing required field(s): {', '.join(missing)}")
    if action == Action.USE.value and cmd.name in _USE_NEEDS_TARGET and not cmd.targetPos:
        raise CommandError(f"use {cmd.name} requires targetPos")
    if cmd.num < 1:
        raise CommandError(f"{action} num must be >= 1, got {cmd.num}")
