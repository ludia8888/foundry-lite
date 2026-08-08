"""Fail-closed helpers for human-approved Ontology MCP Action proposals."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from foundry_lite.application.services.aip.action_proposal import compute_action_proposal_fingerprint
from foundry_lite.application.services.aip.approval_execution_types import (
    ApprovalExecutionError,
    PreparedExecution,
)
from foundry_lite.domain.context import RequestContext

_MCP_SOURCE = "ontology_mcp"
_MCP_PROTOCOL_VERSION = "2025-06-18"


def is_external_mcp_execution(prepared: PreparedExecution) -> bool:
    return prepared.source == _MCP_SOURCE


def external_mcp_execution_context(ctx: RequestContext, prepared: PreparedExecution) -> RequestContext:
    if not is_external_mcp_execution(prepared):
        return ctx
    if not prepared.application_id or not prepared.client_id or not prepared.token_scopes:
        raise ApprovalExecutionError("invalid_proposal", "Ontology MCP proposal is missing its authority snapshot")
    return replace(
        ctx,
        application_id=prepared.application_id,
        client_id=prepared.client_id,
        token_scopes=prepared.token_scopes,
        originating_service_principal_id=prepared.originating_actor_user_id,
        originating_mcp_review_id=prepared.review_id,
    )


def require_external_mcp_fingerprint(
    prepared: PreparedExecution,
    evidence_refs: Sequence[Mapping[str, object]],
) -> None:
    application_id = _required(prepared.application_id, "applicationId")
    fingerprint = compute_action_proposal_fingerprint(
        action_type=prepared.action_type,
        target_object_type=prepared.target_object_type,
        target_object_id=prepared.target_object_id,
        expected_object_version=prepared.expected_object_version,
        parameters=prepared.parameters,
        evidence_refs=evidence_refs,
        agent_version_id=f"ontology-mcp:{application_id}:{_MCP_PROTOCOL_VERSION}",
        policy_version=prepared.policy_version,
        plan_hash=prepared.plan_hash,
        action_version=prepared.action_version,
        object_versions=prepared.object_versions,
    )
    if fingerprint != prepared.proposal_fingerprint:
        raise ApprovalExecutionError("fingerprint_mismatch", "approved Ontology MCP proposal was changed")


def _required(value: str | None, field: str) -> str:
    if value is None:
        raise ApprovalExecutionError("invalid_proposal", f"Ontology MCP proposal is missing {field}")
    return value
