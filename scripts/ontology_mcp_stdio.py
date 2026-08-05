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
_CONFIRM_ENV_NAME = "FOUNDRY_LITE_MCP_CONFIRM_TOOL"
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
    confirmed_tool_id: str | None = None

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
        headers=_headers(config.access_token, session_id, config.confirmed_tool_id),
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


def run_stdio(config: StdioProxyConfig, *, source: TextIO = sys.stdin, sink: TextIO = sys.stdout) -> int:
    session_id: str | None = None
    for raw_line in source:
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = _request_payload(line)
            response, session_id = forward_message(config, payload, session_id=session_id)
            if response is not None:
                sink.write(json.dumps(response, separators=(",", ":")) + "\n")
                sink.flush()
        except (json.JSONDecodeError, StdioProxyError, ValueError) as exc:
            _write_error(sink, str(exc))
    return 0


def _headers(access_token: str, session_id: str | None, confirmed_tool_id: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": "2025-06-18",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    if confirmed_tool_id:
        headers["X-FDE-Confirm-Tool"] = confirmed_tool_id
    return headers


def _response_payload(response: _HttpResponse) -> dict[str, object]:
    raw = response.read(_MAX_MESSAGE_BYTES + 1)
    if len(raw) > _MAX_MESSAGE_BYTES:
        raise StdioProxyError("MCP HTTP response exceeds the 1 MiB limit")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, Mapping):
        raise StdioProxyError("MCP HTTP response must be a JSON object")
    return {str(key): value for key, value in payload.items()}


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
    confirmed_tool_id = environ.get(_CONFIRM_ENV_NAME, "").strip() or None
    return StdioProxyConfig(args.base_url, args.application_id, token, args.timeout_seconds, plane, confirmed_tool_id)


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
