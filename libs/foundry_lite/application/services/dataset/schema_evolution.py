"""Dataset schema evolution planning for commit-time compatibility checks."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from foundry_lite.application.ports import DatasetCheckResult, DatasetSchemaJson, DatasetSchemaReference
from foundry_lite.application.ports.dataset_version_repository import DatasetSchemaRow
from foundry_lite.application.primitives import _json_hash
from foundry_lite.application.services.dataset.schema_backfill import resume_schema_backfill_progress
from foundry_lite.domain.errors import ValidationFailed

DatasetSchemaColumn = Mapping[str, object]
DatasetColumnsByName = dict[str, DatasetSchemaColumn]

TYPE_ALIASES = {
    "bigint": "long",
    "bool": "boolean",
    "date": "timestamp",
    "datetime": "timestamp",
    "decimal": "float",
    "double": "float",
    "int": "integer",
    "long": "long",
    "real": "float",
}
NUMERIC_WIDENING_RANK = {"integer": 1, "long": 2, "float": 3}


@dataclass(frozen=True)
class DatasetSchemaEvolutionChange:
    """One schema change classified for compatibility and operator evidence."""

    kind: str
    column: str
    status: str
    reason: str
    previous_type: str | None = None
    next_type: str | None = None
    require_backfill: bool = False

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "kind": self.kind,
            "column": self.column,
            "status": self.status,
            "reason": self.reason,
        }
        if self.previous_type is not None:
            payload["previousType"] = self.previous_type
        if self.next_type is not None:
            payload["nextType"] = self.next_type
        if self.require_backfill:
            payload["requiresBackfill"] = True
        return payload


@dataclass(frozen=True)
class DatasetSchemaEvolutionPlan:
    """Compatibility plan for a candidate dataset schema."""

    changes: tuple[DatasetSchemaEvolutionChange, ...]

    @property
    def has_changes(self) -> bool:
        return bool(self.changes)

    @property
    def status(self) -> str:
        if self.blocking_changes:
            return "blocked"
        if self.warning_changes:
            return "compatible_with_warning"
        return "compatible"

    @property
    def blocking_changes(self) -> tuple[DatasetSchemaEvolutionChange, ...]:
        return tuple(change for change in self.changes if change.status == "blocked")

    @property
    def warning_changes(self) -> tuple[DatasetSchemaEvolutionChange, ...]:
        return tuple(change for change in self.changes if change.status == "warning")

    @property
    def backfill_changes(self) -> tuple[DatasetSchemaEvolutionChange, ...]:
        return tuple(change for change in self.changes if change.require_backfill)

    def failure(self) -> DatasetCheckResult | None:
        """Return a blocking compatibility failure payload when needed."""
        if not self.blocking_changes:
            return None
        return {
            "check": "schema_compatibility",
            "status": "failed",
            "compatibilityStatus": self.status,
            "consumerCompatibility": "blocked_until_schema_migration_plan",
            "changes": [change.to_payload() for change in self.changes],
            "blockedChanges": [change.to_payload() for change in self.blocking_changes],
        }

    def metadata(
        self,
        *,
        dataset_id: str,
        current_schema: DatasetSchemaRow,
        next_schema: DatasetSchemaJson,
        target_schema: DatasetSchemaReference,
    ) -> dict[str, object] | None:
        """Return operator-visible metadata for compatible schema changes."""
        if not self.has_changes:
            return None
        metadata: dict[str, object] = {
            "status": self.status,
            "consumerCompatibility": _consumer_compatibility(self),
            "sourceSchemaVersionId": current_schema["id"],
            "sourceSchemaVersion": current_schema["version"],
            "targetSchemaVersionId": target_schema.schema_id,
            "targetSchemaVersion": target_schema.version,
            "changes": [change.to_payload() for change in self.changes],
        }
        if self.backfill_changes:
            metadata["backfillProgress"] = resume_schema_backfill_progress(
                dataset_id=dataset_id,
                source_schema_hash=str(current_schema["schema_hash"]),
                target_schema_hash=_json_hash(next_schema),
                changes=self.backfill_changes,
            )
        return metadata


@dataclass(frozen=True)
class DatasetSchemaEvolutionResult:
    """Commit-time schema evolution result returned to Dataset transactions."""

    failure: DatasetCheckResult | None
    metadata: dict[str, object] | None


def build_schema_evolution_result(
    *,
    dataset_id: str,
    current_schema: DatasetSchemaRow,
    next_schema: DatasetSchemaJson,
    primary_key: Sequence[str],
    target_schema: DatasetSchemaReference,
) -> DatasetSchemaEvolutionResult:
    """Build the failure and metadata payloads for one candidate schema."""
    plan = plan_schema_evolution(
        current_schema=current_schema["schema_json"],
        next_schema=next_schema,
        primary_key=primary_key,
    )
    return DatasetSchemaEvolutionResult(
        failure=plan.failure(),
        metadata=plan.metadata(
            dataset_id=dataset_id,
            current_schema=current_schema,
            next_schema=next_schema,
            target_schema=target_schema,
        ),
    )


def plan_schema_evolution(
    *,
    current_schema: DatasetSchemaJson,
    next_schema: DatasetSchemaJson,
    primary_key: Sequence[str],
) -> DatasetSchemaEvolutionPlan:
    """Classify a candidate schema against the latest committed schema."""
    current_columns = schema_columns_by_name(current_schema)
    next_columns = schema_columns_by_name(next_schema)
    changes = [
        *_removed_columns(current_columns, next_columns),
        *_added_columns(current_columns, next_columns),
        *_changed_columns(current_columns, next_columns),
        *_primary_key_changes(current_schema, next_schema, next_columns, primary_key),
    ]
    return DatasetSchemaEvolutionPlan(tuple(changes))


def schema_columns_by_name(schema: DatasetSchemaJson) -> DatasetColumnsByName:
    """Return schema columns keyed by name after validating the common shape."""
    columns = schema.get("columns")
    if not isinstance(columns, Sequence) or isinstance(columns, str | bytes):
        raise ValidationFailed("dataset schema columns must be a list")
    result: DatasetColumnsByName = {}
    for index, column in enumerate(columns):
        column_map = _schema_column(column, index)
        result[_column_name(column_map, index)] = column_map
    return result


def resolve_inferred_null_types(
    current_schema: DatasetSchemaJson,
    next_schema: DatasetSchemaJson,
) -> DatasetSchemaJson:
    """Keep a known logical type when a new snapshot contains only null values."""

    current_columns = schema_columns_by_name(current_schema)
    next_columns = schema_columns_by_name(next_schema)
    resolved_columns: list[dict[str, object]] = []
    for name, raw_column in next_columns.items():
        column = dict(raw_column)
        previous = current_columns.get(name)
        if _column_type(column) == "null" and previous is not None and _column_type(previous) != "null":
            column["type"] = _column_type(previous)
        resolved_columns.append(column)
    return {**next_schema, "columns": resolved_columns}


def _removed_columns(
    current_columns: DatasetColumnsByName,
    next_columns: DatasetColumnsByName,
) -> list[DatasetSchemaEvolutionChange]:
    return [
        DatasetSchemaEvolutionChange(
            kind="drop_column",
            column=name,
            status="blocked",
            reason="column deletion can break transforms, ontology mappings, and consumers",
            previous_type=_column_type(column),
        )
        for name, column in sorted(current_columns.items())
        if name not in next_columns
    ]


def _added_columns(
    current_columns: DatasetColumnsByName,
    next_columns: DatasetColumnsByName,
) -> list[DatasetSchemaEvolutionChange]:
    return [
        _added_column_change(name, column)
        for name, column in sorted(next_columns.items())
        if name not in current_columns
    ]


def _changed_columns(
    current_columns: DatasetColumnsByName,
    next_columns: DatasetColumnsByName,
) -> list[DatasetSchemaEvolutionChange]:
    changes: list[DatasetSchemaEvolutionChange] = []
    for name, current in sorted(current_columns.items()):
        if name not in next_columns:
            continue
        changes.extend(_column_type_changes(name, current, next_columns[name]))
        changes.extend(_column_nullability_changes(name, current, next_columns[name]))
        if _is_deprecated(next_columns[name]) and not _is_deprecated(current):
            changes.append(_deprecated_column_change(name, next_columns[name]))
    return changes


def _primary_key_changes(
    current_schema: DatasetSchemaJson,
    next_schema: DatasetSchemaJson,
    next_columns: DatasetColumnsByName,
    primary_key: Sequence[str],
) -> list[DatasetSchemaEvolutionChange]:
    missing = [
        _blocked_primary_key_change(pk, "primary key column is missing from the next schema")
        for pk in primary_key
        if pk not in next_columns
    ]
    if _schema_primary_key(current_schema) == _schema_primary_key(next_schema):
        return missing
    return [*missing, _blocked_primary_key_change(",".join(primary_key), "primary key changes require a new dataset")]


def _added_column_change(name: str, column: DatasetSchemaColumn) -> DatasetSchemaEvolutionChange:
    if _rename_source(column):
        return DatasetSchemaEvolutionChange(
            kind="rename_column",
            column=name,
            status="blocked",
            reason="renames require an explicit migration plan and consumer mapping",
            next_type=_column_type(column),
        )
    if not _nullable(column) and not _has_default(column):
        return DatasetSchemaEvolutionChange(
            kind="add_required_column_without_default",
            column=name,
            status="blocked",
            reason="new non-null columns require a default or backfill before old writers can coexist",
            next_type=_column_type(column),
            require_backfill=True,
        )
    if not _nullable(column):
        return DatasetSchemaEvolutionChange(
            kind="add_required_column_with_default",
            column=name,
            status="warning",
            reason="existing rows need a deterministic backfill/default proof",
            next_type=_column_type(column),
            require_backfill=True,
        )
    return DatasetSchemaEvolutionChange(
        kind="add_nullable_column",
        column=name,
        status="compatible",
        reason="nullable column addition is backward-compatible",
        next_type=_column_type(column),
    )


def _column_type_changes(
    name: str,
    current: DatasetSchemaColumn,
    next_column: DatasetSchemaColumn,
) -> list[DatasetSchemaEvolutionChange]:
    previous_type = _column_type(current)
    next_type = _column_type(next_column)
    if previous_type == next_type:
        return []
    if previous_type == "null":
        return [
            DatasetSchemaEvolutionChange(
                kind="resolve_null_type",
                column=name,
                status="compatible",
                reason="earlier snapshots contained only null values and established no conflicting logical type",
                previous_type=previous_type,
                next_type=next_type,
            )
        ]
    if next_type == "null":
        return []
    if _is_type_widening(previous_type, next_type):
        return [_type_widening_change(name, previous_type, next_type)]
    return [_blocking_type_change(name, previous_type, next_type)]


def _column_nullability_changes(
    name: str,
    current: DatasetSchemaColumn,
    next_column: DatasetSchemaColumn,
) -> list[DatasetSchemaEvolutionChange]:
    if _nullable(current) and not _nullable(next_column):
        return [
            DatasetSchemaEvolutionChange(
                kind="tighten_nullability",
                column=name,
                status="blocked",
                reason="nullable to non-null requires full validation and a backfill plan",
                previous_type=_column_type(current),
                next_type=_column_type(next_column),
                require_backfill=True,
            )
        ]
    if not _nullable(current) and _nullable(next_column):
        return [_nullability_relaxed_change(name, next_column)]
    return []


def _schema_column(value: object, index: int) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValidationFailed("dataset schema column must be a mapping", details={"index": index})
    result: dict[str, object] = {}
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str):
            raise ValidationFailed("dataset schema column keys must be strings", details={"index": index})
        result[raw_key] = raw_value
    return result


def _column_name(column: Mapping[str, object], index: int) -> str:
    name = column.get("name")
    if not isinstance(name, str) or not name:
        raise ValidationFailed("dataset schema column name must be a non-empty string", details={"index": index})
    return name


def _column_type(column: DatasetSchemaColumn) -> str:
    raw_type = str(column.get("type", "string")).lower()
    return TYPE_ALIASES.get(raw_type, raw_type)


def _nullable(column: DatasetSchemaColumn) -> bool:
    value = column.get("nullable")
    return value if isinstance(value, bool) else True


def _has_default(column: DatasetSchemaColumn) -> bool:
    return any(key in column for key in ("default", "defaultValue", "backfillDefault"))


def _rename_source(column: DatasetSchemaColumn) -> str | None:
    value = column.get("renamedFrom") or column.get("previousName")
    return value if isinstance(value, str) and value else None


def _is_deprecated(column: DatasetSchemaColumn) -> bool:
    return column.get("deprecated") is True


def _is_type_widening(previous_type: str, next_type: str) -> bool:
    previous_rank = NUMERIC_WIDENING_RANK.get(previous_type)
    next_rank = NUMERIC_WIDENING_RANK.get(next_type)
    return previous_rank is not None and next_rank is not None and next_rank > previous_rank


def _type_widening_change(name: str, previous_type: str, next_type: str) -> DatasetSchemaEvolutionChange:
    return DatasetSchemaEvolutionChange(
        kind="type_widening",
        column=name,
        status="warning",
        reason="numeric widening is compatible but downstream schema hashes should refresh",
        previous_type=previous_type,
        next_type=next_type,
    )


def _blocking_type_change(name: str, previous_type: str, next_type: str) -> DatasetSchemaEvolutionChange:
    return DatasetSchemaEvolutionChange(
        kind="type_change",
        column=name,
        status="blocked",
        reason="type narrowing or incompatible type changes can break consumers",
        previous_type=previous_type,
        next_type=next_type,
    )


def _nullability_relaxed_change(name: str, next_column: DatasetSchemaColumn) -> DatasetSchemaEvolutionChange:
    return DatasetSchemaEvolutionChange(
        kind="relax_nullability",
        column=name,
        status="warning",
        reason="non-null to nullable is compatible but downstream checks should refresh",
        next_type=_column_type(next_column),
    )


def _deprecated_column_change(name: str, column: DatasetSchemaColumn) -> DatasetSchemaEvolutionChange:
    return DatasetSchemaEvolutionChange(
        kind="deprecate_column",
        column=name,
        status="warning",
        reason="deprecated fields stay readable while consumers migrate",
        next_type=_column_type(column),
    )


def _blocked_primary_key_change(column: str, reason: str) -> DatasetSchemaEvolutionChange:
    return DatasetSchemaEvolutionChange(
        kind="primary_key_change",
        column=column,
        status="blocked",
        reason=reason,
    )


def _schema_primary_key(schema: DatasetSchemaJson) -> tuple[str, ...]:
    value = schema.get("primary_key")
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return ()
    return tuple(str(item) for item in value)


def _consumer_compatibility(plan: DatasetSchemaEvolutionPlan) -> str:
    if plan.blocking_changes:
        return "blocked"
    if plan.warning_changes:
        return "review_recommended"
    return "compatible"
