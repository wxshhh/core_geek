"""测试：回合日志落盘与确定性回放（工作包 4）。

覆盖：
- JSONL 落盘契约（一行一个 JSON 对象，键 roundNo/request/response/result）
- 缓冲/批量 flush 策略可配，写失败与不可序列化输入降级（绝不崩溃，任务书 §八）
- 确定性回放（同一日志两次回放字节一致）
- 损坏/截断行按行号检测与报告（不崩溃）；strict 模式首错即抛
- 摘要的缺回合/重复回合检测与空日志
- 回放 CLI 的退出码与 --verify/--strict/-o

运行方式（二选一）：
    python3 -m pytest tests/ -q
    python3 tests/test_replay.py
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from future_war.observability.logger import DEFAULT_LOG_DIR  # noqa: E402
from future_war.observability.replay import (  # noqa: E402
    LogParseError,
    RoundLogger,
    canonical_summary,
    load_rounds,
    render_summary,
    replay_match,
    resolve_log_dir,
    verify_replay,
)

REPLAY_CLI = REPO_ROOT / "scripts" / "replay.py"


def _load_cli() -> Any:
    """以文件路径加载 scripts/replay.py（scripts 非包，避免命名空间歧义）。"""
    spec = importlib.util.spec_from_file_location("replay_cli", REPLAY_CLI)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_round(round_no: int, commands: int = 1, errors: list[str] | None = None) -> dict[str, Any]:
    """构造一个最小合法回合记录（原始 dict，模拟判题器一轮交互）。"""
    role_command_map = {
        10010 + i: {"action": "move", "param": [round_no, 5]} for i in range(commands)
    }
    return {
        "roundNo": round_no,
        "request": {"roundNo": round_no, "teamOur": {"gold": 75 + round_no}},
        "response": {
            "roleCommandMap": role_command_map,
            "prompt": "",
            "executeCmd": "",
        },
        "result": {"errors": errors or [], "metrics": {"gold": 75 + round_no}},
    }


def write_match(tmp_dir: Path, rounds: list[int]) -> Path:
    """在临时目录落盘一场对局日志，返回日志路径。"""
    logger = RoundLogger(tmp_dir)
    try:
        for i in rounds:
            assert logger.record(make_round(i)) is True
    finally:
        logger.close()
    assert logger.log_path is not None
    return logger.log_path


class FlushSpy:
    """白盒探针：包装真实文件对象，计数底层 flush() 调用。"""

    def __init__(self, real: Any) -> None:
        self._real = real
        self.flushes = 0

    def write(self, text: str) -> int:
        return self._real.write(text)

    def flush(self) -> None:
        self.flushes += 1
        self._real.flush()

    def close(self) -> None:
        self._real.close()


# ---------------------------------------------------------------- 落盘契约

def test_round_logger_writes_one_json_object_per_line() -> None:
    """Given RoundLogger（临时目录），When record 3 回合，Then 文件恰 3 行且每行含四键。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = write_match(Path(tmp), [1, 2, 3])
        lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    for line in lines:
        obj = json.loads(line)
        assert set(obj.keys()) == {"roundNo", "request", "response", "result"}
    assert [json.loads(line)["roundNo"] for line in lines] == [1, 2, 3]


def test_resolve_log_dir_env_override() -> None:
    """Given FUTURE_WAR_LOG_DIR 环境变量，When resolve_log_dir()，Then env 优先、缺省 logs。"""
    with tempfile.TemporaryDirectory() as tmp:
        old = os.environ.get("FUTURE_WAR_LOG_DIR")
        os.environ["FUTURE_WAR_LOG_DIR"] = tmp
        try:
            assert resolve_log_dir() == Path(tmp)
        finally:
            if old is None:
                os.environ.pop("FUTURE_WAR_LOG_DIR", None)
            else:
                os.environ["FUTURE_WAR_LOG_DIR"] = old
    assert resolve_log_dir() == Path(DEFAULT_LOG_DIR)


def test_flush_every_one_flushes_per_record() -> None:
    """Given flush_every=1（默认行刷），When 写 3 回合，Then 每笔 record 触发一次 flush。"""
    with tempfile.TemporaryDirectory() as tmp:
        logger = RoundLogger(tmp, flush_every=1)
        spy = FlushSpy(logger._file)  # type: ignore[attr-defined]  # 白盒
        logger._file = spy  # type: ignore[attr-defined]
        try:
            for i in (1, 2, 3):
                logger.record(make_round(i))
                assert spy.flushes == i
        finally:
            logger.close()


def test_flush_every_batches_writes() -> None:
    """Given flush_every=2，When 写 2 回合，Then 第 2 笔才批量 flush（共 1 次）。"""
    with tempfile.TemporaryDirectory() as tmp:
        logger = RoundLogger(tmp, flush_every=2)
        spy = FlushSpy(logger._file)  # type: ignore[attr-defined]  # 白盒
        logger._file = spy  # type: ignore[attr-defined]
        try:
            logger.record(make_round(1))
            assert spy.flushes == 0
            logger.record(make_round(2))
            assert spy.flushes == 1
        finally:
            logger.close()


def test_record_is_thread_safe() -> None:
    """Given 8 线程并发 record，When 各写 25 回合，Then 文件恰 200 行且回合号互异可解析。"""
    with tempfile.TemporaryDirectory() as tmp:
        logger = RoundLogger(tmp)
        failures: list[BaseException] = []

        def worker(offset: int) -> None:
            try:
                for i in range(25):
                    logger.record(make_round(offset + i * 8 + 1))
            except BaseException as exc:  # noqa: BROAD_EXCEPT_OK — 线程内收集任何异常供断言
                failures.append(exc)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        logger.close()
        assert logger.log_path is not None
        lines = logger.log_path.read_text(encoding="utf-8").splitlines()
    assert failures == []
    assert len(lines) == 200
    assert len({json.loads(line)["roundNo"] for line in lines}) == 200


# ---------------------------------------------------------------- 降级（绝不崩溃）

def test_logger_degrades_when_log_dir_is_a_file() -> None:
    """Given 日志目录位置被文件占据，When 构造 logger 并 record，Then 不抛异常、healthy=False。"""
    with tempfile.TemporaryDirectory() as tmp:
        blocker = Path(tmp) / "blocker"
        blocker.write_text("x", encoding="utf-8")
        logger = RoundLogger(blocker)
        try:
            assert logger.healthy is False
            assert logger.log_path is None
            assert logger.record(make_round(1)) is False
        finally:
            logger.close()


def test_logger_degrades_on_unserializable_record() -> None:
    """Given record 含 set()（不可序列化），When record，Then 不抛异常、返回 False 并降级。"""
    with tempfile.TemporaryDirectory() as tmp:
        logger = RoundLogger(tmp)
        try:
            bad = make_round(1)
            bad["request"]["weird"] = {1, 2}  # type: ignore[assignment]
            assert logger.record(bad) is False  # type: ignore[arg-type]
            assert logger.healthy is False
        finally:
            logger.close()


# ---------------------------------------------------------------- 确定性回放

def test_replay_twice_is_byte_identical() -> None:
    """Given ≥3 回合的对局日志，When replay_match 两次，Then canonical 字节一致、verify OK。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = write_match(Path(tmp), [1, 2, 3, 4])
        first = replay_match(path)
        second = replay_match(path)
        ok, canonical = verify_replay(path)
    assert first.round_count == 4
    assert first.corruptions == ()
    assert first.canonical == second.canonical
    assert ok is True
    assert canonical == first.canonical


def test_summary_detects_missing_and_duplicate_rounds() -> None:
    """Given 回合号 1,2,2,4，When canonical_summary，Then duplicateRounds={"2":2} 且 missingRounds=[3]。"""
    records = [make_round(round_no) for round_no in (1, 2, 2, 4)]
    summary = canonical_summary(records, [], source="x.jsonl")
    assert summary["totalRounds"] == 4
    assert summary["duplicateRounds"] == {"2": 2}
    assert summary["missingRounds"] == [3]
    assert summary["firstRound"] == 1
    assert summary["lastRound"] == 4
    rendered = json.loads(render_summary(summary))
    assert rendered["duplicateRounds"] == {"2": 2}
    assert [entry["roundNo"] for entry in rendered["perRound"]] == [1, 2, 2, 4]


def test_empty_log_replays_with_zero_rounds() -> None:
    """Given 空日志文件，When replay_match，Then totalRounds=0、firstRound/lastRound 为 null。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "empty.jsonl"
        path.write_text("", encoding="utf-8")
        outcome = replay_match(path)
        summary = json.loads(outcome.canonical)
    assert outcome.round_count == 0
    assert summary["totalRounds"] == 0
    assert summary["firstRound"] is None
    assert summary["missingRounds"] == []


# ---------------------------------------------------------------- 损坏/截断检测

def test_corrupted_lines_reported_without_crash() -> None:
    """Given 垃圾行与截断行，When load_rounds/replay_match，Then 按行号报告且不崩溃。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = write_match(Path(tmp), [1, 2])
        text = path.read_text(encoding="utf-8")
        path.write_text(
            text + "this is not json at all\n" + '{"roundNo": 3, "request": {"x": 1',
            encoding="utf-8",
        )
        records, corruptions = load_rounds(path)
        outcome = replay_match(path)
    assert len(records) == 2
    assert [c["line"] for c in corruptions] == [3, 4]
    assert all("JSON" in c["reason"] for c in corruptions)
    assert outcome.round_count == 2
    assert outcome.corruptions == tuple(corruptions)


def test_strict_mode_raises_on_first_corruption() -> None:
    """Given 含损坏行的日志，When load_rounds(strict=True)，Then 首错抛 LogParseError 且带行号。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = write_match(Path(tmp), [1])
        path.write_text(path.read_text(encoding="utf-8") + "garbage\n", encoding="utf-8")
        try:
            load_rounds(path, strict=True)
        except LogParseError as exc:
            assert exc.line == 2
            return
    raise AssertionError("strict 模式未抛 LogParseError")


def test_missing_round_no_reported() -> None:
    """Given 缺 roundNo 的行，When load_rounds，Then 损坏原因为 "missing 'roundNo'"。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = write_match(Path(tmp), [1])
        path.write_text(
            path.read_text(encoding="utf-8")
            + '{"request": {}, "response": {}, "result": {}}\n',
            encoding="utf-8",
        )
        records, corruptions = load_rounds(path)
    assert len(records) == 1
    assert corruptions == [{"line": 2, "reason": "missing 'roundNo'"}]


def test_missing_result_defaults_to_empty_object() -> None:
    """Given 行缺 result 键，When load_rounds，Then 不视为损坏且 result 补 {}。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = write_match(Path(tmp), [1])
        path.write_text(
            path.read_text(encoding="utf-8")
            + '{"roundNo": 2, "request": {}, "response": {}}\n',
            encoding="utf-8",
        )
        records, corruptions = load_rounds(path)
    assert corruptions == []
    assert records[-1]["result"] == {}


# ---------------------------------------------------------------- 回放 CLI

def test_cli_clean_log_exits_zero_and_prints_canonical() -> None:
    """Given 干净日志，When main()，Then 退出码 0 且 stdout 为可解析规范摘要。"""
    cli = _load_cli()
    with tempfile.TemporaryDirectory() as tmp:
        path = write_match(Path(tmp), [1, 2, 3])
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = cli.main([str(path)])
    assert rc == 0
    assert json.loads(stdout.getvalue())["totalRounds"] == 3
    assert stderr.getvalue() == ""


def test_cli_corrupted_log_exits_one_and_warns_to_stderr() -> None:
    """Given 含垃圾行的日志，When main()，Then 退出码 1、stderr 报告行号、stdout 仍输出摘要。"""
    cli = _load_cli()
    with tempfile.TemporaryDirectory() as tmp:
        path = write_match(Path(tmp), [1])
        path.write_text(path.read_text(encoding="utf-8") + "garbage\n", encoding="utf-8")
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = cli.main([str(path)])
    assert rc == 1
    assert "WARN line 2" in stderr.getvalue()
    assert json.loads(stdout.getvalue())["corruptedLines"][0]["line"] == 2


def test_cli_strict_aborts_on_first_corruption() -> None:
    """Given --strict 与损坏行，When main()，Then 退出码 1、ERROR 报行号、stdout 无输出。"""
    cli = _load_cli()
    with tempfile.TemporaryDirectory() as tmp:
        path = write_match(Path(tmp), [1])
        path.write_text(path.read_text(encoding="utf-8") + "garbage\n", encoding="utf-8")
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = cli.main(["--strict", str(path)])
    assert rc == 1
    assert "ERROR line 2" in stderr.getvalue()
    assert stdout.getvalue() == ""


def test_cli_verify_reports_ok() -> None:
    """Given --verify 与干净日志，When main()，Then 两次回放一致、stderr 报 VERIFY OK。"""
    cli = _load_cli()
    with tempfile.TemporaryDirectory() as tmp:
        path = write_match(Path(tmp), [1, 2, 3])
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = cli.main(["--verify", str(path)])
    assert rc == 0
    assert "verify: OK" in stderr.getvalue()
    assert json.loads(stdout.getvalue())["totalRounds"] == 3


def test_cli_missing_file_exits_two() -> None:
    """Given 不存在的日志路径，When main()，Then 退出码 2 且报错。"""
    cli = _load_cli()
    with tempfile.TemporaryDirectory() as tmp:
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            rc = cli.main([str(Path(tmp) / "nope.jsonl")])
    assert rc == 2
    assert "not found" in stderr.getvalue()


def test_cli_output_flag_writes_summary_file() -> None:
    """Given -o 输出路径，When main()，Then 摘要写入文件且退出码 0。"""
    cli = _load_cli()
    with tempfile.TemporaryDirectory() as tmp:
        path = write_match(Path(tmp), [1, 2])
        out_file = Path(tmp) / "summary.json"
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc = cli.main([str(path), "-o", str(out_file)])
        assert rc == 0
        assert json.loads(out_file.read_text(encoding="utf-8"))["totalRounds"] == 2


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
