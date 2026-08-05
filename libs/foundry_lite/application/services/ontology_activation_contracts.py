"""Cross-resource contracts used only by Ontology activation orchestration."""

from __future__ import annotations

from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.media_repository import MediaRepository
from foundry_lite.application.ports.ontology_repository import FunctionTypeDefinition
from foundry_lite.application.services.aip.visual_builder import VisualBuilderService
from foundry_lite.application.services.ontology_function_validation import (
    import_function_types as import_function_types,
)
from foundry_lite.application.services.ontology_function_validation import (
    validate_ontology_functions,
)
from foundry_lite.application.services.ontology_interface_validation import (
    import_action_types as import_action_types,
)
from foundry_lite.application.services.ontology_interface_validation import (
    import_interface_types as import_interface_types,
)
from foundry_lite.application.services.ontology_interface_validation import (
    object_type_implements as object_type_implements,
)
from foundry_lite.application.services.ontology_interface_validation import (
    validate_ontology_interfaces,
)
from foundry_lite.application.services.ontology_media_validation import validate_ontology_media_sets
from foundry_lite.application.services.ontology_validation import (
    DatasetColumnsLookup,
    validate_ontology_definition,
)
from foundry_lite.application.services.ontology_validation import (
    ontology_validation_result as ontology_validation_result,
)
from foundry_lite.application.services.ontology_yaml import YamlObject
from foundry_lite.domain.context import RequestContext


def validate_activation_contracts(
    media_repository: MediaRepository,
    transaction: TransactionContext,
    ctx: RequestContext,
    definition: YamlObject,
    dataset_columns_for_ref: DatasetColumnsLookup,
    visual_builder: VisualBuilderService,
) -> dict[str, FunctionTypeDefinition]:
    """Validate media, object, interface, Action, and function references together."""
    validate_ontology_media_sets(media_repository, transaction, ctx, definition)
    validate_ontology_definition(transaction, ctx, definition, dataset_columns_for_ref)
    validate_ontology_interfaces(definition)
    return validate_ontology_functions(definition, ctx, visual_builder)
