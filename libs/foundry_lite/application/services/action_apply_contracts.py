"""Shared contracts for the Action apply orchestration boundary."""

from foundry_lite.application.action_types import ActionApplyCommand, ActionApplyOutcome, ActionApplyResponse
from foundry_lite.application.ports import (
    ActionRunRecord,
    ActionRunRow,
    ActionTypeRow,
    ObjectRecordRow,
    OsdkResourceOperation,
    TransactionContext,
)
from foundry_lite.application.services.action_protocols import ActionOsdkScopeBoundary
from foundry_lite.domain.action_runtime.edit_plan import EditPlan
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    ConflictDetected,
    InvariantViolation,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)

__all__ = [
    "ActionApplyCommand",
    "ActionApplyOutcome",
    "ActionApplyResponse",
    "ActionOsdkScopeBoundary",
    "ActionRunRecord",
    "ActionRunRow",
    "ActionTypeRow",
    "ConflictDetected",
    "EditPlan",
    "InvariantViolation",
    "NotFound",
    "ObjectRecordRow",
    "OsdkResourceOperation",
    "PermissionDenied",
    "RequestContext",
    "TransactionContext",
    "ValidationFailed",
]
