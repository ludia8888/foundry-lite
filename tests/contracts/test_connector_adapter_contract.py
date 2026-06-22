from __future__ import annotations

import pytest
from foundry_lite.application.ports.connector_adapter import (
    ConnectorAdapter,
    ConnectorSnapshot,
    ConnectorSnapshotRequest,
)
from foundry_lite.infrastructure.adapters import FakeConnectorAdapter, LocalConnectorAdapter


@pytest.fixture(params=[LocalConnectorAdapter, FakeConnectorAdapter])
def adapter(request: pytest.FixtureRequest) -> ConnectorAdapter:
    adapter_type = request.param
    return adapter_type(
        {
            ("tenant-demo", "postgres", "orders"): ConnectorSnapshot(
                connector_name="postgres",
                resource_name="orders",
                rows=({"order_id": "O-1001", "status": "PENDING"},),
                schema={"columns": ["order_id", "status"]},
                cursor={"lsn": "42"},
                source_watermark="2026-06-12T00:00:00Z",
            ),
            ("tenant-other", "postgres", "orders"): ConnectorSnapshot(
                connector_name="postgres",
                resource_name="orders",
                rows=({"order_id": "O-2001", "status": "SHIPPED"},),
                schema={"columns": ["order_id", "status"]},
                cursor={"lsn": "84"},
                source_watermark="2026-06-13T00:00:00Z",
            ),
        }
    )


def test_connector_adapter_contract_returns_configured_snapshot(adapter: ConnectorAdapter) -> None:
    snapshot = adapter.snapshot(
        ConnectorSnapshotRequest(
            connector_name="postgres",
            resource_name="orders",
            tenant_id="tenant-demo",
            request_id="req-connector-1",
        )
    )

    assert snapshot.connector_name == "postgres"
    assert snapshot.resource_name == "orders"
    assert snapshot.rows[0]["order_id"] == "O-1001"
    assert snapshot.cursor == {"lsn": "42"}


def test_connector_adapter_contract_scopes_snapshots_by_tenant(adapter: ConnectorAdapter) -> None:
    tenant_a = adapter.snapshot(
        ConnectorSnapshotRequest(
            connector_name="postgres",
            resource_name="orders",
            tenant_id="tenant-demo",
            request_id="req-tenant-a",
        )
    )
    tenant_b = adapter.snapshot(
        ConnectorSnapshotRequest(
            connector_name="postgres",
            resource_name="orders",
            tenant_id="tenant-other",
            request_id="req-tenant-b",
        )
    )

    assert tenant_a.rows == ({"order_id": "O-1001", "status": "PENDING"},)
    assert tenant_b.rows == ({"order_id": "O-2001", "status": "SHIPPED"},)
    assert tenant_b.cursor == {"lsn": "84"}


def test_connector_adapter_contract_returns_empty_missing_snapshot(adapter: ConnectorAdapter) -> None:
    snapshot = adapter.snapshot(
        ConnectorSnapshotRequest(
            connector_name="postgres",
            resource_name="customers",
            tenant_id="tenant-demo",
            request_id="req-connector-2",
            cursor={"lsn": "41"},
        )
    )

    assert snapshot.rows == ()
    assert snapshot.schema == {"columns": []}
    assert snapshot.cursor == {"lsn": "41"}
    assert snapshot.source_watermark == "req-connector-2"
