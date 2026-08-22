from __future__ import annotations

import json

import pytest
from foundry_lite.infrastructure.adapters.kubernetes_deployment import KubernetesTransportError
from foundry_lite.infrastructure.kubernetes_release_controller import (
    KubernetesReleaseControllerConfig,
    KubernetesReleaseControllerResult,
)
from foundry_lite_worker import kubernetes_release_controller as worker


def _controller_config() -> KubernetesReleaseControllerConfig:
    return KubernetesReleaseControllerConfig(
        namespace="foundry-qa",
        signature_issuer="https://token.actions.githubusercontent.com",
        signature_identity_regexp=r"^https://github.com/ludia8888/foundry-lite/.*$",
    )


def test_release_worker_config_parses_exact_controller_contract() -> None:
    config = worker.config_from_env(
        {
            "FOUNDRY_LITE_KUBERNETES_RELEASE_NAMESPACE": "foundry-qa",
            "FOUNDRY_LITE_KUBERNETES_SIGNATURE_ISSUER": "https://token.actions.githubusercontent.com",
            "FOUNDRY_LITE_KUBERNETES_SIGNATURE_IDENTITY_REGEXP": "^workflow$",
            "FOUNDRY_LITE_KUBERNETES_CONTROLLER_TIMEOUT_SECONDS": "12.5",
            "FOUNDRY_LITE_KUBERNETES_ROLLOUT_TIMEOUT_SECONDS": "240",
            "FOUNDRY_LITE_KUBERNETES_REQUIRE_SIGNATURE": "yes",
            "FOUNDRY_LITE_KUBERNETES_CONTROLLER_POLL_SECONDS": "0.25",
            "FOUNDRY_LITE_KUBERNETES_CONTROLLER_MAX_ITERATIONS": "3",
        }
    )

    assert config.controller.namespace == "foundry-qa"
    assert config.controller.timeout_seconds == 12.5
    assert config.controller.rollout_timeout_seconds == 240
    assert config.controller.require_signature is True
    assert config.poll_seconds == 0.25
    assert config.max_iterations == 3

    unsigned = worker.config_from_env(
        {
            "FOUNDRY_LITE_KUBERNETES_RELEASE_NAMESPACE": "foundry-qa",
            "FOUNDRY_LITE_KUBERNETES_SIGNATURE_ISSUER": "https://issuer.example.test",
            "FOUNDRY_LITE_KUBERNETES_SIGNATURE_IDENTITY_REGEXP": "identity",
            "FOUNDRY_LITE_KUBERNETES_REQUIRE_SIGNATURE": "no",
        }
    )
    assert unsigned.controller.require_signature is False


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({}, "required_controller_configuration_missing"),
        (
            {
                "FOUNDRY_LITE_KUBERNETES_RELEASE_NAMESPACE": "foundry-qa",
                "FOUNDRY_LITE_KUBERNETES_SIGNATURE_ISSUER": "issuer",
                "FOUNDRY_LITE_KUBERNETES_SIGNATURE_IDENTITY_REGEXP": "identity",
                "FOUNDRY_LITE_KUBERNETES_CONTROLLER_TIMEOUT_SECONDS": "not-a-number",
            },
            "invalid_controller_number",
        ),
        (
            {
                "FOUNDRY_LITE_KUBERNETES_RELEASE_NAMESPACE": "foundry-qa",
                "FOUNDRY_LITE_KUBERNETES_SIGNATURE_ISSUER": "issuer",
                "FOUNDRY_LITE_KUBERNETES_SIGNATURE_IDENTITY_REGEXP": "identity",
                "FOUNDRY_LITE_KUBERNETES_CONTROLLER_MAX_ITERATIONS": "1.5",
            },
            "invalid_controller_number",
        ),
        (
            {
                "FOUNDRY_LITE_KUBERNETES_RELEASE_NAMESPACE": "foundry-qa",
                "FOUNDRY_LITE_KUBERNETES_SIGNATURE_ISSUER": "issuer",
                "FOUNDRY_LITE_KUBERNETES_SIGNATURE_IDENTITY_REGEXP": "identity",
                "FOUNDRY_LITE_KUBERNETES_REQUIRE_SIGNATURE": "sometimes",
            },
            "invalid_controller_boolean",
        ),
    ],
)
def test_release_worker_config_rejects_missing_or_invalid_values(
    overrides: dict[str, str],
    reason: str,
) -> None:
    with pytest.raises(ValueError, match=reason):
        worker.config_from_env(overrides)


def test_release_worker_runs_bounded_reconcile_and_emits_safe_results(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []
    sleeps: list[float] = []

    class _Controller:
        def __init__(self, config: KubernetesReleaseControllerConfig) -> None:
            calls.append(config.namespace)

        def reconcile_once(self) -> tuple[KubernetesReleaseControllerResult, ...]:
            calls.append("reconcile")
            return (KubernetesReleaseControllerResult("release-a", "reconciled", "Live", "exact_digest_live"),)

    monkeypatch.setattr(worker, "KubernetesReleaseController", _Controller)
    monkeypatch.setattr(worker.time, "sleep", sleeps.append)
    config = worker.KubernetesReleaseWorkerConfig(_controller_config(), poll_seconds=0.5, max_iterations=2)

    assert worker.run_controller(config) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [line["iteration"] for line in lines] == [1, 2]
    assert lines[0]["results"][0]["phase"] == "Live"
    assert calls == ["foundry-qa", "reconcile", "reconcile"]
    assert sleeps == [0.5]


def test_release_worker_reports_bounded_error_without_exception_detail(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class _Controller:
        def __init__(self, _config: KubernetesReleaseControllerConfig) -> None:
            pass

        def reconcile_once(self) -> tuple[KubernetesReleaseControllerResult, ...]:
            raise RuntimeError("private-cluster-detail")

    monkeypatch.setattr(worker, "KubernetesReleaseController", _Controller)
    config = worker.KubernetesReleaseWorkerConfig(_controller_config(), poll_seconds=1, max_iterations=1)

    assert worker.run_controller(config) == 1
    output = capsys.readouterr().out
    assert json.loads(output)["reason"] == "reconcile_failed"
    assert "private-cluster-detail" not in output


def test_release_worker_classifies_kubernetes_transport_failure_without_raw_detail(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class _Controller:
        def __init__(self, _config: KubernetesReleaseControllerConfig) -> None:
            pass

        def reconcile_once(self) -> tuple[KubernetesReleaseControllerResult, ...]:
            raise KubernetesTransportError("unavailable")

    monkeypatch.setattr(worker, "KubernetesReleaseController", _Controller)
    config = worker.KubernetesReleaseWorkerConfig(_controller_config(), poll_seconds=1, max_iterations=1)

    assert worker.run_controller(config) == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "kubernetes_transport_unavailable"


@pytest.mark.parametrize(
    "config",
    [
        worker.KubernetesReleaseWorkerConfig(_controller_config(), poll_seconds=0, max_iterations=1),
        worker.KubernetesReleaseWorkerConfig(_controller_config(), poll_seconds=61, max_iterations=1),
        worker.KubernetesReleaseWorkerConfig(_controller_config(), poll_seconds=1, max_iterations=-1),
    ],
)
def test_release_worker_rejects_unbounded_loop_configuration(
    config: worker.KubernetesReleaseWorkerConfig,
) -> None:
    with pytest.raises(ValueError, match="invalid_controller_loop_configuration"):
        worker.run_controller(config)


def test_release_worker_main_forces_single_iteration(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: list[worker.KubernetesReleaseWorkerConfig] = []
    configured = worker.KubernetesReleaseWorkerConfig(_controller_config(), poll_seconds=2, max_iterations=0)
    monkeypatch.setattr(worker, "config_from_env", lambda: configured)
    monkeypatch.setattr(worker, "run_controller", lambda config: observed.append(config) or 0)

    assert worker.main(["--once"]) == 0
    assert observed[0].max_iterations == 1
    assert observed[0].poll_seconds == 2


def test_release_worker_main_fails_closed_on_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def invalid() -> worker.KubernetesReleaseWorkerConfig:
        raise ValueError("private-value")

    monkeypatch.setattr(worker, "config_from_env", invalid)

    assert worker.main([]) == 2
    output = capsys.readouterr().out
    assert json.loads(output)["reason"] == "invalid_or_missing_configuration"
    assert "private-value" not in output
