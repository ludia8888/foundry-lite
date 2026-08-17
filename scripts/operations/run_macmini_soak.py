"""Collect bounded 24-hour availability and Kubernetes stability evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess  # nosec B404 - fixed kubectl/business probes only; remove if arbitrary commands are introduced.
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from scripts.operations.macmini_qa_guard import QA_ROOT, assert_host_boundary, assert_namespace


@dataclass(frozen=True, slots=True)
class Probe:
    name: str
    url: str


@dataclass(frozen=True, slots=True)
class SoakMetrics:
    probe_availability: dict[str, float]
    availability: float
    unexpected_restarts: int
    unexpected_replacements: int
    business_executions: int
    business_failures: int
    oom_count: int
    memory_percent: int | float
    disk_percent: int | float
    is_resource_metrics_complete: bool
    is_memory_growth_sustained: bool
    is_disk_growth_sustained: bool

    @property
    def is_passed(self) -> bool:
        checks = (
            bool(self.probe_availability),
            all(value >= 0.999 for value in self.probe_availability.values()),
            self.unexpected_restarts == 0,
            self.unexpected_replacements == 0,
            self.business_executions > 0,
            self.business_failures == 0,
            self.oom_count == 0,
            self.memory_percent < 85,
            self.disk_percent < 80,
            self.is_resource_metrics_complete,
            not self.is_memory_growth_sustained,
            not self.is_disk_growth_sustained,
        )
        return all(checks)


def run_soak(args: argparse.Namespace) -> dict[str, object]:
    assert_host_boundary()
    assert_namespace(args.namespace)
    probes = tuple(_parse_probe(value) for value in args.probe)
    if not probes:
        raise ValueError("macmini_soak_probe_required")
    duration = _bounded(args.duration_seconds, 60, 172800, "duration")
    interval = _bounded(args.interval_seconds, 5, 300, "interval")
    _bounded_float(args.http_timeout_seconds, 0.1, 30.0, "http_timeout")
    _bounded(args.business_probe_every, 1, 1440, "business_probe_every")
    _bounded_float(args.business_probe_timeout_seconds, 0.1, 300.0, "business_probe_timeout")
    token = _read_token(args.bearer_token_file)
    fault_windows = _fault_windows(args.fault_windows_file)
    target = _soak_directory(args.run_id)
    samples_path = target / "samples.ndjson"
    started = time.monotonic()
    deadline = started + duration
    sample_count = 0
    with samples_path.open("x", encoding="utf-8") as stream:
        os.chmod(samples_path, 0o600)
        while time.monotonic() < deadline:
            sampled_at = datetime.now(UTC).isoformat()
            sample = _sample(args, probes, token, sampled_at, sample_count, fault_windows)
            _append_sample(stream, sample)
            sample_count += 1
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(interval, remaining))
    summary = _summarize(samples_path, duration, _fault_windows(args.fault_windows_file))
    _write_new_json(target / "summary.json", summary)
    return summary


def _sample(
    args: argparse.Namespace,
    probes: tuple[Probe, ...],
    token: str | None,
    sampled_at: str,
    sample_index: int,
    fault_windows: tuple[tuple[datetime, datetime], ...],
) -> dict[str, object]:
    results = [_http_probe(probe, token, args.http_timeout_seconds) for probe in probes]
    pods = _kubectl_json(args.kubectl, args.kubeconfig, args.namespace, ("get", "pods", "-o", "json"))
    node_metrics = _node_metrics(args.kubectl, args.kubeconfig)
    disk = _colima_disk_metrics()
    business = None
    if args.business_probe_command_json and sample_index % args.business_probe_every == 0:
        business = _business_probe(args.business_probe_command_json, args.business_probe_timeout_seconds)
    return {
        "schemaVersion": 1,
        "sampledAt": sampled_at,
        "isDeclaredFault": _is_declared_fault(sampled_at, fault_windows),
        "http": results,
        "pods": _pod_snapshot(pods),
        "nodeMetrics": node_metrics,
        "disk": disk,
        "businessProbe": business,
    }


def _http_probe(probe: Probe, token: str | None, timeout_seconds: float) -> dict[str, object]:
    headers = {"accept": "application/json", "user-agent": "Foundry-lite-enterprise-QA/1"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    request = urllib.request.Request(probe.url, headers=headers)
    started = time.monotonic()
    status = 0
    reason = "ok"
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout_seconds) as response:
            status = response.status
            response.read(4096)
    except urllib.error.HTTPError as exc:
        status = exc.code
        reason = "http_error"
    except (urllib.error.URLError, TimeoutError, OSError, RuntimeError):
        reason = "unavailable"
    return {
        "name": probe.name,
        "statusCode": status,
        "isAvailable": 200 <= status < 300,
        "durationMs": int((time.monotonic() - started) * 1000),
        "reason": reason,
    }


def _business_probe(command_json: str, timeout_seconds: float) -> dict[str, object]:
    command = json.loads(command_json)
    if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
        raise ValueError("macmini_soak_business_probe_invalid")
    started = time.monotonic()
    try:
        result = subprocess.run(  # nosec B603 - JSON argv is bounded and shell-free; remove if string commands appear.
            command, check=False, capture_output=True, timeout=timeout_seconds
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"status": "failed", "reason": "unavailable_or_timeout"}
    receipt = _safe_business_receipt(result.stdout)
    return {
        "status": "passed" if result.returncode == 0 and receipt is not None else "failed",
        "returnCode": result.returncode,
        "durationMs": int((time.monotonic() - started) * 1000),
        "stdoutSha256": "sha256:" + hashlib.sha256(result.stdout).hexdigest(),
        "stderrSha256": "sha256:" + hashlib.sha256(result.stderr).hexdigest(),
        "receipt": receipt,
    }


def _safe_business_receipt(raw: bytes) -> dict[str, object] | None:
    if not raw or len(raw) > 64 * 1024:
        return None
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1 or payload.get("status") != "passed":
        return None
    receipt: dict[str, object] = {key: payload.get(key) for key in _business_receipt_fields()}
    if not _valid_business_receipt(receipt):
        return None
    return receipt


def _business_receipt_fields() -> tuple[str, ...]:
    return (
        "schemaVersion",
        "status",
        "observedAt",
        "actionRunId",
        "idempotentReplay",
        "materializationVersionId",
        "materializationRowCount",
        "objectVersion",
        "datasetPreviewMatched",
        "objectQueryMatched",
    )


def _valid_business_receipt(receipt: Mapping[str, object]) -> bool:
    text_fields = ("observedAt", "actionRunId", "materializationVersionId")
    boolean_fields = ("idempotentReplay", "datasetPreviewMatched", "objectQueryMatched")
    integer_fields = ("materializationRowCount", "objectVersion")
    return (
        receipt.get("schemaVersion") == 1
        and receipt.get("status") == "passed"
        and all(isinstance(receipt.get(field), str) and 0 < len(str(receipt[field])) <= 200 for field in text_fields)
        and all(receipt.get(field) is True for field in boolean_fields)
        and all(_is_positive_integer(receipt.get(field)) for field in integer_fields)
    )


def _is_positive_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _kubectl_json(
    kubectl: str,
    kubeconfig: str,
    namespace: str,
    operation: tuple[str, ...],
) -> object:
    command = (kubectl, "--kubeconfig", kubeconfig, "--namespace", namespace, *operation)
    try:
        result = subprocess.run(  # nosec B603 - namespace-bound kubectl argv; remove if shell or free argv appears.
            command, check=False, capture_output=True, timeout=15
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"status": "unavailable"}
    if result.returncode != 0 or len(result.stdout) > 8 * 1024 * 1024:
        return {"status": "failed", "returnCode": result.returncode}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"status": "invalid_json"}


def _pod_snapshot(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return {"status": "unavailable", "items": []}
    items: list[dict[str, object]] = []
    for pod in payload["items"]:
        if not isinstance(pod, dict):
            continue
        metadata = _mapping_or_empty(pod.get("metadata"))
        status = _mapping_or_empty(pod.get("status"))
        containers = _container_statuses(status.get("containerStatuses"))
        owner_kind, owner_name = _pod_owner(metadata.get("ownerReferences"))
        restarts = sum(_restart_count(item) for item in containers)
        oom_count = sum(_container_oom_count(item) for item in containers)
        items.append(
            {
                "name": metadata.get("name"),
                "phase": status.get("phase"),
                "ownerKind": owner_kind,
                "ownerName": owner_name,
                "restarts": restarts,
                "oomCount": oom_count,
            }
        )
    return {"status": "ok", "items": items}


def _summarize(
    path: Path,
    requested_duration_seconds: int,
    fault_windows: tuple[tuple[datetime, datetime], ...] = (),
) -> dict[str, object]:
    samples = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    eligible = [sample for sample in samples if not _is_declared_fault(str(sample["sampledAt"]), fault_windows)]
    metrics = _soak_metrics(samples, eligible, fault_windows)
    return {
        "schemaVersion": 1,
        "status": "passed" if metrics.is_passed else "failed",
        "requestedDurationSeconds": requested_duration_seconds,
        "sampleCount": len(samples),
        "eligibleSampleCount": len(eligible),
        "availability": metrics.availability,
        "availabilityByProbe": metrics.probe_availability,
        "maximumObservedRestartCount": metrics.unexpected_restarts,
        "unexpectedPodReplacementCount": metrics.unexpected_replacements,
        "businessProbeExecutions": metrics.business_executions,
        "businessProbeFailures": metrics.business_failures,
        "lastBusinessProbeReceipt": _last_business_receipt(eligible),
        "oomCount": metrics.oom_count,
        "maximumNodeMemoryPercent": metrics.memory_percent,
        "maximumNodeDiskPercent": metrics.disk_percent,
        "resourceMetricsComplete": metrics.is_resource_metrics_complete,
        "isSustainedMemoryGrowth": metrics.is_memory_growth_sustained,
        "isSustainedDiskGrowth": metrics.is_disk_growth_sustained,
        "hostPhysicalFailureTolerance": "notProven",
        "multiNodeAvailability": "notProven",
    }


def _soak_metrics(
    samples: list[dict[str, object]],
    eligible: list[dict[str, object]],
    fault_windows: tuple[tuple[datetime, datetime], ...],
) -> SoakMetrics:
    probe_results = [probe for sample in eligible for probe in _http_results(sample)]
    available = sum(1 for probe in probe_results if probe["isAvailable"])
    return SoakMetrics(
        probe_availability=_probe_availability(probe_results),
        availability=available / len(probe_results) if probe_results else 0.0,
        unexpected_restarts=_unexpected_restart_increments(samples, fault_windows),
        unexpected_replacements=_unexpected_pod_replacements(samples, fault_windows),
        business_executions=_business_probe_executions(eligible),
        business_failures=_business_probe_failures(eligible),
        oom_count=_maximum_pod_value(samples, "oomCount"),
        memory_percent=_maximum_section_value(samples, "nodeMetrics", "memoryPercent"),
        disk_percent=_maximum_section_value(samples, "disk", "usedPercent"),
        is_resource_metrics_complete=_resource_metrics_complete(samples),
        is_memory_growth_sustained=_sustained_growth(eligible, "nodeMetrics", "memoryPercent", minimum_delta=10),
        is_disk_growth_sustained=_sustained_growth(eligible, "disk", "usedPercent", minimum_delta=5),
    )


def _business_probe_failures(samples: list[dict[str, object]]) -> int:
    return sum(1 for sample in samples if _business_probe_failed(sample))


def _business_probe_executions(samples: list[dict[str, object]]) -> int:
    return sum(1 for sample in samples if isinstance(sample.get("businessProbe"), dict))


def _last_business_receipt(samples: list[dict[str, object]]) -> dict[str, object] | None:
    for sample in reversed(samples):
        probe = sample.get("businessProbe")
        if isinstance(probe, dict) and isinstance(probe.get("receipt"), dict):
            return probe["receipt"]
    return None


def _business_probe_failed(sample: dict[str, object]) -> bool:
    probe = sample.get("businessProbe")
    return isinstance(probe, dict) and probe.get("status") != "passed"


def _http_results(sample: dict[str, object]) -> list[dict[str, object]]:
    value = sample.get("http")
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _maximum_pod_value(samples: list[dict[str, object]], field: str) -> int:
    values: list[int] = []
    for sample in samples:
        items = _mapping_or_empty(sample.get("pods")).get("items")
        if isinstance(items, list):
            values.extend(int(pod.get(field, 0)) for pod in items if isinstance(pod, dict))
    return max(values, default=0)


def _maximum_section_value(samples: list[dict[str, object]], section: str, field: str) -> int | float:
    values = [_mapping_or_empty(sample.get(section)).get(field, 0) for sample in samples]
    numeric = [value for value in values if isinstance(value, int | float) and not isinstance(value, bool)]
    return max(numeric, default=0)


def _resource_metrics_complete(samples: list[dict[str, object]]) -> bool:
    return all(
        _mapping_or_empty(sample.get("nodeMetrics")).get("status") == "ok"
        and _mapping_or_empty(sample.get("disk")).get("status") == "ok"
        for sample in samples
    )


def _probe_availability(results: list[dict[str, object]]) -> dict[str, float]:
    totals: dict[str, int] = {}
    available: dict[str, int] = {}
    for result in results:
        name = result.get("name")
        if not isinstance(name, str):
            continue
        totals[name] = totals.get(name, 0) + 1
        available[name] = available.get(name, 0) + int(result.get("isAvailable") is True)
    return {name: available.get(name, 0) / total for name, total in sorted(totals.items())}


def _unexpected_restart_increments(
    samples: list[dict[str, object]],
    fault_windows: tuple[tuple[datetime, datetime], ...],
) -> int:
    previous: dict[str, int] = {}
    previous_at: datetime | None = None
    should_ignore_new_baseline = False
    unexpected = 0
    for sample in samples:
        sampled_at = datetime.fromisoformat(str(sample["sampledAt"]))
        if _is_declared_fault(sampled_at.isoformat(), fault_windows) or _crosses_fault(
            previous_at, sampled_at, fault_windows
        ):
            previous = {}
            should_ignore_new_baseline = True
        else:
            current = _pod_restart_map(sample)
            if previous:
                unexpected += sum(max(0, count - previous[name]) for name, count in current.items() if name in previous)
            elif not should_ignore_new_baseline:
                unexpected += sum(current.values())
            previous = current
            should_ignore_new_baseline = False
        previous_at = sampled_at
    return unexpected


def _unexpected_pod_replacements(
    samples: list[dict[str, object]],
    fault_windows: tuple[tuple[datetime, datetime], ...],
) -> int:
    previous: dict[tuple[str, str], frozenset[str]] = {}
    previous_at: datetime | None = None
    replacements = 0
    for sample in samples:
        sampled_at = datetime.fromisoformat(str(sample["sampledAt"]))
        if _is_declared_fault(sampled_at.isoformat(), fault_windows) or _crosses_fault(
            previous_at, sampled_at, fault_windows
        ):
            previous = {}
        else:
            current = _long_running_pods(sample)
            replacements += sum(1 for owner, names in current.items() if owner in previous and names != previous[owner])
            previous = current
        previous_at = sampled_at
    return replacements


def _pod_restart_map(sample: dict[str, object]) -> dict[str, int]:
    pods = _mapping_or_empty(sample.get("pods")).get("items")
    if not isinstance(pods, list):
        return {}
    return {
        str(pod["name"]): int(pod["restarts"])
        for pod in pods
        if isinstance(pod, dict) and isinstance(pod.get("name"), str) and isinstance(pod.get("restarts"), int)
    }


def _long_running_pods(sample: dict[str, object]) -> dict[tuple[str, str], frozenset[str]]:
    pods = _mapping_or_empty(sample.get("pods")).get("items")
    grouped: dict[tuple[str, str], set[str]] = {}
    if not isinstance(pods, list):
        return {}
    for pod in pods:
        if not isinstance(pod, dict) or pod.get("ownerKind") not in {"ReplicaSet", "StatefulSet"}:
            continue
        owner_name, pod_name = pod.get("ownerName"), pod.get("name")
        if isinstance(owner_name, str) and isinstance(pod_name, str):
            grouped.setdefault((str(pod["ownerKind"]), owner_name), set()).add(pod_name)
    return {owner: frozenset(names) for owner, names in grouped.items()}


def _sustained_growth(
    samples: list[dict[str, object]],
    section: str,
    field: str,
    *,
    minimum_delta: int,
) -> bool:
    values = [
        value
        for sample in samples
        if isinstance((payload := sample.get(section)), dict)
        and isinstance((value := payload.get(field)), int)
        and not isinstance(value, bool)
    ]
    if len(values) < 10:
        return False
    window = max(3, len(values) // 10)
    delta = sum(values[-window:]) / window - sum(values[:window]) / window
    rises = sum(1 for before, after in zip(values, values[1:], strict=False) if after >= before)
    return delta >= minimum_delta and rises / (len(values) - 1) >= 0.8


def _pod_owner(value: object) -> tuple[str | None, str | None]:
    if not isinstance(value, list):
        return None, None
    for owner in value:
        if isinstance(owner, dict) and owner.get("controller") is True:
            kind, name = owner.get("kind"), owner.get("name")
            return (kind if isinstance(kind, str) else None, name if isinstance(name, str) else None)
    return None, None


def _crosses_fault(
    previous: datetime | None,
    current: datetime,
    windows: tuple[tuple[datetime, datetime], ...],
) -> bool:
    return previous is not None and any(start <= current and end >= previous for start, end in windows)


def _parse_probe(value: str) -> Probe:
    name, separator, url = value.partition("=")
    parsed = urllib.parse.urlsplit(url)
    is_loopback_http = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
    if (
        not separator
        or not name
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
        or (parsed.scheme != "https" and not is_loopback_http)
    ):
        raise ValueError("macmini_soak_probe_invalid")
    return Probe(name, url)


def _read_token(path: str | None) -> str | None:
    if path is None:
        return None
    token_path = Path(path)
    if not token_path.is_file() or token_path.stat().st_mode & 0o077:
        raise ValueError("macmini_soak_bearer_token_permissions_invalid")
    token = token_path.read_text(encoding="utf-8").strip()
    if not token:
        raise ValueError("macmini_soak_bearer_token_missing")
    return token


def _container_oom_count(container: dict[str, object]) -> int:
    for key in ("state", "lastState"):
        state = container.get(key)
        if not isinstance(state, dict):
            continue
        terminated = state.get("terminated")
        if isinstance(terminated, dict) and terminated.get("reason") == "OOMKilled":
            return 1
    return 0


def _container_statuses(value: object) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, dict))


def _restart_count(container: dict[str, object]) -> int:
    value = container.get("restartCount")
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _mapping_or_empty(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _node_metrics(kubectl: str, kubeconfig: str) -> dict[str, object]:
    command = (kubectl, "--kubeconfig", kubeconfig, "top", "nodes", "--no-headers")
    try:
        result = subprocess.run(  # nosec B603 - fixed Colima metrics argv; remove if free argv appears.
            command, check=False, capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"status": "unavailable"}
    fields = result.stdout.split()
    if result.returncode != 0 or len(fields) < 5 or not fields[4].endswith("%"):
        return {"status": "unavailable"}
    try:
        return {"status": "ok", "memoryPercent": int(fields[4].removesuffix("%"))}
    except ValueError:
        return {"status": "invalid"}


def _colima_disk_metrics() -> dict[str, object]:
    command = ("colima", "ssh", "--profile", "foundry-qa", "--", "df", "-Pk", "/")
    try:
        result = subprocess.run(  # nosec B603 - fixed Colima disk argv; remove if free argv appears.
            command, check=False, capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"status": "unavailable"}
    lines = result.stdout.splitlines()
    if result.returncode != 0 or len(lines) < 2:
        return {"status": "unavailable"}
    fields = lines[-1].split()
    if len(fields) < 5 or not fields[4].endswith("%"):
        return {"status": "invalid"}
    try:
        return {"status": "ok", "usedPercent": int(fields[4].removesuffix("%"))}
    except ValueError:
        return {"status": "invalid"}


def _fault_windows(path: str | None) -> tuple[tuple[datetime, datetime], ...]:
    if path is None:
        return ()
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("macmini_soak_fault_windows_invalid")
    windows: list[tuple[datetime, datetime]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("macmini_soak_fault_windows_invalid")
        start = datetime.fromisoformat(str(item.get("startedAt")))
        end = datetime.fromisoformat(str(item.get("endedAt")))
        if start.tzinfo is None or end.tzinfo is None or end < start:
            raise ValueError("macmini_soak_fault_windows_invalid")
        windows.append((start, end))
    return tuple(windows)


def _is_declared_fault(sampled_at: str, windows: tuple[tuple[datetime, datetime], ...]) -> bool:
    sample = datetime.fromisoformat(sampled_at)
    return any(start <= sample <= end for start, end in windows)


def _soak_directory(run_id: str) -> Path:
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
    if not run_id or any(value not in allowed for value in run_id):
        raise ValueError("macmini_soak_run_id_invalid")
    target = QA_ROOT / "evidence" / run_id / "soak"
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    return target


def _append_sample(stream: TextIO, payload: object) -> None:
    stream.write(json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n")
    stream.flush()
    os.fsync(stream.fileno())


def _write_new_json(path: Path, payload: object) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")


def _bounded(value: int, minimum: int, maximum: int, name: str) -> int:
    if value < minimum or value > maximum:
        raise ValueError(f"macmini_soak_{name}_out_of_range")
    return value


def _bounded_float(value: float, minimum: float, maximum: float, name: str) -> float:
    if not math.isfinite(value) or value < minimum or value > maximum:
        raise ValueError(f"macmini_soak_{name}_out_of_range")
    return value


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request: object, fp: object, code: int, msg: str, headers: object, url: str) -> None:
        raise RuntimeError("macmini_soak_redirect_not_allowed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--namespace", default="foundry-qa")
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--kubectl", default=str(QA_ROOT / "bin" / "kubectl"))
    parser.add_argument("--duration-seconds", type=int, default=86400)
    parser.add_argument("--interval-seconds", type=int, default=60)
    parser.add_argument("--http-timeout-seconds", type=float, default=10.0)
    parser.add_argument("--probe", action="append", default=[])
    parser.add_argument("--bearer-token-file")
    parser.add_argument("--business-probe-command-json")
    parser.add_argument("--business-probe-every", type=int, default=5)
    parser.add_argument("--business-probe-timeout-seconds", type=float, default=60.0)
    parser.add_argument("--fault-windows-file")
    summary = run_soak(parser.parse_args())
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
