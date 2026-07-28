"""AIP Citation Service (P0f, §8.7/§9.4).

The model only receives opaque context IDs. This service turns those IDs into
renderable citations by checking the tenant-scoped AI run manifest, requiring
caller source access, verifying the current source version/hash, and issuing a
signed navigation reference. Raw source URLs are never accepted from model
output.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from urllib.parse import quote

from foundry_lite.application.ports.ai_run_repository import AiCitationRecord, AiLedgerRow
from foundry_lite.application.ports.citation_source import (
    CitationSourceEvidence,
    CitationSourceVerificationFailed,
    CitationSourceVerificationRequest,
    CitationSourceVerificationResult,
    CitationSourceVerifier,
    citation_source_evidence_payload,
)
from foundry_lite.application.services.aip.citation_navigation import (
    CitationNavigationTokenError,
    decode_navigation_ref,
    encode_navigation_ref,
)
from foundry_lite.application.services.aip.source_permissions import source_permission_for_type
from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied
from foundry_lite.security.policy import PolicyService

_NAV_SIGNING_CREDENTIAL_REF = "aip_citation_navigation_signer"


@dataclass(frozen=True)
class CitationClaim:
    """One model-proposed citation, containing only a context id and answer span."""

    context_id: str
    claim_span: Mapping[str, object]
    citation_order: int


@dataclass(frozen=True)
class CitationResolveRequest:
    """Resolve all citations for one assistant message."""

    ai_run_id: str
    message_id: str
    claims: tuple[CitationClaim, ...]
    issued_at: str
    expires_at: str


@dataclass(frozen=True)
class ResolvedCitation:
    """Citation display payload plus durable ledger row."""

    context_id: str
    rendered_ref: str
    signed_navigation_ref: str
    display_payload: Mapping[str, object]
    ledger_record: AiCitationRecord


@dataclass(frozen=True)
class CitationResolveResult:
    """Resolved citations in display order."""

    citations: tuple[ResolvedCitation, ...]


@dataclass(frozen=True)
class CitationNavigationResolveResult:
    """Authoritative source and locator returned only after token re-verification."""

    navigation_path: str
    source_resource_type: str
    source_resource_id: str
    source_version: str
    content_hash: str
    display_label: str
    evidence: CitationSourceEvidence | None

    def to_payload(self) -> dict[str, object]:
        return {
            "navigationPath": self.navigation_path,
            "sourceResourceType": self.source_resource_type,
            "sourceResourceId": self.source_resource_id,
            "sourceVersion": self.source_version,
            "contentHash": self.content_hash,
            "displayLabel": self.display_label,
            "evidence": citation_source_evidence_payload(self.evidence),
        }


@dataclass
class CitationServiceError(Exception):
    """Typed fail-closed citation rejection."""

    reason: str
    detail: str

    def __post_init__(self) -> None:
        Exception.__init__(self, self.detail)


class CitationService(CoreService):
    """Resolve model citations through the canonical run ledger and source verifier."""

    required_dependencies = ("engine", "policy", "ai_run_repository", "citation_source_verifier", "secret_provider")
    required_collaborators = ()

    def resolve(self, ctx: RequestContext, request: CitationResolveRequest) -> CitationResolveResult:
        claims = _validate_claims(request.claims)
        secret = self.secret_provider.get_secret(_NAV_SIGNING_CREDENTIAL_REF)
        with self.engine.begin() as transaction:
            ledger = self.ai_run_repository.ledger_for_run(
                transaction=transaction, tenant_id=ctx.tenant_id, ai_run_id=request.ai_run_id
            )
            if ledger is None:
                raise CitationServiceError("run_not_found", f"AI run {request.ai_run_id} was not found")
            context_items = _context_items_by_id(ledger["contextItems"])
            resolved = tuple(self._resolve_claim(ctx, request, claim, context_items, secret.value) for claim in claims)
            for citation in resolved:
                self.ai_run_repository.record_citation(transaction=transaction, record=citation.ledger_record)
        return CitationResolveResult(citations=resolved)

    def resolve_navigation(
        self,
        ctx: RequestContext,
        navigation_ref: str,
    ) -> CitationNavigationResolveResult:
        secret = self.secret_provider.get_secret(_NAV_SIGNING_CREDENTIAL_REF)
        payload = _decode_navigation(ctx, navigation_ref, secret.value)
        source_request = _navigation_source_request(payload)
        _require_navigation_source_access(ctx, self.policy, source_request.source_resource_type)
        current = _verify_source(self.citation_source_verifier, ctx, source_request)
        _require_navigation_source_current(payload, current)
        return CitationNavigationResolveResult(
            navigation_path=current.navigation_path,
            source_resource_type=current.source_resource_type,
            source_resource_id=current.source_resource_id,
            source_version=current.source_version,
            content_hash=current.content_hash,
            display_label=current.display_label,
            evidence=current.evidence,
        )

    def _resolve_claim(
        self,
        ctx: RequestContext,
        request: CitationResolveRequest,
        claim: CitationClaim,
        context_items: Mapping[str, AiLedgerRow],
        signing_key: str,
    ) -> ResolvedCitation:
        item = _selected_context_item(context_items, claim.context_id)
        _require_source_access(ctx, self.policy, item)
        source = _verify_source(self.citation_source_verifier, ctx, _source_request(item))
        _require_fresh_source(item, source.source_version, source.content_hash)
        rendered_ref = f"[{claim.citation_order}] {source.display_label}"
        navigation_ref = _signed_navigation_ref(ctx, request, claim, item, source, signing_key)
        display_payload = _display_payload(claim, item, source, navigation_ref)
        return ResolvedCitation(
            context_id=claim.context_id,
            rendered_ref=rendered_ref,
            signed_navigation_ref=navigation_ref,
            display_payload=display_payload,
            ledger_record=_ledger_record(ctx, request, claim, item, rendered_ref),
        )


def _validate_claims(claims: tuple[CitationClaim, ...]) -> tuple[CitationClaim, ...]:
    if not claims:
        raise CitationServiceError("missing_citation_claims", "at least one citation claim is required")
    orders: set[int] = set()
    for claim in claims:
        if claim.context_id == "":
            raise CitationServiceError("missing_context_id", "citation context_id must be non-empty")
        if claim.citation_order <= 0:
            raise CitationServiceError("invalid_citation_order", "citation order must be positive")
        if claim.citation_order in orders:
            raise CitationServiceError("duplicate_citation_order", "citation order is duplicated")
        orders.add(claim.citation_order)
        _validate_claim_span(claim.claim_span)
    return tuple(sorted(claims, key=lambda claim: claim.citation_order))


def _validate_claim_span(span: Mapping[str, object]) -> None:
    start_raw = span.get("start")
    end_raw = span.get("end")
    if not _is_non_bool_int(start_raw) or not _is_non_bool_int(end_raw):
        raise CitationServiceError("invalid_claim_span", "claim span must contain integer start/end offsets")
    start = cast(int, start_raw)
    end = cast(int, end_raw)
    if start < 0 or end <= start:
        raise CitationServiceError("invalid_claim_span", "claim span must contain integer start/end offsets")
    if end - start > 4096:
        raise CitationServiceError("claim_span_too_large", "claim span exceeds the bounded citation window")


def _context_items_by_id(rows: list[AiLedgerRow]) -> dict[str, AiLedgerRow]:
    items: dict[str, AiLedgerRow] = {}
    for row in rows:
        context_id = row.get("context_id")
        if isinstance(context_id, str):
            items[context_id] = row
    return items


def _selected_context_item(context_items: Mapping[str, AiLedgerRow], context_id: str) -> AiLedgerRow:
    item = context_items.get(context_id)
    if item is None:
        raise CitationServiceError("context_not_in_manifest", f"context id {context_id} is not in the run manifest")
    if item.get("selected") is not True:
        raise CitationServiceError("context_not_selected", f"context id {context_id} was not selected for the run")
    return item


def _require_source_access(ctx: RequestContext, policy: PolicyService, item: AiLedgerRow) -> None:
    permission = _permission_for_source(item)
    try:
        policy.require(ctx, permission)
    except PermissionDenied as exc:
        raise CitationServiceError("policy_denied", f"permission denied for {permission}") from exc


def _permission_for_source(item: AiLedgerRow) -> str:
    resource_type = _required_str(item, "source_resource_type")
    return source_permission_for_type(resource_type)


def _source_request(item: AiLedgerRow) -> CitationSourceVerificationRequest:
    return CitationSourceVerificationRequest(
        source_resource_type=_required_str(item, "source_resource_type"),
        source_resource_id=_required_str(item, "source_resource_id"),
        source_version=_required_str(item, "source_version"),
        content_hash=_required_str(item, "content_hash"),
    )


def _require_fresh_source(item: AiLedgerRow, current_version: str, current_hash: str) -> None:
    if current_version != _required_str(item, "source_version") or current_hash != _required_str(item, "content_hash"):
        raise CitationServiceError("source_stale", "source version/hash no longer matches the run context manifest")


def _display_payload(
    claim: CitationClaim,
    item: AiLedgerRow,
    source: CitationSourceVerificationResult,
    signed_navigation_ref: str,
) -> dict[str, object]:
    return {
        "contextId": claim.context_id,
        "citationOrder": claim.citation_order,
        "claimSpan": dict(claim.claim_span),
        "sourceResourceType": _required_str(item, "source_resource_type"),
        "sourceResourceId": _required_str(item, "source_resource_id"),
        "sourceVersion": _required_str(item, "source_version"),
        "contentHash": _required_str(item, "content_hash"),
        "displayLabel": source.display_label,
        "navigationRef": signed_navigation_ref,
        "navigationPath": _display_navigation_path(source, signed_navigation_ref),
        "evidence": citation_source_evidence_payload(source.evidence),
        "sourcePreview": _source_preview(item),
    }


def _source_preview(item: AiLedgerRow) -> dict[str, object]:
    return {
        "contextItemId": _required_str(item, "id"),
        "kind": _required_str(item, "kind"),
        "sourceResourceType": _required_str(item, "source_resource_type"),
        "sourceResourceId": _required_str(item, "source_resource_id"),
        "sourceVersion": _required_str(item, "source_version"),
        "contentHash": _required_str(item, "content_hash"),
        "retrievalMethod": _required_str(item, "retrieval_method"),
        "relevanceScore": item.get("relevance_score"),
        "tokenEstimate": item.get("token_estimate"),
        "securityPartition": _required_str(item, "security_partition"),
        "selected": item.get("selected") is True,
    }


def _ledger_record(
    ctx: RequestContext, request: CitationResolveRequest, claim: CitationClaim, item: AiLedgerRow, rendered_ref: str
) -> AiCitationRecord:
    return AiCitationRecord(
        id=_citation_id(ctx, request, claim),
        tenant_id=ctx.tenant_id,
        ai_run_id=request.ai_run_id,
        message_id=request.message_id,
        context_item_id=_required_str(item, "id"),
        claim_span=dict(claim.claim_span),
        citation_order=claim.citation_order,
        rendered_ref=rendered_ref,
    )


def _signed_navigation_ref(
    ctx: RequestContext,
    request: CitationResolveRequest,
    claim: CitationClaim,
    item: AiLedgerRow,
    source: CitationSourceVerificationResult,
    signing_key: str,
) -> str:
    payload = {
        "tenant_id": ctx.tenant_id,
        "ai_run_id": request.ai_run_id,
        "message_id": request.message_id,
        "context_id": claim.context_id,
        "context_item_id": _required_str(item, "id"),
        "source_resource_type": _required_str(item, "source_resource_type"),
        "source_resource_id": _required_str(item, "source_resource_id"),
        "source_version": _required_str(item, "source_version"),
        "content_hash": _required_str(item, "content_hash"),
        "navigation_path": source.navigation_path,
        "display_label": source.display_label,
        "evidence": citation_source_evidence_payload(source.evidence),
        "iat": request.issued_at,
        "exp": request.expires_at,
    }
    return encode_navigation_ref(payload, signing_key)


def _decode_navigation(ctx: RequestContext, navigation_ref: str, signing_key: str) -> dict[str, object]:
    try:
        return decode_navigation_ref(
            navigation_ref,
            signing_key,
            expected_tenant_id=ctx.tenant_id,
            observed_at=_now(),
        )
    except CitationNavigationTokenError as exc:
        raise CitationServiceError(exc.reason, exc.detail) from exc


def _navigation_source_request(payload: Mapping[str, object]) -> CitationSourceVerificationRequest:
    return CitationSourceVerificationRequest(
        source_resource_type=_required_payload_str(payload, "source_resource_type"),
        source_resource_id=_required_payload_str(payload, "source_resource_id"),
        source_version=_required_payload_str(payload, "source_version"),
        content_hash=_required_payload_str(payload, "content_hash"),
    )


def _require_navigation_source_access(ctx: RequestContext, policy: PolicyService, resource_type: str) -> None:
    permission = source_permission_for_type(resource_type)
    try:
        policy.require(ctx, permission)
    except PermissionDenied as exc:
        raise CitationServiceError("policy_denied", f"permission denied for {permission}") from exc


def _verify_source(
    verifier: CitationSourceVerifier,
    ctx: RequestContext,
    request: CitationSourceVerificationRequest,
) -> CitationSourceVerificationResult:
    try:
        return verifier.verify_source(ctx, request)
    except CitationSourceVerificationFailed as exc:
        raise CitationServiceError(exc.reason, exc.detail) from exc


def _require_navigation_source_current(
    payload: Mapping[str, object],
    current: CitationSourceVerificationResult,
) -> None:
    signed_identity = (
        _required_payload_str(payload, "source_resource_type"),
        _required_payload_str(payload, "source_resource_id"),
        _required_payload_str(payload, "source_version"),
        _required_payload_str(payload, "content_hash"),
        _required_payload_str(payload, "navigation_path"),
        _required_payload_str(payload, "display_label"),
    )
    current_identity = (
        current.source_resource_type,
        current.source_resource_id,
        current.source_version,
        current.content_hash,
        current.navigation_path,
        current.display_label,
    )
    if signed_identity != current_identity or payload.get("evidence") != citation_source_evidence_payload(
        current.evidence
    ):
        raise CitationServiceError("citation_source_stale", "citation source evidence no longer matches")


def _required_payload_str(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise CitationServiceError("invalid_navigation_ref", f"citation navigation field {key} is missing")
    return value


def _display_navigation_path(
    source: CitationSourceVerificationResult,
    navigation_ref: str,
) -> str:
    if source.evidence is None:
        return source.navigation_path
    return f"{source.navigation_path}?citation={quote(navigation_ref, safe='')}"


def _citation_id(ctx: RequestContext, request: CitationResolveRequest, claim: CitationClaim) -> str:
    digest = hashlib.sha256(
        _json_block(
            {
                "tenant_id": ctx.tenant_id,
                "ai_run_id": request.ai_run_id,
                "message_id": request.message_id,
                "context_id": claim.context_id,
                "citation_order": claim.citation_order,
            }
        ).encode("utf-8")
    ).hexdigest()
    return f"ai-citation-{digest[:24]}"


def _required_str(row: Mapping[str, object], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or value == "":
        raise CitationServiceError("invalid_context_manifest", f"context manifest field {key} is missing")
    return value


def _is_non_bool_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _json_block(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _now() -> datetime:
    return datetime.now(UTC)
