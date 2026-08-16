"""Row-level (instance) security: rowPolicies declared per object type in ontology YAML.

Covers the fail-closed semantics end to end through public facades: policy-scoped
query/search/aggregate, hidden get_object/link/action targets behaving exactly like
missing ones, object set materialization, subscription snapshots, apply-time
validation, and snapshot/rollback round-trips.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
import yaml
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.domain.errors import NotFound, ValidationFailed
from foundry_lite.infrastructure.adapters import LocalEmbeddingAdapter
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies

ORDERS_CSV = (
    "order_id,customer_id,region,status,amount,scheduled_at\n"
    "O-1,C-100,NA,PENDING,100.0,2026-01-01T01:00:00+02:00\n"
    "O-2,C-100,EU,PENDING,200.0,2025-12-31T23:30:00Z\n"
    "O-3,C-101,NA,REVIEW,300.0,2026-01-01T00:00:00Z\n"
)
CUSTOMERS_CSV = "customer_id,name\nC-100,Acme\nC-101,Globex\n"

NA_FILTER = {"property": "region", "op": "eq", "value": "NA"}
NA_POLICY: list[dict[str, object]] = [{"role": "ops_manager", "filter": NA_FILTER}]


def _ops_ctx() -> RequestContext:
    return RequestContext(actor_user_id="ops-user", roles=("ops_manager",))


def _viewer_ctx() -> RequestContext:
    return RequestContext(actor_user_id="viewer-user", roles=("viewer",))


def _order_type(row_policies: list[dict[str, object]] | None) -> dict[str, object]:
    order: dict[str, object] = {
        "apiName": "Order",
        "primaryKey": "orderId",
        "backing": {"dataset": "clean.rp_orders", "mode": "snapshot", "primaryKeyColumns": ["order_id"]},
        "properties": [
            {"apiName": "orderId", "column": "order_id", "type": "string", "nullable": False, "indexed": True},
            {"apiName": "customerId", "column": "customer_id", "type": "string", "indexed": True},
            {"apiName": "region", "column": "region", "type": "string", "indexed": True},
            {"apiName": "status", "column": "status", "type": "string", "searchable": True, "editable": True},
            {"apiName": "amount", "column": "amount", "type": "float"},
            {"apiName": "scheduledAt", "column": "scheduled_at", "type": "timestamp"},
            {"apiName": "note", "type": "string", "source": "edit_layer", "editable": True},
        ],
    }
    if row_policies is not None:
        order["rowPolicies"] = row_policies
    return order


def _definition(row_policies: list[dict[str, object]] | None = NA_POLICY) -> dict[str, object]:
    return {
        "objectTypes": [
            _order_type(row_policies),
            {
                "apiName": "Customer",
                "primaryKey": "customerId",
                "backing": {"dataset": "clean.rp_customers", "mode": "snapshot", "primaryKeyColumns": ["customer_id"]},
                "properties": [
                    {"apiName": "customerId", "column": "customer_id", "type": "string", "nullable": False},
                    {"apiName": "name", "column": "name", "type": "string"},
                ],
            },
        ],
        "linkTypes": [
            {
                "apiName": "RpOrderCustomer",
                "from": "Order",
                "to": "Customer",
                "cardinality": "many_to_one",
                "backing": {"dataset": "clean.rp_orders", "fromKey": "order_id", "toKey": "customer_id"},
            }
        ],
        "actionTypes": [
            {
                "apiName": "ApproveRpOrder",
                "target": "Order",
                "parameters": [{"apiName": "reason", "type": "string", "required": True}],
                "permissions": {"allowedRoles": ["ops_manager"]},
                "mutations": [{"type": "setProperty", "property": "status", "value": "APPROVED"}],
            }
        ],
    }


def _yaml_text(definition: dict[str, object]) -> str:
    return yaml.safe_dump(definition, sort_keys=False)


def _prepare_row_policy_demo(
    foundry: FoundryLite,
    tmp_path: Path,
    row_policies: list[dict[str, object]] | None = NA_POLICY,
) -> RequestContext:
    admin = demo_admin_context()
    (tmp_path / "rp_orders.csv").write_text(ORDERS_CSV, encoding="utf-8")
    (tmp_path / "rp_customers.csv").write_text(CUSTOMERS_CSV, encoding="utf-8")
    foundry.datasets.ensure("clean.rp_orders", ctx=admin, primary_key=["order_id"])
    foundry.datasets.upload_csv("clean.rp_orders", str(tmp_path / "rp_orders.csv"), ctx=admin)
    foundry.datasets.ensure("clean.rp_customers", ctx=admin, primary_key=["customer_id"])
    foundry.datasets.upload_csv("clean.rp_customers", str(tmp_path / "rp_customers.csv"), ctx=admin)
    foundry.ontology.apply_text(_yaml_text(_definition(row_policies)), ctx=admin)
    foundry.objects.reindex("Order", ctx=admin)
    foundry.objects.reindex("Customer", ctx=admin)
    return admin


def _order_ids(foundry: FoundryLite, ctx: RequestContext, **query: object) -> list[str]:
    result = foundry.objects.query("Order", ctx=ctx, **query)  # type: ignore[arg-type]
    return sorted(item["objectId"] for item in result["items"])


def test_query_scopes_rows_to_matching_policy_roles(foundry: FoundryLite, tmp_path: Path) -> None:
    admin = _prepare_row_policy_demo(foundry, tmp_path)

    assert _order_ids(foundry, _ops_ctx()) == ["O-1", "O-3"]
    assert _order_ids(foundry, _viewer_ctx()) == []
    assert _order_ids(foundry, admin) == ["O-1", "O-2", "O-3"]


def test_query_user_filter_is_intersected_with_policy_filter(foundry: FoundryLite, tmp_path: Path) -> None:
    _prepare_row_policy_demo(foundry, tmp_path)

    pending = {"property": "status", "op": "eq", "value": "PENDING"}
    assert _order_ids(foundry, _ops_ctx(), filter_ast=pending) == ["O-1"]
    # A caller filter cannot widen visibility beyond the policy scope.
    eu_probe = {"property": "region", "op": "eq", "value": "EU"}
    assert _order_ids(foundry, _ops_ctx(), filter_ast=eu_probe) == []


def test_multiple_matching_policies_union_their_filters(foundry: FoundryLite, tmp_path: Path) -> None:
    policies = NA_POLICY + [{"role": "finance", "filter": {"property": "region", "op": "eq", "value": "EU"}}]
    _prepare_row_policy_demo(foundry, tmp_path, row_policies=policies)

    finance_only = RequestContext(actor_user_id="fin-user", roles=("finance",))
    both = RequestContext(actor_user_id="fin-ops-user", roles=("ops_manager", "finance"))
    assert _order_ids(foundry, finance_only) == ["O-2"]
    assert _order_ids(foundry, both) == ["O-1", "O-2", "O-3"]


def test_keyword_search_respects_row_policies(tmp_path: Path) -> None:
    # Keyword-only deployment: the composed runtime would wire real fastembed,
    # which needs a model download this test must not depend on.
    dependencies = replace(
        create_local_core_dependencies(storage_root=tmp_path / "flite"),
        embedding_model_adapter=LocalEmbeddingAdapter(),
    )
    foundry = FoundryLite(dependencies=dependencies)
    admin = _prepare_row_policy_demo(foundry, tmp_path)
    foundry.objects.rebuild_search("Order", ctx=admin)

    admin_hits = _order_ids(foundry, admin, search_text="PENDING")
    ops_hits = _order_ids(foundry, _ops_ctx(), search_text="PENDING")
    viewer_hits = _order_ids(foundry, _viewer_ctx(), search_text="PENDING")

    assert admin_hits == ["O-1", "O-2"]
    assert ops_hits == ["O-1"]
    assert viewer_hits == []


def test_aggregation_respects_row_policies(foundry: FoundryLite, tmp_path: Path) -> None:
    admin = _prepare_row_policy_demo(foundry, tmp_path)
    select = [{"function": "count"}, {"function": "sum", "property": "amount"}]

    ops_result = foundry.objects.aggregate("Order", ctx=_ops_ctx(), select=select)
    viewer_result = foundry.objects.aggregate("Order", ctx=_viewer_ctx(), select=select)
    admin_result = foundry.objects.aggregate("Order", ctx=admin, select=select)

    assert ops_result["groups"][0]["metrics"] == {"count": 2, "sum_amount": 400.0}
    # Zero visibility yields zero groups, not a leaked global count.
    assert viewer_result == {"groups": [], "totalGroups": 0}
    assert admin_result["groups"][0]["metrics"] == {"count": 3, "sum_amount": 600.0}


def test_get_object_hides_policy_filtered_rows_as_not_found(foundry: FoundryLite, tmp_path: Path) -> None:
    admin = _prepare_row_policy_demo(foundry, tmp_path)

    assert foundry.objects.get("Order", "O-1", ctx=_ops_ctx())["objectId"] == "O-1"
    assert foundry.objects.get("Order", "O-2", ctx=admin)["objectId"] == "O-2"
    with pytest.raises(NotFound) as hidden:
        foundry.objects.get("Order", "O-2", ctx=_ops_ctx())
    with pytest.raises(NotFound) as missing:
        foundry.objects.get("Order", "O-404", ctx=_ops_ctx())
    # Hidden rows are indistinguishable from missing rows (no existence leak).
    assert str(hidden.value) == str(missing.value)


def test_timestamp_row_policy_matches_query_and_single_object_visibility(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    policy = [
        {
            "role": "ops_manager",
            "filter": {
                "property": "scheduledAt",
                "op": "gte",
                "value": "2025-12-31T23:30:00Z",
            },
        }
    ]
    _prepare_row_policy_demo(foundry, tmp_path, row_policies=policy)

    assert _order_ids(foundry, _ops_ctx()) == ["O-2", "O-3"]
    assert foundry.objects.get("Order", "O-2", ctx=_ops_ctx())["objectId"] == "O-2"
    with pytest.raises(NotFound):
        foundry.objects.get("Order", "O-1", ctx=_ops_ctx())


def test_links_filter_hidden_targets_and_hidden_sources(foundry: FoundryLite, tmp_path: Path) -> None:
    admin = _prepare_row_policy_demo(foundry, tmp_path)

    admin_links = foundry.objects.links("Customer", "C-100", "RpOrderCustomer", ctx=admin)
    ops_links = foundry.objects.links("Customer", "C-100", "RpOrderCustomer", ctx=_ops_ctx())
    assert sorted(link["to"]["objectId"] for link in admin_links) == ["O-1", "O-2"]
    # The hidden EU order is dropped entirely — not surfaced as a missing-target warning.
    assert [link["to"]["objectId"] for link in ops_links] == ["O-1"]

    # Traversal FROM a hidden object behaves like a nonexistent one: empty.
    assert foundry.objects.links("Order", "O-2", "RpOrderCustomer", ctx=_ops_ctx()) == []
    assert [link["to"]["objectId"] for link in foundry.objects.links("Order", "O-2", "RpOrderCustomer", ctx=admin)] == [
        "C-100"
    ]


def test_link_fanout_limit_applies_before_row_policy_filter(
    foundry: FoundryLite,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from foundry_lite.application.services.object_store import links as links_module

    eu_policy = [{"role": "ops_manager", "filter": {"property": "region", "op": "eq", "value": "EU"}}]
    _prepare_row_policy_demo(foundry, tmp_path, row_policies=eu_policy)
    monkeypatch.setattr(links_module, "MAX_LINK_FANOUT", 1)

    links = foundry.objects.links("Customer", "C-100", "RpOrderCustomer", ctx=_ops_ctx())

    assert links == []


def test_action_apply_on_hidden_target_is_not_found(foundry: FoundryLite, tmp_path: Path) -> None:
    admin = _prepare_row_policy_demo(foundry, tmp_path)
    hidden_version = foundry.objects.get("Order", "O-2", ctx=admin)["objectVersion"]

    with pytest.raises(NotFound, match="target object not found"):
        foundry.actions.apply(
            "ApproveRpOrder",
            object_type="Order",
            object_id="O-2",
            expected_object_version=hidden_version,
            params={"reason": "attempt on hidden row"},
            idempotency_key="rp-hidden-apply",
            ctx=_ops_ctx(),
        )

    visible_version = foundry.objects.get("Order", "O-1", ctx=_ops_ctx())["objectVersion"]
    response = foundry.actions.apply(
        "ApproveRpOrder",
        object_type="Order",
        object_id="O-1",
        expected_object_version=visible_version,
        params={"reason": "visible row"},
        idempotency_key="rp-visible-apply",
        ctx=_ops_ctx(),
    )
    assert response["status"] == "succeeded"
    assert foundry.objects.get("Order", "O-2", ctx=admin)["properties"]["status"] == "PENDING"


def test_action_validate_reports_hidden_target_exactly_like_missing(foundry: FoundryLite, tmp_path: Path) -> None:
    _prepare_row_policy_demo(foundry, tmp_path)

    hidden = foundry.actions.validate(
        "ApproveRpOrder",
        object_type="Order",
        object_id="O-2",
        expected_object_version=1,
        params={"reason": "probe"},
        ctx=_ops_ctx(),
    )
    missing = foundry.actions.validate(
        "ApproveRpOrder",
        object_type="Order",
        object_id="O-404",
        expected_object_version=1,
        params={"reason": "probe"},
        ctx=_ops_ctx(),
    )
    assert hidden["result"] == "INVALID"
    # Byte-identical payloads: validation cannot be used to probe hidden rows.
    assert hidden == missing


def test_action_batch_apply_rejects_hidden_target_as_not_found(foundry: FoundryLite, tmp_path: Path) -> None:
    admin = _prepare_row_policy_demo(foundry, tmp_path)
    versions = {
        object_id: foundry.objects.get("Order", object_id, ctx=admin)["objectVersion"] for object_id in ("O-1", "O-2")
    }

    with pytest.raises(ValidationFailed, match="batch action apply rejected") as exc_info:
        foundry.actions.apply_batch(
            "ApproveRpOrder",
            object_type="Order",
            targets=[
                {"object_id": "O-1", "expected_object_version": versions["O-1"]},
                {"object_id": "O-2", "expected_object_version": versions["O-2"]},
            ],
            params={"reason": "batch with hidden row"},
            idempotency_key="rp-hidden-batch",
            ctx=_ops_ctx(),
        )

    failures = cast(list[dict[str, object]], exc_info.value.details["failures"])
    assert [(failure["objectId"], failure["code"]) for failure in failures] == [("O-2", "NOT_FOUND")]
    # All-or-nothing: the visible target stays untouched too.
    assert foundry.objects.get("Order", "O-1", ctx=admin)["properties"]["status"] == "PENDING"


def test_subscription_snapshot_respects_row_policies(foundry: FoundryLite, tmp_path: Path) -> None:
    _prepare_row_policy_demo(foundry, tmp_path)

    ops_snapshot = next(iter(foundry.objects.subscription_events("Order", ctx=_ops_ctx(), max_events=1)))
    viewer_snapshot = next(iter(foundry.objects.subscription_events("Order", ctx=_viewer_ctx(), max_events=1)))

    assert ops_snapshot["event"] == "snapshot"
    ops_items = cast(list[dict[str, object]], ops_snapshot["items"])
    assert sorted(str(item["objectId"]) for item in ops_items) == ["O-1", "O-3"]
    assert viewer_snapshot["items"] == []


def test_object_sets_materialize_only_visible_rows(foundry: FoundryLite, tmp_path: Path) -> None:
    admin = _prepare_row_policy_demo(foundry, tmp_path)
    static_set = foundry.objects.create_set(
        "All orders snapshot",
        "Order",
        set_type="static",
        object_ids=["O-1", "O-2"],
        access_scope="public",
        ctx=admin,
    )
    dynamic_set = foundry.objects.create_set(
        "Pending orders",
        "Order",
        set_type="dynamic",
        filter_ast={"property": "status", "op": "eq", "value": "PENDING"},
        access_scope="public",
        ctx=admin,
    )

    ops_static = foundry.objects.get_set(static_set["id"], ctx=_ops_ctx())
    ops_dynamic = foundry.objects.get_set(dynamic_set["id"], ctx=_ops_ctx())
    admin_static = foundry.objects.get_set(static_set["id"], ctx=admin)

    assert ops_static["objectIds"] == ["O-1"]
    assert [item["objectId"] for item in ops_static["items"]] == ["O-1"]
    assert ops_dynamic["objectIds"] == ["O-1"]
    assert admin_static["objectIds"] == ["O-1", "O-2"]


def test_apply_time_validation_rejects_bad_row_policy_declarations(foundry: FoundryLite, tmp_path: Path) -> None:
    admin = _prepare_row_policy_demo(foundry, tmp_path, row_policies=None)

    unknown_property: list[dict[str, object]] = [
        {"role": "ops_manager", "filter": {"property": "warehouse", "op": "eq", "value": "NA"}}
    ]
    with pytest.raises(ValidationFailed, match="row policy references missing property") as unknown_exc:
        foundry.ontology.validate(_yaml_text(_definition(unknown_property)), ctx=admin)
    assert unknown_exc.value.details == {"objectType": "Order", "role": "ops_manager", "property": "warehouse"}

    unsupported_op: list[dict[str, object]] = [
        {"role": "ops_manager", "filter": {"property": "region", "op": "matches", "value": "N.*"}}
    ]
    with pytest.raises(ValidationFailed, match="unsupported filter operation"):
        foundry.ontology.validate(_yaml_text(_definition(unsupported_op)), ctx=admin)

    edit_layer_property: list[dict[str, object]] = [
        {"role": "ops_manager", "filter": {"property": "note", "op": "eq", "value": "vip"}}
    ]
    with pytest.raises(ValidationFailed, match="row policy property must be dataset-backed") as edit_exc:
        foundry.ontology.validate(_yaml_text(_definition(edit_layer_property)), ctx=admin)
    assert edit_exc.value.details == {"objectType": "Order", "role": "ops_manager", "property": "note"}


def test_rollback_restores_row_policy_declarations_and_enforcement(foundry: FoundryLite, tmp_path: Path) -> None:
    admin = _prepare_row_policy_demo(foundry, tmp_path)
    catalog = foundry.ontology.catalog(ctx=admin)
    order_type = next(item for item in catalog["objectTypes"] if item["apiName"] == "Order")
    assert order_type["rowPolicies"] == [{"role": "ops_manager", "filter": NA_FILTER}]

    foundry.ontology.apply_text(_yaml_text(_definition(row_policies=None)), ctx=admin)
    assert _order_ids(foundry, _ops_ctx()) == ["O-1", "O-2", "O-3"]

    result = foundry.ontology.rollback(1, ctx=admin)

    assert result["rolled_back_to_version_number"] == 1
    restored = foundry.ontology.catalog(ctx=admin)
    restored_order = next(item for item in restored["objectTypes"] if item["apiName"] == "Order")
    assert restored_order["rowPolicies"] == [{"role": "ops_manager", "filter": NA_FILTER}]
    assert _order_ids(foundry, _ops_ctx()) == ["O-1", "O-3"]
