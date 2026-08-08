"""Typed payloads for approved AIP action execution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from foundry_lite.domain.errors import ValidationFailed

JsonObject = Mapping[str, object]


@dataclass(frozen=True)
class ApprovalExecutionRequest:
    review_id: str
    expected_proposal_fingerprint: str
    idempotency_key: str


@dataclass(frozen=True)
class ApprovalExecutionResult:
    review_id: str
    proposal_fingerprint: str
    action_run_id: str
    action_response: Mapping[str, object]
    review_payload: Mapping[str, object]


@dataclass
class ApprovalExecutionError(ValidationFailed):
    reason: str
    detail: str

    def __post_init__(self) -> None:
        ValidationFailed.__init__(self, self.detail, details={"reason": self.reason})


@dataclass(frozen=True)
class PreparedExecution:
    review_id: str
    proposal_fingerprint: str
    originating_ai_run_id: str
    originating_tool_call_id: str | None
    action_type: str
    target_object_type: str
    target_object_id: str
    expected_object_version: int
    parameters: JsonObject
    policy_version: str
    originating_actor_user_id: str
    source: str | None = None
    application_id: str | None = None
    client_id: str | None = None
    token_scopes: tuple[str, ...] = ()
    plan_hash: str | None = None
    action_version: str | None = None
    object_versions: JsonObject | None = None


@dataclass(frozen=True)
class ExecutionClaim:
    idempotency_key: str
    request_fingerprint: str
    proposal_fingerprint: str
