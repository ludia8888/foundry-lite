from __future__ import annotations

from typing import cast

import pytest
from foundry_lite.application.ports import OsdkResourceOperation, OsdkResourceType
from foundry_lite.application.services import osdk_application_records as records
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.domain.errors import PermissionDenied, ValidationFailed


def test_osdk_application_record_helpers_validate_scope_vocabulary() -> None:
    assert records.scope_for("object", "Order", "read") == "osdk:object:Order:read"
    assert records.scope_for("action", "ApproveOrder", "execute") == "osdk:action:ApproveOrder:execute"
    with pytest.raises(ValidationFailed, match="unsupported OSDK resource scope request"):
        records.scope_for(cast(OsdkResourceType, "dataset"), "Order", "read")
    with pytest.raises(ValidationFailed, match="unsupported OSDK resource scope request"):
        records.scope_for("object", "Order", cast(OsdkResourceOperation, "write"))


def test_osdk_application_record_helpers_validate_app_client_and_resource_records() -> None:
    ctx = demo_admin_context()

    app = records._application_record(ctx, "OrdersApp", "", "idem-app", "2026-07-01T00:00:00+00:00")
    resource = records._resource_record(
        ctx.tenant_id,
        app.application_id,
        {"resourceType": "object", "resourceApiName": "Order", "scopes": ["osdk:object:Order:read"]},
        "2026-07-01T00:00:00+00:00",
    )
    client = records._client_record(ctx, app.application_id, " orders-web ", "2026-07-01T00:00:00+00:00")

    assert app.display_name == "OrdersApp"
    assert resource.scopes == ("osdk:object:Order:read",)
    assert client.client_id == "orders-web"
    with pytest.raises(ValidationFailed, match="API name is invalid"):
        records._application_record(ctx, "1Orders", "bad", "idem-bad", "2026-07-01T00:00:00+00:00")
    with pytest.raises(ValidationFailed, match="client id is required"):
        records._client_record(ctx, app.application_id, " ", "2026-07-01T00:00:00+00:00")
    with pytest.raises(ValidationFailed, match="resource type is unsupported"):
        records._resource_record(
            ctx.tenant_id,
            app.application_id,
            {"resourceType": "dataset", "resourceApiName": "Order", "scopes": []},
            "2026-07-01T00:00:00+00:00",
        )
    with pytest.raises(ValidationFailed, match="field is missing"):
        records._resource_record(
            ctx.tenant_id,
            app.application_id,
            {"resourceType": "object", "scopes": []},
            "2026-07-01T00:00:00+00:00",
        )
    with pytest.raises(ValidationFailed, match="scopes must be a list"):
        records._resource_record(
            ctx.tenant_id,
            app.application_id,
            {"resourceType": "object", "resourceApiName": "Order", "scopes": "osdk:object:Order:read"},
            "2026-07-01T00:00:00+00:00",
        )
    with pytest.raises(ValidationFailed, match="scope does not match"):
        records._resource_record(
            ctx.tenant_id,
            app.application_id,
            {"resourceType": "object", "resourceApiName": "Order", "scopes": ["osdk:object:Customer:read"]},
            "2026-07-01T00:00:00+00:00",
        )


def test_osdk_application_record_helpers_scope_matching_and_misc_validation() -> None:
    resource = {
        "id": "resource-1",
        "tenant_id": "tenant-demo",
        "app_id": "app-1",
        "resource_type": "object",
        "resource_api_name": "Order",
        "scopes": ["osdk:object:Order:read"],
        "created_at": "2026-07-01T00:00:00+00:00",
    }

    assert records._scope_allowed("osdk:object:Order:read", ["osdk:object:Order:read"], [resource]) is True
    assert records._scope_allowed("osdk:object:Order:read", ["osdk:*"], [resource]) is True
    assert records._scope_allowed("osdk:object:Order:subscribe", ["osdk:*"], [resource]) is False
    assert records._channel("stable") == "stable"
    assert records._language("python") == "python"
    assert records._clean_strings([" a ", " ", "b"]) == ["a", "b"]
    assert records._string_sequence("not-a-list") == ()
    assert records._string_sequence(["read", "", 1]) == ("read", "1")
    assert records._positive_ttl(1) == 1
    assert records._ttl("bad", 900) == 900
    assert records._default_client_id("OrdersApp") == "client-OrdersApp"
    assert records._client_request("app-1", "client-1", [" uri "], [" scope "], 1, 2)["redirectUris"] == ["uri"]
    assert records._client_update_request("app-1", "row-1", "inactive", [], [], 1, 2)["status"] == "inactive"
    with pytest.raises(PermissionDenied, match="OSDK application scope denied"):
        records._raise_scope_denied("osdk:object:Order:read", "object", "Order")
    with pytest.raises(ValidationFailed, match="release channel"):
        records._channel("prod")
    with pytest.raises(ValidationFailed, match="language"):
        records._language("ruby")
    with pytest.raises(ValidationFailed, match="TTL"):
        records._positive_ttl(0)
    with pytest.raises(ValidationFailed, match="Idempotency-Key"):
        records._require_idempotency_key("")
