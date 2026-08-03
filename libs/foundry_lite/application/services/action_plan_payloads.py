"""Public, redacted payload projection for resolved Action execution plans."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.action_types import ActionExecutionPlanResponse
from foundry_lite.application.ports import ObjectRecordRow, TransactionContext
from foundry_lite.application.services.action_protocols import ActionObjectRecordLookup
from foundry_lite.domain.action_runtime.edit_plan import EditPlan, ObjectModify
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected
from foundry_lite.security.policy import PolicyService


def action_plan_object_versions(plan: ActionExecutionPlanResponse) -> dict[str, object]:
    target = plan["target"]
    object_type = target.get("objectType")
    object_id = target.get("objectId")
    version = target.get("expectedObjectVersion")
    result: dict[str, object] = {}
    if isinstance(object_type, str) and isinstance(object_id, str) and isinstance(version, int):
        result[f"{object_type}:{object_id}"] = version
    for diff in plan["diffs"]:
        _add_diff_version(result, diff)
    return dict(sorted(result.items()))


def action_plan_diffs(
    conn: TransactionContext,
    ctx: RequestContext,
    policy: PolicyService,
    objects: ActionObjectRecordLookup,
    plan: EditPlan,
) -> list[dict[str, object]]:
    diffs = [
        _create_diff(ctx, policy, item.object_type, str(item.primary_key), dict(item.properties))
        for item in plan.objects_to_create
    ]
    diffs.extend(_modify_diff(conn, ctx, policy, objects, item) for item in plan.objects_to_modify)
    diffs.extend(
        _delete_diff(conn, ctx, policy, objects, item.object_type, item.object_id) for item in plan.objects_to_delete
    )
    diffs.extend(
        _link_diff("create_link", item.link_type, item.source_object_id, item.target_object_id)
        for item in plan.links_to_create
    )
    diffs.extend(
        _link_diff("delete_link", item.link_type, item.source_object_id, item.target_object_id)
        for item in plan.links_to_delete
    )
    return diffs


def _create_diff(
    ctx: RequestContext,
    policy: PolicyService,
    object_type: str,
    object_id: str,
    properties: dict[str, object],
) -> dict[str, object]:
    return {
        "operation": "create_object",
        "objectType": object_type,
        "objectId": object_id,
        "before": None,
        "after": policy.mask_sensitive_properties(ctx, object_type, properties),
    }


def _modify_diff(
    conn: TransactionContext,
    ctx: RequestContext,
    policy: PolicyService,
    objects: ActionObjectRecordLookup,
    item: ObjectModify,
) -> dict[str, object]:
    record = objects._object_record(conn, ctx, item.object_type, item.object_id)
    if record is None and item.should_create_if_absent:
        return _create_diff(ctx, policy, item.object_type, item.object_id, dict(item.patch))
    current = _required_record(record, item.object_type, item.object_id)
    before = {name: current["properties"].get(name) for name in item.patch}
    after = {**before, **item.patch}
    return {
        "operation": "modify_object",
        "objectType": item.object_type,
        "objectId": item.object_id,
        "expectedObjectVersion": item.expected_version,
        "before": policy.mask_sensitive_properties(ctx, item.object_type, before),
        "after": policy.mask_sensitive_properties(ctx, item.object_type, after),
    }


def _delete_diff(
    conn: TransactionContext,
    ctx: RequestContext,
    policy: PolicyService,
    objects: ActionObjectRecordLookup,
    object_type: str,
    object_id: str,
) -> dict[str, object]:
    current = _required_record(objects._object_record(conn, ctx, object_type, object_id), object_type, object_id)
    return {
        "operation": "delete_object",
        "objectType": object_type,
        "objectId": object_id,
        "expectedObjectVersion": current["object_version"],
        "before": policy.mask_sensitive_properties(ctx, object_type, dict(current["properties"])),
        "after": None,
    }


def _required_record(
    record: ObjectRecordRow | None,
    object_type: str,
    object_id: str,
) -> ObjectRecordRow:
    if record is not None:
        return record
    raise ConflictDetected(
        "planned object disappeared before the plan snapshot was built",
        details={"objectType": object_type, "objectId": object_id},
    )


def _link_diff(operation: str, link_type: str, source: str, target: str) -> dict[str, object]:
    return {
        "operation": operation,
        "linkType": link_type,
        "sourceObjectId": source,
        "targetObjectId": target,
        "before": operation == "delete_link",
        "after": operation == "create_link",
    }


def _add_diff_version(result: dict[str, object], diff: Mapping[str, object]) -> None:
    diff_type = diff.get("objectType")
    diff_id = diff.get("objectId")
    diff_version = diff.get("expectedObjectVersion")
    if isinstance(diff_type, str) and isinstance(diff_id, str) and isinstance(diff_version, int):
        result[f"{diff_type}:{diff_id}"] = diff_version
