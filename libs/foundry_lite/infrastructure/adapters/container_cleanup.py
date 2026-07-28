"""Fail-closed container removal with redacted operator evidence."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from foundry_lite.infrastructure.adapters.container_code_execution_runtime import (
    ContainerCommandRunner,
)


@dataclass(frozen=True, slots=True)
class ContainerCleanupEvidence:
    """Proof that timeout cleanup either completed or needs operator attention."""

    status: Literal["CONFIRMED", "FAILED"]
    exit_code: int | None = None
    stderr_sha256: str | None = None
    stderr_byte_count: int = 0
    exception_type: str | None = None
    exception_message_sha256: str | None = None

    @property
    def is_confirmed(self) -> bool:
        return self.status == "CONFIRMED"

    def to_payload(self) -> dict[str, object]:
        return {
            "status": self.status,
            "exitCode": self.exit_code,
            "stderrSha256": self.stderr_sha256,
            "stderrByteCount": self.stderr_byte_count,
            "exceptionType": self.exception_type,
            "exceptionMessageSha256": self.exception_message_sha256,
        }


def force_remove_container(
    command_runner: ContainerCommandRunner,
    *,
    runtime_binary: str,
    container_name: str,
    timeout_seconds: float,
    environment: Mapping[str, str],
) -> ContainerCleanupEvidence:
    """Remove a named container and never expose its name or raw runtime output."""

    command = (runtime_binary, "rm", "--force", container_name)
    try:
        result = command_runner(command, timeout_seconds, environment)
    except Exception as exc:
        message = str(exc).encode()
        return ContainerCleanupEvidence(
            status="FAILED",
            exception_type=type(exc).__name__,
            exception_message_sha256=hashlib.sha256(message).hexdigest(),
        )
    stderr = result.stderr
    if result.return_code == 0:
        return ContainerCleanupEvidence(status="CONFIRMED", exit_code=0)
    return ContainerCleanupEvidence(
        status="FAILED",
        exit_code=result.return_code,
        stderr_sha256=hashlib.sha256(stderr).hexdigest() if stderr else None,
        stderr_byte_count=len(stderr),
    )
