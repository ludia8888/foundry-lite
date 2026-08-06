"""Application port for isolated user-code execution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract
from foundry_lite.application.ports.compute_adapter import PythonTransformPlan, TransformExecutionResult

CodeExecutionFailureType = Literal[
    "runtime_unavailable",
    "sandbox_timeout",
    "resource_limit",
    "runner_contract_error",
    "user_code_error",
    "output_validation_error",
]


@dataclass(frozen=True)
class CodeExecutionSandboxPolicy:
    """Security and resource controls required for one isolated execution."""

    non_root_uid: int
    non_root_gid: int
    cpu_count: float
    memory_mb: int
    pids_limit: int
    timeout_seconds: int
    tmpfs_mb: int
    is_network_disabled: bool
    is_root_filesystem_read_only: bool
    is_capability_set_dropped: bool
    has_no_new_privileges: bool
    allowed_environment_keys: tuple[str, ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "nonRootUid": self.non_root_uid,
            "nonRootGid": self.non_root_gid,
            "cpuCount": self.cpu_count,
            "memoryMb": self.memory_mb,
            "pidsLimit": self.pids_limit,
            "timeoutSeconds": self.timeout_seconds,
            "tmpfsMb": self.tmpfs_mb,
            "networkDisabled": self.is_network_disabled,
            "rootFilesystemReadOnly": self.is_root_filesystem_read_only,
            "capabilitiesDropped": self.is_capability_set_dropped,
            "noNewPrivileges": self.has_no_new_privileges,
            "allowedEnvironmentKeys": list(self.allowed_environment_keys),
        }


@dataclass(frozen=True)
class CodeExecutionFailureEvidence:
    """Operator-safe evidence for a failed sandbox execution."""

    failure_type: CodeExecutionFailureType
    sandbox_policy: CodeExecutionSandboxPolicy
    runtime_profile: str
    image_reference: str
    exit_code: int | None = None
    signal_name: str | None = None
    stderr_sha256: str | None = None
    stderr_byte_count: int = 0
    runner_failure_type: str | None = None
    exception_type: str | None = None
    exception_message_sha256: str | None = None
    cleanup: Mapping[str, object] | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "failureType": self.failure_type,
            "runtimeProfile": self.runtime_profile,
            "imageReference": self.image_reference,
            "exitCode": self.exit_code,
            "signalName": self.signal_name,
            "stderrSha256": self.stderr_sha256,
            "stderrByteCount": self.stderr_byte_count,
            "runnerFailureType": self.runner_failure_type,
            "exceptionType": self.exception_type,
            "exceptionMessageSha256": self.exception_message_sha256,
            "cleanup": dict(self.cleanup) if self.cleanup is not None else None,
            "sandboxPolicy": self.sandbox_policy.to_payload(),
        }


@dataclass(frozen=True)
class FunctionExecutionPlan:
    """One ontology function to run in the sandbox, with its inputs already resolved.

    Inputs arrive materialized rather than as a query the sandbox could issue. The sandbox has
    no network, so every object and object set the function reads has already been resolved
    host-side through the governed object services under the caller's policy. That keeps the
    security boundary in one place: user code never holds a database handle or a credential,
    only a JSON document the platform decided it was allowed to see.

    The cost is that an object set is a list here, not the lazy handle Palantir gives a function,
    so a function cannot stream a collection larger than the host is willing to materialize.
    ``input_byte_limit`` is where that ceiling is enforced and reported.
    """

    function_api_name: str
    function_version: str
    entrypoint: str
    source: str
    inputs_json: Mapping[str, object]
    output_type: str
    timeout_seconds: int
    input_byte_limit: int


@dataclass(frozen=True)
class FunctionExecutionResult:
    """Sandbox outcome for one function call."""

    output: object
    stderr_byte_count: int
    duration_ms: int


class CodeExecutionAdapter(Protocol):
    """Boundary that executes untrusted Python outside the API process."""

    @property
    def profile_name(self) -> str: ...

    def failure_contract(self) -> AdapterFailureContract:
        """Return the stable failure taxonomy for the isolation runtime."""
        ...

    def execute_python_transform(self, plan: PythonTransformPlan) -> TransformExecutionResult:
        """Execute a pinned Python transform inside the configured sandbox."""
        ...

    def execute_function(self, plan: FunctionExecutionPlan) -> FunctionExecutionResult:
        """Execute one ontology function inside the same sandbox as a transform."""
        ...
