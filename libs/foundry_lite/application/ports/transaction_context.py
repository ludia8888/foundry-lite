from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class StatusTransition:
    """Allowed current statuses and the single next status for a CAS update."""

    from_statuses: tuple[str, ...]
    to_status: str


DATASET_TRANSACTION_ABORT = StatusTransition(("OPEN",), "ABORTED")
SYNC_RUN_COMMITTED = StatusTransition(("EXTRACTING",), "COMMITTED")
SYNC_RUN_FAILED = StatusTransition(("EXTRACTING",), "FAILED")
TRANSFORM_RUN_SUCCEEDED = StatusTransition(("RUNNING",), "SUCCESS")
TRANSFORM_RUN_FAILED = StatusTransition(("RUNNING",), "FAILED")
MATERIALIZATION_RUN_SUCCEEDED = StatusTransition(("running",), "succeeded")
MATERIALIZATION_RUN_FAILED = StatusTransition(("running",), "failed")
MATERIALIZATION_RUN_ABORT_FAILED = StatusTransition(("running",), "FAILED")
INDEX_RUN_SUCCEEDED = StatusTransition(("running",), "succeeded")
INDEX_RUN_FAILED = StatusTransition(("running",), "failed")
ONTOLOGY_VERSION_ARCHIVED = StatusTransition(("active",), "archived")
ONTOLOGY_VERSION_ACTIVE = StatusTransition(("draft",), "active")
ACTION_RUN_SUCCEEDED = StatusTransition(("received",), "succeeded")
ACTION_RUN_FAILED = StatusTransition(("received",), "failed")
ACTION_RUN_CONFLICT = StatusTransition(("received",), "conflict")
ACTION_RUN_OUTCOME_UNKNOWN = StatusTransition(("received",), "outcome_unknown")
ACTION_RUN_COMPENSATION_REQUIRED = StatusTransition(("received",), "compensation_required")
ACTION_RUN_RECONCILED = StatusTransition(("outcome_unknown",), "reconciled")
ACTION_WRITEBACK_RECONCILED = StatusTransition(("outcome_unknown",), "reconciled")
DEAD_LETTER_REPLAY_REQUESTED = StatusTransition(("QUARANTINED",), "REPLAY_REQUESTED")
DEAD_LETTER_REPLAY_SUCCEEDED = StatusTransition(("REPLAY_REQUESTED", "REPLAYING"), "RESOLVED")
DEAD_LETTER_REPLAY_FAILED = StatusTransition(("REPLAY_REQUESTED", "REPLAYING"), "QUARANTINED")
DEAD_LETTER_DISCARDED = StatusTransition(("QUARANTINED",), "DISCARDED")
OUTBOX_RETRY_PENDING = StatusTransition(("failed",), "pending")


def dataset_run_failed_transition(run_kind: str) -> StatusTransition:
    if run_kind == "sync":
        return SYNC_RUN_FAILED
    if run_kind == "transform":
        return TRANSFORM_RUN_FAILED
    return MATERIALIZATION_RUN_ABORT_FAILED


@runtime_checkable
class TransactionContext(Protocol):
    """Opaque transaction handle passed from application services into repositories.

    Application services own transaction lifecycle (via the composition root's
    engine/begin equivalent) and pass the resulting handle into repository ports.
    Repositories may invoke vendor-specific methods on the handle internally;
    callers must not. This protocol exists so that fake adapters, in-memory
    runtimes, and future scale-out backends (PostgreSQL test containers,
    transactional Kafka outbox writers, etc.) can each provide their own
    transaction handle type while sharing the same port surface.

    The protocol is intentionally empty (a marker contract): the supported
    operations are defined by the concrete repository implementation pairing.
    What it documents is the boundary, not the vendor API.
    """


class TransactionManager(Protocol):
    """Application-visible transaction lifecycle boundary."""

    def begin(self) -> AbstractContextManager[TransactionContext]:
        """Open a transaction context and yield its opaque handle."""
        ...
