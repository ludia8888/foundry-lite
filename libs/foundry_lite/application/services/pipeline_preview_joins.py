"""Row-join helpers for bounded, non-committing Pipeline Builder previews.

Split out of ``pipeline_preview_transforms`` to keep that module under the
application module-size limit. The governance-sensitive part lives here: a
joined row must carry the *strongest* classification of its inputs, because a
plain dict merge lets the right input's ``securityEnvelope`` silently overwrite
the left's and understate the preview's governance passport.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.domain.classification import classification_rank

JsonObject = dict[str, object]


def join_rows(
    left: Sequence[JsonObject],
    right: Sequence[JsonObject],
    left_key: str,
    right_key: str,
    join_type: str,
) -> list[JsonObject]:
    rows, matched_right = _left_join_rows(left, right, left_key, right_key, join_type)
    if join_type in {"right", "full outer"}:
        rows.extend(dict(row) for index, row in enumerate(right) if index not in matched_right)
    return rows


def _left_join_rows(
    left: Sequence[JsonObject],
    right: Sequence[JsonObject],
    left_key: str,
    right_key: str,
    join_type: str,
) -> tuple[list[JsonObject], set[int]]:
    rows: list[JsonObject] = []
    matched_right: set[int] = set()
    for left_row in left:
        matches = [(index, row) for index, row in enumerate(right) if left_row.get(left_key) == row.get(right_key)]
        rows.extend(_merged_join_row(left_row, right_row) for _, right_row in matches)
        matched_right.update(index for index, _ in matches)
        if not matches and join_type in {"left", "full outer"}:
            rows.append(dict(left_row))
    return rows, matched_right


def _merged_join_row(left_row: JsonObject, right_row: JsonObject) -> JsonObject:
    merged = {**left_row, **right_row}
    # The dict-merge lets the right input's securityEnvelope overwrite the left's,
    # dropping the left classification from the joined row and understating the
    # preview's governance passport. The merged row must carry the strongest
    # classification of its inputs.
    envelope = _stronger_security_envelope(left_row.get("securityEnvelope"), right_row.get("securityEnvelope"))
    if envelope is not None:
        merged["securityEnvelope"] = envelope
    else:
        merged.pop("securityEnvelope", None)
    return merged


def _stronger_security_envelope(left: object, right: object) -> Mapping[str, object] | None:
    left_env = left if isinstance(left, Mapping) else None
    right_env = right if isinstance(right, Mapping) else None
    if left_env is None:
        return right_env
    if right_env is None:
        return left_env
    left_rank = classification_rank(left_env.get("classification"))
    right_rank = classification_rank(right_env.get("classification"))
    # Fail closed: an unrankable (unknown) classification is treated as at least
    # as strong as any known one, so it is never dropped in favor of a weaker label.
    if left_rank is None:
        return left_env
    if right_rank is None:
        return right_env
    return left_env if left_rank >= right_rank else right_env
