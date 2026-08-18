from __future__ import annotations

from scripts.operations.verify_macmini_mcp_tenant_quota import _validated


def test_live_quota_receipt_requires_tenant_denial_and_neighbor_allowance() -> None:
    payload = {
        "schemaVersion": 1,
        "status": "passed",
        "tenantAQuotaDenied": True,
        "tenantBStillAllowed": True,
        "durableDenialAuditOutboxRequested": True,
        "rawIdentityStored": False,
    }

    assert _validated(payload) == payload
