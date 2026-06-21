from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.quality import check_frontend_backend_surface as gate


def test_frontend_backend_surface_gate_passes_current_repo() -> None:
    assert gate.collect_findings() == []


def test_frontend_backend_surface_requires_every_api_route_classified(tmp_path: Path) -> None:
    _write_surface_tree(tmp_path)
    matrix_path = tmp_path / "docs" / "frontend-api-sdk-surface-matrix.json"
    matrix = _load_json(matrix_path)
    matrix["surfaces"] = []
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

    findings = _collect(tmp_path)

    assert any(finding.code == "api_route_missing_frontend_surface_classification" for finding in findings)


def test_frontend_backend_surface_requires_named_sdk_method(tmp_path: Path) -> None:
    _write_surface_tree(tmp_path)
    matrix_path = tmp_path / "docs" / "frontend-api-sdk-surface-matrix.json"
    matrix = _load_json(matrix_path)
    matrix["surfaces"][0]["sdkSurface"] = "datasets.missing"
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

    findings = _collect(tmp_path)

    assert any(finding.code == "frontend_surface_missing_generated_sdk_method" for finding in findings)


def test_frontend_backend_surface_rejects_raw_web_api_request(tmp_path: Path) -> None:
    _write_surface_tree(tmp_path)
    web_path = tmp_path / "apps" / "web" / "index.html"
    web_path.write_text("sdkClient().request('/api/object-sets')\n", encoding="utf-8")

    findings = _collect(tmp_path)

    assert any(finding.code == "web_uses_raw_api_request" for finding in findings)


def test_frontend_backend_surface_requires_collectable_proof_test(tmp_path: Path) -> None:
    _write_surface_tree(tmp_path)
    matrix_path = tmp_path / "docs" / "frontend-api-sdk-surface-matrix.json"
    matrix = _load_json(matrix_path)
    matrix["surfaces"][0]["proofTests"] = ["test_missing_surface_proof"]
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

    findings = _collect(tmp_path)

    assert any(finding.code == "frontend_surface_proof_test_not_collectable" for finding in findings)


def test_frontend_backend_surface_requires_sdk_request_proof_class(tmp_path: Path) -> None:
    _write_surface_tree(tmp_path)
    matrix_path = tmp_path / "docs" / "frontend-api-sdk-surface-matrix.json"
    matrix = _load_json(matrix_path)
    matrix["surfaces"][0].pop("proofClass")
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

    findings = _collect(tmp_path)

    assert any(finding.code == "frontend_surface_missing_sdk_request_proof_class" for finding in findings)


def test_frontend_backend_surface_requires_sdk_request_contract_test(tmp_path: Path) -> None:
    _write_surface_tree(tmp_path)
    matrix_path = tmp_path / "docs" / "frontend-api-sdk-surface-matrix.json"
    matrix = _load_json(matrix_path)
    matrix["surfaces"][0]["proofTests"] = ["test_surface_proof"]
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

    findings = _collect(tmp_path)

    assert any(finding.code == "frontend_surface_missing_sdk_request_contract_test" for finding in findings)


def test_frontend_backend_surface_requires_idempotency_marker_for_header_route(tmp_path: Path) -> None:
    _write_surface_tree(tmp_path)
    _write_idempotency_route(tmp_path)
    matrix_path = tmp_path / "docs" / "frontend-api-sdk-surface-matrix.json"
    matrix = _load_json(matrix_path)
    matrix["surfaces"][0]["route"] = "POST /api/datasets/{namespace}/{name}/preview"
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

    findings = _collect(tmp_path)

    assert any(finding.code == "frontend_surface_missing_idempotency_requirement" for finding in findings)


def test_frontend_backend_surface_rejects_stale_idempotency_marker(tmp_path: Path) -> None:
    _write_surface_tree(tmp_path)
    matrix_path = tmp_path / "docs" / "frontend-api-sdk-surface-matrix.json"
    matrix = _load_json(matrix_path)
    matrix["surfaces"][0]["requiresIdempotencyKey"] = True
    matrix["surfaces"][0]["operatorEvidence"] = "Requires Idempotency-Key."
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

    findings = _collect(tmp_path)

    assert any(finding.code == "frontend_surface_stale_idempotency_requirement" for finding in findings)


def test_frontend_backend_surface_requires_idempotency_operator_evidence(tmp_path: Path) -> None:
    _write_surface_tree(tmp_path)
    _write_idempotency_route(tmp_path)
    matrix_path = tmp_path / "docs" / "frontend-api-sdk-surface-matrix.json"
    matrix = _load_json(matrix_path)
    matrix["surfaces"][0]["route"] = "POST /api/datasets/{namespace}/{name}/preview"
    matrix["surfaces"][0]["requiresIdempotencyKey"] = True
    matrix["surfaces"][0]["operatorEvidence"] = "request_id only"
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

    findings = _collect(tmp_path)

    assert any(finding.code == "frontend_surface_idempotency_evidence_missing" for finding in findings)


def test_frontend_backend_surface_rejects_stale_documented_surface_count(tmp_path: Path) -> None:
    _write_surface_tree(tmp_path)
    (tmp_path / "README.md").write_text(
        "The browser SDK request contract covers 42 frontend route surface rows.\n",
        encoding="utf-8",
    )

    findings = _collect(tmp_path)

    assert any(finding.code == "frontend_surface_count_claim_mismatch" for finding in findings)


def test_frontend_backend_surface_rejects_stale_documented_helper_count(tmp_path: Path) -> None:
    _write_surface_tree(tmp_path)
    (tmp_path / "README.md").write_text(
        "The frontend foundation exposes 12 SDK helper rows.\n",
        encoding="utf-8",
    )

    findings = _collect(tmp_path)

    assert any(finding.code == "sdk_helper_count_claim_mismatch" for finding in findings)


def test_frontend_backend_surface_requires_helper_matrix_row(tmp_path: Path) -> None:
    _write_surface_tree(tmp_path)
    matrix_path = tmp_path / "docs" / "frontend-api-sdk-surface-matrix.json"
    matrix = _load_json(matrix_path)
    matrix["sdkHelpers"] = []
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

    findings = _collect(tmp_path)

    assert any(finding.code == "sdk_helper_missing_matrix_row" for finding in findings)


def test_frontend_backend_surface_requires_helper_export(tmp_path: Path) -> None:
    _write_surface_tree(tmp_path)
    sdk_path = tmp_path / "packages" / "sdk-ts" / "src" / "generated.ts"
    sdk_path.write_text(
        'export const SDK_CLIENT_SURFACE = {"datasets":["preview"],"helpers":["retryWithBackoff"]};\n',
        encoding="utf-8",
    )

    findings = _collect(tmp_path)

    assert any(finding.code == "sdk_helper_missing_export" for finding in findings)


def test_frontend_backend_surface_requires_helper_contract_test(tmp_path: Path) -> None:
    _write_surface_tree(tmp_path)
    matrix_path = tmp_path / "docs" / "frontend-api-sdk-surface-matrix.json"
    matrix = _load_json(matrix_path)
    matrix["sdkHelpers"][0]["proofTests"] = ["test_surface_proof"]
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

    findings = _collect(tmp_path)

    assert any(finding.code == "sdk_helper_missing_request_contract_test" for finding in findings)


def _collect(root: Path) -> list[gate.FrontendBackendSurfaceFinding]:
    return gate.collect_findings(
        root,
        api_path=root / "apps" / "api" / "foundry_lite_api" / "main.py",
        matrix_path=root / "docs" / "frontend-api-sdk-surface-matrix.json",
        sdk_path=root / "packages" / "sdk-ts" / "src" / "generated.ts",
        web_path=root / "apps" / "web" / "index.html",
        tests_root=root / "tests",
    )


def _write_surface_tree(root: Path) -> None:
    (root / "apps" / "api" / "foundry_lite_api").mkdir(parents=True)
    (root / "apps" / "web").mkdir(parents=True)
    (root / "docs").mkdir(parents=True)
    (root / "packages" / "sdk-ts" / "src").mkdir(parents=True)
    (root / "tests" / "unit").mkdir(parents=True)
    (root / "apps" / "api" / "foundry_lite_api" / "main.py").write_text(
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        "@app.get('/api/datasets/{namespace}/{name}/preview')\n"
        "def preview_dataset():\n"
        "    return []\n",
        encoding="utf-8",
    )
    (root / "packages" / "sdk-ts" / "src" / "generated.ts").write_text(
        'export const SDK_CLIENT_SURFACE = {"datasets":["preview"],"helpers":["retryWithBackoff"]};\n'
        "export async function retryWithBackoff() {}\n",
        encoding="utf-8",
    )
    (root / "apps" / "web" / "index.html").write_text(
        "const client = sdkClient(); client.datasets.preview('clean', 'orders');\n",
        encoding="utf-8",
    )
    (root / "tests" / "unit" / "test_surface_proof.py").write_text(
        "def test_sdk_request_contract_covers_all_frontend_surface_routes():\n    assert True\n"
        "def test_sdk_request_contract_covers_frontend_foundation_helpers():\n    assert True\n"
        "def test_surface_proof():\n    assert True\n",
        encoding="utf-8",
    )
    (root / "docs" / "frontend-api-sdk-surface-matrix.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "surfaces": [
                    {
                        "route": "GET /api/datasets/{namespace}/{name}/preview",
                        "sdkSurface": "datasets.preview",
                        "rawRequestPolicy": "named-sdk-only",
                        "operatorEvidence": "request_id",
                        "proofClass": "sdk-request-contract",
                        "proofTests": ["test_sdk_request_contract_covers_all_frontend_surface_routes"],
                    }
                ],
                "sdkHelpers": [
                    {
                        "id": "helpers.retryWithBackoff",
                        "sdkHelper": "retryWithBackoff",
                        "operatorEvidence": "retryable errors keep request_id metadata.",
                        "proofClass": "sdk-helper-contract",
                        "proofTests": ["test_sdk_request_contract_covers_frontend_foundation_helpers"],
                    }
                ],
                "nonFrontendRoutes": [],
            }
        ),
        encoding="utf-8",
    )


def _write_idempotency_route(root: Path) -> None:
    (root / "apps" / "api" / "foundry_lite_api" / "main.py").write_text(
        "from fastapi import FastAPI, Header\n"
        "app = FastAPI()\n"
        "@app.post('/api/datasets/{namespace}/{name}/preview')\n"
        "def preview_dataset(idempotency_key: str = Header(alias='Idempotency-Key')):\n"
        "    return []\n",
        encoding="utf-8",
    )


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload
