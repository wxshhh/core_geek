"""私有模块：JSON 工具——解析错误的类型、宽松修复与基础类型强制。

本模块只依赖标准库，是 models 包解析依赖链的根基（其余模块由此单向引用）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from enum import Enum
from typing import Any, Final, TypeVar

E = TypeVar("E", bound=Enum)
_WHITESPACE: Final = " \t\r\n"


class ParseError(ValueError):
    """输入无法解析为合法数据结构。调用方必须捕获；进程绝不因此崩溃。"""


def remove_trailing_commas(text: str) -> str:
    """删除 JSON 中对象/数组末尾的非法逗号（字符串字面量内的逗号不受影响）。

    判题器走严格 JSON；此函数仅用于本地 fixture 的宽松加载。
    """
    out: list[str] = []
    in_string = False
    escaped = False
    i = 0
    size = len(text)
    while i < size:
        ch = text[i]
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
            out.append(ch)
        elif ch == ",":
            j = i + 1
            while j < size and text[j] in _WHITESPACE:
                j += 1
            if j < size and text[j] in "}]":
                pass  # 末尾逗号：丢弃
            else:
                out.append(ch)
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def fail(path: str, message: str) -> ParseError:
    return ParseError(f"{path}: {message}")


def as_object(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise fail(path, f"expected object, got {type(value).__name__}")
    return value


def as_array(value: Any, path: str) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    raise fail(path, f"expected array, got {type(value).__name__}")


def as_int(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise fail(path, f"expected int, got {type(value).__name__}")
    return value


def as_str(value: Any, path: str) -> str:
    if not isinstance(value, str):
        raise fail(path, f"expected string, got {type(value).__name__}")
    return value


def as_bool(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise fail(path, f"expected bool, got {type(value).__name__}")
    return value


def required(data: Mapping[str, Any], key: str, path: str) -> Any:
    if key not in data:
        raise fail(path, f"missing required field {key!r}")
    return data[key]


def opt_str(data: Mapping[str, Any], key: str, path: str, default: str = "") -> str:
    if key not in data or data[key] is None:
        return default
    return as_str(data[key], f"{path}.{key}")


def enum_or_raw(enum_cls: type[E], value: Any, path: str) -> E | str:
    if not isinstance(value, str):
        raise fail(path, f"expected string, got {type(value).__name__}")
    try:
        return enum_cls(value)
    except ValueError:
        return value  # 前向兼容：协议新增取值时原样保留


def load_json_object(text: str, *, lenient: bool) -> Mapping[str, Any]:
    """把 JSON 文本解析为对象；lenient 时先剥离末尾逗号。"""
    if lenient:
        text = remove_trailing_commas(text)
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParseError(f"invalid JSON: {exc}") from exc
    return as_object(loaded, "$")
