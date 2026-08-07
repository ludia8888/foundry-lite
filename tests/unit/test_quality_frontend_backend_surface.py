from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.quality import check_frontend_backend_surface as gate


def test_frontend_backend_surface_gate_passes_current_repo() -> None:
    assert gate.collect_findings() == []


def test_frontend_backend_surface_requires_every_api_route_classified(tmp_path: Path) -> None:
    _write_surface_tree(tmp_path)
    matrix_path = tmp_path / "docs" / "frontend-api-sdk-surface-matrix.json"
    matrix = _load_json(matrix_path)
    matrix["surfaces"] = []
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

    findings = _collect(tmp_path)

    assert any(finding.code == "api_route_missing_frontend_surface_classification" for finding in findings)


def test_frontend_backend_surface_requires_named_sdk_method(tmp_path: Path) -> None:
    _write_surface_tree(tmp_path)
    matrix_path = tmp_path / "docs" / "frontend-api-sdk-surface-matrix.json"
    matrix = _load_json(matrix_path)
    matrix["surfaces"][0]["sdkSurface"] = "datasets.missing"
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

    findings = _collect(tmp_path)

    assert any(finding.code == "frontend_surface_missing_generated_sdk_method" for finding in findings)


def test_frontend_backend_surface_rejects_raw_web_api_request(tmp_path: Path) -> None:
    _write_surface_tree(tmp_path)
    raw_call_path = tmp_path / "apps" / "foundry" / "src" / "RawCall.tsx"
    raw_call_path.write_text('const rows = await fetch("/api/object-sets");\n', encoding="utf-8")

    findings = _collect(tmp_path)

    assert any(finding.code == "web_uses_raw_api_request" for finding in findings)


def test_frontend_backend_surface_requires_collectable_proof_test(tmp_path: Path) -> None:
    _write_surface_tree(tmp_path)
    matrix_path = tmp_path / "docs" / "frontend-api-sdk-surface-matrix.json"
    matrix = _load_json(matrix_path)
    matrix["surfaces"][0]["proofTests"] = ["test_missing_surface_proof"]
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

    findings = _collect(tmp_path)

    assert any(finding.code == "frontend_surface_proof_test_not_collectable" for finding in findings)


def test_frontend_backend_surface_requires_sdk_request_proof_class(tmp_path: Path) -> None:
    _write_surface_tree(tmp_path)
    matrix_path = tmp_path / "docs" / "frontend-api-sdk-surface-matrix.json"
    matrix = _load_json(matrix_path)
    matrix["surfaces"][0].pop("proofClass")
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

    findings = _collect(tmp_path)

    assert any(finding.code == "frontend_surface_missing_sdk_request_proof_class" for finding in findings)


def test_frontend_backend_surface_requires_sdk_request_contract_test(tmp_path: Path) -> None:
    _write_surface_tree(tmp_path)
    matrix_path = tmp_path / "docs" / "frontend-api-sdk-surface-matrix.json"
    matrix = _load_json(matrix_path)
    matrix["surfaces"][0]["proofTests"] = ["test_surface_proof"]
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

    findings = _collect(tmp_path)

    assert any(finding.code == "frontend_surface_missing_sdk_request_contract_test" for finding in findings)


def test_frontend_backend_surface_requires_idempotency_marker_for_header_route(tmp_path: Path) -> None:
    _write_surface_tree(tmp_path)
    _write_idempotency_route(tmp_path)
    matrix_path = tmp_path / "docs" / "frontend-api-sdk-surface-matrix.json"
    matrix = _load_json(matrix_path)
    matrix["surfaces"][0]["route"] = "POST /api/datasets/{namespace}/{name}/preview"
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

    findings = _collect(tmp_path)

    assert any(finding.code == "frontend_surface_missing_idempotency_requirement" for finding in findings)


def test_frontend_backend_surface_rejects_stale_idempotency_marker(tmp_path: Path) -> None:
    _write_surface_tree(tmp_path)
    matrix_path = tmp_path / "docs" / "frontend-api-sdk-surface-matrix.json"
    matrix = _load_json(matrix_path)
    matrix["surfaces"][0]["requiresIdempotencyKey"] = True
    matrix["surfaces"][0]["operatorEvidence"] = "Requires Idempotency-Key."
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

    findings = _collect(tmp_path)

    assert any(finding.code == "frontend_surface_stale_idempotency_requirement" for finding in findings)


def test_frontend_backend_surface_requires_idempotency_operator_evidence(tmp_path: Path) -> None:
    _write_surface_tree(tmp_path)
    _write_idempotency_route(tmp_path)
    matrix_path = tmp_path / "docs" / "frontend-api-sdk-surface-matrix.json"
    matrix = _load_json(matrix_path)
    matrix["surfaces"][0]["route"] = "POST /api/datasets/{namespace}/{name}/preview"
    matrix["surfaces"][0]["requiresIdempotencyKey"] = True
    matrix["surfaces"][0]["operatorEvidence"] = "request_id only"
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

    findings = _collect(tmp_path)

    assert any(finding.code == "frontend_surface_idempotency_evidence_missing" for finding in findings)


def test_frontend_backend_surface_rejects_stale_documented_surface_count(tmp_path: Path) -> None:
    _write_surface_tree(tmp_path)
    (tmp_path / "README.md").write_text(
        "The browser SDK request contract covers 42 frontend route surface rows.\n",
        encoding="utf-8",
    )

    findings = _collect(tmp_path)

    assert any(finding.code == "frontend_surface_count_claim_mismatch" for finding in findings)


def test_frontend_backend_surface_rejects_stale_documented_helper_count(tmp_path: Path) -> None:
    _write_surface_tree(tmp_path)
    (tmp_path / "README.md").write_text(
        "The frontend foundation exposes 12 SDK helper rows.\n",
        encoding="utf-8",
    )

    findings = _collect(tmp_path)

    assert any(finding.code == "sdk_helper_count_claim_mismatch" for finding in findings)


def test_frontend_backend_surface_requires_recipe_fragments(tmp_path: Path) -> None:
    _write_surface_tree(tmp_path)
    (tmp_path / "docs" / "frontend-backend-surface-contract.md").write_text(
        "## Frontend SDK Recipes\n### Session-aware client\nuseFoundryLiteSessionClient\n",
        encoding="utf-8",
    )

    findings = _collect(tmp_path)

    assert any(finding.code == "frontend_recipe_missing_fragment" for finding in findings)


def test_frontend_backend_surface_requires_frontend_cookbook(tmp_path: Path) -> None:
    _write_surface_tree(tmp_path)
    (tmp_path / "docs" / "sdk-frontend-cookbook.md").unlink()

    findings = _collect(tmp_path)

    assert any(finding.code == "frontend_cookbook_missing" for finding in findings)


def test_frontend_backend_surface_requires_frontend_cookbook_fragments(tmp_path: Path) -> None:
    _write_surface_tree(tmp_path)
    (tmp_path / "docs" / "sdk-frontend-cookbook.md").write_text(
        "# Foundry-lite SDK Frontend Cookbook\n",
        encoding="utf-8",
    )

    findings = _collect(tmp_path)

    assert any(finding.code == "frontend_cookbook_missing_fragment" for finding in findings)


def test_frontend_backend_surface_requires_typechecked_screen_recipe_source(tmp_path: Path) -> None:
    _write_surface_tree(tmp_path)
    (tmp_path / "packages" / "sdk-ts" / "src" / "screen-recipes.ts").unlink()

    findings = _collect(tmp_path)

    assert any(finding.code == "frontend_recipe_source_missing" for finding in findings)


def test_frontend_backend_surface_rejects_raw_api_in_screen_recipe_source(tmp_path: Path) -> None:
    _write_surface_tree(tmp_path)
    (tmp_path / "packages" / "sdk-ts" / "src" / "screen-recipes.ts").write_text(
        "export function createDatasetExplorerRecipe() { return fetch('/api/datasets'); }\n",
        encoding="utf-8",
    )

    findings = _collect(tmp_path)

    assert any(finding.code == "frontend_recipe_source_uses_raw_api" for finding in findings)


def test_frontend_backend_surface_requires_screen_recipe_package_export(tmp_path: Path) -> None:
    _write_surface_tree(tmp_path)
    package_path = tmp_path / "packages" / "sdk-ts" / "package.json"
    package_json = _load_json(package_path)
    package_json["exports"].pop("./screen-recipes")
    package_path.write_text(json.dumps(package_json), encoding="utf-8")

    findings = _collect(tmp_path)

    assert any(finding.code == "frontend_recipe_package_export_missing" for finding in findings)


def test_frontend_backend_surface_requires_helper_matrix_row(tmp_path: Path) -> None:
    _write_surface_tree(tmp_path)
    matrix_path = tmp_path / "docs" / "frontend-api-sdk-surface-matrix.json"
    matrix = _load_json(matrix_path)
    matrix["sdkHelpers"] = []
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

    findings = _collect(tmp_path)

    assert any(finding.code == "sdk_helper_missing_matrix_row" for finding in findings)


def test_frontend_backend_surface_requires_helper_export(tmp_path: Path) -> None:
    _write_surface_tree(tmp_path)
    sdk_path = tmp_path / "packages" / "sdk-ts" / "src" / "generated.ts"
    sdk_path.write_text(
        'export const SDK_CLIENT_SURFACE = {"datasets":["preview"],"helpers":["retryWithBackoff"]};\n',
        encoding="utf-8",
    )

    findings = _collect(tmp_path)

    assert any(finding.code == "sdk_helper_missing_export" for finding in findings)


def test_frontend_backend_surface_accepts_async_generator_helper_export(tmp_path: Path) -> None:
    _write_surface_tree(tmp_path)
    sdk_path = tmp_path / "packages" / "sdk-ts" / "src" / "generated.ts"
    sdk_path.write_text(
        'export const SDK_CLIENT_SURFACE = {"datasets":["preview"],"helpers":["retryWithBackoff"]};\n'
        "export async function* retryWithBackoff() {}\n",
        encoding="utf-8",
    )

    findings = _collect(tmp_path)

    assert not any(finding.code == "sdk_helper_missing_export" for finding in findings)


def test_frontend_backend_surface_requires_helper_contract_test(tmp_path: Path) -> None:
    _write_surface_tree(tmp_path)
    matrix_path = tmp_path / "docs" / "frontend-api-sdk-surface-matrix.json"
    matrix = _load_json(matrix_path)
    matrix["sdkHelpers"][0]["proofTests"] = ["test_surface_proof"]
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

    findings = _collect(tmp_path)

    assert any(finding.code == "sdk_helper_missing_request_contract_test" for finding in findings)


def test_frontend_backend_surface_accepts_nested_sdk_surface_metadata() -> None:
    surface = {
        "datasets": {"_self": ["preview"], "qualityChecks": ["list", "create"]},
        "sources": {
            "_self": ["list", "get"],
            "cdc": {"debezium": ["create", "startSync"]},
        },
    }

    assert gate._sdk_surface_exists(surface, "datasets.preview")
    assert gate._sdk_surface_exists(surface, "datasets.qualityChecks.list")
    assert gate._sdk_surface_exists(surface, "sources.list")
    assert gate._sdk_surface_exists(surface, "sources.cdc.debezium.startSync")
    assert not gate._sdk_surface_exists(surface, "datasets.qualityChecks.delete")
    assert not gate._sdk_surface_exists(surface, "sources.cdc.debezium.pause")


def _collect(root: Path) -> list[gate.FrontendBackendSurfaceFinding]:
    return gate.collect_findings(
        root,
        api_path=root / "apps" / "api" / "foundry_lite_api" / "main.py",
        matrix_path=root / "docs" / "frontend-api-sdk-surface-matrix.json",
        sdk_path=root / "packages" / "sdk-ts" / "src" / "generated.ts",
        web_path=root / "apps" / "foundry" / "src",
        tests_root=root / "tests",
        frontend_cookbook_path=root / "docs" / "sdk-frontend-cookbook.md",
        screen_recipe_path=root / "packages" / "sdk-ts" / "src" / "screen-recipes.ts",
        sdk_package_path=root / "packages" / "sdk-ts" / "package.json",
    )


def _write_surface_tree(root: Path) -> None:
    (root / "apps" / "api" / "foundry_lite_api").mkdir(parents=True)
    (root / "apps" / "foundry" / "src").mkdir(parents=True)
    (root / "docs").mkdir(parents=True)
    (root / "packages" / "sdk-ts" / "src").mkdir(parents=True)
    (root / "tests" / "unit").mkdir(parents=True)
    (root / "apps" / "api" / "foundry_lite_api" / "main.py").write_text(
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        "@app.get('/api/datasets/{namespace}/{name}/preview')\n"
        "def preview_dataset():\n"
        "    return []\n",
        encoding="utf-8",
    )
    (root / "packages" / "sdk-ts" / "src" / "generated.ts").write_text(
        'export const SDK_CLIENT_SURFACE = {"datasets":["preview"],"helpers":["retryWithBackoff"]};\n'
        "export async function retryWithBackoff() {}\n",
        encoding="utf-8",
    )
    (root / "packages" / "sdk-ts" / "src" / "screen-recipes.ts").write_text(
        "\n".join(
            [
                "export function createFoundryLiteScreenRecipes() {",
                "  operatorWorkspace;",
                "  datasetExplorer;",
                "  objectActionWorkspace;",
                "  adminOperations;",
                "}",
                "export async function loadFoundryLiteScreenRecipes() {}",
                "export function createOperatorWorkspaceRecipe() {",
                "  operatorWorkspaceHomeView;",
                "  operatorWorkspaceNavigation;",
                "  quickActions;",
                "  attentionItems;",
                "  loadHome;",
                "  client.datasets.list;",
                "  client.operations.runs.list;",
                "  adminOperationsLaunchpad;",
                "  recoveryOperations.loadRecoveryOverview;",
                "}",
                "export function createDatasetExplorerRecipe() {",
                "  client.datasets.inspect;",
                "  client.operations.lineage.get;",
                "}",
                "export function createObjectActionWorkspaceRecipe() {",
                "  createFoundryLiteOsdkClient;",
                "  createFoundryLiteOntologyIndex;",
                "  getObjectType;",
                "  getActionType;",
                "}",
                "export function createMediaWorkspaceRecipe() { uploadAndCommit; osdk.media; }",
                "export function createAipWorkspaceRecipe() {",
                "  client.aip.agent.run;",
                "  client.operations.runs.detail;",
                "  runAgentAndLoadOperationsDetail;",
                "}",
                "export function createInsightReviewWorkspaceRecipe() {",
                "  insightReviewQueueView;",
                "  client.insights.reviews.list;",
                "  client.insights.reviews.decide;",
                "}",
                "export function createPipelineBuilderRecipe() {",
                "  client.transforms.registerSql;",
                "  client.transforms.run;",
                "}",
                "export function createLongRunningOperationRecipe() {",
                "  pollFoundryLiteOperation;",
                "  streamFoundryLiteOperationEvents;",
                "}",
                "export function createOperationsEvidenceRecipe() {",
                "  loadRunInvestigation;",
                "  client.operations.runs.list;",
                "  client.operations.runs.detail;",
                "  client.operations.runs.promptArtifact;",
                "}",
                "export function createRecordDlqOperationsRecipe() {",
                "  client.operations.deadLetterRecords.list;",
                "  client.operations.deadLetterRecords.retry;",
                "  client.operations.deadLetterRecords.bulkRetry;",
                "  client.operations.deadLetterRecords.discard;",
                "}",
                "export function createWritebackReconciliationRecipe() {",
                "  client.operations.reconciliation.list;",
                "  client.operations.reconciliation.resolve;",
                "}",
                "export function createMaintenanceOperationsRecipe() {",
                "  client.operations.observability.detect;",
                "  client.operations.icebergMaintenance.planReadOnly;",
                "  client.operations.index.replayObjectType;",
                "  client.operations.transforms.retry;",
                "}",
                "export function createAdminOperationsRecipe() {",
                "  adminOperationsLaunchModel;",
                "  adminOperationsLaunchpad;",
                "  adminCommandCenter;",
                "  adminInternalOperationsWorkbench;",
                "  loadLaunchModel;",
                "  loadLaunchpad;",
                "  loadInternalOperationsWorkbench;",
                "  migrationCommands;",
                "  operatorCommandCards;",
                "  privilegedCommandCards;",
                "  loadOperatorCommandCards;",
                "  canCopyCommand;",
                "  copyLabel;",
                "  visibleCommandCards;",
                "  selectedAreaId;",
                "  readiness;",
                "  visibleSections;",
                "  riskLevel;",
                "  adminOperationsBoard;",
                "  client.operations.admin.taskPlan;",
                "}",
                "export function createRecoveryOperationsRecipe() {",
                "  client.operations.backupRestore.recoveryOverview;",
                "  client.operations.backupRestore.restoreArtifact;",
                "  client.operations.backupRestore.executeArtifactRestore;",
                "  client.operations.backupRestore.approveResume;",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    _write_frontend_cookbook(root)
    (root / "packages" / "sdk-ts" / "package.json").write_text(
        json.dumps(
            {
                "name": "@foundry-lite/sdk",
                "exports": {
                    ".": "./src/client.ts",
                    "./generated": "./src/generated.ts",
                    "./react": "./src/react.ts",
                    "./screen-recipes": "./src/screen-recipes.ts",
                },
            }
        ),
        encoding="utf-8",
    )
    (root / "apps" / "foundry" / "src" / "DatasetPreview.tsx").write_text(
        "const rows = client.datasets.preview('clean', 'orders');\n",
        encoding="utf-8",
    )
    (root / "tests" / "unit" / "test_surface_proof.py").write_text(
        "def test_sdk_request_contract_covers_all_frontend_surface_routes():\n    assert True\n"
        "def test_sdk_request_contract_covers_frontend_foundation_helpers():\n    assert True\n"
        "def test_surface_proof():\n    assert True\n",
        encoding="utf-8",
    )
    (root / "docs" / "frontend-api-sdk-surface-matrix.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "surfaces": [
                    {
                        "route": "GET /api/datasets/{namespace}/{name}/preview",
                        "sdkSurface": "datasets.preview",
                        "rawRequestPolicy": "named-sdk-only",
                        "operatorEvidence": "request_id",
                        "proofClass": "sdk-request-contract",
                        "proofTests": ["test_sdk_request_contract_covers_all_frontend_surface_routes"],
                    }
                ],
                "sdkHelpers": [
                    {
                        "id": "helpers.retryWithBackoff",
                        "sdkHelper": "retryWithBackoff",
                        "operatorEvidence": "retryable errors keep request_id metadata.",
                        "proofClass": "sdk-helper-contract",
                        "proofTests": ["test_sdk_request_contract_covers_frontend_foundation_helpers"],
                    }
                ],
                "nonFrontendRoutes": [],
            }
        ),
        encoding="utf-8",
    )
    _write_frontend_recipe_contract(root)


def _write_frontend_cookbook(root: Path) -> None:
    (root / "docs" / "sdk-frontend-cookbook.md").write_text(
        "\n".join(
            [
                "# Foundry-lite SDK Frontend Cookbook",
                "loadFoundryLiteScreenRecipes",
                "createOperatorWorkspaceRecipe",
                "@foundry-lite/sdk/screen-recipes",
                "FoundryLiteProvider",
                "createSessionTokenProvider",
                "useFoundryLiteClient",
                "useFoundryLiteSessionClient",
                "useFoundryLiteSessionStatus",
                "useFoundryLiteScreenStatus",
                "useFoundryLiteOsdkClient",
                "useFoundryLiteProvidedScreenRecipes",
                "useFoundryLiteScreenRecipes",
                "useFoundryLiteProvidedOperatorAppShell",
                "useFoundryLiteProvidedOperatorWorkspaceHome",
                "needsAuthentication",
                "isPermissionDenied",
                "canRetryLastRequest",
                "shouldShowSkeleton",
                "shouldRenderContent",
                "createOperatorWorkspaceRecipe",
                "createDatasetExplorerRecipe",
                "useFoundryLiteProvidedDatasetExplorer",
                "createObjectActionWorkspaceRecipe",
                "useFoundryLiteGenericObjectQuery",
                "useFoundryLiteObjectActionWorkspace",
                "useFoundryLiteProvidedObjectActionWorkspace",
                "objectQuery",
                "selectedObject",
                "recommendedObject",
                "actionForm",
                "foundryLiteActionFormView",
                "useFoundryLiteActionForm",
                "useFoundryLiteProvidedActionForm",
                "parameterFields",
                "missingFields",
                "canSubmitAction",
                "setParam",
                "submit()",
                "selectedActionForm",
                "useFoundryLiteActionProposalSubmit",
                "useFoundryLiteProvidedOntologyExplorer",
                "useFoundryLiteOntologyWorkspaceShell",
                "createMediaWorkspaceRecipe",
                "useFoundryLiteProvidedMediaPipeline",
                "createAipWorkspaceRecipe",
                "useFoundryLiteProvidedAipAgentRunWithOperationsDetail",
                "createInsightReviewWorkspaceRecipe",
                "createPipelineBuilderRecipe",
                "operatorWorkspace.loadHome",
                "operatorWorkspaceNavigation(home).navItems",
                "operatorHome.navigation.navItems",
                "operatorHome.navigation.quickActions",
                "useFoundryLiteProvidedOperatorAppShell",
                "canRenderWorkspace",
                "shouldShowSignIn",
                "shouldShowPermissionDenied",
                "reloadWorkspace",
                "createLongRunningOperationRecipe",
                "streamOperationEvents",
                "useFoundryLiteProvidedOperationEventStream",
                "createOperationsEvidenceRecipe",
                "useFoundryLiteOperationsInvestigation",
                "useFoundryLitePromptArtifact",
                "createRecordDlqOperationsRecipe",
                "useFoundryLiteRecordDlqQueue",
                "createWritebackReconciliationRecipe",
                "useFoundryLiteWritebackReconciliationQueue",
                "createMaintenanceOperationsRecipe",
                "useFoundryLiteObservabilityDetect",
                "useFoundryLiteIcebergMaintenancePlan",
                "createAdminOperationsRecipe",
                "useFoundryLiteProvidedAdminLaunchModel",
                "useFoundryLiteProvidedAdminLaunchpad",
                "useFoundryLiteProvidedAdminCommandCenter",
                "useFoundryLiteProvidedAdminInternalOperationsWorkbench",
                "useFoundryLiteProvidedOperatorWorkspaceShell",
                "loadLaunchModel",
                "loadLaunchpad",
                "loadInternalOperationsWorkbench",
                "operatorWorkspace.loadShell",
                "operatorWorkspaceShell",
                "migrationCommands",
                "operatorCommandCards",
                "privilegedCommandCards",
                "canCopyCommand",
                "visibleCommandCards",
                "selectedAreaId",
                "readiness",
                "visibleSections",
                "areaSurfaces",
                "selectedSurface",
                "primaryQuickAction",
                "createRecoveryOperationsRecipe",
                "What is current",
                "What remains future",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_idempotency_route(root: Path) -> None:
    (root / "apps" / "api" / "foundry_lite_api" / "main.py").write_text(
        "from fastapi import FastAPI, Header\n"
        "app = FastAPI()\n"
        "@app.post('/api/datasets/{namespace}/{name}/preview')\n"
        "def preview_dataset(idempotency_key: str = Header(alias='Idempotency-Key')):\n"
        "    return []\n",
        encoding="utf-8",
    )


def _write_frontend_recipe_contract(root: Path) -> None:
    (root / "docs" / "frontend-backend-surface-contract.md").write_text(
        "\n".join(
            [
                "## Frontend SDK Recipes",
                "### Session-aware client",
                "FoundryLiteProvider",
                "useFoundryLiteClient",
                "useFoundryLiteSessionClient",
                "useFoundryLiteSessionStatus",
                "useFoundryLiteScreenStatus",
                "useFoundryLiteOsdkClient",
                "useFoundryLiteProvidedScreenRecipes",
                "useFoundryLiteProvidedOperatorWorkspaceShell",
                "createSessionTokenProvider",
                "needsAuthentication",
                "isPermissionDenied",
                "canRetryLastRequest",
                "### Dataset explorer screen",
                "useFoundryLiteDatasetExplorer",
                "useFoundryLiteProvidedDatasetExplorer",
                "previewRows",
                "qualitySummary",
                "client.datasets.inspect",
                "client.operations.lineage.get",
                "### Object action workspace screen",
                "useFoundryLiteObjectQuery",
                "useFoundryLiteGenericObjectQuery",
                "useFoundryLiteObjectActionWorkspace",
                "useFoundryLiteProvidedObjectActionWorkspace",
                "objectQuery",
                "selectedObject",
                "recommendedObject",
                "actionForm",
                "useFoundryLiteActionSubmit",
                "foundryLiteActionFormView",
                "useFoundryLiteActionForm",
                "useFoundryLiteProvidedActionForm",
                "parameterFields",
                "missingFields",
                "canSubmitAction",
                "setParam",
                "submit()",
                "foundryLiteActionProposalView",
                "useFoundryLiteActionProposalSubmit",
                "expectedObjectVersion",
                "selectedActionForm",
                "operatorWorkspace.loadHome",
                "operatorWorkspace.loadShell",
                "operatorWorkspaceShell",
                "operatorWorkspaceNavigation(home).navItems",
                "useFoundryLiteProvidedOperatorAppShell",
                "useFoundryLiteProvidedOperatorWorkspaceShell",
                "canRenderWorkspace",
                "shouldShowSignIn",
                "shouldShowPermissionDenied",
                "reloadWorkspace",
                "areaSurfaces",
                "selectedSurface",
                "primaryQuickAction",
                "operatorHome.navigation.navItems",
                "operatorHome.navigation.quickActions",
                "### Large ontology registry lookup",
                "$Ontology.objectApiNames",
                "getActionType",
                "useFoundryLiteOntologyCatalog",
                "useFoundryLiteProvidedOntologyExplorer",
                "useFoundryLiteOntologyWorkspaceShell",
                "useFoundryLiteProvidedOntologyWorkspaceShell",
                "selectedActionPalette",
                "selectedActionPaletteItems",
                "canQuerySelectedObject",
                "### Media upload and search",
                "useFoundryLiteMediaUpload",
                "useFoundryLiteMediaProcessing",
                "useFoundryLiteMediaSearch",
                "useFoundryLiteProvidedMediaPipeline",
                "servingTruthMediaItemVersionId",
                "### AIP agent and builder runs",
                "useFoundryLiteAipAgentRun",
                "useFoundryLiteAipBuilderRun",
                "useFoundryLiteProvidedAipAgentRunWithOperationsDetail",
                "useFoundryLiteProvidedAipBuilderRunWithOperationsDetail",
                "operationsDetail",
                "hasOperationsDetail",
                "### Insight review workspace",
                "createInsightReviewWorkspaceRecipe",
                "useFoundryLiteProvidedInsightReviewQueue",
                "useFoundryLiteInsightReviewDecision",
                "useFoundryLiteActionProposalSubmit",
                "### SQL pipeline builder",
                "useFoundryLiteSqlTransformSubmit",
                "useFoundryLiteProvidedSqlTransformSubmit",
                "foundryLiteSqlTransformSubmitView",
                "client.transforms.run",
                "outputDatasetVersionId",
                "outputDatasetRowCount",
                "outputManifestUri",
                "### Long-running operation",
                "useFoundryLiteLongRunningJob",
                "useFoundryLiteLiveOperationTimeline",
                "useFoundryLiteProvidedLiveOperationTimeline",
                "pollFoundryLiteOperation",
                "useFoundryLiteProvidedOperationEventStream",
                "timelineItems",
                "### Operations evidence screen",
                "useFoundryLiteOperationsInvestigation",
                "useFoundryLiteOperationsRunList",
                "useFoundryLiteOperationsRunDetail",
                "useFoundryLitePromptArtifact",
                "client.operations.runs.promptArtifact",
                "### Record DLQ and reconciliation workbench",
                "useFoundryLiteRecordDlqQueue",
                "useFoundryLiteRecordDlqControls",
                "useFoundryLiteWritebackReconciliationQueue",
                "useFoundryLiteWritebackResolve",
                "createRecordDlqOperationsRecipe",
                "createWritebackReconciliationRecipe",
                "### Maintenance and observability workbench",
                "createMaintenanceOperationsRecipe",
                "useFoundryLiteObservabilityDetect",
                "useFoundryLiteIcebergMaintenancePlan",
                "useFoundryLiteMaintenanceControls",
                "planIcebergMaintenanceReadOnly",
                "client.operations.icebergMaintenance.run",
                "### Admin readiness screen",
                "useFoundryLiteAdminConsole",
                "useFoundryLiteAdminTaskPlan",
                "useFoundryLiteProvidedAdminLaunchModel",
                "useFoundryLiteProvidedAdminLaunchpad",
                "useFoundryLiteProvidedAdminCommandCenter",
                "useFoundryLiteProvidedAdminInternalOperationsWorkbench",
                "foundryLiteAdminCommandCenter",
                "adminInternalOperationsWorkbench",
                "operatorCommandActions",
                "workerCommands",
                "bootstrapCommands",
                "operatorCommandCards",
                "privilegedCommandCards",
                "canCopyCommand",
                "visibleCards",
                "visibleCommandCards",
                "selectedCard",
                "selectedAreaId",
                "readiness",
                "sectionCounts",
                "visibleSections",
                "### Recovery and bounded operations controls",
                "useFoundryLiteRecoveryOverview",
                "useFoundryLiteOutboxPublish",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload
