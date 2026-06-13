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
SUMMARY_TOKEN = re.compile(r"\d+\s+(?:passed|failed|error|errors|skipped|xfailed|xpassed|warnings?|deselected)")


@dataclass(frozen=True)
class FlakyRun:
    iteration: int
    returncode: int
    summary: str
    duration_seconds: float
    stdout_tail: list[str]
    stderr_tail: list[str]


@dataclass(frozen=True)
class FlakyReport:
    command: list[str]
    iterations: int
    gate_pass: bool
    is_stable_outcome: bool
    runs: list[FlakyRun]


def _tail_lines(value: str, *, limit: int = 20) -> list[str]:
    lines = [line for line in value.splitlines() if line.strip()]
    return lines[-limit:]


def _pytest_summary(stdout: str, stderr: str) -> str:
    for line in reversed(_tail_lines(f"{stdout}\n{stderr}", limit=80)):
        tokens = SUMMARY_TOKEN.findall(line)
        if tokens:
            return ", ".join(tokens)
    return "<no pytest summary>"


def _run_once(command: Sequence[str], *, cwd: Path, iteration: int) -> FlakyRun:
    started_at = time.monotonic()
    # The command is CI-controlled and never uses shell=True.
    result = subprocess.run(  # nosec B603
        list(command),
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
        stdout_tail=_tail_lines(result.stdout),
        stderr_tail=_tail_lines(result.stderr),
    )


def run_gate(command: Sequence[str], *, iterations: int, cwd: Path) -> FlakyReport:
    if iterations < 2:
        raise ValueError("flaky detection needs at least two iterations")
    runs = [_run_once(command, cwd=cwd, iteration=index) for index in range(1, iterations + 1)]
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
        print(f"- iteration {run.iteration}: rc={run.returncode}, summary={run.summary}")
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
