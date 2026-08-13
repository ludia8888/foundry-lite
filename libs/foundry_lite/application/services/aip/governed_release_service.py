from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.services.aip.external_release_rollback import (
    verified_application_rollback_target,
)
from foundry_lite.application.services.aip.governed_release_arguments import mapping_items as _items
from foundry_lite.application.services.aip.governed_release_arguments import optional_text as _optional_text
from foundry_lite.application.services.aip.governed_release_arguments import release_kind as _release_kind
from foundry_lite.application.services.aip.governed_release_arguments import require_pipeline as _require_pipeline
from foundry_lite.application.services.aip.governed_release_arguments import required_integer as _required_integer
from foundry_lite.application.services.aip.governed_release_arguments import required_text as _required_text
from foundry_lite.application.services.aip.governed_release_candidate_evidence import (
    release_candidate_evidence,
)
from foundry_lite.application.services.aip.governed_release_evidence import (
    next_actions,
    ontology_release_evidence,
    pipeline_deploy_options,
    pipeline_release_evidence,
    release_candidate,
    release_stage,
    require_deployment_replay,
    require_proposal_identity,
    required_mapping,
    verified_pipeline_deploy_target,
    verified_rollback_target,
)
from foundry_lite.application.services.aip.governed_release_external_boundary import (
    ExternalReleaseDeliveryBoundary,
    require_external_delivery_result,
)
from foundry_lite.application.services.aip.governed_release_outcomes import (
    GovernedReleaseMutationOutcomeUnknown,
    project_confirmed_mutation,
)
from foundry_lite.application.services.aip.governed_release_service_boundaries import (
    GovernedReleaseCompletionBoundary,
    GovernedReleaseProposalReader,
    GovernedReleaseStatusBoundary,
    OntologyProposalBoundary,
    OntologyReleaseBoundary,
    PipelineReleaseBoundary,
)
from foundry_lite.application.services.aip.governed_release_status_evidence import (
    RELEASE_AUDIT_EVENT_TYPES,
    release_audit_resource_refs,
    release_status_evidence,
)
from foundry_lite.application.services.aip.governed_release_status_projection import (
    external_delivery_recovery_evidence,
    is_failed_application_candidate,
    operational_completion,
    unavailable_completion_coordinates,
)
from foundry_lite.application.services.ontology_proposal_recovery import OntologyProposalExecutionOutcomeUnknown
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, PermissionDenied, ValidationFailed

JsonObject = Mapping[str, object]


class GovernedReleaseService:
    required_dependencies: tuple[str, ...] = ()
    required_collaborators: tuple[str, ...] = ()
    proposal_reader: GovernedReleaseProposalReader
    ontology_proposals: OntologyProposalBoundary
    ontology: OntologyReleaseBoundary
    pipelines: PipelineReleaseBoundary
    status_reader: GovernedReleaseStatusBoundary | None
    external_delivery: ExternalReleaseDeliveryBoundary | None
    completion_reader: GovernedReleaseCompletionBoundary | None

    def __init__(
        self,
        *,
        proposal_reader: GovernedReleaseProposalReader,
        ontology_proposals: OntologyProposalBoundary,
        ontology: OntologyReleaseBoundary,
        pipelines: PipelineReleaseBoundary,
        status_reader: GovernedReleaseStatusBoundary | None = None,
        external_delivery: ExternalReleaseDeliveryBoundary | None = None,
        completion_reader: GovernedReleaseCompletionBoundary | None = None,
    ) -> None:
        self.proposal_reader = proposal_reader
        self.ontology_proposals = ontology_proposals
        self.ontology = ontology
        self.pipelines = pipelines
        self.status_reader = status_reader
        self.external_delivery = external_delivery
        self.completion_reader = completion_reader

    def get_candidate(self, ctx: RequestContext, arguments: JsonObject) -> dict[str, object]:
        return self._release_view(ctx, arguments)

    def get_status(self, ctx: RequestContext, arguments: JsonObject) -> dict[str, object]:
        view = self._status_view(ctx, arguments)
        view["observedAs"] = "current_status"
        return view

    def publish_candidate(self, ctx: RequestContext, arguments: JsonObject) -> dict[str, object]:
        kind = _release_kind(arguments)
        proposal_id = _required_text(arguments, "proposalId")
        proposal = self._proposal(ctx, kind, proposal_id)
        self._require_proposal_author(ctx, kind, proposal)
        if self.external_delivery is None:
            raise ConflictDetected("source candidate publication is not configured")
        result = self.external_delivery.publish_source_candidate(ctx, kind, proposal, arguments)
        require_external_delivery_result(result, "source_publish")
        return self._confirmed_snapshot(ctx, arguments, "source_candidate_published", result)

    def submit_decision(self, ctx: RequestContext, arguments: JsonObject) -> dict[str, object]:
        kind = _release_kind(arguments)
        proposal_id = _required_text(arguments, "proposalId")
        decision = _required_text(arguments, "decision")
        comment = _optional_text(arguments, "comment")
        proposal = self._proposal(ctx, kind, proposal_id)
        self._require_published_source_candidate(ctx, proposal)
        if kind == "pipeline":
            result = self.pipelines.decide_proposal(proposal_id, decision=decision, comment=comment, ctx=ctx)
        else:
            self._require_assigned_ontology_reviewer(ctx, proposal_id)
            result = self.ontology_proposals.decide_proposal(
                proposal_id,
                decision=decision,
                expected_fingerprint=_required_text(arguments, "expectedFingerprint"),
                comment=comment,
                ctx=ctx,
            )
        return self._confirmed_snapshot(ctx, arguments, "decision_recorded", result)

    def execute_approved(self, ctx: RequestContext, arguments: JsonObject) -> dict[str, object]:
        kind = _release_kind(arguments)
        proposal_id = _required_text(arguments, "proposalId")
        proposal = self._proposal(ctx, kind, proposal_id)
        source_result = (
            self.external_delivery.execute_source_merge(ctx, proposal, arguments)
            if self.external_delivery is not None
            else {"status": "skipped", "reason": "external_delivery_boundary_unavailable"}
        )
        require_external_delivery_result(source_result, "source_merge")
        try:
            result, stage = self._execute_internal_proposal(ctx, arguments, kind, proposal_id)
        except Exception as exc:
            if source_result.get("status") == "landed":
                raise GovernedReleaseMutationOutcomeUnknown("source_merged_internal_release", exc) from exc
            raise
        return self._confirmed_snapshot(ctx, arguments, stage, {"sourceMerge": source_result, "internal": result})

    def deploy(self, ctx: RequestContext, arguments: JsonObject) -> dict[str, object]:
        _require_pipeline(arguments)
        proposal_id = _required_text(arguments, "proposalId")
        source_commit = (
            self.external_delivery.deployment_source_commit(ctx, proposal_id)
            if self.external_delivery is not None
            else None
        )
        replay = self.pipelines.replay_deployment(_required_text(arguments, "idempotencyKey"), ctx=ctx)
        if replay is not None:
            self._require_pipeline_replay(ctx, arguments, replay, is_rollback=False)
            internal = replay
        else:
            internal = self._deploy_pipeline(ctx, arguments)
        try:
            infrastructure = (
                self.external_delivery.start_application_deployment(
                    ctx,
                    proposal_id,
                    source_commit,
                    _required_text(arguments, "idempotencyKey"),
                )
                if self.external_delivery is not None and source_commit is not None
                else {"status": "skipped", "reason": "application_delivery_not_configured"}
            )
            require_external_delivery_result(infrastructure, "application_deploy")
        except Exception as exc:
            raise GovernedReleaseMutationOutcomeUnknown("pipeline_promoted_application_deploy", exc) from exc
        return self._confirmed_snapshot(
            ctx,
            arguments,
            "deployed",
            {"internal": internal, "infrastructure": infrastructure},
        )

    def rollback(self, ctx: RequestContext, arguments: JsonObject) -> dict[str, object]:
        kind = _release_kind(arguments)
        external_target = self._external_rollback_target(ctx, arguments, kind)
        if kind == "ontology":
            replay = self._ontology_rollback_replay(ctx, arguments)
            if replay is not None:
                return self._confirmed_snapshot(ctx, arguments, "rollback_active", dict(replay))
        else:
            replay = self.pipelines.replay_deployment(_required_text(arguments, "idempotencyKey"), ctx=ctx)
            if replay is not None:
                self._require_pipeline_replay(ctx, arguments, replay, is_rollback=True)
                result = replay
            else:
                view = self._release_view(ctx, arguments)
                rollback_target = verified_rollback_target(arguments, view)
                result = self._rollback_pipeline(ctx, arguments, rollback_target)
            try:
                infrastructure = self._rollback_application(ctx, arguments, external_target)
                require_external_delivery_result(infrastructure, "application_rollback")
            except Exception as exc:
                raise GovernedReleaseMutationOutcomeUnknown("pipeline_rollback_application_rollback", exc) from exc
            return self._confirmed_snapshot(
                ctx,
                arguments,
                "rollback_deployed",
                {"internal": result, "infrastructure": infrastructure},
            )
        view = self._release_view(ctx, arguments)
        rollback_target = verified_rollback_target(arguments, view)
        ontology_result = self.ontology.rollback_to_version(
            _required_integer(rollback_target, "targetVersionNumber"),
            expected_active_version_number=_required_integer(
                rollback_target,
                "expectedActiveVersionNumber",
            ),
            idempotency_key=_required_text(arguments, "idempotencyKey"),
            ctx=ctx,
        )
        return self._confirmed_snapshot(ctx, arguments, "rollback_active", dict(ontology_result))

    def _ontology_rollback_replay(
        self,
        ctx: RequestContext,
        arguments: JsonObject,
    ) -> Mapping[str, object] | None:
        return self.ontology.replay_rollback(
            _required_integer(arguments, "targetVersionNumber"),
            expected_active_version_number=_required_integer(arguments, "expectedActiveVersionNumber"),
            idempotency_key=_required_text(arguments, "idempotencyKey"),
            ctx=ctx,
        )

    def _require_pipeline_replay(
        self,
        ctx: RequestContext,
        arguments: JsonObject,
        replay: JsonObject,
        *,
        is_rollback: bool,
    ) -> None:
        deployment = required_mapping(replay, "deployment")
        proposal = self._proposal(ctx, "pipeline", _required_text(arguments, "proposalId"))
        evidence = self._pipeline_evidence(ctx, proposal)
        require_deployment_replay(arguments, deployment, evidence, is_rollback=is_rollback)

    def _release_view(self, ctx: RequestContext, arguments: JsonObject) -> dict[str, object]:
        kind = _release_kind(arguments)
        proposal_id = _required_text(arguments, "proposalId")
        proposal = self._proposal(ctx, kind, proposal_id)
        release_evidence = (
            self._pipeline_evidence(ctx, proposal) if kind == "pipeline" else self._ontology_evidence(ctx, proposal)
        )
        if self.external_delivery is not None:
            release_evidence["externalSourceControl"] = self.external_delivery.source_evidence(ctx, proposal)
            delivery = self.external_delivery.delivery_evidence(ctx, proposal_id)
            release_evidence["externalDelivery"] = external_delivery_recovery_evidence(delivery)
        stage = release_stage(kind, proposal, release_evidence)
        release_evidence.update(release_candidate_evidence(kind, proposal, release_evidence))
        candidate = release_candidate(kind, proposal, ctx)
        if stage == "awaiting_review" and candidate.get("canCurrentUserClaim") is True:
            stage = "awaiting_assignment"
        can_publish = self._is_proposal_author(ctx, kind, proposal) and stage in {
            "awaiting_assignment",
            "awaiting_review",
        }
        candidate["canCurrentUserPublish"] = can_publish
        release_evidence["canCurrentUserPublish"] = can_publish
        release_evidence["canCurrentUserClaim"] = candidate.get("canCurrentUserClaim") is True
        release_evidence["canCurrentUserReview"] = candidate.get("canCurrentUserReview") is True
        return {
            "releaseKind": kind,
            "proposalId": proposal_id,
            "stage": stage,
            "candidate": candidate,
            "releaseEvidence": release_evidence,
            "rollbackTarget": release_evidence.get("rollbackTarget"),
            "nextActions": next_actions(kind, stage, release_evidence),
        }

    def _mutation_snapshot(
        self,
        ctx: RequestContext,
        arguments: JsonObject,
        operation: str,
        result: JsonObject,
    ) -> dict[str, object]:
        snapshot = self._status_view(ctx, arguments)
        snapshot["lastOperation"] = {
            "operation": operation,
            "idempotencyKey": arguments.get("idempotencyKey"),
            "result": dict(result),
        }
        return snapshot

    def _status_view(self, ctx: RequestContext, arguments: JsonObject) -> dict[str, object]:
        view = self._release_view(ctx, arguments)
        candidate = required_mapping(view, "candidate")
        evidence = dict(required_mapping(view, "releaseEvidence"))
        rows: list[Mapping[str, object]] = []
        if self.status_reader is not None:
            refs = release_audit_resource_refs(
                str(view["releaseKind"]),
                str(view["proposalId"]),
                candidate,
                evidence,
            )
            rows = self.status_reader.list_release_audit_events(
                refs,
                RELEASE_AUDIT_EVENT_TYPES,
                limit=100,
                ctx=ctx,
            )
        evidence.update(release_status_evidence(rows))
        view["releaseEvidence"] = evidence
        view["operationalCompletion"] = operational_completion(view)
        view["completionCoordinates"] = self._completion_coordinates(ctx, str(view.get("stage")))
        return view

    def _completion_coordinates(self, ctx: RequestContext, stage: str) -> dict[str, object]:
        if stage != "superseded":
            return unavailable_completion_coordinates("release_not_ready_for_rollback_rehearsal_attestation")
        application_id = ctx.application_id
        if self.completion_reader is not None and isinstance(application_id, str) and application_id:
            return self.completion_reader.completion_coordinates(ctx, application_id)
        return unavailable_completion_coordinates("server_completion_coordinate_reader_unavailable")

    def _confirmed_snapshot(
        self,
        ctx: RequestContext,
        arguments: JsonObject,
        operation: str,
        result: JsonObject,
    ) -> dict[str, object]:
        return project_confirmed_mutation(
            operation,
            lambda: self._mutation_snapshot(ctx, arguments, operation, result),
        )

    def _proposal(self, ctx: RequestContext, kind: str, proposal_id: str) -> dict[str, object]:
        proposal = self.proposal_reader.load_governed_release_proposal(ctx, kind, proposal_id)
        require_proposal_identity(proposal, proposal_id)
        return proposal

    def _pipeline_evidence(self, ctx: RequestContext, proposal: JsonObject) -> dict[str, object]:
        pipeline_id = _required_text(proposal, "pipelineId")
        versions = _items(self.pipelines.list_versions(pipeline_id, limit=50, ctx=ctx))
        deployments = _items(self.pipelines.list_deployments(pipeline_id, limit=50, ctx=ctx))
        return pipeline_release_evidence(pipeline_id, proposal, versions, deployments)

    def _ontology_evidence(self, ctx: RequestContext, proposal: JsonObject) -> dict[str, object]:
        active = dict(self.ontology.release_active_version_summary(ctx=ctx))
        return ontology_release_evidence(active, proposal)

    def _require_assigned_ontology_reviewer(self, ctx: RequestContext, proposal_id: str) -> None:
        proposal = self._proposal(ctx, "ontology", proposal_id)
        assignee = proposal.get("assigneeUserId")
        if not isinstance(assignee, str) or not assignee.strip():
            raise ValidationFailed(
                "ontology proposal must be assigned before review",
                details={"proposalId": proposal_id},
            )
        if assignee != ctx.actor_user_id:
            raise PermissionDenied(
                "only the assigned human reviewer can decide this ontology proposal",
                details={"proposalId": proposal_id, "assigneeUserId": assignee},
            )

    def _require_proposal_author(self, ctx: RequestContext, kind: str, proposal: JsonObject) -> None:
        field = "createdBy" if kind == "pipeline" else "submittedByUserId"
        author = proposal.get(field)
        if not isinstance(author, str) or not author:
            raise ConflictDetected("release proposal author identity is unavailable")
        if author != ctx.actor_user_id:
            raise PermissionDenied(
                "only the proposal author can publish its source candidate",
                details={"proposalId": proposal.get("id")},
            )
        if proposal.get("status") not in {"submitted", "in_review"}:
            raise ConflictDetected("source candidate publication requires a proposal awaiting review")

    def _is_proposal_author(self, ctx: RequestContext, kind: str, proposal: JsonObject) -> bool:
        field = "createdBy" if kind == "pipeline" else "submittedByUserId"
        return proposal.get(field) == ctx.actor_user_id

    def _require_published_source_candidate(self, ctx: RequestContext, proposal: JsonObject) -> None:
        if self.external_delivery is None:
            return
        evidence = self.external_delivery.source_evidence(ctx, proposal)
        if evidence.get("isConfigured") is not True:
            if evidence.get("isRequired") is True:
                raise ConflictDetected("required source candidate publication is not configured")
            return
        publication = evidence.get("publication")
        if not isinstance(publication, Mapping) or publication.get("receiptStatus") != "published":
            raise ConflictDetected("release decision requires an exact published source candidate")

    def _rollback_pipeline(
        self,
        ctx: RequestContext,
        arguments: JsonObject,
        rollback_target: JsonObject,
    ) -> dict[str, object]:
        return self.pipelines.deploy(
            _required_text(rollback_target, "pipelineId"),
            _required_text(rollback_target, "targetVersionId"),
            idempotency_key=_required_text(arguments, "idempotencyKey"),
            options={
                "rolledBackFromId": _required_text(rollback_target, "rolledBackFromId"),
                "expectedCurrentDeploymentId": _required_text(rollback_target, "rolledBackFromId"),
            },
            ctx=ctx,
        )

    def _execute_internal_proposal(
        self,
        ctx: RequestContext,
        arguments: JsonObject,
        kind: str,
        proposal_id: str,
    ) -> tuple[dict[str, object], str]:
        if kind == "pipeline":
            return self.pipelines.execute_proposal(proposal_id, ctx=ctx), "merged"
        try:
            result = self.ontology_proposals.execute_proposal(
                proposal_id,
                expected_fingerprint=_required_text(arguments, "expectedFingerprint"),
                ctx=ctx,
            )
        except OntologyProposalExecutionOutcomeUnknown as exc:
            raise GovernedReleaseMutationOutcomeUnknown("active", exc.original) from exc
        return result, "active"

    def _deploy_pipeline(self, ctx: RequestContext, arguments: JsonObject) -> dict[str, object]:
        proposal = self._proposal(ctx, "pipeline", _required_text(arguments, "proposalId"))
        evidence = self._pipeline_evidence(ctx, proposal)
        pipeline_id, version_id = verified_pipeline_deploy_target(arguments, proposal, evidence)
        return self.pipelines.deploy(
            pipeline_id,
            version_id,
            idempotency_key=_required_text(arguments, "idempotencyKey"),
            options=pipeline_deploy_options(evidence),
            ctx=ctx,
        )

    def _external_rollback_target(
        self,
        ctx: RequestContext,
        arguments: JsonObject,
        kind: str,
    ) -> dict[str, object] | None:
        if kind != "pipeline" or self.external_delivery is None:
            return None
        if not self.external_delivery.is_application_delivery_enabled():
            return None
        delivery = self.external_delivery.delivery_evidence(
            ctx,
            _required_text(arguments, "proposalId"),
        )
        if is_failed_application_candidate(delivery):
            return None
        target = self.external_delivery.application_rollback_target(
            ctx,
            _required_text(arguments, "proposalId"),
            _required_text(arguments, "idempotencyKey"),
        )
        if target is None:
            raise ConflictDetected("application deployment has no verified rollback target")
        return verified_application_rollback_target(arguments, target)

    def _rollback_application(
        self,
        ctx: RequestContext,
        arguments: JsonObject,
        target: JsonObject | None,
    ) -> dict[str, object]:
        if self.external_delivery is None:
            return {"status": "skipped", "reason": "application_delivery_not_configured"}
        if not self.external_delivery.is_application_delivery_enabled():
            return {"status": "skipped", "reason": "application_delivery_not_configured"}
        if target is None:
            return {
                "status": "skipped",
                "reason": "failed_candidate_never_became_live_existing_deploy_preserved",
            }
        return self.external_delivery.start_application_rollback(
            ctx,
            _required_text(arguments, "proposalId"),
            target,
            _required_text(arguments, "idempotencyKey"),
        )


__all__ = ["GovernedReleaseProposalReader", "GovernedReleaseService", "GovernedReleaseStatusBoundary"]
