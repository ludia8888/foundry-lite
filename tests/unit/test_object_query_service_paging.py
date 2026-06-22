from __future__ import annotations

from contextlib import contextmanager

import pytest
from foundry_lite.application.ports import ObjectRecordRow
from foundry_lite.application.services.object_store.query import ObjectQueryService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed


class _FakeEngine:
    @contextmanager
    def begin(self):
        yield object()


class _AllowPolicy:
    def require(self, _ctx: RequestContext, _permission: str) -> None:
        return None

    def mask_properties(
        self,
        _ctx: RequestContext,
        _object_type: str,
        properties: dict[str, object],
    ) -> dict[str, object]:
        return properties

    def masked_property_names(self, _ctx: RequestContext, _object_type: str) -> set[str]:
        return set()


class _OntologyLookup:
    def _active_object_type(self, *_args: object) -> dict[str, object]:
        return {"id": "ot_order", "config": {}, "api_name": "Order"}

    def _properties_for_object_type(self, *_args: object) -> list[dict[str, object]]:
        return [{"api_name": "amount"}, {"api_name": "status"}]


class _ObjectIndexRepository:
    def __init__(self, active_index_version: str = "active") -> None:
        self.active_index_version_value = active_index_version

    def active_index_version(self, **_kwargs: object) -> str:
        return self.active_index_version_value


class _PagedObjectRepository:
    def __init__(self) -> None:
        self.requested_limit: int | None = None
        self.requested_property_object_type_id: object | None = None

    def active_object_rows(self, **_kwargs: object) -> list[ObjectRecordRow]:
        raise AssertionError("object query must not read the full row set")

    def query_active_object_rows(self, **kwargs: object) -> list[ObjectRecordRow]:
        self.requested_limit = int(kwargs["limit"])
        self.requested_property_object_type_id = kwargs.get("property_object_type_id")
        return [_object_row("O-1", 10.0), _object_row("O-2", 9.0)]


def test_object_query_service_requests_db_keyset_page_with_one_row_lookahead() -> None:
    repository = _PagedObjectRepository()
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
        }
    )

    result = service.query_objects(
        "Order",
        filter_ast={"property": "amount", "op": "gte", "value": 5.0},
        order_by=[{"property": "amount", "direction": "desc"}],
        limit=1,
        ctx=RequestContext(roles=("viewer",)),
    )

    assert repository.requested_limit == 2
    assert repository.requested_property_object_type_id == "ot_order"
    assert [item["objectId"] for item in result["items"]] == ["O-1"]
    assert result["nextCursor"] is not None


def test_object_query_db_backed_keyset_no_memory_slice() -> None:
    repository = _PagedObjectRepository()
    service = _object_query_service(repository)

    result = service.query_objects(
        "Order",
        filter_ast={"property": "amount", "op": "gte", "value": 5.0},
        order_by=[{"property": "amount", "direction": "desc"}],
        limit=1,
        ctx=RequestContext(roles=("viewer",)),
    )

    assert repository.requested_limit == 2
    assert [item["objectId"] for item in result["items"]] == ["O-1"]


def test_object_query_cursor_signed_tamper_proof_query_shape_bound() -> None:
    service = _object_query_service(_PagedObjectRepository())
    first_page = service.query_objects(
        "Order",
        filter_ast={"property": "amount", "op": "gte", "value": 5.0},
        order_by=[{"property": "amount", "direction": "desc"}],
        limit=1,
        ctx=RequestContext(roles=("viewer",)),
    )
    cursor = str(first_page["nextCursor"])
    tampered = cursor[:-1] + ("A" if cursor[-1] != "A" else "B")

    with pytest.raises(ValidationFailed, match="invalid object query cursor"):
        service.query_objects(
            "Order",
            filter_ast={"property": "amount", "op": "gte", "value": 5.0},
            order_by=[{"property": "amount", "direction": "desc"}],
            cursor=tampered,
            ctx=RequestContext(roles=("viewer",)),
        )
    with pytest.raises(ValidationFailed, match="query shape"):
        service.query_objects(
            "Order",
            filter_ast={"property": "amount", "op": "gte", "value": 6.0},
            order_by=[{"property": "amount", "direction": "desc"}],
            cursor=cursor,
            ctx=RequestContext(roles=("viewer",)),
        )


def test_object_query_cursor_rejects_active_index_version_change() -> None:
    object_index_repository = _ObjectIndexRepository()
    service = _object_query_service(_PagedObjectRepository(), object_index_repository=object_index_repository)
    first_page = service.query_objects(
        "Order",
        filter_ast={"property": "amount", "op": "gte", "value": 5.0},
        order_by=[{"property": "amount", "direction": "desc"}],
        limit=1,
        ctx=RequestContext(roles=("viewer",)),
    )
    cursor = str(first_page["nextCursor"])

    object_index_repository.active_index_version_value = "index_run_shadow_promoted"

    with pytest.raises(ValidationFailed, match="active index version"):
        service.query_objects(
            "Order",
            filter_ast={"property": "amount", "op": "gte", "value": 5.0},
            order_by=[{"property": "amount", "direction": "desc"}],
            cursor=cursor,
            ctx=RequestContext(roles=("viewer",)),
        )


def test_object_query_service_rejects_missing_filter_and_order_properties() -> None:
    service = _object_query_service(_PagedObjectRepository())

    with pytest.raises(ValidationFailed, match="missing property"):
        service.query_objects(
            "Order",
            filter_ast={"property": "missing", "op": "eq", "value": "x"},
            ctx=RequestContext(roles=("viewer",)),
        )
    with pytest.raises(ValidationFailed, match="missing property"):
        service.query_objects(
            "Order",
            order_by=[{"property": "missing", "direction": "asc"}],
            ctx=RequestContext(roles=("viewer",)),
        )


def _object_query_service(
    repository: _PagedObjectRepository,
    *,
    object_index_repository: _ObjectIndexRepository | None = None,
) -> ObjectQueryService:
    service = ObjectQueryService(
        engine=_FakeEngine(),
        policy=_AllowPolicy(),
        object_index_repository=object_index_repository or _ObjectIndexRepository(),
        object_read_repository=repository,
    )
    service.bind_collaborators(
        {
            "object_records_service": object(),
            "runtime_service": object(),
            "ontology_service": _OntologyLookup(),
            "object_search_service": object(),
        }
    )
    return service


def _object_row(object_id: str, amount: float) -> ObjectRecordRow:
    return {
        "id": f"obj_{object_id}",
        "tenant_id": "tenant-demo",
        "object_type_id": "ot_order",
        "object_type_api_name": "Order",
        "object_id": object_id,
        "index_version": "active",
        "is_active": True,
        "properties": {"amount": amount},
        "base_properties": {"amount": amount},
        "edit_properties": {},
        "property_versions": {"amount": 1},
        "source_dataset_version_id": "dsv_orders_1",
        "source_hash": "hash-demo",
        "object_version": 1,
        "deleted": False,
        "deletion_reason": None,
        "created_at": "2026-06-13T00:00:00Z",
        "updated_at": "2026-06-13T00:00:00Z",
    }
