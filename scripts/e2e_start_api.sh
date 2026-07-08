#!/usr/bin/env bash
set -euo pipefail

export PYTHONPATH=".:libs:apps/cli:apps/api"
export FOUNDRY_LITE_HOME="${FOUNDRY_LITE_HOME:-.foundry-lite-e2e}"
export FOUNDRY_LITE_OTEL_DISABLED="${FOUNDRY_LITE_OTEL_DISABLED:-1}"
export FOUNDRY_LITE_CONNECTOR_PROFILE="${FOUNDRY_LITE_CONNECTOR_PROFILE:-rest}"
export FOUNDRY_LITE_SECRET_AIP_PROMPT_ARTIFACT_ENCRYPTION_KEY="${FOUNDRY_LITE_SECRET_AIP_PROMPT_ARTIFACT_ENCRYPTION_KEY:-e2e-prompt-artifact-key}"
export FOUNDRY_LITE_SECRET_AIP_CITATION_NAVIGATION_SIGNER="${FOUNDRY_LITE_SECRET_AIP_CITATION_NAVIGATION_SIGNER:-e2e-citation-navigation-signer}"

rm -rf "$FOUNDRY_LITE_HOME"
uv run python - <<'PY' >/tmp/foundry-lite-e2e-seed.json
import json
import os
from pathlib import Path

from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import ActionRunRecord, ActionWritebackRecord, DeadLetterRecord, ObjectRecordInsert
from foundry_lite.application.ports.external_writeback_adapter import (
    ExternalWritebackPayload,
    ExternalWriteTarget,
    RemoteOutcome,
    RemoteOutcomeStatus,
    WriteReceipt,
)
from foundry_lite.application.primitives import _json_hash, _new_id, _now
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.domain.errors import ExternalOutcomeUnknown
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies


class E2EExternalWritebackAdapter:
    profile_name = "e2e-memory-external-writeback"

    def write(self, target: ExternalWriteTarget, payload: ExternalWritebackPayload) -> WriteReceipt:
        del target, payload
        return WriteReceipt(status=RemoteOutcomeStatus.AMBIGUOUS)

    def remote_lookup(self, target: ExternalWriteTarget) -> RemoteOutcome:
        del target
        return RemoteOutcome(status=RemoteOutcomeStatus.LANDED, remote_resource_id="remote-sensitive-writeback")


def margin_action_ontology_path():
    ontology = Path("examples/supply-chain-demo/ontology/order-customer.yaml").read_text(encoding="utf-8")
    ontology = ontology.replace(
        "      - apiName: margin\n        column: margin\n        type: float\n        classification: finance",
        "      - apiName: margin\n"
        "        column: margin\n"
        "        type: float\n"
        "        classification: finance\n"
        "        editable: true\n"
        "        editPolicy: edit_wins",
    )
    ontology = f"""{ontology}
  - apiName: AdjustMargin
    displayName: Adjust margin
    target: Order
    parameters:
      - apiName: margin
        type: float
        required: true
    mutations:
      - type: setProperty
        property: margin
        valueFrom: params.margin
"""
    path = Path(os.environ["FOUNDRY_LITE_HOME"]) / "order-customer-margin-action.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ontology, encoding="utf-8")
    return path

ctx = demo_admin_context()
dependencies = create_local_core_dependencies(storage_root=os.environ["FOUNDRY_LITE_HOME"])
core = FoundryLite(dependencies=dependencies)
core.actions._action.set_external_writeback_adapter(E2EExternalWritebackAdapter())
core.demo.seed_files()
core.datasets.ensure("raw.erp_orders", ctx=ctx, primary_key=["order_id"])
core.datasets.ensure("raw.crm_customers", ctx=ctx, primary_key=["customer_id"])
core.datasets.ensure("clean.orders", ctx=ctx, primary_key=["order_id"])
core.datasets.ensure("clean.order_finance", ctx=ctx, primary_key=["order_id"])
core.datasets.ensure("clean.customers", ctx=ctx, primary_key=["customer_id"])
core.datasets.ensure("ops.action_log", ctx=ctx, primary_key=["action_run_id"])
core.datasets.ensure("ops.order_current", ctx=ctx, primary_key=["orderId"])
core.datasets.ensure("raw.shipment_cdc", ctx=ctx, primary_key=["event_id"])
core.demo.register_transforms(ctx)
orders_raw = core.datasets.upload_csv("raw.erp_orders", "examples/supply-chain-demo/data/orders.csv", ctx=ctx)
customers_raw = core.datasets.upload_csv("raw.crm_customers", "examples/supply-chain-demo/data/customers.csv", ctx=ctx)
clean_orders = core.transforms.run("clean_orders", ctx=ctx)
clean_order_finance = core.transforms.run("clean_order_finance", ctx=ctx)
clean_customers = core.transforms.run("clean_customers", ctx=ctx)
ontology = core.ontology.apply(str(margin_action_ontology_path()), ctx=ctx)
order_index = core.objects.reindex("Order", ctx=ctx)
customer_index = core.objects.reindex("Customer", ctx=ctx)

def seed_isolated_margin_order():
    source = core.objects.get("Order", "O-1002", ctx=ctx)
    properties = {
        "orderId": "O-1999",
        "customerId": "C-100",
        "status": "APPROVED",
        "amount": 410.0,
        "margin": 55.0,
        "riskScore": 0.1,
    }
    now = _now()
    with dependencies.engine.begin() as transaction:
        object_type = core._services.ontology.entrypoint._active_object_type(transaction, ctx, "Order")
        dependencies.object_index_repository.insert_object_record(
            transaction=transaction,
            record=ObjectRecordInsert(
                record_id=_new_id("obj"),
                tenant_id=ctx.tenant_id,
                object_type_id=object_type["id"],
                object_type_api_name="Order",
                object_id="O-1999",
                properties=properties,
                base_properties=properties,
                edit_properties={},
                property_versions={key: 1 for key in properties},
                source_dataset_version_id=str(source["sourceDatasetVersionId"]),
                source_hash=_json_hash(properties),
                object_version=1,
                deleted=False,
                deletion_reason=None,
                created_at=now,
                updated_at=now,
            ),
        )

def seed_stale_conflict_order():
    source = core.objects.get("Order", "O-1002", ctx=ctx)
    properties = {
        "orderId": "O-2999",
        "customerId": "C-100",
        "status": "PENDING",
        "amount": 128.0,
        "margin": 18.0,
        "riskScore": 0.2,
    }
    now = _now()
    with dependencies.engine.begin() as transaction:
        object_type = core._services.ontology.entrypoint._active_object_type(transaction, ctx, "Order")
        dependencies.object_index_repository.insert_object_record(
            transaction=transaction,
            record=ObjectRecordInsert(
                record_id=_new_id("obj"),
                tenant_id=ctx.tenant_id,
                object_type_id=object_type["id"],
                object_type_api_name="Order",
                object_id="O-2999",
                properties=properties,
                base_properties=properties,
                edit_properties={},
                property_versions={key: 1 for key in properties},
                source_dataset_version_id=str(source["sourceDatasetVersionId"]),
                source_hash=_json_hash(properties),
                object_version=1,
                deleted=False,
                deletion_reason=None,
                created_at=now,
                updated_at=now,
            ),
        )

def seed_action_writeback_reconciliation():
    action_run_id = "action_run_web_reconcile_o1003"
    writeback_id = "writeback_web_reconcile_o1003"
    idempotency_key = "operations-web-reconcile-o1003"
    request_fingerprint = "sha256:operations-web-reconcile-o1003"
    reason = "Operations reconciliation seed"
    target_object = core.objects.get("Order", "O-1003", ctx=ctx)
    now = _now()
    with dependencies.engine.begin() as transaction:
        action_type = core._services.ontology.entrypoint._active_action_type(transaction, ctx, "ApproveOrder")
        object_type = core._services.ontology.entrypoint._active_object_type(transaction, ctx, "Order")
        dependencies.action_repository.insert_action_run(
            transaction=transaction,
            record=ActionRunRecord(
                action_run_id=action_run_id,
                tenant_id=ctx.tenant_id,
                action_type_id=action_type["id"],
                action_type_api_name="ApproveOrder",
                actor_user_id="web-demo-operator",
                target_object_type_id=object_type["id"],
                target_object_type_api_name="Order",
                target_object_id="O-1003",
                expected_object_version=int(target_object["objectVersion"]),
                parameters={"reason": reason},
                status="outcome_unknown",
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                result=None,
                error={
                    "code": "EXTERNAL_OUTCOME_UNKNOWN",
                    "message": "writeback outcome is unknown",
                    "details": {
                        "connector_id": "mock_erp_simulator",
                        "idempotency_key": idempotency_key,
                        "request_hash": request_fingerprint,
                    },
                },
                created_at=now,
                completed_at=None,
            ),
        )
        dependencies.action_repository.insert_action_writeback(
            transaction=transaction,
            record=ActionWritebackRecord(
                writeback_id=writeback_id,
                tenant_id=ctx.tenant_id,
                action_run_id=action_run_id,
                mode="beforeCommit",
                connector_id="mock_erp_simulator",
                request={
                    "actionApiName": "ApproveOrder",
                    "objectType": "Order",
                    "objectId": "O-1003",
                    "expectedObjectVersion": target_object["objectVersion"],
                    "params": {"reason": reason},
                },
                response={
                    "status_code": 504,
                    "outcome_unknown": True,
                    "last_observed_status": "unknown",
                    "reconciliation_deadline": now,
                    "request_hash": request_fingerprint,
                },
                status="outcome_unknown",
                idempotency_key=idempotency_key,
                attempts=1,
                created_at=now,
                completed_at=None,
            ),
        )

def seed_action_writeback_approval_release():
    order = core.objects.get("Order", "O-1999", ctx=ctx)
    try:
        core.actions.apply(
            "AdjustMargin",
            object_type="Order",
            object_id="O-1999",
            expected_object_version=order["objectVersion"],
            params={"margin": 7777.77},
            idempotency_key="operations-web-approval-release-o1999",
            external_writeback_uri="memory://erp/orders/O-1999/margin",
            ctx=ctx,
        )
    except ExternalOutcomeUnknown:
        return
    raise RuntimeError("expected AdjustMargin seed writeback to remain outcome_unknown")

def seed_record_dlq(record_id, source_event_id, payload_hash):
    offset = int(source_event_id.rsplit(":", maxsplit=1)[-1])
    payload = {
        "op": "u",
        "pk": {"shipment_id": record_id},
        "before": None,
        "after": {"shipment_id": record_id, "status": "IN_TRANSIT"},
        "ordering": {"lsn": record_id},
    }
    with dependencies.engine.begin() as transaction:
        dependencies.dataset_transaction_repository.insert_dead_letter_record(
            transaction=transaction,
            record=DeadLetterRecord(
                dead_letter_record_id=record_id,
                tenant_id=ctx.tenant_id,
                source_event_id=source_event_id,
                source_dataset_version_id=None,
                source_run_id="sync_stream_web",
                payload=payload,
                payload_hash=payload_hash,
                schema_version=1,
                transform_version=None,
                error_kind="VALIDATION_FAILED",
                error_message="cdc envelope field must be an object",
                event_time=None,
                ingested_at="2026-06-10T00:03:00Z",
                first_failed_at="2026-06-10T00:03:00Z",
                attempts=1,
                status="QUARANTINED",
                replay_status="NOT_REQUESTED",
                replay_run_id=None,
                is_closed_partition_affected=False,
                metadata={
                    "dataset_ref": "raw.shipment_cdc",
                    "schema_strategy": "cdc_envelope_json",
                    "stream": "shipment_cdc",
                    "topic": "shipment_cdc_events",
                    "consumer_group": "foundry-lite-archive",
                    "partition": 0,
                    "offset": offset,
                    "event_type": "shipment.changed",
                    "event_key": record_id,
                    "request_id": ctx.request_id,
                },
            ),
        )

seed_record_dlq("dlqr_web_retry", "shipment_cdc_events:0:31", "payload-hash-web-retry")
seed_record_dlq("dlqr_web_discard", "shipment_cdc_events:0:32", "payload-hash-web-discard")
seed_isolated_margin_order()
seed_stale_conflict_order()
seed_action_writeback_reconciliation()
seed_action_writeback_approval_release()
print(
    json.dumps(
        {
            "rawOrdersVersion": orders_raw.version_id,
            "rawCustomersVersion": customers_raw.version_id,
            "cleanOrdersVersion": clean_orders.version_id,
            "cleanOrderFinanceVersion": clean_order_finance.version_id,
            "cleanCustomersVersion": clean_customers.version_id,
            "ontology": ontology,
            "orderIndex": order_index,
            "customerIndex": customer_index,
        },
        sort_keys=True,
    )
)
PY
uv run python - <<'PY'
import uvicorn

from foundry_lite.application.ports.external_writeback_adapter import (
    ExternalWritebackPayload,
    ExternalWriteTarget,
    RemoteOutcome,
    RemoteOutcomeStatus,
    WriteReceipt,
)
from foundry_lite_api import runtime
from foundry_lite_api.main import app


class E2EExternalWritebackAdapter:
    profile_name = "e2e-memory-external-writeback"

    def write(self, target: ExternalWriteTarget, payload: ExternalWritebackPayload) -> WriteReceipt:
        del target, payload
        return WriteReceipt(status=RemoteOutcomeStatus.AMBIGUOUS)

    def remote_lookup(self, target: ExternalWriteTarget) -> RemoteOutcome:
        del target
        return RemoteOutcome(status=RemoteOutcomeStatus.LANDED, remote_resource_id="remote-sensitive-writeback")


api_runtime = runtime.initialize_api_runtime()
api_runtime.foundry.actions._action.set_external_writeback_adapter(E2EExternalWritebackAdapter())
uvicorn.run(app, host="127.0.0.1", port=8000)
PY
