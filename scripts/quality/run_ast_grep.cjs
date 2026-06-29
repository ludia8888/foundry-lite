#!/usr/bin/env node

const { spawnSync } = require("node:child_process");
const { resolveBinaryPath } = require("@ast-grep/cli/postinstall.js");

const binaryPath = resolveBinaryPath();

if (!binaryPath) {
  console.error("Failed to locate @ast-grep/cli native binary.");
  process.exit(1);
}

const result = spawnSync(binaryPath, process.argv.slice(2), {
  stdio: "inherit",
});

if (result.error) {
  console.error(`Failed to run @ast-grep/cli: ${result.error.message}`);
  process.exit(1);
}

if (result.signal) {
  process.kill(process.pid, result.signal);
}

process.exit(result.status ?? 1);
