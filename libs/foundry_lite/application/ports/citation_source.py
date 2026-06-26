"""Citation source verification port (AIP-lite P0f — Citation Service, §8.7).

The citation service must not trust model-supplied URLs or source metadata. It
asks this port to re-read the current source handle and confirm the version/hash
that was originally placed in the AI run context manifest still matches.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract
from foundry_lite.domain.context import RequestContext


@dataclass(frozen=True)
class CitationSourceVerificationRequest:
    """Source identity copied from the tenant-scoped AI context manifest."""

    source_resource_type: str
    source_resource_id: str
    source_version: str
    content_hash: str


@dataclass(frozen=True)
class CitationSourceVerificationResult:
    """Current source state used to prove a citation has not gone stale."""

    source_resource_type: str
    source_resource_id: str
    source_version: str
    content_hash: str
    display_label: str
    navigation_path: str


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
