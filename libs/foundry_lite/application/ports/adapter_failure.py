"""Application port contract for adapter failure."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from foundry_lite.domain.errors import FoundryLiteError

AdapterFailureKind = Literal[
    "validation",
    "authentication",
    "authorization",
    "rate_limited",
    "timeout",
    "unavailable",
    "conflict",
    "unsupported",
    "not_found",
    "unknown",
]


def _empty_details() -> Mapping[str, object]:
    return {}


@dataclass(frozen=True)
class AdapterFailureMode:
    """One failure mode a concrete adapter promises to report consistently."""

    operation: str
    kind: AdapterFailureKind
    is_retryable: bool
    operator_message: str
    timeout_seconds: int | None = None
    has_required_idempotency_key: bool = False


@dataclass(frozen=True)
class AdapterFailureContract:
    """Adapter-level failure taxonomy used by gates, operations UI, and future scale adapters."""

    adapter_profile: str
    modes: tuple[AdapterFailureMode, ...]


@dataclass(frozen=True)
class AdapterFailure:
    """Concrete adapter failure payload stored in run/audit error metadata."""

    adapter_profile: str
    operation: str
    kind: AdapterFailureKind
    is_retryable: bool
    operator_message: str
    timeout_seconds: int | None = None
    idempotency_key: str | None = None
    details: Mapping[str, object] = field(default_factory=_empty_details)

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "adapterProfile": self.adapter_profile,
            "operation": self.operation,
            "kind": self.kind,
            "retryable": self.is_retryable,
            "operatorMessage": self.operator_message,
            "timeoutSeconds": self.timeout_seconds,
            "idempotencyKey": self.idempotency_key,
            "details": dict(self.details),
        }
        return payload


class AdapterError(FoundryLiteError):
    """Typed adapter failure with stable retry/timeout/idempotency semantics."""

    code = "ADAPTER_FAILURE"

    def __init__(self, failure: AdapterFailure) -> None:
        super().__init__(failure.operator_message, details={"adapterFailure": failure.to_payload()})
        self.failure = failure


def adapter_failure_payload(exc: AdapterError) -> dict[str, object]:
    return {
        "type": exc.code,
        "message": exc.message,
        "details": exc.details,
        "adapterFailure": exc.failure.to_payload(),
    }
