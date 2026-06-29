from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol, cast

from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.transform_repository import (
    TransformCheck,
    TransformRecord,
    TransformRepository,
    TransformRow,
)
from foundry_lite.application.primitives import _new_id
from foundry_lite.application.services.transform_protocols import TransformRuntimeBoundary
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import InvariantViolation


class _TransformDefinitionOwner(Protocol):
    transform_repository: TransformRepository
    runtime_service: TransformRuntimeBoundary


def _owner(service: object) -> _TransformDefinitionOwner:
    return cast(_TransformDefinitionOwner, service)


class TransformDefinitionRegistrationMixin:
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
        """Insert a new transform definition and emit its audit record."""
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
        row = _owner(self).transform_repository.transform_by_id(transaction=conn, transform_id=transform_id)
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
        _owner(self).transform_repository.insert_transform(
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
        _owner(self).runtime_service._audit(
            conn,
            ctx,
            event_type="transform.definition.created",
            resource_type="transform",
            resource_id=transform_id,
            action="register",
            after_ref={"api_name": api_name, "output_dataset_ref": output_dataset_ref},
        )

    def _update_transform_definition(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        existing: TransformRow,
        *,
        language: str,
        entrypoint: str | Path,
        mode: str,
        inputs: Mapping[str, str],
        output_dataset_ref: str,
        checks: Sequence[TransformCheck],
    ) -> None:
        """Persist a replacement transform definition under the tenant boundary."""
        _owner(self).transform_repository.update_transform_definition(
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
        """Update an existing transform definition and emit its audit record."""
        self._update_transform_definition(
            conn,
            ctx,
            existing,
            language=language,
            entrypoint=entrypoint,
            mode=mode,
            inputs=inputs,
            output_dataset_ref=output_dataset_ref,
            checks=checks,
        )
        _owner(self).runtime_service._audit(
            conn,
            ctx,
            event_type="transform.definition.updated",
            resource_type="transform",
            resource_id=existing["id"],
            action="register",
            after_ref={"api_name": api_name, "output_dataset_ref": output_dataset_ref},
        )
        row = _owner(self).transform_repository.transform_by_id(transaction=conn, transform_id=existing["id"])
        if row is None:
            raise InvariantViolation("transform row missing after update", details={"transform_id": existing["id"]})
        return row

    def _normalized_checks(self, checks: Sequence[TransformCheck] | None) -> list[dict[str, object]]:
        return [dict(check) for check in checks or ()]
