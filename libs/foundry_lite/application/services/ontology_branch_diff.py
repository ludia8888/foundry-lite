"""Pure three-way resource-level diff and merge rules for ontology branches.

Deterministic, no I/O: three YAML texts (the branch's recorded base, the
branch's working copy, and the currently active main definition) are parsed
into per-resource maps keyed by ``(kind, apiName)`` and classified per
resource. A resource conflicts only when BOTH the branch and main changed it
relative to the base and their results differ (deep equality over a JSON
round-trip normalization, so YAML formatting never causes false conflicts).

Merge (rebase) starts from the CURRENT main definition, re-applies the
branch's non-conflicted changes, and settles each conflict per an explicit
per-resource resolution ("main" keeps main's version, "branch" keeps the
branch's version, including a branch-side removal). The merged definition is
serialized with ``json.dumps``: JSON is a strict subset of YAML 1.2, so the
resulting text is valid input for the ontology YAML loader.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Literal, TypedDict, cast

import yaml

ResourceKind = Literal["objectType", "linkType", "actionType", "interface", "functionType"]
ResourceChange = Literal["added", "modified", "removed", "unchanged"]
ConflictResolution = Literal["main", "branch"]
ResourceKey = tuple[str, str]
ResourceMap = dict[ResourceKey, dict[str, object]]

_SECTION_BY_KIND: dict[str, str] = {
    "objectType": "objectTypes",
    "linkType": "linkTypes",
    "actionType": "actionTypes",
    "interface": "interfaces",
    "functionType": "functionTypes",
}
_ALWAYS_PRESENT_SECTIONS = ("objectTypes", "linkTypes", "actionTypes")


class BranchResourceDiff(TypedDict):
    kind: str
    apiName: str
    branchChange: ResourceChange
    mainChange: ResourceChange
    hasConflict: bool


class BranchDiffResult(TypedDict):
    resources: list[BranchResourceDiff]
    conflicts: list[BranchResourceDiff]


def parse_resource_map(yaml_text: str) -> ResourceMap:
    """Parse an ontology definition into a normalized per-resource map."""
    loaded = yaml.safe_load(yaml_text)
    definition = loaded if isinstance(loaded, Mapping) else {}
    resources: ResourceMap = {}
    for kind, section in _SECTION_BY_KIND.items():
        items = definition.get(section)
        if not isinstance(items, Sequence) or isinstance(items, str):
            continue
        for item in items:
            if isinstance(item, Mapping) and isinstance(item.get("apiName"), str):
                resources[(kind, str(item["apiName"]))] = _normalized(item)
    return resources


def evaluate_branch_diff(base: ResourceMap, branch: ResourceMap, main: ResourceMap) -> BranchDiffResult:
    """Classify every resource that changed on the branch and/or on main."""
    resources: list[BranchResourceDiff] = []
    conflicts: list[BranchResourceDiff] = []
    for key in sorted(set(base) | set(branch) | set(main)):
        branch_change = _change_for(base.get(key), branch.get(key))
        main_change = _change_for(base.get(key), main.get(key))
        if branch_change == "unchanged" and main_change == "unchanged":
            continue
        entry = BranchResourceDiff(
            kind=key[0],
            apiName=key[1],
            branchChange=branch_change,
            mainChange=main_change,
            hasConflict=_is_conflicting(branch_change, main_change, branch.get(key), main.get(key)),
        )
        resources.append(entry)
        if entry["hasConflict"]:
            conflicts.append(entry)
    return BranchDiffResult(resources=resources, conflicts=conflicts)


def unresolved_conflicts(
    diff: BranchDiffResult,
    resolutions: Mapping[ResourceKey, str],
) -> list[BranchResourceDiff]:
    """Return the conflicts that the given per-resource resolutions do not cover."""
    return [entry for entry in diff["conflicts"] if (entry["kind"], entry["apiName"]) not in resolutions]


def unknown_resolutions(
    diff: BranchDiffResult,
    resolutions: Mapping[ResourceKey, str],
) -> list[ResourceKey]:
    """Return resolution targets that are not current conflicts (client drift)."""
    conflict_keys = {(entry["kind"], entry["apiName"]) for entry in diff["conflicts"]}
    return sorted(key for key in resolutions if key not in conflict_keys)


def merge_branch_onto_main(
    base: ResourceMap,
    branch: ResourceMap,
    main: ResourceMap,
    diff: BranchDiffResult,
    resolutions: Mapping[ResourceKey, str],
) -> dict[str, object]:
    """Rebase: current main plus the branch's changes, conflicts settled per resolution."""
    merged: ResourceMap = dict(main)
    for entry in diff["resources"]:
        key = (entry["kind"], entry["apiName"])
        if entry["branchChange"] == "unchanged":
            continue
        if entry["hasConflict"] and resolutions.get(key) == "main":
            continue
        branch_value = branch.get(key)
        if branch_value is None:
            merged.pop(key, None)
        else:
            merged[key] = branch_value
    return _definition_from_resource_map(merged)


def serialize_definition_yaml(definition: Mapping[str, object]) -> str:
    """Serialize a definition as JSON text (a strict subset of YAML 1.2)."""
    return json.dumps(definition, indent=2, sort_keys=True)


def serialize_resource_map(resources: ResourceMap) -> str:
    """Serialize a normalized resource map back to canonical branch content."""
    return serialize_definition_yaml(_definition_from_resource_map(resources))


def _definition_from_resource_map(resources: ResourceMap) -> dict[str, object]:
    definition: dict[str, object] = {}
    for kind, section in _SECTION_BY_KIND.items():
        items = [resources[key] for key in sorted(resources) if key[0] == kind]
        if items or section in _ALWAYS_PRESENT_SECTIONS:
            definition[section] = items
    return definition


def _change_for(base_value: dict[str, object] | None, other_value: dict[str, object] | None) -> ResourceChange:
    if base_value is None and other_value is None:
        return "unchanged"
    if base_value is None:
        return "added"
    if other_value is None:
        return "removed"
    return "unchanged" if base_value == other_value else "modified"


def _is_conflicting(
    branch_change: ResourceChange,
    main_change: ResourceChange,
    branch_value: dict[str, object] | None,
    main_value: dict[str, object] | None,
) -> bool:
    if branch_change == "unchanged" or main_change == "unchanged":
        return False
    # Both sides changed the resource: identical outcomes converge silently.
    return branch_value != main_value


def _normalized(item: Mapping[str, object]) -> dict[str, object]:
    # JSON round-trip: mapping in, mapping out; drops YAML-only node types.
    return cast(dict[str, object], json.loads(json.dumps(item, sort_keys=True)))
