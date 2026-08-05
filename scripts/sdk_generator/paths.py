"""Repository paths and SDK package identity constants for the OSDK generator."""

from __future__ import annotations

from pathlib import Path

# scripts/sdk_generator/paths.py 기준 두 단계 위가 저장소 루트다.
ROOT = Path(__file__).resolve().parents[2]


DEFAULT_ONTOLOGY = ROOT / "examples" / "supply-chain-demo" / "ontology" / "order-customer.yaml"


DEFAULT_TS_OUTPUT = ROOT / "packages" / "sdk-ts" / "src" / "generated.ts"


DEFAULT_WEB_OUTPUT = ROOT / "apps" / "web" / "generated-sdk.js"


DEFAULT_PYTHON_OUTPUT = ROOT / "packages" / "sdk-python" / "src" / "foundry_lite_sdk" / "generated.py"


DEFAULT_PYTHON_INIT_OUTPUT = ROOT / "packages" / "sdk-python" / "src" / "foundry_lite_sdk" / "__init__.py"


SDK_PACKAGE_JSON = ROOT / "packages" / "sdk-ts" / "package.json"


SDK_GENERATOR_NAME = "foundry-lite-osdk-generator"


SDK_ONTOLOGY_SCOPE = "supply-chain-demo"


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)
