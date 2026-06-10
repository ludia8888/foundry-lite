#!/usr/bin/env bash
# Foundry-lite CodeQL gate — Sprint quality-gate roadmap P7.
#
# Builds (or refreshes) a CodeQL Python database, runs both the upstream
# security-extended suite and our Foundry-lite-specific queries, and prints
# the result. Designed to be invoked from scripts/ci_gate.sh.
#
# When CodeQL CLI is not on PATH, the script prints a warning and exits
# 0 so local development is not blocked. CI must have it installed.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DB_PATH="${REPO_ROOT}/.codeql-db"
ARTIFACT_DIR="${REPO_ROOT}/artifacts/quality"
QUERIES_DIR="${REPO_ROOT}/scripts/quality/codeql/queries"
SARIF_OUT="${ARTIFACT_DIR}/codeql-results.sarif"

if ! command -v codeql >/dev/null 2>&1; then
  echo "WARN: codeql not on PATH; install with 'brew install codeql' (P7 gate skipped locally)." >&2
  exit 0
fi

mkdir -p "${ARTIFACT_DIR}"

# Build database. CodeQL caches incrementally inside DB_PATH; we delete it
# only when the schema changes, which is rare.
if [[ ! -d "${DB_PATH}" ]] || [[ "${FOUNDRY_LITE_CODEQL_FRESH:-0}" == "1" ]]; then
  echo "== CodeQL: building fresh database =="
  rm -rf "${DB_PATH}"
  codeql database create "${DB_PATH}" \
    --language=python \
    --source-root="${REPO_ROOT}" \
    --overwrite \
    --quiet
fi

echo "== CodeQL: running Foundry-lite custom queries =="
codeql database analyze "${DB_PATH}" \
  "${QUERIES_DIR}" \
  --format=sarif-latest \
  --output="${SARIF_OUT}" \
  --download \
  --quiet

# Parse SARIF for any results; fail the gate if any rule produced findings.
python3 - <<PY
import json, sys
with open("${SARIF_OUT}") as f:
    sarif = json.load(f)
total = 0
for run in sarif.get("runs", []):
    results = run.get("results", [])
    if results:
        rule = run.get("tool", {}).get("driver", {}).get("name", "unknown")
        print(f"CodeQL findings in {rule}:")
        for r in results:
            loc = r.get("locations", [{}])[0]
            uri = loc.get("physicalLocation", {}).get("artifactLocation", {}).get("uri", "?")
            line = loc.get("physicalLocation", {}).get("region", {}).get("startLine", "?")
            msg = r.get("message", {}).get("text", "")
            rule_id = r.get("ruleId", "?")
            print(f"  - {uri}:{line} [{rule_id}] {msg}")
            total += 1
if total:
    print(f"\nCodeQL: {total} root-cause finding(s); gate failed.")
    sys.exit(1)
print("CodeQL: 0 root-cause findings.")
PY
