from __future__ import annotations

import os
from pathlib import Path

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies


# Anchor every demo asset path to the real repository root so the test suite is
# independent of pytest cwd. Three sources, in priority order:
#   1. FOUNDRY_LITE_REPO_ROOT env var (explicit override).
#   2. Conftest path heuristic, adjusted when the file lives under mutants/
#      because mutmut copies the tree into mutants/ and runs pytest from there.
#   3. Plain parents[1] for the normal layout.
def _repo_root() -> Path:
    override = os.environ.get("FOUNDRY_LITE_REPO_ROOT")
    if override:
        return Path(override)
    here = Path(__file__).resolve()
    candidate = here.parents[1]
    if candidate.name == "mutants":
        return candidate.parent
    return candidate


REPO_ROOT = _repo_root()
DEMO_ROOT = REPO_ROOT / "examples" / "supply-chain-demo"


def _configure_xdist_foundry_home(worker_id: str | None) -> None:
    if not worker_id or os.environ.get("FOUNDRY_LITE_HOME"):
        return

    os.environ["FOUNDRY_LITE_HOME"] = str(REPO_ROOT / ".foundry-lite-pytest-xdist" / f"{worker_id}-{os.getpid()}")


def pytest_configure(config: pytest.Config) -> None:
    worker_input = getattr(config, "workerinput", None)
    worker_id = worker_input.get("workerid") if isinstance(worker_input, dict) else None
    _configure_xdist_foundry_home(worker_id)
    os.environ.setdefault("FOUNDRY_LITE_SECRET_AIP_PROMPT_ARTIFACT_ENCRYPTION_KEY", "pytest-prompt-artifact-key")


@pytest.fixture
def foundry(tmp_path: Path) -> FoundryLite:
    return FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))


def prepare_indexed_demo(foundry: FoundryLite) -> RequestContext:
    ctx = demo_admin_context()
    foundry.demo.seed_files()
    foundry.datasets.ensure("raw.erp_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("raw.crm_customers", ctx=ctx, primary_key=["customer_id"])
    foundry.datasets.ensure("clean.orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("clean.order_finance", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("clean.customers", ctx=ctx, primary_key=["customer_id"])
    foundry.datasets.ensure("ops.action_log", ctx=ctx, primary_key=["action_run_id"])
    foundry.datasets.ensure("ops.order_current", ctx=ctx, primary_key=["orderId"])
    foundry.demo.register_transforms(ctx)
    foundry.datasets.upload_csv("raw.erp_orders", str(DEMO_ROOT / "data" / "orders.csv"), ctx=ctx)
    foundry.datasets.upload_csv("raw.crm_customers", str(DEMO_ROOT / "data" / "customers.csv"), ctx=ctx)
    foundry.transforms.run("clean_orders", ctx=ctx)
    foundry.transforms.run("clean_order_finance", ctx=ctx)
    foundry.transforms.run("clean_customers", ctx=ctx)
    foundry.ontology.apply(str(DEMO_ROOT / "ontology" / "order-customer.yaml"), ctx=ctx)
    foundry.objects.reindex("Order", ctx=ctx)
    foundry.objects.reindex("Customer", ctx=ctx)
    return ctx
