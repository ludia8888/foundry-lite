from __future__ import annotations


class FoundryLiteError(Exception):
    code = "FOUNDRY_LITE_ERROR"

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ValidationFailed(FoundryLiteError):
    code = "VALIDATION_FAILED"


class DatasetCommitBlocked(ValidationFailed):
    code = "VALIDATION_FAILED"


class PermissionDenied(FoundryLiteError):
    code = "PERMISSION_DENIED"


class NotFound(FoundryLiteError):
    code = "NOT_FOUND"


class ConflictDetected(FoundryLiteError):
    code = "CONFLICT"


class InvariantViolation(FoundryLiteError):
    code = "INVARIANT_VIOLATION"


class ExternalSystemError(FoundryLiteError):
    code = "EXTERNAL_SYSTEM_ERROR"


class ExternalOutcomeUnknown(ExternalSystemError):
    code = "EXTERNAL_OUTCOME_UNKNOWN"


class ExternalCompensationRequired(ExternalSystemError):
    code = "EXTERNAL_COMPENSATION_REQUIRED"
