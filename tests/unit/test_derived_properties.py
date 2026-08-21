"""Derived properties: a link traversal plus an aggregation, exposed as an ordinary property.

The demo ontology ships `Customer.approvedOrderCount` as a column the pipeline pre-computes.
That is the pattern derived properties replace: when the counting happens upstream, the number
goes stale the moment the links change and nobody notices, because the column still has a value.
A derived property counts at read time, so it cannot disagree with the links it summarizes.

Each test below pins one half of Palantir's contract — how the value is computed, and the fact
that filter/groupBy/orderBy/aggregate accept it with no new syntax, which is the entire reason
for pushing traversal into a property instead of into the query language.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.services import ontology_derived_property_validation as derived_property_validation
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed

from tests.conftest import DEMO_ROOT, demo_admin_context

_DERIVED_PROPERTIES = """
      - apiName: linkedOrderCount
        type: integer
        derivation: {link: CustomerOrders, aggregation: count}
      - apiName: linkedOrderTotal
        type: float
        derivation: {link: CustomerOrders, aggregation: sum, property: amount}
      - apiName: linkedOrderAverage
        type: float
        derivation: {link: CustomerOrders, aggregation: avg, property: amount}
      - apiName: linkedOrderSmallest
        type: float
        derivation: {link: CustomerOrders, aggregation: min, property: amount}
      - apiName: linkedOrderLargest
        type: float
        derivation: {link: CustomerOrders, aggregation: max, property: amount}
      - apiName: distinctOrderStatuses
        type: integer
        derivation: {link: CustomerOrders, aggregation: approximateCardinality, property: status}
"""

_DIRECT_LINK_DERIVED_PROPERTY = """
      - apiName: linkedCustomerName
        type: string
        derivation: {link: OrderCustomer, property: name}
"""

DEMO_ONTOLOGY = (
    Path(__file__).resolve().parents[2] / "examples" / "supply-chain-demo" / "ontology" / "order-customer.yaml"
)


def _customer_ontology(
    tmp_path: Path,
    derived: str = _DERIVED_PROPERTIES,
    order_derived: str = "",
) -> Path:
    """The demo ontology, plus a link back from Customer to its Orders and derived summaries.

    Reusing the demo document verbatim matters: redefining an existing object type's backing is a
    blocked migration, and the point here is that adding a derived property is *not* one — it
    changes how a value is read, never where the rows come from.
    """
    source = DEMO_ONTOLOGY.read_text()
    anchor = """      - apiName: approvedOrderCount
        column: approved_order_count
        type: integer
        indexed: true
"""
    assert anchor in source, "demo ontology no longer has the pre-computed Customer count"
    document = source.replace(anchor, anchor + derived.strip("\n") + "\n")
    order_anchor = """      - apiName: operatorNote
        type: string
        searchable: true
        editable: true
        source: edit_layer
        editPolicy: edit_only
"""
    assert order_anchor in document, "demo ontology no longer has the Order edit-layer property"
    document = document.replace(order_anchor, order_anchor + order_derived.strip("\n") + "\n")
    document = document.replace(
        """linkTypes:
  - apiName: OrderCustomer""",
        """linkTypes:
  - apiName: CustomerOrders
    displayName: Customer placed Orders
    from: Customer
    to: Order
    cardinality: many_to_many
    backing:
      dataset: clean.orders
      fromKey: customer_id
      toKey: order_id
  - apiName: OrderCustomer""",
    )
    path = tmp_path / f"customer-derived-{abs(hash(derived)) % 10_000}.yaml"
    path.write_text(document)
    return path


def _prepare(
    foundry: FoundryLite,
    tmp_path: Path,
    derived: str = _DERIVED_PROPERTIES,
    order_derived: str = "",
) -> RequestContext:
    """Seed the demo data, then apply the derived-property ontology as the FIRST version.

    Applying it as a second version would leave the first version's object records servable under
    the same api names, so every query would return each object twice and the comparisons below
    would drift for a reason unrelated to derived properties.
    """
    ctx = demo_admin_context()
    foundry.demo.seed_files()
    for dataset, key in (
        ("raw.erp_orders", "order_id"),
        ("raw.crm_customers", "customer_id"),
        ("clean.orders", "order_id"),
        ("clean.order_finance", "order_id"),
        ("clean.customers", "customer_id"),
        ("ops.action_log", "action_run_id"),
        ("ops.order_current", "orderId"),
    ):
        foundry.datasets.ensure(dataset, ctx=ctx, primary_key=[key])
    foundry.demo.register_transforms(ctx)
    foundry.datasets.upload_csv("raw.erp_orders", str(DEMO_ROOT / "data" / "orders.csv"), ctx=ctx)
    foundry.datasets.upload_csv("raw.crm_customers", str(DEMO_ROOT / "data" / "customers.csv"), ctx=ctx)
    for transform in ("clean_orders", "clean_order_finance", "clean_customers"):
        foundry.transforms.run(transform, ctx=ctx)
    foundry.ontology.apply(str(_customer_ontology(tmp_path, derived, order_derived)), ctx=ctx)
    foundry.objects.reindex("Order", ctx=ctx)
    foundry.objects.reindex("Customer", ctx=ctx)
    return ctx


def _orders_by_customer(foundry: FoundryLite, ctx: RequestContext) -> dict[str, list[dict[str, object]]]:
    """The truth the derived properties must reproduce, read straight off the Order objects."""
    grouped: dict[str, list[dict[str, object]]] = {}
    cursor: str | None = None
    while True:
        page = foundry.objects.query("Order", ctx=ctx, limit=200, cursor=cursor)
        for item in page["items"]:
            grouped.setdefault(str(item["properties"]["customerId"]), []).append(dict(item["properties"]))
        cursor = page["nextCursor"]
        if not cursor:
            break
    return grouped


def _customers(foundry: FoundryLite, ctx: RequestContext) -> list[dict[str, object]]:
    return [dict(item["properties"]) for item in foundry.objects.query("Customer", ctx=ctx, limit=200)["items"]]


def test_derived_properties_match_the_links_they_summarize(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = _prepare(foundry, tmp_path)
    expected = _orders_by_customer(foundry, ctx)

    customers = _customers(foundry, ctx)
    assert customers

    checked = 0
    for props in customers:
        orders = expected.get(str(props["customerId"]), [])
        amounts = [float(order["amount"]) for order in orders if order.get("amount") is not None]  # type: ignore[arg-type]
        assert props["linkedOrderCount"] == len(orders), props["customerId"]
        assert props["distinctOrderStatuses"] == len({order["status"] for order in orders})
        if amounts:
            assert props["linkedOrderTotal"] == pytest.approx(sum(amounts))
            assert props["linkedOrderAverage"] == pytest.approx(sum(amounts) / len(amounts))
            assert props["linkedOrderSmallest"] == pytest.approx(min(amounts))
            assert props["linkedOrderLargest"] == pytest.approx(max(amounts))
            checked += 1
    assert checked, "no customer had orders, so the numeric aggregations were never exercised"


def test_derived_properties_work_in_filter_group_by_and_order_by(foundry: FoundryLite, tmp_path: Path) -> None:
    """No new query syntax: a derived property is accepted everywhere a stored one is."""
    ctx = _prepare(foundry, tmp_path)
    expected = _orders_by_customer(foundry, ctx)
    with_orders = {customer_id for customer_id, orders in expected.items() if orders}

    filtered = foundry.objects.query(
        "Customer",
        ctx=ctx,
        filter_ast={"property": "linkedOrderCount", "op": "gt", "value": 0},
        limit=200,
    )["items"]
    assert {str(item["properties"]["customerId"]) for item in filtered} == with_orders

    ordered = foundry.objects.query(
        "Customer",
        ctx=ctx,
        order_by=[{"property": "linkedOrderCount", "direction": "desc"}],
        limit=200,
    )["items"]
    counts = [int(item["properties"]["linkedOrderCount"]) for item in ordered]  # type: ignore[arg-type]
    assert counts == sorted(counts, reverse=True)

    grouped = foundry.objects.aggregate(
        "Customer",
        ctx=ctx,
        group_by=["linkedOrderCount"],
        select=[{"function": "count", "name": "customers"}],
    )
    by_count = {group["key"]["linkedOrderCount"]: group["metrics"]["customers"] for group in grouped["groups"]}
    assert by_count[len(next(iter(expected[customer_id] for customer_id in with_orders)))] >= 1
    assert sum(by_count.values()) == len(expected) or sum(by_count.values()) == len(
        foundry.objects.query("Customer", ctx=ctx, limit=200)["items"]
    )


def test_derived_properties_are_aggregable_like_any_other_number(foundry: FoundryLite, tmp_path: Path) -> None:
    """`avg(linkedOrderCount)` is the question "how many orders does a customer typically have".

    It only works if the aggregate builds its metric over the traversal subquery rather than over
    a stored JSON field, which is the one place a derived property is not shaped like the others.
    """
    ctx = _prepare(foundry, tmp_path)
    expected = _orders_by_customer(foundry, ctx)
    customers = _customers(foundry, ctx)
    per_customer = [len(expected.get(str(props["customerId"]), [])) for props in customers]

    aggregated = foundry.objects.aggregate(
        "Customer",
        ctx=ctx,
        select=[
            {"function": "avg", "property": "linkedOrderCount", "name": "avgOrders"},
            {"function": "sum", "property": "linkedOrderCount", "name": "totalOrders"},
            {"function": "max", "property": "linkedOrderCount", "name": "mostOrders"},
        ],
    )
    metrics = aggregated["groups"][0]["metrics"]
    assert metrics["totalOrders"] == pytest.approx(sum(per_customer))
    assert metrics["avgOrders"] == pytest.approx(sum(per_customer) / len(per_customer))
    assert metrics["mostOrders"] == pytest.approx(max(per_customer))


def test_single_link_derived_property_reads_filters_and_sorts_the_linked_scalar(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    ctx = _prepare(foundry, tmp_path, order_derived=_DIRECT_LINK_DERIVED_PROPERTY)
    customers = {props["customerId"]: props["name"] for props in _customers(foundry, ctx)}

    orders = foundry.objects.query("Order", ctx=ctx, limit=200)["items"]
    assert orders
    assert all(
        item["properties"]["linkedCustomerName"] == customers[item["properties"]["customerId"]] for item in orders
    )

    customer_name = next(iter(customers.values()))
    filtered = foundry.objects.query(
        "Order",
        ctx=ctx,
        filter_ast={"property": "linkedCustomerName", "op": "eq", "value": customer_name},
        limit=200,
    )["items"]
    assert filtered
    assert all(item["properties"]["linkedCustomerName"] == customer_name for item in filtered)

    ordered = foundry.objects.query(
        "Order",
        ctx=ctx,
        order_by=[{"property": "linkedCustomerName", "direction": "asc"}],
        limit=200,
    )["items"]
    names = [str(item["properties"]["linkedCustomerName"]) for item in ordered]
    assert names == sorted(names)


def test_derived_property_without_an_aggregation_is_rejected(foundry: FoundryLite, tmp_path: Path) -> None:
    """Palantir's rule: a link that can reach many objects has no single value to hold."""
    derived = """
      - apiName: linkedOrderCount
        type: integer
        derivation: {link: CustomerOrders}
"""
    with pytest.raises(ValidationFailed) as excinfo:
        _prepare(foundry, tmp_path, derived)
    assert "aggregation" in str(excinfo.value).lower()


def test_derived_property_counting_does_not_take_a_target_property(foundry: FoundryLite, tmp_path: Path) -> None:
    """`count` counts linked objects; naming a property would mean something the value is not."""
    derived = """
      - apiName: linkedOrderCount
        type: integer
        derivation: {link: CustomerOrders, aggregation: count, property: amount}
"""
    with pytest.raises(ValidationFailed):
        _prepare(foundry, tmp_path, derived)


@pytest.mark.parametrize(
    "derived",
    [
        """
      - apiName: linkedOrderCount
        type: integer
        derivation: {expression: orders.count, link: CustomerOrders, aggregation: count}
""",
        """
      - apiName: linkedOrderCount
        type: integer
        derivation: {aggregation: count}
""",
        """
      - apiName: linkedOrderCount
        type: string
        derivation: {link: CustomerOrders, aggregation: count}
""",
        """
      - apiName: linkedOrderCount
        type: integer
        editable: true
        derivation: {link: CustomerOrders, aggregation: count}
""",
        """
      - apiName: linkedOrderCount
        type: integer
        derivation: {expression: ""}
""",
    ],
)
def test_derived_property_rejects_ambiguous_or_incompatible_declarations(
    foundry: FoundryLite,
    tmp_path: Path,
    derived: str,
) -> None:
    with pytest.raises(ValidationFailed):
        _prepare(foundry, tmp_path, derived)


def test_derived_property_numeric_aggregation_needs_a_numeric_target(foundry: FoundryLite, tmp_path: Path) -> None:
    derived = """
      - apiName: linkedOrderTotal
        type: float
        derivation: {link: CustomerOrders, aggregation: sum, property: status}
"""
    with pytest.raises(ValidationFailed):
        _prepare(foundry, tmp_path, derived)


def test_derived_property_must_start_at_its_own_object_type(foundry: FoundryLite, tmp_path: Path) -> None:
    """A link whose `from` is another type cannot be followed off this one."""
    derived = """
      - apiName: linkedOrderCount
        type: integer
        derivation: {link: OrderCustomer, aggregation: count}
"""
    with pytest.raises(ValidationFailed):
        _prepare(foundry, tmp_path, derived)


def test_derived_property_cannot_silently_chain_through_another_link_derived_property(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    """One-hop declarations must not return NULL just because their target is also runtime-derived."""
    derived = """
      - apiName: linkedCustomerOrderCount
        type: integer
        derivation: {link: OrderCustomer, property: linkedOrderCount}
"""
    with pytest.raises(ValidationFailed) as excinfo:
        _prepare(foundry, tmp_path, order_derived=derived)
    assert "another link-derived" in str(excinfo.value)


def test_link_derived_property_validation_rule_is_directly_executable() -> None:
    """The direct rule keeps Palantir's many-link aggregation requirement unit-testable."""
    customer = {
        "apiName": "Customer",
        "primaryKey": "id",
        "properties": [
            {"apiName": "id", "type": "string"},
            {
                "apiName": "linkedOrderCount",
                "type": "integer",
                "derivation": {"link": "CustomerOrders", "aggregation": "count"},
            },
        ],
    }
    order = {"apiName": "Order", "primaryKey": "id", "properties": [{"apiName": "id", "type": "string"}]}
    links = {
        "CustomerOrders": {
            "apiName": "CustomerOrders",
            "from": "Customer",
            "to": "Order",
            "cardinality": "many_to_many",
        }
    }

    derived_property_validation.validate_derived_properties(customer, {"Customer": customer, "Order": order}, links)
