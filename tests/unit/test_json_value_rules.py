from __future__ import annotations

from foundry_lite.domain.json_values import is_bounded_json_value


def test_bounded_json_rejects_non_finite_numbers_and_non_string_keys() -> None:
    assert is_bounded_json_value({"nested": [None, True, 1, 1.5, "ok"]})
    assert not is_bounded_json_value(float("nan"))
    assert not is_bounded_json_value(float("inf"))
    assert not is_bounded_json_value({1: "not-json-object-key"})


def test_bounded_json_rejects_cycles_and_excessive_depth() -> None:
    cyclic: list[object] = []
    cyclic.append(cyclic)
    assert not is_bounded_json_value(cyclic)

    nested: object = "leaf"
    for _ in range(66):
        nested = [nested]
    assert not is_bounded_json_value(nested)
