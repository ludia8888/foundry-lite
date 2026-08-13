from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import pyarrow.parquet as pq
import pytest
import yaml
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.code_execution import FunctionExecutionPlan
from foundry_lite.application.ports.compute_adapter import PythonTransformPlan
from foundry_lite.application.services.aip.fde_domain_os_blueprint import (
    application_resources,
    build_domain_os_blueprint,
    ontology_resources,
)
from foundry_lite.domain.context import demo_admin_context
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


def test_live_container_python_object_set_reads_through_the_governed_bridge() -> None:
    requests: list[Mapping[str, object]] = []

    def execute_query(request: Mapping[str, object]) -> Mapping[str, object]:
        requests.append(dict(request))
        return {
            "items": [{"objectId": "T-4", "properties": {"capacity": 4}}],
            "nextCursor": None,
        }

    source = (
        "from functions.api import function\n"
        "from ontology_sdk import FoundryClient\n"
        "from ontology_sdk.ontology.objects import DiningTable\n"
        "@function\n"
        "def compute():\n"
        "    tables = FoundryClient().ontology.objects.DiningTable\n"
        "    eligible = tables.where(DiningTable.object_type.capacity > 2)\n"
        "    return eligible.all()[0].capacity\n"
    )

    result = ContainerCodeExecutionAdapter().execute_function(
        FunctionExecutionPlan(
            function_api_name="EligibleTableCapacity",
            function_version="v1",
            runtime="python",
            entrypoint="compute",
            source=source,
            inputs_json={},
            argument_order=(),
            output_type="integer",
            timeout_seconds=30,
            input_byte_limit=4096,
        ),
        query_executor=execute_query,
    )

    assert result.output == 4
    assert requests == [
        {
            "operation": "fetchPage",
            "objectType": "DiningTable",
            "filter": {"property": "capacity", "op": "gt", "value": 2},
            "orderBy": [],
            "pageSize": 500,
            "pageToken": None,
        }
    ]


def test_live_container_typescript_v2_object_set_reads_through_the_governed_bridge() -> None:
    requests: list[Mapping[str, object]] = []

    def execute_query(request: Mapping[str, object]) -> Mapping[str, object]:
        requests.append(dict(request))
        return {
            "items": [{"objectId": "T-6", "properties": {"capacity": 6}}],
            "nextCursor": None,
        }

    source = (
        "export default function compute(tables: unknown) {\n"
        "  return tables.where({ capacity: { $gt: 4 } }).all()[0].capacity;\n"
        "}\n"
    )
    descriptor = {
        "$foundryObjectSet": {
            "objectType": "DiningTable",
            "filter": None,
            "orderBy": [],
        }
    }

    result = ContainerCodeExecutionAdapter().execute_function(
        FunctionExecutionPlan(
            function_api_name="EligibleTableCapacityTs",
            function_version="v1",
            runtime="typescript",
            entrypoint="compute",
            source=source,
            inputs_json={"tables": descriptor},
            argument_order=("tables",),
            output_type="integer",
            timeout_seconds=30,
            input_byte_limit=4096,
        ),
        query_executor=execute_query,
    )

    assert result.output == 6
    assert requests[0]["objectType"] == "DiningTable"
    assert requests[0]["filter"] == {"property": "capacity", "op": "gt", "value": 4}


def test_live_generated_domain_function_executes_its_governed_object_set(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    ctx = demo_admin_context()
    arguments = _maintenance_domain_arguments()
    blueprint = build_domain_os_blueprint(arguments)
    dataset_ref = "seed.maintenance_function_live"
    csv_path = tmp_path / "work-orders.csv"
    csv_path.write_text(
        "work_order_id,name,status,severity\nWO-1,Leaking pipe,REPORTED,urgent\nWO-2,Loose handle,REPORTED,normal\n",
        encoding="utf-8",
    )
    foundry.datasets.ensure(dataset_ref, ctx=ctx, primary_key=["work_order_id"])
    foundry.datasets.upload_csv(dataset_ref, str(csv_path), ctx=ctx)
    resources = ontology_resources(blueprint, dataset_ref)
    definition = {
        "objectTypes": [item["definition"] for item in resources if item["kind"] == "objectType"],
        "actionTypes": [item["definition"] for item in resources if item["kind"] == "actionType"],
        "functionTypes": [item["definition"] for item in resources if item["kind"] == "functionType"],
    }
    foundry.ontology.apply_text(yaml.safe_dump(definition, sort_keys=False), ctx=ctx)
    foundry.objects.reindex("WorkOrder", ctx=ctx)
    app_resources = [
        item for item in application_resources(blueprint) if item["resourceType"] in {"object", "function"}
    ]
    application = foundry.developer_console.create_osdk_application(
        app_api_name="MaintenanceFunctionLive",
        display_name="Maintenance Function Live",
        client_id="maintenance-function-client",
        resources=app_resources,
        idempotency_key="maintenance-function-live-app",
        ctx=ctx,
    )
    scopes = tuple(scope for item in app_resources for scope in item["scopes"])
    scoped_ctx = replace(
        ctx,
        application_id=str(application["application"]["id"]),
        client_id="maintenance-function-client",
        token_scopes=scopes,
    )

    result = foundry.functions.execute("CountUrgentWorkOrders", inputs={}, ctx=scoped_ctx)

    assert result["status"] == "SUCCEEDED"
    assert result["aiRunId"] is None
    assert result["output"]["value"] == {
        "groups": [{"key": {}, "metrics": {"value": 1}}],
        "totalGroups": 1,
    }


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


def _maintenance_domain_arguments() -> dict[str, object]:
    return {
        "applicationName": "Maintenance Desk",
        "domainDescription": "시설 요청을 접수하고 긴급 요청 수를 계산한 뒤 담당자가 처리합니다.",
        "domainBrief": {
            "actors": ["coordinator"],
            "records": [
                {
                    "name": "Work order",
                    "apiName": "WorkOrder",
                    "fields": [{"name": "severity", "apiName": "severity", "type": "string", "required": True}],
                }
            ],
            "lifecycleStates": ["REPORTED", "COMPLETED"],
            "actions": [
                {
                    "name": "Complete work order",
                    "apiName": "CompleteWorkOrder",
                    "fromStates": ["REPORTED"],
                    "toState": "COMPLETED",
                    "requiredInformation": [],
                    "allowedActors": ["coordinator"],
                }
            ],
            "policies": [
                {
                    "name": "Urgent first",
                    "statement": "Urgent work orders are handled first.",
                    "enforcement": "manual_review",
                    "appliesToActions": ["CompleteWorkOrder"],
                }
            ],
            "functions": [
                {
                    "name": "Urgent work order count",
                    "apiName": "CountUrgentWorkOrders",
                    "recordApiName": "WorkOrder",
                    "aggregation": "count",
                    "filters": [{"propertyApiName": "severity", "operator": "eq", "value": "urgent"}],
                    "allowedActors": ["coordinator"],
                }
            ],
            "evidence": ["actor and timestamp"],
            "integrations": [],
            "successMeasures": ["no missed urgent work"],
        },
    }


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
