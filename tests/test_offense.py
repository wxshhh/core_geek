"""工作包 27/28/29 测试：进攻策略（火箭狙击 / 召唤骚扰 / 角色狙击）。

Given/When/Then 风格；运行方式（二选一）：
    python3 -m pytest tests/ -q
    python3 tests/test_offense.py
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from future_war.config import Config  # noqa: E402
from future_war.models import Pos, enum_to_str, parse_request  # noqa: E402
from future_war.core import WorldModel  # noqa: E402
from future_war.strategy import OffenseState, plan_offense  # noqa: E402

NIGHT = 85
FULL_MAP = 2147483647


def _role(rid, rtype, x, y, health=100, **extra):
    return {"id": rid, "pos": {"x": x, "y": y}, "roleType": rtype, "health": health, **extra}


def _view(*, roles, enemy=None, robots=None, gold=0, round_no=NIGHT, zones=None):
    data = {
        "roundNo": round_no,
        "mapInfo": {"width": 41, "height": 32, "zones": zones or []},
        "teamOur": {
            "type": "challenger",
            "teamId": "t",
            "teamName": "t",
            "goldNum": gold,
            "totalScore": 0,
            "roles": roles,
        },
        "teamEnemy": {"roles": enemy or []},
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
    return WorldModel().apply_round(parse_request(data))


def _config(**offense):
    return Config(data={"offense": offense}, profile="t", commit="c", config_hash="h")


ENEMY_BASE = [_role(20013, "station", 30, 10, 1500, level=1)]
SHOP = [{"pos": {"x": 25, "y": 20}, "neutralType": "weaponShop"}]


def test_base_snipe_when_rocket_covers_enemy_base() -> None:
    """Given 全图火箭有操控者，When 规划进攻，Then 轰击敌方基地。"""
    roles = [
        _role(10013, "station", 10, 24, 1500, level=1),
        _role(10040, "rocket", 9, 24, 1000, level=1, attackPower=20, attackRange=FULL_MAP),
        _role(10010, "worker", 10, 24, 220),
    ]
    view = _view(roles=roles, enemy=ENEMY_BASE)
    commands = plan_offense(view, _config(base_snipe_enabled=True))
    assert enum_to_str(commands[10040].action) == "attack"
    assert commands[10040].targetPos == (Pos(30, 10),)


def test_base_snipe_disabled_by_config() -> None:
    """Given 关闭火箭狙击，When 规划进攻，Then 不产出指令。"""
    roles = [
        _role(10013, "station", 10, 24, 1500, level=1),
        _role(10040, "rocket", 9, 24, 1000, level=1, attackPower=20, attackRange=FULL_MAP),
        _role(10010, "worker", 10, 24, 220),
    ]
    view = _view(roles=roles, enemy=ENEMY_BASE)
    assert plan_offense(view, _config(base_snipe_enabled=False)) == {}


def test_role_snipe_when_enabled() -> None:
    """Given 视野内敌方工人，When 开启角色狙击，Then 武器攻击该工人。"""
    roles = [
        _role(10013, "station", 10, 24, 1500, level=1),
        _role(10040, "rocket", 9, 24, 1000, level=1, attackPower=20, attackRange=10),
        _role(10010, "worker", 10, 24, 220),
    ]
    enemy = [_role(20010, "worker", 12, 24, 220)]
    view = _view(roles=roles, enemy=enemy)
    commands = plan_offense(view, _config(role_snipe_enabled=True))
    assert enum_to_str(commands[10040].action) == "attack"
    assert commands[10040].targetPos == (Pos(12, 24),)


def test_role_snipe_off_by_default() -> None:
    """Given 默认配置，When 有敌方工人，Then 不狙杀。"""
    roles = [
        _role(10013, "station", 10, 24, 1500, level=1),
        _role(10040, "rocket", 9, 24, 1000, level=1, attackPower=20, attackRange=10),
        _role(10010, "worker", 10, 24, 220),
    ]
    enemy = [_role(20010, "worker", 12, 24, 220)]
    view = _view(roles=roles, enemy=enemy)
    assert plan_offense(view, _config()) == {}


def test_summon_when_enabled_and_rich() -> None:
    """Given 余钱且工人在小贩/商店旁，When 开启召唤骚扰，Then 购买召唤令。"""
    roles = [
        _role(10013, "station", 20, 20, 1500, level=1),
        _role(10010, "worker", 24, 20, 220),
    ]
    view = _view(roles=roles, gold=200, zones=SHOP)
    state = OffenseState()
    commands = plan_offense(view, _config(summon_harass_enabled=True), state=state)
    assert enum_to_str(commands[10010].action) == "buy"
    assert commands[10010].name == "SmallRobotSummonOrder"
    assert state.summons_today == 1


def test_summon_respects_daily_cap() -> None:
    """Given 当天召唤已达上限，When 规划，Then 不再购买。"""
    roles = [
        _role(10013, "station", 20, 20, 1500, level=1),
        _role(10010, "worker", 24, 20, 220),
    ]
    view = _view(roles=roles, gold=200, zones=SHOP)
    state = OffenseState(day=view.day, summons_today=10)
    commands = plan_offense(
        view, _config(summon_harass_enabled=True, summon_daily_cap=10), state=state
    )
    assert 10010 not in commands


def test_offense_skips_excluded_weapons() -> None:
    """Given 武器已被防御占用，When 规划进攻，Then 不覆盖该武器。"""
    roles = [
        _role(10013, "station", 10, 24, 1500, level=1),
        _role(10040, "rocket", 9, 24, 1000, level=1, attackPower=20, attackRange=FULL_MAP),
        _role(10010, "worker", 10, 24, 220),
    ]
    view = _view(roles=roles, enemy=ENEMY_BASE)
    commands = plan_offense(view, _config(base_snipe_enabled=True), exclude=frozenset({10040}))
    assert 10040 not in commands


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
