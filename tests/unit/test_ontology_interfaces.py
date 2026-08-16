"""Ontology interfaces: shared property contracts, conformance, catalog, query, rollback.

Covers apply-time conformance validation (missing property, type mismatch,
indexed downgrade, loosened nullable — reported together), catalog and
snapshot round-trips, migration guard semantics (implements drops block,
additions warn), and the interface-scoped polymorphic query with rowPolicies
and masking composed per implementing type.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import yaml
from fastapi.testclient import TestClient
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.services.ontology_interface_validation import validate_ontology_interfaces
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.domain.errors import NotFound, ValidationFailed
from foundry_lite_api import runtime as api_runtime
from foundry_lite_api.main import app
from pytest import MonkeyPatch, raises

ORDERS_CSV = "order_id,region,status,risk_score\nO-1,NA,PENDING,0.9\nO-2,EU,PENDING,0.5\nO-3,NA,REVIEW,0.7\n"
CUSTOMERS_CSV = "customer_id,name,risk_score\nC-100,Acme,0.8\nC-101,Globex,0.2\n"

ADMIN_HEADERS = {
    "X-Tenant-ID": "tenant-demo",
    "X-User-ID": "user-demo-admin",
    "X-Roles": "admin,data_engineer,ops_manager",
}

NA_ORDER_POLICY: list[dict[str, object]] = [
    {"role": "ops_manager", "filter": {"property": "region", "op": "eq", "value": "NA"}}
]


def _asset_interface(
    *,
    prop_type: str = "float",
    indexed: bool = True,
    nullable: bool = True,
) -> dict[str, object]:
    return {
        "apiName": "Asset",
        "displayName": "Asset",
        "properties": [{"apiName": "riskScore", "type": prop_type, "indexed": indexed, "nullable": nullable}],
    }


def test_interface_allows_temporal_properties_when_implementers_match() -> None:
    interface = {
        "apiName": "ScheduledAsset",
        "properties": [
            {"apiName": "serviceDate", "type": "date", "nullable": True},
            {"apiName": "scheduledAt", "type": "timestamp", "nullable": True},
        ],
    }
    implementation = {
        "apiName": "Machine",
        "implements": ["ScheduledAsset"],
        "properties": [
            {"apiName": "serviceDate", "type": "date", "nullable": True},
            {"apiName": "scheduledAt", "type": "timestamp", "nullable": True},
        ],
    }

    validate_ontology_interfaces({"interfaces": [interface], "objectTypes": [implementation]})


def _order_type(
    *,
    implements: list[str] | None = None,
    risk_property: dict[str, object] | None = None,
    row_policies: list[dict[str, object]] | None = None,
    risk_classification: str | None = None,
) -> dict[str, object]:
    risk = risk_property or {"apiName": "riskScore", "column": "risk_score", "type": "float", "indexed": True}
    if risk_classification is not None:
        risk = {**risk, "classification": risk_classification}
    order: dict[str, object] = {
        "apiName": "Order",
        "primaryKey": "orderId",
        "backing": {"dataset": "clean.if_orders", "mode": "snapshot", "primaryKeyColumns": ["order_id"]},
        "properties": [
            {"apiName": "orderId", "column": "order_id", "type": "string", "nullable": False, "indexed": True},
            {"apiName": "region", "column": "region", "type": "string", "indexed": True},
            {"apiName": "status", "column": "status", "type": "string"},
            risk,
        ],
    }
    if implements is not None:
        order["implements"] = implements
    if row_policies is not None:
        order["rowPolicies"] = row_policies
    return order


def _customer_type(*, implements: list[str] | None = None) -> dict[str, object]:
    customer: dict[str, object] = {
        "apiName": "Customer",
        "primaryKey": "customerId",
        "backing": {"dataset": "clean.if_customers", "mode": "snapshot", "primaryKeyColumns": ["customer_id"]},
        "properties": [
            {"apiName": "customerId", "column": "customer_id", "type": "string", "nullable": False, "indexed": True},
            {"apiName": "name", "column": "name", "type": "string"},
            {"apiName": "riskScore", "column": "risk_score", "type": "float", "indexed": True},
        ],
    }
    if implements is not None:
        customer["implements"] = implements
    return customer


def _definition(
    *,
    interfaces: list[dict[str, object]] | None = None,
    order: dict[str, object] | None = None,
    customer: dict[str, object] | None = None,
) -> dict[str, object]:
    definition: dict[str, object] = {
        "objectTypes": [
            order if order is not None else _order_type(implements=["Asset"]),
            customer if customer is not None else _customer_type(implements=["Asset"]),
        ]
    }
    definition["interfaces"] = interfaces if interfaces is not None else [_asset_interface()]
    return definition


def _yaml_text(definition: dict[str, object]) -> str:
    return yaml.safe_dump(definition, sort_keys=False)


def _prepare_datasets(foundry: FoundryLite, tmp_path: Path) -> RequestContext:
    admin = demo_admin_context()
    (tmp_path / "if_orders.csv").write_text(ORDERS_CSV, encoding="utf-8")
    (tmp_path / "if_customers.csv").write_text(CUSTOMERS_CSV, encoding="utf-8")
    foundry.datasets.ensure("clean.if_orders", ctx=admin, primary_key=["order_id"])
    foundry.datasets.upload_csv("clean.if_orders", str(tmp_path / "if_orders.csv"), ctx=admin)
    foundry.datasets.ensure("clean.if_customers", ctx=admin, primary_key=["customer_id"])
    foundry.datasets.upload_csv("clean.if_customers", str(tmp_path / "if_customers.csv"), ctx=admin)
    return admin


def _prepare_indexed(
    foundry: FoundryLite,
    tmp_path: Path,
    definition: dict[str, object] | None = None,
) -> RequestContext:
    admin = _prepare_datasets(foundry, tmp_path)
    foundry.ontology.apply_text(_yaml_text(definition or _definition()), ctx=admin)
    foundry.objects.reindex("Order", ctx=admin)
    foundry.objects.reindex("Customer", ctx=admin)
    return admin


def _keys(result: dict[str, object]) -> list[tuple[str, str]]:
    items = cast(list[dict[str, object]], result["items"])
    return [(str(item["objectType"]), str(item["objectId"])) for item in items]


def test_validate_ontology_interfaces_accepts_conforming_and_rejects_duplicates() -> None:
    validate_ontology_interfaces(_definition())

    duplicated = _definition(interfaces=[_asset_interface(), _asset_interface()])
    with raises(ValidationFailed, match="duplicate interface apiName"):
        validate_ontology_interfaces(duplicated)

    bad_type = _definition(interfaces=[_asset_interface(prop_type="uuid")])
    with raises(ValidationFailed, match="unsupported interface property type"):
        validate_ontology_interfaces(bad_type)


def test_interface_link_constraints_validate_target_cardinality_and_required_implementation() -> None:
    interface = _asset_interface()
    interface["linkConstraints"] = [
        {
            "apiName": "customer",
            "targetKind": "object",
            "target": "Customer",
            "cardinality": "one",
            "required": True,
        }
    ]
    definition = _definition(
        interfaces=[interface],
        order=_order_type(implements=["Asset"]),
        customer=_customer_type(implements=[]),
    )
    definition["linkTypes"] = [
        {"apiName": "OrderCustomer", "from": "Order", "to": "Customer", "cardinality": "many_to_one"}
    ]
    validate_ontology_interfaces(definition)

    definition["linkTypes"] = []
    with raises(ValidationFailed, match="do not conform") as missing:
        validate_ontology_interfaces(definition)
    assert missing.value.details["violations"] == [
        {
            "kind": "missing_required_interface_link",
            "objectType": "Order",
            "interface": "Asset",
            "constraint": "customer",
        }
    ]

    interface["linkConstraints"] = [
        {"apiName": "ghost", "targetKind": "object", "target": "Ghost", "cardinality": "many"}
    ]
    with raises(ValidationFailed, match="target was not found"):
        validate_ontology_interfaces(definition)


def test_apply_publishes_interfaces_and_implements_in_catalog(foundry: FoundryLite, tmp_path: Path) -> None:
    admin = _prepare_datasets(foundry, tmp_path)
    foundry.ontology.apply_text(_yaml_text(_definition()), ctx=admin)

    catalog = foundry.ontology.catalog(ctx=admin)

    assert catalog["interfaces"] == [
        {
            "apiName": "Asset",
            "displayName": "Asset",
            "properties": [{"apiName": "riskScore", "type": "float", "nullable": True, "indexed": True}],
            "linkConstraints": [],
            "implementedBy": ["Customer", "Order"],
        }
    ]
    implements_by_type = {item["apiName"]: item["implements"] for item in catalog["objectTypes"]}
    assert implements_by_type == {"Customer": ["Asset"], "Order": ["Asset"]}


def test_conformance_failures_are_reported_together(foundry: FoundryLite, tmp_path: Path) -> None:
    admin = _prepare_datasets(foundry, tmp_path)
    definition = _definition(
        interfaces=[_asset_interface(nullable=False)],
        order=_order_type(
            implements=["Asset"],
            # type mismatch + indexed downgrade + nullable loosened, all at once.
            risk_property={"apiName": "riskScore", "column": "risk_score", "type": "string"},
        ),
        customer={
            "apiName": "Customer",
            "primaryKey": "customerId",
            "backing": {"dataset": "clean.if_customers", "mode": "snapshot", "primaryKeyColumns": ["customer_id"]},
            "implements": ["Asset"],
            "properties": [
                {
                    "apiName": "customerId",
                    "column": "customer_id",
                    "type": "string",
                    "nullable": False,
                    "indexed": True,
                }
            ],
        },
    )

    with raises(ValidationFailed, match="do not conform") as exc_info:
        foundry.ontology.validate(_yaml_text(definition), ctx=admin)

    violations = cast(list[dict[str, object]], exc_info.value.details["violations"])
    kinds = {(item["objectType"], item["kind"]) for item in violations}
    assert kinds == {
        ("Order", "type_mismatch"),
        ("Order", "indexed_downgrade"),
        ("Order", "nullable_loosened"),
        ("Customer", "missing_property"),
    }
    assert all(item["interface"] == "Asset" for item in violations)


def test_implements_must_reference_a_declared_interface(foundry: FoundryLite, tmp_path: Path) -> None:
    admin = _prepare_datasets(foundry, tmp_path)
    definition = _definition(interfaces=[])

    with raises(ValidationFailed) as exc_info:
        foundry.ontology.validate(_yaml_text(definition), ctx=admin)

    violations = cast(list[dict[str, object]], exc_info.value.details["violations"])
    assert {item["kind"] for item in violations} == {"unknown_interface"}


def test_interface_api_name_must_not_collide_with_object_type(foundry: FoundryLite, tmp_path: Path) -> None:
    admin = _prepare_datasets(foundry, tmp_path)
    interface = _asset_interface()
    interface["apiName"] = "Order"
    definition = _definition(interfaces=[interface], order=_order_type(), customer=_customer_type())

    with raises(ValidationFailed, match="collides"):
        foundry.ontology.validate(_yaml_text(definition), ctx=admin)


def test_adding_interface_and_implements_is_a_warning(foundry: FoundryLite, tmp_path: Path) -> None:
    admin = _prepare_datasets(foundry, tmp_path)
    plain = _definition(interfaces=[], order=_order_type(), customer=_customer_type())
    foundry.ontology.apply_text(_yaml_text(plain), ctx=admin)

    result = foundry.ontology.validate(_yaml_text(_definition()), ctx=admin)

    assert result["migration_plan"]["status"] == "compatible_with_warning"
    kinds = {change["kind"] for change in result["migration_plan"]["warnings"]}
    assert "interface_added" in kinds
    assert "object_implements_added" in kinds


def test_dropping_implements_or_interface_is_blocked(foundry: FoundryLite, tmp_path: Path) -> None:
    admin = _prepare_datasets(foundry, tmp_path)
    foundry.ontology.apply_text(_yaml_text(_definition()), ctx=admin)

    dropped_implements = _definition(order=_order_type(), customer=_customer_type(implements=["Asset"]))
    blocked = foundry.ontology.validate(_yaml_text(dropped_implements), ctx=admin)
    assert blocked["migration_plan"]["status"] == "blocked"
    assert any(
        change["kind"] == "object_implements_removed" and change["path"] == "objectTypes.Order.implements.Asset"
        for change in blocked["migration_plan"]["blockedChanges"]
    )

    dropped_interface = _definition(interfaces=[], order=_order_type(), customer=_customer_type())
    with raises(ValidationFailed) as exc_info:
        foundry.ontology.apply_text(_yaml_text(dropped_interface), ctx=admin)
    plan = cast(dict[str, object], exc_info.value.details["ontologyMigration"])
    kinds = {change["kind"] for change in cast(list[dict[str, object]], plan["blockedChanges"])}
    assert "interface_removed" in kinds


def test_dropping_an_interface_property_is_blocked(foundry: FoundryLite, tmp_path: Path) -> None:
    admin = _prepare_datasets(foundry, tmp_path)
    foundry.ontology.apply_text(_yaml_text(_definition()), ctx=admin)

    emptied = _asset_interface()
    emptied["properties"] = []
    blocked = foundry.ontology.validate(_yaml_text(_definition(interfaces=[emptied])), ctx=admin)

    assert blocked["migration_plan"]["status"] == "blocked"
    assert any(
        change["kind"] == "interface_property_removed" and change["path"] == "interfaces.Asset.properties.riskScore"
        for change in blocked["migration_plan"]["blockedChanges"]
    )


def test_rollback_round_trip_preserves_interfaces(foundry: FoundryLite, tmp_path: Path) -> None:
    admin = _prepare_datasets(foundry, tmp_path)
    foundry.ontology.apply_text(_yaml_text(_definition()), ctx=admin)
    second = _definition(order=_order_type(implements=["Asset"], row_policies=NA_ORDER_POLICY))
    foundry.ontology.apply_text(_yaml_text(second), ctx=admin)

    result = foundry.ontology.rollback(1, ctx=admin)

    assert result["version_number"] == 3
    catalog = foundry.ontology.catalog(ctx=admin)
    assert [item["apiName"] for item in catalog["interfaces"]] == ["Asset"]
    assert catalog["interfaces"][0]["implementedBy"] == ["Customer", "Order"]
    implements_by_type = {item["apiName"]: item["implements"] for item in catalog["objectTypes"]}
    assert implements_by_type == {"Customer": ["Asset"], "Order": ["Asset"]}


def test_query_by_interface_merges_types_with_interface_ordering(foundry: FoundryLite, tmp_path: Path) -> None:
    admin = _prepare_indexed(foundry, tmp_path)

    result = foundry.objects.query_by_interface(
        "Asset",
        ctx=admin,
        order_by=[{"property": "riskScore", "direction": "desc"}],
    )

    assert _keys(result) == [
        ("Order", "O-1"),
        ("Customer", "C-100"),
        ("Order", "O-3"),
        ("Order", "O-2"),
        ("Customer", "C-101"),
    ]
    assert result["nextCursor"] is None


def test_query_by_interface_filters_on_interface_properties_only(foundry: FoundryLite, tmp_path: Path) -> None:
    admin = _prepare_indexed(foundry, tmp_path)

    filtered = foundry.objects.query_by_interface(
        "Asset",
        ctx=admin,
        filter_ast={"property": "riskScore", "op": "gte", "value": 0.7},
        order_by=[{"property": "riskScore", "direction": "asc"}],
    )
    assert _keys(filtered) == [("Order", "O-3"), ("Customer", "C-100"), ("Order", "O-1")]

    with raises(ValidationFailed, match="interface query may only reference interface properties"):
        foundry.objects.query_by_interface(
            "Asset",
            ctx=admin,
            filter_ast={"property": "region", "op": "eq", "value": "NA"},
        )


def test_query_by_interface_rejects_unknown_interface_and_oversized_limit(foundry: FoundryLite, tmp_path: Path) -> None:
    admin = _prepare_indexed(foundry, tmp_path)

    with raises(NotFound, match="interface type not found"):
        foundry.objects.query_by_interface("Ghost", ctx=admin)
    with raises(ValidationFailed, match="interface query limit exceeds maximum"):
        foundry.objects.query_by_interface("Asset", ctx=admin, limit=201)


def test_query_by_interface_applies_row_policies_per_type(foundry: FoundryLite, tmp_path: Path) -> None:
    definition = _definition(order=_order_type(implements=["Asset"], row_policies=NA_ORDER_POLICY))
    _prepare_indexed(foundry, tmp_path, definition)
    ops = RequestContext(actor_user_id="ops-user", roles=("ops_manager",))

    result = foundry.objects.query_by_interface(
        "Asset",
        ctx=ops,
        order_by=[{"property": "riskScore", "direction": "desc"}],
    )

    # EU order O-2 is hidden by the Order rowPolicy; Customer rows are unaffected.
    assert _keys(result) == [
        ("Order", "O-1"),
        ("Customer", "C-100"),
        ("Order", "O-3"),
        ("Customer", "C-101"),
    ]


def test_query_by_interface_masks_classified_properties_per_type(foundry: FoundryLite, tmp_path: Path) -> None:
    definition = _definition(order=_order_type(implements=["Asset"], risk_classification="pii"))
    _prepare_indexed(foundry, tmp_path, definition)
    ops = RequestContext(actor_user_id="ops-user", roles=("ops_manager",))

    result = foundry.objects.query_by_interface("Asset", ctx=ops)

    risk_by_key = {}
    for item in cast(list[dict[str, object]], result["items"]):
        properties = cast(dict[str, object], item["properties"])
        risk_by_key[(item["objectType"], item["objectId"])] = properties["riskScore"]
    assert risk_by_key[("Order", "O-1")] == "***MASKED***"
    assert risk_by_key[("Customer", "C-100")] == 0.8
    # Ordering on a property masked for one implementing type follows that
    # type's masking rules and is rejected, exactly like a per-type query.
    with raises(ValidationFailed, match="masked property"):
        foundry.objects.query_by_interface(
            "Asset",
            ctx=ops,
            order_by=[{"property": "riskScore", "direction": "desc"}],
        )


def test_api_interface_query_endpoint_returns_merged_page(
    foundry: FoundryLite, tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _prepare_indexed(foundry, tmp_path)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)

    response = client.post(
        "/api/interfaces/Asset/query",
        headers=ADMIN_HEADERS,
        json={"orderBy": [{"property": "riskScore", "direction": "desc"}], "limit": 3},
    )

    assert response.status_code == 200
    payload = response.json()
    assert [[item["objectType"], item["objectId"]] for item in payload["items"]] == [
        ["Order", "O-1"],
        ["Customer", "C-100"],
        ["Order", "O-3"],
    ]
    assert payload["nextCursor"] is None
