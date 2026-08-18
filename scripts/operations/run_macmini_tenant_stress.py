"""Run bounded two-tenant fairness and cross-tenant isolation stress over the live API."""

from __future__ import annotations

import argparse
import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import TypedDict

from scripts.operations.macmini_qa_guard import (
    QA_ROOT,
    assert_host_boundary,
    write_json_receipt,
)
from scripts.operations.run_macmini_business_probe import (
    OBJECT_ID,
    OBJECT_TYPE,
    ApiClient,
    BusinessProbeError,
    bootstrap_probe,
    run_probe,
)


class TenantStressResult(TypedDict):
    tenantARequests: int
    tenantAErrors: int
    tenantBDurationsMs: list[int]
    tenantBFailures: int


def run_stress(args: argparse.Namespace) -> dict[str, object]:
    assert_host_boundary()
    _validate(args)
    tenant_a, tenant_b = _tenant_ids(args.run_id)
    target = QA_ROOT / "evidence" / args.run_id / "tenant-stress"
    target.mkdir(mode=0o700, parents=True, exist_ok=False)
    client_a = _client(args, tenant_a, "tenant-a-operator")
    client_b = _client(args, tenant_b, "tenant-b-operator")
    bootstrap_probe(client_a, target / "tenant-a-business-probe.json")
    isolation = _cross_tenant_read_is_hidden(client_b)
    config_b_path = target / "tenant-b-business-probe.json"
    bootstrap_probe(client_b, config_b_path)
    config_b = json.loads(config_b_path.read_text(encoding="utf-8"))
    baseline = [_timed_business_probe(client_b, config_b) for _ in range(args.baseline_samples)]
    flood = _run_flood(args, tenant_a, client_b, config_b)
    baseline_p95 = _percentile([sample for sample, is_passed in baseline if is_passed], 0.95)
    tenant_b_p95 = _percentile(flood["tenantBDurationsMs"], 0.95)
    latency_limit = max(5_000, baseline_p95 * 3)
    is_fair = flood["tenantBFailures"] == 0 and tenant_b_p95 <= latency_limit
    receipt = {
        "schemaVersion": 1,
        "status": "passed" if isolation and is_fair else "failed",
        "runId": args.run_id,
        "observedAt": datetime.now(UTC).isoformat(),
        "tenantARequestCount": flood["tenantARequests"],
        "tenantAErrorCount": flood["tenantAErrors"],
        "tenantBBusinessExecutions": len(flood["tenantBDurationsMs"]),
        "tenantBBusinessFailures": flood["tenantBFailures"],
        "tenantBBaselineP95Ms": baseline_p95,
        "tenantBStressP95Ms": tenant_b_p95,
        "tenantBLatencyLimitMs": latency_limit,
        "crossTenantReadHiddenBeforeTenantBBootstrap": isolation,
        "tenantBFairnessObserved": is_fair,
        "quotaEnforcement": "notProven",
        "rawHeadersStored": False,
        "rawTokensStored": False,
    }
    write_json_receipt(target / "receipt.json", receipt)
    return receipt


def _run_flood(
    args: argparse.Namespace,
    tenant_a: str,
    client_b: ApiClient,
    config_b: dict[str, object],
) -> TenantStressResult:
    stop = threading.Event()
    counts = {"requests": 0, "errors": 0}
    lock = threading.Lock()

    def flood_worker(index: int) -> None:
        client = _client(args, tenant_a, f"tenant-a-flood-{index}")
        while not stop.is_set():
            try:
                client.json_request("GET", f"/api/objects/{OBJECT_TYPE}/{OBJECT_ID}")
                key = "requests"
            except BusinessProbeError:
                key = "errors"
            with lock:
                counts[key] += 1

    durations: list[int] = []
    failures = 0
    deadline = time.monotonic() + args.duration_seconds
    with ThreadPoolExecutor(max_workers=args.tenant_a_concurrency) as executor:
        futures = [executor.submit(flood_worker, index) for index in range(args.tenant_a_concurrency)]
        while time.monotonic() < deadline:
            duration, is_passed = _timed_business_probe(client_b, config_b)
            durations.append(duration)
            failures += int(not is_passed)
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(args.tenant_b_interval_seconds, remaining))
        stop.set()
        for future in futures:
            future.result(timeout=30)
    return {
        "tenantARequests": counts["requests"],
        "tenantAErrors": counts["errors"],
        "tenantBDurationsMs": durations,
        "tenantBFailures": failures,
    }


def _cross_tenant_read_is_hidden(client: ApiClient) -> bool:
    response = client.json_request(
        "GET",
        f"/api/objects/{OBJECT_TYPE}/{OBJECT_ID}",
        acceptable_statuses=(404,),
    )
    return response.status_code == 404


def _timed_business_probe(client: ApiClient, config: dict[str, object]) -> tuple[int, bool]:
    started = time.monotonic()
    try:
        receipt = run_probe(client, config)
        is_passed = receipt.get("status") == "passed"
    except BusinessProbeError:
        is_passed = False
    return int((time.monotonic() - started) * 1000), is_passed


def _client(args: argparse.Namespace, tenant_id: str, actor_id: str) -> ApiClient:
    return ApiClient(
        args.base_url,
        tenant_id=tenant_id,
        actor_user_id=actor_id,
        roles="admin,data_engineer,ops_manager",
        bearer_token=None,
        timeout_seconds=args.request_timeout_seconds,
    )


def _tenant_ids(run_id: str) -> tuple[str, str]:
    if not run_id or not all(value.isalnum() or value in "-_" for value in run_id):
        raise ValueError("macmini_tenant_stress_run_id_invalid")
    suffix = run_id[-48:].lower().replace("_", "-")
    return f"qa-{suffix}-a", f"qa-{suffix}-b"


def _percentile(values: list[int], quantile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * quantile) - 1)]


def _validate(args: argparse.Namespace) -> None:
    if not 30 <= args.duration_seconds <= 900:
        raise ValueError("macmini_tenant_stress_duration_invalid")
    if not 1 <= args.tenant_a_concurrency <= 32:
        raise ValueError("macmini_tenant_stress_concurrency_invalid")
    if not 3 <= args.baseline_samples <= 20:
        raise ValueError("macmini_tenant_stress_baseline_invalid")
    if not math.isfinite(args.tenant_b_interval_seconds) or not 0.5 <= args.tenant_b_interval_seconds <= 10:
        raise ValueError("macmini_tenant_stress_interval_invalid")
    if not math.isfinite(args.request_timeout_seconds) or not 1 <= args.request_timeout_seconds <= 60:
        raise ValueError("macmini_tenant_stress_timeout_invalid")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:30443")
    parser.add_argument("--duration-seconds", type=int, default=600)
    parser.add_argument("--tenant-a-concurrency", type=int, default=8)
    parser.add_argument("--tenant-b-interval-seconds", type=float, default=2.0)
    parser.add_argument("--baseline-samples", type=int, default=5)
    parser.add_argument("--request-timeout-seconds", type=float, default=20.0)
    try:
        receipt = run_stress(parser.parse_args())
    except (
        BusinessProbeError,
        RuntimeError,
        ValueError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(
            json.dumps(
                {"schemaVersion": 1, "status": "failed", "reason": str(exc)},
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(receipt, separators=(",", ":"), sort_keys=True))
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
