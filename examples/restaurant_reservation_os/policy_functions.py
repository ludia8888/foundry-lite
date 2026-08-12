"""Deterministic policy Functions for the restaurant reservation OS demo.

The same source is published into four version-pinned Ontology Functions:

* ``quote_reservation_policy`` computes a customer-visible quote without writes.
* ``create_policy_compliant_reservation`` returns an ``ontology_edit_batch``;
  the Action runtime revalidates its read set and performs the actual write.
* ``authorize_reservation_deposit`` converts an unexpired hold into a booking.
* ``cancel_reservation`` releases inventory and records the refund outcome.

Only the Python standard library is used because this code runs in the
network-disabled, read-only Function sandbox.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any

_ACTIVE_INVENTORY_STATUSES = frozenset({"HELD", "BOOKED"})
_ACCEPTED_SANDBOX_PAYMENT_METHODS = frozenset({"pm_sandbox_visa_4242", "pm_sandbox_mastercard_5555"})
_WEEKDAY_CODES = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")


def quote_reservation_policy(
    restaurant: dict[str, Any],
    table: dict[str, Any],
    desiredAt: str,
    requestedAt: str,
    partySize: int,
    guestPhone: str,
    seatingPreference: str,
) -> dict[str, Any]:
    """Return the exact policy outcome a diner must see before booking."""

    restaurant_view = _object_view(restaurant)
    table_view = _object_view(table)
    desired_at = _aware_datetime(desiredAt, "desiredAt")
    requested_at = _aware_datetime(requestedAt, "requestedAt")
    reason_codes = _eligibility_reasons(
        restaurant_view,
        table_view,
        desired_at,
        requested_at,
        partySize,
        guestPhone,
        seatingPreference,
    )
    decision = _decision(restaurant_view, partySize, reason_codes)
    deposit = _deposit_amount(restaurant_view, partySize, decision)
    cancellation_deadline = desired_at - timedelta(hours=_integer(restaurant_view, "cancellationCutoffHours", 24))
    quote_expires_at = requested_at + timedelta(minutes=_integer(restaurant_view, "quoteValidityMinutes", 5))
    turn_minutes = _integer(table_view, "turnTimeMinutes", 120)
    return {
        "decision": decision,
        "isBookable": decision in {"AUTO_CONFIRMED", "PAYMENT_REQUIRED"},
        "reasonCodes": reason_codes,
        "depositAmountKrw": deposit,
        "cancellationDeadlineAt": cancellation_deadline.isoformat(),
        "lateArrivalGraceMinutes": _integer(restaurant_view, "lateArrivalGraceMinutes", 15),
        "quoteExpiresAt": quote_expires_at.isoformat(),
        "inventoryHoldStatus": "AVAILABLE_NOT_HELD",
        "holdDurationMinutes": _integer(restaurant_view, "depositHoldMinutes", 10),
        "serviceEndsAt": (desired_at + timedelta(minutes=turn_minutes)).isoformat(),
        "turnTimeMinutes": turn_minutes,
        "policyVersion": str(restaurant_view.get("policyVersion") or "unversioned"),
        "policyMessage": str(restaurant_view.get("policyMessage") or ""),
        "restaurantId": str(restaurant_view.get("restaurantId") or ""),
        "tableId": str(table_view.get("tableId") or ""),
    }


def create_policy_compliant_reservation(
    restaurant: dict[str, Any],
    table: dict[str, Any],
    reservationId: str,
    guestName: str,
    guestPhone: str,
    guestEmail: str,
    partySize: int,
    desiredAt: str,
    requestedAt: str,
    seatingPreference: str,
    specialRequest: str,
    customerAcceptedPolicy: bool,
) -> dict[str, Any]:
    """Atomically create a reservation and CAS the server-owned table ledger."""

    if not customerAcceptedPolicy:
        raise ValueError("customer policy acceptance is required")
    quote = quote_reservation_policy(
        restaurant,
        table,
        desiredAt,
        requestedAt,
        partySize,
        guestPhone,
        seatingPreference,
    )
    if not quote["isBookable"]:
        joined = ",".join(quote["reasonCodes"])
        raise ValueError(f"reservation policy rejected the request: {joined}")
    restaurant_view = _object_view(restaurant)
    table_view = _object_view(table)
    requested_at = _aware_datetime(requestedAt, "requestedAt")
    desired_at = _aware_datetime(desiredAt, "desiredAt")
    hold_expires_at = _hold_expiry(restaurant_view, requested_at, quote["decision"])
    inventory_status = "HELD" if quote["decision"] == "PAYMENT_REQUIRED" else "BOOKED"
    ledger = _active_inventory(table_view, requested_at)
    ledger.append(_ledger_entry(reservationId, desired_at, quote, inventory_status, hold_expires_at))
    properties = {
        "id": reservationId,
        "reservationId": reservationId,
        "restaurantId": quote["restaurantId"],
        "tableId": quote["tableId"],
        "guestName": guestName.strip(),
        "guestPhone": guestPhone.strip(),
        "guestEmail": guestEmail.strip(),
        "partySize": partySize,
        "reservationAt": desiredAt,
        "requestedAt": requestedAt,
        "createdAt": requestedAt,
        "status": quote["decision"],
        "seatingPreference": seatingPreference,
        "specialRequest": specialRequest.strip(),
        "policyVersion": quote["policyVersion"],
        "policyDecision": quote["decision"],
        "quotedDepositKrw": quote["depositAmountKrw"],
        "cancellationDeadlineAt": quote["cancellationDeadlineAt"],
        "lateArrivalGraceMinutes": quote["lateArrivalGraceMinutes"],
        "quoteExpiresAt": quote["quoteExpiresAt"],
        "inventoryHoldStatus": inventory_status,
        "holdExpiresAt": _iso_or_empty(hold_expires_at),
        "paymentStatus": "REQUIRED" if inventory_status == "HELD" else "NOT_REQUIRED",
        "paymentProvider": "LOCAL_SANDBOX" if inventory_status == "HELD" else "NONE",
        "paidDepositKrw": 0,
        "refundStatus": "NOT_APPLICABLE",
        "refundedAmountKrw": 0,
        "inventoryVersion": _object_version(table_view) + 1,
        "turnTimeMinutes": quote["turnTimeMinutes"],
        "customerAcceptedPolicy": customerAcceptedPolicy,
        "sourceChannel": "WEB",
    }
    return {
        "edits": [
            {
                "kind": "createObject",
                "ruleId": "create-policy-compliant-reservation",
                "objectType": "Reservation",
                "primaryKey": reservationId,
                "properties": properties,
            },
            {
                "kind": "modifyObject",
                "ruleId": "reserve-table-inventory",
                "objectType": "DiningTable",
                "objectId": _object_id(table_view, "DiningTable"),
                "expectedVersion": _object_version(table_view),
                "patch": {
                    "reservationLedgerJson": _ledger_json(ledger),
                    "inventoryUpdatedAt": requestedAt,
                    "lastReservationId": reservationId,
                },
            },
        ],
        "readSetVersions": _read_set_versions(restaurant, table),
        "provenance": {
            "policyVersion": quote["policyVersion"],
            "decision": quote["decision"],
            "reservationId": reservationId,
            "inventoryHoldStatus": inventory_status,
            "holdExpiresAt": _iso_or_empty(hold_expires_at),
            "inventoryVersion": _object_version(table_view) + 1,
        },
    }


def authorize_reservation_deposit(
    reservation: dict[str, Any],
    table: dict[str, Any],
    paymentAttemptId: str,
    paymentMethodRef: str,
    requestedAt: str,
) -> dict[str, Any]:
    """Authorize a local-sandbox deposit and convert a live hold into a booking."""

    reservation_view = _object_view(reservation)
    table_view = _object_view(table)
    requested_at = _aware_datetime(requestedAt, "requestedAt")
    _require_matching_table(reservation_view, table_view)
    _require_payment_ready(reservation_view, requested_at, paymentMethodRef)
    payment_reference = _sandbox_reference("pay", paymentAttemptId, reservation_view, paymentMethodRef)
    ledger = _transition_inventory_entry(table_view, reservation_view, requested_at, "BOOKED")
    reservation_patch = {
        "status": "CONFIRMED",
        "inventoryHoldStatus": "BOOKED",
        "paymentStatus": "AUTHORIZED",
        "paymentProvider": "LOCAL_SANDBOX",
        "paymentReference": payment_reference,
        "paymentAuthorizedAt": requestedAt,
        "paymentAttemptId": paymentAttemptId,
        "paidDepositKrw": _integer(reservation_view, "quotedDepositKrw", 0),
        "inventoryVersion": _object_version(table_view) + 1,
    }
    return _two_object_edit_batch(
        reservation,
        table,
        reservation_patch,
        ledger,
        requestedAt,
        "authorize-reservation-deposit",
        {"paymentReference": payment_reference, "provider": "LOCAL_SANDBOX"},
    )


def cancel_reservation(
    reservation: dict[str, Any],
    table: dict[str, Any],
    cancellationId: str,
    cancellationReason: str,
    requestedAt: str,
) -> dict[str, Any]:
    """Cancel one reservation, release inventory, and calculate sandbox refund policy."""

    reservation_view = _object_view(reservation)
    table_view = _object_view(table)
    requested_at = _aware_datetime(requestedAt, "requestedAt")
    _require_matching_table(reservation_view, table_view)
    if reservation_view.get("status") == "CANCELLED":
        raise ValueError("reservation is already cancelled")
    ledger = _remove_inventory_entry(table_view, reservation_view, requested_at)
    refund = _refund_outcome(reservation_view, requested_at, cancellationId)
    reservation_patch = {
        "status": "CANCELLED",
        "inventoryHoldStatus": "RELEASED",
        "cancelledAt": requestedAt,
        "cancellationId": cancellationId,
        "cancellationReason": cancellationReason.strip(),
        "refundStatus": refund["status"],
        "refundReference": refund["reference"],
        "refundedAmountKrw": refund["amountKrw"],
        "refundedAt": requestedAt if refund["status"] == "REFUNDED" else "",
        "paymentStatus": refund["paymentStatus"],
        "inventoryVersion": _object_version(table_view) + 1,
    }
    return _two_object_edit_batch(
        reservation,
        table,
        reservation_patch,
        ledger,
        requestedAt,
        "cancel-reservation",
        {"refundStatus": refund["status"], "refundedAmountKrw": refund["amountKrw"]},
    )


def _eligibility_reasons(
    restaurant: dict[str, Any],
    table: dict[str, Any],
    desired_at: datetime,
    requested_at: datetime,
    party_size: int,
    guest_phone: str,
    seating_preference: str,
) -> list[str]:
    reasons: list[str] = []
    _append_if(reasons, restaurant.get("status") != "OPEN", "RESTAURANT_CLOSED")
    _append_if(reasons, table.get("status") != "AVAILABLE", "TABLE_UNAVAILABLE")
    _append_if(
        reasons,
        table.get("restaurantId") != restaurant.get("restaurantId"),
        "TABLE_RESTAURANT_MISMATCH",
    )
    _append_if(reasons, party_size < _integer(table, "minPartySize", 1), "PARTY_TOO_SMALL")
    _append_if(reasons, party_size > _integer(table, "capacity", 0), "TABLE_CAPACITY_EXCEEDED")
    _append_if(reasons, not guest_phone.strip(), "PHONE_REQUIRED")
    _append_time_policy_reasons(reasons, restaurant, desired_at, requested_at)
    _append_seating_reasons(reasons, table, seating_preference)
    _append_if(
        reasons,
        _has_turn_conflict(table, desired_at, requested_at),
        "TURN_TIME_CONFLICT",
    )
    return reasons


def _append_time_policy_reasons(
    reasons: list[str],
    restaurant: dict[str, Any],
    desired_at: datetime,
    requested_at: datetime,
) -> None:
    lead_minutes = (desired_at - requested_at).total_seconds() / 60
    _append_if(
        reasons,
        lead_minutes < _integer(restaurant, "minAdvanceMinutes", 0),
        "MINIMUM_NOTICE_NOT_MET",
    )
    _append_if(
        reasons,
        desired_at > requested_at + timedelta(days=_integer(restaurant, "bookingWindowDays", 30)),
        "OUTSIDE_BOOKING_WINDOW",
    )
    closed = {
        value.strip().upper() for value in str(restaurant.get("closedWeekdaysCsv") or "").split(",") if value.strip()
    }
    _append_if(reasons, _WEEKDAY_CODES[desired_at.weekday()] in closed, "CLOSED_WEEKDAY")
    start_minutes = _clock_minutes(str(restaurant.get("serviceStartLocal") or "00:00"))
    last_minutes = _clock_minutes(str(restaurant.get("lastSeatingLocal") or "23:59"))
    desired_minutes = desired_at.hour * 60 + desired_at.minute
    _append_if(
        reasons,
        desired_minutes < start_minutes or desired_minutes > last_minutes,
        "OUTSIDE_SERVICE_HOURS",
    )
    interval = _integer(restaurant, "slotIntervalMinutes", 30)
    _append_if(
        reasons,
        interval <= 0 or (desired_minutes - start_minutes) % interval != 0,
        "INVALID_SLOT_INTERVAL",
    )


def _append_seating_reasons(
    reasons: list[str],
    table: dict[str, Any],
    seating_preference: str,
) -> None:
    preference = seating_preference.strip().upper()
    if preference == "ACCESSIBLE":
        _append_if(reasons, not bool(table.get("isAccessible")), "ACCESSIBLE_SEAT_UNAVAILABLE")
    if preference == "WINDOW":
        _append_if(reasons, str(table.get("area") or "").upper() != "WINDOW", "WINDOW_SEAT_UNAVAILABLE")


def _has_turn_conflict(table: dict[str, Any], desired_at: datetime, requested_at: datetime) -> bool:
    requested_end = desired_at + timedelta(minutes=_integer(table, "turnTimeMinutes", 120))
    for item in _active_inventory(table, requested_at):
        existing_start = _aware_datetime(str(item["startsAt"]), "reservationLedgerJson.startsAt")
        existing_end = _aware_datetime(str(item["endsAt"]), "reservationLedgerJson.endsAt")
        if desired_at < existing_end and existing_start < requested_end:
            return True
    return False


def _decision(
    restaurant: dict[str, Any],
    party_size: int,
    reason_codes: list[str],
) -> str:
    if reason_codes:
        return "UNAVAILABLE"
    if party_size > _integer(restaurant, "maxOnlinePartySize", 6):
        return "CALL_RESTAURANT"
    if party_size >= _integer(restaurant, "largePartyThreshold", 5):
        return "PAYMENT_REQUIRED"
    if party_size <= _integer(restaurant, "autoConfirmPartySizeMax", 4):
        return "AUTO_CONFIRMED"
    return "REQUESTED"


def _deposit_amount(
    restaurant: dict[str, Any],
    party_size: int,
    decision: str,
) -> int:
    if decision != "PAYMENT_REQUIRED":
        return 0
    return party_size * _integer(restaurant, "depositPerPersonKrw", 0)


def _read_set_versions(
    first: dict[str, Any],
    second: dict[str, Any],
    *,
    object_types: tuple[str, str] = ("Restaurant", "DiningTable"),
) -> dict[str, int]:
    read_set: dict[str, int] = {}
    _append_read_version(read_set, object_types[0], first)
    _append_read_version(read_set, object_types[1], second)
    return read_set


def _two_object_edit_batch(
    reservation: dict[str, Any],
    table: dict[str, Any],
    reservation_patch: dict[str, Any],
    ledger: list[dict[str, Any]],
    requested_at: str,
    rule_id: str,
    provenance: dict[str, Any],
) -> dict[str, Any]:
    reservation_view = _object_view(reservation)
    table_view = _object_view(table)
    edits = [
        _modify_edit("Reservation", reservation_view, reservation_patch, rule_id),
        _modify_edit(
            "DiningTable",
            table_view,
            {"reservationLedgerJson": _ledger_json(ledger), "inventoryUpdatedAt": requested_at},
            f"{rule_id}-inventory",
        ),
    ]
    return {
        "edits": edits,
        "readSetVersions": _read_set_versions(reservation, table, object_types=("Reservation", "DiningTable")),
        "provenance": provenance,
    }


def _modify_edit(
    object_type: str,
    item: dict[str, Any],
    patch: dict[str, Any],
    rule_id: str,
) -> dict[str, Any]:
    return {
        "kind": "modifyObject",
        "ruleId": rule_id,
        "objectType": object_type,
        "objectId": _object_id(item, object_type),
        "expectedVersion": _object_version(item),
        "patch": patch,
    }


def _ledger_entry(
    reservation_id: str,
    desired_at: datetime,
    quote: dict[str, Any],
    status: str,
    hold_expires_at: datetime | None,
) -> dict[str, Any]:
    return {
        "reservationId": reservation_id,
        "startsAt": desired_at.isoformat(),
        "endsAt": str(quote["serviceEndsAt"]),
        "status": status,
        "holdExpiresAt": _iso_or_empty(hold_expires_at),
    }


def _active_inventory(table: dict[str, Any], at: datetime) -> list[dict[str, Any]]:
    active: list[dict[str, Any]] = []
    for entry in _inventory_ledger(table):
        status = entry.get("status")
        if status not in _ACTIVE_INVENTORY_STATUSES:
            continue
        if _aware_datetime(str(entry.get("endsAt") or ""), "reservationLedgerJson.endsAt") <= at:
            continue
        if status == "HELD":
            expiry = _aware_datetime(str(entry.get("holdExpiresAt") or ""), "reservationLedgerJson.holdExpiresAt")
            if expiry <= at:
                continue
        active.append(entry)
    return active


def _inventory_ledger(table: dict[str, Any]) -> list[dict[str, Any]]:
    raw = table.get("reservationLedgerJson")
    if raw is None or raw == "":
        return []
    if not isinstance(raw, str):
        raise ValueError("reservationLedgerJson must be a JSON string")
    parsed = json.loads(raw)
    if not isinstance(parsed, list) or any(not isinstance(item, dict) for item in parsed):
        raise ValueError("reservationLedgerJson must contain a JSON object array")
    if len(parsed) > 500:
        raise ValueError("reservationLedgerJson exceeds the bounded active inventory limit")
    return [dict(item) for item in parsed]


def _ledger_json(ledger: list[dict[str, Any]]) -> str:
    if len(ledger) > 500:
        raise ValueError("active table inventory exceeds 500 reservations")
    return json.dumps(ledger, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _transition_inventory_entry(
    table: dict[str, Any],
    reservation: dict[str, Any],
    at: datetime,
    next_status: str,
) -> list[dict[str, Any]]:
    reservation_id = str(reservation.get("reservationId") or _object_id(reservation, "Reservation"))
    ledger = _active_inventory(table, at)
    matched = False
    for entry in ledger:
        if entry.get("reservationId") == reservation_id:
            entry["status"] = next_status
            entry["holdExpiresAt"] = ""
            matched = True
    if not matched:
        raise ValueError("reservation inventory hold is missing or expired")
    return ledger


def _remove_inventory_entry(table: dict[str, Any], reservation: dict[str, Any], at: datetime) -> list[dict[str, Any]]:
    reservation_id = str(reservation.get("reservationId") or _object_id(reservation, "Reservation"))
    ledger = _active_inventory(table, at)
    if not any(entry.get("reservationId") == reservation_id for entry in ledger):
        raise ValueError("reservation inventory entry is missing or already released")
    return [entry for entry in ledger if entry.get("reservationId") != reservation_id]


def _require_matching_table(reservation: dict[str, Any], table: dict[str, Any]) -> None:
    if reservation.get("tableId") != table.get("tableId"):
        raise ValueError("reservation does not belong to the supplied table")


def _require_payment_ready(reservation: dict[str, Any], at: datetime, payment_method_ref: str) -> None:
    if reservation.get("status") != "PAYMENT_REQUIRED" or reservation.get("inventoryHoldStatus") != "HELD":
        raise ValueError("reservation is not awaiting a deposit")
    expiry = _aware_datetime(str(reservation.get("holdExpiresAt") or ""), "holdExpiresAt")
    if expiry <= at:
        raise ValueError("reservation payment hold has expired")
    if payment_method_ref not in _ACCEPTED_SANDBOX_PAYMENT_METHODS:
        raise ValueError("local sandbox payment was declined")


def _refund_outcome(reservation: dict[str, Any], at: datetime, cancellation_id: str) -> dict[str, Any]:
    payment_status = str(reservation.get("paymentStatus") or "NOT_REQUIRED")
    if payment_status != "AUTHORIZED":
        next_status = "CANCELLED_BEFORE_PAYMENT" if payment_status == "REQUIRED" else payment_status
        return {"status": "NOT_APPLICABLE", "amountKrw": 0, "reference": "", "paymentStatus": next_status}
    cutoff = _aware_datetime(str(reservation.get("cancellationDeadlineAt") or ""), "cancellationDeadlineAt")
    if at > cutoff:
        return {"status": "FORFEITED", "amountKrw": 0, "reference": "", "paymentStatus": "AUTHORIZED"}
    amount = _integer(reservation, "paidDepositKrw", 0)
    reference = _sandbox_reference("rfnd", cancellation_id, reservation, str(amount))
    return {"status": "REFUNDED", "amountKrw": amount, "reference": reference, "paymentStatus": "REFUNDED"}


def _sandbox_reference(prefix: str, operation_id: str, reservation: dict[str, Any], discriminator: str) -> str:
    if not operation_id.strip():
        raise ValueError("operation id is required")
    material = f"{operation_id}|{reservation.get('reservationId')}|{discriminator}".encode()
    return f"{prefix}_sbx_{hashlib.sha256(material).hexdigest()[:20]}"


def _hold_expiry(restaurant: dict[str, Any], requested_at: datetime, decision: str) -> datetime | None:
    if decision != "PAYMENT_REQUIRED":
        return None
    return requested_at + timedelta(minutes=_integer(restaurant, "depositHoldMinutes", 10))


def _iso_or_empty(value: datetime | None) -> str:
    return "" if value is None else value.isoformat()


def _object_id(item: dict[str, Any], object_type: str) -> str:
    value = item.get("objectId")
    if not isinstance(value, str) or not value:
        raise ValueError(f"{object_type} objectId is required")
    return value


def _object_version(item: dict[str, Any]) -> int:
    value = item.get("objectVersion")
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("objectVersion must be a non-negative integer")
    return value


def _append_read_version(
    read_set: dict[str, int],
    object_type: str,
    item: dict[str, Any],
) -> None:
    object_id = item.get("objectId")
    version = item.get("objectVersion")
    if isinstance(object_id, str) and isinstance(version, int) and not isinstance(version, bool):
        read_set[f"{object_type}:{object_id}"] = version


def _object_view(item: dict[str, Any]) -> dict[str, Any]:
    properties = item.get("properties")
    view = dict(properties) if isinstance(properties, dict) else dict(item)
    if isinstance(item.get("objectId"), str):
        view["objectId"] = item["objectId"]
    if isinstance(item.get("objectVersion"), int):
        view["objectVersion"] = item["objectVersion"]
    return view


def _aware_datetime(raw: str, field: str) -> datetime:
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include an explicit UTC offset")
    return parsed


def _clock_minutes(raw: str) -> int:
    normalized = raw.removeprefix("T")
    if len(normalized) == 4 and normalized.isdigit():
        hours_text, minutes_text = normalized[:2], normalized[2:]
    else:
        hours_text, minutes_text = normalized.split(":", maxsplit=1)
    hours = int(hours_text)
    minutes = int(minutes_text)
    if hours not in range(24) or minutes not in range(60):
        raise ValueError("restaurant service clock is invalid")
    return hours * 60 + minutes


def _integer(container: dict[str, Any], key: str, default: int) -> int:
    value = container.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"restaurant policy {key} must be an integer")
    return value


def _append_if(reasons: list[str], condition: bool, code: str) -> None:
    if condition:
        reasons.append(code)
