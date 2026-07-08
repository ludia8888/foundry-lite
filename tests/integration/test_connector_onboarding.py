from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any, cast

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.adapters import RestPullConnectorAdapter
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from foundry_lite.infrastructure.secrets import EnvSecretProvider
from sqlalchemy import func, select

from tests.contracts.test_rest_connector_adapter_contract import MockRestServer


def test_connector_onboarding_tests_resource_without_dataset_commit(tmp_path) -> None:
    with MockRestServer() as server:
        foundry = _foundry_with_rest_connector(tmp_path, {"FOUNDRY_LITE_SECRET_ERP_TOKEN": "token-v1"})
        ctx = demo_admin_context()
        connection = _create_connection(foundry, server.base_url, ctx=ctx)
        resource = _upsert_orders_resource(foundry, ctx=ctx)
        result = foundry.connectors.test_resource("erp", "orders", ctx=ctx)

    assert connection["connectorName"] == "erp"
    assert resource["datasetRef"] == "raw.erp_orders"
    assert result["status"] == "succeeded"
    assert result["rowCount"] == 1
    assert cast(list[dict[str, object]], result["sampleRows"])[0]["order_id"] == "O-1001"
    assert _dataset_version_count(foundry, "raw.erp_orders", ctx.tenant_id) == 0
    assert server.requests[0]["authorization"] == "Bearer token-v1"


def test_connector_onboarding_start_sync_replays_and_commits_registered_rest_source(tmp_path) -> None:
    with MockRestServer() as server:
        foundry = _foundry_with_rest_connector(tmp_path, {"FOUNDRY_LITE_SECRET_ERP_TOKEN": "token-v1"})
        ctx = demo_admin_context()
        _create_connection(foundry, server.base_url, ctx=ctx)
        _upsert_orders_resource(foundry, ctx=ctx)

        first = foundry.connectors.start_resource_sync(
            "erp",
            "orders",
            idempotency_key="sync-orders-v1",
            sync_name="erp-orders-first-sync",
            ctx=ctx,
        )
        replay = foundry.connectors.start_resource_sync(
            "erp",
            "orders",
            idempotency_key="sync-orders-v1",
            sync_name="erp-orders-first-sync",
            ctx=ctx,
        )

    assert replay["workflowRunId"] == first["workflowRunId"]
    assert first["status"] == "succeeded"
    assert first["output"]["datasetRef"] == "raw.erp_orders"
    assert first["output"]["connectorName"] == "erp"
    assert str(first["output"]["configFingerprint"]).startswith("sha256:")
    assert first["output"]["workflowKind"] == "connector_sync"
    assert first["output"]["rowCount"] == 1
    assert foundry.datasets.preview("raw.erp_orders", ctx=ctx)[0]["order_id"] == "O-1001"
    assert len(server.requests) == 1


def test_managed_rest_sync_run_closes_after_connector_workflow_commit(tmp_path) -> None:
    with MockRestServer() as server:
        foundry = _foundry_with_rest_connector(tmp_path, {"FOUNDRY_LITE_SECRET_ERP_TOKEN": "token-v1"})
        ctx = demo_admin_context()
        _create_connection(foundry, server.base_url, ctx=ctx)
        _upsert_orders_resource(foundry, ctx=ctx)
        foundry.sources.create_managed_sync(
            sync_name="erp_orders_managed",
            source_name="erp",
            display_name="ERP Orders Managed",
            source_type="rest_api",
            capability="batch",
            mode="APPEND",
            config_summary={"connectorName": "erp", "resourceName": "orders"},
            target_dataset_ref="raw.erp_orders",
            schedule={"mode": "manual"},
            idempotency_key="create-managed-erp-orders",
            ctx=ctx,
        )

        run = foundry.sources.start_managed_sync_run(
            "erp_orders_managed",
            idempotency_key="run-managed-erp-orders",
            ctx=ctx,
        )

    assert run["status"] == "succeeded"
    assert str(run["workflowRunId"]).startswith("flite:")
    assert str(run["datasetVersionId"]).startswith("dsv_")
    assert run["resultSummary"]["workflowRun"]["output"]["rowCount"] == 1
    assert foundry.datasets.preview("raw.erp_orders", ctx=ctx)[0]["order_id"] == "O-1001"


def test_managed_rest_sync_run_fails_when_target_differs_from_connector_resource(tmp_path) -> None:
    with MockRestServer() as server:
        foundry = _foundry_with_rest_connector(tmp_path, {"FOUNDRY_LITE_SECRET_ERP_TOKEN": "token-v1"})
        ctx = demo_admin_context()
        _create_connection(foundry, server.base_url, ctx=ctx)
        _upsert_orders_resource(foundry, ctx=ctx)
        foundry.sources.create_managed_sync(
            sync_name="erp_orders_wrong_target",
            source_name="erp",
            display_name="ERP Orders Wrong Target",
            source_type="rest_api",
            capability="batch",
            mode="APPEND",
            config_summary={"connectorName": "erp", "resourceName": "orders"},
            target_dataset_ref="raw.erp_orders_ui_target",
            schedule={"mode": "manual"},
            idempotency_key="create-managed-erp-orders-wrong-target",
            ctx=ctx,
        )

        run = foundry.sources.start_managed_sync_run(
            "erp_orders_wrong_target",
            idempotency_key="run-managed-erp-orders-wrong-target",
            ctx=ctx,
        )

    assert run["status"] == "failed"
    assert run["datasetVersionId"] is None
    assert run["error"]["message"] == "managed sync target dataset does not match connector resource dataset"
    assert run["error"]["details"]["targetDatasetRef"] == "raw.erp_orders_ui_target"
    assert run["error"]["details"]["connectorDatasetRef"] == "raw.erp_orders"
    assert _dataset_version_count(foundry, "raw.erp_orders", ctx.tenant_id) == 0
    assert _dataset_version_count(foundry, "raw.erp_orders_ui_target", ctx.tenant_id) == 0
    assert server.requests == []


def test_connector_registered_activity_fails_closed_after_config_fingerprint_change(tmp_path) -> None:
    with MockRestServer() as server:
        foundry = _foundry_with_rest_connector(tmp_path, {"FOUNDRY_LITE_SECRET_ERP_TOKEN": "token-v1"})
        ctx = demo_admin_context()
        _create_connection(foundry, server.base_url, ctx=ctx)
        _upsert_orders_resource(foundry, ctx=ctx)
        run = foundry.connectors.start_resource_sync(
            "erp",
            "orders",
            idempotency_key="sync-orders-before-change",
            ctx=ctx,
        )
        foundry.connectors.update_connection(
            "erp",
            idempotency_key="update-erp-rate-limit",
            rate_limit_per_minute=25,
            ctx=ctx,
        )

        with pytest.raises(ConflictDetected, match="connector config changed"):
            foundry.connectors.run_registered_sync_activity(run["output"], ctx=ctx)


def test_connector_onboarding_rejects_raw_secret_values(tmp_path) -> None:
    with MockRestServer() as server:
        foundry = _foundry_with_rest_connector(tmp_path, {"FOUNDRY_LITE_SECRET_ERP_TOKEN": "token-v1"})
        ctx = demo_admin_context()
        with pytest.raises(ValidationFailed, match="secretRef only") as exc_info:
            foundry.connectors.create_connection(
                connector_name="erp",
                display_name="ERP",
                base_url=server.base_url,
                auth={"mode": "bearer", "token": "raw-token"},
                idempotency_key="create-raw-token",
                allow_private_network=True,
                ctx=ctx,
            )

    assert exc_info.value.details == {"secretValue": "***REDACTED***"}


def test_connector_resource_test_reports_missing_secret_ref_without_raw_secret(tmp_path) -> None:
    with MockRestServer() as server:
        foundry = _foundry_with_rest_connector(tmp_path, {})
        ctx = demo_admin_context()
        _create_connection(foundry, server.base_url, ctx=ctx)
        _upsert_orders_resource(foundry, ctx=ctx)
        result = foundry.connectors.test_resource("erp", "orders", ctx=ctx)

    assert result["status"] == "failed"
    error = cast(Mapping[str, object], result["error"])
    error_details = cast(Mapping[str, object], error["details"])
    assert error["type"] == "VALIDATION_FAILED"
    assert error_details["secret_value"] == "***REDACTED***"
    assert "token-v1" not in str(result)


def test_connector_resource_upsert_conflicts_on_primary_key_mismatch(tmp_path) -> None:
    with MockRestServer() as server:
        foundry = _foundry_with_rest_connector(tmp_path, {"FOUNDRY_LITE_SECRET_ERP_TOKEN": "token-v1"})
        ctx = demo_admin_context()
        _create_connection(foundry, server.base_url, ctx=ctx)
        foundry.datasets.ensure("raw.erp_orders", ctx=ctx, primary_key=["order_id"])

        with pytest.raises(ConflictDetected, match="primary key"):
            foundry.connectors.upsert_resource(
                "erp",
                "orders",
                dataset_ref="raw.erp_orders",
                resource_path="/orders",
                primary_key=["id"],
                idempotency_key="upsert-orders-wrong-pk",
                ctx=ctx,
            )


def _foundry_with_rest_connector(tmp_path, environ: dict[str, str]) -> FoundryLite:
    secret_provider = EnvSecretProvider(environ=environ)
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "flite")
    return FoundryLite(
        dependencies=replace(
            dependencies,
            connector_adapter=RestPullConnectorAdapter(secret_provider=secret_provider),
            secret_provider=secret_provider,
        )
    )


def _create_connection(foundry: FoundryLite, base_url: str, *, ctx) -> dict[str, object]:
    return foundry.connectors.create_connection(
        connector_name="erp",
        display_name="ERP",
        base_url=base_url,
        auth={"mode": "bearer", "tokenSecretRef": "erp-token"},
        idempotency_key="create-erp",
        rate_limit_per_minute=60,
        allow_private_network=True,
        ctx=ctx,
    )


def _upsert_orders_resource(foundry: FoundryLite, *, ctx) -> dict[str, object]:
    return foundry.connectors.upsert_resource(
        "erp",
        "orders",
        dataset_ref="raw.erp_orders",
        resource_path="/orders",
        pagination={
            "strategy": "cursor",
            "itemsPath": "items",
            "nextCursorPath": "nextCursor",
            "cursorQueryParam": "cursor",
            "cursorKey": "cursor",
        },
        schema_columns=["order_id", "amount"],
        primary_key=["order_id"],
        idempotency_key="upsert-orders",
        ctx=ctx,
    )


def _dataset_version_count(foundry: FoundryLite, dataset_ref: str, tenant_id: str) -> int:
    namespace, name = dataset_ref.split(".", 1)
    with foundry.engine.begin() as conn:
        sql_conn = cast(Any, conn)
        dataset_id = sql_conn.execute(
            select(db.datasets.c.id).where(
                db.datasets.c.tenant_id == tenant_id,
                db.datasets.c.namespace == namespace,
                db.datasets.c.name == name,
            )
        ).scalar_one()
        return int(
            sql_conn.execute(
                select(func.count())
                .select_from(db.dataset_versions)
                .where(db.dataset_versions.c.dataset_id == dataset_id)
            ).scalar_one()
        )
