"""Public request, result, and error contracts for AIP Action proposals."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from foundry_lite.application.services.mcp_json_rpc import JsonRpcRequestId
from foundry_lite.domain.errors import FoundryLiteError

JsonObject = Mapping[str, object]


@dataclass(frozen=True)
class ActionProposalRequest:
    """One model-proposed ontology action awaiting human review."""

    originating_ai_run_id: str
    action_type: str
    target_object_type: str
    target_object_id: str
    expected_object_version: int
    parameters: JsonObject
    evidence_context_ids: tuple[str, ...]
    agent_allowed_actions: tuple[str, ...]
    policy_version: str
    expires_at: str
    claim_text: str
    originating_tool_call_id: str | None = None
    originating_json_rpc_id: JsonRpcRequestId | None = None
    priority: str = "normal"
    assignee_user_id: str | None = None


@dataclass(frozen=True)
class ActionProposalResult:
    """Created review plus canonical proposal fingerprint."""

    proposal_id: str
    proposal_fingerprint: str
    review_id: str
    review_payload: Mapping[str, object]
    action_proposal: Mapping[str, object]


@dataclass
class ActionProposalError(FoundryLiteError):
    """Typed fail-closed action proposal rejection."""

    code = "ACTION_PROPOSAL_ERROR"

    reason: str
    detail: str

    def __post_init__(self) -> None:
        FoundryLiteError.__init__(self, self.detail, details={"reason": self.reason})
