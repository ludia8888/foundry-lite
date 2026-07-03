"""Application adapter for ontology migration planning."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.ontology_repository import (
    ActionTypeRow,
    ObjectTypeRow,
    OntologyRepository,
    PropertyTypeRow,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.ontology.migration import build_ontology_migration_plan
from foundry_lite.domain.ontology.migration_types import OntologyMigrationPlan


def plan_ontology_migration(
    *,
    repository: OntologyRepository,
    transaction: TransactionContext,
    ctx: RequestContext,
    active_ontology_version_id: str | None,
    definition: Mapping[str, object],
) -> OntologyMigrationPlan:
    """Build a migration plan against the active ontology version, if any."""
    if active_ontology_version_id is None:
        return OntologyMigrationPlan(None, (), ())
    current_objects = _active_object_types(repository, transaction, ctx, active_ontology_version_id)
    current_properties = _active_properties(repository, transaction, current_objects)
    current_links = repository.link_types_for_version(
        transaction=transaction,
        tenant_id=ctx.tenant_id,
        ontology_version_id=active_ontology_version_id,
    )
    current_actions = _active_actions(repository, transaction, current_objects)
    current_interfaces = repository.interface_types_for_version(
        transaction=transaction,
        tenant_id=ctx.tenant_id,
        ontology_version_id=active_ontology_version_id,
    )
    current_functions = repository.function_types_for_version(
        transaction=transaction,
        tenant_id=ctx.tenant_id,
        ontology_version_id=active_ontology_version_id,
    )
    return build_ontology_migration_plan(
        source_ontology_version_id=active_ontology_version_id,
        current_objects=current_objects,
        current_properties=current_properties,
        current_links=current_links,
        current_actions=current_actions,
        definition=definition,
        current_interfaces=current_interfaces,
        current_functions=current_functions,
    )


def _active_object_types(
    repository: OntologyRepository,
    transaction: TransactionContext,
    ctx: RequestContext,
    active_ontology_version_id: str,
) -> dict[str, ObjectTypeRow]:
    rows = repository.object_types_for_version(
        transaction=transaction,
        tenant_id=ctx.tenant_id,
        ontology_version_id=active_ontology_version_id,
    )
    return _object_rows_by_api(rows)


def _active_properties(
    repository: OntologyRepository,
    transaction: TransactionContext,
    current_objects: Mapping[str, ObjectTypeRow],
) -> dict[str, Sequence[PropertyTypeRow]]:
    return {
        api_name: repository.properties_for_object_type(transaction=transaction, object_type_id=row["id"])
        for api_name, row in sorted(current_objects.items())
    }


def _active_actions(
    repository: OntologyRepository,
    transaction: TransactionContext,
    current_objects: Mapping[str, ObjectTypeRow],
) -> tuple[ActionTypeRow, ...]:
    rows: list[ActionTypeRow] = []
    for object_type in current_objects.values():
        rows.extend(repository.actions_for_target(transaction=transaction, object_type_id=object_type["id"]))
    return tuple(rows)


def _object_rows_by_api(rows: Sequence[ObjectTypeRow]) -> dict[str, ObjectTypeRow]:
    return {row["api_name"]: row for row in rows}


__all__ = ["build_ontology_migration_plan", "plan_ontology_migration"]
