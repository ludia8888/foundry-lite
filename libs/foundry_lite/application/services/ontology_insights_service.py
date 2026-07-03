"""Ontology resource usage and dependents read use-case service."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Protocol

from foundry_lite.application.ports import LinkTypeRow, TransactionContext
from foundry_lite.application.ports.osdk_application_repository import OsdkApplicationRow
from foundry_lite.application.ports.runtime_repository import RuntimeRow
from foundry_lite.application.ports.runtime_repository_types import RuntimeRowsTable
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.ontology_insights import (
    DEFAULT_USAGE_WINDOW_DAYS,
    MAX_USAGE_WINDOW_DAYS,
    ONTOLOGY_RESOURCE_TYPES,
    DependentOsdkApplication,
    OntologyResourceDependentsResult,
    OntologyResourceType,
    OntologyResourceUsageResult,
    ResourceAuditUsage,
    action_run_usage_payload,
    audit_usage_payload,
    base_dependents_result,
    declared_materialization,
    declared_title_property,
    declared_writebacks,
    dependent_action_payload,
    dependent_link_payload,
    empty_action_run_usage,
    empty_index_run_usage,
    index_run_usage_payload,
    side_effect_topics,
)
from foundry_lite.application.services.ontology_lookup_service import OntologyLookupService
from foundry_lite.application.services.osdk_application_records import scope_for
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, ValidationFailed

_LINK_TYPE_USAGE_NOTE = (
    "Link types have no dedicated runtime run ledger; usage reports the declared shape with zero run counts."
)


class OntologyInsightsRuntimeBoundary(Protocol):
    """Runtime surface the insights read models need: permission gate + evidence reads."""

    def _require_or_audit(
        self,
        ctx: RequestContext,
        permission: str,
        resource_type: str,
        resource_id: str,
    ) -> None: ...

    def _rows_for_tenant(
        self,
        conn: TransactionContext,
        table: RuntimeRowsTable,
        ctx: RequestContext,
    ) -> Sequence[RuntimeRow]: ...


class OntologyInsightsService(CoreService):
    """Read-only usage and dependents read models for active ontology resources.

    Mirrors Ontology Manager's usage tab / dependents view: everything is
    served from evidence and declarations that already exist, so this service
    never writes anything beyond permission-denial audit events.
    """

    required_dependencies = ("engine", "action_repository", "object_index_repository", "osdk_application_repository")
    required_collaborators = ("ontology_lookup_service", "runtime_service")
    ontology_lookup_service: OntologyLookupService
    runtime_service: OntologyInsightsRuntimeBoundary

    def resource_usage(
        self,
        resource_type: str,
        api_name: str,
        *,
        window_days: int = DEFAULT_USAGE_WINDOW_DAYS,
        ctx: RequestContext | None = None,
    ) -> OntologyResourceUsageResult:
        ctx = ctx or RequestContext()
        checked_type = _required_resource_type(resource_type)
        window = _required_window_days(window_days)
        # Same read permission as the catalog endpoint: usage is metadata about
        # runtime activity, not row-level data, so ontology:read is sufficient.
        self.runtime_service._require_or_audit(ctx, "ontology:read", checked_type, api_name)
        since = _window_start(window)
        with self.engine.begin() as conn:
            if checked_type == "action_type":
                return self._action_type_usage(conn, ctx, api_name, window_days=window, since=since)
            if checked_type == "object_type":
                return self._object_type_usage(conn, ctx, api_name, window_days=window, since=since)
            return self._link_type_usage(conn, ctx, api_name, window_days=window, since=since)

    def resource_dependents(
        self,
        resource_type: str,
        api_name: str,
        *,
        ctx: RequestContext | None = None,
    ) -> OntologyResourceDependentsResult:
        ctx = ctx or RequestContext()
        checked_type = _required_resource_type(resource_type)
        self.runtime_service._require_or_audit(ctx, "ontology:read", checked_type, api_name)
        with self.engine.begin() as conn:
            if checked_type == "action_type":
                return self._action_type_dependents(conn, ctx, api_name)
            if checked_type == "object_type":
                return self._object_type_dependents(conn, ctx, api_name)
            return self._link_type_dependents(conn, ctx, api_name)

    def _action_type_usage(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        api_name: str,
        *,
        window_days: int,
        since: str,
    ) -> OntologyResourceUsageResult:
        row = self.ontology_lookup_service._active_action_type(conn, ctx, api_name)
        runs = self.action_repository.action_run_usage(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            since=since,
            action_type_api_name=api_name,
        )
        return {
            "resourceType": "action_type",
            "apiName": api_name,
            "windowDays": window_days,
            "since": since,
            "actionRuns": action_run_usage_payload(runs),
            "indexRuns": empty_index_run_usage(),
            "auditEvents": self._audit_usage(conn, ctx, ids={api_name, row["id"]}, types={"action_type"}, since=since),
            "notes": [],
        }

    def _object_type_usage(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        api_name: str,
        *,
        window_days: int,
        since: str,
    ) -> OntologyResourceUsageResult:
        row = self.ontology_lookup_service._active_object_type(conn, ctx, api_name)
        index_runs = self.object_index_repository.index_run_usage(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            object_type_api_name=api_name,
            since=since,
        )
        targeting_runs = self.action_repository.action_run_usage(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            since=since,
            target_object_type_api_name=api_name,
        )
        audit = self._audit_usage(conn, ctx, ids={api_name, row["id"]}, types={"object_type", "object"}, since=since)
        return {
            "resourceType": "object_type",
            "apiName": api_name,
            "windowDays": window_days,
            "since": since,
            "actionRuns": action_run_usage_payload(targeting_runs),
            "indexRuns": index_run_usage_payload(index_runs),
            "auditEvents": audit,
            "notes": [],
        }

    def _link_type_usage(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        api_name: str,
        *,
        window_days: int,
        since: str,
    ) -> OntologyResourceUsageResult:
        row = self._active_link_type(conn, ctx, api_name)
        return {
            "resourceType": "link_type",
            "apiName": api_name,
            "windowDays": window_days,
            "since": since,
            "actionRuns": empty_action_run_usage(),
            "indexRuns": empty_index_run_usage(),
            "auditEvents": self._audit_usage(conn, ctx, ids={api_name, row["id"]}, types={"link_type"}, since=since),
            "notes": [_LINK_TYPE_USAGE_NOTE],
        }

    def _object_type_dependents(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        api_name: str,
    ) -> OntologyResourceDependentsResult:
        active = self.ontology_lookup_service._active_ontology_version(conn, ctx)
        row = self.ontology_lookup_service._active_object_type(conn, ctx, api_name)
        backing = row["backing"]
        cdc = backing.get("cdc")
        properties = self.ontology_lookup_service._properties_for_object_type(conn, row["id"])
        result = base_dependents_result("object_type", api_name)
        result["backingDatasetRef"] = backing["dataset"]
        result["cdcDatasetRef"] = cdc["dataset"] if cdc is not None else None
        result["materialization"] = declared_materialization(row["config"])
        result["titleProperty"] = declared_title_property(row["config"])
        result["classifiedProperties"] = sorted(
            item["api_name"] for item in properties if item["classification"] is not None
        )
        result["actionTypes"] = [
            dependent_action_payload(item) for item in self.ontology_lookup_service._actions_for_target(conn, row["id"])
        ]
        result["linkTypes"] = [
            dependent_link_payload(item)
            for item in self.ontology_lookup_service._link_types_for_version(conn, ctx, active["id"])
            if api_name in (item["from_api_name"], item["to_api_name"])
        ]
        result["osdkApplications"] = self._osdk_dependents(conn, ctx, resource_type="object", api_name=api_name)
        return result

    def _action_type_dependents(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        api_name: str,
    ) -> OntologyResourceDependentsResult:
        row = self.ontology_lookup_service._active_action_type(conn, ctx, api_name)
        result = base_dependents_result("action_type", api_name)
        result["targetObjectType"] = row["target_api_name"]
        result["writebacks"] = declared_writebacks(row["definition"])
        result["sideEffectTopics"] = side_effect_topics(row["definition"])
        result["osdkApplications"] = self._osdk_dependents(
            conn,
            ctx,
            resource_type="action",
            api_name=api_name,
            required_scope=scope_for("action", api_name, "execute"),
        )
        return result

    def _link_type_dependents(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        api_name: str,
    ) -> OntologyResourceDependentsResult:
        row = self._active_link_type(conn, ctx, api_name)
        result = base_dependents_result("link_type", api_name)
        result["backingDatasetRef"] = row["backing"]["dataset"]
        result["fromObjectType"] = row["from_api_name"]
        result["toObjectType"] = row["to_api_name"]
        result["osdkApplications"] = self._osdk_dependents(conn, ctx, resource_type="link", api_name=api_name)
        return result

    def _active_link_type(self, conn: TransactionContext, ctx: RequestContext, api_name: str) -> LinkTypeRow:
        active = self.ontology_lookup_service._active_ontology_version(conn, ctx)
        for row in self.ontology_lookup_service._link_types_for_version(conn, ctx, active["id"]):
            if row["api_name"] == api_name:
                return row
        raise NotFound("link type not found", details={"api_name": api_name})

    def _audit_usage(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        *,
        ids: set[str],
        types: set[str],
        since: str,
    ) -> ResourceAuditUsage:
        rows = self.runtime_service._rows_for_tenant(conn, "audit_events", ctx)
        return audit_usage_payload(rows, resource_types=frozenset(types), resource_ids=frozenset(ids), since=since)

    def _osdk_dependents(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        *,
        resource_type: str,
        api_name: str,
        required_scope: str | None = None,
    ) -> list[DependentOsdkApplication]:
        applications = self.osdk_application_repository.list_applications(transaction=conn, tenant_id=ctx.tenant_id)
        dependents = [
            self._osdk_dependent(conn, ctx, app, resource_type=resource_type, api_name=api_name) for app in applications
        ]
        return [
            item
            for item in dependents
            if item is not None and (required_scope is None or required_scope in item["scopes"])
        ]

    def _osdk_dependent(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        application: OsdkApplicationRow,
        *,
        resource_type: str,
        api_name: str,
    ) -> DependentOsdkApplication | None:
        resources = self.osdk_application_repository.resources_for_application(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            app_id=application["id"],
        )
        scopes = sorted(
            {
                scope
                for resource in resources
                if resource["resource_type"] == resource_type and resource["resource_api_name"] == api_name
                for scope in resource["scopes"]
            }
        )
        if not scopes:
            return None
        return {
            "appApiName": application["app_api_name"],
            "displayName": application["display_name"],
            "scopes": scopes,
        }


def _required_resource_type(resource_type: str) -> OntologyResourceType:
    if resource_type not in ONTOLOGY_RESOURCE_TYPES:
        raise ValidationFailed(
            "unknown ontology resource type",
            details={"resource_type": resource_type, "allowed": list(ONTOLOGY_RESOURCE_TYPES)},
        )
    return resource_type


def _required_window_days(window_days: int) -> int:
    if window_days < 1 or window_days > MAX_USAGE_WINDOW_DAYS:
        raise ValidationFailed(
            "windowDays out of bounds",
            details={"window_days": window_days, "min": 1, "max": MAX_USAGE_WINDOW_DAYS},
        )
    return window_days


def _window_start(window_days: int) -> str:
    """Compute the inclusive window start in the same local-offset ISO format as
    ``primitives._now`` so lexicographic timestamp comparison stays valid."""
    return (datetime.now().astimezone() - timedelta(days=window_days)).isoformat()
