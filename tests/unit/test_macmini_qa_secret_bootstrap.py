from __future__ import annotations

from argparse import Namespace

from scripts.operations import bootstrap_macmini_qa_secrets as subject


def test_bootstrap_generates_all_protected_runtime_signing_material(monkeypatch) -> None:
    captured: dict[str, dict[str, str]] = {}
    monkeypatch.setattr(subject, "assert_host_boundary", lambda: None)
    monkeypatch.setattr(subject, "assert_namespace", lambda _namespace: None)
    monkeypatch.setattr(subject, "_recipient", lambda _path: "age1recipient")
    monkeypatch.setattr(subject, "_secret_exists", lambda _args, _name: False)
    monkeypatch.setattr(subject, "_apply_secret", lambda _args, name, values: captured.setdefault(name, values))
    monkeypatch.setattr(subject, "_write_keycloak_login", lambda _password: None)

    receipt = subject.bootstrap(
        Namespace(
            namespace="foundry-qa",
            kubeconfig="/private/kubeconfig",
            kubectl="/private/kubectl",
            age_recipient_file="/private/age.pub",
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
    assert receipt["status"] == "created"
