from __future__ import annotations

from pathlib import Path

from foundry_lite.application.core import FoundryLiteCore
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies


def test_dataset_commit_public_api_survives_fake_storage_swap(tmp_path: Path) -> None:
    core = FoundryLiteCore(
        dependencies=create_local_core_dependencies(
            storage_root=tmp_path / "fake-runtime",
            adapter_profile="fake-storage",
        )
    )
    ctx = demo_admin_context()

    core.ensure_dataset("raw.crm_customers", ctx=ctx, primary_key=["customer_id"])
    commit = core.upload_csv("raw.crm_customers", "examples/supply-chain-demo/data/customers.csv", ctx=ctx)
    preview = core.preview_dataset("raw.crm_customers", ctx=ctx, limit=1)
    inspected = core.inspect_dataset("raw.crm_customers", ctx=ctx)

    assert commit.row_count > 0
    assert preview[0]["customer_id"] == "C-100"
    assert inspected["manifest"]["storage_profile"] == "fake-storage"
    assert inspected["manifest"]["files"][0]["uri"].startswith("fake-storage://")
