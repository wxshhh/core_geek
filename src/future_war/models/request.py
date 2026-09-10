"""Request 树：判题器每回合 POST 的战场状态（docs/接口文档.md §1）。

全部为 frozen/slots dataclass（不可变值对象，仅标准库）。
- 解析：见 codec.parse_request（缺省/未知字段容错，ParseError 可捕获）。
- 序列化：to_dict()（供回放/日志/往返测试）。
- 未知顶层字段原样保留在 Request.raw（浅拷贝），避免后续回合丢数据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .enums import NeutralType, RobotRoleType, RoleType, TeamType, enum_to_str


@dataclass(frozen=True, slots=True)
class Pos:
    """坐标结构体（§1.2.2）。"""

    x: int
    y: int

    def to_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y}


@dataclass(frozen=True, slots=True)
class Zone:
    """地图中立区域元素（§1.2.1）。"""

    pos: Pos
    neutralType: NeutralType | str

    def to_dict(self) -> dict[str, object]:
        return {"pos": self.pos.to_dict(), "neutralType": enum_to_str(self.neutralType)}


@dataclass(frozen=True, slots=True)
class MapInfo:
    """地图整体信息（§1.2）。"""

    width: int
    height: int
    zones: tuple[Zone, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "width": self.width,
            "height": self.height,
            "zones": [z.to_dict() for z in self.zones],
        }


@dataclass(frozen=True, slots=True)
class Role:
    """单位通用属性（§1.3.1）。level 仅建筑持有；cooldown 仅火箭发射台非零。"""

    id: int
    pos: Pos
    roleType: RoleType | str
    health: int
    attackPower: int = 0
    attackRange: int = 0
    backPackCapability: int = 0
    backpack: tuple[str, ...] = ()
    level: int | None = None
    cooldown: int = 0

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "id": self.id,
            "pos": self.pos.to_dict(),
            "roleType": enum_to_str(self.roleType),
            "health": self.health,
            "attackPower": self.attackPower,
            "attackRange": self.attackRange,
            "backPackCapability": self.backPackCapability,
            "backpack": list(self.backpack),
            "cooldown": self.cooldown,
        }
        if self.level is not None:
            out["level"] = self.level
        return out


@dataclass(frozen=True, slots=True)
class PlayerTask:
    """任务点信息（§1.3.2）。"""

    taskType: str
    taskPosition: Pos
    coldDownRounds: int = 0
    scoreReward: int = 0
    goldReward: int = 0
    isValid: bool = False
    timeoutRounds: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "taskType": self.taskType,
            "taskPosition": self.taskPosition.to_dict(),
            "coldDownRounds": self.coldDownRounds,
            "scoreReward": self.scoreReward,
            "goldReward": self.goldReward,
            "isValid": self.isValid,
            "timeoutRounds": self.timeoutRounds,
        }


@dataclass(frozen=True, slots=True)
class TeamOur:
    """我方队伍全部信息（§1.3）。"""

    type: TeamType | str
    teamId: str = ""
    teamName: str = ""
    goldNum: int = 0
    totalScore: int = 0
    playerTasks: tuple[PlayerTask, ...] = ()
    roles: tuple[Role, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "type": enum_to_str(self.type),
            "teamId": self.teamId,
            "teamName": self.teamName,
            "goldNum": self.goldNum,
            "totalScore": self.totalScore,
            "playerTasks": [t.to_dict() for t in self.playerTasks],
            "roles": [r.to_dict() for r in self.roles],
        }


@dataclass(frozen=True, slots=True)
class TeamEnemy:
    """敌方可见信息（§1.4）：基地/围墙全图可见，其余单位需进入己方视野。"""

    roles: tuple[Role, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {"roles": [r.to_dict() for r in self.roles]}


@dataclass(frozen=True, slots=True)
class RobotRole:
    """机器人单位（§1.5.1），全图可见。"""

    id: int
    pos: Pos
    roleType: RobotRoleType | str
    health: int
    abnormalState: str = ""
    targetTeam: TeamType | str | None = None

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "id": self.id,
            "pos": self.pos.to_dict(),
            "roleType": enum_to_str(self.roleType),
            "health": self.health,
            "abnormalState": self.abnormalState,
        }
        if self.targetTeam is not None:
            out["targetTeam"] = enum_to_str(self.targetTeam)
        return out


@dataclass(frozen=True, slots=True)
class Robot:
    """当前场上全部存活机器人（§1.5）。"""

    roles: tuple[RobotRole, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {"roles": [r.to_dict() for r in self.roles]}


@dataclass(frozen=True, slots=True)
class WorldNews:
    """世界消息（§1.6）：官方消息与民间传闻。"""

    officialNews: str = ""
    folkLegends: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"officialNews": self.officialNews, "folkLegends": self.folkLegends}


@dataclass(frozen=True, slots=True)
class Error:
    """错误结构体（§1.7）。"""

    errorCode: int
    description: str = ""

    def to_dict(self) -> dict[str, object]:
        return {"errorCode": self.errorCode, "description": self.description}


@dataclass(frozen=True, slots=True)
class ShopItem:
    """商店条目（§1.1）：{name, price}。"""

    name: str
    price: int

    def to_dict(self) -> dict[str, int | str]:
        return {"name": self.name, "price": self.price}


@dataclass(frozen=True, slots=True)
class Request:
    """判题器每回合 POST 的完整战场状态（§1.1）。

    注意：本对象不可哈希（lastRoundRoleActionResults 为 dict 字段）。
    """

    roundNo: int
    mapInfo: MapInfo
    teamOur: TeamOur
    teamEnemy: TeamEnemy
    robot: Robot
    phaseTask: str = ""
    lastRoundRoleActionResults: dict[int, bool] = field(default_factory=dict)
    lastSummonTreasureResult: int = 0
    llmResp: str = ""
    worldNews: WorldNews = field(default_factory=WorldNews)
    lastCmdResult: str = ""
    vendorShopList: tuple[ShopItem, ...] = ()
    weaponShopList: tuple[ShopItem, ...] = ()
    errors: tuple[Error, ...] = ()
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)

    def to_dict(self) -> dict[str, object]:
        """规范化为 JSON 兼容 dict；raw 中未知顶层字段浅拷贝保留。"""
        out: dict[str, object] = dict(self.raw)
        out.update(
            {
                "roundNo": self.roundNo,
                "mapInfo": self.mapInfo.to_dict(),
                "teamOur": self.teamOur.to_dict(),
                "teamEnemy": self.teamEnemy.to_dict(),
                "robot": self.robot.to_dict(),
                "phaseTask": self.phaseTask,
                "lastRoundRoleActionResults": {
                    str(k): v for k, v in self.lastRoundRoleActionResults.items()
                },
                "lastSummonTreasureResult": self.lastSummonTreasureResult,
                "llmResp": self.llmResp,
                "worldNews": self.worldNews.to_dict(),
                "lastCmdResult": self.lastCmdResult,
                "vendorShopList": [i.to_dict() for i in self.vendorShopList],
                "weaponShopList": [i.to_dict() for i in self.weaponShopList],
                "errors": [e.to_dict() for e in self.errors],
            }
        )
        return out
