"""Prompt-driven semantic interpretation for bounded Pipeline Builder previews."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, wait
from functools import partial

from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.language_model import (
    GovernedSemanticModelPort,
    ModelMediaReference,
    ModelMessage,
    ModelRequest,
    ModelResolution,
    ModelResponse,
)
from foundry_lite.application.services.pipeline_semantic_cache_coordinates import (
    semantic_cache_coordinates,
)
from foundry_lite.application.services.pipeline_semantic_config import (
    SemanticInterpretationSpec as SemanticInterpretationSpec,
)
from foundry_lite.application.services.pipeline_semantic_config import (
    semantic_interpretation_spec as semantic_interpretation_spec,
)
from foundry_lite.application.services.pipeline_semantic_inputs import (
    SemanticMediaInputUnsupported as SemanticMediaInputUnsupported,
)
from foundry_lite.application.services.pipeline_semantic_inputs import (
    render_semantic_prompt,
    require_semantic_envelope_classification,
    require_semantic_item_classification,
    selected_semantic_input,
    semantic_json_block,
    semantic_media_references,
    semantic_system_message,
)
from foundry_lite.application.services.pipeline_semantic_row_cache import (
    SemanticCacheContext,
    SemanticCacheCoordinates,
    SemanticRowCacheSession,
    cached_semantic_model_evidence,
    cached_semantic_model_response,
    fresh_semantic_model_evidence,
    semantic_request_fingerprint,
)
from foundry_lite.application.services.pipeline_semantic_schema import (
    SemanticOutputError,
    parse_semantic_model_output,
)
from foundry_lite.application.services.pipeline_semantic_trial_evidence import (
    SemanticTrialContext,
    SemanticTrialEvidence,
    semantic_trial_error,
    semantic_trial_failure,
    semantic_trial_success,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import FoundryLiteError, ValidationFailed

JsonObject = dict[str, object]
_DEFAULT_MODEL_TIMEOUT_SECONDS = 60
_ROW_LEVEL_ADAPTER_FAILURE_REASONS = frozenset({"structured_output_incomplete"})


def interpret_semantic_items(
    items: Sequence[Mapping[str, object]],
    *,
    spec: SemanticInterpretationSpec,
    gateway: GovernedSemanticModelPort,
    ctx: RequestContext,
    include_trial_evidence: bool = False,
    sensitive_fields: frozenset[str] = frozenset(),
    cache: SemanticRowCacheSession | None = None,
    cache_context: SemanticCacheContext | None = None,
    source_security_envelope: Mapping[str, object] | None = None,
    max_concurrency: int = 1,
    request_timeout_seconds: int = _DEFAULT_MODEL_TIMEOUT_SECONDS,
) -> list[JsonObject]:
    """Apply one governed model request per bounded preview item."""

    if source_security_envelope is not None:
        require_semantic_envelope_classification(source_security_envelope, spec.data_classification)
    effective_context = _effective_cache_context(cache, cache_context)
    _require_matching_cache_generation(spec, cache, effective_context)
    worker = partial(
        _interpret_item,
        spec=spec,
        gateway=gateway,
        ctx=ctx,
        include_trial_evidence=include_trial_evidence,
        sensitive_fields=sensitive_fields,
        cache=cache,
        cache_context=effective_context,
        request_timeout_seconds=_positive_timeout(request_timeout_seconds),
    )
    if len(items) < 2 or max_concurrency == 1:
        return [worker(item) for item in items]
    worker_count = min(len(items), _positive_concurrency(max_concurrency))
    return _interpret_concurrently(worker, items, worker_count)


def _interpret_concurrently(
    worker: Callable[[Mapping[str, object]], JsonObject],
    items: Sequence[Mapping[str, object]],
    worker_count: int,
) -> list[JsonObject]:
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="pipeline-semantic") as executor:
        futures = [executor.submit(worker, item) for item in items]
        wait(futures)
        return [future.result() for future in futures]


def _positive_timeout(value: int) -> int:
    if isinstance(value, bool) or value < 1:
        raise ValidationFailed("pipeline semantic request timeout must be positive")
    return value


def _positive_concurrency(value: int) -> int:
    if isinstance(value, bool) or value < 1:
        raise ValidationFailed("pipeline semantic concurrency must be positive")
    return value


def _effective_cache_context(
    cache: SemanticRowCacheSession | None,
    cache_context: SemanticCacheContext | None,
) -> SemanticCacheContext:
    if cache_context is not None:
        return cache_context
    if cache is not None:
        raise ValidationFailed("pipeline semantic cache requires an explicit resource and node ownership context")
    return SemanticCacheContext(
        pipeline_id="uncached",
        scope_kind="uncached",
        scope_id="uncached",
        node_id="uncached",
        descriptor_id="transform.use_llm",
        spec_version="1",
        cache_generation=1,
        resource_security_policy_fingerprint="uncached",
    )


def _require_matching_cache_generation(
    spec: SemanticInterpretationSpec,
    cache: SemanticRowCacheSession | None,
    cache_context: SemanticCacheContext,
) -> None:
    if cache is None or cache_context.cache_generation == spec.cache_generation:
        return
    raise ValidationFailed(
        "pipeline semantic cache generation does not match the node configuration",
        details={
            "cacheGeneration": cache_context.cache_generation,
            "configuredCacheGeneration": spec.cache_generation,
        },
    )


def _interpret_item(
    item: Mapping[str, object],
    *,
    spec: SemanticInterpretationSpec,
    gateway: GovernedSemanticModelPort,
    ctx: RequestContext,
    include_trial_evidence: bool,
    sensitive_fields: frozenset[str],
    cache: SemanticRowCacheSession | None,
    cache_context: SemanticCacheContext,
    request_timeout_seconds: int,
) -> JsonObject:
    require_semantic_item_classification(item, spec.data_classification)
    selected = selected_semantic_input(item, spec.input_fields)
    prompt = render_semantic_prompt(spec.prompt_template, item)
    media_references = semantic_media_references(item, spec.media_reference_field)
    request = _model_request(ctx, spec, selected, prompt, media_references, timeout_seconds=request_timeout_seconds)
    coordinates = semantic_cache_coordinates(
        spec,
        selected,
        request,
        media_references,
        item,
        cache_context,
    )
    trial_context = _trial_context(
        spec,
        selected,
        include_trial_evidence=include_trial_evidence,
        sensitive_fields=sensitive_fields,
    )
    cached = _cached_output(item, spec, request, coordinates, trial_context, cache, ctx)
    if cached is not None:
        return cached
    return _invoke_semantic_model(item, spec, request, coordinates, trial_context, cache, gateway, ctx)


def _trial_context(
    spec: SemanticInterpretationSpec,
    selected: Mapping[str, object],
    *,
    include_trial_evidence: bool,
    sensitive_fields: frozenset[str],
) -> SemanticTrialContext | None:
    if not include_trial_evidence:
        return None
    return SemanticTrialContext(
        selected_input=dict(selected),
        input_fields=spec.input_fields,
        output_schema=dict(spec.output_schema),
        model_alias=spec.model_alias,
        prompt_version_id=spec.prompt_version_id,
        prompt_mode=spec.prompt_mode,
        data_classification=spec.data_classification,
        sensitive_fields=sensitive_fields,
    )


def _cached_output(
    item: Mapping[str, object],
    spec: SemanticInterpretationSpec,
    request: ModelRequest,
    coordinates: SemanticCacheCoordinates,
    trial_context: SemanticTrialContext | None,
    cache: SemanticRowCacheSession | None,
    ctx: RequestContext,
) -> JsonObject | None:
    if not spec.skip_recomputing_rows or cache is None:
        return None
    record = cache.lookup(ctx, coordinates).record
    if record is None:
        return None
    evidence = cached_semantic_model_evidence(coordinates, record)
    cached_response = cached_semantic_model_response(record)
    trial = (
        semantic_trial_success(trial_context, request, cached_response, record.output_value) if trial_context else None
    )
    return _output_success(item, spec, evidence, record.output_value, trial)


def _invoke_semantic_model(
    item: Mapping[str, object],
    spec: SemanticInterpretationSpec,
    request: ModelRequest,
    coordinates: SemanticCacheCoordinates,
    trial_context: SemanticTrialContext | None,
    cache: SemanticRowCacheSession | None,
    gateway: GovernedSemanticModelPort,
    ctx: RequestContext,
) -> JsonObject:
    try:
        response = gateway.invoke(ctx, request)
    except AdapterError as exc:
        if not _is_row_level_adapter_failure(spec, exc):
            raise
        return _semantic_adapter_failure(item, spec, request, coordinates, trial_context, gateway, ctx, exc)
    cache_status = _cache_status(spec, cache)
    cache_key = cache.response_cache_key(ctx, coordinates, response) if spec.skip_recomputing_rows and cache else None
    evidence = fresh_semantic_model_evidence(
        coordinates,
        response,
        cache_status=cache_status,
        cache_key=cache_key,
    )
    try:
        output = parse_semantic_model_output(response.content, spec.output_schema)
    except SemanticOutputError as exc:
        return _semantic_output_failure(item, spec, request, response, evidence, trial_context, exc)
    output = _store_semantic_success(ctx, spec, coordinates, response, output, evidence, cache)
    trial = semantic_trial_success(trial_context, request, response, output) if trial_context else None
    return _output_success(item, spec, evidence, output, trial)


def _is_row_level_adapter_failure(spec: SemanticInterpretationSpec, error: AdapterError) -> bool:
    reason = error.failure.details.get("reason")
    is_item_failure = error.failure.kind == "timeout" or reason in _ROW_LEVEL_ADAPTER_FAILURE_REASONS
    return spec.output_mode == "with_errors" and is_item_failure


def _semantic_adapter_failure(
    item: Mapping[str, object],
    spec: SemanticInterpretationSpec,
    request: ModelRequest,
    coordinates: SemanticCacheCoordinates,
    trial_context: SemanticTrialContext | None,
    gateway: GovernedSemanticModelPort,
    ctx: RequestContext,
    error: AdapterError,
) -> JsonObject:
    resolution = gateway.resolve_model(ctx, spec.model_alias, spec.environment)
    response = _adapter_failure_response(request, resolution, coordinates, error)
    evidence = fresh_semantic_model_evidence(coordinates, response, cache_status="miss", cache_key=None)
    trial = semantic_trial_failure(trial_context, request, response, error) if trial_context else None
    return _output_error(item, spec, evidence, error, trial)


def _adapter_failure_response(
    request: ModelRequest,
    resolution: ModelResolution,
    coordinates: SemanticCacheCoordinates,
    error: AdapterError,
) -> ModelResponse:
    details = error.failure.details
    revision = resolution.revision
    model_hash = hashlib.sha256(f"{resolution.model_id}@{revision}".encode()).hexdigest()
    return ModelResponse(
        provider=resolution.provider,
        resolved_model_id=resolution.model_id,
        resolved_model_revision=revision,
        content="",
        finish_reason=str(details.get("stopReason") or "adapter_failure"),
        input_tokens=0,
        output_tokens=_non_negative_int(details.get("outputTokens")),
        provider_request_id=str(details.get("providerRequestId") or ""),
        model_hash=f"sha256:{model_hash}",
        prompt_hash=f"sha256:{coordinates.prompt_fingerprint}",
    )


def _non_negative_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _semantic_output_failure(
    item: Mapping[str, object],
    spec: SemanticInterpretationSpec,
    request: ModelRequest,
    response: ModelResponse,
    evidence: Mapping[str, object],
    trial_context: SemanticTrialContext | None,
    error: SemanticOutputError,
) -> JsonObject:
    trial = semantic_trial_failure(trial_context, request, response, error) if trial_context else None
    if spec.output_mode == "simple":
        raise semantic_trial_error(error, trial) from error
    return _output_error(item, spec, evidence, error, trial)


def _store_semantic_success(
    ctx: RequestContext,
    spec: SemanticInterpretationSpec,
    coordinates: SemanticCacheCoordinates,
    response: ModelResponse,
    output: object,
    evidence: Mapping[str, object],
    cache: SemanticRowCacheSession | None,
) -> object:
    if not spec.skip_recomputing_rows or cache is None:
        return output
    stored = cache.store_success(
        ctx,
        coordinates=coordinates,
        response=response,
        output=output,
        evidence=evidence,
    )
    return stored.output_value


def _model_request(
    ctx: RequestContext,
    spec: SemanticInterpretationSpec,
    selected: Mapping[str, object],
    prompt: str,
    media_references: tuple[ModelMediaReference, ...],
    *,
    timeout_seconds: int,
) -> ModelRequest:
    messages = (
        ModelMessage(role="system", content=semantic_system_message(spec)),
        ModelMessage(
            role="user",
            content=f"{prompt}\n\n## input_json\n{semantic_json_block(selected)}",
            media_references=media_references,
        ),
    )
    request_hash = _semantic_request_hash(spec, messages)
    return ModelRequest(
        model_alias=spec.model_alias,
        expected_model_id=spec.expected_model_id,
        expected_model_revision=spec.expected_model_revision,
        messages=messages,
        environment=spec.environment,
        response_schema=semantic_json_block(spec.output_schema),
        temperature=spec.temperature,
        max_output_tokens=spec.max_output_tokens,
        thinking_mode=spec.thinking_mode,
        request_id=ctx.request_id,
        request_hash=request_hash,
        data_classification=spec.data_classification,
        region_requirement=spec.region_requirement,
        timeout_seconds=timeout_seconds,
    )


def _semantic_request_hash(spec: SemanticInterpretationSpec, messages: tuple[ModelMessage, ...]) -> str:
    return semantic_request_fingerprint(
        model_alias=spec.model_alias,
        prompt_version_id=spec.prompt_version_id,
        messages=messages,
        output_schema=spec.output_schema,
        temperature=spec.temperature,
        max_output_tokens=spec.max_output_tokens,
        thinking_mode=spec.thinking_mode,
    )


def _output_success(
    item: Mapping[str, object],
    spec: SemanticInterpretationSpec,
    evidence: Mapping[str, object],
    output: object,
    trial: SemanticTrialEvidence | None,
) -> JsonObject:
    row = dict(item)
    row[spec.output_column] = {"output": output, "error": None} if spec.output_mode == "with_errors" else output
    row["_pipelineModelEvidence"] = dict(evidence)
    if trial is not None:
        row["_pipelineModelTrialEvidence"] = dict(trial)
    return row


def _output_error(
    item: Mapping[str, object],
    spec: SemanticInterpretationSpec,
    evidence: Mapping[str, object],
    error: FoundryLiteError,
    trial: SemanticTrialEvidence | None,
) -> JsonObject:
    row = dict(item)
    payload = {"code": error.code, "message": str(error), "details": dict(error.details)}
    row[spec.output_column] = {"output": None, "error": payload} if spec.output_mode == "with_errors" else None
    row["_pipelineModelEvidence"] = {**dict(evidence), "outputError": payload}
    if trial is not None:
        row["_pipelineModelTrialEvidence"] = dict(trial)
    return row


def _cache_status(
    spec: SemanticInterpretationSpec,
    cache: SemanticRowCacheSession | None,
) -> str:
    if not spec.skip_recomputing_rows:
        return "bypass"
    return "miss" if cache is not None else "unavailable"
