"""Application-layer models and helpers for osdk manifest."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TypedDict

from foundry_lite.domain.errors import ValidationFailed


class OsdkPackageManifest(TypedDict):
    package_name: str
    package_version: str
    generator_name: str
    generator_version: str
    ontology_scope: str
    ontology_source: str
    ontology_fingerprint: str
    object_api_names: tuple[str, ...]
    action_api_names: tuple[str, ...]
    link_api_names: tuple[str, ...]


class OsdkDriftReport(TypedDict):
    manifest: OsdkPackageManifest
    expected_ontology_fingerprint: str
    actual_ontology_fingerprint: str | None
    is_fingerprint_matched: bool | None
    generated_object_api_names: tuple[str, ...]
    live_object_api_names: tuple[str, ...]
    dynamic_only_object_api_names: tuple[str, ...]
    static_only_object_api_names: tuple[str, ...]
    generated_action_api_names: tuple[str, ...]
    live_action_api_names: tuple[str, ...]
    dynamic_only_action_api_names: tuple[str, ...]
    static_only_action_api_names: tuple[str, ...]
    generated_link_api_names: tuple[str, ...]
    live_link_api_names: tuple[str, ...]
    dynamic_only_link_api_names: tuple[str, ...]
    static_only_link_api_names: tuple[str, ...]
    requires_sdk_regeneration: bool
    reason_codes: tuple[str, ...]
    regeneration_hint: str


class _FingerprintState(TypedDict):
    expected: str
    actual: str | None
    matched: bool | None


class _DriftNameSets(TypedDict):
    generated_objects: tuple[str, ...]
    live_objects: tuple[str, ...]
    dynamic_objects: tuple[str, ...]
    static_objects: tuple[str, ...]
    generated_actions: tuple[str, ...]
    live_actions: tuple[str, ...]
    dynamic_actions: tuple[str, ...]
    static_actions: tuple[str, ...]
    generated_links: tuple[str, ...]
    live_links: tuple[str, ...]
    dynamic_links: tuple[str, ...]
    static_links: tuple[str, ...]


OSDK_PACKAGE_MANIFEST: OsdkPackageManifest = {
    "package_name": "foundry-lite",
    "package_version": "0.1.0",
    "generator_name": "foundry-lite-python-osdk-mirror",
    "generator_version": "0.1.0",
    "ontology_scope": "supply-chain-demo",
    "ontology_source": "examples/supply-chain-demo/ontology/order-customer.yaml",
    "ontology_fingerprint": "13b1f74e129614c3",
    "object_api_names": ("Order", "Customer"),
    "action_api_names": ("ApproveOrder",),
    "link_api_names": ("OrderCustomer",),
}


def sdk_package_manifest() -> OsdkPackageManifest:
    return OSDK_PACKAGE_MANIFEST


def sdk_ontology_drift_report(catalog: Mapping[str, object] | None = None) -> OsdkDriftReport:
    fingerprint = _fingerprint_state(catalog)
    names = _drift_name_sets(catalog)
    reason_codes = _reason_codes_from_names(fingerprint["matched"], names)
    has_catalog = catalog is not None
    requires_regeneration = has_catalog and len(reason_codes) > 0
    return _drift_report(fingerprint, names, reason_codes, has_catalog, requires_regeneration)


def _fingerprint_state(catalog: Mapping[str, object] | None) -> _FingerprintState:
    expected = OSDK_PACKAGE_MANIFEST["ontology_fingerprint"]
    actual = _catalog_fingerprint(catalog)
    return {"expected": expected, "actual": actual, "matched": None if actual is None else actual == expected}


def _drift_name_sets(catalog: Mapping[str, object] | None) -> _DriftNameSets:
    live_objects = _catalog_object_api_names(catalog)
    live_actions = _catalog_action_api_names(catalog)
    live_links = _catalog_link_api_names(catalog)
    generated_objects = OSDK_PACKAGE_MANIFEST["object_api_names"]
    generated_actions = OSDK_PACKAGE_MANIFEST["action_api_names"]
    generated_links = OSDK_PACKAGE_MANIFEST["link_api_names"]
    return {
        "generated_objects": generated_objects,
        "live_objects": live_objects,
        "dynamic_objects": _difference(live_objects, generated_objects),
        "static_objects": _difference(generated_objects, live_objects),
        "generated_actions": generated_actions,
        "live_actions": live_actions,
        "dynamic_actions": _difference(live_actions, generated_actions),
        "static_actions": _difference(generated_actions, live_actions),
        "generated_links": generated_links,
        "live_links": live_links,
        "dynamic_links": _difference(live_links, generated_links),
        "static_links": _difference(generated_links, live_links),
    }


def _drift_report(
    fingerprint: _FingerprintState,
    names: _DriftNameSets,
    reason_codes: tuple[str, ...],
    has_catalog: bool,
    requires_regeneration: bool,
) -> OsdkDriftReport:
    return {
        "manifest": OSDK_PACKAGE_MANIFEST,
        "expected_ontology_fingerprint": fingerprint["expected"],
        "actual_ontology_fingerprint": fingerprint["actual"],
        "is_fingerprint_matched": fingerprint["matched"],
        "generated_object_api_names": names["generated_objects"],
        "live_object_api_names": names["live_objects"],
        "dynamic_only_object_api_names": names["dynamic_objects"],
        "static_only_object_api_names": names["static_objects"],
        "generated_action_api_names": names["generated_actions"],
        "live_action_api_names": names["live_actions"],
        "dynamic_only_action_api_names": names["dynamic_actions"],
        "static_only_action_api_names": names["static_actions"],
        "generated_link_api_names": names["generated_links"],
        "live_link_api_names": names["live_links"],
        "dynamic_only_link_api_names": names["dynamic_links"],
        "static_only_link_api_names": names["static_links"],
        "requires_sdk_regeneration": requires_regeneration,
        "reason_codes": reason_codes,
        "regeneration_hint": _regeneration_hint(has_catalog, requires_regeneration),
    }


def assert_foundry_lite_sdk_fresh(catalog: Mapping[str, object] | None = None) -> OsdkDriftReport:
    report = sdk_ontology_drift_report(catalog)
    if report["requires_sdk_regeneration"]:
        raise ValidationFailed(
            "Generated Python OSDK mirror is stale for the provided ontology catalog",
            details={"report": report},
        )
    return report


def _catalog_fingerprint(catalog: Mapping[str, object] | None) -> str | None:
    if catalog is None:
        return None
    for key in ("ontologyContractFingerprint", "contractFingerprint", "fingerprint"):
        value = catalog.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _catalog_object_api_names(catalog: Mapping[str, object] | None) -> tuple[str, ...]:
    return _sorted_names(_api_names(_mapping_items(catalog, "objectTypes", "object_types")))


def _catalog_action_api_names(catalog: Mapping[str, object] | None) -> tuple[str, ...]:
    action_names = list(_api_names(_mapping_items(catalog, "actionTypes", "action_types")))
    for object_type in _mapping_items(catalog, "objectTypes", "object_types"):
        action_names.extend(_api_names(_mapping_items(object_type, "actions")))
    return _sorted_names(action_names)


def _catalog_link_api_names(catalog: Mapping[str, object] | None) -> tuple[str, ...]:
    return _sorted_names(_api_names(_mapping_items(catalog, "linkTypes", "link_types")))


def _mapping_items(source: Mapping[str, object] | None, *keys: str) -> tuple[Mapping[str, object], ...]:
    if source is None:
        return ()
    for key in keys:
        value = source.get(key)
        if isinstance(value, Sequence) and not isinstance(value, str | bytes):
            return tuple(item for item in value if isinstance(item, Mapping))
    return ()


def _api_names(items: Sequence[Mapping[str, object]]) -> tuple[str, ...]:
    names: list[str] = []
    for item in items:
        value = item.get("apiName")
        if isinstance(value, str):
            names.append(value)
    return tuple(names)


def _sorted_names(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values)))


def _difference(left: Sequence[str], right: Sequence[str]) -> tuple[str, ...]:
    right_names = set(right)
    return tuple(name for name in left if name not in right_names)


def _reason_codes_from_names(
    is_fingerprint_matched: bool | None,
    names: _DriftNameSets,
) -> tuple[str, ...]:
    return _reason_codes(
        is_fingerprint_matched=is_fingerprint_matched,
        dynamic_objects=names["dynamic_objects"],
        static_objects=names["static_objects"],
        dynamic_actions=names["dynamic_actions"],
        static_actions=names["static_actions"],
        dynamic_links=names["dynamic_links"],
        static_links=names["static_links"],
    )


def _reason_codes(
    *,
    is_fingerprint_matched: bool | None,
    dynamic_objects: Sequence[str],
    static_objects: Sequence[str],
    dynamic_actions: Sequence[str],
    static_actions: Sequence[str],
    dynamic_links: Sequence[str],
    static_links: Sequence[str],
) -> tuple[str, ...]:
    reasons: list[str] = []
    if is_fingerprint_matched is False:
        reasons.append("ONTOLOGY_FINGERPRINT_MISMATCH")
    if dynamic_objects:
        reasons.append("DYNAMIC_OBJECT_TYPES")
    if static_objects:
        reasons.append("STATIC_OBJECT_TYPES")
    if dynamic_actions:
        reasons.append("DYNAMIC_ACTION_TYPES")
    if static_actions:
        reasons.append("STATIC_ACTION_TYPES")
    if dynamic_links:
        reasons.append("DYNAMIC_LINK_TYPES")
    if static_links:
        reasons.append("STATIC_LINK_TYPES")
    return tuple(reasons)


def _regeneration_hint(has_catalog: bool, requires_regeneration: bool) -> str:
    if requires_regeneration:
        return "Regenerate the Foundry-lite Python OSDK mirror from the active Ontology before using these resources."
    if has_catalog:
        return "Generated Python OSDK mirror matches the provided ontology catalog."
    return "No live ontology catalog was provided; compare against foundry.ontology.catalog() before release."
