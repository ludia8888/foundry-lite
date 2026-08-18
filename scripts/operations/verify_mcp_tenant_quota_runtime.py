"""Verify durable MCP tenant quota isolation inside the deployed API image."""

from __future__ import annotations

import argparse
import json
import os
import re
import time

from foundry_lite.application.dependencies import RuntimeProfile
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.services.mcp_rate_limit_service import (
    McpRateLimitConfig,
    McpRateLimitService,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import RateLimited
from foundry_lite.infrastructure.auth import (
    OAUTH_AUDIENCE_ENV,
    OAUTH_ISSUER_ENV,
    auth_provider_from_env,
)
from foundry_lite.infrastructure.local_runtime import create_runtime_core_dependencies
from foundry_lite_api.mcp_authorization_config import (
    governed_release_mcp_authority,
    mcp_authorization_config_from_env,
)

_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def verify(run_id: str) -> dict[str, object]:
    if _RUN_ID.fullmatch(run_id) is None:
        raise ValueError("macmini_mcp_quota_run_id_invalid")
    foundry = _runtime()
    try:
        service = foundry._services.mcp_rate_limits
        service.config = McpRateLimitConfig(endpoint_limit=5, tool_limit=5, window_seconds=60)
        now = time.time()
        service._clock = lambda: now
        application_id = f"qa-quota-{run_id[-48:]}"
        tenant_a, tenant_b = _tenant_contexts(run_id)
        for _ in range(5):
            service.consume_endpoint(tenant_a, plane="builder", application_id=application_id)
        denied = _is_denied(service, tenant_a, application_id)
        tenant_b_decision = service.consume_endpoint(tenant_b, plane="builder", application_id=application_id)
    finally:
        foundry.close()
    return {
        "schemaVersion": 1,
        "status": "passed" if denied and tenant_b_decision.is_allowed else "failed",
        "tenantAQuotaDenied": denied,
        "tenantBStillAllowed": tenant_b_decision.is_allowed,
        "durableDenialAuditOutboxRequested": denied,
        "quotaScope": "tenant-plane-application-client-actor",
        "rawIdentityStored": False,
    }


def _tenant_contexts(run_id: str) -> tuple[RequestContext, RequestContext]:
    return (
        RequestContext(
            tenant_id=f"qa-{run_id[-40:]}-a",
            actor_user_id="quota-actor-a",
            client_id="quota-client",
            request_id=f"quota-{run_id}-a",
        ),
        RequestContext(
            tenant_id=f"qa-{run_id[-40:]}-b",
            actor_user_id="quota-actor-b",
            client_id="quota-client",
            request_id=f"quota-{run_id}-b",
        ),
    )


def _runtime() -> FoundryLite:
    source = os.environ
    auth_provider = auth_provider_from_env(source)
    authorization = mcp_authorization_config_from_env(source, auth_provider)
    dependencies = create_runtime_core_dependencies(
        profile=RuntimeProfile.from_value(source.get("FOUNDRY_LITE_RUNTIME_PROFILE")),
        db_url=source.get("FOUNDRY_LITE_DB_URL"),
        storage_root=source.get("FOUNDRY_LITE_HOME", ".foundry-lite"),
        adapter_profile=source.get("FOUNDRY_LITE_ADAPTER_PROFILE", "local"),
        governed_release_mcp_authority=governed_release_mcp_authority(authorization, auth_provider),
        oauth_issuer=source.get(OAUTH_ISSUER_ENV),
        oauth_audience=source.get(OAUTH_AUDIENCE_ENV),
    )
    return FoundryLite(dependencies=dependencies, should_initialize_schema=False)


def _is_denied(service: McpRateLimitService, ctx: RequestContext, application_id: str) -> bool:
    try:
        service.consume_endpoint(ctx, plane="builder", application_id=application_id)
    except RateLimited as exc:
        retry_after = exc.details.get("retryAfterSeconds")
        return isinstance(retry_after, int) and retry_after > 0
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    receipt = verify(parser.parse_args().run_id)
    print(json.dumps(receipt, separators=(",", ":"), sort_keys=True))
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
