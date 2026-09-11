"""跨平台打包脚本：把项目压缩为 dist/future-war-bot-<version>.tar.gz。

版本统一在仓库根 ``VERSION`` 文件管理（单一真源）；也可用参数覆盖。
**Windows / macOS / Linux 通用**（仅标准库，不依赖 rsync/shasum/bash）。

用法::

    python scripts/package.py            # 用 VERSION 文件的版本
    python scripts/package.py 0.2.0      # 临时覆盖版本

产物：``dist/future-war-bot-<version>.tar.gz`` + 同名 ``.sha256`` 校验文件；
包内含 ``BUILD_INFO.txt``（version/commit/built）。
"""

from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "VERSION"

_EXCLUDE_DIR_NAMES = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "logs",
    "dist",
    ".idea",
    ".vscode",
}
_EXCLUDE_OMO_SUBDIRS = {"evidence", "run-continuation"}
_EXCLUDE_FILE_NAMES = {"uv.lock", ".DS_Store", "boulder.json"}
_EXCLUDE_SUFFIXES = (".pyc", ".pyo")


def read_version() -> str:
    """从 VERSION 文件读版本；缺失返回 0.0.0。"""
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def git_commit() -> str:
    """短提交号；不可用返回 unknown。"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else "unknown"


def _excluded(rel: Path) -> bool:
    """相对路径是否属于开发/运行/工具产物（不打进包）。"""
    parts = rel.parts
    if not parts:
        return False
    if any(part in _EXCLUDE_DIR_NAMES for part in parts):
        return True
    if any(part.endswith(".egg-info") for part in parts):
        return True
    if parts[0] == ".omo" and len(parts) >= 2 and parts[1] in _EXCLUDE_OMO_SUBDIRS:
        return True
    if rel.name in _EXCLUDE_FILE_NAMES or rel.suffix in _EXCLUDE_SUFFIXES:
        return True
    return False


def _iter_files():
    """遍历待打包文件（剪掉排除目录，避免进入 .git 等）。"""
    for dirpath, dirnames, filenames in os.walk(ROOT):
        rel_dir = Path(dirpath).relative_to(ROOT)
        dirnames[:] = sorted(d for d in dirnames if not _excluded(rel_dir / d))
        for filename in sorted(filenames):
            rel = rel_dir / filename
            if not _excluded(rel):
                yield rel


def build_archive(version: str, prefix: str | None = None) -> Path:
    """生成 tar.gz 并返回路径。

    ``prefix``：归档内的顶层目录名。默认 ``future-war-bot-<version>``（把整个项目
    目录打包）；传空字符串则平铺到归档根目录（``--flat``）。
    """
    name = f"future-war-bot-{version}"
    if prefix is None:
        prefix = name
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    archive = dist / f"{name}.tar.gz"
    build_info = (
        f"name:    {name}\n"
        f"version: {version}\n"
        f"commit:  {git_commit()}\n"
        f"built:   {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
    ).encode("utf-8")

    def arc(rel: str) -> str:
        return f"{prefix}/{rel}" if prefix else rel

    with tarfile.open(archive, "w:gz") as tar:
        for rel in _iter_files():
            tar.add(ROOT / rel, arcname=arc(rel.as_posix()))
        info = tarfile.TarInfo(arc("BUILD_INFO.txt"))
        info.size = len(build_info)
        info.mtime = int(datetime.now(timezone.utc).timestamp())
        tar.addfile(info, io.BytesIO(build_info))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (dist / f"{name}.tar.gz.sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="utf-8"
    )
    return archive


def _parse_args(args: list[str]) -> tuple[str, str | None]:
    """解析命令行：位置参数为版本，``--flat`` 平铺，``--prefix <name>`` 指定顶层目录。"""
    prefix: str | None = None
    positional: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--flat":
            prefix = ""
            index += 1
        elif arg == "--prefix" and index + 1 < len(args):
            prefix = args[index + 1]
            index += 2
        elif arg.startswith("--prefix="):
            prefix = arg.split("=", 1)[1]
            index += 1
        else:
            positional.append(arg)
            index += 1
    version = positional[0] if positional else read_version()
    return version, prefix


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:]) if argv is None else list(argv)
    version, prefix = _parse_args(args)
    archive = build_archive(version, prefix)
    size_kb = archive.stat().st_size / 1024
    print("打包完成：")
    print(f"  版本：  {version} (源：{VERSION_FILE.name})")
    print(f"  提交：  {git_commit()}")
    print(f"  压缩包：{archive}")
    print(f"  大小：  {size_kb:.0f} KB")
    print(f"  校验：  {archive}.sha256")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
