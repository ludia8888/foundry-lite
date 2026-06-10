#!/usr/bin/env bash
set -euo pipefail

export PYTHONPATH=".:libs:apps/cli:apps/api:apps/worker"
mkdir -p artifacts/coverage artifacts/demo artifacts/test-results artifacts/quality

if [[ "${FOUNDRY_LITE_SKIP_POSTGRES_CONTRACTS:-}" == "1" ]]; then
  echo "ERROR: FOUNDRY_LITE_SKIP_POSTGRES_CONTRACTS=1 is local-only; ci:gate requires PostgreSQL contract suites." >&2
  exit 1
fi

echo "== Static: Ruff lint =="
uv run ruff check .

echo "== Static: Ruff format =="
uv run ruff format --check .

echo "== Static: mypy =="
uv run mypy libs apps/api apps/cli apps/worker scripts

echo "== Static: pyright =="
uv run pyright

echo "== Static: dependency graph and layer rules =="
uv run python scripts/quality/check_dependency_graph.py

echo "== Static: import-linter layered architecture contracts =="
uv run lint-imports --config .importlinter

echo "== Static: infra import and service collaborator boundaries =="
uv run python scripts/quality/check_infra_import_boundary.py --max-application-imports 0
uv run python scripts/quality/check_service_dependencies.py
uv run python scripts/quality/check_service_call_graph.py --max-depth 7 --max-fan-out 10

echo "== Static: application module size guard =="
uv run python scripts/quality/check_application_module_size.py --max-lines 500

echo "== Static: no skipped/flaky/xfail release bypasses =="
uv run python scripts/quality/check_no_test_bypasses.py

echo "== Static: private test reference baseline =="
uv run python scripts/quality/check_private_test_references.py --max-count 0

echo "== Static: Bandit security scan =="
uv run bandit -c pyproject.toml -r libs apps scripts

echo "== Static: Semgrep design-pattern rules =="
mkdir -p artifacts/quality
uv run semgrep --config scripts/quality/semgrep-rules/foundry-lite.yml \
  --error --metrics off --quiet \
  --json --output artifacts/quality/semgrep.json \
  .

echo "== Static: gitleaks secret scan =="
if command -v gitleaks >/dev/null 2>&1; then
  gitleaks dir --no-banner --config .gitleaks.toml --report-path artifacts/quality/gitleaks.json --report-format json
else
  echo "WARN: gitleaks not on PATH; install with 'brew install gitleaks' (P9 gate skipped locally)." >&2
fi

echo "== Static: pip-audit dependency vulnerability scan =="
# pyjwt PYSEC-2026-175/177/178/179 are pinned <2.13 by semgrep 1.165 (dev-only
# transitive). We do not import pyjwt directly; we ignore these CVEs at the
# audit boundary and re-evaluate when semgrep lifts the pin or we ship a
# dedicated JwtAuthProvider that brings pyjwt in via the application layer.
uv run pip-audit --progress-spinner off \
  --ignore-vuln PYSEC-2026-175 \
  --ignore-vuln PYSEC-2026-177 \
  --ignore-vuln PYSEC-2026-178 \
  --ignore-vuln PYSEC-2026-179

echo "== Static: Radon complexity =="
uv run radon cc libs apps scripts -s -a
uv run radon cc libs apps scripts -s -a -j -O artifacts/quality/radon_cc.json

echo "== Static: Vulture dead code (80% confidence baseline) =="
# Vulture finds unreachable functions/variables/imports. We start at the
# 80% confidence threshold because lower confidence levels report Protocol
# methods and public API surfaces. Future P10 work narrows this with
# per-file allowlist as the project shrinks.
uv run vulture libs/foundry_lite --min-confidence 80

echo "== Static: Interrogate docstring coverage (baseline 25%) =="
# Interrogate enforces a minimum docstring coverage. We pin the current
# baseline (25%) and require monotonic increase: lowering it requires
# a docs/quality-gate-roadmap.md amendment.
uv run interrogate libs/foundry_lite --fail-under 25 --quiet

echo "== Static: Xenon complexity gate, max block B =="
uv run xenon --max-absolute B --max-modules B --max-average A libs apps scripts

echo "== Dynamic: pytest with branch coverage =="
# pytest-randomly is auto-loaded and shuffles test order per run, exposing
# hidden inter-test dependencies (state leaks across fixtures, shared module
# globals, etc.). A consistent --randomly-seed is logged so failures can be
# reproduced exactly.
uv run pytest tests \
  --cov=libs/foundry_lite \
  --cov-branch \
  --cov-fail-under=95 \
  --junitxml=artifacts/test-results/pytest.xml

echo "== Dynamic: pytest stability under random order + parallel =="
# Re-run the suite without coverage instrumentation under pytest-xdist to
# surface race conditions and shared-resource contention that the serial
# coverage run cannot expose. A fresh random seed each run is the point.
uv run pytest tests -n auto --no-header -q

echo "== Dynamic: public callable smoke coverage gate =="
uv run coverage json -o artifacts/coverage/coverage.json
uv run python scripts/quality/check_public_api_coverage.py artifacts/coverage/coverage.json --threshold 95

echo "== Dynamic: supply-chain demo smoke =="
rm -rf .foundry-lite-ci-smoke
FOUNDRY_LITE_HOME=.foundry-lite-ci-smoke pnpm --silent demo:supply-chain --fresh > artifacts/demo/supply-chain.json
uv run python -m json.tool artifacts/demo/supply-chain.json > /dev/null

echo "== Dynamic: runtime diagnostics =="
rm -rf .foundry-lite-diagnostics
uv run python scripts/diagnostics/run_runtime_diagnostics.py

echo "== Dynamic: Playwright E2E =="
pnpm exec playwright test

echo "Foundry-lite CI gate passed."
