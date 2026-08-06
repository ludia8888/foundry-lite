"""Parallel driver for the static quality lane.

Every static check is read-only, so they run concurrently instead of serially.
Two structural speedups over the old one-command-per-line shell lane:

1. Python check scripts are spawned with this interpreter directly, removing
   ~45 `uv run` bootstraps per gate run.
2. All checks always run to completion and failures are reported together,
   so one broken gate no longer hides the rest (the old lane was fail-fast).

The full check inventory is identical to the previous serial lane; only the
execution model changed. The ``pr`` profile is an explicit latency profile:
it keeps typing, architecture, security, schema, and documentation invariants
but moves network CVE lookup, full product-test bundles, and broad complexity
rehearsals to the post-merge/release lanes.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess  # nosec B404 — this driver exists to spawn the gate tools
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable
SEMGREP_TOOL = ["uv", "tool", "run", "--from", "semgrep==1.169.0", "semgrep"]
PIPELINE_QUALITY_GATE_INVENTORY: tuple[tuple[str, str], ...] = (
    ("quality:pipeline-graph-v2", "static"),
    ("quality:pipeline-artifact-execution", "static"),
    ("quality:pipeline-preview", "static"),
    ("quality:pipeline-multimodal", "static"),
    ("quality:pipeline-scheduler", "static"),
    ("quality:pipeline-async-dag", "static"),
    ("quality:pipeline-collaboration", "static"),
    ("quality:pipeline-python-isolation", "static"),
    ("quality:pipeline-trained-model-sidecar", "static"),
    ("quality:pipeline-builder-performance", "static"),
    ("quality:pipeline-builder-e2e", "e2e"),
)

# Ordered heaviest-first so long-running tools overlap the cheap AST checks.
HEAVY_CHECKS: tuple[tuple[str, list[str]], ...] = (
    ("pyright", ["uv", "run", "--no-sync", "pyright"]),
    ("mypy", ["uv", "run", "--no-sync", "mypy", "libs", "apps/api", "apps/cli", "apps/worker", "scripts"]),
    (
        "semgrep",
        [
            *SEMGREP_TOOL,
            "--config",
            "scripts/quality/semgrep-rules/foundry-lite.yml",
            "--error",
            "--metrics",
            "off",
            "--quiet",
            "--json",
            "--output",
            "artifacts/quality/semgrep.json",
            ".",
        ],
    ),
    ("bandit", ["uv", "run", "--no-sync", "bandit", "-c", "pyproject.toml", "-r", "libs", "apps", "scripts", "-q"]),
    # PyJWT ignores mirror the previous serial lane; re-evaluate when PyJWT or
    # semgrep changes (JwtOidcAuthProvider coverage lives in quality:auth-secrets).
    (
        "pip-audit",
        [
            "uv",
            "run",
            "--no-sync",
            "pip-audit",
            "--progress-spinner",
            "off",
            "--ignore-vuln",
            "PYSEC-2026-175",
            "--ignore-vuln",
            "PYSEC-2026-177",
            "--ignore-vuln",
            "PYSEC-2026-178",
            "--ignore-vuln",
            "PYSEC-2026-179",
        ],
    ),
    (
        "migration-runner-tests",
        ["uv", "run", "--no-sync", "pytest", "tests/unit/test_migration_runner.py", "-q", "-ra"],
    ),
    ("pipeline-graph-v2", ["pnpm", "--silent", "quality:pipeline-graph-v2"]),
    ("pipeline-artifact-execution", ["pnpm", "--silent", "quality:pipeline-artifact-execution"]),
    ("pipeline-preview", ["pnpm", "--silent", "quality:pipeline-preview"]),
    ("pipeline-multimodal", ["pnpm", "--silent", "quality:pipeline-multimodal"]),
    ("pipeline-scheduler", ["pnpm", "--silent", "quality:pipeline-scheduler"]),
    ("pipeline-async-dag", ["pnpm", "--silent", "quality:pipeline-async-dag"]),
    ("pipeline-collaboration", ["pnpm", "--silent", "quality:pipeline-collaboration"]),
    ("pipeline-python-isolation", ["pnpm", "--silent", "quality:pipeline-python-isolation"]),
    ("pipeline-trained-model-sidecar", ["pnpm", "--silent", "quality:pipeline-trained-model-sidecar"]),
    ("pipeline-builder-performance", ["pnpm", "--silent", "quality:pipeline-builder-performance"]),
    ("sdk-ts-drift", [PYTHON, "scripts/generate_sdk_ts.py", "--check"]),
    ("frontend-foundation", ["pnpm", "--silent", "quality:frontend-foundation"]),
    ("ast-grep", ["node", "scripts/quality/run_ast_grep.cjs", "scan", "-c", "sgconfig.yml"]),
    ("ruff-lint", ["uv", "run", "--no-sync", "ruff", "check", "."]),
    ("ruff-format", ["uv", "run", "--no-sync", "ruff", "format", "--check", "."]),
    ("import-linter", ["uv", "run", "--no-sync", "lint-imports", "--config", ".importlinter"]),
    ("tach", ["uv", "run", "--no-sync", "tach", "check", "--dependencies"]),
    (
        "xenon",
        [
            "uv",
            "run",
            "--no-sync",
            "xenon",
            "--max-absolute",
            "B",
            "--max-modules",
            "B",
            "--max-average",
            "A",
            "libs",
            "apps",
            "scripts",
        ],
    ),
    (
        "vulture",
        [
            "uv",
            "run",
            "--no-sync",
            "vulture",
            "libs/foundry_lite",
            "scripts/vulture_allowlist.py",
            "--min-confidence",
            "80",
        ],
    ),
    ("interrogate", ["uv", "run", "--no-sync", "interrogate", "libs/foundry_lite", "--fail-under", "25", "--quiet"]),
    (
        "radon",
        [
            "uv",
            "run",
            "--no-sync",
            "radon",
            "cc",
            "libs",
            "apps",
            "scripts",
            "-s",
            "-a",
            "-j",
            "-O",
            "artifacts/quality/radon_cc.json",
        ],
    ),
)

# The PR profile must fit inside the budgeted PR workflow window, including a
# cold dependency restore. Full Semgrep/pip-audit, product test bundles, and
# broad documentation coverage remain mandatory on main/release/nightly.
# Pull requests get Bandit, both type checkers, Ruff, dependency boundaries,
# schema/SDK drift, and every focused Python invariant.
#
# xenon and gitleaks were main-only until both let a regression through on the same push:
# extending the action-condition operator set pushed `_compare` to complexity rank C, and a
# virtual-table test's vault pointer read as a generic API key. Neither was visible on the PR,
# so both landed and turned main red. They earn their place here on cost as much as on value --
# xenon is 6.8s and needs nothing installed, and a secret caught after the merge is already in
# history permanently, where reverting the commit does not remove it.
PR_HEAVY_CHECK_NAMES = frozenset(
    {
        "pyright",
        "mypy",
        "bandit",
        "sdk-ts-drift",
        "ruff-lint",
        "ruff-format",
        "import-linter",
        "tach",
        "xenon",
    }
)

# name -> (script, extra args); all run as `<this python> scripts/quality/<script>`.
PYTHON_CHECKS: tuple[tuple[str, list[str]], ...] = (
    ("dependency-graph", ["scripts/quality/check_dependency_graph.py"]),
    ("infra-import-boundary", ["scripts/quality/check_infra_import_boundary.py", "--max-application-imports", "0"]),
    ("service-dependencies", ["scripts/quality/check_service_dependencies.py"]),
    ("core-service-wiring", ["scripts/quality/check_core_service_wiring.py"]),
    ("service-call-graph", ["scripts/quality/check_service_call_graph.py", "--max-depth", "7", "--max-fan-out", "10"]),
    ("application-module-size", ["scripts/quality/check_application_module_size.py", "--max-lines", "500"]),
    ("function-length", ["scripts/quality/check_function_length.py"]),
    ("boolean-naming", ["scripts/quality/check_boolean_naming.py"]),
    ("dict-any-budget", ["scripts/quality/check_dict_any_budget.py"]),
    ("application-any-budget", ["scripts/quality/check_application_any_budget.py"]),
    ("router-layer-purity", ["scripts/quality/check_router_layer_purity.py"]),
    ("query-side-effects", ["scripts/quality/check_query_side_effects.py"]),
    ("repository-no-business", ["scripts/quality/check_repository_no_business.py"]),
    ("tenant-write-guard", ["scripts/quality/check_tenant_write_guard.py"]),
    ("status-transition-cas", ["scripts/quality/check_status_transition_cas.py"]),
    ("contract-test-per-port", ["scripts/quality/check_contract_test_per_port.py"]),
    ("strategy-specification-tests", ["scripts/quality/check_strategy_specification_tests.py"]),
    ("integration-scenario-markers", ["scripts/quality/check_integration_scenario_markers.py"]),
    ("regression-test-per-bugfix", ["scripts/quality/check_regression_test_per_bugfix.py"]),
    ("pr-root-cause-section", ["scripts/quality/check_pr_root_cause_section.py"]),
    ("doc-drift", ["scripts/quality/check_doc_drift.py"]),
    ("evidence-ledger-commands", ["scripts/quality/check_evidence_ledger_commands.py"]),
    ("documentation-map", ["scripts/quality/check_documentation_map.py"]),
    ("checklist-evidence", ["scripts/quality/check_checklist_evidence.py"]),
    ("infra-tricky-matrix", ["scripts/quality/check_infra_tricky_matrix.py"]),
    ("semantic-doc-consistency", ["scripts/quality/check_semantic_doc_consistency.py"]),
    ("data-pattern-matrix", ["scripts/quality/check_data_pattern_matrix.py"]),
    ("data-platform-sprint-status", ["scripts/quality/check_data_platform_sprint_status.py"]),
    ("pipeline-parity-matrix", ["scripts/quality/check_pipeline_parity_matrix.py"]),
    ("pipeline-quality-gate-inventory", ["scripts/quality/check_pipeline_quality_gate_inventory.py"]),
    ("frontend-backend-surface", ["scripts/quality/check_frontend_backend_surface.py"]),
    ("schema-revision-guard", ["scripts/quality/check_schema_revision_guard.py"]),
    ("schema-migrations", ["scripts/quality/check_schema_migrations.py"]),
    ("audit-on-mutation", ["scripts/quality/check_audit_on_mutation.py"]),
    ("transaction-outbox-pair", ["scripts/quality/check_transaction_outbox_pair.py"]),
    ("idempotency-on-action", ["scripts/quality/check_idempotency_on_action.py"]),
    ("error-response-request-id", ["scripts/quality/check_error_response_has_request_id.py"]),
    ("log-trace-keys", ["scripts/quality/check_log_has_trace_keys.py"]),
    ("metrics-exposed", ["scripts/quality/check_metrics_exposed.py"]),
    ("adapter-failure-taxonomy", ["scripts/quality/check_adapter_failure_taxonomy.py"]),
    ("infra-ratchet", ["scripts/quality/check_infra_ratchet.py"]),
    ("no-test-bypasses", ["scripts/quality/check_no_test_bypasses.py"]),
    ("no-test-sleep", ["scripts/quality/check_no_test_sleep.py"]),
    ("pragma-no-cover-budget", ["scripts/quality/check_pragma_no_cover_budget.py", "--baseline", "0"]),
    ("private-test-references", ["scripts/quality/check_private_test_references.py", "--max-count", "0"]),
)


@dataclass(frozen=True)
class CheckResult:
    name: str
    returncode: int
    duration_seconds: float
    output: str


def _safe_check_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-") or "check"


def _gitleaks_check() -> tuple[str, list[str]] | None:
    if shutil.which("gitleaks"):
        return (
            "gitleaks",
            [
                "gitleaks",
                "dir",
                "--no-banner",
                "--redact",
                "--config",
                ".gitleaks.toml",
                "--report-path",
                "artifacts/quality/gitleaks.json",
                "--report-format",
                "json",
            ],
        )
    if os.environ.get("CI") == "true" or os.environ.get("FOUNDRY_LITE_STRICT_EXTERNAL_TOOLS") == "1":
        print("ERROR: gitleaks not on PATH; CI/release evidence cannot skip the P9 secret scan.", file=sys.stderr)
        raise SystemExit(1)
    print("WARN: gitleaks not on PATH; install with 'brew install gitleaks' (P9 gate skipped locally only).")
    return None


def _all_checks(profile: str = "full") -> list[tuple[str, list[str]]]:
    if profile == "pr":
        checks = [check for check in HEAVY_CHECKS if check[0] in PR_HEAVY_CHECK_NAMES]
    else:
        checks = list(HEAVY_CHECKS)
    # Both profiles: the scan is worth far more before the merge than after it.
    gitleaks = _gitleaks_check()
    if gitleaks is not None:
        checks.insert(0, gitleaks)
    checks.extend((name, [PYTHON, *script]) for name, script in PYTHON_CHECKS)
    return checks


def _env_for_check(name: str, env: dict[str, str]) -> tuple[dict[str, str], Path | None]:
    check_env = dict(env)
    if check_env.get("FOUNDRY_LITE_HOME"):
        return check_env, None

    # Several static gates collect/import tests. Importing the API composition
    # root used to bootstrap a local FoundryLite runtime, so parallel static
    # checks keep isolated homes for any remaining import-time side effects.
    home = Path(tempfile.mkdtemp(prefix=f"foundry-lite-static-{_safe_check_name(name)}-"))
    check_env["FOUNDRY_LITE_HOME"] = str(home)
    return check_env, home


def _run_check(name: str, command: list[str], env: dict[str, str]) -> CheckResult:
    started = time.perf_counter()
    check_env, ephemeral_home = _env_for_check(name, env)
    try:
        completed = subprocess.run(  # nosec B603 — fixed gate commands, shell=False
            command,
            cwd=ROOT,
            env=check_env,
            capture_output=True,
            text=True,
        )
        output = (completed.stdout + completed.stderr).strip()
        return CheckResult(name, completed.returncode, time.perf_counter() - started, output)
    finally:
        if ephemeral_home is not None:
            shutil.rmtree(ephemeral_home, ignore_errors=True)


def _base_env() -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("PYTHONPATH", ".:libs:apps/cli:apps/api:apps/worker")
    if not env.get("SSL_CERT_FILE"):
        import certifi

        env["SSL_CERT_FILE"] = certifi.where()
    return env


def _check_env() -> dict[str, str]:
    return _base_env()


def _run_checks(checks: list[tuple[str, list[str]]], env: dict[str, str], jobs: int) -> list[CheckResult]:
    results: list[CheckResult] = []
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(_run_check, name, command, env): name for name, command in checks}
        for future in as_completed(futures):
            result = future.result()
            status = "PASS" if result.returncode == 0 else "FAIL"
            print(f"[{status}] {result.name} ({result.duration_seconds:.1f}s)", flush=True)
            results.append(result)
    return results


def _report(results: list[CheckResult], jobs: int, total_seconds: float) -> int:
    failures = [result for result in results if result.returncode != 0]
    slowest = sorted(results, key=lambda r: r.duration_seconds, reverse=True)[:5]
    print(f"\nStatic lane: {len(results)} checks in {total_seconds:.1f}s (jobs={jobs})")
    print("Slowest:", ", ".join(f"{r.name}={r.duration_seconds:.1f}s" for r in slowest))
    for result in failures:
        print(f"\n===== FAIL {result.name} =====\n{result.output}", file=sys.stderr)
    if failures:
        print(f"\n{len(failures)} static checks failed: {', '.join(r.name for r in failures)}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=min(10, os.cpu_count() or 4))
    parser.add_argument("--profile", choices=("full", "pr"), default="full")
    args = parser.parse_args(argv)

    (ROOT / "artifacts" / "quality").mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    results = _run_checks(_all_checks(args.profile), _check_env(), args.jobs)
    return _report(results, args.jobs, time.perf_counter() - started)


if __name__ == "__main__":
    sys.exit(main())
