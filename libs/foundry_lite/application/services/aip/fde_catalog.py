"""Canonical AI FDE modes, capabilities, and server-owned tool contracts."""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.services.aip.tool_broker import ToolSpec
from foundry_lite.domain.errors import ValidationFailed

FDE_MODE_ONTOLOGY = "ontology_editing"
FDE_MODE_EXPLORATION = "exploration"
FDE_TOOL_DISCOVERY_EAGER = "eager"
FDE_TOOL_DISCOVERY_LAZY = "lazy"
_META_CAPABILITY = "fde.control"


@dataclass(frozen=True)
class FdeModeSpec:
    mode_id: str
    title: str
    description: str
    capabilities: tuple[str, ...]
    scope_prefixes: tuple[str, ...]
    availability: str = "current"

    def to_payload(self) -> dict[str, object]:
        return {
            "modeId": self.mode_id,
            "title": self.title,
            "description": self.description,
            "availability": self.availability,
            "capabilities": list(self.capabilities),
            "scopePrefixes": list(self.scope_prefixes),
        }


FDE_MODES = (
    FdeModeSpec(
        FDE_MODE_EXPLORATION,
        "Exploration",
        "Search and inspect governed platform resources without mutation.",
        ("resource.search", "resource.inspect"),
        ("tenant:", "project:", "resource:"),
    ),
    FdeModeSpec(
        FDE_MODE_ONTOLOGY,
        "Ontology editing",
        "Create or update ontology resources on an isolated branch and submit a proposal.",
        ("ontology.inspect", "ontology.validate", "ontology.edit", "ontology.propose"),
        ("ontology-branch:",),
    ),
    FdeModeSpec(
        "data_integration",
        "Data integration",
        "Build, validate, test, and propose governed Pipeline Builder branches.",
        ("pipeline.inspect", "pipeline.validate", "pipeline.edit", "pipeline.test", "pipeline.propose"),
        ("pipeline-branch:",),
    ),
    FdeModeSpec(
        "data_connection",
        "Data connection",
        "Inspect a governed Source, diagnose connection evidence, and run an approved live probe.",
        ("source.inspect", "source.diagnostics", "source.test"),
        ("source:",),
    ),
    FdeModeSpec(
        "functions_editing",
        "Functions editing",
        "Author functionType resources on an Ontology branch and evaluate active functions.",
        ("ontology.inspect", "ontology.validate", "ontology.edit", "ontology.propose", "function.execute"),
        ("ontology-branch:", "function:"),
    ),
    FdeModeSpec(
        "governance",
        "Governance",
        "Inspect permission-scoped projects, resources, and governance evidence.",
        ("resource.search", "resource.inspect", "governance.project.inspect"),
        ("tenant:", "project:", "resource:"),
    ),
    FdeModeSpec(
        "ml",
        "Machine learning",
        "Inspect the governed trained-model catalog and runtime contracts.",
        ("ml.catalog.inspect",),
        ("tenant:", "model:"),
    ),
    FdeModeSpec(
        "osdk_react",
        "OSDK React",
        "Inspect and explicitly update Developer Console application resource scopes.",
        ("osdk.inspect", "osdk.edit", "pilot.plan", "pilot.generate"),
        ("osdk-app:", "project:"),
    ),
    FdeModeSpec(
        "platform_qa",
        "Platform Q&A",
        "Answer platform questions from a curated, versioned documentation allowlist.",
        ("platform.docs.search",),
        ("tenant:", "docs:"),
    ),
)


def fde_catalog_payload() -> dict[str, object]:
    return {
        "modes": [mode.to_payload() for mode in FDE_MODES],
        "tools": [_tool_payload(tool) for tool in _FDE_TOOLS],
        "toolDiscovery": [FDE_TOOL_DISCOVERY_LAZY, FDE_TOOL_DISCOVERY_EAGER],
        "safetyBoundary": {
            "writes": "governed_scope_only",
            "branchFirstResources": ["ontology", "pipeline"],
            "productionMerge": "human_proposal_review_required",
            "identity": "invoking_user",
            "externalApprovalTools": "not_exposed",
        },
    }


def fde_tool_catalog(mode_id: str, capabilities: tuple[str, ...]) -> tuple[ToolSpec, ...]:
    mode = current_fde_mode(mode_id)
    enabled = set(capabilities or mode.capabilities)
    unknown = enabled - set(mode.capabilities)
    if unknown:
        raise ValidationFailed(
            "AI FDE capability is not available in this mode",
            details={"mode": mode_id, "capabilities": sorted(unknown)},
        )
    selected = enabled | {_META_CAPABILITY}
    tool_ids = {tool_id for capability in selected for tool_id in _TOOLS_BY_CAPABILITY[capability]}
    return tuple(tool for tool in _FDE_TOOLS if tool.tool_id in tool_ids)


def fde_tool_manifest(
    mode_id: str,
    capabilities: tuple[str, ...],
    tool_discovery: str = FDE_TOOL_DISCOVERY_EAGER,
) -> tuple[ToolSpec, ...]:
    catalog = fde_tool_catalog(mode_id, capabilities)
    if tool_discovery == FDE_TOOL_DISCOVERY_EAGER:
        return catalog
    if tool_discovery != FDE_TOOL_DISCOVERY_LAZY:
        raise ValidationFailed("AI FDE toolDiscovery must be eager or lazy")
    meta_ids = set(_TOOLS_BY_CAPABILITY[_META_CAPABILITY])
    return tuple(tool for tool in catalog if tool.tool_id in meta_ids)


def current_fde_mode(mode_id: str) -> FdeModeSpec:
    for mode in FDE_MODES:
        if mode.mode_id == mode_id and mode.availability == "current":
            return mode
    raise ValidationFailed("AI FDE mode is not currently available", details={"mode": mode_id})


def _tool_payload(tool: ToolSpec) -> dict[str, object]:
    return {
        "toolId": tool.tool_id,
        "version": tool.version,
        "description": tool.description,
        "inputSchema": dict(tool.input_schema),
        "effect": tool.effect,
        "confirmationPolicy": tool.confirmation_policy,
        "requiredPermission": tool.required_permission,
        "modeIds": _mode_ids_for_tool(tool.tool_id),
    }


def _mode_ids_for_tool(tool_id: str) -> list[str]:
    return [
        mode.mode_id
        for mode in FDE_MODES
        if any(tool_id in _TOOLS_BY_CAPABILITY[capability] for capability in (*mode.capabilities, _META_CAPABILITY))
    ]


def _schema(properties: dict[str, object], required: list[str]) -> dict[str, object]:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def _tool(
    tool_id: str,
    description: str,
    permission: str,
    properties: dict[str, object] | None = None,
    required: list[str] | None = None,
    effect: str = "READ",
    confirmation: str = "NONE",
) -> ToolSpec:
    return ToolSpec(
        tool_id=tool_id,
        version="v1",
        description=description,
        input_schema=_schema(properties or {}, required or []),
        output_schema={"type": "object"},
        effect=effect,  # type: ignore[arg-type]
        required_permission=permission,
        confirmation_policy=confirmation,  # type: ignore[arg-type]
        result_classification="internal",
    )


_FDE_TOOLS = (
    _tool(
        "fde.tools.search",
        "Search the server-owned tool catalog and lazily activate matching governed tools.",
        "ontology:read",
        {"query": {"type": "string"}, "maxResults": {"type": "integer"}},
        ["query"],
    ),
    _tool(
        "fde.plan.present",
        "Persist and present a structured execution plan before mutating resources.",
        "ontology:read",
        {
            "objective": {"type": "string"},
            "steps": {"type": "array"},
            "assumptions": {"type": "array"},
            "risks": {"type": "array"},
            "requiredApprovals": {"type": "array"},
        },
        ["objective", "steps", "assumptions", "risks", "requiredApprovals"],
    ),
    _tool(
        "fde.clarification.request",
        "Persist a structured, optionally blocking clarification request for the user.",
        "ontology:read",
        {
            "question": {"type": "string"},
            "options": {"type": "array"},
            "reason": {"type": "string"},
            "isBlocking": {"type": "boolean"},
        },
        ["question", "options", "reason", "isBlocking"],
    ),
    _tool(
        "resource.search",
        "Search permission-scoped Compass resources.",
        "ontology:read",
        {"query": {"type": "string"}},
        ["query"],
    ),
    _tool(
        "resource.inspect",
        "Inspect one permission-scoped Compass resource by RID.",
        "ontology:read",
        {"rid": {"type": "string"}},
        ["rid"],
    ),
    _tool(
        "governance.project.inspect",
        "Inspect a project and its visible folders and resources.",
        "ontology:read",
        {"projectId": {"type": "string"}},
        ["projectId"],
    ),
    _tool(
        "ontology.branch.inspect",
        "Inspect the selected Ontology branch, resources, and three-way diff.",
        "ontology:validate",
    ),
    _tool(
        "ontology.branch.validate",
        "Validate the selected Ontology branch and return migration evidence.",
        "ontology:validate",
    ),
    _tool(
        "ontology.branch.apply_patch",
        "Atomically patch supported resources on the selected Ontology branch; never edits active Ontology.",
        "ontology:validate",
        {
            "upsertResources": {"type": "array"},
            "deleteResources": {"type": "array"},
            "changeSummary": {"type": "string"},
        },
        ["upsertResources", "deleteResources", "changeSummary"],
        "WRITE",
        "USER",
    ),
    _tool(
        "ontology.branch.propose",
        "Submit the selected Ontology branch for human review without merging it.",
        "ontology:validate",
        {"title": {"type": "string"}, "description": {"type": "string"}, "idempotencyKey": {"type": "string"}},
        ["title", "description", "idempotencyKey"],
        "PROPOSE_WRITE",
        "HUMAN_REVIEW",
    ),
    _tool("pipeline.branch.inspect", "Inspect the selected Pipeline branch and graph diff.", "pipeline:read"),
    _tool("pipeline.branch.validate", "Validate the selected Pipeline graph and source contracts.", "pipeline:read"),
    _tool(
        "pipeline.branch.update_graph",
        "CAS-update the selected Pipeline branch graph; never deploys it.",
        "pipeline:write",
        {"graph": {"type": "object"}, "expectedFingerprint": {"type": "string"}},
        ["graph", "expectedFingerprint"],
        "WRITE",
        "USER",
    ),
    _tool(
        "pipeline.branch.run_tests",
        "Run persisted validation tests on the selected Pipeline branch.",
        "pipeline:write",
        effect="WRITE",
        confirmation="USER",
    ),
    _tool(
        "pipeline.branch.propose",
        "Submit the selected Pipeline branch to its human review queue without deployment.",
        "pipeline:write",
        {"title": {"type": "string"}, "description": {"type": "string"}, "idempotencyKey": {"type": "string"}},
        ["title", "idempotencyKey"],
        "PROPOSE_WRITE",
        "HUMAN_REVIEW",
    ),
    _tool("source.inspect", "Inspect selected Source configuration without exposing credentials.", "source:read"),
    _tool(
        "source.connection_history",
        "Read durable connection-test and egress evidence for the selected Source.",
        "source:read",
        {"limit": {"type": "integer"}},
    ),
    _tool(
        "source.test_connection",
        "Run one idempotent, approved live Source probe through its governed network route.",
        "source:write",
        {"expectedConfigFingerprint": {"type": "string"}, "idempotencyKey": {"type": "string"}},
        ["expectedConfigFingerprint", "idempotencyKey"],
        "WRITE",
        "USER",
    ),
    _tool(
        "function.execute",
        "Execute one active, permission-scoped Ontology function through Logic runtime.",
        "function:execute",
        {"functionApiName": {"type": "string"}, "inputs": {"type": "object"}},
        ["functionApiName", "inputs"],
    ),
    _tool(
        "ml.catalog.inspect",
        "Inspect governed trained-model definitions available to Pipeline Builder.",
        "pipeline:read",
    ),
    _tool(
        "osdk.application.inspect",
        "Inspect one Developer Console application and its resource restrictions.",
        "developer_console:read",
    ),
    _tool(
        "osdk.application.update_resources",
        "Explicitly replace one application's governed resource restrictions with idempotency evidence.",
        "developer_console:manage",
        {"resources": {"type": "array"}, "idempotencyKey": {"type": "string"}},
        ["resources", "idempotencyKey"],
        "WRITE",
        "USER",
    ),
    _tool(
        "platform.docs.search",
        "Search a curated allowlist of checked-in platform documentation.",
        "ontology:read",
        {"query": {"type": "string"}, "maxResults": {"type": "integer"}},
        ["query"],
    ),
    _tool(
        "pilot.application.plan",
        "Create a typed Pilot generation plan without mutation.",
        "developer_console:read",
        {"applicationName": {"type": "string"}, "domainDescription": {"type": "string"}},
        ["applicationName", "domainDescription"],
    ),
    _tool(
        "pilot.application.generate",
        "Generate a governed Pilot bundle with Ontology branch, seed contract, React manifest, CI, and OSDK app.",
        "developer_console:manage",
        {"plan": {"type": "object"}, "idempotencyKey": {"type": "string"}},
        ["plan", "idempotencyKey"],
        "WRITE",
        "USER",
    ),
)

_TOOLS_BY_CAPABILITY = {
    _META_CAPABILITY: ("fde.tools.search", "fde.plan.present", "fde.clarification.request"),
    "resource.search": ("resource.search",),
    "resource.inspect": ("resource.inspect",),
    "governance.project.inspect": ("governance.project.inspect",),
    "ontology.inspect": ("ontology.branch.inspect",),
    "ontology.validate": ("ontology.branch.validate",),
    "ontology.edit": ("ontology.branch.apply_patch",),
    "ontology.propose": ("ontology.branch.propose",),
    "pipeline.inspect": ("pipeline.branch.inspect",),
    "pipeline.validate": ("pipeline.branch.validate",),
    "pipeline.edit": ("pipeline.branch.update_graph",),
    "pipeline.test": ("pipeline.branch.run_tests",),
    "pipeline.propose": ("pipeline.branch.propose",),
    "source.inspect": ("source.inspect",),
    "source.diagnostics": ("source.connection_history",),
    "source.test": ("source.test_connection",),
    "function.execute": ("function.execute",),
    "ml.catalog.inspect": ("ml.catalog.inspect",),
    "osdk.inspect": ("osdk.application.inspect",),
    "osdk.edit": ("osdk.application.update_resources",),
    "platform.docs.search": ("platform.docs.search",),
    "pilot.plan": ("pilot.application.plan",),
    "pilot.generate": ("pilot.application.generate",),
}
