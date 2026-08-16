"""Durable execution evidence for Source Explorer reads."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from foundry_lite.application.ports import SourceManagementRepository, TransactionManager
from foundry_lite.application.services.runtime_error_payloads import (
    record_runtime_cleanup_failure,
    record_runtime_operations_evidence,
    runtime_error_payload,
)
from foundry_lite.application.services.source_exploration_payloads import (
    SourceExplorationDependencies,
    explore_source_payload,
)
from foundry_lite.application.services.source_management_config import exploration_run_record
from foundry_lite.application.services.source_management_helpers import SourceRuntimeBoundary
from foundry_lite.application.services.source_management_views import exploration_view
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.error_redaction import scrub_error_mapping


class SourceExplorationStore(Protocol):
    engine: TransactionManager
    source_management_repository: SourceManagementRepository


def execute_source_exploration(
    store: SourceExplorationStore,
    runtime_service: SourceRuntimeBoundary,
    dependencies: SourceExplorationDependencies,
    ctx: RequestContext,
    *,
    source_name: str,
    source_type: str,
    request: Mapping[str, object],
) -> dict[str, object]:
    """Execute a read and persist both successful and failed operator evidence."""

    try:
        result = explore_source_payload(dependencies, ctx, source_name, source_type, request)
    except Exception as exc:
        evidence = _record_failed_exploration(store, runtime_service, ctx, source_name, source_type, request, exc)
        if evidence is not None:
            record_runtime_operations_evidence(
                exc,
                run_type="source_exploration",
                run_id=str(evidence["explorationRunId"]),
            )
        raise
    return _record_exploration(
        store,
        runtime_service,
        ctx,
        source_name,
        source_type,
        request,
        result=result,
        error=None,
    )


def _record_failed_exploration(
    store: SourceExplorationStore,
    runtime_service: SourceRuntimeBoundary,
    ctx: RequestContext,
    source_name: str,
    source_type: str,
    request: Mapping[str, object],
    exc: Exception,
) -> dict[str, object] | None:
    try:
        return _record_exploration(
            store,
            runtime_service,
            ctx,
            source_name,
            source_type,
            request,
            result={},
            error=runtime_error_payload(exc, ctx, adapter="source_exploration"),
        )
    except Exception as evidence_error:
        record_runtime_cleanup_failure(
            exc,
            operation="sourceExplorationFailureEvidence",
            cleanup_error=evidence_error,
        )
        return None


def _record_exploration(
    store: SourceExplorationStore,
    runtime_service: SourceRuntimeBoundary,
    ctx: RequestContext,
    source_name: str,
    source_type: str,
    request: Mapping[str, object],
    *,
    result: Mapping[str, object],
    error: Mapping[str, object] | None,
) -> dict[str, object]:
    status = "failed" if error is not None else "succeeded"
    record = exploration_run_record(
        ctx,
        source_name=source_name,
        source_type=source_type,
        request=scrub_error_mapping(request),
        result_summary=result,
        status=status,
        error=error,
    )
    view = exploration_view(record.__dict__)
    with store.engine.begin() as transaction:
        store.source_management_repository.create_exploration_run(transaction=transaction, record=record)
        runtime_service._audit(
            transaction,
            ctx,
            event_type=f"source.exploration.{status}",
            resource_type="source_exploration_run",
            resource_id=record.id,
            action="explore_source",
            decision="deny" if error is not None else "allow",
            after_ref=_exploration_audit_ref(view),
            correlation_id=ctx.request_id,
        )
    return view


def _exploration_audit_ref(view: Mapping[str, object]) -> dict[str, object]:
    return {
        "explorationRunId": view["explorationRunId"],
        "sourceName": view["sourceName"],
        "sourceType": view["sourceType"],
        "status": view["status"],
        "operationsPath": view["operationsPath"],
        "createdAt": view["createdAt"],
        "hasError": view.get("error") is not None,
    }


__all__ = ["execute_source_exploration"]
