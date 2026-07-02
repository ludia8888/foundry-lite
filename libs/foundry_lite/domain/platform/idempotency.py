"""Pure idempotency replay/conflict rules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from foundry_lite.domain.errors import ConflictDetected, ValidationFailed

IdempotencyDecision = Literal["new", "replay"]


@dataclass(frozen=True)
class StoredIdempotency:
    request_hash: str
    response: object


def require_idempotency_key(value: str, *, surface: str = "mutation") -> str:
    if not value:
        raise ValidationFailed(f"Idempotency-Key is required for {surface}")
    return value


def decide_idempotency(
    stored: StoredIdempotency | None,
    request_hash: str,
    *,
    conflict_message: str = "idempotency key reused with a different request",
) -> IdempotencyDecision:
    if stored is None:
        return "new"
    if stored.request_hash != request_hash:
        raise ConflictDetected(conflict_message)
    return "replay"
