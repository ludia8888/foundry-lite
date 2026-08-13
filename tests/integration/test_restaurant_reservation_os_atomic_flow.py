"""Real Function Action proof for atomic restaurant inventory, payment, and refund."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from time import monotonic, sleep

import pytest
import yaml
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.domain.context import RequestContext, demo_admin_context

from examples.restaurant_reservation_os.publish_policy_ontology import _add_policy_resources

pytestmark = pytest.mark.code_execution_image


def _property(
    api_name: str,
    column: str,
    data_type: str,
    *,
    is_editable: bool = False,
    is_indexed: bool = False,
) -> dict[str, object]:
    prop: dict[str, object] = {
        "apiName": api_name,
        "column": column,
        "type": data_type,
        "nullable": api_name != "id",
        "indexed": is_indexed,
    }
    if is_editable:
        prop.update({"editable": True, "editPolicy": "edit_wins"})
    return prop


def _object_type(api_name: str, dataset: str, properties: list[dict[str, object]]) -> dict[str, object]:
    return {
        "apiName": api_name,
        "primaryKey": "id",
        "backing": {"dataset": dataset, "mode": "snapshot", "primaryKeyColumns": ["id"]},
        "properties": properties,
    }


def _restaurant_properties() -> list[dict[str, object]]:
    fields = [
        ("id", "id", "string"),
        ("restaurantId", "restaurant_id", "string"),
        ("name", "name", "string"),
        ("location", "location", "string"),
        ("status", "status", "string"),
        ("timeZone", "time_zone", "string"),
        ("serviceStartLocal", "service_start", "string"),
        ("lastSeatingLocal", "last_seating", "string"),
        ("slotIntervalMinutes", "slot_interval", "integer"),
        ("bookingWindowDays", "booking_window", "integer"),
        ("minAdvanceMinutes", "min_advance", "integer"),
        ("closedWeekdaysCsv", "closed_weekdays", "string"),
        ("maxOnlinePartySize", "max_online_party", "integer"),
        ("largePartyThreshold", "large_party", "integer"),
        ("depositPerPersonKrw", "deposit_per_person", "integer"),
        ("cancellationCutoffHours", "cancellation_cutoff", "integer"),
        ("lateArrivalGraceMinutes", "late_grace", "integer"),
        ("quoteValidityMinutes", "quote_validity", "integer"),
        ("depositHoldMinutes", "deposit_hold", "integer"),
        ("autoConfirmPartySizeMax", "auto_confirm_max", "integer"),
        ("policyVersion", "policy_version", "string"),
        ("policyMessage", "policy_message", "string"),
    ]
    configurable = {
        "timeZone",
        "serviceStartLocal",
        "lastSeatingLocal",
        "slotIntervalMinutes",
        "bookingWindowDays",
        "minAdvanceMinutes",
        "closedWeekdaysCsv",
        "maxOnlinePartySize",
        "largePartyThreshold",
        "depositPerPersonKrw",
        "cancellationCutoffHours",
        "lateArrivalGraceMinutes",
        "quoteValidityMinutes",
        "depositHoldMinutes",
        "autoConfirmPartySizeMax",
        "policyVersion",
        "policyMessage",
    }
    return [
        _property(
            *field,
            is_editable=field[0] in configurable,
            is_indexed=field[0] in {"id", "policyVersion"},
        )
        for field in fields
    ]


def _table_properties() -> list[dict[str, object]]:
    fields = [
        ("id", "id", "string"),
        ("restaurantId", "restaurant_id", "string"),
        ("tableId", "table_id", "string"),
        ("label", "label", "string"),
        ("capacity", "capacity", "integer"),
        ("status", "status", "string"),
        ("area", "area", "string"),
        ("minPartySize", "min_party", "integer"),
        ("turnTimeMinutes", "turn_time", "integer"),
        ("isAccessible", "is_accessible", "boolean"),
        ("isHighChairCompatible", "high_chair", "boolean"),
        ("isCombinable", "combinable", "boolean"),
        ("tableType", "table_type", "string"),
        ("reservationLedgerJson", "ledger", "string"),
    ]
    return [
        _property(
            *field,
            is_editable=field[0]
            in {
                "area",
                "minPartySize",
                "turnTimeMinutes",
                "isAccessible",
                "isHighChairCompatible",
                "isCombinable",
                "tableType",
                "reservationLedgerJson",
            },
            is_indexed=field[0] == "id",
        )
        for field in fields
    ]


def _reservation_properties() -> list[dict[str, object]]:
    fields = [
        ("id", "id", "string"),
        ("reservationId", "reservation_id", "string"),
        ("restaurantId", "restaurant_id", "string"),
        ("tableId", "table_id", "string"),
        ("guestName", "guest_name", "string"),
        ("guestPhone", "guest_phone", "string"),
        ("partySize", "party_size", "integer"),
        ("reservationAt", "reservation_at", "string"),
        ("status", "status", "string"),
    ]
    return [_property(*field, is_editable=True, is_indexed=field[0] in {"id", "status"}) for field in fields]


def _definition() -> dict[str, object]:
    definition: dict[str, object] = {
        "objectTypes": [
            _object_type("Restaurant", "clean.atomic_restaurants", _restaurant_properties()),
            _object_type("DiningTable", "clean.atomic_tables", _table_properties()),
            _object_type("Reservation", "clean.atomic_reservations", _reservation_properties()),
        ],
        "functionTypes": [],
        "actionTypes": [],
    }
    _add_policy_resources(definition)
    return definition


def _write_seed_csvs(tmp_path: Path) -> Mapping[str, Path]:
    restaurant = tmp_path / "atomic-restaurants.csv"
    restaurant.write_text(
        "id,restaurant_id,name,location,status,time_zone,service_start,last_seating,slot_interval,"
        "booking_window,min_advance,closed_weekdays,max_online_party,large_party,deposit_per_person,"
        "cancellation_cutoff,late_grace,quote_validity,deposit_hold,auto_confirm_max,policy_version,policy_message\n"
        "restaurant-1,REST-SEOUL-01,MCP Bistro Seoul,Seoul,OPEN,Asia/Seoul,T1730,T2130,30,30,120,MON,"
        "6,5,20000,24,15,5,10,4,2026.08.3,24 hours free cancellation\n",
        encoding="utf-8",
    )
    table = tmp_path / "atomic-tables.csv"
    table.write_text(
        "id,restaurant_id,table_id,label,capacity,status,area,min_party,turn_time,is_accessible,high_chair,"
        "combinable,table_type,ledger\n"
        'table-1,REST-SEOUL-01,TABLE-WINDOW-04,Window 4,6,AVAILABLE,WINDOW,2,120,true,true,false,STANDARD,"[]"\n',
        encoding="utf-8",
    )
    reservation = tmp_path / "atomic-reservations.csv"
    reservation.write_text(
        "id,reservation_id,restaurant_id,table_id,guest_name,guest_phone,party_size,reservation_at,status\n"
        "reservation-history,reservation-history,REST-SEOUL-01,TABLE-WINDOW-04,Historical Guest,"
        "010-0000-0000,2,2026-08-01T19:00:00+09:00,CANCELLED\n",
        encoding="utf-8",
    )
    return {
        "clean.atomic_restaurants": restaurant,
        "clean.atomic_tables": table,
        "clean.atomic_reservations": reservation,
    }


def _prepare(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = demo_admin_context()
    for dataset, csv_path in _write_seed_csvs(tmp_path).items():
        foundry.datasets.ensure(dataset, ctx=ctx, primary_key=["id"])
        foundry.datasets.upload_csv(dataset, str(csv_path), ctx=ctx)
    foundry.ontology.apply_text(yaml.safe_dump(_definition(), sort_keys=False), ctx=ctx)
    for object_type in ("Restaurant", "DiningTable", "Reservation"):
        foundry.objects.reindex(object_type, ctx=ctx)


def _booking_params(reservation_id: str, *, party_size: int, desired_at: str) -> dict[str, object]:
    return {
        "restaurant": "restaurant-1",
        "table": "table-1",
        "reservationId": reservation_id,
        "guestName": "Kim Minji",
        "guestPhone": "010-1234-5678",
        "guestEmail": "minji@example.com",
        "partySize": party_size,
        "desiredAt": desired_at,
        "requestedAt": "2026-08-12T12:00:00+09:00",
        "seatingPreference": "WINDOW",
        "specialRequest": "anniversary",
        "customerAcceptedPolicy": True,
    }


def _start(
    foundry: FoundryLite,
    action: str,
    object_type: str,
    object_id: str,
    expected_version: int,
    params: Mapping[str, object],
    key: str,
) -> dict[str, object]:
    return foundry.actions.start_run(
        action,
        object_type=object_type,
        object_id=object_id,
        expected_object_version=expected_version,
        params=params,
        idempotency_key=key,
        wait_seconds=10,
        ctx=demo_admin_context(),
    )


def _wait_terminal(foundry: FoundryLite, run_id: str, timeout_seconds: float = 20) -> dict[str, object]:
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        run = foundry.actions.get_run(run_id, ctx=demo_admin_context())
        if run["status"] in {"succeeded", "failed", "cancelled"}:
            return run
        sleep(0.05)
    return foundry.actions.get_run(run_id, ctx=demo_admin_context())


def test_booking_payment_refund_replay_and_masking_end_to_end(foundry: FoundryLite, tmp_path: Path) -> None:
    _prepare(foundry, tmp_path)
    restaurant = foundry.objects.get("Restaurant", "restaurant-1", ctx=demo_admin_context())
    booked = _start(
        foundry,
        "ReserveTableWithAtomicInventory",
        "Restaurant",
        "restaurant-1",
        int(restaurant["objectVersion"]),
        _booking_params("RSV-ATOMIC-001", party_size=5, desired_at="2026-08-15T19:30:00+09:00"),
        "atomic-book-001",
    )

    assert booked["status"] == "succeeded"
    reservation = foundry.objects.get("Reservation", "RSV-ATOMIC-001", ctx=demo_admin_context())
    table = foundry.objects.get("DiningTable", "table-1", ctx=demo_admin_context())
    assert reservation["properties"]["inventoryHoldStatus"] == "HELD"
    assert reservation["properties"]["paymentStatus"] == "REQUIRED"

    paid = _start(
        foundry,
        "PayReservationDeposit",
        "Reservation",
        "RSV-ATOMIC-001",
        int(reservation["objectVersion"]),
        {
            "reservation": "RSV-ATOMIC-001",
            "table": "table-1",
            "paymentAttemptId": "PAY-ATOMIC-001",
            "paymentMethodRef": "pm_sandbox_visa_4242",
            "requestedAt": "2026-08-12T12:05:00+09:00",
        },
        "atomic-pay-001",
    )
    replay = _start(
        foundry,
        "PayReservationDeposit",
        "Reservation",
        "RSV-ATOMIC-001",
        int(reservation["objectVersion"]),
        {
            "reservation": "RSV-ATOMIC-001",
            "table": "table-1",
            "paymentAttemptId": "PAY-ATOMIC-001",
            "paymentMethodRef": "pm_sandbox_visa_4242",
            "requestedAt": "2026-08-12T12:05:00+09:00",
        },
        "atomic-pay-001",
    )

    assert paid["status"] == "succeeded"
    assert replay["actionRunId"] == paid["actionRunId"]
    paid_reservation = foundry.objects.get("Reservation", "RSV-ATOMIC-001", ctx=demo_admin_context())
    table_after_payment = foundry.objects.get("DiningTable", "table-1", ctx=demo_admin_context())
    assert paid_reservation["properties"]["paymentStatus"] == "AUTHORIZED"
    assert paid_reservation["properties"]["paidDepositKrw"] == 100_000
    assert table_after_payment["objectVersion"] == int(table["objectVersion"]) + 1

    cancelled = _start(
        foundry,
        "CancelCustomerReservation",
        "Reservation",
        "RSV-ATOMIC-001",
        int(paid_reservation["objectVersion"]),
        {
            "reservation": "RSV-ATOMIC-001",
            "table": "table-1",
            "cancellationId": "CANCEL-ATOMIC-001",
            "cancellationReason": "schedule changed",
            "requestedAt": "2026-08-13T12:00:00+09:00",
        },
        "atomic-cancel-001",
    )

    assert cancelled["status"] == "succeeded"
    final = foundry.objects.get("Reservation", "RSV-ATOMIC-001", ctx=demo_admin_context())
    final_table = foundry.objects.get("DiningTable", "table-1", ctx=demo_admin_context())
    assert final["properties"]["status"] == "CANCELLED"
    assert final["properties"]["refundStatus"] == "REFUNDED"
    assert final["properties"]["refundedAmountKrw"] == 100_000
    assert final_table["properties"]["reservationLedgerJson"] == "[]"

    viewer = RequestContext("tenant-demo", "reservation-viewer", "reservation-viewer-request", ("viewer",))
    masked = foundry.objects.get("Reservation", "RSV-ATOMIC-001", ctx=viewer)
    assert masked["properties"]["guestName"] == "***MASKED***"
    assert masked["properties"]["guestPhone"] == "***MASKED***"
    assert masked["properties"]["paymentReference"] == "***MASKED***"
    assert masked["properties"]["refundedAmountKrw"] == "***MASKED***"


def test_two_concurrent_bookings_cannot_both_commit_one_table(foundry: FoundryLite, tmp_path: Path) -> None:
    _prepare(foundry, tmp_path)
    restaurant = foundry.objects.get("Restaurant", "restaurant-1", ctx=demo_admin_context())
    stale_second_batch = foundry.functions.execute(
        "CreateAtomicReservation",
        inputs=_booking_params("RSV-RACE-2", party_size=2, desired_at="2026-08-16T19:00:00+09:00"),
        ctx=demo_admin_context(),
    )["output"]["value"]
    first = _start(
        foundry,
        "ReserveTableWithAtomicInventory",
        "Restaurant",
        "restaurant-1",
        int(restaurant["objectVersion"]),
        _booking_params("RSV-RACE-1", party_size=2, desired_at="2026-08-16T19:00:00+09:00"),
        "atomic-race-1",
    )

    foundry._services.action.distributed.action_function_executor.register_driver(
        lambda _request: {"output": stale_second_batch, "logicRunId": "stale-race-function"}
    )
    second = _start(
        foundry,
        "ReserveTableWithAtomicInventory",
        "Restaurant",
        "restaurant-1",
        int(restaurant["objectVersion"]),
        _booking_params("RSV-RACE-2", party_size=2, desired_at="2026-08-16T19:00:00+09:00"),
        "atomic-race-2",
    )

    assert first["status"] == "succeeded"
    assert second["status"] == "conflict"
    assert "version" in str(second["error"]).lower()
    reservations = foundry.objects.query("Reservation", ctx=demo_admin_context(), limit=20)
    race_ids = {item["objectId"] for item in reservations["items"] if str(item["objectId"]).startswith("RSV-RACE-")}
    assert len(race_ids) == 1
    table = foundry.objects.get("DiningTable", "table-1", ctx=demo_admin_context())
    ledger = str(table["properties"]["reservationLedgerJson"])
    assert sum(reservation_id in ledger for reservation_id in ("RSV-RACE-1", "RSV-RACE-2")) == 1
