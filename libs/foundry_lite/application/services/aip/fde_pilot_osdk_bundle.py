"""Server-owned strict application OSDK source bundle for AI FDE Pilot."""

from __future__ import annotations

import json
from collections.abc import Mapping

from foundry_lite.application.services.aip.fde_pilot_osdk_runtime import portable_runtime_source
from foundry_lite.application.services.aip.fde_pilot_osdk_source import (
    application_source,
    consumer_contract,
    generated_source,
    ontology_reexport,
    react_hook_source,
)
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

    package_name = _package_name(plan)
    return {
        "package.json": _application_package_json(package_name),
        "index.html": _index_html(),
        "tsconfig.json": _tsconfig_json(),
        "vite.config.ts": _vite_config_source(),
        "consumer-osdk.contract.json": consumer_contract(plan),
        "public/business-system.json": _business_system_json(plan),
        "src/App.tsx": application_source(plan, package_name),
        "src/main.tsx": _main_source(package_name),
        "src/styles.css": _styles_source(),
        "src/generated/ontology.ts": ontology_reexport(plan, package_name),
        "scripts/check-consumer-osdk.mjs": _strict_checker_source(package_name),
        "scripts/check-runtime-contract.mjs": _runtime_contract_source(),
        "packages/application-osdk/package.json": _osdk_package_json(package_name),
        "packages/application-osdk/src/generated.ts": generated_source(plan),
        "packages/application-osdk/src/react.ts": react_hook_source(plan),
        "packages/application-osdk/src/runtime.ts": portable_runtime_source(),
    }


def ci_workflow() -> str:
    return (
        "steps:\n"
        "  - run: pnpm install --no-frozen-lockfile\n"
        "  - run: pnpm consumer-osdk:check\n"
        "  - run: pnpm typecheck\n"
        "  - run: pnpm test\n"
        "  - run: pnpm build\n"
    )


def _application_package_json(package_name: str) -> str:
    value = {
        "name": package_name.removesuffix("-osdk") + "-app",
        "private": True,
        "packageManager": "pnpm@10.23.0",
        "dependencies": {package_name: "file:packages/application-osdk", "react": "19.2.7", "react-dom": "19.2.7"},
        "devDependencies": {
            "@types/react": "19.2.17",
            "@types/react-dom": "19.2.3",
            "@vitejs/plugin-react": "4.7.0",
            "typescript": "6.0.3",
            "vite": "7.3.6",
        },
        "scripts": {
            "build": "vite build",
            "consumer-osdk:check": "node scripts/check-consumer-osdk.mjs",
            "test": "node scripts/check-runtime-contract.mjs",
            "typecheck": "tsc --noEmit",
        },
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _business_system_json(plan: JsonObject) -> str:
    value = _mapping(plan.get("businessSystemDefinition"), "businessSystemDefinition")
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def _index_html() -> str:
    return (
        '<!doctype html><html lang="ko"><head><meta charset="UTF-8" />'
        '<meta name="viewport" content="width=device-width,initial-scale=1.0" />'
        '<title>Domain OS</title></head><body><div id="root"></div>'
        '<script type="module" src="/src/main.tsx"></script></body></html>\n'
    )


def _tsconfig_json() -> str:
    value = {
        "compilerOptions": {
            "target": "ES2022",
            "lib": ["ES2022", "DOM", "DOM.Iterable"],
            "module": "ESNext",
            "moduleResolution": "Bundler",
            "jsx": "react-jsx",
            "strict": True,
            "noEmit": True,
            "skipLibCheck": True,
            "types": ["vite/client"],
        },
        "include": ["src", "packages/application-osdk/src", "vite.config.ts"],
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _vite_config_source() -> str:
    return (
        'import react from "@vitejs/plugin-react";\n'
        'import { defineConfig } from "vite";\n'
        "export default defineConfig({ plugins: [react()] });\n"
    )


def _main_source(package_name: str) -> str:
    return (
        'import { StrictMode } from "react";\n'
        'import { createRoot } from "react-dom/client";\n'
        f"import {{ PilotApplicationProvider }} from {json.dumps(f'{package_name}/react')};\n"
        'import App from "./App";\n'
        'import "./styles.css";\n'
        'const root = document.getElementById("root");\n'
        'if (!root) throw new Error("앱을 표시할 영역을 찾지 못했습니다.");\n'
        "createRoot(root).render(<StrictMode><PilotApplicationProvider>"
        "<App /></PilotApplicationProvider></StrictMode>);\n"
    )


def _styles_source() -> str:
    return (
        ":root{font-family:Inter,Pretendard,system-ui,sans-serif;color:#172033;background:#eef3f8;line-height:1.5}"
        "*{box-sizing:border-box}body{margin:0}main{max-width:1120px;margin:auto;padding:32px 24px 64px}"
        "header{border-radius:20px;background:#14243a;color:#f8fafc;padding:28px}header>p:first-child{color:#7dd3fc}"
        "header h1{font-size:clamp(2rem,5vw,4rem);line-height:1;margin:10px 0 18px}"
        "nav{display:flex;gap:8px;overflow:auto;padding:20px 0}nav span{white-space:nowrap;border-radius:999px;"
        "background:#dbeafe;color:#1e3a5f;padding:7px 12px;font-size:.8rem;font-weight:700}"
        "section,aside{margin-top:16px;border:1px solid #cbd5e1;border-radius:16px;background:white;padding:22px}"
        "article{display:grid;gap:14px}form{border-top:1px solid #e2e8f0;padding-top:16px;display:grid;gap:10px}"
        "dl{display:grid;gap:8px}dl div{display:grid;grid-template-columns:minmax(110px,1fr) 2fr;gap:12px}"
        "dt{color:#64748b;font-size:.8rem}dd{margin:0;overflow-wrap:anywhere}"
        ".permission{margin:0;color:#64748b;font-size:.78rem}"
        "label{display:grid;gap:5px;font-size:.82rem;font-weight:650}input{border:1px solid #94a3b8;border-radius:8px;"
        "padding:10px;font:inherit}button{justify-self:start;border:0;border-radius:9px;background:#0369a1;color:white;"
        "padding:10px 15px;font:inherit;font-weight:750;cursor:pointer}button:focus-visible,input:focus-visible{"
        "outline:3px solid #7dd3fc}[role=status]{position:sticky;bottom:16px;border-radius:10px;background:#0f172a;"
        "color:white;padding:12px 16px}pre{overflow:auto;background:#f1f5f9;padding:14px;border-radius:10px}"
        "@media(min-width:800px){main{display:grid;grid-template-columns:2fr 1fr;gap:18px}header,nav,[role=status]{"
        "grid-column:1/-1}section,aside{margin-top:0}}"
    )


def _osdk_package_json(package_name: str) -> str:
    value = {
        "name": package_name,
        "version": "0.1.0",
        "private": True,
        "type": "module",
        "exports": {".": "./src/generated.ts", "./react": "./src/react.ts"},
        "peerDependencies": {"react": ">=18"},
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _strict_checker_source(package_name: str) -> str:
    return _STRICT_CHECKER_SOURCE.replace("__PACKAGE_NAME__", package_name).replace("{{", "{").replace("}}", "}")


def _runtime_contract_source() -> str:
    return (
        'import { readFileSync } from "node:fs";\n'
        'const runtime = readFileSync("packages/application-osdk/src/runtime.ts", "utf8");\n'
        'const react = readFileSync("packages/application-osdk/src/react.ts", "utf8");\n'
        'for (const value of ["/api/objects/", "/api/actions/", "/api/functions/", "Idempotency-Key", '
        '"credentials: \\"include\\""]) '
        "if (!runtime.includes(value)) throw new Error(`portable OSDK runtime is missing ${value}`);\n"
        'if (!react.includes("PilotApplicationProvider")) throw new Error("application provider is missing");\n'
        'process.stdout.write("portable Domain OS runtime contract passed\\n");\n'
    )


def _package_name(plan: JsonObject) -> str:
    return required_text(_mapping(plan.get("consumerOsdk"), "consumerOsdk"), "packageName")


def _mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise FdePlatformToolError("schema_invalid", f"{field} must be an object")
    return {str(name): item for name, item in value.items()}


__all__ = ["ci_workflow", "consumer_osdk_plan", "react_files"]
