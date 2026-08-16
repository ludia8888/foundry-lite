"""Canonical mapping from workflow terminal evidence to Source Sync state."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from foundry_lite.domain.error_redaction import scrub_error_mapping


@dataclass(frozen=True)
class SourceWorkflowCompletion:
    status: str
    dataset_version_id: str | None
    output: Mapping[str, object]
    error: Mapping[str, object] | None


def source_workflow_completion(workflow: Mapping[str, object]) -> SourceWorkflowCompletion:
    status = str(workflow.get("status") or "running")
    output = scrub_error_mapping(_mapping(workflow.get("output")))
    if status == "succeeded":
        committed_version_id = output.get("committedVersionId")
        if (
            isinstance(committed_version_id, str)
            and committed_version_id
            and committed_version_id.strip() == committed_version_id
        ):
            return SourceWorkflowCompletion("succeeded", committed_version_id, output, None)
        return SourceWorkflowCompletion(
            "failed",
            None,
            output,
            {"type": "WORKFLOW_OUTPUT_INVALID", "message": "successful workflow omitted committedVersionId"},
        )
    if status == "failed":
        error = scrub_error_mapping(_mapping(workflow.get("error")))
        if not error:
            error = {"type": "WORKFLOW_FAILED", "message": "workflow failed without error evidence"}
        return SourceWorkflowCompletion("failed", None, output, error)
    if status == "cancelled":
        return SourceWorkflowCompletion("cancelled", None, output, None)
    if status in {"requested", "starting", "queued", "running", "start_unknown"}:
        return SourceWorkflowCompletion("running", None, output, None)
    return SourceWorkflowCompletion(
        "failed",
        None,
        output,
        {"type": "WORKFLOW_STATUS_INVALID", "message": "workflow returned an unsupported status"},
    )


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


__all__ = ["SourceWorkflowCompletion", "source_workflow_completion"]
