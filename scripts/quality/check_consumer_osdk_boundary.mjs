#!/usr/bin/env node

import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { readFileSync, readdirSync, statSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, extname, relative, resolve } from "node:path";
import ts from "typescript";

const SCRIPT_ROOT = resolve(dirname(new URL(import.meta.url).pathname), "../..");
const SOURCE_EXTENSIONS = new Set([".ts", ".tsx", ".js", ".jsx"]);
const LOW_LEVEL_CHAINS = [
  ["objects", "generic"],
  ["functions", "generic"],
  ["actions", "runs"],
  ["actions", "apply"],
  ["actions", "validate"],
];
const LOW_LEVEL_SYMBOLS = new Set([
  "FoundryLiteGeneratedClient",
  "createFoundryLiteClient",
  "useFoundryLiteClient",
  "useFoundryLiteSession",
]);

function argumentsMap(argv) {
  const values = new Map();
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (!key.startsWith("--")) continue;
    const next = argv[index + 1];
    if (next && !next.startsWith("--")) {
      values.set(key, next);
      index += 1;
    } else {
      values.set(key, true);
    }
  }
  return values;
}

function readJson(path) {
  return JSON.parse(readFileSync(path, "utf8"));
}

function stableValue(value) {
  if (Array.isArray(value)) return value.map(stableValue);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, stableValue(value[key])]));
  }
  return value;
}

function sha256(value) {
  return `sha256:${createHash("sha256").update(value).digest("hex")}`;
}

function sourceFiles(root) {
  if (!statSync(root).isDirectory()) return [root];
  const files = [];
  for (const name of readdirSync(root).sort()) {
    if (name === "node_modules" || name.startsWith(".")) continue;
    const path = resolve(root, name);
    if (statSync(path).isDirectory()) files.push(...sourceFiles(path));
    else if (SOURCE_EXTENSIONS.has(extname(path))) files.push(path);
  }
  return files;
}

function sourceLocation(source, node) {
  const position = source.getLineAndCharacterOfPosition(node.getStart(source));
  return { line: position.line + 1, column: position.character + 1 };
}

function violation(source, node, code, message, workspaceRoot) {
  return {
    code,
    file: relative(workspaceRoot, source.fileName),
    ...sourceLocation(source, node),
    message,
  };
}

function importModuleName(node) {
  if (ts.isImportDeclaration(node) && ts.isStringLiteral(node.moduleSpecifier)) return node.moduleSpecifier.text;
  if (ts.isCallExpression(node) && node.expression.kind === ts.SyntaxKind.ImportKeyword) {
    const [argument] = node.arguments;
    return argument && ts.isStringLiteral(argument) ? argument.text : null;
  }
  if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === "require") {
    const [argument] = node.arguments;
    return argument && ts.isStringLiteral(argument) ? argument.text : null;
  }
  return null;
}

function propertyChain(node) {
  const parts = [];
  let current = node;
  while (ts.isPropertyAccessExpression(current) || ts.isElementAccessExpression(current)) {
    if (ts.isPropertyAccessExpression(current)) parts.unshift(current.name.text);
    else if (current.argumentExpression && ts.isStringLiteral(current.argumentExpression)) {
      parts.unshift(current.argumentExpression.text);
    } else return [];
    current = current.expression;
  }
  if (ts.isIdentifier(current)) parts.unshift(current.text);
  return parts;
}

function containsSequence(values, sequence) {
  return values.some((_, index) => sequence.every((value, offset) => values[index + offset] === value));
}

function importedBindings(node) {
  if (!ts.isImportDeclaration(node) || !node.importClause) return [];
  const bindings = [];
  if (node.importClause.name) bindings.push(node.importClause.name.text);
  const named = node.importClause.namedBindings;
  if (named && ts.isNamedImports(named)) {
    for (const element of named.elements) bindings.push((element.propertyName ?? element.name).text);
  }
  return bindings;
}

function scanFile(path, workspaceRoot, appPackageName, isConsumerSource) {
  const sourceText = readFileSync(path, "utf8");
  const kind = extname(path) === ".tsx" ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const source = ts.createSourceFile(path, sourceText, ts.ScriptTarget.Latest, true, kind);
  const violations = [];
  let hasApplicationImport = false;
  function visit(node) {
    const moduleName = importModuleName(node);
    if (moduleName?.startsWith(appPackageName)) hasApplicationImport = true;
    if (moduleName?.startsWith("@foundry-lite/sdk")) {
      if (isConsumerSource) {
        violations.push(violation(
          source, node, "BASE_SDK_IMPORT_FORBIDDEN",
          `strict consumer source must import ${appPackageName}, not ${moduleName}`,
          workspaceRoot,
        ));
      }
      for (const binding of importedBindings(node)) {
        if (LOW_LEVEL_SYMBOLS.has(binding)) {
          violations.push(violation(
            source, node, "LOW_LEVEL_SDK_SYMBOL_FORBIDDEN",
            `${binding} bypasses the high-level application OSDK`, workspaceRoot,
          ));
        }
      }
    }
    if (ts.isPropertyAccessExpression(node) || ts.isElementAccessExpression(node)) {
      const chain = propertyChain(node);
      for (const forbidden of LOW_LEVEL_CHAINS) {
        if (containsSequence(chain, forbidden)) {
          violations.push(violation(
            source, node, "LOW_LEVEL_SDK_CHAIN_FORBIDDEN",
            `${forbidden.join(".")} bypasses typed application resources`, workspaceRoot,
          ));
          break;
        }
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(source);
  return { violations, hasApplicationImport, sourceText };
}

function fileTreeHash(paths, workspaceRoot) {
  const entries = paths.sort().map((path) => ({
    path: relative(workspaceRoot, path),
    hash: sha256(readFileSync(path)),
  }));
  return { entries, hash: sha256(JSON.stringify(entries)) };
}

function gitCommit(workspaceRoot) {
  try {
    return execFileSync("git", ["rev-parse", "HEAD"], { cwd: workspaceRoot, encoding: "utf8" }).trim();
  } catch {
    return null;
  }
}

function auditApplication(app, workspaceRoot) {
  const sourcePaths = app.sourceRoots.flatMap((path) => sourceFiles(resolve(workspaceRoot, path)));
  const packageRoot = resolve(workspaceRoot, app.sdkPackageRoot);
  const packagePaths = sourceFiles(resolve(packageRoot, "src"));
  const violations = [];
  let hasApplicationImport = false;
  for (const path of sourcePaths) {
    const result = scanFile(path, workspaceRoot, app.sdkPackageName, true);
    violations.push(...result.violations);
    hasApplicationImport ||= result.hasApplicationImport;
  }
  for (const path of packagePaths) {
    violations.push(...scanFile(path, workspaceRoot, app.sdkPackageName, false).violations);
  }
  if (!hasApplicationImport) {
    violations.push({
      code: "APPLICATION_OSDK_IMPORT_REQUIRED",
      file: app.sourceRoots[0],
      line: 1,
      column: 1,
      message: `strict consumer source must import ${app.sdkPackageName}`,
    });
  }
  if (app.profile !== "consumer_osdk_strict") {
    violations.push({ code: "STRICT_PROFILE_REQUIRED", file: "config/consumer-osdk-apps.json", line: 1, column: 1,
      message: `${app.applicationId} must use consumer_osdk_strict` });
  }
  if (!Array.isArray(app.exceptions) || app.exceptions.length !== 0) {
    violations.push({ code: "STRICT_EXCEPTION_BUDGET_EXCEEDED", file: "config/consumer-osdk-apps.json", line: 1,
      column: 1, message: `${app.applicationId} must keep a zero-exception budget` });
  }
  const contract = readJson(resolve(workspaceRoot, app.contractPath));
  const sourceTree = fileTreeHash(sourcePaths, workspaceRoot);
  const packageTree = fileTreeHash(packagePaths, workspaceRoot);
  return {
    applicationId: app.applicationId,
    profile: app.profile,
    sdkPackageName: app.sdkPackageName,
    ontologyFingerprint: sha256(JSON.stringify(stableValue(contract))),
    sourceTreeHash: sourceTree.hash,
    sdkPackageTreeHash: packageTree.hash,
    sdkArtifactHash: sha256(readFileSync(resolve(workspaceRoot, app.generatedPath))),
    sourceFiles: sourceTree.entries,
    sdkPackageFiles: packageTree.entries,
    exceptionCount: Array.isArray(app.exceptions) ? app.exceptions.length : -1,
    violationCount: violations.length,
    isCompliant: violations.length === 0,
    violations,
  };
}

function main() {
  const args = argumentsMap(process.argv.slice(2));
  const workspaceRoot = resolve(String(args.get("--workspace-root") ?? SCRIPT_ROOT));
  const inventoryPath = resolve(workspaceRoot, String(args.get("--inventory") ?? "config/consumer-osdk-apps.json"));
  const outputPath = resolve(workspaceRoot, String(
    args.get("--output") ?? "artifacts/quality/consumer-osdk-compliance.json",
  ));
  const inventory = readJson(inventoryPath);
  const applications = inventory.applications.map((app) => auditApplication(app, workspaceRoot));
  const receipt = {
    schemaVersion: "foundry-lite-consumer-osdk-compliance/v1",
    sourceCommit: gitCommit(workspaceRoot),
    inventoryHash: sha256(readFileSync(inventoryPath)),
    applicationCount: applications.length,
    isCompliant: applications.every((app) => app.isCompliant),
    applications,
  };
  if (!args.has("--no-write")) {
    mkdirSync(dirname(outputPath), { recursive: true });
    writeFileSync(outputPath, `${JSON.stringify(receipt, null, 2)}\n`, "utf8");
  }
  process.stdout.write(`${JSON.stringify(receipt, null, 2)}\n`);
  if (!receipt.isCompliant) process.exitCode = 1;
}

main();
