from __future__ import annotations

from dataclasses import replace

import pytest
from foundry_lite.application.core import FoundryLiteCore
from foundry_lite.application.ports import RestSourceConfig
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.adapters import DuckDBComputeAdapter, RestPullConnectorAdapter
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from sqlalchemy import select

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
            rest=RestSourceConfig(base_url=server.base_url, resource_path="/orders", allow_private_network=True),
        )

    preview = core.preview_dataset("raw.rest_orders", ctx=ctx)
    runs = core.query_runs(ctx=ctx, run_type="sync", status="COMMITTED")

    assert result.version_number == 1
    assert preview[0]["amount"] == 100
    assert preview[0]["order_id"] == "O-1001"
    assert runs["syncRuns"][0]["source_type"] == "connector.rest"


def test_rest_connector_snapshot_resumes_from_committed_cursor(tmp_path) -> None:
    with MockRestServer() as server:
        core = _core_with_rest_connector(tmp_path)
        ctx = demo_admin_context()
        core.ensure_dataset("raw.rest_orders", ctx=ctx, primary_key=["order_id"])
        rest = RestSourceConfig(base_url=server.base_url, resource_path="/orders", allow_private_network=True)

        first = core.sync_connector_snapshot(
            "raw.rest_orders",
            connector_name="rest",
            resource_name="orders",
            ctx=ctx,
            rest=rest,
        )
        second = core.sync_connector_snapshot(
            "raw.rest_orders",
            connector_name="rest",
            resource_name="orders",
            ctx=ctx,
            rest=rest,
        )

    latest_metadata = _transaction_metadata(core, second.transaction_id)
    preview = core.preview_dataset("raw.rest_orders", ctx=ctx)

    assert first.version_number == 1
    assert second.version_number == 2
    assert server.requests[0]["cursor"] is None
    assert server.requests[1]["cursor"] == "page-2"
    assert preview[0]["order_id"] == "O-1002"
    assert latest_metadata["connectorCursor"] == {
        "connectorName": "rest",
        "resourceName": "orders",
        "requestCursor": {"cursor": "page-2"},
        "nextCursor": None,
    }


def test_rest_cursor_not_advanced_when_dataset_commit_fails(tmp_path) -> None:
    with MockRestServer() as server:
        dependencies = create_local_core_dependencies(storage_root=tmp_path / "flite")
        core = FoundryLiteCore(dependencies=replace(dependencies, connector_adapter=RestPullConnectorAdapter()))
        failing_core = FoundryLiteCore(
            dependencies=replace(
                dependencies,
                compute_adapter=_ExplodingRowsComputeAdapter(),
                connector_adapter=RestPullConnectorAdapter(),
            )
        )
        ctx = demo_admin_context()
        rest = RestSourceConfig(base_url=server.base_url, resource_path="/orders", allow_private_network=True)
        core.ensure_dataset("raw.rest_orders", ctx=ctx, primary_key=["order_id"])

        core.sync_connector_snapshot(
            "raw.rest_orders",
            connector_name="rest",
            resource_name="orders",
            ctx=ctx,
            rest=rest,
        )
        with pytest.raises(ValidationFailed, match="connector snapshot sync failed"):
            failing_core.sync_connector_snapshot(
                "raw.rest_orders",
                connector_name="rest",
                resource_name="orders",
                ctx=ctx,
                rest=rest,
            )
        retry = core.sync_connector_snapshot(
            "raw.rest_orders",
            connector_name="rest",
            resource_name="orders",
            ctx=ctx,
            rest=rest,
        )

    latest_metadata = _transaction_metadata(core, retry.transaction_id)
    assert [request["cursor"] for request in server.requests] == [None, "page-2", "page-2"]
    assert latest_metadata["connectorCursor"]["requestCursor"] == {"cursor": "page-2"}
    assert latest_metadata["connectorCursor"]["nextCursor"] is None


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
                rest=RestSourceConfig(
                    base_url=server.base_url,
                    resource_path="/rate-limit",
                    allow_private_network=True,
                ),
            )

    failed_runs = core.query_runs(ctx=ctx, run_type="sync", status="FAILED")["syncRuns"]
    assert failed_runs[0]["source_type"] == "connector.rest"
    assert failed_runs[0]["error"]["type"] == "ADAPTER_FAILURE"
    assert failed_runs[0]["error"]["adapterFailure"]["kind"] == "rate_limited"
    assert failed_runs[0]["error"]["adapterFailure"]["retryable"] is True


def _core_with_rest_connector(tmp_path) -> FoundryLiteCore:
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "flite")
    return FoundryLiteCore(dependencies=replace(dependencies, connector_adapter=RestPullConnectorAdapter()))


def _transaction_metadata(core: FoundryLiteCore, transaction_id: str) -> dict[str, object]:
    with core.engine.begin() as conn:
        row = (
            conn.execute(select(db.dataset_transactions).where(db.dataset_transactions.c.id == transaction_id))
            .mappings()
            .one()
        )
        return dict(row["metadata"])


class _ExplodingRowsComputeAdapter(DuckDBComputeAdapter):
    def rows_to_parquet(self, *_args: object, **_kwargs: object) -> None:
        raise RuntimeError("connector snapshot parquet write failed")
