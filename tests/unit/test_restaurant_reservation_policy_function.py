"""Domain examples for the restaurant reservation policy Functions."""

from __future__ import annotations

from examples.restaurant_reservation_os.policy_functions import (
    authorize_reservation_deposit,
    cancel_reservation,
    create_policy_compliant_reservation,
    quote_reservation_policy,
)


def _restaurant() -> dict[str, object]:
    return {
        "objectId": "restaurant-1",
        "objectVersion": 2,
        "properties": {
            "restaurantId": "REST-SEOUL-01",
            "status": "OPEN",
            "serviceStartLocal": "17:30",
            "lastSeatingLocal": "21:30",
            "slotIntervalMinutes": 30,
            "bookingWindowDays": 30,
            "minAdvanceMinutes": 120,
            "closedWeekdaysCsv": "MON",
            "maxOnlinePartySize": 6,
            "largePartyThreshold": 5,
            "depositPerPersonKrw": 20_000,
            "cancellationCutoffHours": 24,
            "lateArrivalGraceMinutes": 15,
            "quoteValidityMinutes": 5,
            "depositHoldMinutes": 10,
            "autoConfirmPartySizeMax": 4,
            "policyVersion": "2026.08.1",
            "policyMessage": "24시간 전까지 무료 취소",
        },
    }


def _table(*, ledger: str = "[]", object_version: int = 3) -> dict[str, object]:
    return {
        "objectId": "table-1",
        "objectVersion": object_version,
        "properties": {
            "restaurantId": "REST-SEOUL-01",
            "tableId": "TABLE-WINDOW-04",
            "status": "AVAILABLE",
            "capacity": 6,
            "minPartySize": 2,
            "turnTimeMinutes": 120,
            "area": "WINDOW",
            "isAccessible": True,
            "reservationLedgerJson": ledger,
        },
    }


def _quote(*, party_size: int = 2, desired_at: str = "2026-08-15T19:00:00+09:00") -> dict[str, object]:
    return quote_reservation_policy(
        _restaurant(),
        _table(),
        desired_at,
        "2026-08-12T12:00:00+09:00",
        party_size,
        "010-1234-5678",
        "WINDOW",
    )


def test_small_party_inside_service_window_is_auto_confirmed() -> None:
    quote = _quote()

    assert quote["decision"] == "AUTO_CONFIRMED"
    assert quote["depositAmountKrw"] == 0
    assert quote["cancellationDeadlineAt"] == "2026-08-14T19:00:00+09:00"
    assert quote["inventoryHoldStatus"] == "AVAILABLE_NOT_HELD"
    assert quote["holdDurationMinutes"] == 10


def test_large_party_gets_a_per_person_deposit_quote() -> None:
    quote = _quote(party_size=5)

    assert quote["decision"] == "PAYMENT_REQUIRED"
    assert quote["depositAmountKrw"] == 100_000


def test_turn_time_overlap_blocks_the_slot() -> None:
    table = _table(
        ledger='[{"reservationId":"reservation-existing","startsAt":"2026-08-15T18:30:00+09:00",'
        '"endsAt":"2026-08-15T20:30:00+09:00","status":"BOOKED","holdExpiresAt":""}]'
    )

    quote = quote_reservation_policy(
        _restaurant(),
        table,
        "2026-08-15T19:00:00+09:00",
        "2026-08-12T12:00:00+09:00",
        2,
        "010-1234-5678",
        "WINDOW",
    )

    assert quote["decision"] == "UNAVAILABLE"
    assert quote["reasonCodes"] == ["TURN_TIME_CONFLICT"]


def test_unexpired_payment_hold_blocks_but_expired_hold_is_ignored() -> None:
    table = _table(
        ledger='[{"reservationId":"reservation-held","startsAt":"2026-08-15T19:30:00+09:00",'
        '"endsAt":"2026-08-15T21:30:00+09:00","status":"HELD",'
        '"holdExpiresAt":"2026-08-12T12:10:00+09:00"}]'
    )

    quote = quote_reservation_policy(
        _restaurant(),
        table,
        "2026-08-15T20:00:00+09:00",
        "2026-08-12T12:00:00+09:00",
        2,
        "010-1234-5678",
        "WINDOW",
    )

    assert quote["decision"] == "UNAVAILABLE"
    assert quote["reasonCodes"] == ["TURN_TIME_CONFLICT"]

    after_expiry = quote_reservation_policy(
        _restaurant(),
        table,
        "2026-08-15T20:00:00+09:00",
        "2026-08-12T12:11:00+09:00",
        2,
        "010-1234-5678",
        "WINDOW",
    )

    assert after_expiry["decision"] == "AUTO_CONFIRMED"


def test_function_backed_action_result_seals_policy_and_read_versions() -> None:
    batch = create_policy_compliant_reservation(
        _restaurant(),
        _table(),
        "RSV-20260815-1900-001",
        "홍길동",
        "010-1234-5678",
        "guest@example.com",
        2,
        "2026-08-15T19:00:00+09:00",
        "2026-08-12T12:00:00+09:00",
        "WINDOW",
        "기념일",
        True,
    )

    create_edit, inventory_edit = batch["edits"]
    assert create_edit["primaryKey"] == "RSV-20260815-1900-001"
    assert create_edit["properties"]["status"] == "AUTO_CONFIRMED"
    assert create_edit["properties"]["inventoryHoldStatus"] == "BOOKED"
    assert create_edit["properties"]["policyVersion"] == "2026.08.1"
    assert inventory_edit["objectType"] == "DiningTable"
    assert inventory_edit["expectedVersion"] == 3
    assert '"status":"BOOKED"' in inventory_edit["patch"]["reservationLedgerJson"]
    assert batch["readSetVersions"] == {
        "Restaurant:restaurant-1": 2,
        "DiningTable:table-1": 3,
    }


def test_deposit_hold_payment_and_free_cancellation_are_atomic_two_object_edits() -> None:
    created = create_policy_compliant_reservation(
        _restaurant(),
        _table(),
        "RSV-20260815-1930-005",
        "김민지",
        "010-1234-5678",
        "guest@example.com",
        5,
        "2026-08-15T19:30:00+09:00",
        "2026-08-12T12:00:00+09:00",
        "WINDOW",
        "알레르기 없음",
        True,
    )
    reservation_properties = created["edits"][0]["properties"]
    ledger = created["edits"][1]["patch"]["reservationLedgerJson"]
    reservation = {
        "objectId": "RSV-20260815-1930-005",
        "objectVersion": 1,
        "properties": reservation_properties,
    }
    table_after_hold = _table(ledger=ledger, object_version=4)

    payment = authorize_reservation_deposit(
        reservation,
        table_after_hold,
        "PAY-ATTEMPT-001",
        "pm_sandbox_visa_4242",
        "2026-08-12T12:05:00+09:00",
    )

    assert payment["edits"][0]["patch"]["paymentStatus"] == "AUTHORIZED"
    assert payment["edits"][0]["patch"]["paidDepositKrw"] == 100_000
    assert payment["edits"][0]["patch"]["paymentReference"].startswith("pay_sbx_")
    assert '"status":"BOOKED"' in payment["edits"][1]["patch"]["reservationLedgerJson"]
    assert payment["readSetVersions"] == {
        "Reservation:RSV-20260815-1930-005": 1,
        "DiningTable:table-1": 4,
    }

    paid_reservation = {
        "objectId": reservation["objectId"],
        "objectVersion": 2,
        "properties": {**reservation_properties, **payment["edits"][0]["patch"]},
    }
    table_after_payment = _table(ledger=payment["edits"][1]["patch"]["reservationLedgerJson"], object_version=5)
    cancellation = cancel_reservation(
        paid_reservation,
        table_after_payment,
        "CANCEL-001",
        "일정 변경",
        "2026-08-13T12:00:00+09:00",
    )

    assert cancellation["edits"][0]["patch"]["status"] == "CANCELLED"
    assert cancellation["edits"][0]["patch"]["refundStatus"] == "REFUNDED"
    assert cancellation["edits"][0]["patch"]["refundedAmountKrw"] == 100_000
    assert cancellation["edits"][1]["patch"]["reservationLedgerJson"] == "[]"


def test_declined_or_expired_sandbox_payment_produces_no_edit_batch() -> None:
    created = create_policy_compliant_reservation(
        _restaurant(),
        _table(),
        "RSV-20260815-2000-005",
        "김민지",
        "010-1234-5678",
        "guest@example.com",
        5,
        "2026-08-15T20:00:00+09:00",
        "2026-08-12T12:00:00+09:00",
        "WINDOW",
        "",
        True,
    )
    reservation = {
        "objectId": "RSV-20260815-2000-005",
        "objectVersion": 1,
        "properties": created["edits"][0]["properties"],
    }
    table = _table(ledger=created["edits"][1]["patch"]["reservationLedgerJson"], object_version=4)

    import pytest

    with pytest.raises(ValueError, match="declined"):
        authorize_reservation_deposit(
            reservation,
            table,
            "PAY-DECLINED",
            "pm_sandbox_declined",
            "2026-08-12T12:05:00+09:00",
        )
    with pytest.raises(ValueError, match="expired"):
        authorize_reservation_deposit(
            reservation,
            table,
            "PAY-LATE",
            "pm_sandbox_visa_4242",
            "2026-08-12T12:11:00+09:00",
        )
