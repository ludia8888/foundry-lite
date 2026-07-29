"""Typed worker payload parsing and attempt-heartbeat support."""

from __future__ import annotations

import threading
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from foundry_lite.application.ports.pipeline_execution_repository import PipelineExecutionRepository
from foundry_lite.application.ports.pipeline_repository import PipelineVersionRow
from foundry_lite.application.ports.transaction_context import TransactionManager
from foundry_lite.application.services.pipeline_graph_v2_execution_evidence_types import (
    PipelineGraphV2AttemptContext,
    PipelineGraphV2WorkerLease,
)
from foundry_lite.application.services.pipeline_v2_runtime_contracts import (
    PipelineV2RunResult,
    PipelineV2RuntimeNode,
)
from foundry_lite.domain.context import DEMO_ADMIN_ROLES, RequestContext
from foundry_lite.domain.errors import InvariantViolation

_ATTEMPT_LEASE_SECONDS = 30
_ATTEMPT_HEARTBEAT_SECONDS = 10


class AttemptHeartbeat:
    """Renew the active attempt lease until node execution finishes."""

    def __init__(
        self,
        engine: TransactionManager,
        repository: PipelineExecutionRepository,
        ctx: RequestContext,
    ) -> None:
        """Bind the heartbeat to its transaction and tenant scope."""

        self._engine = engine
        self._repository = repository
        self._ctx = ctx
        self._attempt: PipelineGraphV2AttemptContext | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, attempt: PipelineGraphV2AttemptContext) -> None:
        """Start lease renewal for the claimed attempt."""

        self._attempt = attempt
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop renewal and join the heartbeat thread."""

        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def _loop(self) -> None:
        while not self._stop.wait(_ATTEMPT_HEARTBEAT_SECONDS):
            if not self._renew():
                return

    def _renew(self) -> bool:
        attempt = self._attempt
        if attempt is None or attempt.worker_id is None or attempt.lease_token is None or attempt.fencing_token is None:
            return False
        now = datetime.now(UTC)
        with self._engine.begin() as transaction:
            renewed = self._repository.heartbeat_node_attempt(
                transaction=transaction,
                tenant_id=self._ctx.tenant_id,
                attempt_id=attempt.attempt_id,
                worker_id=attempt.worker_id,
                lease_token=attempt.lease_token,
                fencing_token=attempt.fencing_token,
                lease_expires_at=timestamp(now + timedelta(seconds=_ATTEMPT_LEASE_SECONDS)),
                heartbeat_at=timestamp(now),
            )
        return renewed is not None


def worker_context(payload: Mapping[str, object]) -> RequestContext:
    """Rebuild the traceable tenant context carried by a Temporal activity."""

    return RequestContext(
        tenant_id=required_text(payload, "tenant_id"),
        actor_user_id="pipeline-dag-worker",
        request_id=required_text(payload, "request_id"),
        roles=DEMO_ADMIN_ROLES,
    )


def worker_lease(payload: Mapping[str, object]) -> PipelineGraphV2WorkerLease:
    """Create the initial DB lease coordinates for one activity delivery."""

    now = datetime.now(UTC)
    return PipelineGraphV2WorkerLease(
        worker_id=str(payload.get("workerIdentity") or "pipeline-worker"),
        lease_token=uuid4().hex,
        lease_expires_at=timestamp(now + timedelta(seconds=_ATTEMPT_LEASE_SECONDS)),
        heartbeat_at=timestamp(now),
        external_execution_id=str(payload.get("externalExecutionId") or "") or None,
    )


def node_id(payload: Mapping[str, object]) -> str:
    """Read the required node identifier from an activity payload."""

    node = payload.get("node")
    if not isinstance(node, Mapping):
        raise InvariantViolation("pipeline worker node payload is missing")
    return required_text(node, "nodeId")


def required_runtime_node(
    nodes: tuple[PipelineV2RuntimeNode, ...],
    required_node_id: str,
) -> PipelineV2RuntimeNode:
    """Resolve exactly one node from the immutable runtime plan."""

    for node in nodes:
        if node.node_id == required_node_id:
            return node
    raise InvariantViolation("pipeline worker node is not present in the execution plan")


def mapping_rows(value: object) -> list[Mapping[str, object]]:
    """Normalize list- or mapping-backed JSON rows without accepting scalars."""

    if isinstance(value, Mapping):
        return [item for item in value.values() if isinstance(item, Mapping)]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, Mapping)]
    return []


def run_result(outcomes: list[Mapping[str, object]]) -> PipelineV2RunResult:
    """Project Temporal node outcomes into the existing v2 run result."""

    outputs = tuple(dict(output) for outcome in outcomes for output in mapping_rows(outcome.get("outputs")))
    failure = next((item for item in outcomes if item.get("status") == "failed"), None)
    error = failure.get("error") if failure is not None else None
    return PipelineV2RunResult(
        outputs=outputs,
        timeline=_outcome_timeline(outcomes),
        error=dict(error) if isinstance(error, Mapping) else None,
        failed_node_id=str(failure["nodeId"]) if failure is not None else None,
        skipped_node_ids=tuple(str(item["nodeId"]) for item in outcomes if item.get("status") == "skipped"),
    )


def node_policy(version: PipelineVersionRow, required_node_id: str) -> Mapping[str, object]:
    """Read the compiler-pinned execution policy for one node."""

    plan = version["execution_plan"] or {}
    for node in mapping_rows(plan.get("nodes")):
        policy = node.get("executionPolicy")
        if node.get("nodeId") == required_node_id and isinstance(policy, Mapping):
            return policy
    return {}


def has_stable_idempotency(config: Mapping[str, object]) -> bool:
    """Return whether an external-cost node has a stable replay coordinate."""

    keys = ("stableRequestKey", "cacheKey", "idempotencyKey", "promptVersionId")
    return any(isinstance(config.get(key), str) and str(config[key]).strip() for key in keys)


def required_text(payload: Mapping[str, object], field: str) -> str:
    """Read a non-empty worker coordinate or fail closed."""

    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise InvariantViolation("pipeline DAG worker coordinate is invalid", details={"field": field})
    return value.strip()


def integer(value: object, default: int) -> int:
    """Normalize a JSON integer while excluding booleans."""

    return value if isinstance(value, int) and not isinstance(value, bool) else default


def timestamp(value: datetime) -> str:
    """Serialize a UTC timestamp in the sortable repository format."""

    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _outcome_timeline(outcomes: list[Mapping[str, object]]) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "event": f"pipeline.node.{item.get('status')}",
            "nodeId": item.get("nodeId"),
        }
        for item in outcomes
    )
