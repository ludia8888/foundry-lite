from __future__ import annotations

import json
import subprocess
from argparse import Namespace
from pathlib import Path
from typing import cast

import pytest

from scripts.operations import deploy_macmini_qa as subject


def _manifest() -> dict[str, object]:
    revision = "a" * 40
    return {
        "revision": revision,
        "images": {
            name: f"ghcr.io/ludia8888/foundry-lite-{_image_suffix(name)}@sha256:{index:x}{'a' * 63}"
            for index, name in enumerate(subject._IMAGE_NAMES, start=1)
        },
    }


def _image_suffix(name: str) -> str:
    return {
        "codeExecution": "code-execution",
        "nodeCodeExecution": "node-code-execution",
        "trainedModel": "trained-model",
    }.get(name, name)


def test_image_manifest_requires_exact_sha_and_expected_repositories(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(_manifest()), encoding="utf-8")

    loaded = subject._load_manifest(path)
    images = cast(dict[str, dict[str, str]], loaded["images"])

    assert loaded["revision"] == "a" * 40
    assert set(images) == set(subject._IMAGE_NAMES)
    assert images["api"]["digest"].startswith("sha256:")


def test_image_manifest_rejects_mutable_or_foreign_image(tmp_path: Path) -> None:
    value = _manifest()
    cast(dict[str, object], value["images"])["api"] = "ghcr.io/attacker/foundry-lite-api@sha256:" + "a" * 64
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ValueError, match="image_coordinate_invalid"):
        subject._load_manifest(path)


def test_foundation_phase_disables_application_until_migration(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(subject, "QA_ROOT", tmp_path)
    (tmp_path / "state").mkdir()
    manifest = subject._load_manifest(_write_manifest(tmp_path))

    override, foundation = subject._write_overrides("run-1", manifest)
    immutable = json.loads(override.read_text(encoding="utf-8"))
    phase = json.loads(foundation.read_text(encoding="utf-8"))

    assert immutable["global"]["revision"] == "a" * 40
    assert phase["api"]["replicas"] == 0
    assert phase["web"]["replicas"] == 0
    assert phase["runtimePersistence"]["enabled"] is False
    assert phase["migrations"]["enabled"] is False
    assert all(not config["enabled"] for config in phase["workers"].values())
    assert override.stat().st_mode & 0o077 == 0


def test_foundation_overrides_are_idempotent_for_exact_retry(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(subject, "QA_ROOT", tmp_path)
    (tmp_path / "state").mkdir()
    manifest = subject._load_manifest(_write_manifest(tmp_path))

    first = subject._write_overrides("run-1", manifest)
    second = subject._write_overrides("run-1", manifest)

    assert second == first
    assert all(path.stat().st_mode & 0o077 == 0 for path in second)


def test_private_override_retry_rejects_changed_payload(tmp_path: Path) -> None:
    target = tmp_path / "override.json"
    subject._write_private_json(target, {"revision": "first"})

    with pytest.raises(RuntimeError, match="private_json_conflict"):
        subject._write_private_json(target, {"revision": "changed"})


def test_macmini_profile_requires_private_registry_pull_secret() -> None:
    values = subject.yaml.safe_load(Path("deploy/helm/foundry-lite/values.macmini-qa.yaml").read_text(encoding="utf-8"))

    assert values["global"]["imagePullSecrets"] == ["foundry-lite-ghcr"]


def test_initial_deploy_requires_explicit_embedded_oauth_overlay(tmp_path: Path) -> None:
    valid = tmp_path / "embedded.yaml"
    valid.write_text(
        """
global: {runtimeProfile: test}
auth:
  profile: header-trust
  localOAuthIssuer: https://foundry.invalid
  dynamicClientApplicationId: foundry-lite
  localConsentRoles: tenant_admin ops_manager
mcp: {publicBaseUrl: https://foundry.invalid}
external: {oidc: {discoveryUrl: ''}}
""",
        encoding="utf-8",
    )
    subject._validate_initial_auth_values(valid)

    invalid = tmp_path / "external.yaml"
    invalid.write_text("global: {runtimeProfile: production}\nauth: {profile: oidc}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="initial_auth_values_invalid"):
        subject._validate_initial_auth_values(invalid)


def test_helm_applies_base_then_initial_auth_then_immutable_overrides(tmp_path: Path, monkeypatch) -> None:
    observed: list[str] = []

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed.extend(command)
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subject.subprocess, "run", run)
    paths = tuple(tmp_path / name for name in ("base.yaml", "auth.yaml"))
    override = tmp_path / "images.json"

    subject._helm(Namespace(helm="helm", namespace="foundry-qa"), tmp_path, paths, override, None)

    assert observed.count("--values") == 3
    assert [observed[index + 1] for index, value in enumerate(observed) if value == "--values"] == [
        str(paths[0]),
        str(paths[1]),
        str(override),
    ]


def test_migration_evidence_requires_observed_idempotent_receipt(monkeypatch) -> None:
    output = b'first migration output\n{"status":"passed","runs":2,"isIdempotent":true}\n'
    monkeypatch.setattr(
        subject,
        "_kubectl",
        lambda *_args: subprocess.CompletedProcess([], 0, stdout=output, stderr=b""),
    )

    evidence = subject._migration_evidence(Namespace(), 2)

    assert evidence["job"] == "foundry-lite-migrate-2"
    assert evidence["runs"] == 2
    assert evidence["isIdempotent"] is True
    assert str(evidence["logSha256"]).startswith("sha256:")
    assert evidence["rawLogStored"] is False


def test_kubectl_is_always_bound_to_the_approved_namespace(monkeypatch) -> None:
    observed: tuple[str, ...] = ()

    def fake_run(command, **_kwargs):
        nonlocal observed
        observed = tuple(command)
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subject.subprocess, "run", fake_run)

    subject._kubectl(
        Namespace(kubectl="kubectl", kubeconfig="/private/kubeconfig", namespace="foundry-qa"),
        ("logs", "job/foundry-lite-migrate-2", "-c", "migrate"),
        60,
    )

    assert observed[:6] == (
        "kubectl",
        "--kubeconfig",
        "/private/kubeconfig",
        "--namespace",
        "foundry-qa",
        "logs",
    )


@pytest.mark.parametrize(
    "output",
    (
        b"no receipt\n",
        b'{"status":"passed","runs":1,"isIdempotent":true}\n',
        b'{"status":"passed","runs":2,"isIdempotent":false}\n',
    ),
)
def test_migration_evidence_rejects_unproven_runs(monkeypatch, output: bytes) -> None:
    monkeypatch.setattr(
        subject,
        "_kubectl",
        lambda *_args: subprocess.CompletedProcess([], 0, stdout=output, stderr=b""),
    )

    with pytest.raises(RuntimeError, match="migration_evidence_invalid"):
        subject._migration_evidence(Namespace(), 2)


def test_initial_deploy_rejects_existing_release(monkeypatch) -> None:
    monkeypatch.setattr(
        subject.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, stdout=b"{}", stderr=b""),
    )

    with pytest.raises(RuntimeError, match="initial_deploy_release_exists"):
        subject._assert_fresh_release(Namespace(helm="helm", namespace="foundry-qa"))


def test_deploy_cli_prints_only_the_safe_receipt_pointer(monkeypatch, capsys) -> None:
    monkeypatch.setattr(subject, "deploy", lambda _args: {"secret": "must-not-be-printed"})

    result = subject.main(
        [
            "--run-id",
            "run-1",
            "--kubeconfig",
            "/private/kubeconfig",
            "--chart",
            "/private/chart",
            "--values",
            "/private/values.yaml",
            "--initial-auth-values",
            "/private/auth.yaml",
            "--image-manifest",
            "/private/images.json",
            "--age-recipient-file",
            "/private/age.pub",
            "--registry-token-file",
            "/private/github-token",
        ]
    )

    output = capsys.readouterr().out
    assert result == 0
    assert json.loads(output) == {"receiptStored": True, "status": "passed"}
    assert "must-not-be-printed" not in output


def _write_manifest(tmp_path: Path) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(_manifest()), encoding="utf-8")
    return path
