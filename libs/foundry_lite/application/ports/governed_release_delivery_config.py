"""Server-owned target binding for external governed-release operations."""

from __future__ import annotations

import re
from dataclasses import dataclass

from foundry_lite.application.ports.source_control_release import (
    SourceControlMergeMethod,
    SourceRepositoryRef,
)

_SAFE_BRANCH_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
_SAFE_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclass(frozen=True, slots=True)
class GovernedReleaseDeliveryConfig:
    """Pin every open-world release target outside model-controlled input."""

    source_repository: SourceRepositoryRef | None = None
    source_base_ref: str = "main"
    source_head_prefix: str = "codex/"
    source_merge_method: SourceControlMergeMethod = SourceControlMergeMethod.SQUASH
    is_source_control_required: bool = False
    deployment_service_id: str | None = None
    deployment_environment: str = "production"
    is_deployment_required: bool = False

    def __post_init__(self) -> None:
        _validate_required_targets(self)
        _validate_target_names(self.source_base_ref, self.deployment_environment)

    @property
    def is_source_control_enabled(self) -> bool:
        return self.source_repository is not None

    @property
    def is_deployment_enabled(self) -> bool:
        return bool(self.deployment_service_id)

    def source_head_ref(self, branch_name: str) -> str:
        """Derive one bounded provider ref from server-read proposal identity."""

        if _SAFE_BRANCH_NAME.fullmatch(branch_name) is None or _unsafe_ref_fragment(branch_name):
            raise ValueError("release branch name cannot be mapped to a safe source-control ref")
        head_ref = f"{self.source_head_prefix}{branch_name}"
        if _SAFE_BRANCH_NAME.fullmatch(head_ref) is None or _unsafe_ref_fragment(head_ref):
            raise ValueError("configured source head prefix produces an unsafe ref")
        return head_ref


def _unsafe_ref_fragment(value: str) -> bool:
    return (
        value.startswith(("/", "."))
        or value.endswith(("/", ".", ".lock"))
        or ".." in value
        or "//" in value
        or "@{" in value
    )


def _validate_required_targets(config: GovernedReleaseDeliveryConfig) -> None:
    if config.is_source_control_required and config.source_repository is None:
        raise ValueError("required governed source control has no server-owned repository")
    if config.is_deployment_required and not config.deployment_service_id:
        raise ValueError("required governed deployment has no server-owned service")
    if config.deployment_service_id and config.source_repository is None:
        raise ValueError("governed deployment requires a source-control merge receipt")


def _validate_target_names(source_base_ref: str, deployment_environment: str) -> None:
    if not source_base_ref or not deployment_environment:
        raise ValueError("release base ref and deployment environment are required")
    if _SAFE_BRANCH_NAME.fullmatch(source_base_ref) is None or _unsafe_ref_fragment(source_base_ref):
        raise ValueError("release base ref is invalid")
    if _SAFE_ENVIRONMENT_NAME.fullmatch(deployment_environment) is None:
        raise ValueError("release deployment environment is invalid")


__all__ = ["GovernedReleaseDeliveryConfig"]
