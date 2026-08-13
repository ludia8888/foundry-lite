"""Run the budgeted pull-request gate without weakening release evidence.

Enforces engineering guideline §16 and quality-gate roadmap §5. Pull requests
run diff security checks, repository invariants, directly related tests, and
frontend type contracts inside one bounded job. Full coverage, live runtime,
browser, dependency, and CodeQL evidence continues on main/release/nightly.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess  # nosec B404 - this gate intentionally spawns fixed quality commands
import sys
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_GIT_EXECUTABLE = shutil.which("git")
if _GIT_EXECUTABLE is None:
    raise RuntimeError("git executable is required for the pull-request gate")
GIT: str = _GIT_EXECUTABLE
REPORT_PATH = ROOT / "artifacts" / "quality" / "pr_fast_gate.json"
SECURITY_REPORT_PATH = ROOT / "artifacts" / "quality" / "pr_diff_security.json"
DEFAULT_BUDGET_SECONDS = 420.0
MAX_SELECTED_TEST_FILES = 32
# Floor of source-linked test files, held even when changed tests already fill the cap.
MIN_LINKED_TEST_FILES = 8
GENERIC_MODULE_STEMS = {
    "__init__",
    "base",
    "client",
    "common",
    "config",
    "constants",
    "dependencies",
    "helpers",
    "main",
    "models",
    "policy",
    "protocols",
    "repository",
    "runtime",
    "schemas",
    "service",
    "types",
    "utils",
}
HIGH_CONFIDENCE_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
)
DANGEROUS_PYTHON_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("dynamic-eval", re.compile(r"(?<![A-Za-z0-9_])(eval|exec)\s*\(")),
    ("shell-true", re.compile(r"\bshell\s*=\s*True\b")),
    ("unsafe-pickle-load", re.compile(r"\bpickle\.loads?\s*\(")),
)


@dataclass(frozen=True)
class PullRequestPlan:
    changed_files: tuple[str, ...]
    selected_tests: tuple[str, ...]
    source_files_without_tests: tuple[str, ...]
    has_backend: bool
    has_frontend: bool
    has_sdk_contract: bool
    is_docs_only: bool
    should_install_node: bool = False
    should_build_code_execution_image: bool = False
    dropped_linked_tests: tuple[str, ...] = ()


@dataclass(frozen=True)
class CommandResult:
    name: str
    returncode: int
    duration_seconds: float
    output: str


def _safe_ref(value: str) -> str:
    if value.startswith("-") or not re.fullmatch(r"[A-Za-z0-9_./-]+", value):
        raise ValueError(f"unsafe git ref: {value!r}")
    return value


def _git_output(arguments: list[str]) -> str:
    completed = subprocess.run(  # nosec B603 - fixed git executable and validated refs
        [GIT, *arguments],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def changed_files(base: str, head: str) -> tuple[str, ...]:
    diff_range = f"{_safe_ref(base)}...{_safe_ref(head)}"
    output = _git_output(["diff", "--name-only", "--diff-filter=ACMRTUXB", diff_range])
    paths = {line.strip() for line in output.splitlines() if line.strip()}
    if head == "HEAD" and os.environ.get("GITHUB_ACTIONS") != "true":
        local_output = _git_output(["ls-files", "--modified", "--others", "--exclude-standard"])
        paths.update(line.strip() for line in local_output.splitlines() if line.strip())
    return tuple(sorted(paths))


def _is_backend_path(path: str) -> bool:
    return (
        path.endswith(".py")
        or path.startswith(("migrations/", "infra/schema_revisions/"))
        or path
        in {
            "pyproject.toml",
            "uv.lock",
        }
    )


def _is_frontend_path(path: str) -> bool:
    return (
        path.startswith(("apps/foundry/", "packages/sdk-ts/", "tests/sdk/"))
        or path.endswith((".ts", ".tsx", ".js", ".mjs", ".css"))
        or path in {"package.json", "pnpm-lock.yaml", "pnpm-workspace.yaml"}
    )


def _needs_sdk_contract(path: str) -> bool:
    return _is_frontend_path(path) or path.startswith(
        ("apps/api/", "scripts/sdk_generator/", "libs/foundry_lite/interfaces/")
    )


def _is_docs_path(path: str) -> bool:
    return path.endswith((".md", ".json")) and (
        path.startswith("docs/") or "/docs/" in path or path in {"README.md", "AGENTS.md"}
    )


def _quality_control_changed(paths: tuple[str, ...]) -> bool:
    return any(
        path.startswith((".github/workflows/", "scripts/quality/"))
        or path in {"package.json", "pyproject.toml", "scripts/ci_gate.sh"}
        for path in paths
    )


def _source_module_marker(path: str) -> str:
    source = Path(path)
    if source.suffix != ".py" or path.startswith("tests/"):
        return ""
    return source.stem if source.stem not in GENERIC_MODULE_STEMS and len(source.stem) >= 6 else ""


def _direct_test_matches(source_path: str, test_paths: tuple[Path, ...]) -> set[str]:
    stem = _source_module_marker(source_path)
    if not stem:
        return set()
    matches: set[str] = set()
    for test_path in test_paths:
        relative = test_path.relative_to(ROOT).as_posix()
        if not _is_fast_test_path(relative):
            continue
        if stem in test_path.stem:
            matches.add(relative)
    return matches


def _bounded_test_selection(
    changed_tests: set[str],
    ranked_direct_tests: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Select changed tests plus a never-empty slice of source-linked tests.

    A source-linked test is the only thing that catches "a source file changed but its own
    test file did not" — the exact shape that broke main after PR #164, where 55 changed test
    files consumed every slot and the linked selection silently became empty. So the linked
    slice keeps a floor: a large change widens the risk that this evidence covers, it must not
    delete it. Anything the floor cannot fit is returned so the gate can report the drop
    instead of presenting a truncated run as full coverage.
    """
    required = sorted(changed_tests)
    candidates = [path for path in ranked_direct_tests if path not in changed_tests]
    slots = max(MIN_LINKED_TEST_FILES, MAX_SELECTED_TEST_FILES - len(required))
    return tuple([*required, *candidates[:slots]]), tuple(sorted(candidates[slots:]))


def _is_fast_test_path(path: str) -> bool:
    if not path.startswith("tests/") or not path.endswith(".py"):
        return False
    # Live-infrastructure tests boot real brokers, clusters, and CDC connectors
    # through testcontainers, so they belong to the runtime lane (AGENTS.md §28)
    # and are already enforced by named ratchets there. The `_live.py` suffix
    # only covers files that end that way; `test_kafka_live_broker_*`,
    # `test_debezium_live_cdc.py`, and `test_elasticsearch_live_cluster.py` name
    # the infrastructure mid-stem and previously leaked into the PR budget.
    slow_markers = (
        "_live.py",
        "live_broker",
        "live_cdc",
        "live_cluster",
        "temporal",
        "infra_composition",
        "all_infra",
        "golden_pipeline",
        "rest_connector",
    )
    if any(marker in path for marker in slow_markers):
        return False
    if path.startswith(("tests/unit/", "tests/contracts/", "tests/smoke/")):
        return True
    if not path.startswith("tests/integration/test_"):
        return False
    return True


def build_plan(paths: tuple[str, ...]) -> PullRequestPlan:
    """Build the bounded PR evidence plan from changed paths."""
    existing_tests = tuple(sorted(ROOT.glob("tests/**/test_*.py")))
    changed_python_tests = _changed_python_tests(paths)
    selected_tests, untested, dropped_linked = _source_test_evidence(paths, existing_tests, changed_python_tests)
    should_build_code_execution_image = _selected_tests_require_code_execution_image(selected_tests)
    return PullRequestPlan(
        changed_files=paths,
        selected_tests=selected_tests,
        source_files_without_tests=untested,
        dropped_linked_tests=dropped_linked,
        has_backend=any(_is_backend_path(path) for path in paths),
        has_frontend=any(_is_frontend_path(path) for path in paths),
        has_sdk_contract=any(_needs_sdk_contract(path) for path in paths),
        is_docs_only=bool(paths) and all(_is_docs_path(path) for path in paths),
        should_install_node=_should_install_node_runtime(paths) or should_build_code_execution_image,
        should_build_code_execution_image=should_build_code_execution_image,
    )


def _should_install_node_runtime(paths: tuple[str, ...]) -> bool:
    """Whether this run executes anything that needs installed Node dependencies.

    Frontend and SDK-contract checks obviously do. So does a quality-control change: it pulls
    `tests/unit/test_quality_ci_workflows.py` into the selection, and that file shells out to
    `node scripts/quality/run_ast_grep.cjs`. Without this the job skips `pnpm install` and the
    test fails on empty stdout rather than on anything it is meant to assert.
    """
    return (
        any(_is_frontend_path(path) for path in paths)
        or any(_needs_sdk_contract(path) for path in paths)
        or _quality_control_changed(paths)
    )


def _selected_tests_require_code_execution_image(selected_tests: Sequence[str]) -> bool:
    """Detect focused tests whose contract executes real user code in the pinned sandbox."""
    marker = "pytest.mark.code_execution_image"
    return any(marker in (ROOT / path).read_text(encoding="utf-8") for path in selected_tests)


def _changed_python_tests(paths: tuple[str, ...]) -> set[str]:
    changed_python_tests = {path for path in paths if _is_fast_test_path(path) and (ROOT / path).is_file()}
    if _quality_control_changed(paths):
        changed_python_tests.update(
            {
                "tests/unit/test_pr_fast_gate.py",
                "tests/unit/test_quality_ci_workflows.py",
            }
        )
    return changed_python_tests


def _source_test_evidence(
    paths: tuple[str, ...],
    existing_tests: tuple[Path, ...],
    changed_python_tests: set[str],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    source_paths = _source_python_paths(paths)
    source_matches = {path: _direct_test_matches(path, existing_tests) for path in source_paths}
    ranked = _rank_direct_tests(source_matches)
    selected_tests, dropped_linked = _bounded_test_selection(changed_python_tests, ranked)
    untested = tuple(
        sorted(path for path, matches in source_matches.items() if not matches and not changed_python_tests)
    )
    return selected_tests, untested, dropped_linked


def _rank_direct_tests(source_matches: Mapping[str, set[str]]) -> tuple[str, ...]:
    """Order linked tests by how many changed sources feed them, then by name.

    When the floor cannot hold every linked test, the ones kept should be those covering the
    most changed surface rather than whichever sorts first alphabetically.
    """
    link_counts: Counter[str] = Counter()
    for matches in source_matches.values():
        link_counts.update(matches)
    return tuple(sorted(link_counts, key=lambda test: (-link_counts[test], test)))


def _source_python_paths(paths: tuple[str, ...]) -> tuple[str, ...]:
    source_paths = tuple(
        path
        for path in paths
        if path.endswith(".py") and path.startswith(("libs/", "apps/", "scripts/")) and not path.startswith("tests/")
    )
    return source_paths


def _github_output(plan: PullRequestPlan, path: Path) -> None:
    values = {
        "has_backend": str(plan.has_backend).lower(),
        "has_frontend": str(plan.has_frontend).lower(),
        "has_sdk_contract": str(plan.has_sdk_contract).lower(),
        "is_docs_only": str(plan.is_docs_only).lower(),
        "should_install_node": str(plan.should_install_node).lower(),
        "should_build_code_execution_image": str(plan.should_build_code_execution_image).lower(),
    }
    with path.open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def _added_lines_by_file(base: str, head: str) -> dict[str, list[str]]:
    added = _parse_added_lines(_diff_patch(base, head))
    if _is_local_head(head):
        _include_untracked_lines(added)
    return added


def _diff_patch(base: str, head: str) -> str:
    diff_range = f"{_safe_ref(base)}...{_safe_ref(head)}"
    patch = _git_output(["diff", "--unified=0", "--no-color", diff_range])
    if _is_local_head(head):
        patch += _git_output(["diff", "--unified=0", "--no-color", "HEAD"])
    return patch


def _parse_added_lines(patch: str) -> dict[str, list[str]]:
    added: dict[str, list[str]] = {}
    current = ""
    for line in patch.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
            added.setdefault(current, [])
        elif current and line.startswith("+") and not line.startswith("+++"):
            added[current].append(line[1:])
    return added


def _is_local_head(head: str) -> bool:
    return head == "HEAD" and os.environ.get("GITHUB_ACTIONS") != "true"


def _include_untracked_lines(added: dict[str, list[str]]) -> None:
    untracked = {
        line.strip()
        for line in _git_output(["ls-files", "--others", "--exclude-standard"]).splitlines()
        if line.strip()
    }
    for path in untracked - set(added):
        candidate = ROOT / path
        if candidate.is_file():
            added[path] = candidate.read_text(encoding="utf-8", errors="replace").splitlines()


# A path that came out of a diff is untrusted text. The report is printed to CI logs, so it is
# reconstructed through this allowlist rather than interpolated: a filename cannot smuggle diff
# content, control characters, or a newline that would forge a second finding.
_REPORTABLE_PATH = re.compile(r"^[A-Za-z0-9._/-]{1,256}$")


def _violation(path: str, line_number: int, name: str) -> str:
    """Where a finding is, never what it matched.

    A secret scan that echoed the secret it found would put the credential in the CI log of
    every run that detects one, which is worse than the leak it is reporting. The record carries
    a path, a line number, and the name of the pattern that fired.
    """
    reportable = path if _REPORTABLE_PATH.match(path) else "<unprintable-path>"
    return f"{reportable}:added-{line_number}:{name}"


def security_violations(added_lines: dict[str, list[str]]) -> list[str]:
    violations: list[str] = []
    for path, lines in sorted(added_lines.items()):
        for line_number, line in enumerate(lines, start=1):
            for name, pattern in HIGH_CONFIDENCE_SECRET_PATTERNS:
                if pattern.search(line):
                    violations.append(_violation(path, line_number, name))
            if path.endswith(".py") and not path.startswith("tests/"):
                for name, pattern in DANGEROUS_PYTHON_PATTERNS:
                    if pattern.search(line) and "# nosec" not in line:
                        violations.append(_violation(path, line_number, name))
    return violations


def _write_report(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_security(base: str, head: str) -> int:
    violations = security_violations(_added_lines_by_file(base, head))
    report = {"count": len(violations), "violations": violations, "baseline": 0, "gate_pass": not violations}
    _write_report(SECURITY_REPORT_PATH, report)
    # codeql[py/clear-text-logging-sensitive-data]
    # The taint is the diff this scan reads; what is printed is not. `_violation` builds each
    # record from a path, a line number, and the name of the pattern that fired, and never from
    # the matched text -- printing the match would put the credential in the log of every run
    # that detects one. CodeQL cannot tell a dict's keys from its values, so the path carries
    # the taint of the lines beside it.
    print(json.dumps(report, ensure_ascii=False))
    return int(bool(violations))


def _docs_commands() -> list[tuple[str, list[str]]]:
    scripts = (
        "check_doc_drift.py",
        "check_documentation_map.py",
        "check_evidence_ledger_commands.py",
        "check_semantic_doc_consistency.py",
        "check_data_pattern_matrix.py",
        "check_data_platform_sprint_status.py",
    )
    return [(script.removesuffix(".py"), [sys.executable, f"scripts/quality/{script}"]) for script in scripts]


def _commands(plan: PullRequestPlan, base: str, head: str) -> list[tuple[str, list[str]]]:
    commands: list[tuple[str, list[str]]] = [
        (
            "diff-security",
            [sys.executable, "scripts/quality/pr_fast_gate.py", "security", "--base", base, "--head", head],
        )
    ]
    if plan.is_docs_only:
        commands.extend(_docs_commands())
        return commands
    commands.append(
        (
            "static-invariants",
            [sys.executable, "scripts/quality/run_static_checks.py", "--profile", "pr", "--jobs", "3"],
        )
    )
    if plan.selected_tests:
        commands.append(
            (
                "direct-tests",
                [sys.executable, "-m", "pytest", *plan.selected_tests, "-q", "-n", "2", "-p", "no:tach"],
            )
        )
    if plan.has_frontend:
        commands.extend(
            (
                ("sdk-typecheck", ["pnpm", "--filter", "@foundry-lite/sdk", "typecheck"]),
                ("foundry-typecheck", ["pnpm", "--filter", "@foundry-lite/foundry", "typecheck"]),
            )
        )
    if plan.has_sdk_contract:
        commands.append(
            ("sdk-request-contract", ["node", "--experimental-strip-types", "tests/sdk/request_contract.mjs"])
        )
    return commands


def _run_command(name: str, command: list[str], timeout_seconds: float) -> CommandResult:
    started = time.perf_counter()
    env = dict(os.environ)
    env.setdefault("PYTHONPATH", ".:libs:apps/cli:apps/api:apps/worker")
    env.setdefault("FOUNDRY_LITE_SKIP_POSTGRES_CONTRACTS", "1")
    try:
        completed = subprocess.run(  # nosec B603 - commands are built from fixed gate inventory
            command,
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        output = (completed.stdout + completed.stderr).strip()
        return CommandResult(name, completed.returncode, time.perf_counter() - started, output)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        output = f"timed out after {timeout_seconds:.0f}s\n{stdout}\n{stderr}".strip()
        return CommandResult(name, 124, time.perf_counter() - started, output)


def run_gate(base: str, head: str, budget_seconds: float) -> int:
    """Run independent PR checks in parallel and persist one bounded report."""
    started = time.perf_counter()
    plan = build_plan(changed_files(base, head))
    commands = _commands(plan, base, head)
    command_timeout = max(30.0, budget_seconds - 5.0)
    results = _execute_commands(commands, command_timeout)
    duration = time.perf_counter() - started
    violations = _gate_violations(plan, results, duration, budget_seconds)
    _write_report(REPORT_PATH, _gate_payload(plan, results, violations, duration, budget_seconds))
    _print_command_failures(results)
    _print_dropped_linked_tests(plan)
    print(f"PR fast gate: {len(results)} checks in {duration:.1f}s; violations={len(violations)}")
    return int(bool(violations))


def _print_dropped_linked_tests(plan: PullRequestPlan) -> None:
    """Name the source-linked tests this run did not execute.

    The lane is budgeted, so a large change cannot run every linked test. Saying so is the
    difference between a bounded run and a run that looks complete but is not: whoever reads
    this output should know the main-push coverage lane is the backstop for these files.
    """
    if not plan.dropped_linked_tests:
        return
    print(
        f"PR fast gate: {len(plan.dropped_linked_tests)} source-linked test files exceeded the "
        f"selection budget and did not run here; the main coverage lane covers them:"
    )
    for path in plan.dropped_linked_tests:
        print(f"  - {path}")


def _execute_commands(commands: list[tuple[str, list[str]]], timeout_seconds: float) -> list[CommandResult]:
    results: list[CommandResult] = []
    with ThreadPoolExecutor(max_workers=min(5, len(commands))) as pool:
        futures = {pool.submit(_run_command, name, command, timeout_seconds): name for name, command in commands}
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(f"[{'PASS' if result.returncode == 0 else 'FAIL'}] {result.name} ({result.duration_seconds:.1f}s)")
    return results


def _gate_violations(
    plan: PullRequestPlan,
    results: list[CommandResult],
    duration: float,
    budget_seconds: float,
) -> list[str]:
    violations = [result.name for result in results if result.returncode != 0]
    violations.extend(f"missing-direct-test:{path}" for path in plan.source_files_without_tests)
    if duration > budget_seconds:
        violations.append(f"wall-time-budget:{duration:.1f}s>{budget_seconds:.1f}s")
    return violations


def _gate_payload(
    plan: PullRequestPlan,
    results: list[CommandResult],
    violations: list[str],
    duration: float,
    budget_seconds: float,
) -> dict[str, object]:
    return {
        "count": len(violations),
        "violations": violations,
        "baseline": 0,
        "gate_pass": not violations,
        "budget_seconds": budget_seconds,
        "duration_seconds": round(duration, 3),
        "plan": asdict(plan),
        "checks": [
            asdict(result) | {"output": result.output[-4000:]} for result in sorted(results, key=lambda item: item.name)
        ],
    }


def _print_command_failures(results: list[CommandResult]) -> None:
    for result in results:
        if result.returncode != 0:
            print(f"\n===== FAIL {result.name} =====\n{result.output}", file=sys.stderr)


def _default_ref(name: str, fallback: str) -> str:
    return os.environ.get(name) or fallback


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("plan", "security", "run"):
        child = subparsers.add_parser(command)
        child.add_argument("--base", default=_default_ref("FOUNDRY_LITE_PR_BASE_SHA", "origin/main"))
        child.add_argument("--head", default=_default_ref("FOUNDRY_LITE_PR_HEAD_SHA", "HEAD"))
    plan_parser = subparsers.choices["plan"]
    plan_parser.add_argument("--github-output", type=Path)
    run_parser = subparsers.choices["run"]
    run_parser.add_argument("--budget-seconds", type=float, default=DEFAULT_BUDGET_SECONDS)
    args = parser.parse_args(argv)

    if args.command == "security":
        return run_security(args.base, args.head)
    if args.command == "run":
        return run_gate(args.base, args.head, args.budget_seconds)
    plan = build_plan(changed_files(args.base, args.head))
    if args.github_output:
        _github_output(plan, args.github_output)
    print(json.dumps(asdict(plan), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
