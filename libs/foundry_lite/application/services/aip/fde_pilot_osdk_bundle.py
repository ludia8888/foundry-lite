"""Server-owned strict application OSDK source bundle for AI FDE Pilot."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from foundry_lite.application.services.aip.fde_tool_result import FdePlatformToolError, required_text

JsonObject = Mapping[str, object]


_STRICT_CHECKER_SOURCE = """import {{ readFileSync, readdirSync, statSync }} from "node:fs";
import {{ dirname, extname, resolve }} from "node:path";
import {{ fileURLToPath }} from "node:url";
import ts from "typescript";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const packageName = "__PACKAGE_NAME__";
const extensions = new Set([".ts", ".tsx"]);
const forbidden = [
  ["objects", "generic"], ["functions", "generic"], ["actions", "apply"],
  ["actions", "runs"], ["actions", "validate"],
];
const issues = [];
let hasApplicationImport = false;

function files(path) {{
  if (!statSync(path).isDirectory()) return [path];
  return readdirSync(path).sort().flatMap((name) => {{
    const child = resolve(path, name);
    return statSync(child).isDirectory() ? files(child) : extensions.has(extname(child)) ? [child] : [];
  }});
}}

function chain(node) {{
  const values = [];
  let current = node;
  while (ts.isPropertyAccessExpression(current) || ts.isElementAccessExpression(current)) {{
    if (ts.isPropertyAccessExpression(current)) values.unshift(current.name.text);
    else if (current.argumentExpression && ts.isStringLiteral(current.argumentExpression))
      values.unshift(current.argumentExpression.text);
    else return [];
    current = current.expression;
  }}
  if (ts.isIdentifier(current)) values.unshift(current.text);
  return values;
}}

function moduleName(node) {{
  if (ts.isImportDeclaration(node) && ts.isStringLiteral(node.moduleSpecifier)) return node.moduleSpecifier.text;
  if (ts.isCallExpression(node) && node.expression.kind === ts.SyntaxKind.ImportKeyword) {{
    const [argument] = node.arguments;
    return argument && ts.isStringLiteral(argument) ? argument.text : null;
  }}
  if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === "require") {{
    const [argument] = node.arguments;
    return argument && ts.isStringLiteral(argument) ? argument.text : null;
  }}
  return null;
}}

function isModuleOrSubpath(module, packageRoot) {{
  return module === packageRoot || module.startsWith(`${{packageRoot}}/`);
}}

for (const path of files(resolve(root, "src"))) {{
  const source = ts.createSourceFile(
    path,
    readFileSync(path, "utf8"),
    ts.ScriptTarget.Latest,
    true,
    path.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );
  function visit(node) {{
    const importedModule = moduleName(node);
    if (importedModule) {{
      if (isModuleOrSubpath(importedModule, packageName)) hasApplicationImport = true;
      const packageRoot = importedModule.split("/").slice(0, 2).join("/");
      if (packageRoot.endsWith("-osdk") && !isModuleOrSubpath(importedModule, packageName))
        issues.push(`${{path}} imports foreign application OSDK ${{importedModule}}`);
      if (isModuleOrSubpath(importedModule, "@foundry-lite/sdk"))
        issues.push(`${{path}} imports forbidden base SDK ${{importedModule}}`);
    }}
    if (ts.isPropertyAccessExpression(node) || ts.isElementAccessExpression(node)) {{
      const values = chain(node);
      for (const sequence of forbidden) {{
        if (values.some((_, index) => sequence.every((value, offset) => values[index + offset] === value))) {{
          issues.push(`${{path}} uses forbidden low-level chain ${{sequence.join(".")}}`);
          break;
        }}
      }}
    }}
    ts.forEachChild(node, visit);
  }}
  visit(source);
}}

if (!hasApplicationImport) issues.push(`src must import ${{packageName}}`);
if (issues.length) {{ process.stderr.write(`${{issues.join("\\n")}}\\n`); process.exitCode = 1; }}
else process.stdout.write(`consumer OSDK boundary passed for ${{packageName}}\\n`);
"""


def consumer_osdk_plan(application_name: str, slug: str) -> dict[str, object]:
    """Return the non-bypassable OSDK boundary for one generated Pilot app."""

    return {
        "applicationId": slug.replace("-", "_"),
        "displayName": application_name,
        "profile": "consumer_osdk_strict",
        "packageName": f"@foundry-lite/{slug}-osdk",
        "applicationSourceRoots": ["src"],
        "sdkPackageRoot": "packages/application-osdk",
        "exceptions": [],
    }


def react_files(plan: JsonObject) -> dict[str, str]:
    """Render a consumer screen that can only import its application OSDK package."""

    definition = _object_definition(plan)
    object_name = required_text(definition, "apiName")
    package_name = _package_name(plan)
    return {
        "package.json": _application_package_json(package_name),
        "consumer-osdk.contract.json": _consumer_contract(plan, definition),
        "src/App.tsx": _application_source(plan, package_name),
        "src/main.tsx": "import App from './App';\nexport { App };\n",
        "src/generated/ontology.ts": (
            f"export {{ {object_name} as PilotObjectType }} from {json.dumps(package_name)};\n"
        ),
        "scripts/check-consumer-osdk.mjs": _strict_checker_source(package_name),
        "packages/application-osdk/package.json": _osdk_package_json(package_name),
        "packages/application-osdk/src/generated.ts": _generated_source(plan, definition),
        "packages/application-osdk/src/react.ts": _react_hook_source(object_name),
    }


def ci_workflow() -> str:
    return (
        "steps:\n"
        "  - run: pnpm install --frozen-lockfile\n"
        "  - run: pnpm consumer-osdk:check\n"
        "  - run: pnpm typecheck\n"
        "  - run: pnpm test\n"
        "  - run: pnpm build\n"
    )


def _application_package_json(package_name: str) -> str:
    value = {
        "private": True,
        "workspaces": ["packages/*"],
        "dependencies": {package_name: "workspace:*", "react": "^19.0.0"},
        "devDependencies": {"typescript": "^6.0.3"},
        "scripts": {
            "build": "vite build",
            "consumer-osdk:check": "node scripts/check-consumer-osdk.mjs",
            "test": "vitest run",
            "typecheck": "tsc --noEmit",
        },
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _osdk_package_json(package_name: str) -> str:
    value = {
        "name": package_name,
        "version": "0.1.0",
        "private": True,
        "type": "module",
        "exports": {".": "./src/generated.ts", "./react": "./src/react.ts"},
        "dependencies": {"@foundry-lite/sdk": "workspace:*"},
        "peerDependencies": {"react": ">=18"},
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _consumer_contract(plan: JsonObject, definition: JsonObject) -> str:
    boundary = _mapping(plan.get("consumerOsdk"), "consumerOsdk")
    value = {
        "schemaVersion": "foundry-lite-generated-consumer-osdk/v1",
        **boundary,
        "objects": [dict(definition)],
        "actions": [],
        "functions": [],
        "requiredGate": "quality:consumer-osdk",
    }
    return json.dumps(value, sort_keys=True, indent=2) + "\n"


def _application_source(plan: JsonObject, package_name: str) -> str:
    title = json.dumps(str(plan["applicationName"]), ensure_ascii=True)
    return (
        f"import {{ usePilotApplicationScreen }} from {json.dumps(f'{package_name}/react')};\n"
        f"const title = {title};\n"
        "export default function App() { const screen = usePilotApplicationScreen(); "
        "if (screen.isLoading) return <main><h1>{title}</h1><p>Loading…</p></main>; "
        "if (screen.error) return <main><h1>{title}</h1><p>{screen.error.message}</p></main>; "
        "return <main><h1>{title}</h1><pre>{JSON.stringify(screen.items, null, 2)}</pre></main>; }\n"
    )


def _generated_source(plan: JsonObject, definition: JsonObject) -> str:
    object_name = required_text(definition, "apiName")
    properties = _mapping_items(definition.get("properties"))
    descriptor = _object_descriptor(definition, properties)
    fields = "\n".join(_property_field(item) for item in properties)
    manifest = {
        "schemaVersion": "foundry-lite-consumer-osdk-manifest/v1",
        "applicationId": _mapping(plan.get("consumerOsdk"), "consumerOsdk")["applicationId"],
        "profile": "consumer_osdk_strict",
        "objectApiNames": [object_name],
        "actionApiNames": [],
        "functionApiNames": [],
    }
    return (
        "// Generated application OSDK. Do not edit by hand.\n"
        'import type { FoundryLiteObject, OsdkObjectType } from "@foundry-lite/sdk";\n\n'
        f"export type {object_name}Properties = {{\n{fields}\n}};\n"
        f"export type {object_name} = FoundryLiteObject<{json.dumps(object_name)}, {object_name}Properties>;\n"
        f"export const {object_name} = {descriptor} as const as OsdkObjectType<{object_name}>;\n"
        f"export const $Objects = {{ {object_name} }} as const;\n"
        f"export const CONSUMER_OSDK_MANIFEST = {json.dumps(manifest, sort_keys=True)} as const;\n"
    )


def _react_hook_source(object_name: str) -> str:
    return (
        'import { useEffect, useState } from "react";\n'
        'import { useFoundryLiteOsdkClient } from "@foundry-lite/sdk/react";\n'
        f'import {{ {object_name}, type {object_name} as {object_name}Object }} from "./generated";\n\n'
        "export function usePilotApplicationScreen() {\n"
        "  const osdk = useFoundryLiteOsdkClient();\n"
        f"  const [items, setItems] = useState<readonly {object_name}Object[]>([]);\n"
        "  const [error, setError] = useState<Error | null>(null);\n"
        "  const [isLoading, setIsLoading] = useState(true);\n"
        "  useEffect(() => { let isActive = true; setIsLoading(true); setError(null);\n"
        f"    void osdk({object_name}).fetchPage({{ pageSize: 50 }}).then((page) => {{\n"
        "      if (isActive) setItems(page.data);\n"
        "    }).catch((reason: unknown) => {\n"
        "      if (isActive) setError(reason instanceof Error ? reason : new Error(String(reason)));\n"
        "    }).finally(() => { if (isActive) setIsLoading(false); });\n"
        "    return () => { isActive = false; }; }, [osdk]);\n"
        "  return { items, error, isLoading } as const;\n"
        "}\n"
    )


def _strict_checker_source(package_name: str) -> str:
    return _STRICT_CHECKER_SOURCE.replace("__PACKAGE_NAME__", package_name).replace("{{", "{").replace("}}", "}")


def _object_definition(plan: JsonObject) -> dict[str, object]:
    resources = _mapping_items(plan.get("ontologyResources"))
    if not resources:
        raise FdePlatformToolError("schema_invalid", "Pilot requires one Ontology object resource")
    return _mapping(resources[0].get("definition"), "ontology resource definition")


def _object_descriptor(definition: JsonObject, properties: Sequence[JsonObject]) -> str:
    property_names = [required_text(item, "apiName") for item in properties]
    value: dict[str, object] = {
        "kind": "object",
        "apiName": required_text(definition, "apiName"),
        "primaryKey": required_text(definition, "primaryKey"),
        "titleProperty": "name" if "name" in property_names else None,
        "properties": property_names,
        "propertyDatasources": {},
    }
    return json.dumps(value, sort_keys=True)


def _property_field(value: JsonObject) -> str:
    name = required_text(value, "apiName")
    scalar = _typescript_scalar(required_text(value, "type"))
    optional = "" if value.get("nullable") is False else "?"
    nullable = "" if value.get("nullable") is False else " | null"
    return f"  readonly {name}{optional}: {scalar}{nullable};"


def _typescript_scalar(value: str) -> str:
    return {"boolean": "boolean", "double": "number", "integer": "number", "long": "number"}.get(value, "string")


def _package_name(plan: JsonObject) -> str:
    return required_text(_mapping(plan.get("consumerOsdk"), "consumerOsdk"), "packageName")


def _mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise FdePlatformToolError("schema_invalid", f"{field} must be an object")
    return {str(name): item for name, item in value.items()}


def _mapping_items(value: object) -> list[dict[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise FdePlatformToolError("schema_invalid", "expected a list of objects")
    if not all(isinstance(item, Mapping) for item in value):
        raise FdePlatformToolError("schema_invalid", "expected a list of objects")
    return [{str(name): field for name, field in item.items()} for item in value if isinstance(item, Mapping)]


__all__ = ["ci_workflow", "consumer_osdk_plan", "react_files"]
