"""工作包 14 测试：建造规划（武器布局排序 + 升级券使用）。

Given/When/Then 风格；运行方式（二选一）：
    python3 -m pytest tests/ -q
    python3 tests/test_builder.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from future_war.config import Config  # noqa: E402
from future_war.models import Pos, enum_to_str, parse_request  # noqa: E402
from future_war.core import WorldModel, chebyshev  # noqa: E402
from future_war.strategy import plan_upgrades, preferred_weapon_cells, upgrade_order  # noqa: E402
from future_war.strategy.builder import (  # noqa: E402
    nearest_shelter,
    shelter_cells,
    voucher_trip,
)


def _pos(x: int, y: int) -> dict[str, int]:
    return {"x": x, "y": y}


def _role(
    role_id: int,
    role_type: str,
    x: int,
    y: int,
    health: int = 100,
    backpack: list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    role: dict[str, Any] = {
        "id": role_id,
        "pos": _pos(x, y),
        "roleType": role_type,
        "health": health,
        **extra,
    }
    if backpack is not None:
        role["backpack"] = backpack
    return role


def _request(roles: list[dict[str, Any]], *, gold: int = 0) -> dict[str, Any]:
    return {
        "roundNo": 1,
        "mapInfo": {"width": 41, "height": 32, "zones": []},
        "teamOur": {
            "type": "challenger",
            "teamId": "t",
            "teamName": "t",
            "goldNum": gold,
            "totalScore": 0,
            "roles": roles,
        },
        "teamEnemy": {"roles": []},
        "robot": {"roles": []},
        "phaseTask": "",
        "lastRoundRoleActionResults": {},
        "lastSummonTreasureResult": 0,
        "llmResp": "",
        "worldNews": {"officialNews": "", "folkLegends": ""},
        "lastCmdResult": "",
        "vendorShopList": [],
        "weaponShopList": [],
        "errors": [],
    }


def _view(roles, *, gold=0):
    return WorldModel().apply_round(parse_request(_request(roles, gold=gold)))


def _config(order: list[str] | None = None) -> Config:
    data: dict[str, Any] = {}
    if order is not None:
        data["build"] = {"upgrade_order": order}
    return Config(data=data, profile="t", commit="c", config_hash="h")


STATION = _role(10013, "station", 20, 20, 1500, level=1)


def test_preferred_weapon_cells_ranks_near_base_first() -> None:
    """Given 蓝色可建造格，When 排序，Then 离基地越近越靠前（贴基地建造）。"""
    view = _view([STATION])
    cells = preferred_weapon_cells(view)
    assert cells
    block = tuple(view.base_cells())
    distances = [min(chebyshev(c, cell) for cell in block) for c in cells]
    assert distances == sorted(distances)


def test_upgrade_order_reads_config() -> None:
    """Given 配置升级顺序，When 读取，Then 原样返回；缺配置返回空。"""
    assert upgrade_order(_config(["rocket_l3", "base_l2"])) == ("rocket_l3", "base_l2")
    assert upgrade_order(None) == ()


def test_plan_upgrades_uses_voucher_on_adjacent_building() -> None:
    """Given 工人持武器升级券且身旁有 1 级武器，When 规划，Then 发出 use 指定目标。"""
    roles = [
        STATION,
        _role(10040, "rocket", 21, 20, 1000, level=1, attackRange=10),
        _role(10010, "worker", 22, 20, 220, backpack=["WeaponUpgradeVoucher1"]),
    ]
    view = _view(roles)
    cmd = plan_upgrades(view, _config())[10010]
    assert enum_to_str(cmd.action) == "use"
    assert cmd.name == "WeaponUpgradeVoucher1"
    assert cmd.targetPos == (Pos(21, 20),)


def test_plan_upgrades_no_command_without_adjacent_target() -> None:
    """Given 升级券但身旁无可升级建筑，When 规划，Then 不产出指令。"""
    roles = [
        STATION,
        _role(10040, "rocket", 21, 20, 1000, level=1, attackRange=10),
        _role(10010, "worker", 30, 30, 220, backpack=["WeaponUpgradeVoucher1"]),
    ]
    assert plan_upgrades(_view(roles), _config()) == {}


def test_plan_upgrades_skips_max_level_building() -> None:
    """Given 建筑已满级，When 规划升级，Then 不产出指令。"""
    roles = [
        STATION,
        _role(10040, "rocket", 21, 20, 2000, level=3, attackRange=20),
        _role(10010, "worker", 22, 20, 220, backpack=["WeaponUpgradeVoucher2"]),
    ]
    assert plan_upgrades(_view(roles), _config()) == {}


def test_plan_upgrades_respects_configured_order() -> None:
    """Given 两种券可用，When 配置指定优先级，Then 按配置选择。"""
    roles = [
        STATION,
        _role(10040, "rocket", 22, 21, 1000, level=1, attackRange=10),
        _role(10010, "worker", 21, 21, 220,
              backpack=["WeaponUpgradeVoucher1", "StationUpgradeVoucher1"]),
    ]
    view = _view(roles)
    order = ["StationUpgradeVoucher1", "WeaponUpgradeVoucher1"]
    cmd = plan_upgrades(view, _config(order))[10010]
    assert cmd.name == "StationUpgradeVoucher1"


def test_wall_line_builds_three_sides_and_skips_back() -> None:
    """Given 来袭方向在基地右侧，When 规划围墙，Then 只建右/上/下三面且右侧优先。

    用户实测：机器人从基地一侧刷出，所以围墙只需三面（U 形），背面不建 ——
    既省石头又不会把己方角色围死。
    """
    from future_war.strategy.builder import wall_line

    view = _view([STATION])
    base = view.base_pos()
    assert base is not None
    line = wall_line(view, None, None, None, (1, 0))
    assert line, "应产出候选墙位"
    # 背面（来袭方向的反面）完全不建：左下方属「下面」，仍保留
    block = tuple(view.base_cells())
    def backish(c):
        dx, dy = c.x - base.x, c.y - base.y
        dist = max(abs(dx), abs(dy))
        return dist > 0 and (dx * 1 + dy * 0) / dist < -0.5

    assert not [c for c in line if backish(c)]
    # 右侧（正对来袭面）整体排在前面
    right = [c for c in line if c.x > base.x]
    assert right
    assert line[0] in right
    # 上/下两面仍然要建（补成 U 形，防绕后）
    assert any(c.y > base.y for c in line)
    assert any(c.y < base.y for c in line)


def test_wall_line_covers_all_sides_without_direction() -> None:
    """Given 未知来袭方向，When 规划围墙，Then 退化为四面全建（不排除任何一面）。"""
    from future_war.strategy.builder import wall_line

    view = _view([STATION])
    base = view.base_pos()
    assert base is not None
    line = wall_line(view, None, None, None, None)
    assert any(c.x < base.x for c in line)
    assert any(c.x > base.x for c in line)


# ------------------------------------------------------------------ 墙内安全位


def test_shelter_cells_are_base_adjacent_and_free() -> None:
    """Given 基地与周边建筑，When 取安全位，Then 都是紧贴基地块的空格。"""
    roles = [
        STATION,  # 基地左上角 (20,20) → 基地块 (20..21, 20..21)
        _role(10040, "wall", 19, 19, 1000, level=1),  # 占据一个邻格
    ]
    view = _view(roles)
    cells = shelter_cells(view)
    assert cells, "应至少剩一个贴基地的空位"
    block = tuple(view.base_cells())
    for cell in cells:
        assert min(chebyshev(cell, c) for c in block) == 1, f"{cell} 不是紧贴基地块的空格"
        assert cell != Pos(19, 19), "已被围墙占据的格不能是安全位"
    assert list(cells) == sorted(cells, key=lambda c: (c.x, c.y))


def test_nearest_shelter_picks_closest_and_falls_back_to_base() -> None:
    """Given 有/无安全位，When 取最近安全位，Then 就近选择、无位时退回基地。"""
    view = _view([STATION])
    near = nearest_shelter(view, Pos(30, 30))
    assert near is not None
    assert near in shelter_cells(view)
    empty = _view([_role(10013, "station", 0, 0, 1500, level=1)])
    assert nearest_shelter(empty, Pos(5, 5)) is not None  # 仍有贴基地的空位


# ------------------------------------------------------------------ 升级券的使用路径


def test_voucher_trip_walks_then_signals_use() -> None:
    """Given 工人背着围墙券、目标在远处，When 规划，Then 先 walk、到旁边改判 use。

    回归：`plan_turn` 用 `setdefault` 合并升级指令，经济指令永远先占住角色 →
    `plan_upgrades` 的 use 被静默丢掉（实测整局 buy 3~8 次、use 0 次）。
    """
    wall = _role(10043, "wall", 30, 20, 1000, level=1)
    far_view = _view([STATION, wall, _role(10010, "worker", 5, 5, 220,
                                           backpack=["WallUpgradeVoucher1"])])
    near_view = _view([STATION, wall, _role(10010, "worker", 29, 20, 220,
                                            backpack=["WallUpgradeVoucher1"])])
    action, target = voucher_trip(far_view, far_view.own_workers()[0])
    assert action == "walk" and target == Pos(30, 20)
    assert voucher_trip(near_view, near_view.own_workers()[0]) == ("use", None)


def test_voucher_trip_ignores_voucher_without_target() -> None:
    """Given 场上一座围墙都没有，When 背着围墙券，Then 不产生行程（没地方用）。"""
    view = _view([STATION, _role(10010, "worker", 5, 5, 220,
                                 backpack=["WallUpgradeVoucher1"])])
    assert voucher_trip(view, view.own_workers()[0]) == ("none", None)


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
        except Exception as exc:  # noqa: BROAD_EXCEPT_OK — 运行器顶层边界：收集失败并继续
            failed += 1
            print(f"FAIL {test.__name__}: {exc!r}", file=sys.stderr)
        else:
            print(f"PASS {test.__name__}")
    print(f"{len(test_funcs) - failed}/{len(test_funcs)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
