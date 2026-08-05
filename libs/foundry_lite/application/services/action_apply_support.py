"""Pure helpers used by the Action apply orchestration service."""

from collections.abc import Mapping

from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.safe_expression import validate_action_request
from foundry_lite.application.services.action_apply_contracts import (
    ActionApplyCommand,
    ActionTypeRow,
    ConflictDetected,
    NotFound,
    ObjectRecordRow,
    RequestContext,
)
from foundry_lite.application.services.action_helpers import (
    action_command,
    action_failure_transition,
    action_replay_response,
    action_target_record_error,
    audit_idempotency_conflict,
    require_action_target_api_name,
    require_action_write_open,
    resolved_action_command,
    stable_parameter_id_generator,
)
from foundry_lite.application.services.action_interface_resolution import (
    interface_create_target_record as interface_create_target_record,
)
from foundry_lite.application.services.action_ir_compiler import canonical_action_contract
from foundry_lite.application.services.action_permission_guards import (
    require_action_permission,
    require_action_target_read,
    require_failure_injection_for_command,
    segment_mutation_denied_error,
)
from foundry_lite.application.services.object_store.row_policies import visible_record
from foundry_lite.security.policy import PolicyService


def action_request_error(
    policy: PolicyService,
    ctx: RequestContext,
    action_type: ActionTypeRow,
    record: ObjectRecordRow | None,
    command: ActionApplyCommand,
    linked_object_properties: Mapping[str, object] | None = None,
) -> Exception | None:
    """Return the pre-commit validation error without leaking hidden segment detail."""
    if record is None:
        return NotFound("target object not found")
    if (error := segment_mutation_denied_error(policy, ctx, action_type)) is not None:
        return error
    if record["object_version"] != command.expected_object_version:
        return ConflictDetected(
            "object version conflict",
            details={
                "currentObjectVersion": record["object_version"],
                "expectedObjectVersion": command.expected_object_version,
            },
        )
    return validate_action_request(
        action_type,
        record,
        command.params,
        ctx,
        generate_id=stable_parameter_id_generator(command.idempotency_key),
        linked_object_properties=linked_object_properties,
    )


__all__ = [
    "_new_id",
    "_now",
    "action_command",
    "action_failure_transition",
    "action_replay_response",
    "action_request_error",
    "action_target_record_error",
    "audit_idempotency_conflict",
    "canonical_action_contract",
    "interface_create_target_record",
    "require_action_permission",
    "require_action_target_api_name",
    "require_action_target_read",
    "require_action_write_open",
    "require_failure_injection_for_command",
    "resolved_action_command",
    "segment_mutation_denied_error",
    "stable_parameter_id_generator",
    "validate_action_request",
    "visible_record",
]
