"""External-writeback boundary for ontology actions (L8: real before-commit writeback).

An ontology action with an external side effect "reaches out" to a vendor system before the
local mutation commits, exactly like a Palantir Foundry external transform. That reach-out has
three relevant shapes, and conflating them is the failure mode this port closes:

- **landed** — the external write provably succeeded (a receipt). The service may then commit
  the local mutation; if the *local* commit fails afterwards, the external side effect is already
  live, so a compensation path is required (never a silent local-only rollback).
- **ambiguous** — a timeout or connection error while writing. The write *may* have landed, so the
  outcome is unknown, NOT a guaranteed failure: the service records ``outcome_unknown`` and a later
  ``remote_lookup`` (idempotent re-read by idempotency key) resolves it; a replay is never a blind
  new write.

The default ``UnavailableExternalWritebackAdapter`` raises (no vendor bundled), mirroring the
``local-external`` media reader, so the simulated ``simulate_writeback_*`` flags remain the only
exercised path until a real adapter (e.g. the S3 implementation) is injected.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

ExternalWritebackPayload = Mapping[str, object]


def _empty_payload() -> ExternalWritebackPayload:
    return {}


class RemoteOutcomeStatus(StrEnum):
    """The three outcome shapes of an external reach-out, kept distinct on purpose."""

    LANDED = "landed"
    AMBIGUOUS = "ambiguous"
    ABSENT = "absent"


@dataclass(frozen=True)
class ExternalWriteTarget:
    """Where the external write lands, addressed idempotently by the action idempotency key."""

    uri: str
    idempotency_key: str


@dataclass(frozen=True)
class WriteReceipt:
    """Proof of an external write outcome returned by ``write``.

    ``status`` is ``LANDED`` when the write provably succeeded, or ``AMBIGUOUS`` when a timeout or
    connection error left the outcome unknown (the write may still have landed remotely).
    """

    status: RemoteOutcomeStatus
    remote_resource_id: str | None = None
    detail: ExternalWritebackPayload = field(default_factory=_empty_payload)


@dataclass(frozen=True)
class RemoteOutcome:
    """The result of an idempotent ``remote_lookup`` used to resolve an outcome_unknown writeback."""

    status: RemoteOutcomeStatus
    remote_resource_id: str | None = None


class ExternalWritebackAdapter(Protocol):
    """Boundary for a real before-commit external writeback (action external side effect)."""

    @property
    def profile_name(self) -> str: ...

    def write(self, target: ExternalWriteTarget, payload: ExternalWritebackPayload) -> WriteReceipt:
        """Write the payload to the external system; a timeout/connection error returns AMBIGUOUS."""
        ...

    def remote_lookup(self, target: ExternalWriteTarget) -> RemoteOutcome:
        """Idempotently re-read whether the external write landed (resolves an outcome_unknown)."""
        ...


class ExternalWritebackError(Exception):
    """Raised when no external-writeback connector is bundled."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class UnavailableExternalWritebackAdapter:
    """Default adapter: no vendor bundled, so a real writeback raises (keeps the seam injectable)."""

    profile_name = "external-writeback-unavailable"

    def write(self, target: ExternalWriteTarget, payload: ExternalWritebackPayload) -> WriteReceipt:
        del target, payload
        raise ExternalWritebackError("external_writeback_unavailable")

    def remote_lookup(self, target: ExternalWriteTarget) -> RemoteOutcome:
        del target
        raise ExternalWritebackError("external_writeback_unavailable")
