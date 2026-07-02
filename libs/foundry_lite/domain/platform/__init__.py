"""Platform-wide domain rule kernel."""

from foundry_lite.domain.platform.evidence import redact_evidence
from foundry_lite.domain.platform.idempotency import IdempotencyDecision, decide_idempotency
from foundry_lite.domain.platform.scopes import is_scope_allowed, resource_scope
from foundry_lite.domain.platform.state import (
    ensure_nonterminal_transition,
    is_retryable_terminal_state,
    is_terminal_state,
)
from foundry_lite.domain.platform.traffic import decide_write_traffic

__all__ = [
    "IdempotencyDecision",
    "decide_idempotency",
    "decide_write_traffic",
    "ensure_nonterminal_transition",
    "is_retryable_terminal_state",
    "is_scope_allowed",
    "is_terminal_state",
    "redact_evidence",
    "resource_scope",
]
