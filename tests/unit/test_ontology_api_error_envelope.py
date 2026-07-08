"""HTTP error envelope for ontology validate: YAML parse failures and unhandled 5xx CORS."""

from __future__ import annotations

from fastapi.testclient import TestClient
from foundry_lite_api import runtime as api_runtime
from foundry_lite_api.main import app
from pytest import MonkeyPatch

BROWSER_ORIGIN = "http://127.0.0.1:4173"

ADMIN_HEADERS = {
    "X-Tenant-ID": "tenant-demo",
    "X-User-ID": "user-demo-admin",
    "X-Roles": "admin,data_engineer,ops_manager",
    "Origin": BROWSER_ORIGIN,
}

BROKEN_YAML = "objectTypes:\n  - apiName: broken\n    properties:\n   - bad indent\n  [unclosed"


def test_api_ontology_validate_maps_yaml_parse_failure_to_400_with_location(foundry, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(api_runtime, "foundry", foundry)

    response = TestClient(app).post(
        "/api/ontology/validate",
        headers=ADMIN_HEADERS,
        json={"yaml": BROKEN_YAML},
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "VALIDATION_FAILED"
    assert detail["message"] == "ontology yaml parse failed"
    assert isinstance(detail["details"].get("line"), int)
    assert isinstance(detail["details"].get("column"), int)
    assert detail["details"].get("problem")
    # Browser callers must be able to read the failure (not a CORS-blocked network error).
    assert response.headers.get("access-control-allow-origin") == BROWSER_ORIGIN


def test_api_ontology_apply_maps_yaml_parse_failure_to_400(foundry, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(api_runtime, "foundry", foundry)

    response = TestClient(app).post(
        "/api/ontology/apply",
        headers=ADMIN_HEADERS,
        json={"yamlText": BROKEN_YAML},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "VALIDATION_FAILED"
    assert response.json()["detail"]["message"] == "ontology yaml parse failed"


class _ExplodingOntology:
    def validate(self, *_args: object, **_kwargs: object) -> None:
        raise RuntimeError("boom")


class _ExplodingFoundry:
    ontology = _ExplodingOntology()


def test_api_unhandled_exception_returns_json_envelope_with_cors_headers(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(api_runtime, "foundry", _ExplodingFoundry())

    response = TestClient(app, raise_server_exceptions=False).post(
        "/api/ontology/validate",
        headers=ADMIN_HEADERS,
        json={"yaml": "objectTypes: []"},
    )

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail["code"] == "INTERNAL"
    assert detail["request_id"]
    assert response.headers.get("access-control-allow-origin") == BROWSER_ORIGIN
    assert response.headers.get("x-request-id") == detail["request_id"]


def test_api_unhandled_exception_omits_cors_headers_for_unknown_origin(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(api_runtime, "foundry", _ExplodingFoundry())

    response = TestClient(app, raise_server_exceptions=False).post(
        "/api/ontology/validate",
        headers={**ADMIN_HEADERS, "Origin": "http://evil.example"},
        json={"yaml": "objectTypes: []"},
    )

    assert response.status_code == 500
    assert response.headers.get("access-control-allow-origin") is None
