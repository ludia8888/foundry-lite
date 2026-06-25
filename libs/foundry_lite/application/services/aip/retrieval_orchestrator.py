"""AIP Retrieval Orchestrator baseline (P0o, §8.5).

The orchestrator converts user turn state into authorized, versioned context
items. This first product slice supports ontology object context: explicit
state objects are authoritatively re-read, and object keyword hits are re-read
before they become model-visible context.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from foundry_lite.application.ports import ObjectPayload, ObjectQueryResult
from foundry_lite.application.ports.context_provider import (
    ContextRetrievalError,
    ContextRetrievalRequest,
    RetrievedContextItem,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import FoundryLiteError, NotFound


class RetrievalObjectQuery(Protocol):
    def get_object(
        self,
        object_type_api_name: str,
        object_id: str,
        *,
        ctx: RequestContext | None = None,
        include_explain: bool = False,
    ) -> ObjectPayload: ...

    def query_objects(
        self,
        object_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
        limit: int = 50,
        search_text: str | None = None,
    ) -> ObjectQueryResult: ...


@dataclass(frozen=True)
class ObjectRef:
    object_type: str
    object_id: str


@dataclass(frozen=True)
class RetrievalOrchestrator:
    """Retrieve object context through existing authorization boundaries."""

    object_query_service: RetrievalObjectQuery

    def retrieve_context(
        self, *, ctx: RequestContext, request: ContextRetrievalRequest
    ) -> tuple[RetrievedContextItem, ...]:
        _guard_request(ctx, request)
        items = self._candidate_items(ctx, request)
        return _pack_items(tuple(_dedupe_items(items)), request)

    def _candidate_items(
        self, ctx: RequestContext, request: ContextRetrievalRequest
    ) -> tuple[RetrievedContextItem, ...]:
        explicit = _explicit_object_ref(request.state_json)
        if explicit is not None:
            return (self._explicit_object_item(ctx, request, explicit),)
        object_type = _state_string(request.state_json, "objectType")
        if object_type is None:
            return ()
        return self._query_object_items(ctx, request, object_type)

    def _explicit_object_item(
        self, ctx: RequestContext, request: ContextRetrievalRequest, ref: ObjectRef
    ) -> RetrievedContextItem:
        try:
            payload = self.object_query_service.get_object(
                ref.object_type,
                ref.object_id,
                ctx=ctx,
                include_explain=True,
            )
        except NotFound as exc:
            raise _retrieval_failure("context_source_unavailable", exc) from exc
        return _object_item(payload, request, "object_authoritative_reread", 1.0)

    def _query_object_items(
        self, ctx: RequestContext, request: ContextRetrievalRequest, object_type: str
    ) -> tuple[RetrievedContextItem, ...]:
        try:
            result = self.object_query_service.query_objects(
                object_type,
                ctx=ctx,
                search_text=_normalized_query(request.query),
                limit=request.max_context_items,
            )
        except FoundryLiteError as exc:
            raise _retrieval_failure("context_search_unavailable", exc) from exc
        return tuple(
            _object_item(payload, request, "object_keyword_search_authoritative_reread", _rank_score(index))
            for index, payload in enumerate(self._reread_query_hits(ctx, result))
        )

    def _reread_query_hits(self, ctx: RequestContext, result: ObjectQueryResult) -> tuple[ObjectPayload, ...]:
        payloads: list[ObjectPayload] = []
        for item in result["items"]:
            try:
                payloads.append(
                    self.object_query_service.get_object(
                        item["objectType"],
                        item["objectId"],
                        ctx=ctx,
                        include_explain=False,
                    )
                )
            except NotFound:
                continue
        return tuple(payloads)


def _guard_request(ctx: RequestContext, request: ContextRetrievalRequest) -> None:
    if request.tenant_id != ctx.tenant_id or request.actor_user_id != ctx.actor_user_id:
        raise ContextRetrievalError("request_context_mismatch", "retrieval request does not match actor context")
    if not request.security_partition.startswith(f"{ctx.tenant_id}:"):
        raise ContextRetrievalError("security_partition_mismatch", "security partition is outside the tenant boundary")
    if request.max_context_items < 0 or request.max_context_tokens < 0:
        raise ContextRetrievalError("invalid_context_budget", "context budgets must be non-negative")


def _explicit_object_ref(state_json: Mapping[str, object]) -> ObjectRef | None:
    object_id = _state_string(state_json, "objectId")
    object_type = _state_string(state_json, "objectType")
    if object_id is None:
        return None
    if object_type is None:
        raise ContextRetrievalError("invalid_state_context", "objectId requires objectType")
    return ObjectRef(object_type=object_type, object_id=object_id)


def _object_item(
    payload: Mapping[str, object],
    request: ContextRetrievalRequest,
    retrieval_method: str,
    relevance_score: float,
) -> RetrievedContextItem:
    text = _object_text(payload)
    source_ref = f"object://{payload['objectType']}/{payload['objectId']}"
    source_version = str(payload["objectVersion"])
    content_hash = _hash_text(text)
    return RetrievedContextItem(
        context_id=f"ctx-{_short_hash(source_ref, source_version, content_hash)}",
        kind="object",
        text=text,
        source_ref=source_ref,
        source_version=source_version,
        content_hash=content_hash,
        relevance_score=relevance_score,
        retrieval_method=retrieval_method,
        security_partition=request.security_partition,
        token_estimate=_token_estimate(text),
    )


def _object_text(payload: Mapping[str, object]) -> str:
    visible_payload = {
        "objectId": payload["objectId"],
        "objectType": payload["objectType"],
        "objectVersion": payload["objectVersion"],
        "properties": payload.get("properties", {}),
    }
    return _json_block(visible_payload)


def _pack_items(
    items: tuple[RetrievedContextItem, ...], request: ContextRetrievalRequest
) -> tuple[RetrievedContextItem, ...]:
    packed: list[RetrievedContextItem] = []
    token_total = 0
    for item in items:
        if len(packed) >= request.max_context_items:
            break
        if token_total + item.token_estimate > request.max_context_tokens:
            continue
        packed.append(item)
        token_total += item.token_estimate
    if items and not packed:
        raise ContextRetrievalError("context_budget_exceeded", "no retrieved context item fits the token budget")
    return tuple(packed)


def _dedupe_items(items: tuple[RetrievedContextItem, ...]) -> tuple[RetrievedContextItem, ...]:
    seen: set[str] = set()
    deduped: list[RetrievedContextItem] = []
    for item in items:
        key = f"{item.source_ref}@{item.source_version}"
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    return tuple(deduped)


def _state_string(state_json: Mapping[str, object], key: str) -> str | None:
    value = state_json.get(key)
    return value if isinstance(value, str) and value else None


def _retrieval_failure(reason: str, exc: FoundryLiteError) -> ContextRetrievalError:
    return ContextRetrievalError(reason, exc.message)


def _normalized_query(value: str) -> str:
    return " ".join(value.split())


def _rank_score(index: int) -> float:
    return 1.0 / float(index + 1)


def _token_estimate(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def _json_block(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _hash_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode()).hexdigest()}"


def _short_hash(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]
