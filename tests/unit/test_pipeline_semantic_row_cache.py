from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest
from foundry_lite.application.ports.language_model import (
    ModelRequest,
    ModelResolution,
    ModelResponse,
)
from foundry_lite.application.services.pipeline_semantic_config import (
    SemanticInterpretationSpec,
)
from foundry_lite.application.services.pipeline_semantic_interpretation import (
    interpret_semantic_items,
    semantic_interpretation_spec,
)
from foundry_lite.application.services.pipeline_semantic_row_cache import (
    SemanticCacheContext,
    SemanticRowCacheSession,
    semantic_resource_security_policy_fingerprint,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.semantic_row_cache_repository import (
    SqlAlchemySemanticRowCacheRepository,
)
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine


class _SemanticGateway:
    def __init__(self, content: str = '{"label":"ok","score":1}') -> None:
        self.content = content
        self.revision = "2026-07-17-a"
        self.requests: list[ModelRequest] = []
        self.resolutions: list[tuple[str, str, str]] = []

    def resolve_model(
        self,
        ctx: RequestContext,
        model_alias: str,
        environment: str = "prod",
    ) -> ModelResolution:
        self.resolutions.append((ctx.tenant_id, model_alias, environment))
        return ModelResolution(
            provider="local-test",
            model_id="semantic-model",
            revision=self.revision,
        )

    def invoke(self, ctx: RequestContext, request: ModelRequest) -> ModelResponse:
        del ctx
        self.requests.append(request)
        return ModelResponse(
            provider="local-test",
            resolved_model_id="semantic-model",
            resolved_model_revision=self.revision,
            content=self.content,
            finish_reason="stop",
            input_tokens=7,
            output_tokens=3,
            prompt_hash="sha256:prompt",
        )


def test_successful_row_is_reused_with_explicit_hit_and_miss_evidence(tmp_path: Path) -> None:
    engine, gateway, cache = _runtime(tmp_path)
    ctx = RequestContext(tenant_id="tenant-a", request_id="request-a")
    spec = semantic_interpretation_spec(_config())
    cache_context = _cache_context()

    first = interpret_semantic_items(
        [_item()],
        spec=spec,
        gateway=gateway,
        ctx=ctx,
        cache=cache,
        cache_context=cache_context,
    )
    second = interpret_semantic_items(
        [_item()],
        spec=spec,
        gateway=gateway,
        ctx=ctx,
        cache=cache,
        cache_context=cache_context,
    )

    first_evidence = first[0]["_pipelineModelEvidence"]
    second_evidence = second[0]["_pipelineModelEvidence"]
    assert isinstance(first_evidence, dict) and first_evidence["cacheStatus"] == "miss"
    assert isinstance(second_evidence, dict) and second_evidence["cacheStatus"] == "hit"
    assert second_evidence["cacheHit"] is True
    assert second_evidence["inputTokens"] == 0
    assert second_evidence["cacheGenerationInputTokens"] == 7
    assert first_evidence["cacheKey"] == second_evidence["cacheKey"]
    assert len(gateway.requests) == 1
    assert _cache_count(engine) == 1


def test_skip_recomputing_rows_false_bypasses_cache_reads_and_writes(tmp_path: Path) -> None:
    engine, gateway, cache = _runtime(tmp_path)
    ctx = RequestContext(tenant_id="tenant-a", request_id="request-a")
    spec = semantic_interpretation_spec({**_config(), "skipRecomputingRows": False})

    rows = interpret_semantic_items(
        [_item(), _item()],
        spec=spec,
        gateway=gateway,
        ctx=ctx,
        cache=cache,
        cache_context=_cache_context(),
    )

    assert len(gateway.requests) == 2
    assert all(cast(Mapping[str, object], row["_pipelineModelEvidence"])["cacheStatus"] == "bypass" for row in rows)
    assert _cache_count(engine) == 0


def test_invalid_model_output_is_never_cached(tmp_path: Path) -> None:
    engine, gateway, cache = _runtime(tmp_path, content="not-json")
    ctx = RequestContext(tenant_id="tenant-a", request_id="request-a")
    config = {**_config(), "outputMode": "with_errors"}
    spec = semantic_interpretation_spec(config)
    cache_context = _cache_context()

    first = interpret_semantic_items(
        [_item()],
        spec=spec,
        gateway=gateway,
        ctx=ctx,
        cache=cache,
        cache_context=cache_context,
    )
    second = interpret_semantic_items(
        [_item()],
        spec=spec,
        gateway=gateway,
        ctx=ctx,
        cache=cache,
        cache_context=cache_context,
    )

    first_result = cast(Mapping[str, object], first[0]["interpretation"])
    second_result = cast(Mapping[str, object], second[0]["interpretation"])
    first_error = cast(Mapping[str, object], first_result["error"])
    second_error = cast(Mapping[str, object], second_result["error"])
    assert first_error["code"] == "PIPELINE_SEMANTIC_OUTPUT_INVALID"
    assert second_error["code"] == "PIPELINE_SEMANTIC_OUTPUT_INVALID"
    assert len(gateway.requests) == 2
    assert _cache_count(engine) == 0


def test_every_semantic_input_and_version_coordinate_invalidates_cache(tmp_path: Path) -> None:
    engine, gateway, cache = _runtime(tmp_path)
    ctx = RequestContext(tenant_id="tenant-a", request_id="request-a")

    _run(cache, gateway, ctx, _config(), _item())
    _run(cache, gateway, ctx, _config(), _item(text="changed input"))
    _run(cache, gateway, ctx, {**_config(), "promptVersionId": "prompt@2"}, _item())
    _run(cache, gateway, ctx, {**_config(), "promptTemplate": "Reinterpret {{text}}"}, _item())
    _run(cache, gateway, ctx, _schema_changed_config(), _item())
    _run(cache, gateway, ctx, _temperature_changed_config(), _item())
    gateway.revision = "2026-07-17-b"
    _run(cache, gateway, ctx, _config(), _item())
    _run(cache, gateway, ctx, _vision_config(), _vision_item("sha256:image-a"))
    _run(cache, gateway, ctx, _vision_config(), _vision_item("sha256:image-b"))

    assert len(gateway.requests) == 9
    assert _cache_count(engine) == 9


def test_cache_key_is_tenant_scoped(tmp_path: Path) -> None:
    engine, gateway, cache = _runtime(tmp_path)
    spec = semantic_interpretation_spec(_config())

    for tenant_id in ("tenant-a", "tenant-b"):
        interpret_semantic_items(
            [_item()],
            spec=spec,
            gateway=gateway,
            ctx=RequestContext(tenant_id=tenant_id, request_id=f"request-{tenant_id}"),
            cache=cache,
            cache_context=_cache_context(),
        )

    assert len(gateway.requests) == 2
    assert _cache_count(engine) == 2


def test_cache_is_owned_by_pipeline_scope_node_generation_and_security_policy(
    tmp_path: Path,
) -> None:
    engine, gateway, cache = _runtime(tmp_path)
    spec = semantic_interpretation_spec(_config())
    collaborators = (
        RequestContext(
            tenant_id="tenant-a",
            actor_user_id="engineer-a",
            request_id="request-a",
            roles=("data_engineer",),
        ),
        RequestContext(
            tenant_id="tenant-a",
            actor_user_id="engineer-b",
            request_id="request-b",
            roles=("data_engineer",),
        ),
    )

    for ctx in collaborators:
        _interpret(cache, gateway, ctx, spec, _cache_context())
    for context in (
        _cache_context(pipeline_id="pipeline-b"),
        _cache_context(scope_id="branch-b"),
        _cache_context(node_id="semantic-b"),
        _cache_context(resource_security_policy_fingerprint="sha256:policy-b"),
    ):
        _interpret(cache, gateway, collaborators[0], spec, context)
    _interpret(
        cache,
        gateway,
        collaborators[0],
        semantic_interpretation_spec({**_config(), "cacheGeneration": 2}),
        _cache_context(cache_generation=2),
    )

    assert len(gateway.requests) == 6
    assert _cache_count(engine) == 6


def test_cache_enabled_without_explicit_ownership_context_fails_closed(
    tmp_path: Path,
) -> None:
    _, gateway, cache = _runtime(tmp_path)

    with pytest.raises(ValidationFailed, match="explicit resource and node ownership"):
        interpret_semantic_items(
            [_item()],
            spec=semantic_interpretation_spec(_config()),
            gateway=gateway,
            ctx=RequestContext(tenant_id="tenant-a", request_id="request-a"),
            cache=cache,
        )


def test_cache_context_generation_must_match_node_configuration(
    tmp_path: Path,
) -> None:
    _, gateway, cache = _runtime(tmp_path)

    with pytest.raises(ValidationFailed, match="generation does not match"):
        interpret_semantic_items(
            [_item()],
            spec=semantic_interpretation_spec({**_config(), "cacheGeneration": 2}),
            gateway=gateway,
            ctx=RequestContext(tenant_id="tenant-a", request_id="request-a"),
            cache=cache,
            cache_context=_cache_context(cache_generation=1),
        )


def test_resource_security_fingerprint_distinguishes_effective_masking_policy() -> None:
    privileged = semantic_resource_security_policy_fingerprint(
        permission="pipeline:write",
        policy_reason="role matched",
        sensitive_fields=("customer_ssn",),
        masked_fields=(),
    )
    redacted = semantic_resource_security_policy_fingerprint(
        permission="pipeline:write",
        policy_reason="role matched",
        sensitive_fields=("customer_ssn",),
        masked_fields=("customer_ssn",),
    )

    assert privileged != redacted


def _run(
    cache: SemanticRowCacheSession,
    gateway: _SemanticGateway,
    ctx: RequestContext,
    config: Mapping[str, object],
    item: Mapping[str, object],
) -> None:
    interpret_semantic_items(
        [item],
        spec=semantic_interpretation_spec(config),
        gateway=gateway,
        ctx=ctx,
        cache=cache,
        cache_context=_cache_context(
            cache_generation=_configured_cache_generation(config),
        ),
    )


def _configured_cache_generation(config: Mapping[str, object]) -> int:
    value = config.get("cacheGeneration", 1)
    assert isinstance(value, int) and not isinstance(value, bool)
    return value


def _interpret(
    cache: SemanticRowCacheSession,
    gateway: _SemanticGateway,
    ctx: RequestContext,
    spec: SemanticInterpretationSpec,
    cache_context: SemanticCacheContext,
) -> None:
    interpret_semantic_items(
        [_item()],
        spec=spec,
        gateway=gateway,
        ctx=ctx,
        cache=cache,
        cache_context=cache_context,
    )


def _runtime(
    tmp_path: Path,
    *,
    content: str = '{"label":"ok","score":1}',
) -> tuple[Engine, _SemanticGateway, SemanticRowCacheSession]:
    engine = create_engine(f"sqlite:///{tmp_path / 'semantic-cache.db'}", future=True)
    db.create_database(engine)
    gateway = _SemanticGateway(content)
    repository = SqlAlchemySemanticRowCacheRepository(engine)
    return (
        engine,
        gateway,
        SemanticRowCacheSession(
            transaction_manager=engine,
            repository=repository,
            model_gateway=gateway,
        ),
    )


def _cache_count(engine: Engine) -> int:
    with engine.begin() as transaction:
        return int(transaction.execute(select(func.count()).select_from(db.pipeline_semantic_row_cache)).scalar_one())


def _config() -> dict[str, object]:
    return {
        "modelAlias": "semantic-default",
        "promptVersionId": "prompt@1",
        "promptTemplate": "Classify {{text}}",
        "outputColumn": "interpretation",
        "inputFields": ["text"],
        "outputSchema": _schema(),
        "dataClassification": "public",
        "skipRecomputingRows": True,
        "cacheGeneration": 1,
        "modelParameters": {"temperature": 0, "maxOutputTokens": 256, "thinkingMode": "disabled"},
    }


def _schema_changed_config() -> dict[str, object]:
    schema = _schema()
    schema["properties"] = {
        "label": {"type": "string"},
        "score": {"type": "integer"},
        "explanation": {"type": "string"},
    }
    return {**_config(), "outputSchema": schema}


def _temperature_changed_config() -> dict[str, object]:
    return {
        **_config(),
        "modelParameters": {"temperature": 0.5, "maxOutputTokens": 256, "thinkingMode": "disabled"},
    }


def _vision_config() -> dict[str, object]:
    return {
        **_config(),
        "promptTemplate": "Classify the image.",
        "inputFields": ["text"],
        "mediaReferenceField": "media",
    }


def _schema() -> dict[str, object]:
    return {
        "type": "object",
        "required": ["label"],
        "properties": {"label": {"type": "string"}, "score": {"type": "integer"}},
        "additionalProperties": False,
    }


def _item(*, text: str = "base input") -> dict[str, object]:
    return {
        "text": text,
        "securityEnvelope": {"classification": "public"},
        "sourceLocator": {"row": 1},
    }


def _vision_item(content_hash: str) -> dict[str, object]:
    return {
        **_item(),
        "media": {
            "mediaItemVersionId": "media-version-a",
            "mimeType": "image/png",
            "contentHash": content_hash,
            "sourceLocator": {"frame": 0},
        },
    }


def _cache_context(
    *,
    pipeline_id: str = "pipeline-a",
    scope_id: str = "branch-a",
    node_id: str = "semantic-a",
    cache_generation: int = 1,
    resource_security_policy_fingerprint: str = "sha256:policy-a",
) -> SemanticCacheContext:
    return SemanticCacheContext(
        pipeline_id=pipeline_id,
        scope_kind="branch",
        scope_id=scope_id,
        node_id=node_id,
        descriptor_id="transform.use_llm",
        spec_version="1",
        cache_generation=cache_generation,
        resource_security_policy_fingerprint=resource_security_policy_fingerprint,
    )
