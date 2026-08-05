"""Branch-only Ontology tools used by the governed AI FDE runtime."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from foundry_lite.application.ports.ai_run_repository import AiToolCallRecord
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.aip.fde_palantir_mcp_catalog import PALANTIR_MCP_ONTOLOGY_TOOL_IDS
from foundry_lite.application.services.aip.tool_broker import ToolBrokerResult, ToolSpec
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.ontology_branch_diff import (
    ResourceMap,
    parse_resource_map,
    serialize_resource_map,
)
from foundry_lite.application.services.ontology_branch_service import OntologyBranchService
from foundry_lite.application.services.ontology_service import OntologyService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, ValidationFailed

JsonObject = Mapping[str, object]
_RESOURCE_KINDS = frozenset({"objectType", "linkType", "actionType", "interface", "functionType"})


@dataclass(frozen=True)
class FdeOntologyToolRequest:
    tool_call_id: str
    ai_run_id: str
    sequence: int
    branch_id: str
    spec: ToolSpec
    arguments: JsonObject
    approved_tool_ids: tuple[str, ...]
    max_output_bytes: int
    occurred_at: str


@dataclass
class FdeOntologyToolError(Exception):
    reason: str
    detail: str

    def __post_init__(self) -> None:
        Exception.__init__(self, self.detail)


class FdeOntologyToolService(CoreService):
    """Execute ontology authoring tools without ever writing the active ontology."""

    required_dependencies = ("policy",)
    required_collaborators = ("ontology_branch_service", "ontology_service")
    ontology_branch_service: OntologyBranchService
    ontology_service: OntologyService

    def execute(self, ctx: RequestContext, request: FdeOntologyToolRequest) -> ToolBrokerResult:
        self.policy.require(ctx, request.spec.required_permission)
        _require_approval(request)
        output = self._dispatch(ctx, request)
        return _result(ctx, request, output)

    def _dispatch(self, ctx: RequestContext, request: FdeOntologyToolRequest) -> dict[str, object]:
        if request.spec.tool_id in PALANTIR_MCP_ONTOLOGY_TOOL_IDS:
            return self._official_dispatch(ctx, request)
        if request.spec.tool_id == "ontology.branch.inspect":
            return self._inspect(ctx, request.branch_id)
        if request.spec.tool_id == "ontology.branch.validate":
            return self._validate(ctx, request.branch_id)
        if request.spec.tool_id == "ontology.branch.apply_patch":
            return self._apply_patch(ctx, request.branch_id, request.arguments)
        if request.spec.tool_id == "ontology.branch.propose":
            return self._propose(ctx, request.branch_id, request.arguments)
        raise FdeOntologyToolError("unknown_fde_tool", f"unsupported AI FDE tool {request.spec.tool_id}")

    def _official_dispatch(self, ctx: RequestContext, request: FdeOntologyToolRequest) -> dict[str, object]:
        tool_id = request.spec.tool_id
        if tool_id == "get_foundry_ontology_rid":
            return self._official_identity(ctx, request.branch_id)
        if tool_id in {"search_foundry_ontology", "search_foundry_functions"}:
            return self._official_search(
                ctx,
                request.branch_id,
                request.arguments,
                is_function_only=tool_id.endswith("functions"),
            )
        if tool_id.startswith("view_foundry_"):
            return self._official_view(ctx, request.branch_id, tool_id, request.arguments)
        return self._official_mutation(ctx, request.branch_id, tool_id, request.arguments)

    def _official_identity(self, ctx: RequestContext, branch_id: str) -> dict[str, object]:
        branch = self.ontology_branch_service.get_branch(branch_id, ctx=ctx)
        return {
            "ontologyRid": f"ri.foundry-lite.ontology.{ctx.tenant_id}",
            "branchRid": f"ri.foundry-lite.ontology-branch.{branch_id}",
            "branchId": branch_id,
            "baseVersionId": branch.get("baseVersionId"),
            "baseVersionNumber": branch.get("baseVersionNumber"),
            "contentFingerprint": branch.get("contentFingerprint"),
            "isBaseStale": branch.get("baseStale"),
        }

    def _official_search(
        self,
        ctx: RequestContext,
        branch_id: str,
        arguments: JsonObject,
        *,
        is_function_only: bool,
    ) -> dict[str, object]:
        query = _required_text(arguments, "query")
        resources = _branch_resources(self.ontology_branch_service.get_branch(branch_id, ctx=ctx))
        matches = _search_resources(resources, query, _bounded_results(arguments.get("maxResults")), is_function_only)
        return {"query": query, "items": matches, "count": len(matches), "branchId": branch_id}

    def _official_view(
        self, ctx: RequestContext, branch_id: str, tool_id: str, arguments: JsonObject
    ) -> dict[str, object]:
        api_name = _required_text(arguments, "apiName")
        kind = _official_kind(tool_id)
        definition = _branch_resources(self.ontology_branch_service.get_branch(branch_id, ctx=ctx)).get(
            (kind, api_name)
        )
        if definition is None:
            raise NotFound("Ontology branch resource not found", details={"kind": kind, "apiName": api_name})
        return {"branchId": branch_id, "kind": kind, "apiName": api_name, "definition": definition}

    def _official_mutation(
        self, ctx: RequestContext, branch_id: str, tool_id: str, arguments: JsonObject
    ) -> dict[str, object]:
        kind = _official_kind(tool_id)
        change_summary = _required_text(arguments, "changeSummary")
        patch: dict[str, object]
        if tool_id.startswith("delete_"):
            patch = {
                "upsertResources": [],
                "deleteResources": [{"kind": kind, "apiName": _required_text(arguments, "apiName")}],
            }
        else:
            definition = _required_mapping(arguments, "definition")
            patch = {
                "upsertResources": [{"kind": kind, "definition": definition}],
                "deleteResources": [],
            }
        return self._apply_patch(ctx, branch_id, {**patch, "changeSummary": change_summary})

    def _inspect(self, ctx: RequestContext, branch_id: str) -> dict[str, object]:
        branch = self.ontology_branch_service.get_branch(branch_id, ctx=ctx)
        yaml_text = _branch_yaml(branch)
        resources = [
            {"kind": kind, "apiName": api_name, "definition": definition}
            for (kind, api_name), definition in sorted(parse_resource_map(yaml_text).items())
        ]
        return {"branch": _public_branch(branch), "resources": resources, "diff": self._diff(ctx, branch_id)}

    def _validate(self, ctx: RequestContext, branch_id: str) -> dict[str, object]:
        branch = self.ontology_branch_service.get_branch(branch_id, ctx=ctx)
        validation = self.ontology_service.validate_yaml_text(_branch_yaml(branch), ctx=ctx)
        return {"branch": _public_branch(branch), "validation": dict(validation), "diff": self._diff(ctx, branch_id)}

    def _apply_patch(self, ctx: RequestContext, branch_id: str, arguments: JsonObject) -> dict[str, object]:
        branch = self.ontology_branch_service.get_branch(branch_id, ctx=ctx)
        resources = parse_resource_map(_branch_yaml(branch))
        _apply_upserts(resources, arguments.get("upsertResources"))
        _apply_deletes(resources, arguments.get("deleteResources"))
        updated = self.ontology_branch_service.update_branch_content(
            branch_id,
            yaml_text=serialize_resource_map(resources),
            expected_fingerprint=_branch_fingerprint(branch),
            ctx=ctx,
        )
        detail = self.ontology_branch_service.get_branch(branch_id, ctx=ctx)
        validation = self.ontology_service.validate_yaml_text(_branch_yaml(detail), ctx=ctx)
        return {
            "branch": _public_branch(updated),
            "changeSummary": _required_text(arguments, "changeSummary"),
            "validation": dict(validation),
            "diff": self._diff(ctx, branch_id),
        }

    def _propose(self, ctx: RequestContext, branch_id: str, arguments: JsonObject) -> dict[str, object]:
        return self.ontology_branch_service.propose_branch(
            branch_id,
            title=_required_text(arguments, "title"),
            description=_required_text(arguments, "description"),
            idempotency_key=_required_text(arguments, "idempotencyKey"),
            ctx=ctx,
        )

    def _diff(self, ctx: RequestContext, branch_id: str) -> dict[str, object]:
        return self.ontology_branch_service.branch_diff(branch_id, ctx=ctx)


def _require_approval(request: FdeOntologyToolRequest) -> None:
    if request.spec.effect == "READ":
        return
    if request.spec.tool_id not in request.approved_tool_ids:
        raise FdeOntologyToolError(
            "tool_approval_required",
            f"explicit user approval is required for {request.spec.tool_id} on branch {request.branch_id}",
        )


def _branch_resources(branch: JsonObject) -> ResourceMap:
    return parse_resource_map(_branch_yaml(branch))


def _search_resources(
    resources: ResourceMap,
    query: str,
    limit: int,
    is_function_only: bool,
) -> list[dict[str, object]]:
    terms = tuple(term for term in query.lower().split() if term)
    items: list[dict[str, object]] = []
    for (kind, api_name), definition in sorted(resources.items()):
        if is_function_only and kind != "functionType":
            continue
        text = f"{kind} {api_name} {definition.get('displayName', '')} {definition.get('description', '')}".lower()
        if all(term in text for term in terms):
            items.append({"kind": kind, "apiName": api_name, "definition": definition})
    return items[:limit]


def _official_kind(tool_id: str) -> str:
    for fragment, kind in (
        ("object_type", "objectType"),
        ("link_type", "linkType"),
        ("action_type", "actionType"),
    ):
        if fragment in tool_id:
            return kind
    raise FdeOntologyToolError("unknown_fde_tool", f"unsupported Ontology resource tool {tool_id}")


def _bounded_results(value: object) -> int:
    if value is None:
        return 20
    if not isinstance(value, int) or isinstance(value, bool) or value < 1 or value > 50:
        raise ValidationFailed("maxResults must be between 1 and 50")
    return value


def _required_mapping(value: JsonObject, key: str) -> dict[str, object]:
    item = value.get(key)
    if not isinstance(item, Mapping):
        raise ValidationFailed(f"{key} must be an object", details={"field": key})
    return _json_mapping(item)


def _apply_upserts(resources: ResourceMap, value: object) -> None:
    for entry in _mapping_sequence(value, "upsertResources"):
        kind = _resource_kind(entry)
        definition = entry.get("definition")
        if not isinstance(definition, Mapping):
            raise ValidationFailed("AI FDE resource definition must be an object")
        normalized = _json_mapping(definition)
        api_name = _required_text(normalized, "apiName")
        resources[(kind, api_name)] = normalized


def _apply_deletes(resources: ResourceMap, value: object) -> None:
    for entry in _mapping_sequence(value, "deleteResources"):
        resources.pop((_resource_kind(entry), _required_text(entry, "apiName")), None)


def _resource_kind(entry: JsonObject) -> str:
    kind = _required_text(entry, "kind")
    if kind not in _RESOURCE_KINDS:
        raise ValidationFailed("unsupported AI FDE ontology resource kind", details={"kind": kind})
    return kind


def _mapping_sequence(value: object, field: str) -> tuple[JsonObject, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ValidationFailed(f"{field} must be a list")
    if not all(isinstance(item, Mapping) for item in value):
        raise ValidationFailed(f"{field} entries must be objects")
    return tuple(item for item in value if isinstance(item, Mapping))


def _required_text(value: JsonObject, key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValidationFailed(f"{key} is required", details={"field": key})
    return item.strip()


def _branch_yaml(branch: JsonObject) -> str:
    return _required_text(branch, "yamlText")


def _branch_fingerprint(branch: JsonObject) -> str:
    return _required_text(branch, "contentFingerprint")


def _public_branch(branch: JsonObject) -> dict[str, object]:
    return {key: value for key, value in branch.items() if key not in {"yamlText", "baseYamlText"}}


def _json_mapping(value: Mapping[object, object]) -> dict[str, object]:
    return json.loads(json.dumps(value, sort_keys=True))


def _result(ctx: RequestContext, request: FdeOntologyToolRequest, output: JsonObject) -> ToolBrokerResult:
    public_output = _bounded_output(request, output)
    arguments_hash = _hash_json(request.arguments)
    result_hash = _hash_json(output)
    decision = "allowed_read" if request.spec.effect == "READ" else "allowed_by_user_preapproval"
    ledger = AiToolCallRecord(
        id=request.tool_call_id,
        tenant_id=ctx.tenant_id,
        ai_run_id=request.ai_run_id,
        sequence=request.sequence,
        tool_id=request.spec.tool_id,
        tool_version=request.spec.version,
        arguments_hash=arguments_hash,
        effect=request.spec.effect,
        authorization_decision=decision,
        confirmation_policy=request.spec.confirmation_policy,
        status="succeeded",
        result_hash=result_hash,
        linked_action_run_id=None,
        started_at=request.occurred_at,
        completed_at=_now(),
        error_json=None,
        result_json=dict(output),
    )
    return ToolBrokerResult(
        tool_call_id=request.tool_call_id,
        status="succeeded",
        authorization_decision=decision,
        output_json=public_output,
        redacted_preview=json.dumps(public_output, sort_keys=True)[:512],
        arguments_hash=arguments_hash,
        result_hash=result_hash,
        ledger_record=ledger,
    )


def _bounded_output(request: FdeOntologyToolRequest, output: JsonObject) -> JsonObject:
    payload = json.dumps(output, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    if len(payload.encode()) <= request.max_output_bytes:
        return output
    if request.spec.effect == "READ":
        raise FdeOntologyToolError("budget_exceeded", "AI FDE tool result exceeds max_tool_output_bytes")
    compact = {
        "isOutputTruncated": True,
        "resultHash": _hash_json(output),
        "toolId": request.spec.tool_id,
        "nextStep": "Inspect or validate the branch to observe the committed working-copy change.",
    }
    compact_payload = json.dumps(compact, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    if len(compact_payload.encode()) > request.max_output_bytes:
        raise FdeOntologyToolError("budget_exceeded", "AI FDE compact result exceeds max_tool_output_bytes")
    return compact


def _hash_json(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(payload.encode()).hexdigest()}"
