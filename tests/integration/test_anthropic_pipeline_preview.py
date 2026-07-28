from __future__ import annotations

import base64
import io
import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import cast

from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports.model_registry_repository import ModelCatalogSeed
from foundry_lite.application.ports.transaction_context import TransactionManager
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.adapters.anthropic_language_model import (
    AnthropicHttpResponse,
    AnthropicLanguageModel,
)
from foundry_lite.infrastructure.adapters.model_media_resolver import RepositoryModelMediaResolver
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from foundry_lite.infrastructure.secrets import EnvSecretProvider
from sqlalchemy import func, select
from sqlalchemy.engine import Connection

_MODEL_ID = "anthropic:claude-sonnet-5"
_MODEL_REVISION = "claude-sonnet-5"
_PROMPT_VERSION = "document-layout-contract@1"
_PDF_PATH = Path(__file__).resolve().parents[2] / "docs" / "Foundry-lite_AIP_Architecture_Report.pdf"
_OUTPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "required": ["documentType", "primaryHeading"],
    "properties": {
        "documentType": {"type": "string"},
        "primaryHeading": {"type": "string"},
    },
    "additionalProperties": False,
}


class _CapturedAnthropicTransport:
    def __init__(
        self,
        content: str = '{"documentType":"architecture_report","primaryHeading":"Foundry-lite AIP"}',
    ) -> None:
        self.headers: dict[str, str] | None = None
        self.payload: dict[str, object] | None = None
        self.timeout_seconds: int | None = None
        self.content = content

    def __call__(
        self,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_seconds: int,
    ) -> AnthropicHttpResponse:
        self.headers = dict(headers)
        self.payload = dict(payload)
        self.timeout_seconds = timeout_seconds
        return AnthropicHttpResponse(
            status_code=200,
            headers={"request-id": "req_anthropic_pipeline_preview"},
            body={
                "id": "msg_anthropic_pipeline_preview",
                "model": _MODEL_REVISION,
                "stop_reason": "end_turn",
                "content": [
                    {
                        "type": "text",
                        "text": self.content,
                    }
                ],
                "usage": {"input_tokens": 721, "output_tokens": 19},
            },
        )


def test_anthropic_layout_aware_pdf_preview_is_structured_and_non_committing(tmp_path: Path) -> None:
    transport = _CapturedAnthropicTransport()
    dependencies = _anthropic_dependencies(tmp_path, transport)
    foundry = FoundryLite(dependencies=dependencies)
    ctx = demo_admin_context()
    source_bytes = _PDF_PATH.read_bytes()
    media_version_id = _commit_pdf(foundry, source_bytes)
    branch_id = _create_branch(foundry)
    before = _serving_counts(dependencies.engine)
    assert before == {
        "datasetVersions": 0,
        "mediaItemVersions": 1,
        "mediaDerivatives": 0,
        "contentUnits": 0,
    }

    queued = foundry.pipelines.create_preview_run(
        branch_id,
        graph=_graph(media_version_id),
        target_node_id="out",
        limits={"mediaItems": 1, "tableRows": 1, "totalBytes": 4 * 1024 * 1024},
        idempotency_key="anthropic-layout-preview-run",
        ctx=ctx,
    )
    completed = foundry.pipelines.execute_preview_run(str(queued["id"]), ctx=ctx)

    _assert_preview_result(completed)
    _assert_anthropic_payload(transport, source_bytes)
    _assert_persisted_evidence(dependencies.engine, str(queued["id"]))
    assert _serving_counts(dependencies.engine) == before


def test_anthropic_invalid_json_preview_persists_safe_trial_error_without_serving_commit(tmp_path: Path) -> None:
    transport = _CapturedAnthropicTransport("not-json password=raw-provider-secret")
    dependencies = _anthropic_dependencies(tmp_path, transport)
    foundry = FoundryLite(dependencies=dependencies)
    ctx = demo_admin_context()
    media_version_id = _commit_pdf(foundry, _PDF_PATH.read_bytes())
    branch_id = _create_branch(foundry)
    before = _serving_counts(dependencies.engine)

    queued = foundry.pipelines.create_preview_run(
        branch_id,
        graph=_graph(media_version_id),
        target_node_id="out",
        limits={"mediaItems": 1, "tableRows": 1, "totalBytes": 4 * 1024 * 1024},
        idempotency_key="anthropic-invalid-json-preview-run",
        ctx=ctx,
    )
    completed = foundry.pipelines.execute_preview_run(str(queued["id"]), ctx=ctx)
    persisted = foundry.pipelines.get_preview_run(str(queued["id"]), ctx=ctx)

    assert completed["status"] == "FAILED"
    assert completed["commitForbidden"] is True
    assert completed["servingVersionCreated"] is False
    error = cast("dict[str, object]", completed["error"])
    trial = cast("dict[str, object]", cast("dict[str, object]", error["details"])["trialEvidence"])
    assert cast("dict[str, object]", trial["final"])["status"] == "failed"
    parse = cast("list[dict[str, object]]", trial["parseAttempts"])[0]
    assert parse["status"] == "parse_failed"
    assert cast("dict[str, object]", parse["responseSnapshot"])["contentRedacted"] is True
    assert persisted["error"] == completed["error"]
    assert "raw-provider-secret" not in json.dumps(completed)
    assert "test-only-anthropic-key" not in json.dumps(completed)
    assert _serving_counts(dependencies.engine) == before


def _anthropic_dependencies(
    tmp_path: Path,
    transport: _CapturedAnthropicTransport,
) -> CoreDependencies:
    base = create_local_core_dependencies(
        db_url=f"sqlite:///{tmp_path / 'anthropic-preview.db'}",
        storage_root=tmp_path / "runtime",
    )
    resolver = RepositoryModelMediaResolver(base.engine, base.media_repository, base.media_storage)
    secrets = EnvSecretProvider(
        env_aliases={"anthropic_api_key": "TEST_ANTHROPIC_API_KEY"},
        environ={"TEST_ANTHROPIC_API_KEY": "test-only-anthropic-key"},
    )
    adapter = AnthropicLanguageModel(secrets, media_resolver=resolver, transport=transport)
    aip = replace(base.aip, language_model_adapter=adapter, model_catalog_seed=_model_catalog_seed())
    return CoreDependencies(
        paths=base.paths,
        security=base.security,
        action=base.action,
        data=base.data,
        object_store=base.object_store,
        runtime=base.runtime,
        aip=aip,
        media=base.media,
        source=base.source,
        profile=base.profile,
    )


def _model_catalog_seed() -> ModelCatalogSeed:
    return ModelCatalogSeed(
        provider_id="anthropic-preview-provider",
        provider_type="anthropic",
        profile_name="anthropic",
        region="global",
        secret_ref="anthropic_api_key",
        retention_policy="test-no-retention",
        training_policy="test-no-training",
        model_id=_MODEL_ID,
        provider_model_id=_MODEL_REVISION,
        revision=_MODEL_REVISION,
        lifecycle="stable",
        capabilities_json={
            "pdf_input": True,
            "structured_outputs": True,
            "sampling_parameters": False,
        },
        context_limit=1_000_000,
        output_limit=128_000,
        pricing_json={},
        allowed_classifications=("public",),
        aliases=("document-vlm",),
    )


def _commit_pdf(foundry: FoundryLite, source_bytes: bytes) -> str:
    ctx = demo_admin_context()
    media_set = foundry.media.create_media_set(
        ctx,
        namespace="qa",
        name="anthropic_layout_pdf",
        schema_type="document",
        primary_format="pdf",
        allowed_input_formats=("pdf",),
        classification="public",
    )
    transaction_id = foundry.media.open_transaction(
        ctx,
        media_set_id=media_set.media_set_id,
        idempotency_key="anthropic-layout-pdf-upload",
    )
    staged = foundry.media.upload(
        ctx,
        media_set_id=media_set.media_set_id,
        media_transaction_id=transaction_id,
        logical_path="/reports/foundry-lite-aip.pdf",
        source=io.BytesIO(source_bytes),
        supplied_mime_type="application/pdf",
        schema_type="document",
        format="pdf",
        security_envelope={"tenantId": ctx.tenant_id, "classification": "public"},
    )
    foundry.media.commit(ctx, media_transaction_id=transaction_id)
    return staged.media_item_version_id


def _create_branch(foundry: FoundryLite) -> str:
    branch = foundry.pipelines.create_branch(
        pipeline_id="anthropic-layout-preview",
        name="draft",
        idempotency_key="anthropic-layout-preview-branch",
        ctx=demo_admin_context(),
    )
    return str(branch["id"])


def _graph(media_version_id: str) -> dict[str, object]:
    nodes = [
        _node(
            "media",
            "source",
            "source.media_set",
            {
                "mediaSetRef": "qa.anthropic_layout_pdf",
                "selection": {"mode": "version_ids", "versionIds": [media_version_id]},
            },
        ),
        _node("rows", "transform", "bridge.media_to_table_rows", {}),
        _node("semantic", "transform", "transform.use_llm", _semantic_config()),
        _node("out", "output", "output.dataset", {"outputDatasetRef": "preview.anthropic_layout"}),
    ]
    edges = [
        _edge("media-rows", "media", "media", "rows", "media"),
        _edge("rows-semantic", "rows", "dataset", "semantic", "input"),
        _edge("semantic-out", "semantic", "dataset", "out", "input"),
    ]
    return {
        "schemaVersion": 2,
        "nodes": nodes,
        "edges": edges,
        "layout": {},
        "outputContract": {"columns": []},
        "tests": [],
        "schedule": None,
    }


def _semantic_config() -> dict[str, object]:
    return {
        "modelAlias": "document-vlm",
        "promptVersionId": _PROMPT_VERSION,
        "promptMode": "layout_aware_vision",
        "systemPrompt": "Interpret headings, tables, and body regions as an architecture report.",
        "inputFields": ["mediaReference"],
        "mediaReferenceField": "mediaReference",
        "outputColumn": "interpretation",
        "outputSchema": dict(_OUTPUT_SCHEMA),
        "dataClassification": "public",
        "outputMode": "simple",
        "skipRecomputingRows": True,
        "modelParameters": {"temperature": 0, "maxOutputTokens": 128},
    }


def _assert_preview_result(completed: dict[str, object]) -> None:
    assert completed["status"] == "SUCCEEDED"
    assert completed["commitForbidden"] is True
    assert completed["servingVersionCreated"] is False
    outputs = cast("list[dict[str, object]]", completed["outputs"])
    rows = cast("list[dict[str, object]]", outputs[0]["items"])
    assert rows[0]["interpretation"] == {
        "documentType": "architecture_report",
        "primaryHeading": "Foundry-lite AIP",
    }
    evidence = cast("dict[str, object]", rows[0]["_pipelineModelEvidence"])
    assert evidence["provider"] == "anthropic"
    assert evidence["resolvedModelId"] == _MODEL_ID
    assert evidence["resolvedModelRevision"] == _MODEL_REVISION
    assert evidence["promptVersionId"] == _PROMPT_VERSION
    assert evidence["promptMode"] == "layout_aware_vision"
    assert str(evidence["promptHash"]).startswith("sha256:")
    trial = cast("dict[str, object]", rows[0]["_pipelineModelTrialEvidence"])
    assert trial["schemaVersion"] == 1
    assert cast("dict[str, object]", trial["noCommit"]) == {
        "commitForbidden": True,
        "servingVersionCreated": False,
    }
    assert cast("dict[str, object]", trial["final"])["typedOutput"] == rows[0]["interpretation"]
    pins = cast("dict[str, object]", trial["pins"])
    assert pins["resolvedModelId"] == _MODEL_ID
    assert pins["promptVersionId"] == _PROMPT_VERSION
    assert "Interpret headings, tables, and body regions" not in json.dumps(trial)
    assert "test-only-anthropic-key" not in json.dumps(trial)


def _assert_anthropic_payload(transport: _CapturedAnthropicTransport, source_bytes: bytes) -> None:
    payload = transport.payload
    assert payload is not None
    assert transport.timeout_seconds is not None
    assert 1 <= transport.timeout_seconds <= 30
    assert transport.headers is not None
    assert transport.headers["x-api-key"] == "test-only-anthropic-key"
    assert payload["model"] == _MODEL_REVISION
    assert "temperature" not in payload
    system = str(payload["system"])
    assert "Interpret headings, tables, and body regions as an architecture report." in system
    assert f"## prompt_version\n{_PROMPT_VERSION}" in system
    assert payload["output_config"] == {"format": {"type": "json_schema", "schema": _OUTPUT_SCHEMA}}
    messages = cast("list[dict[str, object]]", payload["messages"])
    assert [message["role"] for message in messages] == ["user"]
    content = cast("list[dict[str, object]]", messages[0]["content"])
    assert content[0]["type"] == "document"
    source = cast("dict[str, object]", content[0]["source"])
    assert source["type"] == "base64"
    assert source["media_type"] == "application/pdf"
    assert base64.standard_b64decode(str(source["data"])) == source_bytes
    assert "using its visual layout" in str(content[1]["text"])


def _assert_persisted_evidence(engine: TransactionManager, preview_run_id: str) -> None:
    with engine.begin() as transaction:
        connection = cast(Connection, transaction)
        outputs = connection.execute(
            select(db.pipeline_preview_runs.c.outputs).where(db.pipeline_preview_runs.c.id == preview_run_id)
        ).scalar_one()
    rows = cast("list[dict[str, object]]", outputs[0]["items"])
    evidence = cast("dict[str, object]", rows[0]["_pipelineModelEvidence"])
    assert evidence["provider"] == "anthropic"
    assert evidence["resolvedModelId"] == _MODEL_ID
    assert evidence["promptVersionId"] == _PROMPT_VERSION
    trial = cast("dict[str, object]", rows[0]["_pipelineModelTrialEvidence"])
    assert cast("dict[str, object]", trial["noCommit"])["commitForbidden"] is True
    assert cast("dict[str, object]", trial["final"])["status"] == "succeeded"
    assert "test-only-anthropic-key" not in json.dumps(trial)


def _serving_counts(engine: TransactionManager) -> dict[str, int]:
    with engine.begin() as transaction:
        connection = cast(Connection, transaction)
        tables = {
            "datasetVersions": db.dataset_versions,
            "mediaItemVersions": db.media_item_versions,
            "mediaDerivatives": db.media_derivatives,
            "contentUnits": db.content_units,
        }
        return {
            name: int(connection.execute(select(func.count()).select_from(table)).scalar_one())
            for name, table in tables.items()
        }


def _node(
    node_id: str,
    kind: str,
    descriptor_id: str,
    config: dict[str, object],
) -> dict[str, object]:
    return {
        "id": node_id,
        "kind": kind,
        "descriptorId": descriptor_id,
        "specVersion": 1,
        "config": config,
    }


def _edge(
    edge_id: str,
    source: str,
    source_port: str,
    target: str,
    target_port: str,
) -> dict[str, object]:
    return {
        "id": edge_id,
        "sourceNodeId": source,
        "sourcePortId": source_port,
        "targetNodeId": target,
        "targetPortId": target_port,
    }
