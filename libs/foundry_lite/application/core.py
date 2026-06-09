from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, cast, overload
from uuid import uuid4

import duckdb
import yaml
from sqlalchemy import and_, create_engine, desc, func, insert, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from foundry_lite.application import (
    SUPPLY_CHAIN_DEMO_ROOT,
    ensure_supply_chain_demo_files,
    evaluate_safe_expression,
    matches_filter,
    precondition_expression,
    register_supply_chain_demo_transforms,
    require_csv_size_limit,
    validate_action_request,
)
from foundry_lite.domain.context import DEFAULT_ACTOR_USER_ID, DEFAULT_TENANT_ID, RequestContext, demo_admin_context
from foundry_lite.domain.errors import (
    ConflictDetected,
    ExternalSystemError,
    InvariantViolation,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.observability.tracing import trace_public_methods
from foundry_lite.security.policy import PolicyService

INPUT_PATTERN = re.compile(r"\{\{\s*input\('([^']+)'\)\s*\}\}")
SQL_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MOCK_WRITEBACK_CONNECTOR = "mock_erp_simulator"


@dataclass(frozen=True)
class CommitResult:
    dataset_id: str
    dataset_ref: str
    transaction_id: str
    version_id: str
    version_number: int
    row_count: int
    manifest_uri: str
    schema_hash: str


@dataclass(frozen=True)
class StagedFileStats:
    parquet_path: Path
    row_count: int
    byte_size: int
    content_hash: str
    schema_json: dict[str, Any]
    schema_hash: str


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _sql_literal(path: str | Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def _sql_identifier(value: str) -> str:
    if not SQL_IDENTIFIER_PATTERN.fullmatch(value):
        raise ValidationFailed("unsafe SQL identifier", details={"identifier": value})
    return f'"{value}"'


def _json_hash(value: dict[str, Any]) -> str:
    payload = json.dumps(_json_ready(value), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _required_row(row: tuple[Any, ...] | None, operation: str) -> tuple[Any, ...]:
    if row is None:
        raise InvariantViolation(f"{operation} did not return a row")
    return row


def _json_ready(value: Any) -> Any:
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    return value


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dataset_ref_parts(dataset_ref: str) -> tuple[str, str]:
    if "." not in dataset_ref:
        raise ValidationFailed(
            "dataset reference must be namespace.name",
            details={"dataset_ref": dataset_ref},
        )
    namespace, name = dataset_ref.split(".", 1)
    if not namespace or not name:
        raise ValidationFailed(
            "dataset reference must be namespace.name",
            details={"dataset_ref": dataset_ref},
        )
    return namespace, name


def _dataset_ref(row: dict[str, Any]) -> str:
    return f"{row['namespace']}.{row['name']}"


def _rows_from_parquet(path: Path) -> list[dict[str, Any]]:
    con = duckdb.connect()
    try:
        result = con.execute("select * from read_parquet(?)", [str(path)])
        names = [column[0] for column in result.description]
        return [_json_ready(dict(zip(names, row, strict=True))) for row in result.fetchall()]
    finally:
        con.close()


def _write_rows_to_csv(rows: list[dict[str, Any]], path: Path, fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fieldnames})


def _normalize_duckdb_type(duckdb_type: str) -> str:
    normalized = duckdb_type.upper()
    if any(token in normalized for token in ["INT", "BIGINT", "HUGEINT", "SMALLINT"]):
        return "integer"
    if any(token in normalized for token in ["DOUBLE", "FLOAT", "DECIMAL", "REAL"]):
        return "float"
    if "BOOL" in normalized:
        return "boolean"
    if "TIME" in normalized or "DATE" in normalized:
        return "timestamp"
    return "string"


@trace_public_methods
class FoundryLiteCore:
    """Application service for the MVP closed loop.

    The local implementation uses SQLite and filesystem storage, while preserving the documented
    transaction, manifest, audit, lineage, and outbox contracts.
    """

    def __init__(
        self,
        *,
        db_url: str | None = None,
        storage_root: str | Path | None = None,
    ) -> None:
        self.root = Path(storage_root or ".foundry-lite").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.storage_root = self.root / "object-storage"
        self.storage_root.mkdir(parents=True, exist_ok=True)
        database_url = db_url or f"sqlite:///{self.root / 'foundry-lite.db'}"
        self.engine = create_engine(database_url, future=True)
        db.create_database(self.engine)
        self.policy = PolicyService()
        self.bootstrap()

    def reset(self, *, confirm_dev: bool = False) -> None:
        if not confirm_dev:
            raise ValidationFailed("reset is destructive and requires confirm_dev=True")
        db.metadata.drop_all(self.engine)
        db.create_database(self.engine)
        if self.storage_root.exists():
            shutil.rmtree(self.storage_root)
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self.bootstrap()

    def bootstrap(self) -> None:
        with self.engine.begin() as conn:
            if self._select_by_id(conn, db.tenants, DEFAULT_TENANT_ID) is None:
                conn.execute(
                    insert(db.tenants).values(
                        id=DEFAULT_TENANT_ID,
                        name="Demo Tenant",
                        created_at=_now(),
                    )
                )
            if self._select_by_id(conn, db.users, DEFAULT_ACTOR_USER_ID) is None:
                conn.execute(
                    insert(db.users).values(
                        id=DEFAULT_ACTOR_USER_ID,
                        tenant_id=DEFAULT_TENANT_ID,
                        email="demo@foundry-lite.local",
                        roles=["admin", "data_engineer", "ops_manager", "finance"],
                        created_at=_now(),
                    )
                )

    def create_dataset(
        self,
        dataset_ref: str,
        *,
        ctx: RequestContext | None = None,
        primary_key: list[str] | None = None,
        storage_kind: str = "parquet_manifest",
        description: str | None = None,
        owner_team: str | None = None,
        classification: str | None = None,
    ) -> dict[str, Any]:
        ctx = ctx or RequestContext()
        self._require_or_audit(ctx, "dataset:write", "dataset", dataset_ref)
        namespace, name = _dataset_ref_parts(dataset_ref)
        dataset_id = _new_id("ds")
        now = _now()
        with self.engine.begin() as conn:
            try:
                conn.execute(
                    insert(db.datasets).values(
                        id=dataset_id,
                        tenant_id=ctx.tenant_id,
                        namespace=namespace,
                        name=name,
                        description=description,
                        storage_kind=storage_kind,
                        storage_uri=str(self.storage_root / ctx.tenant_id / "datasets" / dataset_id),
                        owner_team=owner_team,
                        classification=classification,
                        status="active",
                        primary_key=primary_key or [],
                        created_at=now,
                        updated_at=now,
                    )
                )
            except IntegrityError as exc:
                raise ConflictDetected(
                    "dataset already exists in this tenant",
                    details={"dataset_ref": dataset_ref},
                ) from exc
            self._audit(
                conn,
                ctx,
                event_type="dataset.created",
                resource_type="dataset",
                resource_id=dataset_id,
                action="create",
                after_ref={"dataset_ref": dataset_ref},
            )
        return self.get_dataset(dataset_ref, ctx=ctx)

    def ensure_dataset(
        self,
        dataset_ref: str,
        *,
        ctx: RequestContext | None = None,
        primary_key: list[str] | None = None,
        storage_kind: str = "parquet_manifest",
    ) -> dict[str, Any]:
        ctx = ctx or RequestContext()
        existing = self.find_dataset(dataset_ref, ctx=ctx)
        if existing is not None:
            return existing
        return self.create_dataset(
            dataset_ref,
            ctx=ctx,
            primary_key=primary_key,
            storage_kind=storage_kind,
        )

    def find_dataset(self, dataset_ref: str, *, ctx: RequestContext | None = None) -> dict[str, Any] | None:
        ctx = ctx or RequestContext()
        namespace, name = _dataset_ref_parts(dataset_ref)
        with self.engine.begin() as conn:
            row = (
                conn.execute(
                    select(db.datasets).where(
                        and_(
                            db.datasets.c.tenant_id == ctx.tenant_id,
                            db.datasets.c.namespace == namespace,
                            db.datasets.c.name == name,
                            db.datasets.c.status == "active",
                        )
                    )
                )
                .mappings()
                .first()
            )
            return dict(row) if row else None

    def get_dataset(self, dataset_ref: str, *, ctx: RequestContext | None = None) -> dict[str, Any]:
        ctx = ctx or RequestContext()
        self._require_or_audit(ctx, "dataset:read", "dataset", dataset_ref)
        dataset = self.find_dataset(dataset_ref, ctx=ctx)
        if dataset is None:
            raise NotFound("dataset not found", details={"dataset_ref": dataset_ref})
        return dataset

    def upload_csv(
        self,
        dataset_ref: str,
        csv_path: str | Path,
        *,
        ctx: RequestContext | None = None,
        sync_name: str | None = None,
    ) -> CommitResult:
        ctx = ctx or RequestContext()
        self._require_or_audit(ctx, "dataset:write", "dataset", dataset_ref)
        dataset = self.get_dataset(dataset_ref, ctx=ctx)
        source_path = Path(csv_path).resolve()
        if not source_path.exists():
            raise NotFound("csv file not found", details={"path": str(source_path)})
        require_csv_size_limit(source_path)

        with self.engine.begin() as conn:
            tx_id = self._open_dataset_transaction(conn, ctx, dataset, "SNAPSHOT")
            run_id = _new_id("sync_run")
            conn.execute(
                insert(db.sync_runs).values(
                    id=run_id,
                    tenant_id=ctx.tenant_id,
                    sync_name=sync_name or f"upload:{dataset_ref}",
                    source_type="file.csv",
                    output_dataset_id=dataset["id"],
                    transaction_id=tx_id,
                    committed_version_id=None,
                    status="EXTRACTING",
                    error=None,
                    created_at=_now(),
                    completed_at=None,
                )
            )

        staged = self._staging_file(dataset, tx_id, "part-00000.parquet")
        try:
            self._csv_to_parquet(source_path, staged)
            with self.engine.begin() as conn:
                result = self._finalize_open_transaction(
                    conn,
                    ctx,
                    dataset=dataset,
                    transaction_id=tx_id,
                    staged_parquet=staged,
                    run_id=run_id,
                    audit_action="csv_upload_commit",
                    outbox_event_type="dataset.version.committed",
                )
                conn.execute(
                    update(db.sync_runs)
                    .where(db.sync_runs.c.id == run_id)
                    .values(
                        status="COMMITTED",
                        committed_version_id=result.version_id,
                        completed_at=_now(),
                    )
                )
                return result
        except Exception as exc:
            self._abort_transaction_after_error(ctx, tx_id, run_id, exc, db.sync_runs)
            if isinstance(exc, ValidationFailed | NotFound | ConflictDetected | InvariantViolation):
                raise
            raise ValidationFailed("csv upload failed", details={"error": str(exc)}) from exc

    def list_dataset_versions(
        self,
        dataset_ref: str,
        *,
        ctx: RequestContext | None = None,
    ) -> list[dict[str, Any]]:
        ctx = ctx or RequestContext()
        dataset = self.get_dataset(dataset_ref, ctx=ctx)
        with self.engine.begin() as conn:
            rows = (
                conn.execute(
                    select(db.dataset_versions)
                    .where(db.dataset_versions.c.dataset_id == dataset["id"])
                    .order_by(db.dataset_versions.c.version_number)
                )
                .mappings()
                .all()
            )
            return [dict(row) for row in rows]

    def preview_dataset(
        self,
        dataset_ref: str,
        *,
        ctx: RequestContext | None = None,
        limit: int = 100,
        version: str = "latest",
    ) -> list[dict[str, Any]]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "dataset:read")
        dataset = self.get_dataset(dataset_ref, ctx=ctx)
        version_row = self._get_version(dataset["id"], version, ctx=ctx)
        parquet_path = self._version_file_path(version_row)
        con = duckdb.connect()
        try:
            result = con.execute("select * from read_parquet(?) limit ?", [str(parquet_path), int(limit)])
            columns = [column[0] for column in result.description]
            return [dict(zip(columns, row, strict=True)) for row in result.fetchall()]
        finally:
            con.close()

    def inspect_dataset(
        self,
        dataset_ref: str,
        *,
        ctx: RequestContext | None = None,
        version: str = "latest",
    ) -> dict[str, Any]:
        ctx = ctx or RequestContext()
        dataset = self.get_dataset(dataset_ref, ctx=ctx)
        version_row = self._get_version(dataset["id"], version, ctx=ctx)
        schema_row = self._schema_for_version(dataset["id"], version_row["schema_version"])
        return {
            "dataset": dataset_ref,
            "dataset_id": dataset["id"],
            "version": dict(version_row),
            "schema": schema_row["schema_json"],
            "manifest": self._load_manifest(version_row["manifest_uri"]),
        }

    def register_transform(
        self,
        api_name: str,
        *,
        entrypoint: str | Path,
        inputs: dict[str, str],
        output_dataset_ref: str,
        checks: list[dict[str, Any]] | None = None,
        ctx: RequestContext | None = None,
        mode: str = "snapshot",
        language: str = "sql",
    ) -> dict[str, Any]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "transform:run")
        transform_id = _new_id("tf")
        with self.engine.begin() as conn:
            existing = (
                conn.execute(
                    select(db.transforms).where(
                        and_(
                            db.transforms.c.tenant_id == ctx.tenant_id,
                            db.transforms.c.api_name == api_name,
                        )
                    )
                )
                .mappings()
                .first()
            )
            if existing:
                conn.execute(
                    update(db.transforms)
                    .where(db.transforms.c.id == existing["id"])
                    .values(
                        language=language,
                        entrypoint=str(entrypoint),
                        mode=mode,
                        inputs=inputs,
                        output_dataset_ref=output_dataset_ref,
                        checks=checks or [],
                    )
                )
                return dict(existing)
            conn.execute(
                insert(db.transforms).values(
                    id=transform_id,
                    tenant_id=ctx.tenant_id,
                    api_name=api_name,
                    language=language,
                    entrypoint=str(entrypoint),
                    mode=mode,
                    inputs=inputs,
                    output_dataset_ref=output_dataset_ref,
                    checks=checks or [],
                )
            )
            return self._select_by_id(conn, db.transforms, transform_id) or {}

    def run_transform(self, api_name: str, *, ctx: RequestContext | None = None) -> CommitResult:
        ctx = ctx or RequestContext()
        self._require_or_audit(ctx, "transform:run", "transform", api_name)
        with self.engine.begin() as conn:
            transform = self._get_transform(conn, ctx, api_name)
            output_dataset = self.get_dataset(transform["output_dataset_ref"], ctx=ctx)
            input_versions = self._resolve_transform_inputs(conn, ctx, transform)
            tx_id = self._open_dataset_transaction(conn, ctx, output_dataset, "SNAPSHOT")
            run_id = _new_id("transform_run")
            conn.execute(
                insert(db.transform_runs).values(
                    id=run_id,
                    tenant_id=ctx.tenant_id,
                    transform_id=transform["id"],
                    status="RUNNING",
                    input_versions=input_versions,
                    output_version_id=None,
                    transaction_id=tx_id,
                    error=None,
                    created_at=_now(),
                    completed_at=None,
                )
            )

        staged = self._staging_file(output_dataset, tx_id, "part-00000.parquet")
        try:
            self._execute_sql_transform(transform, input_versions, staged)
            with self.engine.begin() as conn:
                result = self._finalize_open_transaction(
                    conn,
                    ctx,
                    dataset=output_dataset,
                    transaction_id=tx_id,
                    staged_parquet=staged,
                    run_id=run_id,
                    audit_action="transform_output_commit",
                    outbox_event_type="dataset.version.committed",
                    extra_checks=transform["checks"],
                )
                for version_id in input_versions.values():
                    self._lineage(
                        conn,
                        ctx,
                        "dataset_version",
                        version_id,
                        "dataset_version",
                        result.version_id,
                        "input_to",
                        run_id,
                    )
                conn.execute(
                    update(db.transform_runs)
                    .where(db.transform_runs.c.id == run_id)
                    .values(
                        status="SUCCESS",
                        output_version_id=result.version_id,
                        completed_at=_now(),
                    )
                )
                return result
        except Exception as exc:
            self._abort_transaction_after_error(ctx, tx_id, run_id, exc, db.transform_runs)
            if isinstance(exc, ValidationFailed | NotFound | ConflictDetected | InvariantViolation):
                raise
            raise ValidationFailed("transform failed", details={"error": str(exc)}) from exc

    def apply_ontology(
        self,
        yaml_path: str | Path,
        *,
        ctx: RequestContext | None = None,
    ) -> dict[str, Any]:
        ctx = ctx or RequestContext()
        self._require_or_audit(ctx, "ontology:activate", "ontology", "draft")
        definition = yaml.safe_load(Path(yaml_path).read_text(encoding="utf-8"))
        if not isinstance(definition, dict):
            raise ValidationFailed("ontology yaml must be a mapping")
        with self.engine.begin() as conn:
            version_number = self._next_ontology_version(conn, ctx)
            ontology_version_id = _new_id("ont")
            conn.execute(
                insert(db.ontology_versions).values(
                    id=ontology_version_id,
                    tenant_id=ctx.tenant_id,
                    version_number=version_number,
                    status="draft",
                    created_by=ctx.actor_user_id,
                    created_at=_now(),
                    activated_at=None,
                )
            )
            object_map = self._import_object_types(conn, ctx, ontology_version_id, definition)
            self._import_link_types(conn, ctx, ontology_version_id, definition, object_map)
            self._import_action_types(conn, ctx, ontology_version_id, definition, object_map)
            self._validate_ontology(conn, ctx, ontology_version_id)
            conn.execute(
                update(db.ontology_versions)
                .where(
                    and_(
                        db.ontology_versions.c.tenant_id == ctx.tenant_id,
                        db.ontology_versions.c.status == "active",
                    )
                )
                .values(status="archived")
            )
            conn.execute(
                update(db.ontology_versions)
                .where(db.ontology_versions.c.id == ontology_version_id)
                .values(status="active", activated_at=_now())
            )
            self._outbox(
                conn,
                ctx,
                "ontology.version.activated",
                "ontology_version",
                ontology_version_id,
                {"ontologyVersionId": ontology_version_id},
                idempotency_key=ontology_version_id,
                correlation_id=ctx.request_id,
            )
            self._audit(
                conn,
                ctx,
                event_type="ontology.version.activated",
                resource_type="ontology_version",
                resource_id=ontology_version_id,
                action="activate",
                after_ref={"version_number": version_number},
            )
            return {"ontology_version_id": ontology_version_id, "version_number": version_number}

    def index_rebuild(
        self,
        object_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
    ) -> dict[str, Any]:
        ctx = ctx or RequestContext()
        with self.engine.begin() as conn:
            object_type = self._active_object_type(conn, ctx, object_type_api_name)
            backing = object_type["backing"]
            dataset = self.get_dataset(backing["dataset"], ctx=ctx)
            version = self._latest_version_by_dataset_id(conn, dataset["id"])
            run_id = _new_id("index_run")
            conn.execute(
                insert(db.index_runs).values(
                    id=run_id,
                    tenant_id=ctx.tenant_id,
                    object_type_id=object_type["id"],
                    object_type_api_name=object_type_api_name,
                    trigger_type="reindex",
                    source_ref={"dataset_version_id": version["id"]},
                    status="running",
                    cursor={},
                    rows_read=0,
                    objects_upserted=0,
                    objects_deleted=0,
                    links_upserted=0,
                    error=None,
                    started_at=_now(),
                    completed_at=None,
                    created_at=_now(),
                )
            )

        rows = _rows_from_parquet(self._version_file_path(version))
        objects_upserted = 0
        links_upserted = 0
        try:
            with self.engine.begin() as conn:
                for row in rows:
                    self._index_object_row(conn, ctx, object_type, row, version["id"])
                    objects_upserted += 1
                links_upserted = self._index_links_for_object_type(
                    conn,
                    ctx,
                    object_type,
                    rows,
                    version["id"],
                )
                conn.execute(
                    update(db.index_runs)
                    .where(db.index_runs.c.id == run_id)
                    .values(
                        status="succeeded",
                        rows_read=len(rows),
                        objects_upserted=objects_upserted,
                        links_upserted=links_upserted,
                        cursor={"last_row": len(rows)},
                        completed_at=_now(),
                    )
                )
                self._audit(
                    conn,
                    ctx,
                    event_type="object.index.rebuilt",
                    resource_type="object_type",
                    resource_id=object_type["id"],
                    action="index_rebuild",
                    after_ref={"objects_upserted": objects_upserted},
                )
            return {
                "index_run_id": run_id,
                "object_type": object_type_api_name,
                "rows_read": len(rows),
                "objects_upserted": objects_upserted,
                "links_upserted": links_upserted,
            }
        except Exception as exc:
            with self.engine.begin() as conn:
                conn.execute(
                    update(db.index_runs)
                    .where(db.index_runs.c.id == run_id)
                    .values(status="failed", error=self._error_payload(exc), completed_at=_now())
                )
            raise

    def get_object(
        self,
        object_type_api_name: str,
        object_id: str,
        *,
        ctx: RequestContext | None = None,
        explain: bool = False,
    ) -> dict[str, Any]:
        ctx = ctx or RequestContext()
        self._require_or_audit(ctx, "object:read", object_type_api_name, object_id)
        with self.engine.begin() as conn:
            record = self._object_record(conn, ctx, object_type_api_name, object_id)
            if record is None:
                raise NotFound(
                    "object not found",
                    details={"object_type": object_type_api_name, "object_id": object_id},
                )
            properties = self.policy.mask_properties(
                ctx,
                object_type_api_name,
                dict(record["properties"]),
            )
            payload = {
                "objectType": object_type_api_name,
                "objectId": object_id,
                "objectVersion": record["object_version"],
                "properties": properties,
                "sourceDatasetVersionId": record["source_dataset_version_id"],
            }
            if explain:
                payload["explain"] = {
                    "baseProperties": record["base_properties"],
                    "editProperties": record["edit_properties"],
                    "lineage": self.lineage_for_resource(record["source_dataset_version_id"], ctx=ctx)
                    if record["source_dataset_version_id"]
                    else [],
                }
            return payload

    def query_objects(
        self,
        object_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
        filter_ast: dict[str, Any] | None = None,
        order_by: list[dict[str, str]] | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "object:read")
        with self.engine.begin() as conn:
            records = self._object_query_rows(conn, ctx, object_type_api_name)
        records = self._apply_object_query_options(records, cursor=cursor, filter_ast=filter_ast, order_by=order_by)
        page = records[:limit]
        return {
            "items": [self._object_query_item(ctx, object_type_api_name, row) for row in page],
            "nextCursor": page[-1]["object_id"] if len(records) > len(page) and page else None,
        }

    def _object_query_rows(
        self,
        conn: Connection,
        ctx: RequestContext,
        object_type_api_name: str,
    ) -> list[dict[str, Any]]:
        rows = (
            conn.execute(
                select(db.object_records)
                .where(
                    and_(
                        db.object_records.c.tenant_id == ctx.tenant_id,
                        db.object_records.c.object_type_api_name == object_type_api_name,
                        db.object_records.c.deleted == False,  # noqa: E712
                    )
                )
                .order_by(db.object_records.c.object_id)
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    def _apply_object_query_options(
        self,
        records: list[dict[str, Any]],
        *,
        cursor: str | None,
        filter_ast: dict[str, Any] | None,
        order_by: list[dict[str, str]] | None,
    ) -> list[dict[str, Any]]:
        filtered = self._apply_object_cursor(records, cursor)
        filtered = self._apply_object_filter(filtered, filter_ast)
        return self._apply_object_sort(filtered, order_by)

    def _apply_object_cursor(
        self,
        records: list[dict[str, Any]],
        cursor: str | None,
    ) -> list[dict[str, Any]]:
        if cursor is None:
            return records
        return [row for row in records if row["object_id"] > cursor]

    def _apply_object_filter(
        self,
        records: list[dict[str, Any]],
        filter_ast: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        if not filter_ast:
            return records
        return [row for row in records if self._matches_filter(row["properties"], filter_ast)]

    def _apply_object_sort(
        self,
        records: list[dict[str, Any]],
        order_by: list[dict[str, str]] | None,
    ) -> list[dict[str, Any]]:
        if not order_by:
            return records
        sorted_records = list(records)
        for order in reversed(order_by):
            prop = order["property"]
            reverse = order.get("direction", "asc") == "desc"
            sorted_records.sort(key=lambda item: item["properties"].get(prop), reverse=reverse)
        return sorted_records

    def _object_query_item(
        self,
        ctx: RequestContext,
        object_type_api_name: str,
        row: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "objectType": object_type_api_name,
            "objectId": row["object_id"],
            "objectVersion": row["object_version"],
            "properties": self.policy.mask_properties(ctx, object_type_api_name, dict(row["properties"])),
        }

    def get_links(
        self,
        object_type_api_name: str,
        object_id: str,
        link_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
    ) -> list[dict[str, Any]]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "object:read")
        with self.engine.begin() as conn:
            links = (
                conn.execute(
                    select(db.object_links).where(
                        and_(
                            db.object_links.c.tenant_id == ctx.tenant_id,
                            db.object_links.c.link_type_api_name == link_type_api_name,
                            db.object_links.c.from_api_name == object_type_api_name,
                            db.object_links.c.from_object_id == object_id,
                            db.object_links.c.deleted == False,  # noqa: E712
                        )
                    )
                )
                .mappings()
                .all()
            )
            results = []
            for link in links:
                target = self._object_record(conn, ctx, link["to_api_name"], link["to_object_id"])
                if target is None:
                    continue
                results.append(
                    {
                        "linkType": link_type_api_name,
                        "from": {"objectType": object_type_api_name, "objectId": object_id},
                        "to": {
                            "objectType": link["to_api_name"],
                            "objectId": link["to_object_id"],
                            "properties": self.policy.mask_properties(
                                ctx,
                                link["to_api_name"],
                                dict(target["properties"]),
                            ),
                        },
                    }
                )
            return results

    def apply_action(
        self,
        action_api_name: str,
        *,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: dict[str, Any],
        idempotency_key: str,
        ctx: RequestContext | None = None,
        simulate_writeback_failure: bool = False,
    ) -> dict[str, Any]:
        ctx = ctx or RequestContext()
        if not idempotency_key:
            raise ValidationFailed("idempotency key is required")
        self._require_action_permission(ctx, action_api_name)
        action_run_id = _new_id("action_run")
        deferred_error: Exception | None = None
        response: dict[str, Any] | None = None

        with self.engine.begin() as conn:
            action_type = self._active_action_type(conn, ctx, action_api_name)
            existing = self._existing_action_run(conn, ctx, action_type, idempotency_key)
            if existing is not None:
                return self._action_replay_response(existing)

            self._insert_action_run(
                conn,
                ctx,
                action_type=action_type,
                action_api_name=action_api_name,
                action_run_id=action_run_id,
                object_type=object_type,
                object_id=object_id,
                expected_object_version=expected_object_version,
                params=params,
                idempotency_key=idempotency_key,
            )
            record = self._object_record(conn, ctx, object_type, object_id)
            deferred_error = self._action_request_error(action_type, record, expected_object_version, params)

            if deferred_error is not None:
                self._fail_action_run(conn, ctx, action_run_id, deferred_error)
            elif simulate_writeback_failure:
                deferred_error = self._fail_before_commit_writeback(conn, ctx, action_run_id, idempotency_key)
            else:
                if record is None:
                    raise InvariantViolation("action target record disappeared before commit")
                self._record_writeback(
                    conn,
                    ctx,
                    action_run_id,
                    status="succeeded",
                    idempotency_key=idempotency_key,
                    response={"status_code": 200},
                )
                response = self._commit_action_mutations(
                    conn,
                    ctx,
                    action_type=action_type,
                    action_run_id=action_run_id,
                    record=record,
                    params=params,
                    idempotency_key=idempotency_key,
                )
        if deferred_error is not None:
            raise deferred_error
        if response is None:
            raise InvariantViolation("action did not produce a response")
        return response

    def _require_action_permission(self, ctx: RequestContext, action_api_name: str) -> None:
        permission = f"action:execute:{action_api_name}"
        try:
            self.policy.require(ctx, permission)
        except PermissionDenied:
            with self.engine.begin() as conn:
                self._audit(
                    conn,
                    ctx,
                    event_type="permission.denied",
                    resource_type="action_type",
                    resource_id=action_api_name,
                    action="apply",
                    decision="deny",
                    after_ref={"permission": permission},
                )
            raise

    def _existing_action_run(
        self,
        conn: Connection,
        ctx: RequestContext,
        action_type: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        row = (
            conn.execute(
                select(db.action_runs).where(
                    and_(
                        db.action_runs.c.tenant_id == ctx.tenant_id,
                        db.action_runs.c.action_type_id == action_type["id"],
                        db.action_runs.c.actor_user_id == ctx.actor_user_id,
                        db.action_runs.c.idempotency_key == idempotency_key,
                    )
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    def _action_replay_response(self, existing: dict[str, Any]) -> dict[str, Any]:
        return {
            "actionRunId": existing["id"],
            "status": existing["status"],
            "idempotentReplay": True,
            "target": {
                "objectType": existing["target_object_type_api_name"],
                "objectId": existing["target_object_id"],
            },
        }

    def _insert_action_run(
        self,
        conn: Connection,
        ctx: RequestContext,
        *,
        action_type: dict[str, Any],
        action_api_name: str,
        action_run_id: str,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: dict[str, Any],
        idempotency_key: str,
    ) -> None:
        conn.execute(
            insert(db.action_runs).values(
                id=action_run_id,
                tenant_id=ctx.tenant_id,
                action_type_id=action_type["id"],
                action_type_api_name=action_api_name,
                actor_user_id=ctx.actor_user_id,
                target_object_type_id=action_type["target_object_type_id"],
                target_object_type_api_name=object_type,
                target_object_id=object_id,
                expected_object_version=expected_object_version,
                parameters=params,
                status="received",
                idempotency_key=idempotency_key,
                error=None,
                created_at=_now(),
                completed_at=None,
            )
        )

    def _action_request_error(
        self,
        action_type: dict[str, Any],
        record: dict[str, Any] | None,
        expected_object_version: int,
        params: dict[str, Any],
    ) -> Exception | None:
        if record is None:
            return NotFound("target object not found")
        if record["object_version"] != expected_object_version:
            return ConflictDetected(
                "object version conflict",
                details={
                    "currentObjectVersion": record["object_version"],
                    "expectedObjectVersion": expected_object_version,
                },
            )
        return self._validate_action_request(action_type, record, params)

    def _fail_action_run(
        self,
        conn: Connection,
        ctx: RequestContext,
        action_run_id: str,
        error: Exception,
    ) -> None:
        status = "conflict" if isinstance(error, ConflictDetected) else "failed"
        conn.execute(
            update(db.action_runs)
            .where(db.action_runs.c.id == action_run_id)
            .values(status=status, error=self._error_payload(error), completed_at=_now())
        )
        self._audit(
            conn,
            ctx,
            event_type="action.run.failed",
            resource_type="action_run",
            resource_id=action_run_id,
            action="apply",
            decision="deny" if isinstance(error, PermissionDenied) else "allow",
            after_ref=self._error_payload(error),
            correlation_id=action_run_id,
        )

    def _fail_before_commit_writeback(
        self,
        conn: Connection,
        ctx: RequestContext,
        action_run_id: str,
        idempotency_key: str,
    ) -> ExternalSystemError:
        error = ExternalSystemError(
            "mock before-commit writeback failed",
            details={"connector": MOCK_WRITEBACK_CONNECTOR, "simulated": True},
        )
        self._record_writeback(
            conn,
            ctx,
            action_run_id,
            status="failed",
            idempotency_key=idempotency_key,
            response={"status_code": 500},
        )
        conn.execute(
            update(db.action_runs)
            .where(db.action_runs.c.id == action_run_id)
            .values(status="failed", error=self._error_payload(error), completed_at=_now())
        )
        return error

    def materialize(
        self,
        api_name: str,
        *,
        ctx: RequestContext | None = None,
    ) -> CommitResult:
        ctx = ctx or RequestContext()
        self._require_or_audit(ctx, "materialization:run", "materialization", api_name)
        materialization = self._ensure_materialization(api_name, ctx=ctx)
        target_dataset = self.get_dataset(materialization["target_ref"]["dataset"], ctx=ctx)
        with self.engine.begin() as conn:
            tx_id = self._open_dataset_transaction(conn, ctx, target_dataset, "SNAPSHOT")
            run_id = _new_id("mat_run")
            watermark = self._materialization_watermark(conn, materialization)
            conn.execute(
                insert(db.materialization_runs).values(
                    id=run_id,
                    tenant_id=ctx.tenant_id,
                    materialization_id=materialization["id"],
                    api_name=api_name,
                    status="running",
                    source_cursor=watermark,
                    object_store_watermark=watermark,
                    consistency_level="watermark",
                    target_dataset_version_id=None,
                    row_count=None,
                    error=None,
                    created_at=_now(),
                    completed_at=None,
                )
            )
        rows, fieldnames = self._materialization_rows(materialization, ctx=ctx)
        staged = self._staging_file(target_dataset, tx_id, "part-00000.parquet")
        self._rows_to_parquet(rows, staged, fieldnames)
        try:
            with self.engine.begin() as conn:
                result = self._finalize_open_transaction(
                    conn,
                    ctx,
                    dataset=target_dataset,
                    transaction_id=tx_id,
                    staged_parquet=staged,
                    run_id=run_id,
                    audit_action="materialization_commit",
                    outbox_event_type="materialization.completed",
                )
                conn.execute(
                    update(db.materialization_runs)
                    .where(db.materialization_runs.c.id == run_id)
                    .values(
                        status="succeeded",
                        target_dataset_version_id=result.version_id,
                        row_count=result.row_count,
                        completed_at=_now(),
                    )
                )
                self._lineage(
                    conn,
                    ctx,
                    "materialization",
                    materialization["id"],
                    "dataset_version",
                    result.version_id,
                    "materializes_to",
                    run_id,
                )
                return result
        except Exception as exc:
            self._abort_transaction_after_error(ctx, tx_id, run_id, exc, db.materialization_runs)
            raise

    def lineage_for_resource(
        self,
        resource_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> list[dict[str, Any]]:
        ctx = ctx or RequestContext()
        with self.engine.begin() as conn:
            rows = (
                conn.execute(
                    select(db.lineage_edges).where(
                        and_(
                            db.lineage_edges.c.tenant_id == ctx.tenant_id,
                            (
                                (db.lineage_edges.c.from_resource_id == resource_id)
                                | (db.lineage_edges.c.to_resource_id == resource_id)
                            ),
                        )
                    )
                )
                .mappings()
                .all()
            )
            return [dict(row) for row in rows]

    def list_runs(self, *, ctx: RequestContext | None = None) -> dict[str, list[dict[str, Any]]]:
        ctx = ctx or RequestContext()
        with self.engine.begin() as conn:
            return {
                "syncRuns": self._rows_for_tenant(conn, db.sync_runs, ctx),
                "transformRuns": self._rows_for_tenant(conn, db.transform_runs, ctx),
                "indexRuns": self._rows_for_tenant(conn, db.index_runs, ctx),
                "actionRuns": self._rows_for_tenant(conn, db.action_runs, ctx),
                "actionWritebacks": self._rows_for_tenant(conn, db.action_writebacks, ctx),
                "materializationRuns": self._rows_for_tenant(conn, db.materialization_runs, ctx),
                "outboxEvents": self._rows_for_tenant(conn, db.outbox_events, ctx),
                "auditEvents": self._rows_for_tenant(conn, db.audit_events, ctx),
            }

    def run_supply_chain_demo(self, *, ctx: RequestContext | None = None) -> dict[str, Any]:
        ctx = ctx or demo_admin_context()
        self.seed_supply_chain_demo_files()
        self.ensure_dataset("raw.erp_orders", ctx=ctx, primary_key=["order_id"])
        self.ensure_dataset("raw.crm_customers", ctx=ctx, primary_key=["customer_id"])
        self.ensure_dataset("clean.orders", ctx=ctx, primary_key=["order_id"])
        self.ensure_dataset("clean.customers", ctx=ctx, primary_key=["customer_id"])
        self.ensure_dataset("ops.action_log", ctx=ctx, primary_key=["action_run_id"])
        self.ensure_dataset("ops.order_current", ctx=ctx, primary_key=["orderId"])
        self._register_demo_transforms(ctx)

        orders_raw = self.upload_csv(
            "raw.erp_orders",
            SUPPLY_CHAIN_DEMO_ROOT / "data" / "orders.csv",
            ctx=ctx,
            sync_name="sync_orders_pg",
        )
        customers_raw = self.upload_csv(
            "raw.crm_customers",
            SUPPLY_CHAIN_DEMO_ROOT / "data" / "customers.csv",
            ctx=ctx,
            sync_name="upload_customers_csv",
        )
        clean_orders = self.run_transform("clean_orders", ctx=ctx)
        clean_customers = self.run_transform("clean_customers", ctx=ctx)
        ontology = self.apply_ontology(SUPPLY_CHAIN_DEMO_ROOT / "ontology" / "order-customer.yaml", ctx=ctx)
        order_index = self.index_rebuild("Order", ctx=ctx)
        customer_index = self.index_rebuild("Customer", ctx=ctx)
        order_before = self.get_object("Order", "O-1001", ctx=ctx)
        action = self.apply_action(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order_before["objectVersion"],
            params={"reason": "Inventory confirmed"},
            idempotency_key="approve-O-1001-demo",
            ctx=ctx,
        )
        action_log = self.materialize("action_log", ctx=ctx)
        order_current = self.materialize("order_current", ctx=ctx)
        customer_risk = self.run_transform("customer_risk", ctx=ctx)
        customer_reindex = self.index_rebuild("Customer", ctx=ctx)
        customer = self.get_object("Customer", "C-100", ctx=ctx, explain=True)
        return {
            "rawOrdersVersion": orders_raw.version_id,
            "rawCustomersVersion": customers_raw.version_id,
            "cleanOrdersVersion": clean_orders.version_id,
            "cleanCustomersVersion": clean_customers.version_id,
            "ontology": ontology,
            "orderIndex": order_index,
            "customerIndex": customer_index,
            "action": action,
            "actionLogVersion": action_log.version_id,
            "orderCurrentVersion": order_current.version_id,
            "customerRiskVersion": customer_risk.version_id,
            "customerReindex": customer_reindex,
            "customer": customer,
        }

    def register_supply_chain_demo_transforms(self, ctx: RequestContext | None = None) -> None:
        self._register_demo_transforms(ctx or demo_admin_context())

    def seed_supply_chain_demo_files(self) -> None:
        ensure_supply_chain_demo_files()

    def _register_demo_transforms(self, ctx: RequestContext) -> None:
        register_supply_chain_demo_transforms(self.register_transform, ctx)

    def _require_or_audit(
        self,
        ctx: RequestContext,
        permission: str,
        resource_type: str,
        resource_id: str,
    ) -> None:
        try:
            self.policy.require(ctx, permission)
        except PermissionDenied:
            with self.engine.begin() as conn:
                self._audit(
                    conn,
                    ctx,
                    event_type="permission.denied",
                    resource_type=resource_type,
                    resource_id=resource_id,
                    action=permission,
                    decision="deny",
                )
            raise

    def _select_by_id(self, conn: Connection, table: Any, row_id: str) -> dict[str, Any] | None:
        row = conn.execute(select(table).where(table.c.id == row_id)).mappings().first()
        return dict(row) if row else None

    def _rows_for_tenant(self, conn: Connection, table: Any, ctx: RequestContext) -> list[dict[str, Any]]:
        return [
            dict(row) for row in conn.execute(select(table).where(table.c.tenant_id == ctx.tenant_id)).mappings().all()
        ]

    def _audit(
        self,
        conn: Connection,
        ctx: RequestContext,
        *,
        event_type: str,
        resource_type: str,
        resource_id: str | None,
        action: str,
        decision: str = "allow",
        policy_decision: dict[str, Any] | None = None,
        before_ref: dict[str, Any] | None = None,
        after_ref: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        conn.execute(
            insert(db.audit_events).values(
                id=_new_id("audit"),
                tenant_id=ctx.tenant_id,
                actor_user_id=ctx.actor_user_id,
                event_type=event_type,
                resource_type=resource_type,
                resource_id=resource_id,
                action=action,
                decision=decision,
                policy_decision=policy_decision or {},
                before_ref=before_ref or {},
                after_ref=after_ref or {},
                correlation_id=correlation_id or ctx.request_id,
                request_id=ctx.request_id,
                metadata={},
                created_at=_now(),
            )
        )

    def _outbox(
        self,
        conn: Connection,
        ctx: RequestContext,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
        correlation_id: str,
    ) -> None:
        try:
            conn.execute(
                insert(db.outbox_events).values(
                    id=_new_id("outbox"),
                    tenant_id=ctx.tenant_id,
                    event_type=event_type,
                    aggregate_type=aggregate_type,
                    aggregate_id=aggregate_id,
                    payload=payload,
                    status="pending",
                    attempts=0,
                    idempotency_key=idempotency_key,
                    correlation_id=correlation_id,
                    created_at=_now(),
                    published_at=None,
                )
            )
        except IntegrityError:
            return

    def _lineage(
        self,
        conn: Connection,
        ctx: RequestContext,
        from_type: str,
        from_id: str,
        to_type: str,
        to_id: str,
        relation: str,
        run_id: str,
    ) -> None:
        conn.execute(
            insert(db.lineage_edges).values(
                id=_new_id("lineage"),
                tenant_id=ctx.tenant_id,
                from_resource_type=from_type,
                from_resource_id=from_id,
                to_resource_type=to_type,
                to_resource_id=to_id,
                relation=relation,
                created_by_run_id=run_id,
                created_at=_now(),
            )
        )

    def _open_dataset_transaction(
        self,
        conn: Connection,
        ctx: RequestContext,
        dataset: dict[str, Any],
        tx_type: str,
        *,
        branch: str = "main",
    ) -> str:
        if tx_type not in {"SNAPSHOT", "APPEND"}:
            raise ValidationFailed("v1 supports only SNAPSHOT and APPEND transactions")
        tx_id = _new_id("dstx")
        latest = self._latest_version_by_dataset_id(conn, dataset["id"], allow_missing=True)
        conn.execute(
            insert(db.dataset_transactions).values(
                id=tx_id,
                tenant_id=ctx.tenant_id,
                dataset_id=dataset["id"],
                branch=branch,
                tx_type=tx_type,
                status="OPEN",
                base_version_id=latest["id"] if latest else None,
                committed_version_id=None,
                schema_version=None,
                created_by=ctx.actor_user_id,
                created_at=_now(),
                committed_at=None,
                metadata={},
            )
        )
        return tx_id

    def _staging_file(self, dataset: dict[str, Any], transaction_id: str, file_name: str) -> Path:
        path = (
            self.storage_root
            / dataset["tenant_id"]
            / "datasets"
            / dataset["id"]
            / "_staging"
            / transaction_id
            / file_name
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _version_file_path(self, version: dict[str, Any]) -> Path:
        manifest = self._load_manifest(version["manifest_uri"])
        return Path(manifest["files"][0]["uri"])

    def _load_manifest(self, manifest_uri: str) -> dict[str, Any]:
        return json.loads(Path(manifest_uri).read_text(encoding="utf-8"))

    def _csv_to_parquet(self, source_path: Path, target_path: Path) -> None:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect()
        try:
            con.read_csv(str(source_path), header=True).write_parquet(str(target_path))
        except Exception as exc:
            raise ValidationFailed("invalid csv input", details={"path": str(source_path)}) from exc
        finally:
            con.close()

    def _rows_to_parquet(self, rows: list[dict[str, Any]], target_path: Path, fieldnames: list[str]) -> None:
        csv_path = target_path.with_suffix(".csv")
        _write_rows_to_csv(rows, csv_path, fieldnames)
        self._csv_to_parquet(csv_path, target_path)

    def _finalize_open_transaction(
        self,
        conn: Connection,
        ctx: RequestContext,
        *,
        dataset: dict[str, Any],
        transaction_id: str,
        staged_parquet: Path,
        run_id: str,
        audit_action: str,
        outbox_event_type: str,
        extra_checks: list[dict[str, Any]] | None = None,
    ) -> CommitResult:
        tx = self._require_open_transaction(conn, transaction_id)
        stats = self._inspect_parquet(staged_parquet, dataset["primary_key"])
        failures = self._run_dataset_checks(
            conn,
            ctx,
            dataset,
            stats.parquet_path,
            stats.row_count,
            run_id,
            transaction_id,
            extra_checks=extra_checks or [],
        )
        compatibility_error = self._schema_compatibility_error(conn, dataset, stats.schema_json)
        if compatibility_error:
            failures.append(compatibility_error)
        if failures:
            conn.execute(
                update(db.dataset_transactions)
                .where(db.dataset_transactions.c.id == transaction_id)
                .values(status="ABORTED", metadata={"validationFailures": failures})
            )
            self._audit(
                conn,
                ctx,
                event_type="dataset.transaction.aborted",
                resource_type="dataset_transaction",
                resource_id=transaction_id,
                action="abort",
                after_ref={"failures": failures},
            )
            raise ValidationFailed("dataset checks failed", details={"failures": failures})

        schema_version = self._ensure_schema(conn, dataset, stats.schema_json, stats.schema_hash)
        version_number = self._next_dataset_version_number(conn, dataset["id"])
        version_id = _new_id("dsv")
        version_dir = (
            self.storage_root / ctx.tenant_id / "datasets" / dataset["id"] / "branch=main" / f"version={version_id}"
        )
        version_dir.mkdir(parents=True, exist_ok=True)
        final_parquet = version_dir / "part-00000.parquet"
        shutil.copy2(staged_parquet, final_parquet)
        manifest_path = version_dir / "manifest.json"
        manifest = {
            "version_id": version_id,
            "dataset": _dataset_ref(dataset),
            "branch": tx["branch"],
            "schema_hash": stats.schema_hash,
            "files": [
                {
                    "uri": str(final_parquet),
                    "format": "parquet",
                    "row_count": stats.row_count,
                    "byte_size": final_parquet.stat().st_size,
                    "content_hash": _file_hash(final_parquet),
                }
            ],
            "created_at": _now(),
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        byte_size = final_parquet.stat().st_size
        content_hash = _file_hash(final_parquet)
        conn.execute(
            insert(db.dataset_versions).values(
                id=version_id,
                tenant_id=ctx.tenant_id,
                dataset_id=dataset["id"],
                branch=tx["branch"],
                version_number=version_number,
                transaction_id=transaction_id,
                schema_version=schema_version,
                manifest_uri=str(manifest_path),
                row_count=stats.row_count,
                byte_size=byte_size,
                status="active",
                superseded_by_version_id=None,
                created_at=_now(),
            )
        )
        conn.execute(
            insert(db.dataset_files).values(
                id=_new_id("dsf"),
                tenant_id=ctx.tenant_id,
                dataset_version_id=version_id,
                uri=str(final_parquet),
                format="parquet",
                row_count=stats.row_count,
                byte_size=byte_size,
                content_hash=content_hash,
                partition_values={},
            )
        )
        conn.execute(
            update(db.dataset_transactions)
            .where(db.dataset_transactions.c.id == transaction_id)
            .values(
                status="COMMITTED",
                committed_version_id=version_id,
                schema_version=schema_version,
                committed_at=_now(),
            )
        )
        self._outbox(
            conn,
            ctx,
            outbox_event_type,
            "dataset_version",
            version_id,
            {
                "datasetId": dataset["id"],
                "datasetRef": _dataset_ref(dataset),
                "versionId": version_id,
                "branch": tx["branch"],
            },
            idempotency_key=version_id,
            correlation_id=run_id,
        )
        self._audit(
            conn,
            ctx,
            event_type="dataset.version.committed",
            resource_type="dataset_version",
            resource_id=version_id,
            action=audit_action,
            after_ref={"dataset_ref": _dataset_ref(dataset), "version_number": version_number},
            correlation_id=run_id,
        )
        return CommitResult(
            dataset_id=dataset["id"],
            dataset_ref=_dataset_ref(dataset),
            transaction_id=transaction_id,
            version_id=version_id,
            version_number=version_number,
            row_count=stats.row_count,
            manifest_uri=str(manifest_path),
            schema_hash=stats.schema_hash,
        )

    def _require_open_transaction(self, conn: Connection, transaction_id: str) -> dict[str, Any]:
        tx = self._select_by_id(conn, db.dataset_transactions, transaction_id)
        if tx is None:
            raise NotFound("dataset transaction not found", details={"transaction_id": transaction_id})
        if tx["status"] != "OPEN":
            raise ConflictDetected(
                "dataset transaction is not open",
                details={"transaction_id": transaction_id, "status": tx["status"]},
            )
        return tx

    def _inspect_parquet(self, parquet_path: Path, primary_key: list[str]) -> StagedFileStats:
        con = duckdb.connect()
        try:
            row_count = int(
                _required_row(
                    con.execute("select count(*) from read_parquet(?)", [str(parquet_path)]).fetchone(),
                    "parquet row count",
                )[0]
            )
            describe = con.execute("describe select * from read_parquet(?)", [str(parquet_path)]).fetchall()
        finally:
            con.close()
        columns = [
            {
                "name": row[0],
                "type": _normalize_duckdb_type(row[1]),
                "nullable": row[0] not in set(primary_key),
            }
            for row in describe
        ]
        schema_json = {"columns": columns, "primary_key": primary_key, "cdc": {"enabled": False}}
        return StagedFileStats(
            parquet_path=parquet_path,
            row_count=int(row_count),
            byte_size=parquet_path.stat().st_size,
            content_hash=_file_hash(parquet_path),
            schema_json=schema_json,
            schema_hash=_json_hash(schema_json),
        )

    def _ensure_schema(
        self,
        conn: Connection,
        dataset: dict[str, Any],
        schema_json: dict[str, Any],
        schema_hash: str,
    ) -> int:
        existing = (
            conn.execute(
                select(db.dataset_schemas).where(
                    and_(
                        db.dataset_schemas.c.dataset_id == dataset["id"],
                        db.dataset_schemas.c.schema_hash == schema_hash,
                    )
                )
            )
            .mappings()
            .first()
        )
        if existing:
            return int(existing["version"])
        latest = (
            conn.execute(
                select(func.max(db.dataset_schemas.c.version)).where(db.dataset_schemas.c.dataset_id == dataset["id"])
            ).scalar()
            or 0
        )
        version = int(latest) + 1
        conn.execute(
            insert(db.dataset_schemas).values(
                id=_new_id("schema"),
                dataset_id=dataset["id"],
                version=version,
                schema_json=schema_json,
                schema_hash=schema_hash,
                created_at=_now(),
            )
        )
        return version

    def _schema_compatibility_error(
        self,
        conn: Connection,
        dataset: dict[str, Any],
        next_schema: dict[str, Any],
    ) -> dict[str, Any] | None:
        latest_version = self._latest_version_by_dataset_id(conn, dataset["id"], allow_missing=True)
        if latest_version is None:
            return None
        current_schema = self._schema_for_version(dataset["id"], latest_version["schema_version"])["schema_json"]
        current_columns = self._schema_columns_by_name(current_schema)
        next_columns = self._schema_columns_by_name(next_schema)
        return (
            self._missing_columns_error(current_columns, next_columns)
            or self._type_change_error(current_columns, next_columns)
            or self._missing_primary_key_error(dataset, next_columns)
        )

    def _schema_columns_by_name(self, schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {column["name"]: column for column in schema["columns"]}

    def _missing_columns_error(
        self,
        current_columns: dict[str, dict[str, Any]],
        next_columns: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        missing = sorted(set(current_columns) - set(next_columns))
        if not missing:
            return None
        return {"check": "schema_compatibility", "status": "failed", "missing_columns": missing}

    def _type_change_error(
        self,
        current_columns: dict[str, dict[str, Any]],
        next_columns: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        for name, column in current_columns.items():
            if name in next_columns and column["type"] != next_columns[name]["type"]:
                return {
                    "check": "schema_compatibility",
                    "status": "failed",
                    "column": name,
                    "from": column["type"],
                    "to": next_columns[name]["type"],
                }
        return None

    def _missing_primary_key_error(
        self,
        dataset: dict[str, Any],
        next_columns: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        for pk in dataset["primary_key"]:
            if pk not in next_columns:
                return {"check": "schema_compatibility", "status": "failed", "missing_primary_key": pk}
        return None

    def _run_dataset_checks(
        self,
        conn: Connection,
        ctx: RequestContext,
        dataset: dict[str, Any],
        parquet_path: Path,
        row_count: int,
        run_id: str,
        transaction_id: str,
        *,
        extra_checks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        checks = [{"type": "row_count_min", "min": 1}]
        for pk in dataset["primary_key"]:
            checks.append({"type": "not_null", "columns": [pk]})
            checks.append({"type": "unique", "column": pk})
        checks.extend(extra_checks)
        failures: list[dict[str, Any]] = []
        for check in checks:
            check_id = self._ensure_dataset_check(conn, ctx, dataset, check)
            result = self._execute_check(parquet_path, row_count, check)
            conn.execute(
                insert(db.dataset_check_results).values(
                    id=_new_id("check_result"),
                    tenant_id=ctx.tenant_id,
                    check_id=check_id,
                    run_id=run_id,
                    transaction_id=transaction_id,
                    status=result["status"],
                    details=result,
                    created_at=_now(),
                )
            )
            if result["status"] == "failed":
                failures.append(result)
        return failures

    def _ensure_dataset_check(
        self,
        conn: Connection,
        ctx: RequestContext,
        dataset: dict[str, Any],
        check: dict[str, Any],
    ) -> str:
        name = json.dumps(check, sort_keys=True)
        row = (
            conn.execute(
                select(db.dataset_checks).where(
                    and_(
                        db.dataset_checks.c.tenant_id == ctx.tenant_id,
                        db.dataset_checks.c.dataset_id == dataset["id"],
                        db.dataset_checks.c.name == name,
                    )
                )
            )
            .mappings()
            .first()
        )
        if row:
            return row["id"]
        check_id = _new_id("check")
        conn.execute(
            insert(db.dataset_checks).values(
                id=check_id,
                tenant_id=ctx.tenant_id,
                dataset_id=dataset["id"],
                name=name,
                check_type=check["type"],
                config=check,
                severity="error",
                enabled=True,
            )
        )
        return check_id

    def _execute_check(
        self,
        parquet_path: Path,
        row_count: int,
        check: dict[str, Any],
    ) -> dict[str, Any]:
        check_type = check["type"]
        if check_type == "row_count_min":
            return self._row_count_min_check(row_count, check)
        con = duckdb.connect()
        try:
            if check_type == "not_null":
                return self._not_null_check(con, parquet_path, check)
            if check_type == "unique":
                return self._unique_check(con, parquet_path, check)
        finally:
            con.close()
        return {"check": check_type, "status": "passed", "note": "unsupported check treated as noop"}

    def _row_count_min_check(self, row_count: int, check: dict[str, Any]) -> dict[str, Any]:
        status = "passed" if row_count >= int(check["min"]) else "failed"
        return {"check": check["type"], "status": status, "row_count": row_count, "min": check["min"]}

    def _not_null_check(
        self,
        con: duckdb.DuckDBPyConnection,
        parquet_path: Path,
        check: dict[str, Any],
    ) -> dict[str, Any]:
        failures: dict[str, int] = {}
        for column in check["columns"]:
            count = self._null_count(con, parquet_path, column)
            if count:
                failures[column] = count
        return {
            "check": check["type"],
            "status": "failed" if failures else "passed",
            "failures": failures,
        }

    def _null_count(self, con: duckdb.DuckDBPyConnection, parquet_path: Path, column: str) -> int:
        column_identifier = _sql_identifier(column)
        # column_identifier is validated and parquet path is a bound parameter.
        null_check_sql = f"select count(*) from read_parquet(?) where {column_identifier} is null"  # nosec B608
        return int(
            _required_row(
                con.execute(null_check_sql, [str(parquet_path)]).fetchone(),  # nosec B608
                "not null health check",
            )[0]
        )

    def _unique_check(
        self,
        con: duckdb.DuckDBPyConnection,
        parquet_path: Path,
        check: dict[str, Any],
    ) -> dict[str, Any]:
        column = check["column"]
        duplicate_count = self._duplicate_group_count(con, parquet_path, column)
        return {
            "check": check["type"],
            "status": "failed" if duplicate_count else "passed",
            "column": column,
            "duplicate_groups": duplicate_count,
        }

    def _duplicate_group_count(self, con: duckdb.DuckDBPyConnection, parquet_path: Path, column: str) -> int:
        column_identifier = _sql_identifier(column)
        duplicate_check_sql = (
            "select count(*) from (select "  # nosec B608
            f"{column_identifier}, count(*) c from read_parquet(?) "
            f"group by {column_identifier} having c > 1)"
        )
        return int(
            _required_row(
                con.execute(duplicate_check_sql, [str(parquet_path)]).fetchone(),  # nosec B608
                "unique health check",
            )[0]
        )

    def _next_dataset_version_number(self, conn: Connection, dataset_id: str) -> int:
        latest = (
            conn.execute(
                select(func.max(db.dataset_versions.c.version_number)).where(
                    db.dataset_versions.c.dataset_id == dataset_id
                )
            ).scalar()
            or 0
        )
        return int(latest) + 1

    def _schema_for_version(self, dataset_id: str, schema_version: int) -> dict[str, Any]:
        with self.engine.begin() as conn:
            row = (
                conn.execute(
                    select(db.dataset_schemas).where(
                        and_(
                            db.dataset_schemas.c.dataset_id == dataset_id,
                            db.dataset_schemas.c.version == schema_version,
                        )
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise NotFound("dataset schema not found")
            return dict(row)

    def _get_version(
        self,
        dataset_id: str,
        version: str,
        *,
        ctx: RequestContext,
    ) -> dict[str, Any]:
        with self.engine.begin() as conn:
            if version == "latest":
                return self._latest_version_by_dataset_id(conn, dataset_id)
            row = self._select_by_id(conn, db.dataset_versions, version)
            if row is None:
                raise NotFound("dataset version not found", details={"version": version})
            if row["tenant_id"] != ctx.tenant_id:
                raise NotFound("dataset version not found", details={"version": version})
            return row

    @overload
    def _latest_version_by_dataset_id(
        self,
        conn: Connection,
        dataset_id: str,
        *,
        allow_missing: Literal[False] = False,
    ) -> dict[str, Any]: ...

    @overload
    def _latest_version_by_dataset_id(
        self,
        conn: Connection,
        dataset_id: str,
        *,
        allow_missing: Literal[True],
    ) -> dict[str, Any] | None: ...

    def _latest_version_by_dataset_id(
        self,
        conn: Connection,
        dataset_id: str,
        *,
        allow_missing: bool = False,
    ) -> dict[str, Any] | None:
        row = (
            conn.execute(
                select(db.dataset_versions)
                .where(db.dataset_versions.c.dataset_id == dataset_id)
                .order_by(desc(db.dataset_versions.c.version_number))
                .limit(1)
            )
            .mappings()
            .first()
        )
        if row is None:
            if allow_missing:
                return None
            raise NotFound("dataset has no committed version", details={"dataset_id": dataset_id})
        return dict(row)

    def _abort_transaction_after_error(
        self,
        ctx: RequestContext,
        transaction_id: str,
        run_id: str,
        exc: Exception,
        run_table: Any,
    ) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                update(db.dataset_transactions)
                .where(
                    and_(
                        db.dataset_transactions.c.id == transaction_id,
                        db.dataset_transactions.c.status == "OPEN",
                    )
                )
                .values(status="ABORTED", metadata={"error": self._error_payload(exc)})
            )
            conn.execute(
                update(run_table)
                .where(run_table.c.id == run_id)
                .values(status="FAILED", error=self._error_payload(exc), completed_at=_now())
            )

    def _get_transform(self, conn: Connection, ctx: RequestContext, api_name: str) -> dict[str, Any]:
        row = (
            conn.execute(
                select(db.transforms).where(
                    and_(
                        db.transforms.c.tenant_id == ctx.tenant_id,
                        db.transforms.c.api_name == api_name,
                    )
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise NotFound("transform not found", details={"api_name": api_name})
        return dict(row)

    def _resolve_transform_inputs(
        self,
        conn: Connection,
        ctx: RequestContext,
        transform: dict[str, Any],
    ) -> dict[str, str]:
        resolved: dict[str, str] = {}
        template = Path(transform["entrypoint"]).read_text(encoding="utf-8")
        referenced = set(INPUT_PATTERN.findall(template))
        configured = set(transform["inputs"].values())
        for dataset_ref in sorted(referenced | configured):
            dataset = self.get_dataset(dataset_ref, ctx=ctx)
            version = self._latest_version_by_dataset_id(conn, dataset["id"])
            resolved[dataset_ref] = version["id"]
        return resolved

    def _execute_sql_transform(
        self,
        transform: dict[str, Any],
        input_versions: dict[str, str],
        staged: Path,
    ) -> None:
        sql = Path(transform["entrypoint"]).read_text(encoding="utf-8")
        con = duckdb.connect()
        try:
            for index, (dataset_ref, version_id) in enumerate(input_versions.items()):
                version = self._get_version_by_id(version_id)
                view = f"input_{index}"
                _sql_identifier(view)
                con.read_parquet(str(self._version_file_path(version))).create_view(view, replace=True)
                sql = sql.replace(f"{{{{ input('{dataset_ref}') }}}}", view)
            unresolved = INPUT_PATTERN.findall(sql)
            if unresolved:
                raise ValidationFailed("transform has unresolved inputs", details={"inputs": unresolved})
            staged.parent.mkdir(parents=True, exist_ok=True)
            con.execute(f"copy ({sql}) to {_sql_literal(staged)} (format parquet)")
        finally:
            con.close()

    def _get_version_by_id(self, version_id: str) -> dict[str, Any]:
        with self.engine.begin() as conn:
            version = self._select_by_id(conn, db.dataset_versions, version_id)
            if version is None:
                raise NotFound("dataset version not found", details={"version_id": version_id})
            return version

    def _next_ontology_version(self, conn: Connection, ctx: RequestContext) -> int:
        current = (
            conn.execute(
                select(func.max(db.ontology_versions.c.version_number)).where(
                    db.ontology_versions.c.tenant_id == ctx.tenant_id
                )
            ).scalar()
            or 0
        )
        return int(current) + 1

    def _import_object_types(
        self,
        conn: Connection,
        ctx: RequestContext,
        ontology_version_id: str,
        definition: dict[str, Any],
    ) -> dict[str, str]:
        object_map: dict[str, str] = {}
        for item in definition.get("objectTypes", []):
            object_id = _new_id("otype")
            api_name = item["apiName"]
            conn.execute(
                insert(db.object_types).values(
                    id=object_id,
                    tenant_id=ctx.tenant_id,
                    ontology_version_id=ontology_version_id,
                    api_name=api_name,
                    display_name=item.get("displayName", api_name),
                    description=item.get("description"),
                    primary_key_property=item["primaryKey"],
                    backing=item["backing"],
                    config={},
                )
            )
            object_map[api_name] = object_id
            seen_properties: set[str] = set()
            for prop in item.get("properties", []):
                prop_api = prop["apiName"]
                if prop_api in seen_properties:
                    raise ValidationFailed("duplicate property apiName", details={"property": prop_api})
                seen_properties.add(prop_api)
                source = prop.get("source", "dataset" if "column" in prop else "edit_layer")
                conn.execute(
                    insert(db.property_types).values(
                        id=_new_id("ptype"),
                        tenant_id=ctx.tenant_id,
                        object_type_id=object_id,
                        api_name=prop_api,
                        display_name=prop.get("displayName", prop_api),
                        data_type=prop["type"],
                        nullable=prop.get("nullable", True),
                        indexed=prop.get("indexed", False),
                        searchable=prop.get("searchable", False),
                        editable=prop.get("editable", False),
                        classification=prop.get("classification"),
                        source=source,
                        column_name=prop.get("column"),
                        edit_policy=prop.get("editPolicy", "edit_only" if source == "edit_layer" else "source_wins"),
                        derivation=prop.get("derivation"),
                    )
                )
        return object_map

    def _import_link_types(
        self,
        conn: Connection,
        ctx: RequestContext,
        ontology_version_id: str,
        definition: dict[str, Any],
        object_map: dict[str, str],
    ) -> None:
        for item in definition.get("linkTypes", []):
            if item["from"] not in object_map or item["to"] not in object_map:
                raise ValidationFailed("link references unknown object type", details=item)
            conn.execute(
                insert(db.link_types).values(
                    id=_new_id("ltype"),
                    tenant_id=ctx.tenant_id,
                    ontology_version_id=ontology_version_id,
                    api_name=item["apiName"],
                    display_name=item.get("displayName", item["apiName"]),
                    from_object_type_id=object_map[item["from"]],
                    from_api_name=item["from"],
                    to_object_type_id=object_map[item["to"]],
                    to_api_name=item["to"],
                    cardinality=item.get("cardinality", "many_to_one"),
                    backing=item["backing"],
                )
            )

    def _import_action_types(
        self,
        conn: Connection,
        ctx: RequestContext,
        ontology_version_id: str,
        definition: dict[str, Any],
        object_map: dict[str, str],
    ) -> None:
        for item in definition.get("actionTypes", []):
            target = item["target"]
            if target not in object_map:
                raise ValidationFailed("action target object type not found", details=item)
            parameter_schema = {
                "type": "object",
                "required": [
                    parameter["apiName"] for parameter in item.get("parameters", []) if parameter.get("required", False)
                ],
                "properties": {
                    parameter["apiName"]: {"type": parameter["type"]} for parameter in item.get("parameters", [])
                },
            }
            conn.execute(
                insert(db.action_types).values(
                    id=_new_id("atype"),
                    tenant_id=ctx.tenant_id,
                    ontology_version_id=ontology_version_id,
                    api_name=item["apiName"],
                    display_name=item.get("displayName", item["apiName"]),
                    target_object_type_id=object_map[target],
                    target_api_name=target,
                    parameter_schema=parameter_schema,
                    definition=item,
                    enabled=True,
                )
            )

    def _validate_ontology(self, conn: Connection, ctx: RequestContext, ontology_version_id: str) -> None:
        object_rows = self._object_types_for_version(conn, ctx, ontology_version_id)
        object_by_api = {row["api_name"]: row for row in object_rows}
        for object_type in object_rows:
            self._validate_ontology_object_type(conn, ctx, object_type)
        for link in self._link_types_for_version(conn, ctx, ontology_version_id):
            self._validate_ontology_link(conn, ctx, link, object_by_api)

    def _validate_ontology_object_type(
        self,
        conn: Connection,
        ctx: RequestContext,
        object_type: dict[str, Any],
    ) -> None:
        columns = self._dataset_columns_for_ref(conn, ctx, object_type["backing"]["dataset"])
        properties = self._properties_for_object_type(conn, object_type["id"])
        property_by_api = {prop["api_name"]: prop for prop in properties}
        self._validate_primary_key_property(object_type, property_by_api, columns)
        self._validate_dataset_backed_properties(object_type, properties, columns)
        self._validate_ontology_action_mutations(conn, object_type, property_by_api)

    def _dataset_columns_for_ref(
        self,
        conn: Connection,
        ctx: RequestContext,
        dataset_ref: str,
    ) -> dict[str, dict[str, Any]]:
        dataset = self.get_dataset(dataset_ref, ctx=ctx)
        latest_version = self._latest_version_by_dataset_id(conn, dataset["id"])
        schema = self._schema_for_version(dataset["id"], latest_version["schema_version"])["schema_json"]
        return {column["name"]: column for column in schema["columns"]}

    def _validate_primary_key_property(
        self,
        object_type: dict[str, Any],
        property_by_api: dict[str, dict[str, Any]],
        columns: dict[str, dict[str, Any]],
    ) -> None:
        pk_prop = object_type["primary_key_property"]
        if pk_prop not in property_by_api:
            raise ValidationFailed("primary key property missing", details={"objectType": object_type["api_name"]})
        pk_column = property_by_api[pk_prop]["column_name"]
        if pk_column not in columns:
            raise ValidationFailed("primary key column missing", details={"column": pk_column})
        if columns[pk_column]["nullable"]:
            raise ValidationFailed("primary key column must be non-null", details={"column": pk_column})

    def _validate_dataset_backed_properties(
        self,
        object_type: dict[str, Any],
        properties: list[dict[str, Any]],
        columns: dict[str, dict[str, Any]],
    ) -> None:
        for prop in properties:
            if prop["source"] == "dataset" and prop["column_name"] not in columns:
                raise ValidationFailed(
                    "property column missing",
                    details={"objectType": object_type["api_name"], "property": prop["api_name"]},
                )

    def _validate_ontology_action_mutations(
        self,
        conn: Connection,
        object_type: dict[str, Any],
        property_by_api: dict[str, dict[str, Any]],
    ) -> None:
        for action in self._actions_for_target(conn, object_type["id"]):
            for mutation in action["definition"].get("mutations", []):
                self._validate_action_mutation_property(mutation, property_by_api)

    def _validate_action_mutation_property(
        self,
        mutation: dict[str, Any],
        property_by_api: dict[str, dict[str, Any]],
    ) -> None:
        prop = mutation.get("property")
        if prop not in property_by_api:
            raise ValidationFailed("action mutation property missing", details=mutation)
        if not property_by_api[prop]["editable"]:
            raise ValidationFailed("action mutation property must be editable", details=mutation)

    def _validate_ontology_link(
        self,
        conn: Connection,
        ctx: RequestContext,
        link: dict[str, Any],
        object_by_api: dict[str, dict[str, Any]],
    ) -> None:
        if link["from_api_name"] not in object_by_api or link["to_api_name"] not in object_by_api:
            raise ValidationFailed("link object type missing", details={"linkType": link["api_name"]})
        columns = set(self._dataset_columns_for_ref(conn, ctx, link["backing"]["dataset"]))
        missing_keys = [key for key in [link["backing"]["fromKey"], link["backing"]["toKey"]] if key not in columns]
        if missing_keys:
            raise ValidationFailed(
                "link backing key missing",
                details={"linkType": link["api_name"], "missing": missing_keys},
            )

    def _object_types_for_version(
        self,
        conn: Connection,
        ctx: RequestContext,
        ontology_version_id: str,
    ) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in conn.execute(
                select(db.object_types).where(
                    and_(
                        db.object_types.c.tenant_id == ctx.tenant_id,
                        db.object_types.c.ontology_version_id == ontology_version_id,
                    )
                )
            )
            .mappings()
            .all()
        ]

    def _link_types_for_version(
        self,
        conn: Connection,
        ctx: RequestContext,
        ontology_version_id: str,
    ) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in conn.execute(
                select(db.link_types).where(
                    and_(
                        db.link_types.c.tenant_id == ctx.tenant_id,
                        db.link_types.c.ontology_version_id == ontology_version_id,
                    )
                )
            )
            .mappings()
            .all()
        ]

    def _properties_for_object_type(self, conn: Connection, object_type_id: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in conn.execute(
                select(db.property_types).where(db.property_types.c.object_type_id == object_type_id)
            )
            .mappings()
            .all()
        ]

    def _actions_for_target(self, conn: Connection, object_type_id: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in conn.execute(
                select(db.action_types).where(db.action_types.c.target_object_type_id == object_type_id)
            )
            .mappings()
            .all()
        ]

    def _active_ontology_version(self, conn: Connection, ctx: RequestContext) -> dict[str, Any]:
        row = (
            conn.execute(
                select(db.ontology_versions).where(
                    and_(
                        db.ontology_versions.c.tenant_id == ctx.tenant_id,
                        db.ontology_versions.c.status == "active",
                    )
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise NotFound("active ontology not found")
        return dict(row)

    def _active_object_type(
        self,
        conn: Connection,
        ctx: RequestContext,
        api_name: str,
    ) -> dict[str, Any]:
        active = self._active_ontology_version(conn, ctx)
        row = (
            conn.execute(
                select(db.object_types).where(
                    and_(
                        db.object_types.c.tenant_id == ctx.tenant_id,
                        db.object_types.c.ontology_version_id == active["id"],
                        db.object_types.c.api_name == api_name,
                    )
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise NotFound("object type not found", details={"api_name": api_name})
        return dict(row)

    def _active_action_type(
        self,
        conn: Connection,
        ctx: RequestContext,
        api_name: str,
    ) -> dict[str, Any]:
        active = self._active_ontology_version(conn, ctx)
        row = (
            conn.execute(
                select(db.action_types).where(
                    and_(
                        db.action_types.c.tenant_id == ctx.tenant_id,
                        db.action_types.c.ontology_version_id == active["id"],
                        db.action_types.c.api_name == api_name,
                        db.action_types.c.enabled == True,  # noqa: E712
                    )
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise NotFound("action type not found", details={"api_name": api_name})
        return dict(row)

    def _index_object_row(
        self,
        conn: Connection,
        ctx: RequestContext,
        object_type: dict[str, Any],
        row: dict[str, Any],
        source_dataset_version_id: str,
    ) -> None:
        properties = self._properties_for_object_type(conn, object_type["id"])
        pk_prop = next(prop for prop in properties if prop["api_name"] == object_type["primary_key_property"])
        object_id = row.get(pk_prop["column_name"])
        if object_id in {None, ""}:
            raise ValidationFailed("object primary key cannot be null")
        base_patch = {}
        for prop in properties:
            if prop["source"] == "dataset":
                base_patch[prop["api_name"]] = row.get(prop["column_name"])
        existing = self._object_record(conn, ctx, object_type["api_name"], str(object_id))
        now = _now()
        if existing is None:
            current = self._merge_properties(conn, object_type["id"], base_patch, {})
            conn.execute(
                insert(db.object_records).values(
                    id=_new_id("obj"),
                    tenant_id=ctx.tenant_id,
                    object_type_id=object_type["id"],
                    object_type_api_name=object_type["api_name"],
                    object_id=str(object_id),
                    properties=current,
                    base_properties=base_patch,
                    edit_properties={},
                    property_versions={key: 1 for key in current},
                    source_dataset_version_id=source_dataset_version_id,
                    source_hash=_json_hash(base_patch),
                    object_version=1,
                    deleted=False,
                    deletion_reason=None,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            self._record_conflicts_for_base_update(
                conn,
                ctx,
                object_type,
                str(object_id),
                existing,
                base_patch,
                source_dataset_version_id,
            )
            current = self._merge_properties(conn, object_type["id"], base_patch, existing["edit_properties"])
            conn.execute(
                update(db.object_records)
                .where(db.object_records.c.id == existing["id"])
                .values(
                    properties=current,
                    base_properties=base_patch,
                    source_dataset_version_id=source_dataset_version_id,
                    source_hash=_json_hash(base_patch),
                    object_version=existing["object_version"] + 1,
                    updated_at=now,
                )
            )
        self._outbox(
            conn,
            ctx,
            "object.changed",
            "object",
            f"{object_type['api_name']}/{object_id}",
            {"objectType": object_type["api_name"], "objectId": str(object_id)},
            idempotency_key=f"{source_dataset_version_id}:{object_type['api_name']}:{object_id}",
            correlation_id=source_dataset_version_id,
        )

    def _record_conflicts_for_base_update(
        self,
        conn: Connection,
        ctx: RequestContext,
        object_type: dict[str, Any],
        object_id: str,
        existing: dict[str, Any],
        base_patch: dict[str, Any],
        source_dataset_version_id: str,
    ) -> None:
        for prop in self._properties_for_object_type(conn, object_type["id"]):
            if prop["edit_policy"] != "conflict_requires_review":
                continue
            api_name = prop["api_name"]
            has_conflicting_edit = api_name in existing["edit_properties"] and existing["edit_properties"][
                api_name
            ] != base_patch.get(api_name)
            if has_conflicting_edit:
                conn.execute(
                    insert(db.object_conflicts).values(
                        id=_new_id("conflict"),
                        tenant_id=ctx.tenant_id,
                        object_type_id=object_type["id"],
                        object_id=object_id,
                        property_api_name=api_name,
                        source_value=base_patch.get(api_name),
                        edit_value=existing["edit_properties"][api_name],
                        source_dataset_version_id=source_dataset_version_id,
                        edit_id=None,
                        status="open",
                        created_at=_now(),
                    )
                )

    def _merge_properties(
        self,
        conn: Connection,
        object_type_id: str,
        base: dict[str, Any],
        edits: dict[str, Any],
    ) -> dict[str, Any]:
        current: dict[str, Any] = {}
        for prop in self._properties_for_object_type(conn, object_type_id):
            name = prop["api_name"]
            policy = prop["edit_policy"]
            if policy == "edit_only":
                if name in edits:
                    current[name] = edits[name]
            elif policy in {"edit_wins", "conflict_requires_review"}:
                current[name] = edits[name] if name in edits else base.get(name)
            elif policy == "source_wins":
                current[name] = base[name] if name in base else edits.get(name)
            else:
                current[name] = base.get(name)
        return current

    def _index_links_for_object_type(
        self,
        conn: Connection,
        ctx: RequestContext,
        object_type: dict[str, Any],
        rows: list[dict[str, Any]],
        source_dataset_version_id: str,
    ) -> int:
        active = self._active_ontology_version(conn, ctx)
        links = [
            dict(row)
            for row in conn.execute(
                select(db.link_types).where(
                    and_(
                        db.link_types.c.tenant_id == ctx.tenant_id,
                        db.link_types.c.ontology_version_id == active["id"],
                        db.link_types.c.from_object_type_id == object_type["id"],
                    )
                )
            )
            .mappings()
            .all()
        ]
        count = 0
        for link in links:
            from_key = link["backing"]["fromKey"]
            to_key = link["backing"]["toKey"]
            for row in rows:
                from_id = row.get(from_key)
                to_id = row.get(to_key)
                if from_id in {None, ""} or to_id in {None, ""}:
                    continue
                existing = (
                    conn.execute(
                        select(db.object_links).where(
                            and_(
                                db.object_links.c.tenant_id == ctx.tenant_id,
                                db.object_links.c.link_type_id == link["id"],
                                db.object_links.c.from_object_id == str(from_id),
                                db.object_links.c.to_object_id == str(to_id),
                            )
                        )
                    )
                    .mappings()
                    .first()
                )
                if existing:
                    conn.execute(
                        update(db.object_links)
                        .where(db.object_links.c.id == existing["id"])
                        .values(
                            link_version=existing["link_version"] + 1,
                            source_dataset_version_id=source_dataset_version_id,
                            deleted=False,
                            updated_at=_now(),
                        )
                    )
                else:
                    conn.execute(
                        insert(db.object_links).values(
                            id=_new_id("olink"),
                            tenant_id=ctx.tenant_id,
                            link_type_id=link["id"],
                            link_type_api_name=link["api_name"],
                            from_object_type_id=link["from_object_type_id"],
                            from_api_name=link["from_api_name"],
                            from_object_id=str(from_id),
                            to_object_type_id=link["to_object_type_id"],
                            to_api_name=link["to_api_name"],
                            to_object_id=str(to_id),
                            properties={},
                            source_dataset_version_id=source_dataset_version_id,
                            link_version=1,
                            deleted=False,
                            deletion_reason=None,
                            updated_at=_now(),
                        )
                    )
                count += 1
        return count

    def _object_record(
        self,
        conn: Connection,
        ctx: RequestContext,
        object_type_api_name: str,
        object_id: str,
    ) -> dict[str, Any] | None:
        row = (
            conn.execute(
                select(db.object_records).where(
                    and_(
                        db.object_records.c.tenant_id == ctx.tenant_id,
                        db.object_records.c.object_type_api_name == object_type_api_name,
                        db.object_records.c.object_id == object_id,
                    )
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    def _matches_filter(self, properties: dict[str, Any], filter_ast: dict[str, Any]) -> bool:
        return matches_filter(properties, filter_ast)

    def _validate_action_request(
        self,
        action_type: dict[str, Any],
        record: dict[str, Any],
        params: dict[str, Any],
    ) -> Exception | None:
        return validate_action_request(action_type, record, params)

    def _precondition_expression(self, precondition: dict[str, Any]) -> str:
        return precondition_expression(precondition)

    def _evaluate_precondition(self, expression: str, properties: dict[str, Any]) -> bool:
        return evaluate_safe_expression(expression, properties)

    def _record_writeback(
        self,
        conn: Connection,
        ctx: RequestContext,
        action_run_id: str,
        *,
        status: str,
        idempotency_key: str,
        response: dict[str, Any],
    ) -> None:
        conn.execute(
            insert(db.action_writebacks).values(
                id=_new_id("writeback"),
                tenant_id=ctx.tenant_id,
                action_run_id=action_run_id,
                mode="before_commit",
                connector_id=MOCK_WRITEBACK_CONNECTOR,
                request={"connector": MOCK_WRITEBACK_CONNECTOR, "simulated": True, "networkCall": False},
                response={**response, "simulated": True},
                status=status,
                idempotency_key=idempotency_key,
                attempts=1,
                created_at=_now(),
                completed_at=_now(),
            )
        )

    def _commit_action_mutations(
        self,
        conn: Connection,
        ctx: RequestContext,
        *,
        action_type: dict[str, Any],
        action_run_id: str,
        record: dict[str, Any],
        params: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        patch = self._action_patch(action_type, params)
        previous_values = self._previous_action_values(record, patch)
        self._update_action_target(conn, record, patch)
        edit_id = self._insert_object_edit(conn, ctx, action_run_id, record, patch, previous_values, idempotency_key)
        conn.execute(
            update(db.action_runs)
            .where(db.action_runs.c.id == action_run_id)
            .values(status="succeeded", error=None, completed_at=_now())
        )
        self._publish_action_commit_events(conn, ctx, action_run_id, record, edit_id)
        self._audit(
            conn,
            ctx,
            event_type="action.run.committed",
            resource_type="action_run",
            resource_id=action_run_id,
            action="apply",
            before_ref=previous_values,
            after_ref={"patch": patch, "object_edit_id": edit_id},
            correlation_id=action_run_id,
        )
        return {
            "actionRunId": action_run_id,
            "status": "succeeded",
            "objectEditId": edit_id,
            "target": {"objectType": record["object_type_api_name"], "objectId": record["object_id"]},
            "newObjectVersion": record["object_version"] + 1,
            "patch": patch,
        }

    def _action_patch(self, action_type: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
        patch: dict[str, Any] = {}
        for mutation in action_type["definition"].get("mutations", []):
            if mutation["type"] != "setProperty":
                raise ValidationFailed("v1 action supports setProperty only", details=mutation)
            patch[mutation["property"]] = self._mutation_value(mutation, params)
        return patch

    def _mutation_value(self, mutation: dict[str, Any], params: dict[str, Any]) -> Any:
        if "valueFrom" in mutation:
            return self._resolve_value_from(mutation["valueFrom"], params)
        return mutation.get("value")

    def _previous_action_values(self, record: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
        return {key: record["properties"].get(key) for key in patch}

    def _update_action_target(
        self,
        conn: Connection,
        record: dict[str, Any],
        patch: dict[str, Any],
    ) -> None:
        edit_properties = dict(record["edit_properties"])
        edit_properties.update(patch)
        current = self._merge_properties(conn, record["object_type_id"], record["base_properties"], edit_properties)
        result = conn.execute(
            update(db.object_records)
            .where(
                and_(
                    db.object_records.c.id == record["id"],
                    db.object_records.c.object_version == record["object_version"],
                )
            )
            .values(
                edit_properties=edit_properties,
                properties=current,
                object_version=record["object_version"] + 1,
                updated_at=_now(),
            )
        )
        if result.rowcount != 1:
            raise ConflictDetected("object version conflict during commit")

    def _insert_object_edit(
        self,
        conn: Connection,
        ctx: RequestContext,
        action_run_id: str,
        record: dict[str, Any],
        patch: dict[str, Any],
        previous_values: dict[str, Any],
        idempotency_key: str,
    ) -> str:
        edit_id = _new_id("edit")
        conn.execute(
            insert(db.object_edits).values(
                id=edit_id,
                tenant_id=ctx.tenant_id,
                action_run_id=action_run_id,
                object_type_id=record["object_type_id"],
                object_type_api_name=record["object_type_api_name"],
                object_id=record["object_id"],
                edit_type="set_property",
                patch=patch,
                previous_values=previous_values,
                actor_user_id=ctx.actor_user_id,
                idempotency_key=idempotency_key,
                created_at=_now(),
            )
        )
        return edit_id

    def _publish_action_commit_events(
        self,
        conn: Connection,
        ctx: RequestContext,
        action_run_id: str,
        record: dict[str, Any],
        edit_id: str,
    ) -> None:
        payload = {
            "actionRunId": action_run_id,
            "objectType": record["object_type_api_name"],
            "objectId": record["object_id"],
            "editId": edit_id,
        }
        for event_type in ["action.run.committed", "object.edit.committed", "object.changed"]:
            aggregate_type = "action_run" if event_type == "action.run.committed" else "object"
            aggregate_id = action_run_id if event_type == "action.run.committed" else record["object_id"]
            self._outbox(
                conn,
                ctx,
                event_type,
                aggregate_type,
                aggregate_id,
                payload,
                idempotency_key=f"{event_type}:{action_run_id}",
                correlation_id=action_run_id,
            )

    def _resolve_value_from(self, expression: str, params: dict[str, Any]) -> Any:
        if expression.startswith("params."):
            key = expression.split(".", 1)[1]
            return params.get(key)
        raise ValidationFailed("unsupported valueFrom expression", details={"expression": expression})

    def _ensure_materialization(
        self,
        api_name: str,
        *,
        ctx: RequestContext,
    ) -> dict[str, Any]:
        target_by_name = {
            "action_log": {
                "type": "action_log",
                "source": {"type": "action_runs"},
                "target": {"dataset": "ops.action_log"},
            },
            "order_current": {
                "type": "object_snapshot",
                "source": {"objectType": "Order"},
                "target": {"dataset": "ops.order_current"},
            },
        }
        if api_name not in target_by_name:
            raise NotFound("materialization not found", details={"api_name": api_name})
        spec = target_by_name[api_name]
        with self.engine.begin() as conn:
            existing = (
                conn.execute(
                    select(db.materializations).where(
                        and_(
                            db.materializations.c.tenant_id == ctx.tenant_id,
                            db.materializations.c.api_name == api_name,
                        )
                    )
                )
                .mappings()
                .first()
            )
            if existing:
                return dict(existing)
            materialization_id = _new_id("mat")
            conn.execute(
                insert(db.materializations).values(
                    id=materialization_id,
                    tenant_id=ctx.tenant_id,
                    api_name=api_name,
                    materialization_type=spec["type"],
                    source_ref=spec["source"],
                    target_ref=spec["target"],
                    trigger_config={"type": "manual"},
                    enabled=True,
                )
            )
            row = self._select_by_id(conn, db.materializations, materialization_id)
            if row is None:
                raise InvariantViolation("materialization insert failed")
            return row

    def _materialization_watermark(
        self,
        conn: Connection,
        materialization: dict[str, Any],
    ) -> dict[str, Any]:
        if materialization["materialization_type"] == "action_log":
            max_created = conn.execute(select(func.max(db.action_runs.c.created_at))).scalar()
            return {"action_run_created_at_lte": max_created}
        max_updated = conn.execute(select(func.max(db.object_records.c.updated_at))).scalar()
        return {"object_updated_at_lte": max_updated}

    def _materialization_rows(
        self,
        materialization: dict[str, Any],
        *,
        ctx: RequestContext,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        with self.engine.begin() as conn:
            if materialization["materialization_type"] == "action_log":
                return self._action_log_materialization_rows(conn, ctx)
            return self._order_current_materialization_rows(conn, ctx)

    def _action_log_materialization_rows(
        self,
        conn: Connection,
        ctx: RequestContext,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        action_rows = self._rows_for_tenant(conn, db.action_runs, ctx)
        edit_rows = self._rows_for_tenant(conn, db.object_edits, ctx)
        edit_by_action = {row["action_run_id"]: row for row in edit_rows}
        rows = [self._action_log_row(action, edit_by_action.get(action["id"], {})) for action in action_rows]
        return rows, [
            "action_run_id",
            "actor_user_id",
            "action_type",
            "target_object_type",
            "target_object_id",
            "status",
            "parameters_json",
            "edit_patch_json",
            "created_at",
        ]

    def _action_log_row(self, action: dict[str, Any], edit: dict[str, Any]) -> dict[str, Any]:
        return {
            "action_run_id": action["id"],
            "actor_user_id": action["actor_user_id"],
            "action_type": action["action_type_api_name"],
            "target_object_type": action["target_object_type_api_name"],
            "target_object_id": action["target_object_id"],
            "status": action["status"],
            "parameters_json": json.dumps(action["parameters"], sort_keys=True),
            "edit_patch_json": json.dumps(edit.get("patch", {}), sort_keys=True),
            "created_at": action["created_at"],
        }

    def _order_current_materialization_rows(
        self,
        conn: Connection,
        ctx: RequestContext,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        records = [
            row
            for row in self._rows_for_tenant(conn, db.object_records, ctx)
            if row["object_type_api_name"] == "Order" and not row["deleted"]
        ]
        rows = [self._order_current_row(record) for record in records]
        return rows, [
            "orderId",
            "customerId",
            "status",
            "operatorNote",
            "amount",
            "margin",
            "riskScore",
            "objectVersion",
            "updatedAt",
        ]

    def _order_current_row(self, record: dict[str, Any]) -> dict[str, Any]:
        props = record["properties"]
        return {
            "orderId": props.get("orderId"),
            "customerId": props.get("customerId"),
            "status": props.get("status"),
            "operatorNote": props.get("operatorNote"),
            "amount": props.get("amount"),
            "margin": props.get("margin"),
            "riskScore": props.get("riskScore"),
            "objectVersion": record["object_version"],
            "updatedAt": record["updated_at"],
        }

    def _error_payload(self, exc: Exception) -> dict[str, Any]:
        if hasattr(exc, "code"):
            typed_exc = cast(Any, exc)
            return {
                "type": typed_exc.code,
                "message": str(exc),
                "details": getattr(typed_exc, "details", {}),
            }
        return {"type": exc.__class__.__name__, "message": str(exc), "details": {}}
