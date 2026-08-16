"""Create private, generated Kubernetes QA secrets without writing values files."""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import subprocess  # nosec B404 - fixed kubectl only; remove if arbitrary command input is introduced.
import urllib.parse
from pathlib import Path

from scripts.operations.macmini_qa_guard import QA_ROOT, assert_host_boundary, assert_namespace

_SECRET_NAMES = (
    "foundry-lite-application",
    "foundry-lite-qa-dependencies",
    "foundry-lite-backup-age",
)


def bootstrap(args: argparse.Namespace) -> dict[str, object]:
    assert_host_boundary()
    assert_namespace(args.namespace)
    recipient = _recipient(args.age_recipient_file)
    present = {name: _secret_exists(args, name) for name in _SECRET_NAMES}
    if all(present.values()):
        return _receipt("already_exists")
    if any(present.values()):
        raise RuntimeError("macmini_qa_secret_set_is_partial")
    postgres_password = secrets.token_urlsafe(36)
    minio_user = "foundryqa"
    minio_password = secrets.token_urlsafe(36)
    keycloak_admin_password = secrets.token_urlsafe(36)
    keycloak_qa_password = secrets.token_urlsafe(24)
    database_url = _database_url(postgres_password)
    application = {
        "FOUNDRY_LITE_DB_URL": database_url,
        "AWS_ACCESS_KEY_ID": minio_user,
        "AWS_SECRET_ACCESS_KEY": minio_password,
        "FOUNDRY_LITE_S3_ACCESS_KEY_ID": minio_user,
        "FOUNDRY_LITE_S3_SECRET_ACCESS_KEY": minio_password,
        "FOUNDRY_LITE_CODE_EXECUTION_BROKER_TOKEN": secrets.token_urlsafe(48),
        "FOUNDRY_LITE_SECRET_AIP_PROMPT_ARTIFACT_ENCRYPTION_KEY": secrets.token_urlsafe(48),
        "FOUNDRY_LITE_OBJECT_QUERY_CURSOR_SIGNING_KEY_ID": "macmini-qa-v1",
        "FOUNDRY_LITE_OBJECT_QUERY_CURSOR_SIGNING_KEY": secrets.token_urlsafe(48),
        "FOUNDRY_LITE_OPERATIONS_CURSOR_SIGNING_KEY_ID": "macmini-qa-v1",
        "FOUNDRY_LITE_OPERATIONS_CURSOR_SIGNING_KEY": secrets.token_urlsafe(48),
    }
    dependencies = {
        "POSTGRES_PASSWORD": postgres_password,
        "MINIO_ROOT_USER": minio_user,
        "MINIO_ROOT_PASSWORD": minio_password,
        "GRAFANA_ADMIN_USER": "foundry-qa-admin",
        "GRAFANA_ADMIN_PASSWORD": secrets.token_urlsafe(36),
        "KEYCLOAK_ADMIN": "foundry-qa-admin",
        "KEYCLOAK_ADMIN_PASSWORD": keycloak_admin_password,
        "KEYCLOAK_QA_USER": "sean1234",
        "KEYCLOAK_QA_USER_PASSWORD": keycloak_qa_password,
    }
    _apply_secret(args, "foundry-lite-application", application)
    _apply_secret(args, "foundry-lite-qa-dependencies", dependencies)
    _apply_secret(args, "foundry-lite-backup-age", {"recipient": recipient})
    _write_keycloak_login(keycloak_qa_password)
    return _receipt("created")


def _secret_exists(args: argparse.Namespace, name: str) -> bool:
    result = subprocess.run(  # nosec B603 - namespace-bound kubectl argv; remove if shell or free argv appears.
        _kubectl(args, ("get", "secret", name, "-o", "name")),
        check=False,
        capture_output=True,
        timeout=30,
    )
    return result.returncode == 0


def _apply_secret(args: argparse.Namespace, name: str, values: dict[str, str]) -> None:
    payload = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": name, "namespace": args.namespace, "labels": {"app.kubernetes.io/name": "foundry-lite"}},
        "type": "Opaque",
        "immutable": True,
        "data": {key: base64.b64encode(value.encode()).decode() for key, value in values.items()},
    }
    result = subprocess.run(  # nosec B603 - namespace-bound kubectl argv; remove if shell or free argv appears.
        _kubectl(args, ("apply", "-f", "-")),
        input=json.dumps(payload).encode(),
        check=False,
        capture_output=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError("macmini_qa_secret_apply_failed")


def _database_url(password: str) -> str:
    encoded = urllib.parse.quote(password, safe="")
    return f"postgresql+psycopg://postgres:{encoded}@foundry-lite-postgresql:5432/foundry_lite"


def _recipient(path: str) -> str:
    value = Path(path).read_text(encoding="utf-8").strip()
    if not value.startswith("age1") or len(value) > 200:
        raise ValueError("macmini_qa_age_recipient_invalid")
    return value


def _write_keycloak_login(password: str) -> None:
    path = QA_ROOT / "state" / "keycloak-qa-login.txt"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write("username=sean1234\n")
        stream.write(f"password={password}\n")


def _kubectl(args: argparse.Namespace, operation: tuple[str, ...]) -> tuple[str, ...]:
    return (args.kubectl, "--kubeconfig", args.kubeconfig, "--namespace", args.namespace, *operation)


def _receipt(status: str) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "status": status,
        "secretNames": list(_SECRET_NAMES),
        "valuesWrittenToHelm": False,
        "rawValuesInReceipt": False,
        "secretsAreImmutable": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--namespace", default="foundry-qa")
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--kubectl", default=str(QA_ROOT / "bin" / "kubectl"))
    parser.add_argument("--age-recipient-file", required=True)
    receipt = bootstrap(parser.parse_args())
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
