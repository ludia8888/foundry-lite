from __future__ import annotations

import json
import subprocess
from argparse import Namespace

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
