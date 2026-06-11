from __future__ import annotations

from pathlib import Path

from scripts.quality import check_no_test_sleep


def _write(root: Path, relative_path: str, source: str) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def test_no_test_sleep_gate_flags_time_sleep(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(check_no_test_sleep, "ROOT", tmp_path)
    monkeypatch.setattr(check_no_test_sleep, "TESTS", tmp_path / "tests")
    path = _write(
        tmp_path,
        "tests/test_racy.py",
        "import time\n\n\ndef test_waits_by_sleeping():\n    time.sleep(1)\n",
    )

    findings = check_no_test_sleep.collect_findings()

    assert [(finding.path, finding.line, finding.call) for finding in findings] == [
        (str(path.relative_to(tmp_path)), 5, "time.sleep")
    ]


def test_no_test_sleep_gate_flags_asyncio_sleep(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(check_no_test_sleep, "ROOT", tmp_path)
    monkeypatch.setattr(check_no_test_sleep, "TESTS", tmp_path / "tests")
    path = _write(
        tmp_path,
        "tests/test_async_racy.py",
        "import asyncio\n\n\nasync def test_waits_by_sleeping():\n    await asyncio.sleep(1)\n",
    )

    findings = check_no_test_sleep.collect_findings()

    assert [(finding.path, finding.line, finding.call) for finding in findings] == [
        (str(path.relative_to(tmp_path)), 5, "asyncio.sleep")
    ]


def test_no_test_sleep_gate_ignores_documented_strings(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(check_no_test_sleep, "ROOT", tmp_path)
    monkeypatch.setattr(check_no_test_sleep, "TESTS", tmp_path / "tests")
    _write(
        tmp_path,
        "tests/test_docs.py",
        "def test_mentions_sleep_in_fixture_source():\n    source = 'time.sleep(1)'\n    assert source\n",
    )

    assert check_no_test_sleep.collect_findings() == []
