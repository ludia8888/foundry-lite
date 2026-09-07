"""Bind one approved private-pilot owner through audited public use cases.

Run inside the existing API deployment's environment, never against a new DB.
This is operator provisioning, not evidence of a hosted GPT or human OAuth run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping

from foundry_lite.application.foundry import FoundryLite
from foundry_lite.domain.context import RequestContext
from foundry_lite_api.runtime import initialize_api_runtime, shutdown_api_runtime


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("private_application_definition_missing")
    return value


def _domain_roles(bundle: Mapping[str, object]) -> list[str]:
    role_rows = _mapping(bundle["domainOsBlueprint"])["actorRoles"]
    if not isinstance(role_rows, list) or not role_rows:
        raise ValueError("private_application_roles_missing")
    roles = sorted({str(_mapping(row)["role"]) for row in role_rows})
    if any(not role.startswith("domain_actor_") for role in roles):
        raise ValueError("private_application_requires_domain_scoped_roles")
    return roles


def bind_owner(
    foundry: FoundryLite, application_id: str, client_id: str, subject: str, ctx: RequestContext
) -> dict[str, object]:
    if not subject or subject == ctx.actor_user_id:
        raise ValueError("private_application_owner_required")
    bundle = foundry.aip.get_operating_pilot_application(application_id, ctx=ctx)
    app_bundle = foundry.developer_console.get_osdk_application(application_id, ctx=ctx)
    if not any(row["client_id"] == client_id and row["status"] == "active" for row in app_bundle["clients"]):
        raise ValueError("private_application_client_mismatch")
    project_id = str(_mapping(bundle["project"])["id"])
    roles = _domain_roles(bundle)
    key = "private-browser-owner:" + hashlib.sha256(f"{application_id}:{subject}".encode()).hexdigest()
    grant = foundry.resources.upsert_project_grant(
        project_id,
        principal_type="user",
        principal_id=subject,
        role="viewer",
        idempotency_key=f"{key}:project",
        ctx=ctx,
    )
    mapping = foundry.resources.register_resource(
        resource_type="business_application_role_mapping",
        display_name="비공개 시험 앱 소유자",
        project_id=project_id,
        source_surface="private_browser_owner_provisioning",
        source_ref=f"{application_id}:{subject}",
        metadata={
            "applicationId": application_id,
            "principalId": subject,
            "roles": roles,
            "mappingMode": "private_pilot_owner",
        },
        idempotency_key=f"{key}:roles",
        ctx=ctx,
    )
    return {
        "status": "access_bound",
        "applicationId": application_id,
        "subject": subject,
        "projectId": project_id,
        "projectGrant": grant,
        "roleMapping": mapping,
        "browserRoles": ["viewer"],
        "domainRoles": roles,
        "operatingApplication": bundle["operatingApplication"],
        "hostedHumanExecutionProven": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--application-id", required=True)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--subject", required=True)
    args = parser.parse_args()
    runtime = initialize_api_runtime()
    try:
        ctx = RequestContext(
            tenant_id=args.tenant_id,
            actor_user_id="private-browser-access-operator",
            roles=("admin",),
            request_id=f"private-owner-{args.application_id}",
        )
        print(json.dumps(bind_owner(runtime.foundry, args.application_id, args.client_id, args.subject, ctx)))
    finally:
        shutdown_api_runtime()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
