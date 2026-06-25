"""Unit tests for the AIP Retrieval Orchestrator baseline (P0o, §8.5)."""

from __future__ import annotations

from typing import Any

import pytest
from foundry_lite.application.ports import ObjectPayload, ObjectQueryResult
from foundry_lite.application.ports.context_provider import (
    ContextRetrievalError,
    ContextRetrievalRequest,
    RetrievedContextItem,
)
from foundry_lite.application.services.aip.context_compiler import ContextCompileRequest, ContextCompilerService
from foundry_lite.application.services.aip.retrieval_orchestrator import RetrievalObjectQuery, RetrievalOrchestrator
from foundry_lite.domain.context import RequestContext

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


def _service(object_query: RetrievalObjectQuery) -> RetrievalOrchestrator:
    return RetrievalOrchestrator(object_query)


def _request(
    *,
    state_json: dict[str, object] | None = None,
    query: str = "Explain Order O-1001 for the operator.",
    max_context_tokens: int = 1200,
    security_partition: str = "tenant-demo:internal",
) -> ContextRetrievalRequest:
    return ContextRetrievalRequest(
        tenant_id=_CTX.tenant_id,
        actor_user_id=_CTX.actor_user_id,
        query=query,
        agent_version_id="agent.order-ops.v1",
        ontology_version_id="active-ontology",
        max_context_items=4,
        max_context_tokens=max_context_tokens,
        security_partition=security_partition,
        state_json=state_json or {"objectType": "Order", "objectId": "O-1001"},
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
