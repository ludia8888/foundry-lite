#!/usr/bin/env python3
"""Local stdio proxy for governed Ontology or Builder MCP HTTP servers."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol, TextIO, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

_AUTH_ENV_NAME = "FOUNDRY_LITE_MCP_ACCESS_TOKEN"
_MAX_MESSAGE_BYTES = 1_048_576
_DEFAULT_TIMEOUT_SECONDS = 30.0
_DEFAULT_OPENER = cast("_Opener", urlopen)


class _HttpResponse(Protocol):
    status: int
    headers: Mapping[str, str]

    def read(self, amount: int = -1) -> bytes: ...

    def __enter__(self) -> _HttpResponse: ...

    def __exit__(self, *args: object) -> None: ...


class _Opener(Protocol):
    def __call__(self, request: Request, *, timeout: float) -> _HttpResponse: ...


@dataclass(frozen=True, slots=True)
class StdioProxyConfig:
    base_url: str
    application_id: str
    access_token: str = field(repr=False)
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
    plane: str = "ontology"

    @property
    def endpoint(self) -> str:
        return f"{self.base_url.rstrip('/')}/mcp/{self.plane}/{quote(self.application_id, safe='')}"


class StdioProxyError(RuntimeError):
    """Safe local proxy failure that never embeds the bearer token."""


def forward_message(
    config: StdioProxyConfig,
    payload: Mapping[str, object],
    *,
    session_id: str | None,
    opener: _Opener = _DEFAULT_OPENER,
) -> tuple[dict[str, object] | None, str | None]:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if len(body) > _MAX_MESSAGE_BYTES:
        raise StdioProxyError("MCP stdio message exceeds the 1 MiB limit")
    request = Request(
        config.endpoint,
        data=body,
        headers=_headers(config.access_token, session_id),
        method="POST",
    )
    try:
        with opener(request, timeout=config.timeout_seconds) as response:
            next_session = response.headers.get("Mcp-Session-Id") or session_id
            if response.status == 202:
                return None, next_session
            return _response_payload(response), next_session
    except HTTPError as exc:
        raise StdioProxyError(f"MCP HTTP request failed with status {exc.code}") from exc
    except URLError as exc:
        raise StdioProxyError("MCP server is unreachable") from exc


def forward_notifications(
    config: StdioProxyConfig,
    session_id: str,
    *,
    last_event_id: str | None,
    opener: _Opener = _DEFAULT_OPENER,
) -> tuple[list[dict[str, object]], str | None]:
    headers = _headers(config.access_token, session_id)
    headers["Accept"] = "text/event-stream"
    if last_event_id:
        headers["Last-Event-ID"] = last_event_id
    request = Request(config.endpoint, headers=headers, method="GET")
    try:
        with opener(request, timeout=config.timeout_seconds) as response:
            if response.status != 200:
                raise StdioProxyError(f"MCP SSE request failed with status {response.status}")
            return _sse_payloads(response, last_event_id)
    except HTTPError as exc:
        raise StdioProxyError(f"MCP SSE request failed with status {exc.code}") from exc
    except URLError as exc:
        raise StdioProxyError("MCP SSE server is unreachable") from exc


def close_session(
    config: StdioProxyConfig,
    session_id: str,
    *,
    opener: _Opener = _DEFAULT_OPENER,
) -> None:
    request = Request(
        config.endpoint,
        headers=_headers(config.access_token, session_id),
        method="DELETE",
    )
    try:
        with opener(request, timeout=config.timeout_seconds) as response:
            if response.status < 200 or response.status >= 300:
                raise StdioProxyError(f"MCP session DELETE failed with status {response.status}")
    except HTTPError as exc:
        raise StdioProxyError(f"MCP session DELETE failed with status {exc.code}") from exc
    except URLError as exc:
        raise StdioProxyError("MCP session DELETE server is unreachable") from exc


def run_stdio(
    config: StdioProxyConfig,
    *,
    source: TextIO = sys.stdin,
    sink: TextIO = sys.stdout,
    opener: _Opener = _DEFAULT_OPENER,
) -> int:
    session_id: str | None = None
    last_event_id: str | None = None
    for raw_line in source:
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = _request_payload(line)
            response, session_id = forward_message(config, payload, session_id=session_id, opener=opener)
            if response is not None:
                _write_payload(sink, response)
            if session_id:
                notifications, last_event_id = forward_notifications(
                    config,
                    session_id,
                    last_event_id=last_event_id,
                    opener=opener,
                )
                for notification in notifications:
                    _write_payload(sink, notification)
        except (json.JSONDecodeError, StdioProxyError, ValueError) as exc:
            _write_error(sink, str(exc))
    if session_id:
        try:
            close_session(config, session_id, opener=opener)
        except StdioProxyError as exc:
            _write_error(sink, str(exc))
            return 1
    return 0


def _headers(access_token: str, session_id: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": "2025-06-18",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    return headers


def _response_payload(response: _HttpResponse) -> dict[str, object]:
    raw = response.read(_MAX_MESSAGE_BYTES + 1)
    if len(raw) > _MAX_MESSAGE_BYTES:
        raise StdioProxyError("MCP HTTP response exceeds the 1 MiB limit")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, Mapping):
        raise StdioProxyError("MCP HTTP response must be a JSON object")
    return {str(key): value for key, value in payload.items()}


def _sse_payloads(
    response: _HttpResponse,
    last_event_id: str | None,
) -> tuple[list[dict[str, object]], str | None]:
    raw = response.read(_MAX_MESSAGE_BYTES + 1)
    if len(raw) > _MAX_MESSAGE_BYTES:
        raise StdioProxyError("MCP SSE response exceeds the 1 MiB limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StdioProxyError("MCP SSE response must be UTF-8") from exc
    payloads: list[dict[str, object]] = []
    cursor = last_event_id
    for event_id, data in _sse_events(text):
        if not data or not _is_newer_event_id(event_id, cursor):
            continue
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            raise StdioProxyError("MCP SSE data must be a JSON object") from exc
        if not isinstance(payload, Mapping):
            raise StdioProxyError("MCP SSE data must be a JSON object")
        payloads.append({str(key): value for key, value in payload.items()})
        cursor = event_id or cursor
    return payloads, cursor


def _sse_events(text: str) -> list[tuple[str | None, str]]:
    events: list[tuple[str | None, str]] = []
    event_id: str | None = None
    data_lines: list[str] = []
    for line in (*text.splitlines(), ""):
        if line == "":
            if data_lines:
                events.append((event_id, "\n".join(data_lines)))
            event_id = None
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        normalized = value[1:] if value.startswith(" ") else value
        if field == "id":
            event_id = normalized
        elif field == "data":
            data_lines.append(normalized)
    return events


def _is_newer_event_id(candidate: str | None, previous: str | None) -> bool:
    if candidate is None or previous is None:
        return True
    candidate_prefix, candidate_sequence = _event_coordinate(candidate)
    previous_prefix, previous_sequence = _event_coordinate(previous)
    if candidate_prefix == previous_prefix and candidate_sequence is not None and previous_sequence is not None:
        return candidate_sequence > previous_sequence
    return candidate != previous


def _event_coordinate(event_id: str) -> tuple[str, int | None]:
    prefix, separator, raw_sequence = event_id.rpartition(":")
    if not separator:
        return event_id, None
    try:
        return prefix, int(raw_sequence)
    except ValueError:
        return event_id, None


def _request_payload(line: str) -> dict[str, object]:
    if len(line.encode("utf-8")) > _MAX_MESSAGE_BYTES:
        raise StdioProxyError("MCP stdio message exceeds the 1 MiB limit")
    payload = json.loads(line)
    is_valid = (
        isinstance(payload, Mapping) and payload.get("jsonrpc") == "2.0" and isinstance(payload.get("method"), str)
    )
    if not is_valid:
        raise ValueError("MCP stdio input must be a JSON-RPC 2.0 request")
    return {str(key): value for key, value in payload.items()}


def _write_error(sink: TextIO, message: str) -> None:
    payload = {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": message}}
    _write_payload(sink, payload)


def _write_payload(sink: TextIO, payload: Mapping[str, object]) -> None:
    sink.write(json.dumps(payload, separators=(",", ":")) + "\n")
    sink.flush()


def _config(args: argparse.Namespace, environ: Mapping[str, str]) -> StdioProxyConfig:
    token = environ.get(_AUTH_ENV_NAME, "")
    if not token:
        raise StdioProxyError(f"{_AUTH_ENV_NAME} must contain a short-lived OAuth access token")
    if args.timeout_seconds <= 0 or args.timeout_seconds > 300:
        raise StdioProxyError("timeout-seconds must be between 0 and 300")
    plane = getattr(args, "plane", "ontology")
    if plane not in {"ontology", "builder"}:
        raise StdioProxyError("plane must be ontology or builder")
    return StdioProxyConfig(args.base_url, args.application_id, token, args.timeout_seconds, plane)


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Proxy MCP stdio JSON-RPC to Foundry-lite MCP HTTP")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--application-id", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=_DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--plane", choices=("ontology", "builder"), default="ontology")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run_stdio(_config(_parse_args(argv or sys.argv[1:]), os.environ))
    except StdioProxyError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
