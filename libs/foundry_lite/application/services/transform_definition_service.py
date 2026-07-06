"""Transform definition registration use cases."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path

from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.transform_repository import (
    TransformCheck,
    TransformRecord,
    TransformRepository,
    TransformRow,
)
from foundry_lite.application.primitives import _new_id
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.transform_protocols import (
    TransformRuntimeBoundary,
    require_transform_write_open,
)
from foundry_lite.application.services.transform_sql_guards import validate_sql_transform_uses_declared_inputs
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import InvariantViolation, ValidationFailed
from foundry_lite.domain.transform import (
    normalize_transform_language,
    normalize_transform_output_mode,
    safe_transform_path_token,
)


class TransformDefinitionService(CoreService):
    """Register and replace transform definitions inside the Transform context."""

    required_dependencies = ("root", "engine", "transform_repository")
    required_collaborators = ("runtime_service",)
    runtime_service: TransformRuntimeBoundary
    transform_repository: TransformRepository

    def register_transform(
        self,
        api_name: str,
        *,
        entrypoint: str | Path,
        inputs: Mapping[str, str],
        output_dataset_ref: str,
        checks: Sequence[TransformCheck] | None = None,
        ctx: RequestContext | None = None,
        mode: str = "snapshot",
        language: str = "sql",
    ) -> TransformRow:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "transform:run", "transform", api_name)
        require_transform_write_open(self.runtime_service, ctx, "register_transform", "transform", api_name)
        return self._register_definition(
            ctx,
            api_name,
            entrypoint=entrypoint,
            inputs=inputs,
            output_dataset_ref=output_dataset_ref,
            checks=checks,
            mode=mode,
            language=language,
        )

    def register_sql_transform(
        self,
        api_name: str,
        *,
        sql: str,
        inputs: Mapping[str, str],
        output_dataset_ref: str,
        checks: Sequence[TransformCheck] | None = None,
        ctx: RequestContext | None = None,
        mode: str = "snapshot",
    ) -> TransformRow:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "transform:run", "transform", api_name)
        require_transform_write_open(self.runtime_service, ctx, "register_sql_transform", "transform", api_name)
        entrypoint = self._write_registered_sql(ctx, api_name, sql)
        return self._register_definition(
            ctx,
            api_name,
            entrypoint=entrypoint,
            inputs=inputs,
            output_dataset_ref=output_dataset_ref,
            checks=checks,
            mode=mode,
            language="sql",
        )

    def register_python_transform(
        self,
        api_name: str,
        *,
        source_code: str,
        function_name: str | None,
        inputs: Mapping[str, str],
        output_dataset_ref: str,
        checks: Sequence[TransformCheck] | None = None,
        ctx: RequestContext | None = None,
        mode: str = "snapshot",
    ) -> TransformRow:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "transform:run", "transform", api_name)
        require_transform_write_open(self.runtime_service, ctx, "register_python_transform", "transform", api_name)
        entrypoint = self._write_registered_python(ctx, api_name, source_code, function_name)
        return self._register_definition(
            ctx,
            api_name,
            entrypoint=entrypoint,
            inputs=inputs,
            output_dataset_ref=output_dataset_ref,
            checks=checks,
            mode=mode,
            language="python",
        )

    def _write_registered_sql(self, ctx: RequestContext, api_name: str, sql: str) -> Path:
        if not sql.strip():
            raise ValidationFailed("SQL transform body is required", details={"api_name": api_name})
        validate_sql_transform_uses_declared_inputs(sql)
        entrypoint = _registered_sql_entrypoint(self.root, ctx.tenant_id, api_name)
        entrypoint.parent.mkdir(parents=True, exist_ok=True)
        entrypoint.write_text(sql, encoding="utf-8")
        return entrypoint

    def _write_registered_python(
        self,
        ctx: RequestContext,
        api_name: str,
        source_code: str,
        function_name: str | None,
    ) -> str:
        if not source_code.strip():
            raise ValidationFailed("Python transform sourceCode is required", details={"api_name": api_name})
        normalized_function = _normalized_python_function(function_name)
        entrypoint = _registered_python_entrypoint(self.root, ctx.tenant_id, api_name)
        entrypoint.parent.mkdir(parents=True, exist_ok=True)
        entrypoint.write_text(source_code, encoding="utf-8")
        if normalized_function is None:
            return str(entrypoint)
        return f"{entrypoint}:{normalized_function}"

    def _register_definition(
        self,
        ctx: RequestContext,
        api_name: str,
        *,
        entrypoint: str | Path,
        inputs: Mapping[str, str],
        output_dataset_ref: str,
        checks: Sequence[TransformCheck] | None,
        mode: str,
        language: str,
    ) -> TransformRow:
        normalized_language = normalize_transform_language(language)
        normalized_mode = normalize_transform_output_mode(mode)
        normalized_checks = [dict(check) for check in checks or ()]
        with self.engine.begin() as conn:
            existing = self._transform_by_api_name(conn, ctx, api_name)
            return self._upsert_transform_definition(
                conn,
                ctx,
                api_name,
                existing,
                entrypoint=entrypoint,
                inputs=inputs,
                output_dataset_ref=output_dataset_ref,
                checks=normalized_checks,
                mode=normalized_mode,
                language=normalized_language,
            )

    def _upsert_transform_definition(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        api_name: str,
        existing: TransformRow | None,
        *,
        entrypoint: str | Path,
        inputs: Mapping[str, str],
        output_dataset_ref: str,
        checks: Sequence[TransformCheck],
        mode: str,
        language: str,
    ) -> TransformRow:
        if existing is None:
            return self._create_transform_definition(
                conn,
                ctx,
                api_name,
                entrypoint=entrypoint,
                inputs=inputs,
                output_dataset_ref=output_dataset_ref,
                checks=[dict(check) for check in checks],
                mode=mode,
                language=language,
            )
        return self._replace_transform_definition(
            conn,
            ctx,
            existing,
            api_name,
            entrypoint,
            inputs,
            output_dataset_ref,
            checks,
            mode,
            language,
        )

    def _transform_by_api_name(
        self, conn: TransactionContext, ctx: RequestContext, api_name: str
    ) -> TransformRow | None:
        return self.transform_repository.transform_by_api_name(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            api_name=api_name,
        )

    def _create_transform_definition(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        api_name: str,
        *,
        entrypoint: str | Path,
        inputs: Mapping[str, str],
        output_dataset_ref: str,
        checks: list[dict[str, object]],
        mode: str,
        language: str,
    ) -> TransformRow:
        transform_id = _new_id("tf")
        self._insert_transform_definition(
            conn,
            ctx,
            transform_id,
            api_name,
            entrypoint=entrypoint,
            inputs=inputs,
            output_dataset_ref=output_dataset_ref,
            checks=checks,
            mode=mode,
            language=language,
        )
        self._audit_transform_definition_created(conn, ctx, transform_id, api_name, output_dataset_ref)
        row = self.transform_repository.transform_by_id(transaction=conn, transform_id=transform_id)
        if row is None:
            raise InvariantViolation("transform row missing after insert", details={"transform_id": transform_id})
        return row

    def _insert_transform_definition(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        transform_id: str,
        api_name: str,
        *,
        entrypoint: str | Path,
        inputs: Mapping[str, str],
        output_dataset_ref: str,
        checks: list[dict[str, object]],
        mode: str,
        language: str,
    ) -> None:
        self.transform_repository.insert_transform(
            transaction=conn,
            record=TransformRecord(
                transform_id=transform_id,
                tenant_id=ctx.tenant_id,
                api_name=api_name,
                language=language,
                entrypoint=str(entrypoint),
                mode=mode,
                inputs=dict(inputs),
                output_dataset_ref=output_dataset_ref,
                checks=checks,
            ),
        )

    def _audit_transform_definition_created(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        transform_id: str,
        api_name: str,
        output_dataset_ref: str,
    ) -> None:
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="transform.definition.created",
            resource_type="transform",
            resource_id=transform_id,
            action="register",
            after_ref={"api_name": api_name, "output_dataset_ref": output_dataset_ref},
        )

    def _replace_transform_definition(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        existing: TransformRow,
        api_name: str,
        entrypoint: str | Path,
        inputs: Mapping[str, str],
        output_dataset_ref: str,
        checks: Sequence[TransformCheck],
        mode: str,
        language: str,
    ) -> TransformRow:
        self.transform_repository.update_transform_definition(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            transform_id=existing["id"],
            language=language,
            entrypoint=str(entrypoint),
            mode=mode,
            inputs=dict(inputs),
            output_dataset_ref=output_dataset_ref,
            checks=checks,
        )
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="transform.definition.updated",
            resource_type="transform",
            resource_id=existing["id"],
            action="register",
            after_ref={"api_name": api_name, "output_dataset_ref": output_dataset_ref},
        )
        row = self.transform_repository.transform_by_id(transaction=conn, transform_id=existing["id"])
        if row is None:
            raise InvariantViolation("transform row missing after update", details={"transform_id": existing["id"]})
        return row


def _registered_sql_entrypoint(root: Path, tenant_id: str, api_name: str) -> Path:
    tenant_slug = safe_transform_path_token(tenant_id, "tenant_id")
    slug = safe_transform_path_token(api_name, "api_name")
    digest = hashlib.sha256(f"{tenant_id}:{api_name}".encode()).hexdigest()[:12]
    return root / "registered-transforms" / tenant_slug / f"{slug}-{digest}.sql"


def _registered_python_entrypoint(root: Path, tenant_id: str, api_name: str) -> Path:
    tenant_slug = safe_transform_path_token(tenant_id, "tenant_id")
    slug = safe_transform_path_token(api_name, "api_name")
    digest = hashlib.sha256(f"{tenant_id}:{api_name}".encode()).hexdigest()[:12]
    return root / "registered-transforms" / tenant_slug / f"{slug}-{digest}.py"


def _normalized_python_function(function_name: str | None) -> str | None:
    if function_name is None:
        return None
    normalized = function_name.strip()
    if not normalized:
        raise ValidationFailed("Python transform functionName is empty", details={"functionName": function_name})
    safe_transform_path_token(normalized, "functionName")
    return normalized
