from __future__ import annotations

from dataclasses import replace

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.domain.errors import PermissionDenied


def test_app_consumer_can_read_own_definition_without_developer_or_operator_role(foundry: FoundryLite) -> None:
    admin = demo_admin_context()
    bundle = foundry.developer_console.create_osdk_application(
        app_api_name="PrivateHospital",
        display_name="Private hospital",
        client_id="hospital-browser",
        resources=(),
        idempotency_key="private-hospital",
        ctx=admin,
    )
    app_id = bundle["application"]["id"]
    actor = RequestContext(
        tenant_id=admin.tenant_id,
        actor_user_id="invited-owner",
        roles=("viewer",),
        application_id=app_id,
        client_id="hospital-browser",
    )
    result = foundry.developer_console.get_osdk_application(app_id, ctx=actor)
    assert result["application"]["id"] == app_id
    for other in [
        replace(actor, application_id="other"),
        replace(actor, client_id="other"),
        replace(actor, tenant_id="other"),
        replace(actor, application_id=None),
    ]:
        with pytest.raises(PermissionDenied):
            foundry.developer_console.get_osdk_application(app_id, ctx=other)
    with pytest.raises(PermissionDenied):
        foundry.developer_console.list_osdk_applications(ctx=actor)
