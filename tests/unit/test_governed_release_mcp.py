from __future__ import annotations

import json
from collections.abc import Mapping

import pytest
from foundry_lite.application.services.aip.governed_release_authorization import GOVERNED_RELEASE_SCOPE
from foundry_lite.application.services.aip.governed_release_catalog import (
    GOVERNED_RELEASE_UI_RESOURCE_URI,
)
from foundry_lite.application.services.aip.governed_release_mcp import GovernedReleaseMcpGateway
from foundry_lite.application.services.aip.governed_release_mcp_types import GovernedReleaseMcpToolCall
from foundry_lite.application.services.aip.governed_release_outcomes import project_confirmed_mutation
from foundry_lite.application.services.aip.governed_release_security_contract import (
    GovernedReleaseBinding,
    GovernedReleaseReplay,
    preparation_record,
    release_binding,
    widget_receipt_id,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, PermissionDenied, ValidationFailed

_RELEASE_SCOPE = GOVERNED_RELEASE_SCOPE


class _ReleaseService:
    def __init__(self) -> None:
        self.deploy_count = 0
        self.deployments: dict[str, dict[str, object]] = {}

    def get_candidate(self, _ctx: RequestContext, arguments: Mapping[str, object]) -> dict[str, object]:
        return {"proposalId": arguments["proposalId"], "stage": "approved"}

    def get_status(self, _ctx: RequestContext, arguments: Mapping[str, object]) -> dict[str, object]:
        return {"proposalId": arguments["proposalId"], "stage": "approved"}

    def submit_decision(self, _ctx: RequestContext, arguments: Mapping[str, object]) -> dict[str, object]:
        return dict(arguments)

    def publish_candidate(self, _ctx: RequestContext, arguments: Mapping[str, object]) -> dict[str, object]:
        return dict(arguments)

    def execute_approved(self, _ctx: RequestContext, arguments: Mapping[str, object]) -> dict[str, object]:
        return dict(arguments)

    def deploy(self, _ctx: RequestContext, arguments: Mapping[str, object]) -> dict[str, object]:
        key = str(arguments["idempotencyKey"])
        if key not in self.deployments:
            self.deploy_count += 1
            self.deployments[key] = {"proposalId": arguments["proposalId"], "stage": "deployed"}
        return dict(self.deployments[key])

    def rollback(self, _ctx: RequestContext, arguments: Mapping[str, object]) -> dict[str, object]:
        return dict(arguments)


class _WorkflowService:
    def open_workspace(self, _ctx: RequestContext, arguments: Mapping[str, object]) -> dict[str, object]:
        return {"releaseKind": arguments["releaseKind"], "stage": "workspace_ready"}

    def list_inbox(self, _ctx: RequestContext, arguments: Mapping[str, object]) -> dict[str, object]:
        return {"releaseKind": arguments["releaseKind"], "stage": "empty_inbox"}

    def create_branch(self, _ctx: RequestContext, arguments: Mapping[str, object]) -> dict[str, object]:
        return {"releaseKind": arguments["releaseKind"], "stage": "branch_created"}

    def assign_reviewer(self, _ctx: RequestContext, arguments: Mapping[str, object]) -> dict[str, object]:
        return dict(arguments)


class _ApplicationReader:
    def __init__(self, granted_scopes: tuple[str, ...] = (_RELEASE_SCOPE,)) -> None:
        self.granted_scopes = granted_scopes

    def get_application(self, _app_id: str, *, ctx: RequestContext | None = None) -> Mapping[str, object]:
        del ctx
        return {
            "application": {"status": "active"},
            "clients": [{"client_id": "client-1", "status": "active"}],
            "resources": [{"scopes": list(self.granted_scopes)}],
        }


class _AccessSessions:
    def require_active(self, _ctx: RequestContext, _application_id: str) -> None:
        return None


class _LiveAttestations:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.release_run_ids: list[str | None] = []

    def collect_and_store_server_verified(
        self,
        ctx: RequestContext,
        application_id: str,
        ontology_workflow_run_id: str,
        pipeline_workflow_run_id: str,
    ) -> dict[str, object]:
        self.calls.append((application_id, ontology_workflow_run_id, pipeline_workflow_run_id))
        self.release_run_ids.append(ctx.governed_release_run_id)
        return {"attestationId": "attestation-1", "status": "live_verified", "isCreated": True}


class _Sessions:
    def require_active(self, _ctx: RequestContext, _application_id: str, _session_id: str) -> None:
        return None

    def record_tool_completed(self, *_args: object) -> None:
        return None


class _RateLimits:
    def __init__(self) -> None:
        self.endpoint_planes: list[str] = []
        self.tool_planes: list[str] = []

    def consume_tool(self, _ctx: RequestContext, *, plane: str, application_id: str) -> None:
        del application_id
        self.tool_planes.append(plane)

    def consume_endpoint(self, _ctx: RequestContext, *, plane: str, application_id: str) -> None:
        del application_id
        self.endpoint_planes.append(plane)


class _Security:
    def __init__(self) -> None:
        self.prepared_count = 0
        self.replays: dict[str, GovernedReleaseReplay] = {}
        self.last_prepared_binding: GovernedReleaseBinding | None = None

    def prepare(self, _ctx: RequestContext, binding: GovernedReleaseBinding) -> dict[str, object]:
        self.prepared_count += 1
        self.last_prepared_binding = binding
        return {
            "widgetConfirmationToken": "widget-secret",
            "expiresAt": "2030-01-01T00:00:00+00:00",
            "isReplayed": False,
        }

    def replay(self, _ctx: RequestContext, run_id: str, _binding: object) -> GovernedReleaseReplay | None:
        return self.replays.get(run_id)

    def claim(self, _ctx: RequestContext, run_id: str, _binding: object, _token: str) -> bool:
        return run_id not in self.replays

    def recover(self, _ctx: RequestContext, _run_id: str, _binding: object) -> int | None:
        return None

    def retry_failed(
        self,
        _ctx: RequestContext,
        _run_id: str,
        _binding: object,
        _token: str,
    ) -> int | None:
        return None

    def is_fresh_failed_retry(
        self,
        _ctx: RequestContext,
        _run_id: str,
        _binding: object,
        _token: str,
    ) -> bool:
        return False

    def complete(
        self,
        _ctx: RequestContext,
        run_id: str,
        _binding: object,
        output: Mapping[str, object],
        _execution_attempt: int = 0,
    ) -> str:
        tool_call_id = f"{run_id}-tool-1"
        self.replays[run_id] = GovernedReleaseReplay(tool_call_id, dict(output))
        return tool_call_id

    def fail(self, *_args: object, **_kwargs: object) -> None:
        return None

    def defer(self, *_args: object) -> None:
        return None


class _CrashAfterMutationSecurity(_Security):
    def __init__(self) -> None:
        super().__init__()
        self.claimed_run_ids: set[str] = set()
        self.has_crashed = False
        self.recovery_count = 0

    def claim(self, _ctx: RequestContext, run_id: str, _binding: object, _token: str) -> bool:
        self.claimed_run_ids.add(run_id)
        return True

    def recover(self, _ctx: RequestContext, run_id: str, _binding: object) -> int | None:
        if run_id not in self.claimed_run_ids or run_id in self.replays or self.recovery_count > 0:
            return None
        self.recovery_count += 1
        return self.recovery_count

    def complete(
        self,
        ctx: RequestContext,
        run_id: str,
        binding: object,
        output: Mapping[str, object],
        execution_attempt: int = 0,
    ) -> str:
        if not self.has_crashed:
            self.has_crashed = True
            raise RuntimeError("simulated response-stage failure after the domain commit")
        return super().complete(ctx, run_id, binding, output, execution_attempt)


class _InProgressSecurity(_Security):
    def recover(self, _ctx: RequestContext, _run_id: str, _binding: object) -> int | None:
        raise ConflictDetected(
            "release action is still running",
            details={"reason": "release_run_in_progress", "isRecoverable": True, "retryAfterSeconds": 17},
        )


class _ProjectionFaultSecurity(_Security):
    def __init__(self) -> None:
        super().__init__()
        self.claimed: set[str] = set()
        self.deferred: set[str] = set()
        self.recovered: set[str] = set()
        self.fail_count = 0

    def claim(self, _ctx: RequestContext, run_id: str, _binding: object, _token: str) -> bool:
        self.claimed.add(run_id)
        return True

    def recover(self, _ctx: RequestContext, run_id: str, _binding: object) -> int | None:
        if run_id in self.deferred and run_id not in self.recovered:
            self.recovered.add(run_id)
            return 1
        return None

    def fail(self, *_args: object, **_kwargs: object) -> None:
        self.fail_count += 1

    def defer(self, _ctx: RequestContext, run_id: str, *_args: object) -> None:
        self.deferred.add(run_id)


class _ProjectionFaultRelease(_ReleaseService):
    def __init__(self, selected_tool: str, effects: dict[str, set[str]], invocations: dict[str, int]) -> None:
        super().__init__()
        self.selected_tool = selected_tool
        self.effects = effects
        self.invocations = invocations
        self.failed_tools: set[str] = set()

    def get_candidate(self, _ctx: RequestContext, arguments: Mapping[str, object]) -> dict[str, object]:
        return self._project("assign_release_reviewer", arguments)

    def submit_decision(self, _ctx: RequestContext, arguments: Mapping[str, object]) -> dict[str, object]:
        return self._mutate("submit_release_decision", arguments)

    def publish_candidate(self, _ctx: RequestContext, arguments: Mapping[str, object]) -> dict[str, object]:
        return self._mutate("publish_release_candidate", arguments)

    def execute_approved(self, _ctx: RequestContext, arguments: Mapping[str, object]) -> dict[str, object]:
        return self._mutate("execute_approved_release", arguments)

    def deploy(self, _ctx: RequestContext, arguments: Mapping[str, object]) -> dict[str, object]:
        return self._mutate("deploy_release", arguments)

    def rollback(self, _ctx: RequestContext, arguments: Mapping[str, object]) -> dict[str, object]:
        return self._mutate("rollback_release", arguments)

    def _mutate(self, tool_name: str, arguments: Mapping[str, object]) -> dict[str, object]:
        self.invocations[tool_name] = self.invocations.get(tool_name, 0) + 1
        self.effects.setdefault(tool_name, set()).add(str(arguments["idempotencyKey"]))
        return project_confirmed_mutation(tool_name, lambda: self._project(tool_name, arguments))

    def _project(self, tool_name: str, arguments: Mapping[str, object]) -> dict[str, object]:
        if tool_name == self.selected_tool and tool_name not in self.failed_tools:
            self.failed_tools.add(tool_name)
            raise RuntimeError(f"{tool_name} projection failed after commit")
        return {"proposalId": arguments.get("proposalId", "branch-1"), "stage": "confirmed"}


class _ProjectionFaultWorkflow(_WorkflowService):
    def __init__(self, selected_tool: str, effects: dict[str, set[str]], invocations: dict[str, int]) -> None:
        self.selected_tool = selected_tool
        self.effects = effects
        self.invocations = invocations
        self.has_failed_create = False

    def create_branch(self, _ctx: RequestContext, arguments: Mapping[str, object]) -> dict[str, object]:
        self.invocations["create_release_branch"] = self.invocations.get("create_release_branch", 0) + 1
        self.effects.setdefault("create_release_branch", set()).add(str(arguments["idempotencyKey"]))

        def projection() -> dict[str, object]:
            if self.selected_tool == "create_release_branch" and not self.has_failed_create:
                self.has_failed_create = True
                raise RuntimeError("create projection failed after commit")
            return {"releaseKind": arguments["releaseKind"], "stage": "branch_created"}

        return project_confirmed_mutation("create_release_branch", projection)

    def assign_reviewer(self, _ctx: RequestContext, arguments: Mapping[str, object]) -> dict[str, object]:
        self.invocations["assign_release_reviewer"] = self.invocations.get("assign_release_reviewer", 0) + 1
        self.effects.setdefault("assign_release_reviewer", set()).add(str(arguments["idempotencyKey"]))
        return dict(arguments)


class _PreMutationValidationRelease(_ReleaseService):
    def submit_decision(self, _ctx: RequestContext, _arguments: Mapping[str, object]) -> dict[str, object]:
        raise ValidationFailed("proposal is stale before mutation")


class _FailOncePreMutationRelease(_ReleaseService):
    def __init__(self) -> None:
        super().__init__()
        self.decision_count = 0

    def submit_decision(self, _ctx: RequestContext, arguments: Mapping[str, object]) -> dict[str, object]:
        self.decision_count += 1
        if self.decision_count == 1:
            raise ValidationFailed("proposal is stale before mutation")
        return dict(arguments)


class _SafeFailedRetrySecurity(_Security):
    def __init__(self) -> None:
        super().__init__()
        self.retry_count = 0

    def prepare(self, _ctx: RequestContext, _binding: object) -> dict[str, object]:
        self.prepared_count += 1
        return {
            "widgetConfirmationToken": "fresh-widget-secret",
            "expiresAt": "2030-01-01T00:00:00+00:00",
            "isReplayed": True,
        }

    def fail(self, _ctx: RequestContext, run_id: str, *_args: object, **_kwargs: object) -> None:
        self.replays[run_id] = GovernedReleaseReplay(
            f"{run_id}-tool-1",
            {"error": {"type": "VALIDATION_FAILED", "message": "proposal is stale"}},
            is_error=True,
        )

    def retry_failed(
        self,
        _ctx: RequestContext,
        run_id: str,
        _binding: object,
        token: str,
    ) -> int | None:
        if token != "fresh-widget-secret":
            return None
        self.retry_count += 1
        self.replays.pop(run_id, None)
        return self.retry_count

    def is_fresh_failed_retry(
        self,
        _ctx: RequestContext,
        _run_id: str,
        _binding: object,
        token: str,
    ) -> bool:
        return token == "fresh-widget-secret"


def _ctx(*, scopes: tuple[str, ...] = (_RELEASE_SCOPE,), roles: tuple[str, ...] = ("admin",)) -> RequestContext:
    return RequestContext(
        actor_user_id="reviewer-1",
        roles=roles,
        application_id="app-1",
        client_id="client-1",
        oauth_session_id="oauth-session-1",
        oauth_session_hash="oauth-session:sha256:unit-local-session",
        oauth_session_authority="local",
        authorization_server_issuer="https://foundry-lite.local/osdk-oauth",
        oauth_grant_type="authorization_code",
        oauth_resource="https://foundry.example.test/mcp/release/app-1",
        oauth_token_issued_at=1_786_224_000,
        oauth_token_expires_at=1_786_224_900,
        is_human_oauth=True,
        token_scopes=scopes,
    )


def _gateway(
    *,
    granted_scopes: tuple[str, ...] = (_RELEASE_SCOPE,),
    security: _Security | None = None,
    release: _ReleaseService | None = None,
    workflow: _WorkflowService | None = None,
    live_attestations: _LiveAttestations | None = None,
) -> tuple[GovernedReleaseMcpGateway, _ReleaseService, _RateLimits, _Security]:
    release = release or _ReleaseService()
    rates = _RateLimits()
    security = security or _Security()
    gateway = GovernedReleaseMcpGateway(
        release_service=release,  # type: ignore[arg-type]
        workflow_service=workflow or _WorkflowService(),  # type: ignore[arg-type]
        application_reader=_ApplicationReader(granted_scopes),
        access_session_validator=_AccessSessions(),
        sessions=_Sessions(),  # type: ignore[arg-type]
        rate_limits=rates,  # type: ignore[arg-type]
        security=security,  # type: ignore[arg-type]
        live_attestation_service=live_attestations or _LiveAttestations(),
    )
    return gateway, release, rates, security


def _call(
    tool_name: str,
    arguments: Mapping[str, object],
    *,
    rpc_id: int = 1,
    widget_confirmation_token: str | None = "widget-secret",
) -> GovernedReleaseMcpToolCall:
    return GovernedReleaseMcpToolCall(
        application_id="app-1",
        session_id="mcp-release-session-1",
        json_rpc_id=rpc_id,
        tool_name=tool_name,
        arguments=arguments,
        widget_confirmation_token=widget_confirmation_token,
        origin="https://chatgpt.com",
    )


def test_catalog_marks_reads_as_render_tools_and_actions_as_app_only() -> None:
    gateway, _, _, _ = _gateway()
    tools = gateway.list_tools(_ctx(), "app-1", session_id="mcp-release-session-1")["tools"]
    by_name = {tool["name"]: tool for tool in tools}
    expected_security = [{"type": "oauth2", "scopes": [_RELEASE_SCOPE]}]

    for tool in tools:
        assert tool["securitySchemes"] == expected_security
        assert tool["_meta"]["securitySchemes"] == expected_security

    for name in (
        "open_release_workspace",
        "list_release_inbox",
        "get_release_candidate",
        "get_release_status",
    ):
        assert by_name[name]["_meta"]["ui"]["resourceUri"] == GOVERNED_RELEASE_UI_RESOURCE_URI
        assert by_name[name]["_meta"]["openai/outputTemplate"] == GOVERNED_RELEASE_UI_RESOURCE_URI
    for name in (
        "prepare_release_action",
        "create_release_branch",
        "publish_release_candidate",
        "assign_release_reviewer",
        "submit_release_decision",
        "execute_approved_release",
        "deploy_release",
        "rollback_release",
        "verify_release_completion",
    ):
        assert by_name[name]["_meta"]["ui"]["visibility"] == ["app"]
        assert by_name[name]["_meta"]["openai/visibility"] == "private"


def test_endpoint_rate_limit_uses_the_isolated_release_plane() -> None:
    gateway, _, rates, _ = _gateway()

    gateway.consume_endpoint_rate_limit(_ctx(), "app-1")

    assert rates.endpoint_planes == ["release"]


@pytest.mark.parametrize("session_id", ["mcp-builder-session-1", "ontology-mcp-session-0001"])
def test_release_rejects_a_foreign_plane_session_namespace(session_id: str) -> None:
    gateway, _, _, _ = _gateway()

    with pytest.raises(ValidationFailed, match="release-plane session"):
        gateway.list_tools(_ctx(), "app-1", session_id=session_id)


def test_prepare_validates_target_schema_before_issuing_hidden_token() -> None:
    gateway, _, _, security = _gateway()
    request = _call(
        "prepare_release_action",
        {
            "targetTool": "deploy_release",
            "arguments": {
                "releaseKind": "pipeline",
                "proposalId": "proposal-1",
                "pipelineId": "pipeline-1",
                "versionId": "version-2",
                "idempotencyKey": "deploy-1",
                "unexpected": True,
            },
        },
    )

    with pytest.raises(ValidationFailed, match="inputSchema"):
        gateway.execute_tool(_ctx(), request)
    assert security.prepared_count == 0


def test_prepare_returns_secret_only_in_result_meta() -> None:
    gateway, _, _, security = _gateway()
    request = _call(
        "prepare_release_action",
        {
            "targetTool": "deploy_release",
            "arguments": {
                "releaseKind": "pipeline",
                "proposalId": "proposal-1",
                "pipelineId": "pipeline-1",
                "versionId": "version-2",
                "idempotencyKey": "deploy-1",
            },
        },
    )

    result = gateway.execute_tool(_ctx(), request)

    assert security.prepared_count == 1
    assert result["_meta"] == {"widgetConfirmationToken": "widget-secret"}
    assert "widget-secret" not in json.dumps(result["structuredContent"], sort_keys=True)
    assert "widget-secret" not in json.dumps(result["content"], sort_keys=True)


def test_verify_completion_uses_confirmed_server_collector_and_exact_ai_replay() -> None:
    live_attestations = _LiveAttestations()
    gateway, _, rates, security = _gateway(live_attestations=live_attestations)
    arguments = {
        "ontologyWorkflowRunId": "ontology-workflow-1",
        "pipelineWorkflowRunId": "pipeline-workflow-1",
        "idempotencyKey": "completion-1",
    }

    prepared = gateway.execute_tool(
        _ctx(),
        _call(
            "prepare_release_action",
            {"targetTool": "verify_release_completion", "arguments": arguments},
        ),
    )
    first = gateway.execute_tool(_ctx(), _call("verify_release_completion", arguments, rpc_id=2))
    replay = gateway.execute_tool(
        _ctx(),
        _call("verify_release_completion", arguments, rpc_id=3, widget_confirmation_token=None),
    )

    assert prepared["structuredContent"]["release"]["targetTool"] == "verify_release_completion"
    assert security.last_prepared_binding is not None
    assert security.last_prepared_binding.required_permission == "pipeline:deploy"
    assert security.last_prepared_binding.release_kind == "combined"
    assert first["structuredContent"]["release"]["attestationId"] == "attestation-1"
    assert replay["isReplayed"] is True
    assert first["aiRunId"] == replay["aiRunId"]
    assert live_attestations.calls == [("app-1", "ontology-workflow-1", "pipeline-workflow-1")]
    assert len(live_attestations.release_run_ids) == 1
    assert str(live_attestations.release_run_ids[0]).startswith("governed_release_run_")
    assert rates.tool_planes == ["release", "release"]


def test_verify_completion_rejects_caller_supplied_evidence_or_live_claims() -> None:
    live_attestations = _LiveAttestations()
    gateway, _, _, security = _gateway(live_attestations=live_attestations)
    request = _call(
        "verify_release_completion",
        {
            "ontologyWorkflowRunId": "ontology-workflow-1",
            "pipelineWorkflowRunId": "pipeline-workflow-1",
            "idempotencyKey": "verify-completion-forged",
            "evidence": {"provider": "caller"},
            "status": "live_verified",
            "isLive": True,
        },
    )

    with pytest.raises(ValidationFailed, match="inputSchema"):
        gateway.execute_tool(_ctx(), request)
    assert live_attestations.calls == []
    assert security.replays == {}


def test_widget_secret_is_hash_addressed_in_persistent_security_record() -> None:
    ctx = _ctx()
    arguments = {
        "releaseKind": "pipeline",
        "proposalId": "proposal-1",
        "pipelineId": "pipeline-1",
        "versionId": "version-2",
        "idempotencyKey": "deploy-1",
    }
    binding = release_binding(
        ctx,
        application_id="app-1",
        session_id="release-session-1",
        tool_name="deploy_release",
        arguments=arguments,
        required_permission="pipeline:deploy",
        origin="https://chatgpt.com",
    )
    secret = "opaque-widget-secret"
    receipt_id = widget_receipt_id(secret)
    record = preparation_record(
        ctx,
        binding,
        "prepare-1",
        receipt_id,
        "2026-08-09T00:00:00+00:00",
        "2026-08-09T00:05:00+00:00",
    )

    assert secret not in json.dumps(record.budget_json, sort_keys=True)
    assert record.budget_json["receiptId"] == receipt_id
    assert secret not in receipt_id


def test_mutation_replays_by_idempotency_key_without_second_quota_or_deploy() -> None:
    gateway, release, rates, _ = _gateway()
    arguments = {
        "releaseKind": "pipeline",
        "proposalId": "proposal-1",
        "pipelineId": "pipeline-1",
        "versionId": "version-2",
        "idempotencyKey": "deploy-1",
    }

    first = gateway.execute_tool(_ctx(), _call("deploy_release", arguments, rpc_id=1))
    second = gateway.execute_tool(_ctx(), _call("deploy_release", arguments, rpc_id=999))

    assert first["isReplayed"] is False
    assert second["isReplayed"] is True
    assert first["aiRunId"] == second["aiRunId"]
    assert release.deploy_count == 1
    assert rates.tool_planes == ["release"]


def test_response_stage_failure_after_domain_commit_recovers_without_a_second_token_or_quota() -> None:
    crash_security = _CrashAfterMutationSecurity()
    gateway, release, rates, _ = _gateway(security=crash_security)
    arguments = {
        "releaseKind": "pipeline",
        "proposalId": "proposal-1",
        "pipelineId": "pipeline-1",
        "versionId": "version-2",
        "idempotencyKey": "deploy-crash-1",
    }

    with pytest.raises(RuntimeError, match="simulated response-stage failure"):
        gateway.execute_tool(_ctx(), _call("deploy_release", arguments, rpc_id=1))

    recovered = gateway.execute_tool(
        _ctx(),
        _call("deploy_release", arguments, rpc_id=2, widget_confirmation_token=None),
    )
    replayed = gateway.execute_tool(
        _ctx(),
        _call("deploy_release", arguments, rpc_id=3, widget_confirmation_token=None),
    )

    assert recovered["isError"] is False
    assert recovered["isReplayed"] is False
    assert replayed["isReplayed"] is True
    assert recovered["aiRunId"] == replayed["aiRunId"]
    assert release.deploy_count == 1
    assert crash_security.recovery_count == 1
    assert rates.tool_planes == ["release"]


def test_in_progress_recovery_is_a_structured_tool_error_with_retry_timing() -> None:
    gateway, release, rates, _ = _gateway(security=_InProgressSecurity())
    arguments = {
        "releaseKind": "pipeline",
        "proposalId": "proposal-1",
        "pipelineId": "pipeline-1",
        "versionId": "version-2",
        "idempotencyKey": "deploy-in-progress-1",
    }

    result = gateway.execute_tool(
        _ctx(),
        _call("deploy_release", arguments, widget_confirmation_token=None),
    )

    assert result["isError"] is True
    assert result["structuredContent"]["error"]["details"] == {
        "reason": "release_run_in_progress",
        "isRecoverable": True,
        "retryAfterSeconds": 17,
    }
    assert release.deploy_count == 0
    assert rates.tool_planes == []


_PROJECTION_FAULT_ACTIONS = {
    "create_release_branch": {
        "releaseKind": "pipeline",
        "branchName": "candidate",
        "pipelineId": "pipeline-1",
        "idempotencyKey": "create-1",
    },
    "assign_release_reviewer": {
        "releaseKind": "pipeline",
        "proposalId": "proposal-1",
        "idempotencyKey": "assign-1",
    },
    "publish_release_candidate": {
        "releaseKind": "pipeline",
        "proposalId": "proposal-1",
        "idempotencyKey": "publish-1",
    },
    "submit_release_decision": {
        "releaseKind": "pipeline",
        "proposalId": "proposal-1",
        "decision": "approve",
        "idempotencyKey": "decision-1",
    },
    "execute_approved_release": {
        "releaseKind": "pipeline",
        "proposalId": "proposal-1",
        "idempotencyKey": "execute-1",
    },
    "deploy_release": {
        "releaseKind": "pipeline",
        "proposalId": "proposal-1",
        "pipelineId": "pipeline-1",
        "versionId": "version-2",
        "idempotencyKey": "deploy-1",
    },
    "rollback_release": {
        "releaseKind": "pipeline",
        "proposalId": "proposal-1",
        "pipelineId": "pipeline-1",
        "targetVersionId": "version-1",
        "rolledBackFromId": "deployment-2",
        "idempotencyKey": "rollback-1",
    },
}


@pytest.mark.parametrize(("tool_name", "arguments"), _PROJECTION_FAULT_ACTIONS.items())
def test_post_commit_projection_fault_recovers_all_actions_without_duplicate_effect_or_quota(
    tool_name: str,
    arguments: Mapping[str, object],
) -> None:
    effects: dict[str, set[str]] = {}
    invocations: dict[str, int] = {}
    security = _ProjectionFaultSecurity()
    release = _ProjectionFaultRelease(tool_name, effects, invocations)
    workflow = _ProjectionFaultWorkflow(tool_name, effects, invocations)
    gateway, _, rates, _ = _gateway(security=security, release=release, workflow=workflow)

    first = gateway.execute_tool(_ctx(), _call(tool_name, arguments, rpc_id=1))
    recovered = gateway.execute_tool(
        _ctx(),
        _call(tool_name, arguments, rpc_id=2, widget_confirmation_token=None),
    )
    replayed = gateway.execute_tool(
        _ctx(),
        _call(tool_name, arguments, rpc_id=3, widget_confirmation_token=None),
    )

    assert first["isError"] is True
    assert first["structuredContent"]["error"]["details"]["reason"] == "release_run_in_progress"
    assert recovered["isError"] is False
    assert replayed["isReplayed"] is True
    assert len(effects[tool_name]) == 1
    assert invocations[tool_name] == 2
    assert security.fail_count == 0
    assert len(security.deferred) == 1
    assert rates.tool_planes == ["release"]


def test_pre_mutation_validation_failure_is_terminal_not_deferred() -> None:
    security = _ProjectionFaultSecurity()
    gateway, _, _, _ = _gateway(security=security, release=_PreMutationValidationRelease())
    arguments = _PROJECTION_FAULT_ACTIONS["submit_release_decision"]

    result = gateway.execute_tool(_ctx(), _call("submit_release_decision", arguments))

    assert result["isError"] is True
    assert result["structuredContent"]["error"]["type"] == "VALIDATION_FAILED"
    assert security.fail_count == 1
    assert security.deferred == set()


def test_safe_terminal_failure_reopens_only_after_fresh_prepare_and_confirmation() -> None:
    security = _SafeFailedRetrySecurity()
    release = _FailOncePreMutationRelease()
    gateway, _, rates, _ = _gateway(security=security, release=release)
    arguments = _PROJECTION_FAULT_ACTIONS["submit_release_decision"]

    first = gateway.execute_tool(_ctx(), _call("submit_release_decision", arguments, rpc_id=1))
    same_consumed_token = gateway.execute_tool(
        _ctx(),
        _call("submit_release_decision", arguments, rpc_id=2, widget_confirmation_token="widget-secret"),
    )
    tokenless = gateway.execute_tool(
        _ctx(), _call("submit_release_decision", arguments, rpc_id=3, widget_confirmation_token=None)
    )
    prepared = gateway.execute_tool(
        _ctx(),
        _call(
            "prepare_release_action",
            {"targetTool": "submit_release_decision", "arguments": arguments},
            rpc_id=4,
        ),
    )
    retried = gateway.execute_tool(
        _ctx(),
        _call(
            "submit_release_decision",
            arguments,
            rpc_id=5,
            widget_confirmation_token=str(prepared["_meta"]["widgetConfirmationToken"]),
        ),
    )
    replayed = gateway.execute_tool(
        _ctx(),
        _call("submit_release_decision", arguments, rpc_id=6, widget_confirmation_token=None),
    )

    assert first["isError"] is True
    assert same_consumed_token["isReplayed"] is True
    assert same_consumed_token["isError"] is True
    assert tokenless["isReplayed"] is True
    assert tokenless["isError"] is True
    assert retried["isError"] is False
    assert replayed["isReplayed"] is True
    assert release.decision_count == 2
    assert security.retry_count == 1
    assert rates.tool_planes == ["release", "release", "release"]


@pytest.mark.parametrize(
    ("token_scopes", "granted_scopes"),
    [
        (("osdk:connector:other:execute",), (_RELEASE_SCOPE,)),
        ((_RELEASE_SCOPE,), ("osdk:connector:other:execute",)),
        (("osdk:*",), (_RELEASE_SCOPE,)),
        ((_RELEASE_SCOPE,), ("osdk:*",)),
    ],
)
def test_release_requires_exact_token_and_application_scope(
    token_scopes: tuple[str, ...],
    granted_scopes: tuple[str, ...],
) -> None:
    gateway, _, _, _ = _gateway(granted_scopes=granted_scopes)

    with pytest.raises(PermissionDenied, match="scope"):
        gateway.list_tools(_ctx(scopes=token_scopes), "app-1")


def test_release_rejects_client_credentials_service_principal() -> None:
    gateway, _, _, _ = _gateway()
    machine = RequestContext(
        actor_user_id="service-principal:client-1",
        roles=("osdk_service_principal",),
        application_id="app-1",
        client_id="client-1",
        oauth_session_id="oauth-session-1",
        token_scopes=(_RELEASE_SCOPE,),
    )

    with pytest.raises(PermissionDenied, match="authorization-code human"):
        gateway.list_tools(machine, "app-1")


def test_release_accepts_issuer_authoritative_human_without_local_oauth_client_row() -> None:
    gateway, _, _, _ = _gateway()
    external_human = RequestContext(
        actor_user_id="external-reviewer-1",
        roles=("admin",),
        application_id="app-1",
        client_id="https://chatgpt.com/oauth/release/client.json",
        oauth_session_id="issuer-session:verified-binding",
        oauth_session_hash="oauth-session:sha256:verified-binding",
        oauth_session_authority="issuer",
        authorization_server_issuer="https://identity.example.test",
        oauth_grant_type="authorization_code",
        oauth_resource="https://foundry.example.test/mcp/release/app-1",
        oauth_token_issued_at=1_786_224_000,
        oauth_token_expires_at=1_786_224_900,
        is_human_oauth=True,
        token_scopes=(_RELEASE_SCOPE,),
    )

    tools = gateway.list_tools(external_human, "app-1")

    assert len(tools["tools"]) == 13


@pytest.mark.parametrize(
    "context_changes",
    [
        {"is_human_oauth": False},
        {"authorization_server_issuer": None},
        {"oauth_grant_type": None},
        {"oauth_resource": None},
        {"oauth_session_hash": None},
        {"actor_user_id": "service-account:chatgpt-client"},
    ],
)
def test_release_rejects_incomplete_external_human_identity(context_changes: dict[str, object]) -> None:
    gateway, _, _, _ = _gateway()
    values: dict[str, object] = {
        "actor_user_id": "external-reviewer-1",
        "roles": ("admin",),
        "application_id": "app-1",
        "client_id": "chatgpt-client",
        "oauth_session_id": "issuer-session:verified-binding",
        "oauth_session_hash": "oauth-session:sha256:verified-binding",
        "oauth_session_authority": "issuer",
        "authorization_server_issuer": "https://identity.example.test",
        "oauth_grant_type": "authorization_code",
        "oauth_resource": "https://foundry.example.test/mcp/release/app-1",
        "oauth_token_issued_at": 1_786_224_000,
        "oauth_token_expires_at": 1_786_224_900,
        "is_human_oauth": True,
        "token_scopes": (_RELEASE_SCOPE,),
    }
    values.update(context_changes)

    with pytest.raises(PermissionDenied):
        gateway.list_tools(RequestContext(**values), "app-1")  # type: ignore[arg-type]
