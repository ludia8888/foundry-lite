"""Reviewer-facing release diff, validation, impact, and risk projections."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.services.pipeline_graph_model import output_dataset_refs

JsonObject = Mapping[str, object]
_RISK_POLICY_VERSION = "foundry-lite-release-risk-v1"


def release_candidate_evidence(kind: str, proposal: JsonObject, evidence: JsonObject) -> dict[str, object]:
    if kind == "pipeline":
        return _pipeline_candidate_evidence(proposal, evidence)
    return _ontology_candidate_evidence(proposal, evidence)


def _pipeline_candidate_evidence(proposal: JsonObject, evidence: JsonObject) -> dict[str, object]:
    change_diff_value = evidence.get("changeDiff")
    change_diff = change_diff_value if isinstance(change_diff_value, Mapping) else None
    receipt_value = evidence.get("testReceipt")
    receipt = receipt_value if isinstance(receipt_value, Mapping) else None
    graph_value = proposal.get("graph")
    graph = graph_value if isinstance(graph_value, Mapping) else {}
    external_ci = _external_ci_evidence(evidence)
    return {
        "changeDiff": dict(change_diff) if change_diff is not None else _missing_diff(proposal),
        "validationEvidence": _pipeline_validation_evidence(receipt, external_ci),
        "impactScope": _pipeline_impact_scope(graph, change_diff),
        "riskClassification": _pipeline_risk(change_diff, receipt, external_ci),
    }


def _ontology_candidate_evidence(proposal: JsonObject, evidence: JsonObject | None = None) -> dict[str, object]:
    plan = proposal.get("submittedMigrationPlan")
    migration_plan = plan if isinstance(plan, Mapping) else None
    changes = _mapping_items(migration_plan.get("changes")) if migration_plan is not None else []
    external_ci = _external_ci_evidence(evidence or {})
    return {
        "changeDiff": _ontology_change_diff(proposal, migration_plan, changes),
        "validationEvidence": _ontology_validation_evidence(proposal, migration_plan, external_ci),
        "impactScope": _ontology_impact_scope(migration_plan, changes, proposal),
        "riskClassification": _ontology_risk(
            migration_plan,
            changes,
            external_ci,
            has_resource_diff=bool(_ontology_resource_items(proposal)),
            is_revalidated_against_active=_is_revalidated_against_active(migration_plan, evidence or {}),
        ),
    }


def _pipeline_validation_evidence(
    receipt: Mapping[str, object] | None,
    external_ci: Mapping[str, object],
) -> list[dict[str, object]]:
    status = str(receipt.get("status")) if receipt is not None else "missing"
    return [
        {
            "id": "pipeline-durable-tests",
            "label": "Current graph validation and declared tests",
            "status": status,
            "proofKind": receipt.get("proofKind") if receipt is not None else "static_graph_output_contract",
            "details": dict(receipt or {}),
        },
        dict(external_ci),
    ]


def _ontology_validation_evidence(
    proposal: JsonObject,
    plan: Mapping[str, object] | None,
    external_ci: Mapping[str, object],
) -> list[dict[str, object]]:
    status = str(plan.get("status")) if plan is not None else "missing"
    migration_status = _migration_validation_status(status)
    structural_value = proposal.get("validation")
    structural = structural_value if isinstance(structural_value, Mapping) else None
    return [
        {
            "id": "ontology-structural-validation",
            "label": "Ontology YAML structural validation",
            "status": "passed" if structural is not None else "missing",
            "proofKind": "ontology_yaml_validation",
            "details": dict(structural or {}),
        },
        {
            "id": "ontology-migration-plan",
            "label": "Consumer and SDK compatibility plan",
            "status": migration_status,
            "proofKind": "ontology_migration_plan",
            "details": {**_plan_summary(plan), "evidenceBasis": "submitted_at"},
        },
        dict(external_ci),
    ]


def _external_ci_evidence(evidence: JsonObject) -> dict[str, object]:
    source_value = evidence.get("externalSourceControl")
    source = source_value if isinstance(source_value, Mapping) else {}
    receipt = source.get("ciReceipt")
    if isinstance(receipt, Mapping):
        return dict(receipt)
    return {
        "id": "external-ci-receipt",
        "label": "Candidate-scoped external CI receipt",
        "status": "not_available",
        "proofKind": "external_ci",
        "details": {"reason": "no candidate-scoped external CI receipt is persisted"},
    }


def _pipeline_impact_scope(
    graph: Mapping[str, object],
    change_diff: Mapping[str, object] | None,
) -> dict[str, object]:
    diff_items = _mapping_items(change_diff.get("items")) if change_diff is not None else []
    resources = [
        {
            "type": item.get("resourceType"),
            "id": item.get("resourceId"),
            "label": item.get("summary"),
            "impact": item.get("changeType"),
        }
        for item in diff_items
        if item.get("resourceType") != "pipeline_layout"
    ]
    resources.extend(
        {"type": "output_dataset", "id": ref, "label": ref, "impact": "serving_output"}
        for ref in _pipeline_output_refs(graph)
    )
    return {
        "summary": f"{len(resources)} operational resources in scope",
        "resources": resources,
        "isComplete": isinstance(graph.get("nodes"), list) and change_diff is not None,
    }


def _ontology_impact_scope(
    plan: Mapping[str, object] | None,
    changes: Sequence[Mapping[str, object]],
    proposal: JsonObject | None = None,
) -> dict[str, object]:
    resources = [
        {"type": "ontology_contract", "id": item.get("path"), "label": item.get("kind"), "impact": item.get("status")}
        for item in changes
    ]
    # Scope has to name the resources the reviewer is approving, not only the ones that threaten a
    # consumer, or a pure addition reports an empty blast radius.
    resources.extend(
        {
            "type": str(item.get("resourceType", "ontology_resource")),
            "id": item.get("resourceId"),
            "label": item.get("resourceType"),
            "impact": item.get("changeType"),
        }
        for item in _ontology_resource_items(proposal or {})
    )
    reindex = _mapping_items(plan.get("objectReindexPlan")) if plan is not None else []
    resources.extend(
        {
            "type": "object_index",
            "id": item.get("objectTypeApiName"),
            "label": item.get("reason"),
            "impact": "reindex_required",
        }
        for item in reindex
    )
    return {"summary": f"{len(resources)} ontology resources in scope", "resources": resources}


def _pipeline_risk(
    change_diff: Mapping[str, object] | None,
    receipt: Mapping[str, object] | None,
    external_ci: Mapping[str, object],
) -> dict[str, object]:
    if change_diff is None:
        return _risk("unclassified", ["Pipeline branch diff is unavailable."])
    items = _mapping_items(change_diff.get("items"))
    test_status = receipt.get("status") if receipt is not None else "missing"
    is_external_ci_passed = external_ci.get("status") == "passed"
    if test_status != "passed":
        return _risk(
            "high",
            [f"Durable candidate tests are {test_status}.", *_external_ci_reason(is_external_ci_passed)],
            missing_evidence=_pipeline_missing_evidence(is_external_ci_passed),
        )
    return _classified_pipeline_risk(items, is_external_ci_passed)


def _classified_pipeline_risk(
    items: Sequence[Mapping[str, object]],
    is_external_ci_passed: bool,
) -> dict[str, object]:
    high_change = any(
        item.get("changeType") == "removed"
        or item.get("resourceType") in {"pipeline_output_contract", "pipeline_schedule"}
        for item in items
    )
    if high_change:
        return _risk(
            "high",
            [
                "The change removes resources or changes output/schedule contracts.",
                *_external_ci_reason(is_external_ci_passed),
            ],
            is_complete=is_external_ci_passed,
            missing_evidence=_pipeline_missing_evidence(is_external_ci_passed),
        )
    if items:
        return _risk(
            "medium",
            ["The Pipeline execution graph changes.", *_external_ci_reason(is_external_ci_passed)],
            is_complete=is_external_ci_passed,
            missing_evidence=_pipeline_missing_evidence(is_external_ci_passed),
        )
    return _risk(
        "low",
        ["No operational Pipeline graph change was detected.", *_external_ci_reason(is_external_ci_passed)],
        is_complete=is_external_ci_passed,
        missing_evidence=_pipeline_missing_evidence(is_external_ci_passed),
    )


def _ontology_risk(
    plan: Mapping[str, object] | None,
    changes: Sequence[Mapping[str, object]],
    external_ci: Mapping[str, object],
    *,
    has_resource_diff: bool = False,
    is_revalidated_against_active: bool = False,
) -> dict[str, object]:
    if plan is None:
        return _risk(
            "unclassified",
            ["Ontology migration plan is unavailable."],
            missing_evidence=["external_ci", "full_resource_diff", "current_active_revalidation"],
        )
    level, reason = _ontology_plan_risk(plan, changes)
    return _ontology_submitted_risk(
        level,
        [reason],
        external_ci,
        has_resource_diff,
        is_revalidated_against_active,
    )


def _ontology_plan_risk(
    plan: Mapping[str, object],
    changes: Sequence[Mapping[str, object]],
) -> tuple[str, str]:
    if _mapping_items(plan.get("blockedChanges")):
        return "critical", "The migration plan contains consumer-blocking changes."
    if _mapping_items(plan.get("objectReindexPlan")):
        return "high", "The submitted plan requires a deterministic object reindex."
    if _mapping_items(plan.get("warnings")) or changes:
        return "medium", "The submitted plan still affects consumer contracts."
    return "low", "The submitted plan reports no blocking or warning changes."


def _risk(
    level: str,
    reasons: list[str],
    *,
    is_complete: bool = False,
    missing_evidence: list[str] | None = None,
) -> dict[str, object]:
    return {
        "level": level,
        "reasons": reasons,
        "policyVersion": _RISK_POLICY_VERSION,
        "isComplete": is_complete,
        "missingEvidence": list(missing_evidence or ([] if is_complete else ["external_ci"])),
    }


def _ontology_submitted_risk(
    level: str,
    reasons: list[str],
    external_ci: Mapping[str, object],
    has_resource_diff: bool = False,
    is_revalidated_against_active: bool = False,
) -> dict[str, object]:
    is_external_ci_passed = external_ci.get("status") == "passed"
    return _risk(
        level,
        [
            *reasons,
            *_external_ci_reason(is_external_ci_passed),
            *([] if is_revalidated_against_active else ["The submitted plan predates the active Ontology version."]),
        ],
        missing_evidence=[
            *([] if is_external_ci_passed else ["external_ci"]),
            *([] if has_resource_diff else ["full_resource_diff"]),
            *([] if is_revalidated_against_active else ["current_active_revalidation"]),
        ],
    )


def _external_ci_reason(is_passed: bool) -> list[str]:
    return [] if is_passed else ["Candidate-scoped external CI is not confirmed as passed."]


def _pipeline_missing_evidence(is_external_ci_passed: bool) -> list[str]:
    return [] if is_external_ci_passed else ["external_ci"]


def _is_revalidated_against_active(plan: Mapping[str, object] | None, evidence: JsonObject) -> bool:
    """Whether the submitted plan still describes a merge into the Ontology that is active now.

    Foundry runs merge checks against Main rather than trusting evidence captured when a proposal
    was opened, because Main keeps moving while a proposal waits for review. The submitted plan
    names the version it was computed against, so it only stays a valid merge check while that
    version is still the active one; once Main advances the branch has to be rebased and the plan
    recomputed.
    """

    if plan is None:
        return False
    submitted_against = plan.get("sourceOntologyVersionId")
    active = evidence.get("activeOntology")
    active_id = active.get("ontologyVersionId") if isinstance(active, Mapping) else None
    if not isinstance(submitted_against, str) or not isinstance(active_id, str):
        return False
    return submitted_against == active_id


def _ontology_resource_items(proposal: JsonObject) -> list[dict[str, object]]:
    """Project the branch's per-resource changes into reviewable candidate diff entries."""

    source = proposal.get("sourceBranch")
    resources = source.get("resources") if isinstance(source, Mapping) else None
    if not isinstance(resources, list):
        return []
    return [
        {
            "changeType": str(item.get("change", "modified")),
            "resourceType": str(item.get("kind", "ontology_resource")),
            "resourceId": str(item.get("apiName", "")),
            "summary": f"{item.get('kind', 'resource')} {item.get('apiName', '')} {item.get('change', 'modified')}",
        }
        for item in resources
        if isinstance(item, Mapping) and item.get("apiName")
    ]


def _diff_completeness(proposal: JsonObject, plan: Mapping[str, object] | None) -> str:
    if plan is None:
        return "unavailable"
    return "full_resource" if _ontology_resource_items(proposal) else "compatibility_only"


def _ontology_change_diff(
    proposal: JsonObject,
    plan: Mapping[str, object] | None,
    changes: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    items = [
        {
            "changeType": item.get("status", "modified"),
            "resourceType": "ontology_contract",
            "resourceId": item.get("path"),
            "summary": item.get("reason", item.get("kind", "ontology change")),
        }
        for item in changes
    ]
    # A reviewer approves resource by resource, so every changed resource has to appear even when
    # it threatens no consumer. The migration plan only carries compatibility-relevant entries, so
    # a branch that purely adds object types renders as "no changes" without this.
    items.extend(_ontology_resource_items(proposal))
    return {
        "completeness": _diff_completeness(proposal, plan),
        "evidenceBasis": "submitted_at",
        "summary": {
            "totalChangeCount": len(items),
            "blockedCount": len(_mapping_items(plan.get("blockedChanges"))) if plan is not None else 0,
            "warningCount": len(_mapping_items(plan.get("warnings"))) if plan is not None else 0,
        },
        "baseRef": plan.get("sourceOntologyVersionId") if plan is not None else None,
        "candidateRef": proposal.get("fingerprint"),
        "items": items,
    }


def _missing_diff(proposal: JsonObject) -> dict[str, object]:
    return {
        "summary": {"totalChangeCount": None, "status": "missing"},
        "candidateRef": proposal.get("graphFingerprint"),
        "items": [],
    }


def _plan_summary(plan: Mapping[str, object] | None) -> dict[str, object]:
    if plan is None:
        return {"status": "missing"}
    return {
        "status": plan.get("status"),
        "consumerCompatibility": plan.get("consumerCompatibility"),
        "sdkCompatibility": plan.get("sdkCompatibility"),
        "blockedCount": len(_mapping_items(plan.get("blockedChanges"))),
        "warningCount": len(_mapping_items(plan.get("warnings"))),
        "reindexCount": len(_mapping_items(plan.get("objectReindexPlan"))),
    }


def _mapping_items(value: object) -> list[Mapping[str, object]]:
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def _pipeline_output_refs(graph: Mapping[str, object]) -> list[str]:
    return output_dataset_refs(graph) if isinstance(graph.get("nodes"), list) else []


def _migration_validation_status(status: str) -> str:
    if status == "compatible":
        return "passed"
    if status == "compatible_with_warning":
        return "warning"
    return status


__all__ = [
    "release_candidate_evidence",
]
