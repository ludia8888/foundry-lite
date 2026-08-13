from __future__ import annotations

from dataclasses import replace

import pytest
from foundry_lite.application.ports.release_delivery_repository import (
    ClaimReleaseDeliveryCommand,
    CompleteReleaseDeliveryCommand,
    PrepareReleaseDeliveryCommand,
    ReconcileReleaseDeliveryCommand,
    ReleaseDeliveryIdempotencyConflict,
    ReleaseDeliveryOperation,
    ReleaseDeliveryRepository,
    ReleaseDeliveryTerminalConflict,
    ReleaseDeliveryTerminalStatus,
)
from foundry_lite.application.ports.transaction_context import TransactionContext
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.release_delivery_repository import (
    SqlAlchemyReleaseDeliveryRepository,
)
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

NOW = "2026-08-09T09:00:00Z"
LATER = "2026-08-09T09:00:01Z"
DONE = "2026-08-09T09:00:02Z"


def test_prepare_distinguishes_new_exact_replay_and_fingerprint_conflict() -> None:
    repository, engine = _repository()
    with engine.begin() as transaction:
        created, is_created = repository.prepare(transaction=transaction, command=_prepare())
        replayed, is_replay_created = repository.prepare(
            transaction=transaction,
            command=replace(
                _prepare(),
                delivery_id="delivery-replay",
                request_id="request-retry",
                created_at=LATER,
                updated_at=LATER,
            ),
        )
        with pytest.raises(ReleaseDeliveryIdempotencyConflict):
            repository.prepare(
                transaction=transaction,
                command=replace(
                    _prepare(),
                    delivery_id="delivery-conflict",
                    request_fingerprint="sha256:different",
                ),
            )
        with pytest.raises(ReleaseDeliveryIdempotencyConflict):
            repository.prepare(
                transaction=transaction,
                command=replace(
                    _prepare(),
                    delivery_id="delivery-binding-conflict",
                    ai_run_id="ai-run-other",
                    binding_hash="sha256:other-binding",
                ),
            )
        with pytest.raises(ReleaseDeliveryIdempotencyConflict):
            repository.prepare(
                transaction=transaction,
                command=replace(
                    _prepare(),
                    delivery_id="delivery-lineage-conflict",
                    parent_delivery_id="delivery-other-parent",
                ),
            )

    assert is_created is True
    assert is_replay_created is False
    assert created.delivery_id == "delivery-a"
    assert replayed.delivery_id == created.delivery_id
    assert replayed.status == "prepared"
    assert replayed.application_id == "application-a"
    assert replayed.release_kind == "pipeline"
    assert replayed.workflow_run_id == "ai-run-9"
    assert replayed.parent_delivery_id == "delivery-root"


def test_source_publish_is_a_first_class_durable_delivery_operation() -> None:
    repository, engine = _repository()
    with engine.begin() as transaction:
        row, is_created = repository.prepare(
            transaction=transaction,
            command=_prepare(operation="source_publish"),
        )

    assert is_created is True
    assert row.operation == "source_publish"
    assert row.status == "prepared"


def test_lookup_and_lists_are_tenant_scoped() -> None:
    repository, engine = _repository()
    with engine.begin() as transaction:
        repository.prepare(transaction=transaction, command=_prepare())
        repository.prepare(
            transaction=transaction,
            command=_prepare(
                delivery_id="delivery-b",
                tenant_id="tenant-b",
                proposal_id="proposal-b",
            ),
        )
        wrong_tenant = repository.get(
            transaction=transaction,
            tenant_id="tenant-b",
            delivery_id="delivery-a",
        )
        tenant_a_rows = repository.list_for_proposal(
            transaction=transaction,
            tenant_id="tenant-a",
            proposal_id="proposal-a",
            limit=20,
        )
        tenant_b_reconcile = repository.list_by_statuses(
            transaction=transaction,
            tenant_id="tenant-b",
            statuses=("prepared",),
            limit=20,
        )

    assert wrong_tenant is None
    assert [row.delivery_id for row in tenant_a_rows] == ["delivery-a"]
    assert [row.delivery_id for row in tenant_b_reconcile] == ["delivery-b"]


def test_workflow_list_is_tenant_application_and_root_scoped_in_chain_order() -> None:
    repository, engine = _repository()
    commands = (
        _prepare(
            delivery_id="delivery-root",
            operation="source_publish",
            idempotency_key="workflow-root",
            created_at=NOW,
        ),
        _prepare(
            delivery_id="delivery-merge",
            operation="source_merge",
            idempotency_key="workflow-merge",
            parent_delivery_id="delivery-root",
            created_at=LATER,
        ),
        _prepare(
            delivery_id="delivery-deploy",
            operation="application_deploy",
            idempotency_key="workflow-deploy",
            parent_delivery_id="delivery-merge",
            created_at=DONE,
        ),
        _prepare(
            delivery_id="foreign-application",
            application_id="application-b",
            operation="source_publish",
            idempotency_key="foreign-application",
        ),
        _prepare(
            delivery_id="foreign-tenant",
            tenant_id="tenant-b",
            operation="source_publish",
            idempotency_key="foreign-tenant",
        ),
        _prepare(
            delivery_id="foreign-workflow",
            operation="source_publish",
            workflow_run_id="ai-run-other",
            ai_run_id="ai-run-other",
            idempotency_key="foreign-workflow",
        ),
    )
    with engine.begin() as transaction:
        for command in reversed(commands):
            repository.prepare(transaction=transaction, command=command)
        rows = repository.list_for_workflow(
            transaction=transaction,
            tenant_id="tenant-a",
            application_id="application-a",
            workflow_run_id="ai-run-9",
            limit=20,
        )
        bounded = repository.list_for_workflow(
            transaction=transaction,
            tenant_id="tenant-a",
            application_id="application-a",
            workflow_run_id="ai-run-9",
            limit=2,
        )

    assert [row.delivery_id for row in rows] == ["delivery-root", "delivery-merge", "delivery-deploy"]
    assert [row.delivery_id for row in bounded] == ["delivery-root", "delivery-merge"]


def test_workflow_root_list_is_bounded_to_landed_application_and_release_kind() -> None:
    repository, engine = _repository()
    old_root = _prepare(
        delivery_id="pipeline-root-old",
        operation="source_publish",
        workflow_run_id="pipeline-run-old",
        ai_run_id="pipeline-run-old",
        idempotency_key="pipeline-root-old",
        created_at=NOW,
    )
    new_root = replace(
        old_root,
        delivery_id="pipeline-root-new",
        workflow_run_id="pipeline-run-new",
        ai_run_id="pipeline-run-new",
        idempotency_key="pipeline-root-new",
        created_at=LATER,
        updated_at=LATER,
    )
    ontology_root = replace(
        old_root,
        delivery_id="ontology-root",
        release_kind="ontology",
        workflow_run_id="ontology-run",
        ai_run_id="ontology-run",
        idempotency_key="ontology-root",
    )
    with engine.begin() as transaction:
        for command in (old_root, new_root, ontology_root):
            _land_delivery(repository, transaction, command)
        pipeline = repository.list_workflow_roots(
            transaction=transaction,
            tenant_id="tenant-a",
            application_id="application-a",
            release_kind="pipeline",
            limit=1,
        )
        ontology = repository.list_workflow_roots(
            transaction=transaction,
            tenant_id="tenant-a",
            application_id="application-a",
            release_kind="ontology",
            limit=5,
        )

    assert [row.delivery_id for row in pipeline] == ["pipeline-root-new"]
    assert [row.delivery_id for row in ontology] == ["ontology-root"]


def test_dispatch_and_completion_are_fenced_compare_and_set_operations() -> None:
    repository, engine = _repository()
    with engine.begin() as transaction:
        repository.prepare(transaction=transaction, command=_prepare())
        claimed = repository.claim_dispatch(transaction=transaction, command=_claim())
        duplicate_claim = repository.claim_dispatch(transaction=transaction, command=_claim())
        stale_complete = repository.complete(
            transaction=transaction,
            command=replace(_complete(), expected_attempt=0),
        )
        completed = repository.complete(transaction=transaction, command=_complete())

    assert claimed is not None
    assert claimed.status == "dispatching"
    assert claimed.execution_attempt == 1
    assert duplicate_claim is None
    assert stale_complete is None
    assert completed is not None
    completed_record, is_transitioned = completed
    assert is_transitioned is True
    assert completed_record.status == "landed"
    assert completed_record.provider_resource_id == "resource-42"


def test_exact_terminal_replay_is_read_only_and_mismatched_payload_conflicts() -> None:
    repository, engine = _repository()
    command = _complete()
    with engine.begin() as transaction:
        repository.prepare(transaction=transaction, command=_prepare())
        repository.claim_dispatch(transaction=transaction, command=_claim())
        first = repository.complete(transaction=transaction, command=command)
        replay = repository.complete(
            transaction=transaction,
            command=replace(
                command,
                completed_at="2026-08-09T09:00:09Z",
                updated_at="2026-08-09T09:00:09Z",
            ),
        )
        with pytest.raises(ReleaseDeliveryTerminalConflict):
            repository.complete(
                transaction=transaction,
                command=replace(command, result_ref={"commitSha": "different"}),
            )

    assert first is not None and first[1] is True
    assert replay is not None and replay[1] is False
    assert replay[0].result_ref == {"commitSha": "abc123"}


def test_ambiguous_delivery_reconciles_once_to_remote_lookup_truth() -> None:
    repository, engine = _repository()
    ambiguous = replace(
        _complete(),
        terminal_status="ambiguous",
        provider_resource_id=None,
        result_ref=None,
        error_ref={"code": "provider_timeout"},
    )
    reconciled_command = ReconcileReleaseDeliveryCommand(
        tenant_id="tenant-a",
        delivery_id="delivery-a",
        expected_status="ambiguous",
        expected_attempt=1,
        terminal_status="landed",
        provider_operation_id="operation-7",
        provider_resource_id="resource-42",
        prior_resource_id="resource-41",
        result_ref={"lookup": "landed"},
        error_ref=None,
        completed_at="2026-08-09T09:01:00Z",
        updated_at="2026-08-09T09:01:00Z",
    )
    with engine.begin() as transaction:
        repository.prepare(transaction=transaction, command=_prepare())
        repository.claim_dispatch(transaction=transaction, command=_claim())
        completed = repository.complete(transaction=transaction, command=ambiguous)
        pending = repository.list_by_statuses(
            transaction=transaction,
            tenant_id="tenant-a",
            statuses=("dispatching", "ambiguous"),
            limit=20,
        )
        reconciled = repository.reconcile(transaction=transaction, command=reconciled_command)
        replay = repository.reconcile(
            transaction=transaction,
            command=replace(
                reconciled_command,
                completed_at="2026-08-09T09:01:01Z",
                updated_at="2026-08-09T09:01:01Z",
            ),
        )
        with pytest.raises(ReleaseDeliveryTerminalConflict):
            repository.reconcile(
                transaction=transaction,
                command=replace(reconciled_command, terminal_status="absent"),
            )

    assert completed is not None and completed[0].status == "ambiguous"
    assert [row.delivery_id for row in pending] == ["delivery-a"]
    assert reconciled is not None and reconciled[1] is True
    assert reconciled[0].status == "landed"
    assert replay is not None and replay[1] is False


def _repository() -> tuple[ReleaseDeliveryRepository, Engine]:
    engine = create_engine("sqlite:///:memory:", future=True)
    db.metadata.create_all(engine)
    return SqlAlchemyReleaseDeliveryRepository(engine), engine


def _land_delivery(
    repository: ReleaseDeliveryRepository,
    transaction: TransactionContext,
    command: PrepareReleaseDeliveryCommand,
) -> None:
    repository.prepare(transaction=transaction, command=command)
    repository.claim_dispatch(
        transaction=transaction,
        command=replace(_claim(), delivery_id=command.delivery_id),
    )
    repository.complete(
        transaction=transaction,
        command=replace(_complete(), delivery_id=command.delivery_id, prior_resource_id=None),
    )


def _prepare(
    *,
    delivery_id: str = "delivery-a",
    tenant_id: str = "tenant-a",
    application_id: str = "application-a",
    proposal_id: str = "proposal-a",
    operation: ReleaseDeliveryOperation = "source_merge",
    workflow_run_id: str = "ai-run-9",
    parent_delivery_id: str | None = None,
    ai_run_id: str = "ai-run-9",
    idempotency_key: str = "delivery-key",
    created_at: str = NOW,
) -> PrepareReleaseDeliveryCommand:
    return PrepareReleaseDeliveryCommand(
        delivery_id=delivery_id,
        tenant_id=tenant_id,
        application_id=application_id,
        proposal_id=proposal_id,
        release_kind="pipeline",
        workflow_run_id=workflow_run_id,
        parent_delivery_id=(None if operation == "source_publish" else parent_delivery_id or "delivery-root"),
        provider="github",
        operation=operation,
        target_ref={"repository": "acme/platform", "branch": "main"},
        candidate_ref={"branch": "release/candidate"},
        environment="production",
        idempotency_key=idempotency_key,
        request_fingerprint="sha256:request",
        prior_resource_id="resource-41",
        ai_run_id=ai_run_id,
        binding_hash="sha256:binding",
        request_id="request-9",
        created_by="reviewer-7",
        created_at=created_at,
        updated_at=created_at,
    )


def _claim() -> ClaimReleaseDeliveryCommand:
    return ClaimReleaseDeliveryCommand(
        tenant_id="tenant-a",
        delivery_id="delivery-a",
        expected_status="prepared",
        expected_attempt=0,
        dispatch_started_at=LATER,
        updated_at=LATER,
    )


def _complete(
    *,
    terminal_status: ReleaseDeliveryTerminalStatus = "landed",
) -> CompleteReleaseDeliveryCommand:
    return CompleteReleaseDeliveryCommand(
        tenant_id="tenant-a",
        delivery_id="delivery-a",
        expected_status="dispatching",
        expected_attempt=1,
        terminal_status=terminal_status,
        provider_operation_id="operation-7",
        provider_resource_id="resource-42",
        prior_resource_id="resource-41",
        result_ref={"commitSha": "abc123"},
        error_ref=None,
        completed_at=DONE,
        updated_at=DONE,
    )
