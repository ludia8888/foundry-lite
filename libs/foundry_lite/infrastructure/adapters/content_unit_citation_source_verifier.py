"""Authoritative content-unit citation verifier with a bounded legacy fallback."""

from __future__ import annotations

from urllib.parse import unquote, urlsplit

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract, AdapterFailureMode
from foundry_lite.application.ports.citation_source import (
    CitationSourceVerificationFailed,
    CitationSourceVerificationRequest,
    CitationSourceVerificationResult,
    CitationSourceVerifier,
)
from foundry_lite.application.services.media.content_unit_evidence import ContentUnitEvidenceService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import FoundryLiteError


class AuthoritativeCitationSourceVerifier:
    """Route content-unit citations through committed DB truth and delegate other source types."""

    profile_name = "authoritative-content-unit-citation-source-verifier"

    def __init__(
        self,
        content_unit_evidence_service: ContentUnitEvidenceService,
        fallback: CitationSourceVerifier,
    ) -> None:
        self._content_unit_evidence_service = content_unit_evidence_service
        self._fallback = fallback

    def verify_source(
        self,
        ctx: RequestContext,
        request: CitationSourceVerificationRequest,
    ) -> CitationSourceVerificationResult:
        if request.source_resource_type != "content_unit":
            return self._fallback.verify_source(ctx, request)
        media_item_version_id, content_unit_id = _content_unit_ref(request.source_resource_id)
        if media_item_version_id != request.source_version:
            raise CitationSourceVerificationFailed(
                "source_version_mismatch",
                "content-unit URI version does not match the citation source version",
            )
        try:
            resolved = self._content_unit_evidence_service.resolve(
                ctx,
                media_item_version_id=media_item_version_id,
                content_unit_id=content_unit_id,
            )
        except FoundryLiteError as exc:
            raise CitationSourceVerificationFailed(
                "source_verification_failed",
                exc.message,
            ) from exc
        if resolved.content_hash != request.content_hash:
            raise CitationSourceVerificationFailed(
                "source_hash_mismatch",
                "content-unit hash does not match the citation context",
            )
        return CitationSourceVerificationResult(
            source_resource_type="content_unit",
            source_resource_id=resolved.source_resource_id,
            source_version=resolved.source_version,
            content_hash=resolved.content_hash,
            display_label=resolved.display_label,
            navigation_path=resolved.navigation_path,
            evidence=resolved.evidence,
        )

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    "verify_source",
                    "validation",
                    False,
                    "Content-unit identity, commit state, pins, security, version, or hash verification failed.",
                ),
            ),
        )


def _content_unit_ref(source_resource_id: str) -> tuple[str, str]:
    parsed = urlsplit(source_resource_id)
    path_parts = [unquote(part) for part in parsed.path.split("/") if part]
    if parsed.scheme != "content-unit" or not parsed.netloc or len(path_parts) != 1 or parsed.query or parsed.fragment:
        raise CitationSourceVerificationFailed(
            "invalid_content_unit_ref",
            "content-unit source reference is malformed",
        )
    return unquote(parsed.netloc), path_parts[0]
