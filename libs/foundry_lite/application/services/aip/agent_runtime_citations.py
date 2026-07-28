"""Agent Runtime citation rendering helpers (P0q, §8.7/§8.9)."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, cast

from foundry_lite.application.services.aip.citation_navigation import citation_expiry
from foundry_lite.application.services.aip.citation_service import (
    CitationClaim,
    CitationResolveRequest,
    CitationResolveResult,
    CitationServiceError,
)
from foundry_lite.domain.context import RequestContext

JsonObject = Mapping[str, object]


class CitationResolver(Protocol):
    def resolve(self, ctx: RequestContext, request: CitationResolveRequest) -> CitationResolveResult: ...


@dataclass(frozen=True)
class AgentRuntimeAnswer:
    """Final answer text plus server-validated citation display payloads."""

    answer: str
    citations: tuple[JsonObject, ...] = ()


def resolve_agent_answer_citations(
    ctx: RequestContext,
    citation_service: CitationResolver,
    *,
    ai_run_id: str,
    message_id: str,
    model_content: str,
    issued_at: str,
) -> AgentRuntimeAnswer:
    """Resolve model-proposed citation ids against the AI run ledger.

    Plain-text model output stays backward compatible. Structured JSON output may
    include ``answer`` and ``citations``; the citations are never trusted until
    ``CitationService`` verifies the run manifest and source version/hash.
    """

    payload = _structured_payload(model_content)
    if payload is None:
        return AgentRuntimeAnswer(answer=model_content)
    answer = _answer_text(payload, fallback=model_content)
    claims = _claims(payload.get("citations"))
    if not claims:
        return AgentRuntimeAnswer(answer=answer)
    result = citation_service.resolve(
        ctx,
        CitationResolveRequest(
            ai_run_id=ai_run_id,
            message_id=message_id,
            claims=claims,
            issued_at=issued_at,
            expires_at=citation_expiry(issued_at),
        ),
    )
    return AgentRuntimeAnswer(
        answer=answer, citations=tuple(dict(citation.display_payload) for citation in result.citations)
    )


def citation_error_payload(exc: Exception) -> dict[str, object] | None:
    if isinstance(exc, CitationServiceError):
        return {"reason": exc.reason, "detail": exc.detail}
    return None


def _structured_payload(content: str) -> Mapping[str, object] | None:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return None
    return cast(Mapping[str, object], payload) if isinstance(payload, Mapping) else None


def _answer_text(payload: Mapping[str, object], *, fallback: str) -> str:
    answer = payload.get("answer")
    return answer if isinstance(answer, str) and answer else fallback


def _claims(raw_claims: object) -> tuple[CitationClaim, ...]:
    if raw_claims is None:
        return ()
    if not isinstance(raw_claims, Sequence) or isinstance(raw_claims, str | bytes):
        raise CitationServiceError("invalid_citation_payload", "citations must be a list of claim objects")
    return tuple(_claim(item) for item in raw_claims)


def _claim(raw_claim: object) -> CitationClaim:
    if not isinstance(raw_claim, Mapping):
        raise CitationServiceError("invalid_citation_payload", "each citation must be an object")
    return CitationClaim(
        context_id=_required_str(raw_claim, "contextId", "context_id"),
        claim_span=_required_mapping(raw_claim, "claimSpan", "claim_span"),
        citation_order=_required_positive_int(raw_claim, "citationOrder", "citation_order"),
    )


def _required_str(payload: Mapping[str, object], camel_key: str, snake_key: str) -> str:
    value = payload.get(camel_key, payload.get(snake_key))
    if not isinstance(value, str) or not value:
        raise CitationServiceError("invalid_citation_payload", f"{camel_key} is required")
    return value


def _required_mapping(payload: Mapping[str, object], camel_key: str, snake_key: str) -> Mapping[str, object]:
    value = payload.get(camel_key, payload.get(snake_key))
    if not isinstance(value, Mapping):
        raise CitationServiceError("invalid_citation_payload", f"{camel_key} is required")
    return value


def _required_positive_int(payload: Mapping[str, object], camel_key: str, snake_key: str) -> int:
    value = payload.get(camel_key, payload.get(snake_key))
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise CitationServiceError("invalid_citation_payload", f"{camel_key} must be a positive integer")
    return value
