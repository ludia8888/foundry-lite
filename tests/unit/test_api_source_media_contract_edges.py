from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from io import BytesIO
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile
from foundry_lite.application.facades.media_workspace import MediaWorkspace
from foundry_lite.application.facades.source_workspace import SourceWorkspace
from foundry_lite.application.ports import SourceConnectionAlreadyExistsError
from foundry_lite.application.ports.media_preview_renderer import PreviewRenderError, PreviewRenderRequest
from foundry_lite.application.services import connector_onboarding_config as connector_config
from foundry_lite.application.services import source_management_config as management_config
from foundry_lite.application.services import source_management_helpers as management_helpers
from foundry_lite.application.services import source_onboarding_config as onboarding_config
from foundry_lite.application.services import source_onboarding_helpers as onboarding_helpers
from foundry_lite.application.services.aip import eval_coercion
from foundry_lite.application.services.aip.eval_types import AiEvalError
from foundry_lite.application.services.source_management_service import SourceManagementService
from foundry_lite.application.services.source_onboarding_config import SourceUpload
from foundry_lite.application.services.source_onboarding_service import SourceOnboardingService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, FoundryLiteError, NotFound, ValidationFailed
from foundry_lite.infrastructure.adapters.local_preview_renderer import LocalPreviewRendererAdapter
from foundry_lite.infrastructure.secrets.local_vault import (
    LocalSecretVaultProvider,
    _redacted_details,
    _safe_metadata,
    _secret_record,
)
from foundry_lite_api import main as api_main
from foundry_lite_api import runtime as api_runtime
from foundry_lite_api.routers import media as media_router
from foundry_lite_api.routers import sources as sources_router
from foundry_lite_worker import outbox_publisher
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.testclient import TestClient


@dataclass(frozen=True)
class _Payload:
    id: str = "payload-1"
    status: str = "ok"
    generation: str = "g1"
    mediaSetId: str = "media-set-1"
    mediaItemVersionId: str = "media-version-1"
    mediaDerivativeId: str = "media-derivative-1"
    mediaProcessingRunId: str = "media-run-1"


@dataclass(frozen=True)
class _EvalPayload:
    eval_run_id: str = "eval-run-1"
    status: str = "passed"


@dataclass(frozen=True)
class _ReleasePayload:
    agent_version_id: str = "agent-v1"
    promoted_channel: str = "prod"


class _ApprovalExecutionError(FoundryLiteError):
    code = "APPROVAL_EXECUTION_ERROR"


@dataclass(frozen=True)
class _Commit:
    dataset_id: str = "dataset-1"
    dataset_ref: str = "raw.orders"
    transaction_id: str = "tx-1"
    version_id: str = "version-1"
    version_number: int = 1
    row_count: int = 1
    manifest_uri: str = "file://manifest.json"
    schema_hash: str = "schema-hash"


class _FakeDatasets:
    def list_quality_contract_checks(self, *_args, **_kwargs):
        return {"datasetRef": "raw.orders", "checks": []}

    def list_quality_results(self, *_args, **_kwargs):
        return {"datasetRef": "raw.orders", "results": []}

    def get_quality_result_summary(self, *_args, **_kwargs):
        return {"datasetRef": "raw.orders", "latestResults": [], "statusCounts": {}, "totalResults": 0}

    def create_quality_contract_check(self, *_args, **_kwargs):
        return {"check": {"id": "check-1"}, "created": True}

    def update_quality_contract_check(self, *_args, **_kwargs):
        return {"id": "check-1", "enabled": False}

    def ingest_webhook_event(self, *_args, **_kwargs):
        return {"transactionId": "tx-webhook"}


class _FakeMedia:
    def create_media_set(self, *_args, **_kwargs):
        return _Payload(id="media-set-1")

    def get_media_set(self, *_args, **_kwargs):
        return _Payload(id="media-set-1")

    def open_transaction(self, *_args, **_kwargs):
        return "media-tx-1"

    def upload(self, *_args, **_kwargs):
        return _Payload(id="upload-1")

    def commit(self, *_args, **_kwargs):
        return _Payload(id="commit-1")

    def process(self, *_args, **_kwargs):
        return _Payload(id="process-1")

    def source_read(self, *_args, **kwargs):
        assert kwargs["should_proxy"] is True
        byte_range = kwargs.get("byte_range")
        version = SimpleNamespace(
            sniffed_mime_type="application/pdf",
            content_hash="sha256:source",
            byte_size=8,
        )
        resolved = SimpleNamespace(version=version)
        grant = SimpleNamespace(url=None, stream=BytesIO(b"%PDFdata"))
        return SimpleNamespace(resolved=resolved, grant=grant, byte_range=byte_range)

    def resolve_derivative(self, *_args, **_kwargs):
        return _Payload(id="derivative-1")

    def list_derivative_content_units(self, *_args, **_kwargs):
        unit = SimpleNamespace(
            content_unit_id="content-unit-1",
            source_media_item_version_id="media-version-1",
            derivative_id="media-derivative-1",
            unit_kind="layout_block",
            ordinal=0,
            text="Heading",
            text_hash="sha256:heading",
            page_number=1,
            start_ms=None,
            end_ms=None,
            bbox={"x": 10, "y": 20, "width": 80, "height": 12},
            parent_content_unit_id=None,
            source_locator={"pageNumber": 1},
            structure={"role": "heading_1", "isHeuristic": True},
            confidence=0.79,
            speaker=None,
            language=None,
            security_envelope={"classification": "internal"},
            embedding=(),
            created_at="2026-07-16T00:00:00Z",
        )
        return SimpleNamespace(
            media_derivative_id="media-derivative-1",
            source_media_item_version_id="media-version-1",
            items=(unit,),
            next_cursor=None,
        )

    def index_derivative(self, *_args, **_kwargs):
        return _Payload(id="index-1")

    def search_content(self, *_args, **_kwargs):
        return [_Payload(id="content-hit-1")]

    def index_visual_derivative(self, *_args, **_kwargs):
        return _Payload(id="visual-index-1")

    def promote_visual_generation(self, *_args, **_kwargs):
        return None

    def search_visual(self, *_args, **_kwargs):
        return [_Payload(id="visual-hit-1")]

    def list_media_runs(self, *_args, **_kwargs):
        return [_Payload(id="run-1")]

    def media_run_detail(self, *_args, **_kwargs):
        return _Payload(id="run-1")

    def bind_reference(self, *_args, **_kwargs):
        return _Payload(id="reference-1")

    def resolve_bound_reference(self, *_args, **_kwargs):
        return _Payload(id="reference-1")


class _FakeSources:
    def list_sources(self, **_kwargs):
        return [{"sourceName": "source-1"}]

    def get_source(self, *_args, **_kwargs):
        return {"sourceName": "webhook-1", "kind": "webhook_listener"}

    def upload_batch_files(self, **_kwargs):
        return {"source": {"sourceName": _kwargs["source_name"]}, "commits": []}

    def create_webhook_listener(self, **_kwargs):
        return {"source": {"sourceName": _kwargs["source_name"], "inboundUrl": _kwargs["inbound_url"]}}

    def ingest_webhook_listener_event(self, *_args, **_kwargs):
        return {"source": {"sourceName": _args[0]}, "commit": {"transactionId": "tx-source-webhook"}}

    def create_debezium_source(self, **_kwargs):
        return {
            "source": {"sourceName": _kwargs["source_name"], "kind": "debezium_cdc"},
            "primaryKey": list(_kwargs.get("primary_key", ())),
        }

    def debezium_operation_plan(self, *_args, **_kwargs):
        return {
            "source": {"sourceName": _args[0], "kind": "debezium_cdc"},
            "readiness": {"status": "ready_for_cdc_workers"},
            "objectTypeApiName": _kwargs.get("object_type_api_name"),
        }

    def start_debezium_sync(self, *_args, **_kwargs):
        return {"source": {"sourceName": _args[0]}, "testResult": {"status": "no_events"}}

    def start_debezium_object_index(self, *_args, **_kwargs):
        return {
            "source": {"sourceName": _args[0]},
            "status": "INDEXED",
            "indexRunId": "index_run_cdc",
            "operationsPath": "/api/operations/runs/index/index_run_cdc",
        }

    def upload_media(self, **_kwargs):
        return {"source": {"sourceName": _kwargs["source_name"]}, "mediaCommit": {"status": "committed"}}


class _FakeAip:
    def run_eval(self, **_kwargs):
        return _EvalPayload()

    def promote_agent_release(self, **_kwargs):
        return _ReleasePayload()

    def execute_approved_action(self, **_kwargs):
        return _Payload(id="approval-execution-1")


class _AiEvalFailureAip:
    def run_eval(self, **_kwargs):
        raise AiEvalError("invalid_min_score", "minimum score is outside the supported range")

    def promote_agent_release(self, **_kwargs):
        raise AiEvalError("eval_run_not_passed", "release promotion requires a passed eval run")


class _FakeSecretValue:
    value = "secret"


class _FakeSecretProvider:
    def get_secret(self, *_args, **_kwargs):
        return _FakeSecretValue()


class _FakeOperations:
    def record_failure_audit(self, **_kwargs):
        return None


class _FailingSources:
    def list_sources(self, **_kwargs):
        raise ValidationFailed("source list failed")


class _RaisingNamespace:
    def __getattr__(self, _name: str):
        return self._raise

    def _raise(self, *_args, **_kwargs):
        raise ValidationFailed("boom")


def _fake_foundry() -> SimpleNamespace:
    return SimpleNamespace(
        aip=_FakeAip(),
        datasets=_FakeDatasets(),
        media=_FakeMedia(),
        operations=_FakeOperations(),
        secret_provider=_FakeSecretProvider(),
        sources=_FakeSources(),
    )


def _request(path: str = "/api/test", headers: dict[str, str] | None = None) -> Request:
    raw_headers = [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()]
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": raw_headers,
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("testclient", 50000),
        "root_path": "",
        "router": api_main.app.router,
    }
    request = Request(scope)
    request.state.request_id = "req-unit"
    return request


def _upload(filename: str, body: bytes, content_type: str = "text/plain") -> UploadFile:
    return UploadFile(file=BytesIO(body), filename=filename, headers=Headers({"content-type": content_type}))


def test_api_media_and_aip_routes_delegate_with_serialized_payloads(monkeypatch) -> None:
    monkeypatch.setattr(api_runtime, "foundry", _fake_foundry())
    request = _request()

    media_set = api_main.create_media_set(
        request,
        api_main.MediaSetCreateRequest(
            namespace="contracts",
            name="source_docs",
            schemaType="document",
            primaryFormat="txt",
            allowedInputFormats=["txt"],
            classification="internal",
        ),
    )
    assert media_set["id"] == "media-set-1"
    assert api_main.get_media_set(request, "media-set-1")["mediaSetId"] == "media-set-1"
    assert api_main.open_media_transaction(
        request, "media-set-1", api_main.MediaOpenTransactionRequest(), idempotency_key="media-open-1"
    ) == {"mediaTransactionId": "media-tx-1"}
    staged = api_main.upload_media_file(
        request,
        "media-set-1",
        "media-tx-1",
        logical_path="doc.txt",
        schema_type="document",
        format="txt",
        file=_upload("doc.txt", b"hello"),
        security_envelope='{"classification":"internal"}',
        probe_metadata='{"bytes":5}',
    )
    assert staged["id"] == "upload-1"
    assert api_main.commit_media_transaction(request, "media-tx-1")["id"] == "commit-1"
    assert (
        api_main.process_media_version(
            request,
            "media-version-1",
            api_main.MediaProcessRequest(processor="ocr", processorVersion="v1"),
        )["id"]
        == "process-1"
    )
    source_response = api_main.read_media_version_content(
        request,
        "media-version-1",
        range_header="bytes=0-3",
    )
    assert source_response.status_code == 206
    assert source_response.headers["content-range"] == "bytes 0-3/8"
    assert source_response.headers["content-length"] == "4"
    assert api_main.get_media_derivative(request, "media-derivative-1")["id"] == "derivative-1"
    content_units = api_main.list_media_derivative_content_units(request, "media-derivative-1")
    assert content_units["items"][0]["structure"] == {"role": "heading_1", "isHeuristic": True}
    assert content_units["items"][0]["bbox"] == {"x": 10, "y": 20, "width": 80, "height": 12}
    assert (
        api_main.index_media_derivative(
            request, "media-derivative-1", api_main.MediaIndexDerivativeRequest(generation="g1")
        )["id"]
        == "index-1"
    )
    assert (
        api_main.search_media_content(request, api_main.MediaSearchRequest(text="invoice"))[0]["id"] == "content-hit-1"
    )
    assert (
        api_main.index_media_visual_derivative(
            request, "media-derivative-1", api_main.MediaIndexDerivativeRequest(generation="g1")
        )["id"]
        == "visual-index-1"
    )
    assert api_main.promote_media_visual_generation(
        request, api_main.MediaVisualPromoteRequest(expectedActive="g0", generation="g1")
    ) == {"status": "ok", "generation": "g1"}
    assert (
        api_main.search_media_visual(request, api_main.MediaVisualSearchRequest(text="chart"))[0]["id"]
        == "visual-hit-1"
    )
    assert api_main.list_media_processing_runs(request)[0]["id"] == "run-1"
    assert api_main.get_media_processing_run(request, "run-1")["id"] == "run-1"
    assert (
        api_main.bind_media_reference(
            request,
            api_main.MediaBindReferenceRequest(
                holderType="Object",
                holderId="O-1",
                propertyName="sourceDocument",
                mediaItemVersionId="media-version-1",
            ),
            idempotency_key="bind-1",
        )["id"]
        == "reference-1"
    )
    assert (
        api_main.resolve_media_reference(
            request,
            holder_type="Object",
            holder_id="O-1",
            property_name="sourceDocument",
            allowed_classifications=["internal"],
        )["id"]
        == "reference-1"
    )

    eval_result = api_main.run_aip_eval(
        request,
        api_main.AipEvalRunRequest(
            evalRunId="eval-run-1",
            suiteApiName="release",
            suiteVersion="v1",
            agentVersionId="agent-v1",
            candidateReleaseChannel="staging",
            cases=[
                api_main.AipEvalCaseRequest(
                    caseApiName="case-1",
                    axis="accuracy",
                    expectedJson={"answer": "yes"},
                    actualJson={"answer": "yes"},
                )
            ],
        ),
    )
    assert eval_result["eval_run_id"] == "eval-run-1"
    release = api_main.promote_aip_release(
        request,
        api_main.AipReleasePromotionRequest(
            agentVersionId="agent-v1", targetReleaseChannel="prod", evalRunId="eval-run-1"
        ),
    )
    assert release["promoted_channel"] == "prod"

    monkeypatch.setattr(api_runtime, "foundry", SimpleNamespace(aip=_AiEvalFailureAip()))
    with pytest.raises(HTTPException) as eval_error:
        api_main.run_aip_eval(
            request,
            api_main.AipEvalRunRequest(
                evalRunId="eval-run-2",
                suiteApiName="release",
                suiteVersion="v1",
                agentVersionId="agent-v1",
                candidateReleaseChannel="staging",
                cases=[
                    api_main.AipEvalCaseRequest(
                        caseApiName="case-1",
                        axis="accuracy",
                        expectedJson={"answer": "yes"},
                        actualJson={"answer": "yes"},
                    )
                ],
            ),
        )
    assert eval_error.value.detail["details"]["reason"] == "invalid_min_score"
    with pytest.raises(HTTPException) as release_error:
        api_main.promote_aip_release(
            request,
            api_main.AipReleasePromotionRequest(
                agentVersionId="agent-v1", targetReleaseChannel="prod", evalRunId="eval-run-2"
            ),
        )
    assert release_error.value.detail["details"]["reason"] == "eval_run_not_passed"


def test_upload_media_file_rejects_oversized_upload_before_staging(monkeypatch) -> None:
    # An upload larger than the configured cap must be rejected before it is staged: the
    # media workspace upload is never invoked.
    fake = _fake_foundry()
    monkeypatch.setattr(api_runtime, "foundry", fake)
    monkeypatch.setattr(media_router, "max_media_upload_bytes", lambda: 4)
    request = _request()

    with pytest.raises(HTTPException) as oversized:
        api_main.upload_media_file(
            request,
            "media-set-1",
            "media-tx-1",
            logical_path="doc.txt",
            schema_type="document",
            format="txt",
            file=_upload("doc.txt", b"way past the four byte cap"),
            security_envelope='{"classification":"internal"}',
        )
    assert oversized.value.status_code == 400
    assert oversized.value.detail["details"]["max_bytes"] == 4


def test_api_source_upload_and_webhook_routes_delegate_to_source_workspace(monkeypatch) -> None:
    monkeypatch.setattr(api_runtime, "foundry", _fake_foundry())
    request = _request()
    manifest = json.dumps(
        {
            "sourceName": "batch_upload",
            "displayName": "Batch Upload",
            "files": [{"fileName": "orders.csv", "datasetRef": "raw.orders"}],
        }
    )

    batch = api_main.upload_batch_file_source(
        request,
        manifest=manifest,
        files=[_upload("orders.csv", b"order_id\nO-1\n", "text/csv")],
        idempotency_key="batch-1",
    )
    assert batch["source"]["sourceName"] == "batch_upload"
    webhook = api_main.create_webhook_listener_source(
        request,
        api_main.SourceWebhookListenerCreateRequest(
            sourceName="webhook_orders",
            displayName="Webhook Orders",
            datasetRef="raw.orders",
            connectorName="mock",
            resourceName="orders",
            signingSecretRef="webhook_signing_key",
        ),
        idempotency_key="webhook-1",
    )
    assert webhook["source"]["inboundUrl"].endswith("/api/sources/webhook-listeners/webhook_orders/events")
    assert api_main.get_webhook_listener_source(request, "webhook_orders")["kind"] == "webhook_listener"
    debezium = api_main.create_debezium_source(
        request,
        api_main.SourceDebeziumCreateRequest(
            sourceName="cdc_orders",
            displayName="CDC Orders",
            datasetRef="raw.orders",
            streamName="orders_stream",
            topic="orders",
            primaryKey=["order_id"],
        ),
        idempotency_key="cdc-1",
    )
    assert debezium["source"]["kind"] == "debezium_cdc"
    assert debezium["primaryKey"] == ["order_id"]
    plan = api_main.get_debezium_operation_plan(request, "cdc_orders", object_type_api_name="Order")
    assert plan["readiness"]["status"] == "ready_for_cdc_workers"
    sync = api_main.start_debezium_source_sync(
        request,
        "cdc_orders",
        api_main.SourceDebeziumSyncStartRequest(expectedConfigFingerprint="sha256:abc", afterOffset=10, limit=2),
        idempotency_key="cdc-sync-1",
    )
    assert sync["testResult"]["status"] == "no_events"
    indexed = api_main.start_debezium_source_object_index(
        request,
        "cdc_orders",
        api_main.SourceDebeziumObjectIndexStartRequest(
            objectTypeApiName="Order",
            expectedConfigFingerprint="sha256:abc",
            maxRowsPerVersion=25,
        ),
        idempotency_key="cdc-index-1",
    )
    assert indexed["status"] == "INDEXED"
    assert indexed["operationsPath"] == "/api/operations/runs/index/index_run_cdc"
    media = api_main.upload_media_source(
        request,
        source_name="source_media",
        display_name="Source Media",
        media_set_id="media-set-1",
        logical_path="doc.txt",
        schema_type="document",
        format="txt",
        file=_upload("doc.txt", b"hello"),
        security_envelope='{"classification":"internal"}',
        probe_metadata='{"bytes":5}',
        idempotency_key="source-media-1",
    )
    assert media["mediaCommit"]["status"] == "committed"

    response = TestClient(api_main.app).post(
        "/api/sources/webhook-listeners/webhook_orders/events",
        headers={
            "X-Foundry-Lite-Signature": "sig",
            "X-Foundry-Lite-Timestamp": "2026-06-29T00:00:00Z",
            "X-Foundry-Lite-Event-ID": "event-1",
            api_main.WEBHOOK_SERVICE_PRINCIPAL_HEADER: "connector",
            api_main.WEBHOOK_SERVICE_TENANT_HEADER: "tenant-demo",
        },
        json={"orderId": "O-1"},
    )
    assert response.status_code == 200
    assert response.json()["commit"]["transactionId"] == "tx-source-webhook"


def test_api_dataset_quality_and_error_edges(monkeypatch) -> None:
    monkeypatch.setattr(api_runtime, "foundry", _fake_foundry())
    request = _request()

    assert api_main.list_dataset_quality_contract_checks(request, "raw", "orders")["checks"] == []
    assert api_main.list_dataset_quality_results(request, "raw", "orders", limit=3)["results"] == []
    assert api_main.get_dataset_quality_result_summary(request, "raw", "orders", latest_limit=2)["totalResults"] == 0
    created = api_main.create_dataset_quality_contract_check(
        request,
        "raw",
        "orders",
        api_main.DatasetQualityContractCheckCreateRequest(config={"type": "not_null"}, severity="error"),
    )
    assert created["check"]["id"] == "check-1"
    assert (
        api_main.update_dataset_quality_contract_check(
            request,
            "raw",
            "orders",
            "check-1",
            api_main.DatasetQualityContractCheckUpdateRequest(enabled=False),
        )["enabled"]
        is False
    )

    monkeypatch.setattr(api_runtime, "foundry", SimpleNamespace(sources=_FailingSources()))
    with pytest.raises(HTTPException) as exc_info:
        api_main.list_sources(request)
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["request_id"] == "req-unit"

    approval_error = _ApprovalExecutionError("approval failed", details={"reason": "policy_denied"})
    handled = api_main._handle_error(approval_error, request)
    assert handled.status_code == 403
    assert handled.detail["code"] == "POLICY_DENIED"
    assert (
        api_main._status_for_error(
            _ApprovalExecutionError("approval failed", details={"reason": "fingerprint_mismatch"}), 400
        )
        == 409
    )
    assert (
        api_main._status_for_error(_ApprovalExecutionError("approval failed", details={"reason": "run_not_found"}), 400)
        == 404
    )
    assert (
        api_main._status_for_error(_ApprovalExecutionError("approval failed", details={"reason": "unexpected"}), 418)
        == 418
    )
    assert api_main._status_for_error(_ApprovalExecutionError("approval failed", details={}), 400) == 400
    assert (
        api_main._code_for_error(_ApprovalExecutionError("approval failed", details={})) == "APPROVAL_EXECUTION_ERROR"
    )
    assert api_main._metrics_route_path(_request()) == "__unmatched__"


def test_api_form_and_webhook_helper_validation_edges() -> None:
    assert api_main._json_form_object('{"a":1}', "securityEnvelope") == {"a": 1}
    assert api_main._optional_json_form_object("  ", "probeMetadata") is None
    assert api_main._json_form_string_list('["id",""]', "primaryKey") == ["id"]
    with pytest.raises(ValidationFailed):
        api_main._json_form_object("[1]", "securityEnvelope")
    with pytest.raises(ValidationFailed):
        api_main._json_form_object("{", "securityEnvelope")
    with pytest.raises(ValidationFailed):
        api_main._json_form_string_list('["id", 1]', "primaryKey")
    with pytest.raises(ValidationFailed):
        api_main._json_form_string_list("{", "primaryKey")

    assert api_main._request_content_length(_request(headers={})) is None
    with pytest.raises(ValidationFailed):
        api_main._request_content_length(_request(headers={"content-length": "nan"}))
    with pytest.raises(ValidationFailed):
        api_main._request_content_length(_request(headers={"content-length": "-1"}))
    with pytest.raises(ValidationFailed):
        api_main._webhook_identity_header("bad principal", api_main.WEBHOOK_SERVICE_PRINCIPAL_HEADER)
    with pytest.raises(ValidationFailed):
        api_main._webhook_request_context(
            _request(),
            service_principal="connector",
            service_tenant_id=None,
        )


def test_source_config_validation_rejects_unsafe_contract_inputs() -> None:
    ctx = RequestContext(tenant_id="tenant-demo", actor_user_id="user-demo", request_id="req-config")
    sync_kwargs = {
        "sync_name": "orders_sync",
        "source_name": "orders_source",
        "display_name": "Orders Sync",
        "source_type": "rest_api",
        "capability": "batch",
        "target_dataset_ref": "raw.orders",
        "target_media_set_id": None,
        "mode": "APPEND",
        "schedule": {},
        "config_summary": {"connectorName": "erp"},
    }

    with pytest.raises(ValidationFailed):
        onboarding_config.require_identifier("bad-name", "sourceName")
    with pytest.raises(ValidationFailed):
        onboarding_config.require_text(" ", "displayName")
    with pytest.raises(ValidationFailed):
        onboarding_config.require_idempotency_key(" ")
    with pytest.raises(ValidationFailed):
        onboarding_config.reject_raw_secret_payload({"nested": [{"apiKey": "raw-secret"}]})
    with pytest.raises(ValidationFailed):
        onboarding_config.reject_raw_secret_payload({"nested": [[{"apiKey": "raw-secret"}]]})

    with pytest.raises(ValidationFailed):
        management_config.require_idempotency_key("")
    with pytest.raises(ValidationFailed):
        management_config.agent_record(
            ctx,
            agent_id="agent_1",
            display_name="Agent",
            mode="side_door",
            capabilities={},
            network_summary={},
        )
    with pytest.raises(ValidationFailed):
        management_config.network_policy_record(
            ctx,
            policy_name="policy",
            display_name="Policy",
            mode="side_door",
            agent_id=None,
            allowed_hosts=[],
        )
    with pytest.raises(ValidationFailed):
        management_config.network_policy_record(
            ctx,
            policy_name="policy",
            display_name="Policy",
            mode="agent_proxy",
            agent_id=None,
            allowed_hosts=[],
        )
    with pytest.raises(ValidationFailed):
        management_config.source_sync_record(ctx, **{**sync_kwargs, "target_dataset_ref": None})
    with pytest.raises(ValidationFailed):
        management_config.source_sync_record(ctx, **{**sync_kwargs, "source_type": "unsupported"})
    with pytest.raises(ValidationFailed):
        management_config.source_sync_record(ctx, **{**sync_kwargs, "capability": "unsupported"})
    with pytest.raises(ValidationFailed):
        management_config.source_sync_record(ctx, **{**sync_kwargs, "mode": "unsupported"})


def test_connector_and_eval_config_helpers_reject_invalid_edges() -> None:
    ctx = RequestContext(tenant_id="tenant-demo", actor_user_id="user-demo", request_id="req-connector")

    connection = connector_config._connection_record(
        ctx,
        connector_name="erp",
        display_name="ERP",
        base_url="https://api.example.test",
        auth={"mode": "bearer", "tokenSecretRef": "secretRef:token"},
        rate_limit_per_minute=60,
        allow_private_network=False,
    )
    resource = connector_config._resource_record(
        ctx,
        connector_name="erp",
        resource_name="orders",
        dataset_ref="raw.orders",
        resource_path="/orders",
        pagination={"itemsPath": "data"},
        schema_columns=("order_id",),
        primary_key=("order_id",),
    )
    assert connection.auth["mode"] == "bearer"
    assert resource.pagination["itemsPath"] == "data"
    assert (
        connector_config._connection_patch(
            _connector_connection_row("erp"),
            display_name="ERP Updated",
            base_url=None,
            auth=None,
            rate_limit_per_minute=None,
            allow_private_network=None,
            status="disabled",
        ).status
        == "disabled"
    )
    assert (
        connector_config._auth_payload(
            {"mode": "header", "headerName": "X-Api-Key", "headerValueSecretRef": "secretRef:key"}
        )["mode"]
        == "header"
    )
    assert (
        connector_config._rest_auth_config(
            {"mode": "header", "headerName": "X-Api-Key", "headerValueSecretRef": "secretRef:key"}
        ).header_name
        == "X-Api-Key"
    )
    assert connector_config._activity_connector_input(
        {"connectorName": "erp", "resourceName": "orders", "configFingerprint": "sha256:abc"}
    ) == ("erp", "orders", "sha256:abc")
    assert connector_config._optional_text("") is None
    assert eval_coercion.json_object("not-a-map") == {}

    with pytest.raises(AiEvalError):
        eval_coercion.number_field({"score": "bad"}, "score")
    with pytest.raises(ValidationFailed):
        connector_config._connection_config(
            "bad-name", "ERP", "https://api.example.test", {"mode": "none"}, 1, False, "active"
        )
    with pytest.raises(ValidationFailed):
        connector_config._connection_config(
            "erp", " ", "https://api.example.test", {"mode": "none"}, 1, False, "active"
        )
    with pytest.raises(ValidationFailed):
        connector_config._connection_config(
            "erp", "ERP", "https://api.example.test", {"mode": "none"}, -1, False, "active"
        )
    with pytest.raises(ValidationFailed):
        connector_config._connection_config("erp", "ERP", "https://api.example.test", {"mode": "none"}, 1, False, "bad")
    with pytest.raises(ValidationFailed):
        connector_config._auth_payload({"mode": "unsupported"})
    with pytest.raises(ValidationFailed):
        connector_config._auth_payload({"mode": "none", "token": "raw"})
    with pytest.raises(ValidationFailed):
        connector_config._auth_payload({"mode": "bearer"})
    with pytest.raises(ValidationFailed):
        connector_config._pagination_payload({"strategy": "offset"})
    with pytest.raises(ValidationFailed):
        connector_config._resource_config("erp", "bad-name", "raw.orders", "/orders", {}, ("id",), ("id",))
    with pytest.raises(ValidationFailed):
        connector_config._resource_config("erp", "orders", "raw.orders", " ", {}, ("id",), ("id",))
    with pytest.raises(ValidationFailed):
        connector_config._resource_config("erp", "orders", "raw.orders", "/orders", {}, ("",), ("id",))
    with pytest.raises(ValidationFailed):
        connector_config._activity_connector_input({"connectorName": "erp"})


def test_source_onboarding_helpers_validate_source_shapes() -> None:
    ctx = RequestContext(tenant_id="tenant-demo", actor_user_id="user-demo", request_id="req-source")
    webhook = onboarding_helpers.webhook_record(
        ctx,
        source_name="webhook_orders",
        display_name="Webhook Orders",
        dataset_ref="raw.orders",
        connector_name="mock",
        resource_name="orders",
        signing_secret_ref="secretRef:webhook",
        inbound_url="https://example.test/webhook",
    )
    assert webhook.kind == "webhook_listener"
    assert "X-Foundry-Lite-Signature" in webhook.config_summary["requiredHeaders"]
    debezium = onboarding_helpers.debezium_record(
        ctx,
        source_name="cdc_orders",
        display_name="CDC Orders",
        dataset_ref="raw.orders",
        stream_name="orders_stream",
        topic="orders",
        consumer_group="cg",
        secret_refs={"passwordRef": "secretRef:db"},
        primary_key=["order_id"],
    )
    assert debezium.kind == "debezium_cdc"
    assert debezium.config_summary["primaryKey"] == ["order_id"]
    config = onboarding_helpers.stream_config(debezium.config_summary, None)
    assert config.stream_name == "orders_stream"
    assert config.limit == 100
    assert onboarding_helpers.first_run_id([]) is None
    assert onboarding_helpers.first_run_id([{"runId": "run-1"}]) == "run-1"
    assert onboarding_helpers.commit_list({"commits": [{"runId": "run-1"}]}) == [{"runId": "run-1"}]
    assert onboarding_helpers.commit_list({"commits": "not-a-list"}) == []

    row = {
        "source_name": "cdc_orders",
        "kind": "debezium_cdc",
        "config_fingerprint": "sha256:old",
    }
    onboarding_helpers.require_kind(row, "debezium_cdc")
    onboarding_helpers.require_source_fingerprint(row, None)
    with pytest.raises(ValidationFailed):
        onboarding_helpers.summary_text({}, "topic")
    with pytest.raises(ValidationFailed):
        onboarding_helpers.require_kind(row, "webhook_listener")
    with pytest.raises(ConflictDetected):
        onboarding_helpers.require_source_fingerprint(row, "sha256:new")
    with pytest.raises(ConflictDetected):
        onboarding_helpers.require_same_config(row, webhook)
    source_registry = _SourceRegistry()
    source_registry.create_source(transaction=object(), record=webhook)
    optional_commit = onboarding_helpers.record_optional_commit(
        _Engine(),
        source_registry,
        _DatasetTransactions(),
        ctx,
        "webhook_orders",
        _Commit(dataset_ref="raw.orders", transaction_id="tx-webhook-optional"),
    )
    assert optional_commit["commitResult"]["transactionId"] == "tx-webhook-optional"
    empty_sources = _SourceRegistry()
    with pytest.raises(NotFound):
        onboarding_helpers.update_source_commit(_Engine(), empty_sources, ctx, "missing", None, {})
    with pytest.raises(NotFound):
        onboarding_helpers.source_row(_Engine(), empty_sources, ctx, "missing")
    with pytest.raises(NotFound):
        onboarding_helpers.rest_connection(_Engine(), _ConnectorRegistry(), ctx, "missing")
    onboarding_helpers.audit_source_created(_RuntimeAudit(), object(), ctx, _source_row_from_record(webhook))


def test_source_management_helpers_parse_and_validate_contract_payloads() -> None:
    bearer = management_helpers.rest_auth({"mode": "bearer", "tokenSecretRef": "secretRef:token"})
    header = management_helpers.rest_auth(
        {"mode": "header", "headerName": "X-Api-Key", "headerValueSecretRef": "secretRef:key"}
    )
    config = management_helpers.rest_source_config(
        {
            "baseUrl": "https://api.example.test",
            "resourcePath": "/orders",
            "auth": {"mode": "none"},
            "pagination": {"itemsPath": "data", "nextCursorPath": "next"},
            "allowPrivateNetwork": True,
        }
    )
    assert bearer.mode == "bearer"
    assert header.header_name == "X-Api-Key"
    assert config.pagination.items_path == "data"
    assert config.allow_private_network is True
    assert management_helpers.int_value(None, 20) == 20
    assert management_helpers.int_value(3, 20) == 3
    assert management_helpers.int_value("4", 20) == 4
    assert management_helpers.fieldnames([], {"columns": [{"name": "order_id"}]}) == ["order_id"]
    assert management_helpers.fieldnames([], {"columns": [{"title": "Order ID"}]}) == []
    assert management_helpers.fieldnames([{"order_id": "O-1"}], {}) == ["order_id"]
    assert management_helpers.mapping("not-a-map") == {}
    assert management_helpers.optional_text("") is None
    assert management_helpers.text({}, "missing", "fallback") == "fallback"
    assert management_helpers.secret_version("secret").startswith("sha256:")
    assert management_helpers.commit_result_payload(None, SimpleNamespace(checkpoint={"cursor": "c1"})) == {
        "rowCount": 0,
        "checkpoint": {"cursor": "c1"},
        "networkEvidence": {},
    }

    commit = SimpleNamespace(version_id="v1", row_count=2, schema_hash="schema", manifest_uri="manifest.json")
    assert management_helpers.commit_result_payload(commit, SimpleNamespace(checkpoint={}))["datasetVersionId"] == "v1"
    assert (
        management_helpers.require_agent_exists_for_proxy(SimpleNamespace(), object(), RequestContext(), "direct", None)
        is None
    )
    empty_store = SimpleNamespace(source_management_repository=_EmptySourceManagementRepository())
    ctx = RequestContext(tenant_id="tenant-demo", actor_user_id="user-demo", request_id="req-management")
    with pytest.raises(ConflictDetected):
        management_helpers.require_same_fingerprint({"config_fingerprint": "sha256:old"}, "sha256:new")
    with pytest.raises(NotFound):
        management_helpers.credential_row(empty_store, object(), ctx, "missing")
    with pytest.raises(NotFound):
        management_helpers.agent_row(empty_store, object(), ctx, "missing")
    with pytest.raises(NotFound):
        management_helpers.network_policy_row(empty_store, object(), ctx, "missing")
    with pytest.raises(NotFound):
        management_helpers.sync_row(empty_store, object(), ctx, "missing")
    with pytest.raises(NotFound):
        management_helpers.run_row(empty_store, object(), ctx, "missing")
    with pytest.raises(ValidationFailed):
        management_helpers.int_value(object(), 20)
    with pytest.raises(ValidationFailed):
        management_helpers.required_text({}, "baseUrl")
    with pytest.raises(ValidationFailed):
        management_helpers.target_dataset_ref({})


def test_source_management_helpers_reuse_existing_records_without_duplicate_writes() -> None:
    ctx = RequestContext(tenant_id="tenant-demo", actor_user_id="user-demo", request_id="req-management-reuse")
    repo = _ExistingSourceManagementRepository()
    store = SimpleNamespace(engine=_Engine(), source_management_repository=repo, secret_vault=_SecretVault())

    assert (
        management_helpers.create_credential_row(
            store,
            _RuntimeAudit(),
            ctx,
            "db",
            "db",
            "secret",
            SimpleNamespace(config_fingerprint="sha256:cred"),
        )["credential_name"]
        == "db"
    )
    assert (
        management_helpers.create_network_policy_row(
            store,
            _RuntimeAudit(),
            ctx,
            "direct",
            "direct",
            None,
            SimpleNamespace(config_fingerprint="sha256:policy"),
        )["policy_name"]
        == "direct"
    )
    assert (
        management_helpers.create_sync_row(
            store,
            _RuntimeAudit(),
            ctx,
            "rest_sync",
            SimpleNamespace(config_fingerprint="sha256:rest_sync"),
        )["sync_name"]
        == "rest_sync"
    )
    assert repo.write_count == 0


def test_api_remaining_error_edges_and_approval_execution(monkeypatch) -> None:
    request = _request()
    raising_foundry = SimpleNamespace(
        actions=_RaisingNamespace(),
        aip=_RaisingNamespace(),
        connectors=_RaisingNamespace(),
        datasets=_RaisingNamespace(),
        media=_RaisingNamespace(),
        operations=_RaisingNamespace(),
        sources=_RaisingNamespace(),
        transforms=_RaisingNamespace(),
    )
    monkeypatch.setattr(api_runtime, "foundry", raising_foundry)

    invocations = [
        lambda: api_main.list_dataset_quality_contract_checks(request, "raw", "orders"),
        lambda: api_main.list_dataset_quality_results(request, "raw", "orders"),
        lambda: api_main.get_dataset_quality_result_summary(request, "raw", "orders"),
        lambda: api_main.create_dataset_quality_contract_check(
            request,
            "raw",
            "orders",
            api_main.DatasetQualityContractCheckCreateRequest(config={"type": "not_null"}),
        ),
        lambda: api_main.update_dataset_quality_contract_check(
            request,
            "raw",
            "orders",
            "check-1",
            api_main.DatasetQualityContractCheckUpdateRequest(enabled=True),
        ),
        lambda: api_main.run_aip_eval(
            request,
            api_main.AipEvalRunRequest(
                evalRunId="eval-run-1",
                suiteApiName="release",
                suiteVersion="v1",
                agentVersionId="agent-v1",
                candidateReleaseChannel="staging",
                cases=[
                    api_main.AipEvalCaseRequest(
                        caseApiName="case-1",
                        axis="accuracy",
                        expectedJson={"answer": "yes"},
                        actualJson={"answer": "yes"},
                    )
                ],
            ),
        ),
        lambda: api_main.promote_aip_release(
            request,
            api_main.AipReleasePromotionRequest(
                agentVersionId="agent-v1", targetReleaseChannel="prod", evalRunId="eval-run-1"
            ),
        ),
        lambda: api_main.create_media_set(
            request,
            api_main.MediaSetCreateRequest(
                namespace="contracts",
                name="source_docs",
                schemaType="document",
                primaryFormat="txt",
                allowedInputFormats=["txt"],
                classification="internal",
            ),
        ),
        lambda: api_main.get_media_set(request, "media-set-1"),
        lambda: api_main.open_media_transaction(
            request, "media-set-1", api_main.MediaOpenTransactionRequest(), idempotency_key="open-1"
        ),
        lambda: api_main.commit_media_transaction(request, "media-tx-1"),
        lambda: api_main.process_media_version(
            request, "media-version-1", api_main.MediaProcessRequest(processor="ocr", processorVersion="v1")
        ),
        lambda: api_main.search_media_content(request, api_main.MediaSearchRequest(text="invoice")),
        lambda: api_main.list_source_templates(request),
        lambda: api_main.create_source_credential(
            request,
            api_main.SourceCredentialCreateRequest(
                credentialName="cred",
                displayName="Credential",
                kind="database",
                authScheme="password",
                secretValue="secret",
            ),
            idempotency_key="cred-1",
        ),
        lambda: api_main.list_source_credentials(request),
        lambda: api_main.get_source_credential(request, "cred"),
        lambda: api_main.register_source_agent(
            request,
            api_main.SourceAgentRegisterRequest(agentId="agent", displayName="Agent", mode="proxy"),
            idempotency_key="agent-1",
        ),
        lambda: api_main.list_source_agents(request),
        lambda: api_main.heartbeat_source_agent(request, "agent"),
        lambda: api_main.create_source_network_policy(
            request,
            api_main.SourceNetworkPolicyCreateRequest(policyName="policy", displayName="Policy", mode="direct"),
            idempotency_key="policy-1",
        ),
        lambda: api_main.list_source_network_policies(request),
        lambda: api_main.explore_source(
            request, api_main.SourceExploreRequest(sourceName="rest", sourceType="rest_api", request={})
        ),
        lambda: api_main.create_source_managed_sync(
            request,
            api_main.SourceManagedSyncCreateRequest(
                syncName="sync",
                sourceName="rest",
                displayName="Sync",
                sourceType="rest_api",
                capability="snapshot",
            ),
            idempotency_key="sync-1",
        ),
        lambda: api_main.list_source_managed_syncs(request),
        lambda: api_main.get_source_managed_sync(request, "sync"),
        lambda: api_main.update_source_managed_sync_schedule(
            request,
            "sync",
            api_main.SourceManagedSyncScheduleUpdateRequest(
                schedule={"mode": "manual"}, expectedConfigFingerprint="sha256:sync"
            ),
            idempotency_key="sync-schedule-1",
        ),
        lambda: api_main.start_source_managed_sync_run(
            request,
            "sync",
            api_main.SourceManagedSyncRunStartRequest(),
            idempotency_key="run-1",
        ),
        lambda: api_main.pause_source_managed_sync_schedule(
            request,
            "sync",
            sources_router.SourceManagedSyncScheduleStateRequest(expectedConfigFingerprint="sha256:sync"),
            idempotency_key="pause-1",
        ),
        lambda: api_main.resume_source_managed_sync_schedule(
            request,
            "sync",
            sources_router.SourceManagedSyncScheduleStateRequest(expectedConfigFingerprint="sha256:sync"),
            idempotency_key="resume-1",
        ),
        lambda: sources_router.start_source_managed_streaming_sync(
            request,
            "sync",
            sources_router.SourceManagedStreamingSyncStateRequest(expectedConfigFingerprint="sha256:sync"),
            idempotency_key="stream-start-1",
        ),
        lambda: sources_router.stop_source_managed_streaming_sync(
            request,
            "sync",
            sources_router.SourceManagedStreamingSyncStateRequest(expectedConfigFingerprint="sha256:sync"),
            idempotency_key="stream-stop-1",
        ),
        lambda: sources_router.get_source_managed_streaming_sync_status(request, "sync"),
        lambda: api_main.list_source_managed_sync_runs(request, "sync"),
        lambda: api_main.get_source_managed_sync_run(request, "run-1"),
        lambda: api_main.get_source(request, "source-1"),
        lambda: api_main.create_connector_connection(
            request,
            api_main.RestConnectorConnectionCreateRequest(
                connectorName="erp", displayName="ERP", baseUrl="https://api.example.test"
            ),
            idempotency_key="connector-1",
        ),
        lambda: api_main.list_connector_connections(request),
        lambda: api_main.get_connector_connection(request, "erp"),
        lambda: api_main.update_connector_connection(
            request,
            "erp",
            api_main.RestConnectorConnectionUpdateRequest(displayName="ERP Updated"),
            idempotency_key="connector-update-1",
        ),
        lambda: api_main.upsert_connector_resource(
            request,
            "erp",
            "orders",
            api_main.RestConnectorResourceUpsertRequest(datasetRef="raw.orders", resourcePath="/orders"),
            idempotency_key="resource-1",
        ),
        lambda: api_main.test_connector_resource(request, "erp", "orders"),
        lambda: api_main.start_connector_resource_sync(
            request,
            "erp",
            "orders",
            api_main.ConnectorResourceSyncStartRequest(syncName="sync"),
            idempotency_key="resource-sync-1",
        ),
        lambda: api_main.list_action_writeback_reconciliation_queue(request),
        lambda: api_main.resolve_action_writeback_reconciliation(
            request,
            "writeback-1",
            api_main.ActionWritebackReconciliationRequest(remoteStatus="succeeded", remoteResourceId="remote-1"),
        ),
        lambda: api_main.register_sql_transform(
            request,
            api_main.TransformSqlRegisterRequest(
                apiName="clean_orders",
                sql="select 1",
                inputs={"raw": "raw.orders"},
                outputDatasetRef="clean.orders",
            ),
        ),
        lambda: api_main.run_transform(request, "clean_orders"),
    ]
    for invoke in invocations:
        with pytest.raises(HTTPException) as exc_info:
            invoke()
        assert exc_info.value.detail["code"] == "VALIDATION_FAILED"

    monkeypatch.setattr(api_runtime, "foundry", _fake_foundry())
    with pytest.raises(HTTPException) as missing_key:
        api_main.execute_approved_action(
            request,
            "review-1",
            api_main.ApprovalExecutionRequest(expectedProposalFingerprint="sha256:proposal"),
            idempotency_key="",
        )
    assert missing_key.value.status_code == 400
    executed = api_main.execute_approved_action(
        request,
        "review-1",
        api_main.ApprovalExecutionRequest(expectedProposalFingerprint="sha256:proposal"),
        idempotency_key="execute-1",
    )
    assert executed["id"] == "approval-execution-1"


def test_media_and_source_facades_delegate_remaining_edges() -> None:
    ctx = RequestContext()
    media_calls: list[str] = []

    class MediaComponent:
        def __getattr__(self, name: str):
            def call(*_args, **_kwargs):
                media_calls.append(name)
                if name.startswith("sweep") or name == "purge_access_caches":
                    return ["removed"]
                if name.startswith("mark"):
                    return 1
                return _Payload(id=name)

            return call

    class MediaSearchAccess:
        """Classification filter that passes hits through.

        The facade funnels both content and visual search through ``filter_hits``; returning
        the hits unchanged keeps the delegated payload observable so this test still proves
        which component produced it, while recording that the filter was applied.
        """

        def filter_hits(self, _ctx: object, hits: object) -> object:
            media_calls.append("filter_hits")
            return hits

    media = MediaWorkspace(
        SimpleNamespace(
            access_pattern=MediaComponent(),
            binding=MediaComponent(),
            catalog=MediaComponent(),
            indexing=MediaComponent(),
            processing=MediaComponent(),
            reference=MediaComponent(),
            retention=MediaComponent(),
            retrieval=MediaComponent(),
            search_access=MediaSearchAccess(),
            transaction=MediaComponent(),
            upload=MediaComponent(),
            virtual_sets=MediaComponent(),
            visual_search=MediaComponent(),
        )
    )
    assert media.get_media_set(ctx, media_set_id="media-set-1").id == "get_media_set"
    media.abort(ctx, media_transaction_id="tx-1", error={"reason": "test"})
    assert media.sweep_orphans(ctx, older_than="2026-01-01") == ["removed"]
    assert media.sweep_orphan_derivatives(ctx, older_than="2026-01-01") == ["removed"]
    assert media.media_run_detail(ctx, media_processing_run_id="run-1").id == "media_run_detail"
    assert media.rebuild_content_index(ctx, generation="g1", source_media_item_version_ids=["v1"]).id == "rebuild"
    assert media.search_content(ctx, query=SimpleNamespace()).id == "search_content"
    assert (
        media.index_visual_derivative(ctx, media_derivative_id="der-1", generation="g1").id == "index_visual_derivative"
    )
    media.promote_visual_generation(ctx, expected_active="g0", generation="g1")
    assert media.search_visual(ctx, text="chart").id == "search_visual"
    assert media.preview(ctx, media_item_version_id="v1", access_pattern="thumbnail").id == "preview"
    assert media.purge_access_caches(ctx, media_item_version_id="v1") == ["removed"]
    assert media.register_external_version(ctx, media_set_id="set", logical_path="a", source_ref={}).id == (
        "register_external_version"
    )
    assert media.resolve_external_version(ctx, media_item_version_id="v1").id == "resolve_external_version"
    assert media.mark_media_versions_for_retention(ctx, media_item_version_ids=["v1"], now=datetime.now()) == 1
    media.place_legal_hold(ctx, media_item_version_id="v1")
    media.release_legal_hold(ctx, media_item_version_id="v1")
    assert media.purge_marked_media_versions(ctx, now=datetime.now(), grace=timedelta()).id == (
        "purge_marked_media_versions"
    )
    assert "abort" in media_calls
    assert "filter_hits" in media_calls

    class FacadeDelegate:
        def __getattr__(self, name: str):
            def call(*_args, **_kwargs):
                return {"called": name}

            return call

    class SourceServiceBundle:
        source_onboarding = FacadeDelegate()
        source_management = FacadeDelegate()
        source_lifecycle = FacadeDelegate()
        source_connection_test = FacadeDelegate()
        source_scheduler = FacadeDelegate()
        source_cdc_object_index = FacadeDelegate()

    source = SourceWorkspace(SourceServiceBundle())
    assert source.list_credentials(ctx=ctx)["called"] == "list_credentials"
    assert source.list_agents(ctx=ctx)["called"] == "list_agents"
    assert source.list_network_policies(ctx=ctx)["called"] == "list_network_policies"
    assert source.list_managed_syncs(ctx=ctx)["called"] == "list_managed_syncs"
    assert source.get_managed_sync("sync", ctx=ctx)["called"] == "get_managed_sync"
    assert (
        source.update_source_status(
            "source",
            status="disabled",
            expected_config_fingerprint="sha256:source",
            idempotency_key="source-disable-1",
            ctx=ctx,
        )["called"]
        == "update_source_status"
    )
    assert (
        source.update_managed_sync_schedule(
            "sync",
            schedule={"mode": "manual"},
            expected_config_fingerprint="sha256:sync",
            idempotency_key="sync-schedule-1",
            ctx=ctx,
        )["called"]
        == "update_managed_sync_schedule"
    )
    assert source.preview_due_managed_syncs(ctx=ctx)["called"] == "preview_due_managed_syncs"
    assert source.run_due_managed_syncs(ctx=ctx, max_runs=2)["called"] == "run_due_managed_syncs"
    assert (
        source.start_debezium_object_index(
            "cdc",
            object_type_api_name="Order",
            idempotency_key="cdc-index-1",
            ctx=ctx,
        )["called"]
        == "start_debezium_object_index"
    )
    assert (
        source.upload_batch_files(
            source_name="batch",
            display_name="Batch",
            uploads=[],
            idempotency_key="batch-1",
            ctx=ctx,
        )["called"]
        == "upload_batch_files"
    )
    assert (
        source.create_webhook_listener(
            source_name="webhook",
            display_name="Webhook",
            dataset_ref="raw.orders",
            connector_name="mock",
            resource_name="orders",
            signing_secret_ref="secretRef:webhook",
            inbound_url="https://example.test",
            idempotency_key="webhook-1",
            ctx=ctx,
        )["called"]
        == "create_webhook_listener"
    )
    assert (
        source.ingest_webhook_listener_event(
            "webhook",
            payload={},
            raw_body=b"{}",
            signature="sig",
            signature_timestamp="now",
            event_id="event-1",
            ctx=ctx,
        )["called"]
        == "ingest_webhook_listener_event"
    )


def test_outbox_worker_failure_and_empty_batch_edges(monkeypatch, tmp_path) -> None:
    batches = [
        {"requested": 0, "published": 0, "failed": 0, "skipped": 0, "eventIds": [], "deadLetterEventIds": []},
        {"requested": 1, "published": 1, "failed": 0, "skipped": 0, "eventIds": ["event-1"], "deadLetterEventIds": []},
    ]

    class Operations:
        def publish_pending_outbox(self, **_kwargs):
            return batches.pop(0)

    monkeypatch.setattr(outbox_publisher, "_build_foundry", lambda _config: SimpleNamespace(operations=Operations()))
    result = outbox_publisher.publish_outbox_batches(
        outbox_publisher.OutboxPublisherWorkerConfig(
            storage_root=tmp_path,
            max_batches=2,
            max_empty_batches=2,
            evidence_path=tmp_path / "evidence" / "outbox.json",
        )
    )
    assert result.stop_reason == "max_batches"
    assert result.empty_batches == 0
    assert result.event_ids == ("event-1",)

    config = outbox_publisher.OutboxPublisherWorkerConfig(storage_root=tmp_path, request_id="req-worker")
    value_payload = outbox_publisher._failure_payload(ValueError("bad config"), config)
    domain_payload = outbox_publisher._failure_payload(ValidationFailed("bad runtime"), config)
    assert value_payload["type"] == "CONFIGURATION_ERROR"
    assert value_payload["trace"]["request_id"] == "req-worker:batch-1"
    assert domain_payload["trace"]["adapter"] == "outbox_publisher_worker"
    assert outbox_publisher._failure_trace(None) == {"adapter": "outbox_publisher_worker"}
    assert outbox_publisher._failure_context(None) is None
    assert (
        outbox_publisher._failure_context(
            outbox_publisher.OutboxPublisherWorkerConfig(storage_root=tmp_path, request_id=" ")
        )
        is None
    )

    monkeypatch.setattr(
        outbox_publisher, "publish_outbox_batches", lambda _config: (_ for _ in ()).throw(ValueError("x"))
    )
    assert outbox_publisher.main(["--storage-root", str(tmp_path), "--max-batches", "0"]) == 1


def test_local_secret_vault_and_preview_adapters_fail_closed(tmp_path) -> None:
    vault = LocalSecretVaultProvider(tmp_path / "vault")
    assert vault.failure_contract().adapter_profile == "local-secret-vault"
    secret = vault.put_secret("api_token", "value", metadata={"password": "raw", "owner": "ops"})
    assert secret.version.startswith("sha256:")
    assert vault.get_secret("api_token").value == "value"
    assert _safe_metadata({"token": "raw", "owner": "ops"}) == {"token": "***REDACTED***", "owner": "ops"}
    assert _secret_record({"secrets": []}, "missing", None) is None
    assert _secret_record({"secrets": {"missing": {"versions": {}}}}, "missing", None) is None
    assert _redacted_details("api_token")["secret_value"] == "***REDACTED***"

    with pytest.raises(ValidationFailed):
        vault.put_secret("blank", "")
    with pytest.raises(ValidationFailed):
        vault.put_secret("bad name", "value")

    payload = json.loads(vault._vault_path.read_text(encoding="utf-8"))
    payload["secrets"]["api_token"]["versions"][secret.version]["ciphertext"] = "not-a-fernet-token"
    vault._vault_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValidationFailed):
        vault.get_secret("api_token")

    invalid_vault = LocalSecretVaultProvider(tmp_path / "invalid-vault")
    invalid_vault._vault_path.write_text("{", encoding="utf-8")
    with pytest.raises(ValidationFailed):
        invalid_vault.get_secret("missing")

    renderer = LocalPreviewRendererAdapter()
    assert renderer.supports("thumbnail") is True
    assert renderer.supports("waveform") is False
    assert renderer.failure_contract().adapter_profile == "local-preview"
    with pytest.raises(PreviewRenderError) as unsupported:
        renderer.render(PreviewRenderRequest(access_pattern="waveform", source_path=str(tmp_path / "audio.wav")))
    assert unsupported.value.reason == "preview_renderer_unavailable"

    timeout_blocker = Event()

    def slow_thumbnail(*_args) -> bytes:
        timeout_blocker.wait(0.05)
        return b"late"

    timeout_renderer = LocalPreviewRendererAdapter(timeout_seconds=0, thumbnail_engine=slow_thumbnail)
    with pytest.raises(PreviewRenderError) as timed_out:
        timeout_renderer.render(
            PreviewRenderRequest(access_pattern="thumbnail", source_path=str(tmp_path / "image.png"))
        )
    assert timed_out.value.reason == "render_timeout"


def test_source_onboarding_service_covers_batch_webhook_cdc_and_replay_paths(tmp_path) -> None:
    ctx = RequestContext(tenant_id="tenant-demo", actor_user_id="user-demo", request_id="req-source-service")
    service = _source_onboarding_service(tmp_path)

    first_upload = service.upload_csv(
        source_name="csv_orders",
        display_name="CSV Orders",
        dataset_ref="raw.orders",
        file_name="orders.csv",
        source=BytesIO(b"order_id\nO-1\n"),
        idempotency_key="csv-1",
        ctx=ctx,
        primary_key=("order_id",),
    )
    replay = service.upload_csv(
        source_name="csv_orders",
        display_name="CSV Orders",
        dataset_ref="raw.orders",
        file_name="orders.csv",
        source=BytesIO(b"order_id\nO-1\n"),
        idempotency_key="csv-1",
        ctx=ctx,
        primary_key=("order_id",),
    )
    assert first_upload["commitResult"]["transactionId"] == "tx-raw-orders"
    assert replay["isIdempotentReplay"] is True

    batch = service.upload_batch_files(
        source_name="batch_orders",
        display_name="Batch Orders",
        uploads=[
            SourceUpload("orders.csv", "raw.orders", BytesIO(b"order_id\nO-1\n")),
            SourceUpload("customers.csv", "raw.customers", BytesIO(b"customer_id\nC-1\n")),
        ],
        idempotency_key="batch-1",
        ctx=ctx,
        sync_name="nightly",
    )
    assert [row["datasetRef"] for row in batch["commitResults"]] == ["raw.orders", "raw.customers"]
    batch_replay = service.upload_batch_files(
        source_name="batch_orders",
        display_name="Batch Orders",
        uploads=[
            SourceUpload("orders.csv", "raw.orders", BytesIO(b"order_id\nO-1\n")),
            SourceUpload("customers.csv", "raw.customers", BytesIO(b"customer_id\nC-1\n")),
        ],
        idempotency_key="batch-1",
        ctx=ctx,
        sync_name="nightly",
    )
    assert batch_replay["isIdempotentReplay"] is True

    webhook = service.create_webhook_listener(
        source_name="webhook_orders",
        display_name="Webhook Orders",
        dataset_ref="raw.orders",
        connector_name="mock",
        resource_name="orders",
        signing_secret_ref="secretRef:webhook",
        inbound_url="https://example.test/webhook",
        idempotency_key="webhook-1",
        ctx=ctx,
    )
    assert webhook["source"]["kind"] == "webhook_listener"
    ingested = service.ingest_webhook_listener_event(
        "webhook_orders",
        payload={"orderId": "O-1"},
        raw_body=b'{"orderId":"O-1"}',
        signature="sig",
        signature_timestamp="now",
        event_id="event-1",
        ctx=ctx,
    )
    assert ingested["commitResult"]["datasetRef"] == "raw.orders"

    debezium = service.create_debezium_source(
        source_name="cdc_orders",
        display_name="CDC Orders",
        dataset_ref="raw.orders",
        stream_name="orders_stream",
        topic="orders",
        primary_key=["order_id"],
        idempotency_key="cdc-1",
        ctx=ctx,
    )
    assert debezium["source"]["kind"] == "debezium_cdc"
    assert debezium["source"]["configSummary"]["primaryKey"] == ["order_id"]
    cdc_plan = service.debezium_operation_plan("cdc_orders", ctx=ctx, object_type_api_name="Order")
    assert cdc_plan["readiness"]["status"] == "ready_for_cdc_workers"
    assert "--cdc-primary-key order_id" in cdc_plan["workerCommands"]["streamArchive"]
    assert "--source-dataset raw.orders" in cdc_plan["workerCommands"]["objectIndexer"]
    no_events = service.start_debezium_sync("cdc_orders", idempotency_key="cdc-run-1", ctx=ctx, limit=10)
    assert no_events["testResult"]["status"] == "no_events"
    assert no_events["testResult"]["datasetRef"] == "raw.orders"
    assert str(no_events["testResult"]["runId"]).startswith("sync_run_")
    assert no_events["source"]["lastRunId"] == no_events["testResult"]["runId"]
    assert no_events["source"]["operationsPath"] == f"/api/operations/runs/sync/{no_events['testResult']['runId']}"
    assert service.get_source("rest_orders", ctx=ctx)["kind"] == "rest"

    with pytest.raises(ConflictDetected):
        service.source_registry_repository.create_source(
            transaction=object(),
            record=onboarding_helpers.webhook_record(
                ctx,
                source_name="webhook_orders",
                display_name="Changed",
                dataset_ref="raw.orders",
                connector_name="mock",
                resource_name="orders",
                signing_secret_ref="secretRef:webhook",
                inbound_url="https://example.test/webhook",
            ),
        )

    service.source_registry_repository = _ConcurrentSourceRegistry()
    with pytest.raises(ConflictDetected):
        service._create_source(
            ctx,
            onboarding_helpers.webhook_record(
                ctx,
                source_name="race_orders",
                display_name="Race Orders",
                dataset_ref="raw.orders",
                connector_name="mock",
                resource_name="orders",
                signing_secret_ref="secretRef:webhook",
                inbound_url="https://example.test/webhook",
            ),
        )


def test_source_management_service_covers_query_explore_and_run_branches() -> None:
    ctx = RequestContext(tenant_id="tenant-demo", actor_user_id="user-demo", request_id="req-source-management")
    service = _source_management_service()

    assert service.list_credentials(ctx=ctx)[0]["credentialName"] == "db"
    created_agent = service.register_agent(
        agent_id="agent_1",
        display_name="Agent",
        mode="agent_proxy",
        capabilities={"database": True},
        network_summary={"host": "db.internal"},
        idempotency_key="agent-1",
        ctx=ctx,
    )
    replayed_agent = service.register_agent(
        agent_id="agent_1",
        display_name="Agent",
        mode="agent_proxy",
        capabilities={"database": True},
        network_summary={"host": "db.internal"},
        idempotency_key="agent-1-replay",
        ctx=ctx,
    )
    assert created_agent == replayed_agent
    assert service.list_agents(ctx=ctx)[0]["agentId"] == "agent_1"
    with pytest.raises(NotFound):
        service.heartbeat_agent("missing", ctx=ctx)
    assert service.list_network_policies(ctx=ctx)[0]["policyName"] == "direct"

    sync = service.create_managed_sync(
        sync_name="rest_sync",
        source_name="rest",
        display_name="REST Sync",
        source_type="rest_api",
        capability="batch",
        mode="APPEND",
        config_summary={"connectorName": "erp", "resourceName": "orders"},
        target_dataset_ref="raw.orders",
        idempotency_key="sync-1",
        ctx=ctx,
    )
    assert sync["syncName"] == "rest_sync"
    assert service.list_managed_syncs(ctx=ctx)[0]["syncName"] == "rest_sync"
    assert service.get_managed_sync("rest_sync", ctx=ctx)["sourceName"] == "rest"
    with pytest.raises(NotFound):
        service.get_managed_sync_run("missing", ctx=ctx)

    rest_preview = service.explore_source(
        source_name="erp",
        source_type="rest_api",
        request={"baseUrl": "https://api.example.test", "resourcePath": "/orders", "resourceName": "orders"},
        ctx=ctx,
    )
    assert rest_preview["resultSummary"]["sample"] == [{"id": "O-1"}]
    tables = service.explore_source(
        source_name="warehouse",
        source_type="postgres_jdbc",
        request={"databaseUrlSecretRef": "db", "sampleLimit": "2"},
        ctx=ctx,
    )
    rows = service.explore_source(
        source_name="warehouse",
        source_type="postgres_jdbc",
        request={"databaseUrlSecretRef": "db", "tableName": "orders", "sampleLimit": "2"},
        ctx=ctx,
    )
    assert tables["resultSummary"]["tables"][0]["name"] == "orders"
    assert rows["resultSummary"]["sample"][0]["order_id"] == "O-1"
    with pytest.raises(ValidationFailed):
        service.explore_source(source_name="unknown", source_type="unsupported", request={}, ctx=ctx)

    run = _sync_run_row(
        "run-rest",
        "rest_sync",
        "rest_api",
        idempotency_key="idem-rest",
    )
    rest_run = service._execute_run(ctx, service.source_management_repository.syncs["rest_sync"], run)
    assert rest_run["status"] == "running"

    db_sync = _sync_row(
        "db_sync",
        "postgres_jdbc",
        target_dataset_ref="raw.orders",
        config_summary={"databaseUrlSecretRef": "db", "tableName": "orders", "batchLimit": "2"},
    )
    service.source_registry_repository.rows["source"] = {
        "source_name": "source",
        "config_summary": {"connectionMode": "direct"},
    }
    service.source_management_repository.syncs["db_sync"] = db_sync
    db_run = _sync_run_row("run-db", "db_sync", "postgres_jdbc", batch_limit=1)
    completed = service._execute_run(ctx, db_sync, db_run)
    assert completed["status"] == "succeeded"

    failed = service._execute_run(ctx, {"sync_name": "bad_sync", "source_type": "unsupported"}, db_run)
    assert failed["status"] == "failed"


class _Tx:
    def __enter__(self):
        return object()

    def __exit__(self, *_args):
        return False


class _Engine:
    def begin(self):
        return _Tx()


class _AllowPolicy:
    def require(self, *_args, **_kwargs) -> None:
        return None


class _RuntimeAudit:
    def _require_write_traffic_open(self, *_args, **_kwargs) -> None:
        return None

    def _audit(self, *_args, **_kwargs) -> None:
        return None

    def _outbox(self, *_args, **_kwargs) -> str:
        return "outbox-1"


class _RuntimeRepository:
    def workflow_run_by_idempotency(self, **_kwargs):
        return None


class _DatasetRegistry:
    def ensure_dataset(self, *_args, **_kwargs):
        dataset_ref = str(_args[0])
        return {"id": f"dataset-{dataset_ref.replace('.', '-')}"}

    def list_dataset_versions(self, dataset_ref, **_kwargs):
        return [{"id": f"version-{str(dataset_ref).replace('.', '-')}", "version_number": 1}]


class _DatasetTransactions:
    def __init__(self) -> None:
        self.runs: dict[str, object] = {}

    def sync_run_by_transaction_id(self, **_kwargs):
        return {"id": f"run-{_kwargs['transaction_id']}"}

    def insert_sync_run(self, *, transaction, record) -> None:
        self.runs[record.sync_run_id] = record


class _DatasetIngest:
    def upload_csv(self, dataset_ref, *_args, **_kwargs):
        return _Commit(dataset_ref=dataset_ref, transaction_id=f"tx-{dataset_ref.replace('.', '-')}")

    def ingest_webhook_event(self, dataset_ref, **_kwargs):
        return _Commit(dataset_ref=dataset_ref, transaction_id="tx-webhook")

    def archive_stream_events(self, *_args, **_kwargs):
        return None

    def sync_rows_batch(self, dataset_ref, rows, **_kwargs):
        return _Commit(dataset_ref=dataset_ref, row_count=len(rows), transaction_id="tx-db-sync")


class _SecretProvider:
    def get_secret(self, *_args, **_kwargs):
        return SimpleNamespace(value="secret")


class _SourceRegistry:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, object]] = {}

    def create_source(self, *, transaction, record) -> None:
        if record.source_name in self.rows:
            raise ConflictDetected("source already exists", details={"source_name": record.source_name})
        self.rows[record.source_name] = _source_row_from_record(record)

    def update_source(self, *, transaction, tenant_id, source_name, patch):
        row = self.rows.get(source_name)
        if row is None:
            return None
        for key, value in patch.__dict__.items():
            if value is not None:
                row[key] = value
        return row

    def source_by_name(self, *, transaction, tenant_id, source_name):
        return self.rows.get(source_name)

    def list_sources(self, *, tenant_id):
        return list(self.rows.values())


class _ConcurrentSourceRegistry(_SourceRegistry):
    def source_by_name(self, *, transaction, tenant_id, source_name):
        return None

    def create_source(self, *, transaction, record) -> None:
        raise SourceConnectionAlreadyExistsError()


class _ResourceCatalogRepository:
    def __init__(self) -> None:
        self.projects: list[dict[str, object]] = []
        self.resources: dict[tuple[str, str], dict[str, object]] = {}

    def list_projects(self, *, transaction, tenant_id):
        return [row for row in self.projects if row["tenant_id"] == tenant_id]

    def insert_project(self, *, transaction, record):
        row = {
            "id": record.project_id,
            "tenant_id": record.tenant_id,
            "rid": record.rid,
            "display_name": record.display_name,
            "description": record.description,
            "status": record.status,
            "created_by_user_id": record.created_by_user_id,
            "metadata": record.metadata,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }
        self.projects.append(row)
        return row

    def upsert_project_grant(self, *, transaction, record):
        return {
            "id": record.grant_id,
            "tenant_id": record.tenant_id,
            "project_id": record.project_id,
            "principal_type": record.principal_type,
            "principal_id": record.principal_id,
            "role": record.role,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }

    def upsert_resource(self, *, transaction, record):
        key = (record.source_surface, record.source_ref)
        row = {
            "id": record.resource_id,
            "tenant_id": record.tenant_id,
            "rid": record.rid,
            "resource_type": record.resource_type,
            "display_name": record.display_name,
            "project_id": record.project_id,
            "folder_id": record.folder_id,
            "source_surface": record.source_surface,
            "source_ref": record.source_ref,
            "operations_path": record.operations_path,
            "status": record.status,
            "metadata": record.metadata,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }
        self.resources[key] = row
        return row


class _ConnectorRegistry:
    def list_connections(self, *, tenant_id):
        return [_connector_connection_row("rest_orders")]

    def connection_by_name(self, *, transaction, tenant_id, connector_name):
        return _connector_connection_row(connector_name) if connector_name == "rest_orders" else None

    def resources_for_connection(self, *, transaction, tenant_id, connector_name):
        return [
            {
                "id": "resource-1",
                "tenant_id": tenant_id,
                "connector_name": connector_name,
                "resource_name": "orders",
                "dataset_ref": "raw.orders",
                "resource_path": "/orders",
                "pagination": {},
                "schema_columns": ["order_id"],
                "primary_key": ["order_id"],
                "config_fingerprint": "sha256:resource",
                "status": "active",
                "created_at": "2026-06-29T00:00:00Z",
                "updated_at": "2026-06-29T00:00:00Z",
            }
        ]


def _source_onboarding_service(tmp_path) -> SourceOnboardingService:
    service = object.__new__(SourceOnboardingService)
    service.engine = _Engine()
    service.policy = _AllowPolicy()
    service.root = tmp_path
    service.connector_registry_repository = _ConnectorRegistry()
    service.source_registry_repository = _SourceRegistry()
    service.runtime_repository = _RuntimeRepository()
    service.dataset_transaction_repository = _DatasetTransactions()
    service.resource_catalog_repository = _ResourceCatalogRepository()
    service.secret_provider = _SecretProvider()
    service.dataset_ingest_service = _DatasetIngest()
    service.dataset_registry_service = _DatasetRegistry()
    service.media_transaction_service = SimpleNamespace()
    service.media_upload_service = SimpleNamespace()
    service.runtime_service = _RuntimeAudit()
    return service


def _source_row_from_record(record) -> dict[str, object]:
    return {
        "id": record.source_id,
        "tenant_id": record.tenant_id,
        "source_name": record.source_name,
        "display_name": record.display_name,
        "kind": record.kind,
        "target_dataset_ref": record.target_dataset_ref,
        "target_media_set_id": record.target_media_set_id,
        "status": record.status,
        "config_summary": record.config_summary,
        "config_fingerprint": record.config_fingerprint,
        "last_run_id": record.last_run_id,
        "last_workflow_run_id": record.last_workflow_run_id,
        "last_commit_ref": record.last_commit_ref,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _connector_connection_row(connector_name: str) -> dict[str, object]:
    return {
        "id": "connector-1",
        "tenant_id": "tenant-demo",
        "connector_name": connector_name,
        "display_name": "REST Orders",
        "kind": "rest",
        "base_url": "https://api.example.test",
        "auth": {"mode": "none"},
        "rate_limit_per_minute": None,
        "allow_private_network": False,
        "status": "active",
        "config_fingerprint": "sha256:connector",
        "created_at": "2026-06-29T00:00:00Z",
        "updated_at": "2026-06-29T00:00:00Z",
    }


class _SecretVault:
    def put_secret(self, *_args, **_kwargs):
        return None

    def get_secret(self, *_args, **_kwargs):
        return SimpleNamespace(value="sqlite:///source.db")


class _ConnectorAdapter:
    def snapshot(self, *_args, **_kwargs):
        return SimpleNamespace(rows=[{"id": "O-1"}], schema={"columns": [{"name": "id"}]}, cursor={"next": "c2"})


class _SourceDatabaseAdapter:
    def test_connection(self, *_args, **_kwargs):
        return SimpleNamespace(
            adapter_profile="test-source-database",
            database_kind="sqlite",
            driver="pysqlite",
            visible_resource_count=1,
        )

    def list_tables(self, *_args, **_kwargs):
        return [{"name": "orders"}]

    def read_table_batch(self, *_args, **_kwargs):
        return SimpleNamespace(
            rows=[{"order_id": "O-1"}],
            schema={"columns": [{"name": "order_id"}]},
            checkpoint={"lastValue": "O-1"},
            network_evidence={},
        )


class _ConnectorOnboarding:
    def start_resource_sync(self, *_args, **_kwargs):
        return {"workflowRunId": "workflow-1", "status": "running"}

    def get_connection(self, *_args, **_kwargs):
        return {"resources": [{"resourceName": "orders", "datasetRef": "raw.orders"}]}


class _EmptySourceManagementRepository:
    def credential_by_name(self, **_kwargs):
        return None

    def agent_by_id(self, **_kwargs):
        return None

    def network_policy_by_name(self, **_kwargs):
        return None

    def sync_by_name(self, **_kwargs):
        return None

    def sync_run_by_id(self, **_kwargs):
        return None


class _ExistingSourceManagementRepository:
    def __init__(self) -> None:
        self.write_count = 0
        self.credential = _credential_row()
        self.policy = _network_policy_row()
        self.sync = _sync_row("rest_sync", "rest_api", target_dataset_ref="raw.orders")

    def credential_by_name(self, **_kwargs):
        return self.credential

    def network_policy_by_name(self, **_kwargs):
        return self.policy

    def sync_by_name(self, **_kwargs):
        return self.sync

    def create_credential(self, **_kwargs):
        self.write_count += 1

    def create_network_policy(self, **_kwargs):
        self.write_count += 1

    def create_sync(self, **_kwargs):
        self.write_count += 1


class _SourceManagementRepository:
    def __init__(self) -> None:
        self.credentials = {"db": _credential_row()}
        self.agents: dict[str, dict[str, object]] = {}
        self.policies = {"direct": _network_policy_row()}
        self.syncs: dict[str, dict[str, object]] = {}
        self.runs: dict[str, dict[str, object]] = {}

    def list_credentials(self, *, tenant_id):
        return list(self.credentials.values())

    def credential_by_name(self, *, transaction, tenant_id, credential_name):
        return self.credentials.get(credential_name)

    def create_agent(self, *, transaction, record):
        self.agents[record.agent_id] = _agent_row(record)

    def agent_by_id(self, *, transaction, tenant_id, agent_id):
        return self.agents.get(agent_id)

    def list_agents(self, *, tenant_id):
        return list(self.agents.values())

    def update_agent_heartbeat(self, *, transaction, tenant_id, agent_id, heartbeat_at):
        row = self.agents.get(agent_id)
        if row is not None:
            row["last_heartbeat_at"] = heartbeat_at
        return row

    def list_network_policies(self, *, tenant_id):
        return list(self.policies.values())

    def create_sync(self, *, transaction, record):
        self.syncs[record.sync_name] = _sync_row_from_record(record)

    def sync_by_name(self, *, transaction, tenant_id, sync_name):
        return self.syncs.get(sync_name)

    def list_syncs(self, *, tenant_id):
        return list(self.syncs.values())

    def sync_run_by_id(self, *, transaction, tenant_id, run_id):
        return self.runs.get(run_id)

    def sync_run_by_idempotency_key(self, **_kwargs):
        return None

    def create_sync_run(self, *, transaction, record):
        self.runs[record.id] = _sync_run_from_record(record)

    def list_sync_runs(self, *, tenant_id, sync_name):
        return [row for row in self.runs.values() if row["sync_name"] == sync_name]

    def update_sync_run_result(self, *, transaction, tenant_id, run_id, **patch):
        row = self.runs.setdefault(run_id, _sync_run_row(run_id, "sync", "rest_api"))
        row.update(patch)
        return row

    def update_sync_after_run(
        self, *, transaction, tenant_id, sync_name, run_id, workflow_run_id, checkpoint, updated_at
    ):
        sync = self.syncs.get(sync_name)
        if sync is not None:
            sync["last_run_id"] = run_id
            sync["last_workflow_run_id"] = workflow_run_id
            sync["checkpoint"] = checkpoint
            sync["updated_at"] = updated_at

    def create_exploration_run(self, *, transaction, record):
        return None


def _source_management_service() -> SourceManagementService:
    service = object.__new__(SourceManagementService)
    service.engine = _Engine()
    service.policy = _AllowPolicy()
    service.connector_adapter = _ConnectorAdapter()
    service.source_database_adapter = _SourceDatabaseAdapter()
    service.source_stream_adapter = SimpleNamespace()
    service.source_management_repository = _SourceManagementRepository()
    service.source_registry_repository = _SourceRegistry()
    service.resource_catalog_repository = _ResourceCatalogRepository()
    service.secret_vault = _SecretVault()
    service.connector_onboarding_service = _ConnectorOnboarding()
    service.dataset_ingest_service = _DatasetIngest()
    service.dataset_registry_service = _DatasetRegistry()
    service.runtime_service = _RuntimeAudit()
    return service


def _credential_row() -> dict[str, object]:
    return {
        "id": "cred-1",
        "tenant_id": "tenant-demo",
        "credential_name": "db",
        "display_name": "DB",
        "kind": "database",
        "auth_scheme": "password",
        "secret_name": "db",
        "secret_version": "sha256:secret",
        "status": "active",
        "config_summary": {},
        "config_fingerprint": "sha256:cred",
        "created_at": "2026-06-29T00:00:00Z",
        "updated_at": "2026-06-29T00:00:00Z",
    }


def _agent_row(record) -> dict[str, object]:
    return {
        "id": record.id,
        "tenant_id": record.tenant_id,
        "agent_id": record.agent_id,
        "display_name": record.display_name,
        "mode": record.mode,
        "status": record.status,
        "capabilities": record.capabilities,
        "network_summary": record.network_summary,
        "last_heartbeat_at": record.last_heartbeat_at,
        "config_fingerprint": record.config_fingerprint,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _network_policy_row() -> dict[str, object]:
    return {
        "id": "policy-1",
        "tenant_id": "tenant-demo",
        "policy_name": "direct",
        "display_name": "Direct",
        "mode": "direct",
        "agent_id": None,
        "allowed_hosts": {"hosts": ["api.example.test"]},
        "status": "active",
        "config_fingerprint": "sha256:policy",
        "created_at": "2026-06-29T00:00:00Z",
        "updated_at": "2026-06-29T00:00:00Z",
    }


def _sync_row_from_record(record) -> dict[str, object]:
    return {
        "id": record.id,
        "tenant_id": record.tenant_id,
        "sync_name": record.sync_name,
        "source_name": record.source_name,
        "display_name": record.display_name,
        "source_type": record.source_type,
        "capability": record.capability,
        "target_dataset_ref": record.target_dataset_ref,
        "target_media_set_id": record.target_media_set_id,
        "mode": record.mode,
        "schedule": record.schedule,
        "config_summary": record.config_summary,
        "config_fingerprint": record.config_fingerprint,
        "status": record.status,
        "last_run_id": record.last_run_id,
        "last_workflow_run_id": record.last_workflow_run_id,
        "checkpoint": record.checkpoint,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _sync_row(
    sync_name: str,
    source_type: str,
    *,
    target_dataset_ref: str | None = None,
    config_summary: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "id": f"sync-{sync_name}",
        "tenant_id": "tenant-demo",
        "sync_name": sync_name,
        "source_name": "source",
        "display_name": sync_name,
        "source_type": source_type,
        "capability": "snapshot",
        "target_dataset_ref": target_dataset_ref,
        "target_media_set_id": None,
        "mode": "APPEND",
        "schedule": {},
        "config_summary": config_summary or {},
        "config_fingerprint": f"sha256:{sync_name}",
        "status": "active",
        "last_run_id": None,
        "last_workflow_run_id": None,
        "checkpoint": {},
        "created_at": "2026-06-29T00:00:00Z",
        "updated_at": "2026-06-29T00:00:00Z",
    }


def _sync_run_from_record(record) -> dict[str, object]:
    return {
        "id": record.id,
        "tenant_id": record.tenant_id,
        "sync_name": record.sync_name,
        "source_name": record.source_name,
        "source_type": record.source_type,
        "capability": record.capability,
        "workflow_run_id": record.workflow_run_id,
        "dataset_version_id": record.dataset_version_id,
        "status": record.status,
        "trigger_type": record.trigger_type,
        "idempotency_key": record.idempotency_key,
        "batch_limit": record.batch_limit,
        "checkpoint_start": record.checkpoint_start,
        "checkpoint_end": record.checkpoint_end,
        "result_summary": record.result_summary,
        "error": record.error,
        "operations_path": record.operations_path,
        "started_at": record.started_at,
        "completed_at": record.completed_at,
        "created_at": record.created_at,
    }


def _sync_run_row(
    run_id: str,
    sync_name: str,
    source_type: str,
    *,
    idempotency_key: str = "idem",
    batch_limit: int | None = None,
) -> dict[str, object]:
    return {
        "id": run_id,
        "tenant_id": "tenant-demo",
        "sync_name": sync_name,
        "source_name": "source",
        "source_type": source_type,
        "capability": "snapshot",
        "workflow_run_id": None,
        "dataset_version_id": None,
        "status": "running",
        "trigger_type": "manual",
        "idempotency_key": idempotency_key,
        "batch_limit": batch_limit,
        "checkpoint_start": {},
        "checkpoint_end": {},
        "result_summary": {},
        "error": None,
        "operations_path": f"/api/sources/managed-sync-runs/{run_id}",
        "started_at": "2026-06-29T00:00:00Z",
        "completed_at": None,
        "created_at": "2026-06-29T00:00:00Z",
    }
