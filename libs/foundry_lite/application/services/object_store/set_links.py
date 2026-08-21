"""Ontology link lookup and scope checks shared by ObjectSet traversal paths."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.services.object_store.set_protocols import SetLinkScopeBoundary, SetOntologyLookup
from foundry_lite.domain.context import RequestContext


def link_types_by_api_name(
    ontology_service: SetOntologyLookup,
    conn: TransactionContext,
    ctx: RequestContext,
) -> dict[str, Mapping[str, object]]:
    active = ontology_service._active_ontology_version(conn, ctx)
    return {
        row["api_name"]: cast(Mapping[str, object], row)
        for row in ontology_service._link_types_for_version(conn, ctx, active["id"])
    }


def require_link_read_scope(
    link_scope_boundary: SetLinkScopeBoundary,
    ctx: RequestContext,
    link_type_api_name: str,
) -> None:
    link_scope_boundary.require_resource_scope(
        ctx,
        resource_type="link",
        resource_api_name=link_type_api_name,
        operation="read",
    )
