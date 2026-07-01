from __future__ import annotations

import pyarrow as pa
import pytest
from foundry_lite.transforms_sdk import Input, Output, _mapped_output_rows, _runtime_input, _runtime_output


def test_transforms_sdk_runtime_input_map_rows_and_quarantine_recorder() -> None:
    recorded: list[tuple[int, dict[str, object], str, str]] = []
    table = pa.Table.from_pylist([{"order_id": "O-1"}, {"order_id": "O-2"}])
    runtime_input = _runtime_input(
        "raw.orders",
        lambda: table,
        lambda index, row, kind, message: recorded.append((index, dict(row), kind, message)),
    )

    mapped = runtime_input.map_rows(
        lambda row: {"order_id": row["order_id"], "status": "OK"} if row["order_id"] == "O-1" else None
    )
    quarantined = runtime_input.map_rows(
        lambda row: _raise_for_order(row) if row["order_id"] == "O-2" else row,
        on_error="quarantine",
        error_kind="BAD_ROW",
    )

    assert runtime_input.read_rows() == [{"order_id": "O-1"}, {"order_id": "O-2"}]
    assert mapped == [{"order_id": "O-1", "status": "OK"}]
    assert quarantined == [{"order_id": "O-1"}]
    assert recorded == [(1, {"order_id": "O-2"}, "BAD_ROW", "bad order")]
    with pytest.raises(ValueError, match="unsupported row error policy"):
        runtime_input.map_rows(lambda row: row, on_error="skip")  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="row-DLQ recorder"):
        _runtime_input("raw.orders", lambda: table).map_rows(lambda _row: _raise_for_order({}), on_error="dlq")


def test_transforms_sdk_runtime_output_and_mapping_edges() -> None:
    written: list[pa.Table] = []
    output = _runtime_output("clean.orders", written.append)

    output.write_rows([{"order_id": "O-1"}])
    output.write_polars(_PolarsLike(pa.Table.from_pylist([{"order_id": "O-3"}])))

    assert [table.to_pylist() for table in written] == [
        [{"order_id": "O-1"}],
        [{"order_id": "O-3"}],
    ]
    assert _mapped_output_rows(None) == []
    assert _mapped_output_rows({"order_id": "O-1"}) == [{"order_id": "O-1"}]
    assert _mapped_output_rows([{"order_id": "O-1"}]) == [{"order_id": "O-1"}]
    with pytest.raises(RuntimeError, match="Output handle is not bound"):
        Output("clean.orders").write_rows([{"order_id": "O-1"}])
    with pytest.raises(RuntimeError, match="Input handle is not bound"):
        Input("raw.orders").read_rows()
    with pytest.raises(TypeError, match="row mapper"):
        _mapped_output_rows("bad")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="row mapper"):
        _mapped_output_rows([{"ok": True}, "bad"])  # type: ignore[list-item]


def _raise_for_order(row: object) -> object:
    del row
    raise ValueError("bad order")


class _PolarsLike:
    def __init__(self, table: pa.Table) -> None:
        self._table = table

    def to_arrow(self) -> pa.Table:
        return self._table
