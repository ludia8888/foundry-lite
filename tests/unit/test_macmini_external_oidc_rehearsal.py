from __future__ import annotations

import json
from argparse import Namespace

from scripts.operations import run_macmini_external_oidc_rehearsal as subject


def _args() -> Namespace:
    return Namespace(
        run_id="run-1",
        namespace="foundry-qa",
        kubeconfig="kubeconfig",
        kubectl="kubectl",
        helm="helm",
        chart="chart",
        public_base_url="https://foundry.example",
        identity_base_url="https://identity.example",
        application_id="foundry-lite",
        principals_file="principals",
        duration_seconds=15,
    )


def test_rehearsal_issues_fresh_tokens_faults_and_restores_exact_revision(tmp_path, monkeypatch) -> None:
    client = subject.issue_macmini_external_oidc_tokens.IssuedClient(
        "client-1", "https://identity.example/register/client-1", "registration-secret"
    )
    monkeypatch.setattr(subject, "QA_ROOT", tmp_path)
    monkeypatch.setattr(subject, "assert_host_boundary", lambda: None)
    monkeypatch.setattr(subject, "assert_namespace", lambda _namespace: None)
    monkeypatch.setattr(subject, "_helm_revision", lambda _args: 42)
    monkeypatch.setattr(subject.switch_macmini_external_oidc, "_helm_values", lambda _args: {"auth": "header"})
    monkeypatch.setattr(subject.switch_macmini_external_oidc, "_hash_json", lambda _value: "sha256:before")
    monkeypatch.setattr(subject, "_prepare_identity_hostname", lambda _args: {"status": "passed"})
    monkeypatch.setattr(
        subject.issue_macmini_external_oidc_tokens,
        "issue",
        lambda _args: ({"status": "passed"}, client),
    )
    monkeypatch.setattr(subject.switch_macmini_external_oidc, "switch", lambda _args: {"status": "passed"})
    monkeypatch.setattr(subject.run_macmini_external_oidc_fault, "run", lambda _args: {"status": "passed"})
    monkeypatch.setattr(subject, "_client_cleanup", lambda _client: {"status": "passed", "performed": True})
    monkeypatch.setattr(subject, "_token_cleanup", lambda: {"status": "passed", "removedFileCount": 2})
    monkeypatch.setattr(
        subject,
        "_restore_revision",
        lambda _args, revision, value_hash: {
            "status": "passed",
            "sourceRevision": revision,
            "actualValuesSha256": value_hash,
        },
    )
    monkeypatch.setattr(
        subject,
        "write_json_receipt",
        lambda path, payload: path.write_text(json.dumps(payload), encoding="utf-8"),
    )

    receipt = subject.run(_args())

    assert receipt["status"] == "passed"
    assert receipt["sourceHelmRevision"] == 42
    assert receipt["ephemeralClientRemoved"] is True
    assert receipt["steps"]["restoration"]["actualValuesSha256"] == "sha256:before"


def test_rehearsal_restores_even_when_token_issuance_fails(tmp_path, monkeypatch) -> None:
    restored: list[int] = []
    monkeypatch.setattr(subject, "QA_ROOT", tmp_path)
    monkeypatch.setattr(subject, "assert_host_boundary", lambda: None)
    monkeypatch.setattr(subject, "assert_namespace", lambda _namespace: None)
    monkeypatch.setattr(subject, "_helm_revision", lambda _args: 42)
    monkeypatch.setattr(subject.switch_macmini_external_oidc, "_helm_values", lambda _args: {"auth": "header"})
    monkeypatch.setattr(subject.switch_macmini_external_oidc, "_hash_json", lambda _value: "sha256:before")
    monkeypatch.setattr(subject, "_prepare_identity_hostname", lambda _args: {"status": "passed"})
    monkeypatch.setattr(
        subject.issue_macmini_external_oidc_tokens,
        "issue",
        lambda _args: (_ for _ in ()).throw(RuntimeError("issuance")),
    )
    monkeypatch.setattr(subject, "_client_cleanup", lambda _client: {"status": "passed", "performed": False})
    monkeypatch.setattr(subject, "_token_cleanup", lambda: {"status": "passed", "removedFileCount": 0})
    monkeypatch.setattr(
        subject,
        "_restore_revision",
        lambda _args, revision, _hash: restored.append(revision) or {"status": "passed"},
    )
    monkeypatch.setattr(subject, "write_json_receipt", lambda _path, _payload: None)

    receipt = subject.run(_args())

    assert receipt["status"] == "failed"
    assert receipt["failureType"] == "RuntimeError"
    assert restored == [42]
