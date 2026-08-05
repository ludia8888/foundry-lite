"""Stable public payload and cursor helpers for the Action catalog."""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping

from foundry_lite.application.action_types import ActionCatalogItem
from foundry_lite.application.ports import ActionTypeRow
from foundry_lite.domain.action_runtime.action_contract import (
    action_contract_fingerprint,
    action_contract_payload,
    action_parameter_json_schema,
    compile_action_contract,
)
from foundry_lite.domain.action_runtime.action_permissions import can_access_action
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed


def action_catalog_item(
    row: ActionTypeRow,
    ontology_version_id: str,
    ctx: RequestContext,
) -> ActionCatalogItem:
    contract = compile_action_contract(row["definition"])
    return {
        "apiName": contract.api_name,
        "displayName": contract.display_name,
        "description": contract.description,
        "target": {"kind": contract.target.kind, "apiName": contract.target.api_name},
        "ontologyVersionId": ontology_version_id,
        "contractVersion": 3,
        "contractFingerprint": action_contract_fingerprint(contract),
        "parameterSchema": action_parameter_json_schema(contract),
        "contract": action_contract_payload(contract),
        "riskLevel": contract.risk_level,
        "agentExecutionPolicy": contract.agent_execution_policy,
        "access": {
            "canView": can_access_action(ctx, contract.permissions, "view"),
            "canEdit": can_access_action(ctx, contract.permissions, "edit"),
            "canApply": can_access_action(ctx, contract.permissions, "apply"),
        },
        "enabled": bool(row["enabled"]),
    }


def encode_action_catalog_cursor(
    ontology_version_id: str,
    application_id: str | None,
    after_api_name: str,
) -> str:
    payload = {"ontologyVersionId": ontology_version_id, "applicationId": application_id, "after": after_api_name}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_action_catalog_cursor(
    cursor: str | None,
    ontology_version_id: str,
    application_id: str | None,
) -> str | None:
    if cursor is None:
        return None
    payload = _cursor_payload(cursor)
    if payload.get("ontologyVersionId") != ontology_version_id or payload.get("applicationId") != application_id:
        raise ValidationFailed("action catalog cursor does not match the current catalog")
    after = payload.get("after")
    if not isinstance(after, str) or not after:
        raise ValidationFailed("invalid action catalog cursor")
    return after


def _cursor_payload(cursor: str) -> Mapping[str, object]:
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        payload = json.loads(decoded)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationFailed("invalid action catalog cursor") from exc
    if not isinstance(payload, Mapping):
        raise ValidationFailed("invalid action catalog cursor")
    return payload
