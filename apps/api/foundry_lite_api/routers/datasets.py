"""Dataset catalog, preview, and quality-contract routes."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from foundry_lite.application.ports import (
    DatasetInspectionPayload,
    DatasetQualityContractCheck,
    DatasetQualityContractCheckCreateResult,
    DatasetQualityContractCheckList,
    DatasetQualityContractVersion,
    DatasetQualityContractVersionCreateResult,
    DatasetQualityContractVersionList,
    DatasetQualityResultHistory,
    DatasetQualityResultSummary,
    DatasetRow,
    DatasetVersionRow,
    TabularRow,
)
from foundry_lite.domain.errors import FoundryLiteError

from foundry_lite_api import runtime
from foundry_lite_api.errors import _handle_error
from foundry_lite_api.request_context import _ctx
from foundry_lite_api.schemas import (
    DatasetQualityContractCheckCreateRequest,
    DatasetQualityContractCheckUpdateRequest,
    DatasetQualityContractVersionCreateRequest,
)

router = APIRouter()


@router.get("/api/datasets")
def list_datasets(request: Request) -> list[DatasetRow]:
    try:
        return runtime.foundry.datasets.list_datasets(ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/datasets/{namespace}/{name}/preview")
def preview_dataset(request: Request, namespace: str, name: str, limit: int = 100) -> list[TabularRow]:
    try:
        return runtime.foundry.datasets.preview(f"{namespace}.{name}", limit=limit, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/datasets/{namespace}/{name}/versions")
def list_dataset_versions(request: Request, namespace: str, name: str) -> list[DatasetVersionRow]:
    try:
        return runtime.foundry.datasets.list_versions(f"{namespace}.{name}", ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/datasets/{namespace}/{name}/inspect")
def inspect_dataset(request: Request, namespace: str, name: str, version: str = "latest") -> DatasetInspectionPayload:
    try:
        return runtime.foundry.datasets.inspect(f"{namespace}.{name}", version=version, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/datasets/{namespace}/{name}/quality-contract/checks")
def list_dataset_quality_contract_checks(
    request: Request,
    namespace: str,
    name: str,
) -> DatasetQualityContractCheckList:
    try:
        return runtime.foundry.datasets.list_quality_contract_checks(f"{namespace}.{name}", ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/datasets/{namespace}/{name}/quality-contract/contracts")
def list_dataset_quality_contract_versions(
    request: Request,
    namespace: str,
    name: str,
    limit: int = Query(default=20, ge=1, le=100),
) -> DatasetQualityContractVersionList:
    try:
        return runtime.foundry.datasets.list_data_contract_versions(
            f"{namespace}.{name}", limit=limit, ctx=_ctx(request)
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/datasets/{namespace}/{name}/quality-contract/contracts")
def create_dataset_quality_contract_version(
    request: Request,
    namespace: str,
    name: str,
    payload: DatasetQualityContractVersionCreateRequest,
) -> DatasetQualityContractVersionCreateResult:
    try:
        return runtime.foundry.datasets.create_data_contract_version(
            f"{namespace}.{name}",
            contract_key=payload.contract_key,
            owner_user_id=payload.owner_user_id,
            description=payload.description,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/datasets/{namespace}/{name}/quality-contract/contracts/{contract_version_id}")
def get_dataset_quality_contract_version(
    request: Request,
    namespace: str,
    name: str,
    contract_version_id: str,
) -> DatasetQualityContractVersion:
    try:
        return runtime.foundry.datasets.get_data_contract_version(
            f"{namespace}.{name}",
            contract_version_id,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/datasets/{namespace}/{name}/quality-contract/contracts/{contract_version_id}/activate")
def activate_dataset_quality_contract_version(
    request: Request,
    namespace: str,
    name: str,
    contract_version_id: str,
) -> DatasetQualityContractVersion:
    try:
        return runtime.foundry.datasets.activate_data_contract_version(
            f"{namespace}.{name}",
            contract_version_id,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/datasets/{namespace}/{name}/quality-contract/results")
def list_dataset_quality_results(
    request: Request,
    namespace: str,
    name: str,
    limit: int = Query(default=50, ge=1, le=100),
) -> DatasetQualityResultHistory:
    try:
        return runtime.foundry.datasets.list_quality_results(f"{namespace}.{name}", limit=limit, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/datasets/{namespace}/{name}/quality-contract/results/summary")
def get_dataset_quality_result_summary(
    request: Request,
    namespace: str,
    name: str,
    latest_limit: int = Query(default=5, ge=1, le=20),
) -> DatasetQualityResultSummary:
    try:
        return runtime.foundry.datasets.get_quality_result_summary(
            f"{namespace}.{name}",
            latest_limit=latest_limit,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/datasets/{namespace}/{name}/quality-contract/checks")
def create_dataset_quality_contract_check(
    request: Request,
    namespace: str,
    name: str,
    payload: DatasetQualityContractCheckCreateRequest,
) -> DatasetQualityContractCheckCreateResult:
    try:
        return runtime.foundry.datasets.create_quality_contract_check(
            f"{namespace}.{name}",
            config=payload.config,
            severity=payload.severity,
            enabled=payload.enabled,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.patch("/api/datasets/{namespace}/{name}/quality-contract/checks/{check_id}")
def update_dataset_quality_contract_check(
    request: Request,
    namespace: str,
    name: str,
    check_id: str,
    payload: DatasetQualityContractCheckUpdateRequest,
) -> DatasetQualityContractCheck:
    try:
        return runtime.foundry.datasets.update_quality_contract_check(
            f"{namespace}.{name}",
            check_id,
            config=payload.config,
            severity=payload.severity,
            enabled=payload.enabled,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc
