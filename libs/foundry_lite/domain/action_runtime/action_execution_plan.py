"""Deterministic serialization and sealing of resolved Ontology edit plans."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import cast

from foundry_lite.domain.action_runtime.edit_plan import (
    CriteriaReadExpectation,
    EditPlan,
    LinkCreate,
    LinkDelete,
    ObjectCreate,
    ObjectDelete,
    ObjectModify,
    validate_edit_plan,
)
from foundry_lite.domain.errors import ValidationFailed


def edit_plan_manifest(plan: EditPlan) -> dict[str, object]:
    """Return the stable, storage-safe operation manifest used by approvals."""
    manifest: dict[str, object] = {
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
    if plan.criteria_read_expectations:
        manifest["criteriaReadSet"] = [_criteria_read_manifest(item) for item in plan.criteria_read_expectations]
    return manifest


def seal_action_execution_plan(payload: Mapping[str, object]) -> dict[str, object]:
    """Attach a sha256 over every execution-relevant field except presentation metadata."""
    ignored = {"planHash", "requestId", "createdAt", "isDryRun", "authorization"}
    canonical = {key: value for key, value in payload.items() if key not in ignored}
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str)
    return {**payload, "planHash": f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"}


def edit_plan_from_manifest(manifest: Mapping[str, object]) -> EditPlan:
    """Rebuild the immutable plan frozen before asynchronous dispatch."""
    plan = EditPlan(
        objects_to_create=tuple(_object_create(item) for item in _items(manifest, "objectCreates")),
        objects_to_modify=tuple(_object_modify(item) for item in _items(manifest, "objectModifies")),
        objects_to_delete=tuple(_object_delete(item) for item in _items(manifest, "objectDeletes")),
        links_to_create=tuple(_link_create(item) for item in _items(manifest, "linkCreates")),
        links_to_delete=tuple(_link_delete(item) for item in _items(manifest, "linkDeletes")),
        read_set_versions=_read_versions(manifest.get("readSetVersions")),
        criteria_read_expectations=tuple(_criteria_read(item) for item in _items(manifest, "criteriaReadSet")),
    )
    validate_edit_plan(plan)
    return plan


def criteria_read_expectations_from_manifest(
    manifest: Mapping[str, object],
) -> tuple[CriteriaReadExpectation, ...]:
    """Read criteria OCC evidence even when a function-backed plan has no static edits."""
    return tuple(_criteria_read(item) for item in _items(manifest, "criteriaReadSet"))


def _link_manifest(item: LinkCreate | LinkDelete) -> dict[str, object]:
    return {
        "operationKey": item.operation_key,
        "ruleId": item.rule_id,
        "linkType": item.link_type,
        "sourceObjectId": item.source_object_id,
        "targetObjectId": item.target_object_id,
    }


def _criteria_read_manifest(item: CriteriaReadExpectation) -> dict[str, object]:
    return {
        "referenceKey": item.reference_key,
        "linkType": item.link_type,
        "direction": item.direction,
        "anchorObjectType": item.anchor_object_type,
        "anchorObjectId": item.anchor_object_id,
        "linkedObjectType": item.linked_object_type,
        "property": item.property_name,
        "aggregation": item.aggregation,
        "snapshotFingerprint": item.snapshot_fingerprint,
    }


def _criteria_read(item: Mapping[str, object]) -> CriteriaReadExpectation:
    direction = _text(item, "direction")
    aggregation = _text(item, "aggregation")
    if direction not in {"incoming", "outgoing"}:
        raise ValidationFailed("Action criteria read direction is invalid")
    if aggregation not in {"values", "count"}:
        raise ValidationFailed("Action criteria read aggregation is invalid")
    return CriteriaReadExpectation(
        reference_key=_text(item, "referenceKey"),
        link_type=_text(item, "linkType"),
        direction=direction,
        anchor_object_type=_text(item, "anchorObjectType"),
        anchor_object_id=_text(item, "anchorObjectId"),
        linked_object_type=_text(item, "linkedObjectType"),
        property_name=_text(item, "property"),
        aggregation=aggregation,
        snapshot_fingerprint=_text(item, "snapshotFingerprint"),
    )


def _object_create(item: Mapping[str, object]) -> ObjectCreate:
    return ObjectCreate(
        _text(item, "operationKey"),
        _text(item, "ruleId"),
        _text(item, "objectType"),
        item.get("objectId"),
        _mapping(item.get("properties"), "properties"),
    )


def _object_modify(item: Mapping[str, object]) -> ObjectModify:
    return ObjectModify(
        _text(item, "operationKey"),
        _text(item, "ruleId"),
        _text(item, "objectType"),
        _text(item, "objectId"),
        _version(item, "expectedObjectVersion"),
        _mapping(item.get("patch"), "patch"),
        bool(item.get("createIfAbsent", False)),
    )


def _object_delete(item: Mapping[str, object]) -> ObjectDelete:
    return ObjectDelete(
        _text(item, "operationKey"),
        _text(item, "ruleId"),
        _text(item, "objectType"),
        _text(item, "objectId"),
        _version(item, "expectedObjectVersion"),
    )


def _link_create(item: Mapping[str, object]) -> LinkCreate:
    return LinkCreate(
        _text(item, "operationKey"),
        _text(item, "ruleId"),
        _text(item, "linkType"),
        _text(item, "sourceObjectId"),
        _text(item, "targetObjectId"),
    )


def _link_delete(item: Mapping[str, object]) -> LinkDelete:
    return LinkDelete(
        _text(item, "operationKey"),
        _text(item, "ruleId"),
        _text(item, "linkType"),
        _text(item, "sourceObjectId"),
        _text(item, "targetObjectId"),
    )


def _items(manifest: Mapping[str, object], key: str) -> tuple[Mapping[str, object], ...]:
    raw = manifest.get(key, ())
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        raise ValidationFailed("Action plan manifest field must be a list", details={"field": key})
    return tuple(_mapping(item, key) for item in cast(Sequence[object], raw))


def _mapping(raw: object, field: str) -> Mapping[str, object]:
    if not isinstance(raw, Mapping):
        raise ValidationFailed("Action plan manifest field must be an object", details={"field": field})
    return cast(Mapping[str, object], raw)


def _text(item: Mapping[str, object], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value:
        raise ValidationFailed("Action plan manifest text field is invalid", details={"field": key})
    return value


def _version(item: Mapping[str, object], key: str) -> int:
    value = item.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValidationFailed("Action plan manifest version is invalid", details={"field": key})
    return value


def _read_versions(raw: object) -> dict[str, int]:
    values = _mapping(raw or {}, "readSetVersions")
    result: dict[str, int] = {}
    for key, value in values.items():
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValidationFailed("Action plan read-set version is invalid", details={"key": key})
        result[str(key)] = value
    return result
