from __future__ import annotations

import pytest
from foundry_lite.security.erasure import (
    ErasureActionReceipt,
    ErasureManifestAction,
    ErasureReceiptStatus,
    ErasureRequest,
    ErasureResolution,
    ErasureResourceRef,
    ErasureRetentionPolicy,
    build_erasure_certificate,
    build_erasure_manifest,
    is_erased_resource,
    resolve_erasure_subject,
)


def test_erasure_manifest_removes_subject_from_serving_surfaces_without_raw_values() -> None:
    request = _erasure_request()
    retention_policy = _retention_policy()
    resolutions = (
        resolve_erasure_subject(
            request,
            [
                _record("obj_order_1", "object_record"),
                _record("obj_order_2", "object_record", email="other@example.com"),
            ],
            identity_fields=("email",),
            resource_type="object_record",
            surface="object_store",
        ),
        resolve_erasure_subject(
            request,
            [_record("search_order_1", "search_document")],
            identity_fields=("email",),
            resource_type="search_document",
            surface="search_index",
        ),
        resolve_erasure_subject(
            request,
            [_record("mat_row_1", "materialization_row")],
            identity_fields=("email",),
            resource_type="materialization_row",
            surface="ops.customer_summary",
        ),
        resolve_erasure_subject(
            request,
            [_record("dlq_1", "dead_letter_record")],
            identity_fields=("email",),
            resource_type="dead_letter_record",
            surface="record_dlq",
        ),
    )

    manifest = build_erasure_manifest(request, resolutions, retention_policy=retention_policy)
    action_types = {action.action_type for action in manifest.actions}
    rendered = repr(manifest.redacted_evidence())

    assert manifest.status == "READY_TO_APPLY"
    assert action_types == {
        "minimize_audit_evidence",
        "redact_dead_letter_payload",
        "rebuild_materialization",
        "remove_search_document",
        "tombstone_object",
    }
    assert "ada@example.com" not in rendered
    assert request.subject_hash in rendered


def test_erasure_subject_hash_is_secret_hmac_token() -> None:
    request = _erasure_request()
    replay = _erasure_request()
    rotated = _erasure_request(subject_token_secret="new-secret", subject_token_key_id="erasure-key-2")

    assert request.subject_hash == replay.subject_hash
    assert request.subject_hash != rotated.subject_hash
    assert request.subject_hash.startswith("hmac-sha256:erasure-key-1:")
    assert "ada@example.com" not in repr(request.redacted_evidence())
    with pytest.raises(ValueError, match="subject token secret is required"):
        _ = _erasure_request(subject_token_secret="").subject_hash


def test_erasure_resolution_is_tenant_scoped() -> None:
    request = _erasure_request()
    records = [
        _record("tenant_a_object", "object_record", tenant_id="tenant-a"),
        _record("tenant_b_object", "object_record", tenant_id="tenant-b"),
    ]

    resolution = resolve_erasure_subject(
        request,
        records,
        identity_fields=("email",),
        resource_type="object_record",
        surface="object_store",
    )

    assert [resource.resource_id for resource in resolution.resources] == ["tenant_a_object"]
    with pytest.raises(ValueError) as exc_info:
        ErasureResolution(
            request=request,
            resources=(
                ErasureResourceRef(
                    tenant_id="tenant-b",
                    resource_type="object_record",
                    resource_id="tenant_b_object",
                    surface="object_store",
                ),
            ),
        )
    assert str(exc_info.value) == "erasure resolution cannot include resources from another tenant"


def test_erasure_manifest_retry_is_idempotent() -> None:
    request = _erasure_request()
    retention_policy = _retention_policy()
    first_resolution = resolve_erasure_subject(
        request,
        [_record("search_1", "search_document"), _record("object_1", "object_record")],
        identity_fields=("email",),
        resource_type="search_document",
        surface="search_index",
    )
    replay_resolution = resolve_erasure_subject(
        request,
        [_record("object_1", "object_record"), _record("search_1", "search_document")],
        identity_fields=("email",),
        resource_type="search_document",
        surface="search_index",
    )

    first = build_erasure_manifest(request, (first_resolution,), retention_policy=retention_policy)
    replay = build_erasure_manifest(request, (replay_resolution,), retention_policy=retention_policy)

    assert first == replay


def test_backup_retention_exposes_pending_erasure_state() -> None:
    request = _erasure_request()
    retention_policy = _retention_policy()
    resolution = resolve_erasure_subject(
        request,
        [_record("backup_20260619", "backup_snapshot")],
        identity_fields=("email",),
        resource_type="backup_snapshot",
        surface="backup_manifest",
    )

    manifest = build_erasure_manifest(request, (resolution,), retention_policy=retention_policy)
    backup_action = next(action for action in manifest.actions if action.action_type == "defer_backup_expiration")

    assert manifest.status == "PENDING_RETENTION"
    assert backup_action.status == "DEFERRED"
    assert backup_action.evidence["retentionUntil"] == "2026-07-19T00:00:00Z"
    assert backup_action.evidence["cryptoShreddingKeyRef"] == "kms://tenant-a/customer-erasure"
    assert "ada@example.com" not in repr(manifest.redacted_evidence())


def test_dataset_row_redaction_is_deferred_to_maintenance_lane() -> None:
    request = _erasure_request()
    retention_policy = _retention_policy()
    resolution = resolve_erasure_subject(
        request,
        [_record("clean.orders:row-7", "dataset_row")],
        identity_fields=("email",),
        resource_type="dataset_row",
        surface="clean.orders",
    )

    manifest = build_erasure_manifest(request, (resolution,), retention_policy=retention_policy)
    dataset_action = next(action for action in manifest.actions if action.action_type == "redact_dataset_row")

    # Dataset rows live in immutable version files, so the physical rewrite is deferred;
    # the manifest still carries a raw-value-free durable record of the row to redact.
    assert manifest.status == "PENDING_RETENTION"
    assert dataset_action.status == "DEFERRED"
    assert dataset_action.resource.resource_id == "clean.orders:row-7"
    assert dataset_action.reason == "deferred_to_redaction_maintenance"
    assert dataset_action.evidence["subjectHash"] == request.subject_hash
    assert "ada@example.com" not in repr(manifest.redacted_evidence())


def test_search_rebuild_does_not_resurrect_erased_subject() -> None:
    request = _erasure_request()
    retention_policy = _retention_policy()
    resolution = resolve_erasure_subject(
        request,
        [
            _record("search_order_1", "search_document"),
            _record("search_order_2", "search_document", email="other@example.com"),
        ],
        identity_fields=("email",),
        resource_type="search_document",
        surface="search_index",
    )
    manifest = build_erasure_manifest(request, (resolution,), retention_policy=retention_policy)

    assert is_erased_resource(manifest, resource_type="search_document", resource_id="search_order_1")
    assert not is_erased_resource(manifest, resource_type="search_document", resource_id="search_order_2")


def test_erasure_certificate_requires_receipt_for_every_manifest_action() -> None:
    request = _erasure_request()
    retention_policy = _retention_policy()
    resolution = resolve_erasure_subject(
        request,
        [_record("object_1", "object_record"), _record("search_1", "search_document")],
        identity_fields=("email",),
        resource_type="object_record",
        surface="object_store",
    )
    manifest = build_erasure_manifest(request, (resolution,), retention_policy=retention_policy)
    receipts = tuple(_receipt_for(action) for action in manifest.actions)

    certificate = build_erasure_certificate(
        manifest,
        receipts,
        certified_at="2026-07-20T00:00:00Z",
        certified_by="privacy-officer",
    )

    rendered = repr(certificate.redacted_evidence())
    assert certificate.status == "CERTIFIED"
    assert certificate.subject_hash == request.subject_hash
    assert certificate.subject_token_key_id == "erasure-key-1"
    assert "ada@example.com" not in rendered
    with pytest.raises(ValueError, match="missing receipt"):
        build_erasure_certificate(
            manifest,
            receipts[:-1],
            certified_at="2026-07-20T00:00:00Z",
            certified_by="privacy-officer",
        )


def _erasure_request(
    *,
    subject_token_secret: str = "tenant-a-erasure-secret",
    subject_token_key_id: str = "erasure-key-1",
) -> ErasureRequest:
    return ErasureRequest(
        tenant_id="tenant-a",
        request_id="erase_req_1",
        subject_kind="email",
        subject_value="ada@example.com",
        requested_by="privacy-officer",
        idempotency_key="erase-key-1",
        reason="user_request",
        requested_at="2026-06-19T00:00:00Z",
        subject_token_secret=subject_token_secret,
        subject_token_key_id=subject_token_key_id,
    )


def _retention_policy() -> ErasureRetentionPolicy:
    return ErasureRetentionPolicy(
        backup_retention_until="2026-07-19T00:00:00Z",
        crypto_shredding_key_ref="kms://tenant-a/customer-erasure",
    )


def _record(
    resource_id: str,
    surface_kind: str,
    *,
    tenant_id: str = "tenant-a",
    email: str = "ada@example.com",
) -> dict[str, object]:
    return {
        "id": resource_id,
        "tenant_id": tenant_id,
        "email": email,
        "surfaceKind": surface_kind,
    }


def _receipt_for(action: ErasureManifestAction) -> ErasureActionReceipt:
    status: ErasureReceiptStatus = "DEFERRED" if action.status == "DEFERRED" else "APPLIED"
    return ErasureActionReceipt(
        action_type=action.action_type,
        resource=action.resource,
        status=status,
        completed_at="2026-07-20T00:00:00Z",
        evidence={"executor": "unit-test", "subjectHash": _erasure_request().subject_hash},
    )
