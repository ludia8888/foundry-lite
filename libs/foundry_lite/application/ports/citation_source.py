"""Citation source verification port (AIP-lite P0f — Citation Service, §8.7).

The citation service must not trust model-supplied URLs or source metadata. It
asks this port to re-read the current source handle and confirm the version/hash
that was originally placed in the AI run context manifest still matches.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract
from foundry_lite.domain.context import RequestContext


class CitationSourceVerificationFailed(Exception):
    """Safe fail-closed source-verification error that never contains raw source text."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class CitationSourceVerificationRequest:
    """Source identity copied from the tenant-scoped AI context manifest."""

    source_resource_type: str
    source_resource_id: str
    source_version: str
    content_hash: str


@dataclass(frozen=True)
class CitationSourceEvidence:
    """Verified locator and processing pins for one immutable media content unit."""

    media_item_version_id: str
    media_derivative_id: str
    content_unit_id: str
    page_number: int | None
    bbox: Mapping[str, object] | None
    timecode: Mapping[str, object] | None
    source_locator: Mapping[str, object] | None
    derivative_kind: str
    processor_name: str
    processor_version: str
    processor_spec_hash: str
    model_name: str | None
    model_version: str | None
    params_hash: str
    security_envelope: Mapping[str, object]


@dataclass(frozen=True)
class CitationSourceVerificationResult:
    """Current source state used to prove a citation has not gone stale."""

    source_resource_type: str
    source_resource_id: str
    source_version: str
    content_hash: str
    display_label: str
    navigation_path: str
    evidence: CitationSourceEvidence | None = None


def citation_source_evidence_payload(evidence: CitationSourceEvidence | None) -> dict[str, object] | None:
    """Canonical public evidence used by display, signatures, and navigation re-verification."""

    if evidence is None:
        return None
    return {
        "mediaItemVersionId": evidence.media_item_version_id,
        "mediaDerivativeId": evidence.media_derivative_id,
        "contentUnitId": evidence.content_unit_id,
        "pageNumber": evidence.page_number,
        "bbox": dict(evidence.bbox) if evidence.bbox is not None else None,
        "timecode": dict(evidence.timecode) if evidence.timecode is not None else None,
        "sourceLocator": dict(evidence.source_locator) if evidence.source_locator is not None else None,
        "derivativeKind": evidence.derivative_kind,
        "processorName": evidence.processor_name,
        "processorVersion": evidence.processor_version,
        "processorSpecHash": evidence.processor_spec_hash,
        "modelName": evidence.model_name,
        "modelVersion": evidence.model_version,
        "paramsHash": evidence.params_hash,
        "securityEnvelope": dict(evidence.security_envelope),
    }


@runtime_checkable
class CitationSourceVerifier(Protocol):
    """Verify a source version/hash before the product renders a citation."""

    @property
    def profile_name(self) -> str: ...

    def verify_source(
        self, ctx: RequestContext, request: CitationSourceVerificationRequest
    ) -> CitationSourceVerificationResult:
        """Return the current source snapshot for one context item."""
        ...

    def failure_contract(self) -> AdapterFailureContract:
        """Return the adapter failure taxonomy promised by this profile."""
        ...
