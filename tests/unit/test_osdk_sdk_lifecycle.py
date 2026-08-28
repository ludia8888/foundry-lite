from __future__ import annotations

import base64
import hashlib
import importlib
import sys
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import OsdkApplicationBundle
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.domain.errors import ConflictDetected, PermissionDenied

from tests.conftest import prepare_indexed_demo

_READ_SCOPE = "osdk:object:Order:read"
_SUBSCRIBE_SCOPE = "osdk:object:Order:subscribe"


def test_osdk_sdk_lifecycle_channels_install_metadata_and_scoped_artifact_download(
    foundry: FoundryLite, tmp_path: Path
) -> None:
    ctx = demo_admin_context()
    app = _create_app(foundry, ctx, resources=(_resource("object", "Order", _READ_SCOPE),))
    app_id = cast(str, app["application"]["id"])

    ts_release = foundry.developer_console.create_osdk_sdk_version(
        app_id,
        language="typescript",
        package_name="@acme/orders-osdk",
        requested_bump="minor",
        idempotency_key="lifecycle-ts-v1",
        ctx=ctx,
    )
    py_release = foundry.developer_console.create_osdk_sdk_version(
        app_id,
        language="python",
        package_name="orders_osdk_lifecycle",
        requested_bump="minor",
        idempotency_key="lifecycle-py-v1",
        ctx=ctx,
    )

    channel = foundry.developer_console.promote_osdk_sdk_version(
        app_id,
        cast(str, ts_release["sdkVersion"]["id"]),
        channel="stable",
        idempotency_key="promote-ts-stable",
        ctx=ctx,
    )
    channels = foundry.developer_console.list_osdk_release_channels(app_id, language="typescript", ctx=ctx)
    metadata = foundry.developer_console.osdk_install_metadata(app_id, ctx=ctx)
    download = foundry.developer_console.create_osdk_artifact_download_token(
        app_id,
        cast(str, ts_release["sdkVersion"]["id"]),
        "typescript",
        ttl_seconds=60,
        idempotency_key="ts-download-token",
        ctx=ctx,
    )
    artifact = foundry.developer_console.osdk_release_artifact_by_download_token(
        cast(str, download["downloadToken"]), ctx=ctx
    )

    assert channel["channel"] == "stable"
    assert channels[0]["sdk_version_id"] == ts_release["sdkVersion"]["id"]
    metadata_channels = cast(list[Mapping[str, object]], metadata["channels"])
    assert metadata_channels[0]["channel"] == "stable"
    raw = base64.b64decode(cast(str, artifact["contentBase64"]))
    artifact_record = cast(Mapping[str, object], artifact["artifact"])
    assert artifact_record["content_hash"] == f"sha256:{hashlib.sha256(raw).hexdigest()}"
    with pytest.raises(PermissionDenied, match="download token is invalid"):
        foundry.developer_console.osdk_release_artifact_by_download_token(cast(str, download["downloadToken"]), ctx=ctx)

    py_artifact = foundry.developer_console.osdk_release_artifact(
        app_id,
        cast(str, py_release["sdkVersion"]["id"]),
        "python",
        ctx=ctx,
    )
    zip_path = tmp_path / "orders_osdk_lifecycle.zip"
    zip_path.write_bytes(base64.b64decode(cast(str, py_artifact["contentBase64"])))
    sys.path.insert(0, str(zip_path))
    try:
        module = importlib.import_module("orders_osdk_lifecycle")
        assert module.SDK_PACKAGE_MANIFEST["packageName"] == "orders_osdk_lifecycle"
    finally:
        sys.path.remove(str(zip_path))
        sys.modules.pop("orders_osdk_lifecycle", None)


def test_osdk_sdk_lifecycle_compatibility_window_keeps_deprecated_grant_alive(
    foundry: FoundryLite,
) -> None:
    ctx = prepare_indexed_demo(foundry)
    app = _create_app(
        foundry,
        ctx,
        resources=(_resource("object", "Order", _READ_SCOPE, _SUBSCRIBE_SCOPE),),
        client_id="orders-window-client",
        app_api_name="OrdersWindowApp",
    )
    app_id = cast(str, app["application"]["id"])
    scoped_ctx = replace(ctx, application_id=app_id, client_id="orders-window-client", token_scopes=(_SUBSCRIBE_SCOPE,))
    v1 = foundry.developer_console.create_osdk_sdk_version(
        app_id,
        language="typescript",
        requested_bump="minor",
        idempotency_key="window-v1",
        ctx=ctx,
    )

    assert next(foundry.objects.subscription_events("Order", ctx=scoped_ctx, max_events=1))["event"] == "snapshot"
    foundry.developer_console.update_osdk_application_resources(
        app_id,
        resources=(_resource("object", "Order", _READ_SCOPE),),
        idempotency_key="remove-subscribe-for-window",
        ctx=ctx,
    )
    with pytest.raises(PermissionDenied, match="OSDK application scope denied"):
        next(foundry.objects.subscription_events("Order", ctx=scoped_ctx, max_events=1))

    v2 = foundry.developer_console.create_osdk_sdk_version(
        app_id,
        language="typescript",
        requested_bump="major",
        idempotency_key="window-v2-major",
        ctx=ctx,
    )
    window = foundry.developer_console.create_osdk_compatibility_window(
        app_id,
        from_version_id=cast(str, v1["sdkVersion"]["id"]),
        to_version_id=cast(str, v2["sdkVersion"]["id"]),
        supported_until="2099-01-01T00:00:00+00:00",
        idempotency_key="window-v1-v2",
        ctx=ctx,
    )

    snapshot = next(foundry.objects.subscription_events("Order", ctx=scoped_ctx, max_events=1))

    assert window["status"] == "active"
    assert snapshot["event"] == "snapshot"


def test_osdk_sdk_lifecycle_blocks_major_removal_without_major_bump(foundry: FoundryLite) -> None:
    ctx = demo_admin_context()
    app = _create_app(
        foundry,
        ctx,
        resources=(_resource("object", "Order", _READ_SCOPE, _SUBSCRIBE_SCOPE),),
        app_api_name="OrdersMajorGate",
    )
    app_id = cast(str, app["application"]["id"])
    released = foundry.developer_console.create_osdk_sdk_version(
        app_id,
        language="typescript",
        requested_bump="minor",
        idempotency_key="major-gate-v1",
        ctx=ctx,
    )
    foundry.developer_console.update_osdk_application_resources(
        app_id,
        resources=(_resource("object", "Order", _READ_SCOPE),),
        idempotency_key="major-gate-remove-subscribe",
        ctx=ctx,
    )

    blocked = foundry.developer_console.create_osdk_sdk_version(
        app_id,
        language="typescript",
        requested_bump="minor",
        idempotency_key="major-gate-v2-blocked",
        ctx=ctx,
    )

    assert blocked["sdkVersion"]["release_status"] == "blocked"
    assert blocked["sdkVersion"]["compatibility_status"] == "major_version_required"
    assert blocked.get("artifacts", []) == []
    released_artifacts = cast(list[Mapping[str, object]], released["artifacts"])
    artifact_root = Path(str(released_artifacts[0]["storage_uri"])).parents[3]
    blocked_version = str(blocked["sdkVersion"]["version"])
    assert not (artifact_root / ctx.tenant_id / app_id / blocked_version).exists()


def test_osdk_sdk_lifecycle_mutations_are_replay_safe_idempotency_contract(
    foundry: FoundryLite,
) -> None:
    ctx = demo_admin_context()
    app = _create_app(
        foundry,
        ctx,
        resources=(_resource("object", "Order", _READ_SCOPE, _SUBSCRIBE_SCOPE),),
        app_api_name="OrdersIdempotencyContract",
        client_id="orders-idem-default",
    )
    app_id = cast(str, app["application"]["id"])

    first_client = foundry.developer_console.create_osdk_application_client(
        app_id,
        client_id="orders-idem-web",
        redirect_uris=("https://orders.example.test/callback",),
        allowed_scopes=(_READ_SCOPE,),
        idempotency_key="idem-client-create",
        ctx=ctx,
    )
    replayed_client = foundry.developer_console.create_osdk_application_client(
        app_id,
        client_id="orders-idem-web",
        redirect_uris=("https://orders.example.test/callback",),
        allowed_scopes=(_READ_SCOPE,),
        idempotency_key="idem-client-create",
        ctx=ctx,
    )
    with pytest.raises(ConflictDetected, match="different request"):
        foundry.developer_console.create_osdk_application_client(
            app_id,
            client_id="orders-idem-web",
            redirect_uris=("https://orders.example.test/other-callback",),
            allowed_scopes=(_READ_SCOPE,),
            idempotency_key="idem-client-create",
            ctx=ctx,
        )

    updated = foundry.developer_console.update_osdk_application_client(
        app_id,
        cast(str, first_client["id"]),
        status="active",
        redirect_uris=("https://orders.example.test/callback",),
        allowed_scopes=(_READ_SCOPE, _SUBSCRIBE_SCOPE),
        idempotency_key="idem-client-update",
        ctx=ctx,
    )
    replayed_update = foundry.developer_console.update_osdk_application_client(
        app_id,
        cast(str, first_client["id"]),
        status="active",
        redirect_uris=("https://orders.example.test/callback",),
        allowed_scopes=(_READ_SCOPE, _SUBSCRIBE_SCOPE),
        idempotency_key="idem-client-update",
        ctx=ctx,
    )
    with pytest.raises(ConflictDetected, match="different request"):
        foundry.developer_console.update_osdk_application_client(
            app_id,
            cast(str, first_client["id"]),
            status="inactive",
            redirect_uris=("https://orders.example.test/callback",),
            allowed_scopes=(_READ_SCOPE, _SUBSCRIBE_SCOPE),
            idempotency_key="idem-client-update",
            ctx=ctx,
        )

    deactivated = foundry.developer_console.deactivate_osdk_application_client(
        app_id,
        cast(str, first_client["id"]),
        idempotency_key="idem-client-deactivate",
        ctx=ctx,
    )
    replayed_deactivate = foundry.developer_console.deactivate_osdk_application_client(
        app_id,
        cast(str, first_client["id"]),
        idempotency_key="idem-client-deactivate",
        ctx=ctx,
    )
    release = foundry.developer_console.create_osdk_sdk_version(
        app_id,
        language="typescript",
        requested_bump="minor",
        idempotency_key="idem-release-v1",
        ctx=ctx,
    )
    promoted = foundry.developer_console.promote_osdk_sdk_version(
        app_id,
        cast(str, release["sdkVersion"]["id"]),
        channel="stable",
        idempotency_key="idem-promote-stable",
        ctx=ctx,
    )
    replayed_promote = foundry.developer_console.promote_osdk_sdk_version(
        app_id,
        cast(str, release["sdkVersion"]["id"]),
        channel="stable",
        idempotency_key="idem-promote-stable",
        ctx=ctx,
    )
    with pytest.raises(ConflictDetected, match="different request"):
        foundry.developer_console.promote_osdk_sdk_version(
            app_id,
            cast(str, release["sdkVersion"]["id"]),
            channel="beta",
            idempotency_key="idem-promote-stable",
            ctx=ctx,
        )

    window = foundry.developer_console.create_osdk_compatibility_window(
        app_id,
        from_version_id=cast(str, release["sdkVersion"]["id"]),
        to_version_id=cast(str, release["sdkVersion"]["id"]),
        supported_until="2099-01-01T00:00:00+00:00",
        idempotency_key="idem-window",
        ctx=ctx,
    )
    replayed_window = foundry.developer_console.create_osdk_compatibility_window(
        app_id,
        from_version_id=cast(str, release["sdkVersion"]["id"]),
        to_version_id=cast(str, release["sdkVersion"]["id"]),
        supported_until="2099-01-01T00:00:00+00:00",
        idempotency_key="idem-window",
        ctx=ctx,
    )
    with pytest.raises(ConflictDetected, match="different request"):
        foundry.developer_console.create_osdk_compatibility_window(
            app_id,
            from_version_id=cast(str, release["sdkVersion"]["id"]),
            to_version_id=cast(str, release["sdkVersion"]["id"]),
            supported_until="2099-02-01T00:00:00+00:00",
            idempotency_key="idem-window",
            ctx=ctx,
        )

    assert replayed_client["id"] == first_client["id"]
    assert replayed_update.get("allowed_scopes") == updated.get("allowed_scopes")
    assert replayed_deactivate["status"] == deactivated["status"] == "inactive"
    assert replayed_promote["id"] == promoted["id"]
    assert replayed_window["id"] == window["id"]


def test_osdk_sdk_lifecycle_download_token_replay_is_deterministic_and_redacted(
    foundry: FoundryLite,
) -> None:
    ctx = demo_admin_context()
    app = _create_app(
        foundry,
        ctx,
        resources=(_resource("object", "Order", _READ_SCOPE),),
        app_api_name="OrdersDownloadTokenIdem",
        client_id="orders-download-token-client",
    )
    app_id = cast(str, app["application"]["id"])
    release = foundry.developer_console.create_osdk_sdk_version(
        app_id,
        language="typescript",
        requested_bump="minor",
        idempotency_key="download-token-release",
        ctx=ctx,
    )

    first = foundry.developer_console.create_osdk_artifact_download_token(
        app_id,
        cast(str, release["sdkVersion"]["id"]),
        "typescript",
        ttl_seconds=120,
        idempotency_key="download-token-idem",
        ctx=ctx,
    )
    replayed = foundry.developer_console.create_osdk_artifact_download_token(
        app_id,
        cast(str, release["sdkVersion"]["id"]),
        "typescript",
        ttl_seconds=120,
        idempotency_key="download-token-idem",
        ctx=ctx,
    )
    with pytest.raises(ConflictDetected, match="different request"):
        foundry.developer_console.create_osdk_artifact_download_token(
            app_id,
            cast(str, release["sdkVersion"]["id"]),
            "typescript",
            ttl_seconds=121,
            idempotency_key="download-token-idem",
            ctx=ctx,
        )

    audit_payload = repr(foundry.operations.list_runs(ctx=ctx)["auditEvents"])
    raw_download_token = cast(str, first["downloadToken"])

    assert replayed == first
    assert raw_download_token not in audit_payload


def _create_app(
    foundry: FoundryLite,
    ctx: RequestContext,
    *,
    resources: tuple[dict[str, object], ...],
    app_api_name: str = "OrdersSdkLifecycle",
    client_id: str = "orders-sdk-client",
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
