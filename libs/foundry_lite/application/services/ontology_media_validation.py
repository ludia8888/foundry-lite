"""Cross-resource validation for Ontology media and attachment declarations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.media_repository import MediaRepository
from foundry_lite.application.services.ontology_yaml import YamlObject, mapping_sequence, required_str
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed


def validate_ontology_media_sets(
    repository: MediaRepository,
    transaction: TransactionContext,
    ctx: RequestContext,
    definition: YamlObject,
) -> None:
    """Fail activation when a declared media contract has no tenant Media Set."""
    missing = []
    for location, media_set_ref in _declared_media_sets(definition):
        namespace, name = media_set_ref.split(".", 1)
        row = repository.media_set_by_ref(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            namespace=namespace,
            name=name,
        )
        if row is None:
            missing.append({"location": location, "mediaSet": media_set_ref})
    if missing:
        raise ValidationFailed("ontology references Media Sets that do not exist", details={"missing": missing})


def _declared_media_sets(definition: YamlObject) -> tuple[tuple[str, str], ...]:
    refs: list[tuple[str, str]] = []
    for object_type in mapping_sequence(definition, "objectTypes"):
        object_name = required_str(object_type, "apiName")
        for prop in mapping_sequence(object_type, "properties"):
            if prop.get("type") in {"media_reference", "attachment"}:
                location = f"objectTypes.{object_name}.{required_str(prop, 'apiName')}"
                refs.append((location, required_str(prop, "mediaSet")))
    for action in mapping_sequence(definition, "actionTypes"):
        action_name = required_str(action, "apiName")
        for parameter in mapping_sequence(action, "parameters"):
            _collect_parameter_media_sets(parameter, f"actionTypes.{action_name}", refs)
    return tuple(refs)


def _collect_parameter_media_sets(
    parameter: Mapping[str, object],
    parent: str,
    refs: list[tuple[str, str]],
) -> None:
    name = required_str(parameter, "apiName")
    location = f"{parent}.{name}"
    data_type = required_str(parameter, "type")
    item_type = parameter.get("itemType")
    if data_type in {"media", "attachment"} or item_type in {"media", "attachment"}:
        refs.append((location, required_str(parameter, "mediaSet")))
    fields = parameter.get("fields")
    if isinstance(fields, Sequence) and not isinstance(fields, str | bytes):
        for field in fields:
            if isinstance(field, Mapping):
                _collect_parameter_media_sets(field, location, refs)
