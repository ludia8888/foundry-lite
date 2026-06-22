"""Unit tests for pure compute-adapter and OIDC-auth helper functions."""

from __future__ import annotations

import pytest
from foundry_lite.domain.errors import InvariantViolation, PermissionDenied, ValidationFailed
from foundry_lite.infrastructure.adapters.compute import (
    _check_columns,
    _required_int_cell,
    _tabular_row,
)
from foundry_lite.infrastructure.auth.oidc import (
    _int_from_env,
    _jwks_keys,
    _required_header,
)


def test_tabular_row_requires_string_keyed_mapping() -> None:
    assert _tabular_row({"a": 1, "b": "x"}) == {"a": 1, "b": "x"}
    with pytest.raises(ValidationFailed):
        _tabular_row([1, 2, 3])


def test_check_columns_validates_string_column_list() -> None:
    assert _check_columns({"columns": ["a", "b"]}) == ["a", "b"]
    with pytest.raises(ValidationFailed):
        _check_columns({"columns": "a"})
    with pytest.raises(ValidationFailed):
        _check_columns({"columns": ["a", 2]})
    with pytest.raises(ValidationFailed):
        _check_columns({"columns": []})


def test_required_int_cell_rejects_non_integers() -> None:
    assert _required_int_cell((7,), "count") == 7
    with pytest.raises(ValidationFailed):
        _required_int_cell((True,), "count")
    with pytest.raises(ValidationFailed):
        _required_int_cell(("x",), "count")
    with pytest.raises(InvariantViolation):
        _required_int_cell(None, "count")


def test_int_from_env_parses_defaults_and_rejects_negative() -> None:
    assert _int_from_env({}, "TTL", 30) == 30
    assert _int_from_env({"TTL": "45"}, "TTL", 30) == 45
    with pytest.raises(ValueError):
        _int_from_env({"TTL": "-1"}, "TTL", 30)


def test_jwks_keys_requires_a_keys_list() -> None:
    assert _jwks_keys({"keys": [{"kid": "a"}, "not-a-mapping"]}) == ({"kid": "a"},)
    with pytest.raises(ValueError):
        _jwks_keys({"keys": "nope"})


def test_required_header_rejects_missing_or_blank() -> None:
    assert _required_header({"X-Tenant": "t1"}, "X-Tenant", "oidc") == "t1"
    with pytest.raises(PermissionDenied):
        _required_header({"X-Tenant": "  "}, "X-Tenant", "oidc")
    with pytest.raises(PermissionDenied):
        _required_header({}, "X-Tenant", "oidc")
