"""Explicit boundary for mutations whose durable outcome is known but projection failed."""

from __future__ import annotations

from collections.abc import Callable


class GovernedReleaseMutationOutcomeUnknown(Exception):
    """Signal that a mutation returned successfully before response projection failed."""

    operation: str
    original: Exception

    def __init__(self, operation: str, original: Exception) -> None:
        super().__init__(f"{operation} committed, but its release projection could not be confirmed")
        self.operation = operation
        self.original = original


def project_confirmed_mutation(
    operation: str,
    projection: Callable[[], dict[str, object]],
) -> dict[str, object]:
    """Project a known mutation result without misclassifying pre-mutation failures."""

    try:
        return projection()
    except Exception as exc:
        raise GovernedReleaseMutationOutcomeUnknown(operation, exc) from exc


__all__ = ["GovernedReleaseMutationOutcomeUnknown", "project_confirmed_mutation"]
