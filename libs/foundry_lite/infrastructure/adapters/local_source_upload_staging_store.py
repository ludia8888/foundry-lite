"""Local filesystem implementation of transient Source upload staging."""

from __future__ import annotations

import errno
import hashlib
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from foundry_lite.application.ports.adapter_failure import (
    AdapterError,
    AdapterFailure,
    AdapterFailureContract,
    AdapterFailureKind,
    AdapterFailureMode,
)
from foundry_lite.application.ports.source_upload_staging_store import (
    SourceUploadStageRequest,
    StagedSourceArtifact,
)


class LocalSourceUploadStagingStore:
    """Stage uploads below one fixed root and remove only owned artifacts."""

    profile_name = "local-source-upload-staging-store"

    def __init__(self, root: str | Path) -> None:
        self._root = (Path(root) / "source-uploads").resolve()

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    operation="stage_uploads",
                    kind="validation",
                    is_retryable=False,
                    operator_message="Source upload location or stream is invalid; verify the upload request.",
                ),
                AdapterFailureMode(
                    operation="stage_uploads",
                    kind="unavailable",
                    is_retryable=True,
                    operator_message="Source upload staging is unavailable; retry the same request.",
                ),
                AdapterFailureMode(
                    operation="read_upload",
                    kind="validation",
                    is_retryable=False,
                    operator_message="Source upload location is outside the configured staging store.",
                ),
                AdapterFailureMode(
                    operation="read_upload",
                    kind="not_found",
                    is_retryable=False,
                    operator_message="Staged Source upload is missing.",
                ),
                AdapterFailureMode(
                    operation="read_upload",
                    kind="unavailable",
                    is_retryable=True,
                    operator_message="Source upload staging is unavailable; retry the same read.",
                ),
                AdapterFailureMode(
                    operation="cleanup_uploads",
                    kind="unavailable",
                    is_retryable=True,
                    operator_message="Source upload cleanup is unavailable; retry cleanup.",
                ),
            ),
        )

    def stage_uploads(self, requests: Sequence[SourceUploadStageRequest]) -> tuple[StagedSourceArtifact, ...]:
        staged: list[StagedSourceArtifact] = []
        try:
            for request in requests:
                staged.append(self._stage_upload(request))
        except Exception as exc:
            self._rollback_staged(staged)
            if isinstance(exc, AdapterError):
                raise
            raise self._error(
                "stage_uploads",
                "unavailable",
                True,
                "Source upload staging is unavailable; retry the same request.",
            ) from exc
        return tuple(staged)

    @contextmanager
    def materialize_path(self, storage_uri: str) -> Iterator[Path]:
        yield self._read_path(storage_uri)

    def open_upload(self, storage_uri: str) -> BinaryIO:
        path = self._read_path(storage_uri)
        try:
            return path.open("rb")
        except OSError as exc:
            raise self._error(
                "read_upload",
                "unavailable",
                True,
                "Source upload staging is unavailable; retry the same read.",
            ) from exc

    def cleanup_uploads(self, storage_uris: Sequence[str]) -> None:
        paths = [self._owned_path(storage_uri, operation="cleanup_uploads") for storage_uri in storage_uris]
        try:
            self._cleanup_paths(paths)
        except OSError as exc:
            raise self._error(
                "cleanup_uploads",
                "unavailable",
                True,
                "Source upload cleanup is unavailable; retry cleanup.",
            ) from exc

    def _stage_upload(self, request: SourceUploadStageRequest) -> StagedSourceArtifact:
        source_name = self._safe_source_name(request.source_name)
        file_name = self._safe_file_name(request.file_name)
        target = self._owned_path(str(self._root / source_name / f"upload-{uuid4().hex}-{file_name}"))
        temporary = target.with_suffix(f"{target.suffix}.part")
        digest = hashlib.sha256()
        byte_size = 0
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("xb") as output:
                for chunk in iter(lambda: request.source.read(1024 * 1024), b""):
                    if not isinstance(chunk, bytes):
                        raise TypeError("upload stream must return bytes")
                    byte_size += len(chunk)
                    digest.update(chunk)
                    output.write(chunk)
            temporary.replace(target)
        except (OSError, TypeError) as exc:
            temporary.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            kind: AdapterFailureKind = "validation" if isinstance(exc, TypeError) else "unavailable"
            raise self._error(
                "stage_uploads",
                kind,
                kind == "unavailable",
                "Source upload location or stream is invalid; verify the upload request."
                if kind == "validation"
                else "Source upload staging is unavailable; retry the same request.",
            ) from exc
        return StagedSourceArtifact(file_name, str(target), f"sha256:{digest.hexdigest()}", byte_size)

    def _read_path(self, storage_uri: str) -> Path:
        path = self._owned_path(storage_uri, operation="read_upload")
        if not path.is_file():
            raise self._error("read_upload", "not_found", False, "Staged Source upload is missing.")
        return path

    def _owned_path(self, storage_uri: str, *, operation: str = "stage_uploads") -> Path:
        path = Path(storage_uri).resolve()
        if not path.is_relative_to(self._root):
            raise self._error(
                operation,
                "validation",
                False,
                "Source upload location is outside the configured staging store.",
            )
        return path

    def _safe_source_name(self, value: str) -> str:
        if value and value not in {".", ".."} and "/" not in value and "\\" not in value:
            return value
        raise self._error(
            "stage_uploads",
            "validation",
            False,
            "Source upload location or stream is invalid; verify the upload request.",
        )

    def _safe_file_name(self, value: str) -> str:
        file_name = value.replace("\\", "/").rsplit("/", maxsplit=1)[-1]
        if file_name and file_name not in {".", ".."}:
            return file_name
        raise self._error(
            "stage_uploads",
            "validation",
            False,
            "Source upload location or stream is invalid; verify the upload request.",
        )

    def _rollback_staged(self, staged: Sequence[StagedSourceArtifact]) -> None:
        paths = [Path(artifact.storage_uri) for artifact in staged]
        try:
            self._cleanup_paths(paths)
        except OSError:
            return

    def _cleanup_paths(self, paths: Sequence[Path]) -> None:
        parents: set[Path] = set()
        for path in paths:
            path.unlink(missing_ok=True)
            parents.add(path.parent)
        for parent in parents:
            try:
                parent.rmdir()
            except FileNotFoundError:
                continue
            except OSError as exc:
                if exc.errno != errno.ENOTEMPTY:
                    raise

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
