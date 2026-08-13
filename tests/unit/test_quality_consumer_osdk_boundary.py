from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / "scripts" / "quality" / "check_consumer_osdk_boundary.mjs"


def test_consumer_osdk_boundary_passes_current_strict_application() -> None:
    result = _run_gate(ROOT)

    assert result.returncode == 0, result.stdout + result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["isCompliant"] is True
    assert receipt["applications"][0]["profile"] == "consumer_osdk_strict"
    assert receipt["applications"][0]["violationCount"] == 0
    assert receipt["applications"][0]["exceptionCount"] == 0
    assert receipt["applications"][0]["sourceTreeHash"].startswith("sha256:")
    assert receipt["applications"][0]["sdkArtifactHash"].startswith("sha256:")


def test_consumer_osdk_boundary_rejects_aliased_base_sdk_import(tmp_path: Path) -> None:
    _write_fixture(
        tmp_path,
        'import { useFoundryLiteClient as useDomain } from "@foundry-lite/sdk/react";\n'
        "export const value = useDomain;\n",
    )

    result = _run_gate(tmp_path)

    assert result.returncode == 1
    assert "BASE_SDK_IMPORT_FORBIDDEN" in result.stdout
    assert "LOW_LEVEL_SDK_SYMBOL_FORBIDDEN" in result.stdout


def test_consumer_osdk_boundary_rejects_bracket_generic_chain(tmp_path: Path) -> None:
    _write_fixture(
        tmp_path,
        'import { app } from "@foundry-lite/test-osdk";\n'
        'export const run = (client) => client["functions"]["generic"].execute(app);\n',
    )

    result = _run_gate(tmp_path)

    assert result.returncode == 1
    assert "LOW_LEVEL_SDK_CHAIN_FORBIDDEN" in result.stdout


def test_consumer_osdk_boundary_rejects_strict_profile_exception(tmp_path: Path) -> None:
    _write_fixture(tmp_path, 'import { app } from "@foundry-lite/test-osdk";\nexport { app };\n')
    inventory_path = tmp_path / "config" / "consumer-osdk-apps.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    inventory["applications"][0]["exceptions"] = [{"reason": "temporary bypass"}]
    inventory_path.write_text(json.dumps(inventory), encoding="utf-8")

    result = _run_gate(tmp_path)

    assert result.returncode == 1
    assert "STRICT_EXCEPTION_BUDGET_EXCEEDED" in result.stdout


def test_consumer_osdk_boundary_rejects_foreign_or_prefix_lookalike_application_package(tmp_path: Path) -> None:
    _write_fixture(
        tmp_path,
        'import { useAnything } from "@foundry-lite/test-osdk-evil/react";\n'
        'import { Reservation } from "@foundry-lite/foreign-osdk";\n'
        "export const value = [useAnything, Reservation];\n",
    )

    result = _run_gate(tmp_path)

    assert result.returncode == 1
    assert "FOREIGN_APPLICATION_OSDK_FORBIDDEN" in result.stdout
    assert "APPLICATION_OSDK_IMPORT_REQUIRED" in result.stdout


def _run_gate(workspace_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["node", str(GATE), "--workspace-root", str(workspace_root), "--no-write"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _write_fixture(root: Path, consumer_source: str) -> None:
    source_root = root / "apps" / "web" / "src"
    package_root = root / "packages" / "test-osdk"
    (package_root / "src").mkdir(parents=True)
    source_root.mkdir(parents=True)
    (root / "config").mkdir(parents=True)
    contract = {
        "schemaVersion": "foundry-lite-consumer-osdk-contract/v1",
        "applicationId": "test-app",
        "packageName": "@foundry-lite/test-osdk",
        "domainTypes": [],
        "objects": [],
        "functions": [],
        "actions": [],
    }
    (package_root / "osdk.contract.json").write_text(json.dumps(contract), encoding="utf-8")
    (package_root / "src" / "generated.ts").write_text(
        'import type { OsdkObjectType } from "@foundry-lite/sdk";\nexport const app: OsdkObjectType | null = null;\n',
        encoding="utf-8",
    )
    (source_root / "App.tsx").write_text(consumer_source, encoding="utf-8")
    inventory = {
        "schemaVersion": "foundry-lite-consumer-osdk-apps/v1",
        "applications": [
            {
                "applicationId": "test-app",
                "profile": "consumer_osdk_strict",
                "sourceRoots": ["apps/web/src"],
                "sdkPackageName": "@foundry-lite/test-osdk",
                "sdkPackageRoot": "packages/test-osdk",
                "contractPath": "packages/test-osdk/osdk.contract.json",
                "generatedPath": "packages/test-osdk/src/generated.ts",
                "exceptions": [],
            }
        ],
    }
    (root / "config" / "consumer-osdk-apps.json").write_text(json.dumps(inventory), encoding="utf-8")
