"""Compose prompt and backup artifact stores without growing the runtime root."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from foundry_lite.application.ports.backup_artifact_store import BackupArtifactStore
from foundry_lite.application.ports.prompt_artifact_store import PromptArtifactStore
from foundry_lite.application.ports.secret_provider import SecretProvider
from foundry_lite.infrastructure.adapters.local_backup_artifact_store import LocalBackupArtifactStore
from foundry_lite.infrastructure.adapters.local_prompt_artifact_store import LocalPromptArtifactStore
from foundry_lite.infrastructure.adapters.s3_backup_artifact_store import (
    S3BackupArtifactStore,
    S3BackupArtifactStoreConfig,
)
from foundry_lite.infrastructure.adapters.s3_prompt_artifact_store import (
    S3PromptArtifactStore,
    S3PromptArtifactStoreConfig,
)

_PROMPT_BACKEND_ENV = "FOUNDRY_LITE_PROMPT_ARTIFACT_BACKEND"
_BACKUP_BACKEND_ENV = "FOUNDRY_LITE_BACKUP_ARTIFACT_BACKEND"
ALLOW_LOCAL_PROMPT_ARTIFACT_KEY_ENV = "FOUNDRY_LITE_ALLOW_LOCAL_PROMPT_ARTIFACT_KEY"


def is_local_prompt_artifact_key_allowed(environment: Mapping[str, str] = os.environ) -> bool:
    """Return whether a local-only prompt encryption key fallback was explicitly allowed."""

    return environment.get(ALLOW_LOCAL_PROMPT_ARTIFACT_KEY_ENV, "").strip().casefold() in {"1", "true", "yes"}


def artifact_stores(
    prompt_root: Path,
    backup_root: Path,
    secret_provider: SecretProvider,
    environment: Mapping[str, str] = os.environ,
) -> tuple[PromptArtifactStore, BackupArtifactStore]:
    """Build independently selected prompt and backup stores."""

    return (
        _prompt_store(prompt_root, secret_provider, environment),
        _backup_store(backup_root, environment),
    )


def reject_protected_local_artifact_backends(environment: Mapping[str, str]) -> None:
    """Require durable object storage for every protected artifact family."""

    required = {_PROMPT_BACKEND_ENV: "s3", _BACKUP_BACKEND_ENV: "s3"}
    invalid = [
        f"{key}={environment.get(key, 'local')} (required: {value})"
        for key, value in required.items()
        if environment.get(key, "local") != value
    ]
    if invalid:
        raise ValueError("production runtime requires durable S3 artifact backends; " + "; ".join(invalid))


def _prompt_store(
    root: Path,
    secret_provider: SecretProvider,
    environment: Mapping[str, str],
) -> PromptArtifactStore:
    backend = _profile(environment.get(_PROMPT_BACKEND_ENV, "local"))
    if backend == "local":
        is_fallback_allowed = is_local_prompt_artifact_key_allowed(environment)
        return LocalPromptArtifactStore(root, secret_provider, allow_local_dev_fallback=is_fallback_allowed)
    if backend == "s3":
        return S3PromptArtifactStore(_s3_prompt_config(environment), secret_provider)
    raise ValueError(f"unknown prompt artifact backend: {backend}")


def _backup_store(root: Path, environment: Mapping[str, str]) -> BackupArtifactStore:
    backend = _profile(environment.get(_BACKUP_BACKEND_ENV, "local"))
    if backend == "local":
        return LocalBackupArtifactStore(root)
    if backend == "s3":
        return S3BackupArtifactStore(_s3_backup_config(environment))
    raise ValueError(f"unknown backup artifact backend: {backend}")


def _s3_prompt_config(environment: Mapping[str, str]) -> S3PromptArtifactStoreConfig:
    return S3PromptArtifactStoreConfig(
        bucket=environment["FOUNDRY_LITE_S3_BUCKET"],
        endpoint_url=environment.get("FOUNDRY_LITE_S3_ENDPOINT_URL"),
        access_key_id=environment.get("FOUNDRY_LITE_S3_ACCESS_KEY_ID"),
        secret_access_key=environment.get("FOUNDRY_LITE_S3_SECRET_ACCESS_KEY"),
        region_name=environment.get("FOUNDRY_LITE_S3_REGION", "us-east-1"),
        prefix=f"{environment.get('FOUNDRY_LITE_S3_PREFIX', 'foundry-lite')}/prompt-artifacts",
    )


def _s3_backup_config(environment: Mapping[str, str]) -> S3BackupArtifactStoreConfig:
    return S3BackupArtifactStoreConfig(
        bucket=environment["FOUNDRY_LITE_S3_BUCKET"],
        endpoint_url=environment.get("FOUNDRY_LITE_S3_ENDPOINT_URL"),
        access_key_id=environment.get("FOUNDRY_LITE_S3_ACCESS_KEY_ID"),
        secret_access_key=environment.get("FOUNDRY_LITE_S3_SECRET_ACCESS_KEY"),
        region_name=environment.get("FOUNDRY_LITE_S3_REGION", "us-east-1"),
        prefix=f"{environment.get('FOUNDRY_LITE_S3_PREFIX', 'foundry-lite')}/backup-artifacts",
    )


def _profile(value: str) -> str:
    normalized = value.strip().casefold()
    return normalized or "local"
