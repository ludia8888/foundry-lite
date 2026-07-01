"""Domain-layer types and rules for context."""

from __future__ import annotations

from dataclasses import dataclass, field
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

    def has_role(self, role: str) -> bool:
        return role in self.roles


def demo_admin_context() -> RequestContext:
    return RequestContext(actor_user_id="user-demo-admin", roles=DEMO_ADMIN_ROLES)
