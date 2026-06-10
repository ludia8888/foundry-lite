from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "infra_import_boundary.json"
LOCAL_INFRA_MODULES = {
    "duckdb",
    "sqlalchemy",
}
SCALE_INFRA_MODULES = {
    "boto3",
    "botocore",
    "confluent_kafka",
    "kafka",
    "opensearchpy",
    "pyflink",
    "pyspark",
    "temporalio",
}
FORBIDDEN_MODULES = LOCAL_INFRA_MODULES | SCALE_INFRA_MODULES


@dataclass(frozen=True)
class ImportFinding:
    layer: str
    path: str
    line: int
    module: str


def _module_root(module_name: str) -> str:
    return module_name.split(".", 1)[0]


def _imports_from_tree(tree: ast.AST) -> list[tuple[int, str]]:
    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append((node.lineno, node.module))
    return imports


def _layer_for_path(path: Path) -> str | None:
    relative = path.relative_to(ROOT)
    parts = relative.parts
    if parts[:3] == ("libs", "foundry_lite", "domain"):
        return "domain"
    if parts[:3] == ("libs", "foundry_lite", "application"):
        return "application"
    return None


def collect_findings() -> list[ImportFinding]:
    findings: list[ImportFinding] = []
    for path in sorted((ROOT / "libs" / "foundry_lite").rglob("*.py")):
        layer = _layer_for_path(path)
        if layer is None:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for line, module in _imports_from_tree(tree):
            if _module_root(module) in FORBIDDEN_MODULES:
                findings.append(
                    ImportFinding(
                        layer=layer,
                        path=str(path.relative_to(ROOT)),
                        line=line,
                        module=module,
                    )
                )
    return findings


def write_report(output: Path, findings: list[ImportFinding]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps([asdict(finding) for finding in findings], indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _print_findings(findings: list[ImportFinding]) -> None:
    for finding in findings:
        print(f"{finding.layer}: {finding.path}:{finding.line} imports {finding.module}")


def _findings_for_layer(findings: list[ImportFinding], layer: str) -> list[ImportFinding]:
    return [finding for finding in findings if finding.layer == layer]


def _scale_findings(findings: list[ImportFinding]) -> list[ImportFinding]:
    return [finding for finding in findings if _module_root(finding.module) in SCALE_INFRA_MODULES]


def _threshold_messages(
    *,
    domain_count: int,
    application_count: int,
    max_domain_imports: int,
    max_application_imports: int,
) -> list[str]:
    messages: list[str] = []
    if domain_count > max_domain_imports:
        messages.append(f"Domain concrete infra imports exceeded: {domain_count} > {max_domain_imports}")
    if application_count > max_application_imports:
        messages.append(
            f"Application concrete infra import baseline exceeded: {application_count} > {max_application_imports}"
        )
    return messages


def _print_failure_report(
    *,
    messages: list[str],
    scale_imports: list[ImportFinding],
    findings: list[ImportFinding],
    output: Path,
) -> None:
    for message in messages:
        print(message)
    if scale_imports:
        print("Scale infra SDK imports must live behind adapter modules, not domain/application:")
        _print_findings(scale_imports)
    print(f"Infra import boundary report: {output}")
    _print_findings(findings)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-application-imports", type=int, required=True)
    parser.add_argument("--max-domain-imports", type=int, default=0)
    args = parser.parse_args(argv)

    findings = collect_findings()
    write_report(args.output, findings)
    domain_findings = _findings_for_layer(findings, "domain")
    application_findings = _findings_for_layer(findings, "application")
    scale_imports = _scale_findings(findings)
    messages = _threshold_messages(
        domain_count=len(domain_findings),
        application_count=len(application_findings),
        max_domain_imports=args.max_domain_imports,
        max_application_imports=args.max_application_imports,
    )
    if messages or scale_imports:
        _print_failure_report(
            messages=messages,
            scale_imports=scale_imports,
            findings=findings,
            output=args.output,
        )
        return 1

    print(
        "Infra import boundary OK: "
        f"domain={len(domain_findings)}/{args.max_domain_imports}, "
        f"application={len(application_findings)}/{args.max_application_imports}, "
        f"report={args.output}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
