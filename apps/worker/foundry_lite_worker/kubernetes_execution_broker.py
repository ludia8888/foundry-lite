"""Minimal authenticated HTTP boundary for the restricted Kubernetes execution broker."""

from __future__ import annotations

import json
import os
import secrets
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from foundry_lite.infrastructure.kubernetes_execution_broker import (
    KubernetesExecutionBroker,
    KubernetesExecutionBrokerConfig,
)

_MAX_REQUEST_BYTES = 128 * 1024


def create_app(
    *,
    broker: KubernetesExecutionBroker | None = None,
    bearer_token: str | None = None,
) -> FastAPI:
    resolved_broker = broker or _broker_from_env()
    resolved_token = (
        bearer_token if bearer_token is not None else os.getenv("FOUNDRY_LITE_CODE_EXECUTION_BROKER_TOKEN", "")
    )
    if len(resolved_token) < 32:
        raise ValueError("Kubernetes execution broker token must contain at least 32 characters")
    app = FastAPI(title="Foundry-lite execution broker", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/executions")
    async def execute(request: Request) -> JSONResponse:
        if not _is_authorized(request, resolved_token):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            payload = await _json_body(request)
            result = resolved_broker.execute(payload)
            return JSONResponse(result.to_payload())
        except ValueError:
            return JSONResponse({"error": "invalid_execution_request"}, status_code=422)
        except RuntimeError:
            return JSONResponse({"error": "execution_broker_unavailable"}, status_code=503)

    @app.delete("/v1/executions/{name}")
    async def cleanup(name: str, request: Request) -> JSONResponse:
        if not _is_authorized(request, resolved_token):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            is_deleted = resolved_broker.cleanup(name)
        except ValueError:
            return JSONResponse({"error": "invalid_execution_name"}, status_code=422)
        except RuntimeError:
            return JSONResponse({"error": "execution_broker_unavailable"}, status_code=503)
        return JSONResponse({"name": name, "deleted": is_deleted}, status_code=200 if is_deleted else 503)

    return app


def _broker_from_env() -> KubernetesExecutionBroker:
    return KubernetesExecutionBroker(
        KubernetesExecutionBrokerConfig(
            namespace=os.environ["FOUNDRY_LITE_KUBERNETES_EXECUTION_NAMESPACE"],
            pvc_name=os.environ["FOUNDRY_LITE_KUBERNETES_EXECUTION_PVC"],
            shared_workspace_root=Path(
                os.getenv(
                    "FOUNDRY_LITE_CODE_EXECUTION_WORKSPACE_ROOT",
                    "/var/data/code-execution-workspaces",
                )
            ),
            pvc_mount_root=Path("/var/data"),
        )
    )


async def _json_body(request: Request) -> Mapping[str, object]:
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > _MAX_REQUEST_BYTES:
            raise ValueError("execution request is too large")
        raw.extend(chunk)
    try:
        value = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("execution request must be JSON") from exc
    if not isinstance(value, Mapping):
        raise ValueError("execution request must be an object")
    return cast(Mapping[str, object], value)


def _is_authorized(request: Request, expected_token: str) -> bool:
    provided = request.headers.get("authorization", "")
    prefix = "Bearer "
    return provided.startswith(prefix) and secrets.compare_digest(provided.removeprefix(prefix), expected_token)


def main() -> None:
    uvicorn.run(
        create_app(),
        host="0.0.0.0",  # nosec B104 - cluster Service needs pod-wide bind; remove if it becomes host-exposed.
        port=int(os.getenv("PORT", "8080")),
        access_log=False,
        server_header=False,
    )


if __name__ == "__main__":
    main()
