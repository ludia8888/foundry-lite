"""Property-based tests for the safe-expression evaluator.

Per docs/quality-gate-roadmap.md P5, hypothesis covers the input space that
example-based tests can never enumerate. The safe-expression evaluator is a
high-value target because:

  * It is invoked on every action precondition.
  * Its grammar is small (object.X in [...], object.X == '...').
  * It produces a binary verdict, so unexpected inputs are easy to assert on
    (must either return bool or raise ValidationFailed, never other behaviour).

The properties below encode the invariants the evaluator must hold under
arbitrary string and dict inputs.
"""

from __future__ import annotations

import os
import string

import pytest
from foundry_lite.application.safe_expression import (
    evaluate_safe_expression,
    precondition_expression,
    validate_action_request,
)
from foundry_lite.domain.errors import ValidationFailed
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# Mutmut copies the source tree; skip property tests under mutmut runs to
# keep mutation cycles short.
pytestmark = pytest.mark.skipif(
    os.environ.get("MUTMUT_RUN") == "1",
    reason="Property tests are too slow for per-mutant baseline runs",
)

_IDENT = st.text(alphabet=string.ascii_letters + "_", min_size=1, max_size=8)
_VALUE = st.text(alphabet=string.ascii_letters + string.digits, min_size=0, max_size=8)


# ──────────────────────────────────────────────────────────────────────────
# precondition_expression: must be a string, never raise, prefer safeExpression.
# ──────────────────────────────────────────────────────────────────────────


@given(
    safe=st.one_of(st.none(), _VALUE),
    expr=st.one_of(st.none(), _VALUE),
    cel=st.one_of(st.none(), _VALUE),
)
def test_precondition_expression_always_returns_string(safe, expr, cel) -> None:
    precondition = {}
    if safe is not None:
        precondition["safeExpression"] = safe
    if expr is not None:
        precondition["expression"] = expr
    if cel is not None:
        precondition["cel"] = cel
    result = precondition_expression(precondition)
    assert isinstance(result, str)


@given(safe=_VALUE, expr=_VALUE, cel=_VALUE)
def test_precondition_expression_prefers_safe_over_expression_over_cel(safe, expr, cel) -> None:
    assert precondition_expression({"safeExpression": safe, "expression": expr, "cel": cel}) == safe
    assert precondition_expression({"expression": expr, "cel": cel}) == expr
    assert precondition_expression({"cel": cel}) == cel


# ──────────────────────────────────────────────────────────────────────────
# evaluate_safe_expression: must return bool or raise ValidationFailed.
# Never any other exception type, never any non-bool return.
# ──────────────────────────────────────────────────────────────────────────


@given(expression=st.text(max_size=100), properties=st.dictionaries(_IDENT, _VALUE, max_size=10))
@settings(suppress_health_check=[HealthCheck.too_slow])
def test_evaluate_safe_expression_never_raises_unexpected(expression: str, properties: dict) -> None:
    try:
        result = evaluate_safe_expression(expression, properties)
    except ValidationFailed:
        return  # documented failure path
    assert isinstance(result, bool), f"non-bool return for {expression!r}: {result!r}"


@given(prop=_IDENT, value=_VALUE)
def test_evaluate_safe_expression_eq_matches_property(prop: str, value: str) -> None:
    expression = f"object.{prop} == '{value}'"
    properties = {prop: value}
    assert evaluate_safe_expression(expression, properties) is True


@given(prop=_IDENT, value=_VALUE, wrong=_VALUE)
def test_evaluate_safe_expression_eq_rejects_wrong_value(prop: str, value: str, wrong: str) -> None:
    if value == wrong:
        return
    expression = f"object.{prop} == '{value}'"
    properties = {prop: wrong}
    assert evaluate_safe_expression(expression, properties) is False


@given(prop=_IDENT, values=st.lists(_VALUE, min_size=1, max_size=4, unique=True))
def test_evaluate_safe_expression_in_includes_listed_values(prop: str, values: list[str]) -> None:
    rendered = ", ".join(f"'{v}'" for v in values)
    expression = f"object.{prop} in [{rendered}]"
    for v in values:
        assert evaluate_safe_expression(expression, {prop: v}) is True


@given(prop=_IDENT, values=st.lists(_VALUE, min_size=1, max_size=4, unique=True), other=_VALUE)
def test_evaluate_safe_expression_in_rejects_unlisted_value(prop: str, values: list[str], other: str) -> None:
    if other in values:
        return
    rendered = ", ".join(f"'{v}'" for v in values)
    expression = f"object.{prop} in [{rendered}]"
    assert evaluate_safe_expression(expression, {prop: other}) is False


# ──────────────────────────────────────────────────────────────────────────
# validate_action_request: missing param surfaces as ValidationFailed,
# never raises through.
# ──────────────────────────────────────────────────────────────────────────


@given(
    required=st.lists(_IDENT, min_size=1, max_size=4, unique=True),
    provided=st.dictionaries(_IDENT, _VALUE, max_size=4),
)
def test_validate_action_request_returns_validation_failed_on_missing(required, provided) -> None:
    missing = [name for name in required if name not in provided]
    action_type = {
        "parameter_schema": {"required": required},
        "definition": {"preconditions": []},
    }
    record = {"properties": {}}
    result = validate_action_request(action_type, record, provided)
    if missing:
        assert isinstance(result, ValidationFailed)
    else:
        assert result is None
