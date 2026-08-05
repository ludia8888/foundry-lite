"""Python OSDK Action types, bound invokers, and durable-run helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import BinaryIO, Literal, Protocol, cast

from foundry_lite.application.action_types import (
    ActionApplyResponse,
    ActionCatalogItem,
    ActionCatalogPage,
    ActionExecutionPlanResponse,
    ActionMediaUploadResult,
    ActionValidationResponse,
)
from foundry_lite.application.osdk_protocols import OsdkHost
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed


@dataclass(frozen=True)
class OsdkActionType:
    api_name: str
    target_object_type: str
    parameter_names: tuple[str, ...] = ()
    required_parameters: tuple[str, ...] = ()
    target_kind: Literal["object", "interface"] = "object"


@dataclass(frozen=True)
class OsdkActionBinding:
    alias: str
    action_type: OsdkActionType


class OsdkActionTarget(Protocol):
    @property
    def _client(self) -> OsdkHost: ...

    @property
    def _ctx(self) -> RequestContext | None: ...

    @property
    def object_type(self) -> str: ...

    @property
    def object_id(self) -> str: ...

    @property
    def object_version(self) -> int: ...


@dataclass(frozen=True)
class OsdkTargetRef:
    object_type: str
    object_id: str
    object_version: int


@dataclass(frozen=True)
class OsdkActionInvoker:
    _client: OsdkHost
    action_type: OsdkActionType
    _ctx: RequestContext | None = None

    def list_actions(self, *, cursor: str | None = None, limit: int = 50) -> ActionCatalogPage:
        return self._client.actions.list(cursor=cursor, limit=limit, ctx=self._ctx)

    def get_action(self) -> ActionCatalogItem:
        return self._client.actions.get(self.action_type.api_name, ctx=self._ctx)

    def action_schema(self) -> dict[str, object]:
        return self._client.actions.schema(self.action_type.api_name, ctx=self._ctx)

    def upload_parameter(
        self,
        parameter_name: str,
        *,
        source: BinaryIO,
        file_name: str,
        supplied_mime_type: str,
        idempotency_key: str,
        target: OsdkActionTarget | None = None,
        object_type: str | None = None,
        object_id: str | None = None,
        format: str | None = None,
    ) -> ActionMediaUploadResult:
        target_type, target_id = _upload_target(self.action_type, target, object_type, object_id)
        _require_idempotency_key(idempotency_key)
        return self._client.actions.upload_parameter(
            self.action_type.api_name,
            parameter_name,
            object_type=target_type,
            object_id=target_id,
            file_name=file_name,
            source=source,
            supplied_mime_type=supplied_mime_type,
            idempotency_key=idempotency_key,
            format=format,
            ctx=self._ctx,
        )

    def apply_action(
        self,
        params: Mapping[str, object],
        *,
        idempotency_key: str,
        target: OsdkActionTarget | None = None,
        object_type: str | None = None,
        object_id: str | None = None,
        expected_object_version: int | None = None,
        ctx: RequestContext | None = None,
    ) -> ActionApplyResponse:
        target_ref = self._validated_target(params, target, object_type, object_id, expected_object_version)
        _require_idempotency_key(idempotency_key)
        return self._client.actions.apply(
            self.action_type.api_name,
            object_type=target_ref.object_type,
            object_id=target_ref.object_id,
            expected_object_version=target_ref.object_version,
            params=params,
            idempotency_key=idempotency_key,
            ctx=ctx or self._ctx,
        )

    def validate_action(
        self,
        params: Mapping[str, object],
        *,
        target: OsdkActionTarget | None = None,
        object_type: str | None = None,
        object_id: str | None = None,
        expected_object_version: int | None = None,
        ctx: RequestContext | None = None,
    ) -> ActionValidationResponse:
        target_ref = self._validated_target(params, target, object_type, object_id, expected_object_version)
        return self._client.actions.validate(
            self.action_type.api_name,
            object_type=target_ref.object_type,
            object_id=target_ref.object_id,
            expected_object_version=target_ref.object_version,
            params=params,
            ctx=ctx or self._ctx,
        )

    def plan_action(
        self,
        params: Mapping[str, object],
        *,
        target: OsdkActionTarget | None = None,
        object_type: str | None = None,
        object_id: str | None = None,
        expected_object_version: int | None = None,
        branch_id: str | None = None,
        ctx: RequestContext | None = None,
    ) -> ActionExecutionPlanResponse:
        target_ref = self._validated_target(params, target, object_type, object_id, expected_object_version)
        return self._client.actions.plan(
            self.action_type.api_name,
            object_type=target_ref.object_type,
            object_id=target_ref.object_id,
            expected_object_version=target_ref.object_version,
            params=params,
            branch_id=branch_id,
            ctx=ctx or self._ctx,
        )

    def dry_run_action(
        self,
        params: Mapping[str, object],
        *,
        target: OsdkActionTarget | None = None,
        object_type: str | None = None,
        object_id: str | None = None,
        expected_object_version: int | None = None,
        branch_id: str | None = None,
        ctx: RequestContext | None = None,
    ) -> ActionExecutionPlanResponse:
        target_ref = self._validated_target(params, target, object_type, object_id, expected_object_version)
        return self._client.actions.dry_run(
            self.action_type.api_name,
            object_type=target_ref.object_type,
            object_id=target_ref.object_id,
            expected_object_version=target_ref.object_version,
            params=params,
            branch_id=branch_id,
            ctx=ctx or self._ctx,
        )

    def apply_on_branch(
        self,
        params: Mapping[str, object],
        *,
        branch_id: str,
        idempotency_key: str,
        target: OsdkActionTarget | None = None,
        object_type: str | None = None,
        object_id: str | None = None,
        expected_object_version: int | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        target_ref = self._validated_target(params, target, object_type, object_id, expected_object_version)
        _require_idempotency_key(idempotency_key)
        return self._client.actions.execute_branch(
            self.action_type.api_name,
            branch_id=branch_id,
            object_type=target_ref.object_type,
            object_id=target_ref.object_id,
            expected_object_version=target_ref.object_version,
            params=params,
            idempotency_key=idempotency_key,
            ctx=ctx or self._ctx,
        )

    def branch_object(
        self,
        branch_id: str,
        object_type: str,
        object_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._client.actions.branch_object(branch_id, object_type, object_id, ctx=ctx or self._ctx)

    def branch_link(
        self,
        branch_id: str,
        link_type: str,
        from_object_id: str,
        to_object_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._client.actions.branch_link(
            branch_id, link_type, from_object_id, to_object_id, ctx=ctx or self._ctx
        )

    def branch_diff(self, branch_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._client.actions.branch_diff(branch_id, ctx=ctx or self._ctx)

    def start_action_run(
        self,
        params: Mapping[str, object],
        *,
        idempotency_key: str,
        target: OsdkActionTarget | None = None,
        object_type: str | None = None,
        object_id: str | None = None,
        expected_object_version: int | None = None,
        wait_seconds: int = 0,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        target_ref = self._validated_target(params, target, object_type, object_id, expected_object_version)
        _require_idempotency_key(idempotency_key)
        return self._client.actions.start_run(
            self.action_type.api_name,
            object_type=target_ref.object_type,
            object_id=target_ref.object_id,
            expected_object_version=target_ref.object_version,
            params=params,
            idempotency_key=idempotency_key,
            wait_seconds=wait_seconds,
            ctx=ctx or self._ctx,
        )

    def get_run(self, run_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._client.actions.get_run(run_id, ctx=ctx or self._ctx)

    def start_action_batch_run(
        self,
        items: Sequence[Mapping[str, object]],
        *,
        idempotency_key: str,
        object_type: str | None = None,
        wait_seconds: int = 0,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        _require_idempotency_key(idempotency_key)
        return self._client.actions.start_batch_run(
            self.action_type.api_name,
            object_type=object_type or self.action_type.target_object_type,
            items=items,
            idempotency_key=idempotency_key,
            wait_seconds=wait_seconds,
            ctx=ctx or self._ctx,
        )

    def list_runs(
        self, *, cursor: str | None = None, limit: int = 50, ctx: RequestContext | None = None
    ) -> dict[str, object]:
        return self._client.actions.list_runs(cursor=cursor, limit=limit, ctx=ctx or self._ctx)

    def run_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._client.actions.events(run_id, after_sequence=after_sequence, limit=limit, ctx=ctx or self._ctx)

    def cancel_run(
        self,
        run_id: str,
        *,
        idempotency_key: str,
        reason: str | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        _require_idempotency_key(idempotency_key)
        return self._client.actions.cancel(run_id, idempotency_key=idempotency_key, reason=reason, ctx=ctx or self._ctx)

    def logs(
        self, *, cursor: str | None = None, limit: int = 50, ctx: RequestContext | None = None
    ) -> dict[str, object]:
        return self._client.actions.logs(cursor=cursor, limit=limit, ctx=ctx or self._ctx)

    def revert_eligibility(self, run_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._client.actions.revert_eligibility(run_id, ctx=ctx or self._ctx)

    def revert_run(
        self,
        run_id: str,
        *,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        _require_idempotency_key(idempotency_key)
        return self._client.actions.revert(run_id, idempotency_key=idempotency_key, ctx=ctx or self._ctx)

    def _validated_target(
        self,
        params: Mapping[str, object],
        target: OsdkActionTarget | None,
        object_type: str | None,
        object_id: str | None,
        expected_object_version: int | None,
    ) -> OsdkTargetRef:
        _validate_action_params(self.action_type, params)
        return _action_target_ref(self.action_type, target, object_type, object_id, expected_object_version)


@dataclass(frozen=True)
class OsdkBoundAction:
    source: OsdkActionTarget
    action_type: OsdkActionType

    def apply_action(self, params: Mapping[str, object], *, idempotency_key: str) -> ActionApplyResponse:
        return self._invoker().apply_action(params, idempotency_key=idempotency_key, target=self.source)

    def validate_action(self, params: Mapping[str, object]) -> ActionValidationResponse:
        return self._invoker().validate_action(params, target=self.source)

    def plan_action(self, params: Mapping[str, object], *, branch_id: str | None = None) -> ActionExecutionPlanResponse:
        return self._invoker().plan_action(params, target=self.source, branch_id=branch_id)

    def dry_run_action(
        self, params: Mapping[str, object], *, branch_id: str | None = None
    ) -> ActionExecutionPlanResponse:
        return self._invoker().dry_run_action(params, target=self.source, branch_id=branch_id)

    def apply_on_branch(
        self, params: Mapping[str, object], *, branch_id: str, idempotency_key: str
    ) -> dict[str, object]:
        return self._invoker().apply_on_branch(
            params,
            branch_id=branch_id,
            idempotency_key=idempotency_key,
            target=self.source,
        )

    def start_action_run(
        self, params: Mapping[str, object], *, idempotency_key: str, wait_seconds: int = 0
    ) -> dict[str, object]:
        return self._invoker().start_action_run(
            params,
            idempotency_key=idempotency_key,
            target=self.source,
            wait_seconds=wait_seconds,
        )

    def _invoker(self) -> OsdkActionInvoker:
        return OsdkActionInvoker(self.source._client, self.action_type, self.source._ctx)


def action_type(
    api_name: str,
    *,
    target_object_type: str,
    parameter_names: Sequence[str] = (),
    required_parameters: Sequence[str] = (),
    target_kind: Literal["object", "interface"] = "object",
) -> OsdkActionType:
    return OsdkActionType(
        api_name,
        target_object_type,
        tuple(parameter_names),
        tuple(required_parameters),
        _target_kind(target_kind),
    )


def _validate_action_params(action_type_resource: OsdkActionType, params: Mapping[str, object]) -> None:
    missing = sorted(set(action_type_resource.required_parameters) - set(params))
    if missing:
        raise ValidationFailed("Python OSDK action params missing required parameter", details={"missing": missing})
    unknown = sorted(set(params) - set(action_type_resource.parameter_names))
    if action_type_resource.parameter_names and unknown:
        raise ValidationFailed("Python OSDK action params include unknown parameter", details={"unknown": unknown})


def _upload_target(
    action_type_resource: OsdkActionType,
    target: OsdkActionTarget | None,
    object_type: str | None,
    object_id: str | None,
) -> tuple[str, str]:
    if target is not None:
        concrete_type = target.object_type
        target_id = target.object_id
    else:
        concrete_type = object_type or _default_target_object_type(action_type_resource)
        target_id = object_id or ""
    _validate_target_object_type(action_type_resource, concrete_type)
    if not target_id:
        raise ValidationFailed("Python OSDK Action upload requires object_id")
    return concrete_type, target_id


def _action_target_ref(
    action_type_resource: OsdkActionType,
    target: OsdkActionTarget | None,
    object_type: str | None,
    object_id: str | None,
    expected_object_version: int | None,
) -> OsdkTargetRef:
    if target is not None:
        if object_type is not None and object_type != target.object_type:
            raise ValidationFailed(
                "Python OSDK action target object type conflicts with bound target",
                details={"explicit": object_type, "bound": target.object_type},
            )
        concrete_type = target.object_type
        _validate_target_object_type(action_type_resource, concrete_type)
        return OsdkTargetRef(concrete_type, target.object_id, target.object_version)
    if object_id is None or expected_object_version is None:
        raise ValidationFailed("Python OSDK action target requires object_id and expected_object_version")
    concrete_type = object_type or _default_target_object_type(action_type_resource)
    _validate_target_object_type(action_type_resource, concrete_type)
    return OsdkTargetRef(concrete_type, object_id, expected_object_version)


def _validate_target_object_type(action_type_resource: OsdkActionType, object_type_name: str) -> None:
    if action_type_resource.target_kind == "interface":
        if not object_type_name or object_type_name == action_type_resource.target_object_type:
            raise ValidationFailed(
                "Python OSDK interface action requires a concrete object type",
                details={"interface": action_type_resource.target_object_type},
            )
        return
    if object_type_name != action_type_resource.target_object_type:
        raise ValidationFailed(
            "Python OSDK action target object type mismatch",
            details={"expected": action_type_resource.target_object_type, "actual": object_type_name},
        )


def _default_target_object_type(action_type_resource: OsdkActionType) -> str:
    if action_type_resource.target_kind == "interface":
        raise ValidationFailed(
            "Python OSDK interface action requires object_type for an explicit target",
            details={"interface": action_type_resource.target_object_type},
        )
    return action_type_resource.target_object_type


def _target_kind(value: str) -> Literal["object", "interface"]:
    if value not in {"object", "interface"}:
        raise ValidationFailed("Python OSDK action target kind is invalid", details={"targetKind": value})
    return cast(Literal["object", "interface"], value)


def _require_idempotency_key(idempotency_key: str) -> None:
    if not idempotency_key:
        raise ValidationFailed("Python OSDK action requires idempotency_key")
