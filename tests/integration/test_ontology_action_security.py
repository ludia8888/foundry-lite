from __future__ import annotations

from pathlib import Path

import pytest
from foundry_lite.application.core import FoundryLiteCore
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    ConflictDetected,
    ExternalSystemError,
    PermissionDenied,
    ValidationFailed,
)

from tests.conftest import prepare_indexed_demo


def test_ontology_activation_rejects_missing_backing_column(
    core: FoundryLiteCore,
    tmp_path: Path,
) -> None:
    prepare_indexed_demo(core)
    bad_yaml = tmp_path / "bad-ontology.yaml"
    bad_yaml.write_text(
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
      - apiName: badProperty
        column: does_not_exist
        type: string
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValidationFailed):
        core.apply_ontology(bad_yaml)


def test_action_apply_is_idempotent_and_rejects_stale_object_version(
    core: FoundryLiteCore,
) -> None:
    prepare_indexed_demo(core)
    order = core.get_object("Order", "O-1001")
    first = core.apply_action(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "Inventory confirmed"},
        idempotency_key="same-key",
    )
    replay = core.apply_action(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "Inventory confirmed"},
        idempotency_key="same-key",
    )

    assert replay["idempotentReplay"] is True
    assert replay["actionRunId"] == first["actionRunId"]
    with pytest.raises(ConflictDetected):
        core.apply_action(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "Inventory confirmed again"},
            idempotency_key="different-key",
        )


def test_before_commit_writeback_failure_does_not_edit_object(
    core: FoundryLiteCore,
) -> None:
    prepare_indexed_demo(core)
    order = core.get_object("Order", "O-1001")

    with pytest.raises(ExternalSystemError):
        core.apply_action(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "Inventory confirmed"},
            idempotency_key="writeback-fails",
            simulate_writeback_failure=True,
        )

    after = core.get_object("Order", "O-1001")
    assert after["objectVersion"] == order["objectVersion"]
    assert after["properties"]["status"] == "PENDING"


def test_viewer_sees_masked_margin_and_cannot_approve_order(
    core: FoundryLiteCore,
) -> None:
    prepare_indexed_demo(core)
    viewer = RequestContext(actor_user_id="viewer-1", roles=("viewer",))
    order = core.get_object("Order", "O-1001", ctx=viewer)

    assert order["properties"]["margin"] == "***MASKED***"
    with pytest.raises(PermissionDenied):
        core.apply_action(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "Inventory confirmed"},
            idempotency_key="viewer-denied",
            ctx=viewer,
        )
    assert any(event["decision"] == "deny" for event in core.list_runs()["auditEvents"])
