"""Pure dataset quality contract rules."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

ROW_QUARANTINE_CHECK_TYPES = frozenset({"not_null", "unique", "unique_tuple", "accepted_values"})


def allows_empty_dataset(checks: Sequence[Mapping[str, object]]) -> bool:
    """Return whether a quality contract explicitly allows an empty dataset."""
    return any(check.get("type") == "allow_empty" for check in checks)


def default_primary_key_checks(primary_key: Sequence[str]) -> list[dict[str, object]]:
    """Return default dataset quality checks implied by the dataset primary key."""
    checks: list[dict[str, object]] = []
    for pk in primary_key:
        checks.append({"type": "not_null", "columns": [pk]})
    if len(primary_key) == 1:
        checks.append({"type": "unique", "column": primary_key[0]})
    elif len(primary_key) > 1:
        checks.append({"type": "unique_tuple", "columns": list(primary_key)})
    return checks


def dataset_quality_policy_status(
    result_status: object,
    check_type: object,
    severity: object,
) -> str:
    """Map a check result and severity into the commit policy outcome."""
    if str(result_status) != "failed":
        return "PASS"
    normalized_severity = str(severity or "error").lower()
    if normalized_severity in {"warn", "warning"}:
        return "WARN"
    if normalized_severity in {"quarantine", "quarantined"} and is_row_quarantine_check(check_type):
        return "QUARANTINE"
    return "BLOCK_COMMIT"


def is_row_quarantine_check(check_type: object) -> bool:
    """Return whether a failed check can safely quarantine row-level failures."""
    return str(check_type) in ROW_QUARANTINE_CHECK_TYPES
