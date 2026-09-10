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


def test_preferred_weapon_cells_ranks_forward_first() -> None:
    """Given 蓝色可建造格，When 排序，Then 离基地越远越靠前（前出迎敌）。"""
    view = _view([STATION])
    cells = preferred_weapon_cells(view)
    assert cells
    base = view.base_pos()
    assert base is not None
    distances = [chebyshev(c, base) for c in cells]
    assert distances == sorted(distances, reverse=True)


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
