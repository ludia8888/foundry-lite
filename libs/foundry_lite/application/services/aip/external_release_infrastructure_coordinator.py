"""Provider-specific application deploy, observation, and reconciliation flow."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import NoReturn, cast

from foundry_lite.application.ports.adapter_failure import AdapterError, adapter_failure_payload
from foundry_lite.application.ports.governed_release_delivery_config import GovernedReleaseDeliveryConfig
from foundry_lite.application.ports.infrastructure_deployment_adapter import (
    InfrastructureDeploymentAdapter,
    InfrastructureDeploymentCandidateQuery,
    InfrastructureDeploymentGetRequest,
    InfrastructureDeploymentMutationResult,
    InfrastructureDeploymentObservation,
    InfrastructureDeploymentOutcomeUnknown,
    InfrastructureDeploymentRollbackRequest,
    InfrastructureDeploymentStartRequest,
)
from foundry_lite.application.ports.release_delivery_repository import (
    ReleaseDeliveryRecord,
    ReleaseDeliveryTerminalStatus,
)
from foundry_lite.application.services.aip.external_release_delivery_ledger import ExternalReleaseDeliveryLedger
from foundry_lite.application.services.aip.external_release_delivery_payloads import (
    delivery_projection,
    deployment_observation_ref,
)
from foundry_lite.application.services.aip.external_release_delivery_state import (
    infrastructure_receipt_state,
)
from foundry_lite.application.services.aip.external_release_delivery_support import (
    ExternalReleaseDeliveryOutcomeUnknown,
    delivery_commit_id,
    delivery_dispatch_started_at,
    is_settled_window,
    required_candidate_text,
    required_text,
    strict_rollback_reconciliation_candidates,
    trace_identity,
)
from foundry_lite.application.services.aip.external_release_infrastructure_evidence import (
    ManualDeploymentPolicyFailure,
    infrastructure_receipt_matches_delivery,
    require_manual_deployment_policy_for_service,
    strict_deploy_reconciliation_candidates,
)
from foundry_lite.application.services.runtime_evidence_boundary import RuntimeEvidenceBoundary
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected

_KNOWN_NOT_COMMITTED_KINDS = frozenset(
    {"validation", "authentication", "authorization", "rate_limited", "conflict", "unsupported", "not_found"}
)


class ExternalReleaseInfrastructureCoordinator:
    """Drive one already-prepared infrastructure intent to a durable outcome."""

    def __init__(
        self,
        adapter: InfrastructureDeploymentAdapter,
        config: GovernedReleaseDeliveryConfig,
        ledger: ExternalReleaseDeliveryLedger,
        runtime_service: RuntimeEvidenceBoundary,
    ) -> None:
        self._adapter = adapter
        self._config = config
        self._ledger = ledger
        self._runtime_service = runtime_service

    def resume(self, ctx: RequestContext, row: ReleaseDeliveryRecord) -> dict[str, object]:
        if row.status == "landed":
            return delivery_projection(row)
        if row.status in {"dispatching", "ambiguous"}:
            reconciled = self._reconcile(ctx, row)
            return delivery_projection(self._require_accepted(reconciled))
        if row.status in {"failed", "absent"}:
            raise ConflictDetected("infrastructure delivery previously reached a known non-success outcome")
        self._require_policy(ctx, row)
        claimed = self._ledger.claim(ctx, row)
        if claimed is None:
            raise ExternalReleaseDeliveryOutcomeUnknown("infrastructure dispatch fence was lost")
        return delivery_projection(self._dispatch(ctx, claimed))

    def _require_policy(self, ctx: RequestContext, row: ReleaseDeliveryRecord) -> None:
        service_id = required_text(row.target_ref, "serviceId")
        if self._config.source_repository is None:
            raise ConflictDetected("infrastructure delivery has no source repository binding")
        try:
            require_manual_deployment_policy_for_service(
                ctx,
                row,
                service_id,
                self._adapter,
                self._config,
            )
        except ManualDeploymentPolicyFailure as exc:
            raise ConflictDetected(
                f"infrastructure delivery {row.delivery_id} blocked because manual deployment policy was not verified",
                details=exc.error_ref,
            ) from exc

    def observation(self, ctx: RequestContext, row: ReleaseDeliveryRecord) -> dict[str, object] | None:
        if row.operation not in {"application_deploy", "application_rollback"} or not row.provider_resource_id:
            return None
        try:
            observed = self._adapter.get(
                InfrastructureDeploymentGetRequest(
                    ctx.tenant_id,
                    required_text(row.target_ref, "serviceId"),
                    row.provider_resource_id,
                    ctx.request_id,
                    row.ai_run_id,
                )
            )
            return {"deliveryId": row.delivery_id, **deployment_observation_ref(observed)}
        except AdapterError as exc:
            return {
                "deliveryId": row.delivery_id,
                "status": "observation_unavailable",
                "error": adapter_failure_payload(exc),
            }

    def _dispatch(self, ctx: RequestContext, row: ReleaseDeliveryRecord) -> ReleaseDeliveryRecord:
        try:
            result = self._invoke(ctx, row)
        except InfrastructureDeploymentOutcomeUnknown as exc:
            return self._finish_unknown(ctx, row, exc)
        except AdapterError as exc:
            return self._finish_adapter_error(ctx, row, exc)
        except Exception as exc:
            return self._finish_unknown(ctx, row, exc)
        return self._finish_result(ctx, row, result)

    def _invoke(
        self,
        ctx: RequestContext,
        row: ReleaseDeliveryRecord,
    ) -> InfrastructureDeploymentMutationResult:
        service_id = required_text(row.target_ref, "serviceId")
        trace = trace_identity(ctx)
        if row.operation == "application_deploy":
            return self._adapter.start(
                InfrastructureDeploymentStartRequest(
                    ctx.tenant_id,
                    service_id,
                    required_candidate_text(row, "commitId"),
                    row.idempotency_key,
                    ctx.request_id,
                    trace,
                )
            )
        return self._adapter.rollback(
            InfrastructureDeploymentRollbackRequest(
                ctx.tenant_id,
                service_id,
                required_candidate_text(row, "targetDeployId"),
                required_candidate_text(row, "targetCommitId"),
                row.idempotency_key,
                ctx.request_id,
                trace,
            )
        )

    def _finish_result(
        self,
        ctx: RequestContext,
        row: ReleaseDeliveryRecord,
        result: InfrastructureDeploymentMutationResult,
    ) -> ReleaseDeliveryRecord:
        observation = result.observation
        if result.outcome == "accepted" and observation is not None:
            return self._finish_accepted_result(ctx, row, result, observation)
        error = {"kind": "outcome_unknown", "reason": result.reason, "httpStatus": result.provider_http_status}
        completed = self._complete(ctx, row, "ambiguous", None, error, None)
        raise ExternalReleaseDeliveryOutcomeUnknown(f"infrastructure delivery {completed.delivery_id} is ambiguous")

    def _finish_accepted_result(
        self,
        ctx: RequestContext,
        row: ReleaseDeliveryRecord,
        result: InfrastructureDeploymentMutationResult,
        observation: InfrastructureDeploymentObservation,
    ) -> ReleaseDeliveryRecord:
        payload = deployment_observation_ref(observation)
        if not _mutation_result_matches_delivery(row, result):
            self._raise_receipt_mismatch(ctx, row, payload)
        receipt_state = infrastructure_receipt_state(observation)
        if receipt_state == "failed":
            self._raise_provider_failure(ctx, row, observation, payload)
        if receipt_state == "outcome_unknown":
            self._raise_inconclusive_receipt(ctx, row, observation, payload)
        return self._complete(ctx, row, "landed", payload, None, observation.deploy_id)

    def _raise_receipt_mismatch(
        self,
        ctx: RequestContext,
        row: ReleaseDeliveryRecord,
        payload: dict[str, object],
    ) -> NoReturn:
        completed = self._complete(
            ctx,
            row,
            "ambiguous",
            payload,
            {"kind": "provider_receipt_identity_mismatch"},
            None,
        )
        raise ExternalReleaseDeliveryOutcomeUnknown(
            f"infrastructure delivery {completed.delivery_id} receipt identity is inconclusive"
        )

    def _raise_provider_failure(
        self,
        ctx: RequestContext,
        row: ReleaseDeliveryRecord,
        observation: InfrastructureDeploymentObservation,
        payload: dict[str, object],
    ) -> NoReturn:
        error: dict[str, object] = {
            "kind": "provider_deployment_failed",
            "providerStatus": observation.provider_status,
        }
        completed = self._complete(ctx, row, "failed", payload, error, observation.deploy_id)
        raise ConflictDetected(f"infrastructure delivery {completed.delivery_id} failed at the provider")

    def _raise_inconclusive_receipt(
        self,
        ctx: RequestContext,
        row: ReleaseDeliveryRecord,
        observation: InfrastructureDeploymentObservation,
        payload: dict[str, object],
    ) -> NoReturn:
        completed = self._complete(
            ctx,
            row,
            "ambiguous",
            payload,
            {"kind": "provider_receipt_state_inconsistent"},
            observation.deploy_id,
        )
        raise ExternalReleaseDeliveryOutcomeUnknown(
            f"infrastructure delivery {completed.delivery_id} receipt is inconclusive"
        )

    def _reconcile(self, ctx: RequestContext, row: ReleaseDeliveryRecord) -> ReleaseDeliveryRecord:
        observations = self._candidates(ctx, row)
        if len(observations) == 1:
            observed = observations[0]
            return self._settle_observation(ctx, row, observed)
        if not observations and is_settled_window(row):
            completed = self._settle(ctx, row, "absent", None, {"kind": "authoritative_absence"}, None)
            raise ConflictDetected(f"infrastructure delivery {completed.delivery_id} was not created")
        raise ExternalReleaseDeliveryOutcomeUnknown("infrastructure delivery lookup remains ambiguous")

    def _settle_observation(
        self,
        ctx: RequestContext,
        row: ReleaseDeliveryRecord,
        observed: InfrastructureDeploymentObservation,
    ) -> ReleaseDeliveryRecord:
        payload = deployment_observation_ref(observed)
        receipt_state = infrastructure_receipt_state(observed)
        if receipt_state == "failed":
            error: dict[str, object] = {
                "kind": "provider_deployment_failed",
                "providerStatus": observed.provider_status,
            }
            completed = self._settle(ctx, row, "failed", payload, error, observed.deploy_id)
            raise ConflictDetected(f"infrastructure delivery {completed.delivery_id} failed at the provider")
        if receipt_state == "outcome_unknown":
            raise ExternalReleaseDeliveryOutcomeUnknown("provider deployment observation is inconclusive")
        return self._settle(ctx, row, "landed", payload, None, observed.deploy_id)

    def _candidates(
        self,
        ctx: RequestContext,
        row: ReleaseDeliveryRecord,
    ) -> tuple[InfrastructureDeploymentObservation, ...]:
        observed_at = datetime.now(UTC)
        dispatch_started_at = delivery_dispatch_started_at(row)
        if dispatch_started_at is None:
            return ()
        query = InfrastructureDeploymentCandidateQuery(
            tenant_id=ctx.tenant_id,
            service_id=required_text(row.target_ref, "serviceId"),
            commit_id=delivery_commit_id(row),
            created_after=dispatch_started_at - timedelta(seconds=1),
            created_before=observed_at + timedelta(minutes=1),
            request_id=ctx.request_id,
            correlation_id=trace_identity(ctx),
            limit=20,
        )
        candidates = self._adapter.list_candidates(query)
        if row.operation != "application_rollback":
            return strict_deploy_reconciliation_candidates(row, candidates, observed_at=observed_at)
        verified = strict_rollback_reconciliation_candidates(row, candidates, observed_at=observed_at)
        if candidates and not verified:
            raise ExternalReleaseDeliveryOutcomeUnknown(
                "rollback candidates did not prove the exact new provider deployment"
            )
        return verified

    def _finish_adapter_error(
        self,
        ctx: RequestContext,
        row: ReleaseDeliveryRecord,
        exc: AdapterError,
    ) -> NoReturn:
        error = adapter_failure_payload(exc)
        if exc.failure.kind in _KNOWN_NOT_COMMITTED_KINDS:
            completed = self._complete(ctx, row, "failed", None, error, None)
            raise ConflictDetected(f"infrastructure delivery {completed.delivery_id} failed before acceptance") from exc
        completed = self._complete(ctx, row, "ambiguous", None, error, None)
        raise ExternalReleaseDeliveryOutcomeUnknown(f"delivery {completed.delivery_id} outcome is unknown") from exc

    def _finish_unknown(
        self,
        ctx: RequestContext,
        row: ReleaseDeliveryRecord,
        exc: Exception,
    ) -> NoReturn:
        error = dict(self._runtime_service._error_payload(exc, ctx, run_id=row.ai_run_id))
        error["kind"] = "outcome_unknown"
        completed = self._complete(ctx, row, "ambiguous", None, error, None)
        raise ExternalReleaseDeliveryOutcomeUnknown(f"delivery {completed.delivery_id} outcome is unknown") from exc

    @staticmethod
    def _require_accepted(row: ReleaseDeliveryRecord) -> ReleaseDeliveryRecord:
        if row.status != "landed":
            raise ConflictDetected("infrastructure delivery did not reach a confirmed provider receipt")
        return row

    def _complete(
        self,
        ctx: RequestContext,
        row: ReleaseDeliveryRecord,
        status: str,
        result_ref: dict[str, object] | None,
        error_ref: dict[str, object] | None,
        provider_resource_id: str | None,
    ) -> ReleaseDeliveryRecord:
        completed = self._ledger.complete(
            ctx,
            row,
            cast(ReleaseDeliveryTerminalStatus, status),
            result_ref,
            error_ref,
            provider_resource_id,
        )
        if completed is None:
            raise ExternalReleaseDeliveryOutcomeUnknown("delivery completion lost its dispatch fence")
        return completed

    def _settle(
        self,
        ctx: RequestContext,
        row: ReleaseDeliveryRecord,
        status: str,
        result_ref: dict[str, object] | None,
        error_ref: dict[str, object] | None,
        provider_resource_id: str | None,
    ) -> ReleaseDeliveryRecord:
        completed = self._ledger.settle_lookup(ctx, row, status, result_ref, error_ref, provider_resource_id)
        if completed is None:
            raise ExternalReleaseDeliveryOutcomeUnknown("delivery reconciliation lost its fence")
        return completed


def _mutation_result_matches_delivery(
    row: ReleaseDeliveryRecord,
    result: InfrastructureDeploymentMutationResult,
) -> bool:
    observation = result.observation
    expected_operation = "start" if row.operation == "application_deploy" else "rollback"
    expected_rollback_target = (
        None if row.operation == "application_deploy" else required_candidate_text(row, "targetDeployId")
    )
    return (
        observation is not None
        and result.operation == expected_operation
        and result.rollback_target_deploy_id == expected_rollback_target
        and infrastructure_receipt_matches_delivery(row, observation)
    )


__all__ = ["ExternalReleaseInfrastructureCoordinator"]
