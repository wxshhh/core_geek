"""编解码层：判题请求解析（容错）与响应序列化（docs/接口文档.md §1/§2）。

设计要点：
- parse_request 默认严格模式（真实判题器，json.loads 原生语法）；lenient=True 时
  先剥离对象/数组末尾的非法逗号，仅供本地 fixture（docs/request.txt 含一处）。
- 未知字段一律忽略（顶层未知字段经 Request.raw 原样保留）。
- 可选字段缺失 → 缺省值；真正非法输入（类型错误/必填缺失/JSON 语法错）→ ParseError。
- ParseError 是 ValueError 子类，可捕获；本包绝不向进程外抛未捕获异常。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ._jsonutil import (
    ParseError,
    as_array,
    as_int,
    load_json_object,
    opt_str,
    remove_trailing_commas,
    required,
)
from ._parse import (
    parse_action_results,
    parse_error,
    parse_map_info,
    parse_robot,
    parse_shop_item,
    parse_team_enemy,
    parse_team_our,
    parse_world_news,
)
from .request import Request
from .response import Response

__all__ = ["ParseError", "parse_request", "remove_trailing_commas", "serialize_response"]


def parse_request(
    raw: bytes | str | Mapping[str, Any], *, lenient: bool = False
) -> Request:
    """解析判题请求为 Request。

    lenient=True 仅供本地 fixture（容忍 JSON 末尾逗号）；真实判题器请保持严格。
    """
    data: Mapping[str, Any]
    if isinstance(raw, Mapping):
        data = raw
    else:
        if isinstance(raw, bytes):
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ParseError(f"invalid UTF-8 body: {exc}") from exc
        else:
            text = raw
        data = load_json_object(text, lenient=lenient)
    return Request(
        roundNo=as_int(required(data, "roundNo", "$"), "$.roundNo"),
        mapInfo=parse_map_info(required(data, "mapInfo", "$"), "$.mapInfo"),
        teamOur=parse_team_our(required(data, "teamOur", "$"), "$.teamOur"),
        teamEnemy=parse_team_enemy(data.get("teamEnemy", {}), "$.teamEnemy"),
        robot=parse_robot(data.get("robot", {}), "$.robot"),
        phaseTask=opt_str(data, "phaseTask", "$"),
        lastRoundRoleActionResults=parse_action_results(
            data.get("lastRoundRoleActionResults", {}), "$.lastRoundRoleActionResults"
        ),
        lastSummonTreasureResult=as_int(
            data.get("lastSummonTreasureResult", 0), "$.lastSummonTreasureResult"
        ),
        llmResp=opt_str(data, "llmResp", "$"),
        worldNews=parse_world_news(data.get("worldNews", {}), "$.worldNews"),
        lastCmdResult=opt_str(data, "lastCmdResult", "$"),
        vendorShopList=tuple(
            parse_shop_item(item, f"$.vendorShopList[{i}]")
            for i, item in enumerate(
                as_array(data.get("vendorShopList", []), "$.vendorShopList")
            )
        ),
        weaponShopList=tuple(
            parse_shop_item(item, f"$.weaponShopList[{i}]")
            for i, item in enumerate(
                as_array(data.get("weaponShopList", []), "$.weaponShopList")
            )
        ),
        errors=tuple(
            parse_error(e, f"$.errors[{i}]")
            for i, e in enumerate(as_array(data.get("errors", []), "$.errors"))
        ),
        raw=dict(data),
    )


def serialize_response(resp: Response) -> dict[str, object]:
    """把 Response 序列化为协议 JSON 对象（顶层恰好三键，roleCommandMap 键为整数）。"""
    return resp.to_dict()
