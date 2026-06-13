from __future__ import annotations

from dataclasses import replace

import pytest
from foundry_lite.application.core import FoundryLiteCore
from foundry_lite.application.ports import RestSourceConfig
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.infrastructure.adapters import RestPullConnectorAdapter
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies

from tests.contracts.test_rest_connector_adapter_contract import MockRestServer


def test_rest_connector_snapshot_commits_raw_dataset(tmp_path) -> None:
    with MockRestServer() as server:
        core = _core_with_rest_connector(tmp_path)
        ctx = demo_admin_context()
        core.ensure_dataset("raw.rest_orders", ctx=ctx, primary_key=["order_id"])

        result = core.sync_connector_snapshot(
            "raw.rest_orders",
            connector_name="rest",
            resource_name="orders",
            ctx=ctx,
            rest=RestSourceConfig(base_url=server.base_url, resource_path="/orders"),
        )

    preview = core.preview_dataset("raw.rest_orders", ctx=ctx)
    runs = core.query_runs(ctx=ctx, run_type="sync", status="COMMITTED")

    assert result.version_number == 1
    assert preview[0]["amount"] == 100
    assert preview[0]["order_id"] == "O-1001"
    assert runs["syncRuns"][0]["source_type"] == "connector.rest"


def test_rest_connector_rate_limit_failure_is_visible_in_operations(tmp_path) -> None:
    with MockRestServer() as server:
        core = _core_with_rest_connector(tmp_path)
        ctx = demo_admin_context()
        core.ensure_dataset("raw.rest_limited", ctx=ctx, primary_key=["order_id"])

        with pytest.raises(ValidationFailed, match="connector snapshot sync failed"):
            core.sync_connector_snapshot(
                "raw.rest_limited",
                connector_name="rest",
                resource_name="limited",
                ctx=ctx,
                rest=RestSourceConfig(base_url=server.base_url, resource_path="/rate-limit"),
            )

    failed_runs = core.query_runs(ctx=ctx, run_type="sync", status="FAILED")["syncRuns"]
    assert failed_runs[0]["source_type"] == "connector.rest"
    assert failed_runs[0]["error"]["type"] == "ConnectorRateLimitedError"


def _core_with_rest_connector(tmp_path) -> FoundryLiteCore:
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "flite")
    return FoundryLiteCore(dependencies=replace(dependencies, connector_adapter=RestPullConnectorAdapter()))
