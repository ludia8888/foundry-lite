"""Trusted ontology-function runner intended to execute only inside a sandbox.

Mirrors the transform runner's contract -- manifest path in, result path out, typed failure
evidence rather than a traceback -- because the sandbox reads the result file and never the
process output. Where the transform runner speaks parquet, this one speaks JSON: an ontology
function receives values the host already resolved and returns a value, so nothing here touches
storage, a database, or the network.

Nothing in this module may import from the application layer. It runs as the container entry
point with only the standard library and the function's own source.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

_RESULT_SCHEMA_VERSION = 1

# The value shapes a function may return, keyed by the declared ontology output type. `object`
# and `objectSet` are absent on purpose: a function does not hand objects back to its caller.
# Editing them is `ontology_edit_batch`, which is a proposal the Action committer re-validates
# and applies -- the function itself never holds a write path.
_OUTPUT_VALIDATORS: Mapping[str, Callable[[object], bool]] = {
    "boolean": lambda value: isinstance(value, bool),
    "string": lambda value: isinstance(value, str),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "long": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "float": lambda value: isinstance(value, int | float) and not isinstance(value, bool),
    "decimal": lambda value: isinstance(value, int | float | str) and not isinstance(value, bool),
    "date": lambda value: isinstance(value, str),
    "timestamp": lambda value: isinstance(value, str),
    "struct": lambda value: isinstance(value, dict),
    "array": lambda value: isinstance(value, list),
    "ontology_edit_batch": lambda value: _is_edit_batch(value),
}


def _is_edit_batch(value: object) -> bool:
    """Shape only. The host re-parses through OntologyEditBatch, which owns the real contract.

    Checking here anyway turns "the function returned the wrong thing" into a typed sandbox
    failure naming the function, rather than a parse error surfacing from the Action committer
    one layer away from the code that caused it.
    """
    if not isinstance(value, dict):
        return False
    edits = value.get("edits")
    return isinstance(edits, list) and bool(edits)


@dataclass(frozen=True)
class RunnerFailure(Exception):
    failure_type: str
    exception_type: str
    message_sha256: str


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        return 64
    try:
        result = execute_manifest(_load_manifest(Path(args[0])))
    except RunnerFailure as exc:
        result = _failure_result(exc)
    except BaseException as exc:  # noqa: BLE001 - a fatal user exit must still leave typed evidence
        result = _failure_result(_runner_failure("runner_contract_error", exc))
    _write_result(Path(args[1]), result)
    return 0 if result["status"] == "succeeded" else 1


def execute_manifest(manifest: Mapping[str, object]) -> dict[str, object]:
    entrypoint = _text(manifest, "entrypoint")
    function = _loaded_entrypoint(Path(_text(manifest, "sourcePath")), entrypoint)
    inputs = manifest.get("inputs")
    if not isinstance(inputs, Mapping):
        raise _runner_failure("runner_contract_error", ValueError("manifest inputs must be an object"))
    output = _invoked(function, inputs)
    _require_output_type(output, _text(manifest, "outputType"))
    return {
        "schemaVersion": _RESULT_SCHEMA_VERSION,
        "status": "succeeded",
        "output": output,
    }


def _loaded_entrypoint(source_path: Path, entrypoint: str) -> Callable[..., object]:
    """Import the source as a module, the way the transform runner does.

    Importing rather than ``exec``-ing keeps both runners on one mechanism and gives user code a
    real module identity, so a traceback names a file instead of ``<string>``.
    """
    spec = importlib.util.spec_from_file_location("foundry_lite_user_function", source_path)
    if spec is None or spec.loader is None:
        raise _runner_failure("runner_contract_error", ValueError("function source is not importable"))
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except BaseException as exc:  # noqa: BLE001 - import-time user errors are user errors
        raise _runner_failure("user_code_error", exc) from exc
    function = getattr(module, entrypoint, None)
    if not callable(function):
        raise _runner_failure("runner_contract_error", ValueError(f"function source does not define {entrypoint!r}"))
    return function


def _invoked(function: Callable[..., object], inputs: Mapping[str, object]) -> object:
    try:
        return function(**dict(inputs))
    except TypeError as exc:
        # A signature that does not match the declared inputs is an authoring error, and it is
        # worth separating from a fault inside the body: the platform validated the declaration,
        # so a mismatch means the source and the declaration disagree.
        raise _runner_failure("runner_contract_error", exc) from exc
    except BaseException as exc:  # noqa: BLE001 - anything the body raises is the user's
        raise _runner_failure("user_code_error", exc) from exc


def _require_output_type(output: object, output_type: str) -> None:
    validator = _OUTPUT_VALIDATORS.get(output_type)
    if validator is None:
        raise _runner_failure(
            "output_validation_error", ValueError(f"unsupported function output type {output_type!r}")
        )
    if not validator(output):
        raise _runner_failure(
            "output_validation_error",
            ValueError(f"function returned {type(output).__name__}, declared output type is {output_type!r}"),
        )
    try:
        json.dumps(output)
    except (TypeError, ValueError) as exc:
        raise _runner_failure("output_validation_error", exc) from exc


def _load_manifest(path: Path) -> Mapping[str, object]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise _runner_failure("runner_contract_error", exc) from exc
    if not isinstance(loaded, Mapping):
        raise _runner_failure("runner_contract_error", ValueError("manifest must be a JSON object"))
    return loaded


def _write_result(path: Path, result: Mapping[str, object]) -> None:
    path.write_text(json.dumps(result, sort_keys=True), encoding="utf-8")


def _failure_result(failure: RunnerFailure) -> dict[str, object]:
    return {
        "schemaVersion": _RESULT_SCHEMA_VERSION,
        "status": "failed",
        "failureType": failure.failure_type,
        "exceptionType": failure.exception_type,
        # The message itself never leaves the sandbox: user code can put tenant data in an
        # exception string, and the host records evidence, not payloads.
        "exceptionMessageSha256": failure.message_sha256,
    }


def _runner_failure(failure_type: str, exc: BaseException) -> RunnerFailure:
    digest = hashlib.sha256(str(exc).encode("utf-8")).hexdigest()
    return RunnerFailure(failure_type=failure_type, exception_type=type(exc).__name__, message_sha256=digest)


def _text(manifest: Mapping[str, object], key: str) -> str:
    value = manifest.get(key)
    if not isinstance(value, str):
        raise _runner_failure("runner_contract_error", ValueError(f"manifest {key} must be a string"))
    return value


if __name__ == "__main__":
    raise SystemExit(main())
