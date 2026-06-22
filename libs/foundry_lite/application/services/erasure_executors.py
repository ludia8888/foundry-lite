from __future__ import annotations

from collections.abc import Callable, Mapping

from foundry_lite.application.ports.search_adapter import SearchAdapter
from foundry_lite.application.primitives import _now
from foundry_lite.security.erasure import ErasureActionReceipt, ErasureManifestAction

ErasureActionExecutor = Callable[[ErasureManifestAction], ErasureActionReceipt]


def default_erasure_executors(*, search_adapter: SearchAdapter) -> Mapping[str, ErasureActionExecutor]:
    """Concrete executors for the serving/projection surfaces.

    Surfaces that require richer source context (dataset-row redaction, object
    tombstones, materialization rebuild, dead-letter redaction) are added on top
    of this map as their executors land; an unmapped action fails closed in the
    service rather than being silently certified.
    """
    return {
        "remove_search_document": lambda action: _remove_search_document(action, search_adapter),
        "minimize_audit_evidence": _minimize_audit_evidence,
        "defer_backup_expiration": _defer_backup_expiration,
    }


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
