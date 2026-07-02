"""Application service helpers for transform scheduler workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from foundry_lite.application.ports import TransformRow, TransformRunRow
from foundry_lite.application.primitives import CommitResult
from foundry_lite.application.services.transform_runs import TransformRunPlan
from foundry_lite.domain.transform import (
    TransformScheduleDecision,
    TransformScheduleSpec,
    decide_transform_schedule,
)


def transform_schedule_decision(
    transform: TransformRow,
    *,
    latest_input_versions: Mapping[str, str],
    missing_input_refs: Sequence[str] = (),
    last_successful_run: TransformRunRow | None,
    active_run: TransformRunRow | None,
) -> TransformScheduleDecision:
    return decide_transform_schedule(
        TransformScheduleSpec(
            api_name=str(transform["api_name"]),
            transform_id=str(transform["id"]),
            output_dataset_ref=str(transform["output_dataset_ref"]),
            mode=str(transform["mode"]),
            latest_input_versions=dict(latest_input_versions),
            missing_input_refs=tuple(sorted(missing_input_refs)),
            last_successful_input_versions=_successful_input_versions(last_successful_run),
            active_run_id=str(active_run["id"]) if active_run else None,
            last_successful_run_id=str(last_successful_run["id"]) if last_successful_run else None,
        )
    )


def transform_scheduler_view(
    decisions: Sequence[TransformScheduleDecision],
    started: Sequence[Mapping[str, object]],
    *,
    evaluated_at: datetime,
    max_runs: int,
) -> dict[str, object]:
    due = [decision.as_dict() for decision in decisions if decision.is_due]
    skipped = [decision.as_dict() for decision in decisions if not decision.is_due]
    return {
        "status": "evaluated",
        "evaluatedAt": _iso(evaluated_at),
        "evaluated": len(decisions),
        "due": due,
        "started": [dict(row) for row in started],
        "skipped": skipped,
        "maxRuns": max_runs,
    }


def transform_scheduler_run_payload(
    decision: TransformScheduleDecision,
    result: CommitResult,
    plan: TransformRunPlan,
) -> dict[str, object]:
    return {
        "decision": decision.as_dict(),
        "run": {
            "apiName": decision.api_name,
            "transformRunId": plan.run_id,
            "datasetRef": result.dataset_ref,
            "versionId": result.version_id,
            "transactionId": result.transaction_id,
            "rowCount": result.row_count,
        },
    }


def scheduler_now(value: datetime | None = None) -> datetime:
    return (value or datetime.now(UTC)).astimezone(UTC)


def _string_mapping(value: Mapping[str, object]) -> dict[str, str]:
    return {str(key): str(item) for key, item in value.items() if isinstance(item, str)}


def _successful_input_versions(last_successful_run: TransformRunRow | None) -> dict[str, str]:
    if last_successful_run is None:
        return {}
    return _string_mapping(last_successful_run["input_versions"])


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
