"""Security-envelope inheritance rules for Pipeline Builder Graph v2 runtime."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.services.pipeline_v2_runtime_contracts import (
    PipelineV2RuntimeArtifact,
)
from foundry_lite.domain.classification import (
    CLASSIFICATION_RANKS as _CLASSIFICATION_RANK,
)
from foundry_lite.domain.classification import (
    normalize_classification as _classification,
)
from foundry_lite.domain.errors import ValidationFailed

JsonObject = dict[str, object]


def inherited_runtime_security(
    artifacts: Sequence[PipelineV2RuntimeArtifact],
) -> JsonObject:
    """Return an envelope at least as restrictive as every input."""

    if not artifacts:
        return {"classification": "UNCLASSIFIED", "inheritance": "none"}
    envelopes = [artifact.security_envelope for artifact in artifacts]
    classifications = [_classification(envelope.get("classification")) for envelope in envelopes]
    return {
        "tenantId": _single_optional_value(envelopes, "tenantId"),
        "classification": strongest_classification(classifications),
        "policyVersions": _text_values(envelopes, "policyVersion", "policyVersions"),
        "allowedPrincipalSetIds": _text_values(
            envelopes,
            "allowedPrincipalSetId",
            "allowedPrincipalSetIds",
        ),
        "hasLegalHold": any(bool(envelope.get("hasLegalHold")) for envelope in envelopes),
        "principalSetMode": "all_required",
        "legalHoldMode": "sticky",
        "inheritance": "preserved",
        "sourceClassifications": sorted(set(classifications)),
    }


def require_runtime_security_preserved(
    artifacts: Sequence[PipelineV2RuntimeArtifact],
    output_envelope: Mapping[str, object],
    *,
    resource_ref: str,
) -> None:
    """Fail before commit when policy, principal, or legal-hold inheritance weakens."""

    expected = inherited_runtime_security(artifacts)
    gaps = _security_preservation_gaps(expected, output_envelope)
    if not gaps:
        return
    raise ValidationFailed(
        "pipeline output security envelope would weaken its input artifacts",
        details={"resourceRef": resource_ref, "weakenedFields": gaps},
    )


def require_dataset_classification(
    dataset_classification: object,
    required_classification: str,
    *,
    dataset_ref: str,
) -> None:
    """Reject an existing output Dataset that would weaken its inputs."""

    actual = _classification(dataset_classification)
    required = _classification(required_classification)
    if _at_least(actual, required):
        return
    raise ValidationFailed(
        "pipeline output Dataset classification would weaken its input artifacts",
        details={
            "datasetRef": dataset_ref,
            "datasetClassification": actual,
            "requiredClassification": required,
        },
    )


def strongest_classification(values: Sequence[str]) -> str:
    """Resolve the strongest known classification or fail closed."""

    normalized = [_classification(value) for value in values]
    if all(value in _CLASSIFICATION_RANK for value in normalized):
        return max(normalized, key=lambda value: _CLASSIFICATION_RANK[value])
    first = normalized[0] if normalized else "UNCLASSIFIED"
    if all(value == first for value in normalized):
        return first
    raise ValidationFailed(
        "pipeline input classifications cannot be ordered safely",
        details={"classifications": sorted(set(normalized))},
    )


def _at_least(candidate: str, required: str) -> bool:
    if candidate in _CLASSIFICATION_RANK and required in _CLASSIFICATION_RANK:
        return _CLASSIFICATION_RANK[candidate] >= _CLASSIFICATION_RANK[required]
    return candidate == required


def _single_optional_value(
    envelopes: Sequence[Mapping[str, object]],
    field: str,
) -> object:
    values = [envelope.get(field) for envelope in envelopes if envelope.get(field) is not None]
    fingerprints = {_identity_fingerprint(value) for value in values}
    if len(fingerprints) > 1:
        raise ValidationFailed(
            "pipeline security envelope identities do not match",
            details={"field": field},
        )
    return values[0] if values else None


def _identity_fingerprint(value: object) -> str:
    if isinstance(value, Mapping):
        pairs = sorted((str(key), _identity_fingerprint(item)) for key, item in value.items())
        return repr(pairs)
    if isinstance(value, (list, tuple)):
        return repr([_identity_fingerprint(item) for item in value])
    return repr(value)


def _text_values(
    envelopes: Sequence[Mapping[str, object]],
    scalar_field: str,
    list_field: str,
) -> list[str]:
    values: set[str] = set()
    for envelope in envelopes:
        scalar = envelope.get(scalar_field)
        if isinstance(scalar, str) and scalar.strip():
            values.add(scalar.strip())
        listed = envelope.get(list_field)
        if isinstance(listed, (list, tuple)):
            values.update(str(item).strip() for item in listed if str(item).strip())
    return sorted(values)


def _security_preservation_gaps(
    expected: Mapping[str, object],
    actual: Mapping[str, object],
) -> list[str]:
    checks = (
        ("tenantId", expected.get("tenantId") != actual.get("tenantId")),
        ("classification", _classification_is_weaker(expected, actual)),
        ("policyVersions", _missing_text_values(expected, actual, "policyVersions")),
        ("allowedPrincipalSetIds", _missing_text_values(expected, actual, "allowedPrincipalSetIds")),
        ("principalSetMode", _principal_set_mode_is_weaker(expected, actual)),
        ("hasLegalHold", _legal_hold_is_missing(expected, actual)),
        ("legalHoldMode", _legal_hold_mode_is_weaker(expected, actual)),
    )
    return [field for field, has_gap in checks if has_gap]


def _classification_is_weaker(
    expected: Mapping[str, object],
    actual: Mapping[str, object],
) -> bool:
    return not _at_least(
        _classification(actual.get("classification")),
        _classification(expected.get("classification")),
    )


def _missing_text_values(
    expected: Mapping[str, object],
    actual: Mapping[str, object],
    field: str,
) -> bool:
    return not _text_set(expected.get(field)).issubset(_text_set(actual.get(field)))


def _principal_set_mode_is_weaker(
    expected: Mapping[str, object],
    actual: Mapping[str, object],
) -> bool:
    return bool(expected.get("allowedPrincipalSetIds")) and actual.get("principalSetMode") != "all_required"


def _legal_hold_is_missing(
    expected: Mapping[str, object],
    actual: Mapping[str, object],
) -> bool:
    return bool(expected.get("hasLegalHold")) and not bool(actual.get("hasLegalHold"))


def _legal_hold_mode_is_weaker(
    expected: Mapping[str, object],
    actual: Mapping[str, object],
) -> bool:
    return bool(expected.get("hasLegalHold")) and actual.get("legalHoldMode") != "sticky"


def _text_set(value: object) -> set[str]:
    if not isinstance(value, (list, tuple)):
        return set()
    return {str(item).strip() for item in value if str(item).strip()}
