"""接口契约中的枚举与常量（docs/接口文档.md §1.2–§2.3）。

所有枚举基于 str 基类：成员可直接与协议字符串比较（`RoleType.WORKER == "worker"`），
序列化时取 `.value`。解析层对未知枚举取值**不拒绝**（原样保留字符串），
保证判题器协议升级时 Bot 不会崩溃。
"""

from __future__ import annotations

from enum import Enum
from typing import Final, TypeVar

EnumT = TypeVar("EnumT", bound=Enum)


def enum_to_str(value: EnumT | str) -> str:
    """把枚举成员或原始字符串规范为协议字符串。"""
    return value.value if isinstance(value, Enum) else value


class TeamType(str, Enum):
    """阵营标识（§1.3）。"""

    CHALLENGER = "challenger"
    DEFENDER = "defender"


class RoleType(str, Enum):
    """单位分类（§1.3.1）。"""

    STATION = "station"
    GATLING = "gatling"
    RAILGUN = "railgun"
    ROCKET = "rocket"
    WALL = "wall"
    PIONEER = "pioneer"
    WORKER = "worker"


class NeutralType(str, Enum):
    """地图中立元素类型（§1.2.1）。"""

    STONE = "stone"
    IRON = "iron"
    COPPER = "copper"
    VENDOR = "vendor"
    WEAPON_SHOP = "weaponShop"
    CHALLENGER_TASK_POINT_1 = "challengerTaskPoint1"
    CHALLENGER_TASK_POINT_2 = "challengerTaskPoint2"
    DEFENDER_TASK_POINT_1 = "defenderTaskPoint1"
    DEFENDER_TASK_POINT_2 = "defenderTaskPoint2"


class RobotRoleType(str, Enum):
    """机器人类型（§1.5.1）。"""

    SMALL_ROBOT = "smallRobot"
    MIDDLE_ROBOT = "middleRobot"
    LARGE_ROBOT = "largeRobot"
    BOSS_ROBOT = "bossRobot"


class Action(str, Enum):
    """角色动作码全集（§2.3）。"""

    MOVE = "move"
    ATTACK = "attack"
    SELL = "sell"
    BUY = "buy"
    BUILD = "build"
    REMOVE = "remove"
    ACCEPT_TASK = "acceptTask"
    SUBMIT_ANSWER = "submitAnswer"
    SUMMON_TREASURE = "summonTreasure"
    USE = "use"
    DROP = "drop"
    COLLECT = "collect"


class ErrorCode(int, Enum):
    """判题器错误码（§1.7）。"""

    UNKNOWN = 0
    TASK_TIMEOUT = 1
    WRONG_ANSWER = 2
    NETWORK_ERROR = 3
    INVALID_COMMAND = 4
    LLM_QUOTA_EXCEEDED = 5


class SummonTreasureResult(int, Enum):
    """summonTreasure 上回合结果码（§1.1）。"""

    NOT_USED = 0
    SUCCESS = 1
    NO_TREASURE = 2
    WRONG_OFFERINGS = 3
    EMPTY = 4


class FixedRoleIds:
    """固定角色 ID 分配表（§1.3.1）。墙 ID 自 base 起顺序分配。"""

    CHALLENGER_WORKER1: Final = 10010
    CHALLENGER_PIONEER: Final = 10011
    CHALLENGER_WORKER2: Final = 10012
    CHALLENGER_STATION: Final = 10013
    CHALLENGER_GATLINGS: Final = (10020, 10021, 10022)
    CHALLENGER_RAILGUNS: Final = (10030, 10031, 10032)
    CHALLENGER_ROCKETS: Final = (10040, 10041, 10042)
    CHALLENGER_WALL_BASE: Final = 40000

    DEFENDER_WORKER1: Final = 20010
    DEFENDER_PIONEER: Final = 20011
    DEFENDER_WORKER2: Final = 20012
    DEFENDER_STATION: Final = 20013
    DEFENDER_GATLINGS: Final = (20020, 20021, 20022)
    DEFENDER_RAILGUNS: Final = (20030, 20031, 20032)
    DEFENDER_ROCKETS: Final = (20040, 20041, 20042)
    DEFENDER_WALL_BASE: Final = 41000


def role_kind(role_id: int) -> str | None:
    """按固定 ID 反查角色种类（worker1/pioneer/worker2/station/gatling/railgun/rocket/wall）。"""
    if role_id in (FixedRoleIds.CHALLENGER_WORKER1, FixedRoleIds.DEFENDER_WORKER1):
        return "worker1"
    if role_id in (FixedRoleIds.CHALLENGER_PIONEER, FixedRoleIds.DEFENDER_PIONEER):
        return "pioneer"
    if role_id in (FixedRoleIds.CHALLENGER_WORKER2, FixedRoleIds.DEFENDER_WORKER2):
        return "worker2"
    if role_id in (FixedRoleIds.CHALLENGER_STATION, FixedRoleIds.DEFENDER_STATION):
        return "station"
    if role_id in (*FixedRoleIds.CHALLENGER_GATLINGS, *FixedRoleIds.DEFENDER_GATLINGS):
        return "gatling"
    if role_id in (*FixedRoleIds.CHALLENGER_RAILGUNS, *FixedRoleIds.DEFENDER_RAILGUNS):
        return "railgun"
    if role_id in (*FixedRoleIds.CHALLENGER_ROCKETS, *FixedRoleIds.DEFENDER_ROCKETS):
        return "rocket"
    if FixedRoleIds.CHALLENGER_WALL_BASE <= role_id < FixedRoleIds.DEFENDER_WALL_BASE:
        return "wall"
    if role_id >= FixedRoleIds.DEFENDER_WALL_BASE:
        return "wall"
    return None


def team_of_role_id(role_id: int) -> TeamType | None:
    """按固定 ID 反查所属阵营；未知 ID 返回 None。

    约定：10000–19999 为挑战者、20000–29999 为防守者（§1.3.1 分配表），
    墙 ID 区间 40000–40999 / 41000+ 分别归属两阵营。
    """
    if FixedRoleIds.CHALLENGER_WALL_BASE <= role_id < FixedRoleIds.DEFENDER_WALL_BASE:
        return TeamType.CHALLENGER
    if role_id >= FixedRoleIds.DEFENDER_WALL_BASE:
        return TeamType.DEFENDER
    if 10000 <= role_id < 20000:
        return TeamType.CHALLENGER
    if 20000 <= role_id < 30000:
        return TeamType.DEFENDER
    return None
