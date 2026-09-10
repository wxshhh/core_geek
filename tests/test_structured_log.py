"""测试：结构化日志与稳定事件码（工作包 5）。

覆盖：
- 单行固定格式 `<round> <phase> <TAG> <CODE> <message> <k=v ...>`（方案 §4.1 样例逐字节）
- 昼夜相位 D/N/- 与 phase_of 换算（白天 70 + 夜晚 60 = 130，任务书 §4.2）
- 按 TAG 与 CODE 均可 grep；消息/键/值清洗（单行自解释、无换行）
- 级别上限语义：log.level 控制 DIGEST/EVENT/DECISION/TRACE 输出；log.trace_enabled 独立放行 TRACE；
  ERROR/ANOMALY 标签恒输出（任务书 §八）
- 事件码注册表稳定（code 集合被测试钉死，§4.4 诊断码齐备）、describe() 人类可读描述
- 线程安全（8 线程并发写行数完整）、写失败/不可打印值降级不崩溃、缓冲 flush 可配、可选 stderr 镜像
- 进程级默认实例 configure/get/reset

运行方式（二选一）：
    python3 -m pytest tests/ -q
    python3 tests/test_structured_log.py
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import threading
from contextlib import contextmanager, redirect_stderr
from pathlib import Path
from typing import Any, Iterator

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from future_war.config import Config, load_config  # noqa: E402
from future_war.observability import (  # noqa: E402
    configure_logger,
    get_logger,
    reset_logger,
)
from future_war.observability.events import (  # noqa: E402
    EVENT_REGISTRY,
    EventCode,
    EventSpec,
    LogLevel,
    Tag,
    describe,
)
from future_war.observability.structured_log import (  # noqa: E402
    Phase,
    StructuredLogger,
    format_line,
    phase_of,
)


def _read_lines(logger: StructuredLogger) -> list[str]:
    assert logger.log_path is not None
    return logger.log_path.read_text(encoding="utf-8").splitlines()


@contextmanager
def _clean_config_env() -> Iterator[None]:
    """清空 FUTURE_WAR_* 环境变量（防开发者环境污染 load_config 断言）。"""
    saved = {k: v for k, v in os.environ.items() if k.startswith("FUTURE_WAR_")}
    for key in saved:
        del os.environ[key]
    try:
        yield
    finally:
        os.environ.update(saved)


def _make_config(tmp: Path, level: str, trace_enabled: bool) -> Config:
    """在临时 config 目录注入 log 配置并加载（内置默认 + default.json 合并）。"""
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "default.json").write_text(
        json.dumps({"log": {"level": level, "trace_enabled": trace_enabled}}),
        encoding="utf-8",
    )
    with _clean_config_env():
        return load_config(config_dir=tmp)


class Exploding:
    """__str__ 抛异常的字段值：日志器必须降级为占位符而非崩溃。"""

    def __str__(self) -> str:
        raise RuntimeError("boom")


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


# ---------------------------------------------------------------- 单行格式契约

def test_single_line_format_matches_plan_contract() -> None:
    """Given StructuredLogger，When emit E-01（方案 §4.1 样例字段），Then 行逐字节等于契约。"""
    with tempfile.TemporaryDirectory() as tmp:
        logger = StructuredLogger(tmp)
        try:
            ok = logger.emit(
                EventCode.E_01,
                "collect",
                round_no=85,
                phase="D",
                worker=10010,
                target="stone@(4,24)",
                got=1,
                gold=20,
            )
        finally:
            logger.close()
        line = _read_lines(logger)[0]
    assert ok is True
    assert line == "0085 D [ECON] E-01 collect worker=10010 target=stone@(4,24) got=1 gold=20"


def test_combat_line_and_digest_line_formats() -> None:
    """Given C-03 与 D-01 事件，When emit，Then 行格式含 4 位回合、相位、TAG、CODE、k=v。"""
    with tempfile.TemporaryDirectory() as tmp:
        logger = StructuredLogger(tmp)
        try:
            logger.emit(
                EventCode.C_03,
                "attack",
                round_no=120,
                phase=Phase.NIGHT,
                weapon=10040,
                ctrl=10011,
                target="(4,5)",
                dmg=20,
                cd=3,
            )
            logger.emit(
                EventCode.D_01,
                "",
                round_no=130,
                phase=Phase.NONE,
                day=1,
                gold=45,
                kills=6,
                baseHP=1500,
            )
        finally:
            logger.close()
        lines = _read_lines(logger)
    assert lines[0] == "0120 N [COMBAT] C-03 attack weapon=10040 ctrl=10011 target=(4,5) dmg=20 cd=3"
    assert lines[1] == "0130 - [DIGEST] D-01 day=1 gold=45 kills=6 baseHP=1500"


def test_round_none_and_phase_none_render_as_dash() -> None:
    """Given 无回合/无相位（启动期），When emit I-01，Then 回合与相位位置均为 `-`。"""
    with tempfile.TemporaryDirectory() as tmp:
        logger = StructuredLogger(tmp)
        try:
            logger.emit(EventCode.I_01, "bot started", version="a1b2c3")
        finally:
            logger.close()
        line = _read_lines(logger)[0]
    assert line == "- - [INIT] I-01 bot started version=a1b2c3"


def test_phase_of_maps_round_to_day_or_night() -> None:
    """Given 回合号序列，When phase_of，Then 前 70 为 D、后 60 为 N、跨天循环。"""
    assert phase_of(1) is Phase.DAY
    assert phase_of(70) is Phase.DAY
    assert phase_of(71) is Phase.NIGHT
    assert phase_of(129) is Phase.NIGHT
    assert phase_of(130) is Phase.NIGHT
    assert phase_of(131) is Phase.DAY


def test_lines_greppable_by_tag_and_by_code() -> None:
    """Given 多标签多码事件，When 读取日志，Then 按 `[COMBAT]` 与 `C-03` 均能唯一定位。"""
    with tempfile.TemporaryDirectory() as tmp:
        logger = StructuredLogger(tmp)
        try:
            logger.emit(EventCode.E_01, "collect", round_no=1, phase="D", worker=10010)
            logger.emit(EventCode.C_03, "attack", round_no=2, phase="N", weapon=10040)
            logger.emit(EventCode.N_02, "stuck", round_no=3, phase="D", role=10011)
        finally:
            logger.close()
        text = logger.log_path.read_text(encoding="utf-8")  # type: ignore[union-attr]
    assert "[ECON] E-01 collect" in text
    assert "[COMBAT] C-03 attack" in text
    assert "[NAV] N-02 stuck" in text


def test_message_and_field_sanitization() -> None:
    """Given 消息含换行/制表、字段含空格与非法键名，When emit，Then 单行且值被安全编码。"""
    with tempfile.TemporaryDirectory() as tmp:
        logger = StructuredLogger(tmp)
        try:
            logger.emit(
                EventCode.L_02,
                "prompt\n\tsent",
                round_no=1,
                phase="D",
                raw="two words",
                none_value=None,
                negative=-3,
                flag=True,
                **{"weird key!": 1},
            )
        finally:
            logger.close()
        line = _read_lines(logger)[0]
    assert "\n" not in line and "\t" not in line
    assert "prompt sent" in line
    assert 'raw="two words"' in line
    assert "none_value=-" in line
    assert "negative=-3" in line
    assert "flag=true" in line
    assert "weird_key_=1" in line


# ---------------------------------------------------------------- 级别与开关

def test_level_event_cap_filters_decision_and_trace() -> None:
    """Given log.level=EVENT（默认上限），When 各发 DIGEST/EVENT/DECISION/TRACE，Then 只落前两类。"""
    with tempfile.TemporaryDirectory() as tmp:
        config = _make_config(Path(tmp), "EVENT", False)
        logger = StructuredLogger.from_config(config, log_dir=tmp)
        try:
            ok_digest = logger.emit(EventCode.D_01, "", round_no=1, day=1)
            ok_event = logger.emit(EventCode.E_01, "collect", round_no=1, phase="D")
            ok_decision = logger.emit(EventCode.C_01, "idle", round_no=1, phase="N")
            ok_trace = logger.emit(EventCode.N_01, "route", round_no=1, phase="D")
        finally:
            logger.close()
        text = "\n".join(_read_lines(logger))
    assert ok_digest is True and ok_event is True
    assert ok_decision is False and ok_trace is False
    assert "D-01" in text and "E-01" in text
    assert "C-01" not in text and "N-01" not in text


def test_trace_enabled_gates_trace_lines() -> None:
    """Given log.level=TRACE，When trace_enabled=false/true，Then TRACE 行仅后者落盘。"""
    with tempfile.TemporaryDirectory() as tmp:
        cfg_off = _make_config(Path(tmp) / "off", "TRACE", False)
        off = StructuredLogger.from_config(cfg_off, log_dir=Path(tmp) / "off_log")
        try:
            assert off.emit(EventCode.N_01, "route", round_no=1, phase="D") is False
        finally:
            off.close()
        assert "N-01" not in "\n".join(_read_lines(off))
        cfg_on = _make_config(Path(tmp) / "on", "TRACE", True)
        on = StructuredLogger.from_config(cfg_on, log_dir=Path(tmp) / "on_log")
        try:
            assert on.emit(EventCode.N_01, "route", round_no=1, phase="D") is True
        finally:
            on.close()
        assert "N-01" in "\n".join(_read_lines(on))


def test_error_and_anomaly_lines_always_emitted() -> None:
    """Given log.level=DIGEST（最粗），When emit ERROR/ANOMALY 事件，Then 仍落盘（任务书 §八）。"""
    with tempfile.TemporaryDirectory() as tmp:
        config = _make_config(Path(tmp), "DIGEST", False)
        logger = StructuredLogger.from_config(config, log_dir=tmp)
        try:
            ok_error = logger.emit(EventCode.X_01, "caught", round_no=1, phase="D")
            ok_anomaly = logger.emit(EventCode.X_05, "state odd", round_no=1, phase="D")
            ok_event = logger.emit(EventCode.E_01, "collect", round_no=1, phase="D")
        finally:
            logger.close()
        text = "\n".join(_read_lines(logger))
    assert ok_error is True and ok_anomaly is True
    assert ok_event is False
    assert "[ERROR] X-01" in text and "[ANOMALY] X-05" in text
    assert "E-01" not in text


def test_invalid_config_level_falls_back_to_event() -> None:
    """Given log.level 为非法值，When from_config，Then 告警回退 EVENT 且照常工作。"""
    with tempfile.TemporaryDirectory() as tmp:
        config = _make_config(Path(tmp), "BOGUS", False)
        logger = StructuredLogger.from_config(config, log_dir=tmp)
        try:
            assert logger.emit(EventCode.E_01, "collect", round_no=1, phase="D") is True
            assert logger.emit(EventCode.C_01, "idle", round_no=1, phase="N") is False
        finally:
            logger.close()


# ---------------------------------------------------------------- 事件码注册表

def test_registry_code_set_is_stable() -> None:
    """Given 发布后的注册表，When 枚举全集与 EVENT_REGISTRY 比对，Then 与钉死的集合一致。

    码集一旦发布只增不改（诊断字典/报告协议引用）；本测试是稳定性守卫。
    """
    expected = frozenset(
        {
            "I-01", "I-02", "I-03", "I-04",
            "E-01", "E-02", "E-03", "E-04", "E-05", "E-06",
            "N-01", "N-02", "N-03", "N-04",
            "B-01", "B-02", "B-03", "B-04",
            "C-01", "C-02", "C-03", "C-04", "C-05", "C-06",
            "T-01", "T-02", "T-03", "T-04",
            "R-01", "R-02", "R-03", "R-04",
            "L-01", "L-02", "L-03", "L-04",
            "S-01", "S-02", "S-03", "S-04",
            "O-01", "O-02", "O-03",
            "D-01",
            "M-01",
            "X-01", "X-02", "X-03", "X-04", "X-05", "X-06", "X-99",
        }
    )
    actual = frozenset(code.value for code in EventCode)
    assert actual == expected
    assert set(EVENT_REGISTRY) == expected
    for code in expected:
        spec = EVENT_REGISTRY[code]
        assert isinstance(spec, EventSpec)
        assert spec.description.strip()
        assert spec.tag in Tag
        assert spec.level in LogLevel


def test_diagnosis_dictionary_codes_exist() -> None:
    """Given 方案 §4.4 诊断字典引用的码，When 查注册表，Then 全部存在且标签正确。"""
    cases = {
        "E-01": Tag.ECON,
        "E-04": Tag.ECON,
        "N-02": Tag.NAV,
        "N-03": Tag.NAV,
        "C-01": Tag.COMBAT,
        "C-02": Tag.COMBAT,
        "C-05": Tag.COMBAT,
        "B-02": Tag.BUILD,
        "T-03": Tag.TASK,
        "L-01": Tag.LLM,
    }
    for code, tag in cases.items():
        assert EVENT_REGISTRY[code].tag is tag
        assert EventCode(code).value == code


def test_describe_returns_human_description_and_unknown_raises() -> None:
    """Given 码或枚举，When describe，Then 返回非空描述；未知码抛 KeyError。"""
    assert describe("E-01") == describe(EventCode.E_01)
    assert "矿" in describe("E-01") or "ore" in describe("E-01")
    assert describe(EventCode.L_01)
    try:
        describe("Z-99")
    except KeyError:
        return
    raise AssertionError("未知事件码未抛 KeyError")


def test_emit_rejects_unknown_code() -> None:
    """Given 未注册码，When emit，Then ValueError（编程错误在开发期暴露）。"""
    with tempfile.TemporaryDirectory() as tmp:
        logger = StructuredLogger(tmp)
        try:
            try:
                logger.emit("Z-99", "nope", round_no=1)
            except ValueError:
                return
            raise AssertionError("未知事件码未抛 ValueError")
        finally:
            logger.close()


def test_emit_rejects_invalid_phase() -> None:
    """Given 非法相位字符串，When emit，Then ValueError。"""
    with tempfile.TemporaryDirectory() as tmp:
        logger = StructuredLogger(tmp)
        try:
            try:
                logger.emit(EventCode.E_01, "x", round_no=1, phase="Q")
            except ValueError:
                return
            raise AssertionError("非法相位未抛 ValueError")
        finally:
            logger.close()


# ---------------------------------------------------------------- 健壮性与并发

def test_write_failure_degrades_without_raising() -> None:
    """Given 日志目录被文件占据，When 构造与 emit，Then 不抛异常、healthy=False、emit=False。"""
    with tempfile.TemporaryDirectory() as tmp:
        blocker = Path(tmp) / "blocker"
        blocker.write_text("x", encoding="utf-8")
        logger = StructuredLogger(blocker)
        try:
            assert logger.healthy is False
            assert logger.log_path is None
            assert logger.emit(EventCode.E_01, "collect", round_no=1) is False
        finally:
            logger.close()


def test_unprintable_field_value_does_not_crash() -> None:
    """Given __str__ 抛异常的字段值，When emit，Then 不崩溃且该值落为 `<unprintable>`。"""
    with tempfile.TemporaryDirectory() as tmp:
        logger = StructuredLogger(tmp)
        try:
            ok = logger.emit(EventCode.X_01, "caught", round_no=1, bad=Exploding())
        finally:
            logger.close()
        line = _read_lines(logger)[0]
    assert ok is True
    assert "bad=<unprintable>" in line


def test_thread_safe_concurrent_emits() -> None:
    """Given 8 线程各 emit 25 行，When 并发写，Then 文件恰 200 行且全部可解析出互异 worker。"""
    with tempfile.TemporaryDirectory() as tmp:
        logger = StructuredLogger(tmp)
        failures: list[BaseException] = []

        def worker(offset: int) -> None:
            try:
                for i in range(25):
                    logger.emit(
                        EventCode.E_01,
                        "collect",
                        round_no=offset + i * 8 + 1,
                        phase="D",
                        worker=offset + i * 8,
                    )
            except BaseException as exc:  # noqa: BROAD_EXCEPT_OK — 线程内收集任何异常供断言
                failures.append(exc)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        logger.close()
        lines = _read_lines(logger)
    assert failures == []
    assert len(lines) == 200
    workers = {int(line.split(" worker=", 1)[1].split(" ")[0]) for line in lines}
    assert len(workers) == 200


def test_stderr_echo_only_when_enabled() -> None:
    """Given echo_stderr 开/关，When emit，Then 开时同一行镜像到 stderr、关时 stderr 静默。"""
    with tempfile.TemporaryDirectory() as tmp:
        quiet = StructuredLogger(tmp)
        try:
            with redirect_stderr(io.StringIO()) as stderr:
                quiet.emit(EventCode.E_01, "collect", round_no=1, phase="D")
            assert stderr.getvalue() == ""
        finally:
            quiet.close()
        loud = StructuredLogger(tmp, echo_stderr=True)
        try:
            with redirect_stderr(io.StringIO()) as stderr:
                loud.emit(EventCode.C_03, "attack", round_no=2, phase="N", weapon=10040)
            assert stderr.getvalue().strip().endswith("[COMBAT] C-03 attack weapon=10040")
        finally:
            loud.close()


def test_flush_every_batches_writes() -> None:
    """Given flush_every=2，When 连续 emit，Then 第 2 笔才批量 flush（共 1 次）。"""
    with tempfile.TemporaryDirectory() as tmp:
        logger = StructuredLogger(tmp, flush_every=2)
        spy = FlushSpy(logger._file)  # type: ignore[attr-defined]  # 白盒
        logger._file = spy  # type: ignore[attr-defined]
        try:
            logger.emit(EventCode.E_01, "collect", round_no=1, phase="D")
            assert spy.flushes == 0
            logger.emit(EventCode.E_01, "collect", round_no=2, phase="D")
            assert spy.flushes == 1
        finally:
            logger.close()


def test_written_count_reflects_lines_on_disk() -> None:
    """Given 3 笔落盘 + 1 笔被过滤，When 读计数，Then written_count 恰 3。"""
    with tempfile.TemporaryDirectory() as tmp:
        logger = StructuredLogger(tmp, level=LogLevel.EVENT)
        try:
            for round_no in (1, 2, 3):
                logger.emit(EventCode.E_01, "collect", round_no=round_no, phase="D")
            logger.emit(EventCode.N_01, "route", round_no=4, phase="D")
            assert logger.written_count == 3
        finally:
            logger.close()


def test_format_line_is_deterministic() -> None:
    """Given 相同输入，When format_line 两次，Then 输出逐字节一致（可 diff）。"""
    first = format_line(
        EventCode.C_03, "attack", round_no=120, phase=Phase.NIGHT,
        fields=(("weapon", 10040), ("dmg", 20)),
    )
    second = format_line(
        EventCode.C_03, "attack", round_no=120, phase=Phase.NIGHT,
        fields=(("weapon", 10040), ("dmg", 20)),
    )
    assert first == second
    assert first == "0120 N [COMBAT] C-03 attack weapon=10040 dmg=20"


# ---------------------------------------------------------------- 进程级默认实例

def test_global_logger_configure_get_reset() -> None:
    """Given configure_logger 注入实例，When get_logger，Then 返回同一实例；reset 后重建新实例。"""
    with tempfile.TemporaryDirectory() as tmp:
        configured = StructuredLogger(tmp)
        configure_logger(configured)
        assert get_logger() is configured
        reset_logger()
        try:
            with _clean_config_env():
                old = os.environ.get("FUTURE_WAR_LOG_DIR")
                os.environ["FUTURE_WAR_LOG_DIR"] = tmp
                try:
                    fresh = get_logger()
                finally:
                    if old is None:
                        os.environ.pop("FUTURE_WAR_LOG_DIR", None)
                    else:
                        os.environ["FUTURE_WAR_LOG_DIR"] = old
        finally:
            reset_logger()
            configured.close()
            fresh.close()  # type: ignore[name-defined]
    assert fresh is not configured  # type: ignore[name-defined]


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
