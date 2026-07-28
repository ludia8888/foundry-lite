"""Enforces guideline §11.4: release tests must be stable across repeated runs.

One green pytest run can hide flaky tests. This gate repeats the same pytest
command and fails the release if any run fails or if the passing summary
changes across iterations.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex

# Release gate must spawn pytest with shell=False.
import subprocess  # nosec B404
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "flaky_detector.json"
DEFAULT_COMMAND = "uv run pytest tests -n auto --no-header -q"
DEFAULT_ITERATIONS = 3
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


def _run_once(command: Sequence[str], *, cwd: Path, iteration: int, seed_base: int | None) -> FlakyRun:
    iteration_command, randomly_seed = _command_for_iteration(command, iteration=iteration, seed_base=seed_base)
    started_at = time.monotonic()
    # The command is CI-controlled and never uses shell=True.
    result = subprocess.run(  # nosec B603
        iteration_command,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    duration = time.monotonic() - started_at
    return FlakyRun(
        iteration=iteration,
        returncode=result.returncode,
        summary=_pytest_summary(result.stdout, result.stderr),
        duration_seconds=round(duration, 3),
        command=iteration_command,
        randomly_seed=randomly_seed,
        stdout_tail=_tail_lines(result.stdout),
        stderr_tail=_tail_lines(result.stderr),
    )


def run_gate(command: Sequence[str], *, iterations: int, cwd: Path) -> FlakyReport:
    if iterations < 2:
        raise ValueError("flaky detection needs at least two iterations")
    seed_base = time.time_ns() % PYTEST_RANDOMLY_SEED_MAX if _should_manage_randomly_seed(command) else None
    runs = [_run_once(command, cwd=cwd, iteration=index, seed_base=seed_base) for index in range(1, iterations + 1)]
    outcomes = {(run.returncode, run.summary) for run in runs}
    is_stable_outcome = len(outcomes) == 1
    gate_pass = is_stable_outcome and all(run.returncode == 0 for run in runs)
    return FlakyReport(
        command=list(command),
        iterations=iterations,
        gate_pass=gate_pass,
        is_stable_outcome=is_stable_outcome,
        runs=runs,
    )


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
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    command = shlex.split(args.command)
    report = run_gate(command, iterations=args.iterations, cwd=args.cwd)
    write_report(args.output, report)
    if not report.gate_pass:
        _print_failure(report, args.output)
        return 1
    summary = report.runs[0].summary
    print(f"Flaky detector passed: {args.iterations} repeated pytest runs produced stable result ({summary}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
