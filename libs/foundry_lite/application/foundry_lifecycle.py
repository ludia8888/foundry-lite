"""Resource ownership and shutdown state for the Foundry composition root."""

from __future__ import annotations

import sys
from collections.abc import Generator
from contextlib import contextmanager
from threading import Lock

from foundry_lite.application.ports.stream_adapter import StreamAdapter
from foundry_lite.security.tenant_context import tenant_context


class FoundryRuntimeLifecycle:
    """Close owned resources once while preserving the primary workload error."""

    def __init__(
        self,
        *,
        is_stream_adapter_owned: bool,
        is_engine_owned: bool,
        is_orchestrator_owned: bool,
    ) -> None:
        self._lock = Lock()
        self._is_stream_adapter_owned = is_stream_adapter_owned
        self._is_engine_owned = is_engine_owned
        self._is_orchestrator_owned = is_orchestrator_owned
        self._is_pipeline_orchestrator_closed = False
        self._is_action_orchestrator_closed = False
        self._is_stream_closed = False
        self._is_engine_closed = False
        self._is_closed = False

    @staticmethod
    @contextmanager
    def bootstrap_tenant_context(tenant_id: str) -> Generator[None]:
        """Bind one explicit tenant while idempotent runtime bootstrap executes."""

        with tenant_context(tenant_id):
            yield

    def close(
        self,
        *,
        stream_adapter: StreamAdapter,
        engine: object,
        pipeline_orchestrator: object,
        action_orchestrator: object,
        should_close_stream: bool | None,
        primary_error: BaseException | None,
    ) -> None:
        active_error = primary_error or sys.exception()
        with self._lock:
            if self._is_closed:
                return
            should_close = self._is_stream_adapter_owned if should_close_stream is None else should_close_stream
            errors = self._close_resources(
                pipeline_orchestrator,
                action_orchestrator,
                stream_adapter,
                engine,
                should_close_stream=should_close,
                should_defer_on_orchestrator_error=True,
            )
            self._is_closed = self._resources_are_closed()
            self._propagate_cleanup_errors(errors, active_error)

    def close_failed_initialization(
        self,
        *,
        stream_adapter: StreamAdapter,
        engine: object,
        pipeline_orchestrator: object,
        action_orchestrator: object,
        primary_error: BaseException,
    ) -> None:
        errors = self._close_resources(
            pipeline_orchestrator,
            action_orchestrator,
            stream_adapter,
            engine,
            should_close_stream=self._is_stream_adapter_owned,
            should_defer_on_orchestrator_error=False,
        )
        self._is_closed = self._resources_are_closed()
        self._attach_cleanup_notes(primary_error, errors)

    def _close_resources(
        self,
        pipeline_orchestrator: object,
        action_orchestrator: object,
        stream_adapter: StreamAdapter,
        engine: object,
        *,
        should_close_stream: bool,
        should_defer_on_orchestrator_error: bool,
    ) -> list[tuple[str, BaseException]]:
        orchestrator_attempts = (
            ("pipeline orchestrator", self._close_orchestrator(pipeline_orchestrator, is_pipeline=True)),
            ("action orchestrator", self._close_orchestrator(action_orchestrator, is_pipeline=False)),
        )
        orchestrator_errors = [item for item in orchestrator_attempts if item[1] is not None]
        if orchestrator_errors and should_defer_on_orchestrator_error:
            return [(label, error) for label, error in orchestrator_errors if error is not None]
        resource_attempts = (
            ("stream", self._close_stream(stream_adapter, should_close=should_close_stream)),
            ("database", self._close_engine(engine)),
        )
        return [(label, error) for label, error in (*orchestrator_errors, *resource_attempts) if error is not None]

    def _close_orchestrator(self, orchestrator: object, *, is_pipeline: bool) -> BaseException | None:
        state_attribute = "_is_pipeline_orchestrator_closed" if is_pipeline else "_is_action_orchestrator_closed"
        if not self._is_orchestrator_owned or getattr(self, state_attribute):
            return None
        try:
            close = getattr(orchestrator, "close", None)
            if callable(close):
                close()
        except BaseException as exc:  # noqa: BLE001 - later resources must still close.
            return exc
        setattr(self, state_attribute, True)
        return None

    def _close_stream(self, stream_adapter: StreamAdapter, *, should_close: bool) -> BaseException | None:
        if not should_close or self._is_stream_closed:
            return None
        try:
            stream_adapter.close()
        except BaseException as exc:  # noqa: BLE001 - engine cleanup must still run.
            return exc
        self._is_stream_closed = True
        return None

    def _close_engine(self, engine: object) -> BaseException | None:
        if not self._is_engine_owned or self._is_engine_closed:
            return None
        try:
            dispose = getattr(engine, "dispose", None)
            if callable(dispose):
                dispose()
        except BaseException as exc:  # noqa: BLE001 - caller receives the typed failure.
            return exc
        self._is_engine_closed = True
        return None

    def _resources_are_closed(self) -> bool:
        are_orchestrators_settled = not self._is_orchestrator_owned or (
            self._is_pipeline_orchestrator_closed and self._is_action_orchestrator_closed
        )
        is_stream_settled = not self._is_stream_adapter_owned or self._is_stream_closed
        is_engine_settled = not self._is_engine_owned or self._is_engine_closed
        return are_orchestrators_settled and is_stream_settled and is_engine_settled

    @staticmethod
    def _attach_cleanup_notes(
        primary_error: BaseException,
        errors: list[tuple[str, BaseException]],
    ) -> None:
        for label, error in errors:
            primary_error.add_note(f"{label} cleanup also failed ({type(error).__name__})")

    @staticmethod
    def _propagate_cleanup_errors(
        errors: list[tuple[str, BaseException]],
        primary_error: BaseException | None,
    ) -> None:
        if not errors:
            return
        cleanup_error = errors[0][1]
        for label, error in errors[1:]:
            cleanup_error.add_note(f"{label} cleanup also failed ({type(error).__name__})")
        if primary_error is not None:
            primary_error.add_note(f"runtime cleanup also failed ({type(cleanup_error).__name__})")
            return
        raise cleanup_error
