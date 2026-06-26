"""Deterministic fake ``CitationSourceVerifier`` for local AIP citation proof."""

from __future__ import annotations

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract, AdapterFailureMode
from foundry_lite.application.ports.citation_source import (
    CitationSourceVerificationRequest,
    CitationSourceVerificationResult,
)
from foundry_lite.domain.context import RequestContext


class FakeCitationSourceVerifier:
    """Pure local verifier that echoes a ledger source handle with a product navigation path."""

    profile_name = "fake-citation-source-verifier"

    def verify_source(
        self, ctx: RequestContext, request: CitationSourceVerificationRequest
    ) -> CitationSourceVerificationResult:
        resource_type = request.source_resource_type
        resource_id = request.source_resource_id
        return CitationSourceVerificationResult(
            source_resource_type=resource_type,
            source_resource_id=resource_id,
            source_version=request.source_version,
            content_hash=request.content_hash,
            display_label=f"{resource_type}:{resource_id}@{request.source_version}",
            navigation_path=f"/tenants/{ctx.tenant_id}/sources/{resource_type}/{resource_id}",
        )

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    "verify_source",
                    "validation",
                    False,
                    "The fake citation verifier received malformed source metadata.",
                ),
            ),
        )
