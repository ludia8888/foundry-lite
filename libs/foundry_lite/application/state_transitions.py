"""Application-layer models and helpers for state transitions."""

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


DATASET_TRANSACTION_ABORTING = StatusTransition(("OPEN",), "ABORTING")
DATASET_TRANSACTION_ABORT = StatusTransition(("OPEN", "ABORTING"), "ABORTED")
DATASET_TRANSACTION_COMMIT = StatusTransition(("OPEN",), "COMMITTED")
MEDIA_TRANSACTION_COMMIT = StatusTransition(("OPEN",), "COMMITTED")
MEDIA_TRANSACTION_ABORT = StatusTransition(("OPEN",), "ABORTED")
MEDIA_VERSION_COMMITTED = StatusTransition(("STAGED",), "COMMITTED")
MEDIA_DERIVATIVE_COMMITTED = StatusTransition(("STAGED",), "COMMITTED")
MEDIA_DERIVATIVE_FAILED = StatusTransition(("STAGED",), "FAILED")
MEDIA_RUN_SUCCEEDED = StatusTransition(("RUNNING",), "SUCCEEDED")
MEDIA_RUN_FAILED = StatusTransition(("RUNNING",), "FAILED")
SYNC_RUN_COMMITTED = StatusTransition(("EXTRACTING",), "COMMITTED")
SYNC_RUN_FAILED = StatusTransition(("EXTRACTING",), "FAILED")
SOURCE_AGENT_HEARTBEAT = StatusTransition(("registered", "online"), "online")
SOURCE_CONNECTION_DISABLED = StatusTransition(("active",), "disabled")
SOURCE_CONNECTION_ENABLED = StatusTransition(("disabled",), "active")
SOURCE_CONNECTION_TEST_SUCCEEDED = StatusTransition(("running",), "succeeded")
SOURCE_CONNECTION_TEST_FAILED = StatusTransition(("running",), "failed")
SOURCE_SYNC_SCHEDULE_PAUSED = StatusTransition(("active",), "paused")
SOURCE_SYNC_SCHEDULE_RESUMED = StatusTransition(("paused",), "active")
SOURCE_SYNC_RUN_RUNNING = StatusTransition(("running",), "running")
SOURCE_SYNC_RUN_SUCCEEDED = StatusTransition(("running",), "succeeded")
SOURCE_SYNC_RUN_FAILED = StatusTransition(("running",), "failed")
SOURCE_SYNC_RUN_CANCELLED = StatusTransition(("running",), "cancelled")
TRANSFORM_RUN_SUCCEEDED = StatusTransition(("RUNNING",), "SUCCESS")
TRANSFORM_RUN_FAILED = StatusTransition(("RUNNING",), "FAILED")
MATERIALIZATION_RUN_SUCCEEDED = StatusTransition(("running",), "succeeded")
MATERIALIZATION_RUN_FAILED = StatusTransition(("running",), "failed")
MATERIALIZATION_RUN_ABORT_FAILED = StatusTransition(("running",), "FAILED")
INDEX_RUN_SUCCEEDED = StatusTransition(("running",), "succeeded")
INDEX_RUN_FAILED = StatusTransition(("running",), "failed")
ONTOLOGY_VERSION_ARCHIVED = StatusTransition(("active",), "archived")
ONTOLOGY_VERSION_ACTIVE = StatusTransition(("draft",), "active")
# Ontology change proposals ride on insight_reviews storage: a withdrawal moves a
# not-yet-applied proposal (pending or approved) into the terminal rejected state
# while execution_status records that the submitter withdrew it.
ONTOLOGY_PROPOSAL_WITHDRAWN = StatusTransition(("pending", "approved"), "rejected")
# Ontology branches: an open branch becomes merged only once its proposal has
# been executed (merge-by-proposal), or abandoned by its creator/an activate
# holder. Both transitions are terminal and CAS-guarded.
ONTOLOGY_BRANCH_MERGED = StatusTransition(("open",), "merged")
ONTOLOGY_BRANCH_ABANDONED = StatusTransition(("open",), "abandoned")
# An action with a real external side effect is committed into the non-terminal
# ``external_pending`` write-ahead state BEFORE the external write, so the DB connection is
# released for the network round-trip. A crash between that commit and the resolve is recoverable:
# the recovery sweep HEADs the external system and drives the stranded run to a terminal state.
ACTION_RUN_EXTERNAL_PENDING = StatusTransition(("received",), "external_pending")
# ``succeeded``/``failed``/``outcome_unknown``/``compensation_required`` fire either inline from the
# transient ``received`` state (simulated / no-adapter path) or from the committed ``external_pending``
# write-ahead marker (real-adapter path, inline resolve or recovery sweep).
ACTION_RUN_SUCCEEDED = StatusTransition(("received", "external_pending"), "succeeded")
ACTION_RUN_FAILED = StatusTransition(("received", "external_pending"), "failed")
ACTION_RUN_CONFLICT = StatusTransition(("received",), "conflict")
ACTION_RUN_RETRYABLE = StatusTransition(("received",), "retryable")
ACTION_RUN_OUTCOME_UNKNOWN = StatusTransition(("received", "external_pending"), "outcome_unknown")
ACTION_RUN_COMPENSATION_REQUIRED = StatusTransition(("received", "external_pending"), "compensation_required")
ACTION_RUN_RECONCILED = StatusTransition(("outcome_unknown", "compensation_required"), "reconciled")
ACTION_WRITEBACK_RECONCILED = StatusTransition(("outcome_unknown", "compensation_required"), "reconciled")
ACTION_RUN_ASYNC_RUNNING = StatusTransition(("queued", "running"), "running")
ACTION_RUN_COMMIT_PENDING = StatusTransition(("running",), "commit_pending")
ACTION_RUN_CANCELLING = StatusTransition(("queued", "running", "commit_pending"), "cancelling")
ACTION_RUN_ASYNC_SUCCEEDED = StatusTransition(("commit_pending",), "succeeded")
ACTION_RUN_ASYNC_FAILED = StatusTransition(("queued", "running", "commit_pending", "cancelling"), "failed")
ACTION_RUN_ASYNC_CANCELLED = StatusTransition(("queued", "running", "cancelling"), "cancelled")
ACTION_RUN_ASYNC_CONFLICT = StatusTransition(("running", "commit_pending"), "conflict")
ACTION_RUN_ASYNC_OUTCOME_UNKNOWN = StatusTransition(("running",), "outcome_unknown")
ACTION_STEP_RUNNING = StatusTransition(("pending", "running", "retry_wait"), "running")
ACTION_STEP_RETRY_WAIT = StatusTransition(("running",), "retry_wait")
ACTION_STEP_SUCCEEDED = StatusTransition(("running",), "succeeded")
ACTION_STEP_FAILED = StatusTransition(("running",), "failed")
ACTION_STEP_CANCELLED = StatusTransition(("pending", "running", "retry_wait"), "cancelled")
ACTION_STEP_CANCEL_UNSTARTED = StatusTransition(("pending", "retry_wait"), "cancelled")
ACTION_ATTEMPT_SUCCEEDED = StatusTransition(("running",), "succeeded")
ACTION_ATTEMPT_FAILED = StatusTransition(("running",), "failed")
ACTION_ATTEMPT_CANCELLED = StatusTransition(("running",), "cancelled")
ACTION_ATTEMPT_LOST = StatusTransition(("running",), "lost")
ACTION_EFFECT_DELIVERING = StatusTransition(("pending", "retry_wait", "delivering"), "delivering")
ACTION_EFFECT_SUCCEEDED = StatusTransition(("delivering",), "succeeded")
ACTION_EFFECT_RETRY_WAIT = StatusTransition(("delivering",), "retry_wait")
ACTION_EFFECT_DEAD_LETTER = StatusTransition(("delivering",), "dead_letter")
ACTION_EFFECT_OUTCOME_UNKNOWN = StatusTransition(("delivering",), "outcome_unknown")
ACTION_EFFECT_CANCELLED = StatusTransition(("pending", "retry_wait", "delivering"), "cancelled")
ACTION_EFFECT_MANUAL_RETRY = StatusTransition(("dead_letter",), "retry_wait")
ACTION_EFFECT_RECONCILED_SUCCEEDED = StatusTransition(("outcome_unknown",), "succeeded")
ACTION_EFFECT_RECONCILED_DEAD_LETTER = StatusTransition(("outcome_unknown",), "dead_letter")
ACTION_NOTIFICATION_POLICY_ACTIVE = StatusTransition(("active", "disabled"), "active")
ACTION_NOTIFICATION_POLICY_DISABLED = StatusTransition(("active", "disabled"), "disabled")
DEAD_LETTER_REPLAY_REQUESTED = StatusTransition(("QUARANTINED",), "REPLAY_REQUESTED")
DEAD_LETTER_REPLAY_RUNNING = StatusTransition(("REPLAY_REQUESTED", "REPLAYING"), "REPLAYING")
DEAD_LETTER_REPLAY_SUCCEEDED = StatusTransition(("REPLAY_REQUESTED", "REPLAYING"), "RESOLVED")
DEAD_LETTER_REPLAY_FAILED = StatusTransition(("REPLAY_REQUESTED", "REPLAYING"), "QUARANTINED")
DEAD_LETTER_DISCARDED = StatusTransition(("QUARANTINED",), "DISCARDED")
OUTBOX_PUBLISHING = StatusTransition(("pending",), "publishing")
OUTBOX_PUBLISHED = StatusTransition(("publishing",), "published")
OUTBOX_PUBLISH_RETRY_PENDING = StatusTransition(("publishing",), "pending")
OUTBOX_PUBLISH_FAILED = StatusTransition(("publishing",), "failed")
OUTBOX_RETRY_PENDING = StatusTransition(("failed",), "pending")
OUTBOX_PUBLISHING_RECLAIM = StatusTransition(("publishing",), "pending")
ERASURE_REQUEST_EXECUTED = StatusTransition(("requested",), "executed")
WORKFLOW_RUN_STARTING = StatusTransition(("requested", "start_unknown"), "starting")
WORKFLOW_RUN_RUNNING = StatusTransition(("starting",), "running")
WORKFLOW_RUN_SUCCEEDED = StatusTransition(("starting", "running", "start_unknown"), "succeeded")
WORKFLOW_RUN_FAILED = StatusTransition(("requested", "starting", "running", "start_unknown"), "failed")
WORKFLOW_RUN_CANCELLED = StatusTransition(("requested", "starting", "running", "start_unknown"), "cancelled")
WORKFLOW_RUN_START_UNKNOWN = StatusTransition(("requested", "starting"), "start_unknown")
WORKFLOW_RUN_LEASE_RUNNING = StatusTransition(
    ("requested", "starting", "running", "succeeded", "failed", "cancelled", "start_unknown"), "running"
)
AI_RUN_SUCCEEDED = StatusTransition(("started", "running"), "succeeded")
AI_RUN_CHALLENGE_REFRESHED = StatusTransition(("started",), "started")
AI_RUN_FAILED = StatusTransition(("started", "running"), "failed")
AI_RUN_RETRY_RUNNING = StatusTransition(("failed",), "running")
AI_RUN_CANCELLED = StatusTransition(("started", "running"), "cancelled")
AI_EVAL_RUN_PASSED = StatusTransition(("running",), "passed")
AI_EVAL_RUN_FAILED = StatusTransition(("running",), "failed")
PIPELINE_RUN_RUNNING = StatusTransition(("queued", "running", "executing"), "running")
PIPELINE_RUN_EXECUTING = StatusTransition(("running",), "executing")
PIPELINE_RUN_CANCELLING = StatusTransition(("queued", "running", "executing"), "cancelling")
PIPELINE_RUN_SUCCEEDED = StatusTransition(("running", "executing"), "succeeded")
PIPELINE_RUN_PARTIAL = StatusTransition(("running", "executing", "cancelling"), "partial")
PIPELINE_RUN_FAILED = StatusTransition(("queued", "running", "executing"), "failed")
PIPELINE_RUN_CANCELLED = StatusTransition(("queued", "running", "executing", "cancelling"), "cancelled")
PIPELINE_RUN_RECONCILED_SUCCEEDED = StatusTransition(("partial",), "succeeded")
PIPELINE_RUN_RECONCILED_PARTIAL = StatusTransition(("partial",), "partial")
PIPELINE_RUN_RECONCILED_FAILED = StatusTransition(("partial",), "failed")
PIPELINE_NODE_RUN_RUNNING = StatusTransition(("PENDING", "READY", "RETRY_WAIT"), "RUNNING")
PIPELINE_NODE_RUN_ATTEMPT_CLAIMED = StatusTransition(("PENDING", "READY", "RUNNING", "RETRY_WAIT"), "RUNNING")
PIPELINE_NODE_RUN_SUCCEEDED = StatusTransition(("RUNNING",), "SUCCEEDED")
PIPELINE_NODE_RUN_FAILED = StatusTransition(("RUNNING",), "FAILED")
PIPELINE_NODE_RUN_RETRY_WAIT = StatusTransition(("RUNNING", "FAILED"), "RETRY_WAIT")
PIPELINE_NODE_RUN_SKIPPED = StatusTransition(("PENDING",), "SKIPPED")
PIPELINE_NODE_RUN_CANCELLED = StatusTransition(("PENDING", "READY", "RETRY_WAIT", "RUNNING"), "CANCELLED")
PIPELINE_NODE_ATTEMPT_SUCCEEDED = StatusTransition(("RUNNING",), "SUCCEEDED")
PIPELINE_NODE_ATTEMPT_FAILED = StatusTransition(("RUNNING",), "FAILED")
PIPELINE_NODE_ATTEMPT_LOST = StatusTransition(("RUNNING",), "LOST")
PIPELINE_NODE_ATTEMPT_CANCELLED = StatusTransition(("RUNNING",), "CANCELLED")
PIPELINE_PREVIEW_RUNNING = StatusTransition(("QUEUED",), "RUNNING")
PIPELINE_PREVIEW_CANCEL_REQUESTED = StatusTransition(("QUEUED", "RUNNING"), "CANCEL_REQUESTED")
PIPELINE_PREVIEW_SUCCEEDED = StatusTransition(("QUEUED", "RUNNING"), "SUCCEEDED")
PIPELINE_PREVIEW_FAILED = StatusTransition(("QUEUED", "RUNNING"), "FAILED")
PIPELINE_PREVIEW_CANCELLED = StatusTransition(("QUEUED", "RUNNING", "CANCEL_REQUESTED"), "CANCELLED")
DATASET_QUALITY_CONTRACT_ACTIVE = StatusTransition(("DRAFT",), "ACTIVE")
DATASET_QUALITY_CONTRACT_SUPERSEDED = StatusTransition(("ACTIVE",), "SUPERSEDED")
RESOURCE_CATALOG_TRASH = StatusTransition(("active",), "trashed")
RESOURCE_CATALOG_RESTORE = StatusTransition(("trashed",), "active")
OSDK_APPLICATION_CLIENT_ACTIVE = StatusTransition(("active", "inactive"), "active")
OSDK_APPLICATION_CLIENT_INACTIVE = StatusTransition(("active", "inactive"), "inactive")
OSDK_CLIENT_SECRET_ROTATED = StatusTransition(("active",), "rotated")
OSDK_CLIENT_SECRET_REVOKED = StatusTransition(("active",), "revoked")
OSDK_MCP_SESSION_TERMINATED = StatusTransition(("active",), "terminated")
OSDK_DOWNLOAD_TOKEN_USED = StatusTransition(("active",), "used")
OSDK_OAUTH_REFRESH_REVOKED = StatusTransition(("active", "rotated"), "revoked")
OSDK_OAUTH_REFRESH_ROTATED = StatusTransition(("active",), "rotated")
OSDK_OAUTH_SESSION_COMPROMISED = StatusTransition(("active",), "compromised")
OSDK_OAUTH_SESSION_REVOKED = StatusTransition(("active",), "revoked")
OBSERVABILITY_INCIDENT_ACKNOWLEDGED = StatusTransition(("open",), "acknowledged")
OBSERVABILITY_INCIDENT_RESOLVED = StatusTransition(("open", "acknowledged"), "resolved")


def observability_incident_transition(status: str) -> StatusTransition:
    if status == "acknowledged":
        return OBSERVABILITY_INCIDENT_ACKNOWLEDGED
    if status == "resolved":
        return OBSERVABILITY_INCIDENT_RESOLVED
    raise ValueError(f"unsupported observability incident status transition: {status}")


def dataset_run_failed_transition(run_kind: str) -> StatusTransition:
    if run_kind == "sync":
        return SYNC_RUN_FAILED
    if run_kind == "transform":
        return TRANSFORM_RUN_FAILED
    return MATERIALIZATION_RUN_ABORT_FAILED
