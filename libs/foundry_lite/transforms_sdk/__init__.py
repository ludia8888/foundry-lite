from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Input:
    dataset_ref: str


@dataclass(frozen=True)
class Output:
    dataset_ref: str


def transform(**bindings: Input | Output):
    def decorator(func):
        func._foundry_lite_transform_bindings = bindings
        return func

    return decorator
