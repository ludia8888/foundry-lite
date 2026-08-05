"""Persist validated Action definitions during Ontology activation."""

from __future__ import annotations

from foundry_lite.application.ports import (
    ActionParameterSchema,
    ActionTypeRecord,
    OntologyRepository,
    TransactionContext,
)
from foundry_lite.application.primitives import _new_id
from foundry_lite.application.services.ontology_yaml import (
    YamlObject,
    action_type_definition,
    action_type_parameter_schema,
    mapping_sequence,
    optional_str,
    required_str,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed


def import_action_types(
    repository: OntologyRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    ontology_version_id: str,
    definition: YamlObject,
    object_map: dict[str, str],
) -> None:
    for item in mapping_sequence(definition, "actionTypes"):
        _insert_action_type(repository, conn, ctx, ontology_version_id, item, object_map)


def _insert_action_type(
    repository: OntologyRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    ontology_version_id: str,
    item: YamlObject,
    object_map: dict[str, str],
) -> None:
    target = required_str(item, "target")
    target_kind = optional_str(item, "targetKind", "object") or "object"
    parameter_schema: ActionParameterSchema = action_type_parameter_schema(item)
    persisted_definition = action_type_definition(item)
    api_name = required_str(item, "apiName")
    repository.insert_action_type(
        transaction=conn,
        record=ActionTypeRecord(
            action_type_id=_new_id("atype"),
            tenant_id=ctx.tenant_id,
            ontology_version_id=ontology_version_id,
            api_name=api_name,
            display_name=optional_str(item, "displayName", api_name) or api_name,
            target_kind=target_kind,
            target_object_type_id=_target_id(
                repository, conn, ctx, ontology_version_id, target_kind, target, object_map
            ),
            target_api_name=target,
            parameter_schema=parameter_schema,
            definition=persisted_definition,
            enabled=True,
        ),
    )


def _target_id(
    repository: OntologyRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    ontology_version_id: str,
    target_kind: str,
    target: str,
    object_map: dict[str, str],
) -> str:
    if target_kind == "object" and target in object_map:
        return object_map[target]
    if target_kind == "interface":
        rows = repository.interface_types_for_version(
            transaction=conn, tenant_id=ctx.tenant_id, ontology_version_id=ontology_version_id
        )
        for row in rows:
            if row["api_name"] == target:
                return row["id"]
    raise ValidationFailed(
        "action target reference was not found",
        details={"targetKind": target_kind, "target": target},
    )
