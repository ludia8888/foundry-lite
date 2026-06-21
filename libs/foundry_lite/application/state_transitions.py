from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

TransitionOutcome = Literal[
    "updated",
    "not_found",
    "already_terminal",
    "stale_state",
    "condition_conflict",
]


@dataclass(frozen=True)
class StatusTransition:
    """Allowed current statuses and the single next status for a CAS update."""

    from_statuses: tuple[str, ...]
    to_status: str


@dataclass(frozen=True)
class TransitionResult:
    """Classified outcome of an optimistic status transition."""

    outcome: TransitionOutcome
    transition: StatusTransition
    current_status: str | None

    @property
    def updated(self) -> bool:
        return self.outcome == "updated"


def transition_updated(transition: StatusTransition) -> TransitionResult:
    return TransitionResult("updated", transition, transition.to_status)


def classify_transition_miss(
    transition: StatusTransition,
    current_status: str | None,
) -> TransitionResult:
    if current_status is None:
        return TransitionResult("not_found", transition, None)
    if current_status == transition.to_status:
        return TransitionResult("already_terminal", transition, current_status)
    if current_status in transition.from_statuses:
        return TransitionResult("condition_conflict", transition, current_status)
    return TransitionResult("stale_state", transition, current_status)


DATASET_TRANSACTION_ABORT = StatusTransition(("OPEN",), "ABORTED")
DATASET_TRANSACTION_COMMIT = StatusTransition(("OPEN",), "COMMITTED")
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
DEAD_LETTER_REPLAY_RUNNING = StatusTransition(("REPLAY_REQUESTED", "REPLAYING"), "REPLAYING")
DEAD_LETTER_REPLAY_SUCCEEDED = StatusTransition(("REPLAY_REQUESTED", "REPLAYING"), "RESOLVED")
DEAD_LETTER_REPLAY_FAILED = StatusTransition(("REPLAY_REQUESTED", "REPLAYING"), "QUARANTINED")
DEAD_LETTER_DISCARDED = StatusTransition(("QUARANTINED",), "DISCARDED")
OUTBOX_RETRY_PENDING = StatusTransition(("failed",), "pending")
WORKFLOW_RUN_STARTING = StatusTransition(("requested", "start_unknown"), "starting")
WORKFLOW_RUN_RUNNING = StatusTransition(("starting",), "running")
WORKFLOW_RUN_SUCCEEDED = StatusTransition(("starting", "running"), "succeeded")
WORKFLOW_RUN_FAILED = StatusTransition(("requested", "starting", "running"), "failed")
WORKFLOW_RUN_CANCELLED = StatusTransition(("starting", "running"), "cancelled")
WORKFLOW_RUN_START_UNKNOWN = StatusTransition(("requested", "starting"), "start_unknown")


def dataset_run_failed_transition(run_kind: str) -> StatusTransition:
    if run_kind == "sync":
        return SYNC_RUN_FAILED
    if run_kind == "transform":
        return TRANSFORM_RUN_FAILED
    return MATERIALIZATION_RUN_ABORT_FAILED
