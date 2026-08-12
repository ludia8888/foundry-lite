"""Dependency variants used while composing the core service graph."""

from __future__ import annotations

from dataclasses import replace

from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.model_gateway_bridge import GovernedSemanticModelBridge
from foundry_lite.application.services.aip.model_gateway import ModelGatewayService


def pipeline_dependencies(
    dependencies: CoreDependencies,
    model_gateway: ModelGatewayService,
) -> CoreDependencies:
    """Bind Pipeline services to the already composed governed model gateway."""
    aip = replace(
        dependencies.aip,
        governed_semantic_model_port=GovernedSemanticModelBridge(model_gateway),
    )
    return CoreDependencies(
        paths=dependencies.paths,
        security=dependencies.security,
        action=dependencies.action,
        data=dependencies.data,
        object_store=dependencies.object_store,
        runtime=dependencies.runtime,
        aip=aip,
        media=dependencies.media,
        source=dependencies.source,
        pipeline_dag_orchestrator=dependencies.pipeline_dag_orchestrator,
        profile=dependencies.profile,
    )


__all__ = ["pipeline_dependencies"]
