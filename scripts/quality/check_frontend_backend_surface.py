"""Enforces the frontend backend API/SDK surface contract.

S61-S64 product surfaces must use public API plus the generated SDK instead of
hand-built REST paths. This gate compares the FastAPI route inventory,
`docs/frontend-api-sdk-surface-matrix.json`, generated `SDK_CLIENT_SURFACE`, and
the static Web app so frontend work cannot drift back to raw API calls.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_API = ROOT / "apps" / "api" / "foundry_lite_api" / "main.py"
DEFAULT_MATRIX = ROOT / "docs" / "frontend-api-sdk-surface-matrix.json"
DEFAULT_SDK = ROOT / "packages" / "sdk-ts" / "src" / "generated.ts"
DEFAULT_WEB = ROOT / "apps" / "web" / "index.html"
DEFAULT_TESTS = ROOT / "tests"
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "frontend_backend_surface.json"
DEFAULT_SUMMARY = ROOT / "artifacts" / "quality" / "frontend_backend_surface.md"
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
IGNORED_DOC_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "artifacts",
    "htmlcov",
    "mutants",
    "node_modules",
    "__pycache__",
}
WEB_RAW_API_PATTERNS = (
    "sdkClient().request(",
    "async function request(",
    'request("/api',
    "request('/api",
    "request(`/api",
    '"/api/',
    "'/api/",
    "`/api/",
)
FRONTEND_PROOF_CLASSES = {"sdk-request-contract"}
SDK_HELPER_PROOF_CLASSES = {"sdk-helper-contract"}
REQUIRED_SDK_REQUEST_CONTRACT_TEST = "test_sdk_request_contract_covers_all_frontend_surface_routes"
REQUIRED_SDK_HELPER_CONTRACT_TEST = "test_sdk_request_contract_covers_frontend_foundation_helpers"
EXPORT_FUNCTION_RE = re.compile(r"export\s+(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)\b")
FRONTEND_SURFACE_COUNT_RES = (
    re.compile(
        r"\b(?P<count>\d+)\s*(?:개\s*)?"
        r"(?:frontend\s+API\s+surfaces?|frontend\s+route\s+surface(?:s| rows)?|"
        r"frontend\s+surfaces?|route[-\s]surface(?:s)?|browser\s+SDK\s+(?:route\s+surface|method/path))",
        re.IGNORECASE,
    ),
)
SDK_HELPER_COUNT_RES = (
    re.compile(
        r"\b(?P<count>\d+)\s*(?:개\s*)?"
        r"(?:`?SDK_CLIENT_SURFACE\.helpers`?\s+rows?|SDK\s+helpers?|"
        r"SDK\s+helper(?:[-\s](?:surface|runtime|rows?|behavior))?|"
        r"helper[-\s]surface|matrix-locked\s+safety\s+helpers)",
        re.IGNORECASE,
    ),
)


@dataclass(frozen=True)
class FrontendBackendSurfaceFinding:
    code: str
    route: str
    message: str
    suggested_file: str


def _repo_relative(path: Path, *, root: Path = ROOT) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{_repo_relative(path)} must contain a JSON object")
    return payload


def _fastapi_routes(api_path: Path) -> set[str]:
    tree = ast.parse(api_path.read_text(encoding="utf-8"), filename=str(api_path))
    routes: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            route = _route_from_decorator(decorator)
            if route:
                routes.add(route)
    return routes


def _fastapi_idempotency_routes(api_path: Path) -> set[str]:
    tree = ast.parse(api_path.read_text(encoding="utf-8"), filename=str(api_path))
    routes: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if not _function_requires_idempotency_key(node):
            continue
        for decorator in node.decorator_list:
            route = _route_from_decorator(decorator)
            if route:
                routes.add(route)
    return routes


def _function_requires_idempotency_key(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    defaults = [default for default in node.args.defaults if default is not None]
    defaults.extend(default for default in node.args.kw_defaults if default is not None)
    return any(_is_idempotency_header(default) for default in defaults)


def _is_idempotency_header(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    if isinstance(node.func, ast.Name):
        function_name = node.func.id
    elif isinstance(node.func, ast.Attribute):
        function_name = node.func.attr
    else:
        return False
    if function_name != "Header":
        return False
    return any(
        keyword.arg == "alias" and isinstance(keyword.value, ast.Constant) and keyword.value.value == "Idempotency-Key"
        for keyword in node.keywords
    )


def _route_from_decorator(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    if not isinstance(node.func, ast.Attribute):
        return None
    if node.func.attr not in HTTP_METHODS:
        return None
    if not node.args or not isinstance(node.args[0], ast.Constant):
        return None
    path = node.args[0].value
    if not isinstance(path, str):
        return None
    return f"{node.func.attr.upper()} {path}"


def _sdk_client_surface(sdk_path: Path) -> dict[str, Any]:
    source = sdk_path.read_text(encoding="utf-8")
    prefix = "export const SDK_CLIENT_SURFACE = "
    start = source.index(prefix) + len(prefix)
    end = source.index(";", start)
    payload = json.loads(source[start:end])
    if not isinstance(payload, dict):
        raise ValueError("SDK_CLIENT_SURFACE must be a JSON object")
    return payload


def _sdk_surface_exists(surface: dict[str, Any], sdk_path: str) -> bool:
    parts = sdk_path.split(".")
    if len(parts) == 2:
        group = surface.get(parts[0])
        return isinstance(group, list) and parts[1] in group
    if len(parts) == 3:
        group = surface.get(parts[0])
        if not isinstance(group, dict):
            return False
        methods = group.get(parts[1])
        return isinstance(methods, list) and parts[2] in methods
    return False


def _surface_rows(matrix: dict[str, Any]) -> list[dict[str, Any]]:
    rows = matrix.get("surfaces")
    if not isinstance(rows, list):
        raise ValueError("frontend surface matrix must contain a surfaces list")
    return [row for row in rows if isinstance(row, dict)]


def _non_frontend_rows(matrix: dict[str, Any]) -> list[dict[str, Any]]:
    rows = matrix.get("nonFrontendRoutes")
    if not isinstance(rows, list):
        raise ValueError("frontend surface matrix must contain a nonFrontendRoutes list")
    return [row for row in rows if isinstance(row, dict)]


def _sdk_helper_rows(matrix: dict[str, Any]) -> list[dict[str, Any]]:
    rows = matrix.get("sdkHelpers")
    if not isinstance(rows, list):
        raise ValueError("frontend surface matrix must contain an sdkHelpers list")
    return [row for row in rows if isinstance(row, dict)]


def _row_route(row: dict[str, Any]) -> str:
    route = row.get("route")
    return route if isinstance(route, str) else ""


def _known_test_names(tests_root: Path) -> set[str]:
    names: set[str] = set()
    for path in tests_root.rglob("test_*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("test_"):
                names.add(node.name)
    return names


def _exported_functions(sdk_path: Path) -> set[str]:
    return set(EXPORT_FUNCTION_RE.findall(sdk_path.read_text(encoding="utf-8")))


def _missing_route_findings(
    actual_routes: set[str],
    classified_routes: set[str],
) -> list[FrontendBackendSurfaceFinding]:
    return [
        FrontendBackendSurfaceFinding(
            code="api_route_missing_frontend_surface_classification",
            route=route,
            message="FastAPI route is not classified as frontend SDK surface or non-frontend route.",
            suggested_file="docs/frontend-api-sdk-surface-matrix.json",
        )
        for route in sorted(actual_routes - classified_routes)
    ]


def _stale_route_findings(
    actual_routes: set[str],
    classified_routes: set[str],
) -> list[FrontendBackendSurfaceFinding]:
    return [
        FrontendBackendSurfaceFinding(
            code="frontend_surface_classifies_missing_api_route",
            route=route,
            message="Surface matrix references an API route that FastAPI no longer exposes.",
            suggested_file="docs/frontend-api-sdk-surface-matrix.json",
        )
        for route in sorted(classified_routes - actual_routes)
    ]


def _surface_contract_findings(
    rows: list[dict[str, Any]],
    sdk_surface: dict[str, Any],
    known_tests: set[str],
    idempotency_routes: set[str],
) -> list[FrontendBackendSurfaceFinding]:
    findings: list[FrontendBackendSurfaceFinding] = []
    for row in rows:
        route = _row_route(row)
        findings.extend(_raw_request_policy_findings(row, route))
        findings.extend(_sdk_method_findings(row, route, sdk_surface))
        findings.extend(_operator_evidence_findings(row, route))
        findings.extend(_idempotency_contract_findings(row, route, idempotency_routes))
        findings.extend(_proof_class_findings(row, route))
        findings.extend(_proof_test_findings(row, route, known_tests))
    return findings


def _idempotency_contract_findings(
    row: dict[str, Any],
    route: str,
    idempotency_routes: set[str],
) -> list[FrontendBackendSurfaceFinding]:
    requires_key = row.get("requiresIdempotencyKey")
    evidence = row.get("operatorEvidence")
    findings: list[FrontendBackendSurfaceFinding] = []
    if route in idempotency_routes and requires_key is not True:
        findings.append(
            FrontendBackendSurfaceFinding(
                code="frontend_surface_missing_idempotency_requirement",
                route=route,
                message=(
                    "FastAPI requires Idempotency-Key for this route, but the frontend surface matrix does not "
                    "declare requiresIdempotencyKey=true."
                ),
                suggested_file="docs/frontend-api-sdk-surface-matrix.json",
            )
        )
    if route not in idempotency_routes and requires_key is True:
        findings.append(
            FrontendBackendSurfaceFinding(
                code="frontend_surface_stale_idempotency_requirement",
                route=route,
                message="Surface matrix declares requiresIdempotencyKey=true, but FastAPI no longer requires it.",
                suggested_file="docs/frontend-api-sdk-surface-matrix.json",
            )
        )
    if requires_key is True and (not isinstance(evidence, str) or "Idempotency-Key" not in evidence):
        findings.append(
            FrontendBackendSurfaceFinding(
                code="frontend_surface_idempotency_evidence_missing",
                route=route,
                message="Idempotent mutation surface must mention Idempotency-Key in operatorEvidence.",
                suggested_file="docs/frontend-api-sdk-surface-matrix.json",
            )
        )
    return findings


def _raw_request_policy_findings(
    row: dict[str, Any],
    route: str,
) -> list[FrontendBackendSurfaceFinding]:
    if row.get("rawRequestPolicy") == "named-sdk-only":
        return []
    return [
        FrontendBackendSurfaceFinding(
            code="frontend_surface_raw_request_policy_not_locked",
            route=route,
            message="Frontend-consumable surface must require named-sdk-only access.",
            suggested_file="docs/frontend-api-sdk-surface-matrix.json",
        )
    ]


def _sdk_method_findings(
    row: dict[str, Any],
    route: str,
    sdk_surface: dict[str, Any],
) -> list[FrontendBackendSurfaceFinding]:
    sdk_path = row.get("sdkSurface")
    if isinstance(sdk_path, str) and _sdk_surface_exists(sdk_surface, sdk_path):
        return []
    return [
        FrontendBackendSurfaceFinding(
            code="frontend_surface_missing_generated_sdk_method",
            route=route,
            message=f"SDK surface {sdk_path!r} is not present in SDK_CLIENT_SURFACE.",
            suggested_file="scripts/generate_sdk_ts.py",
        )
    ]


def _operator_evidence_findings(
    row: dict[str, Any],
    route: str,
) -> list[FrontendBackendSurfaceFinding]:
    operator_evidence = row.get("operatorEvidence")
    if isinstance(operator_evidence, str) and operator_evidence:
        return []
    return [
        FrontendBackendSurfaceFinding(
            code="frontend_surface_missing_operator_evidence",
            route=route,
            message="Frontend surface must say where request/run/audit/error evidence is visible.",
            suggested_file="docs/frontend-api-sdk-surface-matrix.json",
        )
    ]


def _proof_class_findings(
    row: dict[str, Any],
    route: str,
) -> list[FrontendBackendSurfaceFinding]:
    proof_class = row.get("proofClass")
    if proof_class in FRONTEND_PROOF_CLASSES:
        return []
    return [
        FrontendBackendSurfaceFinding(
            code="frontend_surface_missing_sdk_request_proof_class",
            route=route,
            message="Frontend surface must declare proofClass='sdk-request-contract'.",
            suggested_file="docs/frontend-api-sdk-surface-matrix.json",
        )
    ]


def _proof_test_findings(
    row: dict[str, Any],
    route: str,
    known_tests: set[str],
) -> list[FrontendBackendSurfaceFinding]:
    proof_tests = row.get("proofTests")
    if not isinstance(proof_tests, list) or not proof_tests:
        return [
            FrontendBackendSurfaceFinding(
                code="frontend_surface_missing_proof_tests",
                route=route,
                message="Frontend surface must cite at least one proof test.",
                suggested_file="docs/frontend-api-sdk-surface-matrix.json",
            )
        ]
    findings = _missing_proof_test_findings(route, proof_tests, known_tests)
    findings.extend(_required_request_contract_findings(route, proof_tests))
    return findings


def _required_request_contract_findings(
    route: str,
    proof_tests: list[Any],
) -> list[FrontendBackendSurfaceFinding]:
    if REQUIRED_SDK_REQUEST_CONTRACT_TEST in proof_tests:
        return []
    return [
        FrontendBackendSurfaceFinding(
            code="frontend_surface_missing_sdk_request_contract_test",
            route=route,
            message=(
                "Frontend surface must cite the browser SDK request-contract test "
                f"{REQUIRED_SDK_REQUEST_CONTRACT_TEST!r}."
            ),
            suggested_file="docs/frontend-api-sdk-surface-matrix.json",
        )
    ]


def _missing_proof_test_findings(
    route: str,
    proof_tests: list[Any],
    known_tests: set[str],
) -> list[FrontendBackendSurfaceFinding]:
    return [
        FrontendBackendSurfaceFinding(
            code="frontend_surface_proof_test_not_collectable",
            route=route,
            message=f"Proof test {test_name!r} was not found in tests/.",
            suggested_file="tests/unit/test_quality_frontend_backend_surface.py",
        )
        for test_name in proof_tests
        if not isinstance(test_name, str) or test_name not in known_tests
    ]


def _sdk_helpers_from_surface(sdk_surface: dict[str, Any]) -> set[str]:
    helpers = sdk_surface.get("helpers")
    if not isinstance(helpers, list):
        return set()
    return {helper for helper in helpers if isinstance(helper, str)}


def _row_helper(row: dict[str, Any]) -> str:
    helper = row.get("sdkHelper")
    return helper if isinstance(helper, str) else ""


def _sdk_helper_contract_findings(
    rows: list[dict[str, Any]],
    sdk_helpers: set[str],
    exported_functions: set[str],
    known_tests: set[str],
) -> list[FrontendBackendSurfaceFinding]:
    findings: list[FrontendBackendSurfaceFinding] = []
    matrix_helpers = {_row_helper(row) for row in rows}
    findings.extend(_missing_sdk_helper_matrix_findings(sdk_helpers, matrix_helpers))
    findings.extend(_stale_sdk_helper_matrix_findings(sdk_helpers, matrix_helpers))
    findings.extend(_duplicate_sdk_helper_findings(rows))
    for row in rows:
        helper = _row_helper(row)
        findings.extend(_sdk_helper_export_findings(helper, exported_functions))
        findings.extend(_sdk_helper_evidence_findings(row, helper))
        findings.extend(_sdk_helper_proof_findings(row, helper, known_tests))
    return findings


def _missing_sdk_helper_matrix_findings(
    sdk_helpers: set[str],
    matrix_helpers: set[str],
) -> list[FrontendBackendSurfaceFinding]:
    return [
        FrontendBackendSurfaceFinding(
            code="sdk_helper_missing_matrix_row",
            route=helper,
            message="SDK_CLIENT_SURFACE exposes this helper, but sdkHelpers has no contract row.",
            suggested_file="docs/frontend-api-sdk-surface-matrix.json",
        )
        for helper in sorted(sdk_helpers - matrix_helpers)
    ]


def _stale_sdk_helper_matrix_findings(
    sdk_helpers: set[str],
    matrix_helpers: set[str],
) -> list[FrontendBackendSurfaceFinding]:
    return [
        FrontendBackendSurfaceFinding(
            code="sdk_helper_matrix_references_missing_helper",
            route=helper,
            message="sdkHelpers references a helper that is not present in SDK_CLIENT_SURFACE.helpers.",
            suggested_file="docs/frontend-api-sdk-surface-matrix.json",
        )
        for helper in sorted(matrix_helpers - sdk_helpers)
    ]


def _duplicate_sdk_helper_findings(rows: list[dict[str, Any]]) -> list[FrontendBackendSurfaceFinding]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for row in rows:
        helper = _row_helper(row)
        if helper in seen:
            duplicates.add(helper)
        seen.add(helper)
    return [
        FrontendBackendSurfaceFinding(
            code="sdk_helper_duplicate_matrix_row",
            route=helper,
            message="sdkHelpers must contain one row per SDK helper.",
            suggested_file="docs/frontend-api-sdk-surface-matrix.json",
        )
        for helper in sorted(duplicates)
    ]


def _sdk_helper_export_findings(
    helper: str,
    exported_functions: set[str],
) -> list[FrontendBackendSurfaceFinding]:
    if helper in exported_functions:
        return []
    return [
        FrontendBackendSurfaceFinding(
            code="sdk_helper_missing_export",
            route=helper,
            message="SDK helper is declared in the surface matrix but is not exported by the generated TypeScript SDK.",
            suggested_file="packages/sdk-ts/src/generated.ts",
        )
    ]


def _sdk_helper_evidence_findings(
    row: dict[str, Any],
    helper: str,
) -> list[FrontendBackendSurfaceFinding]:
    operator_evidence = row.get("operatorEvidence")
    if isinstance(operator_evidence, str) and operator_evidence:
        return []
    return [
        FrontendBackendSurfaceFinding(
            code="sdk_helper_missing_operator_evidence",
            route=helper,
            message="SDK helper must explain the frontend/operator evidence risk it protects.",
            suggested_file="docs/frontend-api-sdk-surface-matrix.json",
        )
    ]


def _sdk_helper_proof_findings(
    row: dict[str, Any],
    helper: str,
    known_tests: set[str],
) -> list[FrontendBackendSurfaceFinding]:
    findings: list[FrontendBackendSurfaceFinding] = []
    if row.get("proofClass") not in SDK_HELPER_PROOF_CLASSES:
        findings.append(
            FrontendBackendSurfaceFinding(
                code="sdk_helper_missing_proof_class",
                route=helper,
                message="SDK helper must declare proofClass='sdk-helper-contract'.",
                suggested_file="docs/frontend-api-sdk-surface-matrix.json",
            )
        )
    proof_tests = row.get("proofTests")
    if not isinstance(proof_tests, list) or not proof_tests:
        return [
            *findings,
            FrontendBackendSurfaceFinding(
                code="sdk_helper_missing_proof_tests",
                route=helper,
                message="SDK helper must cite at least one proof test.",
                suggested_file="docs/frontend-api-sdk-surface-matrix.json",
            ),
        ]
    findings.extend(
        FrontendBackendSurfaceFinding(
            code="sdk_helper_proof_test_not_collectable",
            route=helper,
            message=f"Proof test {test_name!r} was not found in tests/.",
            suggested_file="tests/unit/test_sdk_request_contract.py",
        )
        for test_name in proof_tests
        if not isinstance(test_name, str) or test_name not in known_tests
    )
    if REQUIRED_SDK_HELPER_CONTRACT_TEST not in proof_tests:
        findings.append(
            FrontendBackendSurfaceFinding(
                code="sdk_helper_missing_request_contract_test",
                route=helper,
                message=(
                    f"SDK helper must cite the browser SDK helper-contract test {REQUIRED_SDK_HELPER_CONTRACT_TEST!r}."
                ),
                suggested_file="docs/frontend-api-sdk-surface-matrix.json",
            )
        )
    return findings


def _web_raw_request_findings(web_path: Path) -> list[FrontendBackendSurfaceFinding]:
    source = web_path.read_text(encoding="utf-8")
    findings: list[FrontendBackendSurfaceFinding] = []
    for pattern in WEB_RAW_API_PATTERNS:
        if pattern in source:
            findings.append(
                FrontendBackendSurfaceFinding(
                    code="web_uses_raw_api_request",
                    route="apps/web/index.html",
                    message=f"Web app still contains raw API request pattern {pattern!r}; use named SDK methods.",
                    suggested_file="apps/web/index.html",
                )
            )
    return findings


def _is_ignored_doc_path(path: Path, root: Path) -> bool:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        parts = path.parts
    return any(part in IGNORED_DOC_PARTS or part.startswith(".foundry-lite") for part in parts)


def _markdown_docs(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.md") if not _is_ignored_doc_path(path, root))


def _doc_count_claim_findings(
    root: Path,
    *,
    expected_surface_count: int,
    expected_helper_count: int,
) -> list[FrontendBackendSurfaceFinding]:
    findings: list[FrontendBackendSurfaceFinding] = []
    seen: set[tuple[str, str, str]] = set()
    for path in _markdown_docs(root):
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            next_line = lines[index + 1] if index + 1 < len(lines) else ""
            context = f"{line} {next_line}"
            findings.extend(
                _count_claim_findings_for_patterns(
                    path,
                    index + 1,
                    context,
                    patterns=FRONTEND_SURFACE_COUNT_RES,
                    expected_count=expected_surface_count,
                    code="frontend_surface_count_claim_mismatch",
                    label="frontend route surface",
                    seen=seen,
                    root=root,
                )
            )
            findings.extend(
                _count_claim_findings_for_patterns(
                    path,
                    index + 1,
                    context,
                    patterns=SDK_HELPER_COUNT_RES,
                    expected_count=expected_helper_count,
                    code="sdk_helper_count_claim_mismatch",
                    label="SDK helper",
                    seen=seen,
                    root=root,
                )
            )
    return findings


def _count_claim_findings_for_patterns(
    path: Path,
    line_number: int,
    context: str,
    *,
    patterns: tuple[re.Pattern[str], ...],
    expected_count: int,
    code: str,
    label: str,
    seen: set[tuple[str, str, str]],
    root: Path,
) -> list[FrontendBackendSurfaceFinding]:
    findings: list[FrontendBackendSurfaceFinding] = []
    for pattern in patterns:
        for match in pattern.finditer(context):
            count = int(match.group("count"))
            if count == expected_count:
                continue
            reference = f"{_repo_relative(path, root=root)}:{line_number}"
            key = (code, reference, match.group(0))
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                FrontendBackendSurfaceFinding(
                    code=code,
                    route=reference,
                    message=(
                        f"Documentation says {count} {label} contracts, but the matrix currently has {expected_count}."
                    ),
                    suggested_file=_repo_relative(path, root=root),
                )
            )
    return findings


def collect_findings(
    root: Path = ROOT,
    *,
    api_path: Path | None = None,
    matrix_path: Path | None = None,
    sdk_path: Path | None = None,
    web_path: Path | None = None,
    tests_root: Path | None = None,
) -> list[FrontendBackendSurfaceFinding]:
    api = api_path or root / "apps" / "api" / "foundry_lite_api" / "main.py"
    matrix_file = matrix_path or root / "docs" / "frontend-api-sdk-surface-matrix.json"
    sdk = sdk_path or root / "packages" / "sdk-ts" / "src" / "generated.ts"
    web = web_path or root / "apps" / "web" / "index.html"
    tests = tests_root or root / "tests"
    matrix = _load_json(matrix_file)
    surface_rows = _surface_rows(matrix)
    non_frontend_rows = _non_frontend_rows(matrix)
    sdk_helper_rows = _sdk_helper_rows(matrix)
    actual_routes = _fastapi_routes(api)
    idempotency_routes = _fastapi_idempotency_routes(api)
    classified_routes = {_row_route(row) for row in surface_rows + non_frontend_rows}
    sdk_surface = _sdk_client_surface(sdk)
    sdk_helpers = _sdk_helpers_from_surface(sdk_surface)
    exported_functions = _exported_functions(sdk)
    known_tests = _known_test_names(tests)
    findings: list[FrontendBackendSurfaceFinding] = []
    findings.extend(_missing_route_findings(actual_routes, classified_routes))
    findings.extend(_stale_route_findings(actual_routes, classified_routes))
    findings.extend(_surface_contract_findings(surface_rows, sdk_surface, known_tests, idempotency_routes))
    findings.extend(_sdk_helper_contract_findings(sdk_helper_rows, sdk_helpers, exported_functions, known_tests))
    findings.extend(_web_raw_request_findings(web))
    findings.extend(
        _doc_count_claim_findings(
            root,
            expected_surface_count=len(surface_rows),
            expected_helper_count=len(sdk_helpers),
        )
    )
    return findings


def _write_reports(findings: list[FrontendBackendSurfaceFinding], output_path: Path, summary_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "gate": "frontend-backend-surface",
        "status": "FAIL" if findings else "PASS",
        "findingCount": len(findings),
        "findings": [asdict(finding) for finding in findings],
    }
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = ["# Frontend Backend Surface Gate", "", f"Status: {payload['status']}"]
    if findings:
        lines.extend(["", "## Findings"])
        for finding in findings:
            lines.append(f"- `{finding.code}` `{finding.route}`: {finding.message} ({finding.suggested_file})")
    else:
        lines.extend(
            [
                "",
                "All FastAPI frontend routes are classified, named SDK surfaces/helpers exist, and Web uses SDK calls.",
            ]
        )
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _print_findings(findings: list[FrontendBackendSurfaceFinding]) -> None:
    if not findings:
        print(
            "frontend-backend-surface passed: API routes, SDK surface/helpers, docs matrix, and Web calls are aligned."
        )
        return
    print("frontend-backend-surface failed:")
    for finding in findings:
        print(f"- {finding.code} [{finding.route}]: {finding.message}")
        print(f"  suggested file: {finding.suggested_file}")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check frontend API/SDK surface contract.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--api", type=Path, default=DEFAULT_API)
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--sdk", type=Path, default=DEFAULT_SDK)
    parser.add_argument("--web", type=Path, default=DEFAULT_WEB)
    parser.add_argument("--tests", type=Path, default=DEFAULT_TESTS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    findings = collect_findings(
        args.root,
        api_path=args.api,
        matrix_path=args.matrix,
        sdk_path=args.sdk,
        web_path=args.web,
        tests_root=args.tests,
    )
    _write_reports(findings, args.output, args.summary)
    _print_findings(findings)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
