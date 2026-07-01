"""Application-layer models and helpers for osdk protocols."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from foundry_lite.application.action_types import ActionApplyResponse, ActionValidationResponse
from foundry_lite.application.ports import ObjectLinkPayload, ObjectPayload, ObjectQueryResult
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

    def links(
        self,
        object_type_api_name: str,
        object_id: str,
        link_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
    ) -> list[ObjectLinkPayload]: ...


class OsdkActionFacade(Protocol):
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


class OsdkHost(Protocol):
    @property
    def objects(self) -> OsdkObjectFacade: ...

    @property
    def actions(self) -> OsdkActionFacade: ...
