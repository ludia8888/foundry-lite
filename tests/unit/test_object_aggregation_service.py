from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.services.object_store.query import ObjectQueryService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied, ValidationFailed
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from foundry_lite_api import main as api_main
from foundry_lite_api import runtime as api_runtime

from tests.conftest import prepare_indexed_demo


def test_aggregate_count_without_group_by_counts_all_objects(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)

    result = foundry.objects.aggregate("Order", ctx=ctx, select=[{"function": "count"}])

    assert result == {"groups": [{"key": {}, "metrics": {"count": 3}}], "totalGroups": 1}


def test_aggregate_numeric_metrics_group_by_customer_in_stable_order(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)

    result = foundry.objects.aggregate(
        "Order",
        ctx=ctx,
        group_by=["customerId"],
        select=[
            {"function": "count"},
            {"function": "sum", "property": "amount"},
            {"function": "avg", "property": "amount"},
            {"function": "min", "property": "margin"},
            {"function": "max", "property": "margin"},
        ],
    )

    assert result["totalGroups"] == 2
    assert [group["key"] for group in result["groups"]] == [{"customerId": "C-100"}, {"customerId": "C-101"}]
    assert result["groups"][0]["metrics"] == {
        "count": 2,
        "sum_amount": 1500.0,
        "avg_amount": 750.0,
        "min_margin": 20.0,
        "max_margin": 230.0,
    }
    assert result["groups"][1]["metrics"] == {
        "count": 1,
        "sum_amount": 800.0,
        "avg_amount": 800.0,
        "min_margin": 80.0,
        "max_margin": 80.0,
    }


def test_aggregate_respects_filter_ast_and_custom_metric_names(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)

    result = foundry.objects.aggregate(
        "Order",
        ctx=ctx,
        filter_ast={"property": "status", "op": "in", "value": ["PENDING", "REVIEW"]},
        group_by=["status"],
        select=[{"function": "count"}, {"function": "sum", "property": "amount", "name": "totalAmount"}],
    )

    assert [group["key"]["status"] for group in result["groups"]] == ["PENDING", "REVIEW"]
    assert result["groups"][0]["metrics"] == {"count": 1, "totalAmount": 1200.0}
    assert result["groups"][1]["metrics"] == {"count": 1, "totalAmount": 800.0}


def test_aggregate_empty_filter_result_returns_empty_groups(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)

    result = foundry.objects.aggregate(
        "Order",
        ctx=ctx,
        filter_ast={"property": "status", "op": "eq", "value": "NO-SUCH-STATUS"},
        group_by=["status"],
        select=[{"function": "count"}],
    )

    assert result == {"groups": [], "totalGroups": 0}


def test_aggregate_masked_property_fails_closed_for_select_and_group_by(foundry: FoundryLite) -> None:
    prepare_indexed_demo(foundry)
    viewer = RequestContext(actor_user_id="viewer", roles=("viewer",))

    with pytest.raises(PermissionDenied, match="masked property"):
        foundry.objects.aggregate("Order", ctx=viewer, select=[{"function": "sum", "property": "margin"}])

    with pytest.raises(PermissionDenied, match="masked property"):
        foundry.objects.aggregate("Order", ctx=viewer, group_by=["margin"], select=[{"function": "count"}])


def test_aggregate_finance_role_may_aggregate_classified_margin(foundry: FoundryLite) -> None:
    prepare_indexed_demo(foundry)
    finance = RequestContext(actor_user_id="finance-analyst", roles=("finance",))

    result = foundry.objects.aggregate("Order", ctx=finance, select=[{"function": "sum", "property": "margin"}])

    assert result["groups"][0]["metrics"] == {"sum_margin": 330.0}


def test_aggregate_rejects_invalid_request_shapes(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)

    with pytest.raises(ValidationFailed, match="numeric property"):
        foundry.objects.aggregate("Order", ctx=ctx, select=[{"function": "sum", "property": "status"}])
    with pytest.raises(ValidationFailed, match="missing property"):
        foundry.objects.aggregate("Order", ctx=ctx, select=[{"function": "sum", "property": "region"}])
    with pytest.raises(ValidationFailed, match="missing property"):
        foundry.objects.aggregate("Order", ctx=ctx, group_by=["region"], select=[{"function": "count"}])
    with pytest.raises(ValidationFailed, match="at most two groupBy"):
        foundry.objects.aggregate(
            "Order",
            ctx=ctx,
            group_by=["status", "customerId", "orderId"],
            select=[{"function": "count"}],
        )
    with pytest.raises(ValidationFailed, match="at least one metric"):
        foundry.objects.aggregate("Order", ctx=ctx, select=[])
    with pytest.raises(ValidationFailed, match="count, sum, avg, min, or max"):
        foundry.objects.aggregate("Order", ctx=ctx, select=[{"function": "median", "property": "amount"}])


def test_aggregate_group_by_two_properties_orders_groups_by_key(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)

    result = foundry.objects.aggregate(
        "Order",
        ctx=ctx,
        group_by=["customerId", "status"],
        select=[{"function": "count"}],
    )

    assert [group["key"] for group in result["groups"]] == [
        {"customerId": "C-100", "status": "APPROVED"},
        {"customerId": "C-100", "status": "PENDING"},
        {"customerId": "C-101", "status": "REVIEW"},
    ]


def test_aggregate_group_cap_overflow_raises_typed_validation_error() -> None:
    repository = _GroupOverflowRepository(group_count=1_001)
    service = _aggregation_query_service(repository)

    with pytest.raises(ValidationFailed, match="group limit"):
        service.aggregate_objects(
            "Order",
            ctx=RequestContext(roles=("viewer",)),
            group_by=["customerId"],
            select=[{"function": "count"}],
        )

    assert repository.requested_group_limit == 1_001


def test_aggregate_http_route_happy_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    prepare_indexed_demo(foundry)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(api_main.app)

    response = client.post(
        "/api/objects/Order/aggregate",
        json={
            "filter": {"property": "status", "op": "in", "value": ["PENDING", "REVIEW"]},
            "groupBy": ["status"],
            "select": [{"function": "count"}, {"function": "sum", "property": "amount", "name": "totalAmount"}],
        },
        headers=_admin_headers(),
    )

    assert response.status_code == 200
    assert response.json() == {
        "groups": [
            {"key": {"status": "PENDING"}, "metrics": {"count": 1, "totalAmount": 1200.0}},
            {"key": {"status": "REVIEW"}, "metrics": {"count": 1, "totalAmount": 800.0}},
        ],
        "totalGroups": 2,
    }


def test_aggregate_http_route_maps_typed_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    prepare_indexed_demo(foundry)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(api_main.app)
    viewer_headers = {"X-Tenant-ID": "tenant-demo", "X-User-ID": "api-viewer", "X-Roles": "viewer"}

    masked = client.post(
        "/api/objects/Order/aggregate",
        json={"select": [{"function": "sum", "property": "margin"}]},
        headers=viewer_headers,
    )
    invalid = client.post(
        "/api/objects/Order/aggregate",
        json={"select": [{"function": "sum", "property": "status"}]},
        headers=_admin_headers(),
    )

    assert masked.status_code == 403
    assert masked.json()["detail"]["code"] == "PERMISSION_DENIED"
    assert invalid.status_code == 400
    assert invalid.json()["detail"]["code"] == "VALIDATION_FAILED"


def _admin_headers() -> dict[str, str]:
    return {
        "X-Tenant-ID": "tenant-demo",
        "X-User-ID": "api-admin",
        "X-Roles": "admin,data_engineer,ops_manager,finance",
    }


def _aggregation_query_service(repository: _GroupOverflowRepository) -> ObjectQueryService:
    service = ObjectQueryService(
        engine=_FakeEngine(),
        policy=_AllowPolicy(),
        object_index_repository=_ObjectIndexRepository(),
        object_read_repository=repository,
    )
    service.bind_collaborators(
        {
            "object_records_service": object(),
            "runtime_service": object(),
            "ontology_service": _OntologyLookup(),
            "object_search_service": object(),
            "osdk_application_service": _AllowOsdkScope(),
        }
    )
    return service


class _FakeEngine:
    @contextmanager
    def begin(self):
        yield object()


class _AllowPolicy:
    def require(self, _ctx: RequestContext, _permission: str) -> None:
        return None

    def masked_property_names(self, _ctx: RequestContext, _object_type: str) -> set[str]:
        return set()


class _OntologyLookup:
    def _active_object_type(self, *_args: object) -> dict[str, object]:
        return {"id": "ot_order", "config": {}, "api_name": "Order"}

    def _properties_for_object_type(self, *_args: object) -> list[dict[str, object]]:
        return [
            {"api_name": "customerId", "data_type": "string"},
            {"api_name": "amount", "data_type": "float"},
        ]


class _ObjectIndexRepository:
    def active_index_version(self, **_kwargs: object) -> str:
        return "active"


class _AllowOsdkScope:
    def require_resource_scope(self, *_args: object, **_kwargs: object) -> None:
        return None


class _GroupOverflowRepository:
    """Returns more groups than the cap so the typed overflow error is provable without 1,001 rows."""

    def __init__(self, group_count: int) -> None:
        self.group_count = group_count
        self.requested_group_limit: int | None = None

    def aggregate_active_object_rows(self, **kwargs: object) -> list[dict[str, object]]:
        group_limit = kwargs["group_limit"]
        assert isinstance(group_limit, int)
        self.requested_group_limit = group_limit
        return [
            {"key": {"customerId": f"C-{index}"}, "metrics": {"count": 1}}
            for index in range(min(self.group_count, group_limit))
        ]
