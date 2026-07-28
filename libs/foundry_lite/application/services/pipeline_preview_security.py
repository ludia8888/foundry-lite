"""Public security projection for non-committing Pipeline Builder previews."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.primitives import _json_hash

JsonObject = dict[str, object]
_PUBLIC_TEXT_FIELDS = (
    "tenantId",
    "resourceType",
    "resourceId",
    "classification",
    "sourceClassification",
    "policyVersion",
    "allowedPrincipalSetId",
    "retentionPolicyId",
    "inheritance",
)
_PUBLIC_TEXT_LIST_FIELDS = (
    "sourceClassifications",
    "policyVersions",
    "allowedPrincipalSetIds",
)
_PUBLIC_BOOLEAN_FIELDS = ("hasLegalHold",)
_MAX_IDENTIFIER_LENGTH = 256
_MAX_IDENTIFIER_COUNT = 100


def public_preview_security_envelopes(value: object) -> list[JsonObject]:
    """Return only allowlisted governance metadata plus a full-envelope proof."""

    if not isinstance(value, (list, tuple)):
        return []
    return [public_preview_security_envelope(envelope) for envelope in value if isinstance(envelope, Mapping)]


def public_preview_security_envelope(envelope: Mapping[str, object]) -> JsonObject:
    """Project one envelope without exposing arbitrary private extension fields."""

    payload: JsonObject = {
        "securityEnvelopeFingerprint": _json_hash({str(key): value for key, value in envelope.items()}),
    }
    _copy_text_fields(payload, envelope)
    _copy_text_list_fields(payload, envelope)
    _copy_boolean_fields(payload, envelope)
    return payload


def without_internal_security_envelopes(value: object) -> object:
    """Recursively remove row-level internal envelopes from a public preview value."""

    if isinstance(value, Mapping):
        return {
            str(key): without_internal_security_envelopes(item)
            for key, item in value.items()
            if str(key) != "securityEnvelope"
        }
    if isinstance(value, (list, tuple)):
        return [without_internal_security_envelopes(item) for item in value]
    return value


def _copy_text_fields(
    target: JsonObject,
    source: Mapping[str, object],
) -> None:
    for field in _PUBLIC_TEXT_FIELDS:
        value = _bounded_identifier(source.get(field))
        if value is not None:
            target[field] = value


def _copy_text_list_fields(
    target: JsonObject,
    source: Mapping[str, object],
) -> None:
    for field in _PUBLIC_TEXT_LIST_FIELDS:
        values = _bounded_identifiers(source.get(field))
        if values:
            target[field] = values


def _copy_boolean_fields(
    target: JsonObject,
    source: Mapping[str, object],
) -> None:
    for field in _PUBLIC_BOOLEAN_FIELDS:
        value = source.get(field)
        if isinstance(value, bool):
            target[field] = value


def _bounded_identifier(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > _MAX_IDENTIFIER_LENGTH:
        return None
    return normalized


def _bounded_identifiers(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    result = [_bounded_identifier(item) for item in value[:_MAX_IDENTIFIER_COUNT]]
    return [item for item in result if item is not None]
