"""Post-import validation of a persisted ontology version against dataset schemas.

Runs inside the activation transaction, after import and before the version
flips active. Single-dataset object types validate against their one dataset;
multi-datasource types validate per segment (primary key in EVERY datasource,
each property column in ITS datasource). Lives beside ``ontology_validation``
so the activation service stays a thin orchestrator within its size budget.
"""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.ontology_repository import ObjectTypeRow, OntologyRepository
from foundry_lite.application.services.ontology_datasource_validation import (
    DatasetColumnsLookup,
    validate_persisted_object_datasources,
)
from foundry_lite.application.services.ontology_validation import (
    validate_persisted_action_mutations,
    validate_persisted_link,
    validate_persisted_object_type,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.domain.ontology.datasources import is_multi_datasource_backing, primary_dataset_ref


def validate_persisted_ontology(
    repository: OntologyRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    ontology_version_id: str,
    dataset_columns_for_ref: DatasetColumnsLookup,
) -> None:
    """Validate every persisted object and link type of one ontology version."""
    object_rows = repository.object_types_for_version(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        ontology_version_id=ontology_version_id,
    )
    object_by_api = {row["api_name"]: row for row in object_rows}
    for object_type in object_rows:
        _validate_persisted_object_type_backing(repository, conn, ctx, object_type, dataset_columns_for_ref)
    for link in repository.link_types_for_version(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        ontology_version_id=ontology_version_id,
    ):
        dataset_ref = link["backing"].get("dataset")
        if not isinstance(dataset_ref, str) or not dataset_ref:
            raise ValidationFailed("persisted link dataset backing missing", details={"linkType": link["api_name"]})
        columns = dataset_columns_for_ref(conn, ctx, dataset_ref)
        validate_persisted_link(link, object_by_api, columns)


def _validate_persisted_object_type_backing(
    repository: OntologyRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    object_type: ObjectTypeRow,
    dataset_columns_for_ref: DatasetColumnsLookup,
) -> None:
    backing = object_type["backing"]
    if not isinstance(backing, Mapping):
        raise ValidationFailed("object backing must be a mapping")
    properties = repository.properties_for_object_type(transaction=conn, object_type_id=object_type["id"])
    actions = repository.actions_for_target(transaction=conn, object_type_id=object_type["id"])
    if is_multi_datasource_backing(backing):
        # Multi-datasource types validate each property against ITS segment's
        # dataset; only the action-mutation checks remain shared.
        validate_persisted_object_datasources(conn, ctx, object_type, properties, dataset_columns_for_ref)
        validate_persisted_action_mutations(actions, properties)
        return
    columns = dataset_columns_for_ref(conn, ctx, primary_dataset_ref(backing))
    validate_persisted_object_type(object_type, properties, actions, columns)
