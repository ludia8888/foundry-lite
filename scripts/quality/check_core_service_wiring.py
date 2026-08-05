from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICE_ROOT = ROOT / "libs" / "foundry_lite" / "application" / "services"
BASE_PATH = SERVICE_ROOT / "base.py"
CORE_SERVICES_PATH = ROOT / "libs" / "foundry_lite" / "application" / "core_services.py"
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "core_service_wiring.json"
NON_LEAF_SERVICE_CLASSES = {"CoreService", "CoreServices", "DatasetServices", "ObjectServices"}
AIP_COLLABORATORS = frozenset(
    {
        "action_proposal_service",
        "agent_runtime_service",
        "approval_execution_service",
        "builder_runtime_service",
        "citation_service",
        "context_compiler_service",
        "logic_runtime_service",
        "model_gateway_service",
        "prompt_artifact_service",
        "tool_broker_service",
        "visual_builder_service",
    }
)
ALLOWED_AIP_COLLABORATOR_EDGES = frozenset(
    {
        ("FunctionExecutionService", "builder_runtime_service"),
        ("OntologyActivationService", "visual_builder_service"),
    }
)
# Some services are public facade entrypoints rather than internal collaborators.
# They stay visible in the report but are not treated as dead wiring.
ALLOWED_UNUSED_COLLABORATORS = frozenset(
    {
        "action_effect_operator_service",
        "action_notification_policy_service",
        "action_proposal_service",
        "action_service",
        "agent_runtime_service",
        "approval_execution_service",
        "backup_restore_service",
        "builder_runtime_service",
        "citation_service",
        "connector_onboarding_service",
        "content_retrieval_service",
        "context_compiler_service",
        "dataset_quality_api_service",
        "dataset_quality_runtime_service",
        "demo_service",
        "function_execution_service",
        "iceberg_maintenance_service",
        "insight_review_service",
        "logic_runtime_service",
        "materialization_service",
        "media_transaction_service",
        "media_upload_service",
        "media_visual_search_service",
        "model_gateway_service",
        "object_cdc_indexing_service",
        "object_links_service",
        "object_ontology_reindex_service",
        "object_query_service",
        "object_records_service",
        "object_search_service",
        "object_sets_service",
        "object_subscription_service",
        "ontology_branch_service",
        "ontology_catalog_service",
        "ontology_insights_service",
        "ontology_lookup_service",
        "ontology_proposal_service",
        "ontology_reindex_contract_service",
        "ontology_service",
        "osdk_application_client_service",
        "osdk_application_idempotency_service",
        "osdk_application_scope_service",
        "osdk_application_sdk_service",
        "osdk_application_service",
        "osdk_oauth_session_service",
        "outbox_publisher_service",
        "prompt_artifact_service",
        "record_dlq_service",
        "runtime_service",
        "source_management_service",
        "source_onboarding_service",
        "source_scheduler_service",
        "tool_broker_service",
        "transform_service",
        "visual_builder_service",
        "workflow_orchestration_service",
    }
)
SERVICE_CONTEXT_PREFIXES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("osdk_",), "osdk"),
    (("dataset_", "transform_", "materialization_", "iceberg_"), "data"),
    (("object_", "ontology_"), "object"),
    (("source_", "connector_"), "source"),
    (("media_", "content_retrieval_"), "media"),
    (("backup_restore_",), "backup"),
    (("action_", "function_"), "action"),
    (("runtime_", "record_dlq_", "outbox_", "workflow_", "erasure_", "insight_review_"), "runtime"),
)


@dataclass(frozen=True)
class ServiceCollaboratorDeclaration:
    class_name: str
    path: str
    context: str
    collaborators: tuple[str, ...]


@dataclass(frozen=True)
class ForbiddenDependency:
    class_name: str
    path: str
    context: str
    collaborator: str
    message: str


def _service_files() -> list[Path]:
    return sorted(SERVICE_ROOT.rglob("*.py"))


def _service_collaborators_value(node: ast.AST) -> ast.expr | None:
    if isinstance(node, ast.Assign):
        if any(isinstance(target, ast.Name) and target.id == "SERVICE_COLLABORATORS" for target in node.targets):
            return node.value
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        if node.target.id == "SERVICE_COLLABORATORS":
            return node.value
    return None


def _collect_service_collaborator_registry() -> dict[str, str]:
    tree = ast.parse(BASE_PATH.read_text(encoding="utf-8"), filename=str(BASE_PATH))
    for node in ast.walk(tree):
        value = _service_collaborators_value(node)
        if not isinstance(value, ast.Dict):
            continue
        registry: dict[str, str] = {}
        for key, item in zip(value.keys, value.values, strict=True):
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                if isinstance(item, ast.Constant) and isinstance(item.value, str):
                    registry[key.value] = item.value
                else:
                    registry[key.value] = ""
        return registry
    return {}


def _collect_core_services_map_keys() -> set[str]:
    tree = ast.parse(CORE_SERVICES_PATH.read_text(encoding="utf-8"), filename=str(CORE_SERVICES_PATH))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or not node.name.endswith("_collaborator_map"):
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Dict):
                continue
            for key in child.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    keys.add(key.value)
    return keys


def _is_leaf_service_class(node: ast.ClassDef) -> bool:
    return node.name.endswith("Service") and node.name not in NON_LEAF_SERVICE_CLASSES


def _required_string_names(node: ast.AST | None) -> tuple[str, ...] | None:
    if not isinstance(node, ast.Tuple | ast.List | ast.Set):
        return None
    values: list[str] = []
    for item in node.elts:
        if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
            return None
        values.append(item.value)
    return tuple(sorted(values))


def _required_collaborators_value(item: ast.stmt) -> ast.AST | None:
    if isinstance(item, ast.Assign):
        for target in item.targets:
            if isinstance(target, ast.Name) and target.id == "required_collaborators":
                return item.value
    if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
        if item.target.id == "required_collaborators":
            return item.value
    return None


def _class_required_collaborators(node: ast.ClassDef) -> tuple[str, ...]:
    collaborators: tuple[str, ...] = ()
    for item in node.body:
        required = _required_string_names(_required_collaborators_value(item))
        if required is not None:
            collaborators = required
    return collaborators


def _collect_service_required_collaborators() -> dict[str, ServiceCollaboratorDeclaration]:
    declarations: dict[str, ServiceCollaboratorDeclaration] = {}
    for path in _service_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or not _is_leaf_service_class(node):
                continue
            declarations[node.name] = ServiceCollaboratorDeclaration(
                class_name=node.name,
                path=str(path.relative_to(ROOT)),
                context=_service_context(path, node.name),
                collaborators=_class_required_collaborators(node),
            )
    return declarations


def _service_context(path: Path, class_name: str) -> str:
    if "aip" in path.parts:
        return "aip"
    return _context_from_name(_camel_to_snake(class_name))


def _context_from_name(normalized: str) -> str:
    for prefixes, context in SERVICE_CONTEXT_PREFIXES:
        if normalized.startswith(prefixes):
            return context
    return "unknown"


def _camel_to_snake(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()


def _forbidden_dependency_findings(
    declarations: dict[str, ServiceCollaboratorDeclaration],
) -> list[ForbiddenDependency]:
    findings: list[ForbiddenDependency] = []
    for declaration in declarations.values():
        if declaration.context == "aip":
            continue
        for collaborator in declaration.collaborators:
            if collaborator not in AIP_COLLABORATORS:
                continue
            if (declaration.class_name, collaborator) in ALLOWED_AIP_COLLABORATOR_EDGES:
                continue
            findings.append(
                ForbiddenDependency(
                    class_name=declaration.class_name,
                    path=declaration.path,
                    context=declaration.context,
                    collaborator=collaborator,
                    message="non-AIP service declares a direct AIP collaborator dependency",
                )
            )
    return findings


def _write_report(
    output: Path,
    *,
    registry: dict[str, str],
    map_keys: set[str],
    declarations: dict[str, ServiceCollaboratorDeclaration],
    declared_missing_from_map: list[str],
    map_keys_not_declared: list[str],
    unused_map_keys: list[str],
    enforced_unused_map_keys: list[str],
    forbidden_edges: list[ForbiddenDependency],
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    required_keys = sorted({name for declaration in declarations.values() for name in declaration.collaborators})
    report = {
        "registry_keys": sorted(registry),
        "core_services_map_keys": sorted(map_keys),
        "required_collaborator_keys": required_keys,
        "service_required_collaborators": {
            class_name: {
                "path": declaration.path,
                "context": declaration.context,
                "collaborators": list(declaration.collaborators),
            }
            for class_name, declaration in sorted(declarations.items())
        },
        "declared_missing_from_map": declared_missing_from_map,
        "map_keys_not_declared": map_keys_not_declared,
        "unused_map_keys": unused_map_keys,
        "allowed_unused_map_keys": sorted(set(unused_map_keys) & ALLOWED_UNUSED_COLLABORATORS),
        "enforced_unused_map_keys": enforced_unused_map_keys,
        "forbidden_edges": [edge.__dict__ for edge in forbidden_edges],
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _print_failure_report(
    output: Path,
    *,
    declared_missing_from_map: list[str],
    map_keys_not_declared: list[str],
    enforced_unused_map_keys: list[str],
    forbidden_edges: list[ForbiddenDependency],
) -> None:
    print("Core service collaborator wiring mismatch:")
    for key in declared_missing_from_map:
        print(f"- registry declares {key!r}, but CoreServices does not provide it")
    for key in map_keys_not_declared:
        print(f"- CoreServices provides {key!r}, but SERVICE_COLLABORATORS does not declare it")
    for key in enforced_unused_map_keys:
        print(f"- CoreServices provides {key!r}, but no service declares it as required")
    for edge in forbidden_edges:
        print(f"- {edge.path} {edge.class_name}: {edge.message}: {edge.collaborator}")
    print(f"Report: {output.relative_to(ROOT)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    registry = _collect_service_collaborator_registry()
    map_keys = _collect_core_services_map_keys()
    declarations = _collect_service_required_collaborators()
    required_keys = {name for declaration in declarations.values() for name in declaration.collaborators}
    registry_keys = set(registry)
    declared_missing_from_map = sorted(registry_keys - map_keys)
    map_keys_not_declared = sorted(map_keys - registry_keys)
    unused_map_keys = sorted(map_keys - required_keys)
    enforced_unused_map_keys = sorted(set(unused_map_keys) - ALLOWED_UNUSED_COLLABORATORS)
    forbidden_edges = _forbidden_dependency_findings(declarations)

    _write_report(
        args.output,
        registry=registry,
        map_keys=map_keys,
        declarations=declarations,
        declared_missing_from_map=declared_missing_from_map,
        map_keys_not_declared=map_keys_not_declared,
        unused_map_keys=unused_map_keys,
        enforced_unused_map_keys=enforced_unused_map_keys,
        forbidden_edges=forbidden_edges,
    )

    if declared_missing_from_map or map_keys_not_declared or enforced_unused_map_keys or forbidden_edges:
        _print_failure_report(
            args.output,
            declared_missing_from_map=declared_missing_from_map,
            map_keys_not_declared=map_keys_not_declared,
            enforced_unused_map_keys=enforced_unused_map_keys,
            forbidden_edges=forbidden_edges,
        )
        return 1

    print(
        f"Core service wiring OK: {len(map_keys)} collaborators match SERVICE_COLLABORATORS and service declarations."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
