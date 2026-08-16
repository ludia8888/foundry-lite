from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite_api import runtime as api_runtime
from foundry_lite_api.routers import aip, developer_console
from foundry_lite_api.schemas import (
    AipBuilderRunRequest,
    AipBuilderValidateRequest,
    AipFdeRunRequest,
    AipPilotGenerateRequest,
    AipPilotPlanRequest,
    AipPilotPolicyConditionRequest,
    OsdkApplicationClientRequest,
    OsdkApplicationCreateRequest,
    OsdkClientSecretRotateRequest,
    OsdkMcpServerConfigureRequest,
)
from pydantic import ValidationError
from starlette.requests import Request


def _request() -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/test",
        "headers": [
            (b"x-tenant-id", b"tenant-demo"),
            (b"x-user-id", b"api-admin"),
            (b"x-roles", b"admin,data_engineer"),
        ],
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("testclient", 50000),
        "root_path": "",
    }
    request = Request(scope)
    request.state.request_id = "req-api-edge"
    return request


def _builder_payload(*, is_run: bool = False) -> AipBuilderValidateRequest:
    values: dict[str, object] = {
        "agentVersionId": "agent-v1",
        "releaseChannel": "draft",
        "modelAliasVersion": "model-v1",
        "promptVersionId": "prompt-v1",
        "contextSources": [],
        "toolManifest": [],
        "logicBlocks": [],
        "evalAxes": [],
    }
    if is_run:
        values["logicRunId"] = "logic-run-1"
        return AipBuilderRunRequest.model_validate(values)
    return AipBuilderValidateRequest.model_validate(values)


class _RaisingNamespace:
    def __getattr__(self, _name: str):
        def call(*_args: object, **_kwargs: object) -> object:
            raise ValidationFailed("invalid governed request")

        return call


def test_aip_routes_map_every_governed_builder_error_to_request_scoped_http_error(monkeypatch) -> None:
    monkeypatch.setattr(api_runtime, "foundry", SimpleNamespace(aip=_RaisingNamespace()))
    request = _request()
    pilot = AipPilotPlanRequest(
        applicationName="Restaurant Operations",
        domainDescription="Manage reservations.",
        domainBrief={},
    )
    invocations = (
        lambda: aip.get_aip_fde_catalog(request),
        lambda: aip.run_aip_fde(request, AipFdeRunRequest(userMessage="Inspect the branch")),
        lambda: aip.approve_aip_fde_mcp_confirmation(request, "app-1", "challenge-1"),
        lambda: aip.plan_aip_pilot(request, pilot),
        lambda: aip.generate_aip_pilot(request, AipPilotGenerateRequest(plan={}), "idem-pilot"),
        lambda: aip.get_aip_pilot(request, "ri.pilot.1"),
        lambda: aip.validate_aip_builder(request, _builder_payload()),
        lambda: aip.run_aip_builder(request, _builder_payload(is_run=True)),
    )

    for invoke in invocations:
        with pytest.raises(HTTPException) as caught:
            invoke()
        assert caught.value.status_code == 400
        assert caught.value.detail["request_id"] == "req-api-edge"
        assert caught.value.detail["code"] == "VALIDATION_FAILED"


def test_pilot_policy_condition_rest_contract_distinguishes_omitted_and_explicit_null_values() -> None:
    exists = AipPilotPolicyConditionRequest(propertyApiName="severity", operator="exists")
    equality = AipPilotPolicyConditionRequest(propertyApiName="severity", operator="eq", value="urgent")

    assert exists.model_dump(by_alias=True, exclude_none=True) == {
        "propertyApiName": "severity",
        "operator": "exists",
    }
    assert equality.model_dump(by_alias=True, exclude_none=True)["value"] == "urgent"
    with pytest.raises(ValidationError, match="must omit value"):
        AipPilotPolicyConditionRequest(propertyApiName="severity", operator="exists", value=None)
    with pytest.raises(ValidationError, match="require value"):
        AipPilotPolicyConditionRequest(propertyApiName="severity", operator="eq")


def test_developer_console_routes_do_not_leak_domain_errors(monkeypatch) -> None:
    monkeypatch.setattr(api_runtime, "foundry", SimpleNamespace(developer_console=_RaisingNamespace()))
    request = _request()
    app = OsdkApplicationCreateRequest(
        appApiName="restaurant_ops",
        displayName="Restaurant Operations",
        resources=[],
    )
    client = OsdkApplicationClientRequest(clientId="restaurant-web")
    server = OsdkMcpServerConfigureRequest(
        status="enabled",
        descriptionMarkdown="Governed ontology tools for the restaurant application.",
    )
    rotation = OsdkClientSecretRotateRequest(reason="scheduled rotation")
    invocations = (
        lambda: developer_console.create_osdk_application(request, app, "idem-app"),
        lambda: developer_console.list_osdk_applications(request),
        lambda: developer_console.configure_ontology_mcp_server(request, "app-1", server, "idem-server"),
        lambda: developer_console.get_ontology_mcp_server(request, "app-1"),
        lambda: developer_console.list_ontology_mcp_hub(request),
        lambda: developer_console.list_osdk_application_clients(request, "app-1"),
        lambda: developer_console.list_osdk_application_client_secrets(request, "app-1", "client-1"),
        lambda: developer_console.rotate_osdk_application_client_secret(
            request, "app-1", "client-1", rotation, "idem-rotate"
        ),
        lambda: developer_console.revoke_osdk_application_client_secret(request, "app-1", "client-1", "idem-revoke"),
        lambda: developer_console.update_osdk_application_client(request, "app-1", "client-1", client, "idem-client"),
        lambda: developer_console.list_osdk_sdk_versions(request, "app-1"),
        lambda: developer_console.list_osdk_release_channels(request, "app-1", None),
        lambda: developer_console.list_osdk_compatibility_windows(request, "app-1"),
        lambda: developer_console.get_osdk_install_metadata(request, "app-1"),
    )

    for invoke in invocations:
        with pytest.raises(HTTPException) as caught:
            invoke()
        assert caught.value.status_code == 400
        assert caught.value.detail["request_id"] == "req-api-edge"
        assert caught.value.detail["message"] == "invalid governed request"
