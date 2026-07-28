from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest
from foundry_lite.application.ports.pipeline_execution_repository import (
    PipelineNodeAttemptRow,
    PipelineNodeRunRow,
)
from foundry_lite.application.services.pipeline_graph_contracts import PipelineArtifactKind
from foundry_lite.application.services.pipeline_graph_v2_execution_evidence import (
    PipelineGraphV2ExecutionEvidenceWriter,
)
from foundry_lite.application.services.pipeline_graph_v2_execution_evidence_records import (
    pipeline_graph_v2_artifact_idempotency_key,
    pipeline_graph_v2_input_artifact,
)
from foundry_lite.application.services.pipeline_graph_v2_execution_evidence_types import (
    PipelineGraphV2ArtifactSpec,
)
from foundry_lite.application.services.pipeline_run_recovery import (
    PipelineExecutionLeaseGuard,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, InvariantViolation, ValidationFailed
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.pipeline_execution_repository import (
    SqlAlchemyPipelineExecutionRepository,
)
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

_TENANT_ID = "tenant-graph-v2"
_RUN_ID = "pipeline-run-graph-v2"
_COMMITTED_AT = "2026-07-17T12:00:00+00:00"


@dataclass(frozen=True)
class EvidenceHarness:
    engine: Engine
    repository: SqlAlchemyPipelineExecutionRepository
    writer: PipelineGraphV2ExecutionEvidenceWriter


@pytest.fixture
def evidence(tmp_path: Path) -> Iterator[EvidenceHarness]:
    engine = create_engine(f"sqlite:///{tmp_path / 'pipeline-evidence.db'}", future=True)
    db.create_database(engine)
    repository = SqlAlchemyPipelineExecutionRepository(engine)
    writer = PipelineGraphV2ExecutionEvidenceWriter(
        transaction_manager=engine,
        repository=repository,
        ctx=RequestContext(
            tenant_id=_TENANT_ID,
            actor_user_id="user-graph-v2",
            request_id="req-graph-v2",
        ),
        run_id=_RUN_ID,
        execution_lease_guard=cast(PipelineExecutionLeaseGuard, _NoopLeaseGuard()),
    )
    yield EvidenceHarness(engine=engine, repository=repository, writer=writer)
    engine.dispose()


@pytest.mark.parametrize(
    ("artifact_kind", "plane", "port_id", "is_serving", "row_count", "item_count"),
    [
        (PipelineArtifactKind.MEDIA_SET_SELECTION, "media", "selection", False, None, 3),
        (PipelineArtifactKind.MEDIA_DERIVATIVE_SET, "media", "derivatives", False, None, 3),
        (PipelineArtifactKind.CONTENT_UNIT_SET, "content", "content", False, None, 12),
        (PipelineArtifactKind.VECTOR_INDEX_GENERATION, "index", "index", True, None, 12),
        (PipelineArtifactKind.DATASET_VERSION, "dataset", "dataset", True, 12, None),
    ],
)
def test_persists_durable_passport_for_each_required_artifact_kind(
    evidence: EvidenceHarness,
    artifact_kind: PipelineArtifactKind,
    plane: str,
    port_id: str,
    is_serving: bool,
    row_count: int | None,
    item_count: int | None,
) -> None:
    node_id = f"node-{artifact_kind.value}"
    attempt = evidence.writer.start(
        node_id=node_id,
        descriptor_id="document.extract",
        spec_version=3,
        executor_profile="python.media.v2",
        input_artifacts=(),
    )
    spec = _artifact_spec(
        node_id=node_id,
        port_id=port_id,
        artifact_kind=artifact_kind,
        plane=plane,
        is_serving=is_serving,
        row_count=row_count,
        item_count=item_count,
    )

    artifact = evidence.writer.succeed(attempt, spec)
    node_runs, attempts, artifacts = _execution_rows(evidence, attempt.node_run_id)

    assert len(node_runs) == len(attempts) == len(artifacts) == 1
    assert node_runs[0]["status"] == "SUCCEEDED"
    assert node_runs[0]["descriptor_id"] == "document.extract"
    assert node_runs[0]["spec_version"] == "3"
    assert node_runs[0]["output_artifacts"][0]["portId"] == port_id
    assert attempts[0]["status"] == "SUCCEEDED"
    assert attempts[0]["executor_profile"] == "python.media.v2"
    assert attempts[0]["input_manifest"]["descriptorId"] == "document.extract"
    assert attempts[0]["input_manifest"]["specVersion"] == 3
    assert artifact == artifacts[0]
    _assert_artifact_passport(
        artifact,
        artifact_kind=artifact_kind,
        plane=plane,
        port_id=port_id,
        is_serving=is_serving,
        row_count=row_count,
        item_count=item_count,
    )


def test_preserves_named_ports_and_input_passport_across_modalities(
    evidence: EvidenceHarness,
) -> None:
    source_attempt = evidence.writer.start(
        node_id="media-source",
        descriptor_id="source.media-set",
        spec_version=2,
        executor_profile="python.media.v2",
        input_artifacts=(),
    )
    source = evidence.writer.succeed(
        source_attempt,
        _artifact_spec(
            node_id="media-source",
            port_id="selected-media",
            artifact_kind=PipelineArtifactKind.MEDIA_SET_SELECTION,
            plane="media",
            classification="CONFIDENTIAL",
            item_count=2,
        ),
    )
    input_artifact = pipeline_graph_v2_input_artifact(source, target_port_id="media")
    transform_attempt = evidence.writer.start(
        node_id="document-extract",
        descriptor_id="document.extract",
        spec_version=4,
        executor_profile="python.media.v2",
        input_artifacts=(input_artifact,),
    )

    output = evidence.writer.succeed(
        transform_attempt,
        _artifact_spec(
            node_id="document-extract",
            port_id="content-units",
            artifact_kind=PipelineArtifactKind.CONTENT_UNIT_SET,
            plane="content",
            classification="RESTRICTED",
            item_count=18,
        ),
    )
    node_runs, attempts, _artifacts = _execution_rows(evidence, transform_attempt.node_run_id)

    input_passport = node_runs[-1]["input_artifacts"][0]
    assert input_passport["artifactId"] == source["id"]
    assert input_passport["sourceNodeId"] == "media-source"
    assert input_passport["sourcePortId"] == "selected-media"
    assert input_passport["targetPortId"] == "media"
    assert input_passport["securityEnvelope"]["classification"] == "CONFIDENTIAL"
    assert attempts[0]["input_manifest"]["artifacts"] == [input_passport]
    metadata = output["manifest"]["metadata"]
    assert metadata["inputArtifacts"] == [input_passport]
    assert metadata["descriptorId"] == "document.extract"
    assert metadata["specVersion"] == 4
    assert output["security_envelope"]["classification"] == "RESTRICTED"


def test_start_and_success_are_replay_safe_without_duplicate_artifacts(
    evidence: EvidenceHarness,
) -> None:
    first_attempt = evidence.writer.start(
        node_id="chunk",
        descriptor_id="content.chunk",
        spec_version=1,
        executor_profile="python.content.v1",
        input_artifacts=(),
    )
    spec = _artifact_spec(
        node_id="chunk",
        port_id="content",
        artifact_kind=PipelineArtifactKind.CONTENT_UNIT_SET,
        plane="content",
        item_count=20,
    )
    first_artifact = evidence.writer.succeed(first_attempt, spec)

    replay_attempt = evidence.writer.start(
        node_id="chunk",
        descriptor_id="content.chunk",
        spec_version=1,
        executor_profile="python.content.v1",
        input_artifacts=(),
    )
    replay_artifact = evidence.writer.succeed(replay_attempt, spec)
    node_runs, attempts, artifacts = _execution_rows(evidence, first_attempt.node_run_id)

    assert replay_attempt == first_attempt
    assert replay_artifact["id"] == first_artifact["id"]
    assert len(node_runs) == len(attempts) == len(artifacts) == 1


def test_idempotency_collision_with_different_evidence_fails_closed(
    evidence: EvidenceHarness,
) -> None:
    attempt = evidence.writer.start(
        node_id="semantic-index",
        descriptor_id="semantic.index",
        spec_version=2,
        executor_profile="python.search.v1",
        input_artifacts=(),
    )
    original = _artifact_spec(
        node_id="semantic-index",
        port_id="index",
        artifact_kind=PipelineArtifactKind.VECTOR_INDEX_GENERATION,
        plane="index",
        is_serving=True,
        item_count=10,
    )
    evidence.writer.succeed(attempt, original)
    changed = _artifact_spec(
        node_id="semantic-index",
        port_id="index",
        artifact_kind=PipelineArtifactKind.VECTOR_INDEX_GENERATION,
        plane="index",
        is_serving=True,
        item_count=10,
        idempotency_key=original.idempotency_key,
        artifact_ref={"generationId": "generation-changed"},
    )

    with pytest.raises(ConflictDetected, match="different evidence"):
        evidence.writer.succeed(attempt, changed)

    _node_runs, _attempts, artifacts = _execution_rows(evidence, attempt.node_run_id)
    assert len(artifacts) == 1
    assert artifacts[0]["artifact_ref"] == {"resourceId": "resource-semantic-index"}


def test_failure_and_skip_replays_preserve_terminal_evidence(
    evidence: EvidenceHarness,
) -> None:
    attempt = evidence.writer.start(
        node_id="failed-ocr",
        descriptor_id="document.extract",
        spec_version=2,
        executor_profile="python.media.v2",
        input_artifacts=(),
    )
    error = {"code": "OCR_TIMEOUT", "message": "processor exceeded its deadline"}
    evidence.writer.fail(attempt, error)
    evidence.writer.fail(attempt, error)
    skipped = evidence.writer.skip(
        node_id="downstream-chunk",
        descriptor_id="content.chunk",
        spec_version=1,
        input_artifacts=(),
        error={"code": "UPSTREAM_FAILED", "sourceNodeId": "failed-ocr"},
    )
    replayed = evidence.writer.skip(
        node_id="downstream-chunk",
        descriptor_id="content.chunk",
        spec_version=1,
        input_artifacts=(),
        error={"code": "UPSTREAM_FAILED", "sourceNodeId": "failed-ocr"},
    )
    node_runs, attempts, artifacts = _execution_rows(evidence, attempt.node_run_id)

    assert node_runs[0]["status"] == "FAILED"
    assert node_runs[0]["error"] == error
    assert attempts[0]["status"] == "FAILED"
    assert attempts[0]["error"] == error
    assert artifacts == []
    assert skipped["status"] == "SKIPPED"
    assert replayed["id"] == skipped["id"]


def test_security_downgrade_is_rejected_before_artifact_commit(
    evidence: EvidenceHarness,
) -> None:
    source_attempt = evidence.writer.start(
        node_id="classified-media",
        descriptor_id="source.media-set",
        spec_version=1,
        executor_profile="python.media.v2",
        input_artifacts=(),
    )
    source = evidence.writer.succeed(
        source_attempt,
        _artifact_spec(
            node_id="classified-media",
            port_id="media",
            artifact_kind=PipelineArtifactKind.MEDIA_SET_SELECTION,
            plane="media",
            classification="CONFIDENTIAL",
            item_count=1,
        ),
    )
    attempt = evidence.writer.start(
        node_id="unsafe-extract",
        descriptor_id="document.extract",
        spec_version=1,
        executor_profile="python.media.v2",
        input_artifacts=(pipeline_graph_v2_input_artifact(source, target_port_id="media"),),
    )

    with pytest.raises(ValidationFailed, match="weaken"):
        evidence.writer.succeed(
            attempt,
            _artifact_spec(
                node_id="unsafe-extract",
                port_id="content",
                artifact_kind=PipelineArtifactKind.CONTENT_UNIT_SET,
                plane="content",
                classification="INTERNAL",
                item_count=5,
            ),
        )

    node_runs, attempts, artifacts = _execution_rows(evidence, attempt.node_run_id)
    assert node_runs[-1]["status"] == "RUNNING"
    assert attempts[0]["status"] == "RUNNING"
    assert len(artifacts) == 1


def test_invalid_node_coordinates_leave_no_partial_evidence(
    evidence: EvidenceHarness,
) -> None:
    with pytest.raises(ValidationFailed, match="node id"):
        evidence.writer.start(
            node_id="",
            descriptor_id="document.extract",
            spec_version=1,
            executor_profile="python.media.v2",
            input_artifacts=(),
        )

    with evidence.engine.begin() as transaction:
        assert (
            evidence.repository.node_runs_for_run(
                transaction=transaction,
                tenant_id=_TENANT_ID,
                run_id=_RUN_ID,
            )
            == []
        )


def test_writer_requires_non_empty_run_and_error_evidence(
    evidence: EvidenceHarness,
) -> None:
    with pytest.raises(ValidationFailed, match="run id"):
        PipelineGraphV2ExecutionEvidenceWriter(
            transaction_manager=evidence.engine,
            repository=evidence.repository,
            ctx=RequestContext("tenant", "user", "request"),
            run_id=" ",
            execution_lease_guard=cast(PipelineExecutionLeaseGuard, _NoopLeaseGuard()),
        )

    attempt = evidence.writer.start(
        node_id="empty-error",
        descriptor_id="content.chunk",
        spec_version=1,
        executor_profile="python.content.v1",
        input_artifacts=(),
    )
    with pytest.raises(ValidationFailed, match="cannot be empty"):
        evidence.writer.fail(attempt, {})


class _NoopLeaseGuard:
    def require_active(self, _transaction: object | None = None) -> None:
        return None


def test_writer_rejects_conflicting_terminal_replays(
    evidence: EvidenceHarness,
) -> None:
    attempt = evidence.writer.start(
        node_id="terminal-conflicts",
        descriptor_id="content.chunk",
        spec_version=1,
        executor_profile="python.content.v1",
        input_artifacts=(),
    )
    spec = _artifact_spec(
        node_id="terminal-conflicts",
        port_id="content",
        artifact_kind=PipelineArtifactKind.CONTENT_UNIT_SET,
        plane="content",
        item_count=1,
    )
    evidence.writer.succeed(attempt, spec)

    with evidence.engine.begin() as transaction:
        node_rows, attempt_rows, _artifacts = _execution_rows(evidence, attempt.node_run_id)
        node_row = cast(PipelineNodeRunRow, node_rows[0])
        attempt_row = cast(PipelineNodeAttemptRow, attempt_rows[0])
        changed_ref = {"artifactId": "different"}

        with pytest.raises(ConflictDetected, match="attempt success replay"):
            evidence.writer._complete_attempt_success(transaction, attempt_row, changed_ref, _COMMITTED_AT)
        with pytest.raises(ConflictDetected, match="node success replay"):
            evidence.writer._complete_node_success(transaction, node_row, changed_ref, _COMMITTED_AT)
        with pytest.raises(ConflictDetected, match="attempt cannot fail"):
            evidence.writer._complete_attempt_failure(transaction, attempt_row, {"code": "LATE"}, _COMMITTED_AT)
        with pytest.raises(ConflictDetected, match="node cannot fail"):
            evidence.writer._complete_node_failure(transaction, node_row, {"code": "LATE"}, _COMMITTED_AT)
        with pytest.raises(ConflictDetected, match="cannot be skipped"):
            evidence.writer._skip_or_replay_node(transaction, node_row, {"code": "LATE"}, _COMMITTED_AT)


def test_writer_rejects_changed_failure_and_skip_replay_evidence(
    evidence: EvidenceHarness,
) -> None:
    attempt = evidence.writer.start(
        node_id="failure-conflicts",
        descriptor_id="content.chunk",
        spec_version=1,
        executor_profile="python.content.v1",
        input_artifacts=(),
    )
    evidence.writer.fail(attempt, {"code": "FIRST"})
    skipped = evidence.writer.skip(
        node_id="skip-conflicts",
        descriptor_id="content.chunk",
        spec_version=1,
        input_artifacts=(),
        error={"code": "UPSTREAM"},
    )

    with evidence.engine.begin() as transaction:
        node_rows, attempt_rows, _artifacts = _execution_rows(evidence, attempt.node_run_id)
        with pytest.raises(ConflictDetected, match="attempt failure replay"):
            evidence.writer._complete_attempt_failure(
                transaction,
                cast(PipelineNodeAttemptRow, attempt_rows[0]),
                {"code": "CHANGED"},
                _COMMITTED_AT,
            )
        with pytest.raises(ConflictDetected, match="node failure replay"):
            evidence.writer._complete_node_failure(
                transaction,
                cast(PipelineNodeRunRow, node_rows[0]),
                {"code": "CHANGED"},
                _COMMITTED_AT,
            )
        with pytest.raises(ConflictDetected, match="skipped-node replay"):
            evidence.writer._skip_or_replay_node(
                transaction,
                cast(PipelineNodeRunRow, skipped),
                {"code": "CHANGED"},
                _COMMITTED_AT,
            )


def test_writer_rejects_changed_claim_attempt_and_context_coordinates(
    evidence: EvidenceHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt = evidence.writer.start(
        node_id="coordinate-conflicts",
        descriptor_id="content.chunk",
        spec_version=1,
        executor_profile="python.content.v1",
        input_artifacts=(),
    )
    node_rows, attempt_rows, _artifacts = _execution_rows(evidence, attempt.node_run_id)
    node_row = cast(PipelineNodeRunRow, node_rows[0])
    attempt_row = cast(PipelineNodeAttemptRow, attempt_rows[0])

    with evidence.engine.begin() as transaction:
        with pytest.raises(ConflictDetected, match="current state"):
            evidence.writer._claim_or_replay_node(
                transaction,
                cast(PipelineNodeRunRow, {**node_row, "status": "FAILED"}),
                (),
                _COMMITTED_AT,
            )
        monkeypatch.setattr(evidence.repository, "attempt_by_number", lambda **_kwargs: None)
        with pytest.raises(InvariantViolation, match="attempt evidence is missing"):
            evidence.writer._ensure_graph_v2_attempt(
                transaction,
                cast(PipelineNodeRunRow, {**node_row, "status": "PENDING"}),
                (),
                "python.content.v1",
                _COMMITTED_AT,
            )
        with pytest.raises(ConflictDetected, match="node run coordinates"):
            evidence.writer._require_node_contract(
                cast(PipelineNodeRunRow, {**node_row, "descriptor_id": "changed"}),
                attempt.node_id,
                attempt.descriptor_id,
                attempt.spec_version,
                (),
            )
        with pytest.raises(ConflictDetected, match="attempt coordinates"):
            evidence.writer._require_attempt_contract(
                cast(PipelineNodeAttemptRow, {**attempt_row, "executor_profile": "changed"}),
                attempt.descriptor_id,
                attempt.spec_version,
                attempt.executor_profile,
                (),
            )


def _artifact_spec(
    *,
    node_id: str,
    port_id: str,
    artifact_kind: PipelineArtifactKind,
    plane: str,
    classification: str = "INTERNAL",
    is_serving: bool = False,
    row_count: int | None = None,
    item_count: int | None = None,
    idempotency_key: str | None = None,
    artifact_ref: dict[str, object] | None = None,
) -> PipelineGraphV2ArtifactSpec:
    content_fingerprint = f"sha256:{node_id}:{port_id}"
    return PipelineGraphV2ArtifactSpec(
        port_id=port_id,
        artifact_kind=artifact_kind,
        plane=plane,
        artifact_ref=artifact_ref or {"resourceId": f"resource-{node_id}"},
        manifest={"profileId": f"profile-{node_id}", "recordCount": row_count or item_count or 0},
        security_envelope={"classification": classification, "markings": ["tenant-scoped"]},
        content_fingerprint=content_fingerprint,
        status="COMMITTED",
        is_serving=is_serving,
        idempotency_key=idempotency_key
        or pipeline_graph_v2_artifact_idempotency_key(
            run_id=_RUN_ID,
            node_id=node_id,
            port_id=port_id,
            content_fingerprint=content_fingerprint,
        ),
        committed_at=_COMMITTED_AT,
        resource_ref=f"ri.foundry-lite.main.resource.{node_id}",
        pins={
            "descriptorVersion": 3,
            "processorVersion": "processor-2026-07-17",
            "modelVersion": "model-2026-07-17",
        },
        row_count=row_count,
        item_count=item_count,
        byte_count=1024,
    )


def _execution_rows(
    evidence: EvidenceHarness,
    node_run_id: str,
) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    with evidence.engine.begin() as transaction:
        node_runs = evidence.repository.node_runs_for_run(
            transaction=transaction,
            tenant_id=_TENANT_ID,
            run_id=_RUN_ID,
        )
        attempts = evidence.repository.attempts_for_node_run(
            transaction=transaction,
            tenant_id=_TENANT_ID,
            node_run_id=node_run_id,
        )
        artifacts = evidence.repository.artifacts_for_run(
            transaction=transaction,
            tenant_id=_TENANT_ID,
            run_id=_RUN_ID,
        )
    return node_runs, attempts, artifacts


def _assert_artifact_passport(
    artifact: dict[str, object],
    *,
    artifact_kind: PipelineArtifactKind,
    plane: str,
    port_id: str,
    is_serving: bool,
    row_count: int | None,
    item_count: int | None,
) -> None:
    assert artifact["artifact_kind"] == artifact_kind.value
    assert artifact["plane"] == plane
    assert artifact["port_id"] == port_id
    assert artifact["status"] == "COMMITTED"
    assert artifact["is_serving"] is is_serving
    assert artifact["committed_at"] == _COMMITTED_AT
    assert artifact["content_fingerprint"] == f"sha256:node-{artifact_kind.value}:{port_id}"
    assert artifact["security_envelope"]["classification"] == "INTERNAL"
    manifest = artifact["manifest"]
    assert manifest["artifact"]["artifactKind"] == artifact_kind.value
    assert manifest["artifact"]["producerPortId"] == port_id
    assert manifest["artifact"]["descriptorId"] == "document.extract"
    assert manifest["artifact"]["specVersion"] == 3
    assert manifest["metadata"]["executorProfile"] == "python.media.v2"
    assert manifest["metadata"]["requestId"] == "req-graph-v2"
    assert manifest["metadata"]["pins"]["processorVersion"] == "processor-2026-07-17"
    assert manifest["rowCount"] == row_count
    assert manifest["itemCount"] == item_count
    assert manifest["byteCount"] == 1024
