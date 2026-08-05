"""Permission-scoped runtime resolution for linked-object Action criteria."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Protocol

from foundry_lite.application.ports import (
    ActionTypeRow,
    ObjectLinkRow,
    ObjectReadRepository,
    ObjectRecordRow,
    OsdkResourceOperation,
    OsdkResourceType,
    TransactionContext,
)
from foundry_lite.application.ports.action_branch_repository import ActionBranchRepository
from foundry_lite.application.services.action_apply_contracts import ActionApplyCommand
from foundry_lite.application.services.action_apply_support import action_request_error
from foundry_lite.application.services.action_criteria_snapshot import (
    CriteriaLink,
    base_criteria_link,
    branch_link_overlays,
    compose_branch_records,
    criteria_snapshot_fingerprint,
    criteria_target,
    merge_link_overlays,
)
from foundry_lite.application.services.action_protocols import ActionOntologyLookup
from foundry_lite.application.services.object_store.row_policies import row_policy_scope, row_visible
from foundry_lite.domain.action_runtime.action_conditions import (
    LinkedObjectPropertyReference,
    referenced_linked_object_properties,
)
from foundry_lite.domain.action_runtime.action_contract import compile_action_contract
from foundry_lite.domain.action_runtime.edit_plan import CriteriaReadExpectation, EditPlan
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, PermissionDenied, ValidationFailed
from foundry_lite.security.policy import PolicyService

MAX_CRITERIA_LINK_FANOUT = 1_000


class ActionCriteriaScopeBoundary(Protocol):
    def require_resource_scope(
        self,
        ctx: RequestContext,
        *,
        resource_type: OsdkResourceType,
        resource_api_name: str,
        operation: OsdkResourceOperation,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class ResolvedLinkedCriteria:
    values: Mapping[str, object]
    expectations: tuple[CriteriaReadExpectation, ...]


@dataclass(frozen=True, slots=True)
class ActionCriteriaCommitVerifier:
    policy: PolicyService
    object_repository: ObjectReadRepository
    ontology: ActionOntologyLookup
    scope_boundary: ActionCriteriaScopeBoundary
    branch_repository: ActionBranchRepository | None = None
    branch_id: str | None = None

    def verify(self, transaction: TransactionContext, ctx: RequestContext, plan: EditPlan) -> None:
        verify_linked_criteria_expectations(
            transaction,
            ctx,
            self.policy,
            self.object_repository,
            self.ontology,
            self.scope_boundary,
            plan.criteria_read_expectations,
            branch_repository=self.branch_repository,
            branch_id=self.branch_id,
        )


def linked_criteria_request_error(
    transaction: TransactionContext,
    ctx: RequestContext,
    policy: PolicyService,
    object_repository: ObjectReadRepository,
    ontology: ActionOntologyLookup,
    scope_boundary: ActionCriteriaScopeBoundary,
    action_type: ActionTypeRow,
    target: ObjectRecordRow | None,
    command: ActionApplyCommand,
) -> Exception | None:
    resolved = resolve_linked_condition_context(
        transaction, ctx, policy, object_repository, ontology, scope_boundary, action_type, target
    )
    return action_request_error(policy, ctx, action_type, target, command, linked_object_properties=resolved.values)


def resolve_linked_condition_values(
    transaction: TransactionContext,
    ctx: RequestContext,
    policy: PolicyService,
    object_repository: ObjectReadRepository,
    ontology: ActionOntologyLookup,
    scope_boundary: ActionCriteriaScopeBoundary,
    action_type: ActionTypeRow,
    target: ObjectRecordRow,
    *,
    branch_repository: ActionBranchRepository | None = None,
    branch_id: str | None = None,
) -> Mapping[str, object]:
    """Resolve every linked criteria source in the caller's visible snapshot."""
    return resolve_linked_condition_context(
        transaction,
        ctx,
        policy,
        object_repository,
        ontology,
        scope_boundary,
        action_type,
        target,
        branch_repository=branch_repository,
        branch_id=branch_id,
    ).values


def resolve_linked_condition_context(
    transaction: TransactionContext,
    ctx: RequestContext,
    policy: PolicyService,
    object_repository: ObjectReadRepository,
    ontology: ActionOntologyLookup,
    scope_boundary: ActionCriteriaScopeBoundary,
    action_type: ActionTypeRow,
    target: ObjectRecordRow | None,
    *,
    branch_repository: ActionBranchRepository | None = None,
    branch_id: str | None = None,
) -> ResolvedLinkedCriteria:
    criteria = compile_action_contract(action_type["definition"]).submission_criteria
    if criteria is None or target is None:
        return ResolvedLinkedCriteria({}, ())
    references = referenced_linked_object_properties(criteria)
    resolved = [
        _resolve_reference(
            transaction,
            ctx,
            policy,
            object_repository,
            ontology,
            scope_boundary,
            target,
            reference,
            branch_repository,
            branch_id,
        )
        for reference in sorted(references)
    ]
    return ResolvedLinkedCriteria(
        {reference.key: value for reference, value, _expectation in resolved},
        tuple(expectation for _reference, _value, expectation in resolved),
    )


def _resolve_reference(
    transaction: TransactionContext,
    ctx: RequestContext,
    policy: PolicyService,
    repository: ObjectReadRepository,
    ontology: ActionOntologyLookup,
    scope_boundary: ActionCriteriaScopeBoundary,
    target: ObjectRecordRow,
    reference: LinkedObjectPropertyReference,
    branch_repository: ActionBranchRepository | None,
    branch_id: str | None,
) -> tuple[LinkedObjectPropertyReference, object, CriteriaReadExpectation]:
    link = ontology.link_type(transaction, ctx, reference.link_type)
    linked_type = _linked_object_type(link, target["object_type_api_name"], reference)
    _authorize_linked_read(ctx, policy, scope_boundary, reference, linked_type)
    links = _linked_rows(transaction, ctx, repository, target, reference, branch_repository, branch_id)
    all_records, visible_records = _linked_records(
        transaction,
        ctx,
        repository,
        ontology,
        linked_type,
        links,
        reference,
        branch_repository,
        branch_id,
    )
    _require_property_visible(ctx, policy, linked_type, reference.property_name)
    value: object = (
        len(visible_records)
        if reference.aggregation == "count"
        else tuple(record["properties"].get(reference.property_name) for record in visible_records)
    )
    fingerprint = criteria_snapshot_fingerprint(links, all_records)
    return reference, value, _read_expectation(reference, target, linked_type, fingerprint)


def _linked_object_type(link: Mapping[str, object], target_type: str, reference: LinkedObjectPropertyReference) -> str:
    anchor_key, linked_key = (
        ("from_api_name", "to_api_name") if reference.direction == "outgoing" else ("to_api_name", "from_api_name")
    )
    anchor = str(link[anchor_key])
    if anchor != target_type:
        raise ValidationFailed(
            "linked-object criteria link is not anchored at the concrete Action target",
            details={"linkType": reference.link_type, "expectedAnchor": anchor, "objectType": target_type},
        )
    return str(link[linked_key])


def _authorize_linked_read(
    ctx: RequestContext,
    policy: PolicyService,
    scope_boundary: ActionCriteriaScopeBoundary,
    reference: LinkedObjectPropertyReference,
    linked_type: str,
) -> None:
    policy.require(ctx, "object:read")
    scope_boundary.require_resource_scope(
        ctx, resource_type="link", resource_api_name=reference.link_type, operation="read"
    )
    scope_boundary.require_resource_scope(ctx, resource_type="object", resource_api_name=linked_type, operation="read")


def _linked_rows(
    transaction: TransactionContext,
    ctx: RequestContext,
    repository: ObjectReadRepository,
    target: ObjectRecordRow,
    reference: LinkedObjectPropertyReference,
    branch_repository: ActionBranchRepository | None,
    branch_id: str | None,
) -> list[CriteriaLink]:
    rows = _base_link_rows(transaction, ctx, repository, target, reference)
    base = [base_criteria_link(row) for row in rows]
    overlays = branch_link_overlays(
        transaction,
        ctx,
        target,
        reference,
        branch_repository,
        branch_id,
        MAX_CRITERIA_LINK_FANOUT + 1,
    )
    if len(rows) > MAX_CRITERIA_LINK_FANOUT or len(overlays) > MAX_CRITERIA_LINK_FANOUT:
        raise ValidationFailed(
            "linked-object submission criteria exceeds the fanout limit",
            details={"linkType": reference.link_type, "limit": MAX_CRITERIA_LINK_FANOUT},
        )
    return merge_link_overlays(base, overlays)


def _base_link_rows(
    transaction: TransactionContext,
    ctx: RequestContext,
    repository: ObjectReadRepository,
    target: ObjectRecordRow,
    reference: LinkedObjectPropertyReference,
) -> list[ObjectLinkRow]:
    if reference.direction == "outgoing":
        return repository.active_links_from(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            link_type_api_name=reference.link_type,
            from_api_name=target["object_type_api_name"],
            from_object_id=target["object_id"],
            limit=MAX_CRITERIA_LINK_FANOUT + 1,
        )
    return repository.active_links_to(
        transaction=transaction,
        tenant_id=ctx.tenant_id,
        link_type_api_name=reference.link_type,
        to_api_name=target["object_type_api_name"],
        to_object_id=target["object_id"],
        limit=MAX_CRITERIA_LINK_FANOUT + 1,
    )


def _linked_records(
    transaction: TransactionContext,
    ctx: RequestContext,
    repository: ObjectReadRepository,
    ontology: ActionOntologyLookup,
    linked_type: str,
    links: Sequence[CriteriaLink],
    reference: LinkedObjectPropertyReference,
    branch_repository: ActionBranchRepository | None,
    branch_id: str | None,
) -> tuple[list[ObjectRecordRow], list[ObjectRecordRow]]:
    object_ids = [
        row.to_object_id if reference.direction == "outgoing" else row.from_object_id for row in links if row.is_active
    ]
    base_records = repository.object_records(
        transaction=transaction,
        tenant_id=ctx.tenant_id,
        object_type_api_name=linked_type,
        object_ids=object_ids,
    )
    records = compose_branch_records(
        transaction, ctx, linked_type, object_ids, base_records, branch_repository, branch_id
    )
    by_id = {record["object_id"]: record for record in records if not record["deleted"]}
    scope = row_policy_scope(ontology._active_object_type(transaction, ctx, linked_type), ctx.roles)
    visible = [
        by_id[object_id]
        for object_id in object_ids
        if object_id in by_id and row_visible(scope, by_id[object_id]["properties"])
    ]
    return records, visible


def _require_property_visible(ctx: RequestContext, policy: PolicyService, object_type: str, property_name: str) -> None:
    if property_name in policy.masked_property_names(ctx, object_type):
        raise PermissionDenied(
            "linked-object submission criteria cannot read a masked property",
            details={"objectType": object_type, "property": property_name},
        )


def _read_expectation(
    reference: LinkedObjectPropertyReference,
    target: ObjectRecordRow,
    linked_type: str,
    fingerprint: str,
) -> CriteriaReadExpectation:
    return CriteriaReadExpectation(
        reference_key=reference.key,
        link_type=reference.link_type,
        direction=reference.direction,
        anchor_object_type=target["object_type_api_name"],
        anchor_object_id=target["object_id"],
        linked_object_type=linked_type,
        property_name=reference.property_name,
        aggregation=reference.aggregation,
        snapshot_fingerprint=fingerprint,
    )


def with_criteria_expectations(plan: EditPlan, expectations: tuple[CriteriaReadExpectation, ...]) -> EditPlan:
    return replace(plan, criteria_read_expectations=expectations)


def verify_linked_criteria_expectations(
    transaction: TransactionContext,
    ctx: RequestContext,
    policy: PolicyService,
    object_repository: ObjectReadRepository,
    ontology: ActionOntologyLookup,
    scope_boundary: ActionCriteriaScopeBoundary,
    expectations: Sequence[CriteriaReadExpectation],
    *,
    branch_repository: ActionBranchRepository | None = None,
    branch_id: str | None = None,
) -> None:
    for expectation in expectations:
        _verify_expectation(
            transaction,
            ctx,
            policy,
            object_repository,
            ontology,
            scope_boundary,
            expectation,
            branch_repository,
            branch_id,
        )


def _verify_expectation(
    transaction: TransactionContext,
    ctx: RequestContext,
    policy: PolicyService,
    repository: ObjectReadRepository,
    ontology: ActionOntologyLookup,
    scope_boundary: ActionCriteriaScopeBoundary,
    expectation: CriteriaReadExpectation,
    branch_repository: ActionBranchRepository | None,
    branch_id: str | None,
) -> None:
    target = criteria_target(transaction, ctx, repository, expectation, branch_repository, branch_id)
    reference = LinkedObjectPropertyReference(
        expectation.link_type,
        expectation.direction,
        expectation.property_name,
        expectation.aggregation,
    )
    _reference, _value, current = _resolve_reference(
        transaction,
        ctx,
        policy,
        repository,
        ontology,
        scope_boundary,
        target,
        reference,
        branch_repository,
        branch_id,
    )
    if current != expectation:
        raise ConflictDetected(
            "linked-object submission criteria changed after planning",
            details={"reference": expectation.reference_key},
        )
