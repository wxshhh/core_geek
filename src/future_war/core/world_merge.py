"""请求字段合并与缺失回退（工作包 9）：任务书 §八 健壮性核心。

Request 可选字段缺失/为空时回退上一回合值或默认值；每个回退记录为 fallback
描述字符串（进 DynamicState.fallbacks 并经 X-05 日志）。以 Request.raw 的原始
嵌套 dict 判定「字段是否真的存在」——解析层缺失→默认值与显式空值语义不同：
roles=[] 表示全灭（不回退），roles 键缺失才回退。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from future_war.models import Pos, Request, Role, TeamType, Zone, enum_to_str


@dataclass
class FieldCache:
    """跨回合字段缓存：存在即更新，缺失即回退（内部可变，仅 WorldModel 持有）。"""

    team: TeamType | str | None = None
    zones: tuple[Zone, ...] = ()
    own_roles: tuple[Role, ...] = ()
    enemy_roles: tuple[Role, ...] = ()
    own_base: Pos | None = None
    enemy_base: Pos | None = None
    gold: int = 0
    score: int = 0


def has_field(raw: Mapping[str, Any], *path: str) -> bool:
    """请求原文是否实际包含该字段（Request.raw 浅拷贝保留原始嵌套 dict）。"""
    node: object = raw
    for segment in path:
        if not isinstance(node, Mapping) or segment not in node:
            return False
        node = node[segment]
    return True


def station_of(roles: tuple[Role, ...]) -> Role | None:
    """己方/敌方基地：station 单位（接口 §1.3.1，基地 pos 为左上角）。"""
    return next((r for r in roles if enum_to_str(r.roleType) == "station"), None)


def merge_round(cache: FieldCache, request: Request, fallbacks: list[str]) -> None:
    """把本回合 Request 合并进 cache：存在则更新，缺失则记录回退保持原值。"""
    raw = request.raw
    team = request.teamOur.type
    if cache.team is not None and team != cache.team:
        # 半场互换（任务书 §一）：换边后基地/可建造区全部重建
        fallbacks.append(f"team_switch:{cache.team}->{team}")
        cache.enemy_base = None
    cache.team = team

    if has_field(raw, "mapInfo", "zones"):
        if request.mapInfo.zones:
            cache.zones = request.mapInfo.zones
        else:
            fallbacks.append("mapInfo.zones empty → previous")
    else:
        fallbacks.append("mapInfo.zones missing → previous")

    if has_field(raw, "teamOur", "roles"):
        cache.own_roles = request.teamOur.roles
    else:
        fallbacks.append("teamOur.roles missing → previous")
    own_station = station_of(cache.own_roles)
    if own_station is not None:
        cache.own_base = own_station.pos
    elif cache.own_base is None:
        fallbacks.append("own station not found")

    if has_field(raw, "teamEnemy", "roles"):
        cache.enemy_roles = request.teamEnemy.roles
    else:
        fallbacks.append("teamEnemy.roles missing → previous")
    enemy_station = station_of(cache.enemy_roles)
    if enemy_station is not None:
        cache.enemy_base = enemy_station.pos

    if has_field(raw, "teamOur", "goldNum"):
        cache.gold = request.teamOur.goldNum
    else:
        fallbacks.append("teamOur.goldNum missing → previous")
    if has_field(raw, "teamOur", "totalScore"):
        cache.score = request.teamOur.totalScore
    else:
        fallbacks.append("teamOur.totalScore missing → previous")
