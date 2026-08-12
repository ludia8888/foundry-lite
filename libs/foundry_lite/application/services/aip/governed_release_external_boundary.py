"""Protocol exposed by durable external Governed Release delivery."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected

JsonObject = Mapping[str, object]


class ExternalReleaseDeliveryBoundary(Protocol):
    """Publish, land, deploy, and roll back externally hosted release state."""

    def source_evidence(self, ctx: RequestContext, proposal: JsonObject) -> dict[str, object]:
        """Return durable source-publication and merge evidence for a proposal."""
        ...

    def publish_source_candidate(
        self,
        ctx: RequestContext,
        release_kind: str,
        proposal: JsonObject,
        arguments: JsonObject,
    ) -> dict[str, object]:
        """Publish the exact governed manifest as a reviewable source candidate."""
        ...

    def execute_source_merge(
        self,
        ctx: RequestContext,
        proposal: JsonObject,
        arguments: JsonObject,
    ) -> dict[str, object]:
        """Merge the approved exact pull request after fresh provider checks."""
        ...

    def delivery_evidence(self, ctx: RequestContext, proposal_id: str) -> dict[str, object]:
        """Return the durable external-delivery projection for release status."""
        ...

    def deployment_source_commit(self, ctx: RequestContext, proposal_id: str) -> str | None:
        """Resolve the landed source commit that is eligible for deployment."""
        ...

    def start_application_deployment(
        self,
        ctx: RequestContext,
        proposal_id: str,
        commit_id: str,
        idempotency_key: str,
    ) -> dict[str, object]:
        """Start or replay deployment of one exact landed commit."""
        ...

    def is_application_delivery_enabled(self) -> bool:
        """Report whether an external application delivery target is configured."""
        ...

    def application_rollback_target(
        self,
        ctx: RequestContext,
        proposal_id: str,
        idempotency_key: str | None = None,
    ) -> dict[str, object] | None:
        """Resolve a verified prior deployment that can be rolled back safely."""
        ...

    def start_application_rollback(
        self,
        ctx: RequestContext,
        proposal_id: str,
        target: JsonObject,
        idempotency_key: str,
    ) -> dict[str, object]:
        """Start or replay rollback to an exact verified deployment target."""
        ...


def require_external_delivery_result(result: JsonObject, operation: str) -> None:
    """Defense in depth: only a confirmed receipt or explicit skip may advance."""

    status = result.get("status")
    if status not in {"landed", "skipped"}:
        raise ConflictDetected(
            "external release delivery did not reach an admissible outcome",
            details={"operation": operation, "status": status},
        )


__all__ = ["ExternalReleaseDeliveryBoundary", "require_external_delivery_result"]
