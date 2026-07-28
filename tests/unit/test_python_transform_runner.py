from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from foundry_lite.infrastructure.runners import python_transform_runner as runner
from foundry_lite.infrastructure.runners.python_transform_runner import RunnerFailure, execute_manifest, main


def test_python_transform_runner_executes_returned_rows_from_pinned_inputs(tmp_path: Path) -> None:
    input_path = tmp_path / "input.parquet"
    output_path = tmp_path / "output.parquet"
    source_path = tmp_path / "transform.py"
    pq.write_table(pa.Table.from_pylist([{"order_id": "O-1", "amount": 10}]), input_path)
    source_path.write_text(
        "def compute(orders):\n"
        "    return [\n"
        "        {'order_id': row['order_id'], 'amount': row['amount'] * 3}\n"
        "        for row in orders.read_rows()\n"
        "    ]\n",
        encoding="utf-8",
    )

    result = execute_manifest(
        _manifest(
            source_path,
            output_path,
            function_name="compute",
            input_refs={"orders": "raw.orders"},
            input_paths={"raw.orders": [str(input_path)]},
        )
    )

    assert result == {"schemaVersion": 1, "status": "succeeded", "deadLetters": []}
    assert pq.read_table(output_path).to_pylist() == [{"order_id": "O-1", "amount": 30}]


def test_python_transform_runner_supports_sdk_output_and_row_dead_letters(tmp_path: Path) -> None:
    input_path = tmp_path / "input.parquet"
    output_path = tmp_path / "output.parquet"
    source_path = tmp_path / "transform.py"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {"order_id": "O-1", "amount": 10},
                {"order_id": "O-2", "amount": -1},
            ]
        ),
        input_path,
    )
    source_path.write_text(
        "from foundry_lite.transforms_sdk import Input, Output, transform\n"
        "@transform(orders=Input('raw.orders'), out=Output('clean.orders'))\n"
        "def compute(orders, out):\n"
        "    def keep_positive(row):\n"
        "        if row['amount'] < 0:\n"
        "            raise ValueError('negative amount')\n"
        "        return row\n"
        "    out.write_rows(orders.map_rows(keep_positive, on_error='dlq'))\n",
        encoding="utf-8",
    )

    result = execute_manifest(
        _manifest(
            source_path,
            output_path,
            function_name=None,
            input_refs={"orders": "raw.orders"},
            input_paths={"raw.orders": [str(input_path)]},
        )
    )

    assert result["status"] == "succeeded"
    assert result["deadLetters"] == [
        {
            "inputDatasetRef": "raw.orders",
            "rowIndex": 1,
            "payload": {"order_id": "O-2", "amount": -1},
            "errorKind": "PYTHON_ROW_ERROR",
            "errorMessage": "negative amount",
        }
    ]
    assert pq.read_table(output_path).to_pylist() == [{"order_id": "O-1", "amount": 10}]


def test_python_transform_runner_writes_hashed_failure_without_raw_message(tmp_path: Path) -> None:
    source_path = tmp_path / "transform.py"
    manifest_path = tmp_path / "request.json"
    result_path = tmp_path / "result.json"
    source_path.write_text("raise RuntimeError('sensitive customer row')\n", encoding="utf-8")
    manifest_path.write_text(
        json.dumps(_manifest(source_path, tmp_path / "output.parquet", function_name=None)),
        encoding="utf-8",
    )

    exit_code = main([str(manifest_path), str(result_path)])
    result = json.loads(result_path.read_text(encoding="utf-8"))

    assert exit_code == 2
    assert result["status"] == "failed"
    assert result["failure"]["type"] == "entrypoint_load_failed"
    assert result["failure"]["exceptionType"] == "RuntimeError"
    assert result["failure"]["messageSha256"] == hashlib.sha256(b"sensitive customer row").hexdigest()
    assert "sensitive customer row" not in result_path.read_text(encoding="utf-8")


def test_python_transform_runner_rejects_missing_output_with_typed_failure(tmp_path: Path) -> None:
    source_path = tmp_path / "transform.py"
    manifest_path = tmp_path / "request.json"
    result_path = tmp_path / "result.json"
    output_path = tmp_path / "output.parquet"
    output_path.touch()
    source_path.write_text("def compute():\n    return None\n", encoding="utf-8")
    manifest_path.write_text(
        json.dumps(_manifest(source_path, output_path, function_name="compute")),
        encoding="utf-8",
    )

    exit_code = main([str(manifest_path), str(result_path)])
    result = json.loads(result_path.read_text(encoding="utf-8"))

    assert exit_code == 2
    assert result["failure"]["type"] == "output_missing"
    assert result["failure"]["exceptionType"] == "FileNotFoundError"


def test_python_transform_runner_rejects_unreadable_parquet_output(tmp_path: Path) -> None:
    source_path = tmp_path / "transform.py"
    manifest_path = tmp_path / "request.json"
    result_path = tmp_path / "result.json"
    output_path = tmp_path / "output.parquet"
    output_path.write_bytes(b"not parquet")
    source_path.write_text("def compute():\n    return None\n", encoding="utf-8")
    manifest_path.write_text(
        json.dumps(_manifest(source_path, output_path, function_name="compute")),
        encoding="utf-8",
    )

    exit_code = main([str(manifest_path), str(result_path)])
    result = json.loads(result_path.read_text(encoding="utf-8"))

    assert exit_code == 2
    assert result["failure"]["type"] == "output_invalid"


def test_python_transform_runner_rejects_source_changed_after_manifest(tmp_path: Path) -> None:
    source_path = tmp_path / "transform.py"
    source_path.write_text("def compute():\n    return [{'version': 1}]\n", encoding="utf-8")
    manifest = _manifest(source_path, tmp_path / "output.parquet", function_name="compute")
    source_path.write_text("def compute():\n    return [{'version': 2}]\n", encoding="utf-8")

    with pytest.raises(RunnerFailure) as captured:
        execute_manifest(manifest)

    assert captured.value.failure_type == "runner_contract_error"


def test_python_transform_runner_main_validates_cli_and_fatal_runner_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert main([]) == 64

    manifest_path = tmp_path / "request.json"
    result_path = tmp_path / "result.json"
    manifest_path.write_text('{"schemaVersion":1}', encoding="utf-8")
    monkeypatch.setattr(
        runner,
        "execute_manifest",
        lambda _manifest: (_ for _ in ()).throw(KeyboardInterrupt("fatal")),
    )

    assert main([str(manifest_path), str(result_path)]) == 2
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["failure"]["type"] == "runner_contract_error"
    assert result["failure"]["exceptionType"] == "KeyboardInterrupt"


@pytest.mark.parametrize("payload", ["{", "[]", '{"schemaVersion":2}'])
def test_python_transform_runner_manifest_parser_rejects_invalid_contract(
    tmp_path: Path,
    payload: str,
) -> None:
    manifest_path = tmp_path / "request.json"
    result_path = tmp_path / "result.json"
    manifest_path.write_text(payload, encoding="utf-8")

    assert main([str(manifest_path), str(result_path)]) == 2
    assert json.loads(result_path.read_text(encoding="utf-8"))["failure"]["type"] == ("runner_contract_error")


def test_python_transform_runner_rejects_missing_loader_and_callable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "transform.py"
    source_path.write_text("def compute():\n    return []\n", encoding="utf-8")
    monkeypatch.setattr(runner.importlib.util, "spec_from_file_location", lambda *_args: None)
    with pytest.raises(RunnerFailure):
        runner._load_module(source_path)

    module = ModuleType("test_transform")
    with pytest.raises(RunnerFailure) as raised:
        runner._transform_callable(module, "missing")
    assert raised.value.failure_type == "function_not_found"
    with pytest.raises(RunnerFailure) as raised:
        runner._transform_callable(module, None)
    assert raised.value.failure_type == "callable_not_found"

    def compute() -> list[dict[str, object]]:
        return []

    module.compute = compute
    assert runner._transform_callable(module, None) is compute


def test_python_transform_runner_supports_output_and_out_parameter_conventions(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "transform.py"
    output_path = tmp_path / "output.parquet"
    source_path.write_text(
        "def compute(output):\n    output.write_rows([{'kind': 'output'}])\n",
        encoding="utf-8",
    )
    assert execute_manifest(_manifest(source_path, output_path, function_name="compute"))["status"] == "succeeded"
    assert pq.read_table(output_path).to_pylist() == [{"kind": "output"}]

    source_path.write_text(
        "def compute(out):\n    out.write_rows([{'kind': 'out'}])\n",
        encoding="utf-8",
    )
    assert execute_manifest(_manifest(source_path, output_path, function_name="compute"))["status"] == "succeeded"
    assert pq.read_table(output_path).to_pylist() == [{"kind": "out"}]


@pytest.mark.parametrize(
    ("returned", "failure_type"),
    [
        ({"not": "rows"}, "invalid_return_type"),
        ([{"ok": True}, "bad"], "invalid_return_rows"),
    ],
)
def test_python_transform_runner_rejects_invalid_return_shapes(
    tmp_path: Path,
    returned: object,
    failure_type: str,
) -> None:
    manifest = {"outputDatasetRef": "clean.orders"}
    with pytest.raises(RunnerFailure) as raised:
        runner._write_returned_rows(
            manifest,
            lambda: None,
            tmp_path / "output.parquet",
            returned,
        )
    assert raised.value.failure_type == failure_type


def test_python_transform_runner_reads_multiple_parts_and_normalizes_dead_letter_json(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.parquet"
    second = tmp_path / "second.parquet"
    pq.write_table(pa.Table.from_pylist([{"id": 1}]), first)
    pq.write_table(pa.Table.from_pylist([{"id": 2}]), second)

    assert runner._read_arrow_tables((first, second)).to_pylist() == [{"id": 1}, {"id": 2}]
    value = {
        "whole": Decimal("2"),
        "fraction": Decimal("2.5"),
        "date": date(2026, 1, 1),
        "datetime": datetime(2026, 1, 1, 1, 2, 3),
        "nested": [Decimal("3.5")],
    }
    assert runner._json_ready(value) == {
        "whole": 2,
        "fraction": 2.5,
        "date": "2026-01-01",
        "datetime": "2026-01-01T01:02:03",
        "nested": [3.5],
    }


@pytest.mark.parametrize(
    "function",
    [
        lambda: runner._required_string({"field": ""}, "field"),
        lambda: runner._optional_string({"field": 1}, "field"),
        lambda: runner._string_mapping({"field": []}, "field"),
        lambda: runner._string_mapping({"field": {"key": 1}}, "field"),
        lambda: runner._path_sequence_mapping({"field": []}, "field"),
        lambda: runner._path_sequence_mapping({"field": {"ref": []}}, "field"),
    ],
)
def test_python_transform_runner_rejects_invalid_manifest_field_shapes(
    function: Callable[[], object],
) -> None:
    with pytest.raises(RunnerFailure):
        function()


def _manifest(
    source_path: Path,
    output_path: Path,
    *,
    function_name: str | None,
    input_refs: dict[str, str] | None = None,
    input_paths: dict[str, list[str]] | None = None,
) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "sourceSha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        "sourcePath": str(source_path),
        "functionName": function_name,
        "inputRefsByAlias": input_refs or {},
        "inputPathsByRef": input_paths or {},
        "outputDatasetRef": "clean.orders",
        "outputPath": str(output_path),
    }
