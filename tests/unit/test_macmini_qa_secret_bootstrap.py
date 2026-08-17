from __future__ import annotations

import base64
import json
import re
from argparse import Namespace
from pathlib import Path

import pytest

from scripts.operations import bootstrap_macmini_qa_secrets as subject


def test_bootstrap_generates_all_protected_runtime_signing_material(monkeypatch) -> None:
    captured: dict[str, dict[str, str]] = {}
    registry: dict[str, bytes] = {}
    monkeypatch.setattr(subject, "assert_host_boundary", lambda: None)
    monkeypatch.setattr(subject, "assert_namespace", lambda _namespace: None)
    monkeypatch.setattr(subject, "_recipient", lambda _path: "age1recipient")
    monkeypatch.setattr(subject, "_registry_docker_config", lambda _path: b'{"auths":{}}')
    monkeypatch.setattr(subject, "_secret_exists", lambda _args, _name: False)
    monkeypatch.setattr(subject, "_apply_secret", lambda _args, name, values: captured.setdefault(name, values))
    monkeypatch.setattr(
        subject,
        "_apply_registry_secret",
        lambda _args, name, value: registry.setdefault(name, value),
    )
    monkeypatch.setattr(subject, "_write_keycloak_login", lambda _password: None)

    receipt = subject.bootstrap(
        Namespace(
            namespace="foundry-qa",
            kubeconfig="/private/kubeconfig",
            kubectl="/private/kubectl",
            age_recipient_file="/private/age.pub",
            registry_token_file="/private/github-token",
        )
    )

    application = captured["foundry-lite-application"]
    assert application["FOUNDRY_LITE_OBJECT_QUERY_CURSOR_SIGNING_KEY_ID"] == "macmini-qa-v1"
    assert application["FOUNDRY_LITE_OPERATIONS_CURSOR_SIGNING_KEY_ID"] == "macmini-qa-v1"
    assert len(application["FOUNDRY_LITE_OBJECT_QUERY_CURSOR_SIGNING_KEY"]) >= 48
    assert len(application["FOUNDRY_LITE_OPERATIONS_CURSOR_SIGNING_KEY"]) >= 48
    assert (
        application["FOUNDRY_LITE_OBJECT_QUERY_CURSOR_SIGNING_KEY"]
        != application["FOUNDRY_LITE_OPERATIONS_CURSOR_SIGNING_KEY"]
    )
    dependencies = captured["foundry-lite-qa-dependencies"]
    assert dependencies["GRAFANA_ADMIN_USER"] == "foundry-qa-admin"
    assert len(dependencies["GRAFANA_ADMIN_PASSWORD"]) >= 36
    assert dependencies["GRAFANA_ADMIN_PASSWORD"] != dependencies["KEYCLOAK_ADMIN_PASSWORD"]
    referenced_dependency_keys: set[str] = set()
    for template in Path("deploy/helm/foundry-lite/templates").glob("*.yaml"):
        for line in template.read_text(encoding="utf-8").splitlines():
            if ".Values.qaDependencies.credentialsExistingSecret" in line:
                match = re.search(r"key: ([A-Z0-9_]+)", line)
                assert match is not None, line
                referenced_dependency_keys.add(match.group(1))
    assert referenced_dependency_keys == set(dependencies)
    assert registry == {"foundry-lite-ghcr": b'{"auths":{}}'}
    assert "foundry-lite-ghcr" in receipt["secretNames"]
    assert receipt["status"] == "created"


def test_registry_docker_config_requires_private_state_file_and_never_enters_receipt(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(subject, "QA_ROOT", tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    token = state / "github-packages-token"
    token.write_text("github-read-packages-token-value", encoding="utf-8")
    token.chmod(0o600)

    encoded = subject._registry_docker_config(str(token))
    payload = json.loads(encoded)

    assert payload["auths"]["ghcr.io"]["username"] == "ludia8888"
    assert base64.b64decode(payload["auths"]["ghcr.io"]["auth"]).decode().startswith("ludia8888:")
    assert "github-read-packages-token-value" not in json.dumps(subject._receipt("created"))
    token.chmod(0o644)
    with pytest.raises(ValueError, match="registry_token_file_invalid"):
        subject._registry_docker_config(str(token))


def test_existing_complete_secret_set_does_not_require_retained_registry_token(monkeypatch) -> None:
    monkeypatch.setattr(subject, "assert_host_boundary", lambda: None)
    monkeypatch.setattr(subject, "assert_namespace", lambda _namespace: None)
    monkeypatch.setattr(subject, "_secret_exists", lambda _args, _name: True)
    monkeypatch.setattr(subject, "_recipient", lambda _path: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(subject, "_registry_docker_config", lambda _path: (_ for _ in ()).throw(AssertionError()))

    receipt = subject.bootstrap(Namespace(namespace="foundry-qa"))

    assert receipt["status"] == "already_exists"
