"""工作包 8 测试：诊断字典引用的每个事件码与配置键都必须真实存在。"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from future_war.observability.events import EVENT_REGISTRY  # noqa: E402

_DOC = _ROOT / "docs" / "诊断字典.md"
_DEFAULT_CONFIG = _ROOT / "config" / "default.json"

_CODE_RE = re.compile(r"\b[A-Z]-\d{2}\b")
_KEY_RE = re.compile(r"`([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+)`")


def _doc_text() -> str:
    return _DOC.read_text(encoding="utf-8")


def _config_has(path: str) -> bool:
    node: object = json.loads(_DEFAULT_CONFIG.read_text(encoding="utf-8"))
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return True


def test_dictionary_document_exists() -> None:
    assert _DOC.is_file()
    assert "诊断表" in _doc_text()


def test_all_referenced_event_codes_exist() -> None:
    codes = set(_CODE_RE.findall(_doc_text()))
    assert codes, "诊断字典未引用任何事件码"
    missing = sorted(code for code in codes if code not in EVENT_REGISTRY)
    assert not missing, f"诊断字典引用了不存在的事件码: {missing}"


def test_all_referenced_config_keys_exist() -> None:
    top_level = set(json.loads(_DEFAULT_CONFIG.read_text(encoding="utf-8")))
    keys = {
        key
        for key in _KEY_RE.findall(_doc_text())
        if key.split(".")[0] in top_level
    }
    assert keys, "诊断字典未引用任何配置键"
    missing = sorted(key for key in keys if not _config_has(key))
    assert not missing, f"诊断字典引用了不存在的配置键: {missing}"


def test_dictionary_covers_core_symptom_families() -> None:
    text = _doc_text()
    for code in (
        "N-02",
        "C-01",
        "C-02",
        "C-05",
        "E-04",
        "T-03",
        "R-03",
        "L-01",
        "S-02",
        "X-02",
    ):
        assert code in text, f"诊断字典缺少核心症状事件码 {code}"


def test_report_template_present() -> None:
    text = _doc_text()
    assert "[版本=" in text
    assert "[现象=" in text
    assert "[证据=" in text


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
