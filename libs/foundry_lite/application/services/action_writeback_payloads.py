"""Application service helpers for action writeback payloads workflows."""

from __future__ import annotations

from foundry_lite.application.ports.action_repository import ActionWritebackPayload
from foundry_lite.application.primitives import MOCK_WRITEBACK_CONNECTOR
from foundry_lite.domain.errors import ExternalSystemError


def writeback_request(
    *,
    connector_id: str,
    is_simulated: bool,
    idempotency_key: str,
    request_hash: str,
    external_writeback_uri: str | None = None,
) -> ActionWritebackPayload:
    request = {
        "connector": connector_id,
        "simulated": is_simulated,
        "networkCall": not is_simulated,
        "idempotency_key": idempotency_key,
        "request_hash": request_hash,
    }
    if external_writeback_uri is not None:
        request["externalWritebackUri"] = external_writeback_uri
    return request


def writeback_failure_error(*, idempotency_key: str, request_hash: str) -> ExternalSystemError:
    return ExternalSystemError(
        "mock before-commit writeback failed",
        details={
            "connector": MOCK_WRITEBACK_CONNECTOR,
            "simulated": True,
            "idempotency_key": idempotency_key,
            "request_hash": request_hash,
        },
    )


def retryable_details(
    *,
    connector_id: str,
    is_simulated: bool,
    idempotency_key: str,
    request_hash: str,
) -> ActionWritebackPayload:
    return {
        "state": "RETRYABLE",
        "status_code": 503,
        "connector": connector_id,
        "simulated": is_simulated,
        "external_operation_id": None,
        "idempotency_key": idempotency_key,
        "request_hash": request_hash,
        "remote_resource_id": None,
        "external_system_changed": False,
        "last_observed_status": "not_changed",
        "retryable": True,
        "retry_after_seconds": 60,
        "reconciliation_deadline": None,
    }


def outcome_unknown_details(
    *,
    connector_id: str,
    is_simulated: bool,
    idempotency_key: str,
    request_hash: str,
    reconciliation_deadline: str,
    remote_resource_id: str | None,
) -> ActionWritebackPayload:
    return {
        "state": "OUTCOME_UNKNOWN",
        "connector": connector_id,
        "simulated": is_simulated,
        "external_operation_id": _operation_id(connector_id, is_simulated, idempotency_key),
        "idempotency_key": idempotency_key,
        "request_hash": request_hash,
        "remote_resource_id": remote_resource_id,
        "last_observed_status": "unknown",
        "reconciliation_deadline": reconciliation_deadline,
    }


def compensation_required_details(
    *,
    connector_id: str,
    is_simulated: bool,
    idempotency_key: str,
    request_hash: str,
    reconciliation_deadline: str,
    remote_resource_id: str | None,
) -> ActionWritebackPayload:
    resolved_remote_resource_id = (
        f"mock-resource-{idempotency_key}" if is_simulated and remote_resource_id is None else remote_resource_id
    )
    return {
        "state": "COMPENSATION_REQUIRED",
        "connector": connector_id,
        "simulated": is_simulated,
        "external_operation_id": _operation_id(connector_id, is_simulated, idempotency_key),
        "idempotency_key": idempotency_key,
        "request_hash": request_hash,
        "remote_resource_id": resolved_remote_resource_id,
        "last_observed_status": "succeeded",
        "compensation_action_type": "mock_reverse_writeback" if is_simulated else f"{connector_id}_reverse_writeback",
        "reconciliation_deadline": reconciliation_deadline,
    }


def _operation_id(connector_id: str, is_simulated: bool, idempotency_key: str) -> str:
    prefix = "mock-op" if is_simulated else f"{connector_id}-op"
    return f"{prefix}-{idempotency_key}"
