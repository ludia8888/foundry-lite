"""Structural service groups used by the application composition root."""

from __future__ import annotations

from typing import Protocol

from foundry_lite.application.services.base import CoreService


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
    def fde_ontology_tools(self) -> CoreService: ...

    @property
    def fde_application_tools(self) -> CoreService: ...

    @property
    def fde_context(self) -> CoreService: ...

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
        services.fde_ontology_tools,
        services.fde_application_tools,
        services.fde_context,
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
