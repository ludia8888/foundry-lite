from __future__ import annotations

import json
import subprocess
from argparse import Namespace

import pytest

from scripts.operations import inject_macmini_fault as subject
from scripts.operations.inject_macmini_fault import _isolated_internal_selector, _validate_duration


def test_fault_network_partition_removes_api_from_existing_internal_allow_policy() -> None:
    original = {"matchExpressions": [{"key": "foundry-lite.io/execution-sandbox", "operator": "DoesNotExist"}]}

    isolated = _isolated_internal_selector(original)

    assert isolated["matchExpressions"][-1] == {
        "key": "app.kubernetes.io/component",
        "operator": "NotIn",
        "values": ["api"],
    }
    assert original == {"matchExpressions": [{"key": "foundry-lite.io/execution-sandbox", "operator": "DoesNotExist"}]}


def test_fault_duration_is_bounded() -> None:
    _validate_duration(60)
    with pytest.raises(ValueError, match="duration_out_of_range"):
        _validate_duration(61)


def test_network_partition_restores_original_allow_selector_when_fault_wait_fails(monkeypatch) -> None:
    original = {"matchExpressions": [{"key": "sandbox", "operator": "DoesNotExist"}]}
    calls: list[tuple[str, ...]] = []

    def kubectl(_args: Namespace, operation: tuple[str, ...], _timeout: float) -> subprocess.CompletedProcess[bytes]:
        calls.append(operation)
        if operation[:3] == ("get", "networkpolicy", "foundry-lite-internal"):
            return subprocess.CompletedProcess(
                operation, 0, json.dumps({"spec": {"podSelector": original}}).encode(), b""
            )
        return subprocess.CompletedProcess(operation, 0, b"", b"")

    monkeypatch.setattr(subject, "_kubectl", kubectl)
    monkeypatch.setattr(subject.time, "sleep", lambda _duration: (_ for _ in ()).throw(RuntimeError("injected")))

    with pytest.raises(RuntimeError, match="injected"):
        subject._network_partition(Namespace(namespace="foundry-qa", duration_seconds=1))

    patches = [operation for operation in calls if operation[:2] == ("patch", "networkpolicy")]
    assert len(patches) == 2
    assert '"values":["api"]' in patches[0][-1]
    assert '"values":["api"]' not in patches[1][-1]


def test_dependency_fault_scales_back_up_when_fault_wait_fails(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        subject,
        "_kubectl",
        lambda _args, operation, _timeout: (
            calls.append(operation) or subprocess.CompletedProcess(operation, 0, b"", b"")
        ),
    )
    monkeypatch.setattr(subject.time, "sleep", lambda _duration: (_ for _ in ()).throw(RuntimeError("injected")))

    with pytest.raises(RuntimeError, match="injected"):
        subject._dependency_fault(Namespace(duration_seconds=1), "postgresql")

    assert ("scale", "statefulset", "foundry-lite-postgresql", "--replicas=0") in calls
    assert ("scale", "statefulset", "foundry-lite-postgresql", "--replicas=1") in calls
