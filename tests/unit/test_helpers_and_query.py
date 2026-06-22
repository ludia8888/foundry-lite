from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from foundry_lite.application.core_services import CoreServices
from foundry_lite.application.foundry import (
    FoundryLite,
    _dataset_ref_parts,
    _json_ready,
    _normalize_duckdb_type,
    _required_row,
)
from foundry_lite.application.safe_expression import evaluate_safe_expression
from foundry_lite.application.upload_limits import (
    DEFAULT_MAX_WEBHOOK_BODY_BYTES,
    WEBHOOK_BODY_LIMIT_ENV,
    max_webhook_body_bytes,
)
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.domain.errors import (
    ConflictDetected,
    InvariantViolation,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.adapters import DuckDBComputeAdapter
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from foundry_lite.observability.logging import configure_logging, log_event
from foundry_lite.transforms_sdk import Input, Output, transform
from sqlalchemy import select

from tests.conftest import prepare_indexed_demo


# Local DEMO_ROOT so each test module is self-contained and mutmut-friendly.
# Same heuristic as tests/conftest.py.
def _demo_root() -> Path:
    override = os.environ.get("FOUNDRY_LITE_REPO_ROOT")
    base = Path(override) if override else Path(__file__).resolve().parents[2]
    if base.name == "mutants":
        base = base.parent
    return base / "examples" / "supply-chain-demo"


DEMO_ROOT = _demo_root()


class ExplodingCsvComputeAdapter(DuckDBComputeAdapter):
    def csv_to_parquet(self, source_path: Path, target_path: Path) -> None:
        raise RuntimeError("duckdb exploded")


def test_helper_normalization_and_dataset_ref_validation() -> None:
    assert _json_ready({"a": Decimal("1"), "b": (Decimal("1.5"),)}) == {"a": 1, "b": [1.5]}
    assert _normalize_duckdb_type("BOOLEAN") == "boolean"
    assert _normalize_duckdb_type("TIMESTAMP") == "timestamp"
    assert _dataset_ref_parts("raw.orders") == ("raw", "orders")
    assert _required_row(("ok",), "unit query")[0] == "ok"
    with pytest.raises(InvariantViolation):
        _required_row(None, "unit query")
    with pytest.raises(ValidationFailed):
        _dataset_ref_parts("orders")
    with pytest.raises(ValidationFailed):
        _dataset_ref_parts(".orders")


def test_query_objects_filter_sort_cursor_and_invalid_op(foundry: FoundryLite) -> None:
    prepare_indexed_demo(foundry)
    filter_ast = {
        "and": [
            {"property": "status", "op": "in", "value": ["PENDING", "REVIEW"]},
            {"property": "amount", "op": "gte", "value": 700},
        ]
    }
    order_by = [{"property": "amount", "direction": "desc"}]
    first_page = foundry.objects.query(
        "Order",
        filter_ast=filter_ast,
        order_by=order_by,
        limit=1,
    )

    assert first_page["items"][0]["objectId"] == "O-1001"
    next_cursor = first_page["nextCursor"]
    assert next_cursor is not None

    second_page = foundry.objects.query("Order", filter_ast=filter_ast, order_by=order_by, cursor=next_cursor)
    assert [item["objectId"] for item in second_page["items"]] == ["O-1002"]

    with pytest.raises(ValidationFailed):
        foundry.objects.query("Order", cursor=next_cursor)
    with pytest.raises(ValidationFailed):
        foundry.objects.query("Order", cursor="O-1001")
    with pytest.raises(ValidationFailed):
        foundry.objects.query("Order", filter_ast=filter_ast, order_by=order_by, cursor=_tampered_cursor(next_cursor))
    with pytest.raises(ValidationFailed):
        foundry.objects.query("Order", filter_ast=filter_ast, order_by=order_by, cursor="oqc1.not-valid-base64")
    with pytest.raises(ValidationFailed):
        foundry.objects.query("Order", limit=501)
    with pytest.raises(ValidationFailed, match="missing property"):
        foundry.objects.query("Order", filter_ast={"property": "missing", "op": "eq", "value": "PENDING"})
    with pytest.raises(ValidationFailed, match="missing property"):
        foundry.objects.query("Order", order_by=[{"property": "missing", "direction": "asc"}])

    contains_page = foundry.objects.query(
        "Order",
        filter_ast={"property": "customerId", "op": "contains", "value": "C-10"},
    )
    assert contains_page["items"]

    review_or_small = foundry.objects.query(
        "Order",
        filter_ast={
            "or": [
                {"property": "status", "op": "eq", "value": "REVIEW"},
                {"property": "amount", "op": "lte", "value": 300.0},
            ]
        },
    )
    assert {item["objectId"] for item in review_or_small["items"]} == {"O-1002", "O-1003"}

    with pytest.raises(ValidationFailed):
        foundry.objects.query("Order", filter_ast={"property": "status", "op": "bad", "value": "PENDING"})


def _tampered_cursor(cursor: str) -> str:
    prefix = "oqc1."
    encoded = cursor.removeprefix(prefix)
    padded = encoded + "=" * (-len(encoded) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    payload["objectId"] = "O-0000"
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return prefix + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def test_dataset_not_found_duplicate_reset_preview_and_transform_update(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    ctx = demo_admin_context()
    foundry.demo.seed_files()
    with pytest.raises(NotFound):
        foundry.datasets.get("raw.missing")
    with pytest.raises(NotFound):
        foundry.datasets.preview("raw.missing")

    foundry.datasets.ensure("raw.crm_customers", ctx=ctx, primary_key=["customer_id"])
    assert foundry.datasets.ensure("raw.crm_customers", ctx=ctx, primary_key=["customer_id"])["name"] == "crm_customers"
    with pytest.raises(ConflictDetected):
        foundry.datasets.create("raw.crm_customers", ctx=ctx, primary_key=["customer_id"])
    with pytest.raises(NotFound):
        foundry.datasets.upload_csv("raw.crm_customers", tmp_path / "missing.csv", ctx=ctx)
    foundry.datasets.ensure("clean.customers", ctx=ctx, primary_key=["customer_id"])
    committed = foundry.datasets.upload_csv("raw.crm_customers", str(DEMO_ROOT / "data" / "customers.csv"), ctx=ctx)
    assert foundry.datasets.preview("raw.crm_customers", ctx=ctx, version=committed.version_id)
    assert foundry.datasets.inspect("raw.crm_customers", ctx=ctx, version=committed.version_id)["manifest"]
    foundry.datasets.ensure("raw.other_customers", ctx=ctx, primary_key=["customer_id"])
    with pytest.raises(NotFound):
        foundry.datasets.preview("raw.other_customers", ctx=ctx, version=committed.version_id)
    with pytest.raises(NotFound):
        foundry.datasets.inspect("raw.other_customers", ctx=ctx, version=committed.version_id)
    sql_path = tmp_path / "noop.sql"
    sql_path.write_text("select * from {{ input('raw.crm_customers') }}", encoding="utf-8")
    created = foundry.transforms.register(
        "noop",
        entrypoint=sql_path,
        inputs={"customers": "raw.crm_customers"},
        output_dataset_ref="clean.customers",
        ctx=ctx,
    )
    updated = foundry.transforms.register(
        "noop",
        entrypoint=sql_path,
        inputs={"customers": "raw.crm_customers"},
        output_dataset_ref="clean.customers",
        ctx=ctx,
    )
    assert updated["id"] == created["id"]
    audit_event_types = {event["event_type"] for event in foundry.operations.list_runs(ctx=ctx)["auditEvents"]}
    assert "transform.definition.created" in audit_event_types
    assert "transform.definition.updated" in audit_event_types
    with pytest.raises(ValidationFailed):
        foundry.reset()
    foundry.reset(confirm_dev=True)
    assert foundry.datasets.find("raw.crm_customers") is None


def test_permission_deny_on_dataset_write_is_audited(foundry: FoundryLite) -> None:
    viewer = RequestContext(actor_user_id="viewer-1", roles=("viewer",))
    with pytest.raises(PermissionDenied):
        foundry.datasets.create("raw.denied", ctx=viewer)
    audit_events = foundry.operations.list_runs(ctx=demo_admin_context())["auditEvents"]
    assert any(event["decision"] == "deny" for event in audit_events)


def test_permission_deny_on_dataset_read_does_not_write_audit(foundry: FoundryLite) -> None:
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.read_boundary", ctx=ctx, primary_key=["id"])
    audit_count_before = len(foundry.operations.list_runs(ctx=ctx)["auditEvents"])
    blocked_reader = RequestContext(actor_user_id="blocked-reader", roles=())

    with pytest.raises(PermissionDenied):
        foundry.datasets.get("raw.read_boundary", ctx=blocked_reader)

    assert len(foundry.operations.list_runs(ctx=ctx)["auditEvents"]) == audit_count_before


def test_csv_upload_wraps_unexpected_internal_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = demo_admin_context()
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "exploding-foundry")
    exploding_foundry = FoundryLite(dependencies=replace(dependencies, compute_adapter=ExplodingCsvComputeAdapter()))
    exploding_foundry.datasets.ensure("raw.wraps_error", ctx=ctx, primary_key=["id"])
    csv_path = tmp_path / "rows.csv"
    csv_path.write_text("id,value\nA,1\n", encoding="utf-8")

    with pytest.raises(ValidationFailed, match="csv upload failed"):
        exploding_foundry.datasets.upload_csv("raw.wraps_error", csv_path, ctx=ctx)

    normal_foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "normal-foundry"))
    normal_foundry.datasets.ensure("raw.wraps_error", ctx=ctx, primary_key=["id"])
    monkeypatch.setenv("FOUNDRY_LITE_MAX_CSV_UPLOAD_BYTES", "4")
    with pytest.raises(ValidationFailed, match="size limit"):
        normal_foundry.datasets.upload_csv("raw.wraps_error", csv_path, ctx=ctx)


def test_webhook_body_limit_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(WEBHOOK_BODY_LIMIT_ENV, raising=False)
    assert max_webhook_body_bytes() == DEFAULT_MAX_WEBHOOK_BODY_BYTES
    monkeypatch.setenv(WEBHOOK_BODY_LIMIT_ENV, "7")
    assert max_webhook_body_bytes() == 7
    monkeypatch.setenv(WEBHOOK_BODY_LIMIT_ENV, "0")
    with pytest.raises(ValidationFailed, match="must be positive"):
        max_webhook_body_bytes()
    monkeypatch.setenv(WEBHOOK_BODY_LIMIT_ENV, "not-an-integer")
    with pytest.raises(ValidationFailed, match="must be an integer"):
        max_webhook_body_bytes()


def test_action_validation_not_found_and_precondition_paths(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)
    with pytest.raises(ValidationFailed):
        foundry.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=1,
            params={"reason": "Inventory confirmed"},
            idempotency_key="",
            ctx=ctx,
        )
    with pytest.raises(NotFound):
        foundry.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-404",
            expected_object_version=1,
            params={"reason": "Inventory confirmed"},
            idempotency_key="missing-object",
            ctx=ctx,
        )
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    with pytest.raises(ValidationFailed):
        foundry.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={},
            idempotency_key="missing-param",
            ctx=ctx,
        )
    approved = foundry.objects.get("Order", "O-1003", ctx=ctx)
    with pytest.raises(ValidationFailed):
        foundry.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1003",
            expected_object_version=approved["objectVersion"],
            params={"reason": "Already approved"},
            idempotency_key="precondition-false",
            ctx=ctx,
        )
    assert evaluate_safe_expression("object.status == 'PENDING'", {"status": "PENDING"}) is True
    with pytest.raises(ValidationFailed):
        evaluate_safe_expression("object.status matches 'PENDING'", {"status": "PENDING"})


def test_ontology_and_materialization_error_paths(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = demo_admin_context()
    non_mapping_yaml = tmp_path / "list.yaml"
    non_mapping_yaml.write_text("- bad\n", encoding="utf-8")
    with pytest.raises(ValidationFailed):
        foundry.ontology.apply(non_mapping_yaml, ctx=ctx)
    with pytest.raises(NotFound):
        foundry.materialization.run("missing", ctx=ctx)
    with pytest.raises(NotFound):
        foundry.objects.get("Order", "missing")


def test_dataset_schema_drift_not_null_and_empty_file_failures(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.schema_drift", ctx=ctx, primary_key=["id"])
    first = tmp_path / "first.csv"
    first.write_text("id,value\nA,1\n", encoding="utf-8")
    foundry.datasets.upload_csv("raw.schema_drift", first, ctx=ctx)

    missing_column = tmp_path / "missing_column.csv"
    missing_column.write_text("id\nA\n", encoding="utf-8")
    with pytest.raises(ValidationFailed):
        foundry.datasets.upload_csv("raw.schema_drift", missing_column, ctx=ctx)

    foundry.datasets.ensure("raw.null_pk", ctx=ctx, primary_key=["id"])
    null_pk = tmp_path / "null_pk.csv"
    null_pk.write_text("id,value\n,1\n", encoding="utf-8")
    with pytest.raises(ValidationFailed):
        foundry.datasets.upload_csv("raw.null_pk", null_pk, ctx=ctx)

    foundry.datasets.ensure("raw.empty", ctx=ctx, primary_key=["id"])
    empty = tmp_path / "empty.csv"
    empty.write_text("id,value\n", encoding="utf-8")
    with pytest.raises(ValidationFailed):
        foundry.datasets.upload_csv("raw.empty", empty, ctx=ctx)


def test_transform_and_private_guard_failures(tmp_path: Path) -> None:
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "service-edge-foundry")
    foundry = FoundryLite(dependencies=dependencies)
    services = CoreServices.create(dependencies)
    ctx = demo_admin_context()
    foundry.demo.seed_files()
    foundry.datasets.ensure("raw.crm_customers", ctx=ctx, primary_key=["customer_id"])
    foundry.datasets.ensure("clean.customers", ctx=ctx, primary_key=["customer_id"])
    commit = foundry.datasets.upload_csv("raw.crm_customers", str(DEMO_ROOT / "data" / "customers.csv"), ctx=ctx)

    with pytest.raises(NotFound):
        foundry.transforms.run("missing", ctx=ctx)

    bad_sql = tmp_path / "bad.sql"
    bad_sql.write_text("select * from definitely_missing_table", encoding="utf-8")
    foundry.transforms.register(
        "bad_sql",
        entrypoint=bad_sql,
        inputs={"customers": "raw.crm_customers"},
        output_dataset_ref="clean.customers",
        ctx=ctx,
    )
    with pytest.raises(ValidationFailed):
        foundry.transforms.run("bad_sql", ctx=ctx)

    dataset_transactions = services.dataset.transaction
    dataset_versions = services.dataset.version
    dataset_quality = services.dataset.quality
    dataset_ingest = services.dataset.ingest
    runtime = services.runtime

    with foundry.engine.begin() as conn:
        with pytest.raises(NotFound):
            dataset_transactions._require_open_transaction(conn, "missing")
        with pytest.raises(ConflictDetected):
            dataset_transactions._require_open_transaction(conn, commit.transaction_id)
        dataset = foundry.datasets.get("raw.crm_customers", ctx=ctx)
        with pytest.raises(ValidationFailed):
            dataset_transactions._open_dataset_transaction(conn, ctx, dataset, "UPDATE")
        runtime._outbox(
            conn,
            ctx,
            "test.event",
            "test",
            "1",
            {"ok": True},
            idempotency_key="same",
            correlation_id="same",
        )
        runtime._outbox(
            conn,
            ctx,
            "test.event",
            "test",
            "1",
            {"ok": True},
            idempotency_key="same",
            correlation_id="same",
        )
        with pytest.raises(NotFound):
            dataset_versions._latest_version_by_dataset_id(conn, "missing")
        assert dataset_versions._latest_version_by_dataset_id(conn, "missing", allow_missing=True) is None
        version_path = dataset_transactions._version_file_path(dataset_versions._get_version_by_id(commit.version_id))
        with pytest.raises(ValidationFailed, match="unsupported dataset quality check type"):
            dataset_quality._execute_check(version_path, 1, {"type": "custom"})

    with pytest.raises(NotFound):
        dataset_versions._schema_for_version("missing", 1)
    with pytest.raises(NotFound):
        dataset_versions._get_version("missing", "latest", ctx=ctx)
    with pytest.raises(NotFound):
        dataset_versions._get_version("missing", "dsv_missing", ctx=ctx)
    with pytest.raises(NotFound):
        dataset_versions._get_version_by_id("dsv_missing")
    with pytest.raises(ValidationFailed):
        dataset_ingest._csv_to_parquet(tmp_path / "bad.csv", tmp_path / "bad.parquet")


def test_ontology_import_validation_edges_and_missing_active_types(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    ctx = demo_admin_context()
    with pytest.raises(NotFound):
        foundry.objects.reindex("Order", ctx=ctx)

    ctx = prepare_indexed_demo(foundry)
    viewer = RequestContext(tenant_id=ctx.tenant_id, actor_user_id="viewer-user", roles=("viewer",))
    with pytest.raises(PermissionDenied):
        foundry.objects.reindex("Order", ctx=viewer)
    with pytest.raises(NotFound):
        foundry.objects.reindex("MissingType", ctx=ctx)
    with pytest.raises(NotFound):
        foundry.actions.apply(
            "MissingAction",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=1,
            params={"reason": "x"},
            idempotency_key="missing-action",
            ctx=ctx,
        )

    duplicate_property = tmp_path / "duplicate.yaml"
    duplicate_property.write_text(
        """
objectTypes:
  - apiName: Order
    displayName: Order
    primaryKey: orderId
    backing:
      dataset: clean.orders
      mode: snapshot
      primaryKeyColumns: [order_id]
    properties:
      - apiName: orderId
        column: order_id
        type: string
        nullable: false
      - apiName: orderId
        column: order_id
        type: string
""".strip(),
        encoding="utf-8",
    )
    with pytest.raises(ValidationFailed):
        foundry.ontology.apply(duplicate_property, ctx=ctx)

    bad_link = tmp_path / "bad-link.yaml"
    bad_link.write_text(
        """
objectTypes: []
linkTypes:
  - apiName: BadLink
    from: Missing
    to: AlsoMissing
    backing:
      dataset: clean.orders
      fromKey: order_id
      toKey: customer_id
""".strip(),
        encoding="utf-8",
    )
    with pytest.raises(ValidationFailed):
        foundry.ontology.apply(bad_link, ctx=ctx)


def test_link_reports_missing_target_object(foundry: FoundryLite) -> None:
    prepare_indexed_demo(foundry)
    with foundry.engine.begin() as conn:
        conn.execute(
            db.object_records.delete().where(
                (db.object_records.c.object_type_api_name == "Customer") & (db.object_records.c.object_id == "C-100")
            )
        )
    links = foundry.objects.links("Order", "O-1001", "OrderCustomer")

    assert links[0]["to"]["objectType"] == "Customer"
    assert links[0]["to"]["objectId"] == "C-100"
    assert links[0]["to"]["targetMissing"] is True
    assert links[0]["warning"]["type"] == "link_target_missing"


def test_link_reverse_traverses_customer_to_orders(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)

    links = foundry.objects.links("Customer", "C-100", "OrderCustomer", ctx=ctx)

    assert {link["to"]["objectId"] for link in links} == {"O-1001", "O-1003"}
    assert {link["from"]["objectType"] for link in links} == {"Customer"}
    assert {link["from"]["objectId"] for link in links} == {"C-100"}
    assert {link["to"]["objectType"] for link in links} == {"Order"}
    assert {link["linkType"] for link in links} == {"OrderCustomer"}


def test_full_reindex_tombstones_links_missing_from_source_snapshot(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    ctx = prepare_indexed_demo(foundry)
    replacement = tmp_path / "clean_orders_without_o1003.csv"
    replacement.write_text(
        "\n".join(
            [
                "order_id,customer_id,source_status,amount,margin,order_ts,region,risk_score",
                "O-1001,C-100,PENDING,1200.0,230.0,2026-06-09T10:00:00Z,NA,0.8",
                "O-1002,C-101,REVIEW,800.0,80.0,2026-06-09T11:00:00Z,EU,0.3",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    foundry.datasets.upload_csv("clean.orders", str(replacement), ctx=ctx)

    foundry.objects.reindex("Order", ctx=ctx)

    links = foundry.objects.links("Customer", "C-100", "OrderCustomer", ctx=ctx)
    assert {link["to"]["objectId"] for link in links} == {"O-1001"}
    with foundry.engine.begin() as conn:
        stale_link = (
            conn.execute(
                select(db.object_links).where(
                    (db.object_links.c.from_object_id == "O-1003")
                    & (db.object_links.c.to_object_id == "C-100")
                )
            )
            .mappings()
            .one()
        )
    assert stale_link["deleted"] is True
    assert stale_link["deletion_reason"] == "source_missing"


def test_transforms_sdk_decorator_and_logging(caplog) -> None:
    @transform(orders=Input("raw.erp_orders"), out=Output("clean.orders"))
    def compute() -> None:
        return None

    configure_logging()
    with caplog.at_level(logging.INFO):
        log_event(logging.getLogger("foundry-lite-test"), "demo", request_id="req-test")
    with pytest.raises(ValueError):
        log_event(logging.getLogger("foundry-lite-test"), "missing_trace_key")
    assert compute._foundry_lite_transform_bindings["orders"].dataset_ref == "raw.erp_orders"
    assert "demo" in caplog.text
