from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

DEFAULT_TENANT_ID = "tenant-demo"
DEFAULT_ACTOR_USER_ID = "user-demo"


@dataclass(frozen=True)
class RequestContext:
    tenant_id: str = DEFAULT_TENANT_ID
    actor_user_id: str = DEFAULT_ACTOR_USER_ID
    request_id: str = field(default_factory=lambda: f"req-{uuid4()}")
    roles: tuple[str, ...] = ("admin", "data_engineer", "ops_manager", "finance")

    def has_role(self, role: str) -> bool:
        return role in self.roles
