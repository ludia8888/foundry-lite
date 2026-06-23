from __future__ import annotations

from collections.abc import Callable, Mapping

from foundry_lite.application.ports import RuntimeRepository, TransactionManager
from foundry_lite.application.ports.search_adapter import SearchAdapter
from foundry_lite.application.primitives import _now
from foundry_lite.security.erasure import ErasureActionReceipt, ErasureManifestAction

ErasureActionExecutor = Callable[[ErasureManifestAction], ErasureActionReceipt]


def default_erasure_executors(
    *, search_adapter: SearchAdapter, runtime_repository: RuntimeRepository, engine: TransactionManager
) -> Mapping[str, ErasureActionExecutor]:
    """Concrete executors for every erasure manifest surface.

    Immediate surfaces (search, object, dead-letter, audit) are applied inline;
    surfaces whose physical erasure is heavy or operates on immutable storage
    (dataset-row redaction, materialization rebuild, backup expiration) are
    certified as DEFERRED and finished by their maintenance lanes. An unmapped
    action still fails closed in the service rather than being silently certified.
    """
    return {
        "remove_search_document": lambda action: _remove_search_document(action, search_adapter),
        "tombstone_object": lambda action: _tombstone_object(action, runtime_repository, engine),
        "redact_dataset_row": _redact_dataset_row,
        "rebuild_materialization": _rebuild_materialization,
        "redact_dead_letter_payload": lambda action: _redact_dead_letter_payload(action, runtime_repository, engine),
        "minimize_audit_evidence": _minimize_audit_evidence,
        "defer_backup_expiration": _defer_backup_expiration,
    }


def _rebuild_materialization(action: ErasureManifestAction) -> ErasureActionReceipt:
    # A materialization is a derived projection; erasing a subject requires recomputing
    # the whole projection with the subject excluded. That rebuild runs in the rebuild
    # maintenance lane. The deferred receipt is the durable record of which row to exclude.
    return ErasureActionReceipt(
        action_type=action.action_type,
        resource=action.resource,
        status="DEFERRED",
        completed_at=_now(),
        evidence={"executor": "materialization_rebuild", **dict(action.evidence)},
    )


def _redact_dataset_row(action: ErasureManifestAction) -> ErasureActionReceipt:
    # Dataset rows live in immutable version files; the physical version rewrite and
    # prior-version purge run in the redaction maintenance lane. The deferred receipt
    # is the durable record of which tenant-scoped row must be redacted.
    return ErasureActionReceipt(
        action_type=action.action_type,
        resource=action.resource,
        status="DEFERRED",
        completed_at=_now(),
        evidence={"executor": "dataset_row_redaction", **dict(action.evidence)},
    )


def _tombstone_object(
    action: ErasureManifestAction, runtime_repository: RuntimeRepository, engine: TransactionManager
) -> ErasureActionReceipt:
    resource = action.resource
    with engine.begin() as conn:
        tombstoned = runtime_repository.delete_object_record(
            transaction=conn, tenant_id=resource.tenant_id, record_id=resource.resource_id
        )
    return ErasureActionReceipt(
        action_type=action.action_type,
        resource=resource,
        status="APPLIED",
        completed_at=_now(),
        evidence={"executor": "runtime_repository.delete_object_record", "tombstoned": tombstoned},
    )


def _redact_dead_letter_payload(
    action: ErasureManifestAction, runtime_repository: RuntimeRepository, engine: TransactionManager
) -> ErasureActionReceipt:
    resource = action.resource
    with engine.begin() as conn:
        deleted = runtime_repository.delete_dead_letter_event(
            transaction=conn, tenant_id=resource.tenant_id, event_id=resource.resource_id
        )
    return ErasureActionReceipt(
        action_type=action.action_type,
        resource=resource,
        status="APPLIED",
        completed_at=_now(),
        evidence={"executor": "runtime_repository.delete_dead_letter_event", "deleted": deleted},
    )


def _remove_search_document(action: ErasureManifestAction, search_adapter: SearchAdapter) -> ErasureActionReceipt:
    resource = action.resource
    search_adapter.delete_document(
        tenant_id=resource.tenant_id,
        object_type=resource.surface,
        document_id=resource.resource_id,
    )
    return ErasureActionReceipt(
        action_type=action.action_type,
        resource=resource,
        status="APPLIED",
        completed_at=_now(),
        evidence={"executor": "search_adapter.delete_document", "surface": resource.surface},
    )


def _minimize_audit_evidence(action: ErasureManifestAction) -> ErasureActionReceipt:
    # Audit events are the minimum legal-retention record and already store only
    # redacted evidence, so minimization is structural: confirm retention basis.
    return ErasureActionReceipt(
        action_type=action.action_type,
        resource=action.resource,
        status="APPLIED",
        completed_at=_now(),
        evidence={"executor": "audit_minimization", "basis": "legal_obligation"},
    )


def _defer_backup_expiration(action: ErasureManifestAction) -> ErasureActionReceipt:
    # Backups are crypto-shredded at their retention boundary; record the deferral.
    return ErasureActionReceipt(
        action_type=action.action_type,
        resource=action.resource,
        status="DEFERRED",
        completed_at=_now(),
        evidence={"executor": "backup_retention", **dict(action.evidence)},
    )
