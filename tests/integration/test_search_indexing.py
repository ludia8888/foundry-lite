from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from foundry_lite.application.core import FoundryLiteCore
from foundry_lite.application.ports.adapter_failure import AdapterFailureContract, AdapterFailureMode
from foundry_lite.application.ports.search_adapter import SearchDocument, SearchHit, SearchIndexMapping, SearchQuery
from foundry_lite.domain.context import RequestContext
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from sqlalchemy import func, select

from tests.conftest import prepare_indexed_demo


class FailingSearchAdapter:
    profile_name = "failing-search"

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(AdapterFailureMode("search", "unavailable", True, "search unavailable"),),
        )

    def configure_index(self, mapping: SearchIndexMapping) -> None:
        raise RuntimeError(f"search configure failed for {mapping.object_type}")

    def upsert_document(self, document: SearchDocument) -> None:
        raise RuntimeError(f"search upsert failed for {document.document_id}")

    def delete_document(self, *, tenant_id: str, object_type: str, document_id: str) -> None:
        raise RuntimeError(f"search delete failed for {document_id}")

    def document_ids(self, *, tenant_id: str, object_type: str) -> list[str]:
        raise RuntimeError(f"search ids failed for {object_type}")

    def search(self, query: SearchQuery) -> list[SearchHit]:
        raise RuntimeError(f"search failed for {query.object_type}")


def test_search_rebuild_full_text_and_orphan_detection(tmp_path: Path) -> None:
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "flite")
    core = FoundryLiteCore(dependencies=dependencies)
    ctx = prepare_indexed_demo(core)
    note = _approve_order(core, ctx)

    change_result = core.index_search_object_changed("Order", "O-1001", ctx=ctx)
    search_page = core.query_objects("Order", ctx=ctx, search_text=note, limit=10)
    rebuild = core.index_search_rebuild("Order", ctx=ctx)
    dependencies.search_adapter.upsert_document(
        SearchDocument(ctx.tenant_id, "Order", "O-ORPHAN", 1, {"operatorNote": "orphan only"})
    )
    drift = core.index_search_rebuild("Order", ctx=ctx)

    assert change_result["status"] == "indexed"
    assert [item["objectId"] for item in search_page["items"]] == ["O-1001"]
    assert rebuild["objectCount"] == rebuild["searchCount"] == 3
    assert rebuild["status"] == "consistent"
    assert drift["orphanDocumentIds"] == ["O-ORPHAN"]
    assert _object_changed_count(core) >= 1


def test_search_failure_does_not_break_get_or_basic_filter(tmp_path: Path) -> None:
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "flite")
    core = FoundryLiteCore(dependencies=replace(dependencies, search_adapter=FailingSearchAdapter()))
    ctx = prepare_indexed_demo(core)

    order = core.get_object("Order", "O-1001", ctx=ctx)
    filtered = core.query_objects("Order", ctx=ctx, filter_ast={"property": "status", "op": "eq", "value": "PENDING"})

    assert order["objectId"] == "O-1001"
    assert any(item["objectId"] == "O-1001" for item in filtered["items"])


def _approve_order(core: FoundryLiteCore, ctx: RequestContext) -> str:
    order = core.get_object("Order", "O-1001", ctx=ctx)
    note = "Search projection proof"
    core.apply_action(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": note},
        idempotency_key="search-index-proof",
        ctx=ctx,
    )
    return note


def _object_changed_count(core: FoundryLiteCore) -> int:
    with core.engine.begin() as conn:
        count = conn.execute(
            select(func.count()).select_from(db.outbox_events).where(db.outbox_events.c.event_type == "object.changed")
        ).scalar_one()
    return int(count)
