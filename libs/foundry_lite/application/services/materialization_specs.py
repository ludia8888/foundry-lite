"""Resolve materialization specs from the active ontology and build snapshot rows.

Object-snapshot materializations are declared per object type in ontology YAML
(``materialization: {dataset: ops.order_current, mode: object_snapshot}``) and
resolved here at run time, so operators can materialize any object type to a
dataset without code changes. The ``action_log`` built-in stays code-defined
because it reads action runs rather than one object type, and the legacy
``order_current`` name keeps working for ontologies that predate declarations.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.ports import ObjectTypeRow, PropertyTypeRow, TransactionContext
from foundry_lite.application.ports.materialization_repository import (
    MaterializationRepository,
    MaterializationRow,
    ObjectRecordVersionRow,
)
from foundry_lite.application.services.materialization_protocols import MaterializationOntologyLookup
from foundry_lite.application.services.materialization_types import (
    BUILTIN_MATERIALIZATION_SPECS,
    LEGACY_MATERIALIZATION_SPECS,
    MaterializationSpec,
    materialization_spec_name,
    object_snapshot_spec,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound


def object_type_materialization(config: Mapping[str, object]) -> Mapping[str, object] | None:
    """Return the materialization block stored in object-type config JSON, if declared.

    The block was validated at ontology apply time; a malformed persisted value
    is treated as undeclared so the spec simply does not resolve (fail closed).
    """
    value = config.get("materialization")
    if not isinstance(value, Mapping) or not isinstance(value.get("dataset"), str):
        return None
    return value


def resolve_declared_materialization_specs(
    ontology: MaterializationOntologyLookup,
    conn: TransactionContext,
    ctx: RequestContext,
) -> dict[str, MaterializationSpec]:
    """Return ontology-declared specs keyed by derived api name."""
    try:
        active = ontology._active_ontology_version(conn, ctx)
    except NotFound:
        # No active ontology yet: only built-in/legacy specs are runnable
        # (e.g. action_log before the first ontology apply).
        return {}
    specs: dict[str, MaterializationSpec] = {}
    for row in ontology._object_types_for_version(conn, ctx, active["id"]):
        declared = object_type_materialization(row["config"])
        if declared is None:
            continue
        dataset_ref = str(declared["dataset"])
        specs[materialization_spec_name(dataset_ref)] = object_snapshot_spec(row["api_name"], dataset_ref)
    return specs


def resolve_materialization_spec(
    ontology: MaterializationOntologyLookup,
    conn: TransactionContext,
    ctx: RequestContext,
    api_name: str,
) -> MaterializationSpec | None:
    """Resolve one runnable spec by name; an ontology declaration wins over built-in/legacy names."""
    declared = resolve_declared_materialization_specs(ontology, conn, ctx)
    if api_name in declared:
        return declared[api_name]
    return BUILTIN_MATERIALIZATION_SPECS.get(api_name) or LEGACY_MATERIALIZATION_SPECS.get(api_name)


def available_materialization_specs(
    ontology: MaterializationOntologyLookup,
    conn: TransactionContext,
    ctx: RequestContext,
) -> list[dict[str, object]]:
    """List every runnable spec with its origin so operators can discover declared names."""
    merged: dict[str, tuple[str, MaterializationSpec]] = {}
    for origin, table in (
        ("builtin", BUILTIN_MATERIALIZATION_SPECS),
        ("legacy", LEGACY_MATERIALIZATION_SPECS),
        ("ontology", resolve_declared_materialization_specs(ontology, conn, ctx)),
    ):
        for name, spec in table.items():
            merged[name] = (origin, spec)
    return [
        {
            "apiName": name,
            "materializationType": spec.materialization_type,
            "targetDataset": spec.target["dataset"],
            "source": dict(spec.source),
            "origin": origin,
        }
        for name, (origin, spec) in sorted(merged.items())
    ]


def object_snapshot_rows(
    ontology: MaterializationOntologyLookup,
    repository: MaterializationRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    materialization: MaterializationRow,
    watermark: Mapping[str, object],
) -> tuple[list[Mapping[str, object]], list[str]]:
    """Build snapshot rows for any object type: declared properties + objectVersion + updatedAt.

    Property values come from the object store record (base and edit layers
    already merged), so action edits are included. Classified properties are
    intentionally NOT masked here: materialization is a data-plane operator
    output (parity with the original order_current behavior); read-path
    masking still applies to the object APIs.
    """
    object_type = str(materialization["source_ref"].get("objectType", "Order"))
    object_type_row = ontology._active_object_type(conn, ctx, object_type)
    property_names = _snapshot_property_names(
        object_type_row,
        ontology._properties_for_object_type(conn, object_type_row["id"]),
    )
    fieldnames = [*property_names, "objectVersion", "updatedAt"]
    records = _object_records_at_watermark(repository, conn, ctx, object_type, watermark)
    return [_object_snapshot_row(record, property_names) for record in records], fieldnames


def _snapshot_property_names(object_type: ObjectTypeRow, properties: Sequence[PropertyTypeRow]) -> list[str]:
    """Order snapshot columns primary key first, then the repository's stable api-name order."""
    names = [prop["api_name"] for prop in properties]
    primary_key = object_type["primary_key_property"]
    if primary_key not in names:
        return names
    return [primary_key, *[name for name in names if name != primary_key]]


def _object_snapshot_row(record: Mapping[str, object], property_names: Sequence[str]) -> Mapping[str, object]:
    properties = record.get("properties")
    properties = properties if isinstance(properties, Mapping) else {}
    row: dict[str, object] = {name: properties.get(name) for name in property_names}
    row["objectVersion"] = record["object_version"]
    row["updatedAt"] = record["updated_at"]
    return row


def _object_records_at_watermark(
    repository: MaterializationRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    object_type: str,
    watermark: Mapping[str, object],
) -> Sequence[ObjectRecordVersionRow]:
    max_sequence = watermark.get("object_change_sequence_lte")
    index_version = watermark.get("active_index_version")
    if not isinstance(max_sequence, int) or not isinstance(index_version, str):
        return []
    return repository.object_records_at_watermark(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        object_type_api_name=object_type,
        index_version=index_version,
        max_object_change_sequence=max_sequence,
    )
