from __future__ import annotations

import json
import stat
from collections.abc import Mapping
from pathlib import Path

import pytest

from scripts.operations import run_macmini_business_probe as subject
from scripts.operations.run_macmini_business_probe import (
    ACTION_TYPE,
    OBJECT_ID,
    OBJECT_TYPE,
    ApiClient,
    ApiResponse,
    BusinessProbeError,
    _read_token,
    _validated_base_url,
    bootstrap_probe,
    run_probe,
)


class FakeClient(ApiClient):
    base_url = "http://127.0.0.1:30443"

    def __init__(self, responses: Mapping[tuple[str, str], list[ApiResponse]]) -> None:
        self.responses = {key: list(value) for key, value in responses.items()}
        self.calls: list[tuple[str, str, object | None, Mapping[str, str] | None]] = []

    def json_request(
        self,
        method: str,
        path: str,
        *,
        payload: object | None = None,
        headers: Mapping[str, str] | None = None,
        acceptable_statuses: tuple[int, ...] = (200,),
    ) -> ApiResponse:
        del acceptable_statuses
        self.calls.append((method, path, payload, headers))
        return self.responses[(method, path)].pop(0)

    def multipart_request(
        self,
        path: str,
        *,
        body: bytes,
        boundary: str,
        headers: Mapping[str, str],
    ) -> ApiResponse:
        assert boundary.encode() in body
        self.calls.append(("POST", path, body, headers))
        return self.responses[("POST", path)].pop(0)


def test_base_url_allows_https_or_loopback_http_only() -> None:
    assert _validated_base_url("https://qa.example") == "https://qa.example"
    assert _validated_base_url("http://127.0.0.1:30443/") == "http://127.0.0.1:30443"

    with pytest.raises(ValueError, match="base_url_invalid"):
        _validated_base_url("http://qa.example")
    with pytest.raises(ValueError, match="base_url_invalid"):
        _validated_base_url("https://user:secret@qa.example")


def test_bearer_token_requires_private_file(tmp_path: Path) -> None:
    token = tmp_path / "token"
    token.write_text("very-secret-token\n", encoding="utf-8")
    token.chmod(0o644)
    with pytest.raises(ValueError, match="permissions_invalid"):
        _read_token(str(token))

    token.chmod(0o600)
    assert _read_token(str(token)) == "very-secret-token"


def test_probe_requires_same_successful_action_run_and_materialized_row() -> None:
    client = FakeClient(_probe_responses())

    receipt = run_probe(client, _config())

    assert receipt["status"] == "passed"
    assert receipt["actionRunId"] == "action-run-1"
    assert receipt["idempotentReplay"] is True
    assert receipt["materializationVersionId"] == "version-action-log-1"
    assert receipt["datasetPreviewMatched"] is True
    assert receipt["objectQueryMatched"] is True
    action_calls = [call for call in client.calls if call[1].endswith("/apply")]
    assert len(action_calls) == 2
    assert action_calls[0][2] == action_calls[1][2]
    assert action_calls[0][3] == action_calls[1][3]


def test_probe_fails_when_second_action_is_not_an_exact_replay() -> None:
    responses = _probe_responses()
    responses[("POST", f"/api/actions/{ACTION_TYPE}/apply")][1] = ApiResponse(
        200, {"status": "succeeded", "actionRunId": "action-run-2", "idempotentReplay": True}
    )

    with pytest.raises(BusinessProbeError, match="action_replay_mismatch"):
        run_probe(FakeClient(responses), _config())


def test_probe_fails_when_materialization_does_not_contain_action_run() -> None:
    responses = _probe_responses()
    responses[("GET", "/api/datasets/ops/action_log/preview")] = [
        ApiResponse(200, [{"action_run_id": "different-run"}])
    ]

    with pytest.raises(BusinessProbeError, match="materialization_row_missing"):
        run_probe(FakeClient(responses), _config())


def test_probe_rejects_tampered_action_config_before_http_calls() -> None:
    config = _config()
    action = config["action"]
    assert isinstance(action, dict)
    config["action"] = {**action, "apiName": "UnreviewedAction"}
    client = FakeClient({})

    with pytest.raises(ValueError, match="config_invalid"):
        run_probe(client, config)

    assert client.calls == []


def test_bootstrap_uses_api_closed_loop_and_writes_private_pinned_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qa_root = tmp_path / "foundry-qa"
    state = qa_root / "state"
    state.mkdir(parents=True)
    monkeypatch.setattr(subject, "QA_ROOT", qa_root)
    config_path = state / "business-probe.json"
    responses = _probe_responses()
    responses.update(
        {
            ("POST", "/api/sources/csv/uploads"): [ApiResponse(200, {"commitResult": {"rowCount": 1}})],
            ("GET", "/api/ontology/catalog"): [ApiResponse(404, None)],
            ("POST", "/api/ontology/apply"): [ApiResponse(200, {"versionNumber": 1})],
            ("POST", f"/api/operations/index/{OBJECT_TYPE}/replay"): [ApiResponse(200, {"objects_upserted": 1})],
        }
    )
    responses[("GET", f"/api/objects/{OBJECT_TYPE}/{OBJECT_ID}")].insert(0, ApiResponse(200, _object_payload("NEW", 1)))
    client = FakeClient(responses)

    receipt = bootstrap_probe(client, config_path)

    assert receipt["status"] == "passed"
    assert receipt["bootstrap"] == "passed"
    assert receipt["objectsUpserted"] == 1
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["action"]["expectedObjectVersion"] == 1
    assert config["action"]["idempotencyKey"] == "macmini-enterprise-qa-action-v1"
    assert "token" not in config_path.read_text(encoding="utf-8").casefold()
    assert any(call[:2] == ("POST", "/api/ontology/apply") for call in client.calls)


def test_bootstrap_reuses_existing_probe_ontology() -> None:
    responses = _probe_responses()
    responses.update(
        {
            ("POST", "/api/sources/csv/uploads"): [ApiResponse(200, {"commitResult": {"rowCount": 1}})],
            ("GET", "/api/ontology/catalog"): [ApiResponse(200, {"objectTypes": [{"apiName": OBJECT_TYPE}]})],
            ("POST", f"/api/operations/index/{OBJECT_TYPE}/replay"): [ApiResponse(200, {"objects_upserted": 1})],
        }
    )
    responses[("GET", f"/api/objects/{OBJECT_TYPE}/{OBJECT_ID}")].insert(0, ApiResponse(200, _object_payload("NEW", 1)))
    client = FakeClient(responses)

    assert subject._catalog_has_probe_type({"objectTypes": [{"apiName": OBJECT_TYPE}]}) is True
    subject._ensure_probe_ontology(client)
    assert not any(call[:2] == ("POST", "/api/ontology/apply") for call in client.calls)


def test_bootstrap_refuses_existing_config_before_any_api_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qa_root = tmp_path / "foundry-qa"
    state = qa_root / "state"
    state.mkdir(parents=True)
    monkeypatch.setattr(subject, "QA_ROOT", qa_root)
    config_path = state / "business-probe.json"
    config_path.write_text("{}", encoding="utf-8")
    client = FakeClient({})

    with pytest.raises(ValueError, match="already_exists"):
        bootstrap_probe(client, config_path)

    assert client.calls == []


def _config() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "baseUrl": "http://127.0.0.1:30443",
        "action": {
            "apiName": ACTION_TYPE,
            "idempotencyKey": "macmini-enterprise-qa-action-v1",
            "target": {"objectType": OBJECT_TYPE, "objectId": OBJECT_ID},
            "expectedObjectVersion": 1,
            "params": {"reason": "24-hour enterprise QA continuity probe"},
        },
        "materialization": {"apiName": "action_log", "datasetRef": "ops.action_log"},
    }


def _probe_responses() -> dict[tuple[str, str], list[ApiResponse]]:
    return {
        ("POST", f"/api/actions/{ACTION_TYPE}/apply"): [
            ApiResponse(200, {"status": "succeeded", "actionRunId": "action-run-1"}),
            ApiResponse(
                200,
                {"status": "succeeded", "actionRunId": "action-run-1", "idempotentReplay": True},
            ),
        ],
        ("GET", "/api/actions/runs/action-run-1"): [
            ApiResponse(200, {"status": "succeeded", "target_object_id": OBJECT_ID})
        ],
        ("POST", "/api/materializations/action_log/run"): [
            ApiResponse(
                200,
                {
                    "dataset_ref": "ops.action_log",
                    "version_id": "version-action-log-1",
                    "row_count": 1,
                },
            )
        ],
        ("GET", "/api/datasets/ops/action_log/preview"): [ApiResponse(200, [{"action_run_id": "action-run-1"}])],
        ("GET", f"/api/objects/{OBJECT_TYPE}/{OBJECT_ID}"): [ApiResponse(200, _object_payload("ACKNOWLEDGED", 2))],
        ("POST", f"/api/objects/{OBJECT_TYPE}/query"): [ApiResponse(200, {"items": [{"objectId": OBJECT_ID}]})],
    }


def _object_payload(status: str, version: int) -> dict[str, object]:
    return {
        "objectId": OBJECT_ID,
        "objectVersion": version,
        "properties": {"probeOrderId": OBJECT_ID, "status": status},
    }
