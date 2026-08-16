"""Port for executing a pre-registered Action side-effect target."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol, cast

from foundry_lite.domain.action_runtime.action_effects import ActionEffectV3
from foundry_lite.domain.errors import InvariantViolation
from foundry_lite.domain.json_values import is_bounded_json_value

ActionEffectOutcome = Literal["delivered", "ambiguous"]


@dataclass(frozen=True, slots=True)
class ActionEffectExecutionRequest:
    """Governed, idempotent request passed to an Action effect adapter."""

    tenant_id: str
    action_run_id: str
    actor_user_id: str
    request_id: str
    idempotency_key: str
    effect: ActionEffectV3
    parameters: Mapping[str, object]
    committed_result: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class ActionEffectExecutionResult:
    """Classified remote outcome plus redacted response evidence."""

    outcome: ActionEffectOutcome
    external_execution_id: str | None
    response: Mapping[str, object]
    network_evidence: Mapping[str, object]


def require_action_effect_execution_result(value: object) -> ActionEffectExecutionResult:
    """Fail closed when a custom adapter violates its runtime result contract."""

    if not isinstance(value, ActionEffectExecutionResult):
        raise InvariantViolation("Action effect adapter returned an invalid result")
    if value.outcome not in {"delivered", "ambiguous"}:
        raise InvariantViolation("Action effect adapter returned an unknown outcome")
    if not _is_valid_external_execution_id(value.external_execution_id):
        raise InvariantViolation("Action effect adapter returned an invalid external execution id")
    if not _is_bounded_mapping(value.response) or not _is_bounded_mapping(value.network_evidence):
        raise InvariantViolation("Action effect adapter returned invalid evidence")
    return value


def _is_valid_external_execution_id(value: object) -> bool:
    return value is None or (isinstance(value, str) and bool(value))


def _is_bounded_mapping(value: object) -> bool:
    return isinstance(value, Mapping) and is_bounded_json_value(cast(Mapping[object, object], value))


class ActionEffectTransientError(RuntimeError):
    """Known-safe transient failure that may be retried by policy."""

    """A proven-safe transient failure that may use the same idempotency key."""


class ActionEffectPermanentError(RuntimeError):
    """Known permanent rejection that must not be retried."""

    """A validation, policy, permission, or provider rejection that must not retry."""


class ActionEffectExecutor(Protocol):
    """Execute one registered Action effect without owning DB state."""

    @property
    def profile_name(self) -> str:
        """Return the concrete runtime profile used for evidence."""
        ...

    def execute(self, request: ActionEffectExecutionRequest) -> ActionEffectExecutionResult:
        """Deliver one effect and classify its remote outcome."""
        ...


class UnavailableActionEffectExecutor:
    """Fail-closed adapter used when effect infrastructure is not configured."""

    profile_name = "unavailable-action-effect-executor"

    def execute(self, request: ActionEffectExecutionRequest) -> ActionEffectExecutionResult:
        """Reject every effect while preserving the configured profile in the error."""
        raise ActionEffectPermanentError(f"Action effect target is unavailable: {request.effect.target_ref}")
