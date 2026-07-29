from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from foundry_lite.application.pipeline_async_execution_types import PipelineRunArtifactRecord
from foundry_lite.application.ports.pipeline_execution_repository import (
    PipelineNodeAttemptRow,
    PipelineNodeRunRow,
)
from foundry_lite.application.ports.pipeline_repository import PipelineVersionRow
from foundry_lite.application.services import pipeline_distributed_node_support as support
from foundry_lite.application.services.pipeline_graph_v2_execution_evidence_types import (
    PipelineGraphV2AttemptContext,
    PipelineGraphV2WorkerLease,
)
from foundry_lite.application.services.pipeline_graph_v2_worker_evidence import (
    PipelineGraphV2WorkerEvidence,
)
from foundry_lite.application.services.pipeline_v2_runtime_contracts import PipelineV2RuntimeNode
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, InvariantViolation

NOW = "2026-07-29T00:00:00Z"


class _Engine:
    def begin(self) -> _Engine:
        return self

    def __enter__(self) -> object:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _HeartbeatRepository:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def heartbeat_node_attempt(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        return self.result


class _EvidenceRepository:
    def __init__(self) -> None:
        self.claimed: PipelineNodeAttemptRow | None = _attempt_row()
        self.node: PipelineNodeRunRow | None = _node_row()
        self.artifact: dict[str, object] | None = {"id": "artifact-a"}
        self.terminal: PipelineNodeAttemptRow | None = _attempt_row()
        self.claim_payload: object | None = None
        self.terminal_payload: dict[str, object] | None = None

    def claim_node_attempt(self, *, claim: object, **_kwargs: object) -> PipelineNodeAttemptRow | None:
        self.claim_payload = claim
        return self.claimed

    def node_run_by_run_node(self, **_kwargs: object) -> PipelineNodeRunRow | None:
        return self.node

    def insert_fenced_artifact(self, **_kwargs: object) -> dict[str, object] | None:
        return self.artifact

    def update_fenced_node_attempt_terminal(self, **kwargs: object) -> PipelineNodeAttemptRow | None:
        self.terminal_payload = dict(kwargs)
        return self.terminal


def test_distributed_support_parses_worker_payloads_and_runtime_results() -> None:
    payload = {
        "tenant_id": "tenant-a",
        "request_id": "request-a",
        "workerIdentity": "worker-a",
        "externalExecutionId": "activity-a",
        "node": {"nodeId": "node-a"},
    }
    ctx = support.worker_context(payload)
    lease = support.worker_lease(payload)
    node = PipelineV2RuntimeNode("node-a", "kind", "descriptor", 1, "runtime", {"cacheKey": "cache-a"})
    failed = support.run_result(
        [
            {"nodeId": "node-a", "status": "failed", "error": {"kind": "validation"}},
            {"nodeId": "node-b", "status": "skipped"},
        ]
    )
    succeeded = support.run_result(
        [{"nodeId": "node-a", "status": "succeeded", "outputs": {"one": {"status": "COMMITTED"}}}]
    )

    assert (ctx.tenant_id, ctx.request_id) == ("tenant-a", "request-a")
    assert lease.worker_id == "worker-a"
    assert lease.external_execution_id == "activity-a"
    assert support.node_id(payload) == "node-a"
    assert support.required_runtime_node((node,), "node-a") is node
    assert support.mapping_rows({"one": {"id": 1}, "bad": 2}) == [{"id": 1}]
    assert support.mapping_rows([{"id": 2}, "bad"]) == [{"id": 2}]
    assert support.mapping_rows("bad") == []
    assert failed.failed_node_id == "node-a"
    assert failed.skipped_node_ids == ("node-b",)
    assert succeeded.outputs == ({"status": "COMMITTED"},)
    assert support.has_stable_idempotency(node.config) is True
    assert support.has_stable_idempotency({}) is False
    assert support.integer(7, 3) == 7
    assert support.integer(True, 3) == 3
    assert support.timestamp(datetime(2026, 7, 29, tzinfo=UTC)) == NOW

    with pytest.raises(InvariantViolation, match="coordinate is invalid"):
        support.required_text({"field": ""}, "field")
    with pytest.raises(InvariantViolation, match="node payload is missing"):
        support.node_id({})
    with pytest.raises(InvariantViolation, match="not present"):
        support.required_runtime_node((node,), "missing")


def test_distributed_support_reads_pinned_policy_and_heartbeats(monkeypatch: pytest.MonkeyPatch) -> None:
    version = cast(
        PipelineVersionRow,
        {
            "execution_plan": {
                "nodes": [
                    {
                        "nodeId": "node-a",
                        "executionPolicy": {"maximumAttempts": 4},
                    }
                ]
            }
        },
    )
    assert support.node_policy(version, "node-a") == {"maximumAttempts": 4}
    assert support.node_policy(version, "missing") == {}

    repository = _HeartbeatRepository({"id": "attempt-a"})
    heartbeat = support.AttemptHeartbeat(
        cast(object, _Engine()),
        cast(object, repository),
        RequestContext(tenant_id="tenant-a"),
    )
    assert heartbeat._renew() is False
    heartbeat._attempt = _attempt_context()
    assert heartbeat._renew() is True
    assert repository.calls[0]["worker_id"] == "worker-a"

    repository.result = None
    monkeypatch.setattr(support, "_ATTEMPT_HEARTBEAT_SECONDS", 0.001)
    heartbeat.start(_attempt_context())
    heartbeat.stop()

    heartbeat._attempt = _attempt_context()
    cast(Any, heartbeat)._stop = SimpleNamespace(wait=lambda _seconds: False)
    heartbeat._loop()


def test_worker_evidence_claim_commit_and_fencing_failures() -> None:
    repository = _EvidenceRepository()
    evidence = PipelineGraphV2WorkerEvidence(
        cast(object, repository),
        RequestContext(tenant_id="tenant-a", request_id="request-a"),
        "run-a",
        _worker_lease(),
    )
    node, attempt = evidence.claim_attempt(object(), _node_row(), (), "executor", "descriptor", 1)
    artifact = evidence.insert_artifact(object(), _artifact_record())
    evidence.complete_attempt(object(), _attempt_context(), "SUCCEEDED", {"ok": True}, None, NOW)

    assert node["id"] == "node-run-a"
    assert attempt["id"] == "attempt-a"
    assert artifact["id"] == "artifact-a"
    assert repository.claim_payload is not None
    assert repository.terminal_payload is not None
    assert repository.terminal_payload["error_kind"] is None

    evidence.complete_attempt(
        object(),
        _attempt_context(),
        "FAILED",
        {},
        {"code": "adapter_transient"},
        NOW,
    )
    assert repository.terminal_payload["error_kind"] == "adapter_transient"

    repository.claimed = None
    with pytest.raises(ConflictDetected, match="another worker"):
        evidence.claim_attempt(object(), _node_row(), (), "executor", "descriptor", 1)
    repository.artifact = None
    with pytest.raises(ConflictDetected, match="artifact evidence"):
        evidence.insert_artifact(object(), _artifact_record())
    repository.terminal = None
    with pytest.raises(ConflictDetected, match="terminal attempt"):
        evidence.complete_attempt(object(), _attempt_context(), "FAILED", {}, {"kind": "validation"}, NOW)


def test_worker_evidence_rejects_missing_rows_and_incomplete_coordinates() -> None:
    repository = _EvidenceRepository()
    evidence = PipelineGraphV2WorkerEvidence(
        cast(object, repository),
        RequestContext(tenant_id="tenant-a"),
        "run-a",
        _worker_lease(),
    )
    repository.node = None
    with pytest.raises(InvariantViolation, match="disappeared"):
        evidence.claim_attempt(object(), _node_row(), (), "executor", "descriptor", 1)

    incomplete = PipelineGraphV2AttemptContext(
        node_id="node-a",
        descriptor_id="descriptor",
        spec_version=1,
        executor_profile="executor",
        node_run_id="node-run-a",
        attempt_id="attempt-a",
        attempt_number=1,
        input_artifacts=(),
    )
    with pytest.raises(InvariantViolation, match="coordinates are incomplete"):
        evidence.complete_attempt(object(), incomplete, "FAILED", {}, {"kind": "validation"}, NOW)


def _worker_lease() -> PipelineGraphV2WorkerLease:
    return PipelineGraphV2WorkerLease(
        worker_id="worker-a",
        lease_token="lease-a",
        lease_expires_at="2099-01-01T00:00:00Z",
        heartbeat_at=NOW,
        external_execution_id="activity-a",
    )


def _attempt_context() -> PipelineGraphV2AttemptContext:
    return PipelineGraphV2AttemptContext(
        node_id="node-a",
        descriptor_id="descriptor",
        spec_version=1,
        executor_profile="executor",
        node_run_id="node-run-a",
        attempt_id="attempt-a",
        attempt_number=1,
        input_artifacts=(),
        worker_id="worker-a",
        lease_token="lease-a",
        fencing_token=1,
    )


def _node_row() -> PipelineNodeRunRow:
    return cast(
        PipelineNodeRunRow,
        {
            "id": "node-run-a",
            "tenant_id": "tenant-a",
            "run_id": "run-a",
            "node_id": "node-a",
            "descriptor_id": "descriptor",
            "spec_version": "1",
            "status": "RUNNING",
            "attempt_count": 1,
            "input_artifacts": [],
            "output_artifacts": [],
            "error": None,
            "started_at": NOW,
            "completed_at": None,
            "created_at": NOW,
            "updated_at": NOW,
        },
    )


def _attempt_row() -> PipelineNodeAttemptRow:
    return cast(
        PipelineNodeAttemptRow,
        {
            "id": "attempt-a",
            "tenant_id": "tenant-a",
            "node_run_id": "node-run-a",
            "attempt_number": 1,
            "status": "RUNNING",
            "executor_profile": "executor",
            "lease_owner": "worker-a",
            "lease_token": "lease-a",
            "lease_expires_at": "2099-01-01T00:00:00Z",
            "fencing_token": 1,
            "heartbeat_at": NOW,
            "retry_at": None,
            "error_kind": None,
            "external_execution_id": "activity-a",
            "input_manifest": {},
            "output_manifest": {},
            "error": None,
            "started_at": NOW,
            "completed_at": None,
        },
    )


def _artifact_record() -> PipelineRunArtifactRecord:
    return PipelineRunArtifactRecord(
        artifact_id="artifact-a",
        tenant_id="tenant-a",
        run_id="run-a",
        node_run_id="node-run-a",
        attempt_id="attempt-a",
        fencing_token=1,
        node_id="node-a",
        port_id="output",
        artifact_kind="dataset_version",
        plane="dataset",
        artifact_ref={"datasetRef": "dataset-a"},
        manifest={},
        content_fingerprint="content-a",
        security_envelope={"classification": "INTERNAL"},
        status="COMMITTED",
        is_serving=True,
        idempotency_key="artifact-a",
        committed_at=NOW,
        created_at=NOW,
    )
