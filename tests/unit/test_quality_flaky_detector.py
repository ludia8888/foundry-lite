from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

from scripts.quality import check_flaky_detector as gate

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "quality" / "check_flaky_detector.py"


def _run_gate_command(tmp_path: Path, command: str, *, iterations: int = 3) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--iterations",
            str(iterations),
            "--output",
            str(tmp_path / "flaky_detector.json"),
            "--command",
            command,
            "--cwd",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def _run_gate(tmp_path: Path, runner: Path, *, iterations: int = 3) -> subprocess.CompletedProcess[str]:
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(runner))}"
    return _run_gate_command(tmp_path, command, iterations=iterations)


def test_pytest_commands_get_per_iteration_randomly_seeds() -> None:
    command = [sys.executable, "-m", "pytest", "tests", "-n", "auto", "--no-header", "-q"]

    first_command, first_seed = gate._command_for_iteration(command, iteration=1, seed_base=100)
    second_command, second_seed = gate._command_for_iteration(command, iteration=2, seed_base=100)

    assert first_seed == 100
    assert second_seed != first_seed
    assert first_command[-1] == "--randomly-seed=100"
    assert second_command[-1] == f"--randomly-seed={second_seed}"
    assert gate._should_manage_randomly_seed(command) is True


def test_non_pytest_commands_do_not_get_randomly_seeds() -> None:
    command = [sys.executable, "stable.py"]

    iteration_command, randomly_seed = gate._command_for_iteration(command, iteration=1, seed_base=None)

    assert iteration_command == command
    assert randomly_seed is None
    assert gate._should_manage_randomly_seed(command) is False


def test_flaky_detector_reports_per_iteration_pytest_randomly_seeds(tmp_path: Path) -> None:
    (tmp_path / "test_sample.py").write_text("def test_sample():\n    assert True\n", encoding="utf-8")

    result = _run_gate_command(
        tmp_path,
        f"{shlex.quote(sys.executable)} -m pytest -q --no-header",
        iterations=2,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((tmp_path / "flaky_detector.json").read_text(encoding="utf-8"))
    seeds = [run["randomly_seed"] for run in report["runs"]]
    assert all(isinstance(seed, int) for seed in seeds)
    assert len(set(seeds)) == 2
    assert [run["command"][-1] for run in report["runs"]] == [f"--randomly-seed={seed}" for seed in seeds]


def test_flaky_detector_passes_for_stable_passing_runs(tmp_path: Path) -> None:
    runner = tmp_path / "stable.py"
    runner.write_text("print('2 passed in 0.01s')\n", encoding="utf-8")

    result = _run_gate(tmp_path, runner)

    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((tmp_path / "flaky_detector.json").read_text(encoding="utf-8"))
    assert report["gate_pass"] is True
    assert report["is_stable_outcome"] is True
    assert [run["summary"] for run in report["runs"]] == ["2 passed"] * 3
    assert all(run["randomly_seed"] is None for run in report["runs"])


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
    assert all("command" in run for run in report["runs"])


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
