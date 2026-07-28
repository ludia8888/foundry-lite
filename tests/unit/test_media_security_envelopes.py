from __future__ import annotations

import io
from pathlib import Path

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports.media_repository import (
    MediaItemVersionRecord,
    MediaSetRecord,
)
from foundry_lite.application.primitives import _json_hash
from foundry_lite.application.services.media.security_envelopes import (
    MediaSecurityEnvelopeInvalid,
    validated_media_source_envelope,
    validated_media_upload_envelope,
)
from foundry_lite.application.services.pipeline_preview_security import (
    public_preview_security_envelope,
    without_internal_security_envelopes,
)
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies


def test_media_upload_cannot_weaken_media_set_classification() -> None:
    with pytest.raises(MediaSecurityEnvelopeInvalid) as captured:
        validated_media_upload_envelope(
            _media_set(),
            {"tenantId": "tenant-a", "classification": "public"},
            tenant_id="tenant-a",
        )

    assert captured.value.details["reason"] == "source_security_weakened"
    assert captured.value.details["weakenedFields"] == ["classification"]


@pytest.mark.parametrize("classification", ["secret", "RESTRICTED"])
def test_media_upload_accepts_known_highest_classification_aliases(classification: str) -> None:
    validated = validated_media_upload_envelope(
        _media_set(),
        {"tenantId": "tenant-a", "classification": classification},
        tenant_id="tenant-a",
    )

    assert validated["classification"] == classification


def test_media_upload_service_enforces_media_set_security_guard(tmp_path: Path) -> None:
    foundry = FoundryLite(
        dependencies=create_local_core_dependencies(
            db_url=f"sqlite:///{tmp_path / 'media-upload-security.db'}",
            storage_root=tmp_path / "flite",
        )
    )
    ctx = demo_admin_context()
    media_set = foundry.media.create_media_set(
        ctx,
        namespace="legal",
        name="upload_guard",
        schema_type="document",
        primary_format="pdf",
        allowed_input_formats=("pdf",),
        classification="confidential",
    )
    transaction_id = foundry.media.open_transaction(
        ctx,
        media_set_id=media_set.media_set_id,
        idempotency_key="upload-security-guard",
    )

    with pytest.raises(MediaSecurityEnvelopeInvalid):
        foundry.media.upload(
            ctx,
            media_set_id=media_set.media_set_id,
            media_transaction_id=transaction_id,
            logical_path="/contracts/weak.pdf",
            source=io.BytesIO(b"%PDF-1.4\n%%EOF"),
            supplied_mime_type="application/pdf",
            schema_type="document",
            format="pdf",
            security_envelope={"tenantId": ctx.tenant_id, "classification": "public"},
        )


def test_media_source_requires_retention_and_legal_hold_controls() -> None:
    media_set = _media_set(retention_policy_id="retention-7y")
    missing_retention = _version()
    with pytest.raises(MediaSecurityEnvelopeInvalid) as retention:
        validated_media_source_envelope(media_set, missing_retention, tenant_id="tenant-a")

    held_version = _version(
        envelope={
            "tenantId": "tenant-a",
            "classification": "confidential",
            "retentionPolicyId": "retention-7y",
            "hasLegalHold": False,
        },
        has_legal_hold=True,
    )
    with pytest.raises(MediaSecurityEnvelopeInvalid) as legal_hold:
        validated_media_source_envelope(media_set, held_version, tenant_id="tenant-a")

    assert retention.value.details["weakenedFields"] == ["retentionPolicyId"]
    assert legal_hold.value.details["weakenedFields"] == ["hasLegalHold"]


def test_preview_security_projection_allowlists_fields_and_fingerprints_full_envelope() -> None:
    envelope = {
        "tenantId": "tenant-a",
        "classification": "confidential",
        "policyVersion": "policy-v3",
        "allowedPrincipalSetId": "legal-readers",
        "hasLegalHold": True,
        "privateExtension": {"operatorOnly": "not-public"},
    }

    projected = public_preview_security_envelope(envelope)
    public_row = without_internal_security_envelopes(
        {
            "text": "contract",
            "securityEnvelope": envelope,
            "units": [{"securityEnvelope": envelope, "text": "nested"}],
        }
    )

    assert projected == {
        "securityEnvelopeFingerprint": _json_hash(envelope),
        "tenantId": "tenant-a",
        "classification": "confidential",
        "policyVersion": "policy-v3",
        "allowedPrincipalSetId": "legal-readers",
        "hasLegalHold": True,
    }
    assert "privateExtension" not in projected
    assert public_row == {"text": "contract", "units": [{"text": "nested"}]}


def _media_set(*, retention_policy_id: str | None = None) -> MediaSetRecord:
    return MediaSetRecord(
        media_set_id="mset-1",
        tenant_id="tenant-a",
        namespace="legal",
        name="contracts",
        schema_type="document",
        primary_format="pdf",
        allowed_input_formats=("pdf",),
        transaction_policy="transactional",
        storage_profile="local",
        processing_profile="local",
        classification="confidential",
        retention_policy_id=retention_policy_id,
        created_at="2026-07-17T00:00:00Z",
        updated_at="2026-07-17T00:00:00Z",
    )


def _version(
    *,
    envelope: dict[str, object] | None = None,
    has_legal_hold: bool = False,
) -> MediaItemVersionRecord:
    return MediaItemVersionRecord(
        media_item_version_id="mver-1",
        tenant_id="tenant-a",
        media_item_id="mitem-1",
        media_transaction_id="mtx-1",
        version_number=1,
        blob_key="media/contracts/acme.pdf",
        content_hash="sha256:pdf",
        byte_size=512,
        supplied_mime_type="application/pdf",
        sniffed_mime_type="application/pdf",
        schema_type="document",
        format="pdf",
        probe_metadata={},
        security_envelope=envelope
        or {
            "tenantId": "tenant-a",
            "classification": "confidential",
        },
        source_ref=None,
        status="COMMITTED",
        created_at="2026-07-17T00:00:00Z",
        committed_at="2026-07-17T00:00:01Z",
        has_legal_hold=has_legal_hold,
    )
