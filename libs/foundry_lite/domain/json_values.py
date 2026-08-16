"""Bounded JSON-value validation for durable runtime evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import isfinite
from typing import cast

_MAX_JSON_DEPTH = 64


def is_bounded_json_value(value: object, *, max_depth: int = _MAX_JSON_DEPTH) -> bool:
    """Return whether a value can be persisted as finite, acyclic JSON."""

    return _is_json_value(value, depth=0, max_depth=max_depth, ancestors=set())


def _is_json_value(value: object, *, depth: int, max_depth: int, ancestors: set[int]) -> bool:
    if depth > max_depth:
        return False
    if value is None or isinstance(value, bool | str | int):
        return True
    if isinstance(value, float):
        return isfinite(value)
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        return _is_json_mapping(mapping, depth=depth, max_depth=max_depth, ancestors=ancestors)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        sequence = cast(Sequence[object], value)
        return _is_json_sequence(sequence, depth=depth, max_depth=max_depth, ancestors=ancestors)
    return False


def _is_json_mapping(value: Mapping[object, object], *, depth: int, max_depth: int, ancestors: set[int]) -> bool:
    identity = id(value)
    if identity in ancestors:
        return False
    ancestors.add(identity)
    try:
        return all(
            isinstance(key, str) and _is_json_value(item, depth=depth + 1, max_depth=max_depth, ancestors=ancestors)
            for key, item in value.items()
        )
    finally:
        ancestors.remove(identity)


def _is_json_sequence(value: Sequence[object], *, depth: int, max_depth: int, ancestors: set[int]) -> bool:
    identity = id(value)
    if identity in ancestors:
        return False
    ancestors.add(identity)
    try:
        return all(_is_json_value(item, depth=depth + 1, max_depth=max_depth, ancestors=ancestors) for item in value)
    finally:
        ancestors.remove(identity)


__all__ = ["is_bounded_json_value"]
