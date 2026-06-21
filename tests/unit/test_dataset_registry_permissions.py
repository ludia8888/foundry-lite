from __future__ import annotations

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.domain.errors import PermissionDenied


def test_dataset_find_and_existing_ensure_require_read_permission(foundry: FoundryLite) -> None:
    admin = demo_admin_context()
    anonymous = RequestContext(
        tenant_id=admin.tenant_id,
        actor_user_id="anonymous-user",
        roles=(),
        request_id="req-dataset-registry-deny",
    )
    foundry.datasets.create("raw.secret_orders", ctx=admin, primary_key=["id"])

    with pytest.raises(PermissionDenied):
        foundry.datasets.find("raw.secret_orders", ctx=anonymous)
    with pytest.raises(PermissionDenied):
        foundry.datasets.ensure("raw.secret_orders", ctx=anonymous, primary_key=["id"])
