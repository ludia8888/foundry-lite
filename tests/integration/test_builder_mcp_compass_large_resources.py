"""Real Builder JSON-RPC discovery with large durable Workshop definitions."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from foundry_lite.application.services.aip.fde_tool_result import hash_json
from foundry_lite_api import runtime as api_runtime
from foundry_lite_api.main import app

from tests.integration.test_ai_fde_platform import (
    FDE_USER,
    _builder_mcp_application,
    _builder_session_headers,
    _mcp_tool_call_payload,
)


@pytest.mark.parametrize(
    "tool_id",
    ["resource.search", "resource.inspect", "governance.project.inspect", "list_resources_in_foundry_folder"],
)
def test_builder_compass_returns_large_workshop_identity_without_exceeding_output_budget(
    foundry: Any, monkeypatch: Any, tool_id: str
) -> None:
    metadata = {"definition": {"pages": ["환자 업무 화면" * 10000]}, "reactFiles": {"App.tsx": "x" * 70000}}
    project = foundry.resources.create_project(
        display_name="Large Workshop", idempotency_key="large-workshop-project", ctx=FDE_USER
    )["project"]
    folder = foundry.resources.create_folder(
        project["id"], display_name="Applications", idempotency_key="large-workshop-folder", ctx=FDE_USER
    )["folder"]
    resource = foundry.resources.register_resource(
        resource_type="workshop_app",
        display_name="Large Workshop",
        project_id=project["id"],
        folder_id=folder["id"],
        source_surface="workshop",
        source_ref="hospital-application",
        metadata=metadata,
        idempotency_key="large-workshop-resource",
        ctx=FDE_USER,
    )["resource"]
    app_id, headers = _builder_mcp_application(foundry, monkeypatch, "governance")
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    session_headers = _builder_session_headers(client, app_id, headers)
    arguments = {
        "resource.search": {"query": "Large Workshop"},
        "resource.inspect": {"rid": resource["rid"]},
        "governance.project.inspect": {"projectId": project["id"]},
        "list_resources_in_foundry_folder": {"projectId": project["id"], "folderId": folder["id"]},
    }[tool_id]
    response = client.post(
        f"/mcp/builder/{app_id}",
        headers=session_headers,
        json=_mcp_tool_call_payload(
            "large-workshop-read", "governance", f"project:{project['id']}", tool_id, arguments
        ),
    )
    assert response.status_code == 200
    result = response.json()["result"]
    assert result.get("isError") is not True, result
    output = result["structuredContent"]
    row = output["resource"] if "resource" in output else output.get("resources", output.get("items"))[0]
    assert row["rid"] == resource["rid"]
    assert row["metadataSummary"]["isContentIncluded"] is False
    assert row["metadataSummary"]["fingerprint"] == hash_json(metadata)
    assert len(json.dumps(output).encode()) < 65536
    assert foundry.resources.get_resource(resource["rid"], ctx=FDE_USER)["resource"]["metadata"] == metadata
