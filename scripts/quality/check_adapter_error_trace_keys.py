"""Enforces guideline §4.3: adapter failures must preserve trace keys.

Adapter errors are only useful operationally when the failed run records keep
the request, tenant, actor, run, correlation, and adapter context. This gate
forces an adapter failure in an isolated local core and inspects the persisted
run error payload.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from dataclasses import replace as dataclass_replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for module_root in (ROOT / "libs", ROOT / "apps" / "cli", ROOT / "apps" / "api", ROOT / "apps" / "worker"):
    sys.path.insert(0, str(module_root))

from foundry_lite.application.core import FoundryLiteCore  # noqa: E402
from foundry_lite.domain.context import DEMO_ADMIN_ROLES, RequestContext  # noqa: E402
from foundry_lite.domain.errors import ValidationFailed  # noqa: E402
from foundry_lite.infrastructure.adapters import DuckDBComputeAdapter  # noqa: E402
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies  # noqa: E402

DEFAULT_STORAGE_ROOT = ROOT / ".foundry-lite-adapter-error-gate"
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "adapter_error_trace_keys.json"
REQUIRED_TRACE_KEYS = {
    "tenant_id",
    "actor_user_id",
    "request_id",
    "run_id",
    "correlation_id",
    "adapter",
}


class ExplodingCsvComputeAdapter(DuckDBComputeAdapter):
    """Compute adapter that fails at the CSV boundary for the dynamic gate."""

    profile_name = "exploding-csv"

    def csv_to_parquet(self, source_path: Path, target_path: Path) -> None:
        raise RuntimeError("adapter exploded during csv_to_parquet")


@dataclass(frozen=True)
class AdapterTraceFinding:
    code: str
    message: str
    run_id: str | None = None


def _failed_sync_run(runs: list[Mapping[str, object]]) -> Mapping[str, object] | None:
    for run in runs:
        if run.get("status") == "FAILED":
            return run
    return None


def collect_findings(run: Mapping[str, object] | None, expected_trace: dict[str, str]) -> list[AdapterTraceFinding]:
    if run is None:
        return [AdapterTraceFinding(code="missing_failed_run", message="adapter failure did not persist FAILED run")]
    error = run.get("error")
    run_id = str(run.get("id"))
    if not isinstance(error, dict):
        return [
            AdapterTraceFinding(code="missing_error_payload", message="FAILED run has no error payload", run_id=run_id)
        ]
    trace = error.get("trace")
    if not isinstance(trace, dict):
        return [
            AdapterTraceFinding(code="missing_error_trace", message="error payload has no trace object", run_id=run_id)
        ]

    findings: list[AdapterTraceFinding] = []
    missing = sorted(REQUIRED_TRACE_KEYS - set(trace))
    if missing:
        findings.append(
            AdapterTraceFinding(
                code="missing_trace_keys",
                message=f"error trace missing keys: {', '.join(missing)}",
                run_id=run_id,
            )
        )
    for key, expected in expected_trace.items():
        actual = trace.get(key)
        if actual != expected:
            findings.append(
                AdapterTraceFinding(
                    code="trace_key_mismatch",
                    message=f"error trace {key}={actual!r}, expected {expected!r}",
                    run_id=run_id,
                )
            )
    if trace.get("run_id") != run.get("id") or trace.get("correlation_id") != run.get("id"):
        findings.append(
            AdapterTraceFinding(
                code="run_correlation_mismatch",
                message="error trace run_id/correlation_id must match the failed run id",
                run_id=run_id,
            )
        )
    return findings


def _run_probe(storage_root: Path) -> tuple[Mapping[str, object] | None, dict[str, str]]:
    if storage_root.exists():
        shutil.rmtree(storage_root)
    dependencies = create_local_core_dependencies(storage_root=storage_root)
    dependencies = dataclass_replace(dependencies, compute_adapter=ExplodingCsvComputeAdapter())
    core = FoundryLiteCore(dependencies=dependencies)
    ctx = RequestContext(
        tenant_id="tenant-adapter-trace",
        actor_user_id="actor-adapter-trace",
        request_id="request-adapter-trace",
        roles=DEMO_ADMIN_ROLES,
    )
    core.ensure_dataset("raw.adapter_trace", ctx=ctx, primary_key=["id"])
    csv_path = storage_root / "adapter-trace.csv"
    csv_path.write_text("id,value\nA,1\n", encoding="utf-8")
    try:
        core.upload_csv("raw.adapter_trace", csv_path, ctx=ctx)
    except ValidationFailed:
        pass
    else:
        raise AssertionError("exploding adapter unexpectedly let upload_csv succeed")
    expected_trace = {
        "tenant_id": ctx.tenant_id,
        "actor_user_id": ctx.actor_user_id,
        "request_id": ctx.request_id,
        "adapter": "compute_adapter.csv_to_parquet",
    }
    return _failed_sync_run(core.list_runs(ctx=ctx)["syncRuns"]), expected_trace


def run_gate(storage_root: Path) -> list[AdapterTraceFinding]:
    run, expected_trace = _run_probe(storage_root)
    return collect_findings(run, expected_trace)


def write_report(output: Path, findings: list[AdapterTraceFinding]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "gate": "adapter error trace keys",
        "count": len(findings),
        "baseline": 0,
        "gate_pass": not findings,
        "required_trace_keys": sorted(REQUIRED_TRACE_KEYS),
        "violations": [asdict(finding) for finding in findings],
    }
    output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _print_failure(findings: list[AdapterTraceFinding], output: Path) -> None:
    print("Adapter error trace-key gate failed:")
    for finding in findings:
        location = f" run={finding.run_id}" if finding.run_id else ""
        print(f"- {finding.code}{location}: {finding.message}")
    print(f"Report: {output.relative_to(ROOT)}")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify adapter failures preserve trace keys.")
    parser.add_argument("--storage-root", type=Path, default=DEFAULT_STORAGE_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    findings = run_gate(args.storage_root)
    write_report(args.output, findings)
    if findings:
        _print_failure(findings, args.output)
        return 1
    print("Adapter error trace-key gate passed: FAILED adapter runs preserve trace keys.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
