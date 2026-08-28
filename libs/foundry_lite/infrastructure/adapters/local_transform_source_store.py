"""Local filesystem implementation of the transform source storage port."""

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
from foundry_lite.application.ports.transform_source_store import (
    TransformSourceArtifact,
    TransformSourceContent,
    TransformSourceRead,
    TransformSourceWrite,
)
from foundry_lite.domain.transform import safe_transform_path_token


class LocalTransformSourceStore:
    """Persist registered transform source beneath a tenant-scoped local root."""

    profile_name = "local-transform-source-store"

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    operation="write_source",
                    kind="unavailable",
                    is_retryable=True,
                    operator_message="Transform source storage is unavailable; retry the same registration request.",
                ),
                AdapterFailureMode(
                    operation="read_source",
                    kind="not_found",
                    is_retryable=False,
                    operator_message="Transform source is missing; restore or register the definition again.",
                ),
                AdapterFailureMode(
                    operation="read_source",
                    kind="validation",
                    is_retryable=False,
                    operator_message="Transform source is not valid UTF-8; register a valid source artifact.",
                ),
                AdapterFailureMode(
                    operation="read_source",
                    kind="unavailable",
                    is_retryable=True,
                    operator_message="Transform source storage is unavailable; retry the same execution request.",
                ),
            ),
        )

    def write_source(self, request: TransformSourceWrite) -> TransformSourceArtifact:
        data = request.source_code.encode("utf-8")
        path = self._source_path(request)
        try:
            self._write_atomically(path, data)
        except OSError as exc:
            raise AdapterError(
                AdapterFailure(
                    adapter_profile=self.profile_name,
                    operation="write_source",
                    kind="unavailable",
                    is_retryable=True,
                    operator_message="Transform source storage is unavailable; retry the same registration request.",
                )
            ) from exc
        return TransformSourceArtifact(
            entrypoint=str(path),
            content_hash=f"sha256:{hashlib.sha256(data).hexdigest()}",
            byte_size=len(data),
        )

    def read_source(self, request: TransformSourceRead) -> TransformSourceContent:
        try:
            data = Path(request.entrypoint).read_bytes()
        except FileNotFoundError as exc:
            raise self._read_error(
                "not_found",
                False,
                "Transform source is missing; restore or register the definition again.",
            ) from exc
        except OSError as exc:
            raise self._read_error(
                "unavailable",
                True,
                "Transform source storage is unavailable; retry the same execution request.",
            ) from exc
        try:
            source_code = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise self._read_error(
                "validation",
                False,
                "Transform source is not valid UTF-8; register a valid source artifact.",
            ) from exc
        return TransformSourceContent(
            source_code=source_code,
            content_hash=f"sha256:{hashlib.sha256(data).hexdigest()}",
            byte_size=len(data),
        )

    def _read_error(self, kind: AdapterFailureKind, is_retryable: bool, message: str) -> AdapterError:
        return AdapterError(
            AdapterFailure(
                adapter_profile=self.profile_name,
                operation="read_source",
                kind=kind,
                is_retryable=is_retryable,
                operator_message=message,
            )
        )

    def _source_path(self, request: TransformSourceWrite) -> Path:
        tenant_slug = safe_transform_path_token(request.tenant_id, "tenant_id")
        api_slug = safe_transform_path_token(request.api_name, "api_name")
        digest = hashlib.sha256(f"{request.tenant_id}:{request.api_name}".encode()).hexdigest()[:12]
        extension = "py" if request.language == "python" else "sql"
        return self._root / "registered-transforms" / tenant_slug / f"{api_slug}-{digest}.{extension}"

    @staticmethod
    def _write_atomically(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        temporary.write_bytes(data)
        temporary.replace(path)
