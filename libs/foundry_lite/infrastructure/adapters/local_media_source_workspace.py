"""Local ephemeral workspace for processor-facing media source files."""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from foundry_lite.application.ports.adapter_failure import (
    AdapterError,
    AdapterFailure,
    AdapterFailureContract,
    AdapterFailureMode,
)
from foundry_lite.application.ports.media_source_workspace import (
    MaterializedMediaSource,
    MediaSourceWorkspaceRequest,
    MediaSourceWriter,
)


class LocalMediaSourceWorkspace:
    """Create one isolated local file and remove it after processor execution."""

    profile_name = "local-media-source-workspace"

    def __init__(self, root: str | Path | None = None) -> None:
        runtime_root = Path(root) if root is not None else Path(tempfile.gettempdir()) / "foundry-lite"
        self._root = (runtime_root / "media-source-workspaces").resolve()

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    operation="materialize",
                    kind="validation",
                    is_retryable=False,
                    operator_message="Media workspace file name is invalid; verify the processor request.",
                ),
                AdapterFailureMode(
                    operation="materialize",
                    kind="unavailable",
                    is_retryable=True,
                    operator_message="Media source workspace is unavailable; retry the same request.",
                ),
            ),
        )

    @contextmanager
    def materialize(
        self,
        request: MediaSourceWorkspaceRequest,
        write_source: MediaSourceWriter,
    ) -> Iterator[MaterializedMediaSource]:
        file_name = self._safe_file_name(request.file_name)
        workspace = self._create_workspace()
        try:
            source_path = Path(workspace.name) / file_name
            sink = self._open_sink(source_path)
            with sink:
                write_source(sink)
            yield MaterializedMediaSource(str(source_path))
        finally:
            self._cleanup_workspace(workspace)

    def _create_workspace(self) -> tempfile.TemporaryDirectory[str]:
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            return tempfile.TemporaryDirectory(prefix="media-", dir=self._root)
        except OSError as exc:
            raise self._unavailable_error() from exc

    def _open_sink(self, source_path: Path) -> BinaryIO:
        try:
            return source_path.open("xb")
        except OSError as exc:
            raise self._unavailable_error() from exc

    def _cleanup_workspace(self, workspace: tempfile.TemporaryDirectory[str]) -> None:
        try:
            workspace.cleanup()
        except OSError as exc:
            raise self._unavailable_error() from exc

    def _safe_file_name(self, value: str) -> str:
        if value and value not in {".", ".."} and "/" not in value and "\\" not in value:
            return value
        raise AdapterError(
            AdapterFailure(
                adapter_profile=self.profile_name,
                operation="materialize",
                kind="validation",
                is_retryable=False,
                operator_message="Media workspace file name is invalid; verify the processor request.",
            )
        )

    def _unavailable_error(self) -> AdapterError:
        return AdapterError(
            AdapterFailure(
                adapter_profile=self.profile_name,
                operation="materialize",
                kind="unavailable",
                is_retryable=True,
                operator_message="Media source workspace is unavailable; retry the same request.",
            )
        )
