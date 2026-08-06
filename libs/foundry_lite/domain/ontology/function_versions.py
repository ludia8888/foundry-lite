"""Function version identity and range matching.

Palantir versions a published function with semver, treats a published version as immutable, and
lets a consumer depend either on an exact version or on a range. A range is what makes a
downtime-less upgrade possible: an Action keeps working while a patched function is published
underneath it, and a caller with strict uptime requirements pins instead.

The rules here are the compatibility rules that make that safe -- a caret range accepts patches
and additive minors but never a major, because a major is exactly the announcement that existing
consumers may break.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from foundry_lite.domain.errors import ValidationFailed

# `v1` predates semver in this codebase and still appears in persisted definitions, so it is read
# as 1.0.0 rather than rejected -- a stored snapshot must keep resolving after this lands.
_LEGACY_PATTERN = re.compile(r"^v(\d+)$")
_SEMVER_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
# `.*` rather than `.+`: with `.+` the engine backtracks on a bare "^" and reads the operator
# itself as the version, reporting a malformed version instead of an empty requirement.
_RANGE_PATTERN = re.compile(r"^([\^~])?(.*)$")


@dataclass(frozen=True, slots=True, order=True)
class FunctionVersion:
    """One published, immutable function version."""

    major: int
    minor: int
    patch: int

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    @classmethod
    def parse(cls, raw: str) -> FunctionVersion:
        legacy = _LEGACY_PATTERN.match(raw)
        if legacy is not None:
            return cls(int(legacy.group(1)), 0, 0)
        match = _SEMVER_PATTERN.match(raw)
        if match is None:
            raise ValidationFailed(
                "function version must be semver or a legacy vN identifier",
                details={"value": raw},
            )
        return cls(int(match.group(1)), int(match.group(2)), int(match.group(3)))


def satisfies(version: str, requirement: str) -> bool:
    """Does a published version satisfy a dependency requirement?

    Three forms, matching what a consumer can express in Palantir:

    - ``1.4.2``  exact. What an Action with strict uptime requirements pins to.
    - ``^1.4.2`` compatible. Patches and additive minors, never a new major.
    - ``~1.4.2`` patches only. For a consumer that trusts bug fixes but not new surface.
    """
    published = FunctionVersion.parse(version)
    operator, base_raw = _split_requirement(requirement)
    base = FunctionVersion.parse(base_raw)
    if operator is None:
        return published == base
    if published < base or published.major != base.major:
        return False
    return operator == "^" or published.minor == base.minor


def is_range(requirement: str) -> bool:
    """Whether a requirement admits more than one version, so an upgrade can land underneath it."""
    return requirement.startswith(("^", "~"))


def _split_requirement(requirement: str) -> tuple[str | None, str]:
    match = _RANGE_PATTERN.match(requirement.strip())
    if match is None or not match.group(2):
        raise ValidationFailed("function version requirement is empty", details={"value": requirement})
    return match.group(1), match.group(2)
