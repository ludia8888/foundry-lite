"""Unit tests for the AIP Retrieval Orchestrator baseline (P0o, §8.5)."""

from __future__ import annotations

from typing import Any

import pytest
from foundry_lite.application.ports import ObjectPayload, ObjectQueryItem, ObjectQueryResult
from foundry_lite.application.ports.adapter_failure import AdapterError, AdapterFailure
from foundry_lite.application.ports.content_index import ContentSearchHit, HybridContentQuery
from foundry_lite.application.ports.context_provider import (
    ContextRetrievalError,
    ContextRetrievalRequest,
    RetrievedContextItem,
)
from foundry_lite.application.services.aip.context_compiler import ContextCompileRequest, ContextCompilerService
from foundry_lite.application.services.aip.retrieval_orchestrator import (
    RetrievalContentSearch,
    RetrievalObjectQuery,
    RetrievalOrchestrator,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, ValidationFailed

from tests.conftest import prepare_indexed_demo

_CTX = RequestContext(
    tenant_id="tenant-demo",
    actor_user_id="ops-user",
    roles=("admin", "data_engineer", "ops_manager"),
    request_id="req-retrieval-orchestrator",
)


class _FakeObjectQuery:
    def __init__(self) -> None:
        self.reread_count = 0

    def query_objects(
        self,
        object_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
        limit: int = 50,
        search_text: str | None = None,
    ) -> ObjectQueryResult:
        return {
            "items": [
                {
                    "objectType": object_type_api_name,
                    "objectId": "O-1001",
                    "objectVersion": 1,
                    "properties": {"status": "STALE_INDEX_VALUE", "query": search_text, "limit": limit},
                }
            ],
            "nextCursor": None,
        }

    def get_object(
        self,
        object_type_api_name: str,
        object_id: str,
        *,
        ctx: RequestContext | None = None,
        include_explain: bool = False,
    ) -> ObjectPayload:
        self.reread_count += 1
        return {
            "objectType": object_type_api_name,
            "objectId": object_id,
            "objectVersion": 7,
            "properties": {"status": "AUTHORITATIVE_VALUE", "includeExplain": include_explain},
            "sourceDatasetVersionId": "dataset-version-1",
        }


class _MissingExplicitObjectQuery(_FakeObjectQuery):
    def get_object(
        self,
        object_type_api_name: str,
        object_id: str,
        *,
        ctx: RequestContext | None = None,
        include_explain: bool = False,
    ) -> ObjectPayload:
        raise NotFound("object not found")


class _FailingSearchObjectQuery(_FakeObjectQuery):
    def query_objects(
        self,
        object_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
        limit: int = 50,
        search_text: str | None = None,
    ) -> ObjectQueryResult:
        raise ValidationFailed("search unavailable")


class _NoisyQueryObjectQuery(_FakeObjectQuery):
    def query_objects(
        self,
        object_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
        limit: int = 50,
        search_text: str | None = None,
    ) -> ObjectQueryResult:
        return {
            "items": [
                _query_item(object_type_api_name, "O-1001"),
                _query_item(object_type_api_name, "O-1002"),
                _query_item(object_type_api_name, "O-1001"),
                _query_item(object_type_api_name, "O-404"),
            ],
            "nextCursor": None,
        }

    def get_object(
        self,
        object_type_api_name: str,
        object_id: str,
        *,
        ctx: RequestContext | None = None,
        include_explain: bool = False,
    ) -> ObjectPayload:
        if object_id == "O-404":
            raise NotFound("stale search hit")
        return {
            "objectType": object_type_api_name,
            "objectId": object_id,
            "objectVersion": 7,
            "properties": {"status": f"AUTHORITATIVE_{object_id}"},
            "sourceDatasetVersionId": "dataset-version-1",
        }


class _FakeContentSearch:
    def __init__(self) -> None:
        self.last_query: HybridContentQuery | None = None

    def search_content(self, ctx: RequestContext, *, query: HybridContentQuery) -> list[ContentSearchHit]:
        self.last_query = query
        return [
            ContentSearchHit(
                source_media_item_version_id="miv-contract-v3",
                content_unit_id="cu-payment-terms",
                index_generation="content-g17",
                page_number=2,
                text_hash="unit-text-hash",
                text="AUTHORITATIVE payment terms from the re-read content unit",
                chunk_spec_hash="chunk-v1",
                classification="internal",
            )
        ]


class _FailingContentSearch:
    def search_content(self, ctx: RequestContext, *, query: HybridContentQuery) -> list[ContentSearchHit]:
        raise AdapterError(
            AdapterFailure(
                adapter_profile="fake-content",
                operation="search",
                kind="unavailable",
                is_retryable=True,
                operator_message="content index unavailable",
            )
        )


def test_retrieval_orchestrator_rereads_state_object_and_hash_compiles(foundry: Any) -> None:
    prepare_indexed_demo(foundry)

    items = _service(foundry._services.object_store.query).retrieve_context(ctx=_CTX, request=_request())
    compiled = ContextCompilerService().compile(ctx=_CTX, request=_compile_request(items))

    assert len(items) == 1
    assert items[0].kind == "object"
    assert items[0].source_ref == "object://Order/O-1001"
    assert items[0].content_hash.startswith("sha256:")
    assert items[0].retrieval_method == "object_authoritative_reread"
    assert "O-1001" in items[0].text
    assert "PENDING" in items[0].text
    assert compiled.context_ids == (items[0].context_id,)


def test_retrieval_orchestrator_query_hits_are_authoritatively_reread() -> None:
    object_query = _FakeObjectQuery()
    service = _service(object_query)

    items = service.retrieve_context(
        ctx=_CTX,
        request=_request(state_json={"objectType": "Order"}, query="   delayed   order   "),
    )

    assert object_query.reread_count == 1
    assert items[0].source_ref == "object://Order/O-1001"
    assert "AUTHORITATIVE_VALUE" in items[0].text
    assert "STALE_INDEX_VALUE" not in items[0].text
    assert items[0].source_version == "7"


def test_retrieval_orchestrator_rejects_cross_tenant_partition() -> None:
    with pytest.raises(ContextRetrievalError) as exc_info:
        _service(_FakeObjectQuery()).retrieve_context(
            ctx=_CTX,
            request=_request(security_partition="tenant-other:internal"),
        )

    assert exc_info.value.reason == "security_partition_mismatch"


def test_retrieval_orchestrator_fails_when_context_does_not_fit_budget(foundry: Any) -> None:
    prepare_indexed_demo(foundry)

    with pytest.raises(ContextRetrievalError) as exc_info:
        _service(foundry._services.object_store.query).retrieve_context(
            ctx=_CTX,
            request=_request(max_context_tokens=1),
        )

    assert exc_info.value.reason == "context_budget_exceeded"


def test_retrieval_orchestrator_returns_empty_without_object_hint() -> None:
    items = _service(_FakeObjectQuery()).retrieve_context(ctx=_CTX, request=_request(state_json={}))

    assert items == ()


def test_retrieval_orchestrator_packs_document_context_and_hash_compiles() -> None:
    content = _FakeContentSearch()

    items = _service(_FakeObjectQuery(), content).retrieve_context(ctx=_CTX, request=_request(state_json={}))
    compiled = ContextCompilerService().compile(ctx=_CTX, request=_compile_request(items))

    assert len(items) == 1
    assert items[0].kind == "document"
    assert items[0].source_ref == "content-unit://miv-contract-v3/cu-payment-terms"
    assert items[0].source_version == "miv-contract-v3"
    assert items[0].retrieval_method == "content_hybrid_authoritative_reread"
    assert items[0].content_hash.startswith("sha256:")
    assert "AUTHORITATIVE payment terms" in items[0].text
    assert compiled.context_ids == (items[0].context_id,)


def test_retrieval_orchestrator_passes_classification_prefilter_to_content_search() -> None:
    content = _FakeContentSearch()

    _service(_FakeObjectQuery(), content).retrieve_context(
        ctx=_CTX,
        request=_request(
            state_json={},
            allowed_classifications=("public",),
        ),
    )

    assert content.last_query is not None
    assert content.last_query.allowed_classifications == ("public",)


def test_retrieval_orchestrator_fails_closed_when_content_search_unavailable() -> None:
    with pytest.raises(ContextRetrievalError) as exc_info:
        _service(_FakeObjectQuery(), _FailingContentSearch()).retrieve_context(
            ctx=_CTX, request=_request(state_json={})
        )

    assert exc_info.value.reason == "context_content_search_unavailable"


def test_retrieval_orchestrator_fails_closed_for_missing_explicit_object() -> None:
    with pytest.raises(ContextRetrievalError) as exc_info:
        _service(_MissingExplicitObjectQuery()).retrieve_context(ctx=_CTX, request=_request())

    assert exc_info.value.reason == "context_source_unavailable"


def test_retrieval_orchestrator_fails_closed_when_search_unavailable() -> None:
    with pytest.raises(ContextRetrievalError) as exc_info:
        _service(_FailingSearchObjectQuery()).retrieve_context(
            ctx=_CTX,
            request=_request(state_json={"objectType": "Order"}),
        )

    assert exc_info.value.reason == "context_search_unavailable"


def test_retrieval_orchestrator_skips_stale_hits_dedupes_and_packs() -> None:
    items = _service(_NoisyQueryObjectQuery()).retrieve_context(
        ctx=_CTX,
        request=_request(state_json={"objectType": "Order"}, max_context_items=1),
    )

    assert [item.source_ref for item in items] == ["object://Order/O-1001"]


def test_retrieval_orchestrator_rejects_context_mismatch_and_invalid_budget() -> None:
    with pytest.raises(ContextRetrievalError) as mismatch:
        _service(_FakeObjectQuery()).retrieve_context(
            ctx=_CTX,
            request=_request(actor_user_id="other-user"),
        )
    with pytest.raises(ContextRetrievalError) as invalid_budget:
        _service(_FakeObjectQuery()).retrieve_context(
            ctx=_CTX,
            request=_request(max_context_tokens=-1),
        )

    assert mismatch.value.reason == "request_context_mismatch"
    assert invalid_budget.value.reason == "invalid_context_budget"


def test_retrieval_orchestrator_rejects_object_id_without_object_type() -> None:
    with pytest.raises(ContextRetrievalError) as exc_info:
        _service(_FakeObjectQuery()).retrieve_context(
            ctx=_CTX,
            request=_request(state_json={"objectId": "O-1001"}),
        )

    assert exc_info.value.reason == "invalid_state_context"


def _service(
    object_query: RetrievalObjectQuery,
    content_search: RetrievalContentSearch | None = None,
) -> RetrievalOrchestrator:
    return RetrievalOrchestrator(object_query, content_retrieval_service=content_search)


def _request(
    *,
    state_json: dict[str, object] | None = None,
    query: str = "Explain Order O-1001 for the operator.",
    actor_user_id: str = _CTX.actor_user_id,
    max_context_items: int = 4,
    max_context_tokens: int = 1200,
    security_partition: str = "tenant-demo:internal",
    allowed_classifications: tuple[str, ...] | None = None,
) -> ContextRetrievalRequest:
    return ContextRetrievalRequest(
        tenant_id=_CTX.tenant_id,
        actor_user_id=actor_user_id,
        query=query,
        agent_version_id="agent.order-ops.v1",
        ontology_version_id="active-ontology",
        max_context_items=max_context_items,
        max_context_tokens=max_context_tokens,
        security_partition=security_partition,
        state_json=state_json if state_json is not None else {"objectType": "Order", "objectId": "O-1001"},
        allowed_classifications=allowed_classifications,
    )


def _compile_request(items: tuple[RetrievedContextItem, ...]) -> ContextCompileRequest:
    return ContextCompileRequest(
        agent_instruction="Answer as the Order Operations Copilot.",
        user_message="Explain Order O-1001 for the operator.",
        state_json={"objectType": "Order", "objectId": "O-1001"},
        retrieved_context=items,
        allowed_security_partitions=("tenant-demo:internal",),
        output_schema={"type": "object"},
    )


def _query_item(object_type: str, object_id: str) -> ObjectQueryItem:
    return {
        "objectType": object_type,
        "objectId": object_id,
        "objectVersion": 1,
        "properties": {"status": "STALE_INDEX_VALUE"},
    }
