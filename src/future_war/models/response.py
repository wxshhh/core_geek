"""Response 树：Bot 每回合返回的调度指令（docs/接口文档.md §2）。"""

from __future__ import annotations

from dataclasses import dataclass, field

from .enums import Action, enum_to_str
from .request import Pos


@dataclass(frozen=True, slots=True)
class RoleCommand:
    """单角色指令（§2.2）；仅 action 必填，num 缺省 1，其余可选字段缺省即不序列化。"""

    action: Action | str
    controllerId: str | None = None
    targetPos: tuple[Pos, ...] = ()
    name: str | None = None
    num: int = 1
    taskAnswer: str | None = None
    item: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = {"action": enum_to_str(self.action)}
        if self.controllerId is not None:
            out["controllerId"] = self.controllerId
        if self.targetPos:
            out["targetPos"] = [p.to_dict() for p in self.targetPos]
        if self.name is not None:
            out["name"] = self.name
        if self.num != 1:
            out["num"] = self.num
        if self.taskAnswer is not None:
            out["taskAnswer"] = self.taskAnswer
        if self.item:
            out["item"] = list(self.item)
        return out


@dataclass(frozen=True, slots=True)
class Response:
    """Response 顶层结构（§2.1）。注意：本对象不可哈希（roleCommandMap 为 dict 字段）。"""

    roleCommandMap: dict[int, RoleCommand] = field(default_factory=dict)
    prompt: str = ""
    executeCmd: str = ""

    def to_dict(self) -> dict[str, object]:
        """序列化为协议 JSON 对象：恰好三键，roleCommandMap 键为整数角色 ID。"""
        return {
            "roleCommandMap": {
                int(role_id): cmd.to_dict()
                for role_id, cmd in self.roleCommandMap.items()
            },
            "prompt": self.prompt,
            "executeCmd": self.executeCmd,
        }
