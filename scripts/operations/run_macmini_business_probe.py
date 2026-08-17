"""Bootstrap and periodically verify the Mac mini product closed loop over HTTP."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, cast

from scripts.operations.macmini_qa_guard import QA_ROOT, assert_host_boundary

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
OBJECT_TYPE = "EnterpriseQaProbeOrder"
ACTION_TYPE = "AcknowledgeEnterpriseQaProbeOrder"
OBJECT_ID = "enterprise-qa-order-1"
DATASET_REF = "qa.enterprise_probe_orders"
ACTION_IDEMPOTENCY_KEY = "macmini-enterprise-qa-action-v1"
ONTOLOGY_YAML = """objectTypes:
  - apiName: EnterpriseQaProbeOrder
    displayName: Enterprise QA probe order
    primaryKey: probeOrderId
    titleProperty: probeOrderId
    backing:
      dataset: qa.enterprise_probe_orders
      mode: snapshot
      primaryKeyColumns: [probe_order_id]
    properties:
      - apiName: probeOrderId
        column: probe_order_id
        type: string
        indexed: true
        nullable: false
      - apiName: status
        column: status
        type: string
        indexed: true
        editable: true
        editPolicy: edit_wins
      - apiName: operatorNote
        column: operator_note
        type: string
        editable: true
        editPolicy: edit_wins
actionTypes:
  - apiName: AcknowledgeEnterpriseQaProbeOrder
    displayName: Acknowledge enterprise QA probe order
    target: EnterpriseQaProbeOrder
    parameters:
      - apiName: reason
        type: string
        required: true
    permissions:
      allowedRoles: [ops_manager]
    preconditions:
      - safeExpression: "object.status == 'NEW'"
        message: "Only a new probe order can be acknowledged"
    mutations:
      - type: setProperty
        property: status
        value: ACKNOWLEDGED
      - type: setProperty
        property: operatorNote
        valueFrom: params.reason
"""


class BusinessProbeError(RuntimeError):
    """Stable, secret-free operational failure."""


@dataclass(frozen=True, slots=True)
class ApiResponse:
    status_code: int
    payload: object


class ApiClient:
    def __init__(
        self,
        base_url: str,
        *,
        tenant_id: str,
        actor_user_id: str,
        roles: str,
        bearer_token: str | None,
        timeout_seconds: float,
    ) -> None:
        self.base_url = _validated_base_url(base_url)
        self.timeout_seconds = timeout_seconds
        self.headers = {
            "accept": "application/json",
            "user-agent": "Foundry-lite-enterprise-business-probe/1",
        }
        if bearer_token is None:
            self.headers.update({"X-Tenant-ID": tenant_id, "X-User-ID": actor_user_id, "X-Roles": roles})
        else:
            self.headers["authorization"] = f"Bearer {bearer_token}"

    def json_request(
        self,
        method: str,
        path: str,
        *,
        payload: object | None = None,
        headers: Mapping[str, str] | None = None,
        acceptable_statuses: tuple[int, ...] = (200,),
    ) -> ApiResponse:
        body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        request_headers = dict(self.headers)
        if body is not None:
            request_headers["content-type"] = "application/json"
        request_headers.update(headers or {})
        return self._open(method, path, body, request_headers, acceptable_statuses)

    def multipart_request(
        self,
        path: str,
        *,
        body: bytes,
        boundary: str,
        headers: Mapping[str, str],
    ) -> ApiResponse:
        request_headers = dict(self.headers)
        request_headers.update(headers)
        request_headers["content-type"] = f"multipart/form-data; boundary={boundary}"
        return self._open("POST", path, body, request_headers, (200,))

    def _open(
        self,
        method: str,
        path: str,
        body: bytes | None,
        headers: Mapping[str, str],
        acceptable_statuses: tuple[int, ...],
    ) -> ApiResponse:
        request = urllib.request.Request(self.base_url + path, data=body, headers=dict(headers), method=method)
        try:
            with urllib.request.build_opener(_NoRedirect()).open(request, timeout=self.timeout_seconds) as response:
                raw = _bounded_read(response)
                status_code = response.status
        except urllib.error.HTTPError as exc:
            _bounded_read(cast(BinaryIO, exc))
            status_code = exc.code
            raw = b""
        except (urllib.error.URLError, TimeoutError, OSError, RuntimeError) as exc:
            raise BusinessProbeError("macmini_business_probe_http_unavailable") from exc
        if status_code not in acceptable_statuses:
            raise BusinessProbeError(f"macmini_business_probe_http_{status_code}")
        try:
            payload = json.loads(raw) if raw else None
        except json.JSONDecodeError as exc:
            raise BusinessProbeError("macmini_business_probe_invalid_json") from exc
        return ApiResponse(status_code, payload)


def bootstrap_probe(client: ApiClient, config_path: Path) -> dict[str, object]:
    _require_new_config_path(config_path)
    _upload_probe_dataset(client)
    _ensure_probe_ontology(client)
    index = _mapping(client.json_request("POST", f"/api/operations/index/{OBJECT_TYPE}/replay").payload)
    current = _get_probe_object(client)
    config = _probe_config(client.base_url, current)
    _write_private_json(config_path, config)
    receipt = run_probe(client, config)
    return {
        **receipt,
        "bootstrap": "passed",
        "objectsUpserted": index.get("objects_upserted"),
        "configSha256": _json_sha256(config),
    }


def run_probe(client: ApiClient, config: Mapping[str, object]) -> dict[str, object]:
    config = _validated_config(config)
    action = _mapping(config.get("action"))
    target = _mapping(action.get("target"))
    action_path = f"/api/actions/{urllib.parse.quote(str(action['apiName']), safe='')}/apply"
    action_body = {
        "target": dict(target),
        "expectedObjectVersion": action["expectedObjectVersion"],
        "params": action["params"],
    }
    action_headers = {"Idempotency-Key": str(action["idempotencyKey"])}
    expected_version = action.get("expectedObjectVersion")
    if not isinstance(expected_version, int) or isinstance(expected_version, bool):
        raise BusinessProbeError("macmini_business_probe_object_version_invalid")
    first = _mapping(client.json_request("POST", action_path, payload=action_body, headers=action_headers).payload)
    replay = _mapping(client.json_request("POST", action_path, payload=action_body, headers=action_headers).payload)
    action_run_id = _require_action_replay(first, replay)
    _verify_action_run(client, action_run_id, target)
    materialization = _run_and_verify_materialization(client, config, action_run_id)
    current = _get_probe_object(client)
    query_matched = _verify_object_query(client, target)
    _require_acknowledged_object(current, expected_version)
    return _probe_receipt(action_run_id, replay, materialization, current, query_matched)


def _upload_probe_dataset(client: ApiClient) -> None:
    boundary = "foundry-lite-enterprise-qa-boundary-v1"
    fields = {
        "sourceName": "enterprise_qa_probe_orders",
        "displayName": "Enterprise QA probe orders",
        "datasetRef": DATASET_REF,
        "syncName": "enterprise-qa-probe-seed-v1",
        "primaryKey": '["probe_order_id"]',
    }
    csv_bytes = b"probe_order_id,status,operator_note\nenterprise-qa-order-1,NEW,\n"
    body = _multipart_body(boundary, fields, "probe-orders.csv", csv_bytes)
    client.multipart_request(
        "/api/sources/csv/uploads",
        body=body,
        boundary=boundary,
        headers={"Idempotency-Key": "macmini-enterprise-qa-source-v1"},
    )


def _ensure_probe_ontology(client: ApiClient) -> None:
    catalog = client.json_request("GET", "/api/ontology/catalog", acceptable_statuses=(200, 404))
    if catalog.status_code == 200 and _catalog_has_probe_type(catalog.payload):
        return
    client.json_request("POST", "/api/ontology/apply", payload={"yamlText": ONTOLOGY_YAML})


def _catalog_has_probe_type(payload: object) -> bool:
    catalog = _mapping(payload)
    object_types = catalog.get("objectTypes")
    return isinstance(object_types, list) and any(
        isinstance(item, dict) and item.get("apiName") == OBJECT_TYPE for item in object_types
    )


def _probe_config(base_url: str, current: Mapping[str, object]) -> dict[str, object]:
    version = current.get("objectVersion")
    if not isinstance(version, int) or isinstance(version, bool):
        raise BusinessProbeError("macmini_business_probe_object_version_invalid")
    return {
        "schemaVersion": 1,
        "baseUrl": base_url,
        "action": {
            "apiName": ACTION_TYPE,
            "idempotencyKey": ACTION_IDEMPOTENCY_KEY,
            "target": {"objectType": OBJECT_TYPE, "objectId": OBJECT_ID},
            "expectedObjectVersion": version,
            "params": {"reason": "24-hour enterprise QA continuity probe"},
        },
        "materialization": {"apiName": "action_log", "datasetRef": "ops.action_log"},
    }


def _get_probe_object(client: ApiClient) -> dict[str, object]:
    object_type = urllib.parse.quote(OBJECT_TYPE, safe="")
    object_id = urllib.parse.quote(OBJECT_ID, safe="")
    return _mapping(client.json_request("GET", f"/api/objects/{object_type}/{object_id}").payload)


def _require_action_replay(first: Mapping[str, object], replay: Mapping[str, object]) -> str:
    first_run = first.get("actionRunId")
    replay_run = replay.get("actionRunId")
    if (
        not isinstance(first_run, str)
        or first_run != replay_run
        or first.get("status") != "succeeded"
        or replay.get("status") != "succeeded"
        or replay.get("idempotentReplay") is not True
    ):
        raise BusinessProbeError("macmini_business_probe_action_replay_mismatch")
    return first_run


def _verify_action_run(client: ApiClient, action_run_id: str, target: Mapping[str, object]) -> None:
    encoded = urllib.parse.quote(action_run_id, safe="")
    run = _mapping(client.json_request("GET", f"/api/actions/runs/{encoded}").payload)
    if run.get("status") != "succeeded" or run.get("target_object_id") != target.get("objectId"):
        raise BusinessProbeError("macmini_business_probe_action_run_invalid")


def _run_and_verify_materialization(
    client: ApiClient, config: Mapping[str, object], action_run_id: str
) -> dict[str, object]:
    materialization = _mapping(config.get("materialization"))
    api_name = urllib.parse.quote(str(materialization["apiName"]), safe="")
    result = _mapping(client.json_request("POST", f"/api/materializations/{api_name}/run").payload)
    if result.get("dataset_ref") != materialization.get("datasetRef") or not _positive_int(result.get("row_count")):
        raise BusinessProbeError("macmini_business_probe_materialization_invalid")
    preview = client.json_request("GET", "/api/datasets/ops/action_log/preview").payload
    if not isinstance(preview, list) or not any(
        isinstance(row, dict) and row.get("action_run_id") == action_run_id for row in preview
    ):
        raise BusinessProbeError("macmini_business_probe_materialization_row_missing")
    return result


def _verify_object_query(client: ApiClient, target: Mapping[str, object]) -> bool:
    object_type = urllib.parse.quote(str(target["objectType"]), safe="")
    payload = {
        "filter": {"property": "probeOrderId", "op": "eq", "value": target["objectId"]},
        "limit": 2,
    }
    result = _mapping(client.json_request("POST", f"/api/objects/{object_type}/query", payload=payload).payload)
    items = result.get("items")
    if not isinstance(items, list):
        return False
    return any(isinstance(item, dict) and item.get("objectId") == target["objectId"] for item in items)


def _require_acknowledged_object(current: Mapping[str, object], initial_version: int) -> None:
    properties = _mapping(current.get("properties"))
    version = current.get("objectVersion")
    if properties.get("status") != "ACKNOWLEDGED" or not isinstance(version, int) or version <= initial_version:
        raise BusinessProbeError("macmini_business_probe_object_state_invalid")


def _probe_receipt(
    action_run_id: str,
    replay: Mapping[str, object],
    materialization: Mapping[str, object],
    current: Mapping[str, object],
    is_query_matched: bool,
) -> dict[str, object]:
    if not is_query_matched:
        raise BusinessProbeError("macmini_business_probe_object_query_missing")
    return {
        "schemaVersion": 1,
        "status": "passed",
        "observedAt": datetime.now(UTC).isoformat(),
        "actionRunId": action_run_id,
        "idempotentReplay": replay.get("idempotentReplay") is True,
        "materializationVersionId": materialization.get("version_id"),
        "materializationRowCount": materialization.get("row_count"),
        "objectVersion": current.get("objectVersion"),
        "datasetPreviewMatched": True,
        "objectQueryMatched": True,
    }


def _multipart_body(boundary: str, fields: Mapping[str, str], file_name: str, content: bytes) -> bytes:
    body = bytearray()
    for name, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'.encode())
    body.extend(b"Content-Type: text/csv\r\n\r\n")
    body.extend(content)
    body.extend(f"\r\n--{boundary}--\r\n".encode())
    return bytes(body)


def _validated_base_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    is_loopback_http = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or (parsed.scheme != "https" and not is_loopback_http)
    ):
        raise ValueError("macmini_business_probe_base_url_invalid")
    return value.rstrip("/")


def _bounded_read(stream: BinaryIO) -> bytes:
    payload = stream.read(MAX_RESPONSE_BYTES + 1)
    if len(payload) > MAX_RESPONSE_BYTES:
        raise BusinessProbeError("macmini_business_probe_response_too_large")
    return payload


def _read_token(path: str | None) -> str | None:
    if path is None:
        return None
    token_path = Path(path)
    if not token_path.is_file() or token_path.stat().st_mode & 0o077:
        raise ValueError("macmini_business_probe_bearer_token_permissions_invalid")
    token = token_path.read_text(encoding="utf-8").strip()
    if not token:
        raise ValueError("macmini_business_probe_bearer_token_missing")
    return token


def _read_private_config(path: Path) -> dict[str, object]:
    _require_qa_path(path)
    if not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError("macmini_business_probe_config_permissions_invalid")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return _validated_config(_mapping(payload))


def _validated_config(config: Mapping[str, object]) -> dict[str, object]:
    base_url = config.get("baseUrl")
    action = _mapping(config.get("action"))
    target = _mapping(action.get("target"))
    materialization = _mapping(config.get("materialization"))
    version = action.get("expectedObjectVersion")
    expected = (
        config.get("schemaVersion") == 1,
        isinstance(base_url, str),
        action.get("apiName") == ACTION_TYPE,
        action.get("idempotencyKey") == ACTION_IDEMPOTENCY_KEY,
        target == {"objectType": OBJECT_TYPE, "objectId": OBJECT_ID},
        isinstance(version, int) and not isinstance(version, bool) and version > 0,
        action.get("params") == {"reason": "24-hour enterprise QA continuity probe"},
        materialization == {"apiName": "action_log", "datasetRef": "ops.action_log"},
    )
    if not all(expected):
        raise ValueError("macmini_business_probe_config_invalid")
    _validated_base_url(str(base_url))
    return dict(config)


def _write_private_json(path: Path, payload: object) -> None:
    _require_qa_path(path)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ValueError("macmini_business_probe_config_already_exists") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")


def _require_new_config_path(path: Path) -> None:
    _require_qa_path(path)
    if path.exists():
        raise ValueError("macmini_business_probe_config_already_exists")


def _require_qa_path(path: Path) -> None:
    resolved_parent = path.parent.resolve()
    if resolved_parent != QA_ROOT and QA_ROOT not in resolved_parent.parents:
        raise ValueError("macmini_business_probe_config_outside_qa_root")


def _json_sha256(payload: object) -> str:
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise BusinessProbeError("macmini_business_probe_response_shape_invalid")
    return value


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request: object, fp: object, code: int, msg: str, headers: object, url: str) -> None:
        raise RuntimeError("macmini_business_probe_redirect_not_allowed")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("bootstrap", "probe"))
    parser.add_argument("--base-url")
    parser.add_argument("--config", default=str(QA_ROOT / "state" / "business-probe.json"))
    parser.add_argument("--tenant-id", default="tenant-demo")
    parser.add_argument("--actor-user-id", default="enterprise-qa-operator")
    parser.add_argument("--roles", default="admin,data_engineer,ops_manager")
    parser.add_argument("--bearer-token-file")
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    return parser


def _run_from_args(args: argparse.Namespace) -> dict[str, object]:
    if not math.isfinite(args.timeout_seconds) or not 0.1 <= args.timeout_seconds <= 60.0:
        raise ValueError("macmini_business_probe_timeout_invalid")
    token = _read_token(args.bearer_token_file)
    config_path = Path(args.config)
    if args.mode == "bootstrap":
        if not args.base_url:
            raise ValueError("macmini_business_probe_base_url_required")
        client = ApiClient(
            args.base_url,
            tenant_id=args.tenant_id,
            actor_user_id=args.actor_user_id,
            roles=args.roles,
            bearer_token=token,
            timeout_seconds=args.timeout_seconds,
        )
        return bootstrap_probe(client, config_path)
    config = _read_private_config(config_path)
    client = ApiClient(
        str(config["baseUrl"]),
        tenant_id=args.tenant_id,
        actor_user_id=args.actor_user_id,
        roles=args.roles,
        bearer_token=token,
        timeout_seconds=args.timeout_seconds,
    )
    return run_probe(client, config)


def main() -> int:
    try:
        assert_host_boundary()
        result = _run_from_args(_parser().parse_args())
    except (BusinessProbeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
