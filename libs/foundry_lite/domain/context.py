"""Domain-layer types and rules for context."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal
from uuid import uuid4

DEFAULT_TENANT_ID = "tenant-demo"
DEFAULT_ACTOR_USER_ID = "user-demo"
DEFAULT_ROLES = ("viewer",)
DEMO_ADMIN_ROLES = ("admin", "data_engineer", "ops_manager", "finance")


@dataclass(frozen=True)
class RequestContext:
    tenant_id: str = DEFAULT_TENANT_ID
    actor_user_id: str = DEFAULT_ACTOR_USER_ID
    request_id: str = field(default_factory=lambda: f"req-{uuid4()}")
    roles: tuple[str, ...] = DEFAULT_ROLES
    application_id: str | None = None
    client_id: str | None = None
    token_scopes: tuple[str, ...] = ()
    oauth_session_id: str | None = None
    oauth_session_hash: str | None = None
    oauth_session_authority: Literal["local", "issuer"] | None = None
    authorization_server_issuer: str | None = None
    oauth_grant_type: Literal["authorization_code"] | None = None
    oauth_resource: str | None = None
    oauth_token_issued_at: int | None = None
    oauth_token_expires_at: int | None = None
    is_human_oauth: bool | None = None
    user_attributes: Mapping[str, object] = field(default_factory=dict[str, object])
    originating_service_principal_id: str | None = None
    originating_mcp_review_id: str | None = None
    governed_release_run_id: str | None = None
    governed_release_binding_hash: str | None = None
    governed_release_session_id: str | None = None
    governed_release_execution_attempt: int | None = None

    def has_role(self, role: str) -> bool:
        return role in self.roles


def demo_admin_context() -> RequestContext:
    return RequestContext(actor_user_id="user-demo-admin", roles=DEMO_ADMIN_ROLES)
