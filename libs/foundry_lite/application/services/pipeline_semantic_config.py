"""Typed configuration parsing for Pipeline semantic interpretation nodes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, cast

from foundry_lite.application.ports.language_model import ModelThinkingMode
from foundry_lite.application.services.pipeline_semantic_prompts import (
    SemanticPromptMode,
    resolve_semantic_prompt_contract,
    resolve_semantic_prompt_mode,
)
from foundry_lite.application.services.pipeline_semantic_schema import (
    validate_semantic_output_schema,
)
from foundry_lite.domain.errors import ValidationFailed

JsonObject = dict[str, object]
SemanticOutputMode = Literal["simple", "with_errors"]
SemanticCachePolicy = Literal["referenced_fields"]
_MAX_PROMPT_CHARACTERS = 32_000


@dataclass(frozen=True)
class SemanticInterpretationSpec:
    """Pinned prompt, model, output, and cache coordinates for one Use LLM node."""

    model_alias: str
    expected_model_id: str | None
    expected_model_revision: str | None
    prompt_version_id: str
    prompt_template: str
    output_column: str
    input_fields: tuple[str, ...]
    output_schema: Mapping[str, object]
    data_classification: str
    prompt_mode: SemanticPromptMode = "text"
    system_prompt: str = ""
    output_mode: SemanticOutputMode = "simple"
    skip_recomputing_rows: bool = False
    cache_generation: int = 1
    cache_policy: SemanticCachePolicy = "referenced_fields"
    media_reference_field: str | None = None
    environment: str = "prod"
    region_requirement: str | None = None
    temperature: float = 0.0
    max_output_tokens: int = 1024
    thinking_mode: ModelThinkingMode = "provider_default"


def semantic_interpretation_spec(config: Mapping[str, object]) -> SemanticInterpretationSpec:
    """Validate graph config and construct a pinned semantic interpretation spec."""
    model_parameters = _object_value(config.get("modelParameters"), "modelParameters")
    expected_model_id, expected_model_revision = _model_pin(config)
    output_mode = _output_mode(config.get("outputMode"))
    schema = _object_value(config.get("outputSchema"), "outputSchema", is_required=True)
    validate_semantic_output_schema(schema)
    media_reference_field = _optional_text(config.get("mediaReferenceField"))
    prompt_mode, is_explicit = resolve_semantic_prompt_mode(config.get("promptMode"), media_reference_field)
    prompt_template, system_prompt = resolve_semantic_prompt_contract(
        config,
        prompt_mode,
        is_explicit,
        media_reference_field,
    )
    if len(prompt_template) > _MAX_PROMPT_CHARACTERS:
        raise ValidationFailed("pipeline prompt template exceeds the preview limit")
    return SemanticInterpretationSpec(
        model_alias=_required_text(config, "modelAlias"),
        expected_model_id=expected_model_id,
        expected_model_revision=expected_model_revision,
        prompt_version_id=_required_text(config, "promptVersionId"),
        prompt_template=prompt_template,
        output_column=_required_text(config, "outputColumn"),
        input_fields=_string_list(config.get("inputFields"), "inputFields"),
        output_schema=schema,
        data_classification=_required_text(config, "dataClassification"),
        prompt_mode=prompt_mode,
        system_prompt=system_prompt,
        output_mode=output_mode,
        skip_recomputing_rows=_boolean_value(config.get("skipRecomputingRows"), False),
        cache_generation=_cache_generation(config.get("cacheGeneration")),
        cache_policy=_cache_policy(config.get("cachePolicy")),
        media_reference_field=media_reference_field,
        environment=_optional_text(config.get("environment")) or "prod",
        region_requirement=_optional_text(config.get("regionRequirement")),
        temperature=_temperature(model_parameters.get("temperature")),
        max_output_tokens=_max_output_tokens(model_parameters.get("maxOutputTokens")),
        thinking_mode=_thinking_mode(model_parameters.get("thinkingMode")),
    )


def _model_pin(config: Mapping[str, object]) -> tuple[str | None, str | None]:
    model_id = _optional_text(config.get("expectedModelId"))
    revision = _optional_text(config.get("expectedModelRevision"))
    if (model_id is None) != (revision is None):
        raise ValidationFailed(
            "pipeline semantic promoted model pin requires both ID and revision",
            details={
                "hasExpectedModelId": model_id is not None,
                "hasExpectedModelRevision": revision is not None,
            },
        )
    return model_id, revision


def _required_text(config: Mapping[str, object], field_name: str) -> str:
    value = config.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValidationFailed("pipeline semantic config field is required", details={"field": field_name})
    return value.strip()


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _string_list(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValidationFailed("pipeline semantic string list is required", details={"field": field_name})
    return tuple(str(item).strip() for item in value)


def _object_value(value: object, field_name: str, *, is_required: bool = False) -> JsonObject:
    if value is None and not is_required:
        return {}
    if not isinstance(value, Mapping) or (is_required and not value):
        raise ValidationFailed("pipeline semantic object config is invalid", details={"field": field_name})
    return dict(value)


def _boolean_value(value: object, is_default: bool) -> bool:
    if value is None:
        return is_default
    if not isinstance(value, bool):
        raise ValidationFailed("pipeline semantic boolean config is invalid")
    return value


def _output_mode(value: object) -> SemanticOutputMode:
    normalized = _optional_text(value) or "simple"
    if normalized not in {"simple", "with_errors"}:
        raise ValidationFailed("pipeline semantic output mode is invalid", details={"outputMode": normalized})
    return cast(SemanticOutputMode, normalized)


def _thinking_mode(value: object) -> ModelThinkingMode:
    normalized = _optional_text(value) or "provider_default"
    if normalized not in {"provider_default", "disabled", "adaptive"}:
        raise ValidationFailed(
            "pipeline semantic thinking mode is invalid",
            details={"thinkingMode": normalized},
        )
    return cast(ModelThinkingMode, normalized)


def _temperature(value: object) -> float:
    if value is None:
        return 0.0
    if not isinstance(value, int | float) or isinstance(value, bool) or not 0 <= float(value) <= 2:
        raise ValidationFailed("pipeline semantic temperature must be between 0 and 2")
    return float(value)


def _max_output_tokens(value: object) -> int:
    if value is None:
        return 1024
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 32_768:
        raise ValidationFailed("pipeline semantic max output tokens are invalid")
    return value


def _cache_generation(value: object) -> int:
    if value is None:
        return 1
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 2_147_483_647:
        raise ValidationFailed(
            "pipeline semantic cache generation must be a positive integer",
            details={"cacheGeneration": value},
        )
    return value


def _cache_policy(value: object) -> SemanticCachePolicy:
    normalized = _optional_text(value) or "referenced_fields"
    if normalized != "referenced_fields":
        raise ValidationFailed(
            "pipeline semantic cache policy is unsupported",
            details={"cachePolicy": normalized},
        )
    return cast(SemanticCachePolicy, normalized)
