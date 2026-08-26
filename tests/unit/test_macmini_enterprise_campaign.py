from __future__ import annotations

import json
import subprocess
from argparse import Namespace
from pathlib import Path

from scripts.operations import run_macmini_enterprise_campaign as subject
from scripts.operations.macmini_enterprise_campaign_plan import EVENTS


def test_campaign_event_commands_are_fixed_and_shell_free(tmp_path, monkeypatch) -> None:
    qa_root = tmp_path / "foundry-qa"
    monkeypatch.setattr(subject, "QA_ROOT", qa_root)
    args = Namespace(
        run_id="enterprise-qa",
        namespace="foundry-qa",
        kubeconfig=str(qa_root / "state/kubeconfig"),
    )
    event = next(item for item in EVENTS if item.event_id == "api-pod-delete")

    command, timeout = subject._event_command(args, event)

    assert command[:3] == (str(qa_root / "bin/uv"), "run", "python")
    assert "--fault" in command
    assert command[command.index("--fault") + 1] == "api-pod"
    assert timeout >= 900


def test_execute_accepts_only_zero_exit_passed_receipt(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(subject, "QA_ROOT", tmp_path)
    monkeypatch.setattr(subject, "_environment", lambda: {})
    monkeypatch.setattr(
        subject.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 0, json.dumps({"status": "passed"}).encode(), b""
        ),
    )

    receipt = subject._execute(("fixed", "argv"), 10)

    assert receipt["status"] == "passed"
    assert receipt["rawOutputStored"] is False


def test_campaign_summary_never_calls_blocked_or_unproven_work_passed() -> None:
    receipts = [{"status": "passed"}, {"status": "notProven"}, {"status": "blocked"}]

    summary = subject._campaign_summary("run", subject.datetime.now(subject.UTC), receipts, 0)

    assert summary["status"] == "blocked"
    assert summary["p0P1Clear"] is False


def test_campaign_summary_surfaces_phase_and_baseline_return_evidence() -> None:
    soak = {
        "status": "passed",
        "baselineReturn": {"status": "passed"},
        "phaseMetrics": {"baseline": {"sampleCount": 10}},
    }

    summary = subject._campaign_summary("run", subject.datetime.now(subject.UTC), [{"status": "passed"}], 0, soak)

    assert summary["status"] == "passed"
    assert summary["baselineReturn"] == {"status": "passed"}
    assert summary["phaseMetrics"] == {"baseline": {"sampleCount": 10}}


def test_failed_fault_execution_with_passed_recovery_does_not_skip_later_faults() -> None:
    event = next(item for item in EVENTS if item.event_id == "api-sigterm")
    receipt = {
        "status": "failed",
        "execution": {"status": "failed"},
        "recovery": {"status": "passed"},
    }

    assert subject._blocks_later_mutations(event, receipt) is False


def test_failed_recovery_stops_later_mutating_faults() -> None:
    event = next(item for item in EVENTS if item.event_id == "worker-sigkill")
    receipt = {
        "status": "failed",
        "execution": {"status": "passed"},
        "recovery": {"status": "failed"},
    }

    assert subject._blocks_later_mutations(event, receipt) is True


def test_remediation_selects_failed_skipped_and_previously_blocked_events() -> None:
    journal = b"\n".join(
        (
            json.dumps({"eventId": "api-pod-delete", "status": "passed"}).encode(),
            json.dumps({"eventId": "api-sigterm", "status": "failed"}).encode(),
            json.dumps({"eventId": "worker-sigkill", "status": "skipped"}).encode(),
            json.dumps({"eventId": "external-oidc-network-path", "status": "blocked"}).encode(),
        )
    )

    selected = subject._selected_remediation_event_ids(journal)

    assert selected == {"api-sigterm", "worker-sigkill", "external-oidc-network-path"}


def test_remediation_also_selects_planned_events_missing_after_interruption() -> None:
    journal = b"\n".join(
        (
            json.dumps({"eventId": "bad-config", "status": "failed"}).encode(),
            json.dumps({"eventId": "migration-failure", "status": "passed"}).encode(),
        )
    )

    selected = subject._selected_remediation_event_ids(
        journal,
        {"bad-config", "migration-failure", "verified-digest-rollback", "backup-restore-recovery"},
    )

    assert selected == {"bad-config", "verified-digest-rollback", "backup-restore-recovery"}


def test_remediation_reads_interrupted_event_ids_from_saved_plan(tmp_path: Path) -> None:
    path = tmp_path / "plan.json"
    path.write_text(
        json.dumps({"eventIds": ["bad-config", "migration-failure", "verified-digest-rollback"]}),
        encoding="utf-8",
    )

    assert subject._planned_event_ids(path) == {
        "bad-config",
        "migration-failure",
        "verified-digest-rollback",
    }


def test_remediation_summary_does_not_claim_full_24_hour_clear() -> None:
    args = Namespace(run_id="remediation", rerun_failed_and_skipped_from_run_id="source")

    summary = subject._remediation_summary(
        args,
        [{"status": "passed"}, {"status": "passed"}],
        b"journal",
        subject.datetime.now(subject.UTC),
    )

    assert summary["status"] == "passed"
    assert summary["full24HourCampaignStatus"] == "notProven"
    assert summary["p0P1Clear"] is False


def test_outbox_watermark_drain_ignores_newer_global_pending_rows(monkeypatch) -> None:
    observations = iter(
        [
            {
                "status": "passed",
                "receipt": {
                    "outboxPendingCount": 3,
                    "oldestOutboxPendingSeconds": 1,
                    "outboxUnpublishedAtWatermarkCount": 1,
                    "oldestOutboxUnpublishedAtWatermarkSeconds": 1,
                    "deadLetterCount": 259,
                },
            },
            *[
                {
                    "status": "passed",
                    "receipt": {
                        "outboxPendingCount": 2,
                        "oldestOutboxPendingSeconds": 1,
                        "outboxUnpublishedAtWatermarkCount": 0,
                        "oldestOutboxUnpublishedAtWatermarkSeconds": 0,
                        "deadLetterCount": 259,
                    },
                }
                for _ in range(3)
            ],
        ]
    )
    monkeypatch.setattr(subject, "_execute", lambda *_args: next(observations))
    monkeypatch.setattr(subject.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(subject.time, "sleep", lambda _seconds: None)

    watermark = ("2026-08-26T00:00:00+00:00", "evt-watermark")
    receipt = subject._wait_for_outbox_drain(Namespace(namespace="foundry-qa", kubeconfig="kubeconfig"), 259, watermark)

    assert receipt["status"] == "passed"
    assert receipt["observations"] == 4
    assert receipt["watermark"]["eventId"] == "evt-watermark"


def test_outbox_drain_fails_immediately_when_fault_creates_new_dlq(monkeypatch) -> None:
    monkeypatch.setattr(
        subject,
        "_execute",
        lambda *_args: {
            "status": "passed",
            "receipt": {
                "outboxPendingCount": 0,
                "outboxUnpublishedAtWatermarkCount": 0,
                "oldestOutboxUnpublishedAtWatermarkSeconds": 0,
                "deadLetterCount": 260,
            },
        },
    )
    monkeypatch.setattr(subject.time, "monotonic", lambda: 0.0)

    receipt = subject._wait_for_outbox_drain(
        Namespace(namespace="foundry-qa", kubeconfig="kubeconfig"),
        259,
        ("2026-08-26T00:00:00+00:00", "evt-watermark"),
    )

    assert receipt["status"] == "failed"
    assert receipt["reason"] == "outbox_or_dead_letter_invariant_failed"


def test_all_seven_historic_false_failures_use_watermark_scoped_commands() -> None:
    event_ids = {
        "api-pod-delete",
        "worker-pod-delete",
        "controller-pod-delete",
        "minio-stop",
        "clamav-stop",
        "security-time-runtime",
        "bad-image",
    }
    assert event_ids <= {event.event_id for event in EVENTS}
    command = subject._operations_probe_command(
        Namespace(namespace="foundry-qa", kubeconfig="kubeconfig"),
        ("2026-08-26T00:00:00+00:00", "evt-watermark"),
    )
    assert command[-4:] == (
        "--outbox-watermark-created-at",
        "2026-08-26T00:00:00+00:00",
        "--outbox-watermark-event-id",
        "evt-watermark",
    )


def test_external_oidc_event_is_executable_and_mutating(tmp_path, monkeypatch) -> None:
    qa_root = tmp_path / "foundry-qa"
    monkeypatch.setattr(subject, "QA_ROOT", qa_root)
    event = next(item for item in EVENTS if item.event_id == "external-oidc-network-path")
    args = Namespace(
        run_id="run-1",
        namespace="foundry-qa",
        kubeconfig="kubeconfig",
        external_oidc_chart="chart",
        external_oidc_public_base_url="https://foundry.example",
        external_oidc_identity_base_url="https://identity.example",
        external_oidc_application_id="foundry-lite",
        external_oidc_principals_file="principals",
    )

    command, timeout = subject._event_command(args, event)

    assert event.kind == "external-oidc-fault"
    assert subject._is_mutating(event) is True
    assert "scripts/operations/run_macmini_external_oidc_rehearsal.py" in command
    assert "--principals-file" in command
    assert timeout >= 900


def test_recovery_probe_retries_transient_post_restart_failure(monkeypatch) -> None:
    attempts = iter(
        (
            {"status": "failed", "business": {"status": "failed"}, "operations": {"status": "passed"}},
            {"status": "passed", "business": {"status": "passed"}, "operations": {"status": "passed"}},
        )
    )
    sleeps: list[float] = []
    monkeypatch.setattr(subject, "_recovery_attempt", lambda _args: next(attempts))
    monkeypatch.setattr(subject.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(subject.time, "sleep", sleeps.append)

    receipt = subject._recovery_probe(Namespace())

    assert receipt["status"] == "passed"
    assert receipt["attemptCount"] == 2
    assert receipt["firstAttemptStatus"] == "failed"
    assert receipt["recoveredAfterRetry"] is True
    assert receipt["reason"] is None
    assert sleeps == [5]


def test_recovery_probe_fails_after_bounded_deadline(monkeypatch) -> None:
    monotonic_values = iter((0.0, 121.0, 121.0))
    monkeypatch.setattr(
        subject,
        "_recovery_attempt",
        lambda _args: {
            "status": "failed",
            "business": {"status": "failed"},
            "operations": {"status": "passed"},
        },
    )
    monkeypatch.setattr(subject.time, "monotonic", lambda: next(monotonic_values))

    receipt = subject._recovery_probe(Namespace())

    assert receipt["status"] == "failed"
    assert receipt["attemptCount"] == 1
    assert receipt["recoveredAfterRetry"] is False
    assert receipt["reason"] == "recovery_deadline_exceeded"
