from __future__ import annotations

from pathlib import Path

import pytest
from foundry_lite.application.ports import AdapterFailureContract
from foundry_lite.infrastructure.adapters import (
    DebeziumPostgresSourceConfig,
    DebeziumPostgresStreamAdapter,
    DuckDBComputeAdapter,
    FakeComputeAdapter,
    FakeConnectorAdapter,
    FakeDatasetStorageAdapter,
    FakeSearchAdapter,
    FakeStreamAdapter,
    FakeWorkflowAdapter,
    KafkaStreamAdapter,
    KafkaStreamAdapterConfig,
    KafkaStreamSubscription,
    LocalConnectorAdapter,
    LocalDatasetStorageAdapter,
    LocalSearchAdapter,
    LocalStreamAdapter,
    LocalWorkflowAdapter,
    OpenSearchAdapter,
    OpenSearchAdapterConfig,
    RestPullConnectorAdapter,
)
from foundry_lite.infrastructure.auth import DemoAuthProvider, HeaderTrustAuthProvider


@pytest.fixture(
    params=[
        "duckdb",
        "fake-compute",
        "local-storage",
        "fake-storage",
        "local-workflow",
        "fake-workflow",
        "local-stream",
        "fake-stream",
        "kafka-stream",
        "debezium-postgres-stream",
        "local-search",
        "fake-search",
        "opensearch",
        "local-connector",
        "fake-connector",
        "rest-pull-connector",
        "header-trust-auth",
        "demo-auth",
    ]
)
def failure_contract(request: pytest.FixtureRequest, tmp_path: Path) -> AdapterFailureContract:
    return _adapter_failure_contract(str(request.param), tmp_path)


def test_adapter_failure_contract_declares_operator_safe_modes(
    failure_contract: AdapterFailureContract,
) -> None:
    assert failure_contract.adapter_profile
    assert failure_contract.modes
    for mode in failure_contract.modes:
        assert mode.operation
        assert mode.operator_message
        assert isinstance(mode.is_retryable, bool)
        if mode.timeout_seconds is not None:
            assert mode.timeout_seconds > 0


def test_adapter_failure_contract_keeps_timeout_retryable(
    failure_contract: AdapterFailureContract,
) -> None:
    timeout_modes = [mode for mode in failure_contract.modes if mode.kind == "timeout"]

    assert all(mode.is_retryable for mode in timeout_modes)


def _adapter_failure_contract(profile: str, tmp_path: Path) -> AdapterFailureContract:
    adapters = {
        "duckdb": DuckDBComputeAdapter(),
        "fake-compute": FakeComputeAdapter(),
        "local-storage": LocalDatasetStorageAdapter(tmp_path / "local"),
        "fake-storage": FakeDatasetStorageAdapter(tmp_path / "fake"),
        "local-workflow": LocalWorkflowAdapter(),
        "fake-workflow": FakeWorkflowAdapter(),
        "local-stream": LocalStreamAdapter(),
        "fake-stream": FakeStreamAdapter(),
        "kafka-stream": KafkaStreamAdapter(
            KafkaStreamAdapterConfig(
                bootstrap_servers="redpanda:9092",
                subscriptions=(KafkaStreamSubscription("shipments", "shipment_events"),),
            )
        ),
        "debezium-postgres-stream": DebeziumPostgresStreamAdapter(
            FakeStreamAdapter(),
            DebeziumPostgresSourceConfig(primary_key=("order_id",)),
        ),
        "local-search": LocalSearchAdapter(),
        "fake-search": FakeSearchAdapter(),
        "opensearch": OpenSearchAdapter(OpenSearchAdapterConfig(endpoint="http://search:9200")),
        "local-connector": LocalConnectorAdapter(),
        "fake-connector": FakeConnectorAdapter(),
        "rest-pull-connector": RestPullConnectorAdapter(),
        "header-trust-auth": HeaderTrustAuthProvider(),
        "demo-auth": DemoAuthProvider(),
    }
    return adapters[profile].failure_contract()
