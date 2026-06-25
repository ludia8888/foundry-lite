"""Contract for ``ContextProvider`` (AIP-lite P0d retrieval context boundary, §8.5/§15.1)."""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.ports.context_provider import (
    ContextProvider,
    ContextRetrievalRequest,
    RetrievedContextItem,
)
from foundry_lite.domain.context import RequestContext

_TENANT = "tenant-demo"


@dataclass(frozen=True)
class _FakeContextProvider:
    item: RetrievedContextItem

    def retrieve_context(
        self, *, ctx: RequestContext, request: ContextRetrievalRequest
    ) -> tuple[RetrievedContextItem, ...]:
        assert ctx.tenant_id == request.tenant_id
        assert request.security_partition == self.item.security_partition
        return (self.item,)


def test_context_provider_returns_canonical_retrieved_context_item() -> None:
    provider: ContextProvider = _FakeContextProvider(_item())

    result = provider.retrieve_context(ctx=RequestContext(tenant_id=_TENANT), request=_request())

    assert result == (_item(),)
    assert result[0].context_id == "ctx-1"
    assert result[0].kind == "object"
    assert result[0].source_ref == "object://Order/PO-1042"
    assert result[0].source_version == "object-version-7"
    assert result[0].retrieval_method == "authoritative_reread"
    assert result[0].security_partition == "tenant-demo:internal"


def _request() -> ContextRetrievalRequest:
    return ContextRetrievalRequest(
        tenant_id=_TENANT,
        actor_user_id="user-1",
        query="why is PO-1042 delayed?",
        agent_version_id="agent-v1",
        ontology_version_id="ontology-v1",
        max_context_items=5,
        max_context_tokens=2048,
        security_partition="tenant-demo:internal",
    )


def _item() -> RetrievedContextItem:
    return RetrievedContextItem(
        context_id="ctx-1",
        kind="object",
        text="PO-1042 is delayed because shipment SH-77 missed port cutoff.",
        source_ref="object://Order/PO-1042",
        source_version="object-version-7",
        content_hash="3f8917892a2af00dac5854f5c725a6d09cb57f70e859d7df7fef60a2595f2a63",
        relevance_score=0.97,
        retrieval_method="authoritative_reread",
        security_partition="tenant-demo:internal",
        token_estimate=18,
    )
