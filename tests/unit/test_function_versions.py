"""Version ranges are what let a function be patched underneath a running Action.

Palantir publishes a function version as immutable and lets a consumer depend on it exactly or by
range: a pin for strict uptime requirements, a range for a downtime-less upgrade. The rules that
make a range safe are the compatibility rules, so these tests pin the boundaries rather than the
happy path -- a caret that accepted a major would silently hand an Action a function that
announced it might break.
"""

from __future__ import annotations

import pytest
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.domain.ontology.function_versions import FunctionVersion, is_range, satisfies


@pytest.mark.parametrize(
    ("published", "requirement", "expected"),
    [
        # Exact: what a caller pins when no change at all is acceptable.
        ("1.4.2", "1.4.2", True),
        ("1.4.3", "1.4.2", False),
        ("1.4.1", "1.4.2", False),
        # Caret: patches and additive minors. A major is the announcement that consumers may
        # break, so it is the one thing a compatible range must never accept.
        ("1.4.3", "^1.4.2", True),
        ("1.5.0", "^1.4.2", True),
        ("1.4.1", "^1.4.2", False),
        ("2.0.0", "^1.4.2", False),
        ("0.9.9", "^1.0.0", False),
        # Tilde: bug fixes only, for a consumer that does not want new surface.
        ("1.4.9", "~1.4.2", True),
        ("1.4.2", "~1.4.2", True),
        ("1.5.0", "~1.4.2", False),
        ("1.4.1", "~1.4.2", False),
    ],
)
def test_a_requirement_admits_exactly_the_versions_it_should(published: str, requirement: str, expected: bool) -> None:
    assert satisfies(published, requirement) is expected


def test_legacy_vn_identifiers_keep_resolving() -> None:
    """`v1` predates semver here and is still in persisted definitions; reading it as 1.0.0 is
    what keeps a stored snapshot executable after this lands."""
    assert FunctionVersion.parse("v1") == FunctionVersion(1, 0, 0)
    assert satisfies("v1", "v1")
    assert satisfies("v1", "^1.0.0")
    assert not satisfies("v2", "^1.0.0")


def test_a_version_that_is_neither_semver_nor_legacy_is_refused() -> None:
    """Silently accepting `latest` would turn a pin into a range without anyone choosing that."""
    with pytest.raises(ValidationFailed, match="must be semver"):
        FunctionVersion.parse("latest")


def test_an_empty_requirement_is_refused() -> None:
    with pytest.raises(ValidationFailed, match="requirement is empty"):
        satisfies("1.0.0", "^")


def test_versions_order_by_precedence_not_by_string() -> None:
    """String ordering puts 1.10.0 before 1.9.0, which is how a range check quietly goes wrong."""
    assert FunctionVersion.parse("1.9.0") < FunctionVersion.parse("1.10.0")
    assert satisfies("1.10.0", "^1.9.0")


@pytest.mark.parametrize(
    ("requirement", "expected"),
    [("^1.0.0", True), ("~1.0.0", True), ("1.0.0", False), ("v1", False)],
)
def test_a_range_is_distinguishable_from_a_pin(requirement: str, expected: bool) -> None:
    """The distinction is reported on failure, because "your pin is stale" and "nothing in range
    is deployed" are different problems for whoever is reading the error."""
    assert is_range(requirement) is expected


def test_a_version_renders_as_semver_regardless_of_how_it_was_written() -> None:
    """A legacy `v1` must not surface as `v1` in an error after being read as 1.0.0, or the
    message would contradict the range syntax the reader is being asked to use."""
    assert str(FunctionVersion.parse("v1")) == "1.0.0"
    assert str(FunctionVersion.parse("2.1.4")) == "2.1.4"
