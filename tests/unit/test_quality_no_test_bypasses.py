from __future__ import annotations

import os as _os

if not _os.path.exists(_os.path.join(_os.path.dirname(__file__), "..", "..", "scripts", "quality")):
    import pytest

    pytest.skip(
        "quality self-tests require the scripts/ tree; skipped under mutmut's mutants/ copy.",
        allow_module_level=True,
    )

from pathlib import Path

from scripts.quality import check_no_test_bypasses as checker


def _write_test_file(root: Path, relative_path: str, source: str) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def test_no_bypass_gate_blocks_called_skip_decorators_once(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    path = _write_test_file(
        tmp_path,
        "tests/unit/test_skipped.py",
        """
import pytest


@pytest.mark.skip(reason="temporary")
def test_blocked():
    assert True
""",
    )

    assert checker._violations_for_file(path) == ["tests/unit/test_skipped.py:5 uses @pytest.mark.skip"]


def test_no_bypass_gate_blocks_bare_skip_decorators(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    path = _write_test_file(
        tmp_path,
        "tests/unit/test_skipped.py",
        """
import pytest


@pytest.mark.skip
def test_blocked():
    assert True
""",
    )

    assert checker._violations_for_file(path) == ["tests/unit/test_skipped.py:5 uses @pytest.mark.skip"]


def test_no_bypass_gate_blocks_direct_runtime_skips(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    path = _write_test_file(
        tmp_path,
        "tests/unit/test_skipped.py",
        """
import pytest


def test_blocked():
    pytest.skip("temporary")
""",
    )

    assert checker._violations_for_file(path) == ["tests/unit/test_skipped.py:6 uses pytest.skip"]


def test_no_bypass_gate_allows_only_documented_postgres_local_opt_out(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    path = _write_test_file(
        tmp_path,
        "tests/contracts/conftest.py",
        """
import pytest


skip_if_no_postgres = pytest.mark.skipif(
    True,
    reason="FOUNDRY_LITE_SKIP_POSTGRES_CONTRACTS is local-only",
)


def pytest_configure():
    pytest.skip("postgres contract tests disabled")
""",
    )

    assert checker._violations_for_file(path) == []
