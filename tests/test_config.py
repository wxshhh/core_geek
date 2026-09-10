"""配置中心与版本戳测试（工作包 7）。

覆盖：default.json 与内置默认一致性、JSON 改动生效、profile 深合并、
env 覆盖（标量/嵌套/未知键）、profile 选择（argv/env/优先级）、
非法/缺失配置回退默认并告警、版本戳格式、服务启动打印版本戳。

运行方式（二选一）：
    python3 -m pytest tests/ -q
    python3 tests/test_config.py
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Final, Iterator

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from future_war.config import (  # noqa: E402
    DEFAULT_CONFIG,
    load_config,
    parse_profile_arg,
)

REPO_ROOT: Final = Path(__file__).resolve().parents[1]
# stamp 行：commit（短哈希 7-40 位或 unknown）/ config-hash（12 位 hex）/ profile
STAMP_RE: Final = re.compile(
    r"stamp (unknown|[0-9a-f]{7,40}) / [0-9a-f]{12} / [a-zA-Z0-9_-]+"
)


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """测试用深合并（独立实现，不依赖被测模块内部函数）。"""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


@contextlib.contextmanager
def _clean_env() -> Iterator[None]:
    """清除全部 FUTURE_WAR_* 环境变量，保证测试可复现。"""
    saved = {k: v for k, v in os.environ.items() if k.startswith("FUTURE_WAR_")}
    for key in saved:
        os.environ.pop(key, None)
    try:
        yield
    finally:
        os.environ.update(saved)


@contextlib.contextmanager
def _env_var(name: str, value: str) -> Iterator[None]:
    old = os.environ.get(name)
    os.environ[name] = value
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = old


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _capture_stderr(fn: Any) -> tuple[Any, str]:
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        result = fn()
    return result, stderr.getvalue()


# ---------------------------------------------------------------------------
# 单元测试：解析与合并
# ---------------------------------------------------------------------------


def test_parse_profile_arg_extracts_all_forms() -> None:
    """Given 各种 argv 形式，When 解析，Then profile 与剩余位置参数正确。"""
    assert parse_profile_arg(["--profile", "aggressive", "8080"]) == (
        "aggressive",
        ["8080"],
    )
    assert parse_profile_arg(["8080", "--profile=aggressive"]) == (
        "aggressive",
        ["8080"],
    )
    assert parse_profile_arg(["8080"]) == (None, ["8080"])
    assert parse_profile_arg([]) == (None, [])


def test_default_json_matches_builtin_defaults() -> None:
    """Given 仓库 default.json，When 解析，Then 与内置默认完全一致（防漂移）。"""
    default_file = json.loads(
        (REPO_ROOT / "config" / "default.json").read_text(encoding="utf-8")
    )
    assert default_file == DEFAULT_CONFIG


def test_changed_json_value_is_reflected_in_loaded_config() -> None:
    """Given 改动后的 default.json，When 加载，Then 新值生效。"""
    with _clean_env(), tempfile.TemporaryDirectory() as tmp:
        cfg_dir = Path(tmp)
        modified = _merge(DEFAULT_CONFIG, {"build": {"day1_max_weapons": 5}})
        _write_json(cfg_dir / "default.json", modified)
        config = load_config(config_dir=cfg_dir)
    assert config.get("build.day1_max_weapons") == 5


def test_profile_file_deep_merges_over_default() -> None:
    """Given profile 只覆盖部分键，When 加载，Then 深合并（未覆盖键保持默认）。"""
    with _clean_env(), tempfile.TemporaryDirectory() as tmp:
        cfg_dir = Path(tmp)
        _write_json(cfg_dir / "default.json", DEFAULT_CONFIG)
        _write_json(
            cfg_dir / "aggressive.json",
            {
                "offense": {"enabled": True, "all_in_score_gap": 120},
                "combat": {"target_priority": ["boss"]},
            },
        )
        config = load_config(profile="aggressive", config_dir=cfg_dir)
    assert config.profile == "aggressive"
    assert config.get("offense.enabled") is True
    assert config.get("offense.all_in_score_gap") == 120
    assert config.get("combat.target_priority") == ["boss"]
    # 未覆盖键保持默认 → 证明是深合并而非整体替换
    assert config.get("llm.daily_quota") == 3
    assert config.get("offense.base_snipe_enabled") is True


def test_env_overrides_scalar_nested_and_typed_values() -> None:
    """Given FUTURE_WAR_* 覆盖，When 加载，Then 标量/嵌套/JSON 字面量类型正确。"""
    with _clean_env(), tempfile.TemporaryDirectory() as tmp:
        cfg_dir = Path(tmp)
        _write_json(cfg_dir / "default.json", DEFAULT_CONFIG)
        with _env_var("FUTURE_WAR_LOG_LEVEL", "DEBUG"), _env_var(
            "FUTURE_WAR_ECONOMY__EMERGENCY_RESERVE", "250"
        ), _env_var("FUTURE_WAR_COMBAT__TARGET_PRIORITY", '["boss","large"]'):
            config = load_config(config_dir=cfg_dir)
    assert config.get("log.level") == "DEBUG"
    assert config.get("economy.emergency_reserve") == 250  # JSON 字面量 → int
    assert config.get("combat.target_priority") == ["boss", "large"]


def test_env_profile_selection_and_argv_precedence() -> None:
    """Given FUTURE_WAR_PROFILE，When 加载，Then profile 生效；argv 传入时优先。"""
    with _clean_env(), tempfile.TemporaryDirectory() as tmp:
        cfg_dir = Path(tmp)
        _write_json(cfg_dir / "default.json", DEFAULT_CONFIG)
        _write_json(cfg_dir / "aggressive.json", {"offense": {"enabled": True}})
        _write_json(cfg_dir / "defensive.json", {"offense": {"enabled": False}})
        with _env_var("FUTURE_WAR_PROFILE", "aggressive"):
            from_env = load_config(config_dir=cfg_dir)
            from_argv = load_config(profile="defensive", config_dir=cfg_dir)
    assert from_env.profile == "aggressive"
    assert from_env.get("offense.enabled") is True
    assert from_argv.profile == "defensive"
    assert from_argv.get("offense.enabled") is False


def test_unknown_env_key_warns_but_is_applied() -> None:
    """Given 未知 env 键，When 加载，Then 告警但键依然生效（拼写错误可诊断）。"""
    with _clean_env(), tempfile.TemporaryDirectory() as tmp:
        cfg_dir = Path(tmp)
        _write_json(cfg_dir / "default.json", DEFAULT_CONFIG)
        with _env_var("FUTURE_WAR_TOTALLY_UNKNOWN", "42"):
            config, captured = _capture_stderr(
                lambda: load_config(config_dir=cfg_dir)
            )
    assert "TOTALLY_UNKNOWN" in captured
    assert config.get("totally_unknown") == 42


# ---------------------------------------------------------------------------
# 单元测试：回退与告警（绝不崩溃）
# ---------------------------------------------------------------------------


def test_invalid_default_json_falls_back_to_builtin_defaults_with_warning() -> None:
    """Given 损坏的 default.json，When 加载，Then 回退内置默认 + 告警 + 可用。"""
    with _clean_env(), tempfile.TemporaryDirectory() as tmp:
        cfg_dir = Path(tmp)
        (cfg_dir / "default.json").write_text("{not valid json", encoding="utf-8")
        config, captured = _capture_stderr(lambda: load_config(config_dir=cfg_dir))
    assert config.data == DEFAULT_CONFIG
    assert "default.json" in captured
    assert config.get("combat.target_priority") == ["boss", "large", "medium", "small"]


def test_missing_default_json_warns_and_uses_builtin_defaults() -> None:
    """Given 不存在的 default.json，When 加载，Then 回退内置默认 + 告警。"""
    with _clean_env(), tempfile.TemporaryDirectory() as tmp:
        config, captured = _capture_stderr(
            lambda: load_config(config_dir=Path(tmp))
        )
    assert config.data == DEFAULT_CONFIG
    assert "default.json" in captured


def test_missing_profile_warns_and_keeps_requested_name() -> None:
    """Given 不存在的 profile，When 加载，Then 告警、默认值可用、stamp 保留其名。"""
    with _clean_env(), tempfile.TemporaryDirectory() as tmp:
        cfg_dir = Path(tmp)
        _write_json(cfg_dir / "default.json", DEFAULT_CONFIG)
        config, captured = _capture_stderr(
            lambda: load_config(profile="nope", config_dir=cfg_dir)
        )
    assert "nope.json" in captured
    assert config.data == DEFAULT_CONFIG
    assert config.profile == "nope"


def test_invalid_profile_name_warns_and_falls_back_to_default() -> None:
    """Given 含路径穿越的 profile 名，When 加载，Then 拒绝并回退 default。"""
    with _clean_env(), tempfile.TemporaryDirectory() as tmp:
        cfg_dir = Path(tmp)
        _write_json(cfg_dir / "default.json", DEFAULT_CONFIG)
        config, captured = _capture_stderr(
            lambda: load_config(profile="../evil", config_dir=cfg_dir)
        )
    assert "invalid profile name" in captured
    assert config.profile == "default"
    assert config.data == DEFAULT_CONFIG


# ---------------------------------------------------------------------------
# 单元测试：版本戳
# ---------------------------------------------------------------------------


def test_stamp_format_is_commit_slash_hash_slash_profile() -> None:
    """Given 加载配置，When 生成版本戳，Then 格式 commit / config-hash / profile。"""
    with _clean_env():
        config = load_config()
    assert STAMP_RE.search("stamp " + config.stamp())
    assert config.profile == "default"
    assert len(config.config_hash) == 12


def test_config_hash_is_stable_and_changes_with_config() -> None:
    """Given 相同配置，When 两次加载，Then 哈希一致；改动后哈希变化。"""
    with _clean_env(), tempfile.TemporaryDirectory() as tmp:
        cfg_dir = Path(tmp)
        _write_json(cfg_dir / "default.json", DEFAULT_CONFIG)
        first = load_config(config_dir=cfg_dir)
        second = load_config(config_dir=cfg_dir)
        assert first.config_hash == second.config_hash
        modified = _merge(DEFAULT_CONFIG, {"log": {"level": "TRACE"}})
        _write_json(cfg_dir / "default.json", modified)
        third = load_config(config_dir=cfg_dir)
    assert third.config_hash != first.config_hash


def test_get_dotted_path_and_defaults() -> None:
    """Given 点分路径，When 取值，Then 命中/缺失默认语义正确。"""
    with _clean_env():
        config = load_config()
    assert config.get("combat.target_priority") == ["boss", "large", "medium", "small"]
    assert config.get("nope.deep") is None
    assert config.get("nope", "fallback") == "fallback"


# ---------------------------------------------------------------------------
# 集成测试：服务启动打印版本戳且空响应行为不回归
# ---------------------------------------------------------------------------


def _wait_for_text(path: Path, needle: str, timeout: float, proc: subprocess.Popen[bytes]) -> str:
    deadline = time.monotonic() + timeout
    content = ""
    while time.monotonic() < deadline:
        content = path.read_text(encoding="utf-8", errors="replace")
        if needle in content:
            return content
        if proc.poll() is not None:
            return content
        time.sleep(0.05)
    return content


def test_server_startup_prints_version_stamp() -> None:
    """Given 启动真实服务，When 读 stderr，Then 打印一次版本戳且先于 listening。

    Given/When/Then 之外：临时端口 0 绑定，读完后 terminate。
    """
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("FUTURE_WAR_")
    }
    env["PYTHONPATH"] = str(SRC_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    with tempfile.TemporaryDirectory() as tmp:
        err_path = Path(tmp) / "stderr.txt"
        fd = os.open(err_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
        proc: subprocess.Popen[bytes]
        try:
            proc = subprocess.Popen(
                [sys.executable, "-m", "future_war.server", "0"],
                cwd=REPO_ROOT,
                env=env,
                stdout=fd,
                stderr=fd,
            )
        finally:
            os.close(fd)
        try:
            content = _wait_for_text(err_path, "listening", 10.0, proc)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
    lines = content.splitlines()
    stamp_lines = [line for line in lines if "stamp" in line]
    assert len(stamp_lines) == 1
    assert STAMP_RE.search(stamp_lines[0])
    assert lines.index(stamp_lines[0]) < lines.index(
        next(line for line in lines if "listening" in line)
    )


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
