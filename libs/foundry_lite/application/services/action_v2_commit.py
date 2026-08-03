"""Compile, plan, and atomically commit a native ``rulesV2`` action submission.

This concentrates the Action IR v2 apply seam (compiler + plan builder + validator
+ committer + resolution adapter) behind one small collaborator so the apply
service takes a single dependency on the v2 pipeline instead of five. The dual-read
switch (:func:`uses_action_rules_v2`) keeps legacy setProperty actions on the v1
unit-of-work untouched.
"""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.action_types import ActionApplyCommand, ActionApplyResponse
from foundry_lite.application.ports import ActionRepository, ActionTypeRow, TransactionContext
from foundry_lite.application.services.action_edit_plan_committer import (
    ActionEditPlanCommitter,
    CommitLinkTypeResolver,
)
from foundry_lite.application.services.action_edit_plan_results import ActionEditPlanResult, plan_summary
from foundry_lite.application.services.action_ir_compiler import compile_action_definition
from foundry_lite.application.services.action_plan_resolution import LivePlanResolutionContext
from foundry_lite.application.services.action_protocols import (
    ActionObjectIndexer,
    ActionObjectRecordLookup,
    ActionOntologyLookup,
    ActionRuntimeBoundary,
)
from foundry_lite.domain.action_runtime.action_contract import compile_action_contract
from foundry_lite.domain.action_runtime.edit_plan import EditPlan, validate_edit_plan
from foundry_lite.domain.action_runtime.edit_plan_builder import build_edit_plan
from foundry_lite.domain.context import RequestContext

__all__ = ["ActionV2Committer", "CommitLinkTypeResolver", "uses_action_rules_v2"]


def uses_action_rules_v2(action_type: ActionTypeRow) -> bool:
    """Route canonical edit rules and legacy ``rulesV2`` through one committer."""
    definition = action_type["definition"]
    return bool(definition.get("rulesV2") or definition.get("rules"))


@dataclass(frozen=True)
class ActionV2Committer:
    """Run one rulesV2 submission end to end inside the caller's transaction."""

    action_repository: ActionRepository
    object_indexer: ActionObjectIndexer
    object_lookup: ActionObjectRecordLookup
    ontology_lookup: ActionOntologyLookup
    link_type_lookup: CommitLinkTypeResolver
    runtime: ActionRuntimeBoundary

    def commit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_type: ActionTypeRow,
        action_run_id: str,
        command: ActionApplyCommand,
        plan: EditPlan | None = None,
    ) -> ActionApplyResponse:
        resolved_plan = plan or self._resolve_plan(conn, ctx, action_type, command)
        result = self._committer().commit_plan(
            conn,
            ctx,
            action_run_id=action_run_id,
            plan=resolved_plan,
            contract=compile_action_contract(action_type["definition"]),
        )
        return _apply_response(action_run_id, command, result)

    def _resolve_plan(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_type: ActionTypeRow,
        command: ActionApplyCommand,
    ) -> EditPlan:
        compiled = compile_action_definition(action_type["definition"])
        resolution = LivePlanResolutionContext(
            conn, ctx, command, self.object_lookup, self.ontology_lookup, self.link_type_lookup
        )
        plan = build_edit_plan(compiled, resolution)
        validate_edit_plan(plan)
        return plan

    def _committer(self) -> ActionEditPlanCommitter:
        return ActionEditPlanCommitter(
            action_repository=self.action_repository,
            object_indexer=self.object_indexer,
            object_lookup=self.object_lookup,
            ontology_lookup=self.ontology_lookup,
            link_type_lookup=self.link_type_lookup,
            runtime=self.runtime,
        )


def _apply_response(
    action_run_id: str, command: ActionApplyCommand, result: ActionEditPlanResult
) -> ActionApplyResponse:
    return {
        "actionRunId": action_run_id,
        "status": result.status,
        "target": {"objectType": command.object_type, "objectId": command.object_id},
        "plan": plan_summary(result),
    }
