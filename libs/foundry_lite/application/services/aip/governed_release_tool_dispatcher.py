"""Route governed-release MCP tools to their bounded use-case services."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from foundry_lite.application.services.aip.governed_release_mcp_results import project_confirmed_mutation
from foundry_lite.application.services.aip.governed_release_mutation_gate import release_execution_context
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed

JsonObject = Mapping[str, object]


class GovernedReleaseOperations(Protocol):
    """Release operations required by the MCP routing boundary."""

    def get_candidate(self, ctx: RequestContext, arguments: JsonObject) -> dict[str, object]:
        """Return the current candidate, validation, and review projection."""
        ...

    def get_status(self, ctx: RequestContext, arguments: JsonObject) -> dict[str, object]:
        """Return the durable release timeline and next permitted actions."""
        ...

    def submit_decision(self, ctx: RequestContext, arguments: JsonObject) -> dict[str, object]:
        """Submit the assigned human reviewer's decision for a published candidate."""
        ...

    def publish_candidate(self, ctx: RequestContext, arguments: JsonObject) -> dict[str, object]:
        """Publish the proposal author's exact source candidate for review."""
        ...

    def execute_approved(self, ctx: RequestContext, arguments: JsonObject) -> dict[str, object]:
        """Execute an approved proposal and its required source merge."""
        ...

    def deploy(self, ctx: RequestContext, arguments: JsonObject) -> dict[str, object]:
        """Deploy the exact released version or landed application commit."""
        ...

    def rollback(self, ctx: RequestContext, arguments: JsonObject) -> dict[str, object]:
        """Roll back to a verified prior ontology, pipeline, or application target."""
        ...


class GovernedReleaseWorkflowOperations(Protocol):
    """Branch and review operations required by the MCP routing boundary."""

    def open_workspace(self, ctx: RequestContext, arguments: JsonObject) -> dict[str, object]:
        """Open the release workspace with available branch and review actions."""
        ...

    def list_inbox(self, ctx: RequestContext, arguments: JsonObject) -> dict[str, object]:
        """List proposals visible to the current author or reviewer."""
        ...

    def create_branch(self, ctx: RequestContext, arguments: JsonObject) -> dict[str, object]:
        """Create an isolated ontology or pipeline release branch."""
        ...

    def assign_reviewer(self, ctx: RequestContext, arguments: JsonObject) -> dict[str, object]:
        """Assign the current human as reviewer of a submitted proposal."""
        ...


class GovernedReleaseLiveAttestationOperations(Protocol):
    """Server-owned authentic completion collector exposed to the MCP gateway."""

    def collect_and_store_server_verified(
        self,
        ctx: RequestContext,
        application_id: str,
        ontology_workflow_run_id: str,
        pipeline_workflow_run_id: str,
    ) -> dict[str, object]: ...


def execute_governed_release_read(
    release_service: GovernedReleaseOperations,
    workflow_service: GovernedReleaseWorkflowOperations,
    ctx: RequestContext,
    tool_name: str,
    arguments: JsonObject,
) -> dict[str, object]:
    """Dispatch one read-only release tool without exposing service fan-out to the gateway."""

    if tool_name == "open_release_workspace":
        return workflow_service.open_workspace(ctx, arguments)
    if tool_name == "list_release_inbox":
        return workflow_service.list_inbox(ctx, arguments)
    if tool_name == "get_release_candidate":
        return release_service.get_candidate(ctx, arguments)
    if tool_name == "get_release_status":
        return release_service.get_status(ctx, arguments)
    raise ValidationFailed("Governed Release MCP read tool is not implemented")


def dispatch_governed_release_action(
    release_service: GovernedReleaseOperations,
    workflow_service: GovernedReleaseWorkflowOperations,
    live_attestation_service: GovernedReleaseLiveAttestationOperations,
    ctx: RequestContext,
    application_id: str,
    tool_name: str,
    arguments: JsonObject,
    *,
    run_id: str,
    binding_hash: str,
    session_id: str,
    execution_attempt: int,
) -> dict[str, object]:
    """Bind durable admission evidence and dispatch one governed mutation."""

    action_ctx = release_execution_context(ctx, run_id, binding_hash, session_id, execution_attempt)
    if tool_name == "create_release_branch":
        return workflow_service.create_branch(action_ctx, arguments)
    if tool_name == "assign_release_reviewer":
        workflow_service.assign_reviewer(action_ctx, arguments)
        return project_confirmed_mutation(
            "assign_release_reviewer",
            lambda: release_service.get_candidate(action_ctx, arguments),
        )
    if tool_name == "publish_release_candidate":
        return release_service.publish_candidate(action_ctx, arguments)
    if tool_name == "submit_release_decision":
        return release_service.submit_decision(action_ctx, arguments)
    if tool_name == "execute_approved_release":
        return release_service.execute_approved(action_ctx, arguments)
    if tool_name == "deploy_release":
        return release_service.deploy(action_ctx, arguments)
    if tool_name == "rollback_release":
        return release_service.rollback(action_ctx, arguments)
    if tool_name == "verify_release_completion":
        return _verify_release_completion(live_attestation_service, action_ctx, application_id, arguments)
    raise ValidationFailed("Governed Release MCP action tool is not implemented")


def _verify_release_completion(
    service: GovernedReleaseLiveAttestationOperations,
    ctx: RequestContext,
    application_id: str,
    arguments: JsonObject,
) -> dict[str, object]:
    return service.collect_and_store_server_verified(
        ctx,
        application_id,
        str(arguments["ontologyWorkflowRunId"]),
        str(arguments["pipelineWorkflowRunId"]),
    )


__all__ = [
    "GovernedReleaseOperations",
    "GovernedReleaseLiveAttestationOperations",
    "GovernedReleaseWorkflowOperations",
    "dispatch_governed_release_action",
    "execute_governed_release_read",
]
