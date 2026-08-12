"""Bounded authoritative DB reads for one Governed Release golden collection."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from foundry_lite.application.ports.ai_run_repository import AiRunRepository
from foundry_lite.application.ports.release_delivery_repository import (
    ReleaseDeliveryRecord,
    ReleaseDeliveryRepository,
)
from foundry_lite.application.ports.runtime_repository import RuntimeRepository
from foundry_lite.application.ports.runtime_repository_types import RuntimeRow
from foundry_lite.application.ports.transaction_context import TransactionContext
from foundry_lite.application.services.aip.governed_release_live_collection_contract import (
    DeliveryOperation,
    ReleaseKind,
    ReleaseTool,
)
from foundry_lite.application.services.aip.governed_release_live_collection_db_actions import (
    loaded_action,
    require_action_identities,
)
from foundry_lite.application.services.aip.governed_release_live_collection_db_snapshot import (
    DELIVERY_TOOL,
    database_snapshot,
    delivery_claims,
    delivery_times,
)
from foundry_lite.application.services.aip.governed_release_live_collection_db_types import (
    ActionLedgerSource,
    LoadedActionLedger,
    SelectedAuditEvidence,
    ServerActionResultClaim,
    ServerLoadedDatabaseSnapshot,
    conflict,
    invalid,
    is_text,
    required_row_text,
    row_time,
)

_AUDIT_EVENT = "governed_release.action.succeeded"
_AUDIT_LIMIT = 33
_SCENARIOS: tuple[ReleaseKind, ...] = ("ontology", "pipeline")

_ACTION_SEQUENCE: dict[ReleaseKind, tuple[ReleaseTool, ...]] = {
    "ontology": (
        "publish_release_candidate",
        "assign_release_reviewer",
        "submit_release_decision",
        "execute_approved_release",
        "rollback_release",
    ),
    "pipeline": (
        "publish_release_candidate",
        "assign_release_reviewer",
        "submit_release_decision",
        "execute_approved_release",
        "deploy_release",
        "rollback_release",
    ),
}
_DELIVERY_SEQUENCE: dict[ReleaseKind, tuple[DeliveryOperation, ...]] = {
    "ontology": ("source_publish", "source_merge"),
    "pipeline": ("source_publish", "source_merge", "application_deploy", "application_rollback"),
}
_AUDIT_ACTIONS: frozenset[tuple[ReleaseKind, ReleaseTool]] = frozenset(
    (kind, tool) for kind, tools in _ACTION_SEQUENCE.items() for tool in tools
)


class GovernedReleaseLiveCollectionDatabaseLoader:
    """Load only server-persisted evidence selected by two immutable workflow roots."""

    def __init__(
        self,
        deliveries: ReleaseDeliveryRepository,
        ai_runs: AiRunRepository,
        runtime: RuntimeRepository,
    ) -> None:
        self._deliveries = deliveries
        self._ai_runs = ai_runs
        self._runtime = runtime

    def load(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        application_id: str,
        ontology_workflow_run_id: str,
        pipeline_workflow_run_id: str,
    ) -> ServerLoadedDatabaseSnapshot:
        """Resolve one exact two-scenario ledger selection without caller evidence."""

        run_ids: dict[ReleaseKind, str] = {
            "ontology": ontology_workflow_run_id,
            "pipeline": pipeline_workflow_run_id,
        }
        _require_scope(tenant_id, application_id, run_ids)
        chains = self._load_chains(transaction, tenant_id, application_id, run_ids)
        proposals = _proposal_ids(chains)
        audits = self._load_audits(transaction, tenant_id, proposals)
        sources = _action_sources(chains, proposals, audits)
        loaded = tuple(self._load_action(transaction, tenant_id, application_id, source) for source in sources)
        require_action_identities(loaded)
        claims = delivery_claims(chains, loaded)
        return database_snapshot(tenant_id, application_id, run_ids, proposals, chains, loaded, claims, audits)

    def _load_chains(
        self,
        transaction: TransactionContext,
        tenant_id: str,
        application_id: str,
        run_ids: Mapping[ReleaseKind, str],
    ) -> dict[ReleaseKind, tuple[ReleaseDeliveryRecord, ...]]:
        rows: dict[ReleaseKind, tuple[ReleaseDeliveryRecord, ...]] = {}
        for kind in _SCENARIOS:
            expected = _DELIVERY_SEQUENCE[kind]
            chain = self._deliveries.list_for_workflow(
                transaction=transaction,
                tenant_id=tenant_id,
                application_id=application_id,
                workflow_run_id=run_ids[kind],
                limit=len(expected) + 1,
            )
            _require_chain(chain, tenant_id, application_id, kind, run_ids[kind], expected)
            rows[kind] = chain
        return rows

    def _load_audits(
        self,
        transaction: TransactionContext,
        tenant_id: str,
        proposals: Mapping[ReleaseKind, str],
    ) -> dict[tuple[ReleaseKind, ReleaseTool], SelectedAuditEvidence]:
        refs = [("governed_release_proposal", proposals[kind]) for kind in _SCENARIOS]
        rows = self._runtime.audit_events_for_resources(
            transaction=transaction,
            tenant_id=tenant_id,
            resource_refs=refs,
            event_types=[_AUDIT_EVENT],
            limit=_AUDIT_LIMIT,
        )
        if len(rows) >= _AUDIT_LIMIT:
            conflict("action_audit_window_ambiguous")
        return _required_audits(rows, tenant_id, proposals)

    def _load_action(
        self,
        transaction: TransactionContext,
        tenant_id: str,
        application_id: str,
        source: ActionLedgerSource,
    ) -> LoadedActionLedger:
        ledger = self._ai_runs.ledger_for_run(
            transaction=transaction,
            tenant_id=tenant_id,
            ai_run_id=source.ai_run_id,
        )
        if ledger is None:
            invalid("action_ledger_missing")
        return loaded_action(ledger, tenant_id, application_id, source)


def _require_scope(tenant_id: str, application_id: str, run_ids: Mapping[ReleaseKind, str]) -> None:
    values = (tenant_id, application_id, run_ids.get("ontology"), run_ids.get("pipeline"))
    if not all(is_text(value) for value in values):
        invalid("collection_database_scope_invalid")
    if run_ids["ontology"] == run_ids["pipeline"]:
        conflict("scenario_workflow_roots_overlap")


def _require_chain(
    rows: Sequence[ReleaseDeliveryRecord],
    tenant_id: str,
    application_id: str,
    kind: ReleaseKind,
    run_id: str,
    expected: tuple[DeliveryOperation, ...],
) -> None:
    if not rows:
        invalid("workflow_delivery_chain_missing")
    if len(rows) != len(expected) or tuple(row.operation for row in rows) != expected:
        conflict("workflow_delivery_chain_mismatch")
    proposal_id = rows[0].proposal_id
    for index, row in enumerate(rows):
        if (row.tenant_id, row.application_id, row.release_kind) != (tenant_id, application_id, kind):
            conflict("workflow_delivery_scope_mismatch")
        if row.workflow_run_id != run_id or row.proposal_id != proposal_id:
            conflict("workflow_delivery_scope_mismatch")
        _require_delivery_row(row, expected[index], rows[index - 1] if index else None)


def _require_delivery_row(
    row: ReleaseDeliveryRecord,
    operation: DeliveryOperation,
    parent: ReleaseDeliveryRecord | None,
) -> None:
    _require_delivery_state(row, operation)
    _require_delivery_lineage(row, parent)
    _require_delivery_receipt(row)
    delivery_times(row)


def _require_delivery_state(row: ReleaseDeliveryRecord, operation: DeliveryOperation) -> None:
    expected_provider = "github" if operation.startswith("source_") else "render"
    if row.operation != operation or row.provider != expected_provider or row.status != "landed":
        invalid("workflow_delivery_not_landed")


def _require_delivery_lineage(
    row: ReleaseDeliveryRecord,
    parent: ReleaseDeliveryRecord | None,
) -> None:
    expected_parent = parent.delivery_id if parent is not None else None
    has_wrong_root = parent is not None and row.workflow_run_id != parent.workflow_run_id
    if row.parent_delivery_id != expected_parent or has_wrong_root:
        conflict("workflow_delivery_lineage_mismatch")
    if parent is None and row.workflow_run_id != row.ai_run_id:
        conflict("workflow_delivery_root_mismatch")


def _require_delivery_receipt(row: ReleaseDeliveryRecord) -> None:
    if not is_text(row.provider_resource_id) or not isinstance(row.result_ref, Mapping):
        invalid("workflow_delivery_receipt_invalid")


def _proposal_ids(
    chains: Mapping[ReleaseKind, tuple[ReleaseDeliveryRecord, ...]],
) -> dict[ReleaseKind, str]:
    values: dict[ReleaseKind, str] = {kind: rows[0].proposal_id for kind, rows in chains.items()}
    if not all(is_text(value) for value in values.values()):
        invalid("scenario_proposal_missing")
    if len(set(values.values())) != 2:
        conflict("scenario_proposal_overlap")
    return values


def _required_audits(
    rows: Sequence[RuntimeRow],
    tenant_id: str,
    proposals: Mapping[ReleaseKind, str],
) -> dict[tuple[ReleaseKind, ReleaseTool], SelectedAuditEvidence]:
    grouped: dict[tuple[ReleaseKind, ReleaseTool], list[SelectedAuditEvidence]] = {}
    proposal_kinds: dict[str, ReleaseKind] = {proposal_id: kind for kind, proposal_id in proposals.items()}
    for row in rows:
        parsed = _parse_audit(row, tenant_id, proposal_kinds)
        if parsed is not None:
            grouped.setdefault(parsed[0], []).append(parsed[1])
    selected: dict[tuple[ReleaseKind, ReleaseTool], SelectedAuditEvidence] = {}
    for key in _AUDIT_ACTIONS:
        matches = grouped.get(key, [])
        if not matches:
            invalid("action_audit_missing")
        if len(matches) != 1:
            conflict("action_audit_duplicate")
        selected[key] = matches[0]
    return selected


def _parse_audit(
    row: RuntimeRow,
    tenant_id: str,
    proposal_kinds: Mapping[str, ReleaseKind],
) -> tuple[tuple[ReleaseKind, ReleaseTool], SelectedAuditEvidence] | None:
    proposal_id = row.get("resource_id")
    is_scoped = (
        row.get("tenant_id") == tenant_id
        and row.get("resource_type") == "governed_release_proposal"
        and proposal_id in proposal_kinds
    )
    if not is_scoped:
        conflict("action_audit_scope_mismatch")
    if row.get("event_type") != _AUDIT_EVENT:
        conflict("action_audit_event_mismatch")
    after = row.get("after_ref")
    if not isinstance(after, Mapping):
        invalid("action_audit_payload_invalid")
    tool = after.get("toolName")
    kind = proposal_kinds[cast(str, proposal_id)]
    if tool not in _ACTION_SEQUENCE[kind]:
        return None
    if row.get("action") != tool or after.get("releaseKind") != kind or after.get("status") != "succeeded":
        conflict("action_audit_binding_mismatch")
    evidence = SelectedAuditEvidence(
        required_row_text(row, "id", "action_audit_id_invalid"),
        required_row_text(row, "actor_user_id", "action_audit_actor_invalid"),
        required_row_text(row, "correlation_id", "action_audit_correlation_invalid"),
        row_time(row, "created_at"),
    )
    return (kind, cast(ReleaseTool, tool)), evidence


def _action_sources(
    chains: Mapping[ReleaseKind, tuple[ReleaseDeliveryRecord, ...]],
    proposals: Mapping[ReleaseKind, str],
    audits: Mapping[tuple[ReleaseKind, ReleaseTool], SelectedAuditEvidence],
) -> tuple[ActionLedgerSource, ...]:
    sources: list[ActionLedgerSource] = []
    for kind, sequence in _ACTION_SEQUENCE.items():
        sources.extend(_scenario_action_sources(kind, sequence, chains[kind], proposals[kind], audits))
    if len({source.ai_run_id for source in sources}) != len(sources):
        conflict("action_run_reused_across_steps")
    return tuple(sources)


def _scenario_action_sources(
    kind: ReleaseKind,
    sequence: Sequence[ReleaseTool],
    deliveries: Sequence[ReleaseDeliveryRecord],
    proposal_id: str,
    audits: Mapping[tuple[ReleaseKind, ReleaseTool], SelectedAuditEvidence],
) -> tuple[ActionLedgerSource, ...]:
    delivery_runs = {DELIVERY_TOOL[row.operation]: row.ai_run_id for row in deliveries}
    return tuple(
        _action_source(kind, proposal_id, tool, delivery_runs.get(tool), audits.get((kind, tool))) for tool in sequence
    )


def _action_source(
    kind: ReleaseKind,
    proposal_id: str,
    tool: ReleaseTool,
    delivery_run_id: str | None,
    audit: SelectedAuditEvidence | None,
) -> ActionLedgerSource:
    run_id = delivery_run_id or (audit.ai_run_id if audit is not None else None)
    if not is_text(run_id):
        invalid("action_run_binding_missing")
    if audit is not None and run_id != audit.ai_run_id:
        conflict("action_audit_delivery_mismatch")
    return ActionLedgerSource(kind, proposal_id, tool, cast(str, run_id), audit)


__all__ = [
    "GovernedReleaseLiveCollectionDatabaseLoader",
    "ServerActionResultClaim",
    "ServerLoadedDatabaseSnapshot",
]
