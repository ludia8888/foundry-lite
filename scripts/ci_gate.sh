#!/usr/bin/env bash
set -euo pipefail

export PYTHONPATH=".:libs:apps/cli:apps/api:apps/worker"
mkdir -p artifacts/coverage artifacts/demo artifacts/test-results artifacts/quality

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

echo "== Static: infra import and mixin conflict boundaries =="
uv run python scripts/quality/check_infra_import_boundary.py --max-application-imports 28
uv run python scripts/quality/check_mixin_method_conflicts.py

echo "== Static: application module size guard =="
uv run python scripts/quality/check_application_module_size.py --max-lines 500

echo "== Static: no skipped/flaky/xfail release bypasses =="
uv run python scripts/quality/check_no_test_bypasses.py

echo "== Static: private test reference baseline =="
uv run python scripts/quality/check_private_test_references.py --max-count 17

echo "== Static: Bandit security scan =="
uv run bandit -c pyproject.toml -r libs apps scripts

echo "== Static: pip-audit dependency vulnerability scan =="
uv run pip-audit --progress-spinner off

echo "== Static: Radon complexity =="
uv run radon cc libs apps scripts -s -a
uv run radon cc libs apps scripts -s -a -j -O artifacts/quality/radon_cc.json

echo "== Static: Xenon complexity gate, max block B =="
uv run xenon --max-absolute B --max-modules B --max-average A libs apps scripts

echo "== Dynamic: pytest with branch coverage =="
uv run pytest tests \
  --cov=libs/foundry_lite \
  --cov-branch \
  --cov-fail-under=95 \
  --junitxml=artifacts/test-results/pytest.xml

echo "== Dynamic: public callable smoke coverage gate =="
uv run coverage json -o artifacts/coverage/coverage.json
uv run python scripts/quality/check_public_api_coverage.py artifacts/coverage/coverage.json --threshold 95

echo "== Dynamic: supply-chain demo smoke =="
rm -rf .foundry-lite-ci-smoke
FOUNDRY_LITE_HOME=.foundry-lite-ci-smoke pnpm demo:supply-chain > artifacts/demo/supply-chain.json

echo "== Dynamic: runtime diagnostics =="
rm -rf .foundry-lite-diagnostics
uv run python scripts/diagnostics/run_runtime_diagnostics.py

echo "== Dynamic: Playwright E2E =="
pnpm exec playwright test

echo "Foundry-lite CI gate passed."
