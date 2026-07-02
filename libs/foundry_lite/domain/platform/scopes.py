"""Pure resource-scope vocabulary and matching rules."""

from __future__ import annotations

from collections.abc import Sequence

from foundry_lite.domain.errors import ValidationFailed

RESOURCE_TYPES = frozenset({"object", "action", "link", "function"})
RESOURCE_OPERATIONS = frozenset({"read", "subscribe", "validate", "execute"})
RESOURCE_OPERATIONS_BY_TYPE = {
    "object": frozenset({"read", "subscribe"}),
    "action": frozenset({"validate", "execute"}),
    "link": frozenset({"read"}),
    "function": frozenset({"execute"}),
}
WILDCARD_SCOPE = "osdk:*"


def resource_scope(resource_type: str, resource_api_name: str, operation: str) -> str:
    if resource_type not in RESOURCE_TYPES or operation not in RESOURCE_OPERATIONS_BY_TYPE[resource_type]:
        raise ValidationFailed("unsupported OSDK resource scope request")
    if not resource_api_name:
        raise ValidationFailed("OSDK resource API name is required")
    return f"osdk:{resource_type}:{resource_api_name}:{operation}"


def validate_resource_scope(scope: str, resource_type: str, resource_api_name: str) -> None:
    expected_prefix = f"osdk:{resource_type}:{resource_api_name}:"
    allowed_operations = RESOURCE_OPERATIONS_BY_TYPE.get(resource_type, frozenset())
    if not scope.startswith(expected_prefix) or scope.rsplit(":", 1)[-1] not in allowed_operations:
        raise ValidationFailed("OSDK application resource scope does not match its resource")


def is_scope_allowed(expected_scope: str, token_scopes: Sequence[str], granted_scopes: Sequence[str]) -> bool:
    token_set = set(token_scopes)
    if expected_scope not in token_set and WILDCARD_SCOPE not in token_set:
        return False
    return expected_scope in set(granted_scopes)
