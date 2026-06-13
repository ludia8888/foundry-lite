from __future__ import annotations

import json
from collections.abc import Mapping
from typing import cast
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from foundry_lite.application.ports.connector_adapter import (
    ConnectorRateLimitedError,
    ConnectorSnapshot,
    ConnectorSnapshotRequest,
    RestAuthConfig,
    RestPaginationConfig,
    RestSourceConfig,
)
from foundry_lite.domain.errors import ValidationFailed


class RestPullConnectorAdapter:
    """HTTP JSON REST connector that returns one cursor page as a snapshot."""

    profile_name = "rest-pull-connector"

    def snapshot(self, request: ConnectorSnapshotRequest) -> ConnectorSnapshot:
        config = _required_rest_config(request.rest)
        payload = _load_json(_http_get(_request_url(config, request.cursor), _auth_headers(config.auth)))
        rows = _rows_from_payload(payload, config.pagination)
        cursor = _next_cursor(payload, config.pagination)
        return ConnectorSnapshot(
            connector_name=request.connector_name,
            resource_name=request.resource_name,
            rows=tuple(rows),
            schema={"columns": _schema_columns(config, rows)},
            cursor=cursor,
            source_watermark=request.request_id,
        )


def _required_rest_config(config: RestSourceConfig | None) -> RestSourceConfig:
    if config is None:
        raise ValidationFailed("REST connector requires source config")
    if not config.base_url or not config.resource_path:
        raise ValidationFailed("REST connector requires baseUrl and resourcePath")
    return config


def _request_url(config: RestSourceConfig, cursor: Mapping[str, object] | None) -> str:
    base = config.base_url.rstrip("/")
    path = config.resource_path.lstrip("/")
    url = f"{base}/{path}"
    cursor_value = _cursor_value(cursor, config.pagination)
    if cursor_value is None:
        return url
    split = urlsplit(url)
    query = dict(parse_qsl(split.query, keep_blank_values=True))
    query[config.pagination.cursor_query_param] = cursor_value
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), split.fragment))


def _cursor_value(cursor: Mapping[str, object] | None, pagination: RestPaginationConfig) -> str | None:
    if cursor is None:
        return None
    value = cursor.get(pagination.cursor_key)
    if value is None:
        return None
    return str(value)


def _auth_headers(auth: RestAuthConfig) -> dict[str, str]:
    if auth.mode == "none":
        return {}
    if auth.mode == "bearer" and auth.token:
        return {"Authorization": f"Bearer {auth.token}"}
    if auth.mode == "header" and auth.header_name and auth.header_value:
        return {auth.header_name: auth.header_value}
    raise ValidationFailed("REST connector auth config is incomplete")


def _http_get(url: str, headers: Mapping[str, str]) -> bytes:
    safe_url = _validated_http_url(url)
    request = Request(safe_url, headers=dict(headers), method="GET")
    try:
        with urlopen(request, timeout=5) as response:  # noqa: S310  # nosec B310
            return cast(bytes, response.read())
    except HTTPError as exc:
        if exc.code == 429:
            retry_after = exc.headers.get("Retry-After")
            raise ConnectorRateLimitedError(f"REST connector rate limited; retry_after={retry_after}") from exc
        raise ValidationFailed("REST connector request failed", details={"status": exc.code, "url": url}) from exc


def _validated_http_url(url: str) -> str:
    split = urlsplit(url)
    if split.scheme not in {"http", "https"} or not split.netloc:
        raise ValidationFailed("REST connector URL must use http or https", details={"url": url})
    return url


def _load_json(body: bytes) -> Mapping[str, object]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationFailed("REST connector returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ValidationFailed("REST connector response must be a JSON object")
    return payload


def _rows_from_payload(payload: Mapping[str, object], pagination: RestPaginationConfig) -> list[Mapping[str, object]]:
    value = _json_path(payload, pagination.items_path)
    if not isinstance(value, list):
        raise ValidationFailed("REST connector items path must be a list")
    rows: list[Mapping[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValidationFailed("REST connector item must be an object")
        rows.append({str(key): value for key, value in item.items()})
    return rows


def _next_cursor(payload: Mapping[str, object], pagination: RestPaginationConfig) -> Mapping[str, object] | None:
    value = _json_path(payload, pagination.next_cursor_path)
    if value is None or value == "":
        return None
    return {pagination.cursor_key: value}


def _json_path(payload: Mapping[str, object], path: str) -> object:
    current: object = payload
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _schema_columns(config: RestSourceConfig, rows: list[Mapping[str, object]]) -> list[str]:
    if config.schema_columns:
        return list(config.schema_columns)
    if not rows:
        return []
    return [str(key) for key in rows[0]]
