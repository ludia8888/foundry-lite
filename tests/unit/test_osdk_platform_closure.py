from __future__ import annotations

import base64
import hashlib
import importlib
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import OsdkApplicationBundle
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.domain.errors import PermissionDenied

from tests.conftest import prepare_indexed_demo


def test_osdk_app_scope_requires_user_permission_and_app_grant(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)
    app = _create_osdk_app(
        foundry,
        ctx,
        resources=(_resource("object", "Order", "osdk:object:Order:read", "osdk:object:Order:subscribe"),),
    )
    scoped_ctx = _scoped_ctx(ctx, app, scopes=("osdk:object:Order:read",))

    page = foundry.objects.query("Order", ctx=scoped_ctx, limit=1)

    assert page["items"][0]["objectType"] == "Order"
    with pytest.raises(PermissionDenied, match="OSDK application scope denied"):
        foundry.actions.validate(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=cast(int, page["items"][0]["objectVersion"]),
            params={"reason": "approve"},
            ctx=replace(scoped_ctx, token_scopes=("osdk:action:ApproveOrder:validate",)),
        )

    viewer_ctx = replace(
        _scoped_ctx(ctx, app, scopes=("osdk:action:ApproveOrder:execute",)),
        actor_user_id="viewer",
        roles=("viewer",),
    )
    with pytest.raises(PermissionDenied):
        foundry.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=cast(int, page["items"][0]["objectVersion"]),
            params={"reason": "viewer denied"},
            idempotency_key="viewer-denied-by-rbac",
            ctx=viewer_ctx,
        )


def test_osdk_app_scope_fails_closed_for_unknown_app_client_or_scope(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)
    app = _create_osdk_app(
        foundry,
        ctx,
        resources=(_resource("object", "Order", "osdk:object:Order:read"),),
        client_id="orders-client",
    )

    missing_scope = _scoped_ctx(ctx, app, scopes=("osdk:object:Customer:read",), client_id="orders-client")
    with pytest.raises(PermissionDenied, match="OSDK application scope denied"):
        foundry.objects.query("Order", ctx=missing_scope)

    unknown_client = _scoped_ctx(ctx, app, scopes=("osdk:object:Order:read",), client_id="unknown-client")
    with pytest.raises(PermissionDenied, match="OSDK client is not active"):
        foundry.objects.query("Order", ctx=unknown_client)

    unknown_app = replace(
        ctx,
        application_id="osdk_app_missing",
        client_id="orders-client",
        token_scopes=("osdk:object:Order:read",),
    )
    with pytest.raises(PermissionDenied, match="OSDK application"):
        foundry.objects.query("Order", ctx=unknown_app)


def test_osdk_sdk_release_publishes_local_typescript_and_python_artifacts(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = demo_admin_context()
    app = _create_osdk_app(
        foundry,
        ctx,
        resources=(_resource("object", "Order", "osdk:object:Order:read"),),
        app_api_name="OrdersSdkRelease",
    )
    app_id = cast(str, app["application"]["id"])

    ts_release = foundry.developer_console.create_osdk_sdk_version(
        app_id,
        language="typescript",
        package_name="@acme/orders-osdk",
        requested_bump="minor",
        idempotency_key="sdk-ts-release",
        ctx=ctx,
    )
    replay = foundry.developer_console.create_osdk_sdk_version(
        app_id,
        language="typescript",
        package_name="@acme/orders-osdk",
        requested_bump="minor",
        idempotency_key="sdk-ts-release",
        ctx=ctx,
    )

    replay_artifacts = replay.get("artifacts", [])
    ts_artifacts = ts_release.get("artifacts", [])
    assert replay["sdkVersion"]["id"] == ts_release["sdkVersion"]["id"]
    assert replay_artifacts[0]["content_hash"] == ts_artifacts[0]["content_hash"]
    artifact = foundry.developer_console.osdk_release_artifact(
        app_id, cast(str, ts_release["sdkVersion"]["id"]), "typescript", ctx=ctx
    )
    raw = base64.b64decode(cast(str, artifact["contentBase64"]))
    assert f"sha256:{hashlib.sha256(raw).hexdigest()}" == ts_artifacts[0]["content_hash"]
    assert json.loads(raw)["packageName"] == "@acme/orders-osdk"

    py_release = foundry.developer_console.create_osdk_sdk_version(
        app_id,
        language="python",
        package_name="orders_osdk",
        requested_bump="minor",
        idempotency_key="sdk-python-release",
        ctx=ctx,
    )
    py_artifact = foundry.developer_console.osdk_release_artifact(
        app_id, cast(str, py_release["sdkVersion"]["id"]), "python", ctx=ctx
    )
    zip_path = tmp_path / "orders_osdk.zip"
    zip_path.write_bytes(base64.b64decode(cast(str, py_artifact["contentBase64"])))
    sys.path.insert(0, str(zip_path))
    try:
        module = importlib.import_module("orders_osdk")
        assert module.SDK_PACKAGE_MANIFEST["language"] == "python"
        assert module.SDK_PACKAGE_MANIFEST["packageName"] == "orders_osdk"
    finally:
        sys.path.remove(str(zip_path))
        sys.modules.pop("orders_osdk", None)


def test_osdk_sdk_release_blocks_resource_removal_without_major_bump(foundry: FoundryLite) -> None:
    ctx = demo_admin_context()
    app = _create_osdk_app(
        foundry,
        ctx,
        resources=(_resource("object", "Order", "osdk:object:Order:read", "osdk:object:Order:subscribe"),),
        app_api_name="OrdersCompat",
    )
    app_id = cast(str, app["application"]["id"])
    foundry.developer_console.create_osdk_sdk_version(
        app_id,
        language="typescript",
        requested_bump="minor",
        idempotency_key="compat-v1",
        ctx=ctx,
    )
    foundry.developer_console.update_osdk_application_resources(
        app_id,
        resources=(_resource("object", "Order", "osdk:object:Order:read"),),
        idempotency_key="compat-remove-subscribe",
        ctx=ctx,
    )

    blocked = foundry.developer_console.create_osdk_sdk_version(
        app_id,
        language="typescript",
        requested_bump="minor",
        idempotency_key="compat-v2-blocked",
        ctx=ctx,
    )

    assert blocked["sdkVersion"]["release_status"] == "blocked"
    assert blocked["sdkVersion"]["compatibility_status"] == "major_version_required"
    assert blocked.get("artifacts", []) == []


def test_object_subscription_streams_snapshot_changes_deletes_and_out_of_date(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)
    app = _create_osdk_app(
        foundry,
        ctx,
        resources=(_resource("object", "Order", "osdk:object:Order:subscribe"),),
    )
    subscribe_ctx = _scoped_ctx(ctx, app, scopes=("osdk:object:Order:subscribe",))

    stream = foundry.objects.subscription_events("Order", ctx=subscribe_ctx, max_events=2, poll_interval_seconds=0)
    snapshot = next(stream)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    foundry.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=cast(int, order["objectVersion"]),
        params={"reason": "subscription update"},
        idempotency_key="subscription-update",
        ctx=ctx,
    )
    changed = next(stream)

    assert snapshot["event"] == "snapshot"
    assert changed["event"] == "object_changed"
    assert changed["state"] == "ADDED_OR_UPDATED"
    changed_object = cast(dict[str, object], changed["object"])
    assert changed_object["objectId"] == "O-1001"

    removed_stream = foundry.objects.subscription_events(
        "Order",
        ctx=subscribe_ctx,
        filter_ast={"property": "status", "op": "eq", "value": "REVIEW"},
        max_events=2,
        poll_interval_seconds=0,
    )
    next(removed_stream)
    pending_order = foundry.objects.get("Order", "O-1002", ctx=ctx)
    foundry.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1002",
        expected_object_version=cast(int, pending_order["objectVersion"]),
        params={"reason": "subscription removal"},
        idempotency_key="subscription-removal",
        ctx=ctx,
    )
    removed = next(removed_stream)

    assert removed["state"] == "DELETED"
    assert removed["object"] == {"objectType": "Order", "objectId": "O-1002"}

    stale = foundry.objects.subscription_events(
        "Order",
        ctx=subscribe_ctx,
        last_seen_object_change_sequence=0,
        max_events=2,
        poll_interval_seconds=0,
    )
    assert next(stale)["event"] == "snapshot"
    assert next(stale)["event"] == "out_of_date"


def test_object_subscription_preserves_masking_and_requires_subscribe_scope(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)
    app = _create_osdk_app(
        foundry,
        ctx,
        resources=(_resource("object", "Order", "osdk:object:Order:subscribe"),),
    )
    viewer_ctx = replace(
        _scoped_ctx(ctx, app, scopes=("osdk:object:Order:subscribe",)),
        actor_user_id="viewer",
        roles=("viewer",),
    )

    snapshot = next(foundry.objects.subscription_events("Order", ctx=viewer_ctx, max_events=1))

    snapshot_items = cast(list[dict[str, object]], snapshot["items"])
    snapshot_properties = cast(dict[str, object], snapshot_items[0]["properties"])
    assert snapshot_properties["margin"] == "***MASKED***"
    missing_scope_ctx = replace(viewer_ctx, token_scopes=("osdk:object:Order:read",))
    with pytest.raises(PermissionDenied, match="OSDK application scope denied"):
        next(foundry.objects.subscription_events("Order", ctx=missing_scope_ctx, max_events=1))


def _create_osdk_app(
    foundry: FoundryLite,
    ctx: RequestContext,
    *,
    resources: tuple[dict[str, object], ...],
    app_api_name: str = "OrdersRuntime",
    client_id: str = "orders-client",
) -> OsdkApplicationBundle:
    return foundry.developer_console.create_osdk_application(
        app_api_name=app_api_name,
        display_name=app_api_name,
        client_id=client_id,
        resources=resources,
        idempotency_key=f"{app_api_name}-create",
        ctx=ctx,
    )


def _resource(resource_type: str, resource_api_name: str, *scopes: str) -> dict[str, object]:
    return {"resourceType": resource_type, "resourceApiName": resource_api_name, "scopes": list(scopes)}


def _scoped_ctx(
    ctx: RequestContext,
    app: OsdkApplicationBundle,
    *,
    scopes: tuple[str, ...],
    client_id: str = "orders-client",
) -> RequestContext:
    return replace(ctx, application_id=app["application"]["id"], client_id=client_id, token_scopes=scopes)
