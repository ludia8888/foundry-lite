from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol, runtime_checkable

from foundry_lite.application.state_transitions import (
    ACTION_RUN_COMPENSATION_REQUIRED,
    ACTION_RUN_CONFLICT,
    ACTION_RUN_FAILED,
    ACTION_RUN_OUTCOME_UNKNOWN,
    ACTION_RUN_RECONCILED,
    ACTION_RUN_SUCCEEDED,
    ACTION_WRITEBACK_RECONCILED,
    DATASET_TRANSACTION_ABORT,
    DATASET_TRANSACTION_ABORTING,
    DATASET_TRANSACTION_COMMIT,
    DEAD_LETTER_DISCARDED,
    DEAD_LETTER_REPLAY_FAILED,
    DEAD_LETTER_REPLAY_REQUESTED,
    DEAD_LETTER_REPLAY_RUNNING,
    DEAD_LETTER_REPLAY_SUCCEEDED,
    INDEX_RUN_FAILED,
    INDEX_RUN_SUCCEEDED,
    MATERIALIZATION_RUN_ABORT_FAILED,
    MATERIALIZATION_RUN_FAILED,
    MATERIALIZATION_RUN_SUCCEEDED,
    ONTOLOGY_VERSION_ACTIVE,
    ONTOLOGY_VERSION_ARCHIVED,
    OUTBOX_RETRY_PENDING,
    SYNC_RUN_COMMITTED,
    SYNC_RUN_FAILED,
    TRANSFORM_RUN_FAILED,
    TRANSFORM_RUN_SUCCEEDED,
    WORKFLOW_RUN_CANCELLED,
    WORKFLOW_RUN_FAILED,
    WORKFLOW_RUN_RUNNING,
    WORKFLOW_RUN_START_UNKNOWN,
    WORKFLOW_RUN_STARTING,
    WORKFLOW_RUN_SUCCEEDED,
    StatusTransition,
    TransitionOutcome,
    TransitionResult,
    dataset_run_failed_transition,
)


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


__all__ = [
    "ACTION_RUN_COMPENSATION_REQUIRED",
    "ACTION_RUN_CONFLICT",
    "ACTION_RUN_FAILED",
    "ACTION_RUN_OUTCOME_UNKNOWN",
    "ACTION_RUN_RECONCILED",
    "ACTION_RUN_SUCCEEDED",
    "ACTION_WRITEBACK_RECONCILED",
    "DATASET_TRANSACTION_ABORT",
    "DATASET_TRANSACTION_ABORTING",
    "DATASET_TRANSACTION_COMMIT",
    "DEAD_LETTER_DISCARDED",
    "DEAD_LETTER_REPLAY_FAILED",
    "DEAD_LETTER_REPLAY_REQUESTED",
    "DEAD_LETTER_REPLAY_RUNNING",
    "DEAD_LETTER_REPLAY_SUCCEEDED",
    "INDEX_RUN_FAILED",
    "INDEX_RUN_SUCCEEDED",
    "MATERIALIZATION_RUN_ABORT_FAILED",
    "MATERIALIZATION_RUN_FAILED",
    "MATERIALIZATION_RUN_SUCCEEDED",
    "ONTOLOGY_VERSION_ACTIVE",
    "ONTOLOGY_VERSION_ARCHIVED",
    "OUTBOX_RETRY_PENDING",
    "SYNC_RUN_COMMITTED",
    "SYNC_RUN_FAILED",
    "TRANSFORM_RUN_FAILED",
    "TRANSFORM_RUN_SUCCEEDED",
    "WORKFLOW_RUN_CANCELLED",
    "WORKFLOW_RUN_FAILED",
    "WORKFLOW_RUN_RUNNING",
    "WORKFLOW_RUN_START_UNKNOWN",
    "WORKFLOW_RUN_STARTING",
    "WORKFLOW_RUN_SUCCEEDED",
    "StatusTransition",
    "TransactionContext",
    "TransactionManager",
    "TransitionOutcome",
    "TransitionResult",
    "dataset_run_failed_transition",
]
