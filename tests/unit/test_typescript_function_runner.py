"""The TypeScript runner must be indistinguishable from the Python one at the contract line.

Two runners is one contract with two implementations, and the way that goes wrong is drift: the
same authoring mistake classified as a user error in one language and a contract error in the
other, or an exception message that leaks in Node after being scrubbed in Python. These tests run
the real runner under Node and assert the same categories the Python suite asserts.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "libs" / "foundry_lite" / "infrastructure" / "runners" / "typescript_function_runner.mjs"


def _node_binary() -> str:
    """Absent node is a broken toolchain, not a reason to skip.

    Skipping would let the TypeScript runtime go unexercised on any machine that happened not to
    have Node, which is exactly the silence the no-test-bypasses gate exists to prevent. Node is
    part of the toolchain every lane installs, so its absence is worth failing over.
    """
    node = shutil.which("node")
    assert node is not None, "node is required to exercise the TypeScript function runner"
    return node


def _run(
    source: str,
    *,
    inputs: dict[str, Any] | None = None,
    argument_order: list[str] | None = None,
    output_type: str = "integer",
    entrypoint: str = "compute",
    tmp_path: Path,
) -> dict[str, Any]:
    manifest = tmp_path / "request.json"
    result = tmp_path / "result.json"
    manifest.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "entrypoint": entrypoint,
                "source": source,
                "inputs": inputs or {},
                "argumentOrder": argument_order or [],
                "outputType": output_type,
            }
        ),
        encoding="utf-8",
    )
    # NODE_PATH mirrors the sandbox image, where `typescript` is a global install rather than a
    # local node_modules the read-only root filesystem could not host. node is resolved
    # absolutely because a version manager may keep it outside the default PATH.
    node = _node_binary()
    subprocess.run(  # noqa: S603
        [node, str(RUNNER), str(manifest), str(result)],
        check=False,
        capture_output=True,
        env={"PATH": str(Path(node).parent), "NODE_PATH": str(ROOT / "node_modules")},
    )
    return json.loads(result.read_text(encoding="utf-8"))


def test_typescript_is_transpiled_and_executed(tmp_path: Path) -> None:
    """Type annotations and an interface must survive the trip; this is the whole runtime."""
    source = (
        "interface Table { seats: number; status: string }\n"
        "export function compute(tables: Table[], minimum: number): number {\n"
        "  return tables.filter(t => t.status === 'FREE' && t.seats >= minimum)\n"
        "               .reduce((total, t) => total + t.seats, 0)\n"
        "}\n"
    )

    result = _run(
        source,
        inputs={"tables": [{"seats": 8, "status": "FREE"}, {"seats": 2, "status": "BOOKED"}], "minimum": 3},
        argument_order=["tables", "minimum"],
        tmp_path=tmp_path,
    )

    assert result == {"schemaVersion": 1, "status": "succeeded", "output": 8}


def test_arguments_are_applied_in_declaration_order(tmp_path: Path) -> None:
    """TypeScript binds positionally, so a wrong order is a wrong answer rather than an error."""
    result = _run(
        "export function compute(a: number, b: number): number { return a - b }",
        inputs={"a": 10, "b": 4},
        argument_order=["a", "b"],
        tmp_path=tmp_path,
    )

    assert result["output"] == 6


def test_typescript_v2_default_export_can_filter_and_return_a_lazy_object_set(tmp_path: Path) -> None:
    source = (
        'import type { ObjectSet } from "@osdk/client";\n'
        'import type { DiningTable } from "@ontology/sdk";\n'
        "export default function compute(tables: ObjectSet<DiningTable>): ObjectSet<DiningTable> {\n"
        "  return tables.where({ status: { $eq: 'FREE' } });\n"
        "}\n"
    )
    result = _run(
        source,
        inputs={"tables": {"$foundryObjectSet": {"objectType": "DiningTable", "filter": None, "orderBy": []}}},
        argument_order=["tables"],
        output_type="objectSet",
        tmp_path=tmp_path,
    )

    assert result["output"] == {
        "$foundryObjectSet": {
            "objectType": "DiningTable",
            "filter": {"property": "status", "op": "eq", "value": "FREE"},
            "orderBy": [],
        }
    }


@pytest.mark.parametrize(
    ("source", "output_type", "expected"),
    [
        ("export function compute(): any { return 'x' }", "integer", "output_validation_error"),
        ("export function compute(): any { return 1.5 }", "integer", "output_validation_error"),
        ("export function compute(): any { return {} }", "object", "output_validation_error"),
        ("export function compute(): any { return { edits: [] } }", "ontology_edit_batch", "output_validation_error"),
        ("export function compute(): number { throw new Error('boom') }", "integer", "user_code_error"),
        ("export function other(): number { return 1 }", "integer", "runner_contract_error"),
    ],
)
def test_failures_carry_the_same_categories_as_the_python_runner(
    source: str, output_type: str, expected: str, tmp_path: Path
) -> None:
    result = _run(source, output_type=output_type, tmp_path=tmp_path)

    assert result["status"] == "failed"
    assert result["failureType"] == expected


@pytest.mark.parametrize(
    "source",
    [
        "export function compute(): number { return Number.NaN }",
        "export function compute(): number { return Number.POSITIVE_INFINITY }",
    ],
)
def test_non_finite_typescript_numbers_never_cross_the_json_boundary(source: str, tmp_path: Path) -> None:
    assert _run(source, output_type="float", tmp_path=tmp_path)["failureType"] == "output_validation_error"


def test_typescript_object_set_rejects_malformed_filters_orders_and_descriptors(tmp_path: Path) -> None:
    malformed_filter = _run(
        "export function compute(tables: any) { return tables.where({ status: {} }) }",
        inputs={"tables": {"$foundryObjectSet": {"objectType": "DiningTable", "filter": None, "orderBy": []}}},
        argument_order=["tables"],
        output_type="objectSet",
        tmp_path=tmp_path,
    )
    assert malformed_filter["failureType"] == "user_code_error"

    malformed_order = _run(
        "export function compute(tables: any) { return tables.orderBy({ seats: 'sideways' }) }",
        inputs={"tables": {"$foundryObjectSet": {"objectType": "DiningTable", "filter": None, "orderBy": []}}},
        argument_order=["tables"],
        output_type="objectSet",
        tmp_path=tmp_path,
    )
    assert malformed_order["failureType"] == "user_code_error"

    malformed_descriptor = _run(
        "export function compute(tables: any) { return tables }",
        inputs={"tables": {"$foundryObjectSet": {"objectType": "", "filter": None, "orderBy": []}}},
        argument_order=["tables"],
        output_type="objectSet",
        tmp_path=tmp_path,
    )
    assert malformed_descriptor["failureType"] == "runner_contract_error"


def test_an_ontology_edit_batch_is_accepted(tmp_path: Path) -> None:
    source = "export function compute() { return { edits: [{ kind: 'modifyObject', objectId: 'T-2' }] } }"

    assert _run(source, output_type="ontology_edit_batch", tmp_path=tmp_path)["status"] == "succeeded"


def test_the_host_module_graph_is_not_reachable_from_user_code(tmp_path: Path) -> None:
    """The container has no network; withholding `require` fails a read at the call site too."""
    source = "export function compute(): string { return require('fs').readFileSync('/etc/passwd', 'utf8') }"

    result = _run(source, output_type="string", tmp_path=tmp_path)

    assert result["failureType"] == "user_code_error"


def test_an_exception_message_leaves_only_as_a_digest(tmp_path: Path) -> None:
    """Same guarantee as the Python runner: the host records evidence, not payloads."""
    tenant_datum = "customer 4041 balance 9900"

    result = _run(f"export function compute(): number {{ throw new Error({tenant_datum!r}) }}", tmp_path=tmp_path)

    assert tenant_datum not in json.dumps(result)
    assert result["exceptionMessageSha256"] == hashlib.sha256(tenant_datum.encode("utf-8")).hexdigest()
