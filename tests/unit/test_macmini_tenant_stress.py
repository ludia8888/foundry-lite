from __future__ import annotations

from scripts.operations.run_macmini_tenant_stress import _percentile, _tenant_ids


def test_tenant_stress_uses_distinct_run_scoped_tenants() -> None:
    tenant_a, tenant_b = _tenant_ids("enterprise-ABC_123")

    assert tenant_a != tenant_b
    assert tenant_a.endswith("-a")
    assert tenant_b.endswith("-b")


def test_tenant_stress_percentile_uses_nearest_rank() -> None:
    assert _percentile(list(range(1, 101)), 0.95) == 95
