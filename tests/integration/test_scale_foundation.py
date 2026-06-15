from __future__ import annotations

from pathlib import Path

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies


@pytest.mark.integration_scenario("connector_sync")
def test_dataset_commit_public_api_survives_fake_storage_swap(tmp_path: Path) -> None:
    dependencies = create_local_core_dependencies(
        storage_root=tmp_path / "fake-runtime",
        adapter_profile="fake-storage",
    )
    core = FoundryLite(dependencies=dependencies)
    ctx = demo_admin_context()

    core.datasets.ensure("raw.crm_customers", ctx=ctx, primary_key=["customer_id"])
    commit = core.datasets.upload_csv("raw.crm_customers", "examples/supply-chain-demo/data/customers.csv", ctx=ctx)
    preview = core.datasets.preview("raw.crm_customers", ctx=ctx, limit=1)
    inspected = core.datasets.inspect("raw.crm_customers", ctx=ctx)
    sync_runs = core.operations.list_runs(ctx=ctx)["syncRuns"]

    assert commit.row_count > 0
    assert preview[0]["customer_id"] == "C-100"
    assert inspected["manifest"]["storage_profile"] == "fake-storage"
    assert inspected["manifest"]["files"][0]["uri"].startswith("fake-storage://")
    assert any(run["status"] == "COMMITTED" and run["committed_version_id"] == commit.version_id for run in sync_runs)
    assert dependencies.connector_adapter.profile_name == "fake-connector"
    assert dependencies.search_adapter.profile_name == "fake-search"
    assert dependencies.stream_adapter.profile_name == "fake-stream"
    assert dependencies.workflow_adapter.profile_name == "fake-workflow"
