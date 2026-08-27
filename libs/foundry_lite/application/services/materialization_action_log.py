"""Action-log row projection for materialization runs."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from foundry_lite.application.ports import MaterializationRepository, RuntimeRow, TransactionContext
from foundry_lite.domain.context import RequestContext
from foundry_lite.security.policy import PolicyService


def action_log_materialization_rows(
    repository: MaterializationRepository,
    policy: PolicyService,
    conn: TransactionContext,
    ctx: RequestContext,
    watermark: Mapping[str, object],
) -> tuple[Sequence[Mapping[str, object]], list[str]]:
    rows = [
        _action_log_row(policy, ctx, action)
        for action in repository.action_log_rows_at_watermark(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            completed_at_lte=_optional_string(watermark.get("action_run_completed_at_lte")),
            action_run_id_lte=_optional_string(watermark.get("action_run_id_lte")),
        )
    ]
    return rows, [
        "action_run_id",
        "actor_user_id",
        "action_type",
        "target_object_type",
        "target_object_id",
        "status",
        "parameters_json",
        "edit_patch_json",
        "created_at",
    ]


def _action_log_row(
    policy: PolicyService,
    ctx: RequestContext,
    action: RuntimeRow,
) -> Mapping[str, object]:
    object_type = str(action["target_object_type_api_name"])
    parameters = _masked_action_mapping(policy, ctx, object_type, action.get("parameters"))
    edit_patch = _masked_action_mapping(policy, ctx, object_type, action.get("edit_patch"))
    return {
        "action_run_id": action["action_run_id"],
        "actor_user_id": action["actor_user_id"],
        "action_type": action["action_type_api_name"],
        "target_object_type": action["target_object_type_api_name"],
        "target_object_id": action["target_object_id"],
        "status": action["status"],
        "parameters_json": json.dumps(parameters, sort_keys=True),
        "edit_patch_json": json.dumps(edit_patch, sort_keys=True),
        "created_at": action["created_at"],
    }


def _masked_action_mapping(
    policy: PolicyService,
    ctx: RequestContext,
    object_type: str,
    value: object,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return policy.mask_sensitive_properties(ctx, object_type, dict(value))


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None
