"""Package exports for the transforms sdk boundary."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib import import_module
from typing import Any, Literal, cast

RowMapper = Callable[[dict[str, object]], Mapping[str, object] | Sequence[Mapping[str, object]] | None]
RowErrorPolicy = Literal["raise", "quarantine", "dlq"]
RuntimeRowErrorRecorder = Callable[[int, Mapping[str, object], str, str], None]


@dataclass(frozen=True)
class Input:
    """Runtime-bound dataset input handle for Python transforms."""

    dataset_ref: str
    _read_arrow: Callable[[], Any] | None = field(default=None, repr=False, compare=False)
    _record_row_error: RuntimeRowErrorRecorder | None = field(default=None, repr=False, compare=False)

    def read_arrow(self) -> Any:
        """Read the input dataset as a PyArrow table."""
        if self._read_arrow is None:
            raise RuntimeError("Input handle is not bound to a running transform")
        return self._read_arrow()

    def read_rows(self) -> list[dict[str, object]]:
        """Read the input dataset as JSON-ready row dictionaries."""
        return [dict(row) for row in self.read_arrow().to_pylist()]

    def map_rows(
        self,
        mapper: RowMapper,
        *,
        on_error: RowErrorPolicy = "raise",
        error_kind: str = "PYTHON_ROW_ERROR",
    ) -> list[dict[str, object]]:
        """Map input rows while optionally quarantining row-local failures."""
        if on_error not in {"raise", "quarantine", "dlq"}:
            raise ValueError(f"unsupported row error policy: {on_error}")
        output_rows: list[dict[str, object]] = []
        for row_index, row in enumerate(self.read_rows()):
            try:
                output_rows.extend(_mapped_output_rows(mapper(row)))
            except Exception as exc:  # noqa: BLE001 - row-DLQ policy intentionally captures mapper failures
                if on_error == "raise":
                    raise
                self._record_quarantined_row(row_index, row, error_kind, exc)
        return output_rows

    def read_pandas(self) -> Any:
        """Read the input dataset as a pandas DataFrame."""
        return self.read_arrow().to_pandas()

    def read_polars(self) -> Any:
        """Read the input dataset as a Polars DataFrame."""
        pl = cast(Any, import_module("polars"))
        return pl.from_arrow(self.read_arrow())

    def _record_quarantined_row(
        self,
        row_index: int,
        row: Mapping[str, object],
        error_kind: str,
        exc: Exception,
    ) -> None:
        if self._record_row_error is None:
            raise RuntimeError("Input handle is not bound to a transform row-DLQ recorder") from exc
        self._record_row_error(row_index, row, error_kind, str(exc))


@dataclass(frozen=True)
class Output:
    """Runtime-bound dataset output handle for Python transforms."""

    dataset_ref: str
    _write_arrow: Callable[[Any], None] | None = field(default=None, repr=False, compare=False)

    def write_arrow(self, table: Any) -> None:
        """Write a PyArrow table to the transform output dataset."""
        if self._write_arrow is None:
            raise RuntimeError("Output handle is not bound to a running transform")
        self._write_arrow(table)

    def write_rows(self, rows: Sequence[Mapping[str, object]]) -> None:
        """Write row dictionaries to the transform output dataset."""
        import pyarrow as pa  # noqa: PLC0415 - optional import cost

        self.write_arrow(pa.Table.from_pylist([dict(row) for row in rows]))

    def write_pandas(self, frame: Any) -> None:
        """Write a pandas DataFrame to the transform output dataset."""
        import pyarrow as pa  # noqa: PLC0415 - optional import cost

        self.write_arrow(pa.Table.from_pandas(frame))

    def write_polars(self, frame: Any) -> None:
        """Write a Polars DataFrame to the transform output dataset."""
        self.write_arrow(frame.to_arrow())


def transform(**bindings: Input | Output):
    """Declare Python transform input and output bindings."""

    def decorator(func):
        func._foundry_lite_transform_bindings = bindings
        return func

    return decorator


def _mapped_output_rows(
    mapped: Mapping[str, object] | Sequence[Mapping[str, object]] | None,
) -> list[dict[str, object]]:
    if mapped is None:
        return []
    if isinstance(mapped, Mapping):
        return [dict(mapped)]
    if isinstance(mapped, Sequence) and not isinstance(mapped, str | bytes):
        if all(isinstance(row, Mapping) for row in mapped):
            return [dict(row) for row in mapped]
    raise TypeError("row mapper must return a mapping, a sequence of mappings, or None")


def _runtime_input(
    dataset_ref: str,
    read_arrow: Callable[[], Any],
    record_row_error: RuntimeRowErrorRecorder | None = None,
) -> Input:
    """Create an input handle bound by the local transform runner."""
    return Input(dataset_ref=dataset_ref, _read_arrow=read_arrow, _record_row_error=record_row_error)


def _runtime_output(dataset_ref: str, write_arrow: Callable[[Any], None]) -> Output:
    """Create an output handle bound by the local transform runner."""
    return Output(dataset_ref=dataset_ref, _write_arrow=write_arrow)
