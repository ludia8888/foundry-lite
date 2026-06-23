from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from foundry_lite.application.ports.media_repository import SecurityEnvelope


@dataclass(frozen=True)
class MediaReadDecision:
    """Auditable decision for one media read, carrying a stable decision id."""

    is_allowed: bool
    security_decision_id: str
    reason: str | None = None


class MediaPolicyEvaluator(Protocol):
    """Security-propagation boundary for media (ADR-0001 invariant 3 / doc §12.1).

    Anchors the rule that every derivative/content-unit/embedding/index entry inherits an
    envelope no weaker than its source. Contract only in this sprint.
    """

    def inherit_envelope(self, source: SecurityEnvelope) -> SecurityEnvelope:
        """Return the envelope a derivative must carry given its source envelope."""
        ...

    def evaluate_read(self, *, tenant_id: str, envelope: SecurityEnvelope, principal_id: str) -> MediaReadDecision:
        """Decide whether a principal may read a blob under the given envelope."""
        ...
