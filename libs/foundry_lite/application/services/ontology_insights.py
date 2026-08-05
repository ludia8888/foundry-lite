"""Read-model payload shapes and builders for ontology usage/dependents views.

Palantir's Ontology Manager exposes a per-resource "usage" tab and a
"dependents" view. Foundry-lite mirrors both as read models assembled from
evidence that already exists (action runs, index runs, audit events) plus
ontology and OSDK declarations. Builders here are pure functions so the
service stays a thin query orchestrator and payload shapes stay testable.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Literal, TypedDict

from foundry_lite.application.ports import (
    ActionRunUsageRow,
    ActionTypeRow,
    IndexRunUsageRow,
    LinkTypeRow,
)
from foundry_lite.application.ports.runtime_repository import RuntimeRow
from foundry_lite.application.services.observability_time import parse_optional_time

OntologyResourceType = Literal["object_type", "action_type", "link_type"]

ONTOLOGY_RESOURCE_TYPES: tuple[OntologyResourceType, ...] = ("object_type", "action_type", "link_type")

# Usage windows are bounded so one request cannot demand an unbounded scan of
# runtime history; 365 days covers a full compliance year.
MAX_USAGE_WINDOW_DAYS = 365
DEFAULT_USAGE_WINDOW_DAYS = 30


class ResourceActionRunUsage(TypedDict):
    """Action-run activity slice of a resource usage read model."""

    statusCounts: dict[str, int]
    totalRuns: int
    distinctActorCount: int
    lastRunAt: str | None


class ResourceIndexRunUsage(TypedDict):
    """Index-run activity slice of a resource usage read model."""

    statusCounts: dict[str, int]
    totalRuns: int
    lastRunAt: str | None
    lastSucceededAt: str | None


class ResourceAuditUsage(TypedDict):
    """Audit-event evidence slice of a resource usage read model."""

    totalEvents: int
    lastEventAt: str | None


class OntologyResourceUsageResult(TypedDict):
    """Public usage read model for one active ontology resource."""

    resourceType: OntologyResourceType
    apiName: str
    windowDays: int
    since: str
    actionRuns: ResourceActionRunUsage
    indexRuns: ResourceIndexRunUsage
    auditEvents: ResourceAuditUsage
    notes: list[str]


class DependentActionType(TypedDict):
    """Action type that targets the inspected object type."""

    apiName: str
    displayName: str
    enabled: bool


class DependentLinkType(TypedDict):
    """Link type touching the inspected object type as source or target."""

    apiName: str
    displayName: str
    fromObjectType: str
    toObjectType: str
    cardinality: str
    backingDatasetRef: str | None


class DependentOsdkApplication(TypedDict):
    """OSDK application holding scopes granted on the inspected resource."""

    appApiName: str
    displayName: str
    scopes: list[str]


class OntologyResourceDependentsResult(TypedDict):
    """Public dependents read model for one active ontology resource.

    One shape serves all three resource types so API consumers get a stable
    schema; fields that do not apply stay None or empty and ``notes`` says why.
    """

    resourceType: OntologyResourceType
    apiName: str
    backingDatasetRef: str | None
    cdcDatasetRef: str | None
    materialization: Mapping[str, object] | None
    targetObjectType: str | None
    fromObjectType: str | None
    toObjectType: str | None
    titleProperty: str | None
    classifiedProperties: list[str]
    actionTypes: list[DependentActionType]
    linkTypes: list[DependentLinkType]
    writebacks: list[Mapping[str, object]]
    sideEffectTopics: list[str]
    osdkApplications: list[DependentOsdkApplication]
    notes: list[str]


def empty_action_run_usage() -> ResourceActionRunUsage:
    return {"statusCounts": {}, "totalRuns": 0, "distinctActorCount": 0, "lastRunAt": None}


def empty_index_run_usage() -> ResourceIndexRunUsage:
    return {"statusCounts": {}, "totalRuns": 0, "lastRunAt": None, "lastSucceededAt": None}


def action_run_usage_payload(row: ActionRunUsageRow) -> ResourceActionRunUsage:
    return {
        "statusCounts": dict(row["status_counts"]),
        "totalRuns": row["total_runs"],
        "distinctActorCount": row["distinct_actor_count"],
        "lastRunAt": row["last_run_at"],
    }


def index_run_usage_payload(row: IndexRunUsageRow) -> ResourceIndexRunUsage:
    return {
        "statusCounts": dict(row["status_counts"]),
        "totalRuns": row["total_runs"],
        "lastRunAt": row["last_run_at"],
        "lastSucceededAt": row["last_succeeded_at"],
    }


def audit_usage_payload(
    rows: Sequence[RuntimeRow],
    *,
    resource_types: frozenset[str],
    resource_ids: frozenset[str],
    since: str,
) -> ResourceAuditUsage:
    """Count in-window audit events for one resource.

    Producers record audit rows against either the resource api name or its
    immutable row id (both patterns exist in indexing services), so callers
    pass every identifier that denotes the same resource.
    """
    # Compare/sort by parsed instants, not raw strings. The window bound and the
    # stored created_at values carry the process's local UTC offset, so a lexical
    # comparison can drop or mis-order events across a DST change or mixed offsets.
    since_instant = parse_optional_time(since)
    in_window: list[tuple[datetime, str]] = []
    for row in rows:
        if str(row.get("resource_type")) not in resource_types or str(row.get("resource_id")) not in resource_ids:
            continue
        created = parse_optional_time(row.get("created_at"))
        if created is None or (since_instant is not None and created < since_instant):
            continue
        in_window.append((created, str(row["created_at"])))
    matched_at = [text for _, text in sorted(in_window, key=lambda item: item[0])]
    return {"totalEvents": len(matched_at), "lastEventAt": matched_at[-1] if matched_at else None}


def dependent_action_payload(row: ActionTypeRow) -> DependentActionType:
    return {"apiName": row["api_name"], "displayName": row["display_name"], "enabled": row["enabled"]}


def dependent_link_payload(row: LinkTypeRow) -> DependentLinkType:
    return {
        "apiName": row["api_name"],
        "displayName": row["display_name"],
        "fromObjectType": row["from_api_name"],
        "toObjectType": row["to_api_name"],
        "cardinality": row["cardinality"],
        "backingDatasetRef": row["backing"].get("dataset"),
    }


def base_dependents_result(resource_type: OntologyResourceType, api_name: str) -> OntologyResourceDependentsResult:
    """Return the stable all-fields shape so each branch only fills what applies."""
    return {
        "resourceType": resource_type,
        "apiName": api_name,
        "backingDatasetRef": None,
        "cdcDatasetRef": None,
        "materialization": None,
        "targetObjectType": None,
        "fromObjectType": None,
        "toObjectType": None,
        "titleProperty": None,
        "classifiedProperties": [],
        "actionTypes": [],
        "linkTypes": [],
        "writebacks": [],
        "sideEffectTopics": [],
        "osdkApplications": [],
        "notes": [],
    }


def declared_materialization(config: Mapping[str, object]) -> dict[str, object] | None:
    """Surface the declared materialization target stored in object-type config JSON."""
    value = config.get("materialization")
    return dict(value) if isinstance(value, Mapping) else None


def declared_title_property(config: Mapping[str, object]) -> str | None:
    """Surface the per-record display-title property stored in config JSON."""
    value = config.get("titleProperty")
    return value if isinstance(value, str) else None


def side_effect_topics(definition: Mapping[str, object]) -> list[str]:
    """Extract declared event topics from an action definition's sideEffects."""
    side_effects = definition.get("sideEffects")
    if not isinstance(side_effects, Sequence) or isinstance(side_effects, str):
        return []
    topics = [
        effect["topic"]
        for effect in side_effects
        if isinstance(effect, Mapping) and isinstance(effect.get("topic"), str)
    ]
    return [str(topic) for topic in topics]


def declared_writebacks(definition: Mapping[str, object]) -> list[Mapping[str, object]]:
    """Extract declared writeback specs from an action definition."""
    writebacks = definition.get("writebacks")
    if not isinstance(writebacks, Sequence) or isinstance(writebacks, str):
        return []
    return [dict(item) for item in writebacks if isinstance(item, Mapping)]
