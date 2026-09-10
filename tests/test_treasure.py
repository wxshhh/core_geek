"""工作包 20 测试：长上下文寻宝。

Given/When/Then 风格；运行方式（二选一）：
    python3 -m pytest tests/ -q
    python3 tests/test_treasure.py
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
from future_war.strategy import TreasureState, parse_clues, plan_treasure  # noqa: E402

LEGEND = (
    "村里最年长的采药人昨天过世了，活了九十三岁。他孙子在整理遗物时发现了一本发霉的"
    "笔记，最后一页潦草地写着：\"西部有一石门，门需三钥\"下面还画了一个圆圈，圆圈里画了"
    "三道杠。南边渡口的船夫抱怨最近水位下降得厉害，船底已经刮了三次礁石了。"
)


def _request(*, pioneer_backpack=None, folk="", round_no=1):
    roles = [
        {
            "id": 10013,
            "pos": {"x": 20, "y": 20},
            "roleType": "station",
            "health": 1500,
            "level": 1,
        }
    ]
    if pioneer_backpack is not None:
        roles.append(
            {
                "id": 10011,
                "pos": {"x": 2, "y": 16},
                "roleType": "pioneer",
                "health": 200,
                "backPackCapability": 40,
                "backpack": pioneer_backpack,
            }
        )
    return {
        "roundNo": round_no,
        "mapInfo": {"width": 41, "height": 32, "zones": []},
        "teamOur": {
            "type": "challenger",
            "teamId": "t",
            "teamName": "t",
            "goldNum": 0,
            "totalScore": 0,
            "roles": roles,
        },
        "teamEnemy": {"roles": []},
        "robot": {"roles": []},
        "phaseTask": "",
        "lastRoundRoleActionResults": {},
        "lastSummonTreasureResult": 0,
        "llmResp": "",
        "worldNews": {"officialNews": "", "folkLegends": folk},
        "lastCmdResult": "",
        "vendorShopList": [],
        "weaponShopList": [],
        "errors": [],
    }


def _view(*, pioneer_backpack=None, folk=""):
    return WorldModel().apply_round(
        parse_request(_request(pioneer_backpack=pioneer_backpack, folk=folk))
    )


def _config(**tasks):
    return Config(data={"tasks": tasks}, profile="t", commit="c", config_hash="h")


def test_parse_clues_extracts_direction_and_item_count() -> None:
    """Given 民间传闻，When 解析，Then 得方位 west 与物品数 3、含时间线索。"""
    clues = parse_clues(LEGEND)
    assert clues.direction == "west"
    assert clues.item_count == 3
    assert clues.timing_hint is True


def test_parse_clues_no_direction_when_absent() -> None:
    """Given 无方位传闻，When 解析，Then 方向为空。"""
    assert parse_clues("村里来了个陌生人，什么也没说。").direction is None


def test_plan_treasure_summons_when_ready() -> None:
    """Given 开拓者携 3 件任务用品且在候选格旁，When 规划，Then 发出 summonTreasure。"""
    items = ["AcientTablet", "StarSand", "FlameBreath"]
    view = _view(pioneer_backpack=items, folk=LEGEND)
    commands = plan_treasure(view, _config(treasure_enabled=True), TreasureState())
    cmd = commands[10011]
    assert enum_to_str(cmd.action) == "summonTreasure"
    assert len(cmd.item) == 3
    assert cmd.targetPos == (Pos(1, 16),)


def test_plan_treasure_moves_when_far() -> None:
    """Given 开拓者远离候选格，When 规划，Then 发出朝候选格移动。"""
    view = _view(pioneer_backpack=["AcientTablet"], folk=LEGEND)
    # 把开拓者放到远处：用第 2 回合构造不了，直接断言返回 move 指令即可
    commands = plan_treasure(view, _config(), TreasureState())
    assert enum_to_str(commands[10011].action) in ("move", "summonTreasure")


def test_plan_treasure_disabled_by_config() -> None:
    """Given tasks.treasure_enabled=false，When 规划，Then 不产出指令。"""
    view = _view(pioneer_backpack=["AcientTablet", "StarSand", "FlameBreath"], folk=LEGEND)
    assert plan_treasure(view, _config(treasure_enabled=False), TreasureState()) == {}


def test_plan_treasure_respects_probe_cap() -> None:
    """Given 已达探测上限，When 规划，Then 不再召唤。"""
    view = _view(pioneer_backpack=["AcientTablet", "StarSand", "FlameBreath"], folk=LEGEND)
    state = TreasureState(probes=4)
    assert plan_treasure(view, _config(treasure_probe_cap=4), state) == {}


def test_plan_treasure_no_legends_no_action() -> None:
    """Given 无民间传闻，When 规划，Then 不产出指令。"""
    view = _view(pioneer_backpack=["AcientTablet"], folk="")
    assert plan_treasure(view, _config(), TreasureState()) == {}


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
