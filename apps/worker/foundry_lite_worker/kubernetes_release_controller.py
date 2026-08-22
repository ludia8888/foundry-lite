"""Long-running entrypoint for immutable Kubernetes release reconciliation."""

from __future__ import annotations

import argparse
import json
import os
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Literal

from foundry_lite.infrastructure.adapters.kubernetes_deployment import KubernetesTransportError
from foundry_lite.infrastructure.kubernetes_release_controller import (
    KubernetesReleaseController,
    KubernetesReleaseControllerConfig,
    KubernetesReleaseControllerResult,
)


@dataclass(frozen=True, slots=True)
class KubernetesReleaseWorkerConfig:
    controller: KubernetesReleaseControllerConfig
    poll_seconds: float = 5.0
    max_iterations: int = 0


def config_from_env(env: Mapping[str, str] | None = None) -> KubernetesReleaseWorkerConfig:
    values = env or os.environ
    return KubernetesReleaseWorkerConfig(
        controller=KubernetesReleaseControllerConfig(
            namespace=_required(values, "FOUNDRY_LITE_KUBERNETES_RELEASE_NAMESPACE"),
            signature_issuer=_required(values, "FOUNDRY_LITE_KUBERNETES_SIGNATURE_ISSUER"),
            signature_identity_regexp=_required(values, "FOUNDRY_LITE_KUBERNETES_SIGNATURE_IDENTITY_REGEXP"),
            timeout_seconds=_float(values, "FOUNDRY_LITE_KUBERNETES_CONTROLLER_TIMEOUT_SECONDS", 20.0),
            rollout_timeout_seconds=_float(values, "FOUNDRY_LITE_KUBERNETES_ROLLOUT_TIMEOUT_SECONDS", 600.0),
            require_signature=_boolean(values, "FOUNDRY_LITE_KUBERNETES_REQUIRE_SIGNATURE", True),
        ),
        poll_seconds=_float(values, "FOUNDRY_LITE_KUBERNETES_CONTROLLER_POLL_SECONDS", 5.0),
        max_iterations=_integer(values, "FOUNDRY_LITE_KUBERNETES_CONTROLLER_MAX_ITERATIONS", 0),
    )


def run_controller(config: KubernetesReleaseWorkerConfig) -> Literal[0, 1]:
    if config.poll_seconds <= 0 or config.poll_seconds > 60 or config.max_iterations < 0:
        raise ValueError("invalid_controller_loop_configuration")
    controller = KubernetesReleaseController(config.controller)
    iteration = 0
    while config.max_iterations == 0 or iteration < config.max_iterations:
        iteration += 1
        try:
            results = controller.reconcile_once()
            payload = {"event": "reconcile", "iteration": iteration, "results": _results(results)}
            print(json.dumps(payload, sort_keys=True))
        except (RuntimeError, ValueError) as exc:
            reason = _controller_error_reason(exc)
            print(json.dumps({"event": "controller_error", "iteration": iteration, "reason": reason}))
            if config.max_iterations == 1:
                return 1
        if config.max_iterations == 0 or iteration < config.max_iterations:
            time.sleep(config.poll_seconds)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reconcile FoundryDeployment resources to verified OCI digests.")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = config_from_env()
        if args.once:
            config = KubernetesReleaseWorkerConfig(config.controller, config.poll_seconds, 1)
        return run_controller(config)
    except ValueError:
        print(json.dumps({"event": "configuration_error", "reason": "invalid_or_missing_configuration"}))
        return 2


def _results(results: tuple[KubernetesReleaseControllerResult, ...]) -> list[Mapping[str, object]]:
    return [asdict(result) for result in results]


def _controller_error_reason(exc: RuntimeError | ValueError) -> str:
    if isinstance(exc, KubernetesTransportError):
        return f"kubernetes_transport_{exc.kind}"
    return "reconcile_failed"


def _required(values: Mapping[str, str], name: str) -> str:
    value = values.get(name, "").strip()
    if not value:
        raise ValueError("required_controller_configuration_missing")
    return value


def _float(values: Mapping[str, str], name: str, default: float) -> float:
    try:
        return float(values.get(name, str(default)))
    except ValueError as exc:
        raise ValueError("invalid_controller_number") from exc


def _integer(values: Mapping[str, str], name: str, default: int) -> int:
    try:
        return int(values.get(name, str(default)))
    except ValueError as exc:
        raise ValueError("invalid_controller_number") from exc


def _boolean(values: Mapping[str, str], name: str, is_enabled_by_default: bool) -> bool:
    value = values.get(name, str(is_enabled_by_default)).strip().lower()
    if value in {"true", "1", "yes"}:
        return True
    if value in {"false", "0", "no"}:
        return False
    raise ValueError("invalid_controller_boolean")


if __name__ == "__main__":
    raise SystemExit(main())
