from __future__ import annotations

import pytest
from foundry_lite.domain.context import RequestContext, demo_admin_context

from scripts.operations.private_browser_app_access import bind_owner


def _bundle(foundry, monkeypatch, role="domain_actor_reception"):
    ctx = demo_admin_context()
    project = foundry.resources.create_project(display_name="Private hospital", idempotency_key="private", ctx=ctx)
    app = foundry.developer_console.create_osdk_application(
        app_api_name="PrivateHospital",
        display_name="Private hospital",
        client_id="private-browser",
        resources=(),
        idempotency_key="private",
        ctx=ctx,
    )
    bundle = {
        "project": project["project"],
        "domainOsBlueprint": {"actorRoles": [{"role": role}]},
        "operatingApplication": {"status": "awaiting_release"},
    }
    monkeypatch.setattr(type(foundry.aip), "get_operating_pilot_application", lambda *_args, **_kwargs: bundle)
    return ctx, app["application"]["id"], project["project"]["id"]


def test_private_owner_binding_is_scoped_and_replay_does_not_duplicate_mapping(foundry, monkeypatch) -> None:
    ctx, app_id, project_id = _bundle(foundry, monkeypatch)
    first = bind_owner(foundry, app_id, "private-browser", "owner", ctx)
    second = bind_owner(foundry, app_id, "private-browser", "owner", ctx)
    assert first == second
    grants = foundry.resources.list_project_grants(project_id, ctx=ctx)
    owner_grants = [row for row in grants["grants"] if row["principalId"] == "owner"]
    assert len(owner_grants) == 1
    assert owner_grants[0]["role"] == "viewer"
    owner = RequestContext(tenant_id=ctx.tenant_id, actor_user_id="owner", roles=("viewer",))
    resources = foundry.resources.list_resources(project_id=project_id, ctx=owner)["items"]
    mappings = [row for row in resources if row["resourceType"] == "business_application_role_mapping"]
    assert len(mappings) == 1
    assert mappings[0]["metadata"]["roles"] == ["domain_actor_reception"]
    assert first["hostedHumanExecutionProven"] is False


def test_wrong_client_and_global_roles_fail_before_owner_grants(foundry, monkeypatch) -> None:
    ctx, app_id, project_id = _bundle(foundry, monkeypatch, role="admin")
    with pytest.raises(ValueError, match="client_mismatch"):
        bind_owner(foundry, app_id, "other-client", "owner", ctx)
    with pytest.raises(ValueError, match="domain_scoped_roles"):
        bind_owner(foundry, app_id, "private-browser", "owner", ctx)
    assert all(
        row["principalId"] != "owner" for row in foundry.resources.list_project_grants(project_id, ctx=ctx)["grants"]
    )
