"""Plan tenant-scoped right-to-erasure work without storing raw subject values."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Literal, cast

ErasureResourceType = Literal[
    "dataset_row",
    "object_record",
    "search_document",
    "materialization_row",
    "dead_letter_record",
    "backup_snapshot",
    "audit_event",
]
ErasureActionType = Literal[
    "redact_dataset_row",
    "tombstone_object",
    "remove_search_document",
    "rebuild_materialization",
    "redact_dead_letter_payload",
    "defer_backup_expiration",
    "minimize_audit_evidence",
]
ErasureActionStatus = Literal["READY", "DEFERRED"]
ErasureManifestStatus = Literal["READY_TO_APPLY", "PENDING_RETENTION"]
ErasureReceiptStatus = Literal["APPLIED", "DEFERRED"]
ErasureCertificateStatus = Literal["CERTIFIED", "CERTIFIED_WITH_DEFERRED_WORK"]

_ERASURE_MANIFEST_NAMESPACE: Final = uuid.UUID("a8019d04-44b6-41d6-b9f8-a42e833e3878")
_PROTECTED_VALUE: Final = "***PROTECTED***"


@dataclass(frozen=True)
class ErasureRequest:
    """A privacy subject erasure request with idempotent, redacted evidence."""

    tenant_id: str
    request_id: str
    subject_kind: str
    subject_value: object
    requested_by: str
    idempotency_key: str
    reason: str
    requested_at: str
    subject_token_secret: str = field(repr=False)
    subject_token_key_id: str = "local-erasure-key"

    @property
    def subject_hash(self) -> str:
        """Return the stable proof key for the protected subject value."""

        payload = {
            "tenantId": self.tenant_id,
            "subjectKind": self.subject_kind,
            "subjectValue": self.subject_value,
        }
        return _subject_hmac_token(
            payload,
            secret=self.subject_token_secret,
            key_id=self.subject_token_key_id,
        )

    def redacted_evidence(self) -> dict[str, object]:
        """Return operator evidence without exposing the raw subject value."""

        return {
            "tenantId": self.tenant_id,
            "requestId": self.request_id,
            "subjectKind": self.subject_kind,
            "subjectValue": _PROTECTED_VALUE,
            "subjectHash": self.subject_hash,
            "subjectTokenKeyId": self.subject_token_key_id,
            "requestedBy": self.requested_by,
            "idempotencyKey": self.idempotency_key,
            "reason": self.reason,
            "requestedAt": self.requested_at,
        }


@dataclass(frozen=True)
class ErasureResourceRef:
    """A tenant-scoped record that must be erased, tombstoned, or rebuilt."""

    tenant_id: str
    resource_type: ErasureResourceType
    resource_id: str
    surface: str

    def manifest_payload(self) -> dict[str, object]:
        """Return the stable resource payload used in manifest hashing."""

        return {
            "tenantId": self.tenant_id,
            "resourceType": self.resource_type,
            "resourceId": self.resource_id,
            "surface": self.surface,
        }


@dataclass(frozen=True)
class ErasureResolution:
    """The resolved resources touched by one erasure request."""

    request: ErasureRequest
    resources: tuple[ErasureResourceRef, ...]

    def __post_init__(self) -> None:
        """Reject cross-tenant resource matches before a manifest is built."""

        mismatched = tuple(ref for ref in self.resources if ref.tenant_id != self.request.tenant_id)
        if mismatched:
            raise ValueError("erasure resolution cannot include resources from another tenant")


@dataclass(frozen=True)
class ErasureRetentionPolicy:
    """Retention rules for backups and the minimum legal audit record."""

    backup_retention_until: str
    crypto_shredding_key_ref: str
    audit_retention_basis: str = "legal_obligation"
    minimum_audit_fields: tuple[str, ...] = ("tenant_id", "request_id", "subject_hash", "action", "created_at")


@dataclass(frozen=True)
class ErasureManifestAction:
    """A single planned erasure action for one storage or projection surface."""

    action_type: ErasureActionType
    resource: ErasureResourceRef
    status: ErasureActionStatus
    reason: str
    evidence: Mapping[str, object]

    def redacted_evidence(self) -> dict[str, object]:
        """Return action evidence safe to store in operator artifacts."""

        return {
            "actionType": self.action_type,
            "resource": self.resource.manifest_payload(),
            "status": self.status,
            "reason": self.reason,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class ErasureManifest:
    """An idempotent erasure plan across serving, projection, and backup surfaces."""

    manifest_id: str
    tenant_id: str
    request_id: str
    subject_hash: str
    subject_token_key_id: str
    status: ErasureManifestStatus
    actions: tuple[ErasureManifestAction, ...]

    def redacted_evidence(self) -> dict[str, object]:
        """Return the full manifest payload without raw subject data."""

        return {
            "manifestId": self.manifest_id,
            "tenantId": self.tenant_id,
            "requestId": self.request_id,
            "subjectHash": self.subject_hash,
            "subjectTokenKeyId": self.subject_token_key_id,
            "status": self.status,
            "actions": [action.redacted_evidence() for action in self.actions],
        }


@dataclass(frozen=True)
class ErasureActionReceipt:
    """Executor receipt for one erasure manifest action."""

    action_type: ErasureActionType
    resource: ErasureResourceRef
    status: ErasureReceiptStatus
    completed_at: str
    evidence: Mapping[str, object]

    def redacted_evidence(self) -> dict[str, object]:
        return {
            "actionType": self.action_type,
            "resource": self.resource.manifest_payload(),
            "status": self.status,
            "completedAt": self.completed_at,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class ErasureCertificate:
    """Raw-subject-free proof that a manifest was applied or safely deferred."""

    certificate_id: str
    manifest_id: str
    tenant_id: str
    request_id: str
    subject_hash: str
    subject_token_key_id: str
    status: ErasureCertificateStatus
    certified_at: str
    certified_by: str
    receipts: tuple[ErasureActionReceipt, ...]

    def redacted_evidence(self) -> dict[str, object]:
        return {
            "certificateId": self.certificate_id,
            "manifestId": self.manifest_id,
            "tenantId": self.tenant_id,
            "requestId": self.request_id,
            "subjectHash": self.subject_hash,
            "subjectTokenKeyId": self.subject_token_key_id,
            "status": self.status,
            "certifiedAt": self.certified_at,
            "certifiedBy": self.certified_by,
            "receipts": [receipt.redacted_evidence() for receipt in self.receipts],
        }


def resolve_erasure_subject(
    request: ErasureRequest,
    records: Sequence[Mapping[str, object]],
    *,
    identity_fields: Sequence[str],
    resource_type: ErasureResourceType,
    surface: str,
    id_field: str = "id",
    tenant_field: str = "tenant_id",
) -> ErasureResolution:
    """Resolve subject matches from one tenant-scoped surface into resources."""

    resources = tuple(
        ErasureResourceRef(
            tenant_id=str(record[tenant_field]),
            resource_type=resource_type,
            resource_id=str(record[id_field]),
            surface=surface,
        )
        for record in records
        if _record_matches_subject(
            request,
            record,
            identity_fields=identity_fields,
            id_field=id_field,
            tenant_field=tenant_field,
        )
    )
    return ErasureResolution(request=request, resources=resources)


def build_erasure_manifest(
    request: ErasureRequest,
    resolutions: Sequence[ErasureResolution],
    *,
    retention_policy: ErasureRetentionPolicy,
) -> ErasureManifest:
    """Build a stable manifest that records immediate and deferred erasure work."""

    resources = _resources_for_request(request, resolutions)
    actions = tuple(
        sorted(
            (
                *(_action_for_resource(request, resource, retention_policy=retention_policy) for resource in resources),
                _audit_minimization_action(request, retention_policy=retention_policy),
            ),
            key=_action_sort_key,
        )
    )
    status: ErasureManifestStatus = "PENDING_RETENTION" if _has_deferred_action(actions) else "READY_TO_APPLY"
    return ErasureManifest(
        manifest_id=_manifest_id(request, actions=actions, retention_policy=retention_policy),
        tenant_id=request.tenant_id,
        request_id=request.request_id,
        subject_hash=request.subject_hash,
        subject_token_key_id=request.subject_token_key_id,
        status=status,
        actions=actions,
    )


def build_erasure_certificate(
    manifest: ErasureManifest,
    receipts: Sequence[ErasureActionReceipt],
    *,
    certified_at: str,
    certified_by: str,
) -> ErasureCertificate:
    """Build a certificate only when every manifest action has a matching receipt."""

    sorted_receipts = tuple(sorted(receipts, key=_receipt_sort_key))
    _validate_action_receipts(manifest.actions, sorted_receipts)
    status: ErasureCertificateStatus = (
        "CERTIFIED_WITH_DEFERRED_WORK"
        if any(receipt.status == "DEFERRED" for receipt in sorted_receipts)
        else "CERTIFIED"
    )
    return ErasureCertificate(
        certificate_id=_certificate_id(manifest, sorted_receipts, certified_at=certified_at, certified_by=certified_by),
        manifest_id=manifest.manifest_id,
        tenant_id=manifest.tenant_id,
        request_id=manifest.request_id,
        subject_hash=manifest.subject_hash,
        subject_token_key_id=manifest.subject_token_key_id,
        status=status,
        certified_at=certified_at,
        certified_by=certified_by,
        receipts=sorted_receipts,
    )


def is_erased_resource(
    manifest: ErasureManifest,
    *,
    resource_type: ErasureResourceType,
    resource_id: str,
) -> bool:
    """Return whether a rebuild should exclude a resource already in the manifest."""

    return any(
        action.resource.resource_type == resource_type and action.resource.resource_id == resource_id
        for action in manifest.actions
        if action.action_type != "minimize_audit_evidence"
    )


def deferred_redaction_targets(
    certificate_evidence: Mapping[str, object],
    *,
    action_type: ErasureActionType,
) -> tuple[ErasureResourceRef, ...]:
    """Return the resources a maintenance lane still owes physical erasure for.

    A persisted certificate records deferred surfaces (dataset-row redaction,
    materialization rebuild) as DEFERRED receipts; their physical work runs later
    in a maintenance lane. This reads one certificate's raw-value-free evidence and
    returns the tenant-scoped resources for one deferred ``action_type``.
    """

    receipts = certificate_evidence.get("receipts")
    if not isinstance(receipts, list):
        return ()
    targets: list[ErasureResourceRef] = []
    for entry in cast("list[object]", receipts):
        if not isinstance(entry, Mapping):
            continue
        receipt = cast("Mapping[str, object]", entry)
        if receipt.get("actionType") != action_type or receipt.get("status") != "DEFERRED":
            continue
        resource = receipt.get("resource")
        if isinstance(resource, Mapping):
            targets.append(_resource_ref_from_payload(cast("Mapping[str, object]", resource)))
    return tuple(targets)


def _resource_ref_from_payload(payload: Mapping[str, object]) -> ErasureResourceRef:
    return ErasureResourceRef(
        tenant_id=str(payload["tenantId"]),
        resource_type=cast(ErasureResourceType, payload["resourceType"]),
        resource_id=str(payload["resourceId"]),
        surface=str(payload["surface"]),
    )


def _record_matches_subject(
    request: ErasureRequest,
    record: Mapping[str, object],
    *,
    identity_fields: Sequence[str],
    id_field: str,
    tenant_field: str,
) -> bool:
    """Match one candidate record without crossing tenant boundaries."""

    if record.get(tenant_field) != request.tenant_id or id_field not in record:
        return False
    return any(record.get(field_name) == request.subject_value for field_name in identity_fields)


def _resources_for_request(
    request: ErasureRequest,
    resolutions: Sequence[ErasureResolution],
) -> tuple[ErasureResourceRef, ...]:
    """Merge already tenant-checked resolutions for the same request."""

    resources: list[ErasureResourceRef] = []
    for resolution in resolutions:
        if resolution.request != request:
            raise ValueError("erasure manifest cannot mix resolutions for different requests")
        resources.extend(resolution.resources)
    return tuple(sorted(resources, key=_resource_sort_key))


def _action_for_resource(
    request: ErasureRequest,
    resource: ErasureResourceRef,
    *,
    retention_policy: ErasureRetentionPolicy,
) -> ErasureManifestAction:
    """Map a resource to the concrete action the erasure executor would apply."""

    if resource.resource_type == "backup_snapshot":
        return _backup_deferred_action(request, resource, retention_policy=retention_policy)
    if resource.resource_type == "dataset_row":
        return _dataset_row_deferred_action(request, resource)
    if resource.resource_type == "materialization_row":
        return _materialization_deferred_action(request, resource)
    return ErasureManifestAction(
        action_type=_action_type(resource.resource_type),
        resource=resource,
        status="READY",
        reason="subject_erasure_requested",
        evidence={
            "requestId": request.request_id,
            "subjectHash": request.subject_hash,
            "subjectTokenKeyId": request.subject_token_key_id,
        },
    )


def _backup_deferred_action(
    request: ErasureRequest,
    resource: ErasureResourceRef,
    *,
    retention_policy: ErasureRetentionPolicy,
) -> ErasureManifestAction:
    """Record why backups wait for retention expiry instead of immediate deletion."""

    return ErasureManifestAction(
        action_type="defer_backup_expiration",
        resource=resource,
        status="DEFERRED",
        reason="backup_retention_window_active",
        evidence={
            "requestId": request.request_id,
            "subjectHash": request.subject_hash,
            "subjectTokenKeyId": request.subject_token_key_id,
            "retentionUntil": retention_policy.backup_retention_until,
            "cryptoShreddingKeyRef": retention_policy.crypto_shredding_key_ref,
        },
    )


def _dataset_row_deferred_action(request: ErasureRequest, resource: ErasureResourceRef) -> ErasureManifestAction:
    """Defer the physical dataset-row rewrite to the redaction maintenance lane.

    Dataset rows live in immutable version files, so erasing one requires a version
    rewrite plus a purge of the prior version's data. That work runs in a separate
    maintenance lane; the deferred action is the raw-value-free durable record of
    which tenant-scoped row must be redacted.
    """

    return ErasureManifestAction(
        action_type="redact_dataset_row",
        resource=resource,
        status="DEFERRED",
        reason="deferred_to_redaction_maintenance",
        evidence={
            "requestId": request.request_id,
            "subjectHash": request.subject_hash,
            "subjectTokenKeyId": request.subject_token_key_id,
        },
    )


def _materialization_deferred_action(request: ErasureRequest, resource: ErasureResourceRef) -> ErasureManifestAction:
    """Defer the materialization rebuild to the rebuild maintenance lane.

    A materialization is a derived projection, so erasing a subject's row requires
    recomputing the whole projection with the subject excluded. That rebuild runs in
    a separate maintenance lane; the deferred action is the raw-value-free durable
    record of which tenant-scoped row the rebuild must exclude.
    """

    return ErasureManifestAction(
        action_type="rebuild_materialization",
        resource=resource,
        status="DEFERRED",
        reason="deferred_to_rebuild_maintenance",
        evidence={
            "requestId": request.request_id,
            "subjectHash": request.subject_hash,
            "subjectTokenKeyId": request.subject_token_key_id,
        },
    )


def _audit_minimization_action(
    request: ErasureRequest,
    *,
    retention_policy: ErasureRetentionPolicy,
) -> ErasureManifestAction:
    """Retain only the minimum legal audit evidence for the erasure request."""

    return ErasureManifestAction(
        action_type="minimize_audit_evidence",
        resource=ErasureResourceRef(
            tenant_id=request.tenant_id,
            resource_type="audit_event",
            resource_id=request.request_id,
            surface="audit_events",
        ),
        status="READY",
        reason="retain_minimum_legal_audit_evidence",
        evidence={
            "requestId": request.request_id,
            "subjectHash": request.subject_hash,
            "subjectTokenKeyId": request.subject_token_key_id,
            "auditRetentionBasis": retention_policy.audit_retention_basis,
            "minimumFields": list(retention_policy.minimum_audit_fields),
        },
    )


def _action_type(resource_type: ErasureResourceType) -> ErasureActionType:
    """Translate a resource class into its manifest action name."""

    action_types: dict[ErasureResourceType, ErasureActionType] = {
        "dataset_row": "redact_dataset_row",
        "object_record": "tombstone_object",
        "search_document": "remove_search_document",
        "materialization_row": "rebuild_materialization",
        "dead_letter_record": "redact_dead_letter_payload",
        "backup_snapshot": "defer_backup_expiration",
        "audit_event": "minimize_audit_evidence",
    }
    return action_types[resource_type]


def _manifest_id(
    request: ErasureRequest,
    *,
    actions: Sequence[ErasureManifestAction],
    retention_policy: ErasureRetentionPolicy,
) -> str:
    """Derive the retry-stable manifest id from redacted request evidence."""

    payload = {
        "request": request.redacted_evidence(),
        "actions": [action.redacted_evidence() for action in actions],
        "retention": {
            "backupRetentionUntil": retention_policy.backup_retention_until,
            "cryptoShreddingKeyRef": retention_policy.crypto_shredding_key_ref,
            "auditRetentionBasis": retention_policy.audit_retention_basis,
            "minimumAuditFields": list(retention_policy.minimum_audit_fields),
        },
    }
    return "erase_manifest_" + str(uuid.uuid5(_ERASURE_MANIFEST_NAMESPACE, _json_dumps(payload)))


def _has_deferred_action(actions: Sequence[ErasureManifestAction]) -> bool:
    """Return whether any action must wait for a retention window."""

    return any(action.status == "DEFERRED" for action in actions)


def _resource_sort_key(resource: ErasureResourceRef) -> tuple[str, str, str, str]:
    """Sort resources deterministically before manifest hashing."""

    return (resource.resource_type, resource.surface, resource.resource_id, resource.tenant_id)


def _action_sort_key(action: ErasureManifestAction) -> tuple[str, str, str, str]:
    """Sort actions deterministically before manifest hashing."""

    return (action.action_type, action.resource.resource_type, action.resource.surface, action.resource.resource_id)


def _receipt_sort_key(receipt: ErasureActionReceipt) -> tuple[str, str, str, str]:
    return (receipt.action_type, receipt.resource.resource_type, receipt.resource.surface, receipt.resource.resource_id)


def _validate_action_receipts(
    actions: Sequence[ErasureManifestAction],
    receipts: Sequence[ErasureActionReceipt],
) -> None:
    receipt_by_key = {_receipt_key(receipt.action_type, receipt.resource): receipt for receipt in receipts}
    for action in actions:
        receipt = receipt_by_key.get(_receipt_key(action.action_type, action.resource))
        if receipt is None:
            raise ValueError(
                f"erasure certificate missing receipt for {action.action_type}:{action.resource.resource_id}"
            )
        _validate_receipt_status(action, receipt)
    if len(receipts) != len(actions):
        raise ValueError("erasure certificate contains receipts outside the manifest")


def _validate_receipt_status(action: ErasureManifestAction, receipt: ErasureActionReceipt) -> None:
    expected_status: ErasureReceiptStatus = "DEFERRED" if action.status == "DEFERRED" else "APPLIED"
    if receipt.status != expected_status:
        raise ValueError(f"erasure receipt status must be {expected_status} for {action.action_type}")


def _receipt_key(action_type: ErasureActionType, resource: ErasureResourceRef) -> tuple[str, str, str, str]:
    return (action_type, resource.resource_type, resource.surface, resource.resource_id)


def _certificate_id(
    manifest: ErasureManifest,
    receipts: Sequence[ErasureActionReceipt],
    *,
    certified_at: str,
    certified_by: str,
) -> str:
    payload = {
        "manifest": manifest.redacted_evidence(),
        "receipts": [receipt.redacted_evidence() for receipt in receipts],
        "certifiedAt": certified_at,
        "certifiedBy": certified_by,
    }
    return "erase_cert_" + str(uuid.uuid5(_ERASURE_MANIFEST_NAMESPACE, _json_dumps(payload)))


def _subject_hmac_token(payload: Mapping[str, object], *, secret: str, key_id: str) -> str:
    if not secret:
        raise ValueError("erasure subject token secret is required")
    digest = hmac.new(secret.encode("utf-8"), _json_dumps(payload).encode("utf-8"), hashlib.sha256).hexdigest()
    return f"hmac-sha256:{key_id}:{digest[:32]}"


def _json_dumps(payload: object) -> str:
    """Dump JSON in the canonical shape used for stable ids."""

    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
