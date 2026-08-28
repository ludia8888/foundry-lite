"""Pydantic request models for Action runtime and notification policy APIs."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from foundry_lite_api.schema_types import JsonObject


class ActionTargetRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    object_type: str = Field(alias="objectType")
    object_id: str = Field(alias="objectId")


class ActionApplyRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    target: ActionTargetRequest
    expected_object_version: int = Field(alias="expectedObjectVersion")
    params: JsonObject
    branch_id: str | None = Field(default=None, alias="branchId")


class ActionRunCancelRequest(BaseModel):
    reason: str | None = None


class ActionEffectCancelRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class ActionEffectReconciliationEvidenceRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    verification_method: Literal["provider_query", "provider_dashboard", "support_confirmation"] = Field(
        alias="verificationMethod"
    )
    provider_reference: str = Field(alias="providerReference", min_length=1, max_length=200)
    verified_at: str = Field(alias="verifiedAt", min_length=1, max_length=80)
    external_execution_id: str | None = Field(default=None, alias="externalExecutionId", max_length=200)


class ActionEffectReconcileRequest(BaseModel):
    resolution: Literal["confirmed_delivered", "confirmed_not_delivered"]
    evidence: ActionEffectReconciliationEvidenceRequest


class ActionNotificationRecipientRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_id: str = Field(alias="userId", min_length=1, max_length=200)
    roles: list[str] = Field(min_length=1, max_length=50)


class ActionNotificationPolicyCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    policy_name: str = Field(alias="policyName", min_length=2, max_length=64)
    display_name: str = Field(alias="displayName", min_length=1, max_length=120)
    delivery_mode: Literal["strict", "best_effort"] = Field(alias="deliveryMode")
    recipients: list[ActionNotificationRecipientRequest] = Field(min_length=1, max_length=500)


class ActionNotificationPolicyUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    display_name: str = Field(alias="displayName", min_length=1, max_length=120)
    delivery_mode: Literal["strict", "best_effort"] = Field(alias="deliveryMode")
    recipients: list[ActionNotificationRecipientRequest] = Field(min_length=1, max_length=500)
    status: Literal["active", "disabled"] = "active"
    expected_fingerprint: str = Field(alias="expectedFingerprint", min_length=1)


class ActionNotificationPolicyDisableRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    expected_fingerprint: str = Field(alias="expectedFingerprint", min_length=1)


class ActionBatchTargetItemRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    object_id: str = Field(alias="objectId")
    expected_object_version: int = Field(alias="expectedObjectVersion")


class ActionApplyBatchRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    object_type: str = Field(alias="objectType")
    targets: list[ActionBatchTargetItemRequest]
    params: JsonObject


class ActionFunctionBatchItemRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    object_id: str = Field(alias="objectId")
    expected_object_version: int = Field(alias="expectedObjectVersion", ge=0)
    params: JsonObject


class ActionFunctionBatchRunRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    object_type: str = Field(alias="objectType")
    items: list[ActionFunctionBatchItemRequest] = Field(min_length=1, max_length=10_000)
