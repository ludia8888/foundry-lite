"""MCP tool catalog for human-governed ontology and pipeline releases."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from foundry_lite.application.services.aip.governed_release_authorization import GOVERNED_RELEASE_SCOPE
from foundry_lite.domain.errors import ValidationFailed

JsonObject = Mapping[str, object]

GOVERNED_RELEASE_UI_RESOURCE_URI = "ui://foundry-lite/governed-release-v9-87ac4aeadd8c.html"

_RELEASE_KIND = {"type": "string", "enum": ["ontology", "pipeline"]}
_PIPELINE_KIND = {"type": "string", "enum": ["pipeline"]}
_TEXT = {"type": "string", "minLength": 1}
_POSITIVE_INTEGER = {"type": "integer", "minimum": 1}


@dataclass(frozen=True)
class GovernedReleaseToolSpec:
    """One release MCP tool plus its component visibility contract."""

    name: str
    description: str
    input_schema: JsonObject
    is_read_only: bool
    is_app_only: bool


def _schema(properties: dict[str, object], required: list[str]) -> dict[str, object]:
    """Build a closed JSON object schema for one release tool input."""

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _candidate_schema() -> dict[str, object]:
    """Describe the proposal identity accepted by candidate and status reads."""

    return _schema(
        {"releaseKind": _RELEASE_KIND, "proposalId": _TEXT},
        ["releaseKind", "proposalId"],
    )


def _workspace_schema() -> dict[str, object]:
    """Describe the isolated branch requested by the workspace opener."""

    return _schema(
        {"releaseKind": _RELEASE_KIND, "branchName": _TEXT, "pipelineId": _TEXT},
        ["releaseKind", "branchName"],
    )


def _create_branch_schema() -> dict[str, object]:
    """Describe an idempotent isolated release-branch creation request."""

    return _schema(
        {
            "releaseKind": _RELEASE_KIND,
            "branchName": _TEXT,
            "pipelineId": _TEXT,
            "idempotencyKey": _TEXT,
        },
        ["releaseKind", "branchName", "idempotencyKey"],
    )


def _inbox_schema() -> dict[str, object]:
    """Describe a bounded release-inbox listing request."""

    return _schema(
        {"releaseKind": _RELEASE_KIND, "limit": {"type": "integer", "minimum": 1, "maximum": 50}},
        ["releaseKind"],
    )


def _assign_schema() -> dict[str, object]:
    """Describe an idempotent assigned-human reviewer claim request."""

    return _schema(
        {"releaseKind": _RELEASE_KIND, "proposalId": _TEXT, "idempotencyKey": _TEXT},
        ["releaseKind", "proposalId", "idempotencyKey"],
    )


def _publish_schema() -> dict[str, object]:
    """Describe an explicit publication of one exact governed source candidate."""

    return _schema(
        {
            "releaseKind": _RELEASE_KIND,
            "proposalId": _TEXT,
            "idempotencyKey": _TEXT,
            "consumerOsdkApplicationId": _TEXT,
            "consumerOsdkCompliance": {"type": "object", "additionalProperties": True},
        },
        ["releaseKind", "proposalId", "idempotencyKey"],
    )


def _decision_schema() -> dict[str, object]:
    """Describe an explicit approve-or-reject decision request."""

    return _schema(
        {
            "releaseKind": _RELEASE_KIND,
            "proposalId": _TEXT,
            "decision": {"type": "string", "enum": ["approve", "reject"]},
            "expectedFingerprint": _TEXT,
            "comment": {"type": "string"},
            "idempotencyKey": _TEXT,
        },
        ["releaseKind", "proposalId", "decision", "idempotencyKey"],
    )


def _execute_schema() -> dict[str, object]:
    """Describe an approved proposal execution request."""

    return _schema(
        {
            "releaseKind": _RELEASE_KIND,
            "proposalId": _TEXT,
            "expectedFingerprint": _TEXT,
            "expectedSourceBaseSha": _TEXT,
            "expectedSourceHeadSha": _TEXT,
            "expectedSourceChecksFingerprint": _TEXT,
            "expectedSourceRulesFingerprint": _TEXT,
            "idempotencyKey": _TEXT,
        },
        ["releaseKind", "proposalId", "idempotencyKey"],
    )


def _deploy_schema() -> dict[str, object]:
    """Describe an idempotent pipeline-version promotion request."""

    return _schema(
        {
            "releaseKind": _PIPELINE_KIND,
            "proposalId": _TEXT,
            "pipelineId": _TEXT,
            "versionId": _TEXT,
            "idempotencyKey": _TEXT,
        },
        ["releaseKind", "proposalId", "pipelineId", "versionId", "idempotencyKey"],
    )


def _rollback_schema() -> dict[str, object]:
    """Describe the explicit target required for a safe rollback."""

    return _schema(
        {
            "releaseKind": _RELEASE_KIND,
            "proposalId": _TEXT,
            "targetVersionNumber": _POSITIVE_INTEGER,
            "expectedActiveVersionNumber": _POSITIVE_INTEGER,
            "pipelineId": _TEXT,
            "targetVersionId": _TEXT,
            "rolledBackFromId": _TEXT,
            "targetDeployId": _TEXT,
            "targetCommitId": _TEXT,
            "rolledBackFromDeployId": _TEXT,
            "idempotencyKey": _TEXT,
        },
        ["releaseKind", "proposalId", "idempotencyKey"],
    )


def _verify_completion_schema() -> dict[str, object]:
    """Identify the two server-owned workflow runs to verify and attest."""

    return _schema(
        {
            "ontologyWorkflowRunId": _TEXT,
            "pipelineWorkflowRunId": _TEXT,
            "idempotencyKey": _TEXT,
        },
        ["ontologyWorkflowRunId", "pipelineWorkflowRunId", "idempotencyKey"],
    )


_ACTION_TOOL_NAMES = (
    "create_release_branch",
    "publish_release_candidate",
    "assign_release_reviewer",
    "submit_release_decision",
    "execute_approved_release",
    "deploy_release",
    "rollback_release",
    "verify_release_completion",
)

GOVERNED_RELEASE_TOOLS = (
    GovernedReleaseToolSpec(
        "open_release_workspace",
        "Open a GPT release workspace for a new isolated Ontology or Pipeline branch.",
        _workspace_schema(),
        True,
        False,
    ),
    GovernedReleaseToolSpec(
        "create_release_branch",
        "Create the explicit isolated branch selected in the GPT release workspace.",
        _create_branch_schema(),
        False,
        True,
    ),
    GovernedReleaseToolSpec(
        "list_release_inbox",
        "List proposals this human can claim or review without exposing branch bodies.",
        _inbox_schema(),
        True,
        False,
    ),
    GovernedReleaseToolSpec(
        "publish_release_candidate",
        "Publish the author's exact validated proposal as a manifest-only GitHub pull request candidate.",
        _publish_schema(),
        False,
        True,
    ),
    GovernedReleaseToolSpec(
        "assign_release_reviewer",
        "Claim one unassigned proposal as the current human reviewer; the proposal author may self-claim.",
        _assign_schema(),
        False,
        True,
    ),
    GovernedReleaseToolSpec(
        "get_release_candidate",
        "Load one governed ontology or pipeline proposal and its next safe release step.",
        _candidate_schema(),
        True,
        False,
    ),
    GovernedReleaseToolSpec(
        "prepare_release_action",
        "Bind one explicit widget action to the current human OAuth principal.",
        _schema(
            {
                "targetTool": {"type": "string", "enum": list(_ACTION_TOOL_NAMES)},
                "arguments": {"type": "object"},
            },
            ["targetTool", "arguments"],
        ),
        False,
        True,
    ),
    GovernedReleaseToolSpec(
        "submit_release_decision",
        "Record the assigned human reviewer's explicit approve or reject decision.",
        _decision_schema(),
        False,
        True,
    ),
    GovernedReleaseToolSpec(
        "execute_approved_release",
        "Merge an approved internal branch by executing its immutable proposal.",
        _execute_schema(),
        False,
        True,
    ),
    GovernedReleaseToolSpec(
        "deploy_release",
        "Promote one explicit merged pipeline version using its idempotency key.",
        _deploy_schema(),
        False,
        True,
    ),
    GovernedReleaseToolSpec(
        "get_release_status",
        "Refresh governed review, merge, activation, and deployment status.",
        _candidate_schema(),
        True,
        False,
    ),
    GovernedReleaseToolSpec(
        "rollback_release",
        "Restore an archived ontology version or redeploy an explicit prior pipeline version.",
        _rollback_schema(),
        False,
        True,
    ),
    GovernedReleaseToolSpec(
        "verify_release_completion",
        "Collect server-owned provider evidence for two completed workflows and store a verified attestation.",
        _verify_completion_schema(),
        False,
        True,
    ),
)


def governed_release_tool(name: str) -> GovernedReleaseToolSpec:
    """Return the canonical release tool specification for ``name``."""

    for tool in GOVERNED_RELEASE_TOOLS:
        if tool.name == name:
            return tool
    raise ValidationFailed("Governed Release MCP tool is not available", details={"toolName": name})


def governed_release_action_tool(name: str) -> GovernedReleaseToolSpec:
    """Return a tool only when it belongs to the governed mutation set."""

    tool = governed_release_tool(name)
    if tool.name not in _ACTION_TOOL_NAMES:
        raise ValidationFailed("targetTool is not a governed release action", details={"targetTool": name})
    return tool


def governed_release_mcp_tool(tool: GovernedReleaseToolSpec) -> dict[str, object]:
    """Project a canonical tool specification into its MCP discovery shape."""

    schema = _action_input_schema(tool) if tool.name in _ACTION_TOOL_NAMES else dict(tool.input_schema)
    return {
        "name": tool.name,
        "title": tool.name,
        "description": tool.description,
        "inputSchema": schema,
        "securitySchemes": _release_security_schemes(),
        "annotations": _annotations(tool),
        "_meta": _tool_meta(tool),
    }


def _action_input_schema(tool: GovernedReleaseToolSpec) -> dict[str, object]:
    """Add the app-only confirmation token field to mutation tool schemas."""

    schema = dict(tool.input_schema)
    raw_properties = schema.get("properties")
    properties = dict(raw_properties) if isinstance(raw_properties, Mapping) else {}
    properties["widgetConfirmationToken"] = _TEXT
    return {**schema, "properties": properties}


def _annotations(tool: GovernedReleaseToolSpec) -> dict[str, object]:
    """Build MCP safety annotations from the tool's release semantics."""

    return {
        "readOnlyHint": tool.is_read_only,
        "destructiveHint": not tool.is_read_only and tool.name != "prepare_release_action",
        "idempotentHint": tool.name != "prepare_release_action",
        "openWorldHint": False,
    }


def _tool_meta(tool: GovernedReleaseToolSpec) -> dict[str, object]:
    """Expose either app-only visibility or the public release widget resource."""

    security = {"securitySchemes": _release_security_schemes()}
    if tool.is_app_only:
        return {**security, "ui": {"visibility": ["app"]}, "openai/visibility": "private"}
    return {
        **security,
        "ui": {"resourceUri": GOVERNED_RELEASE_UI_RESOURCE_URI},
        "openai/outputTemplate": GOVERNED_RELEASE_UI_RESOURCE_URI,
    }


def _release_security_schemes() -> list[dict[str, object]]:
    """Declare the exact OAuth scope required by every release tool."""

    return [{"type": "oauth2", "scopes": [GOVERNED_RELEASE_SCOPE]}]


__all__ = [
    "GOVERNED_RELEASE_TOOLS",
    "GOVERNED_RELEASE_UI_RESOURCE_URI",
    "GovernedReleaseToolSpec",
    "governed_release_action_tool",
    "governed_release_mcp_tool",
    "governed_release_tool",
]
