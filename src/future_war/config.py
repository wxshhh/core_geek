"""配置中心与版本戳（工作包 7）。

所有策略旋钮集中在 `config/default.json`（稳定键名，文档见 `config/README.md`）。
加载语义（后加载者覆盖先加载者，dict 深度合并）::

    内置默认 DEFAULT_CONFIG
      ← config/default.json
      ← config/<profile>.json   （--profile <name> 或 FUTURE_WAR_PROFILE）
      ← FUTURE_WAR_* 环境变量   （嵌套用 `__`，如 FUTURE_WAR_COMBAT__TARGET_PRIORITY）

与方案 §4.3 的偏差：方案原文为 `config/default.yaml`，这里改用 **JSON**——
运行时承诺零第三方依赖，YAML 需要 PyYAML 而 JSON 由标准库提供；代价是
JSON 不支持注释，键名文档集中在 `config/README.md`。

server.py 启动时打印一次版本戳 `commit / config-hash / profile`（Config.stamp）。
配置文件损坏/缺失一律回退内置默认并告警，绝不崩溃（进程崩溃即判负，任务书 §八）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Final, Sequence, cast

from future_war.config_defaults import DEFAULT_CONFIG, JsonValue

ENV_PREFIX: Final = "FUTURE_WAR_"
ENV_PROFILE: Final = "FUTURE_WAR_PROFILE"
DEFAULT_PROFILE_NAME: Final = "default"

# profile 文件名允许的字符集（防御路径穿越，如 `../evil`）
_PROFILE_NAME_RE: Final = re.compile(r"^[A-Za-z0-9_-]+$")


def _warn(message: str) -> None:
    """配置问题只告警不抛异常：进程崩溃即判负（任务书 §八）。"""
    print(f"[future-war] WARN {message}", file=sys.stderr, flush=True)


def _repo_root() -> Path:
    """仓库根目录：src/future_war/config.py 上溯两级。"""
    return Path(__file__).resolve().parents[2]


def _default_config_dir() -> Path:
    return _repo_root() / "config"


def _deep_merge(
    base: dict[str, JsonValue], override: dict[str, JsonValue]
) -> dict[str, JsonValue]:
    """dict 深度合并：override 的 dict 递归覆盖，非 dict 值整体替换。"""
    merged = dict(base)
    for key, value in override.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(
                cast(dict[str, JsonValue], existing),
                cast(dict[str, JsonValue], value),
            )
        else:
            merged[key] = value
    return merged


def _read_json_object(path: Path) -> dict[str, JsonValue] | None:
    """读取 JSON 对象；缺失/损坏/非对象一律告警并返回 None（绝不抛异常）。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        _warn(f"cannot read config file {path}: {exc}")
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        _warn(f"invalid JSON in {path}: {exc}; ignoring this file")
        return None
    if not isinstance(data, dict):
        _warn(
            f"config file {path} is not a JSON object "
            f"(got {type(data).__name__}); ignoring this file"
        )
        return None
    return cast(dict[str, JsonValue], data)


def _parse_env_value(raw: str) -> JsonValue:
    """env 值按 JSON 字面量解析（数字/布尔/列表），失败则作为纯字符串。"""
    try:
        return cast(JsonValue, json.loads(raw))
    except json.JSONDecodeError:
        return raw


def _resolve_path(
    config: dict[str, JsonValue], parts: Sequence[str]
) -> list[str] | None:
    """把 env 键段解析为真实键路径。

    规则：段整体匹配优先；否则尝试按下划线软嵌套（`log_level` → `log.level`，
    配合 `FUTURE_WAR_LOG_LEVEL=DEBUG`）；都失败返回 None（按字面键应用并告警）。
    """

    def walk(node: JsonValue, segs: list[str], acc: list[str]) -> list[str] | None:
        if not segs:
            return acc
        if not isinstance(node, dict):
            return None
        seg = segs[0]
        if seg in node:
            resolved = walk(node[seg], segs[1:], acc + [seg])
            if resolved is not None:
                return resolved
        for index in range(len(seg) - 1, 0, -1):
            if seg[index] != "_":
                continue
            head, tail = seg[:index], seg[index + 1 :]
            if head in node and isinstance(node[head], dict):
                resolved = walk(node[head], [tail] + segs[1:], acc + [head])
                if resolved is not None:
                    return resolved
        return None

    return walk(config, list(parts), [])


def _set_nested(
    data: dict[str, JsonValue], parts: Sequence[str], value: JsonValue
) -> None:
    node = data
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def _apply_env_overrides(config: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """应用 FUTURE_WAR_* 环境变量覆盖（嵌套用 `__`，软嵌套见 _resolve_path）。"""
    for name in sorted(os.environ):
        if not name.startswith(ENV_PREFIX) or name == ENV_PROFILE:
            continue
        parts = name[len(ENV_PREFIX) :].lower().split("__")
        resolved = _resolve_path(config, parts)
        if resolved is None:
            _warn(f"env {name} does not match any config key; applying anyway (typo?)")
            resolved = list(parts)
        _set_nested(config, resolved, _parse_env_value(os.environ[name]))
    return config


@lru_cache(maxsize=1)
def _git_short_commit(repo_root: str) -> str:
    """git rev-parse --short HEAD；不可用（非 git 仓库/git 缺失）时回退 "unknown"。"""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return proc.stdout.strip() or "unknown"


def _config_hash(data: dict[str, JsonValue]) -> str:
    """有效配置的稳定哈希：规范化 JSON 的 sha256 前 12 位。"""
    canonical = json.dumps(
        data, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class Config:
    """加载后的有效配置。只读：不要修改 data（进程内全局共享）。"""

    data: dict[str, JsonValue]
    profile: str
    commit: str
    config_hash: str

    def stamp(self) -> str:
        """版本戳：`commit / config-hash / profile`（方案 §4.3）。"""
        return f"{self.commit} / {self.config_hash} / {self.profile}"

    def get(self, path: str, default: JsonValue | None = None) -> JsonValue | None:
        """按点分路径取值，如 get("combat.target_priority")；缺失返回 default。"""
        node: JsonValue = self.data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


def resolve_profile(argv_profile: str | None) -> str | None:
    """profile 选择：argv（--profile）优先，其次 FUTURE_WAR_PROFILE 环境变量。"""
    if argv_profile is not None:
        return argv_profile
    env_profile = os.environ.get(ENV_PROFILE, "").strip()
    return env_profile or None


def parse_profile_arg(args: Sequence[str]) -> tuple[str | None, list[str]]:
    """从 argv 抽取 --profile <name> / --profile=<name>，返回 (profile, 其余参数)。

    剩下的位置参数交回调用方继续按端口解析，因此 `bash run.sh 18080
    --profile aggressive` 与 `--profile aggressive 18080` 两种顺序都成立。
    """
    profile: str | None = None
    remaining: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--profile":
            if index + 1 < len(args):
                profile = args[index + 1]
                index += 2
                continue
            _warn("--profile given without a value; ignoring")
            index += 1
            continue
        if arg.startswith("--profile="):
            value = arg.split("=", 1)[1].strip()
            if not value:
                _warn("--profile= given with empty value; ignoring")
            else:
                profile = value
            index += 1
            continue
        remaining.append(arg)
        index += 1
    return profile, remaining


def load_config(
    profile: str | None = None, config_dir: Path | None = None
) -> Config:
    """加载有效配置（合并顺序见模块 docstring）。

    - config_dir 缺省为仓库 config/；测试可注入临时目录。
    - profile 缺省时依次看 argv 结果（调用方传入）与 FUTURE_WAR_PROFILE。
    - 任何一层文件缺失/损坏/非法均告警并继续，绝不抛异常。
    """
    base_dir = _default_config_dir() if config_dir is None else config_dir
    config = deepcopy(DEFAULT_CONFIG)
    default_data = _read_json_object(base_dir / "default.json")
    if default_data is not None:
        config = _deep_merge(config, default_data)
    active = resolve_profile(profile)
    if active is not None:
        if _PROFILE_NAME_RE.fullmatch(active) is None:
            _warn(
                f"invalid profile name {active!r} "
                "(allowed charset [A-Za-z0-9_-]); ignoring profile"
            )
            active = None
        else:
            profile_data = _read_json_object(base_dir / f"{active}.json")
            if profile_data is not None:
                config = _deep_merge(config, profile_data)
    config = _apply_env_overrides(config)
    return Config(
        data=config,
        profile=active if active is not None else DEFAULT_PROFILE_NAME,
        commit=_git_short_commit(str(_repo_root())),
        config_hash=_config_hash(config),
    )
