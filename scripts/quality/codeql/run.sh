#!/usr/bin/env bash
# Foundry-lite CodeQL gate — Sprint quality-gate roadmap P7.
#
# Builds (or refreshes) a CodeQL Python database and runs the repo-local
# Foundry-lite-specific queries. This is a manual local debugging helper only:
# the required release gate is .github/workflows/codeql.yml, because fresh DB
# builds are too expensive for every local ci:gate run.
#
# When CodeQL CLI is not on PATH, the script prints a warning and exits 0 only
# for local debugging. CI/release evidence must never silently skip P7.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DB_PATH="${REPO_ROOT}/.codeql-db"
ARTIFACT_DIR="${REPO_ROOT}/artifacts/quality"
QUERIES_DIR="${REPO_ROOT}/scripts/quality/codeql/queries"
SARIF_OUT="${ARTIFACT_DIR}/codeql-results.sarif"

if ! command -v codeql >/dev/null 2>&1; then
  if [[ "${CI:-}" == "true" || "${FOUNDRY_LITE_STRICT_EXTERNAL_TOOLS:-0}" == "1" ]]; then
    echo "ERROR: codeql not on PATH; CI/release evidence cannot skip the P7 data-flow gate." >&2
    exit 1
  fi
  echo "WARN: codeql not on PATH; install with 'brew install codeql' (manual P7 debug skipped locally)." >&2
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

python3 "${REPO_ROOT}/scripts/quality/codeql/fail_on_sarif_findings.py" "${SARIF_OUT}"
