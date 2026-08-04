"""Typed commit results and stable public/audit payloads for Action edit plans."""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.action_types import ActionPlanSummary


@dataclass(frozen=True)
class CommittedEdit:
    """One object edit that the plan produced, for the run result and events."""

    edit_id: str
    object_type: str
    object_id: str
    operation: str


@dataclass(frozen=True)
class ActionEditPlanResult:
    """Atomic commit result across all object and link edits."""

    action_run_id: str
    status: str
    edits: tuple[CommittedEdit, ...]
    created_object_ids: tuple[str, ...]
    deleted_object_ids: tuple[str, ...]
    links_created: int
    links_deleted: int


def action_edit_audit_summary(result: ActionEditPlanResult) -> dict[str, object]:
    """Build the bounded object/link summary stored in audit evidence."""
    return {
        "edits": [
            {"objectType": edit.object_type, "objectId": edit.object_id, "operation": edit.operation}
            for edit in result.edits
        ],
        "linksCreated": result.links_created,
        "linksDeleted": result.links_deleted,
    }


def plan_summary(result: ActionEditPlanResult) -> ActionPlanSummary:
    """Build the result shape shared by fresh responses and idempotent replay."""
    return {
        "editCount": len(result.edits),
        "createdObjectIds": list(result.created_object_ids),
        "deletedObjectIds": list(result.deleted_object_ids),
        "linksCreated": result.links_created,
        "linksDeleted": result.links_deleted,
        "edits": [
            {"objectType": edit.object_type, "objectId": edit.object_id, "operation": edit.operation}
            for edit in result.edits
        ],
    }


def action_edit_result_payload(result: ActionEditPlanResult) -> dict[str, object]:
    """Build the terminal Action run result stored with the commit."""
    return {"status": result.status, "plan": dict(plan_summary(result))}
