// Trusted ontology-function runner for TypeScript, intended to execute only inside a sandbox.
//
// Deliberately the same contract as python_function_runner.py: a manifest path in, a result path
// out, typed failure categories rather than a stack trace, and exception messages reduced to a
// sha256 digest because user code can put tenant data in an error string. The host reads the
// result file and never the process output, so a fatal exit must still leave one behind.
//
// TypeScript is transpiled here rather than on the host. Palantir compiles in a code repository
// and deploys the built artifact; transpiling in the sandbox keeps the same "source in, built
// output executed" shape without a host round trip or a network fetch. What it does not do is
// type-check: types are stripped, exactly as a bundler would, so a type error surfaces at
// runtime as a user_code_error rather than being caught before the function is ever registered.

import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { Script, createContext } from "node:vm";

const RESULT_SCHEMA_VERSION = 1;
const require = createRequire(import.meta.url);

// The value shapes a function may return, keyed by the declared ontology output type. `object`
// and `objectSet` are absent on purpose: a function does not hand objects back to its caller.
// Editing them is `ontology_edit_batch`, a proposal the Action committer re-validates.
const OUTPUT_VALIDATORS = {
  boolean: (value) => typeof value === "boolean",
  string: (value) => typeof value === "string",
  integer: (value) => Number.isInteger(value),
  long: (value) => Number.isInteger(value),
  float: (value) => typeof value === "number" && Number.isFinite(value),
  decimal: (value) => (typeof value === "number" && Number.isFinite(value)) || typeof value === "string",
  date: (value) => typeof value === "string",
  timestamp: (value) => typeof value === "string",
  struct: (value) => isPlainObject(value),
  array: (value) => Array.isArray(value),
  ontology_edit_batch: (value) => isPlainObject(value) && Array.isArray(value.edits) && value.edits.length > 0,
};

class RunnerFailure extends Error {
  constructor(failureType, cause) {
    super(failureType);
    this.failureType = failureType;
    this.exceptionType = cause?.constructor?.name ?? "Error";
    this.messageSha256 = createHash("sha256").update(String(cause?.message ?? cause ?? "")).digest("hex");
  }
}

function isPlainObject(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function transpiled(source) {
  // Transpile only, no type checking -- the same trade a bundler makes. `isolatedModules` makes
  // the compiler reject constructs it cannot strip file-by-file rather than emit something
  // subtly different from what a full program build would produce.
  const ts = require("typescript");
  const output = ts.transpileModule(source, {
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.CommonJS,
      isolatedModules: true,
    },
    reportDiagnostics: true,
  });
  const blocking = (output.diagnostics ?? []).filter((item) => item.category === ts.DiagnosticCategory.Error);
  if (blocking.length > 0) {
    throw new RunnerFailure("user_code_error", new Error(String(blocking[0].messageText)));
  }
  return output.outputText;
}

function loadedEntrypoint(source, entrypoint) {
  const moduleExports = {};
  const sandbox = createContext({
    exports: moduleExports,
    module: { exports: moduleExports },
    // No require, no process, no fetch. The container already has no network; withholding the
    // handles as well means user code fails at the call site instead of at the socket.
    console: { log() {}, error() {} },
  });
  try {
    new Script(transpiled(source), { filename: "function.ts" }).runInContext(sandbox, { timeout: 30_000 });
  } catch (error) {
    if (error instanceof RunnerFailure) throw error;
    throw new RunnerFailure("user_code_error", error);
  }
  const resolved = sandbox.module.exports[entrypoint] ?? sandbox.exports[entrypoint];
  if (typeof resolved !== "function") {
    throw new RunnerFailure("runner_contract_error", new Error(`function source does not define ${entrypoint}`));
  }
  return resolved;
}

function invoked(fn, inputs) {
  // Positional, in the order the platform declared them: TypeScript has no keyword arguments,
  // so the manifest carries argument order and the host owns it.
  try {
    return fn(...inputs);
  } catch (error) {
    throw new RunnerFailure("user_code_error", error);
  }
}

function requireOutputType(output, outputType) {
  const validator = OUTPUT_VALIDATORS[outputType];
  if (validator === undefined) {
    throw new RunnerFailure("output_validation_error", new Error(`unsupported output type ${outputType}`));
  }
  if (!validator(output)) {
    throw new RunnerFailure("output_validation_error", new Error(`declared output type is ${outputType}`));
  }
  try {
    JSON.stringify(output);
  } catch (error) {
    throw new RunnerFailure("output_validation_error", error);
  }
}

export function executeManifest(manifest) {
  const fn = loadedEntrypoint(text(manifest, "source"), text(manifest, "entrypoint"));
  if (!Array.isArray(manifest.argumentOrder)) {
    throw new RunnerFailure("runner_contract_error", new Error("manifest argumentOrder must be an array"));
  }
  const inputs = manifest.inputs;
  if (!isPlainObject(inputs)) {
    throw new RunnerFailure("runner_contract_error", new Error("manifest inputs must be an object"));
  }
  const output = invoked(fn, manifest.argumentOrder.map((name) => inputs[name]));
  requireOutputType(output, text(manifest, "outputType"));
  return { schemaVersion: RESULT_SCHEMA_VERSION, status: "succeeded", output };
}

function text(manifest, key) {
  const value = manifest[key];
  if (typeof value !== "string") {
    throw new RunnerFailure("runner_contract_error", new Error(`manifest ${key} must be a string`));
  }
  return value;
}

function failureResult(failure) {
  return {
    schemaVersion: RESULT_SCHEMA_VERSION,
    status: "failed",
    failureType: failure.failureType,
    exceptionType: failure.exceptionType,
    exceptionMessageSha256: failure.messageSha256,
  };
}

export function main(argv) {
  if (argv.length !== 2) return 64;
  let result;
  try {
    result = executeManifest(JSON.parse(readFileSync(argv[0], "utf8")));
  } catch (error) {
    result = failureResult(error instanceof RunnerFailure ? error : new RunnerFailure("runner_contract_error", error));
  }
  writeFileSync(argv[1], JSON.stringify(result), "utf8");
  return result.status === "succeeded" ? 0 : 1;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  process.exitCode = main(process.argv.slice(2));
}
