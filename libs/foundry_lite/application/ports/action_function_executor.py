"""Compute-only boundary for version-pinned Action functions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from foundry_lite.domain.action_runtime.ontology_edit_batch import OntologyEditBatch


@dataclass(frozen=True, slots=True)
class ActionFunctionExecutionRequest:
    tenant_id: str
    run_id: str
    request_id: str
    actor_user_id: str
    roles: tuple[str, ...]
    token_scopes: tuple[str, ...]
    application_id: str | None
    client_id: str | None
    ontology_version_id: str
    function_api_name: str
    function_version: str
    inputs: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ActionFunctionExecutionResult:
    edit_batch: OntologyEditBatch
    external_execution_id: str
    result_hash: str
    provenance: Mapping[str, object]


class ActionFunctionExecutor(Protocol):
    """Run a function without granting it access to Ontology write ports."""

    @property
    def profile_name(self) -> str: ...

    def execute(self, request: ActionFunctionExecutionRequest) -> ActionFunctionExecutionResult: ...


class UnavailableActionFunctionExecutor:
    profile_name = "unavailable-action-function"

    def execute(self, request: ActionFunctionExecutionRequest) -> ActionFunctionExecutionResult:
        raise RuntimeError(f"action function executor unavailable for run {request.run_id}")
