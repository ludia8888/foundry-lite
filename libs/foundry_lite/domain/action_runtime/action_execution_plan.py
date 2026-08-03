"""Deterministic serialization and sealing of resolved Ontology edit plans."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from foundry_lite.domain.action_runtime.edit_plan import EditPlan, LinkCreate, LinkDelete


def edit_plan_manifest(plan: EditPlan) -> dict[str, object]:
    """Return the stable, storage-safe operation manifest used by approvals."""
    return {
        "objectCreates": [
            {
                "operationKey": item.operation_key,
                "ruleId": item.rule_id,
                "objectType": item.object_type,
                "objectId": item.primary_key,
                "properties": dict(item.properties),
            }
            for item in plan.objects_to_create
        ],
        "objectModifies": [
            {
                "operationKey": item.operation_key,
                "ruleId": item.rule_id,
                "objectType": item.object_type,
                "objectId": item.object_id,
                "expectedObjectVersion": item.expected_version,
                "patch": dict(item.patch),
                "createIfAbsent": item.should_create_if_absent,
            }
            for item in plan.objects_to_modify
        ],
        "objectDeletes": [
            {
                "operationKey": item.operation_key,
                "ruleId": item.rule_id,
                "objectType": item.object_type,
                "objectId": item.object_id,
                "expectedObjectVersion": item.expected_version,
            }
            for item in plan.objects_to_delete
        ],
        "linkCreates": [_link_manifest(item) for item in plan.links_to_create],
        "linkDeletes": [_link_manifest(item) for item in plan.links_to_delete],
        "readSetVersions": dict(sorted(plan.read_set_versions.items())),
    }


def seal_action_execution_plan(payload: Mapping[str, object]) -> dict[str, object]:
    """Attach a sha256 over every execution-relevant field except presentation metadata."""
    ignored = {"planHash", "requestId", "createdAt", "isDryRun", "authorization"}
    canonical = {key: value for key, value in payload.items() if key not in ignored}
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str)
    return {**payload, "planHash": f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"}


def _link_manifest(item: LinkCreate | LinkDelete) -> dict[str, object]:
    return {
        "operationKey": item.operation_key,
        "ruleId": item.rule_id,
        "linkType": item.link_type,
        "sourceObjectId": item.source_object_id,
        "targetObjectId": item.target_object_id,
    }
