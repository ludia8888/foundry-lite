"""Typed runtime shared by generated Foundry-lite Python OSDK packages."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import BinaryIO, Literal, NotRequired, TypedDict, cast

from foundry_lite.application.action_types import (
    ActionApplyResponse,
    ActionExecutionPlanResponse,
    ActionMediaUploadResult,
    ActionValidationResponse,
)
from foundry_lite.application.osdk_actions import OsdkActionInvoker, OsdkActionTarget, OsdkActionType
from foundry_lite.application.osdk_protocols import OsdkHost
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed


class OntologyObjectReference(TypedDict):
    objectType: str
    objectId: str


class ActionMediaReference(TypedDict):
    referenceKind: str
    mediaSetId: str
    mediaItemId: str
    mediaItemVersionId: str
    logicalPath: str
    contentHash: str
    mimeType: str
    byteSize: int
    classification: str


class ActionNotificationRecipient(TypedDict):
    userId: str
    roles: list[str]


class ActionNotificationPolicyCreateRequest(TypedDict):
    policyName: str
    displayName: str
    deliveryMode: Literal["strict", "best_effort"]
    recipients: list[ActionNotificationRecipient]


class ActionNotificationPolicyUpdateRequest(TypedDict):
    displayName: str
    deliveryMode: Literal["strict", "best_effort"]
    recipients: list[ActionNotificationRecipient]
    status: Literal["active", "disabled"]
    expectedFingerprint: str


class ActionEffectReconciliationEvidence(TypedDict):
    verificationMethod: Literal["provider_query", "provider_dashboard", "support_confirmation"]
    providerReference: str
    verifiedAt: str
    externalExecutionId: NotRequired[str]


class ActionEffectReconcileRequest(TypedDict):
    resolution: Literal["confirmed_delivered", "confirmed_not_delivered"]
    evidence: ActionEffectReconciliationEvidence


@dataclass(frozen=True)
class GeneratedActionEffectClient:
    """Typed Python OSDK operations surface for durable after-commit effects."""

    client: OsdkHost
    ctx: RequestContext | None = None

    def list(self, *, status: str | None = None, cursor: str | None = None, limit: int = 50) -> dict[str, object]:
        return self.client.actions.list_effect_receipts(status=status, cursor=cursor, limit=limit, ctx=self.ctx)

    def get(self, receipt_id: str) -> dict[str, object]:
        return self.client.actions.get_effect_receipt(receipt_id, ctx=self.ctx)

    def cancel(self, receipt_id: str, *, reason: str | None, idempotency_key: str) -> dict[str, object]:
        return self.client.actions.cancel_effect(
            receipt_id,
            reason=reason,
            idempotency_key=idempotency_key,
            ctx=self.ctx,
        )

    def retry(self, receipt_id: str, *, idempotency_key: str) -> dict[str, object]:
        return self.client.actions.retry_effect(receipt_id, idempotency_key=idempotency_key, ctx=self.ctx)

    def reconcile(
        self,
        receipt_id: str,
        payload: ActionEffectReconcileRequest,
        *,
        idempotency_key: str,
    ) -> dict[str, object]:
        return self.client.actions.reconcile_effect(
            receipt_id,
            resolution=payload["resolution"],
            evidence=cast(Mapping[str, object], payload["evidence"]),
            idempotency_key=idempotency_key,
            ctx=self.ctx,
        )


@dataclass(frozen=True)
class GeneratedActionNotificationPolicyClient:
    """Typed Python OSDK administration surface for governed recipient policies."""

    client: OsdkHost
    ctx: RequestContext | None = None

    def list(self, *, cursor: str | None = None, limit: int = 50) -> dict[str, object]:
        return self.client.actions.list_notification_policies(cursor=cursor, limit=limit, ctx=self.ctx)

    def get(self, policy_name: str) -> dict[str, object]:
        return self.client.actions.get_notification_policy(policy_name, ctx=self.ctx)

    def create(self, payload: ActionNotificationPolicyCreateRequest, *, idempotency_key: str) -> dict[str, object]:
        return self.client.actions.create_notification_policy(
            payload["policyName"],
            display_name=payload["displayName"],
            delivery_mode=payload["deliveryMode"],
            recipients=cast(list[Mapping[str, object]], payload["recipients"]),
            idempotency_key=idempotency_key,
            ctx=self.ctx,
        )

    def update(
        self,
        policy_name: str,
        payload: ActionNotificationPolicyUpdateRequest,
        *,
        idempotency_key: str,
    ) -> dict[str, object]:
        return self.client.actions.update_notification_policy(
            policy_name,
            display_name=payload["displayName"],
            delivery_mode=payload["deliveryMode"],
            recipients=cast(list[Mapping[str, object]], payload["recipients"]),
            status=payload["status"],
            expected_fingerprint=payload["expectedFingerprint"],
            idempotency_key=idempotency_key,
            ctx=self.ctx,
        )

    def disable(self, policy_name: str, *, expected_fingerprint: str, idempotency_key: str) -> dict[str, object]:
        return self.client.actions.disable_notification_policy(
            policy_name,
            expected_fingerprint=expected_fingerprint,
            idempotency_key=idempotency_key,
            ctx=self.ctx,
        )


@dataclass(frozen=True)
class GeneratedActionType[ParamsT]:
    """One generated Action descriptor with its static parameter contract."""

    descriptor: OsdkActionType

    @property
    def api_name(self) -> str:
        return self.descriptor.api_name

    def bind(self, client: OsdkHost, *, ctx: RequestContext | None = None) -> GeneratedActionClient[ParamsT]:
        return GeneratedActionClient(OsdkActionInvoker(client, self.descriptor, ctx))


@dataclass(frozen=True)
class GeneratedActionClient[ParamsT]:
    """Typed entrypoint that delegates to the governed Python OSDK runtime."""

    invoker: OsdkActionInvoker

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
        return self.invoker.upload_parameter(
            parameter_name,
            source=source,
            file_name=file_name,
            supplied_mime_type=supplied_mime_type,
            idempotency_key=idempotency_key,
            target=target,
            object_type=object_type,
            object_id=object_id,
            format=format,
        )

    def validate(
        self,
        params: ParamsT,
        *,
        target: OsdkActionTarget | None = None,
        object_type: str | None = None,
        object_id: str | None = None,
        expected_object_version: int | None = None,
    ) -> ActionValidationResponse:
        return self.invoker.validate_action(
            _parameter_mapping(params),
            target=target,
            object_type=object_type,
            object_id=object_id,
            expected_object_version=expected_object_version,
        )

    def plan(
        self,
        params: ParamsT,
        *,
        target: OsdkActionTarget | None = None,
        object_type: str | None = None,
        object_id: str | None = None,
        expected_object_version: int | None = None,
        branch_id: str | None = None,
    ) -> ActionExecutionPlanResponse:
        return self.invoker.plan_action(
            _parameter_mapping(params),
            target=target,
            object_type=object_type,
            object_id=object_id,
            expected_object_version=expected_object_version,
            branch_id=branch_id,
        )

    def dry_run(
        self,
        params: ParamsT,
        *,
        target: OsdkActionTarget | None = None,
        object_type: str | None = None,
        object_id: str | None = None,
        expected_object_version: int | None = None,
        branch_id: str | None = None,
    ) -> ActionExecutionPlanResponse:
        return self.invoker.dry_run_action(
            _parameter_mapping(params),
            target=target,
            object_type=object_type,
            object_id=object_id,
            expected_object_version=expected_object_version,
            branch_id=branch_id,
        )

    def apply(
        self,
        params: ParamsT,
        *,
        idempotency_key: str,
        target: OsdkActionTarget | None = None,
        object_type: str | None = None,
        object_id: str | None = None,
        expected_object_version: int | None = None,
    ) -> ActionApplyResponse:
        return self.invoker.apply_action(
            _parameter_mapping(params),
            idempotency_key=idempotency_key,
            target=target,
            object_type=object_type,
            object_id=object_id,
            expected_object_version=expected_object_version,
        )

    def apply_on_branch(
        self,
        params: ParamsT,
        *,
        branch_id: str,
        idempotency_key: str,
        target: OsdkActionTarget | None = None,
        object_type: str | None = None,
        object_id: str | None = None,
        expected_object_version: int | None = None,
    ) -> dict[str, object]:
        return self.invoker.apply_on_branch(
            _parameter_mapping(params),
            branch_id=branch_id,
            idempotency_key=idempotency_key,
            target=target,
            object_type=object_type,
            object_id=object_id,
            expected_object_version=expected_object_version,
        )

    def start_run(
        self,
        params: ParamsT,
        *,
        idempotency_key: str,
        target: OsdkActionTarget | None = None,
        object_type: str | None = None,
        object_id: str | None = None,
        expected_object_version: int | None = None,
        wait_seconds: int = 0,
    ) -> dict[str, object]:
        return self.invoker.start_action_run(
            _parameter_mapping(params),
            idempotency_key=idempotency_key,
            target=target,
            object_type=object_type,
            object_id=object_id,
            expected_object_version=expected_object_version,
            wait_seconds=wait_seconds,
        )

    def get_run(self, run_id: str) -> dict[str, object]:
        return self.invoker.get_run(run_id)

    def list_runs(self, *, cursor: str | None = None, limit: int = 50) -> dict[str, object]:
        return self.invoker.list_runs(cursor=cursor, limit=limit)

    def events(self, run_id: str, *, after_sequence: int = 0, limit: int = 100) -> dict[str, object]:
        return self.invoker.run_events(run_id, after_sequence=after_sequence, limit=limit)

    def cancel(self, run_id: str, *, idempotency_key: str, reason: str | None = None) -> dict[str, object]:
        return self.invoker.cancel_run(run_id, idempotency_key=idempotency_key, reason=reason)

    def logs(self, *, cursor: str | None = None, limit: int = 50) -> dict[str, object]:
        return self.invoker.logs(cursor=cursor, limit=limit)

    def revert_eligibility(self, run_id: str) -> dict[str, object]:
        return self.invoker.revert_eligibility(run_id)

    def revert(self, run_id: str, *, idempotency_key: str) -> dict[str, object]:
        return self.invoker.revert_run(run_id, idempotency_key=idempotency_key)


def _parameter_mapping(params: object) -> Mapping[str, object]:
    if not isinstance(params, Mapping):
        raise ValidationFailed("Generated Python OSDK Action params must be a mapping")
    return cast(Mapping[str, object], params)
