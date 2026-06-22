"""Read-only proactive observability and SLO detectors."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import UTC, datetime
from typing import cast

from foundry_lite.application.ports import (
    ObservabilityDetectorConfig,
    ObservabilityDetectorType,
    ObservabilityIncident,
    ObservabilityReport,
    ObservabilitySeverity,
    RuntimeJsonObject,
    RuntimeRow,
    RuntimeRunLink,
    RuntimeRunSnapshot,
    RuntimeRunType,
)
from foundry_lite.application.services.observability_skew import skew_measurement
from foundry_lite.application.services.runtime_run_queries import RUN_GROUPS, row_references, row_status
from foundry_lite.domain.errors import ValidationFailed

SUCCESS_STATUSES = {"success", "succeeded", "committed", "published", "completed"}


def build_observability_report(
    snapshot: RuntimeRunSnapshot,
    *,
    configs: Sequence[ObservabilityDetectorConfig],
    observed_at: str,
    previous_incidents: Sequence[Mapping[str, object]] = (),
) -> ObservabilityReport:
    """Evaluate detector configs without mutating runtime source-of-truth rows."""
    safe_snapshot = deepcopy(snapshot)
    active: list[ObservabilityIncident] = []
    suppressed: list[ObservabilityIncident] = []
    for config in configs:
        for incident in _incidents_for_config(safe_snapshot, config, observed_at):
            _append_incident(incident, config, previous_incidents, observed_at, active, suppressed)
    return _report(configs, observed_at, active, suppressed)


def _incidents_for_config(
    snapshot: RuntimeRunSnapshot,
    config: ObservabilityDetectorConfig,
    observed_at: str,
) -> list[ObservabilityIncident]:
    try:
        return _build_incidents(snapshot, config, observed_at)
    except Exception as exc:
        return [_detector_failure_incident(config, observed_at, exc)]


def _build_incidents(
    snapshot: RuntimeRunSnapshot,
    config: ObservabilityDetectorConfig,
    observed_at: str,
) -> list[ObservabilityIncident]:
    detector_type = _detector_type(config)
    if detector_type == "flow_interruption":
        incident = _flow_interruption_incident(snapshot, config, observed_at)
    elif detector_type == "lag":
        incident = _lag_incident(snapshot, config, observed_at)
    elif detector_type == "skew":
        incident = _skew_incident(snapshot, config, observed_at)
    elif detector_type == "slo":
        incident = _slo_incident(snapshot, config, observed_at)
    else:
        raise ValidationFailed("unsupported observability detector", details={"detectorType": detector_type})
    return [] if incident is None else [incident]


def _flow_interruption_incident(
    snapshot: RuntimeRunSnapshot,
    config: ObservabilityDetectorConfig,
    observed_at: str,
) -> ObservabilityIncident | None:
    rows = _matching_rows(snapshot, config)
    successful = [row for row in rows if _is_success(row)]
    latest = _latest_row(successful)
    latest_at = _row_timestamp(latest) if latest is not None else None
    gap = _seconds_between(latest_at, _parse_time(observed_at)) if latest_at is not None else None
    cadence = _required_int(config, "expectedCadenceSeconds")
    if gap is not None and gap <= cadence:
        return None
    failed_count = len([row for row in rows if _is_failed(row)])
    evidence = {
        "lastSuccessfulAt": latest_at.isoformat() if latest_at else None,
        "secondsSinceLastSuccess": gap,
        "failedRunCount": failed_count,
        "resourceId": config.get("resourceId"),
    }
    return _incident(
        config,
        observed_at,
        "missing data beyond expected cadence",
        evidence,
        _links_for_rows([latest], _run_type(config)),
    )


def _lag_incident(
    snapshot: RuntimeRunSnapshot,
    config: ObservabilityDetectorConfig,
    observed_at: str,
) -> ObservabilityIncident | None:
    rows = _matching_rows(snapshot, config)
    candidates = [(row, _lag_seconds(row)) for row in rows]
    measured = [(row, value) for row, value in candidates if value is not None]
    if not measured:
        return None
    row, lag_seconds = max(measured, key=lambda item: item[1])
    threshold = _required_int(config, "thresholdSeconds")
    if lag_seconds <= threshold:
        return None
    evidence = {
        "sourceEventTime": row.get("event_time") or row.get("source_event_time"),
        "processingTime": row.get("processed_at") or row.get("completed_at") or row.get("created_at"),
        "eventTimeLagSeconds": lag_seconds,
        "brokerLagEvents": row.get("broker_lag_events"),
        "restCursorObservedAt": row.get("cursor_observed_at"),
    }
    return _incident(
        config,
        observed_at,
        "event processing lag breached threshold",
        evidence,
        [_link_for_row(row, _run_type(config))],
    )


def _skew_incident(
    snapshot: RuntimeRunSnapshot,
    config: ObservabilityDetectorConfig,
    observed_at: str,
) -> ObservabilityIncident | None:
    seasonal_partitions = _seasonal_partitions(config)
    measurement = skew_measurement(_matching_rows(snapshot, config), seasonal_partitions)
    if measurement is None:
        return None
    threshold = _required_float(config, "thresholdRatio")
    if measurement.skew_ratio <= threshold:
        return None
    evidence = {
        "largestPartition": measurement.largest_partition,
        "largestSizeBytes": measurement.largest_size_bytes,
        "medianSizeBytes": measurement.median_size_bytes,
        "skewRatio": measurement.skew_ratio,
        "ignoredSeasonalPartitions": sorted(seasonal_partitions),
    }
    return _incident(
        config,
        observed_at,
        "partition or file size skew breached threshold",
        evidence,
        [_link_for_row(measurement.largest_row, _run_type(config))],
    )


def _slo_incident(
    snapshot: RuntimeRunSnapshot,
    config: ObservabilityDetectorConfig,
    observed_at: str,
) -> ObservabilityIncident | None:
    measured = [(row, _duration_seconds(row)) for row in _matching_rows(snapshot, config)]
    slow = [(row, value) for row, value in measured if value is not None]
    if not slow:
        return None
    row, duration = max(slow, key=lambda item: item[1])
    threshold = _required_int(config, "thresholdSeconds")
    if duration <= threshold:
        return None
    evidence = {
        "durationSeconds": duration,
        "references": row_references(row),
        "datasetVersionId": row.get("committed_version_id") or row.get("output_version_id"),
    }
    return _incident(config, observed_at, "SLO threshold breached", evidence, [_link_for_row(row, _run_type(config))])


def _append_incident(
    incident: ObservabilityIncident,
    config: ObservabilityDetectorConfig,
    previous: Sequence[Mapping[str, object]],
    observed_at: str,
    active: list[ObservabilityIncident],
    suppressed: list[ObservabilityIncident],
) -> None:
    dedupe = _cooldown_suppressed(incident, config, previous, observed_at)
    if dedupe is None:
        active.append(incident)
    else:
        suppressed.append(dedupe)


def _cooldown_suppressed(
    incident: ObservabilityIncident,
    config: ObservabilityDetectorConfig,
    previous: Sequence[Mapping[str, object]],
    observed_at: str,
) -> ObservabilityIncident | None:
    cooldown = int(config.get("cooldownSeconds", 0))
    if cooldown <= 0:
        return None
    current = _parse_time(observed_at)
    for previous_incident in previous:
        if previous_incident.get("dedupeKey") != incident["dedupeKey"]:
            continue
        previous_at = _parse_optional_time(previous_incident.get("lastObservedAt"))
        if previous_at is not None and _seconds_between(previous_at, current) <= cooldown:
            suppressed = dict(incident)
            suppressed["status"] = "suppressed"
            suppressed["evidence"] = {**incident["evidence"], "suppressionReason": "cooldown"}
            return cast(ObservabilityIncident, suppressed)
    return None


def _incident(
    config: ObservabilityDetectorConfig,
    observed_at: str,
    message: str,
    evidence: RuntimeJsonObject,
    links: list[RuntimeRunLink],
) -> ObservabilityIncident:
    detector_id = _required_str(config, "detectorId")
    detector_type = _incident_detector_type(config)
    dedupe_key = _dedupe_key(config, detector_type)
    return {
        "id": f"incident:{dedupe_key}",
        "detectorId": detector_id,
        "detectorType": detector_type,
        "configVersion": _required_str(config, "configVersion"),
        "status": "active",
        "severity": _severity(config),
        "owner": _required_str(config, "owner"),
        "message": message,
        "dedupeKey": dedupe_key,
        "firstObservedAt": observed_at,
        "lastObservedAt": observed_at,
        "evidence": evidence,
        "evidenceLinks": links,
        "threshold": _threshold_payload(config),
    }


def _detector_failure_incident(
    config: ObservabilityDetectorConfig,
    observed_at: str,
    exc: Exception,
) -> ObservabilityIncident:
    fallback = {
        "detectorId": str(config.get("detectorId", "unknown")),
        "detectorType": "detector_failure",
        "configVersion": str(config.get("configVersion", "unknown")),
        "owner": str(config.get("owner", "platform-ops")),
        "severity": "critical",
    }
    incident_config = cast(ObservabilityDetectorConfig, fallback)
    return _incident(
        incident_config,
        observed_at,
        "detector evaluation failed without mutating source-of-truth",
        {"errorType": exc.__class__.__name__, "errorMessage": str(exc)},
        [],
    )


def _report(
    configs: Sequence[ObservabilityDetectorConfig],
    observed_at: str,
    active: list[ObservabilityIncident],
    suppressed: list[ObservabilityIncident],
) -> ObservabilityReport:
    versions = sorted({str(config.get("configVersion", "unknown")) for config in configs})
    return {
        "generatedAt": observed_at,
        "configurationVersion": ",".join(versions) if versions else "none",
        "activeIncidents": active,
        "suppressedIncidents": suppressed,
        "summary": {"active": len(active), "suppressed": len(suppressed), "evaluatedDetectors": len(configs)},
    }


def _matching_rows(snapshot: RuntimeRunSnapshot, config: ObservabilityDetectorConfig) -> list[RuntimeRow]:
    rows = _rows_for_run_type(snapshot, _run_type(config))
    resource_id = config.get("resourceId")
    if not isinstance(resource_id, str) or not resource_id:
        return rows
    return [row for row in rows if _row_has_resource(row, resource_id)]


def _rows_for_run_type(snapshot: RuntimeRunSnapshot, run_type: RuntimeRunType) -> list[RuntimeRow]:
    if run_type == "sync":
        return list(snapshot["syncRuns"])
    if run_type == "transform":
        return list(snapshot["transformRuns"])
    if run_type == "index":
        return list(snapshot["indexRuns"])
    if run_type == "action":
        return list(snapshot["actionRuns"])
    if run_type == "action_writeback":
        return list(snapshot["actionWritebacks"])
    if run_type == "materialization":
        return list(snapshot["materializationRuns"])
    if run_type == "outbox":
        return list(snapshot["outboxEvents"])
    if run_type == "dead_letter":
        return list(snapshot["deadLetterEvents"])
    if run_type == "workflow":
        return list(snapshot["workflowRuns"])
    return list(snapshot["auditEvents"])


def _row_has_resource(row: RuntimeRow, resource_id: str) -> bool:
    refs = row_references(row)
    values = list(refs.values()) + [row.get("id"), row.get("dataset_ref"), row.get("source_ref")]
    return any(_contains_resource(value, resource_id) for value in values)


def _contains_resource(value: object, resource_id: str) -> bool:
    if value == resource_id:
        return True
    if isinstance(value, Mapping):
        return any(_contains_resource(item, resource_id) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, str):
        return any(_contains_resource(item, resource_id) for item in value)
    return False


def _latest_row(rows: Sequence[RuntimeRow]) -> RuntimeRow | None:
    timed = [(row, timestamp) for row in rows if (timestamp := _row_timestamp(row)) is not None]
    if not timed:
        return None
    return max(timed, key=lambda item: item[1])[0]


def _row_timestamp(row: RuntimeRow | None) -> datetime | None:
    if row is None:
        return None
    for key in ("finished_at", "completed_at", "published_at", "created_at", "processed_at"):
        parsed = _parse_optional_time(row.get(key))
        if parsed is not None:
            return parsed
    return None


def _duration_seconds(row: RuntimeRow) -> float | None:
    explicit = _numeric(row.get("duration_seconds"))
    if explicit is not None:
        return explicit
    started = _parse_optional_time(row.get("started_at") or row.get("created_at"))
    finished = _parse_optional_time(row.get("finished_at") or row.get("completed_at"))
    return _seconds_between(started, finished) if started is not None and finished is not None else None


def _lag_seconds(row: RuntimeRow) -> float | None:
    explicit = _numeric(row.get("event_time_lag_seconds") or row.get("lag_seconds"))
    if explicit is not None:
        return explicit
    event_time = _parse_optional_time(row.get("event_time") or row.get("source_event_time"))
    processed = _parse_optional_time(row.get("processed_at") or row.get("completed_at") or row.get("created_at"))
    return _seconds_between(event_time, processed) if event_time is not None and processed is not None else None


def _seconds_between(started: datetime, finished: datetime) -> float:
    return max(0.0, (finished - started).total_seconds())


def _link_for_row(row: RuntimeRow, run_type: RuntimeRunType | None = None) -> RuntimeRunLink:
    row_type = str(row.get("run_type") or run_type or "sync").replace("-", "_")
    parsed_type = cast(RuntimeRunType, row_type if row_type in RUN_GROUPS else "sync")
    run_id = str(row.get("id", "unknown"))
    return {
        "runType": parsed_type,
        "runId": run_id,
        "relation": "observability_evidence",
        "resourceType": "runtime_run",
        "resourceId": run_id,
        "status": row_status(row, parsed_type),
        "operationPath": f"/api/operations/runs/{parsed_type}/{run_id}",
    }


def _links_for_rows(rows: Sequence[RuntimeRow | None], run_type: RuntimeRunType) -> list[RuntimeRunLink]:
    return [_link_for_row(row, run_type) for row in rows if row is not None]


def _seasonal_partitions(config: ObservabilityDetectorConfig) -> set[str]:
    baseline = config.get("seasonalityBaseline")
    if not isinstance(baseline, Mapping):
        return set()
    values = baseline.get("expectedSeasonalPartitions")
    if not isinstance(values, Sequence) or isinstance(values, str):
        return set()
    return {str(value) for value in values}


def _threshold_payload(config: ObservabilityDetectorConfig) -> RuntimeJsonObject:
    keys = ("expectedCadenceSeconds", "thresholdSeconds", "thresholdEvents", "thresholdRatio", "cooldownSeconds")
    return {key: value for key in keys if (value := config.get(key)) is not None}


def _dedupe_key(config: ObservabilityDetectorConfig, detector_type: ObservabilityDetectorType) -> str:
    return ":".join(
        (
            _required_str(config, "detectorId"),
            detector_type,
            str(config.get("resourceType", "runtime")),
            str(config.get("resourceId", "*")),
        )
    )


def _is_success(row: RuntimeRow) -> bool:
    return row_status(row).lower() in SUCCESS_STATUSES


def _is_failed(row: RuntimeRow) -> bool:
    return row_status(row).lower() in {"failed", "failure", "error"}


def _run_type(config: ObservabilityDetectorConfig) -> RuntimeRunType:
    value = config.get("runType")
    if value is None or value not in RUN_GROUPS:
        raise ValidationFailed("observability detector requires supported runType", details={"runType": value})
    return value


def _detector_type(config: ObservabilityDetectorConfig) -> ObservabilityDetectorType:
    value = config.get("detectorType")
    if value not in {"flow_interruption", "lag", "skew", "slo"}:
        raise ValidationFailed(
            "observability detector requires supported detectorType",
            details={"detectorType": value},
        )
    return value


def _incident_detector_type(config: ObservabilityDetectorConfig) -> ObservabilityDetectorType:
    value = config.get("detectorType")
    if value == "detector_failure":
        return "detector_failure"
    return _detector_type(config)


def _severity(config: ObservabilityDetectorConfig) -> ObservabilitySeverity:
    value = config.get("severity")
    return value if value in {"info", "warning", "critical"} else "warning"


def _required_str(config: Mapping[str, object], key: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value:
        raise ValidationFailed("observability detector config is missing required string", details={"key": key})
    return value


def _required_int(config: Mapping[str, object], key: str) -> int:
    value = config.get(key)
    if not isinstance(value, int):
        raise ValidationFailed("observability detector config is missing required integer", details={"key": key})
    return value


def _required_float(config: Mapping[str, object], key: str) -> float:
    value = config.get(key)
    if not isinstance(value, int | float):
        raise ValidationFailed("observability detector config is missing required number", details={"key": key})
    return float(value)


def _numeric(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _parse_optional_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    return _parse_time(value)
