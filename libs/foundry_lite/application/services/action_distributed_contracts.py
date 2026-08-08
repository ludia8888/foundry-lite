"""Dependency-light imports for the durable Action worker service."""

from foundry_lite.application.action_async_execution_types import (
    ActionAsyncRunRecord,
    ActionAsyncRunRow,
    ActionRunEventRecord,
    ActionStepAttemptClaim,
    ActionStepAttemptRow,
)
from foundry_lite.application.ports import (
    ActionRepository,
    ActionTypeRow,
    StatusTransition,
    TransactionContext,
    TransactionManager,
)
from foundry_lite.application.ports.action_execution_repository import ActionExecutionRepository
from foundry_lite.application.ports.action_function_executor import (
    ActionFunctionExecutionRequest,
    ActionFunctionExecutionResult,
    ActionFunctionExecutor,
)
from foundry_lite.application.ports.action_run_orchestrator import (
    ActionRunDispatchRequest,
    ActionRunOrchestrator,
    ActionRunRetryableFailure,
)
from foundry_lite.application.ports.metadata_repository import MetadataRepository
from foundry_lite.application.services.action_edit_plan_results import ActionEditPlanResult
from foundry_lite.application.services.action_protocols import (
    ActionObjectIndexer,
    ActionObjectRecordLookup,
    ActionOntologyLookup,
    ActionOsdkScopeBoundary,
    ActionPlanningBoundary,
    ActionRuntimeBoundary,
)
from foundry_lite.application.services.ontology_lookup_service import OntologyLookupService
from foundry_lite.domain.action_runtime.action_contract import (
    ActionDefinitionV3,
    action_contract_fingerprint,
    compile_action_contract,
    compile_action_contract_snapshot,
)
from foundry_lite.domain.action_runtime.edit_plan import EditPlan
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, FoundryLiteError, InvariantViolation

__all__ = [
    "ActionAsyncRunRow",
    "ActionAsyncRunRecord",
    "ActionDefinitionV3",
    "ActionExecutionRepository",
    "ActionEditPlanResult",
    "ActionFunctionExecutionResult",
    "ActionFunctionExecutionRequest",
    "ActionFunctionExecutor",
    "ActionObjectIndexer",
    "ActionObjectRecordLookup",
    "ActionOntologyLookup",
    "ActionOsdkScopeBoundary",
    "ActionPlanningBoundary",
    "ActionRepository",
    "ActionRunDispatchRequest",
    "ActionRunEventRecord",
    "ActionRunRetryableFailure",
    "ActionRunOrchestrator",
    "ActionRuntimeBoundary",
    "ActionStepAttemptRow",
    "ActionStepAttemptClaim",
    "ActionTypeRow",
    "ConflictDetected",
    "EditPlan",
    "FoundryLiteError",
    "InvariantViolation",
    "MetadataRepository",
    "OntologyLookupService",
    "RequestContext",
    "TransactionContext",
    "TransactionManager",
    "StatusTransition",
    "action_contract_fingerprint",
    "compile_action_contract",
    "compile_action_contract_snapshot",
]
