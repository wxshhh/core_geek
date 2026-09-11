"""启动入口测试：run.py（跨平台）能启动服务并返回真实指令。

Given/When/Then 风格；运行方式（二选一）：
    python3 -m pytest tests/ -q
    python3 tests/test_entrypoints.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _post(port: int, body: bytes) -> tuple[int, bytes]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/", data=body, method="POST"
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        return response.status, response.read()


def _wait_and_post(port: int, body: bytes, proc: subprocess.Popen) -> tuple[int, bytes]:
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(f"process exited early: {proc.returncode}")
        try:
            return _post(port, body)
        except Exception:  # noqa: BLE001 — 端口未就绪，重试
            time.sleep(0.1)
    raise AssertionError("server did not start within 15s")


def test_run_py_entrypoint_starts_and_responds() -> None:
    """Given run.py 入口，When 启动并 POST 样例，Then 返回非空合法指令。"""
    with tempfile.TemporaryDirectory() as tmp:
        port = 18097
        env = {**os.environ, "FUTURE_WAR_LOG_DIR": tmp}
        proc = subprocess.Popen(
            [sys.executable, "run.py", str(port)],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            status, body = _wait_and_post(
                port, (ROOT / "docs" / "request.txt").read_bytes(), proc
            )
            assert status == 200
            payload = json.loads(body)
            assert set(payload) == {"roleCommandMap", "prompt", "executeCmd"}
            assert payload["roleCommandMap"]
        finally:
            proc.terminate()
            proc.wait(timeout=5)


def test_entrypoint_files_exist() -> None:
    """Given 交付规范，When 检查入口文件，Then run.sh / run.py / run.bat 均存在。"""
    for name in ("run.sh", "run.py", "run.bat"):
        assert (ROOT / name).is_file(), f"missing {name}"


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
