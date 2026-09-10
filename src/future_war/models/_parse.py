"""私有模块：Request 子树的解析函数（供 codec.parse_request 调用）。

每个函数把一段 JSON 子树解析为对应的 dataclass；错误经 ParseError 上报，
消息携带 JSON 路径（如 `$.mapInfo.zones[2].pos.x`）便于定位。
"""

from __future__ import annotations

from typing import Any

from ._jsonutil import (
    as_array,
    as_bool,
    as_int,
    as_object,
    as_str,
    enum_or_raw,
    fail,
    opt_str,
    required,
)
from .enums import NeutralType, RobotRoleType, RoleType, TeamType
from .request import (
    Error,
    MapInfo,
    PlayerTask,
    Pos,
    Robot,
    RobotRole,
    Role,
    ShopItem,
    TeamEnemy,
    TeamOur,
    WorldNews,
    Zone,
)


def parse_pos(value: Any, path: str) -> Pos:
    data = as_object(value, path)
    return Pos(
        x=as_int(required(data, "x", path), f"{path}.x"),
        y=as_int(required(data, "y", path), f"{path}.y"),
    )


def parse_zone(value: Any, path: str) -> Zone:
    data = as_object(value, path)
    return Zone(
        pos=parse_pos(required(data, "pos", path), f"{path}.pos"),
        neutralType=enum_or_raw(
            NeutralType, required(data, "neutralType", path), f"{path}.neutralType"
        ),
    )


def parse_map_info(value: Any, path: str) -> MapInfo:
    data = as_object(value, path)
    return MapInfo(
        width=as_int(required(data, "width", path), f"{path}.width"),
        height=as_int(required(data, "height", path), f"{path}.height"),
        zones=tuple(
            parse_zone(z, f"{path}.zones[{i}]")
            for i, z in enumerate(as_array(data.get("zones", []), f"{path}.zones"))
        ),
    )


def parse_role(value: Any, path: str) -> Role:
    data = as_object(value, path)
    level = data.get("level")
    return Role(
        id=as_int(required(data, "id", path), f"{path}.id"),
        pos=parse_pos(required(data, "pos", path), f"{path}.pos"),
        roleType=enum_or_raw(RoleType, required(data, "roleType", path), f"{path}.roleType"),
        health=as_int(required(data, "health", path), f"{path}.health"),
        attackPower=as_int(data.get("attackPower", 0), f"{path}.attackPower"),
        attackRange=as_int(data.get("attackRange", 0), f"{path}.attackRange"),
        backPackCapability=as_int(
            data.get("backPackCapability", 0), f"{path}.backPackCapability"
        ),
        backpack=tuple(
            as_str(b, f"{path}.backpack[{i}]")
            for i, b in enumerate(as_array(data.get("backpack", []), f"{path}.backpack"))
        ),
        level=None if level is None else as_int(level, f"{path}.level"),
        cooldown=as_int(data.get("cooldown", 0), f"{path}.cooldown"),
    )


def parse_player_task(value: Any, path: str) -> PlayerTask:
    data = as_object(value, path)
    return PlayerTask(
        taskType=as_str(required(data, "taskType", path), f"{path}.taskType"),
        taskPosition=parse_pos(required(data, "taskPosition", path), f"{path}.taskPosition"),
        coldDownRounds=as_int(data.get("coldDownRounds", 0), f"{path}.coldDownRounds"),
        scoreReward=as_int(data.get("scoreReward", 0), f"{path}.scoreReward"),
        goldReward=as_int(data.get("goldReward", 0), f"{path}.goldReward"),
        isValid=as_bool(data.get("isValid", False), f"{path}.isValid"),
        timeoutRounds=as_int(data.get("timeoutRounds", 0), f"{path}.timeoutRounds"),
    )


def parse_team_our(value: Any, path: str) -> TeamOur:
    data = as_object(value, path)
    return TeamOur(
        type=enum_or_raw(TeamType, required(data, "type", path), f"{path}.type"),
        teamId=opt_str(data, "teamId", path),
        teamName=opt_str(data, "teamName", path),
        goldNum=as_int(data.get("goldNum", 0), f"{path}.goldNum"),
        totalScore=as_int(data.get("totalScore", 0), f"{path}.totalScore"),
        playerTasks=tuple(
            parse_player_task(t, f"{path}.playerTasks[{i}]")
            for i, t in enumerate(as_array(data.get("playerTasks", []), f"{path}.playerTasks"))
        ),
        roles=tuple(
            parse_role(r, f"{path}.roles[{i}]")
            for i, r in enumerate(as_array(data.get("roles", []), f"{path}.roles"))
        ),
    )


def parse_team_enemy(value: Any, path: str) -> TeamEnemy:
    data = as_object(value, path)
    return TeamEnemy(
        roles=tuple(
            parse_role(r, f"{path}.roles[{i}]")
            for i, r in enumerate(as_array(data.get("roles", []), f"{path}.roles"))
        )
    )


def parse_robot_role(value: Any, path: str) -> RobotRole:
    data = as_object(value, path)
    target = data.get("targetTeam")
    return RobotRole(
        id=as_int(required(data, "id", path), f"{path}.id"),
        pos=parse_pos(required(data, "pos", path), f"{path}.pos"),
        roleType=enum_or_raw(
            RobotRoleType, required(data, "roleType", path), f"{path}.roleType"
        ),
        health=as_int(required(data, "health", path), f"{path}.health"),
        abnormalState=opt_str(data, "abnormalState", path),
        targetTeam=None
        if target is None
        else enum_or_raw(TeamType, target, f"{path}.targetTeam"),
    )


def parse_robot(value: Any, path: str) -> Robot:
    data = as_object(value, path)
    return Robot(
        roles=tuple(
            parse_robot_role(r, f"{path}.roles[{i}]")
            for i, r in enumerate(as_array(data.get("roles", []), f"{path}.roles"))
        )
    )


def parse_world_news(value: Any, path: str) -> WorldNews:
    data = as_object(value, path)
    return WorldNews(
        officialNews=opt_str(data, "officialNews", path),
        folkLegends=opt_str(data, "folkLegends", path),
    )


def parse_error(value: Any, path: str) -> Error:
    data = as_object(value, path)
    return Error(
        errorCode=as_int(required(data, "errorCode", path), f"{path}.errorCode"),
        description=opt_str(data, "description", path),
    )


def parse_shop_item(value: Any, path: str) -> ShopItem:
    data = as_object(value, path)
    return ShopItem(
        name=as_str(required(data, "name", path), f"{path}.name"),
        price=as_int(required(data, "price", path), f"{path}.price"),
    )


def parse_action_results(value: Any, path: str) -> dict[int, bool]:
    data = as_object(value, path)
    results: dict[int, bool] = {}
    for key, raw in data.items():
        try:
            role_id = int(key)
        except ValueError:
            raise fail(path, f"role id key {key!r} is not an integer") from None
        if not isinstance(raw, bool):
            raise fail(f"{path}[{key!r}]", f"expected bool, got {type(raw).__name__}")
        results[role_id] = raw
    return results
