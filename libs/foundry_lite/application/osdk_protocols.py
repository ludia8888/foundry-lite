"""Application-layer models and helpers for osdk protocols."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import BinaryIO, Protocol

from foundry_lite.application.action_types import (
    ActionApplyResponse,
    ActionCatalogItem,
    ActionCatalogPage,
    ActionExecutionPlanResponse,
    ActionMediaUploadResult,
    ActionValidationResponse,
)
from foundry_lite.application.ports import (
    ObjectAggregationResult,
    ObjectLinkPayload,
    ObjectPayload,
    ObjectQueryResult,
)
from foundry_lite.domain.context import RequestContext


class OsdkObjectFacade(Protocol):
    def get(
        self,
        object_type_api_name: str,
        object_id: str,
        *,
        ctx: RequestContext | None = None,
        include_explain: bool = False,
    ) -> ObjectPayload: ...

    def query(
        self,
        object_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
        filter_ast: Mapping[str, object] | None = None,
        order_by: Sequence[Mapping[str, str]] | None = None,
        limit: int = 50,
        cursor: str | None = None,
        search_text: str | None = None,
        semantic_text: str | None = None,
    ) -> ObjectQueryResult: ...

    def aggregate(
        self,
        object_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
        filter_ast: Mapping[str, object] | None = None,
        group_by: Sequence[str] | None = None,
        select: Sequence[Mapping[str, object]] | None = None,
    ) -> ObjectAggregationResult: ...

    def links(
        self,
        object_type_api_name: str,
        object_id: str,
        link_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
    ) -> list[ObjectLinkPayload]: ...


class OsdkActionFacade(Protocol):
    def list_effect_receipts(
        self,
        *,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]: ...

    def get_effect_receipt(self, receipt_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]: ...

    def cancel_effect(
        self,
        receipt_id: str,
        *,
        reason: str | None,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]: ...

    def retry_effect(
        self,
        receipt_id: str,
        *,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]: ...

    def reconcile_effect(
        self,
        receipt_id: str,
        *,
        resolution: str,
        evidence: Mapping[str, object],
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]: ...

    def list_notification_policies(
        self, *, cursor: str | None = None, limit: int = 50, ctx: RequestContext | None = None
    ) -> dict[str, object]: ...

    def get_notification_policy(self, policy_name: str, *, ctx: RequestContext | None = None) -> dict[str, object]: ...

    def create_notification_policy(
        self,
        policy_name: str,
        *,
        display_name: str,
        delivery_mode: str,
        recipients: Sequence[Mapping[str, object]],
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]: ...

    def update_notification_policy(
        self,
        policy_name: str,
        *,
        display_name: str,
        delivery_mode: str,
        recipients: Sequence[Mapping[str, object]],
        status: str,
        expected_fingerprint: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]: ...

    def disable_notification_policy(
        self,
        policy_name: str,
        *,
        expected_fingerprint: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]: ...

    def list(
        self,
        *,
        cursor: str | None = None,
        limit: int = 50,
        ctx: RequestContext | None = None,
    ) -> ActionCatalogPage: ...

    def get(self, action_api_name: str, *, ctx: RequestContext | None = None) -> ActionCatalogItem: ...

    def schema(self, action_api_name: str, *, ctx: RequestContext | None = None) -> dict[str, object]: ...

    def upload_parameter(
        self,
        action_api_name: str,
        parameter_name: str,
        *,
        object_type: str,
        object_id: str,
        file_name: str,
        source: BinaryIO,
        supplied_mime_type: str,
        idempotency_key: str,
        format: str | None = None,
        ctx: RequestContext | None = None,
    ) -> ActionMediaUploadResult: ...

    def apply(
        self,
        action_api_name: str,
        *,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> ActionApplyResponse: ...

    def validate(
        self,
        action_api_name: str,
        *,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        ctx: RequestContext | None = None,
    ) -> ActionValidationResponse: ...

    def plan(
        self,
        action_api_name: str,
        *,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        branch_id: str | None = None,
        ctx: RequestContext | None = None,
    ) -> ActionExecutionPlanResponse: ...

    def dry_run(
        self,
        action_api_name: str,
        *,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        branch_id: str | None = None,
        ctx: RequestContext | None = None,
    ) -> ActionExecutionPlanResponse: ...

    def execute_branch(
        self,
        action_api_name: str,
        *,
        branch_id: str,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]: ...

    def branch_object(
        self,
        branch_id: str,
        object_type: str,
        object_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]: ...

    def branch_link(
        self,
        branch_id: str,
        link_type: str,
        from_object_id: str,
        to_object_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]: ...

    def branch_diff(self, branch_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]: ...

    def start_run(
        self,
        action_api_name: str,
        *,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        idempotency_key: str,
        wait_seconds: int = 0,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]: ...

    def start_batch_run(
        self,
        action_api_name: str,
        *,
        object_type: str,
        items: Sequence[Mapping[str, object]],
        idempotency_key: str,
        wait_seconds: int = 0,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]: ...

    def get_run(self, run_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]: ...

    def list_runs(
        self, *, cursor: str | None = None, limit: int = 50, ctx: RequestContext | None = None
    ) -> dict[str, object]: ...

    def events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]: ...

    def cancel(
        self,
        run_id: str,
        *,
        idempotency_key: str,
        reason: str | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]: ...

    def logs(
        self, *, cursor: str | None = None, limit: int = 50, ctx: RequestContext | None = None
    ) -> dict[str, object]: ...

    def revert_eligibility(self, run_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]: ...

    def revert(
        self,
        run_id: str,
        *,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]: ...


class OsdkHost(Protocol):
    @property
    def objects(self) -> OsdkObjectFacade: ...

    @property
    def actions(self) -> OsdkActionFacade: ...
