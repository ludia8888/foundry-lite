from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.code_execution import FunctionExecutionPlan
from foundry_lite.application.ports.compute_adapter import PythonTransformPlan
from foundry_lite.infrastructure.adapters.container_code_execution import ContainerCodeExecutionAdapter
from foundry_lite.infrastructure.adapters.container_code_execution_runtime import (
    ContainerCodeExecutionConfig,
    default_policy,
)


def test_live_container_python_transform_enforces_process_sandbox(tmp_path: Path) -> None:
    target_path = tmp_path / "sandbox-evidence.parquet"
    adapter = ContainerCodeExecutionAdapter()

    adapter.execute_python_transform(
        PythonTransformPlan(
            entrypoint=str(tmp_path / "sandbox_probe.py"),
            source_code=_PROBE_SOURCE,
            function_name="compute",
            input_refs_by_alias={},
            input_paths_by_ref={},
            output_dataset_ref="sandbox.evidence",
            target_path=target_path,
        )
    )

    row = pq.read_table(target_path).to_pylist()[0]
    assert row["uid"] == 65532
    assert row["gid"] == 65532
    assert row["environmentKeys"] == [
        "HOME",
        "LANG",
        "PATH",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONHASHSEED",
        "PYTHONPATH",
        "PYTHONUNBUFFERED",
        "TMPDIR",
    ]
    assert row["networkBlocked"] is True
    assert row["rootWriteBlocked"] is True
    assert row["outputDirectoryWriteBlocked"] is True
    assert row["effectiveCapabilities"] == "0000000000000000"
    assert row["noNewPrivileges"] == "1"


def test_live_container_typescript_function_resolves_its_runtime_only_dependency() -> None:
    result = ContainerCodeExecutionAdapter().execute_function(
        FunctionExecutionPlan(
            function_api_name="increment",
            function_version="v1",
            runtime="typescript",
            entrypoint="compute",
            source="export function compute(value: number) { return value + 1; }",
            inputs_json={"value": 41},
            argument_order=("value",),
            output_type="integer",
            timeout_seconds=30,
            input_byte_limit=1024,
        )
    )

    assert result.output == 42


def test_live_container_python_failure_is_typed_and_redacted(tmp_path: Path) -> None:
    private_message = "private-customer-value"
    adapter = ContainerCodeExecutionAdapter()

    with pytest.raises(AdapterError) as captured:
        adapter.execute_python_transform(
            _plan(
                tmp_path,
                f"def compute():\n    raise RuntimeError({private_message!r})\n",
            )
        )

    evidence = captured.value.failure.details["codeExecution"]
    assert isinstance(evidence, dict)
    assert captured.value.failure.kind == "validation"
    assert evidence["failureType"] == "user_code_error"
    assert evidence["exceptionType"] == "RuntimeError"
    assert evidence["exceptionMessageSha256"] == hashlib.sha256(private_message.encode()).hexdigest()
    assert private_message not in str(captured.value.failure.details)


def test_live_container_timeout_is_enforced(tmp_path: Path) -> None:
    policy = replace(default_policy(), timeout_seconds=1)
    adapter = ContainerCodeExecutionAdapter(ContainerCodeExecutionConfig(policy=policy))

    with pytest.raises(AdapterError) as captured:
        adapter.execute_python_transform(
            _plan(
                tmp_path,
                "import time\n\ndef compute():\n    time.sleep(30)\n    return []\n",
            )
        )

    evidence = captured.value.failure.details["codeExecution"]
    assert isinstance(evidence, dict)
    assert captured.value.failure.kind == "timeout"
    assert evidence["failureType"] == "sandbox_timeout"


def _plan(tmp_path: Path, source_code: str) -> PythonTransformPlan:
    return PythonTransformPlan(
        entrypoint=str(tmp_path / "sandbox_probe.py"),
        source_code=source_code,
        function_name="compute",
        input_refs_by_alias={},
        input_paths_by_ref={},
        output_dataset_ref="sandbox.evidence",
        target_path=tmp_path / "sandbox-evidence.parquet",
    )


_PROBE_SOURCE = """
import os
import socket


def _status_value(name):
    with open("/proc/self/status", encoding="utf-8") as status:
        for line in status:
            if line.startswith(name + ":"):
                return line.split(":", 1)[1].strip()
    return ""


def _network_blocked():
    try:
        with socket.create_connection(("1.1.1.1", 53), timeout=0.25):
            return False
    except OSError:
        return True


def _root_write_blocked():
    try:
        with open("/sandbox-root-write", "w", encoding="utf-8") as output:
            output.write("unsafe")
        return False
    except OSError:
        return True


def _output_directory_write_blocked():
    try:
        with open("/sandbox-output/unbounded-host-write", "w", encoding="utf-8") as output:
            output.write("unsafe")
        return False
    except OSError:
        return True


def compute():
    return [{
        "uid": os.getuid(),
        "gid": os.getgid(),
        "environmentKeys": sorted(os.environ),
        "networkBlocked": _network_blocked(),
        "rootWriteBlocked": _root_write_blocked(),
        "outputDirectoryWriteBlocked": _output_directory_write_blocked(),
        "effectiveCapabilities": _status_value("CapEff"),
        "noNewPrivileges": _status_value("NoNewPrivs"),
    }]
"""
