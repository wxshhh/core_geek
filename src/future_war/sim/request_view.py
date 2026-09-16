"""Request 构造：从模拟器世界状态生成某队视角的战场视图（接口 §1）。

复用工作包 2 的 ``future_war.models`` 类型。视野规则（§4.3）：

- 己方全部单位视野 4、全局共享；
- 机器人全图可见；
- 敌方基地与围墙全局可见，其余敌方单位需进入己方视野；
- 商店清单为基础价/固定清单（世界新闻未实现，见 sim/README.md）。

``driver`` 参数只读两个字段（上回合指令结果与待发错误），以 Protocol 描述，
避免与 judge 模块形成循环依赖。
"""

from __future__ import annotations

from typing import Protocol

from future_war.models import (
    Error,
    MapInfo,
    Pos,
    Request,
    Robot,
    RobotRole,
    Role,
    ShopItem,
    TeamEnemy,
    TeamOur,
    TeamType,
    Zone,
)
from future_war.sim.rules import (
    HEIGHT,
    ORE_PRICES,
    WIDTH,
    WEAPON_ATTACK,
    WEAPON_SHOP,
    weapon_range_at,
)
from future_war.sim.score import compute_team_scores
from future_war.sim.world import Unit, World

_FULL_MAP_RANGE = 2147483647  # 全图射程哨兵值（与接口样例一致）


class RequestContext(Protocol):
    """build_request 所需的一队调度账本视图。"""

    last_results: dict[int, bool]
    pending_errors: list[Error]


def build_request(world: World, team: str, driver: RequestContext) -> Request:
    """从 team 视角构造本回合 Request（接口 §1）。"""
    layout = world.layout
    ts = world.teams[team]
    opponent = world.opponent(team)
    zones = [
        Zone(pos=Pos(*cell), neutralType=kind)
        for cell, kind in layout.fixed_neutrals.items()
    ]
    zones.extend(
        Zone(pos=Pos(*cell), neutralType=mine.ore)
        for cell, mine in sorted(
            world.mines.items(), key=lambda pair: (pair[0][0], pair[0][1])
        )
    )
    own_roles = [_role_view(u) for u in world.alive_units(team)]
    enemy_roles = [
        _role_view(u)
        for u in world.alive_units(opponent)
        if u.kind in ("station", "wall") or world.unit_visible_to(team, u.x, u.y)
    ]
    robot_roles = [
        RobotRole(
            id=rob.rid,
            pos=Pos(rob.x, rob.y),
            roleType=rob.kind,
            health=max(0, rob.hp),
            abnormalState="dizzy" if rob.dizzy_rounds > 0 else "",
            targetTeam=TeamType(rob.target_team),
        )
        for rob in (world.robots[rid] for rid in sorted(world.robots))
    ]
    scores = compute_team_scores(world)
    return Request(
        roundNo=world.round_no,
        mapInfo=MapInfo(width=WIDTH, height=HEIGHT, zones=tuple(zones)),
        teamOur=TeamOur(
            type=TeamType(team),
            teamId=team,
            teamName=team,
            goldNum=ts.gold,
            totalScore=scores[team].total,
            playerTasks=(),  # 任务系统未实现（README）
            roles=tuple(own_roles),
        ),
        teamEnemy=TeamEnemy(roles=tuple(enemy_roles)),
        robot=Robot(roles=tuple(robot_roles)),
        phaseTask="",
        lastRoundRoleActionResults=dict(driver.last_results),
        lastSummonTreasureResult=0,
        llmResp="",
        lastCmdResult="",
        vendorShopList=tuple(
            ShopItem(name=ore, price=price) for ore, price in ORE_PRICES.items()
        ),
        weaponShopList=tuple(
            ShopItem(name=item, price=price) for item, price in WEAPON_SHOP.items()
        ),
        errors=tuple(driver.pending_errors),
    )


def _role_view(u: Unit) -> Role:
    """模拟器单位 → 接口 Role 视图（§1.3.1）。"""
    if u.is_weapon:
        atk = WEAPON_ATTACK[u.kind] * u.level
        rng = weapon_range_at(u.kind, u.level)
        attack_range = _FULL_MAP_RANGE if rng is None else rng
    else:
        atk = 0
        attack_range = 0
    return Role(
        id=u.uid,
        pos=Pos(u.x, u.y),
        roleType=u.kind,
        health=max(0, u.hp),
        attackPower=atk,
        attackRange=attack_range,
        backPackCapability=u.cap,
        backpack=tuple(u.backpack),
        level=u.level if u.is_building else None,
        cooldown=u.cooldown,
    )
