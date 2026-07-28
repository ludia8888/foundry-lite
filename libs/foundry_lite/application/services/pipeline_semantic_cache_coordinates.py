"""Exact cache coordinates for one Pipeline semantic interpretation row."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.ports.language_model import ModelMediaReference, ModelRequest
from foundry_lite.application.primitives import _json_hash
from foundry_lite.application.services.pipeline_semantic_config import SemanticInterpretationSpec
from foundry_lite.application.services.pipeline_semantic_row_cache import (
    SemanticCacheContext,
    SemanticCacheCoordinates,
    semantic_item_security_policy_fingerprint,
    semantic_media_fingerprint,
    semantic_prompt_fingerprint,
)

JsonObject = dict[str, object]


def semantic_cache_coordinates(
    spec: SemanticInterpretationSpec,
    selected: Mapping[str, object],
    request: ModelRequest,
    media_references: Sequence[ModelMediaReference],
    item: Mapping[str, object],
    cache_context: SemanticCacheContext,
) -> SemanticCacheCoordinates:
    """Bind exact request inputs to resource, node, generation, and security ownership."""

    return SemanticCacheCoordinates(
        context=cache_context,
        model_alias=spec.model_alias,
        environment=spec.environment,
        prompt_version_id=spec.prompt_version_id,
        prompt_mode=spec.prompt_mode,
        thinking_mode=spec.thinking_mode,
        data_classification=spec.data_classification,
        request_fingerprint=request.request_hash,
        prompt_fingerprint=semantic_prompt_fingerprint(request.messages),
        input_fingerprint=_json_hash(dict(selected)),
        media_fingerprint=semantic_media_fingerprint(media_references),
        output_schema_fingerprint=_json_hash(dict(spec.output_schema)),
        config_fingerprint=_semantic_config_fingerprint(spec),
        resource_security_policy_fingerprint=semantic_item_security_policy_fingerprint(
            cache_context.resource_security_policy_fingerprint,
            item,
        ),
        media_item_version_ids=tuple(reference.media_item_version_id for reference in media_references),
        source_locator=_source_locator(item),
    )


def _semantic_config_fingerprint(spec: SemanticInterpretationSpec) -> str:
    return _json_hash(
        {
            "expectedModelId": spec.expected_model_id,
            "expectedModelRevision": spec.expected_model_revision,
            "outputColumn": spec.output_column,
            "inputFields": list(spec.input_fields),
            "outputMode": spec.output_mode,
            "promptMode": spec.prompt_mode,
            "mediaReferenceField": spec.media_reference_field,
            "environment": spec.environment,
            "regionRequirement": spec.region_requirement,
            "temperature": spec.temperature,
            "maxOutputTokens": spec.max_output_tokens,
            "thinkingMode": spec.thinking_mode,
            "dataClassification": spec.data_classification,
            "cacheGeneration": spec.cache_generation,
            "cachePolicy": spec.cache_policy,
        }
    )


def _source_locator(item: Mapping[str, object]) -> JsonObject | None:
    value = item.get("sourceLocator")
    return dict(value) if isinstance(value, Mapping) else None
