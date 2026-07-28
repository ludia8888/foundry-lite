from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from foundry_lite.application.ports.transform_repository import TransformRecord, TransformRow
from foundry_lite.application.services.transform_definition_service import (
    TransformDefinitionService,
    _normalized_python_function,
    _registered_python_entrypoint,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import InvariantViolation, ValidationFailed


class _TransactionManager:
    @contextmanager
    def begin(self) -> Any:
        yield object()


class _DatasetRegistry:
    def __init__(self) -> None:
        self.refs: list[str] = []

    def ensure_dataset(self, dataset_ref: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        self.refs.append(dataset_ref)
        return {"id": "ds_output"}


class _Runtime:
    def __init__(self) -> None:
        self.audit_events: list[dict[str, object]] = []
        self.permissions: list[tuple[str, str]] = []
        self.write_operations: list[str] = []

    def _require_or_audit(
        self,
        ctx: RequestContext,
        permission: str,
        resource_type: str,
        resource_id: str,
    ) -> None:
        self.permissions.append((permission, resource_id))

    def _require_write_traffic_open(
        self,
        ctx: RequestContext,
        *,
        operation: str,
        resource_type: str,
        resource_id: str,
    ) -> None:
        self.write_operations.append(operation)

    def _audit(self, conn: object, ctx: RequestContext, **event: object) -> None:
        self.audit_events.append(event)


class _TransformRepository:
    def __init__(self, existing: TransformRow | None = None) -> None:
        self.row = existing
        self.is_missing_after_write = False

    def transform_by_api_name(
        self,
        *,
        transaction: object,
        tenant_id: str,
        api_name: str,
    ) -> TransformRow | None:
        if self.row is None or self.row["tenant_id"] != tenant_id or self.row["api_name"] != api_name:
            return None
        return self.row

    def insert_transform(self, *, transaction: object, record: TransformRecord) -> None:
        self.row = {
            "id": record.transform_id,
            "tenant_id": record.tenant_id,
            "api_name": record.api_name,
            "language": record.language,
            "entrypoint": record.entrypoint,
            "mode": record.mode,
            "inputs": record.inputs,
            "output_dataset_ref": record.output_dataset_ref,
            "checks": record.checks,
        }

    def update_transform_definition(self, *, transaction: object, **values: object) -> None:
        assert self.row is not None
        self.row = {
            **self.row,
            "language": str(values["language"]),
            "entrypoint": str(values["entrypoint"]),
            "mode": str(values["mode"]),
            "inputs": dict(values["inputs"]),  # type: ignore[arg-type]
            "output_dataset_ref": str(values["output_dataset_ref"]),
            "checks": [dict(check) for check in values["checks"]],  # type: ignore[union-attr]
        }

    def transform_by_id(self, *, transaction: object, transform_id: str) -> TransformRow | None:
        if self.is_missing_after_write or self.row is None or self.row["id"] != transform_id:
            return None
        return self.row


def _existing_row() -> TransformRow:
    return {
        "id": "tf_existing",
        "tenant_id": "tenant-demo",
        "api_name": "clean_rows",
        "language": "sql",
        "entrypoint": "old.sql",
        "mode": "snapshot",
        "inputs": {"source": "input"},
        "output_dataset_ref": "old-output",
        "checks": [],
    }


def _service(
    tmp_path: Path,
    repository: _TransformRepository,
) -> tuple[TransformDefinitionService, _DatasetRegistry, _Runtime]:
    registry = _DatasetRegistry()
    runtime = _Runtime()
    service = TransformDefinitionService(
        root=tmp_path,
        engine=_TransactionManager(),
        transform_repository=repository,
    )
    service.bind_collaborators(
        {
            "dataset_registry_service": registry,
            "runtime_service": runtime,
        }
    )
    return service, registry, runtime


def test_register_python_transform_creates_file_and_definition(tmp_path: Path) -> None:
    repository = _TransformRepository()
    service, registry, runtime = _service(tmp_path, repository)

    row = service.register_python_transform(
        "clean_rows",
        source_code="def transform(rows):\n    return rows\n",
        function_name=" transform ",
        inputs={"source": "input"},
        output_dataset_ref="output",
        checks=[{"type": "row_count_min", "value": 1}],
    )

    entrypoint, function_name = row["entrypoint"].rsplit(":", 1)
    assert Path(entrypoint).read_text(encoding="utf-8").startswith("def transform")
    assert function_name == "transform"
    assert row["language"] == "python"
    assert registry.refs == ["output"]
    assert runtime.permissions == [("transform:run", "clean_rows")]
    assert runtime.write_operations == ["register_python_transform"]
    assert runtime.audit_events[0]["event_type"] == "transform.definition.created"


def test_register_python_transform_replaces_definition_without_function_suffix(tmp_path: Path) -> None:
    repository = _TransformRepository(_existing_row())
    service, _, runtime = _service(tmp_path, repository)

    row = service.register_python_transform(
        "clean_rows",
        source_code="RESULT = []\n",
        function_name=None,
        inputs={},
        output_dataset_ref="new-output",
        mode="append",
    )

    assert row["id"] == "tf_existing"
    assert row["entrypoint"].endswith(".py")
    assert ":" not in Path(row["entrypoint"]).name
    assert row["mode"] == "append"
    assert runtime.audit_events[0]["event_type"] == "transform.definition.updated"


def test_python_transform_registration_rejects_blank_source_and_function(tmp_path: Path) -> None:
    service, _, _ = _service(tmp_path, _TransformRepository())

    with pytest.raises(ValidationFailed, match="sourceCode"):
        service.register_python_transform(
            "clean_rows",
            source_code=" \n",
            function_name=None,
            inputs={},
            output_dataset_ref="output",
        )

    with pytest.raises(ValidationFailed, match="functionName"):
        _normalized_python_function(" \t")


def test_python_entrypoint_is_tenant_scoped_and_stable(tmp_path: Path) -> None:
    first = _registered_python_entrypoint(tmp_path, "tenant-demo", "clean_rows")
    second = _registered_python_entrypoint(tmp_path, "tenant-demo", "clean_rows")

    assert first == second
    assert first.parent.name == "tenant-demo"
    assert first.name.startswith("clean_rows-")


@pytest.mark.parametrize("existing", [None, _existing_row()])
def test_definition_write_requires_repository_row_after_insert_or_update(
    tmp_path: Path,
    existing: TransformRow | None,
) -> None:
    repository = _TransformRepository(existing)
    repository.is_missing_after_write = True
    service, _, _ = _service(tmp_path, repository)

    with pytest.raises(InvariantViolation, match="row missing"):
        service.register_python_transform(
            "clean_rows",
            source_code="RESULT = []\n",
            function_name=None,
            inputs={},
            output_dataset_ref="output",
        )
