from __future__ import annotations

import json
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest

from scripts.operations import verify_macmini_postgres_object_store as collector
from scripts.operations import verify_postgres_object_store_runtime as runtime


def _runtime_evidence() -> dict[str, object]:
    return runtime._receipt("foundry_lite_app", 3)


def test_runtime_receipt_exposes_exact_contract_without_credentials() -> None:
    receipt = _runtime_evidence()

    assert receipt["jsonbColumnCount"] == 15
    assert receipt["productionIndexCount"] == 10
    assert receipt["jsonbPathOpsGinIndexCount"] == 2
    assert receipt["forcedRlsTableCount"] == 9
    assert receipt["crossTenantWriteBlocked"] is True
    assert receipt["rawCredentialsStored"] is False


def test_collector_executes_only_the_protected_api_pod_and_stores_safe_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / "state"
    state.mkdir()
    kubeconfig = state / "kubeconfig"
    kubeconfig.write_text("config", encoding="utf-8")
    kubeconfig.chmod(0o600)
    observed: list[tuple[str, ...]] = []

    def run(command: tuple[str, ...], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps(_runtime_evidence()).encode(), b"")

    monkeypatch.setattr(collector, "QA_ROOT", tmp_path)
    monkeypatch.setattr(collector, "assert_host_boundary", lambda: None)
    monkeypatch.setattr(collector, "assert_namespace", lambda _namespace: None)
    monkeypatch.setattr(collector, "utc_now", lambda: "2026-08-18T00:00:00Z")
    monkeypatch.setattr(collector.subprocess, "run", run)
    monkeypatch.setattr(
        collector,
        "write_json_receipt",
        lambda path, payload: path.write_text(json.dumps(payload), encoding="utf-8"),
    )
    args = Namespace(
        run_id="run-1",
        namespace="foundry-qa",
        tenant_id="tenant-demo",
        kubeconfig=str(kubeconfig),
        kubectl="kubectl",
    )

    receipt = collector.collect(args)

    assert receipt["status"] == "passed"
    assert receipt["otherNamespacesMutated"] is False
    assert observed[0][:5] == ("kubectl", "--kubeconfig", str(kubeconfig), "--namespace", "foundry-qa")
    assert observed[0][5:10] == ("exec", "deployment/foundry-lite", "-c", "api", "--")
    assert (tmp_path / "evidence/run-1/postgres-object-store-live.json").is_file()


def test_collector_rejects_incomplete_runtime_evidence() -> None:
    evidence = _runtime_evidence()
    evidence["canBypassRls"] = True

    with pytest.raises(RuntimeError, match="evidence_invalid"):
        collector._validated_evidence(evidence)
