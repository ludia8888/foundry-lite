"""Pure contracts for durable multi-request Function-backed Actions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from foundry_lite.domain.action_runtime.action_contract import ActionDefinitionV3
from foundry_lite.domain.action_runtime.action_execution_plan import seal_action_execution_plan
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import InvariantViolation, ValidationFailed

ACTION_FUNCTION_BATCH_SENTINEL = "__function_batch__"


@dataclass(frozen=True, slots=True)
class ActionFunctionBatchItem:
    object_id: str
    expected_object_version: int
    parameters: Mapping[str, object]


def parse_action_function_batch_items(
    raw_items: Sequence[Mapping[str, object]],
    *,
    maximum: int,
) -> tuple[ActionFunctionBatchItem, ...]:
    if not raw_items:
        raise ValidationFailed("function Action batch requires at least one item")
    if len(raw_items) > maximum:
        raise ValidationFailed(
            "function Action batch limit exceeded",
            details={"itemCount": len(raw_items), "itemLimit": maximum},
        )
    items = tuple(_batch_item(index, item) for index, item in enumerate(raw_items))
    identities = [item.object_id for item in items]
    if len(set(identities)) != len(identities):
        raise ValidationFailed("function Action batch targets must be unique")
    return items


def action_function_batch_fingerprint(
    ctx: RequestContext,
    action_api_name: str,
    object_type: str,
    items: Sequence[ActionFunctionBatchItem],
) -> str:
    payload = {
        "actionApiName": action_api_name,
        "applicationId": ctx.application_id,
        "clientId": ctx.client_id,
        "objectType": object_type,
        "items": [
            {
                "objectId": item.object_id,
                "expectedObjectVersion": item.expected_object_version,
                "parameters": dict(item.parameters),
            }
            for item in items
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def action_function_batch_plan(
    plans: Sequence[Mapping[str, object]],
    contract: ActionDefinitionV3,
) -> dict[str, object]:
    if not plans or contract.function is None:
        raise InvariantViolation("function Action batch requires planned items and a function contract")
    first = dict(plans[0])
    _require_matching_plans(plans, first)
    first.pop("planHash", None)
    first["target"] = {
        "objectType": contract.target.api_name,
        "objectId": ACTION_FUNCTION_BATCH_SENTINEL,
        "expectedObjectVersion": 0,
        "readObjectVersion": 0,
    }
    first["parameters"] = {}
    first["editManifest"] = _batch_edit_manifest(plans)
    first["diffs"] = []
    first["batchItems"] = [_planned_item(plan) for plan in plans]
    first["batchSize"] = len(plans)
    first["executionMode"] = f"function_batch_{contract.function.execution_mode}"
    return seal_action_execution_plan(first)


def stored_action_function_batch_items(snapshot: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    raw = snapshot.get("batchItems")
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        raise InvariantViolation("stored Action function batch is invalid")
    return tuple(_mapping(item, "batchItems") for item in cast(Sequence[object], raw))


def _batch_item(index: int, raw: Mapping[str, object]) -> ActionFunctionBatchItem:
    object_id = raw.get("objectId")
    expected_version = raw.get("expectedObjectVersion")
    parameters = raw.get("params")
    if not isinstance(object_id, str) or not object_id:
        raise ValidationFailed("function Action batch item requires objectId", details={"itemIndex": index})
    if not isinstance(expected_version, int) or isinstance(expected_version, bool) or expected_version < 0:
        raise ValidationFailed(
            "function Action batch item requires expectedObjectVersion",
            details={"itemIndex": index},
        )
    if not isinstance(parameters, Mapping):
        raise ValidationFailed("function Action batch item requires params", details={"itemIndex": index})
    return ActionFunctionBatchItem(object_id, expected_version, cast(Mapping[str, object], parameters))


def _require_matching_plans(plans: Sequence[Mapping[str, object]], first: Mapping[str, object]) -> None:
    keys = ("actionApiName", "ontologyVersionId", "definitionFingerprint", "functionVersion")
    for index, plan in enumerate(plans[1:], start=1):
        if any(plan.get(key) != first.get(key) for key in keys):
            raise InvariantViolation(
                "function Action batch plans do not share one deployment", details={"index": index}
            )


def _batch_edit_manifest(plans: Sequence[Mapping[str, object]]) -> dict[str, object]:
    criteria: list[object] = []
    for plan in plans:
        item_manifest = _mapping(plan.get("editManifest"), "editManifest")
        raw = item_manifest.get("criteriaReadSet", ())
        if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
            raise InvariantViolation("function Action batch criteria manifest is invalid")
        criteria.extend(raw)
    combined_manifest: dict[str, object] = {
        "objectCreates": [],
        "objectModifies": [],
        "objectDeletes": [],
        "linkCreates": [],
        "linkDeletes": [],
        "readSetVersions": {},
    }
    if criteria:
        combined_manifest["criteriaReadSet"] = criteria
    return combined_manifest


def _planned_item(plan: Mapping[str, object]) -> dict[str, object]:
    return {
        "target": dict(_mapping(plan.get("target"), "target")),
        "parameters": dict(_mapping(plan.get("parameters"), "parameters")),
    }


def _mapping(raw: object, field: str) -> Mapping[str, object]:
    if not isinstance(raw, Mapping):
        raise InvariantViolation("stored Action function batch field is invalid", details={"field": field})
    return cast(Mapping[str, object], raw)
