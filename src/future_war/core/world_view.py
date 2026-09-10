"""世界视图（工作包 9）：策略引擎每回合消费的只读查询接口。

WorldModel.apply_round 每回合返回一个新快照：dynamic/history 均为不可变元组，
旧快照不被后续回合影响。历史型查询（新闻/价格/机器人波次/敌方观测/任务/
宝藏/待决）跨回合累积，由 history 提供。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from future_war.models import (
    Error,
    PlayerTask,
    Pos,
    RobotRole,
    Role,
    Zone,
    enum_to_str,
)
from future_war.core.world_map import BuildableKind, StaticMap
from future_war.core.world_state import (
    DynamicState,
    EnemyObservation,
    HistoryState,
    NewsRecord,
    PendingKind,
    PendingRecord,
    PriceRecord,
    RobotCountRecord,
    TaskEvent,
    TreasureRecord,
    day_of,
)


@dataclass(frozen=True, slots=True)
class WorldView:
    """当前回合的世界快照（不可变；不可哈希——含 dict 字段）。"""

    static_map: StaticMap
    dynamic: DynamicState
    history: HistoryState

    # ------------------------------------------------------------ 时间

    @property
    def round_no(self) -> int:
        return self.dynamic.round

    @property
    def day(self) -> int:
        return self.dynamic.day

    def is_day(self) -> bool:
        return self.dynamic.phase == "D"

    def is_night(self) -> bool:
        return self.dynamic.phase == "N"

    # ------------------------------------------------------------ 角色

    def own_roles(self) -> tuple[Role, ...]:
        return self.dynamic.own_roles

    def own_station(self) -> tuple[Role, ...]:
        return _by_type(self.dynamic.own_roles, "station")

    def own_pioneer(self) -> tuple[Role, ...]:
        return _by_type(self.dynamic.own_roles, "pioneer")

    def own_workers(self) -> tuple[Role, ...]:
        return _by_type(self.dynamic.own_roles, "worker")

    def own_weapons(self) -> tuple[Role, ...]:
        return tuple(r for r in self.dynamic.own_roles if _kind(r) in _WEAPON_KINDS)

    def own_walls(self) -> tuple[Role, ...]:
        return _by_type(self.dynamic.own_roles, "wall")

    def enemy_roles(self) -> tuple[Role, ...]:
        return self.dynamic.enemy_roles

    def enemy_station(self) -> tuple[Role, ...]:
        return _by_type(self.dynamic.enemy_roles, "station")

    def enemy_walls(self) -> tuple[Role, ...]:
        return _by_type(self.dynamic.enemy_roles, "wall")

    def enemy_mobile_units(self) -> tuple[Role, ...]:
        """敌方非建筑单位（视野 4 内可见，任务书 §4.3）。"""
        return tuple(
            r for r in self.dynamic.enemy_roles if _kind(r) in ("pioneer", "worker")
        )

    # ------------------------------------------------------------ 经济

    def gold(self) -> int:
        return self.dynamic.gold

    def total_score(self) -> int:
        return self.dynamic.total_score

    def price(self, ore: str) -> int | None:
        """小贩当前收购价；从未观测到返回 None。"""
        return self.dynamic.vendor_prices.get(ore)

    def price_series(self, ore: str) -> tuple[PriceRecord, ...]:
        return tuple(r for r in self.history.prices if r.ore == ore)

    def weapon_price(self, name: str) -> int | None:
        return self.dynamic.weapon_prices.get(name)

    # ------------------------------------------------------------ 地图

    def base_pos(self) -> Pos | None:
        """己方基地左上角坐标（接口 §1.3.1 注）。"""
        return self.static_map.own_base

    def enemy_base_pos(self) -> Pos | None:
        return self.static_map.enemy_base

    def base_cells(self) -> frozenset[Pos]:
        return self.static_map.own_base_cells()

    def mines(self, kind: str | None = None) -> tuple[Zone, ...]:
        if kind is None:
            return self.dynamic.mines
        return tuple(z for z in self.dynamic.mines if _zone_kind(z) == kind)

    def mine_at(self, pos: Pos) -> str | None:
        for zone in self.dynamic.mines:
            if zone.pos == pos:
                return _zone_kind(zone)
        return None

    def vendor_pos(self) -> Pos | None:
        return self.static_map.vendor_pos

    def weapon_shop_pos(self) -> Pos | None:
        return self.static_map.weapon_shop_pos

    def own_task_points(self) -> tuple[Pos, ...]:
        return self.static_map.own_task_points

    def enemy_task_points(self) -> tuple[Pos, ...]:
        return self.static_map.enemy_task_points

    def blue_build_cells(self) -> frozenset[Pos]:
        return self.static_map.blue

    def yellow_build_cells(self) -> frozenset[Pos]:
        return self.static_map.yellow

    def can_build(self, pos: Pos, kind: BuildableKind | str) -> bool:
        return self.static_map.can_build(pos, kind)

    def in_bounds(self, pos: Pos) -> bool:
        return self.static_map.in_bounds(pos)

    def obstacles(self) -> frozenset[Pos]:
        """当前全部阻挡格：静态中立格 + 矿区 + 双方单位 + 机器人（§4.1）。"""
        blocked = set(self.static_map.static_obstacles)
        blocked.update(z.pos for z in self.dynamic.mines)
        blocked.update(r.pos for r in self.dynamic.own_roles)
        blocked.update(r.pos for r in self.dynamic.enemy_roles)
        blocked.update(r.pos for r in self.dynamic.robots)
        return frozenset(blocked)

    # ------------------------------------------------------------ 机器人

    def robots(self) -> tuple[RobotRole, ...]:
        return self.dynamic.robots

    def robots_targeting_us(self) -> tuple[RobotRole, ...]:
        return tuple(r for r in self.dynamic.robots if r.targetTeam == self.dynamic.team)

    def robot_total(self) -> int:
        return len(self.dynamic.robots)

    def robot_counts_by_night(self) -> dict[int, tuple[RobotCountRecord, ...]]:
        """机器人波次按「第几天」（夜晚）分组。"""
        grouped: dict[int, list[RobotCountRecord]] = {}
        for record in self.history.robot_counts:
            grouped.setdefault(day_of(record.round), []).append(record)
        return {night: tuple(records) for night, records in grouped.items()}

    # ------------------------------------------------------------ 历史

    def official_news_text(self) -> str:
        return "\n".join(r.official for r in self.history.news if r.official)

    def folk_legend_text(self) -> str:
        return "\n".join(r.folk for r in self.history.news if r.folk)

    def task_events(self) -> tuple[TaskEvent, ...]:
        return self.history.task_events

    def treasure_results(self) -> tuple[TreasureRecord, ...]:
        return self.history.treasure_results

    def pending_llm(self) -> tuple[PendingRecord, ...]:
        """仍未收到应答的 LLM 请求（接口 §1.7 异步一回合往返）。"""
        return tuple(
            r for r in self.history.pending if r.kind == PendingKind.LLM and not r.resolved
        )

    def pending_sandbox(self) -> tuple[PendingRecord, ...]:
        return tuple(
            r
            for r in self.history.pending
            if r.kind == PendingKind.SANDBOX and not r.resolved
        )

    # ------------------------------------------------------------ 回合反馈

    def action_results(self) -> Mapping[int, bool]:
        return self.dynamic.action_results

    def action_ok(self, role_id: int) -> bool | None:
        return self.dynamic.action_results.get(role_id)

    def errors(self) -> tuple[Error, ...]:
        return self.dynamic.errors

    def llm_resp(self) -> str:
        return self.dynamic.llm_resp

    def last_cmd_result(self) -> str:
        return self.dynamic.last_cmd_result

    def last_treasure_result(self) -> int:
        return self.dynamic.last_treasure_result

    def phase_task(self) -> str:
        return self.dynamic.phase_task

    def player_tasks(self) -> tuple[PlayerTask, ...]:
        return self.dynamic.player_tasks


_WEAPON_KINDS = ("gatling", "railgun", "rocket")


def _kind(role: Role) -> str:
    return enum_to_str(role.roleType)


def _by_type(roles: tuple[Role, ...], kind: str) -> tuple[Role, ...]:
    return tuple(r for r in roles if _kind(r) == kind)


def _zone_kind(zone: Zone) -> str:
    return enum_to_str(zone.neutralType)
