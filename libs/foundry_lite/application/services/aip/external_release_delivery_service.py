"""Durable source-merge and infrastructure-deployment orchestration."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.dependency_release import (
    GovernedReleaseDeliveryConfig,
    InfrastructureDeploymentAdapter,
    PullRequestSnapshot,
    PullRequestTarget,
    ReleaseDeliveryOperation,
    ReleaseDeliveryRecord,
    ReleaseDeliveryRepository,
    SourceControlReleasePort,
)
from foundry_lite.application.services.aip.external_release_delivery_ledger import ExternalReleaseDeliveryLedger
from foundry_lite.application.services.aip.external_release_delivery_lineage import (
    ExternalReleaseDeliveryLineageResolver,
    ReleaseDeliveryLineage,
    require_delivery_lineage,
)
from foundry_lite.application.services.aip.external_release_delivery_payloads import (
    application_delivery_summary,
    delivery_projection,
    source_candidate_ref,
    source_delivery_evidence,
    source_target_ref,
)
from foundry_lite.application.services.aip.external_release_delivery_support import (
    ExternalReleaseDeliveryOutcomeUnknown,
)
from foundry_lite.application.services.aip.external_release_delivery_support import (
    application_rollback_target as _application_rollback_target,
)
from foundry_lite.application.services.aip.external_release_delivery_support import (
    application_rollback_target_for_request as _application_rollback_target_for_request,
)
from foundry_lite.application.services.aip.external_release_delivery_support import (
    prepare_command as _prepare_command,
)
from foundry_lite.application.services.aip.external_release_delivery_support import (
    proposal_id as _proposal_id,
)
from foundry_lite.application.services.aip.external_release_delivery_support import (
    provider_name as _provider_name,
)
from foundry_lite.application.services.aip.external_release_delivery_support import (
    require_snapshot_binding as _require_snapshot_binding,
)
from foundry_lite.application.services.aip.external_release_delivery_support import (
    require_source_replay_binding as _require_source_replay_binding,
)
from foundry_lite.application.services.aip.external_release_delivery_support import (
    required_text as _required_text,
)
from foundry_lite.application.services.aip.external_release_infrastructure_coordinator import (
    ExternalReleaseInfrastructureCoordinator,
)
from foundry_lite.application.services.aip.external_release_source_coordinator import (
    ExternalReleaseSourceCoordinator,
)
from foundry_lite.application.services.aip.external_release_source_evidence import (
    source_snapshot_evidence_or_failure as _source_snapshot_evidence_or_failure,
)
from foundry_lite.application.services.aip.external_release_source_evidence import (
    unconfigured_source_evidence as _unconfigured_source_evidence,
)
from foundry_lite.application.services.aip.external_release_source_evidence import (
    unpublished_source_evidence as _unpublished_source_evidence,
)
from foundry_lite.application.services.aip.external_release_source_publication import (
    ExternalReleaseSourcePublicationWorkflow,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.runtime_evidence_boundary import RuntimeEvidenceBoundary
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed

JsonObject = Mapping[str, object]


class ExternalReleaseDeliveryService(CoreService):
    """Write intent before provider HTTP and reconcile every ambiguous outcome."""

    required_dependencies = (
        "engine",
        "governed_release_delivery_config",
        "release_delivery_repository",
        "source_control_release_adapter",
        "infrastructure_deployment_adapter",
    )
    required_collaborators = ("runtime_service",)
    governed_release_delivery_config: GovernedReleaseDeliveryConfig
    release_delivery_repository: ReleaseDeliveryRepository
    source_control_release_adapter: SourceControlReleasePort
    infrastructure_deployment_adapter: InfrastructureDeploymentAdapter
    runtime_service: RuntimeEvidenceBoundary

    def source_evidence(self, ctx: RequestContext, proposal: JsonObject) -> dict[str, object]:
        """Read exact PR-head and provider-selected CI evidence without mutation."""

        config = self.governed_release_delivery_config
        if not config.is_source_control_enabled:
            return _unconfigured_source_evidence(config.is_source_control_required)
        stored = self._latest_delivery(ctx, _proposal_id(proposal), "source_merge")
        publication = self._latest_delivery(ctx, _proposal_id(proposal), "source_publish")
        if stored is not None:
            if stored.parent_delivery_id is not None:
                publication = self._ledger().get(ctx, stored.parent_delivery_id)
            evidence = source_delivery_evidence(stored, is_required=config.is_source_control_required)
            if publication is not None and publication.status == "landed":
                evidence["publication"] = delivery_projection(publication)
            return evidence
        if publication is None:
            return _unpublished_source_evidence(config.is_source_control_required)
        if publication.status != "landed":
            return source_delivery_evidence(publication, is_required=config.is_source_control_required)
        evidence = _source_snapshot_evidence_or_failure(
            lambda: self._source_publication().fresh_snapshot(ctx, proposal),
            config.is_source_control_required,
        )
        if evidence["status"] != "unavailable":
            evidence["publication"] = delivery_projection(publication)
        return evidence

    def publish_source_candidate(
        self,
        ctx: RequestContext,
        release_kind: str,
        proposal: JsonObject,
        arguments: JsonObject,
    ) -> dict[str, object]:
        """Publish one exact proposal manifest through a durable write-ahead intent."""

        config = self.governed_release_delivery_config
        if not config.is_source_control_enabled:
            if config.is_source_control_required:
                raise ConflictDetected("required source-control release is not configured")
            return {"status": "skipped", "reason": "source_control_not_configured"}
        return self._source_publication().publish(ctx, release_kind, proposal, arguments)

    def execute_source_merge(
        self,
        ctx: RequestContext,
        proposal: JsonObject,
        arguments: JsonObject,
    ) -> dict[str, object]:
        """Merge one fresh exact-head PR through the durable delivery ledger."""

        config = self.governed_release_delivery_config
        if not config.is_source_control_enabled:
            if config.is_source_control_required:
                raise ConflictDetected("required source-control release is not configured")
            return {"status": "skipped", "reason": "source_control_not_configured"}
        replay = self._source_replay(ctx, proposal, arguments)
        if replay is not None:
            if replay.status == "landed":
                self._lineage().for_replay(ctx, replay, ("source_publish",))
                return self._resume_source_merge(ctx, replay, None)
            publication, snapshot = self._source_publication().fresh_snapshot_with_publication(
                ctx, proposal, replay.parent_delivery_id
            )
            lineage = self._lineage().for_parent(ctx, _proposal_id(proposal), publication, ("source_publish",))
            require_delivery_lineage(replay, lineage)
            _require_snapshot_binding(snapshot, arguments)
            return self._resume_source_merge(ctx, replay, snapshot.target)
        publication, snapshot = self._source_publication().fresh_snapshot_with_publication(ctx, proposal)
        _require_snapshot_binding(snapshot, arguments)
        if not snapshot.is_ready_to_merge:
            raise ConflictDetected(
                "source pull request is not ready to merge",
                details={"blockingReasons": list(snapshot.blocking_reasons)},
            )
        lineage = self._lineage().for_parent(ctx, _proposal_id(proposal), publication, ("source_publish",))
        row = self._prepare_source_merge(ctx, proposal, snapshot, arguments, lineage)
        return self._resume_source_merge(ctx, row, snapshot.target)

    def start_application_deployment(
        self,
        ctx: RequestContext,
        proposal_id: str,
        commit_id: str,
        idempotency_key: str,
    ) -> dict[str, object]:
        """Start the server-bound infrastructure service at one merged commit."""

        config = self.governed_release_delivery_config
        if not config.is_deployment_enabled:
            if config.is_deployment_required:
                raise ConflictDetected("required infrastructure deployment is not configured")
            return {"status": "skipped", "reason": "deployment_not_configured"}
        provider = _provider_name(self.infrastructure_deployment_adapter.profile_name)
        replay = self._ledger().find_by_idempotency(ctx, provider, "application_deploy", idempotency_key)
        lineage = self._lineage().for_application_deploy(ctx, proposal_id, commit_id, replay)
        row = self._prepare_application_delivery(
            ctx,
            proposal_id,
            "application_deploy",
            {"commitId": commit_id},
            idempotency_key,
            lineage,
        )
        return self._resume_application_delivery(ctx, row)

    def start_application_rollback(
        self,
        ctx: RequestContext,
        proposal_id: str,
        target: JsonObject,
        idempotency_key: str,
    ) -> dict[str, object]:
        """Start a provider rollback to the exact prior deployment receipt."""

        target_deploy_id = _required_text(target, "targetDeployId")
        target_commit_id = _required_text(target, "targetCommitId")
        current_deploy_id = _required_text(target, "rolledBackFromDeployId")
        provider = _provider_name(self.infrastructure_deployment_adapter.profile_name)
        replay = self._ledger().find_by_idempotency(ctx, provider, "application_rollback", idempotency_key)
        lineage = self._lineage().for_application_rollback(ctx, proposal_id, current_deploy_id, replay)
        row = self._prepare_application_delivery(
            ctx,
            proposal_id,
            "application_rollback",
            {"targetDeployId": target_deploy_id, "targetCommitId": target_commit_id},
            idempotency_key,
            lineage,
            prior_resource_id=current_deploy_id,
        )
        return self._resume_application_delivery(ctx, row)

    def delivery_evidence(self, ctx: RequestContext, proposal_id: str) -> dict[str, object]:
        """Read local receipts plus current provider observations without local writes."""

        rows = self._proposal_deliveries(ctx, proposal_id)
        projected = [delivery_projection(row) for row in rows]
        raw_observations = [self._deployment_observation(ctx, row) for row in rows]
        observations = [item for item in raw_observations if item is not None]
        config = self.governed_release_delivery_config
        return {
            "isConfigured": config.is_deployment_enabled,
            "isRequired": config.is_deployment_required,
            "deliveries": projected,
            "deploymentObservations": observations,
            "applicationStatus": application_delivery_summary(
                rows,
                observations,
                is_configured=config.is_deployment_enabled,
                is_required=config.is_deployment_required,
            ),
            "applicationRollbackTarget": _application_rollback_target(rows),
        }

    def require_source_merge_commit(self, ctx: RequestContext, proposal_id: str) -> str:
        """Return the exact landed merge commit used for application deployment."""

        row = self._latest_delivery(ctx, proposal_id, "source_merge")
        if row is None or row.status != "landed":
            raise ConflictDetected("application deployment requires a landed source merge receipt")
        result = row.result_ref
        commit_id = result.get("mergeCommitSha") if isinstance(result, Mapping) else None
        if not isinstance(commit_id, str) or not commit_id:
            raise ConflictDetected("application deployment requires a landed source merge receipt")
        self._lineage().for_replay(ctx, row, ("source_publish",))
        return commit_id

    def deployment_source_commit(self, ctx: RequestContext, proposal_id: str) -> str | None:
        if not self.governed_release_delivery_config.is_deployment_enabled:
            return None
        return self.require_source_merge_commit(ctx, proposal_id)

    def is_application_delivery_enabled(self) -> bool:
        return self.governed_release_delivery_config.is_deployment_enabled

    def application_rollback_target(
        self,
        ctx: RequestContext,
        proposal_id: str,
        idempotency_key: str | None = None,
    ) -> dict[str, object] | None:
        rows = self._proposal_deliveries(ctx, proposal_id)
        if idempotency_key is None:
            return _application_rollback_target(rows)
        return _application_rollback_target_for_request(rows, idempotency_key)

    def _source_replay(
        self,
        ctx: RequestContext,
        proposal: JsonObject,
        arguments: JsonObject,
    ) -> ReleaseDeliveryRecord | None:
        config = self.governed_release_delivery_config
        repository = config.source_repository
        if repository is None:
            raise ValidationFailed("source-control repository is not configured")
        row = self._ledger().find_by_idempotency(
            ctx,
            repository.provider,
            "source_merge",
            _required_text(arguments, "idempotencyKey"),
        )
        if row is not None:
            parent_id = row.parent_delivery_id
            publication = self._ledger().get(ctx, parent_id) if parent_id is not None else None
            if publication is None:
                raise ConflictDetected("source release replay parent is unavailable")
            expected_head_ref = _required_text(publication.target_ref, "headRef")
            _require_source_replay_binding(
                row,
                ctx,
                _proposal_id(proposal),
                repository,
                config.source_base_ref,
                expected_head_ref,
                arguments,
            )
        return row

    def _prepare_source_merge(
        self,
        ctx: RequestContext,
        proposal: JsonObject,
        snapshot: PullRequestSnapshot,
        arguments: JsonObject,
        lineage: ReleaseDeliveryLineage,
    ) -> ReleaseDeliveryRecord:
        target = source_target_ref(snapshot.target)
        candidate = source_candidate_ref(snapshot)
        return self._prepare_delivery(
            ctx,
            proposal_id=_proposal_id(proposal),
            provider=snapshot.target.repository.provider,
            operation="source_merge",
            target_ref=target,
            candidate_ref=candidate,
            environment=snapshot.target.expected_base_ref,
            idempotency_key=_required_text(arguments, "idempotencyKey"),
            prior_resource_id=None,
            lineage=lineage,
        )

    def _prepare_application_delivery(
        self,
        ctx: RequestContext,
        proposal_id: str,
        operation: ReleaseDeliveryOperation,
        candidate_ref: dict[str, object],
        idempotency_key: str,
        lineage: ReleaseDeliveryLineage,
        *,
        prior_resource_id: str | None = None,
    ) -> ReleaseDeliveryRecord:
        config = self.governed_release_delivery_config
        service_id = config.deployment_service_id
        if not service_id:
            raise ConflictDetected("infrastructure deployment is not configured")
        return self._prepare_delivery(
            ctx,
            proposal_id=proposal_id,
            provider=_provider_name(self.infrastructure_deployment_adapter.profile_name),
            operation=operation,
            target_ref={"serviceId": service_id},
            candidate_ref=candidate_ref,
            environment=config.deployment_environment,
            idempotency_key=idempotency_key,
            prior_resource_id=prior_resource_id,
            lineage=lineage,
        )

    def _prepare_delivery(
        self,
        ctx: RequestContext,
        *,
        proposal_id: str,
        provider: str,
        operation: ReleaseDeliveryOperation,
        target_ref: dict[str, object],
        candidate_ref: dict[str, object],
        environment: str,
        idempotency_key: str,
        prior_resource_id: str | None,
        lineage: ReleaseDeliveryLineage,
    ) -> ReleaseDeliveryRecord:
        command = _prepare_command(
            ctx,
            proposal_id,
            provider,
            operation,
            target_ref,
            candidate_ref,
            environment,
            idempotency_key,
            prior_resource_id,
            lineage,
        )
        return self._ledger().prepare(ctx, command)

    def _resume_source_merge(
        self,
        ctx: RequestContext,
        row: ReleaseDeliveryRecord,
        target: PullRequestTarget | None,
    ) -> dict[str, object]:
        return self._source_coordinator().resume(ctx, row, target)

    def _resume_application_delivery(
        self,
        ctx: RequestContext,
        row: ReleaseDeliveryRecord,
    ) -> dict[str, object]:
        return self._infrastructure().resume(ctx, row)

    def _proposal_deliveries(self, ctx: RequestContext, proposal_id: str) -> tuple[ReleaseDeliveryRecord, ...]:
        return self._ledger().list_for_proposal(ctx, proposal_id)

    def _latest_delivery(
        self,
        ctx: RequestContext,
        proposal_id: str,
        operation: ReleaseDeliveryOperation,
    ) -> ReleaseDeliveryRecord | None:
        return self._ledger().latest(ctx, proposal_id, operation)

    def _ledger(self) -> ExternalReleaseDeliveryLedger:
        return ExternalReleaseDeliveryLedger(
            self.engine,
            self.release_delivery_repository,
            self.runtime_service,
        )

    def _lineage(self) -> ExternalReleaseDeliveryLineageResolver:
        return ExternalReleaseDeliveryLineageResolver(self._ledger())

    def _infrastructure(self) -> ExternalReleaseInfrastructureCoordinator:
        config = self.governed_release_delivery_config
        repository = config.source_repository
        return ExternalReleaseInfrastructureCoordinator(
            self.infrastructure_deployment_adapter,
            repository.owner if repository is not None else None,
            repository.name if repository is not None else None,
            config.source_base_ref,
            self._ledger(),
            self.runtime_service,
        )

    def _source_coordinator(self) -> ExternalReleaseSourceCoordinator:
        return ExternalReleaseSourceCoordinator(
            self.source_control_release_adapter,
            self.infrastructure_deployment_adapter,
            self.governed_release_delivery_config,
            self._ledger(),
            self.runtime_service,
        )

    def _source_publication(self) -> ExternalReleaseSourcePublicationWorkflow:
        return ExternalReleaseSourcePublicationWorkflow(
            self.source_control_release_adapter,
            self.governed_release_delivery_config,
            self._ledger(),
            self.runtime_service,
        )

    def _deployment_observation(
        self,
        ctx: RequestContext,
        row: ReleaseDeliveryRecord,
    ) -> dict[str, object] | None:
        return self._infrastructure().observation(ctx, row)


__all__ = ["ExternalReleaseDeliveryOutcomeUnknown", "ExternalReleaseDeliveryService"]
