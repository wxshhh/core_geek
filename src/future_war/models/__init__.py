"""接口数据模型与编解码（工作包 2）。

用法：
    from future_war.models import parse_request, serialize_response, ParseError

    request = parse_request(body)          # bytes/str/dict；严格模式
    payload = serialize_response(response)  # {"roleCommandMap": {...}, ...}
"""

from .codec import ParseError, parse_request, remove_trailing_commas, serialize_response
from .enums import (
    Action,
    ErrorCode,
    FixedRoleIds,
    NeutralType,
    RobotRoleType,
    RoleType,
    SummonTreasureResult,
    TeamType,
    enum_to_str,
    role_kind,
    team_of_role_id,
)
from .request import (
    Error,
    MapInfo,
    PlayerTask,
    Pos,
    Request,
    Robot,
    RobotRole,
    Role,
    ShopItem,
    TeamEnemy,
    TeamOur,
    WorldNews,
    Zone,
)
from .response import Response, RoleCommand

__all__ = [
    "Action",
    "Error",
    "ErrorCode",
    "FixedRoleIds",
    "MapInfo",
    "NeutralType",
    "ParseError",
    "PlayerTask",
    "Pos",
    "Request",
    "Response",
    "Robot",
    "RobotRole",
    "RobotRoleType",
    "Role",
    "RoleCommand",
    "RoleType",
    "ShopItem",
    "SummonTreasureResult",
    "TeamEnemy",
    "TeamOur",
    "TeamType",
    "WorldNews",
    "Zone",
    "enum_to_str",
    "parse_request",
    "remove_trailing_commas",
    "role_kind",
    "serialize_response",
    "team_of_role_id",
]
