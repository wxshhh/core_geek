"""Bot 协议层：Bot 适配与响应解析（任务书 §八「异常响应」三类口径）。

三类队伍异常（§八）：
1. 请求响应超时（连接 10s / 响应 5s——本模拟器统一 5s，README 注明）
2. 响应格式错误（非 JSON / 顶层形状错误）
3. 指令错误（动作码不可识别 / 必填字段缺失，见 commands.py）

本层只负责把 Bot 的产出变成「合法指令字典 + 问题清单」；问题清单非空即
计 1 次队伍异常（每回合至多计 1 次，README 注明），具体计数在 judge.py。
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Final, TypeAlias

from future_war.models import Pos, Request, Response, RoleCommand, enum_to_str
from future_war.sim.commands import CommandError, validate_structure

JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)

HTTP_TIMEOUT_SECONDS: Final = 5.0


class BotError(Exception):
    """Bot 响应级异常（§八：超时/格式错误/无法识别）。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# Bot 可返回 Response（进程内）或原始 JSON dict（HTTP 适配器）；None 视为格式错误
BotFn: TypeAlias = Callable[[Request], Response | dict[str, JsonValue] | None]


class HttpBot:
    """HTTP Bot 适配器：向真实 Bot 服务 POST 请求并解析响应（集成用）。"""

    def __init__(self, url: str, timeout: float = HTTP_TIMEOUT_SECONDS) -> None:
        self._url = url.rstrip("/")
        self._timeout = timeout

    def __call__(self, request: Request) -> dict[str, JsonValue]:
        """POST 并返回响应 JSON 对象；任何网络/解析失败抛 BotError。"""
        body = json.dumps(request.to_dict(), ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self._url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise BotError(f"http error: {exc}") from exc
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BotError(f"invalid response JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise BotError("response body is not a JSON object")
        return data


def normalize_response(raw: Response | dict[str, JsonValue] | None) -> dict[str, JsonValue]:
    """把 Bot 产出规范为响应 JSON 对象；非法产出抛 BotError（格式错误）。"""
    if raw is None:
        raise BotError("bot returned no response")
    if isinstance(raw, Response):
        return _response_to_payload(raw)
    if isinstance(raw, dict):
        return raw
    raise BotError(f"bot returned {type(raw).__name__}, expected Response or dict")


def _response_to_payload(resp: Response) -> dict[str, JsonValue]:
    """Response → 协议 JSON 对象（roleCommandMap 键为字符串，与 HTTP 传输一致）。"""
    commands: dict[str, JsonValue] = {}
    for uid, cmd in resp.roleCommandMap.items():
        entry: dict[str, JsonValue] = {"action": enum_to_str(cmd.action)}
        if cmd.controllerId is not None:
            entry["controllerId"] = cmd.controllerId
        if cmd.targetPos:
            entry["targetPos"] = [{"x": p.x, "y": p.y} for p in cmd.targetPos]
        if cmd.name is not None:
            entry["name"] = cmd.name
        if cmd.num != 1:
            entry["num"] = cmd.num
        if cmd.taskAnswer is not None:
            entry["taskAnswer"] = cmd.taskAnswer
        if cmd.item:
            entry["item"] = list(cmd.item)
        commands[str(uid)] = entry
    return {
        "roleCommandMap": commands,
        "prompt": resp.prompt,
        "executeCmd": resp.executeCmd,
    }


def parse_commands(
    payload: dict[str, JsonValue],
) -> tuple[dict[int, RoleCommand], list[str], set[int]]:
    """解析 roleCommandMap → (合法指令, 问题描述, 被丢弃的 uid)。

    顶层形状错误抛 BotError（格式错误）；单条指令的结构性问题（动作码/
    必填字段）与格式问题均计入 problems 并丢弃该指令（§八：计队伍异常）。
    """
    rcm = payload.get("roleCommandMap")
    if not isinstance(rcm, dict):
        raise BotError("roleCommandMap is missing or not an object")
    cmds: dict[int, RoleCommand] = {}
    problems: list[str] = []
    dropped: set[int] = set()
    for key, value in rcm.items():
        try:
            uid = int(str(key))
        except ValueError:
            problems.append(f"role id {key!r} is not an integer")
            continue
        try:
            cmd = _command_from(uid, value)
            validate_structure(cmd)
        except (BotError, CommandError) as exc:
            problems.append(f"role {uid}: {exc.reason}")
            dropped.add(uid)
            continue
        if uid in cmds:
            problems.append(f"role {uid}: duplicate command")
            continue
        cmds[uid] = cmd
    return cmds, problems, dropped


def _command_from(uid: int, value: JsonValue) -> RoleCommand:
    """单条指令 dict → RoleCommand；形状错误抛 BotError。"""
    if not isinstance(value, dict):
        raise BotError("command is not an object")
    action = value.get("action")
    if not isinstance(action, str):
        raise BotError("action missing or not a string")
    return RoleCommand(
        action=action,
        controllerId=_opt_str(value, "controllerId"),
        targetPos=_opt_positions(value, "targetPos"),
        name=_opt_str(value, "name"),
        num=_opt_int(value, "num", 1),
        taskAnswer=_opt_str(value, "taskAnswer"),
        item=_opt_str_list(value, "item"),
    )


def _opt_str(data: dict[str, JsonValue], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise BotError(f"{key} is not a string")
    return value


def _opt_int(data: dict[str, JsonValue], key: str, default: int) -> int:
    value = data.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise BotError(f"{key} is not an integer")
    return value


def _opt_str_list(data: dict[str, JsonValue], key: str) -> tuple[str, ...]:
    value = data.get(key)
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise BotError(f"{key} is not a string array")
    return tuple(value)


def _opt_positions(data: dict[str, JsonValue], key: str) -> tuple[Pos, ...]:
    value = data.get(key)
    if value is None:
        return ()
    if not isinstance(value, list):
        raise BotError(f"{key} is not an array")
    out: list[Pos] = []
    for entry in value:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("x"), int)
            or not isinstance(entry.get("y"), int)
        ):
            raise BotError(f"{key} entry is not an {{x, y}} object")
        out.append(Pos(x=entry["x"], y=entry["y"]))
    return tuple(out)


def warn(message: str) -> None:
    """模拟器自身告警（写失败等）：只告警不抛异常。"""
    print(f"[sim] WARN {message}", file=sys.stderr, flush=True)
