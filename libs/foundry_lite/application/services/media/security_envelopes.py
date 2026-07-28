"""Canonical security-envelope checks shared by Media and Pipeline Builder."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports.media_repository import (
    MediaItemVersionRecord,
    MediaSetRecord,
)
from foundry_lite.domain.classification import (
    CLASSIFICATION_RANKS as _CLASSIFICATION_RANK,
)
from foundry_lite.domain.classification import (
    normalize_classification as _classification,
)
from foundry_lite.domain.errors import ValidationFailed

JsonObject = dict[str, object]


class MediaSecurityEnvelopeInvalid(ValidationFailed):
    """Typed fail-closed result for an unsafe Media Set/version envelope."""

    code = "MEDIA_SECURITY_ENVELOPE_INVALID"


def validated_media_source_envelope(
    media_set: MediaSetRecord,
    version: MediaItemVersionRecord,
    *,
    tenant_id: str,
) -> JsonObject:
    """Validate committed media truth before a processor or model may see it."""

    return _validated_envelope(
        media_set,
        version.security_envelope,
        tenant_id=tenant_id,
        version_tenant_id=version.tenant_id,
        has_expected_legal_hold=version.has_legal_hold,
        media_item_version_id=version.media_item_version_id,
        should_enrich_legal_hold=True,
    )


def validated_media_upload_envelope(
    media_set: MediaSetRecord,
    envelope: Mapping[str, object],
    *,
    tenant_id: str,
) -> JsonObject:
    """Prevent new uploads from weakening their destination Media Set."""

    legal_hold = envelope.get("hasLegalHold", False)
    if not isinstance(legal_hold, bool):
        raise _failure(media_set, "source_security_envelope_invalid", ("hasLegalHold",))
    return _validated_envelope(
        media_set,
        envelope,
        tenant_id=tenant_id,
        version_tenant_id=tenant_id,
        has_expected_legal_hold=legal_hold,
        media_item_version_id=None,
        should_enrich_legal_hold=False,
    )


def _validated_envelope(
    media_set: MediaSetRecord,
    envelope: Mapping[str, object],
    *,
    tenant_id: str,
    version_tenant_id: str,
    has_expected_legal_hold: bool,
    media_item_version_id: str | None,
    should_enrich_legal_hold: bool,
) -> JsonObject:
    payload = {str(key): value for key, value in envelope.items()}
    _require_tenant_identity(media_set, payload, tenant_id, version_tenant_id, media_item_version_id)
    _require_classification(media_set, payload, media_item_version_id)
    _require_retention_policy(media_set, payload, media_item_version_id)
    _require_legal_hold(media_set, payload, has_expected_legal_hold, media_item_version_id)
    payload["tenantId"] = tenant_id
    if should_enrich_legal_hold:
        payload["hasLegalHold"] = has_expected_legal_hold
    return payload


def _require_tenant_identity(
    media_set: MediaSetRecord,
    envelope: Mapping[str, object],
    tenant_id: str,
    version_tenant_id: str,
    media_item_version_id: str | None,
) -> None:
    envelope_tenant = envelope.get("tenantId")
    is_envelope_tenant_valid = envelope_tenant is None or envelope_tenant == tenant_id
    if media_set.tenant_id == tenant_id and version_tenant_id == tenant_id and is_envelope_tenant_valid:
        return
    raise _failure(
        media_set,
        "source_security_tenant_mismatch",
        ("tenantId",),
        media_item_version_id=media_item_version_id,
    )


def _require_classification(
    media_set: MediaSetRecord,
    envelope: Mapping[str, object],
    media_item_version_id: str | None,
) -> None:
    candidate = envelope.get("classification")
    if not isinstance(candidate, str) or not candidate.strip():
        raise _failure(
            media_set,
            "source_security_envelope_invalid",
            ("classification",),
            media_item_version_id=media_item_version_id,
        )
    if not _is_weaker(_classification(candidate), _classification(media_set.classification)):
        return
    raise _failure(
        media_set,
        "source_security_weakened",
        ("classification",),
        media_item_version_id=media_item_version_id,
    )


def _require_retention_policy(
    media_set: MediaSetRecord,
    envelope: Mapping[str, object],
    media_item_version_id: str | None,
) -> None:
    expected = media_set.retention_policy_id
    if expected is None or envelope.get("retentionPolicyId") == expected:
        return
    raise _failure(
        media_set,
        "source_security_weakened",
        ("retentionPolicyId",),
        media_item_version_id=media_item_version_id,
    )


def _require_legal_hold(
    media_set: MediaSetRecord,
    envelope: Mapping[str, object],
    has_expected_legal_hold: bool,
    media_item_version_id: str | None,
) -> None:
    actual = envelope.get("hasLegalHold")
    if actual is None or actual is has_expected_legal_hold:
        return
    reason = "source_security_envelope_invalid" if not isinstance(actual, bool) else "source_security_weakened"
    raise _failure(
        media_set,
        reason,
        ("hasLegalHold",),
        media_item_version_id=media_item_version_id,
    )


def _failure(
    media_set: MediaSetRecord,
    reason: str,
    weakened_fields: tuple[str, ...],
    *,
    media_item_version_id: str | None = None,
) -> MediaSecurityEnvelopeInvalid:
    details: JsonObject = {
        "reason": reason,
        "mediaSetId": media_set.media_set_id,
        "weakenedFields": list(weakened_fields),
    }
    if media_item_version_id is not None:
        details["mediaItemVersionId"] = media_item_version_id
    return MediaSecurityEnvelopeInvalid(
        "media security envelope does not preserve its Media Set controls",
        details=details,
    )


def _is_weaker(candidate: str, source: str) -> bool:
    if candidate in _CLASSIFICATION_RANK and source in _CLASSIFICATION_RANK:
        return _CLASSIFICATION_RANK[candidate] < _CLASSIFICATION_RANK[source]
    return candidate != source
