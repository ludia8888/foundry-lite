"""Crashable PostgreSQL Action-effect worker used by the live takeover proof."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from threading import Event
from typing import cast
from urllib.request import Request, urlopen

from foundry_lite.application.action_async_execution_types import ActionStepAttemptRow
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports.action_effect_executor import ActionEffectExecutionResult
from foundry_lite.application.services.action_effect_delivery_service import ActionBeforeEffectOutcomeUnknown
from foundry_lite.application.services.action_effect_runtime import effect_claim
from foundry_lite.domain.context import RequestContext
from foundry_lite.infrastructure.adapters.action_effect_executor import AllowlistedActionEffectExecutor
from foundry_lite.infrastructure.local_runtime import create_runtime_core_dependencies
from foundry_lite.security.tenant_context import tenant_context


def _runtime() -> FoundryLite:
    return FoundryLite(
        dependencies=create_runtime_core_dependencies(
            db_url=os.environ["FOUNDRY_LITE_DB_URL"],
            storage_root=os.environ["FOUNDRY_LITE_STORAGE_ROOT"],
        ),
        should_initialize_schema=False,
    )


def _write_marker(kind: str, receipt_id: str, worker_id: str) -> None:
    marker = Path(os.environ["FOUNDRY_LITE_LIVE_MARKER_DIR"]) / f"{kind}-{receipt_id}.json"
    marker.write_text(
        json.dumps({"pid": os.getpid(), "receiptId": receipt_id, "workerId": worker_id}),
        encoding="utf-8",
    )


def _claim_and_block(foundry: FoundryLite, tenant_id: str, receipt_id: str, worker_id: str) -> None:
    repository = foundry._services.action_effects.action_execution_repository
    with tenant_context(tenant_id), foundry.engine.begin() as transaction:
        receipt = repository.effect_receipt_by_id(
            transaction=transaction,
            tenant_id=tenant_id,
            receipt_id=receipt_id,
        )
        if receipt is None:
            raise RuntimeError("live Action effect receipt does not exist")
        claimed = repository.claim_effect_receipt(
            transaction=transaction,
            claim=effect_claim(receipt, worker_id, lease_seconds=2),
        )
        if claimed is None:
            raise RuntimeError("live Action effect receipt could not be claimed")
    _write_marker("claimed", receipt_id, worker_id)
    Event().wait(30)


def _provider_handler(receipt_id: str, worker_id: str):
    def deliver(request) -> ActionEffectExecutionResult:
        payload = json.dumps(
            {
                "actionRunId": request.action_run_id,
                "effectId": request.effect.effect_id,
            }
        ).encode("utf-8")
        http_request = Request(  # noqa: S310 - fixed loopback URL owned by the live test.
            os.environ["FOUNDRY_LITE_EFFECT_PROVIDER_URL"],
            data=payload,
            headers={"Content-Type": "application/json", "Idempotency-Key": request.idempotency_key},
            method="POST",
        )
        with urlopen(http_request, timeout=5) as response:  # noqa: S310 - fixed local test endpoint.
            body = json.loads(response.read().decode("utf-8"))
        if os.getenv("FOUNDRY_LITE_EFFECT_BLOCK_AFTER_DISPATCH") == "1":
            _write_marker("dispatched", receipt_id, worker_id)
            Event().wait(30)
        return ActionEffectExecutionResult(
            outcome="delivered",
            external_execution_id=str(body["id"]),
            response=body,
            network_evidence={"transport": "live-loopback-http"},
        )

    return deliver


def _deliver(foundry: FoundryLite, tenant_id: str, receipt_id: str, worker_id: str) -> None:
    adapter = AllowlistedActionEffectExecutor()
    adapter.register_target(
        "topic:live-provider",
        _provider_handler(receipt_id, worker_id),
        allowed_kinds=frozenset({"event"}),
    )
    foundry._services.action_effects.action_effect_executor = adapter
    with tenant_context(tenant_id):
        result = foundry._services.action_effects.deliver_pending(
            tenant_id=tenant_id,
            worker_id=worker_id,
            limit=10,
            lease_seconds=2,
        )
    sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")


def _execute_before(foundry: FoundryLite, tenant_id: str, run_id: str, worker_id: str) -> None:
    adapter = AllowlistedActionEffectExecutor()
    receipt_id = f"{run_id}:effect:before-provider"
    adapter.register_target(
        "connector:live/provider",
        _provider_handler(receipt_id, worker_id),
        allowed_kinds=frozenset({"webhook"}),
    )
    foundry._services.action_effects.action_effect_executor = adapter
    repository = foundry._services.action_effects.action_execution_repository
    with tenant_context(tenant_id), foundry.engine.begin() as transaction:
        row = repository.run_by_id(transaction=transaction, tenant_id=tenant_id, run_id=run_id)
    if row is None:
        raise RuntimeError("live before-effect Action run does not exist")
    attempt = cast(ActionStepAttemptRow, {"worker_id": worker_id})
    ctx = RequestContext(tenant_id=tenant_id, actor_user_id=worker_id, roles=("admin",))
    try:
        result = foundry._services.action_effects.execute_before(ctx, row, attempt)
    except ActionBeforeEffectOutcomeUnknown:
        result = {"status": "outcome_unknown"}
    sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")


def main() -> None:
    if len(sys.argv) != 5:
        raise SystemExit("usage: action_effect_worker_live.py MODE TENANT RECEIPT_OR_RUN WORKER")
    mode, tenant_id, coordinate, worker_id = sys.argv[1:]
    foundry = _runtime()
    if mode == "claim-block":
        _claim_and_block(foundry, tenant_id, coordinate, worker_id)
        return
    if mode == "deliver":
        _deliver(foundry, tenant_id, coordinate, worker_id)
        return
    if mode == "execute-before":
        _execute_before(foundry, tenant_id, coordinate, worker_id)
        return
    raise SystemExit(f"unsupported mode: {mode}")


if __name__ == "__main__":
    main()
