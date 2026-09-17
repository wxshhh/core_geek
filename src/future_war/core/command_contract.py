"""角色指令的结构契约（任务书 §八「指令错误」口径 + 接口 §2.2/§2.3）。

为什么单独抽出一个核心模块（而不是只留在 ``sim/commands.py``）：**这是判题器
计队伍异常的判据**，线上代价是「累计 5 次即停止调度该队」。生产侧（planner）必须
能拿同一张表做发送前自检，而模拟器侧也要用它复现判题器口径 —— 两边共用一份表，
才不会出现「模拟器说合法、真机判非法」这类漂移。

口径（§八注，逐字对应）：

- **指令错误**（计 1 次队伍异常）＝ 动作码不可识别 / 必填字段缺失或无法识别。
  例：``move``/``attack`` 缺 ``targetPos``、``use`` 眩晕法宝/范围炸弹未指定
  ``targetPos``、动作码非法。
- **指令执行失败**（不计异常）＝ 指令结构与字段都合法，只是规则上没生效
  （移动碰撞、落点无目标、金币不足、对非黄区格 ``build`` 等）。这类**不是**本
  模块的职责 —— 它由游戏规则判定，绝不能在这里拦。

仅用标准库。
"""

from __future__ import annotations

from typing import Final

from future_war.models import Action, RoleCommand, enum_to_str

# 各动作的必填字段（接口 §2.3「需指定」列）
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

# ``use`` 中「必须指定 targetPos」的物品（§八注：眩晕法宝 / 范围炸弹）。
# 围墙修复包与升级券同样需要坐标，但它们本来就由调用方带坐标发出，不在此表里
# 硬拦 —— 少带坐标只会算「指令执行失败」，而不是结构错误。
USE_NEEDS_TARGET: Final = frozenset({"DizzyWeapon", "Bomb"})

_ACTION_VALUES: Final = frozenset(action.value for action in Action)


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


def structural_error(cmd: RoleCommand) -> str | None:
    """违反结构契约则返回原因文案（与判题器口径一致），合法则返回 ``None``。

    返回文案而不是抛异常：生产侧要「丢掉这条指令 + 记一行诊断」，模拟器侧要把它
    包成 ``CommandError`` 计异常 —— 两种用法都只需要原因字符串。
    """
    action = enum_to_str(cmd.action)
    if action not in _ACTION_VALUES:
        return f"unknown action {action!r}"
    missing = [field for field in REQUIRED_FIELDS[action] if _field_missing(cmd, field)]
    if missing:
        return f"{action} missing required field(s): {', '.join(missing)}"
    if action == Action.USE.value and cmd.name in USE_NEEDS_TARGET and not cmd.targetPos:
        return f"use {cmd.name} requires targetPos"
    if cmd.num < 1:
        return f"{action} num must be >= 1, got {cmd.num}"
    return None
