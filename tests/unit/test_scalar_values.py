from __future__ import annotations

from foundry_lite.domain.scalar_values import (
    finite_number_value,
    integer_value,
    is_iso_timestamp,
    matches_scalar_type,
    timestamp_from_microseconds,
    timestamp_microseconds,
)


def test_timestamp_contract_preserves_exact_microseconds_across_offsets() -> None:
    assert timestamp_microseconds("2026-01-01T01:00:00.000001+01:00") == timestamp_microseconds(
        "2026-01-01T00:00:00.000001Z"
    )


def test_timestamp_contract_rejects_precision_the_runtime_cannot_preserve() -> None:
    assert is_iso_timestamp("2026-01-01T00:00:00.123456Z") is True
    assert is_iso_timestamp("2026-01-01T00:00:00.1234567Z") is False


def test_timestamp_contract_round_trip_preserves_datetime_boundaries() -> None:
    maximum = "9999-12-31T23:59:59.999999Z"
    assert timestamp_from_microseconds(timestamp_microseconds(maximum)) == maximum
    assert timestamp_from_microseconds(True) is None


def test_query_number_normalizers_reject_python_only_string_syntax() -> None:
    assert finite_number_value("+12") == 12.0
    assert finite_number_value("1e1") == 10.0
    assert finite_number_value("1_2") is None
    assert finite_number_value(" 12 ") is None
    assert integer_value("+12") == 12


def test_action_decimal_wire_contract_is_a_finite_canonical_json_string() -> None:
    assert matches_scalar_type("decimal", "10.25") is True
    assert matches_scalar_type("decimal", "+1e3") is True
    assert matches_scalar_type("decimal", 10.25) is False
    assert matches_scalar_type("decimal", " 10.25 ") is False
    assert matches_scalar_type("decimal", "1_000.00") is False
    assert integer_value("1_2") is None


def test_integer_and_long_follow_the_public_ontology_numeric_bounds() -> None:
    assert matches_scalar_type("integer", -(2**31)) is True
    assert matches_scalar_type("integer", 2**31 - 1) is True
    assert matches_scalar_type("integer", -(2**31) - 1) is False
    assert matches_scalar_type("integer", 2**31) is False
    assert matches_scalar_type("long", -(2**53 - 1)) is True
    assert matches_scalar_type("long", 2**53 - 1) is True
    assert matches_scalar_type("long", -(2**53)) is False
    assert matches_scalar_type("long", 2**53) is False
