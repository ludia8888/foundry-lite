from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.services.aip.governed_release_candidate_evidence import (
    release_candidate_evidence,
)
from foundry_lite.application.services.aip.governed_release_catalog import (
    governed_release_mcp_tool,
    governed_release_tool,
)


def test_execute_schema_allows_optional_exact_source_snapshot_bindings() -> None:
    tool = governed_release_tool("execute_approved_release")
    schema = tool.input_schema
    properties = schema["properties"]

    assert isinstance(properties, Mapping)
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["releaseKind", "proposalId", "idempotencyKey"]
    for field in (
        "expectedSourceBaseSha",
        "expectedSourceHeadSha",
        "expectedSourceChecksFingerprint",
        "expectedSourceRulesFingerprint",
    ):
        assert properties[field] == {"type": "string", "minLength": 1}

    mcp_schema = governed_release_mcp_tool(tool)["inputSchema"]
    assert isinstance(mcp_schema, Mapping)
    mcp_properties = mcp_schema["properties"]
    assert isinstance(mcp_properties, Mapping)
    assert "widgetConfirmationToken" in mcp_properties
    assert all(field not in mcp_schema["required"] for field in properties if field.startswith("expectedSource"))


def test_rollback_schema_allows_optional_exact_external_application_target_binding() -> None:
    tool = governed_release_tool("rollback_release")
    schema = tool.input_schema
    properties = schema["properties"]

    assert isinstance(properties, Mapping)
    assert schema["additionalProperties"] is False
    for field in ("targetDeployId", "targetCommitId", "rolledBackFromDeployId"):
        assert properties[field] == {"type": "string", "minLength": 1}
        assert field not in schema["required"]

    mcp_schema = governed_release_mcp_tool(tool)["inputSchema"]
    assert isinstance(mcp_schema, Mapping)
    mcp_properties = mcp_schema["properties"]
    assert isinstance(mcp_properties, Mapping)
    assert "widgetConfirmationToken" in mcp_properties


def test_verify_completion_schema_accepts_only_server_lookup_ids_and_idempotency() -> None:
    tool = governed_release_tool("verify_release_completion")

    assert tool.is_read_only is False
    assert tool.is_app_only is True
    assert tool.input_schema == {
        "type": "object",
        "properties": {
            "ontologyWorkflowRunId": {"type": "string", "minLength": 1},
            "pipelineWorkflowRunId": {"type": "string", "minLength": 1},
            "idempotencyKey": {"type": "string", "minLength": 1},
        },
        "required": ["ontologyWorkflowRunId", "pipelineWorkflowRunId", "idempotencyKey"],
        "additionalProperties": False,
    }
    mcp_schema = governed_release_mcp_tool(tool)["inputSchema"]
    assert isinstance(mcp_schema, Mapping)
    properties = mcp_schema["properties"]
    assert isinstance(properties, Mapping)
    assert set(properties) == {
        "ontologyWorkflowRunId",
        "pipelineWorkflowRunId",
        "idempotencyKey",
        "widgetConfirmationToken",
    }


def test_ontology_candidate_revalidates_the_submitted_plan_against_the_active_version() -> None:
    """Foundry runs merge checks against Main, not against evidence captured at submission.

    Main keeps moving while a proposal waits for review, so a plan computed against a superseded
    version is no longer a valid merge check and the candidate must say so.
    """

    proposal = {
        "fingerprint": "sha256:candidate",
        "submittedMigrationPlan": {"status": "compatible", "sourceOntologyVersionId": "ont_v2", "changes": []},
    }
    current = release_candidate_evidence("ontology", proposal, {"activeOntology": {"ontologyVersionId": "ont_v2"}})
    superseded = release_candidate_evidence("ontology", proposal, {"activeOntology": {"ontologyVersionId": "ont_v3"}})

    assert "current_active_revalidation" not in current["riskClassification"]["missingEvidence"]
    assert "current_active_revalidation" in superseded["riskClassification"]["missingEvidence"]
    assert any(
        "predates the active Ontology version" in reason for reason in superseded["riskClassification"]["reasons"]
    )
