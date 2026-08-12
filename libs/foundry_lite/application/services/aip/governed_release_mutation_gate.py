"""Server-only provenance checks for protected operational mutations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from foundry_lite.application.services.aip.governed_release_authorization import GOVERNED_RELEASE_SCOPE
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied, ValidationFailed

JsonObject = Mapping[str, object]

_ACTION_RUN_KIND = "governed_release_mcp_action"
_RUN_PREFIX = "governed_release_run_"


@dataclass(frozen=True)
class GovernedReleaseMutationRequirement:
    """Exact release action allowed to cross one operational boundary."""

    release_kind: str
    tool_names: tuple[str, ...]
    required_permission: str
    should_match_proposal_resource: bool


_REQUIREMENTS = {
    "apply_ontology": GovernedReleaseMutationRequirement(
        "ontology", ("execute_approved_release",), "ontology:activate", False
    ),
    "rollback_ontology": GovernedReleaseMutationRequirement(
        "ontology", ("rollback_release",), "ontology:activate", False
    ),
    "execute_ontology_proposal": GovernedReleaseMutationRequirement(
        "ontology", ("execute_approved_release",), "ontology:activate", True
    ),
    "execute_pipeline_proposal": GovernedReleaseMutationRequirement(
        "pipeline", ("execute_approved_release",), "pipeline:deploy", True
    ),
    "deploy_pipeline_version": GovernedReleaseMutationRequirement(
        "pipeline", ("deploy_release", "rollback_release"), "pipeline:deploy", False
    ),
}


def release_execution_context(
    ctx: RequestContext,
    run_id: str,
    binding_hash: str,
    session_id: str,
    execution_attempt: int,
) -> RequestContext:
    """Attach non-transportable action provenance after the durable claim."""

    if (
        not run_id.startswith(_RUN_PREFIX)
        or not binding_hash.startswith("sha256:")
        or not session_id
        or execution_attempt < 0
    ):
        raise ValidationFailed("Governed Release action provenance is invalid")
    return replace(
        ctx,
        governed_release_run_id=run_id,
        governed_release_binding_hash=binding_hash,
        governed_release_session_id=session_id,
        governed_release_execution_attempt=execution_attempt,
    )


def mutation_requirement(operation: str) -> GovernedReleaseMutationRequirement | None:
    """Return the release requirement for an operational write, if any."""

    return _REQUIREMENTS.get(operation)


def is_authorized_release_run(
    run: JsonObject | None,
    ctx: RequestContext,
    requirement: GovernedReleaseMutationRequirement,
    resource_id: str,
) -> bool:
    """Verify the current durable action run and its immutable request binding."""

    if not _has_running_action_identity(run, ctx):
        return False
    budget = _mapping(run.get("budget_json")) if run is not None else None
    binding = _mapping(budget.get("requestBinding")) if budget is not None else None
    if budget is None or binding is None:
        return False
    return _binding_matches_context(binding, budget, ctx, requirement, resource_id)


def governed_release_required(operation: str) -> PermissionDenied:
    """Build a stable public denial without exposing ledger contents."""

    return PermissionDenied(
        "This operational mutation must run through Governed Release",
        details={"reason": "governed_release_required", "operation": operation},
    )


def _has_running_action_identity(run: JsonObject | None, ctx: RequestContext) -> bool:
    return bool(
        run
        and run.get("id") == ctx.governed_release_run_id
        and run.get("tenant_id") == ctx.tenant_id
        and run.get("actor_user_id") == ctx.actor_user_id
        and run.get("session_id") == ctx.governed_release_session_id
        and run.get("compiled_prompt_hash") == ctx.governed_release_binding_hash
        and run.get("status") == "running"
    )


def _binding_matches_context(
    binding: JsonObject,
    budget: JsonObject,
    ctx: RequestContext,
    requirement: GovernedReleaseMutationRequirement,
    resource_id: str,
) -> bool:
    expected_proposal = resource_id if requirement.should_match_proposal_resource else binding.get("proposalId")
    checks = (
        budget.get("kind") == _ACTION_RUN_KIND,
        budget.get("requestBindingHash") == ctx.governed_release_binding_hash,
        binding.get("tenantId") == ctx.tenant_id,
        binding.get("actorUserId") == ctx.actor_user_id,
        binding.get("applicationId") == ctx.application_id,
        binding.get("clientId") == ctx.client_id,
        binding.get("oauthSessionHash") == ctx.oauth_session_hash,
        binding.get("oauthSessionAuthority") == ctx.oauth_session_authority,
        binding.get("authorizationServerIssuer") == ctx.authorization_server_issuer,
        binding.get("oauthGrantType") == ctx.oauth_grant_type == "authorization_code",
        binding.get("oauthResource") == ctx.oauth_resource,
        binding.get("isHuman") is ctx.is_human_oauth is True,
        binding.get("requiredScope") == GOVERNED_RELEASE_SCOPE,
        GOVERNED_RELEASE_SCOPE in ctx.token_scopes,
        binding.get("oauthTokenIssuedAt") == ctx.oauth_token_issued_at,
        binding.get("oauthTokenExpiresAt") == ctx.oauth_token_expires_at,
        binding.get("sessionId") == ctx.governed_release_session_id,
        binding.get("releaseKind") == requirement.release_kind,
        binding.get("toolName") in requirement.tool_names,
        binding.get("requiredPermission") == requirement.required_permission,
        isinstance(binding.get("argumentsHash"), str),
        isinstance(binding.get("idempotencyKey"), str),
        _execution_attempt(budget) == ctx.governed_release_execution_attempt,
        binding.get("proposalId") == expected_proposal,
    )
    return all(checks)


def _mapping(value: object) -> JsonObject | None:
    return value if isinstance(value, Mapping) else None


def _execution_attempt(budget: JsonObject) -> int:
    recovery = _mapping(budget.get("recoveryLease"))
    attempt = recovery.get("attempt") if recovery is not None else None
    return attempt if isinstance(attempt, int) and attempt > 0 else 0


__all__ = [
    "GovernedReleaseMutationRequirement",
    "governed_release_required",
    "is_authorized_release_run",
    "mutation_requirement",
    "release_execution_context",
]
