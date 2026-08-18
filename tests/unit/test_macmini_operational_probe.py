from __future__ import annotations

import json
import subprocess
from argparse import Namespace

import pytest

from scripts.operations import run_macmini_operational_probe as subject


def test_operational_probe_returns_only_bounded_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "databaseConnections": 6,
        "outboxPendingCount": 0,
        "oldestOutboxPendingSeconds": 0,
        "deadLetterCount": 4,
    }
    monkeypatch.setattr(subject, "assert_host_boundary", lambda: None)
    monkeypatch.setattr(subject, "assert_namespace", lambda namespace: None)
    monkeypatch.setattr(
        subject.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, json.dumps(payload).encode(), b""),
    )

    receipt = subject.collect(
        Namespace(
            namespace="foundry-qa",
            kubeconfig="/private/kubeconfig",
            kubectl="kubectl",
            timeout_seconds=30,
        )
    )

    assert receipt["status"] == "passed"
    assert receipt["outboxPendingCount"] == 0
    assert "databaseUrl" not in receipt


def test_operational_probe_rejects_negative_or_non_integer_metrics() -> None:
    with pytest.raises(RuntimeError, match="postgresql_invalid"):
        subject._validated_payload(b'{"databaseConnections":-1}')
