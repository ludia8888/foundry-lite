#!/usr/bin/env bash
set -euo pipefail

export PYTHONPATH=".:libs:apps/cli:apps/api"
export FOUNDRY_LITE_HOME="${FOUNDRY_LITE_HOME:-.foundry-lite-e2e}"
export FOUNDRY_LITE_OTEL_DISABLED="${FOUNDRY_LITE_OTEL_DISABLED:-1}"
export FOUNDRY_LITE_SECRET_AIP_CITATION_NAVIGATION_SIGNER="${FOUNDRY_LITE_SECRET_AIP_CITATION_NAVIGATION_SIGNER:-e2e-citation-navigation-signer}"

rm -rf "$FOUNDRY_LITE_HOME"
uv run python - <<'PY' >/tmp/foundry-lite-e2e-seed.json
import json
import os

from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import DeadLetterRecord
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies

ctx = demo_admin_context()
dependencies = create_local_core_dependencies(storage_root=os.environ["FOUNDRY_LITE_HOME"])
core = FoundryLite(dependencies=dependencies)
core.demo.seed_files()
core.datasets.ensure("raw.erp_orders", ctx=ctx, primary_key=["order_id"])
core.datasets.ensure("raw.crm_customers", ctx=ctx, primary_key=["customer_id"])
core.datasets.ensure("clean.orders", ctx=ctx, primary_key=["order_id"])
core.datasets.ensure("clean.customers", ctx=ctx, primary_key=["customer_id"])
core.datasets.ensure("ops.action_log", ctx=ctx, primary_key=["action_run_id"])
core.datasets.ensure("ops.order_current", ctx=ctx, primary_key=["orderId"])
core.datasets.ensure("raw.shipment_cdc", ctx=ctx, primary_key=["event_id"])
core.demo.register_transforms(ctx)
orders_raw = core.datasets.upload_csv("raw.erp_orders", "examples/supply-chain-demo/data/orders.csv", ctx=ctx)
customers_raw = core.datasets.upload_csv("raw.crm_customers", "examples/supply-chain-demo/data/customers.csv", ctx=ctx)
clean_orders = core.transforms.run("clean_orders", ctx=ctx)
clean_customers = core.transforms.run("clean_customers", ctx=ctx)
ontology = core.ontology.apply("examples/supply-chain-demo/ontology/order-customer.yaml", ctx=ctx)
order_index = core.objects.reindex("Order", ctx=ctx)
customer_index = core.objects.reindex("Customer", ctx=ctx)

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
print(
    json.dumps(
        {
            "rawOrdersVersion": orders_raw.version_id,
            "rawCustomersVersion": customers_raw.version_id,
            "cleanOrdersVersion": clean_orders.version_id,
            "cleanCustomersVersion": clean_customers.version_id,
            "ontology": ontology,
            "orderIndex": order_index,
            "customerIndex": customer_index,
        },
        sort_keys=True,
    )
)
PY
uv run python -m foundry_lite_api.main
