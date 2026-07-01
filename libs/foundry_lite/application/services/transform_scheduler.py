"""Application service helpers for transform scheduler workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from foundry_lite.application.ports import TransformRow, TransformRunRow
from foundry_lite.application.primitives import CommitResult
from foundry_lite.application.services.transform_runs import TransformRunPlan
from foundry_lite.domain.errors import ValidationFailed

_MAX_RUNS_PER_TICK = 500


@dataclass(frozen=True)
class TransformScheduleDecision:
    api_name: str
    transform_id: str
    output_dataset_ref: str
    mode: str
    is_due: bool
    reason: str
    latest_input_versions: Mapping[str, str]
    last_successful_input_versions: Mapping[str, str]
    changed_input_refs: tuple[str, ...]
    missing_input_refs: tuple[str, ...]
    active_run_id: str | None
    last_successful_run_id: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "apiName": self.api_name,
            "transformId": self.transform_id,
            "outputDatasetRef": self.output_dataset_ref,
            "mode": self.mode,
            "due": self.is_due,
            "reason": self.reason,
            "latestInputVersions": dict(self.latest_input_versions),
            "lastSuccessfulInputVersions": dict(self.last_successful_input_versions),
            "changedInputRefs": list(self.changed_input_refs),
            "missingInputRefs": list(self.missing_input_refs),
            "activeRunId": self.active_run_id,
            "lastSuccessfulRunId": self.last_successful_run_id,
        }


def transform_schedule_decision(
    transform: TransformRow,
    *,
    latest_input_versions: Mapping[str, str],
    missing_input_refs: Sequence[str] = (),
    last_successful_run: TransformRunRow | None,
    active_run: TransformRunRow | None,
) -> TransformScheduleDecision:
    mode = str(transform["mode"])
    if mode != "snapshot":
        return _not_due(transform, latest_input_versions, last_successful_run, "append_mode_requires_manual_run")
    if active_run is not None:
        return _not_due(transform, latest_input_versions, last_successful_run, "active_run_in_progress", active_run)
    if missing_input_refs:
        return _not_due(
            transform,
            latest_input_versions,
            last_successful_run,
            "input_version_missing",
            missing=tuple(sorted(missing_input_refs)),
        )
    if last_successful_run is None:
        return _due(transform, latest_input_versions, {}, "never_built")
    previous = _string_mapping(last_successful_run["input_versions"])
    changed = _changed_input_refs(latest_input_versions, previous)
    if changed:
        return _due(transform, latest_input_versions, previous, "input_version_changed", changed=changed)
    return _not_due(transform, latest_input_versions, last_successful_run, "up_to_date")


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


def validate_transform_scheduler_max_runs(value: int) -> int:
    if value < 1 or value > _MAX_RUNS_PER_TICK:
        raise ValidationFailed("transform scheduler max_runs must be between 1 and 500", details={"maxRuns": value})
    return value


def scheduler_now(value: datetime | None = None) -> datetime:
    return (value or datetime.now(UTC)).astimezone(UTC)


def _due(
    transform: TransformRow,
    latest: Mapping[str, str],
    previous: Mapping[str, str],
    reason: str,
    *,
    changed: tuple[str, ...] = (),
) -> TransformScheduleDecision:
    return _decision(transform, latest, previous, True, reason, changed, (), None, None)


def _not_due(
    transform: TransformRow,
    latest: Mapping[str, str],
    last_successful_run: TransformRunRow | None,
    reason: str,
    active_run: TransformRunRow | None = None,
    missing: tuple[str, ...] = (),
) -> TransformScheduleDecision:
    previous = _string_mapping(last_successful_run["input_versions"]) if last_successful_run else {}
    active_run_id = str(active_run["id"]) if active_run else None
    successful_run_id = str(last_successful_run["id"]) if last_successful_run else None
    return _decision(transform, latest, previous, False, reason, (), missing, active_run_id, successful_run_id)


def _decision(
    transform: TransformRow,
    latest: Mapping[str, str],
    previous: Mapping[str, str],
    is_due: bool,
    reason: str,
    changed: tuple[str, ...],
    missing: tuple[str, ...],
    active_run_id: str | None,
    last_successful_run_id: str | None,
) -> TransformScheduleDecision:
    return TransformScheduleDecision(
        api_name=str(transform["api_name"]),
        transform_id=str(transform["id"]),
        output_dataset_ref=str(transform["output_dataset_ref"]),
        mode=str(transform["mode"]),
        is_due=is_due,
        reason=reason,
        latest_input_versions=dict(latest),
        last_successful_input_versions=dict(previous),
        changed_input_refs=changed,
        missing_input_refs=missing,
        active_run_id=active_run_id,
        last_successful_run_id=last_successful_run_id,
    )


def _changed_input_refs(latest: Mapping[str, str], previous: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(sorted(ref for ref, version_id in latest.items() if previous.get(ref) != version_id))


def _string_mapping(value: Mapping[str, object]) -> dict[str, str]:
    return {str(key): str(item) for key, item in value.items() if isinstance(item, str)}


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
