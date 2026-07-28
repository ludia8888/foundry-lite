from __future__ import annotations

import pytest
from foundry_lite.application.services.dataset.serving_projection import (
    project_dataset_serving_rows,
)
from foundry_lite.application.services.pipeline_dataset_output_projection import (
    pipeline_dataset_output_evidence,
    project_pipeline_dataset_output,
)
from foundry_lite.application.services.pipeline_media_reference import (
    required_source_media_reference,
)
from foundry_lite.application.services.pipeline_v2_runtime_contracts import (
    PipelineV2RuntimeArtifact,
)
from foundry_lite.application.services.pipeline_v2_runtime_security import (
    inherited_runtime_security,
    require_runtime_security_preserved,
)
from foundry_lite.domain.errors import InvariantViolation, ValidationFailed


def test_dataset_output_projects_only_declared_user_columns_and_preserves_evidence() -> None:
    source = {
        "order_id": "O-1",
        "analysis": {"risk": 2},
        "securityEnvelope": {"classification": "CONFIDENTIAL"},
        "_pipelineModelEvidence": {"providerRequestId": "provider-1", "cacheHit": True},
        "processingEvidence": {"processor": "ocr-v1"},
    }

    projection = project_pipeline_dataset_output(
        [source],
        {"columns": [{"name": "order_id"}, {"name": "analysis"}]},
    )
    evidence = pipeline_dataset_output_evidence(
        projection,
        {
            "tenantId": "tenant-demo",
            "classification": "CONFIDENTIAL",
            "policyVersions": ["policy-v3"],
            "allowedPrincipalSetIds": ["legal-readers"],
            "hasLegalHold": True,
        },
    )

    assert projection.serving_rows == ({"order_id": "O-1", "analysis": {"risk": 2}},)
    assert projection.fieldnames == ("order_id", "analysis")
    assert projection.row_evidence[0]["internalEvidence"] == {
        "_pipelineModelEvidence": {"providerRequestId": "provider-1", "cacheHit": True},
        "processingEvidence": {"processor": "ocr-v1"},
    }
    assert evidence["securityContract"]["principalMembershipEnforcement"] == "admin_only_without_resolver"
    assert evidence["securityContract"]["hasLegalHold"] is True


def test_dataset_output_contract_rejects_internal_evidence_column() -> None:
    with pytest.raises(ValidationFailed, match="cannot expose internal evidence"):
        project_pipeline_dataset_output(
            [{"order_id": "O-1", "_pipelineModelEvidence": {}}],
            {"columns": [{"name": "_pipelineModelEvidence"}]},
        )


def test_dataset_serving_projection_prefers_durable_serving_columns() -> None:
    rows = [
        {
            "order_id": "O-1",
            "analysis": {"risk": 2},
            "branch": "main",
            "version": "dsv-1",
            "_pipelineModelEvidence": {"providerRequestId": "provider-1"},
        }
    ]

    assert project_dataset_serving_rows(
        rows,
        {"servingColumns": ["order_id", "analysis"]},
        {"columns": [{"name": "order_id"}, {"name": "analysis"}, {"name": "branch"}]},
    ) == [{"order_id": "O-1", "analysis": {"risk": 2}}]


def test_dataset_serving_projection_uses_schema_contract_without_storage_columns() -> None:
    rows = [{"order_id": "O-1", "branch": "main", "version": "dsv-1"}]

    assert project_dataset_serving_rows(
        rows,
        {},
        {"columns": [{"name": "order_id"}]},
    ) == [{"order_id": "O-1"}]


def test_runtime_security_rejects_policy_principal_and_legal_hold_weakening() -> None:
    source = _source_artifact()
    inherited = inherited_runtime_security([source])
    require_runtime_security_preserved([source], inherited, resource_ref="clean.contracts")

    weakened = {
        **inherited,
        "policyVersions": [],
        "allowedPrincipalSetIds": [],
        "hasLegalHold": False,
        "principalSetMode": "any",
        "legalHoldMode": "none",
    }
    with pytest.raises(ValidationFailed) as raised:
        require_runtime_security_preserved([source], weakened, resource_ref="clean.contracts")

    assert set(raised.value.details["weakenedFields"]) == {
        "policyVersions",
        "allowedPrincipalSetIds",
        "principalSetMode",
        "hasLegalHold",
        "legalHoldMode",
    }


def test_derivative_reference_never_combines_original_id_with_derivative_hash() -> None:
    derivative = {
        "mediaDerivativeId": "md-1",
        "mediaItemVersionId": "miv-original",
        "contentHash": "derivative-hash",
        "mimeType": "application/json",
        "sourceMediaReference": {
            "mediaItemVersionId": "miv-original",
            "contentHash": "original-hash",
            "mimeType": "application/pdf",
            "sourceLocator": {"pageNumber": 1},
        },
    }

    assert required_source_media_reference(derivative) == {
        "mediaItemVersionId": "miv-original",
        "contentHash": "original-hash",
        "mimeType": "application/pdf",
        "sourceLocator": {"pageNumber": 1},
    }
    with pytest.raises(InvariantViolation):
        required_source_media_reference(
            {key: value for key, value in derivative.items() if key != "sourceMediaReference"}
        )


def _source_artifact() -> PipelineV2RuntimeArtifact:
    return PipelineV2RuntimeArtifact(
        node_id="source",
        descriptor_id="source.dataset",
        spec_version=1,
        port_id="dataset",
        artifact_kind="dataset_version",
        plane="dataset",
        items=({"order_id": "O-1"},),
        artifact_ref={"datasetRef": "raw.contracts", "versionId": "dsv-1"},
        manifest={},
        security_envelope={
            "tenantId": "tenant-demo",
            "classification": "CONFIDENTIAL",
            "policyVersions": ["policy-v3"],
            "allowedPrincipalSetIds": ["legal-readers"],
            "hasLegalHold": True,
            "principalSetMode": "all_required",
            "legalHoldMode": "sticky",
        },
        status="COMMITTED",
        is_serving=True,
        committed_at="2026-07-17T00:00:00Z",
    )
