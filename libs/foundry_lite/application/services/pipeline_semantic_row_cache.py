"""Exact-fingerprint durable caching for successful Pipeline semantic rows."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from foundry_lite.application.ports.language_model import (
    GovernedSemanticModelPort,
    ModelMediaReference,
    ModelMessage,
    ModelResolution,
    ModelResponse,
)
from foundry_lite.application.ports.semantic_row_cache_repository import (
    SemanticRowCacheRecord,
    SemanticRowCacheRepository,
)
from foundry_lite.application.ports.transaction_context import TransactionManager
from foundry_lite.application.primitives import _json_hash, _new_id, _now
from foundry_lite.domain.context import RequestContext

JsonObject = dict[str, object]
SemanticCacheScopeKind = Literal["branch", "deployment", "uncached"]


@dataclass(frozen=True)
class SemanticCacheContext:
    """Versioned Pipeline node identity that owns one semantic cache contract."""

    pipeline_id: str
    scope_kind: SemanticCacheScopeKind
    scope_id: str
    node_id: str
    descriptor_id: str
    spec_version: str
    cache_generation: int
    resource_security_policy_fingerprint: str


@dataclass(frozen=True)
class SemanticCacheCoordinates:
    """Exact request components that must remain stable for safe row reuse."""

    context: SemanticCacheContext
    model_alias: str
    environment: str
    prompt_version_id: str
    prompt_mode: str
    thinking_mode: str
    data_classification: str
    request_fingerprint: str
    prompt_fingerprint: str
    input_fingerprint: str
    media_fingerprint: str
    output_schema_fingerprint: str
    config_fingerprint: str
    resource_security_policy_fingerprint: str
    media_item_version_ids: tuple[str, ...]
    source_locator: Mapping[str, object] | None


@dataclass(frozen=True)
class SemanticCacheLookup:
    """One exact key lookup and its optional successful cached row."""

    cache_key: str
    record: SemanticRowCacheRecord | None


class SemanticRowCacheSession:
    """Application-owned transaction boundary for semantic row cache reads and writes."""

    def __init__(
        self,
        *,
        transaction_manager: TransactionManager,
        repository: SemanticRowCacheRepository,
        model_gateway: GovernedSemanticModelPort,
    ) -> None:
        self._transaction_manager = transaction_manager
        self._repository = repository
        self._model_gateway = model_gateway

    def lookup(
        self,
        ctx: RequestContext,
        coordinates: SemanticCacheCoordinates,
    ) -> SemanticCacheLookup:
        resolution = self._model_gateway.resolve_model(
            ctx,
            coordinates.model_alias,
            coordinates.environment,
        )
        cache_key = _cache_key(ctx, coordinates, resolution)
        with self._transaction_manager.begin() as transaction:
            record = self._repository.row_by_key(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                cache_key=cache_key,
            )
        return SemanticCacheLookup(cache_key=cache_key, record=record)

    def response_cache_key(
        self,
        ctx: RequestContext,
        coordinates: SemanticCacheCoordinates,
        response: ModelResponse,
    ) -> str:
        resolution = ModelResolution(
            provider=response.provider,
            model_id=response.resolved_model_id,
            revision=response.resolved_model_revision,
        )
        return _cache_key(ctx, coordinates, resolution)

    def store_success(
        self,
        ctx: RequestContext,
        *,
        coordinates: SemanticCacheCoordinates,
        response: ModelResponse,
        output: object,
        evidence: Mapping[str, object],
    ) -> SemanticRowCacheRecord:
        cache_key = self.response_cache_key(ctx, coordinates, response)
        record = _cache_record(ctx, coordinates, response, cache_key, output, evidence)
        with self._transaction_manager.begin() as transaction:
            return self._repository.insert_success(transaction=transaction, record=record)


def semantic_request_fingerprint(
    *,
    model_alias: str,
    prompt_version_id: str,
    messages: Sequence[ModelMessage],
    output_schema: Mapping[str, object],
    temperature: float,
    max_output_tokens: int,
    thinking_mode: str,
) -> str:
    """Hash all provider-visible prompt, input, media, schema, and generation settings."""

    return _json_hash(
        {
            "modelAlias": model_alias,
            "promptVersionId": prompt_version_id,
            "messages": [_message_payload(message) for message in messages],
            "outputSchema": dict(output_schema),
            "temperature": temperature,
            "maxOutputTokens": max_output_tokens,
            "thinkingMode": thinking_mode,
        }
    )


def semantic_prompt_fingerprint(messages: Sequence[ModelMessage]) -> str:
    return _json_hash({"messages": [_message_payload(message) for message in messages]})


def semantic_media_fingerprint(references: Sequence[ModelMediaReference]) -> str:
    return _json_hash({"mediaReferences": [_media_payload(reference) for reference in references]})


def semantic_resource_security_policy_fingerprint(
    *,
    permission: str,
    policy_reason: str,
    sensitive_fields: Sequence[str] = (),
    masked_fields: Sequence[str] = (),
    security_envelope: Mapping[str, object] | None = None,
) -> str:
    """Fingerprint the reusable resource-policy boundary without caller identity."""

    return _json_hash(
        {
            "contractVersion": "pipeline-semantic-resource-policy-v1",
            "permission": permission,
            "policyReason": policy_reason,
            "sensitiveFields": sorted(set(sensitive_fields)),
            "maskedFields": sorted(set(masked_fields)),
            "securityEnvelope": dict(security_envelope or {}),
        }
    )


def semantic_item_security_policy_fingerprint(
    base_fingerprint: str,
    item: Mapping[str, object],
) -> str:
    """Bind one row to its inherited security envelope inside the resource scope."""

    envelope = item.get("securityEnvelope")
    return semantic_scoped_security_policy_fingerprint(
        base_fingerprint,
        dict(envelope) if isinstance(envelope, Mapping) else {},
    )


def semantic_scoped_security_policy_fingerprint(
    base_fingerprint: str,
    security_envelope: Mapping[str, object],
) -> str:
    """Compose pipeline policy and one exact source/artifact security envelope."""

    return _json_hash(
        {
            "baseResourceSecurityPolicyFingerprint": base_fingerprint,
            "securityEnvelope": dict(security_envelope),
        }
    )


def fresh_semantic_model_evidence(
    coordinates: SemanticCacheCoordinates,
    response: ModelResponse,
    *,
    cache_status: str,
    cache_key: str | None,
) -> JsonObject:
    evidence: JsonObject = {
        "modelAlias": coordinates.model_alias,
        "resolvedModelId": response.resolved_model_id,
        "resolvedModelRevision": response.resolved_model_revision,
        "provider": response.provider,
        "promptVersionId": coordinates.prompt_version_id,
        "promptMode": coordinates.prompt_mode,
        "modelHash": response.model_hash,
        "promptHash": response.prompt_hash,
        "inputFingerprint": coordinates.input_fingerprint,
        "outputSchemaFingerprint": coordinates.output_schema_fingerprint,
        "inputTokens": response.input_tokens,
        "outputTokens": response.output_tokens,
        "finishReason": response.finish_reason,
        "providerRequestId": response.provider_request_id,
        "latencyMs": response.latency_ms,
        "thinkingMode": coordinates.thinking_mode,
        "mediaItemVersionIds": list(coordinates.media_item_version_ids),
        "sourceLocator": dict(coordinates.source_locator) if coordinates.source_locator else None,
        "dataClassification": coordinates.data_classification,
        "pipelineId": coordinates.context.pipeline_id,
        "cacheScopeKind": coordinates.context.scope_kind,
        "cacheScopeId": coordinates.context.scope_id,
        "cacheNodeId": coordinates.context.node_id,
        "cacheGeneration": coordinates.context.cache_generation,
        "resourceSecurityPolicyFingerprint": coordinates.resource_security_policy_fingerprint,
        "cacheEligible": cache_status != "bypass",
        "cacheHit": False,
        "cacheStatus": cache_status,
    }
    if cache_key is not None:
        evidence["cacheKey"] = cache_key
    return evidence


def cached_semantic_model_evidence(
    coordinates: SemanticCacheCoordinates,
    record: SemanticRowCacheRecord,
) -> JsonObject:
    evidence = dict(record.model_evidence)
    evidence.update(
        {
            "inputFingerprint": coordinates.input_fingerprint,
            "sourceLocator": dict(coordinates.source_locator) if coordinates.source_locator else None,
            "pipelineId": coordinates.context.pipeline_id,
            "cacheScopeKind": coordinates.context.scope_kind,
            "cacheScopeId": coordinates.context.scope_id,
            "cacheNodeId": coordinates.context.node_id,
            "cacheGeneration": coordinates.context.cache_generation,
            "resourceSecurityPolicyFingerprint": coordinates.resource_security_policy_fingerprint,
            "cacheEligible": True,
            "cacheHit": True,
            "cacheStatus": "hit",
            "cacheKey": record.cache_key,
            "cacheGenerationInputTokens": evidence.get("inputTokens", 0),
            "cacheGenerationOutputTokens": evidence.get("outputTokens", 0),
            "inputTokens": 0,
            "outputTokens": 0,
            "finishReason": "cache_hit",
        }
    )
    return evidence


def cached_semantic_model_response(record: SemanticRowCacheRecord) -> ModelResponse:
    """Reconstruct no-egress response facts for safe interactive trial evidence."""

    evidence = record.model_evidence
    return ModelResponse(
        provider=record.provider,
        resolved_model_id=record.resolved_model_id,
        resolved_model_revision=record.resolved_model_revision,
        content=json.dumps(record.output_value, ensure_ascii=True, sort_keys=True, default=str),
        finish_reason="cache_hit",
        input_tokens=0,
        output_tokens=0,
        model_hash=str(evidence.get("modelHash") or ""),
        prompt_hash=str(evidence.get("promptHash") or ""),
    )


def _cache_key(
    ctx: RequestContext,
    coordinates: SemanticCacheCoordinates,
    resolution: ModelResolution,
) -> str:
    return _json_hash(
        {
            "contractVersion": "pipeline-semantic-row-cache-v2",
            "tenantId": ctx.tenant_id,
            "pipelineId": coordinates.context.pipeline_id,
            "scopeKind": coordinates.context.scope_kind,
            "scopeId": coordinates.context.scope_id,
            "nodeId": coordinates.context.node_id,
            "descriptorId": coordinates.context.descriptor_id,
            "specVersion": coordinates.context.spec_version,
            "cacheGeneration": coordinates.context.cache_generation,
            "resourceSecurityPolicyFingerprint": coordinates.resource_security_policy_fingerprint,
            "modelAlias": coordinates.model_alias,
            "environment": coordinates.environment,
            "provider": resolution.provider,
            "resolvedModelId": resolution.model_id,
            "resolvedModelRevision": resolution.revision,
            "promptVersionId": coordinates.prompt_version_id,
            "promptFingerprint": coordinates.prompt_fingerprint,
            "inputFingerprint": coordinates.input_fingerprint,
            "mediaFingerprint": coordinates.media_fingerprint,
            "outputSchemaFingerprint": coordinates.output_schema_fingerprint,
            "configFingerprint": coordinates.config_fingerprint,
            "requestFingerprint": coordinates.request_fingerprint,
            "dataClassification": coordinates.data_classification,
        }
    )


def _cache_record(
    ctx: RequestContext,
    coordinates: SemanticCacheCoordinates,
    response: ModelResponse,
    cache_key: str,
    output: object,
    evidence: Mapping[str, object],
) -> SemanticRowCacheRecord:
    return SemanticRowCacheRecord(
        semantic_row_cache_id=_new_id("semantic_cache"),
        tenant_id=ctx.tenant_id,
        cache_key=cache_key,
        request_fingerprint=coordinates.request_fingerprint,
        pipeline_id=coordinates.context.pipeline_id,
        scope_kind=coordinates.context.scope_kind,
        scope_id=coordinates.context.scope_id,
        node_id=coordinates.context.node_id,
        descriptor_id=coordinates.context.descriptor_id,
        spec_version=coordinates.context.spec_version,
        cache_generation=coordinates.context.cache_generation,
        resource_security_policy_fingerprint=coordinates.resource_security_policy_fingerprint,
        model_alias=coordinates.model_alias,
        environment=coordinates.environment,
        resolved_model_id=response.resolved_model_id,
        resolved_model_revision=response.resolved_model_revision,
        provider=response.provider,
        prompt_version_id=coordinates.prompt_version_id,
        prompt_fingerprint=coordinates.prompt_fingerprint,
        input_fingerprint=coordinates.input_fingerprint,
        media_fingerprint=coordinates.media_fingerprint,
        output_schema_fingerprint=coordinates.output_schema_fingerprint,
        config_fingerprint=coordinates.config_fingerprint,
        output_value=output,
        model_evidence=dict(evidence),
        created_at=_now(),
    )


def _message_payload(message: ModelMessage) -> JsonObject:
    return {
        "role": message.role,
        "content": message.content,
        "mediaReferences": [_media_payload(reference) for reference in message.media_references],
    }


def _media_payload(reference: ModelMediaReference) -> JsonObject:
    return {
        "mediaItemVersionId": reference.media_item_version_id,
        "mimeType": reference.mime_type,
        "contentHash": reference.content_hash,
        "sourceLocator": dict(reference.source_locator),
    }
