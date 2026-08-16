"""Enforces guideline §11.4: release tests must be stable across repeated runs.

One green pytest run can hide flaky tests. This gate repeats the same pytest
command and fails the release if any run fails or if the passing summary
changes across iterations.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shlex
import signal

# Release gate must spawn pytest with shell=False.
import subprocess  # nosec B404
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "flaky_detector.json"
DEFAULT_COMMAND = "uv run pytest tests -n 4 --dist loadfile --no-header -q"
DEFAULT_ITERATIONS = 3
DEFAULT_ITERATION_TIMEOUT_SECONDS = 900.0
TERMINATION_GRACE_SECONDS = 5.0
PYTEST_RANDOMLY_SEED_MAX = 2**32 - 1
STDIO_TAIL_LINES = 120
SUMMARY_TOKEN = re.compile(r"\d+\s+(?:passed|failed|error|errors|skipped|xfailed|xpassed|warnings?|deselected)")
SUMMARY_LINE = re.compile(
    r"^\s*=*\s*"
    r"(?P<summary>\d+\s+(?:passed|failed|error|errors|skipped|xfailed|xpassed|warnings?|deselected)"
    r"(?:,\s*\d+\s+(?:passed|failed|error|errors|skipped|xfailed|xpassed|warnings?|deselected))*)"
    r"(?:\s+in\s+.+?)?\s*=*\s*$"
)


@dataclass(frozen=True)
class FlakyRun:
    iteration: int
    returncode: int
    summary: str
    duration_seconds: float
    command: list[str]
    randomly_seed: int | None
    is_timed_out: bool
    stdout_tail: list[str]
    stderr_tail: list[str]


@dataclass(frozen=True)
class FlakyReport:
    command: list[str]
    iterations: int
    gate_pass: bool
    is_stable_outcome: bool
    runs: list[FlakyRun]


def _tail_lines(value: str, *, limit: int = STDIO_TAIL_LINES) -> list[str]:
    lines = [line for line in value.splitlines() if line.strip()]
    return lines[-limit:]


def _pytest_summary(stdout: str, stderr: str) -> str:
    del stderr
    for line in reversed(_tail_lines(stdout, limit=80)):
        match = SUMMARY_LINE.fullmatch(line)
        if match is not None:
            return ", ".join(SUMMARY_TOKEN.findall(match.group("summary")))
    return "<no pytest summary>"


def _uses_pytest(command: Sequence[str]) -> bool:
    return any(Path(token).name in {"pytest", "py.test"} for token in command)


def _has_explicit_randomly_seed(command: Sequence[str]) -> bool:
    # pytest-randomly CLI flag, not a credential.
    return any(
        token == "--randomly-seed" or token.startswith("--randomly-seed=")  # nosec B105
        for token in command
    )


def _disables_pytest_randomly(command: Sequence[str]) -> bool:
    for index, token in enumerate(command):
        if token == "-p" and index + 1 < len(command) and command[index + 1] == "no:randomly":  # nosec B105
            return True
        # pytest plugin flag, not a credential.
        if token == "-pno:randomly":  # nosec B105
            return True
    return False


def _should_manage_randomly_seed(command: Sequence[str]) -> bool:
    return _uses_pytest(command) and not _has_explicit_randomly_seed(command) and not _disables_pytest_randomly(command)


def _seed_for_iteration(seed_base: int, iteration: int) -> int:
    seed = (seed_base + ((iteration - 1) * 1_000_003)) % PYTEST_RANDOMLY_SEED_MAX
    return seed or PYTEST_RANDOMLY_SEED_MAX


def _command_for_iteration(
    command: Sequence[str],
    *,
    iteration: int,
    seed_base: int | None,
) -> tuple[list[str], int | None]:
    if seed_base is None:
        return list(command), None

    seed = _seed_for_iteration(seed_base, iteration)
    return [*command, f"--randomly-seed={seed}"], seed


def _run_once(
    command: Sequence[str],
    *,
    cwd: Path,
    iteration: int,
    seed_base: int | None,
    timeout_seconds: float,
) -> FlakyRun:
    iteration_command, randomly_seed = _command_for_iteration(command, iteration=iteration, seed_base=seed_base)
    started_at = time.monotonic()
    # The command is CI-controlled and never uses shell=True.
    process = subprocess.Popen(  # nosec B603
        iteration_command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        start_new_session=os.name == "posix",
    )
    is_timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        is_timed_out = True
        _terminate_process_tree(process)
        stdout, stderr = process.communicate()
    duration = time.monotonic() - started_at
    return FlakyRun(
        iteration=iteration,
        returncode=124 if is_timed_out else process.returncode,
        summary="<timeout>" if is_timed_out else _pytest_summary(stdout, stderr),
        duration_seconds=round(duration, 3),
        command=iteration_command,
        randomly_seed=randomly_seed,
        is_timed_out=is_timed_out,
        stdout_tail=_tail_lines(stdout),
        stderr_tail=_tail_lines(stderr),
    )


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    is_parent_running = process.poll() is None
    if os.name == "posix":
        # communicate() can time out after the group leader has already exited
        # when one of its descendants inherited and still holds stdout/stderr.
        # The process group must therefore be signalled even if poll() says the
        # direct child is done.
        _signal_process_group(process.pid, signal.SIGTERM)
    elif is_parent_running:
        process.terminate()
    if is_parent_running:
        try:
            process.wait(timeout=TERMINATION_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass
    if os.name == "posix":
        # A parent can exit on SIGTERM while a descendant ignores it and keeps
        # stdout/stderr open. Always issue the final group kill before
        # communicate() so the gate cannot hang while collecting its report.
        _signal_process_group(process.pid, signal.SIGKILL)
    elif process.poll() is None:
        process.kill()
    if process.poll() is None:
        process.wait()


def _signal_process_group(process_group_id: int, signal_number: int) -> None:
    try:
        os.killpg(process_group_id, signal_number)
    except ProcessLookupError:
        return


def _report(command: Sequence[str], iterations: int, runs: list[FlakyRun]) -> FlakyReport:
    outcomes = {(run.returncode, run.summary) for run in runs}
    is_stable_outcome = len(runs) == iterations and len(outcomes) == 1
    gate_pass = is_stable_outcome and all(run.returncode == 0 for run in runs)
    return FlakyReport(list(command), iterations, gate_pass, is_stable_outcome, list(runs))


def run_gate(
    command: Sequence[str],
    *,
    iterations: int,
    cwd: Path,
    timeout_seconds: float = DEFAULT_ITERATION_TIMEOUT_SECONDS,
    progress: Callable[[FlakyReport], None] | None = None,
) -> FlakyReport:
    if iterations < 2:
        raise ValueError("flaky detection needs at least two iterations")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("iteration timeout must be finite and positive")
    seed_base = time.time_ns() % PYTEST_RANDOMLY_SEED_MAX if _should_manage_randomly_seed(command) else None
    runs: list[FlakyRun] = []
    for index in range(1, iterations + 1):
        seed = _seed_for_iteration(seed_base, index) if seed_base is not None else None
        print(f"Flaky iteration {index}/{iterations} started (seed={seed}, timeout={timeout_seconds:g}s).", flush=True)
        run = _run_once(
            command,
            cwd=cwd,
            iteration=index,
            seed_base=seed_base,
            timeout_seconds=timeout_seconds,
        )
        runs.append(run)
        partial = _report(command, iterations, runs)
        if progress is not None:
            progress(partial)
        print(
            f"Flaky iteration {index}/{iterations} finished: rc={run.returncode}, "
            f"summary={run.summary}, duration={run.duration_seconds}s.",
            flush=True,
        )
        if run.is_timed_out:
            break
    return _report(command, iterations, runs)


def write_report(output: Path, report: FlakyReport) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "gate": "G19 flaky detector",
        "count": 0 if report.gate_pass else 1,
        "baseline": 0,
        **asdict(report),
    }
    output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _print_failure(report: FlakyReport, output: Path) -> None:
    print("Release gate blocked: pytest results were not stable across repeated runs.")
    for run in report.runs:
        seed_note = f", randomly_seed={run.randomly_seed}" if run.randomly_seed is not None else ""
        print(f"- iteration {run.iteration}: rc={run.returncode}, summary={run.summary}{seed_note}")
    if not report.is_stable_outcome:
        print("- repeated runs produced different outcomes")
    print(f"Report: {output.relative_to(ROOT)}")


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run pytest repeatedly and fail on flaky outcomes.")
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--command", default=DEFAULT_COMMAND)
    parser.add_argument("--cwd", type=Path, default=ROOT)
    parser.add_argument(
        "--iteration-timeout-seconds",
        type=float,
        default=DEFAULT_ITERATION_TIMEOUT_SECONDS,
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    command = shlex.split(args.command)
    report = run_gate(
        command,
        iterations=args.iterations,
        cwd=args.cwd,
        timeout_seconds=args.iteration_timeout_seconds,
        progress=lambda partial: write_report(args.output, partial),
    )
    write_report(args.output, report)
    if not report.gate_pass:
        _print_failure(report, args.output)
        return 1
    summary = report.runs[0].summary
    print(f"Flaky detector passed: {args.iterations} repeated pytest runs produced stable result ({summary}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
