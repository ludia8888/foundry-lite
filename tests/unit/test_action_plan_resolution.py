"""Regression coverage for LivePlanResolutionContext (adversarial-review findings)."""

from __future__ import annotations

from typing import Any

import pytest
from foundry_lite.application.action_types import ActionApplyCommand
from foundry_lite.application.services.action_plan_resolution import LivePlanResolutionContext
from foundry_lite.domain.action_runtime.value_expression import ParameterValue
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound, ValidationFailed


def _command(
    *, object_type: str = "Order", object_id: str = "O-1", expected: int = 5, params: dict[str, object] | None = None
) -> ActionApplyCommand:
    return ActionApplyCommand(
        action_api_name="FulfillOrder",
        object_type=object_type,
        object_id=object_id,
        expected_object_version=expected,
        params=params or {},
        idempotency_key="idem-1",
        request_fingerprint="fp-1",
        simulate_writeback_failure=False,
        simulate_writeback_retryable=False,
        simulate_writeback_outcome_unknown=False,
        simulate_writeback_compensation_required=False,
    )


class _Objects:
    def __init__(self, store: dict[tuple[str, str], int]) -> None:
        self.store = store

    def _object_record(
        self,
        conn: Any,
        ctx: RequestContext,
        object_type_api_name: str,
        object_id: str,
        object_type_id: str | None = None,
    ) -> dict[str, Any] | None:
        del conn, object_type_id
        version = self.store.get((object_type_api_name, object_id))
        if version is None:
            return None
        return {
            "id": f"rec-{object_id}",
            "tenant_id": ctx.tenant_id,
            "object_type_id": f"ot-{object_type_api_name}",
            "object_type_api_name": object_type_api_name,
            "object_id": object_id,
            "index_version": "active",
            "is_active": True,
            "properties": {},
            "base_properties": {},
            "edit_properties": {},
            "property_versions": {},
            "source_dataset_version_id": None,
            "source_hash": None,
            "object_version": version,
            "deleted": False,
            "deletion_reason": None,
            "created_at": "t",
            "updated_at": "t",
        }


class _Ontology:
    def _active_object_type(self, conn: Any, ctx: RequestContext, api_name: str) -> dict[str, Any]:
        del conn, ctx
        return {"id": f"ot-{api_name}", "api_name": api_name, "config": {}}


class _LinkTypes:
    def link_type(self, conn: Any, ctx: RequestContext, api_name: str) -> dict[str, Any]:
        del conn, ctx
        return {"id": f"lt-{api_name}", "from_api_name": "Order", "to_api_name": "Customer"}


def _adapter(store: dict[tuple[str, str], int], command: ActionApplyCommand | None = None) -> LivePlanResolutionContext:
    return LivePlanResolutionContext(
        conn=None,  # type: ignore[arg-type]
        ctx=RequestContext(actor_user_id="u1", roles=("viewer",)),
        command=command or _command(),
        object_lookup=_Objects(store),  # type: ignore[arg-type]
        ontology_lookup=_Ontology(),  # type: ignore[arg-type]
        link_type_lookup=_LinkTypes(),  # type: ignore[arg-type]
    )


def test_target_modify_conflicts_when_a_concurrent_bump_beats_the_client_version() -> None:
    # Finding [2]: the target's CAS binds to the client-validated version, so a fresher
    # read (a concurrent bump between the pre-commit check and planning) conflicts.
    adapter = _adapter({("Order", "O-1"): 6}, command=_command(expected=5))
    with pytest.raises(ConflictDetected, match="version conflict"):
        adapter.resolve_existing_object("Order", ParameterValue("__target__"))


def test_target_modify_binds_to_the_client_version_when_it_matches() -> None:
    adapter = _adapter({("Order", "O-1"): 5}, command=_command(expected=5))
    ref = adapter.resolve_existing_object("Order", ParameterValue("__target__"))
    assert ref.version == 5


def test_secondary_object_binds_to_its_own_read_version() -> None:
    adapter = _adapter(
        {("Order", "O-1"): 5, ("Widget", "W-9"): 3}, command=_command(expected=5, params={"widget": "W-9"})
    )
    ref = adapter.resolve_existing_object("Widget", ParameterValue("widget"))
    assert ref.version == 3


def test_link_endpoint_to_a_missing_object_raises_not_found() -> None:
    # Finding [1]/[3]: an absent param evaluates to None -> 'None' id -> missing endpoint ->
    # NotFound (never a dangling link, never a silent write to an unseen row).
    adapter = _adapter({("Order", "O-1"): 5}, command=_command(params={}))
    with pytest.raises(NotFound):
        adapter.resolve_link_endpoint("OrderCustomer", "target", ParameterValue("relatedCustomer"))


def test_link_endpoint_resolves_a_visible_object_by_its_endpoint_type() -> None:
    adapter = _adapter({("Order", "O-1"): 5, ("Customer", "C-7"): 2}, command=_command(params={"cust": "C-7"}))
    assert adapter.resolve_link_endpoint("OrderCustomer", "target", ParameterValue("cust")) == "C-7"


def test_current_user_attribute_is_rejected_and_bare_user_resolves() -> None:
    adapter = _adapter({}, command=_command())
    with pytest.raises(ValidationFailed, match="attribute"):
        adapter.current_user("email")
    assert adapter.current_user(None) == "u1"


def test_many_cardinality_target_requires_a_list_of_ids() -> None:
    # Finding [6]: a scalar/None must not silently become a zero-target no-op.
    adapter = _adapter({}, command=_command(params={"orders": "not-a-list"}))
    with pytest.raises(ValidationFailed, match="list of object ids"):
        adapter.resolve_existing_object_set("Order", ParameterValue("orders"))
