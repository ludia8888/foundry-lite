"""Verifies every pytest-based quality:* ratchet still selects live tests.

The PR gate runs the full test suite once (coverage lane), so re-executing the
~90 per-capability ratchet subsets serially only re-proves the same runs while
adding wall-clock. What the ratchets uniquely guarantee between releases is
*inventory*: each named capability keeps at least one collectable test, so a
rename or deletion cannot silently empty a capability's evidence.

This gate keeps that guarantee cheap: it parses the pytest selection out of
every `quality:*` script in package.json and runs `pytest --collect-only` for
each in parallel. A ratchet whose selection collects zero tests fails the gate
with the same signal a full run would have produced (pytest exit code 5).
The full execution rehearsal remains in the release/nightly runtime-full lane.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess  # nosec B404 — spawns pytest --collect-only for gate evidence
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "ratchet_manifest.json"
PYTEST_NO_TESTS_COLLECTED = 5


@dataclass(frozen=True)
class RatchetSelection:
    name: str
    pytest_args: list[str]


@dataclass(frozen=True)
class RatchetResult:
    name: str
    is_valid: bool
    returncode: int
    duration_seconds: float
    detail: str


def _pytest_segments(script: str) -> list[list[str]]:
    segments: list[list[str]] = []
    for raw_segment in script.split("&&"):
        tokens = shlex.split(raw_segment.strip())
        # 선행 K=V 환경변수 지정을 제거하고 명령 본체만 남긴다.
        while tokens and "=" in tokens[0] and not tokens[0].startswith("-"):
            tokens.pop(0)
        if "pytest" in tokens:
            segments.append(tokens[tokens.index("pytest") + 1 :])
    return segments


def get_ratchet_selections(package_json: Path) -> list[RatchetSelection]:
    scripts = json.loads(package_json.read_text(encoding="utf-8"))["scripts"]
    selections: list[RatchetSelection] = []
    for name, script in sorted(scripts.items()):
        if not name.startswith("quality:"):
            continue
        for index, args in enumerate(_pytest_segments(script)):
            suffix = f"#{index}" if index else ""
            selections.append(RatchetSelection(name=f"{name}{suffix}", pytest_args=args))
    return selections


def _collect_one(selection: RatchetSelection, env: dict[str, str]) -> RatchetResult:
    started = time.perf_counter()
    command = [
        sys.executable,
        "-m",
        "pytest",
        "--collect-only",
        "-q",
        "--no-header",
        "-p",
        "no:randomly",
        *selection.pytest_args,
    ]
    completed = subprocess.run(  # nosec B603 — fixed pytest command, shell=False
        command,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    duration = time.perf_counter() - started
    if completed.returncode == 0:
        return RatchetResult(selection.name, True, 0, duration, "collected")
    if completed.returncode == PYTEST_NO_TESTS_COLLECTED:
        detail = "ratchet selection collects zero tests; a rename/deletion emptied this capability's evidence"
    else:
        detail = (completed.stdout + completed.stderr).strip()[-2000:]
    return RatchetResult(selection.name, False, completed.returncode, duration, detail)


def collect_results(selections: list[RatchetSelection], *, jobs: int) -> list[RatchetResult]:
    env = dict(os.environ)
    env.setdefault("PYTHONPATH", ".:libs:apps/cli:apps/api:apps/worker")
    results: list[RatchetResult] = []
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = [pool.submit(_collect_one, selection, env) for selection in selections]
        for future in as_completed(futures):
            results.append(future.result())
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=min(10, os.cpu_count() or 4))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    selections = get_ratchet_selections(ROOT / "package.json")
    if not selections:
        print("Ratchet manifest gate failed: no pytest-based quality:* scripts found in package.json", file=sys.stderr)
        return 1

    started = time.perf_counter()
    results = sorted(collect_results(selections, jobs=args.jobs), key=lambda r: r.name)
    failures = [result for result in results if not result.is_valid]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({"gate_pass": not failures, "results": [asdict(r) for r in results]}, indent=2),
        encoding="utf-8",
    )

    total = time.perf_counter() - started
    if failures:
        for result in failures:
            print(f"FAIL {result.name} (exit {result.returncode}): {result.detail}", file=sys.stderr)
        print(f"Ratchet manifest gate failed: {len(failures)}/{len(results)} selections broken.", file=sys.stderr)
        return 1
    print(f"Ratchet manifest gate passed: {len(results)} quality:* pytest selections collect tests ({total:.1f}s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
