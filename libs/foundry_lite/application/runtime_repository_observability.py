"""Application-layer models and helpers for runtime repository observability."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, NotRequired, TypedDict

RuntimeJsonObject = Mapping[str, object]
RuntimeRunType = Literal[
    "sync",
    "transform",
    "index",
    "action",
    "action_writeback",
    "materialization",
    "outbox",
    "dead_letter",
    "workflow",
    "insight_review",
    "ai",
    "audit",
]
ObservabilityDetectorType = Literal["flow_interruption", "lag", "skew", "slo", "detector_failure"]
ObservabilityIncidentStatus = Literal["active", "suppressed"]
StoredObservabilityIncidentStatus = Literal["open", "acknowledged", "resolved"]
ObservabilitySeverity = Literal["info", "warning", "critical"]


class RuntimeRunLink(TypedDict):
    """Navigable link from evidence to an Operations run detail."""

    runType: RuntimeRunType
    runId: str
    relation: str
    resourceType: str
    resourceId: str
    status: str | None
    operationPath: str


class ObservabilityDetectorConfig(TypedDict, total=False):
    """Versioned detector threshold owned by an operator or data product team."""

    detectorId: str
    detectorType: ObservabilityDetectorType
    configVersion: str
    owner: str
    severity: ObservabilitySeverity
    runType: RuntimeRunType
    resourceType: str
    resourceId: str
    expectedCadenceSeconds: int
    thresholdSeconds: int
    thresholdEvents: int
    thresholdRatio: float
    cooldownSeconds: int
    seasonalityBaseline: RuntimeJsonObject


class ObservabilityIncident(TypedDict):
    """Operator-facing active or suppressed detector result."""

    id: str
    detectorId: str
    detectorType: ObservabilityDetectorType
    configVersion: str
    status: ObservabilityIncidentStatus
    severity: ObservabilitySeverity
    owner: str
    message: str
    dedupeKey: str
    firstObservedAt: str
    lastObservedAt: str
    evidence: RuntimeJsonObject
    evidenceLinks: list[RuntimeRunLink]
    threshold: RuntimeJsonObject


class StoredObservabilityIncident(TypedDict):
    """Durable incident row managed after detector evaluation."""

    id: str
    detectorId: str
    detectorType: ObservabilityDetectorType
    configVersion: str
    status: StoredObservabilityIncidentStatus
    severity: ObservabilitySeverity
    owner: str
    message: str
    dedupeKey: str
    firstObservedAt: str
    lastObservedAt: str
    occurrenceCount: int
    evidence: RuntimeJsonObject
    evidenceLinks: list[RuntimeRunLink]
    threshold: RuntimeJsonObject
    acknowledgedAt: NotRequired[str | None]
    acknowledgedBy: NotRequired[str | None]
    resolvedAt: NotRequired[str | None]
    resolvedBy: NotRequired[str | None]
    resolutionReason: NotRequired[str | None]


class ObservabilityStoredReport(TypedDict):
    """Detector report plus durable incident rows touched by recording."""

    generatedAt: str
    configurationVersion: str
    activeIncidents: list[ObservabilityIncident]
    suppressedIncidents: list[ObservabilityIncident]
    storedIncidents: list[StoredObservabilityIncident]
    summary: RuntimeJsonObject


class ObservabilityReport(TypedDict):
    """Read-only proactive observability detector report."""

    generatedAt: str
    configurationVersion: str
    activeIncidents: list[ObservabilityIncident]
    suppressedIncidents: list[ObservabilityIncident]
    summary: RuntimeJsonObject
