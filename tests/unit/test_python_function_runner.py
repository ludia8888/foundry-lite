"""The in-sandbox function runner: typed evidence out, tenant data never out.

The runner is the last code that sees user output before it crosses back. It runs with no
network and no storage, so its only job is to load the source, call the entry point, and decide
whether what came back matches what the function declared. Everything it reports is either a
category or a digest -- an exception message can carry tenant data, and the host records
evidence, not payloads.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

import pytest
from foundry_lite.infrastructure.runners import python_function_runner as runner


def _manifest(
    source: str,
    *,
    inputs: dict[str, Any] | None = None,
    output_type: str = "integer",
    root: Path | None = None,
) -> dict[str, Any]:
    """The runner imports a file, so every manifest needs one written next to it."""
    directory = root or Path(tempfile.mkdtemp())
    source_path = directory / "function.py"
    source_path.write_text(source, encoding="utf-8")
    return {
        "schemaVersion": 1,
        "entrypoint": "compute",
        "sourcePath": str(source_path),
        "inputs": inputs or {},
        "outputType": output_type,
    }


def _failure(manifest: dict[str, Any]) -> runner.RunnerFailure:
    with pytest.raises(runner.RunnerFailure) as caught:
        runner.execute_manifest(manifest)
    return caught.value


def test_a_function_receives_its_inputs_by_name_and_returns_a_value() -> None:
    result = runner.execute_manifest(
        _manifest(
            "def compute(tables):\n    return sum(t['seats'] for t in tables)\n",
            inputs={"tables": [{"seats": 4}, {"seats": 2}]},
        )
    )

    assert result == {"schemaVersion": 1, "status": "succeeded", "output": 6}


# --- the declared type is enforced, not assumed --------------------------------------


@pytest.mark.parametrize(
    ("output_type", "body", "should_pass"),
    [
        ("integer", "return 4", True),
        ("integer", "return 'four'", False),
        # bool is an int in Python, and a function declaring integer that returns True is
        # almost always a bug the caller would otherwise receive as 1.
        ("integer", "return True", False),
        ("boolean", "return True", True),
        ("float", "return 1.5", True),
        ("string", "return 'x'", True),
        ("struct", "return {'a': 1}", True),
        ("struct", "return [1]", False),
        ("array", "return [1]", True),
    ],
)
def test_the_declared_output_type_is_enforced(output_type: str, body: str, should_pass: bool) -> None:
    manifest = _manifest(f"def compute():\n    {body}\n", output_type=output_type)

    if should_pass:
        assert runner.execute_manifest(manifest)["status"] == "succeeded"
        return
    assert _failure(manifest).failure_type == "output_validation_error"


def test_an_output_type_the_runner_does_not_know_is_refused() -> None:
    """`object` and `objectSet` are absent on purpose: returning objects is an Ontology edit."""
    assert _failure(_manifest("def compute():\n    return {}\n", output_type="object")).failure_type == (
        "output_validation_error"
    )


def test_an_unserializable_value_fails_here_rather_than_on_the_way_out() -> None:
    manifest = _manifest("def compute():\n    return {'a': {1, 2}}\n", output_type="struct")

    assert _failure(manifest).failure_type == "output_validation_error"


# --- failures are separated by whose fault they are -----------------------------------


def test_a_fault_inside_the_function_body_is_a_user_code_error() -> None:
    assert _failure(_manifest("def compute():\n    raise ValueError('boom')\n")).failure_type == "user_code_error"


def test_a_signature_that_disagrees_with_the_declaration_is_a_contract_error() -> None:
    """The platform validated the declaration, so a mismatch means source and declaration differ."""
    manifest = _manifest("def compute(a):\n    return 1\n", inputs={"b": 1})

    assert _failure(manifest).failure_type == "runner_contract_error"


def test_source_that_does_not_define_the_entry_point_is_a_contract_error() -> None:
    assert _failure(_manifest("def other():\n    return 1\n")).failure_type == "runner_contract_error"


def test_source_that_fails_at_import_time_is_a_user_code_error() -> None:
    assert _failure(_manifest("raise RuntimeError('at import')\n")).failure_type == "user_code_error"


# --- nothing but a digest leaves the sandbox ------------------------------------------


def test_an_exception_message_leaves_only_as_a_digest() -> None:
    """User code can put tenant data in an exception string; the host gets evidence, not payloads."""
    tenant_datum = "customer 4041 balance 9900"

    failure = _failure(_manifest(f"def compute():\n    raise ValueError({tenant_datum!r})\n"))
    written = json.dumps(runner._failure_result(failure))

    assert tenant_datum not in written
    assert failure.message_sha256 == hashlib.sha256(tenant_datum.encode("utf-8")).hexdigest()
    assert failure.exception_type == "ValueError"


def test_a_failing_run_still_writes_a_typed_result_file(tmp_path: Path) -> None:
    """The host reads the result file, never the process output, so a crash must still leave one."""
    manifest_path = tmp_path / "request.json"
    result_path = tmp_path / "result.json"
    manifest_path.write_text(json.dumps(_manifest("def compute():\n    raise ValueError('x')\n")), encoding="utf-8")

    exit_code = runner.main([str(manifest_path), str(result_path)])

    assert exit_code == 1
    written = json.loads(result_path.read_text(encoding="utf-8"))
    assert written["status"] == "failed"
    assert written["failureType"] == "user_code_error"


def test_a_manifest_that_is_not_an_object_is_a_contract_error(tmp_path: Path) -> None:
    manifest_path = tmp_path / "request.json"
    result_path = tmp_path / "result.json"
    manifest_path.write_text("[]", encoding="utf-8")

    assert runner.main([str(manifest_path), str(result_path)]) == 1
    assert json.loads(result_path.read_text(encoding="utf-8"))["failureType"] == "runner_contract_error"


# --- ontology edits are a proposal, not a write ---------------------------------------


def test_a_function_may_return_an_ontology_edit_batch() -> None:
    """Palantir's TypeScript v2 and Python functions return edits explicitly rather than void."""
    manifest = _manifest(
        'def compute():\n    return {"edits": [{"kind": "modifyObject", "objectId": "T-2"}]}\n',
        output_type="ontology_edit_batch",
    )

    assert runner.execute_manifest(manifest)["status"] == "succeeded"


@pytest.mark.parametrize(
    "body",
    [
        # An empty batch is the shape a filter-then-edit function produces when it matched
        # nothing, and letting it through would record an Action run that changed nothing.
        'return {"edits": []}',
        'return {"edits": "modifyObject"}',
        "return {}",
        'return [{"kind": "modifyObject"}]',
    ],
)
def test_a_malformed_edit_batch_fails_in_the_sandbox(body: str) -> None:
    """Catching the shape here names the function; catching it host-side names the committer."""
    manifest = _manifest(f"def compute():\n    {body}\n", output_type="ontology_edit_batch")

    assert _failure(manifest).failure_type == "output_validation_error"


# --- the runner is the last line, so its own contract breaches must be typed ----------


def test_wrong_argument_count_returns_the_usage_code(tmp_path: Path) -> None:
    """Exit 64 is EX_USAGE: the container was invoked wrongly, which is not a user-code failure."""
    assert runner.main([str(tmp_path / "only-one")]) == 64


def test_a_manifest_that_is_not_readable_is_a_contract_error(tmp_path: Path) -> None:
    result_path = tmp_path / "result.json"

    assert runner.main([str(tmp_path / "absent.json"), str(result_path)]) == 1
    assert json.loads(result_path.read_text(encoding="utf-8"))["failureType"] == "runner_contract_error"


def test_a_manifest_field_of_the_wrong_type_is_a_contract_error(tmp_path: Path) -> None:
    manifest_path = tmp_path / "request.json"
    result_path = tmp_path / "result.json"
    manifest_path.write_text(json.dumps({"schemaVersion": 1, "entrypoint": 7}), encoding="utf-8")

    assert runner.main([str(manifest_path), str(result_path)]) == 1
    assert json.loads(result_path.read_text(encoding="utf-8"))["failureType"] == "runner_contract_error"


def test_manifest_inputs_that_are_not_an_object_are_a_contract_error() -> None:
    manifest = _manifest("def compute():\n    return 1\n")
    manifest["inputs"] = ["positional"]

    assert _failure(manifest).failure_type == "runner_contract_error"


def test_a_source_path_that_is_not_importable_is_a_contract_error(tmp_path: Path) -> None:
    """A directory where a module should be means the workspace was laid out wrong, not that the
    author wrote bad code, so it must not be reported as a user error."""
    manifest = _manifest("def compute():\n    return 1\n", root=tmp_path)
    manifest["sourcePath"] = str(tmp_path)

    assert _failure(manifest).failure_type == "runner_contract_error"


def test_a_fatal_exit_inside_user_code_still_leaves_typed_evidence(tmp_path: Path) -> None:
    """SystemExit does not derive from Exception, so a bare `except Exception` would let the
    runner die without writing the file the host reads."""
    manifest_path = tmp_path / "request.json"
    result_path = tmp_path / "result.json"
    manifest_path.write_text(
        json.dumps(_manifest("import sys\ndef compute():\n    sys.exit(3)\n", root=tmp_path)), encoding="utf-8"
    )

    assert runner.main([str(manifest_path), str(result_path)]) == 1
    assert json.loads(result_path.read_text(encoding="utf-8"))["status"] == "failed"
