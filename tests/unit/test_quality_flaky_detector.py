from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from scripts.quality import check_flaky_detector as gate

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "quality" / "check_flaky_detector.py"


def _run_gate_command(
    tmp_path: Path,
    command: str,
    *,
    iterations: int = 3,
    timeout_seconds: float | None = None,
) -> subprocess.CompletedProcess[str]:
    timeout_arguments = ["--iteration-timeout-seconds", str(timeout_seconds)] if timeout_seconds is not None else []
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
            *timeout_arguments,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def _run_gate(tmp_path: Path, runner: Path, *, iterations: int = 3) -> subprocess.CompletedProcess[str]:
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(runner))}"
    return _run_gate_command(tmp_path, command, iterations=iterations)


def test_pytest_commands_get_per_iteration_randomly_seeds() -> None:
    command = [
        sys.executable,
        "-m",
        "pytest",
        "tests",
        "-n",
        "4",
        "--dist",
        "loadfile",
        "--no-header",
        "-q",
    ]

    first_command, first_seed = gate._command_for_iteration(command, iteration=1, seed_base=100)
    second_command, second_seed = gate._command_for_iteration(command, iteration=2, seed_base=100)

    assert first_seed == 100
    assert second_seed != first_seed
    assert first_command[-1] == "--randomly-seed=100"
    assert second_command[-1] == f"--randomly-seed={second_seed}"
    assert gate._should_manage_randomly_seed(command) is True


def test_default_parallel_pytest_command_keeps_container_tests_on_one_worker_per_file() -> None:
    command = shlex.split(gate.DEFAULT_COMMAND)

    assert command[command.index("-n") + 1] == "4"
    assert command[command.index("--dist") + 1] == "loadfile"


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


def test_pytest_summary_ignores_broker_failure_diagnostics_on_stderr() -> None:
    stdout = "3856 passed in 314.63s (0:05:14)\n"
    stderr = (
        "%3|1785208199.122|FAIL|rdkafka#producer-1| "
        "[thrd:localhost:38108/1]: Connect to ipv4#127.0.0.1:38108 failed: Connection refused\n"
    )

    assert gate._pytest_summary(stdout, stderr) == "3856 passed"


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


def test_flaky_detector_bounds_hung_iteration_and_kills_descendants(tmp_path: Path) -> None:
    runner = tmp_path / "hangs_with_child.py"
    child_pid_path = tmp_path / "child.pid"
    child_setup = (
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"Path({str(child_pid_path)!r}).write_text(str(child.pid))\n"
        if os.name == "posix"
        else ""
    )
    runner.write_text(
        "import subprocess, sys, time\n"
        "from pathlib import Path\n"
        f"{child_setup}"
        "print('runner started', flush=True)\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )

    started_at = time.monotonic()
    result = _run_gate_command(
        tmp_path,
        f"{shlex.quote(sys.executable)} {shlex.quote(str(runner))}",
        timeout_seconds=0.5,
    )
    duration = time.monotonic() - started_at

    assert result.returncode == 1
    assert duration < 8
    report = json.loads((tmp_path / "flaky_detector.json").read_text(encoding="utf-8"))
    assert report["count"] == 1
    assert report["gate_pass"] is False
    assert report["is_stable_outcome"] is False
    assert len(report["runs"]) == 1
    assert report["runs"][0]["returncode"] == 124
    assert report["runs"][0]["summary"] == "<timeout>"
    assert report["runs"][0]["is_timed_out"] is True
    if os.name == "posix":
        assert _wait_until_not_live(int(child_pid_path.read_text(encoding="utf-8")))


def test_flaky_detector_kills_pipe_holding_child_after_parent_exits(tmp_path: Path) -> None:
    if os.name != "posix":
        return
    runner = tmp_path / "exits_with_pipe_holding_child.py"
    child_pid_path = tmp_path / "child.pid"
    runner.write_text(
        "import subprocess, sys\n"
        "from pathlib import Path\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"Path({str(child_pid_path)!r}).write_text(str(child.pid))\n"
        "print('parent exiting', flush=True)\n",
        encoding="utf-8",
    )

    started_at = time.monotonic()
    result = _run_gate_command(
        tmp_path,
        f"{shlex.quote(sys.executable)} {shlex.quote(str(runner))}",
        timeout_seconds=0.5,
    )
    duration = time.monotonic() - started_at

    assert result.returncode == 1
    assert duration < 8
    report = json.loads((tmp_path / "flaky_detector.json").read_text(encoding="utf-8"))
    assert report["runs"][0]["returncode"] == 124
    assert report["runs"][0]["is_timed_out"] is True
    assert _wait_until_not_live(int(child_pid_path.read_text(encoding="utf-8")))


def test_flaky_detector_rejects_non_positive_or_non_finite_timeout(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="finite and positive"):
        gate.run_gate([sys.executable, "-c", "pass"], iterations=2, cwd=tmp_path, timeout_seconds=0)
    with pytest.raises(ValueError, match="finite and positive"):
        gate.run_gate(
            [sys.executable, "-c", "pass"],
            iterations=2,
            cwd=tmp_path,
            timeout_seconds=float("nan"),
        )


def _wait_until_not_live(process_id: int) -> bool:
    deadline = time.monotonic() + 3
    poll_interval = threading.Event()
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(process_id)],
            capture_output=True,
            text=True,
            check=False,
        )
        state = result.stdout.strip()
        if result.returncode != 0 or not state or state.startswith("Z"):
            return True
        poll_interval.wait(0.05)
    return False
