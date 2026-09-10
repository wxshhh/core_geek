"""工作包 19 测试：推理类（官方新闻 → 价格/可采性）。

Given/When/Then 风格；运行方式（二选一）：
    python3 -m pytest tests/ -q
    python3 tests/test_reasoning.py
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from future_war.models import parse_request  # noqa: E402
from future_war.core import WorldModel  # noqa: E402
from future_war.strategy import (  # noqa: E402
    forecast_directions,
    parse_official_news,
    unavailable_ores,
)

COLLAPSE_NEWS = (
    "【官方消息】矿业管理局紧急通报：北部铁矿区昨夜发生严重矿井塌方事故，"
    "主巷道结构受损，部分作业面被掩埋。安全监察部门已下达通知：为保障矿工安全，"
    "矿区将于明日全面停工，进行巷道加固和主矿脉修复。工程队评估：类似规模的塌方事故，"
    "修复工程通常需要2天左右才能完成并恢复开采。"
)


def _view_with_news(official: str):
    data = {
        "roundNo": 1,
        "mapInfo": {"width": 41, "height": 32, "zones": []},
        "teamOur": {
            "type": "challenger",
            "teamId": "t",
            "teamName": "t",
            "goldNum": 0,
            "totalScore": 0,
            "roles": [],
        },
        "teamEnemy": {"roles": []},
        "robot": {"roles": []},
        "phaseTask": "",
        "lastRoundRoleActionResults": {},
        "lastSummonTreasureResult": 0,
        "llmResp": "",
        "worldNews": {"officialNews": official, "folkLegends": ""},
        "lastCmdResult": "",
        "vendorShopList": [],
        "weaponShopList": [],
        "errors": [],
    }
    return WorldModel().apply_round(parse_request(data))


def test_parse_collapse_news_flags_iron_unavailable_up() -> None:
    """Given 铁矿塌方停工新闻，When 解析，Then 铁不可采且价格上行、约 2 天。"""
    effects = parse_official_news(COLLAPSE_NEWS)
    assert len(effects) == 1
    effect = effects[0]
    assert effect.ore == "iron"
    assert effect.direction == "up"
    assert effect.unavailable_days == 2


def test_quiet_news_has_no_effect() -> None:
    """Given 「今日无重大新闻」，When 解析，Then 无影响。"""
    assert parse_official_news("今日无重大新闻") == ()
    assert parse_official_news("") == ()


def test_news_without_stop_words_has_no_effect() -> None:
    """Given 未提停采的新闻，When 解析，Then 无影响。"""
    assert parse_official_news("今天天气晴朗，铜矿产量稳定。") == ()


def test_unavailable_ores_returns_affected_ore() -> None:
    """Given 塌方新闻，When 求不可采矿，Then 含 iron。"""
    assert unavailable_ores(COLLAPSE_NEWS) == frozenset({"iron"})


def test_forecast_directions_from_accumulated_view() -> None:
    """Given 世界新闻已累计塌方消息，When 预测方向，Then iron 为 up。"""
    view = _view_with_news(COLLAPSE_NEWS)
    assert forecast_directions(view).get("iron") == "up"


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
