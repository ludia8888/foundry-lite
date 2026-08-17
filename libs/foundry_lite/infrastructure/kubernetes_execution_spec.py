"""Fail-closed translation from the shared sandbox argv into a Kubernetes Job."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from foundry_lite.infrastructure.adapters.container_code_execution_runtime import (
    FUNCTION_IPC_DIR,
    JOB_DIR,
    NODE_SANDBOX_ENVIRONMENT,
    OUTPUT_DIR,
    OUTPUT_NAME,
    RESULT_NAME,
    SANDBOX_ENVIRONMENT,
    TMP_DIR,
)
from foundry_lite.infrastructure.adapters.container_trained_model_runtime import (
    MODEL_ENVIRONMENT,
    MODEL_INPUT_PATH,
    MODEL_RESULT_PATH,
    MODEL_TMP_DIR,
)

_NAME = re.compile(r"^foundry-lite-(?:python|function|model)-[0-9a-f]{32}$")
_DIGEST_IMAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[0-9a-f]{64}$")
_INPUT_TARGET = re.compile(r"^/sandbox-inputs/input-[0-9]{4}\.parquet$")
_KUBERNETES_DNS_SUBDOMAIN = re.compile(
    r"^(?=.{1,253}$)[a-z0-9](?:[-a-z0-9]*[a-z0-9])?(?:\.[a-z0-9](?:[-a-z0-9]*[a-z0-9])?)*$"
)
_REQUIRED_FLAGS = frozenset(
    {
        "--pull=never",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        "--init",
    }
)
_VALUE_PREFIXES = (
    "--user=",
    "--cpus=",
    "--memory=",
    "--memory-swap=",
    "--pids-limit=",
    "--ulimit=fsize=",
)
_OPTIONAL_EXACT_FLAGS = frozenset({"--workdir=/sandbox-job"})
_MAX_CPU = Decimal("4")
_MAX_MEMORY_MIB = 8192
_MAX_PIDS = 512
_MAX_TMPFS_MIB = 1024
_MAX_FILE_BYTES = 1024 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class KubernetesExecutionMount:
    """One validated shared-PVC mount translated from the sandbox contract."""

    source: Path
    target: str
    sub_path: str
    is_read_only: bool


@dataclass(frozen=True, slots=True)
class KubernetesExecutionSpec:
    """Complete immutable security and resource contract for one Job."""

    name: str
    image_reference: str
    arguments: tuple[str, ...]
    mounts: tuple[KubernetesExecutionMount, ...]
    timeout_seconds: int
    uid: int
    gid: int
    cpu: str
    memory: str
    pids_limit: int
    tmpfs_size: str
    max_file_bytes: int
    tmp_mount_path: str


def parse_kubernetes_execution_command(
    command: Sequence[str],
    *,
    timeout_seconds: float,
    shared_workspace_root: Path,
    pvc_mount_root: Path,
) -> KubernetesExecutionSpec:
    """Validate every capability before constructing an executable Job spec."""

    name, flags, mount_values, image, arguments = _command_parts(command)
    _validate_name_and_image(name, image)
    _validate_flags(flags)
    uid, gid = _user(flags)
    cpu = _cpu(flags)
    memory = _memory(flags)
    pids_limit = _bounded_int(_flag_value(flags, "--pids-limit="), "pids limit", _MAX_PIDS)
    tmpfs_size = _tmpfs_size(flags)
    max_file_bytes = _max_file_bytes(flags)
    execution_kind = _validate_arguments(arguments, flags)
    mounts = _mounts(mount_values, shared_workspace_root, pvc_mount_root, execution_kind)
    bounded_timeout = int(timeout_seconds)
    if bounded_timeout < 1 or bounded_timeout > 3600:
        raise ValueError("Kubernetes execution timeout must be between 1 and 3600 seconds")
    return KubernetesExecutionSpec(
        name=name,
        image_reference=image,
        arguments=arguments,
        mounts=mounts,
        timeout_seconds=bounded_timeout,
        uid=uid,
        gid=gid,
        cpu=cpu,
        memory=memory,
        pids_limit=pids_limit,
        tmpfs_size=tmpfs_size,
        max_file_bytes=max_file_bytes,
        tmp_mount_path=MODEL_TMP_DIR if execution_kind == "model" else TMP_DIR,
    )


def kubernetes_execution_job_payload(
    spec: KubernetesExecutionSpec,
    *,
    namespace: str,
    pvc_name: str,
    image_pull_secrets: tuple[str, ...] = (),
) -> dict[str, object]:
    """Build one non-retrying, no-token, read-only, default-denied Job."""

    labels = {
        "app.kubernetes.io/name": "foundry-lite",
        "app.kubernetes.io/component": "execution-sandbox",
        "foundry-lite.io/execution-sandbox": "true",
        "foundry-lite.io/execution-name": spec.name,
    }
    validated_pull_secrets = validate_kubernetes_image_pull_secrets(image_pull_secrets)
    spec_hash = kubernetes_execution_spec_hash(spec, image_pull_secrets=validated_pull_secrets)
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": spec.name,
            "namespace": namespace,
            "labels": labels,
            "annotations": {"foundry-lite.io/execution-spec-sha256": spec_hash},
        },
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": spec.timeout_seconds,
            "ttlSecondsAfterFinished": 3600,
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "hostNetwork": False,
                    "hostPID": False,
                    "hostIPC": False,
                    "restartPolicy": "Never",
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": spec.uid,
                        "runAsGroup": spec.gid,
                        "fsGroup": spec.gid,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [
                        {
                            "name": "runner",
                            "image": spec.image_reference,
                            "imagePullPolicy": "IfNotPresent",
                            "command": _bounded_runner_command(spec),
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "readOnlyRootFilesystem": True,
                                "runAsNonRoot": True,
                                "runAsUser": spec.uid,
                                "runAsGroup": spec.gid,
                                "capabilities": {"drop": ["ALL"]},
                            },
                            "resources": {
                                "requests": {"cpu": spec.cpu, "memory": spec.memory},
                                "limits": {"cpu": spec.cpu, "memory": spec.memory},
                            },
                            "volumeMounts": [
                                *[_volume_mount(mount) for mount in spec.mounts],
                                {"name": "sandbox-tmp", "mountPath": spec.tmp_mount_path},
                            ],
                        }
                    ],
                    "volumes": [
                        {"name": "runtime", "persistentVolumeClaim": {"claimName": pvc_name}},
                        {
                            "name": "sandbox-tmp",
                            "emptyDir": {"medium": "Memory", "sizeLimit": spec.tmpfs_size},
                        },
                    ],
                    **(
                        {"imagePullSecrets": [{"name": name} for name in validated_pull_secrets]}
                        if validated_pull_secrets
                        else {}
                    ),
                },
            },
        },
    }


def kubernetes_execution_spec_hash(
    spec: KubernetesExecutionSpec,
    *,
    image_pull_secrets: tuple[str, ...] = (),
) -> str:
    """Fingerprint every execution field used for idempotent Job reconciliation."""

    payload = {
        "name": spec.name,
        "imageReference": spec.image_reference,
        "arguments": spec.arguments,
        "mounts": [
            {"target": item.target, "subPath": item.sub_path, "isReadOnly": item.is_read_only} for item in spec.mounts
        ],
        "timeoutSeconds": spec.timeout_seconds,
        "uid": spec.uid,
        "gid": spec.gid,
        "cpu": spec.cpu,
        "memory": spec.memory,
        "pidsLimit": spec.pids_limit,
        "tmpfsSize": spec.tmpfs_size,
        "maxFileBytes": spec.max_file_bytes,
        "tmpMountPath": spec.tmp_mount_path,
        "imagePullSecrets": validate_kubernetes_image_pull_secrets(image_pull_secrets),
    }
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def validate_kubernetes_image_pull_secrets(values: tuple[str, ...]) -> tuple[str, ...]:
    """Validate bounded Kubernetes Secret references used by dynamically created Jobs."""

    if len(values) > 8 or len(set(values)) != len(values):
        raise ValueError("Kubernetes image pull secrets are invalid")
    if any(_KUBERNETES_DNS_SUBDOMAIN.fullmatch(value) is None for value in values):
        raise ValueError("Kubernetes image pull secret name is invalid")
    return values


def _command_parts(
    command: Sequence[str],
) -> tuple[str, tuple[str, ...], tuple[str, ...], str, tuple[str, ...]]:
    if len(command) < 10 or tuple(command[:2]) != ("kubernetes-job-client", "run"):
        raise ValueError("unsupported Kubernetes execution command")
    flags: list[str] = []
    mounts: list[str] = []
    name = ""
    index = 2
    while index < len(command):
        value = command[index]
        if value == "--name" or value == "--mount":
            if index + 1 >= len(command):
                raise ValueError("Kubernetes execution flag value is missing")
            if value == "--name":
                name = command[index + 1]
            else:
                mounts.append(command[index + 1])
            index += 2
            continue
        if value.startswith("--"):
            flags.append(value)
            index += 1
            continue
        image = value
        arguments = tuple(command[index + 1 :])
        return name, tuple(flags), tuple(mounts), image, arguments
    raise ValueError("Kubernetes execution image is missing")


def _validate_name_and_image(name: str, image: str) -> None:
    if _NAME.fullmatch(name) is None:
        raise ValueError("Kubernetes execution name is invalid")
    if _DIGEST_IMAGE.fullmatch(image) is None:
        raise ValueError("Kubernetes execution image must be pinned by sha256 digest")


def _validate_flags(flags: Sequence[str]) -> None:
    _validate_required_flags(flags)
    _validate_allowed_flags(flags)
    _validate_resource_flags(flags)
    memory = _flag_value(flags, "--memory=")
    if _flag_value(flags, "--memory-swap=") != memory:
        raise ValueError("Kubernetes execution memory and swap bounds disagree")


def _validate_required_flags(flags: Sequence[str]) -> None:
    values = set(flags)
    if not _REQUIRED_FLAGS.issubset(values):
        raise ValueError("Kubernetes execution security flags are incomplete")
    if any(flags.count(value) != 1 for value in _REQUIRED_FLAGS):
        raise ValueError("Kubernetes execution security flag is duplicated")


def _validate_allowed_flags(flags: Sequence[str]) -> None:
    unknown = [value for value in flags if not _is_allowed_flag(value)]
    if unknown:
        raise ValueError("Kubernetes execution command contains an unsupported flag")


def _validate_resource_flags(flags: Sequence[str]) -> None:
    for prefix in _VALUE_PREFIXES:
        if sum(value.startswith(prefix) for value in flags) != 1:
            raise ValueError("Kubernetes execution resource flag is missing or duplicated")


def _is_allowed_flag(value: str) -> bool:
    return bool(
        value in _REQUIRED_FLAGS
        or value in _OPTIONAL_EXACT_FLAGS
        or value.startswith(_VALUE_PREFIXES)
        or value.startswith((f"--tmpfs={TMP_DIR}:", f"--tmpfs={MODEL_TMP_DIR}:"))
    )


def _validate_arguments(arguments: tuple[str, ...], flags: Sequence[str]) -> str:
    python_suffixes = {
        (
            "python",
            "/opt/foundry-lite/runner/python_transform_runner.py",
            f"{JOB_DIR}/request.json",
            f"{OUTPUT_DIR}/{RESULT_NAME}",
        ),
        (
            "python",
            "/opt/foundry-lite/runner/python_function_runner.py",
            f"{JOB_DIR}/request.json",
            f"{OUTPUT_DIR}/{RESULT_NAME}",
        ),
    }
    node_suffix = (
        "node",
        "/opt/foundry-lite/runner/typescript_function_runner.mjs",
        f"{JOB_DIR}/request.json",
        f"{OUTPUT_DIR}/{RESULT_NAME}",
    )
    if len(arguments) < 6 or arguments[:2] != ("/usr/bin/env", "-i"):
        raise ValueError("Kubernetes execution environment reset is missing")
    suffix = arguments[-4:]
    environment = _environment(arguments[2:-4])
    if _is_code_runner(suffix, environment, python_suffixes, node_suffix):
        _require_code_workdir(flags)
        return "code"
    if _is_model_runner(suffix, environment, flags):
        return "model"
    raise ValueError("Kubernetes execution runner or environment allowlist is invalid")


def _is_code_runner(
    suffix: tuple[str, ...],
    environment: dict[str, str],
    python_suffixes: set[tuple[str, str, str, str]],
    node_suffix: tuple[str, str, str, str],
) -> bool:
    is_python = suffix in python_suffixes and environment == dict(SANDBOX_ENVIRONMENT)
    is_node = suffix == node_suffix and environment == dict(NODE_SANDBOX_ENVIRONMENT)
    return is_python or is_node


def _require_code_workdir(flags: Sequence[str]) -> None:
    if "--workdir=/sandbox-job" not in flags:
        raise ValueError("Kubernetes execution sandbox workdir is missing")


def _is_model_runner(suffix: tuple[str, ...], environment: dict[str, str], flags: Sequence[str]) -> bool:
    if len(suffix) != 4:
        return False
    runner = suffix[1]
    is_allowed_runner = re.fullmatch(r"/opt/foundry-lite/model/[A-Za-z0-9_.-]+\.py", runner) is not None
    has_model_io = suffix[0] == "python" and suffix[2:] == (MODEL_INPUT_PATH, MODEL_RESULT_PATH)
    return (
        is_allowed_runner
        and has_model_io
        and environment == dict(MODEL_ENVIRONMENT)
        and "--workdir=/sandbox-job" not in flags
    )


def _environment(values: Sequence[str]) -> dict[str, str]:
    environment: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("Kubernetes execution environment entry is invalid")
        key, item = value.split("=", 1)
        if not key or key in environment:
            raise ValueError("Kubernetes execution environment entry is duplicated")
        environment[key] = item
    return environment


def _mounts(
    values: Sequence[str],
    shared_workspace_root: Path,
    pvc_mount_root: Path,
    execution_kind: str,
) -> tuple[KubernetesExecutionMount, ...]:
    shared_root = shared_workspace_root.resolve(strict=True)
    pvc_root = pvc_mount_root.resolve(strict=True)
    if shared_root != pvc_root and pvc_root not in shared_root.parents:
        raise ValueError("Kubernetes execution workspace must live on the configured PVC mount")
    parsed = tuple(_mount(value, shared_root, pvc_root) for value in values)
    targets = [mount.target for mount in parsed]
    required = (
        {MODEL_INPUT_PATH, MODEL_RESULT_PATH} if execution_kind == "model" else {JOB_DIR, f"{OUTPUT_DIR}/{RESULT_NAME}"}
    )
    if not required.issubset(targets) or len(targets) != len(set(targets)):
        raise ValueError("Kubernetes execution mounts are incomplete or duplicated")
    return parsed


def _mount(value: str, shared_root: Path, pvc_root: Path) -> KubernetesExecutionMount:
    fields, is_read_only = _mount_options(value)
    source, target = _mount_coordinates(fields, shared_root)
    _validate_mount_mode(target, is_read_only)
    return KubernetesExecutionMount(
        source=source,
        target=target,
        sub_path=str(source.relative_to(pvc_root)),
        is_read_only=is_read_only,
    )


def _mount_options(value: str) -> tuple[dict[str, str], bool]:
    fields: dict[str, str] = {}
    is_read_only = False
    for part in value.split(","):
        if part == "readonly":
            if is_read_only:
                raise ValueError("Kubernetes execution mount option is duplicated")
            is_read_only = True
        elif "=" in part:
            key, item = part.split("=", 1)
            if key not in {"type", "source", "target"} or key in fields:
                raise ValueError("Kubernetes execution mount option is invalid or duplicated")
            fields[key] = item
        else:
            raise ValueError("Kubernetes execution mount option is invalid")
    return fields, is_read_only


def _mount_coordinates(fields: dict[str, str], shared_root: Path) -> tuple[Path, str]:
    if fields.get("type") != "bind" or not fields.get("source") or not fields.get("target"):
        raise ValueError("Kubernetes execution mount contract is invalid")
    source = Path(fields["source"]).resolve(strict=True)
    if shared_root not in source.parents:
        raise ValueError("Kubernetes execution mount escapes the shared workspace")
    target = fields["target"]
    return source, target


def _validate_mount_mode(target: str, is_read_only: bool) -> None:
    is_input = _INPUT_TARGET.fullmatch(target) is not None
    if target == JOB_DIR or is_input:
        if not is_read_only:
            raise ValueError("Kubernetes execution input mounts must be read-only")
        return
    if target == MODEL_INPUT_PATH:
        if not is_read_only:
            raise ValueError("Kubernetes execution model input must be read-only")
        return
    if target == MODEL_RESULT_PATH:
        if is_read_only:
            raise ValueError("Kubernetes execution model result must be writable")
        return
    if target in {f"{OUTPUT_DIR}/{OUTPUT_NAME}", f"{OUTPUT_DIR}/{RESULT_NAME}", FUNCTION_IPC_DIR}:
        if is_read_only:
            raise ValueError("Kubernetes execution output mounts must be writable")
        return
    raise ValueError("Kubernetes execution mount target is not allowed")


def _user(flags: Sequence[str]) -> tuple[int, int]:
    value = _flag_value(flags, "--user=")
    if ":" not in value:
        raise ValueError("Kubernetes execution user must include UID and GID")
    uid, gid = (_bounded_int(item, "sandbox identity", 2_147_483_647) for item in value.split(":", 1))
    return uid, gid


def _cpu(flags: Sequence[str]) -> str:
    value = _flag_value(flags, "--cpus=")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("Kubernetes execution CPU limit is invalid") from exc
    if not parsed.is_finite() or parsed <= 0 or parsed > _MAX_CPU:
        raise ValueError("Kubernetes execution CPU limit is out of range")
    return value


def _memory(flags: Sequence[str]) -> str:
    value = _flag_value(flags, "--memory=")
    if not value.endswith("m"):
        raise ValueError("Kubernetes execution memory must be expressed in megabytes")
    return f"{_bounded_int(value[:-1], 'memory', _MAX_MEMORY_MIB)}Mi"


def _tmpfs_size(flags: Sequence[str]) -> str:
    sandbox_matches = [value for value in flags if value.startswith(f"--tmpfs={TMP_DIR}:")]
    model_matches = [value for value in flags if value.startswith(f"--tmpfs={MODEL_TMP_DIR}:")]
    if len(sandbox_matches) + len(model_matches) != 1:
        raise ValueError("Kubernetes execution tmpfs flag is missing or duplicated")
    prefix = f"--tmpfs={MODEL_TMP_DIR}:" if model_matches else f"--tmpfs={TMP_DIR}:"
    return _tmpfs_limit(_flag_value(flags, prefix))


def _tmpfs_limit(value: str) -> str:
    parts = value.split(",")
    required = {"rw", "noexec", "nosuid", "nodev"}
    size_items = [item for item in parts if item.startswith("size=")]
    if set(parts) != required | set(size_items) or len(size_items) != 1:
        raise ValueError("Kubernetes execution tmpfs security flags are incomplete")
    size = size_items[0].removeprefix("size=")
    if not size.endswith("m"):
        raise ValueError("Kubernetes execution tmpfs size is invalid")
    return f"{_bounded_int(size[:-1], 'tmpfs size', _MAX_TMPFS_MIB)}Mi"


def _max_file_bytes(flags: Sequence[str]) -> int:
    value = _flag_value(flags, "--ulimit=fsize=")
    if ":" not in value:
        raise ValueError("Kubernetes execution file-size limit is incomplete")
    soft, hard = value.split(":", 1)
    if soft != hard:
        raise ValueError("Kubernetes execution file-size soft and hard limits disagree")
    return _bounded_int(soft, "file-size limit", _MAX_FILE_BYTES)


def _flag_value(flags: Sequence[str], prefix: str) -> str:
    matches = [value.removeprefix(prefix) for value in flags if value.startswith(prefix)]
    if len(matches) != 1 or not matches[0]:
        raise ValueError("Kubernetes execution resource flag is missing or duplicated")
    return matches[0]


def _positive_int(value: str, label: str) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise ValueError(f"Kubernetes execution {label} must be an integer") from exc
    if result <= 0:
        raise ValueError(f"Kubernetes execution {label} must be positive")
    return result


def _bounded_int(value: str, label: str, maximum: int) -> int:
    result = _positive_int(value, label)
    if result > maximum:
        raise ValueError(f"Kubernetes execution {label} exceeds the allowed maximum")
    return result


def _volume_mount(mount: KubernetesExecutionMount) -> dict[str, object]:
    return {
        "name": "runtime",
        "mountPath": mount.target,
        "subPath": mount.sub_path,
        "readOnly": mount.is_read_only,
    }


def _bounded_runner_command(spec: KubernetesExecutionSpec) -> list[str]:
    block_limit = (spec.max_file_bytes + 511) // 512
    return [
        "/bin/sh",
        "-c",
        'ulimit -f "$1"; shift; exec "$@"',
        "foundry-lite-file-limit",
        str(block_limit),
        *spec.arguments,
    ]
