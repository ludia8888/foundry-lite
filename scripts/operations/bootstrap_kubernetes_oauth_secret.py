"""Create one immutable OAuth signing Secret and safely reconcile retries."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from http.client import HTTPException, HTTPSConnection
from pathlib import Path
from typing import Literal, Protocol

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")
_MAX_RESPONSE_BYTES = 1024 * 1024
_SAFE_ERROR_CODES = frozenset(
    {
        "kubernetes_api_configuration_invalid",
        "oauth_secret_create_outcome_unknown",
        "oauth_secret_name_invalid",
        "oauth_secret_read_failed",
        "oauth_secret_timeout_invalid",
        "oauth_secret_validation_failed",
        "secret_api_path_invalid",
        "secret_api_request_failed",
        "secret_api_response_too_large",
        "service_account_token_missing",
    }
)


@dataclass(frozen=True, slots=True)
class OAuthSecretBootstrapConfig:
    namespace: str
    secret_name: str
    secret_key: str
    timeout_seconds: float = 10.0


@dataclass(frozen=True, slots=True)
class SecretApiResponse:
    status_code: int
    body: bytes


class SecretApi(Protocol):
    def request(self, method: Literal["GET", "POST"], path: str, body: bytes | None) -> SecretApiResponse: ...


class InClusterSecretApi:
    def __init__(self, *, timeout_seconds: float) -> None:
        host = os.getenv("KUBERNETES_SERVICE_HOST", "").strip()
        port = int(os.getenv("KUBERNETES_SERVICE_PORT_HTTPS", "443"))
        if not host or port < 1 or port > 65535:
            raise ValueError("kubernetes_api_configuration_invalid")
        self._host = host
        self._port = port
        self._timeout_seconds = timeout_seconds
        self._token_path = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
        ca_path = Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
        self._context = ssl.create_default_context(cafile=str(ca_path))

    def request(self, method: Literal["GET", "POST"], path: str, body: bytes | None) -> SecretApiResponse:
        if not path.startswith("/api/v1/namespaces/"):
            raise RuntimeError("secret_api_path_invalid")
        token = self._token_path.read_text(encoding="utf-8").strip()
        if not token:
            raise RuntimeError("service_account_token_missing")
        connection = HTTPSConnection(self._host, self._port, context=self._context, timeout=self._timeout_seconds)
        try:
            connection.request(
                method,
                path,
                body=body,
                headers={
                    "accept": "application/json",
                    "authorization": f"Bearer {token}",
                    "content-type": "application/json",
                    "user-agent": "Foundry-lite/oauth-secret-bootstrap",
                },
            )
            response = connection.getresponse()
            payload = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(payload) > _MAX_RESPONSE_BYTES:
                raise RuntimeError("secret_api_response_too_large")
            return SecretApiResponse(response.status, payload)
        except (HTTPException, OSError, TimeoutError, UnicodeError) as exc:
            raise RuntimeError("secret_api_request_failed") from exc
        finally:
            connection.close()


def ensure_oauth_secret(
    config: OAuthSecretBootstrapConfig,
    *,
    api: SecretApi | None = None,
) -> Literal["created", "existing"]:
    _validate_config(config)
    secret_api = api or InClusterSecretApi(timeout_seconds=config.timeout_seconds)
    path = _secret_path(config)
    current = secret_api.request("GET", path, None)
    if current.status_code == 200:
        _validate_existing_secret(config, current.body)
        return "existing"
    if current.status_code != 404:
        raise RuntimeError("oauth_secret_read_failed")
    created = secret_api.request("POST", _secret_collection_path(config.namespace), _secret_payload(config))
    if created.status_code == 201:
        _validate_existing_secret(config, created.body)
        return "created"
    if created.status_code != 409:
        raise RuntimeError("oauth_secret_create_outcome_unknown")
    reconciled = secret_api.request("GET", path, None)
    if reconciled.status_code != 200:
        raise RuntimeError("oauth_secret_create_outcome_unknown")
    _validate_existing_secret(config, reconciled.body)
    return "existing"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create or verify the immutable shared OAuth signing Secret.")
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--secret-name", required=True)
    parser.add_argument("--secret-key", required=True)
    args = parser.parse_args(argv)
    config = OAuthSecretBootstrapConfig(args.namespace, args.secret_name, args.secret_key)
    try:
        ensure_oauth_secret(config)
    except (RuntimeError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "reason": "oauth_secret_bootstrap_failed",
                    "errorCode": _safe_error_code(exc),
                }
            )
        )
        return 1
    print('{"isImmutable": true, "status": "completed"}')
    return 0


def _secret_payload(config: OAuthSecretBootstrapConfig) -> bytes:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    payload = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": config.secret_name,
            "namespace": config.namespace,
            "labels": {"app.kubernetes.io/managed-by": "foundry-lite-oauth-bootstrap"},
        },
        "immutable": True,
        "type": "Opaque",
        "data": {config.secret_key: base64.b64encode(pem).decode("ascii")},
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()


def _validate_existing_secret(config: OAuthSecretBootstrapConfig, body: bytes) -> None:
    try:
        payload = json.loads(body)
        metadata = _mapping(payload.get("metadata"))
        data = _mapping(payload.get("data"))
        encoded = data.get(config.secret_key)
        if metadata.get("name") != config.secret_name or payload.get("immutable") is not True:
            raise ValueError("oauth_secret_contract_mismatch")
        if not isinstance(encoded, str):
            raise ValueError("oauth_secret_key_missing")
        key = serialization.load_pem_private_key(base64.b64decode(encoded, validate=True), password=None)
        if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 3072:
            raise ValueError("oauth_secret_key_invalid")
    except (UnicodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
        raise RuntimeError("oauth_secret_validation_failed") from exc


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("oauth_secret_payload_invalid")
    return value


def _safe_error_code(exc: RuntimeError | ValueError) -> str:
    value = str(exc)
    return value if value in _SAFE_ERROR_CODES else "oauth_secret_bootstrap_failed"


def _validate_config(config: OAuthSecretBootstrapConfig) -> None:
    for value in (config.namespace, config.secret_name, config.secret_key):
        if not _DNS_LABEL.fullmatch(value):
            raise ValueError("oauth_secret_name_invalid")
    if config.timeout_seconds <= 0 or config.timeout_seconds > 30:
        raise ValueError("oauth_secret_timeout_invalid")


def _secret_collection_path(namespace: str) -> str:
    return f"/api/v1/namespaces/{namespace}/secrets"


def _secret_path(config: OAuthSecretBootstrapConfig) -> str:
    return f"{_secret_collection_path(config.namespace)}/{config.secret_name}"


if __name__ == "__main__":
    raise SystemExit(main())
