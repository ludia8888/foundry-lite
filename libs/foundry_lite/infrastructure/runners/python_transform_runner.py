"""Trusted Python-transform runner intended to execute only inside a sandbox."""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pyarrow as pa
import pyarrow.parquet as pq

from foundry_lite.transforms_sdk import Output, _runtime_input, _runtime_output

_RESULT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class RunnerFailure(Exception):
    failure_type: str
    exception_type: str
    message_sha256: str


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        return 64
    manifest_path = Path(args[0])
    result_path = Path(args[1])
    try:
        result = execute_manifest(_load_manifest(manifest_path))
    except RunnerFailure as exc:
        result = _failure_result(exc)
    except BaseException as exc:  # noqa: BLE001 - sandbox must return typed evidence for fatal user exits
        result = _failure_result(_runner_failure("runner_contract_error", exc))
    _write_result(result_path, result)
    return 0 if result["status"] == "succeeded" else 2


def execute_manifest(manifest: Mapping[str, object]) -> dict[str, object]:
    source_path = _required_path(manifest, "sourcePath")
    output_path = _required_path(manifest, "outputPath")
    dead_letters: list[dict[str, object]] = []
    try:
        _require_source_hash(manifest, source_path)
        module = _load_module(source_path)
        callable_obj = _transform_callable(module, _optional_string(manifest, "functionName"))
        result = callable_obj(**_transform_kwargs(manifest, callable_obj, dead_letters))
        _write_returned_rows(manifest, callable_obj, output_path, result)
        _require_output_file(output_path)
    except RunnerFailure:
        raise
    except BaseException as exc:  # noqa: BLE001 - user code is untrusted and may raise SystemExit
        raise _runner_failure("user_code_error", exc) from exc
    return {
        "schemaVersion": _RESULT_SCHEMA_VERSION,
        "status": "succeeded",
        "deadLetters": dead_letters,
    }


def _load_manifest(path: Path) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise _runner_failure("runner_contract_error", exc) from exc
    if not isinstance(value, Mapping):
        raise _runner_failure("runner_contract_error", TypeError("manifest must be an object"))
    if value.get("schemaVersion") != _RESULT_SCHEMA_VERSION:
        raise _runner_failure("runner_contract_error", ValueError("unsupported manifest schema"))
    return value


def _load_module(source_path: Path) -> ModuleType:
    module_name = f"_foundry_lite_transform_{hashlib.sha256(source_path.read_bytes()).hexdigest()[:16]}"
    spec = importlib.util.spec_from_file_location(module_name, source_path)
    if spec is None or spec.loader is None:
        raise _runner_failure("entrypoint_load_failed", ImportError("entrypoint loader unavailable"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException as exc:  # noqa: BLE001 - syntax/import errors belong to user code
        raise _runner_failure("entrypoint_load_failed", exc) from exc
    finally:
        sys.modules.pop(module_name, None)
    return module


def _require_source_hash(manifest: Mapping[str, object], source_path: Path) -> None:
    expected = _required_string(manifest, "sourceSha256")
    actual = hashlib.sha256(source_path.read_bytes()).hexdigest()
    if actual != expected:
        raise _runner_failure("runner_contract_error", ValueError("transform source hash mismatch"))


def _transform_callable(module: ModuleType, function_name: str | None) -> Callable[..., object]:
    if function_name is not None:
        candidate = getattr(module, function_name, None)
        if callable(candidate):
            return cast(Callable[..., object], candidate)
        raise _runner_failure("function_not_found", LookupError("configured function was not found"))
    decorated = _decorated_callables(module)
    if len(decorated) == 1:
        return decorated[0]
    candidate = getattr(module, "compute", None)
    if callable(candidate):
        return cast(Callable[..., object], candidate)
    raise _runner_failure("callable_not_found", LookupError("transform callable was not found"))


def _decorated_callables(module: ModuleType) -> list[Callable[..., object]]:
    return [
        cast(Callable[..., object], value)
        for value in vars(module).values()
        if callable(value) and isinstance(getattr(value, "_foundry_lite_transform_bindings", None), Mapping)
    ]


def _transform_kwargs(
    manifest: Mapping[str, object],
    callable_obj: Callable[..., object],
    dead_letters: list[dict[str, object]],
) -> dict[str, object]:
    input_refs = _string_mapping(manifest, "inputRefsByAlias")
    input_paths = _path_sequence_mapping(manifest, "inputPathsByRef")
    kwargs: dict[str, object] = {
        alias: _runtime_input(
            dataset_ref,
            _arrow_reader(input_paths[dataset_ref]),
            _row_error_recorder(dead_letters, dataset_ref),
        )
        for alias, dataset_ref in input_refs.items()
    }
    kwargs.update(_output_kwargs(manifest, callable_obj))
    return kwargs


def _output_kwargs(manifest: Mapping[str, object], callable_obj: Callable[..., object]) -> dict[str, Output]:
    output = _runtime_output(
        _required_string(manifest, "outputDatasetRef"),
        _arrow_writer(_required_path(manifest, "outputPath")),
    )
    bindings = getattr(callable_obj, "_foundry_lite_transform_bindings", {})
    aliases = [name for name, binding in bindings.items() if isinstance(binding, Output)]
    if aliases:
        return {alias: output for alias in aliases}
    parameters = inspect.signature(callable_obj).parameters
    if "output" in parameters:
        return {"output": output}
    if "out" in parameters:
        return {"out": output}
    return {}


def _write_returned_rows(
    manifest: Mapping[str, object],
    callable_obj: Callable[..., object],
    output_path: Path,
    result: object,
) -> None:
    del callable_obj
    if result is None:
        return
    if not isinstance(result, Sequence) or isinstance(result, str | bytes):
        raise _runner_failure("invalid_return_type", TypeError("return value must be a row sequence"))
    if not all(isinstance(row, Mapping) for row in result):
        raise _runner_failure("invalid_return_rows", TypeError("returned rows must be mappings"))
    output = _runtime_output(_required_string(manifest, "outputDatasetRef"), _arrow_writer(output_path))
    output.write_rows(cast(Sequence[Mapping[str, object]], result))


def _require_output_file(output_path: Path) -> None:
    if output_path.is_symlink() or not output_path.is_file() or output_path.stat().st_size == 0:
        raise _runner_failure("output_missing", FileNotFoundError("transform output was not written"))
    try:
        pq.ParquetFile(output_path)
    except Exception as exc:
        raise _runner_failure("output_invalid", exc) from exc


def _row_error_recorder(
    dead_letters: list[dict[str, object]],
    dataset_ref: str,
) -> Callable[[int, Mapping[str, object], str, str], None]:
    def record(row_index: int, row: Mapping[str, object], error_kind: str, error_message: str) -> None:
        dead_letters.append(
            {
                "inputDatasetRef": dataset_ref,
                "rowIndex": row_index,
                "payload": _json_ready(dict(row)),
                "errorKind": error_kind,
                "errorMessage": error_message,
            }
        )

    return record


def _arrow_reader(paths: tuple[Path, ...]) -> Callable[[], Any]:
    return lambda: _read_arrow_tables(paths)


def _read_arrow_tables(paths: tuple[Path, ...]) -> Any:
    if len(paths) == 1:
        return pq.read_table(paths[0])
    return pa.concat_tables([pq.read_table(path) for path in paths])


def _arrow_writer(path: Path) -> Callable[[Any], None]:
    return lambda table: pq.write_table(table, path)


def _failure_result(exc: RunnerFailure) -> dict[str, object]:
    return {
        "schemaVersion": _RESULT_SCHEMA_VERSION,
        "status": "failed",
        "failure": {
            "type": exc.failure_type,
            "exceptionType": exc.exception_type,
            "messageSha256": exc.message_sha256,
        },
    }


def _runner_failure(failure_type: str, exc: BaseException) -> RunnerFailure:
    message = str(exc).encode("utf-8", errors="replace")
    return RunnerFailure(failure_type, type(exc).__name__, hashlib.sha256(message).hexdigest())


def _write_result(path: Path, result: Mapping[str, object]) -> None:
    path.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")), encoding="utf-8")


def _required_string(manifest: Mapping[str, object], key: str) -> str:
    value = manifest.get(key)
    if not isinstance(value, str) or not value:
        raise _runner_failure("runner_contract_error", TypeError(f"{key} must be a non-empty string"))
    return value


def _optional_string(manifest: Mapping[str, object], key: str) -> str | None:
    value = manifest.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise _runner_failure("runner_contract_error", TypeError(f"{key} must be null or a non-empty string"))
    return value


def _required_path(manifest: Mapping[str, object], key: str) -> Path:
    return Path(_required_string(manifest, key))


def _string_mapping(manifest: Mapping[str, object], key: str) -> dict[str, str]:
    value = manifest.get(key)
    if not isinstance(value, Mapping):
        raise _runner_failure("runner_contract_error", TypeError(f"{key} must be an object"))
    if not all(isinstance(item_key, str) and isinstance(item, str) for item_key, item in value.items()):
        raise _runner_failure("runner_contract_error", TypeError(f"{key} must map strings to strings"))
    return {str(item_key): str(item) for item_key, item in value.items()}


def _path_sequence_mapping(manifest: Mapping[str, object], key: str) -> dict[str, tuple[Path, ...]]:
    value = manifest.get(key)
    if not isinstance(value, Mapping):
        raise _runner_failure("runner_contract_error", TypeError(f"{key} must be an object"))
    output: dict[str, tuple[Path, ...]] = {}
    for dataset_ref, raw_paths in value.items():
        if not isinstance(dataset_ref, str) or not _is_string_sequence(raw_paths):
            raise _runner_failure("runner_contract_error", TypeError(f"{key} has an invalid path list"))
        output[dataset_ref] = tuple(Path(path) for path in raw_paths)
    return output


def _is_string_sequence(value: object) -> bool:
    return (
        isinstance(value, Sequence)
        and not isinstance(value, str | bytes)
        and bool(value)
        and all(isinstance(item, str) and item for item in value)
    )


def _json_ready(value: object) -> object:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [_json_ready(item) for item in value]
    return value


if __name__ == "__main__":
    raise SystemExit(main())
