"""Structural service groups used by the application composition root."""

from __future__ import annotations

from typing import Protocol, TypedDict

from foundry_lite.application.services.aip.external_release_delivery_service import ExternalReleaseDeliveryService
from foundry_lite.application.services.aip.governed_release_live_attestation_service import (
    GovernedReleaseLiveAttestationService,
)
from foundry_lite.application.services.aip.runtime_services import (
    FdeApplicationToolService,
    FdeContextService,
    FdeDataConnectionToolService,
    FdeOntologyToolService,
    FdePilotService,
    FdePlatformToolService,
    FdeRuntimeService,
)
from foundry_lite.application.services.backup_restore_services import BackupRestoreServices
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.dataset_service import DatasetServices
from foundry_lite.application.services.mcp_rate_limit_service import McpRateLimitService
from foundry_lite.application.services.media_service import MediaServices
from foundry_lite.application.services.object_service import ObjectServices
from foundry_lite.application.services.runtime_bundle import IcebergMaintenanceService, InsightReviewService
from foundry_lite.application.services.source_services import (
    SourceCdcObjectIndexService,
    SourceConnectionTestService,
    SourceLifecycleService,
    SourceManagementService,
    SourceSchedulerService,
)

__all__ = [
    "CoreServiceGroups",
    "ExternalReleaseDeliveryService",
    "GovernedReleaseLiveAttestationService",
    "McpRateLimitService",
    "SharedCoreServices",
    "aip_service_items",
    "source_service_items",
]


class SharedCoreServices(TypedDict):
    """Concrete services reused while composing the full application graph."""

    backup_restore: BackupRestoreServices
    dataset: DatasetServices
    fde_application_tools: FdeApplicationToolService
    fde_context: FdeContextService
    fde_data_connection_tools: FdeDataConnectionToolService
    fde_ontology_tools: FdeOntologyToolService
    fde_pilot: FdePilotService
    fde_platform_tools: FdePlatformToolService
    fde_runtime: FdeRuntimeService
    iceberg_maintenance: IcebergMaintenanceService
    insight_review: InsightReviewService
    media: MediaServices
    object_store: ObjectServices
    source_management: SourceManagementService
    source_connection_test: SourceConnectionTestService
    source_lifecycle: SourceLifecycleService
    source_cdc_object_index: SourceCdcObjectIndexService
    source_scheduler: SourceSchedulerService


class CoreServiceGroups(Protocol):
    @property
    def agent_runtime(self) -> CoreService: ...

    @property
    def action_proposal(self) -> CoreService: ...

    @property
    def approval_execution(self) -> CoreService: ...

    @property
    def builder_runtime(self) -> CoreService: ...

    @property
    def context_compiler(self) -> CoreService: ...

    @property
    def evals(self) -> CoreService: ...

    @property
    def external_release_delivery(self) -> CoreService: ...

    @property
    def governed_release_live_attestation(self) -> CoreService: ...

    @property
    def fde_ontology_tools(self) -> CoreService: ...

    @property
    def fde_application_tools(self) -> CoreService: ...

    @property
    def fde_context(self) -> CoreService: ...

    @property
    def fde_data_connection_tools(self) -> CoreService: ...

    @property
    def fde_pilot(self) -> CoreService: ...

    @property
    def fde_platform_tools(self) -> CoreService: ...

    @property
    def fde_runtime(self) -> CoreService: ...

    @property
    def citation(self) -> CoreService: ...

    @property
    def logic_runtime(self) -> CoreService: ...

    @property
    def model_gateway(self) -> CoreService: ...

    @property
    def prompt_artifact(self) -> CoreService: ...

    @property
    def tool_broker(self) -> CoreService: ...

    @property
    def visual_builder(self) -> CoreService: ...

    @property
    def source_management(self) -> CoreService: ...

    @property
    def source_connection_test(self) -> CoreService: ...

    @property
    def source_lifecycle(self) -> CoreService: ...

    @property
    def source_cdc_object_index(self) -> CoreService: ...

    @property
    def source_scheduler(self) -> CoreService: ...

    @property
    def source_onboarding(self) -> CoreService: ...


def aip_service_items(services: CoreServiceGroups) -> list[CoreService]:
    return [
        services.agent_runtime,
        services.action_proposal,
        services.approval_execution,
        services.builder_runtime,
        services.context_compiler,
        services.evals,
        services.external_release_delivery,
        services.governed_release_live_attestation,
        services.fde_ontology_tools,
        services.fde_application_tools,
        services.fde_context,
        services.fde_data_connection_tools,
        services.fde_pilot,
        services.fde_platform_tools,
        services.fde_runtime,
        services.citation,
        services.logic_runtime,
        services.model_gateway,
        services.prompt_artifact,
        services.tool_broker,
        services.visual_builder,
    ]


def source_service_items(services: CoreServiceGroups) -> list[CoreService]:
    return [
        services.source_management,
        services.source_connection_test,
        services.source_lifecycle,
        services.source_cdc_object_index,
        services.source_scheduler,
        services.source_onboarding,
    ]
