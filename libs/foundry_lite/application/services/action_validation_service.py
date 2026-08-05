"""Action validation use case service."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.action_types import ActionValidationResponse
from foundry_lite.application.ports import ActionTypeRow, ObjectRecordRow, TransactionContext
from foundry_lite.application.services.action_interface_resolution import (
    interface_create_target_record,
    require_interface_action_target,
)
from foundry_lite.application.services.action_ir_compiler import (
    ActionDefinitionV3,
    compile_action_definition,
)
from foundry_lite.application.services.action_media_runtime_service import ActionMediaRuntimeService
from foundry_lite.application.services.action_permission_guards import (
    require_action_permission,
    require_action_target_read,
)
from foundry_lite.application.services.action_protocols import ActionOsdkScopeBoundary
from foundry_lite.application.services.action_validation import action_validation_response
from foundry_lite.application.services.action_validation_runtime import (
    action_target_record_error,
    authorized_action_contract,
    resolve_linked_condition_values,
)
from foundry_lite.application.services.action_workflow import (
    ActionObjectRecordLookup,
    ActionOntologyLookup,
    ActionRuntimeBoundary,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.object_store.row_policies import visible_record
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import FoundryLiteError


class ActionValidationService(CoreService):
    """Validate an action request without mutating object state."""

    required_dependencies = ("engine", "policy", "object_read_repository")
    required_collaborators = (
        "object_records_service",
        "action_media_runtime_service",
        "ontology_lookup_service",
        "osdk_application_service",
        "runtime_service",
    )
    object_records_service: ActionObjectRecordLookup
    action_media_runtime_service: ActionMediaRuntimeService
    ontology_lookup_service: ActionOntologyLookup
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
        require_action_target_read(
            self.engine,
            self.policy,
            self.runtime_service,
            ctx,
            action_api_name,
            object_type,
            object_id,
            action="validate",
        )
        self._require_action_scope(ctx, action_api_name)
        with self.engine.begin() as conn:
            return self._validate_in_transaction(
                conn, ctx, action_api_name, object_type, object_id, expected_object_version, params
            )

    def _validate_in_transaction(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_api_name: str,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
    ) -> ActionValidationResponse:
        action_type = self.ontology_lookup_service._active_action_type(conn, ctx, action_api_name)
        contract = authorized_action_contract(action_type["definition"], ctx, "apply")
        require_interface_action_target(conn, ctx, self.ontology_lookup_service, contract, object_type)
        record = self.object_records_service._object_record(conn, ctx, object_type, object_id)
        target_type = self.ontology_lookup_service._active_object_type(conn, ctx, object_type)
        record = visible_record(record, target_type, ctx.roles)
        if record is None:
            record = interface_create_target_record(
                contract,
                compile_action_definition(action_type["definition"]),
                target_type,
                object_id,
                expected_object_version,
                ctx.tenant_id,
            )
        if record is not None:
            record_type = self.ontology_lookup_service._object_type_by_id_or_none(conn, ctx, record["object_type_id"])
            if (error := action_target_record_error(action_type, record, record_type)) is not None:
                raise error
        media_error = self._media_error(conn, ctx, contract, params)
        linked_values = self._linked_values(conn, ctx, action_type, record)
        return action_validation_response(
            action_type,
            record,
            expected_object_version,
            params,
            ctx,
            supplemental_error=media_error,
            linked_object_properties=linked_values,
        )

    def _linked_values(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_type: ActionTypeRow,
        record: ObjectRecordRow | None,
    ) -> Mapping[str, object]:
        if record is None:
            return {}
        return resolve_linked_condition_values(
            conn,
            ctx,
            self.policy,
            self.object_read_repository,
            self.ontology_lookup_service,
            self.osdk_application_service,
            action_type,
            record,
        )

    def _media_error(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        contract: ActionDefinitionV3,
        params: Mapping[str, object],
    ) -> FoundryLiteError | None:
        try:
            self.action_media_runtime_service.resolve_parameters(conn, ctx, contract, params)
        except FoundryLiteError as exc:
            return exc
        return None

    def _require_action_scope(self, ctx: RequestContext, action_api_name: str) -> None:
        self.osdk_application_service.require_resource_scope(
            ctx,
            resource_type="action",
            resource_api_name=action_api_name,
            operation="validate",
        )
