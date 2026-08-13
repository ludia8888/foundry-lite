"""Bounded file-RPC bridge from a networkless function sandbox to governed OSDK reads."""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from pathlib import Path

from foundry_lite.application.ports.code_execution import FunctionQueryExecutor

MAX_FUNCTION_QUERY_COUNT = 512
MAX_FUNCTION_QUERY_REQUEST_BYTES = 256 * 1024
MAX_FUNCTION_QUERY_RESPONSE_BYTES = 16 * 1024 * 1024


class FunctionQueryBridge:
    """Serve exact-nonce request files while the isolated container is running."""

    def __init__(self, root: Path, nonce: str, executor: FunctionQueryExecutor | None) -> None:
        self.root = root
        self.nonce = nonce
        self.executor = executor
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: BaseException | None = None
        self._processed: set[str] = set()

    def __enter__(self) -> FunctionQueryBridge:
        self.root.mkdir(mode=0o777, parents=True, exist_ok=True)
        self.root.chmod(0o777)
        self._thread = threading.Thread(target=self._serve, name="function-query-bridge", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)

    def raise_if_failed(self) -> None:
        if self._error is not None:
            raise self._error

    def _serve(self) -> None:
        while not self._stop.is_set():
            for path in sorted(self.root.glob(f"request-{self.nonce}-*.json")):
                if path.name not in self._processed:
                    self._serve_one(path)
            self._stop.wait(0.005)

    def _serve_one(self, path: Path) -> None:
        self._processed.add(path.name)
        response_path = self.root / path.name.replace("request-", "response-", 1)
        try:
            response = self._execute(path)
        except BaseException as exc:  # noqa: BLE001 - re-raised on the execution owner thread
            self._error = exc
            response = {"status": "failed", "failureType": type(exc).__name__}
        self._write_response(response_path, response)

    def _execute(self, path: Path) -> Mapping[str, object]:
        if len(self._processed) > MAX_FUNCTION_QUERY_COUNT:
            raise ValueError("function ObjectSet query count exceeded")
        raw = path.read_bytes()
        if len(raw) > MAX_FUNCTION_QUERY_REQUEST_BYTES:
            raise ValueError("function ObjectSet query request exceeded its byte limit")
        request = json.loads(raw)
        if not isinstance(request, Mapping):
            raise ValueError("function ObjectSet query request must be an object")
        if self.executor is None:
            raise ValueError("function ObjectSet query executor is unavailable")
        return {"status": "succeeded", "result": dict(self.executor(request))}

    def _write_response(self, path: Path, response: Mapping[str, object]) -> None:
        encoded = json.dumps(response, sort_keys=True).encode("utf-8")
        if len(encoded) > MAX_FUNCTION_QUERY_RESPONSE_BYTES:
            self._error = ValueError("function ObjectSet query response exceeded its byte limit")
            encoded = json.dumps({"status": "failed", "failureType": "response_limit"}).encode()
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(encoded)
        temporary.chmod(0o444)
        temporary.replace(path)
