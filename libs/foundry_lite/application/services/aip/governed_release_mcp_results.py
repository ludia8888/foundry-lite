"""MCP result envelopes for Governed Release actions and exact replay."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.services.aip.governed_release_outcomes import (
    GovernedReleaseMutationOutcomeUnknown,
    project_confirmed_mutation,
)
from foundry_lite.application.services.aip.governed_release_run_evidence import is_known_not_committed_error
from foundry_lite.application.services.mcp_tool_results import serialized_text_content, tool_error_result
from foundry_lite.application.services.pipeline_deployment_admission import PipelineDeploymentOutcomeUnknown

JsonObject = Mapping[str, object]


def success_result(
    output: JsonObject,
    *,
    run_id: str | None = None,
    tool_call_id: str | None = None,
    result_meta: JsonObject | None = None,
) -> dict[str, object]:
    """Build a fresh successful MCP result with optional private metadata."""

    result: dict[str, object] = {
        "structuredContent": dict(output),
        "content": serialized_text_content(output),
        "isError": False,
        "isReplayed": False,
    }
    if run_id is not None:
        result["aiRunId"] = run_id
    if tool_call_id is not None:
        result["toolCallId"] = tool_call_id
    if result_meta is not None:
        result["_meta"] = dict(result_meta)
    return result


def replay_result(
    run_id: str,
    tool_call_id: str,
    output: JsonObject,
    *,
    is_error: bool,
) -> dict[str, object]:
    """Build the stable MCP envelope returned by an exact durable replay."""

    return {
        "aiRunId": run_id,
        "toolCallId": tool_call_id,
        "structuredContent": dict(output),
        "content": serialized_text_content(output),
        "isError": is_error,
        "isReplayed": True,
    }


__all__ = [
    "GovernedReleaseMutationOutcomeUnknown",
    "is_known_not_committed_error",
    "PipelineDeploymentOutcomeUnknown",
    "project_confirmed_mutation",
    "replay_result",
    "success_result",
    "tool_error_result",
]
