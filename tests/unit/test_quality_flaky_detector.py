from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "quality" / "check_flaky_detector.py"


def _run_gate(tmp_path: Path, runner: Path, *, iterations: int = 3) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--iterations",
            str(iterations),
            "--output",
            str(tmp_path / "flaky_detector.json"),
            "--command",
            f"{shlex.quote(sys.executable)} {shlex.quote(str(runner))}",
            "--cwd",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_flaky_detector_passes_for_stable_passing_runs(tmp_path: Path) -> None:
    runner = tmp_path / "stable.py"
    runner.write_text("print('2 passed in 0.01s')\n", encoding="utf-8")

    result = _run_gate(tmp_path, runner)

    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((tmp_path / "flaky_detector.json").read_text(encoding="utf-8"))
    assert report["gate_pass"] is True
    assert report["is_stable_outcome"] is True
    assert [run["summary"] for run in report["runs"]] == ["2 passed"] * 3


def test_flaky_detector_fails_when_any_iteration_fails(tmp_path: Path) -> None:
    runner = tmp_path / "fails_once.py"
    runner.write_text(
        "from pathlib import Path\n"
        "counter = Path('counter.txt')\n"
        "count = int(counter.read_text() or '0') if counter.exists() else 0\n"
        "counter.write_text(str(count + 1))\n"
        "if count == 1:\n"
        "    print('1 failed, 1 passed in 0.01s')\n"
        "    raise SystemExit(1)\n"
        "print('2 passed in 0.01s')\n",
        encoding="utf-8",
    )

    result = _run_gate(tmp_path, runner)

    assert result.returncode == 1
    report = json.loads((tmp_path / "flaky_detector.json").read_text(encoding="utf-8"))
    assert report["gate_pass"] is False
    assert any(run["returncode"] == 1 for run in report["runs"])


def test_flaky_detector_fails_when_passing_summary_changes(tmp_path: Path) -> None:
    runner = tmp_path / "changes_collection.py"
    runner.write_text(
        "from pathlib import Path\n"
        "counter = Path('counter.txt')\n"
        "count = int(counter.read_text() or '0') if counter.exists() else 0\n"
        "counter.write_text(str(count + 1))\n"
        "print(f'{2 + count} passed in 0.01s')\n",
        encoding="utf-8",
    )

    result = _run_gate(tmp_path, runner)

    assert result.returncode == 1
    report = json.loads((tmp_path / "flaky_detector.json").read_text(encoding="utf-8"))
    assert report["gate_pass"] is False
    assert report["is_stable_outcome"] is False
