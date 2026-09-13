"""打包脚本测试：版本单一真源 + tar.gz 产物完整性。

Given/When/Then 风格；运行方式（二选一）：
    python3 -m pytest tests/ -q
    python3 tests/test_package.py
"""

from __future__ import annotations

import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_version_file_is_single_source_of_truth() -> None:
    """Given 仓库根 VERSION 文件，When 读取，Then 存在且非空。"""
    version_file = ROOT / "VERSION"
    assert version_file.is_file()
    assert version_file.read_text(encoding="utf-8").strip()


def test_package_script_builds_archive_with_expected_contents() -> None:
    """Given 打包脚本，When 默认执行，Then 生成 CoreGeek/ 顶层目录、无开发产物的 tar.gz。"""
    version = "0.0.0-test"
    name = f"future-war-bot-{version}"
    archive = ROOT / "dist" / f"{name}.tar.gz"
    checksum = ROOT / "dist" / f"{name}.tar.gz.sha256"
    if archive.exists():
        archive.unlink()
    try:
        result = subprocess.run(
            [sys.executable, "scripts/package.py", version],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert archive.is_file()
        assert checksum.is_file()
        with tarfile.open(archive) as tar:
            names = tar.getnames()
        for expected in (
            "CoreGeek",
            "CoreGeek/main3.py",
            "CoreGeek/run.sh",
            "CoreGeek/run.py",
            "CoreGeek/run.bat",
            "CoreGeek/VERSION",
            "CoreGeek/BUILD_INFO.txt",
            "CoreGeek/src/future_war/server.py",
            "CoreGeek/tests/test_server.py",
            "CoreGeek/config/default.json",
        ):
            assert expected in names, f"missing {expected}"
        # 平台按「项目目录」提交：归档里应当只有 CoreGeek 这一个顶层目录
        assert {n.split("/")[0] for n in names} == {"CoreGeek"}, "归档顶层应只有 CoreGeek/"
        assert not any("__pycache__" in n or n.endswith(".pyc") for n in names)
        assert not any(n.startswith("CoreGeek/.git/") for n in names)
        assert not any(n.startswith("CoreGeek/logs/") for n in names)
        assert not any(n.startswith("CoreGeek/dist/") for n in names)
    finally:
        archive.unlink(missing_ok=True)
        checksum.unlink(missing_ok=True)


def test_package_flat_mode_puts_files_at_archive_root() -> None:
    """Given --flat，When 打包，Then 退回「文件直接在归档根」（main3.py 无前缀）。"""
    version = "0.0.0-flat"
    name = f"future-war-bot-{version}"
    archive = ROOT / "dist" / f"{name}.tar.gz"
    checksum = ROOT / "dist" / f"{name}.tar.gz.sha256"
    archive.unlink(missing_ok=True)
    try:
        result = subprocess.run(
            [sys.executable, "scripts/package.py", version, "--flat"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        with tarfile.open(archive) as tar:
            names = tar.getnames()
        assert "main3.py" in names
        assert "BUILD_INFO.txt" in names
        assert not any(n.startswith("CoreGeek/") for n in names)
    finally:
        archive.unlink(missing_ok=True)
        checksum.unlink(missing_ok=True)


def test_package_prefix_override_names_the_top_directory() -> None:
    """Given --prefix MyDir，When 打包，Then 顶层目录名可替换。"""
    version = "0.0.0-prefix"
    name = f"future-war-bot-{version}"
    archive = ROOT / "dist" / f"{name}.tar.gz"
    checksum = ROOT / "dist" / f"{name}.tar.gz.sha256"
    archive.unlink(missing_ok=True)
    try:
        result = subprocess.run(
            [sys.executable, "scripts/package.py", version, "--prefix", "MyDir"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        with tarfile.open(archive) as tar:
            names = tar.getnames()
        assert "MyDir/main3.py" in names
        assert "MyDir/BUILD_INFO.txt" in names
    finally:
        archive.unlink(missing_ok=True)
        checksum.unlink(missing_ok=True)

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
