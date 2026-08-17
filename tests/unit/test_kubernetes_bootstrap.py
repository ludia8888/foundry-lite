from __future__ import annotations

import json
import socket
from collections.abc import Sequence

import pytest
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from scripts.operations import bootstrap_kubernetes_oauth_secret as oauth_subject
from scripts.operations.bootstrap_kubernetes_oauth_secret import (
    OAuthSecretBootstrapConfig,
    SecretApi,
    SecretApiResponse,
    ensure_oauth_secret,
)
from scripts.operations.bootstrap_s3_bucket import ensure_bucket
from scripts.operations.wait_for_tcp import wait_for_tcp


class _SecretApi(SecretApi):
    def __init__(self, responses: Sequence[SecretApiResponse | str]) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, str, bytes | None]] = []

    def request(self, method: str, path: str, body: bytes | None) -> SecretApiResponse:
        self.requests.append((method, path, body))
        response = self.responses.pop(0)
        if response == "echo":
            assert body is not None
            return SecretApiResponse(201, body)
        assert isinstance(response, SecretApiResponse)
        return response


class _S3:
    def __init__(self, *, head_status: int = 200) -> None:
        self.head_status = head_status
        self.calls: list[tuple[str, object]] = []

    def head_bucket(self, *, Bucket: str) -> None:
        self.calls.append(("head", Bucket))
        if self.head_status != 200:
            raise ClientError(
                {"Error": {"Code": "NotFound"}, "ResponseMetadata": {"HTTPStatusCode": self.head_status}},
                "HeadBucket",
            )

    def create_bucket(self, *, Bucket: str) -> None:
        self.calls.append(("create", Bucket))

    def put_bucket_versioning(self, *, Bucket: str, VersioningConfiguration: object) -> None:
        self.calls.append(("versioning", (Bucket, VersioningConfiguration)))


def _oauth_config() -> OAuthSecretBootstrapConfig:
    return OAuthSecretBootstrapConfig("foundry-qa", "foundry-lite-oauth-signing", "oauth-private-key.pem")


def test_oauth_bootstrap_creates_one_immutable_3072_bit_secret() -> None:
    api = _SecretApi([SecretApiResponse(404, b"{}"), "echo"])

    status = ensure_oauth_secret(_oauth_config(), api=api)

    assert status == "created"
    assert [request[0] for request in api.requests] == ["GET", "POST"]
    body = api.requests[1][2]
    assert body is not None
    payload = json.loads(body)
    assert payload["immutable"] is True
    assert set(payload["data"]) == {"oauth-private-key.pem"}
    assert "PRIVATE KEY" not in body.decode()


def test_oauth_bootstrap_reconciles_create_conflict_without_rotation() -> None:
    creating = _SecretApi([SecretApiResponse(404, b"{}"), "echo"])
    ensure_oauth_secret(_oauth_config(), api=creating)
    created_body = creating.requests[1][2]
    assert created_body is not None
    api = _SecretApi(
        [
            SecretApiResponse(404, b"{}"),
            SecretApiResponse(409, b"{}"),
            SecretApiResponse(200, created_body),
        ]
    )

    assert ensure_oauth_secret(_oauth_config(), api=api) == "existing"
    assert [request[0] for request in api.requests] == ["GET", "POST", "GET"]


def test_oauth_bootstrap_rejects_mutable_or_malformed_existing_secret() -> None:
    api = _SecretApi([SecretApiResponse(200, b'{"metadata":{"name":"foundry-lite-oauth-signing"}}')])

    with pytest.raises(RuntimeError, match="oauth_secret_validation_failed"):
        ensure_oauth_secret(_oauth_config(), api=api)


def test_oauth_bootstrap_cli_never_prints_secret_identifiers(monkeypatch, capsys) -> None:
    monkeypatch.setattr(oauth_subject, "ensure_oauth_secret", lambda _config: "created")

    result = oauth_subject.main(
        [
            "--namespace",
            "foundry-qa",
            "--secret-name",
            "sensitive-secret-name",
            "--secret-key",
            "sensitive-secret-key",
        ]
    )

    output = capsys.readouterr().out
    assert result == 0
    assert json.loads(output) == {"isImmutable": True, "status": "completed"}
    assert "sensitive-secret" not in output


def test_s3_bootstrap_is_idempotent_and_enables_versioning_on_create() -> None:
    existing = _S3()
    missing = _S3(head_status=404)

    assert ensure_bucket("http://foundry-lite-minio:9000", "foundry-lite", client=existing) == "existing"
    assert ensure_bucket("http://foundry-lite-minio:9000", "foundry-lite", client=missing) == "created"
    assert missing.calls[1] == ("create", "foundry-lite")
    assert missing.calls[2][0] == "versioning"


def test_s3_bootstrap_rejects_credentials_in_endpoint() -> None:
    with pytest.raises(ValueError, match="s3_endpoint_invalid"):
        ensure_bucket("https://access:secret@s3.example.test", "foundry-lite", client=_S3())


def test_tcp_wait_observes_a_bound_listener_without_application_data() -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]

        assert wait_for_tcp("127.0.0.1", port, timeout_seconds=1)
