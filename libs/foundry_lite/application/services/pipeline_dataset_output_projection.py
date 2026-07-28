"""Serving-row and durable-evidence projection for Pipeline Dataset outputs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from foundry_lite.application.primitives import _json_hash
from foundry_lite.domain.errors import ValidationFailed

JsonObject = dict[str, object]
_INTERNAL_FIELDS = frozenset(
    {
        "cacheEvidence",
        "cacheHit",
        "cacheKey",
        "cacheStatus",
        "internalEvidence",
        "modelEvidence",
        "processingEvidence",
        "providerEvidence",
        "providerRequestId",
        "providerResponse",
        "securityEnvelope",
        "trialEvidence",
    }
)


@dataclass(frozen=True, slots=True)
class PipelineDatasetOutputProjection:
    """Rows visible to users plus evidence retained outside the serving table."""

    serving_rows: tuple[JsonObject, ...]
    fieldnames: tuple[str, ...]
    output_contract: JsonObject
    row_evidence: tuple[JsonObject, ...]


def pipeline_dataset_output_evidence(
    projection: PipelineDatasetOutputProjection,
    security_envelope: Mapping[str, object],
) -> JsonObject:
    """Build the non-serving evidence retained on transaction and lineage records."""

    return {
        "outputContract": dict(projection.output_contract),
        "servingColumns": list(projection.fieldnames),
        "rowEvidence": [dict(item) for item in projection.row_evidence],
        "securityContract": _security_contract(security_envelope),
    }


def project_pipeline_dataset_output(
    rows: Sequence[Mapping[str, object]],
    configured_contract: object,
) -> PipelineDatasetOutputProjection:
    """Apply a declared allowlist or infer only non-internal user columns."""

    columns = _contract_columns(configured_contract)
    fieldnames = _declared_fieldnames(columns) if columns else _inferred_fieldnames(rows)
    serving = tuple({field: row.get(field) for field in fieldnames} for row in rows)
    mode = "declared" if columns else "inferred_user_columns"
    contract_columns = columns or tuple({"name": field} for field in fieldnames)
    return PipelineDatasetOutputProjection(
        serving_rows=serving,
        fieldnames=fieldnames,
        output_contract={"columns": [dict(column) for column in contract_columns], "mode": mode},
        row_evidence=tuple(_row_evidence(index, row) for index, row in enumerate(rows)),
    )


def _contract_columns(value: object) -> tuple[JsonObject, ...]:
    if value is None:
        return ()
    if not isinstance(value, Mapping):
        raise ValidationFailed("pipeline Dataset output contract must be an object")
    columns = value.get("columns", ())
    if not isinstance(columns, (list, tuple)):
        raise ValidationFailed("pipeline Dataset output contract columns must be a list")
    return tuple(_contract_column(column) for column in columns)


def _contract_column(value: object) -> JsonObject:
    if not isinstance(value, Mapping):
        raise ValidationFailed("pipeline Dataset output contract column must be an object")
    name = value.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValidationFailed("pipeline Dataset output contract column name is required")
    clean_name = name.strip()
    if _is_internal_field(clean_name):
        raise ValidationFailed(
            "pipeline Dataset output contract cannot expose internal evidence",
            details={"column": clean_name},
        )
    return {**{str(key): item for key, item in value.items()}, "name": clean_name}


def _declared_fieldnames(columns: Sequence[Mapping[str, object]]) -> tuple[str, ...]:
    names = tuple(str(column["name"]) for column in columns)
    if len(names) != len(set(names)):
        raise ValidationFailed("pipeline Dataset output contract column names must be unique")
    return names


def _inferred_fieldnames(rows: Sequence[Mapping[str, object]]) -> tuple[str, ...]:
    fields = sorted({str(field) for row in rows for field in row if not _is_internal_field(str(field))})
    if not fields:
        raise ValidationFailed("pipeline Dataset output rows have no user-visible fields")
    return tuple(fields)


def _row_evidence(index: int, row: Mapping[str, object]) -> JsonObject:
    internal = {
        str(field): value
        for field, value in row.items()
        if _is_internal_field(str(field)) and field != "securityEnvelope"
    }
    envelope = row.get("securityEnvelope")
    return {
        "rowOrdinal": index,
        "inputFingerprint": _json_hash(dict(row)),
        "securityEnvelope": dict(envelope) if isinstance(envelope, Mapping) else {},
        "internalEvidence": internal,
    }


def _is_internal_field(field: str) -> bool:
    return field.startswith("_pipeline") or field.startswith("__") or field in _INTERNAL_FIELDS


def _security_contract(envelope: Mapping[str, object]) -> JsonObject:
    principal_sets = _text_values(envelope.get("allowedPrincipalSetIds"))
    return {
        "tenantId": envelope.get("tenantId"),
        "classification": envelope.get("classification"),
        "policyVersions": _text_values(envelope.get("policyVersions")),
        "allowedPrincipalSetIds": principal_sets,
        "principalSetMode": "all_required",
        "principalMembershipEnforcement": ("admin_only_without_resolver" if principal_sets else "not_required"),
        "hasLegalHold": bool(envelope.get("hasLegalHold")),
        "legalHoldMode": "sticky",
    }


def _text_values(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return sorted({str(item).strip() for item in value if str(item).strip()})
