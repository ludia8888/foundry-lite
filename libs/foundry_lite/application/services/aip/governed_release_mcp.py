"""Separate MCP gateway for explicit, human-governed internal releases."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from foundry_lite.application.ports import OsdkMcpSessionEventRow, OsdkMcpStreamLease
from foundry_lite.application.services.aip.fde_mcp_sessions import (
    FdeMcpSessionLedger,
    require_mcp_session_namespace,
)
from foundry_lite.application.services.aip.governed_release_authorization import (
    has_active_client as _has_active_client,
)
from foundry_lite.application.services.aip.governed_release_authorization import (
    require_release_scope as _require_release_scope,
)
from foundry_lite.application.services.aip.governed_release_catalog import (
    GOVERNED_RELEASE_TOOLS,
    GovernedReleaseToolSpec,
    governed_release_action_tool,
    governed_release_mcp_tool,
    governed_release_tool,
)
from foundry_lite.application.services.aip.governed_release_mcp_results import (
    GovernedReleaseMutationOutcomeUnknown,
    PipelineDeploymentOutcomeUnknown,
    is_known_not_committed_error,
    replay_result,
    success_result,
    tool_error_result,
)
from foundry_lite.application.services.aip.governed_release_mcp_types import GovernedReleaseMcpToolCall
from foundry_lite.application.services.aip.governed_release_security_contract import (
    GovernedReleaseBinding,
    GovernedReleaseReplay,
    action_run_id,
    release_binding,
    require_human_app_principal,
)
from foundry_lite.application.services.aip.governed_release_tool_dispatcher import (
    GovernedReleaseLiveAttestationOperations,
    GovernedReleaseOperations,
    GovernedReleaseWorkflowOperations,
    dispatch_governed_release_action,
    execute_governed_release_read,
)
from foundry_lite.application.services.mcp_json_schema import McpJsonSchemaError, validate_mcp_json_schema
from foundry_lite.application.services.mcp_rate_limit_service import McpRateLimitService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    ConflictDetected,
    FoundryLiteError,
    PermissionDenied,
    RateLimited,
    ValidationFailed,
)

JsonObject = Mapping[str, object]


class ReleaseApplicationReader(Protocol):
    def get_application(self, app_id: str, *, ctx: RequestContext | None = None) -> JsonObject: ...


class ReleaseAccessSessionValidator(Protocol):
    def require_active(self, ctx: RequestContext, application_id: str) -> None: ...


class GovernedReleaseSecurityBoundary(Protocol):
    def prepare(self, ctx: RequestContext, binding: GovernedReleaseBinding) -> dict[str, object]: ...

    def replay(
        self, ctx: RequestContext, run_id: str, binding: GovernedReleaseBinding
    ) -> GovernedReleaseReplay | None: ...

    def claim(
        self, ctx: RequestContext, run_id: str, binding: GovernedReleaseBinding, widget_confirmation_token: str
    ) -> bool: ...

    def recover(self, ctx: RequestContext, run_id: str, binding: GovernedReleaseBinding) -> int | None: ...

    def retry_failed(
        self,
        ctx: RequestContext,
        run_id: str,
        binding: GovernedReleaseBinding,
        widget_confirmation_token: str,
    ) -> int | None: ...

    def is_fresh_failed_retry(
        self,
        ctx: RequestContext,
        run_id: str,
        binding: GovernedReleaseBinding,
        widget_confirmation_token: str,
    ) -> bool: ...

    def complete(
        self,
        ctx: RequestContext,
        run_id: str,
        binding: GovernedReleaseBinding,
        output: JsonObject,
        execution_attempt: int = 0,
    ) -> str: ...

    def fail(
        self,
        ctx: RequestContext,
        run_id: str,
        binding: GovernedReleaseBinding,
        exc: Exception,
        execution_attempt: int = 0,
        *,
        is_known_not_committed: bool = False,
    ) -> None: ...

    def defer(
        self,
        ctx: RequestContext,
        run_id: str,
        binding: GovernedReleaseBinding,
        exc: Exception,
        execution_attempt: int = 0,
    ) -> None: ...


class GovernedReleaseMcpGateway:
    """Expose only review, merge, deploy, status, and rollback release operations."""

    release_service: GovernedReleaseOperations
    workflow_service: GovernedReleaseWorkflowOperations
    application_reader: ReleaseApplicationReader
    access_session_validator: ReleaseAccessSessionValidator
    sessions: FdeMcpSessionLedger
    rate_limits: McpRateLimitService
    security: GovernedReleaseSecurityBoundary
    live_attestation_service: GovernedReleaseLiveAttestationOperations

    def __init__(
        self,
        *,
        release_service: GovernedReleaseOperations,
        workflow_service: GovernedReleaseWorkflowOperations,
        application_reader: ReleaseApplicationReader,
        access_session_validator: ReleaseAccessSessionValidator,
        sessions: FdeMcpSessionLedger,
        rate_limits: McpRateLimitService,
        security: GovernedReleaseSecurityBoundary,
        live_attestation_service: GovernedReleaseLiveAttestationOperations,
    ) -> None:
        self.release_service = release_service
        self.workflow_service = workflow_service
        self.application_reader = application_reader
        self.access_session_validator = access_session_validator
        self.sessions = sessions
        self.rate_limits = rate_limits
        self.security = security
        self.live_attestation_service = live_attestation_service

    def consume_endpoint_rate_limit(self, ctx: RequestContext, application_id: str) -> None:
        self.rate_limits.consume_endpoint(ctx, plane="release", application_id=application_id)

    def list_tools(
        self,
        ctx: RequestContext,
        application_id: str,
        *,
        session_id: str | None = None,
    ) -> dict[str, object]:
        self._authorized_application(ctx, application_id)
        if session_id is not None:
            _require_release_session_id(session_id)
            self.sessions.require_active(ctx, application_id, session_id)
        return {"tools": [governed_release_mcp_tool(tool) for tool in GOVERNED_RELEASE_TOOLS]}

    def open_session(self, ctx: RequestContext, application_id: str, session_id: str) -> Mapping[str, object]:
        self._authorized_application(ctx, application_id)
        _require_release_session_id(session_id)
        return self.sessions.open(ctx, application_id, session_id)

    def session_events(
        self,
        ctx: RequestContext,
        application_id: str,
        session_id: str,
        *,
        after_sequence: int = 0,
    ) -> list[OsdkMcpSessionEventRow]:
        self._authorized_application(ctx, application_id)
        _require_release_session_id(session_id)
        return self.sessions.events(ctx, application_id, session_id, after_sequence=after_sequence)

    def claim_session_stream(
        self,
        ctx: RequestContext,
        application_id: str,
        session_id: str,
    ) -> OsdkMcpStreamLease:
        self._authorized_application(ctx, application_id)
        _require_release_session_id(session_id)
        return self.sessions.claim_stream(ctx, application_id, session_id)

    def release_session_stream(
        self,
        ctx: RequestContext,
        application_id: str,
        session_id: str,
        lease_id: str,
    ) -> bool:
        _require_release_session_id(session_id)
        return self.sessions.release_stream(ctx, application_id, session_id, lease_id)

    def close_session(self, ctx: RequestContext, application_id: str, session_id: str) -> Mapping[str, object]:
        self._authorized_application(ctx, application_id)
        _require_release_session_id(session_id)
        return self.sessions.close(ctx, application_id, session_id)

    def execute_tool(self, ctx: RequestContext, request: GovernedReleaseMcpToolCall) -> dict[str, object]:
        self._authorized_application(ctx, request.application_id)
        _require_release_session_id(request.session_id)
        self.sessions.require_active(ctx, request.application_id, request.session_id)
        spec = governed_release_tool(request.tool_name)
        _validate_arguments(request.arguments, spec.input_schema)
        try:
            if request.tool_name == "prepare_release_action":
                self._consume_tool_rate_limit(ctx, request.application_id)
                return self._prepare_action(ctx, request)
            if spec.is_read_only:
                self._consume_tool_rate_limit(ctx, request.application_id)
                return self._execute_read(ctx, request)
        except RateLimited as exc:
            return tool_error_result(exc, request_id=ctx.request_id)
        return self._execute_action(ctx, request, spec)

    def _prepare_action(
        self,
        ctx: RequestContext,
        request: GovernedReleaseMcpToolCall,
    ) -> dict[str, object]:
        target_name = _required_text(request.arguments, "targetTool")
        target = governed_release_action_tool(target_name)
        target_arguments = _required_mapping(request.arguments, "arguments")
        _validate_arguments(target_arguments, target.input_schema)
        binding = self._binding(ctx, request, target, target_arguments)
        prepared = self.security.prepare(ctx, binding)
        public = {
            "status": "prepared",
            "targetTool": target_name,
            "expiresAt": prepared["expiresAt"],
            "isReplayed": prepared["isReplayed"],
        }
        return success_result(
            {"release": public},
            result_meta={"widgetConfirmationToken": prepared["widgetConfirmationToken"]},
        )

    def _execute_read(
        self,
        ctx: RequestContext,
        request: GovernedReleaseMcpToolCall,
    ) -> dict[str, object]:
        output = execute_governed_release_read(
            self.release_service,
            self.workflow_service,
            ctx,
            request.tool_name,
            request.arguments,
        )
        return success_result({"release": output})

    def _execute_action(
        self,
        ctx: RequestContext,
        request: GovernedReleaseMcpToolCall,
        spec: GovernedReleaseToolSpec,
    ) -> dict[str, object]:
        binding = self._binding(ctx, request, spec, request.arguments)
        run_id = action_run_id(binding)
        replay = self.security.replay(ctx, run_id, binding)
        if replay is not None:
            return self._replay_or_retry_failed(ctx, request, binding, run_id, replay)
        recovered = self._recover_action(ctx, request, binding, run_id)
        if recovered is not None:
            return recovered
        try:
            self._consume_tool_rate_limit(ctx, request.application_id)
        except RateLimited as exc:
            return tool_error_result(exc, request_id=ctx.request_id)
        token = request.widget_confirmation_token
        if not isinstance(token, str) or not token:
            raise ValidationFailed("widgetConfirmationToken is required for governed release actions")
        if not self.security.claim(ctx, run_id, binding, token):
            replay = self.security.replay(ctx, run_id, binding)
            if replay is not None:
                return replay_result(run_id, replay.tool_call_id, replay.output, is_error=replay.is_error)
            try:
                recovery_attempt = self.security.recover(ctx, run_id, binding)
            except ConflictDetected as exc:
                return tool_error_result(exc, request_id=ctx.request_id)
            if recovery_attempt is not None:
                return self._invoke_action(ctx, request, binding, run_id, recovery_attempt)
            raise ValidationFailed("Governed Release MCP run replay is unavailable")
        return self._invoke_action(ctx, request, binding, run_id, 0)

    def _recover_action(
        self,
        ctx: RequestContext,
        request: GovernedReleaseMcpToolCall,
        binding: GovernedReleaseBinding,
        run_id: str,
    ) -> dict[str, object] | None:
        try:
            attempt = self.security.recover(ctx, run_id, binding)
        except ConflictDetected as exc:
            return tool_error_result(exc, request_id=ctx.request_id)
        if attempt is None:
            return None
        return self._invoke_action(ctx, request, binding, run_id, attempt)

    def _replay_or_retry_failed(
        self,
        ctx: RequestContext,
        request: GovernedReleaseMcpToolCall,
        binding: GovernedReleaseBinding,
        run_id: str,
        replay: GovernedReleaseReplay,
    ) -> dict[str, object]:
        token = request.widget_confirmation_token
        if not replay.is_error or not isinstance(token, str) or not token:
            return replay_result(run_id, replay.tool_call_id, replay.output, is_error=replay.is_error)
        try:
            if not self.security.is_fresh_failed_retry(ctx, run_id, binding, token):
                return replay_result(run_id, replay.tool_call_id, replay.output, is_error=True)
            self._consume_tool_rate_limit(ctx, request.application_id)
            attempt = self.security.retry_failed(ctx, run_id, binding, token)
        except (ConflictDetected, RateLimited) as exc:
            return tool_error_result(exc, request_id=ctx.request_id)
        if attempt is None:
            return replay_result(run_id, replay.tool_call_id, replay.output, is_error=True)
        return self._invoke_action(ctx, request, binding, run_id, attempt)

    def _invoke_action(
        self,
        ctx: RequestContext,
        request: GovernedReleaseMcpToolCall,
        binding: GovernedReleaseBinding,
        run_id: str,
        execution_attempt: int,
    ) -> dict[str, object]:
        try:
            output = self._dispatch_confirmed_action(ctx, request, binding, run_id, execution_attempt)
        except GovernedReleaseMutationOutcomeUnknown as exc:
            self.security.defer(ctx, run_id, binding, exc.original, execution_attempt)
            return tool_error_result(_outcome_unknown_error(exc.original), request_id=ctx.request_id)
        except PipelineDeploymentOutcomeUnknown as exc:
            self.security.defer(ctx, run_id, binding, exc.original, execution_attempt)
            return tool_error_result(_outcome_unknown_error(exc.original), request_id=ctx.request_id)
        except FoundryLiteError as exc:
            self.security.fail(
                ctx,
                run_id,
                binding,
                exc,
                execution_attempt,
                is_known_not_committed=is_known_not_committed_error(exc),
            )
            return tool_error_result(exc, request_id=ctx.request_id)
        except Exception as exc:
            self.security.defer(ctx, run_id, binding, exc, execution_attempt)
            return tool_error_result(_outcome_unknown_error(exc), request_id=ctx.request_id)
        structured = {"release": output}
        tool_call_id = self.security.complete(ctx, run_id, binding, structured, execution_attempt)
        self.sessions.record_tool_completed(
            ctx,
            request.application_id,
            request.session_id,
            request.tool_name,
            run_id,
        )
        return success_result(structured, run_id=run_id, tool_call_id=tool_call_id)

    def _dispatch_confirmed_action(
        self,
        ctx: RequestContext,
        request: GovernedReleaseMcpToolCall,
        binding: GovernedReleaseBinding,
        run_id: str,
        execution_attempt: int,
    ) -> dict[str, object]:
        return dispatch_governed_release_action(
            self.release_service,
            self.workflow_service,
            self.live_attestation_service,
            ctx,
            request.application_id,
            request.tool_name,
            request.arguments,
            run_id=run_id,
            binding_hash=binding.fingerprint,
            session_id=binding.session_id,
            execution_attempt=execution_attempt,
        )

    def _binding(
        self,
        ctx: RequestContext,
        request: GovernedReleaseMcpToolCall,
        spec: GovernedReleaseToolSpec,
        arguments: JsonObject,
    ) -> GovernedReleaseBinding:
        return release_binding(
            ctx,
            application_id=request.application_id,
            session_id=request.session_id,
            tool_name=spec.name,
            arguments=arguments,
            required_permission=_required_permission(spec.name, arguments),
            origin=request.origin,
        )

    def _consume_tool_rate_limit(self, ctx: RequestContext, application_id: str) -> None:
        self.rate_limits.consume_tool(ctx, plane="release", application_id=application_id)

    def _authorized_application(self, ctx: RequestContext, application_id: str) -> JsonObject:
        require_human_app_principal(ctx, application_id)
        self.access_session_validator.require_active(ctx, application_id)
        bundle = self.application_reader.get_application(application_id, ctx=ctx)
        application = bundle.get("application")
        if not isinstance(application, Mapping) or application.get("status") != "active":
            raise PermissionDenied("Governed Release MCP application is not active")
        is_external_session = ctx.oauth_session_authority == "issuer"
        if is_external_session and not ctx.authorization_server_issuer:
            raise PermissionDenied("Governed Release MCP authorization server issuer is missing")
        if not is_external_session and not _has_active_client(bundle.get("clients"), str(ctx.client_id)):
            raise PermissionDenied("Governed Release MCP OAuth client is not active")
        _require_release_scope(ctx, bundle)
        return bundle


def _required_permission(tool_name: str, arguments: JsonObject) -> str:
    kind = arguments.get("releaseKind")
    if tool_name == "verify_release_completion":
        return "pipeline:deploy"
    if tool_name in {"create_release_branch", "publish_release_candidate"}:
        return "pipeline:write" if kind == "pipeline" else "ontology:validate"
    if tool_name == "assign_release_reviewer":
        return "pipeline:review" if kind == "pipeline" else "ontology:activate"
    if tool_name == "submit_release_decision":
        return "pipeline:review" if kind == "pipeline" else "ontology:activate"
    if tool_name in {"execute_approved_release", "deploy_release", "rollback_release"}:
        return "pipeline:deploy" if kind == "pipeline" else "ontology:activate"
    raise ValidationFailed("targetTool is not a governed release action")


def _validate_arguments(arguments: JsonObject, schema: JsonObject) -> None:
    try:
        validate_mcp_json_schema(arguments, schema)
    except McpJsonSchemaError as exc:
        raise ValidationFailed(
            "Governed Release MCP arguments do not match inputSchema",
            details={"path": exc.path, "reason": exc.reason},
        ) from exc


def _required_text(arguments: JsonObject, key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationFailed(f"{key} is required")
    return value.strip()


def _required_mapping(arguments: JsonObject, key: str) -> Mapping[str, object]:
    value = arguments.get(key)
    if not isinstance(value, Mapping):
        raise ValidationFailed(f"{key} must be an object")
    return value


def _require_release_session_id(session_id: str) -> None:
    require_mcp_session_namespace(session_id, "release")


def _outcome_unknown_error(exc: Exception) -> ConflictDetected:
    error_type = exc.code if isinstance(exc, FoundryLiteError) else type(exc).__name__
    return ConflictDetected(
        "Governed release outcome is not yet confirmed; retry the exact action after the recovery lease",
        details={
            "reason": "release_run_in_progress",
            "isRecoverable": True,
            "retryAfterSeconds": 31,
            "originalErrorType": error_type,
        },
    )


__all__ = ["GovernedReleaseMcpGateway"]
