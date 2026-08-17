"""Safety boundary shared by the dedicated Mac mini enterprise-QA tools."""

from __future__ import annotations

import json
import os
import pwd
import stat
import subprocess  # nosec B404 - bounded operator argv only; remove if arbitrary command input is introduced.
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

EXPECTED_USER = "sean1234"
EXPECTED_HOME = Path("/Users/sean1234")
QA_ROOT = EXPECTED_HOME / "foundry-qa"
COLIMA_PROFILE = "foundry-qa"
ALLOWED_NAMESPACES = frozenset({"foundry-qa", "foundry-qa-recovery"})
QA_DIRECTORIES = ("bin", "repo", "state", "evidence", "backups", "logs")


@dataclass(frozen=True, slots=True)
class CommandResult:
    argv: tuple[str, ...]
    return_code: int
    stdout: str
    stderr: str


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def assert_host_boundary() -> None:
    """Reject execution unless the exact Unix principal and home are active."""

    account = pwd.getpwuid(os.getuid())
    if account.pw_name != EXPECTED_USER or Path(account.pw_dir) != EXPECTED_HOME:
        raise RuntimeError("macmini_qa_wrong_unix_principal")
    if Path.home() != EXPECTED_HOME:
        raise RuntimeError("macmini_qa_wrong_home")
    if QA_ROOT.exists() and QA_ROOT.is_symlink():
        raise RuntimeError("macmini_qa_root_must_not_be_symlink")


def ensure_qa_directories() -> None:
    assert_host_boundary()
    QA_ROOT.mkdir(mode=0o700, parents=False, exist_ok=True)
    os.chmod(QA_ROOT, 0o700)
    for name in QA_DIRECTORIES:
        target = QA_ROOT / name
        target.mkdir(mode=0o700, exist_ok=True)
        if target.is_symlink() or target.resolve().parent != QA_ROOT:
            raise RuntimeError("macmini_qa_directory_boundary_violation")
        os.chmod(target, 0o700)
    _assert_private_directory(QA_ROOT)


def assert_namespace(namespace: str) -> None:
    if namespace not in ALLOWED_NAMESPACES:
        raise ValueError("macmini_qa_namespace_not_allowed")


def assert_profile(profile: str) -> None:
    if profile != COLIMA_PROFILE:
        raise ValueError("macmini_qa_colima_profile_not_allowed")


def run(argv: Sequence[str], *, timeout_seconds: float = 120.0) -> CommandResult:
    if not argv or any(not isinstance(item, str) or not item for item in argv):
        raise ValueError("macmini_qa_command_invalid")
    completed = subprocess.run(  # nosec B603 - argv tuple and no shell; remove if shell or string command appears.
        list(argv),
        check=False,
        capture_output=True,
        env=qa_command_environment(),
        text=True,
        timeout=timeout_seconds,
    )
    return CommandResult(tuple(argv), completed.returncode, completed.stdout, completed.stderr)


def qa_command_environment() -> dict[str, str]:
    environment = dict(os.environ)
    qa_bin = str(QA_ROOT / "bin")
    homebrew_bin = "/opt/homebrew/bin"
    inherited = [entry for entry in environment.get("PATH", "").split(os.pathsep) if entry]
    environment["PATH"] = os.pathsep.join(dict.fromkeys((qa_bin, homebrew_bin, *inherited)))
    return environment


def write_json_receipt(path: Path, payload: object) -> None:
    resolved_parent = path.parent.resolve()
    if resolved_parent != QA_ROOT and QA_ROOT not in resolved_parent.parents:
        raise ValueError("macmini_qa_receipt_outside_root")
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(encoded)


def _assert_private_directory(path: Path) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode != 0o700:
        raise RuntimeError("macmini_qa_directory_permissions_invalid")
