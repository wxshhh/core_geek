"""工作包 17 测试：对手建模与侦察适配。

Given/When/Then 风格；运行方式（二选一）：
    python3 -m pytest tests/ -q
    python3 tests/test_opponent.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from future_war.models import Pos, parse_request  # noqa: E402
from future_war.core import WorldModel  # noqa: E402
from future_war.strategy import OpponentModel  # noqa: E402

NIGHT_ROUND = 85


def _pos(x: int, y: int) -> dict[str, int]:
    return {"x": x, "y": y}


def _role(role_id, role_type, x, y, health=100, **extra):
    return {"id": role_id, "pos": _pos(x, y), "roleType": role_type, "health": health, **extra}


def _robot(robot_id, robot_type, x, y, health=40):
    return {
        "id": robot_id,
        "pos": _pos(x, y),
        "roleType": robot_type,
        "health": health,
        "abnormalState": "",
        "targetTeam": "challenger",
    }


def _request(round_no, *, enemy_roles=None, robots=None):
    return {
        "roundNo": round_no,
        "mapInfo": {"width": 41, "height": 32, "zones": []},
        "teamOur": {
            "type": "challenger",
            "teamId": "t",
            "teamName": "t",
            "goldNum": 0,
            "totalScore": 0,
            "roles": [_role(10013, "station", 10, 24, 1500, level=1)],
        },
        "teamEnemy": {"roles": enemy_roles or []},
        "robot": {"roles": robots or []},
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


def _view(round_no, *, enemy_roles=None, robots=None):
    return WorldModel().apply_round(
        parse_request(_request(round_no, enemy_roles=enemy_roles, robots=robots))
    )


def test_observe_records_enemy_tracks() -> None:
    """Given 可见敌方工人，When 观测两回合，Then 轨迹累积两点。"""
    model = OpponentModel()
    enemy = [_role(20010, "worker", 20, 20, 220)]
    model.observe(_view(1, enemy_roles=enemy))
    model.observe(_view(2, enemy_roles=enemy))
    assert model.tracks[20010] == [(20, 20), (20, 20)]


def test_observe_records_spawn_cells_at_night() -> None:
    """Given 夜晚出现机器人，When 观测，Then 记录该夜出生格。"""
    model = OpponentModel()
    robots = [_robot(30001, "smallRobot", 8, 30), _robot(30002, "smallRobot", 9, 30)]
    model.observe(_view(NIGHT_ROUND, robots=robots))
    assert model.observed_nights() == (1,)
    assert model.spawn_cells[1] == {(8, 30), (9, 30)}


def test_threat_detected_when_enemy_near_base() -> None:
    """Given 敌方工人逼近我方基地，When 判定威胁，Then 为真。"""
    model = OpponentModel()
    enemy = [_role(20010, "worker", 12, 24, 220)]
    assert model.threat(_view(1, enemy_roles=enemy)) is True


def test_no_threat_when_enemy_far() -> None:
    """Given 敌方单位远离我方基地，When 判定威胁，Then 为假。"""
    model = OpponentModel()
    enemy = [_role(20010, "worker", 30, 10, 220)]
    assert model.threat(_view(1, enemy_roles=enemy)) is False


def test_spawn_centroid_none_without_observations() -> None:
    """Given 无观测，When 求出生质心，Then 返回 None。"""
    assert OpponentModel().spawn_centroid() is None


def test_spawn_centroid_averages_observed_cells() -> None:
    """Given 观测到出生格，When 求质心，Then 为各格平均。"""
    model = OpponentModel()
    model.observe(
        _view(NIGHT_ROUND, robots=[_robot(30001, "smallRobot", 8, 30), _robot(30002, "smallRobot", 10, 30)])
    )
    assert model.spawn_centroid() == Pos(9, 30)


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
