"""Orchestrate the immutable 24-hour Mac mini enterprise fault campaign."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess  # nosec B404 - fixed QA script argv only; never accepts shell commands.
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import BinaryIO

from scripts.operations.macmini_enterprise_campaign_plan import (
    CAMPAIGN_DURATION_SECONDS,
    EVENTS,
    CampaignEvent,
    build_campaign_plan,
    fault_windows,
)
from scripts.operations.macmini_qa_guard import (
    QA_ROOT,
    assert_host_boundary,
    assert_namespace,
    qa_command_environment,
    write_json_receipt,
)

_PYTHONPATH = ".:libs:apps/cli:apps/api:apps/worker"
_REMEDIATION_SOURCE_STATUSES = frozenset({"failed", "skipped"})


def run_campaign(args: argparse.Namespace) -> dict[str, object]:
    assert_host_boundary()
    assert_namespace(args.namespace)
    _validate_paths(args)
    started_at = datetime.now(UTC) + timedelta(seconds=args.start_delay_seconds)
    target = _campaign_directory(args.run_id)
    plan = build_campaign_plan(started_at)
    windows_path = target / "fault-windows.json"
    plan_path = target / "plan.json"
    write_json_receipt(plan_path, plan)
    write_json_receipt(windows_path, fault_windows(started_at))
    write_json_receipt(
        target / "campaign-process.json",
        {
            "schemaVersion": 1,
            "status": "starting",
            "runId": args.run_id,
            "campaignPid": os.getpid(),
            "plannedStartAt": started_at.isoformat(),
        },
    )
    _wait_until(started_at)
    soak = _start_soak(args, windows_path, plan_path, target)
    write_json_receipt(
        target / "soak-process.json",
        {
            "schemaVersion": 1,
            "status": "running",
            "runId": args.run_id,
            "campaignPid": os.getpid(),
            "soakPid": soak.pid,
            "startedAt": datetime.now(UTC).isoformat(),
        },
    )
    mutation_allowed = True
    event_receipts: list[dict[str, object]] = []
    journal = _open_journal(target / "journal.ndjson")
    try:
        for index, event in enumerate(EVENTS, start=1):
            scheduled_at = started_at + timedelta(seconds=event.offset_second)
            _wait_until(scheduled_at)
            if _is_mutating(event) and not mutation_allowed:
                receipt = _skipped_receipt(event, scheduled_at, "prior_recovery_failure")
            else:
                receipt = _run_event(args, event, scheduled_at)
            if _blocks_later_mutations(event, receipt):
                mutation_allowed = False
            event_receipts.append(receipt)
            _append_journal(journal, receipt)
            write_json_receipt(target / "events" / f"{index:02d}-{event.event_id}.json", receipt)
        soak_return_code = _wait_for_soak(soak, started_at)
    finally:
        journal.close()
    soak_summary = _load_soak_summary(args.run_id)
    summary = _campaign_summary(args.run_id, started_at, event_receipts, soak_return_code, soak_summary)
    write_json_receipt(target / "summary.json", summary)
    return summary


def run_remediation(args: argparse.Namespace) -> dict[str, object]:
    assert_host_boundary()
    assert_namespace(args.namespace)
    _validate_paths(args)
    started_at = datetime.now(UTC)
    events, source_journal = _remediation_events(args.rerun_failed_and_skipped_from_run_id)
    target = _campaign_directory(args.run_id)
    write_json_receipt(target / "plan.json", _remediation_plan(args, events, source_journal, started_at))
    receipts = _run_remediation_events(args, events, target)
    summary = _remediation_summary(args, receipts, source_journal, started_at)
    write_json_receipt(target / "summary.json", summary)
    return summary


def _run_remediation_events(
    args: argparse.Namespace,
    events: tuple[CampaignEvent, ...],
    target: Path,
) -> list[dict[str, object]]:
    receipts: list[dict[str, object]] = []
    mutation_allowed = True
    journal = _open_journal(target / "journal.ndjson")
    try:
        for index, event in enumerate(events, start=1):
            scheduled_at = datetime.now(UTC)
            receipt = (
                _run_event(args, event, scheduled_at)
                if mutation_allowed
                else _skipped_receipt(event, scheduled_at, "prior_recovery_failure")
            )
            mutation_allowed = mutation_allowed and not _blocks_later_mutations(event, receipt)
            receipts.append(receipt)
            _append_journal(journal, receipt)
            write_json_receipt(target / "events" / f"{index:02d}-{event.event_id}.json", receipt)
    finally:
        journal.close()
    return receipts


def _remediation_events(source_run_id: str) -> tuple[tuple[CampaignEvent, ...], bytes]:
    source = QA_ROOT / "evidence" / _validated_run_id(source_run_id) / "campaign" / "journal.ndjson"
    raw = source.read_bytes()
    if not raw or len(raw) > 1024 * 1024:
        raise ValueError("macmini_campaign_source_journal_invalid")
    selected_ids = _selected_remediation_event_ids(raw)
    events = tuple(event for event in EVENTS if event.event_id in selected_ids)
    if not events or len(events) != len(selected_ids):
        raise ValueError("macmini_campaign_remediation_events_invalid")
    return events, raw


def _selected_remediation_event_ids(raw: bytes) -> set[str]:
    selected: set[str] = set()
    try:
        rows = [json.loads(line) for line in raw.splitlines() if line]
    except json.JSONDecodeError as exc:
        raise ValueError("macmini_campaign_source_journal_invalid") from exc
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("macmini_campaign_source_journal_invalid")
        event_id = row.get("eventId")
        if row.get("status") in _REMEDIATION_SOURCE_STATUSES and isinstance(event_id, str):
            selected.add(event_id)
    return selected


def _remediation_plan(
    args: argparse.Namespace,
    events: tuple[CampaignEvent, ...],
    source_journal: bytes,
    started_at: datetime,
) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "scope": "failed-and-skipped-events-only",
        "runId": args.run_id,
        "sourceRunId": args.rerun_failed_and_skipped_from_run_id,
        "startedAt": started_at.isoformat(),
        "sourceJournalSha256": _sha256(source_journal),
        "eventIds": [event.event_id for event in events],
    }


def _remediation_summary(
    args: argparse.Namespace,
    receipts: list[dict[str, object]],
    source_journal: bytes,
    started_at: datetime,
) -> dict[str, object]:
    statuses = [str(receipt.get("status")) for receipt in receipts]
    is_passed = bool(statuses) and all(status == "passed" for status in statuses)
    return {
        "schemaVersion": 1,
        "status": "passed" if is_passed else "failed",
        "scope": "failed-and-skipped-events-only",
        "runId": args.run_id,
        "sourceRunId": args.rerun_failed_and_skipped_from_run_id,
        "startedAt": started_at.isoformat(),
        "endedAt": datetime.now(UTC).isoformat(),
        "sourceJournalSha256": _sha256(source_journal),
        "eventCounts": {value: statuses.count(value) for value in ("passed", "failed", "skipped")},
        "full24HourCampaignStatus": "notProven",
        "p0P1Clear": False,
    }


def _run_event(args: argparse.Namespace, event: CampaignEvent, scheduled_at: datetime) -> dict[str, object]:
    observed_at = datetime.now(UTC)
    if event.kind in {"blocked", "notProven"}:
        return {
            "schemaVersion": 1,
            "eventId": event.event_id,
            "phaseId": event.phase_id,
            "status": event.kind,
            "reason": event.value,
            "scheduledAt": scheduled_at.isoformat(),
            "observedAt": observed_at.isoformat(),
            "mutationPerformed": False,
        }
    if event.kind == "dr":
        execution = _run_dr(args, event)
    else:
        command, timeout = _event_command(args, event)
        execution = _execute(command, timeout)
    recovery = _recovery_probe(args) if _is_mutating(event) else None
    status = "passed" if execution["status"] == "passed" and _recovery_passed(recovery) else "failed"
    return {
        "schemaVersion": 1,
        "eventId": event.event_id,
        "phaseId": event.phase_id,
        "status": status,
        "scheduledAt": scheduled_at.isoformat(),
        "startedAt": observed_at.isoformat(),
        "endedAt": datetime.now(UTC).isoformat(),
        "execution": execution,
        "recovery": recovery,
        "mutationPerformed": _is_mutating(event),
    }


def _event_command(args: argparse.Namespace, event: CampaignEvent) -> tuple[tuple[str, ...], int]:
    prefix = (str(QA_ROOT / "bin" / "uv"), "run", "python")
    if event.kind == "fault":
        release_coordinates = (
            (
                "--current-commit",
                args.current_commit,
                "--rollback-commit",
                args.rollback_commit,
            )
            if event.value == "verified-digest-rollback"
            else ()
        )
        return (
            (
                *prefix,
                "scripts/operations/inject_macmini_fault.py",
                "--run-id",
                args.run_id,
                "--namespace",
                args.namespace,
                "--kubeconfig",
                args.kubeconfig,
                "--fault",
                event.value,
                "--duration-seconds",
                str(event.injection_seconds),
                *release_coordinates,
            ),
            max(900, event.fault_window_seconds + 300),
        )
    if event.kind == "tenant-stress":
        return (
            (
                *prefix,
                "scripts/operations/run_macmini_tenant_stress.py",
                "--run-id",
                args.run_id,
                "--duration-seconds",
                str(event.injection_seconds),
            ),
            event.injection_seconds + 900,
        )
    if event.kind == "postgres-rls":
        return (
            (
                *prefix,
                "scripts/operations/verify_macmini_postgres_object_store.py",
                "--run-id",
                args.run_id,
                "--namespace",
                args.namespace,
                "--tenant-id",
                "tenant-demo",
                "--kubeconfig",
                args.kubeconfig,
            ),
            300,
        )
    if event.kind == "security-time":
        return (
            (
                *prefix,
                "scripts/operations/verify_macmini_security_time.py",
                "--run-id",
                args.run_id,
                "--namespace",
                args.namespace,
                "--kubeconfig",
                args.kubeconfig,
            ),
            600,
        )
    if event.kind == "mcp-quota":
        return (
            (
                *prefix,
                "scripts/operations/verify_macmini_mcp_tenant_quota.py",
                "--run-id",
                args.run_id,
                "--namespace",
                args.namespace,
                "--kubeconfig",
                args.kubeconfig,
            ),
            600,
        )
    raise RuntimeError("macmini_campaign_event_kind_invalid")


def _run_dr(args: argparse.Namespace, event: CampaignEvent) -> dict[str, object]:
    prefix = (str(QA_ROOT / "bin" / "uv"), "run", "python")
    backup = _execute(
        (
            *prefix,
            "scripts/operations/backup_macmini_qa.py",
            "--run-id",
            args.run_id,
            "--namespace",
            args.namespace,
            "--kubeconfig",
            args.kubeconfig,
            "--bearer-token-file",
            args.operator_token_file,
            "--age-recipient-file",
            args.age_recipient_file,
        ),
        3600,
    )
    if backup["status"] != "passed":
        return {"status": "failed", "backup": backup, "restore": {"status": "skipped"}}
    restore = _execute(
        (
            *prefix,
            "scripts/operations/restore_macmini_qa.py",
            "--run-id",
            args.run_id,
            "--source-namespace",
            args.namespace,
            "--recovery-namespace",
            "foundry-qa-recovery",
            "--kubeconfig",
            args.kubeconfig,
            "--age-identity-file",
            args.age_identity_file,
            "--bearer-token-file",
            args.operator_token_file,
        ),
        max(3600, event.fault_window_seconds),
    )
    return {
        "status": "passed" if restore["status"] == "passed" else "failed",
        "backup": backup,
        "restore": restore,
    }


def _recovery_probe(args: argparse.Namespace) -> dict[str, object]:
    business = _execute(_business_probe_command(args), 180)
    operations = _execute(_operations_probe_command(args), 120)
    return {
        "status": ("passed" if business["status"] == "passed" and operations["status"] == "passed" else "failed"),
        "business": business,
        "operations": operations,
    }


def _execute(command: tuple[str, ...], timeout_seconds: int) -> dict[str, object]:
    started = time.monotonic()
    try:
        result = subprocess.run(  # nosec B603 - commands are constructed only from fixed QA scripts and validated paths.
            command,
            cwd=QA_ROOT / "repo",
            env=_environment(),
            check=False,
            capture_output=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "status": "failed",
            "reason": type(exc).__name__,
            "durationMs": int((time.monotonic() - started) * 1000),
        }
    payload = _last_json(result.stdout)
    return {
        "status": ("passed" if result.returncode == 0 and payload.get("status") == "passed" else "failed"),
        "returnCode": result.returncode,
        "durationMs": int((time.monotonic() - started) * 1000),
        "stdoutSha256": _sha256(result.stdout),
        "stderrSha256": _sha256(result.stderr),
        "receipt": payload,
        "rawOutputStored": False,
    }


def _start_soak(args: argparse.Namespace, windows_path: Path, plan_path: Path, target: Path) -> subprocess.Popen[bytes]:
    command = (
        str(QA_ROOT / "bin" / "uv"),
        "run",
        "python",
        "scripts/operations/run_macmini_soak.py",
        "--run-id",
        args.run_id,
        "--namespace",
        args.namespace,
        "--kubeconfig",
        args.kubeconfig,
        "--duration-seconds",
        str(CAMPAIGN_DURATION_SECONDS),
        "--interval-seconds",
        "5",
        "--probe",
        "healthz=http://127.0.0.1:30443/healthz",
        "--probe",
        "readyz=http://127.0.0.1:30443/readyz",
        "--business-probe-every",
        "1",
        "--business-probe-command-json",
        json.dumps(_business_probe_command(args)),
        "--operations-probe-every",
        "12",
        "--operations-probe-command-json",
        json.dumps(_operations_probe_command(args)),
        "--require-operations-probe",
        "--fault-windows-file",
        str(windows_path),
        "--phase-windows-file",
        str(plan_path),
    )
    stdout = (target / "soak.stdout").open("xb")
    stderr = (target / "soak.stderr").open("xb")
    os.chmod(stdout.name, 0o600)
    os.chmod(stderr.name, 0o600)
    try:
        return subprocess.Popen(  # nosec B603 - fixed soak script argv and no shell.
            command,
            cwd=QA_ROOT / "repo",
            env=_environment(),
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
    finally:
        stdout.close()
        stderr.close()


def _business_probe_command(args: argparse.Namespace) -> tuple[str, ...]:
    return (
        str(QA_ROOT / "bin" / "uv"),
        "run",
        "python",
        "scripts/operations/run_macmini_business_probe.py",
        "probe",
        "--config",
        args.business_probe_config,
    )


def _operations_probe_command(args: argparse.Namespace) -> tuple[str, ...]:
    return (
        str(QA_ROOT / "bin" / "uv"),
        "run",
        "python",
        "scripts/operations/run_macmini_operational_probe.py",
        "--namespace",
        args.namespace,
        "--kubeconfig",
        args.kubeconfig,
    )


def _wait_for_soak(process: subprocess.Popen[bytes], started_at: datetime) -> int:
    remaining = (started_at + timedelta(seconds=CAMPAIGN_DURATION_SECONDS + 900) - datetime.now(UTC)).total_seconds()
    try:
        return process.wait(timeout=max(1, remaining))
    except subprocess.TimeoutExpired:
        return 124


def _campaign_summary(
    run_id: str,
    started_at: datetime,
    receipts: list[dict[str, object]],
    soak_return_code: int,
    soak_summary: dict[str, object] | None = None,
) -> dict[str, object]:
    statuses = [str(receipt.get("status")) for receipt in receipts]
    status = _campaign_status(statuses, soak_return_code, soak_summary)
    return {
        "schemaVersion": 1,
        "status": status,
        "runId": run_id,
        "startedAt": started_at.isoformat(),
        "endedAt": datetime.now(UTC).isoformat(),
        "soakReturnCode": soak_return_code,
        "soakSummarySha256": _sha256(json.dumps(soak_summary or {}, sort_keys=True).encode()),
        "baselineReturn": (soak_summary or {}).get("baselineReturn"),
        "phaseMetrics": (soak_summary or {}).get("phaseMetrics"),
        "eventCounts": {
            value: statuses.count(value) for value in ("passed", "failed", "blocked", "notProven", "skipped")
        },
        "p0P1Clear": status == "passed",
        "multiNodeSla": "notProven",
        "multiAzSla": "notProven",
    }


def _campaign_status(
    statuses: list[str],
    soak_return_code: int,
    soak_summary: dict[str, object] | None,
) -> str:
    if (
        soak_return_code != 0
        or "failed" in statuses
        or soak_summary is not None
        and soak_summary.get("status") != "passed"
    ):
        return "failed"
    if "blocked" in statuses:
        return "blocked"
    if "notProven" in statuses:
        return "notProven"
    return "passed"


def _load_soak_summary(run_id: str) -> dict[str, object]:
    path = QA_ROOT / "evidence" / run_id / "soak" / "summary.json"
    try:
        raw = path.read_bytes()
        payload = json.loads(raw) if 0 < len(raw) <= 1024 * 1024 else None
    except (OSError, json.JSONDecodeError):
        payload = None
    return payload if isinstance(payload, dict) else {"status": "failed", "reason": "soak_summary_missing_or_invalid"}


def _skipped_receipt(event: CampaignEvent, scheduled_at: datetime, reason: str) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "eventId": event.event_id,
        "phaseId": event.phase_id,
        "status": "skipped",
        "reason": reason,
        "scheduledAt": scheduled_at.isoformat(),
        "observedAt": datetime.now(UTC).isoformat(),
        "mutationPerformed": False,
    }


def _recovery_passed(value: object) -> bool:
    return value is None or isinstance(value, dict) and value.get("status") == "passed"


def _blocks_later_mutations(event: CampaignEvent, receipt: dict[str, object]) -> bool:
    return _is_mutating(event) and not _recovery_passed(receipt.get("recovery"))


def _is_mutating(event: CampaignEvent) -> bool:
    return event.kind in {"fault", "dr"}


def _wait_until(target: datetime) -> None:
    while (remaining := (target - datetime.now(UTC)).total_seconds()) > 0:
        time.sleep(min(30, remaining))


def _last_json(raw: bytes) -> dict[str, object]:
    for line in reversed(raw.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _campaign_directory(run_id: str) -> Path:
    target = QA_ROOT / "evidence" / _validated_run_id(run_id) / "campaign"
    (target / "events").mkdir(mode=0o700, parents=True, exist_ok=False)
    return target


def _validated_run_id(run_id: str) -> str:
    if not run_id or not all(value.isalnum() or value in "-_" for value in run_id):
        raise ValueError("macmini_campaign_run_id_invalid")
    return run_id


def _open_journal(path: Path) -> BinaryIO:
    stream = path.open("xb")
    os.chmod(path, 0o600)
    return stream


def _append_journal(stream: BinaryIO, payload: object) -> None:
    stream.write(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode() + b"\n")
    stream.flush()
    os.fsync(stream.fileno())


def _environment() -> dict[str, str]:
    environment = qa_command_environment()
    environment["PYTHONPATH"] = _PYTHONPATH
    return environment


def _validate_paths(args: argparse.Namespace) -> None:
    if not 10 <= args.start_delay_seconds <= 600:
        raise ValueError("macmini_campaign_start_delay_invalid")
    _validate_private_paths(
        (
            args.kubeconfig,
            args.business_probe_config,
            args.operator_token_file,
            args.age_recipient_file,
            args.age_identity_file,
        )
    )
    _validate_release_coordinates(args.current_commit, args.rollback_commit)


def _validate_private_paths(raw_paths: tuple[str, ...]) -> None:
    for raw in raw_paths:
        path = Path(raw)
        resolved = path.resolve(strict=True)
        if (
            path.is_symlink()
            or QA_ROOT not in resolved.parents
            or not resolved.is_file()
            or resolved.stat().st_mode & 0o077
        ):
            raise ValueError("macmini_campaign_private_path_invalid")


def _validate_release_coordinates(current_commit: str, rollback_commit: str) -> None:
    if (
        len(current_commit) != 40
        or len(rollback_commit) != 40
        or any(value not in "0123456789abcdef" for value in current_commit + rollback_commit)
        or current_commit == rollback_commit
    ):
        raise ValueError("macmini_campaign_release_coordinate_invalid")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--namespace", default="foundry-qa")
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--business-probe-config", required=True)
    parser.add_argument("--operator-token-file", required=True)
    parser.add_argument("--age-recipient-file", required=True)
    parser.add_argument("--age-identity-file", required=True)
    parser.add_argument("--start-delay-seconds", type=int, default=60)
    parser.add_argument("--current-commit", required=True)
    parser.add_argument("--rollback-commit", required=True)
    parser.add_argument("--rerun-failed-and-skipped-from-run-id", default="")
    args = parser.parse_args()
    summary = run_remediation(args) if args.rerun_failed_and_skipped_from_run_id else run_campaign(args)
    print(json.dumps(summary, separators=(",", ":"), sort_keys=True))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
