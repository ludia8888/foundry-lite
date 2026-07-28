from __future__ import annotations

import hashlib
import io
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest
from foundry_lite.application.core_services import CoreServices
from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports.media_derivative_repository import MediaDerivativeRecord
from foundry_lite.application.ports.media_processor import ProcessorSpec
from foundry_lite.application.ports.transaction_context import TransactionContext
from foundry_lite.application.services.media.processing_records import _canonical_spec_hash
from foundry_lite.application.services.pipeline_media_set_output import (
    PipelineMediaSetOutputCommitter,
)
from foundry_lite.application.services.pipeline_run_recovery import (
    PipelineExecutionLeaseGuard,
)
from foundry_lite.application.services.pipeline_v2_runtime_contracts import (
    PipelineV2CommittedOutputReconciliationRequired,
    PipelineV2RuntimeArtifact,
    PipelineV2RuntimeNode,
)
from foundry_lite.application.services.runtime_error_payloads import (
    runtime_error_payload,
)
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from sqlalchemy import func, select, update


@dataclass(frozen=True)
class _MediaOutputFixture:
    foundry: FoundryLite
    dependencies: CoreDependencies
    ctx: RequestContext
    source_ref: str
    target_ref: str
    source_version_ids: tuple[str, ...]
    source_envelopes: tuple[dict[str, object], ...]
    pipeline_id: str


def test_media_set_output_commits_exact_source_versions_with_durable_lineage_and_replays_once(
    tmp_path: Path,
) -> None:
    fixture = _selection_output_fixture(tmp_path, "selection")
    _deploy_graph(
        fixture,
        _media_selection_output_graph(
            fixture.source_ref,
            fixture.target_ref,
            fixture.source_version_ids,
        ),
    )

    run = fixture.foundry.pipelines.run(
        fixture.pipeline_id,
        idempotency_key="run-media-output-selection",
        ctx=fixture.ctx,
    )
    target = _media_set_by_ref(fixture, fixture.target_ref)
    target_versions = _target_versions(fixture, target.media_set_id)
    output = cast(list[dict[str, Any]], run["outputs"])[0]
    ref = cast(dict[str, Any], output["ref"])
    transaction_id = str(ref["mediaTransactionId"])
    counts = _replay_counts(fixture, str(run["id"]), target.media_set_id, transaction_id)

    assert run["status"] == "succeeded"
    assert output["artifactKind"] == "media_set_selection"
    assert output["plane"] == "media"
    assert output["status"] == "COMMITTED"
    assert output["commitKind"] == "SERVING_ASSET"
    assert output["isServing"] is True
    assert ref["mediaSetRef"] == fixture.target_ref
    assert ref["mediaSetId"] == target.media_set_id
    assert set(ref["mediaItemVersionIds"]) == {row["id"] for row in target_versions}
    assert isinstance(ref["artifactId"], str)
    assert output["manifest"]["commitProtocol"] == ["stage", "validate", "commit"]
    assert output["manifest"]["sourceArtifactKind"] == "media_set_selection"
    assert output["manifest"]["itemCount"] == len(fixture.source_version_ids)
    _assert_exact_target_copies(fixture, target_versions, run)

    replay = fixture.foundry.pipelines.run(
        fixture.pipeline_id,
        idempotency_key="run-media-output-selection",
        ctx=fixture.ctx,
    )

    assert replay["id"] == run["id"]
    assert replay["outputs"] == run["outputs"]
    assert _replay_counts(fixture, str(run["id"]), target.media_set_id, transaction_id) == counts


def test_media_set_output_rejects_an_existing_target_with_a_different_contract(
    tmp_path: Path,
) -> None:
    fixture = _selection_output_fixture(tmp_path, "contract_mismatch")
    _create_media_set(
        fixture,
        fixture.target_ref,
        schema_type="image",
        primary_format="png",
        allowed_input_formats=("png",),
        classification="CONFIDENTIAL",
    )
    _deploy_graph(
        fixture,
        _media_selection_output_graph(
            fixture.source_ref,
            fixture.target_ref,
            fixture.source_version_ids,
        ),
    )

    run = fixture.foundry.pipelines.run(
        fixture.pipeline_id,
        idempotency_key="run-media-output-contract-mismatch",
        ctx=fixture.ctx,
    )
    output = cast(list[dict[str, Any]], run["outputs"])[0]
    target = _media_set_by_ref(fixture, fixture.target_ref)

    assert run["status"] == "failed"
    assert output["status"] == "FAILED"
    assert output["ref"] == {"mediaSetRef": fixture.target_ref}
    assert output["error"]["type"] == "PIPELINE_MEDIA_SET_OUTPUT_CONTRACT_MISMATCH"
    assert output["error"]["details"]["mediaSetRef"] == fixture.target_ref
    assert output["error"]["details"]["mismatches"]["schemaType"] == "image"
    assert _target_versions(fixture, target.media_set_id) == []
    assert _target_transaction_count(fixture, target.media_set_id) == 0


def test_media_set_output_rejects_a_committed_derivative_without_durable_bytes(
    tmp_path: Path,
) -> None:
    fixture = _selection_output_fixture(tmp_path, "derivative_missing_bytes", item_count=1)
    derivative = _seed_derivative(fixture, fixture.source_version_ids[0], body=None)
    _deploy_graph(
        fixture,
        _media_derivative_output_graph(
            fixture.source_ref,
            fixture.target_ref,
            fixture.source_version_ids[0],
        ),
    )

    run = fixture.foundry.pipelines.run(
        fixture.pipeline_id,
        idempotency_key="run-media-output-derivative-missing-bytes",
        ctx=fixture.ctx,
    )
    output = cast(list[dict[str, Any]], run["outputs"])[0]

    assert run["status"] == "failed"
    assert output["status"] == "FAILED"
    assert output["ref"] == {"mediaSetRef": fixture.target_ref}
    assert output["error"]["type"] == "PIPELINE_MEDIA_DERIVATIVE_BYTES_UNAVAILABLE"
    assert output["error"]["details"] == {
        "mediaDerivativeId": derivative.media_derivative_id,
        "reason": "durable_bytes_missing",
    }
    assert _media_set_optional(fixture, fixture.target_ref) is None


def test_media_set_output_copies_a_committed_derivative_with_verified_durable_bytes(
    tmp_path: Path,
) -> None:
    fixture = _selection_output_fixture(tmp_path, "derivative_bytes", item_count=1)
    derivative_body = b"Extracted contract text with durable derivative bytes."
    derivative = _seed_derivative(
        fixture,
        fixture.source_version_ids[0],
        body=derivative_body,
    )
    _deploy_graph(
        fixture,
        _media_derivative_output_graph(
            fixture.source_ref,
            fixture.target_ref,
            fixture.source_version_ids[0],
        ),
    )

    run = fixture.foundry.pipelines.run(
        fixture.pipeline_id,
        idempotency_key="run-media-output-derivative-bytes",
        ctx=fixture.ctx,
    )
    output = cast(list[dict[str, Any]], run["outputs"])[0]
    target = _media_set_by_ref(fixture, fixture.target_ref)
    versions = _target_versions(fixture, target.media_set_id)

    assert run["status"] == "succeeded"
    assert output["manifest"]["sourceArtifactKind"] == "media_derivative_set"
    assert len(versions) == 1
    assert versions[0]["content_hash"] == hashlib.sha256(derivative_body).hexdigest()
    assert versions[0]["byte_size"] == len(derivative_body)
    assert versions[0]["format"] == "txt"
    assert versions[0]["sniffed_mime_type"] == "application/octet-stream"
    assert versions[0]["security_envelope"] == fixture.source_envelopes[0]
    lineage = cast(dict[str, Any], versions[0]["source_ref"])["pipelineOutput"]
    assert lineage["sourceMediaItemVersionId"] == fixture.source_version_ids[0]
    assert lineage["mediaDerivativeId"] == derivative.media_derivative_id
    with fixture.dependencies.media_storage.open_stream(str(versions[0]["blob_key"])) as stream:
        assert stream.read() == derivative_body


def test_media_set_output_rejects_a_derivative_that_weakens_source_security(
    tmp_path: Path,
) -> None:
    fixture = _selection_output_fixture(tmp_path, "derivative_weakened_security", item_count=1)
    derivative = _seed_derivative(
        fixture,
        fixture.source_version_ids[0],
        body=b"Unsafe derivative bytes.",
        security_envelope={
            "tenantId": fixture.ctx.tenant_id,
            "classification": "PUBLIC",
            "policyVersion": "weaker-policy",
            "allowedPrincipalSetId": "broader-principals",
        },
    )
    _deploy_graph(
        fixture,
        _media_derivative_output_graph(
            fixture.source_ref,
            fixture.target_ref,
            fixture.source_version_ids[0],
        ),
    )

    run = fixture.foundry.pipelines.run(
        fixture.pipeline_id,
        idempotency_key="run-media-output-derivative-weakened-security",
        ctx=fixture.ctx,
    )
    output = cast(list[dict[str, Any]], run["outputs"])[0]

    assert run["status"] == "failed"
    assert output["status"] == "FAILED"
    assert output["error"]["type"] == "PIPELINE_MEDIA_DERIVATIVE_SECURITY_MISMATCH"
    assert output["error"]["details"] == {
        "mediaItemVersionId": fixture.source_version_ids[0],
        "mediaDerivativeId": derivative.media_derivative_id,
        "weakenedFields": [
            "allowedPrincipalSetId",
            "classification",
            "policyVersion",
        ],
    }
    assert _media_set_optional(fixture, fixture.target_ref) is None


def test_media_set_output_retry_uses_a_new_fenced_generation_after_abort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _selection_output_fixture(tmp_path, "aborted_generation", item_count=1)
    committer, node, inputs, transaction_service = _direct_committer(fixture)
    real_commit = transaction_service.commit
    commit_calls = 0

    def fail_first_commit(
        ctx: RequestContext,
        *,
        media_transaction_id: str,
        before_commit: Callable[[TransactionContext], None] | None = None,
    ):
        nonlocal commit_calls
        commit_calls += 1
        if commit_calls == 1:
            raise RuntimeError("injected media output commit failure")
        return real_commit(
            ctx,
            media_transaction_id=media_transaction_id,
            before_commit=before_commit,
        )

    monkeypatch.setattr(transaction_service, "commit", fail_first_commit)

    with pytest.raises(RuntimeError, match="injected media output commit failure"):
        committer.commit(node, inputs)

    committed = committer.commit(node, inputs)
    replay = committer.commit(node, inputs)
    target = _media_set_by_ref(fixture, fixture.target_ref)
    transactions = _target_transactions(fixture, target.media_set_id)

    assert [row["status"] for row in transactions] == ["ABORTED", "COMMITTED"]
    assert [row["idempotency_key"] for row in transactions] == [
        "pipeline-output:run-aborted-generation:output",
        "pipeline-output:run-aborted-generation:output:generation:2",
    ]
    assert committed.artifact_ref["mediaTransactionGeneration"] == 2
    assert committed.artifact_ref["mediaTransactionId"] == transactions[1]["id"]
    assert replay.artifact_ref == committed.artifact_ref
    assert replay.items == committed.items
    assert len(_committed_target_versions(fixture, target.media_set_id)) == 1


def test_media_set_output_preserves_primary_error_when_abort_evidence_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _selection_output_fixture(tmp_path, "abort_evidence_failure", item_count=1)
    committer, node, inputs, transaction_service = _direct_committer(fixture)

    def fail_commit(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("primary media commit failure")

    def fail_abort(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("secondary abort evidence failure")

    monkeypatch.setattr(transaction_service, "commit", fail_commit)
    monkeypatch.setattr(transaction_service, "abort", fail_abort)

    with pytest.raises(RuntimeError, match="primary media commit failure") as raised:
        committer.commit(node, inputs)

    assert raised.value.__notes__ == ["media output abort failed: RuntimeError"]
    assert runtime_error_payload(raised.value)["details"] == {
        "cleanupFailures": [
            {
                "operation": "mediaTransactionAbort",
                "status": "FAILED",
                "exceptionType": "RuntimeError",
            }
        ]
    }


def test_committed_media_set_replay_read_failure_keeps_transaction_coordinates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _selection_output_fixture(tmp_path, "committed_replay_read_failure", item_count=1)
    committer, node, inputs, _transaction_service = _direct_committer(fixture)
    committed = committer.commit(node, inputs)

    def fail_version_read(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected committed version read failure")

    monkeypatch.setattr(
        fixture.dependencies.media_repository,
        "fetch_transaction_versions",
        fail_version_read,
    )

    with pytest.raises(PipelineV2CommittedOutputReconciliationRequired) as raised:
        committer.commit(node, inputs)

    artifact = raised.value.committed_artifact
    assert artifact.status == "COMMITTED"
    assert artifact.is_serving is True
    assert artifact.artifact_ref["mediaTransactionId"] == committed.artifact_ref["mediaTransactionId"]
    assert artifact.artifact_ref["mediaSetId"] == committed.artifact_ref["mediaSetId"]
    assert artifact.manifest["coordinateCompleteness"] == "TRANSACTION_ONLY"


def test_media_set_output_evidence_failure_preserves_the_serving_commit_as_partial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _selection_output_fixture(tmp_path, "evidence_failure", item_count=1)
    _deploy_graph(
        fixture,
        _media_selection_output_graph(
            fixture.source_ref,
            fixture.target_ref,
            fixture.source_version_ids,
        ),
    )
    repository = fixture.dependencies.pipeline_execution_repository
    insert_artifact = repository.insert_artifact

    def fail_output_artifact(*, transaction: object, record: object):
        if getattr(record, "node_id", None) == "output":
            raise RuntimeError("injected pipeline artifact evidence failure")
        return insert_artifact(transaction=transaction, record=record)

    monkeypatch.setattr(repository, "insert_artifact", fail_output_artifact)

    run = fixture.foundry.pipelines.run(
        fixture.pipeline_id,
        idempotency_key="run-media-output-evidence-failure",
        ctx=fixture.ctx,
    )
    output = cast(list[dict[str, Any]], run["outputs"])[0]
    target = _media_set_by_ref(fixture, fixture.target_ref)
    committed_versions = _committed_target_versions(fixture, target.media_set_id)

    assert run["status"] == "partial"
    assert run["error"]["type"] == "PIPELINE_OUTPUT_EVIDENCE_PERSISTENCE_FAILED"
    assert output["status"] == "COMMITTED"
    assert output["isServing"] is True
    assert output["ref"]["mediaSetId"] == target.media_set_id
    assert output["artifactEvidence"]["status"] == "RECONCILIATION_REQUIRED"
    assert output["artifactEvidence"]["isDurableRunOutput"] is True
    assert output["artifactEvidence"]["error"]["type"] == "PIPELINE_OUTPUT_EVIDENCE_PERSISTENCE_FAILED"
    assert len(committed_versions) == 1
    assert committed_versions[0]["id"] in output["ref"]["mediaItemVersionIds"]
    assert _run_artifact_count(fixture, str(run["id"]), "output") == 0


def test_media_set_output_post_commit_validation_failure_preserves_serving_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _selection_output_fixture(tmp_path, "post_commit_validation", item_count=1)
    _deploy_graph(
        fixture,
        _media_selection_output_graph(
            fixture.source_ref,
            fixture.target_ref,
            fixture.source_version_ids,
        ),
    )
    transaction_service = fixture.foundry._services.media.transaction
    media_repository = fixture.dependencies.media_repository
    real_commit = transaction_service.commit
    real_stat = fixture.dependencies.media_storage.stat
    real_transaction_by_id = media_repository.transaction_by_id
    real_fetch_versions = media_repository.fetch_transaction_versions
    has_committed = False
    repository_reads_after_commit = 0

    def commit_then_enable_failure(*args: object, **kwargs: object):
        nonlocal has_committed
        result = real_commit(*args, **kwargs)
        has_committed = True
        return result

    def fail_post_commit_stat(object_key: str):
        if has_committed:
            raise RuntimeError("injected post-commit storage stat failure")
        return real_stat(object_key)

    def fail_post_commit_transaction_read(*args: object, **kwargs: object):
        nonlocal repository_reads_after_commit
        if has_committed:
            repository_reads_after_commit += 1
            raise RuntimeError("injected post-commit transaction read failure")
        return real_transaction_by_id(*args, **kwargs)

    def fail_post_commit_version_read(*args: object, **kwargs: object):
        nonlocal repository_reads_after_commit
        if has_committed:
            repository_reads_after_commit += 1
            raise RuntimeError("injected post-commit version read failure")
        return real_fetch_versions(*args, **kwargs)

    monkeypatch.setattr(transaction_service, "commit", commit_then_enable_failure)
    monkeypatch.setattr(fixture.dependencies.media_storage, "stat", fail_post_commit_stat)
    monkeypatch.setattr(media_repository, "transaction_by_id", fail_post_commit_transaction_read)
    monkeypatch.setattr(media_repository, "fetch_transaction_versions", fail_post_commit_version_read)

    run = fixture.foundry.pipelines.run(
        fixture.pipeline_id,
        idempotency_key="run-media-output-post-commit-validation",
        ctx=fixture.ctx,
    )
    output = cast(list[dict[str, Any]], run["outputs"])[0]
    target = _media_set_by_ref(fixture, fixture.target_ref)
    transactions = _target_transactions(fixture, target.media_set_id)

    assert run["status"] == "partial"
    assert run["error"]["type"] == "PIPELINE_OUTPUT_POST_COMMIT_VALIDATION_FAILED"
    assert output["status"] == "COMMITTED"
    assert output["isServing"] is True
    assert output["ref"]["mediaSetId"] == target.media_set_id
    assert output["artifactEvidence"]["status"] == "RECONCILIATION_REQUIRED"
    assert output["artifactEvidence"]["error"]["type"] == "PIPELINE_OUTPUT_POST_COMMIT_VALIDATION_FAILED"
    assert [row["status"] for row in transactions] == ["COMMITTED"]
    assert len(_committed_target_versions(fixture, target.media_set_id)) == 1
    assert _run_artifact_count(fixture, str(run["id"]), "output") == 0
    assert repository_reads_after_commit == 0


def test_media_set_output_unknown_commit_result_keeps_transaction_reconciliation_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _selection_output_fixture(tmp_path, "unknown_commit_result", item_count=1)
    _deploy_graph(
        fixture,
        _media_selection_output_graph(
            fixture.source_ref,
            fixture.target_ref,
            fixture.source_version_ids,
        ),
    )
    transaction_service = fixture.foundry._services.media.transaction
    media_repository = fixture.dependencies.media_repository
    real_commit = transaction_service.commit
    real_transaction_by_id = media_repository.transaction_by_id
    has_committed = False
    failed_state_reads = 0
    abort_calls = 0

    def commit_then_disconnect(*args: object, **kwargs: object):
        nonlocal has_committed
        real_commit(*args, **kwargs)
        has_committed = True
        raise ConnectionError("injected lost commit acknowledgement")

    def fail_commit_state_read(*args: object, **kwargs: object):
        nonlocal failed_state_reads
        if has_committed and failed_state_reads == 0:
            failed_state_reads += 1
            raise ConnectionError("injected reconciliation read outage")
        return real_transaction_by_id(*args, **kwargs)

    def track_abort(*args: object, **kwargs: object) -> None:
        nonlocal abort_calls
        abort_calls += 1

    monkeypatch.setattr(transaction_service, "commit", commit_then_disconnect)
    monkeypatch.setattr(transaction_service, "abort", track_abort)
    monkeypatch.setattr(media_repository, "transaction_by_id", fail_commit_state_read)

    run = fixture.foundry.pipelines.run(
        fixture.pipeline_id,
        idempotency_key="run-media-output-unknown-commit-result",
        ctx=fixture.ctx,
    )
    output = cast(list[dict[str, Any]], run["outputs"])[0]
    target = _media_set_by_ref(fixture, fixture.target_ref)
    transactions = _target_transactions(fixture, target.media_set_id)

    assert run["status"] == "partial"
    assert run["error"]["type"] == "PIPELINE_OUTPUT_COMMIT_OUTCOME_UNKNOWN"
    assert run["error"]["details"]["servingCommitState"] == "UNKNOWN"
    cause = cast(dict[str, Any], run["error"]["details"]["cause"])
    assert cause["details"]["cleanupFailures"][0]["operation"] == "mediaTransactionCommitStateRead"
    assert output["status"] == "COMMIT_OUTCOME_UNKNOWN"
    assert output["isServing"] is False
    assert output["manifest"]["commitOutcome"] == "UNKNOWN"
    assert output["manifest"]["coordinateCompleteness"] == "TRANSACTION_ONLY"
    assert output["artifactEvidence"]["status"] == "RECONCILIATION_REQUIRED"
    assert [row["status"] for row in transactions] == ["COMMITTED"]
    assert abort_calls == 0

    reconciled = fixture.foundry.pipelines.run(
        fixture.pipeline_id,
        idempotency_key="run-media-output-unknown-commit-result",
        ctx=fixture.ctx,
    )
    reconciled_output = cast(list[dict[str, Any]], reconciled["outputs"])[0]

    assert reconciled["id"] == run["id"]
    assert reconciled["status"] == "succeeded"
    assert reconciled["error"] is None
    assert reconciled_output["status"] == "COMMITTED"
    assert reconciled_output["isServing"] is True
    assert len(_target_transactions(fixture, target.media_set_id)) == 1


def test_media_set_output_open_unknown_commit_is_aborted_and_failed_on_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _selection_output_fixture(tmp_path, "open_unknown_commit", item_count=1)
    _deploy_graph(
        fixture,
        _media_selection_output_graph(
            fixture.source_ref,
            fixture.target_ref,
            fixture.source_version_ids,
        ),
    )
    transaction_service = fixture.foundry._services.media.transaction
    media_repository = fixture.dependencies.media_repository
    real_transaction_by_id = media_repository.transaction_by_id
    has_commit_failed = False
    failed_state_reads = 0

    def fail_before_commit(*args: object, **kwargs: object):
        nonlocal has_commit_failed
        has_commit_failed = True
        raise ConnectionError("injected commit transport failure")

    def fail_first_reconciliation_read(*args: object, **kwargs: object):
        nonlocal failed_state_reads
        if has_commit_failed and failed_state_reads == 0:
            failed_state_reads += 1
            raise ConnectionError("injected reconciliation read outage")
        return real_transaction_by_id(*args, **kwargs)

    monkeypatch.setattr(transaction_service, "commit", fail_before_commit)
    monkeypatch.setattr(media_repository, "transaction_by_id", fail_first_reconciliation_read)

    unknown = fixture.foundry.pipelines.run(
        fixture.pipeline_id,
        idempotency_key="run-media-output-open-unknown-commit",
        ctx=fixture.ctx,
    )
    replayed = fixture.foundry.pipelines.run(
        fixture.pipeline_id,
        idempotency_key="run-media-output-open-unknown-commit",
        ctx=fixture.ctx,
    )
    target = _media_set_by_ref(fixture, fixture.target_ref)

    assert unknown["status"] == "partial"
    assert cast(list[dict[str, Any]], unknown["outputs"])[0]["isServing"] is False
    assert replayed["id"] == unknown["id"]
    assert replayed["status"] == "failed"
    assert replayed["outputs"] == []
    assert replayed["error"]["message"] == "pipeline output commit reconciled as not committed"
    assert [row["status"] for row in _target_transactions(fixture, target.media_set_id)] == ["ABORTED"]


def test_media_set_output_aborted_commit_error_remains_a_known_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _selection_output_fixture(tmp_path, "aborted_commit_error", item_count=1)
    _deploy_graph(
        fixture,
        _media_selection_output_graph(
            fixture.source_ref,
            fixture.target_ref,
            fixture.source_version_ids,
        ),
    )
    transaction_service = fixture.foundry._services.media.transaction
    real_abort = transaction_service.abort

    def abort_then_fail(ctx: RequestContext, *, media_transaction_id: str, **kwargs: object):
        real_abort(
            ctx,
            media_transaction_id=media_transaction_id,
            error={"code": "INJECTED_COMMIT_FAILURE"},
        )
        raise RuntimeError("injected known aborted commit failure")

    monkeypatch.setattr(transaction_service, "commit", abort_then_fail)

    run = fixture.foundry.pipelines.run(
        fixture.pipeline_id,
        idempotency_key="run-media-output-aborted-commit-error",
        ctx=fixture.ctx,
    )
    target = _media_set_by_ref(fixture, fixture.target_ref)
    output = cast(list[dict[str, Any]], run["outputs"])[0]

    assert run["status"] == "failed"
    assert output["status"] == "FAILED"
    assert output["isServing"] is False
    assert run["error"]["type"] == "RuntimeError"
    assert [row["status"] for row in _target_transactions(fixture, target.media_set_id)] == ["ABORTED"]


def test_stale_run_recovers_media_transaction_committed_before_artifact_passport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _selection_output_fixture(tmp_path, "stale_transaction_recovery", item_count=1)
    _deploy_graph(
        fixture,
        _media_selection_output_graph(
            fixture.source_ref,
            fixture.target_ref,
            fixture.source_version_ids,
        ),
    )
    repository = fixture.dependencies.pipeline_execution_repository

    class _SimulatedWorkerCrash(BaseException):
        pass

    original_insert = repository.insert_artifact

    def crash_after_commit(*, transaction: object, record: object):
        if getattr(record, "node_id", None) == "output":
            raise _SimulatedWorkerCrash
        return original_insert(transaction=transaction, record=record)

    monkeypatch.setattr(repository, "insert_artifact", crash_after_commit)
    key = "run-media-output-stale-transaction"
    with pytest.raises(_SimulatedWorkerCrash):
        fixture.foundry.pipelines.run(fixture.pipeline_id, idempotency_key=key, ctx=fixture.ctx)

    with fixture.foundry.engine.begin() as transaction:
        row = fixture.dependencies.pipeline_repository.run_by_idempotency_key(
            transaction=transaction,
            tenant_id=fixture.ctx.tenant_id,
            idempotency_key=key,
        )
        assert row is not None and row["status"] == "executing"
        transaction.execute(
            update(db.pipeline_runs)
            .where(
                db.pipeline_runs.c.tenant_id == fixture.ctx.tenant_id,
                db.pipeline_runs.c.id == row["id"],
            )
            .values(execution_lease_expires_at="2000-01-01T00:00:00Z")
        )

    reconciled = fixture.foundry.pipelines.run(fixture.pipeline_id, idempotency_key=key, ctx=fixture.ctx)
    output = cast(list[dict[str, Any]], reconciled["outputs"])[0]
    target = _media_set_by_ref(fixture, fixture.target_ref)
    committed_versions = _committed_target_versions(fixture, target.media_set_id)

    assert reconciled["status"] == "partial"
    assert output["artifactKind"] == "media_set_selection"
    assert output["plane"] == "media"
    assert output["ref"]["mediaSetRef"] == fixture.target_ref
    assert output["ref"]["mediaTransactionId"]
    assert output["artifactEvidence"]["recoverySource"] == "MEDIA_TRANSACTION"
    assert len(committed_versions) == 1
    assert committed_versions[0]["id"] in output["ref"]["mediaItemVersionIds"]
    assert _run_artifact_count(fixture, str(row["id"]), "output") == 0


def _selection_output_fixture(
    tmp_path: Path,
    slug: str,
    *,
    item_count: int = 2,
) -> _MediaOutputFixture:
    dependencies = create_local_core_dependencies(
        db_url=f"sqlite:///{tmp_path / f'{slug}.db'}",
        storage_root=tmp_path / f"{slug}-flite",
    )
    foundry = FoundryLite(dependencies=dependencies)
    ctx = demo_admin_context()
    source_ref = f"media.{slug}_source"
    target_ref = f"media.{slug}_target"
    source_set = _create_media_set(
        _MediaOutputFixture(foundry, dependencies, ctx, source_ref, target_ref, (), (), f"media_output_{slug}"),
        source_ref,
        schema_type="document",
        primary_format="pdf",
        allowed_input_formats=("pdf",),
        classification="CONFIDENTIAL",
    )
    envelopes = tuple(
        {
            "tenantId": ctx.tenant_id,
            "classification": "CONFIDENTIAL",
            "policyVersion": f"policy-{index + 1}",
            "allowedPrincipalSetId": f"principals-{index + 1}",
        }
        for index in range(item_count)
    )
    version_ids = tuple(
        _upload_pdf(
            foundry,
            ctx,
            source_set.media_set_id,
            logical_path=f"/contracts/contract-{index + 1}.pdf",
            body=_make_pdf(f"Contract {index + 1} payment terms"),
            envelope=envelopes[index],
            idempotency_key=f"upload-{slug}-{index + 1}",
        )
        for index in range(item_count)
    )
    return _MediaOutputFixture(
        foundry=foundry,
        dependencies=dependencies,
        ctx=ctx,
        source_ref=source_ref,
        target_ref=target_ref,
        source_version_ids=version_ids,
        source_envelopes=envelopes,
        pipeline_id=f"media_output_{slug}",
    )


def _create_media_set(
    fixture: _MediaOutputFixture,
    media_ref: str,
    *,
    schema_type: str,
    primary_format: str,
    allowed_input_formats: tuple[str, ...],
    classification: str,
):
    namespace, name = media_ref.split(".", 1)
    return fixture.foundry.media.create_media_set(
        fixture.ctx,
        namespace=namespace,
        name=name,
        schema_type=schema_type,
        primary_format=primary_format,
        allowed_input_formats=allowed_input_formats,
        classification=classification,
    )


def _upload_pdf(
    foundry: FoundryLite,
    ctx: RequestContext,
    media_set_id: str,
    *,
    logical_path: str,
    body: bytes,
    envelope: dict[str, object],
    idempotency_key: str,
) -> str:
    transaction_id = foundry.media.open_transaction(
        ctx,
        media_set_id=media_set_id,
        idempotency_key=idempotency_key,
    )
    staged = foundry.media.upload(
        ctx,
        media_set_id=media_set_id,
        media_transaction_id=transaction_id,
        logical_path=logical_path,
        source=io.BytesIO(body),
        supplied_mime_type="application/pdf",
        schema_type="document",
        format="pdf",
        security_envelope=envelope,
    )
    foundry.media.commit(ctx, media_transaction_id=transaction_id)
    return staged.media_item_version_id


def _deploy_graph(
    fixture: _MediaOutputFixture,
    graph: Mapping[str, object],
) -> None:
    branch = fixture.foundry.pipelines.create_branch(
        pipeline_id=fixture.pipeline_id,
        name="media-output-runtime",
        idempotency_key=f"branch-{fixture.pipeline_id}",
        ctx=fixture.ctx,
    )
    fixture.foundry.pipelines.update_graph(
        str(branch["id"]),
        graph=graph,
        expected_fingerprint=str(branch["graphFingerprint"]),
        ctx=fixture.ctx,
    )
    proposal = fixture.foundry.pipelines.propose(
        str(branch["id"]),
        title="Deploy Media Set output",
        idempotency_key=f"proposal-{fixture.pipeline_id}",
        ctx=fixture.ctx,
    )
    reviewer_id = f"{fixture.ctx.actor_user_id}-reviewer"
    fixture.foundry.pipelines.assign(
        str(proposal["id"]),
        assignee_user_id=reviewer_id,
        ctx=fixture.ctx,
    )
    reviewer = RequestContext(
        tenant_id=fixture.ctx.tenant_id,
        actor_user_id=reviewer_id,
        roles=fixture.ctx.roles,
    )
    fixture.foundry.pipelines.approve(str(proposal["id"]), ctx=reviewer)
    version = fixture.foundry.pipelines.execute(str(proposal["id"]), ctx=fixture.ctx)
    fixture.foundry.pipelines.deploy(
        fixture.pipeline_id,
        str(version["id"]),
        idempotency_key=f"deploy-{fixture.pipeline_id}",
        ctx=fixture.ctx,
    )


def _media_selection_output_graph(
    source_ref: str,
    target_ref: str,
    source_version_ids: tuple[str, ...],
) -> dict[str, object]:
    return _graph(
        [
            _node(
                "source",
                "source",
                "source.media_set",
                {"mediaSetRef": source_ref, "mediaItemVersionIds": list(source_version_ids)},
            ),
            _node("output", "output", "output.media_set", {"mediaSetRef": target_ref}),
        ],
        [_edge("source-output", "source", "media", "output", "media")],
    )


def _media_derivative_output_graph(
    source_ref: str,
    target_ref: str,
    source_version_id: str,
) -> dict[str, object]:
    return _graph(
        [
            _node(
                "source",
                "source",
                "source.media_set",
                {"mediaSetRef": source_ref, "mediaItemVersionIds": [source_version_id]},
            ),
            _node(
                "derivative",
                "transform",
                "transform.media",
                {"processorId": "pdf_text_v1@1"},
            ),
            _node("output", "output", "output.media_set", {"mediaSetRef": target_ref}),
        ],
        [
            _edge("source-derivative", "source", "media", "derivative", "media"),
            _edge("derivative-output", "derivative", "derivatives", "output", "media"),
        ],
    )


def _graph(
    nodes: list[dict[str, object]],
    edges: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "schemaVersion": 2,
        "nodes": nodes,
        "edges": edges,
        "layout": {},
        "outputContract": {"columns": []},
        "tests": [],
        "schedule": None,
    }


def _node(
    node_id: str,
    kind: str,
    descriptor_id: str,
    config: Mapping[str, object],
) -> dict[str, object]:
    return {
        "id": node_id,
        "kind": kind,
        "descriptorId": descriptor_id,
        "specVersion": 1,
        "config": dict(config),
    }


def _edge(
    edge_id: str,
    source_node_id: str,
    source_port_id: str,
    target_node_id: str,
    target_port_id: str,
) -> dict[str, object]:
    return {
        "id": edge_id,
        "sourceNodeId": source_node_id,
        "sourcePortId": source_port_id,
        "targetNodeId": target_node_id,
        "targetPortId": target_port_id,
    }


def _seed_derivative(
    fixture: _MediaOutputFixture,
    source_version_id: str,
    *,
    body: bytes | None,
    security_envelope: dict[str, object] | None = None,
) -> MediaDerivativeRecord:
    spec = ProcessorSpec(
        processor="pdf_text_v1",
        processor_version="1",
        model="pypdf",
        model_version="runtime",
        parameters={},
    )
    spec_hash = _canonical_spec_hash(spec)
    blob_key = None
    content_hash = None
    byte_size = None
    if body is not None:
        blob_key = f"derivatives/{fixture.pipeline_id}/seeded.txt"
        fixture.dependencies.media_storage.write_staged(blob_key, io.BytesIO(body))
        stat = fixture.dependencies.media_storage.stat(blob_key)
        content_hash = stat.content_hash
        byte_size = stat.byte_size
    derivative = MediaDerivativeRecord(
        media_derivative_id=f"mder_{fixture.pipeline_id}",
        tenant_id=fixture.ctx.tenant_id,
        source_media_item_version_id=source_version_id,
        derivative_kind="pdf_text",
        processor_spec_hash=spec_hash,
        processor_name=spec.processor,
        processor_version=spec.processor_version,
        model_name=spec.model,
        model_version=spec.model_version or "",
        params_hash=spec_hash,
        security_envelope=dict(security_envelope or fixture.source_envelopes[0]),
        status="COMMITTED",
        blob_key=blob_key,
        content_hash=content_hash,
        byte_size=byte_size,
        mime_type="text/plain" if body is not None else None,
        created_at="2026-07-17T00:00:00+00:00",
        committed_at="2026-07-17T00:00:00+00:00",
    )
    with fixture.foundry.engine.begin() as transaction:
        existing = fixture.dependencies.media_derivative_repository.create_derivative_or_get_existing(
            transaction=transaction,
            record=derivative,
        )
    assert existing is None
    return derivative


def _direct_committer(
    fixture: _MediaOutputFixture,
) -> tuple[
    PipelineMediaSetOutputCommitter,
    PipelineV2RuntimeNode,
    dict[str, tuple[PipelineV2RuntimeArtifact, ...]],
    Any,
]:
    services = CoreServices.create(fixture.dependencies)
    committer = PipelineMediaSetOutputCommitter(
        engine=fixture.dependencies.engine,
        media_repository=fixture.dependencies.media_repository,
        media_derivative_repository=fixture.dependencies.media_derivative_repository,
        media_storage=fixture.dependencies.media_storage,
        media_catalog=services.media.catalog,
        media_transactions=services.media.transaction,
        media_uploads=services.media.upload,
        ctx=fixture.ctx,
        run_id="run-aborted-generation",
        execution_lease_guard=cast(PipelineExecutionLeaseGuard, _NoopLeaseGuard()),
    )
    node = PipelineV2RuntimeNode(
        node_id="output",
        kind="output",
        descriptor_id="output.media_set",
        spec_version=1,
        runtime_capability="media_output_runtime",
        config={"mediaSetRef": fixture.target_ref},
    )
    source = _runtime_source_artifact(fixture)
    return committer, node, {"media": (source,)}, services.media.transaction


class _NoopLeaseGuard:
    def require_active(self, _transaction: object | None = None) -> None:
        return None


def _runtime_source_artifact(
    fixture: _MediaOutputFixture,
) -> PipelineV2RuntimeArtifact:
    rows = _source_version_rows(fixture)
    items = tuple(
        {
            "mediaItemVersionId": version_id,
            "contentHash": rows[version_id]["content_hash"],
            "securityEnvelope": rows[version_id]["security_envelope"],
        }
        for version_id in fixture.source_version_ids
    )
    return PipelineV2RuntimeArtifact(
        node_id="source",
        descriptor_id="source.media_set",
        spec_version=1,
        port_id="media",
        artifact_kind="media_set_selection",
        plane="media",
        items=items,
        artifact_ref={
            "mediaSetRef": fixture.source_ref,
            "mediaItemVersionIds": list(fixture.source_version_ids),
        },
        manifest={"selectionItemCount": len(items)},
        security_envelope=dict(fixture.source_envelopes[0]),
        status="COMMITTED",
        is_serving=True,
        committed_at="2026-07-17T00:00:00+00:00",
    )


def _media_set_optional(
    fixture: _MediaOutputFixture,
    media_ref: str,
):
    namespace, name = media_ref.split(".", 1)
    with fixture.foundry.engine.begin() as transaction:
        return fixture.dependencies.media_repository.media_set_by_ref(
            transaction=transaction,
            tenant_id=fixture.ctx.tenant_id,
            namespace=namespace,
            name=name,
        )


def _media_set_by_ref(
    fixture: _MediaOutputFixture,
    media_ref: str,
):
    result = _media_set_optional(fixture, media_ref)
    assert result is not None
    return result


def _target_versions(
    fixture: _MediaOutputFixture,
    media_set_id: str,
) -> list[dict[str, Any]]:
    with fixture.foundry.engine.begin() as transaction:
        rows = (
            cast(Any, transaction)
            .execute(
                select(
                    db.media_item_versions,
                    db.media_items.c.logical_path,
                )
                .join(
                    db.media_items,
                    db.media_items.c.id == db.media_item_versions.c.media_item_id,
                )
                .where(
                    db.media_item_versions.c.tenant_id == fixture.ctx.tenant_id,
                    db.media_items.c.media_set_id == media_set_id,
                )
                .order_by(db.media_items.c.logical_path)
            )
            .mappings()
            .all()
        )
    return [dict(row) for row in rows]


def _assert_exact_target_copies(
    fixture: _MediaOutputFixture,
    target_versions: list[dict[str, Any]],
    run: Mapping[str, object],
) -> None:
    source_by_id = _source_version_rows(fixture)
    assert len(target_versions) == len(source_by_id)
    for target in target_versions:
        lineage = cast(dict[str, Any], target["source_ref"])["pipelineOutput"]
        source_id = str(lineage["sourceMediaItemVersionId"])
        source = source_by_id[source_id]
        assert target["content_hash"] == source["content_hash"]
        assert target["byte_size"] == source["byte_size"]
        assert target["security_envelope"] == source["security_envelope"]
        assert target["status"] == "COMMITTED"
        assert lineage["pipelineRunId"] == run["id"]
        assert lineage["pipelineNodeId"] == "output"
        assert lineage["descriptorId"] == "output.media_set"
        assert lineage["specVersion"] == 1
        assert lineage["inputNodeId"] == "source"
        assert lineage["inputArtifactKind"] == "media_set_selection"
        assert lineage["mediaDerivativeId"] is None
        assert isinstance(lineage["inputContentFingerprint"], str)
        assert isinstance(lineage["entryFingerprint"], str)
        assert isinstance(lineage["requestFingerprint"], str)
        with fixture.dependencies.media_storage.open_stream(str(target["blob_key"])) as target_stream:
            with fixture.dependencies.media_storage.open_stream(str(source["blob_key"])) as source_stream:
                assert target_stream.read() == source_stream.read()


def _source_version_rows(
    fixture: _MediaOutputFixture,
) -> dict[str, dict[str, Any]]:
    with fixture.foundry.engine.begin() as transaction:
        rows = (
            cast(Any, transaction)
            .execute(
                select(db.media_item_versions).where(
                    db.media_item_versions.c.tenant_id == fixture.ctx.tenant_id,
                    db.media_item_versions.c.id.in_(fixture.source_version_ids),
                )
            )
            .mappings()
            .all()
        )
    return {str(row["id"]): dict(row) for row in rows}


def _replay_counts(
    fixture: _MediaOutputFixture,
    run_id: str,
    media_set_id: str,
    transaction_id: str,
) -> tuple[int, ...]:
    with fixture.foundry.engine.begin() as transaction:
        sql = cast(Any, transaction)
        node_runs = sql.execute(
            select(func.count())
            .select_from(db.pipeline_node_runs)
            .where(
                db.pipeline_node_runs.c.tenant_id == fixture.ctx.tenant_id,
                db.pipeline_node_runs.c.run_id == run_id,
            )
        ).scalar_one()
        attempts = sql.execute(
            select(func.count())
            .select_from(db.pipeline_node_attempts)
            .join(
                db.pipeline_node_runs,
                db.pipeline_node_runs.c.id == db.pipeline_node_attempts.c.node_run_id,
            )
            .where(
                db.pipeline_node_runs.c.tenant_id == fixture.ctx.tenant_id,
                db.pipeline_node_runs.c.run_id == run_id,
            )
        ).scalar_one()
        artifacts = sql.execute(
            select(func.count())
            .select_from(db.pipeline_run_artifacts)
            .where(
                db.pipeline_run_artifacts.c.tenant_id == fixture.ctx.tenant_id,
                db.pipeline_run_artifacts.c.run_id == run_id,
            )
        ).scalar_one()
        versions = sql.execute(
            select(func.count())
            .select_from(db.media_item_versions)
            .join(db.media_items, db.media_items.c.id == db.media_item_versions.c.media_item_id)
            .where(
                db.media_item_versions.c.tenant_id == fixture.ctx.tenant_id,
                db.media_items.c.media_set_id == media_set_id,
            )
        ).scalar_one()
        transactions = sql.execute(
            select(func.count())
            .select_from(db.media_transactions)
            .where(
                db.media_transactions.c.tenant_id == fixture.ctx.tenant_id,
                db.media_transactions.c.media_set_id == media_set_id,
            )
        ).scalar_one()
        audits = sql.execute(
            select(func.count())
            .select_from(db.audit_events)
            .where(
                db.audit_events.c.tenant_id == fixture.ctx.tenant_id,
                db.audit_events.c.event_type == "media.transaction.committed",
                db.audit_events.c.resource_id == transaction_id,
            )
        ).scalar_one()
        outbox = sql.execute(
            select(func.count())
            .select_from(db.outbox_events)
            .where(
                db.outbox_events.c.tenant_id == fixture.ctx.tenant_id,
                db.outbox_events.c.event_type == "media.transaction.committed",
                db.outbox_events.c.aggregate_id == transaction_id,
            )
        ).scalar_one()
    return tuple(
        int(value)
        for value in (
            node_runs,
            attempts,
            artifacts,
            versions,
            transactions,
            audits,
            outbox,
        )
    )


def _target_transaction_count(
    fixture: _MediaOutputFixture,
    media_set_id: str,
) -> int:
    with fixture.foundry.engine.begin() as transaction:
        count = (
            cast(Any, transaction)
            .execute(
                select(func.count())
                .select_from(db.media_transactions)
                .where(
                    db.media_transactions.c.tenant_id == fixture.ctx.tenant_id,
                    db.media_transactions.c.media_set_id == media_set_id,
                )
            )
            .scalar_one()
        )
    return int(count)


def _target_transactions(
    fixture: _MediaOutputFixture,
    media_set_id: str,
) -> list[dict[str, Any]]:
    with fixture.foundry.engine.begin() as transaction:
        rows = (
            cast(Any, transaction)
            .execute(
                select(db.media_transactions)
                .where(
                    db.media_transactions.c.tenant_id == fixture.ctx.tenant_id,
                    db.media_transactions.c.media_set_id == media_set_id,
                )
                .order_by(db.media_transactions.c.idempotency_key)
            )
            .mappings()
            .all()
        )
    return [dict(row) for row in rows]


def _committed_target_versions(
    fixture: _MediaOutputFixture,
    media_set_id: str,
) -> list[dict[str, Any]]:
    return [row for row in _target_versions(fixture, media_set_id) if row["status"] == "COMMITTED"]


def _run_artifact_count(
    fixture: _MediaOutputFixture,
    run_id: str,
    node_id: str,
) -> int:
    with fixture.foundry.engine.begin() as transaction:
        count = (
            cast(Any, transaction)
            .execute(
                select(func.count())
                .select_from(db.pipeline_run_artifacts)
                .where(
                    db.pipeline_run_artifacts.c.tenant_id == fixture.ctx.tenant_id,
                    db.pipeline_run_artifacts.c.run_id == run_id,
                    db.pipeline_run_artifacts.c.node_id == node_id,
                )
            )
            .scalar_one()
        )
    return int(count)


def _make_pdf(text: str) -> bytes:
    content = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length %d>>stream\n" % len(content) + content + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    output = b"%PDF-1.4\n"
    offsets: list[int] = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(output))
        output += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(output)
    output += f"xref\n0 {len(objects) + 1}\n".encode()
    output += b"0000000000 65535 f \n"
    output += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    return output + f"trailer<</Size {len(objects) + 1}/Root 1 0 R>>\nstartxref\n{xref}\n%%EOF".encode()
