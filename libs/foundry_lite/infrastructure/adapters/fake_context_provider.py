"""Infrastructure adapter implementation for fake context provider."""

from __future__ import annotations

import hashlib

from foundry_lite.application.ports.context_provider import ContextRetrievalRequest, RetrievedContextItem
from foundry_lite.domain.context import RequestContext


class FakeContextProvider:
    """Deterministic no-network context provider for local AIP runtime demos."""

    def retrieve_context(
        self, *, ctx: RequestContext, request: ContextRetrievalRequest
    ) -> tuple[RetrievedContextItem, ...]:
        if request.max_context_items <= 0:
            return ()
        text = _context_text(request)
        return (
            RetrievedContextItem(
                context_id=_context_id(ctx, request),
                kind="document",
                text=text,
                source_ref=f"fake-context://{request.agent_version_id}",
                source_version="fake-context-v1",
                content_hash=_hash_text(text),
                relevance_score=1.0,
                retrieval_method="fake-authorized-reread",
                security_partition=request.security_partition,
                token_estimate=min(request.max_context_tokens, max(1, len(text.split()))),
            ),
        )


def _context_text(request: ContextRetrievalRequest) -> str:
    return (
        f"Authorized context for {request.agent_version_id}: "
        f"answer the operator question using tenant partition {request.security_partition}. "
        f"Question summary: {request.query}"
    )


def _context_id(ctx: RequestContext, request: ContextRetrievalRequest) -> str:
    digest = hashlib.sha256(f"{ctx.tenant_id}:{request.agent_version_id}:{request.query}".encode()).hexdigest()[:16]
    return f"ctx-{digest}"


def _hash_text(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode()).hexdigest()}"
