"""Pure transform scheduler due/not-due specification."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

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


@dataclass(frozen=True)
class TransformScheduleSpec:
    api_name: str
    transform_id: str
    output_dataset_ref: str
    mode: str
    latest_input_versions: Mapping[str, str]
    missing_input_refs: tuple[str, ...]
    last_successful_input_versions: Mapping[str, str]
    active_run_id: str | None = None
    last_successful_run_id: str | None = None


def decide_transform_schedule(spec: TransformScheduleSpec) -> TransformScheduleDecision:
    if spec.mode != "snapshot":
        return _not_due(spec, "append_mode_requires_manual_run")
    if spec.active_run_id is not None:
        return _not_due(spec, "active_run_in_progress")
    if spec.missing_input_refs:
        return _not_due(spec, "input_version_missing")
    if spec.last_successful_run_id is None:
        return _due(spec, {}, "never_built")
    changed = _changed_input_refs(spec.latest_input_versions, spec.last_successful_input_versions)
    if changed:
        return _due(spec, spec.last_successful_input_versions, "input_version_changed", changed=changed)
    return _not_due(spec, "up_to_date")


def validate_transform_scheduler_max_runs(value: int) -> int:
    if value < 1 or value > _MAX_RUNS_PER_TICK:
        raise ValidationFailed("transform scheduler max_runs must be between 1 and 500", details={"maxRuns": value})
    return value


def _due(
    spec: TransformScheduleSpec,
    previous: Mapping[str, str],
    reason: str,
    *,
    changed: tuple[str, ...] = (),
) -> TransformScheduleDecision:
    return _decision(spec, True, reason, dict(previous), changed)


def _not_due(spec: TransformScheduleSpec, reason: str) -> TransformScheduleDecision:
    return _decision(
        spec,
        False,
        reason,
        dict(spec.last_successful_input_versions),
        (),
        missing=spec.missing_input_refs,
    )


def _decision(
    spec: TransformScheduleSpec,
    is_due: bool,
    reason: str,
    previous: Mapping[str, str],
    changed: tuple[str, ...],
    *,
    missing: Sequence[str] = (),
) -> TransformScheduleDecision:
    return TransformScheduleDecision(
        api_name=spec.api_name,
        transform_id=spec.transform_id,
        output_dataset_ref=spec.output_dataset_ref,
        mode=spec.mode,
        is_due=is_due,
        reason=reason,
        latest_input_versions=dict(spec.latest_input_versions),
        last_successful_input_versions=dict(previous),
        changed_input_refs=changed,
        missing_input_refs=tuple(sorted(missing)),
        active_run_id=spec.active_run_id,
        last_successful_run_id=spec.last_successful_run_id,
    )


def _changed_input_refs(latest: Mapping[str, str], previous: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(sorted(ref for ref, version_id in latest.items() if previous.get(ref) != version_id))
