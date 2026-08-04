"""Background lease renewal for long-running Action function activities."""

from __future__ import annotations

import os
import threading
from datetime import UTC, datetime, timedelta

from foundry_lite.application.action_async_execution_types import ActionStepAttemptRow
from foundry_lite.application.ports.action_execution_repository import ActionExecutionRepository
from foundry_lite.application.ports.transaction_context import TransactionManager


class ActionAttemptHeartbeat:
    def __init__(
        self,
        transaction_manager: TransactionManager,
        repository: ActionExecutionRepository,
        attempt: ActionStepAttemptRow,
    ) -> None:
        self._transaction_manager = transaction_manager
        self._repository = repository
        self._attempt = attempt
        self._stop = threading.Event()
        self._is_lost = False
        self._thread: threading.Thread | None = None

    @property
    def is_lost(self) -> bool:
        return self._is_lost

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name=f"action-heartbeat:{self._attempt['id']}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _loop(self) -> None:
        duration = max(1, int(os.getenv("FOUNDRY_LITE_ACTION_STEP_LEASE_SECONDS", "300")))
        interval = max(0.25, duration / 3)
        while not self._stop.wait(interval):
            if not self._renew(duration):
                self._is_lost = True
                return

    def _renew(self, duration_seconds: int) -> bool:
        heartbeat = datetime.now(UTC)
        expires = heartbeat + timedelta(seconds=duration_seconds)
        with self._transaction_manager.begin() as transaction:
            row = self._repository.heartbeat_attempt(
                transaction=transaction,
                tenant_id=self._attempt["tenant_id"],
                attempt_id=self._attempt["id"],
                worker_id=self._attempt["worker_id"],
                lease_token=self._attempt["lease_token"],
                fencing_token=self._attempt["fencing_token"],
                lease_expires_at=_timestamp(expires),
                heartbeat_at=_timestamp(heartbeat),
            )
        return row is not None


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
