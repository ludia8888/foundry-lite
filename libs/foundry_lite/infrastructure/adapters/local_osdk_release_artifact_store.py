"""Local filesystem implementation of OSDK release artifact persistence."""

from __future__ import annotations

import hashlib
from pathlib import Path

from foundry_lite.application.ports.adapter_failure import (
    AdapterError,
    AdapterFailure,
    AdapterFailureContract,
    AdapterFailureKind,
    AdapterFailureMode,
)
from foundry_lite.application.ports.osdk_release_artifact_store import (
    OsdkReleaseArtifactContent,
    OsdkReleaseArtifactWrite,
    OsdkStoredReleaseArtifact,
)


class LocalOsdkReleaseArtifactStore:
    """Store generated packages under a fixed local runtime root."""

    profile_name = "local-osdk-release-artifact-store"

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    operation="write_artifact",
                    kind="validation",
                    is_retryable=False,
                    operator_message="OSDK artifact location is invalid; verify release identifiers.",
                ),
                AdapterFailureMode(
                    operation="write_artifact",
                    kind="unavailable",
                    is_retryable=True,
                    operator_message="OSDK artifact storage is unavailable; retry the same release request.",
                ),
                AdapterFailureMode(
                    operation="read_artifact",
                    kind="validation",
                    is_retryable=False,
                    operator_message="OSDK artifact location is outside the configured store.",
                ),
                AdapterFailureMode(
                    operation="read_artifact",
                    kind="not_found",
                    is_retryable=False,
                    operator_message="OSDK release artifact bytes are missing.",
                ),
                AdapterFailureMode(
                    operation="read_artifact",
                    kind="unavailable",
                    is_retryable=True,
                    operator_message="OSDK artifact storage is unavailable; retry the same download request.",
                ),
            ),
        )

    def write_artifact(self, request: OsdkReleaseArtifactWrite) -> OsdkStoredReleaseArtifact:
        path = self._write_path(request)
        try:
            self._write_atomically(path, request.content)
        except OSError as exc:
            raise self._error(
                "write_artifact",
                "unavailable",
                True,
                "OSDK artifact storage is unavailable; retry the same release request.",
            ) from exc
        return OsdkStoredReleaseArtifact(
            storage_uri=str(path),
            content_hash=_content_hash(request.content),
            byte_size=len(request.content),
        )

    def read_artifact(self, storage_uri: str) -> OsdkReleaseArtifactContent:
        path = self._read_path(storage_uri)
        try:
            content = path.read_bytes()
        except FileNotFoundError as exc:
            raise self._error(
                "read_artifact",
                "not_found",
                False,
                "OSDK release artifact bytes are missing.",
            ) from exc
        except OSError as exc:
            raise self._error(
                "read_artifact",
                "unavailable",
                True,
                "OSDK artifact storage is unavailable; retry the same download request.",
            ) from exc
        return OsdkReleaseArtifactContent(content, _content_hash(content), len(content))

    def _write_path(self, request: OsdkReleaseArtifactWrite) -> Path:
        segments = (request.tenant_id, request.app_id, request.version, request.file_name)
        if any(not _is_safe_segment(segment) for segment in segments):
            raise self._error(
                "write_artifact",
                "validation",
                False,
                "OSDK artifact location is invalid; verify release identifiers.",
            )
        return self._root.joinpath(*segments)

    def _read_path(self, storage_uri: str) -> Path:
        path = Path(storage_uri).resolve()
        if not path.is_relative_to(self._root):
            raise self._error(
                "read_artifact",
                "validation",
                False,
                "OSDK artifact location is outside the configured store.",
            )
        return path

    def _error(
        self,
        operation: str,
        kind: AdapterFailureKind,
        is_retryable: bool,
        message: str,
    ) -> AdapterError:
        return AdapterError(
            AdapterFailure(
                adapter_profile=self.profile_name,
                operation=operation,
                kind=kind,
                is_retryable=is_retryable,
                operator_message=message,
            )
        )

    @staticmethod
    def _write_atomically(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        temporary.write_bytes(content)
        temporary.replace(path)


def _is_safe_segment(value: str) -> bool:
    return bool(value) and value not in {".", ".."} and "/" not in value and "\\" not in value


def _content_hash(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"
