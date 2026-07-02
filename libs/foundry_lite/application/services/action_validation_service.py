"""Action validation use case service."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.action_types import ActionValidationResponse
from foundry_lite.application.services.action_helpers import (
    action_target_record_error,
    require_action_target_api_name,
)
from foundry_lite.application.services.action_permission_guards import require_action_permission
from foundry_lite.application.services.action_protocols import ActionOsdkScopeBoundary
from foundry_lite.application.services.action_validation import action_validation_response
from foundry_lite.application.services.action_workflow import (
    ActionObjectRecordLookup,
    ActionOntologyLookup,
    ActionRuntimeBoundary,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext


class ActionValidationService(CoreService):
    """Validate an action request without mutating object state."""

    required_dependencies = ("engine", "policy")
    required_collaborators = (
        "object_records_service",
        "ontology_service",
        "osdk_application_service",
        "runtime_service",
    )
    object_records_service: ActionObjectRecordLookup
    ontology_service: ActionOntologyLookup
    osdk_application_service: ActionOsdkScopeBoundary
    runtime_service: ActionRuntimeBoundary

    def validate_action(
        self,
        action_api_name: str,
        *,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        ctx: RequestContext | None = None,
    ) -> ActionValidationResponse:
        ctx = ctx or RequestContext()
        require_action_permission(
            self.engine, self.policy, self.runtime_service, ctx, action_api_name, action="validate"
        )
        self._require_action_scope(ctx, action_api_name)
        with self.engine.begin() as conn:
            action_type = self.ontology_service._active_action_type(conn, ctx, action_api_name)
            require_action_target_api_name(action_type, object_type)
            record = self.object_records_service._object_record(conn, ctx, object_type, object_id)
            if record is not None and (error := action_target_record_error(action_type, record)) is not None:
                raise error
            return action_validation_response(action_type, record, expected_object_version, params)

    def _require_action_scope(self, ctx: RequestContext, action_api_name: str) -> None:
        self.osdk_application_service.require_resource_scope(
            ctx,
            resource_type="action",
            resource_api_name=action_api_name,
            operation="validate",
        )
