"""Application service helpers for transform runs workflows."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import cast

from foundry_lite.application.ports import TRANSFORM_RUN_SUCCEEDED, InputFilePaths, TransactionContext
from foundry_lite.application.ports.dataset_repository import DatasetRow
from foundry_lite.application.ports.transform_repository import (
    TransformRepository,
    TransformRow,
    TransformRunRecord,
    TransformRunRow,
)
from foundry_lite.application.primitives import INPUT_PATTERN, CommitResult, _new_id, _now
from foundry_lite.application.services.transform_partition_pushdown import (
    infer_sql_partition_filters as infer_sql_partition_filters,
)
from foundry_lite.application.services.transform_protocols import (
    TransformDatasetRegistry,
    TransformDatasetTransactions,
    TransformDatasetVersions,
)
from foundry_lite.application.services.transform_sql_guards import (
    validate_sql_transform_uses_declared_inputs as validate_sql_transform_uses_declared_inputs,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound, ValidationFailed

SUPPORTED_TRANSFORM_OUTPUT_MODES = ("snapshot", "append")
TRANSFORM_OUTPUT_MODE_ALIASES = {"incremental": "append"}


@dataclass(frozen=True)
class TransformRunPlan:
    """Transaction and runtime identifiers for one transform execution."""

    transform_id: str
    language: str
    entrypoint: str
    sql_template: str
    python_source: str | None
    python_function: str | None
    checks: list[dict[str, object]]
    output_dataset: DatasetRow
    input_versions: dict[str, str]
    definition_snapshot: dict[str, object]
    transaction_id: str
    run_id: str
    retry_of_run_id: str | None = None


@dataclass(frozen=True)
class TransformSource:
    """Source code snapshot used for one transform execution."""

    sql_template: str
    python_source: str | None = None
    python_function: str | None = None


def start_transform_run(
    *,
    conn: TransactionContext,
    ctx: RequestContext,
    api_name: str,
    dataset_registry_service: TransformDatasetRegistry,
    dataset_transaction_service: TransformDatasetTransactions,
    dataset_version_service: TransformDatasetVersions,
    transform_repository: TransformRepository,
) -> TransformRunPlan:
    transform = _get_transform(conn, ctx, api_name, transform_repository)
    language = str(transform["language"])
    source = _read_transform_source(str(transform["entrypoint"]), language)
    input_versions = _resolve_transform_inputs(
        conn,
        ctx,
        source.sql_template,
        transform["inputs"],
        dataset_registry_service,
        dataset_version_service,
    )
    return _open_transform_run(
        conn=conn,
        ctx=ctx,
        transform=transform,
        source=source,
        input_versions=input_versions,
        dataset_registry_service=dataset_registry_service,
        dataset_transaction_service=dataset_transaction_service,
        transform_repository=transform_repository,
    )


def start_failed_transform_retry(
    *,
    conn: TransactionContext,
    ctx: RequestContext,
    transform_run_id: str,
    dataset_registry_service: TransformDatasetRegistry,
    dataset_transaction_service: TransformDatasetTransactions,
    transform_repository: TransformRepository,
) -> TransformRunPlan:
    failed_run = _failed_transform_run(conn, ctx, transform_run_id, transform_repository)
    snapshot = _required_run_definition_snapshot(failed_run, transform_run_id)
    transform = _transform_from_run_snapshot(failed_run, ctx, transform_run_id, snapshot)
    source = _source_from_run_snapshot(snapshot, transform_run_id)
    return _open_transform_run(
        conn=conn,
        ctx=ctx,
        transform=transform,
        source=source,
        input_versions=dict(failed_run["input_versions"]),
        dataset_registry_service=dataset_registry_service,
        dataset_transaction_service=dataset_transaction_service,
        transform_repository=transform_repository,
        retry_of_run_id=transform_run_id,
    )


def _open_transform_run(
    *,
    conn: TransactionContext,
    ctx: RequestContext,
    transform: TransformRow,
    source: TransformSource,
    input_versions: dict[str, str],
    dataset_registry_service: TransformDatasetRegistry,
    dataset_transaction_service: TransformDatasetTransactions,
    transform_repository: TransformRepository,
    retry_of_run_id: str | None = None,
) -> TransformRunPlan:
    output_dataset = dataset_registry_service.get_dataset(str(transform["output_dataset_ref"]), ctx=ctx)
    transaction_id = dataset_transaction_service._open_dataset_transaction(
        conn,
        ctx,
        output_dataset,
        output_transaction_type_for_mode(str(transform["mode"])),
    )
    definition_snapshot = _definition_snapshot(transform, source)
    plan = TransformRunPlan(
        transform_id=str(transform["id"]),
        language=str(transform["language"]),
        entrypoint=str(transform["entrypoint"]),
        sql_template=source.sql_template,
        python_source=source.python_source,
        python_function=source.python_function,
        checks=[dict(check) for check in transform["checks"]],
        output_dataset=output_dataset,
        input_versions=input_versions,
        definition_snapshot=definition_snapshot,
        transaction_id=transaction_id,
        run_id=_new_id("transform_run"),
        retry_of_run_id=retry_of_run_id,
    )
    _insert_transform_run(conn, ctx, plan, transform_repository)
    return plan


def _failed_transform_run(
    conn: TransactionContext,
    ctx: RequestContext,
    transform_run_id: str,
    transform_repository: TransformRepository,
) -> TransformRunRow:
    row = transform_repository.transform_run_by_id(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        transform_run_id=transform_run_id,
    )
    if row is None:
        raise NotFound("transform run not found", details={"transform_run_id": transform_run_id})
    if row["status"] != "FAILED":
        raise ValidationFailed("transform run is not failed", details={"transform_run_id": transform_run_id})
    return row


def _insert_transform_run(
    conn: TransactionContext,
    ctx: RequestContext,
    plan: TransformRunPlan,
    transform_repository: TransformRepository,
) -> None:
    transform_repository.insert_transform_run(
        transaction=conn,
        record=TransformRunRecord(
            transform_run_id=plan.run_id,
            tenant_id=ctx.tenant_id,
            transform_id=plan.transform_id,
            status="RUNNING",
            input_versions=plan.input_versions,
            definition_snapshot=plan.definition_snapshot,
            output_version_id=None,
            transaction_id=plan.transaction_id,
            error=None,
            created_at=_now(),
            completed_at=None,
        ),
    )


def mark_transform_run_succeeded(
    repository: TransformRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    run_id: str,
    result: CommitResult,
) -> None:
    updated = repository.update_transform_run_terminal(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        transform_run_id=run_id,
        transition=TRANSFORM_RUN_SUCCEEDED,
        output_version_id=result.version_id,
        error=None,
        completed_at=_now(),
    )
    if not updated:
        raise ConflictDetected("transform run terminal state changed concurrently", details={"run_id": run_id})


def _get_transform(
    conn: TransactionContext,
    ctx: RequestContext,
    api_name: str,
    transform_repository: TransformRepository,
) -> TransformRow:
    row = transform_repository.transform_by_api_name(transaction=conn, tenant_id=ctx.tenant_id, api_name=api_name)
    if row is None:
        raise NotFound("transform not found", details={"api_name": api_name})
    return row


def _resolve_transform_inputs(
    conn: TransactionContext,
    ctx: RequestContext,
    sql_template: str,
    inputs: Mapping[str, str],
    dataset_registry_service: TransformDatasetRegistry,
    dataset_version_service: TransformDatasetVersions,
) -> dict[str, str]:
    resolved: dict[str, str] = {}
    referenced = set(INPUT_PATTERN.findall(sql_template))
    configured = set(inputs.values())
    undeclared = sorted(referenced - configured)
    if undeclared:
        raise ValidationFailed(
            "transform SQL references undeclared input datasets",
            details={"dataset_refs": undeclared},
        )
    for dataset_ref in sorted(referenced | configured):
        dataset = dataset_registry_service.get_dataset(dataset_ref, ctx=ctx)
        version = dataset_version_service._latest_version_by_dataset_id(conn, dataset["id"])
        resolved[dataset_ref] = version["id"]
    return resolved


def transform_input_paths_by_ref(
    plan: TransformRunPlan,
    *,
    dataset_transaction_service: TransformDatasetTransactions,
    dataset_version_service: TransformDatasetVersions,
    partition_filters: Mapping[str, Mapping[str, object]],
) -> dict[str, InputFilePaths]:
    return {
        dataset_ref: tuple(
            _version_input_paths(
                dataset_transaction_service,
                dataset_version_service,
                version_id,
                partition_filters.get(dataset_ref),
            )
        )
        for dataset_ref, version_id in plan.input_versions.items()
    }


def _version_input_paths(
    dataset_transaction_service: TransformDatasetTransactions,
    dataset_version_service: TransformDatasetVersions,
    version_id: str,
    partition_filter: Mapping[str, object] | None,
) -> list[Path]:
    version = dataset_version_service._get_version_by_id(version_id)
    if not partition_filter:
        return dataset_transaction_service._version_file_paths(version)
    paths = dataset_transaction_service._version_file_paths(version, partition_filter=partition_filter)
    return paths if paths else dataset_transaction_service._version_file_paths(version)


def _read_transform_sql_template(entrypoint: str) -> str:
    return Path(entrypoint).read_text(encoding="utf-8")


def _read_transform_source(entrypoint: str, language: str) -> TransformSource:
    if language == "sql":
        return TransformSource(sql_template=_read_transform_sql_template(entrypoint))
    if language == "python":
        path, function_name = _python_entrypoint_parts(entrypoint)
        return TransformSource(
            sql_template="",
            python_source=Path(path).read_text(encoding="utf-8"),
            python_function=function_name,
        )
    raise ValidationFailed("unsupported transform language", details={"language": language})


def _python_entrypoint_parts(entrypoint: str) -> tuple[str, str | None]:
    path, separator, function_name = entrypoint.partition(":")
    if separator and not function_name.strip():
        raise ValidationFailed("Python transform entrypoint function is empty", details={"entrypoint": entrypoint})
    return path, function_name.strip() or None


def normalize_transform_output_mode(mode: str) -> str:
    raw = mode.strip().lower()
    normalized = TRANSFORM_OUTPUT_MODE_ALIASES.get(raw, raw)
    if normalized in SUPPORTED_TRANSFORM_OUTPUT_MODES:
        return normalized
    raise ValidationFailed(
        "unsupported transform output mode",
        details={"mode": mode, "supported_modes": ["snapshot", "append", "incremental"]},
    )


def output_transaction_type_for_mode(mode: str) -> str:
    return "APPEND" if normalize_transform_output_mode(mode) == "append" else "SNAPSHOT"


def _definition_snapshot(transform: TransformRow, source: TransformSource) -> dict[str, object]:
    snapshot: dict[str, object] = {
        "api_name": str(transform["api_name"]),
        "checks": [dict(check) for check in transform["checks"]],
        "entrypoint": str(transform["entrypoint"]),
        "inputs": dict(transform["inputs"]),
        "language": str(transform["language"]),
        "mode": str(transform["mode"]),
        "output_dataset_ref": str(transform["output_dataset_ref"]),
    }
    if str(transform["language"]) == "python":
        python_source = source.python_source or ""
        snapshot["python_source"] = python_source
        snapshot["python_source_sha256"] = sha256(python_source.encode("utf-8")).hexdigest()
        snapshot["python_function"] = source.python_function
    else:
        snapshot["sql_template"] = source.sql_template
        snapshot["sql_template_sha256"] = sha256(source.sql_template.encode("utf-8")).hexdigest()
    return snapshot


def _source_from_run_snapshot(snapshot: Mapping[str, object], transform_run_id: str) -> TransformSource:
    language = _required_snapshot_string(snapshot, "language", transform_run_id)
    if language == "python":
        return TransformSource(
            sql_template="",
            python_source=_required_snapshot_string(snapshot, "python_source", transform_run_id),
            python_function=_optional_snapshot_string(snapshot, "python_function"),
        )
    return TransformSource(
        sql_template=_required_snapshot_string(snapshot, "sql_template", transform_run_id),
    )


def _transform_from_run_snapshot(
    failed_run: TransformRunRow,
    ctx: RequestContext,
    transform_run_id: str,
    snapshot: Mapping[str, object],
) -> TransformRow:
    return cast(
        TransformRow,
        {
            "id": str(failed_run["transform_id"]),
            "tenant_id": ctx.tenant_id,
            "api_name": _required_snapshot_string(snapshot, "api_name", transform_run_id),
            "language": _required_snapshot_string(snapshot, "language", transform_run_id),
            "entrypoint": _required_snapshot_string(snapshot, "entrypoint", transform_run_id),
            "mode": _required_snapshot_string(snapshot, "mode", transform_run_id),
            "inputs": _required_snapshot_string_mapping(snapshot, "inputs", transform_run_id),
            "output_dataset_ref": _required_snapshot_string(snapshot, "output_dataset_ref", transform_run_id),
            "checks": _required_snapshot_checks(snapshot, transform_run_id),
        },
    )


def _required_run_definition_snapshot(
    failed_run: TransformRunRow,
    transform_run_id: str,
) -> Mapping[str, object]:
    snapshot = failed_run["definition_snapshot"]
    if not isinstance(snapshot, dict):
        raise ValidationFailed(
            "transform run cannot be retried without a definition snapshot",
            details={"transform_run_id": transform_run_id},
        )
    return snapshot


def _required_snapshot_string(snapshot: Mapping[str, object], key: str, transform_run_id: str) -> str:
    value = snapshot.get(key)
    if not isinstance(value, str) or value.strip() == "":
        raise ValidationFailed(
            "transform run definition snapshot is incomplete",
            details={"transform_run_id": transform_run_id, "missing": key},
        )
    return value


def _optional_snapshot_string(snapshot: Mapping[str, object], key: str) -> str | None:
    value = snapshot.get(key)
    return value if isinstance(value, str) and value.strip() else None


def _required_snapshot_string_mapping(
    snapshot: Mapping[str, object],
    key: str,
    transform_run_id: str,
) -> dict[str, str]:
    value = snapshot.get(key)
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise ValidationFailed(
            "transform run definition snapshot is incomplete",
            details={"transform_run_id": transform_run_id, "missing": key},
        )
    return cast(dict[str, str], dict(value))


def _required_snapshot_checks(snapshot: Mapping[str, object], transform_run_id: str) -> list[dict[str, object]]:
    value = snapshot.get("checks")
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValidationFailed(
            "transform run definition snapshot is incomplete",
            details={"transform_run_id": transform_run_id, "missing": "checks"},
        )
    return [cast(dict[str, object], dict(item)) for item in value]
