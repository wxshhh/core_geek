"""工作包 2 测试：接口数据模型与编解码（docs/接口文档.md §1/§2）。

Given/When/Then 风格；运行方式（二选一）：
    python3 -m pytest tests/ -q
    python3 tests/test_models.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from future_war.models import (  # noqa: E402 — 需要先插入 src 到 sys.path
    Action,
    FixedRoleIds,
    ParseError,
    Pos,
    Request,
    Response,
    RobotRoleType,
    RoleCommand,
    RoleType,
    TeamType,
    parse_request,
    remove_trailing_commas,
    serialize_response,
)

DOCS_DIR = Path(__file__).resolve().parents[1] / "docs"


def load_fixture() -> Request:
    """docs/request.txt 含非法末尾逗号 → 必须 lenient 加载。"""
    return parse_request((DOCS_DIR / "request.txt").read_bytes(), lenient=True)


def minimal_request_data() -> dict[str, Any]:
    return {
        "roundNo": 1,
        "mapInfo": {"width": 41, "height": 32},
        "teamOur": {"type": "challenger"},
    }


def test_parse_request_txt_lenient_matches_interface_contract() -> None:
    """Given docs/request.txt(lenient)，When 解析，Then 关键字段与接口契约一致。"""
    req = load_fixture()
    assert req.roundNo == 85
    assert req.teamOur.type == TeamType.CHALLENGER
    assert len(req.teamOur.roles) >= 7
    assert RobotRoleType.BOSS_ROBOT in {r.roleType for r in req.robot.roles}
    assert len(req.vendorShopList) > 0
    assert len(req.weaponShopList) > 0
    assert req.teamOur.playerTasks
    assert all(t.coldDownRounds == 0 for t in req.teamOur.playerTasks)
    assert req.mapInfo.width == 41 and req.mapInfo.height == 32
    assert req.lastRoundRoleActionResults[10010] is False
    assert req.lastSummonTreasureResult == 0


def test_parse_request_strict_rejects_fixture_trailing_comma() -> None:
    """Given docs/request.txt，When 严格解析，Then 抛可捕获 ParseError。"""
    try:
        parse_request((DOCS_DIR / "request.txt").read_bytes(), lenient=False)
    except ParseError:
        return
    raise AssertionError("严格模式未拒绝含非法末尾逗号的 fixture")


def test_round_trip_parse_serialize_parse_equals() -> None:
    """Given fixture 解析结果，When to_dict→再解析，Then 两次解析结果相等。"""
    original = load_fixture()
    via_dict = parse_request(original.to_dict())
    via_bytes = parse_request(json.dumps(original.to_dict(), ensure_ascii=False).encode("utf-8"))
    assert via_dict == original
    assert via_bytes == original


def test_missing_optional_fields_get_defaults() -> None:
    """Given 仅含必填字段的最小请求，When 解析，Then 可选字段全部取缺省值。"""
    req = parse_request(minimal_request_data())
    assert req.phaseTask == ""
    assert req.llmResp == ""
    assert req.lastCmdResult == ""
    assert req.lastSummonTreasureResult == 0
    assert req.lastRoundRoleActionResults == {}
    assert req.vendorShopList == ()
    assert req.weaponShopList == ()
    assert req.errors == ()
    assert req.teamEnemy.roles == ()
    assert req.robot.roles == ()
    assert req.teamOur.goldNum == 0
    assert req.teamOur.totalScore == 0
    assert req.teamOur.playerTasks == ()
    assert req.worldNews.officialNews == ""
    assert req.worldNews.folkLegends == ""


def test_unknown_fields_ignored_and_preserved_in_raw() -> None:
    """Given 含未知顶层字段的请求，When 解析，Then 不报错且 raw 保留原始数据。"""
    data = minimal_request_data()
    data["futureTopLevel"] = {"hint": 42}
    req = parse_request(data)
    assert req.raw["futureTopLevel"] == {"hint": 42}


def test_unknown_enum_values_preserved_as_raw_strings() -> None:
    """Given 协议新增的 roleType/neutralType 取值，When 解析，Then 原样保留为字符串。"""
    data = minimal_request_data()
    data["mapInfo"]["zones"] = [{"pos": {"x": 0, "y": 0}, "neutralType": "alienBase"}]
    data["teamOur"]["roles"] = [
        {
            "id": 10010,
            "pos": {"x": 1, "y": 1},
            "roleType": "hoverTank",
            "health": 100,
        }
    ]
    req = parse_request(data)
    assert req.mapInfo.zones[0].neutralType == "alienBase"
    assert req.teamOur.roles[0].roleType == "hoverTank"


def test_enemy_role_without_backpack_fields_gets_defaults() -> None:
    """Given fixture 敌方单位（无背包字段），When 解析，Then 背包字段取缺省值。"""
    req = load_fixture()
    enemy_station = next(r for r in req.teamEnemy.roles if r.id == 20013)
    assert enemy_station.backPackCapability == 0
    assert enemy_station.backpack == ()
    assert enemy_station.level == 1
    assert enemy_station.cooldown == 0
    walls = [r for r in req.teamEnemy.roles if r.roleType == RoleType.WALL]
    assert walls and walls[0].id == 41000


def test_invalid_json_raises_catchable_parse_error() -> None:
    """Given 畸形 JSON/非对象根，When 严格解析，Then 抛 ParseError（可捕获）。"""
    for bad in (b"not json", b"{", b"[]", b"[1,2,3]"):
        try:
            parse_request(bad)
        except ParseError:
            continue
        raise AssertionError(f"未对畸形输入抛 ParseError: {bad!r}")


def test_wrong_field_types_raise_parse_error() -> None:
    """Given 字段类型错误/必填缺失，When 解析，Then 抛 ParseError。"""
    bad_round = minimal_request_data()
    bad_round["roundNo"] = "85"
    try:
        parse_request(bad_round)
    except ParseError:
        pass
    else:
        raise AssertionError("roundNo 为字符串未抛 ParseError")
    missing_width = {"roundNo": 1, "mapInfo": {"height": 32}, "teamOur": {}}
    try:
        parse_request(missing_width)
    except ParseError as exc:
        assert "$.mapInfo" in str(exc)
        assert "missing required field 'width'" in str(exc)
    else:
        raise AssertionError("mapInfo 缺 width 未抛 ParseError")


def test_lenient_repair_removes_nested_trailing_commas() -> None:
    """Given 嵌套对象/数组的末尾逗号，When 宽松修复，Then 产出合法 JSON。"""
    text = '{"a": [1, 2,], "b": {"c": 3,}, "d": "keep,me", "e": "esc\\"",}'
    repaired = remove_trailing_commas(text)
    parsed = json.loads(repaired)
    assert parsed == {"a": [1, 2], "b": {"c": 3}, "d": "keep,me", "e": 'esc"'}
    assert remove_trailing_commas('{"a": [1, 2], "b": "x,y"}') == '{"a": [1, 2], "b": "x,y"}'


def test_serialize_response_shape_and_integer_keys() -> None:
    """Given 含指令的 Response，When 序列化，Then 恰好三键且 roleCommandMap 键为整数。"""
    resp = Response(
        roleCommandMap={10010: RoleCommand(action=Action.MOVE, targetPos=(Pos(29, 7),))}
    )
    out = serialize_response(resp)
    assert set(out.keys()) == {"roleCommandMap", "prompt", "executeCmd"}
    role_id = next(iter(out["roleCommandMap"]))
    assert isinstance(role_id, int) and role_id == 10010
    assert out["roleCommandMap"][10010] == {
        "action": "move",
        "targetPos": [{"x": 29, "y": 7}],
    }


def test_role_command_defaults_and_field_omission() -> None:
    """Given 不同指令，When 序列化，Then 缺省字段省略、num 缺省 1、显式字段保留。"""
    assert RoleCommand(action=Action.ACCEPT_TASK).num == 1
    assert RoleCommand(action=Action.ACCEPT_TASK).to_dict() == {"action": "acceptTask"}
    sell = RoleCommand(action=Action.SELL, name="stone", num=3)
    assert sell.to_dict() == {"action": "sell", "name": "stone", "num": 3}
    full = RoleCommand(
        action=Action.SUMMON_TREASURE,
        controllerId="10011",
        targetPos=(Pos(1, 2), Pos(3, 4)),
        name="x",
        num=2,
        taskAnswer="answer",
        item=("AcientTablet", "StarSand"),
    ).to_dict()
    assert full == {
        "action": "summonTreasure",
        "controllerId": "10011",
        "targetPos": [{"x": 1, "y": 2}, {"x": 3, "y": 4}],
        "name": "x",
        "num": 2,
        "taskAnswer": "answer",
        "item": ["AcientTablet", "StarSand"],
    }


def test_all_actions_serialize_round_trip() -> None:
    """Given 全部动作码，When 构造指令并序列化→json 往返，Then 值与原始一致。"""
    for action in Action:
        cmd = RoleCommand(action=action).to_dict()
        assert cmd == {"action": action.value}
        assert json.loads(json.dumps(cmd)) == {"action": action.value}


def test_response_empty_default_matches_scaffold_contract() -> None:
    """Given 空 Response，When 序列化，Then 与脚手架 EMPTY_RESPONSE 结构一致。"""
    assert serialize_response(Response()) == {
        "roleCommandMap": {},
        "prompt": "",
        "executeCmd": "",
    }


def test_fixed_role_id_constants_match_interface_1_3_1() -> None:
    """Given §1.3.1 固定 ID 表，When 校验常量，Then 与文档一致。"""
    assert FixedRoleIds.CHALLENGER_WORKER1 == 10010
    assert FixedRoleIds.CHALLENGER_PIONEER == 10011
    assert FixedRoleIds.CHALLENGER_WORKER2 == 10012
    assert FixedRoleIds.CHALLENGER_STATION == 10013
    assert FixedRoleIds.CHALLENGER_GATLINGS == (10020, 10021, 10022)
    assert FixedRoleIds.CHALLENGER_RAILGUNS == (10030, 10031, 10032)
    assert FixedRoleIds.CHALLENGER_ROCKETS == (10040, 10041, 10042)
    assert FixedRoleIds.CHALLENGER_WALL_BASE == 40000
    assert FixedRoleIds.DEFENDER_STATION == 20013
    assert FixedRoleIds.DEFENDER_WALL_BASE == 41000
    assert FixedRoleIds.DEFENDER_GATLINGS == (20020, 20021, 20022)


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
