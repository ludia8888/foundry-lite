"""Contract for ``CitationSourceVerifier`` (AIP-lite P0f Citation Service, §8.7)."""

from __future__ import annotations

from foundry_lite.application.ports.citation_source import (
    CitationSourceVerificationRequest,
    CitationSourceVerifier,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.infrastructure.adapters.fake_citation_source_verifier import FakeCitationSourceVerifier


def test_fake_citation_source_verifier_is_runtime_checkable() -> None:
    verifier = FakeCitationSourceVerifier()

    assert isinstance(verifier, CitationSourceVerifier)


def test_fake_citation_source_verifier_returns_current_version_hash_and_navigation_path() -> None:
    verifier = FakeCitationSourceVerifier()
    ctx = RequestContext(tenant_id="tenant-demo", actor_user_id="user-1")

    result = verifier.verify_source(
        ctx,
        CitationSourceVerificationRequest(
            source_resource_type="object",
            source_resource_id="Order:O-1001",
            source_version="object-version-1",
            content_hash="sha256:context",
        ),
    )

    assert result.source_version == "object-version-1"
    assert result.content_hash == "sha256:context"
    assert result.display_label == "object:Order:O-1001@object-version-1"
    assert result.navigation_path == "/tenants/tenant-demo/sources/object/Order:O-1001"


def test_citation_source_verifier_failure_contract_declares_verify_source() -> None:
    contract = FakeCitationSourceVerifier().failure_contract()

    assert contract.adapter_profile == "fake-citation-source-verifier"
    assert {mode.operation for mode in contract.modes} == {"verify_source"}
