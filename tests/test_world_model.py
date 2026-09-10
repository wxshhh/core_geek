"""工作包 9 测试：世界模型与跨回合状态存储（.omo/plans/future-war-bot.md 工作包 9）。

Given/When/Then 风格；运行方式（二选一）：
    python3 -m pytest tests/ -q
    python3 tests/test_world_model.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from future_war.models import parse_request  # noqa: E402 — 需要先插入 src 到 sys.path
from future_war.models import Pos  # noqa: E402
from future_war.core.world import WorldModel  # noqa: E402
from future_war.core.world_map import BuildableKind  # noqa: E402
from future_war.core.world_state import (  # noqa: E402
    PendingKind,
    TaskEventKind,
    day_of,
)

# ---------------------------------------------------------------- fixtures


def _pos(x: int, y: int) -> dict[str, int]:
    return {"x": x, "y": y}


def _role(
    role_id: int,
    role_type: str,
    x: int,
    y: int,
    health: int = 100,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "id": role_id,
        "pos": _pos(x, y),
        "roleType": role_type,
        "health": health,
        **extra,
    }


def _robot(
    role_id: int, robot_type: str, x: int, y: int, health: int = 40
) -> dict[str, Any]:
    return {
        "id": role_id,
        "pos": _pos(x, y),
        "roleType": robot_type,
        "health": health,
        "abnormalState": "",
        "targetTeam": "challenger",
    }


BASE_ZONES: list[dict[str, Any]] = [
    {"pos": _pos(20, 16), "neutralType": "vendor"},
    {"pos": _pos(25, 20), "neutralType": "weaponShop"},
    {"pos": _pos(14, 14), "neutralType": "challengerTaskPoint1"},
    {"pos": _pos(16, 17), "neutralType": "challengerTaskPoint2"},
    {"pos": _pos(23, 14), "neutralType": "defenderTaskPoint1"},
    {"pos": _pos(26, 17), "neutralType": "defenderTaskPoint2"},
    {"pos": _pos(4, 24), "neutralType": "stone"},
    {"pos": _pos(8, 28), "neutralType": "iron"},
    {"pos": _pos(7, 2), "neutralType": "copper"},
]

OUR_ROLES: list[dict[str, Any]] = [
    _role(10013, "station", 10, 24, 1500, level=1),
    _role(10011, "pioneer", 12, 26, 200, backPackCapability=40),
    _role(10010, "worker", 12, 24, 220, backPackCapability=100),
    _role(10012, "worker", 11, 26, 220, backPackCapability=100),
    _role(10040, "rocket", 13, 24, 1000, level=1, attackPower=20, attackRange=10),
    _role(10030, "railgun", 13, 23, 1000, level=1, attackPower=10, attackRange=6),
]

ENEMY_ROLES: list[dict[str, Any]] = [
    _role(20013, "station", 30, 10, 1500, level=1),
    _role(41000, "wall", 28, 7, 1000, level=1),
]

VENDOR: list[dict[str, Any]] = [
    {"name": "stone", "price": 1},
    {"name": "iron", "price": 3},
    {"name": "copper", "price": 5},
]

WEAPON_SHOP: list[dict[str, Any]] = [
    {"name": "WeaponUpgradeVoucher1", "price": 100},
    {"name": "Medicine", "price": 10},
]


def make_request_data(
    round_no: int,
    *,
    gold: int = 75,
    score: int = 0,
    roles: list[dict[str, Any]] | None = None,
    news: dict[str, str] | None = None,
    robots: list[dict[str, Any]] | None = None,
    vendor: list[dict[str, Any]] | None = None,
    weapon: list[dict[str, Any]] | None = None,
    enemy_roles: list[dict[str, Any]] | None = None,
    zones: list[dict[str, Any]] | None = None,
    phase_task: str = "",
    errors: list[dict[str, Any]] | None = None,
    llm_resp: str = "",
    last_cmd: str = "",
    treasure: int = 0,
    action_results: dict[int, bool] | None = None,
) -> dict[str, Any]:
    return {
        "roundNo": round_no,
        "mapInfo": {
            "width": 41,
            "height": 32,
            "zones": BASE_ZONES if zones is None else zones,
        },
        "teamOur": {
            "type": "challenger",
            "teamId": "t1",
            "teamName": "test",
            "goldNum": gold,
            "totalScore": score,
            "roles": OUR_ROLES if roles is None else roles,
        },
        "teamEnemy": {"roles": ENEMY_ROLES if enemy_roles is None else enemy_roles},
        "robot": {"roles": [] if robots is None else robots},
        "phaseTask": phase_task,
        "lastRoundRoleActionResults": {} if action_results is None else action_results,
        "lastSummonTreasureResult": treasure,
        "llmResp": llm_resp,
        "worldNews": (
            {"officialNews": "", "folkLegends": ""} if news is None else news
        ),
        "lastCmdResult": last_cmd,
        "vendorShopList": VENDOR if vendor is None else vendor,
        "weaponShopList": WEAPON_SHOP if weapon is None else weapon,
        "errors": [] if errors is None else errors,
    }


def apply(model: WorldModel, data: dict[str, Any]):
    return model.apply_round(parse_request(data))


def strip_keys(data: dict[str, Any], *paths: list[str]) -> dict[str, Any]:
    """深拷贝并按路径删除键（模拟判题器缺字段）。"""
    import copy

    clone = copy.deepcopy(data)
    for path in paths:
        node: Any = clone
        *parents, last = path
        for seg in parents:
            node = node[seg]
        node.pop(last, None)
    return clone


# ---------------------------------------------------------------- time


def test_day_of_and_phase_computation() -> None:
    """Given 回合号，When 计算天/昼夜，Then 白天 70 + 夜晚 60 = 130 循环。"""
    assert day_of(1) == 1
    assert day_of(70) == 1
    assert day_of(71) == 1
    assert day_of(130) == 1
    assert day_of(131) == 2
    model = WorldModel()
    view = apply(model, make_request_data(1))
    assert view.dynamic.phase == "D"
    assert view.is_day() and not view.is_night()
    night = apply(model, make_request_data(71))
    assert night.dynamic.phase == "N"
    assert night.is_night() and not night.is_day()


# ---------------------------------------------------------------- static map


def test_static_map_buildable_inference_geometry() -> None:
    """Given 基地 (10,24) 2x2 与中立区域，When 推断蓝/黄区，Then 距离环正确且排除基地/中立格/越界。"""
    view = apply(WorldModel(), make_request_data(1))
    static = view.static_map
    assert static.own_base == Pos(10, 24)
    assert static.enemy_base == Pos(30, 10)
    # 蓝区（切比雪夫 ≤3）：距离 2 与 3 的格
    assert static.can_build(Pos(13, 24), BuildableKind.WEAPON)  # 距离 2
    assert static.can_build(Pos(14, 21), BuildableKind.WEAPON)  # 距离 3
    # 黄区（4..6）：(14,20) 距离 4、(5,19) 距离 5
    assert static.can_build(Pos(14, 20), BuildableKind.WALL)
    assert static.can_build(Pos(5, 19), BuildableKind.WALL)
    assert not static.can_build(Pos(14, 20), BuildableKind.WEAPON)  # 黄区不能建武器
    # 排除：基地自身格 / 中立区域格（石矿 (4,24) 距离 6 也须被排除）/ 越界
    assert not static.can_build(Pos(10, 24), BuildableKind.WEAPON)
    assert not static.can_build(Pos(11, 25), BuildableKind.WALL)
    assert not static.can_build(Pos(4, 24), BuildableKind.WALL)  # 石矿格
    assert not static.can_build(Pos(20, 16), BuildableKind.WALL)  # 小贩格
    assert not static.can_build(Pos(41, 10), BuildableKind.WALL)  # 越界
    # 距离 7 超出黄区半径 6
    assert not static.can_build(Pos(3, 19), BuildableKind.WALL)


def test_static_map_stable_across_rounds() -> None:
    """Given 三回合相同地图，When 累积，Then 静态地图完全一致（推断确定性）。"""
    model = WorldModel()
    first = apply(model, make_request_data(1)).static_map
    apply(model, make_request_data(2))
    third = apply(model, make_request_data(3)).static_map
    assert first == third


# ---------------------------------------------------------------- history


def test_history_accumulation_across_rounds() -> None:
    """Given 3 回合变化的价格/新闻/机器人，When 累积，Then 历史序列正确增长且旧快照不变。"""
    model = WorldModel()
    view1 = apply(
        model,
        make_request_data(
            1, gold=75, news={"officialNews": "n1", "folkLegends": ""}
        ),
    )
    view2 = apply(
        model,
        make_request_data(
            2,
            gold=120,
            news={"officialNews": "n2", "folkLegends": "f2"},
            vendor=[
                {"name": "stone", "price": 2},
                {"name": "iron", "price": 3},
                {"name": "copper", "price": 5},
            ],
            robots=[
                _robot(9001, "smallRobot", 15, 10),
                _robot(9002, "middleRobot", 16, 10, 60),
                _robot(9003, "bossRobot", 17, 10, 800),
            ],
        ),
    )
    view3 = apply(
        model,
        make_request_data(
            3,
            gold=200,
            news={"officialNews": "n3", "folkLegends": ""},
            vendor=[
                {"name": "stone", "price": 3},
                {"name": "iron", "price": 4},
                {"name": "copper", "price": 5},
            ],
            robots=[_robot(9001, "smallRobot", 14, 10)],
        ),
    )
    # 价格序列：每矿逐回合累积
    stone = view3.price_series("stone")
    assert len(stone) == 3
    assert [p.price for p in stone] == [1, 2, 3]
    assert [p.round for p in stone] == [1, 2, 3]
    # 新闻列表累积
    assert len(view3.history.news) == 3
    assert view3.official_news_text().split("\n")[-1] == "n3"
    # 机器人波次：仅第 2/3 回合有记录
    assert len(view3.history.robot_counts) == 2
    assert view3.history.robot_counts[-1].total == 1
    assert view3.history.robot_counts[0].boss == 1
    # 金币取最新
    assert view3.dynamic.gold == 200
    # 旧快照不被后续回合影响（快照语义）
    assert len(view1.history.news) == 1
    assert len(view1.history.prices) == 3  # 第 1 回合三种矿各一条


def test_robot_counts_grouped_by_night() -> None:
    """Given 多夜机器人记录，When 按夜分组，Then 夜晚索引正确聚合。"""
    model = WorldModel()
    apply(model, make_request_data(1, robots=[_robot(9001, "smallRobot", 1, 1)]))
    apply(model, make_request_data(2, robots=[_robot(9001, "smallRobot", 2, 1)]))
    apply(model, make_request_data(131, robots=[_robot(9002, "largeRobot", 3, 1, 500)]))
    view = apply(model, make_request_data(132, robots=[_robot(9002, "largeRobot", 4, 1, 500)]))
    grouped = view.robot_counts_by_night()
    assert set(grouped) == {1, 2}
    assert len(grouped[1]) == 2
    assert len(grouped[2]) == 2


def test_enemy_observations_dedupe_consecutive() -> None:
    """Given 敌方观测连续相同，When 累积，Then 仅变更时追加记录。"""
    model = WorldModel()
    apply(model, make_request_data(1))
    apply(model, make_request_data(2))
    view = apply(model, make_request_data(3))
    assert len(view.history.enemy_observations) == 1
    changed = ENEMY_ROLES + [_role(20010, "worker", 29, 12, 220)]
    view2 = apply(model, make_request_data(4, enemy_roles=changed))
    assert len(view2.history.enemy_observations) == 2
    assert view2.enemy_mobile_units()[0].id == 20010


# ---------------------------------------------------------------- fallback


def test_missing_fields_fallback_gracefully() -> None:
    """Given 第 2 回合大量字段缺失，When 更新，Then 回退上一回合/默认且不崩溃。"""
    model = WorldModel()
    apply(model, make_request_data(1))
    sparse = strip_keys(
        make_request_data(2),
        ["vendorShopList"],
        ["weaponShopList"],
        ["worldNews"],
        ["robot"],
        ["teamEnemy"],
        ["mapInfo", "zones"],
        ["teamOur", "roles"],
        ["teamOur", "goldNum"],
        ["teamOur", "totalScore"],
    )
    view = apply(model, sparse)
    assert view.round_no == 2
    assert view.dynamic.fallbacks  # 记录回退事件
    assert view.price("stone") == 1  # 上回合价格
    assert view.gold() == 75  # 上回合金币
    assert view.base_pos() == Pos(10, 24)  # 基地保留
    assert view.enemy_base_pos() == Pos(30, 10)
    assert len(view.mines()) == 3  # 上回合矿区保留
    assert len(view.own_roles()) == 6  # 上回合角色保留
    # 第 3 回合字段恢复后正常更新
    view3 = apply(model, make_request_data(3, gold=90))
    assert view3.gold() == 90
    assert not view3.dynamic.fallbacks


def test_empty_roles_key_present_means_all_dead_not_fallback() -> None:
    """Given roles 键存在但为空，When 更新，Then 视为全灭而非回退。"""
    model = WorldModel()
    apply(model, make_request_data(1))
    view = apply(model, make_request_data(2, roles=[]))
    assert view.own_roles() == ()
    assert not any("roles" in f for f in view.dynamic.fallbacks)


def test_minimal_request_never_crashes() -> None:
    """Given 仅含必填字段的最小请求，When 更新，Then 返回空视图且不抛异常。"""
    model = WorldModel()
    minimal = {
        "roundNo": 1,
        "mapInfo": {"width": 41, "height": 32},
        "teamOur": {"type": "challenger"},
    }
    view = apply(model, minimal)
    assert view.round_no == 1
    assert view.static_map.blue == frozenset()
    assert view.gold() == 0
    assert view.own_roles() == ()


def test_same_sequence_is_deterministic() -> None:
    """Given 相同输入序列，When 两个模型各自累积，Then 快照逐回合相等。"""
    a, b = WorldModel(), WorldModel()
    for round_no in (1, 2, 3):
        data = make_request_data(
            round_no,
            gold=round_no * 10,
            news={"officialNews": f"n{round_no}", "folkLegends": ""},
            vendor=[{"name": "stone", "price": round_no}],
        )
        assert apply(a, data) == apply(b, data)


# ---------------------------------------------------------------- query interface


def test_query_interface_role_groupings() -> None:
    """Given 完整战场，When 查询角色分组，Then 各分组正确。"""
    view = apply(WorldModel(), make_request_data(1))
    assert [r.id for r in view.own_workers()] == [10010, 10012]
    assert [r.id for r in view.own_pioneer()] == [10011]
    assert [r.id for r in view.own_station()] == [10013]
    assert {r.id for r in view.own_weapons()} == {10040, 10030}
    assert view.own_walls() == ()
    assert view.enemy_station()[0].id == 20013
    assert view.enemy_walls()[0].id == 41000
    assert view.base_cells() == frozenset(
        {Pos(10, 24), Pos(11, 24), Pos(10, 25), Pos(11, 25)}
    )


def test_query_interface_map_and_economy() -> None:
    """Given 完整战场，When 查询地图/经济，Then 矿区/商店/任务点/价格正确。"""
    view = apply(WorldModel(), make_request_data(1))
    assert {z.neutralType for z in view.mines()} == {"stone", "iron", "copper"}
    assert view.mines("copper")[0].pos == Pos(7, 2)
    assert view.mine_at(Pos(8, 28)) == "iron"
    assert view.mine_at(Pos(0, 0)) is None
    assert view.vendor_pos() == Pos(20, 16)
    assert view.weapon_shop_pos() == Pos(25, 20)
    assert view.own_task_points() == (Pos(14, 14), Pos(16, 17))
    assert view.enemy_task_points() == (Pos(23, 14), Pos(26, 17))
    assert view.price("stone") == 1
    assert view.price("iron") == 3
    assert view.price("copper") == 5
    assert view.weapon_price("Medicine") == 10
    assert view.weapon_price("Nonexistent") is None


def test_query_interface_robots_and_obstacles() -> None:
    """Given 机器人来袭，When 查询，Then 目标识别与阻挡格正确。"""
    model = WorldModel()
    robots = [
        _robot(9001, "smallRobot", 15, 10),
        {**_robot(9002, "middleRobot", 16, 10, 60), "targetTeam": "defender"},
    ]
    view = apply(model, make_request_data(71, robots=robots))
    assert view.robot_total() == 2
    assert [r.id for r in view.robots_targeting_us()] == [9001]
    obstacles = view.obstacles()
    assert Pos(20, 16) in obstacles  # 小贩
    assert Pos(4, 24) in obstacles  # 矿区
    assert Pos(10, 24) in obstacles  # 己方基地
    assert Pos(30, 10) in obstacles  # 敌方基地
    assert Pos(15, 10) in obstacles  # 机器人
    assert Pos(12, 24) in obstacles  # 己方角色


# ---------------------------------------------------------------- pending / task events


def test_pending_llm_and_sandbox_resolution() -> None:
    """Given 发出 prompt/executeCmd，When 下回合返回应答，Then 待决状态正确配对。"""
    model = WorldModel()
    view1 = apply(model, make_request_data(1))
    model.note_response_sent(prompt="what is 1+1?", execute_cmd="python3 solve.py")
    pending1 = view1.pending_llm() + view1.pending_sandbox()
    assert pending1 == ()  # note 后需下一回合快照才可见
    view2 = apply(
        model,
        make_request_data(2, llm_resp="2", last_cmd="[exitCode:0]\n42"),
    )
    assert view2.pending_llm() == ()  # 已应答
    assert view2.pending_sandbox() == ()
    llm_record = next(r for r in view2.history.pending if r.kind == PendingKind.LLM)
    assert llm_record.resolved and llm_record.response == "2"
    sandbox_record = next(r for r in view2.history.pending if r.kind == PendingKind.SANDBOX)
    assert sandbox_record.resolved and sandbox_record.response.startswith("[exitCode:0]")
    # 未应答的新 prompt 保持待决
    model.note_response_sent(prompt="q2")
    view3 = apply(model, make_request_data(3))
    assert [r.payload for r in view3.pending_llm()] == ["q2"]


def test_task_events_and_treasure_results() -> None:
    """Given 任务生命周期与宝藏探测，When 累积，Then 事件序列正确。"""
    model = WorldModel()
    apply(model, make_request_data(1))
    view2 = apply(
        model,
        make_request_data(
            2, phase_task="自进化类1：输出 42", treasure=2
        ),
    )
    view3 = apply(
        model,
        make_request_data(3, errors=[{"errorCode": 2, "description": "wrong"}], treasure=1),
    )
    kinds = [(e.round, e.kind) for e in view3.history.task_events]
    assert kinds == [
        (2, TaskEventKind.ACCEPTED),
        (3, TaskEventKind.ENDED),
        (3, TaskEventKind.WRONG_ANSWER),
    ]
    assert [t.result for t in view3.history.treasure_results] == [2, 1]
    assert view3.last_treasure_result() == 1
    assert view3.phase_task() == ""


def test_build_attempt_refutes_candidate_on_illegal() -> None:
    """Given 建造反馈为非法，When 下回合反馈到达，Then 候选格被证伪剔除。"""
    model = WorldModel()
    view1 = apply(model, make_request_data(1))
    cell = Pos(13, 24)
    assert view1.can_build(cell, BuildableKind.WEAPON)
    model.record_build_attempt(cell, BuildableKind.WEAPON, role_id=10010)
    view2 = apply(
        model, make_request_data(2, action_results={10010: False})
    )
    assert not view2.can_build(cell, BuildableKind.WEAPON)
    # 合法反馈不证伪
    model.record_build_attempt(Pos(14, 21), BuildableKind.WEAPON, role_id=10010)
    view3 = apply(model, make_request_data(3, action_results={10010: True}))
    assert view3.can_build(Pos(14, 21), BuildableKind.WEAPON)
    assert not view3.can_build(cell, BuildableKind.WEAPON)  # 证伪持续生效


def test_team_switch_rebuilds_map() -> None:
    """Given 半场互换（defender→challenger），When 更新，Then 以新基地重建静态地图。"""
    model = WorldModel()
    view1 = apply(model, make_request_data(1))
    assert view1.base_pos() == Pos(10, 24)
    data = make_request_data(200)
    data["teamOur"]["type"] = "defender"
    data["teamOur"]["roles"] = [
        _role(20013, "station", 30, 10, 1500, level=1),
        _role(20011, "pioneer", 28, 8, 200),
    ]
    view2 = apply(model, data)
    assert view2.base_pos() == Pos(30, 10)
    assert "team_switch" in " ".join(view2.dynamic.fallbacks)
    assert view2.static_map.can_build(Pos(27, 10), BuildableKind.WEAPON)


def test_action_results_and_errors_surface() -> None:
    """Given 判题器反馈，When 查询，Then 动作合法性与错误数组可见。"""
    view = apply(
        model := WorldModel(),
        make_request_data(2, action_results={10010: False, 10011: True}, errors=[{"errorCode": 5}]),
    )
    assert view.action_ok(10010) is False
    assert view.action_ok(10011) is True
    assert view.action_ok(99999) is None
    assert [e.errorCode for e in view.errors()] == [5]
    assert view.round_no == 2


# ---------------------------------------------------------------- zero-dep runner


def main() -> int:
    """零依赖测试运行器：执行全部 test_* 函数并报告。"""
    test_funcs = [
        obj
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    failed = 0
    for test in test_funcs:
        try:
            test()
        except Exception as exc:  # noqa: BLE001 — 运行器顶层边界：收集失败并继续
            failed += 1
            print(f"FAIL {test.__name__}: {exc!r}", file=sys.stderr)
        else:
            print(f"PASS {test.__name__}")
    print(f"{len(test_funcs) - failed}/{len(test_funcs)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
