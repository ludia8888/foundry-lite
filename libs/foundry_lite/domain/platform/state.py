"""Pure terminal state transition rules."""

from __future__ import annotations

from collections.abc import Iterable

from foundry_lite.domain.errors import ConflictDetected

DEFAULT_TERMINAL_STATES = frozenset(
    {
        "succeeded",
        "failed",
        "cancelled",
        "conflict",
        "discarded",
        "resolved",
        "quarantined",
        "reconciled",
        "compensation_required",
        "outcome_unknown",
    }
)
DEFAULT_RETRYABLE_TERMINAL_STATES = frozenset({"failed", "retryable", "outcome_unknown"})


def is_terminal_state(status: str | None, terminal_states: Iterable[str] = DEFAULT_TERMINAL_STATES) -> bool:
    return status is not None and status.casefold() in {item.casefold() for item in terminal_states}


def is_retryable_terminal_state(
    status: str | None,
    retryable_states: Iterable[str] = DEFAULT_RETRYABLE_TERMINAL_STATES,
) -> bool:
    return status is not None and status.casefold() in {item.casefold() for item in retryable_states}


def ensure_nonterminal_transition(
    current_status: str | None,
    next_status: str,
    *,
    resource_id: str,
    terminal_states: Iterable[str] = DEFAULT_TERMINAL_STATES,
) -> None:
    if is_terminal_state(current_status, terminal_states):
        raise ConflictDetected(
            "terminal state cannot transition",
            details={"resource_id": resource_id, "current_status": current_status, "next_status": next_status},
        )
