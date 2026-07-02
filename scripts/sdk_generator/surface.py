"""SDK client surface derivation, JSON renders, and contract fingerprint."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from hashlib import sha256

from scripts.sdk_generator.ontology import ActionDef, LinkDef, ObjectDef, OntologyDef
from scripts.sdk_generator.paths import (
    DEFAULT_ONTOLOGY,
    SDK_GENERATOR_NAME,
    SDK_ONTOLOGY_SCOPE,
    SDK_PACKAGE_JSON,
    _display_path,
)


def _sdk_package_version() -> str:
    package = json.loads(SDK_PACKAGE_JSON.read_text(encoding="utf-8"))
    version = package.get("version")
    if not isinstance(version, str) or not version:
        raise ValueError("packages/sdk-ts/package.json must declare a non-empty version")
    return version


@dataclass(frozen=True)
class ObjectClientSurface:
    api_name: str
    response_type: str
    methods: tuple[str, ...]


@dataclass(frozen=True)
class ActionClientSurface:
    api_name: str
    target: str
    payload_type: str
    methods: tuple[str, ...]


@dataclass(frozen=True)
class OperationClientSurface:
    api_name: str
    methods: tuple[str, ...]


@dataclass(frozen=True)
class SdkClientSurface:
    system: tuple[str, ...]
    datasets: tuple[str, ...]
    sources: tuple[OperationClientSurface, ...]
    connectors: tuple[OperationClientSurface, ...]
    auth: tuple[OperationClientSurface, ...]
    developer_console: tuple[OperationClientSurface, ...]
    ontology: tuple[str, ...]
    insights: tuple[OperationClientSurface, ...]
    media: tuple[OperationClientSurface, ...]
    objects: tuple[ObjectClientSurface, ...]
    actions: tuple[ActionClientSurface, ...]
    object_sets: tuple[str, ...]
    materializations: tuple[str, ...]
    aip: tuple[OperationClientSurface, ...]
    transforms: tuple[str, ...]
    operations: tuple[OperationClientSurface, ...]
    helpers: tuple[str, ...]


def client_surface(ontology: OntologyDef) -> SdkClientSurface:
    return SdkClientSurface(
        system=("health",),
        datasets=("list", "versions", "preview", "inspect"),
        sources=(
            OperationClientSurface("_self", ("list", "get")),
            OperationClientSurface("templates", ("list",)),
            OperationClientSurface("credentials", ("create", "list", "get")),
            OperationClientSurface("agents", ("register", "list", "heartbeat")),
            OperationClientSurface("networkPolicies", ("create", "list")),
            OperationClientSurface("exploration", ("run",)),
            OperationClientSurface("managedSyncs", ("create", "list", "get", "startRun", "listRuns", "getRun")),
            OperationClientSurface("scheduler", ("previewDue", "tick")),
            OperationClientSurface("csv", ("upload",)),
            OperationClientSurface("batchFiles", ("upload",)),
            OperationClientSurface("webhookListeners", ("create", "get")),
            OperationClientSurface("cdc.debezium", ("create", "startSync")),
            OperationClientSurface("media", ("uploadAndCommit",)),
            OperationClientSurface("rest", ("createConnection", "upsertResource", "test", "startSync")),
        ),
        connectors=(
            OperationClientSurface("connections", ("create", "list", "get", "update")),
            OperationClientSurface("resources", ("upsert", "test", "startSync")),
        ),
        auth=(OperationClientSurface("osdkOAuth", ("authorize", "token", "refresh", "revoke")),),
        developer_console=(
            OperationClientSurface(
                "osdkApplications",
                (
                    "create",
                    "list",
                    "get",
                    "updateResources",
                    "createClient",
                    "listClients",
                    "updateClient",
                    "deactivateClient",
                ),
            ),
            OperationClientSurface(
                "sdkVersions",
                (
                    "create",
                    "list",
                    "get",
                    "artifact",
                    "promote",
                    "channels",
                    "createCompatibilityWindow",
                    "compatibilityWindows",
                    "installMetadata",
                    "downloadToken",
                    "artifactByDownloadToken",
                ),
            ),
        ),
        ontology=("catalog", "validate"),
        insights=(
            OperationClientSurface(
                "reviews",
                ("list", "create", "get", "assign", "decide", "executeApprovedAction"),
            ),
        ),
        media=(
            OperationClientSurface("sets", ("create", "get")),
            OperationClientSurface("transactions", ("open", "upload", "commit")),
            OperationClientSurface("versions", ("process",)),
            OperationClientSurface("derivatives", ("get", "index")),
            OperationClientSurface("content", ("search",)),
            OperationClientSurface("visual", ("indexDerivative", "promoteGeneration", "search")),
            OperationClientSurface("processingRuns", ("list", "get")),
            OperationClientSurface("references", ("bind", "resolve")),
        ),
        objects=tuple(ObjectClientSurface(item.api_name, item.api_name, ("get", "query")) for item in ontology.objects),
        actions=tuple(
            ActionClientSurface(item.api_name, item.target, f"{item.api_name}ApplyRequest", ("apply", "validate"))
            for item in ontology.actions
        ),
        object_sets=("list", "create", "get"),
        materializations=("run",),
        aip=(
            OperationClientSurface("builder", ("validate", "run")),
            OperationClientSurface("agent", ("run",)),
            OperationClientSurface("evals", ("run",)),
            OperationClientSurface("releases", ("promote",)),
        ),
        transforms=("registerSql", "run", "previewDue", "tick"),
        operations=(
            OperationClientSurface("admin", ("overview", "taskPlan")),
            OperationClientSurface(
                "backupRestore",
                (
                    "preflight",
                    "createArtifact",
                    "restoreArtifact",
                    "executeArtifactRestore",
                    "startRestoreMode",
                    "restoreModeStatus",
                    "postRestoreValidation",
                    "approveResume",
                    "recoveryOverview",
                ),
            ),
            OperationClientSurface(
                "deadLetterRecords", ("list", "get", "retry", "retryTransform", "bulkRetry", "discard")
            ),
            OperationClientSurface("deadLetterEvents", ("retry",)),
            OperationClientSurface("icebergMaintenance", ("planReadOnly", "plan", "run")),
            OperationClientSurface("index", ("replayObjectType", "replayFailedRun", "replayOntologyReindex")),
            OperationClientSurface("lineage", ("get",)),
            OperationClientSurface(
                "observability",
                ("detect", "detectAndRecord", "listIncidents", "acknowledgeIncident", "resolveIncident"),
            ),
            OperationClientSurface("outbox", ("publishPending",)),
            OperationClientSurface("reconciliation", ("list", "resolve", "approveRecovery")),
            OperationClientSurface("runs", ("list", "detail", "promptArtifact")),
            OperationClientSurface("transforms", ("retry",)),
            OperationClientSurface("workflows", ("startConnectorSync", "get", "cancel")),
        ),
        helpers=(
            "adminCapabilityView",
            "adminOperationsBoard",
            "adminReadinessScreen",
            "adminTaskPlanScreen",
            "adminTaskView",
            "actionLockKey",
            "classifyFoundryLiteError",
            "collectCursorPages",
            "createInFlightActionLock",
            "createFoundryLiteClient",
            "createFoundryLiteOsdkClient",
            "createFoundryLiteOntologyIndex",
            "createSessionTokenProvider",
            "createRequestId",
            "expectedObjectVersion",
            "getActionType",
            "getObjectType",
            "groupAdminCapabilities",
            "idempotencyKey",
            "isRetryableFoundryLiteError",
            "normalizeFoundryLiteError",
            "pollFoundryLiteOperation",
            "requestContextHeaders",
            "retryWithBackoff",
            "sdkOntologyDriftReport",
            "sdkPackageManifest",
            "assertFoundryLiteSdkFresh",
            "streamFoundryLiteOperationEvents",
        ),
    )


def _operation_surface_payload(items: tuple[OperationClientSurface, ...]) -> dict[str, object]:
    payload: dict[str, object] = {}
    for item in items:
        cursor = payload
        parts = item.api_name.split(".")
        for part in parts[:-1]:
            existing = cursor.get(part)
            if not isinstance(existing, dict):
                existing = {}
                cursor[part] = existing
            cursor = existing
        cursor[parts[-1]] = list(item.methods)
    return payload


def render_client_surface_json(surface: SdkClientSurface) -> str:
    payload: dict[str, object] = {
        "actions": {item.api_name: list(item.methods) for item in surface.actions},
        "auth": {item.api_name: list(item.methods) for item in surface.auth},
        "connectors": {item.api_name: list(item.methods) for item in surface.connectors},
        "sources": _operation_surface_payload(surface.sources),
        "datasets": {
            "_self": list(surface.datasets),
            "qualityContracts": ["list", "create", "get", "activate"],
            "qualityChecks": ["list", "create", "update"],
            "qualityResults": ["list", "summary"],
        },
        "helpers": list(surface.helpers),
        "developerConsole": {item.api_name: list(item.methods) for item in surface.developer_console},
        "insights": {item.api_name: list(item.methods) for item in surface.insights},
        "aip": {item.api_name: list(item.methods) for item in surface.aip},
        "media": {item.api_name: list(item.methods) for item in surface.media},
        "materializations": list(surface.materializations),
        "objectSets": list(surface.object_sets),
        "objects": {
            "generic": ["get", "query", "links", "subscribe"],
            **{item.api_name: list(item.methods) for item in surface.objects},
        },
        "ontology": list(surface.ontology),
        "operations": {item.api_name: list(item.methods) for item in surface.operations},
        "system": list(surface.system),
        "transforms": list(surface.transforms),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def render_sdk_package_manifest_json(ontology: OntologyDef) -> str:
    payload = {
        "actionApiNames": [item.api_name for item in ontology.actions],
        "generatorName": SDK_GENERATOR_NAME,
        "generatorVersion": _sdk_package_version(),
        "linkApiNames": [item.api_name for item in ontology.links],
        "objectApiNames": [item.api_name for item in ontology.objects],
        "ontologyFingerprint": contract_fingerprint(ontology),
        "ontologyScope": SDK_ONTOLOGY_SCOPE,
        "ontologySource": _display_path(DEFAULT_ONTOLOGY),
        "packageName": "@foundry-lite/sdk",
        "packageVersion": _sdk_package_version(),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def contract_fingerprint(ontology: OntologyDef) -> str:
    payload = json.dumps(asdict(ontology), sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()[:16]


def _primary_key_property(object_def: ObjectDef) -> str:
    expected = f"{object_def.api_name}Id".lower()
    for prop in object_def.properties:
        if prop.api_name.lower() == expected:
            return prop.api_name
    for prop in object_def.properties:
        if prop.api_name.lower().endswith("id"):
            return prop.api_name
    return object_def.properties[0].api_name if object_def.properties else "id"


def _link_entries_for_object(links: Sequence[LinkDef], object_api_name: str) -> list[tuple[str, LinkDef, str]]:
    entries: list[tuple[str, LinkDef, str]] = []
    for link_def in links:
        if link_def.from_object == object_api_name:
            entries.append((_object_link_alias(link_def.to_object), link_def, link_def.to_object))
            entries.append((link_def.api_name, link_def, link_def.to_object))
        if link_def.to_object == object_api_name:
            entries.append((_object_link_alias(link_def.from_object), link_def, link_def.from_object))
            entries.append((link_def.api_name, link_def, link_def.from_object))
    return _dedupe_relation_entries(entries)


def _dedupe_relation_entries(entries: Sequence[tuple[str, LinkDef, str]]) -> list[tuple[str, LinkDef, str]]:
    seen: set[str] = set()
    deduped: list[tuple[str, LinkDef, str]] = []
    for entry in entries:
        alias = entry[0]
        if alias in seen:
            continue
        seen.add(alias)
        deduped.append(entry)
    return deduped


def _action_alias_entries(actions: Sequence[ActionDef]) -> list[tuple[str, ActionDef]]:
    entries: list[tuple[str, ActionDef]] = []
    seen: set[str] = set()
    for action_def in actions:
        for alias in (_lower_camel(action_def.api_name), action_def.api_name):
            if alias in seen:
                continue
            seen.add(alias)
            entries.append((alias, action_def))
    return entries


def _object_link_alias(object_api_name: str) -> str:
    return _lower_camel(object_api_name)


def _lower_camel(value: str) -> str:
    if not value:
        return value
    return value[0].lower() + value[1:]
