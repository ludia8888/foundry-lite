"""Ontology preconditions are submission criteria, not an expression dialect.

Palantir models a submission criterion as one structured comparison — `[Left] [Operator]
[Right]` over a parameter, an object property, a linked object property, or the current user —
and has no expression-string form. Foundry-lite had both: a canonical condition contract used
by `submissionCriteria`, and a separate string parser used by ontology YAML `preconditions`
that understood exactly two shapes (`object.x == 'literal'` and `object.x in [...]`).

That second dialect could not see parameters at all, so the most ordinary rule in any booking
domain — "the party must fit the table" — was inexpressible and silently absent. These tests
pin the merged behaviour: a precondition that declares an operator goes through the canonical
contract, and the legacy string form still works for definitions written before it existed.
"""

from __future__ import annotations

from typing import Any

import pytest
from foundry_lite.application.safe_expression import validate_action_request
from foundry_lite.domain.action_runtime.action_conditions import (
    StaticActionConditionContext,
    evaluate_action_condition,
    validate_action_condition,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed

_CTX = RequestContext(roles=("host",), actor_user_id="u-1")
_CONDITION_CTX = StaticActionConditionContext(
    parameters={},
    object_properties={},
    actor_user_id="u-1",
    actor_groups=("host",),
)


def _action(preconditions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "api_name": "BookTable",
        "definition": {
            "apiName": "BookTable",
            "target": "DiningTable",
            "parameters": [
                {"apiName": "partySize", "type": "integer"},
                {"apiName": "hostId", "type": "string"},
            ],
            "preconditions": preconditions,
        },
    }


def _record(**properties: Any) -> dict[str, Any]:
    return {"object_id": "T-1", "properties": {"status": "FREE", "seats": 4, **properties}}


def _validate(preconditions: list[dict[str, Any]], **params: Any) -> Exception | None:
    """`validate_action_request` reports the failure as a value; it does not raise."""
    return validate_action_request(_action(preconditions), _record(), params, ctx=_CTX)


def _expect_ok(error: Exception | None) -> None:
    assert error is None, f"expected the criterion to pass, got {error!r}"


def _expect_failure(error: Exception | None, message: str) -> None:
    assert isinstance(error, ValidationFailed), f"expected a ValidationFailed, got {error!r}"
    assert message in str(error), f"expected {message!r} in {error!s}"


# --- the rule that used to be inexpressible -----------------------------------------


def test_a_parameter_can_be_compared_against_an_object_property() -> None:
    """The string dialect could not reach parameters; this is the whole point of the merge."""
    fits = [
        {
            "message": "Party does not fit this table",
            "op": "lte",
            "left": {"kind": "parameter", "parameter": "partySize"},
            "right": {"kind": "objectProperty", "property": "seats"},
        }
    ]

    _expect_ok(_validate(fits, partySize=4))

    _expect_failure(_validate(fits, partySize=5), "Party does not fit this table")


def test_decimal_parameter_precondition_uses_exact_numeric_order() -> None:
    action = _action(
        [
            {
                "message": "amount must exceed the review floor",
                "op": "gt",
                "left": {"kind": "parameter", "parameter": "amount"},
                "right": {"kind": "literal", "value": "2"},
            }
        ]
    )
    action["definition"]["parameters"] = [{"apiName": "amount", "type": "decimal"}]

    _expect_ok(validate_action_request(action, _record(), {"amount": "10"}, ctx=_CTX))
    _expect_failure(
        validate_action_request(action, _record(), {"amount": "1.999999999999999999"}, ctx=_CTX),
        "amount must exceed",
    )


def test_the_authored_message_is_what_the_caller_sees() -> None:
    """Palantir shows the criterion's own failure message; a generic error hides the rule."""
    _expect_failure(
        validate_action_request(
            _action(
                [
                    {
                        "message": "Only a free table can be booked",
                        "op": "eq",
                        "left": {"kind": "objectProperty", "property": "status"},
                        "right": {"kind": "literal", "value": "FREE"},
                    }
                ]
            ),
            _record(status="BOOKED"),
            {},
            ctx=_CTX,
        ),
        "Only a free table can be booked",
    )


def test_current_user_is_available_to_a_precondition() -> None:
    criterion = [
        {
            "message": "Only the owning host may book",
            "op": "eq",
            "left": {"kind": "currentUser"},
            "right": {"kind": "parameter", "parameter": "hostId"},
        }
    ]

    _expect_ok(_validate(criterion, hostId="u-1"))

    _expect_failure(_validate(criterion, hostId="u-other"), "Only the owning host")


def test_grouped_criteria_evaluate_as_one_logical_statement() -> None:
    grouped = [
        {
            "message": "Table is not bookable",
            "all": [
                {
                    "op": "eq",
                    "left": {"kind": "objectProperty", "property": "status"},
                    "right": {"kind": "literal", "value": "FREE"},
                },
                {
                    "op": "lte",
                    "left": {"kind": "parameter", "parameter": "partySize"},
                    "right": {"kind": "objectProperty", "property": "seats"},
                },
            ],
        }
    ]

    _expect_ok(_validate(grouped, partySize=2))

    _expect_failure(_validate(grouped, partySize=9), "Table is not bookable")


# --- Palantir operator parity --------------------------------------------------------


def test_matches_is_a_full_string_regex_not_a_substring_search() -> None:
    """`ACTIVE` accepting `INACTIVE` is the near-miss that reads as correct in review."""
    criterion = [
        {
            "message": "status must match",
            "op": "matches",
            "left": {"kind": "objectProperty", "property": "status"},
            "right": {"kind": "literal", "value": "FR[EA]E"},
        }
    ]

    _expect_ok(_validate(criterion))

    _expect_failure(
        validate_action_request(_action(criterion), _record(status="NOT-FREE"), {}, ctx=_CTX), "status must match"
    )


@pytest.mark.parametrize(
    ("operator", "seats_value", "should_pass"),
    [
        ("eachIs", ["FREE", "FREE"], True),
        ("eachIs", ["FREE", "BOOKED"], False),
        ("eachIsNot", ["BOOKED", "HELD"], True),
        ("eachIsNot", ["BOOKED", "FREE"], False),
        # Vacuous truth: "every member of nothing" satisfies both.
        ("eachIs", [], True),
        ("eachIsNot", [], True),
    ],
)
def test_each_is_quantifies_over_every_member(operator: str, seats_value: list[str], should_pass: bool) -> None:
    criterion = [
        {
            "message": "every seat must qualify",
            "op": operator,
            "left": {"kind": "objectProperty", "property": "seatStates"},
            "right": {"kind": "literal", "value": "FREE"},
        }
    ]
    record = _record(seatStates=seats_value)

    if should_pass:
        _expect_ok(validate_action_request(_action(criterion), record, {}, ctx=_CTX))
        return
    _expect_failure(validate_action_request(_action(criterion), record, {}, ctx=_CTX), "every seat must qualify")


def test_each_is_refuses_a_non_collection_rather_than_widening_it() -> None:
    """Treating a scalar as a one-element list would hide an authoring mistake."""
    criterion = [
        {
            "message": "every seat must qualify",
            "op": "eachIs",
            "left": {"kind": "objectProperty", "property": "status"},
            "right": {"kind": "literal", "value": "FREE"},
        }
    ]

    _expect_failure(_validate(criterion), "every seat must qualify")


def test_contains_any_matches_palantir_includes_any() -> None:
    criterion = [
        {
            "message": "needs an allowed tag",
            "op": "containsAny",
            "left": {"kind": "objectProperty", "property": "tags"},
            "right": {"kind": "literal", "value": ["patio", "quiet"]},
        }
    ]

    _expect_ok(validate_action_request(_action(criterion), _record(tags=["quiet", "window"]), {}, ctx=_CTX))

    _expect_failure(
        validate_action_request(_action(criterion), _record(tags=["window"]), {}, ctx=_CTX), "needs an allowed tag"
    )


def test_an_invalid_regex_fails_loudly_instead_of_never_matching() -> None:
    criterion = [
        {
            "message": "status must match",
            "op": "matches",
            "left": {"kind": "objectProperty", "property": "status"},
            "right": {"kind": "literal", "value": "FR[EA"},
        }
    ]

    # An unparseable pattern is an authoring defect, not an unmet rule, so it surfaces as a
    # raised error rather than a validation result the caller might treat as "condition false".
    with pytest.raises(ValidationFailed, match="regex is invalid"):
        _validate(criterion)


def test_every_accepted_operator_is_dispatchable() -> None:
    """The allowlist and the dispatch table are two lists that must not drift apart.

    An operator in ``COMPARISON_OPERATORS`` but not in the table validates at authoring time and
    then raises "unsupported" at evaluation -- a rule that looks accepted until the action runs.
    ``exists`` is deliberately absent from the table: it is answered before a right-hand value is
    resolved, because "is this property set" has nothing to compare against.
    """
    from foundry_lite.domain.action_runtime.action_conditions import (
        _COMPARISONS,
        COMPARISON_OPERATORS,
    )

    assert COMPARISON_OPERATORS == frozenset(_COMPARISONS) | {"exists"}


# --- fail-closed type, shape, and missing-value boundaries ---------------------------


def _literal_condition(operator: str, left: object, right: object) -> dict[str, object]:
    return {
        "op": operator,
        "left": {"kind": "literal", "value": left},
        "right": {"kind": "literal", "value": right},
    }


@pytest.mark.parametrize(
    ("operator", "left", "right", "expected"),
    [
        ("eq", True, 1, False),
        ("neq", False, 0, True),
        ("in", True, [1], False),
        ("contains", [1], True, False),
        ("containsAny", [1], [True], False),
        ("eachIs", [True], 1, False),
        ("eachIsNot", [True], 1, True),
        ("eq", {"items": [1, True]}, {"items": [1.0, True]}, True),
        ("eq", {"items": [1, True]}, {"items": [1, 1]}, False),
    ],
)
def test_comparisons_use_json_identity_without_boolean_number_coercion(
    operator: str,
    left: object,
    right: object,
    expected: bool,
) -> None:
    assert evaluate_action_condition(_literal_condition(operator, left, right), _CONDITION_CTX) is expected


@pytest.mark.parametrize("operator", ["in", "notIn"])
def test_membership_with_a_non_collection_right_side_fails_closed(operator: str) -> None:
    condition = _literal_condition(operator, "host", None)

    assert evaluate_action_condition(condition, _CONDITION_CTX) is False


@pytest.mark.parametrize(
    ("operator", "left", "right", "expected"),
    [
        ("lt", True, 2, False),
        ("lt", 1, True, False),
        ("lt", 1, "2", False),
        ("lt", 1, 2.0, True),
        ("gte", "2026-08-14", "2026-08-13", True),
        ("lt", "2026-08-14T00:30:00+09:00", "2026-08-13T16:00:00Z", True),
        ("lt", "2026-08-14T00:00:00.000001Z", "2026-08-14T00:00:00.000002Z", True),
        ("gte", "2026-08-14T00:00:00.000001Z", "2026-08-14T00:00:00.000002Z", False),
        ("eq", "2026-08-14T01:00:00+09:00", "2026-08-13T16:00:00Z", False),
        ("lt", "2026-08-14T00:30:00", "2026-08-13T16:00:00Z", False),
        ("lt", "2026-08-14T00:30:00", "2026-08-14T01:30:00", False),
        ("lt", "2026-08-14T00:00:00.0000001Z", "2026-08-14T00:00:00.000002Z", False),
        ("gt", float("inf"), 1, False),
    ],
)
def test_ordering_only_compares_finite_numbers_or_two_strings(
    operator: str,
    left: object,
    right: object,
    expected: bool,
) -> None:
    assert evaluate_action_condition(_literal_condition(operator, left, right), _CONDITION_CTX) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, False),
        ("", False),
        ([], False),
        ({}, False),
        (0, True),
        (False, True),
        ("present", True),
    ],
)
def test_exists_means_non_null_and_non_empty(value: object, expected: bool) -> None:
    condition = {"op": "exists", "left": {"kind": "literal", "value": value}}

    assert evaluate_action_condition(condition, _CONDITION_CTX) is expected


def test_a_missing_runtime_value_cannot_be_inverted_into_permission() -> None:
    missing_equals_expected = {
        "op": "eq",
        "left": {"kind": "objectProperty", "property": "missing"},
        "right": {"kind": "literal", "value": "APPROVED"},
    }

    assert evaluate_action_condition({"not": missing_equals_expected}, _CONDITION_CTX) is False
    assert (
        evaluate_action_condition({"all": [_literal_condition("eq", 1, 1), missing_equals_expected]}, _CONDITION_CTX)
        is False
    )
    assert (
        evaluate_action_condition({"any": [_literal_condition("eq", 1, 1), missing_equals_expected]}, _CONDITION_CTX)
        is True
    )


@pytest.mark.parametrize(
    "condition",
    [
        {},
        {"all": []},
        {"all": [_literal_condition("eq", 1, 1)], "op": "eq"},
        {**_literal_condition("eq", 1, 1), "typo": True},
        {"op": "eq", "left": {"kind": "literal"}, "right": {"kind": "literal", "value": 1}},
        {
            "op": "eq",
            "left": {"kind": "parameter", "parameter": "status", "paramter": "status"},
            "right": {"kind": "literal", "value": "OPEN"},
        },
        {"op": "exists", "left": {"kind": "literal", "value": 1}, "right": {"kind": "literal", "value": 1}},
    ],
)
def test_condition_ast_rejects_ambiguous_or_unknown_shapes(condition: dict[str, object]) -> None:
    with pytest.raises(ValidationFailed):
        validate_action_condition(condition)


@pytest.mark.parametrize(
    ("pattern", "message"),
    [
        ("(a+)+$", "unsafe backtracking"),
        ("(a?)+$", "unsafe backtracking"),
        ("(a|aa)+$", "unsafe backtracking"),
        (r"(a)\1", "unsafe backtracking"),
        ("(?=a)a", "unsafe backtracking"),
        ("a" * 257, "too long"),
    ],
)
def test_regex_rejects_patterns_that_can_stall_action_evaluation(pattern: str, message: str) -> None:
    with pytest.raises(ValidationFailed, match=message):
        validate_action_condition(_literal_condition("matches", "aaaa", pattern))


def test_regex_accepts_a_repeated_fixed_width_non_capturing_group() -> None:
    condition = _literal_condition("matches", "abab", "(?:ab)+")

    validate_action_condition(condition)

    assert evaluate_action_condition(condition, _CONDITION_CTX) is True


# --- legacy dialect stays readable ---------------------------------------------------


def test_a_definition_written_before_the_contract_still_evaluates() -> None:
    legacy = [{"safeExpression": "object.status == 'FREE'", "message": "must be free"}]

    _expect_ok(_validate(legacy))

    _expect_failure(validate_action_request(_action(legacy), _record(status="BOOKED"), {}, ctx=_CTX), "must be free")


def test_structured_and_legacy_preconditions_can_coexist_on_one_action() -> None:
    mixed: list[dict[str, Any]] = [
        {"safeExpression": "object.status == 'FREE'", "message": "must be free"},
        {
            "message": "Party does not fit this table",
            "op": "lte",
            "left": {"kind": "parameter", "parameter": "partySize"},
            "right": {"kind": "objectProperty", "property": "seats"},
        },
    ]

    _expect_ok(_validate(mixed, partySize=3))

    _expect_failure(_validate(mixed, partySize=9), "Party does not fit")
