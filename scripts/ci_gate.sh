#!/usr/bin/env bash
set -euo pipefail

export PYTHONPATH=".:libs:apps/cli:apps/api:apps/worker"
mkdir -p artifacts/coverage artifacts/demo artifacts/test-results artifacts/quality

# CodeQL P7 is intentionally not run from this local/release shell gate.
# Fresh Python DB builds take minutes and would slow every local feedback loop;
# .github/workflows/codeql.yml owns that heavy data-flow gate and fails on SARIF
# findings after each push/PR.

usage() {
  cat <<'EOF'
Usage: bash scripts/ci_gate.sh [all|static|coverage|flaky|runtime|e2e]

Lane mode lets GitHub Actions run the same release evidence in parallel without
weakening any threshold:
  all      local/full serial release gate, matching the historical pnpm ci:gate
  static   format, typing, architecture, security, complexity, doc drift gates
  coverage full pytest branch coverage plus layer/public API coverage gates
  flaky    three repeated random + parallel pytest runs
  runtime  demo, lineage, audit, outbox, correctness, trace, diagnostics gates
  e2e      Playwright browser E2E
EOF
}

ensure_release_postgres_contracts_enabled() {
  if [[ "${FOUNDRY_LITE_SKIP_POSTGRES_CONTRACTS:-}" == "1" ]]; then
    echo "ERROR: FOUNDRY_LITE_SKIP_POSTGRES_CONTRACTS=1 is local-only; ci:gate requires PostgreSQL contract suites." >&2
    exit 1
  fi
}

maybe_run_testcontainers_preflight() {
  if [[ "${FOUNDRY_LITE_TESTCONTAINERS_PREFLIGHT_DONE:-0}" == "1" ]]; then
    return
  fi

  echo "== Preflight: Testcontainers Docker access =="
  uv run python scripts/quality/check_testcontainers_preflight.py
  FOUNDRY_LITE_TESTCONTAINERS_PREFLIGHT_DONE=1
}

run_static_gate() {
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

  echo "== Static: Tach module dependency contracts =="
  uv run tach check --dependencies

  echo "== Static: infra import and service collaborator boundaries =="
  uv run python scripts/quality/check_infra_import_boundary.py --max-application-imports 0
  uv run python scripts/quality/check_service_dependencies.py
  uv run python scripts/quality/check_service_call_graph.py --max-depth 7 --max-fan-out 10

  echo "== Static: application module size guard =="
  uv run python scripts/quality/check_application_module_size.py --max-lines 500

  echo "== Static: application function length hard limit =="
  uv run python scripts/quality/check_function_length.py

  echo "== Static: boolean naming =="
  uv run python scripts/quality/check_boolean_naming.py

  echo "== Static: dict[str, Any] signature budget =="
  uv run python scripts/quality/check_dict_any_budget.py

  echo "== Static: application broad Any boundary =="
  uv run python scripts/quality/check_application_any_budget.py

  echo "== Static: API router layer purity =="
  uv run python scripts/quality/check_router_layer_purity.py

  echo "== Static: query side-effect boundary =="
  uv run python scripts/quality/check_query_side_effects.py

  echo "== Static: repository no-business boundary =="
  uv run python scripts/quality/check_repository_no_business.py

  echo "== Static: tenant-scoped repository writes =="
  uv run python scripts/quality/check_tenant_write_guard.py

  echo "== Static: contract test coverage per port =="
  uv run python scripts/quality/check_contract_test_per_port.py

  echo "== Static: strategy/specification direct tests =="
  uv run python scripts/quality/check_strategy_specification_tests.py

  echo "== Static: required integration scenario markers =="
  uv run python scripts/quality/check_integration_scenario_markers.py

  echo "== Static: regression test per bug-fix commit =="
  uv run python scripts/quality/check_regression_test_per_bugfix.py

  echo "== Static: PR root-cause evidence =="
  uv run python scripts/quality/check_pr_root_cause_section.py

  echo "== Static: current-state documentation drift =="
  uv run python scripts/quality/check_doc_drift.py

  echo "== Static: generated TypeScript SDK drift =="
  uv run python scripts/generate_sdk_ts.py --check

  echo "== Static: DB schema revision guard =="
  uv run python scripts/quality/check_schema_revision_guard.py

  echo "== Static: audit on public service mutations =="
  uv run python scripts/quality/check_audit_on_mutation.py

  echo "== Static: transaction outbox/audit pairing =="
  uv run python scripts/quality/check_transaction_outbox_pair.py

  echo "== Static: action idempotency contract =="
  uv run python scripts/quality/check_idempotency_on_action.py

  echo "== Static: API error response request_id =="
  uv run python scripts/quality/check_error_response_has_request_id.py

  echo "== Static: log trace correlation keys =="
  uv run python scripts/quality/check_log_has_trace_keys.py

  echo "== Static: required operational metrics exposed =="
  uv run python scripts/quality/check_metrics_exposed.py

  echo "== Static: adapter failure taxonomy contract =="
  uv run python scripts/quality/check_adapter_failure_taxonomy.py

  echo "== Static: no skipped/flaky/xfail release bypasses =="
  uv run python scripts/quality/check_no_test_bypasses.py
  uv run python scripts/quality/check_no_test_sleep.py
  uv run python scripts/quality/check_pragma_no_cover_budget.py --baseline 0

  echo "== Static: private test reference baseline =="
  uv run python scripts/quality/check_private_test_references.py --max-count 0

  echo "== Static: Bandit security scan =="
  uv run bandit -c pyproject.toml -r libs apps scripts

  echo "== Static: Semgrep design-pattern rules =="
  mkdir -p artifacts/quality
  export SSL_CERT_FILE="${SSL_CERT_FILE:-$(uv run python -c 'import certifi; print(certifi.where())')}"
  uv run semgrep --config scripts/quality/semgrep-rules/foundry-lite.yml \
    --error --metrics off --quiet \
    --json --output artifacts/quality/semgrep.json \
    .

  echo "== Static: AST-grep structural rules =="
  pnpm exec sg scan -c sgconfig.yml

  echo "== Static: gitleaks secret scan =="
  if command -v gitleaks >/dev/null 2>&1; then
    gitleaks dir --no-banner --config .gitleaks.toml --report-path artifacts/quality/gitleaks.json --report-format json
  elif [[ "${CI:-}" == "true" || "${FOUNDRY_LITE_STRICT_EXTERNAL_TOOLS:-0}" == "1" ]]; then
    echo "ERROR: gitleaks not on PATH; CI/release evidence cannot skip the P9 secret scan." >&2
    exit 1
  else
    echo "WARN: gitleaks not on PATH; install with 'brew install gitleaks' (P9 gate skipped locally only)." >&2
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
}

run_coverage_gate() {
  maybe_run_testcontainers_preflight

  echo "== Dynamic: pytest with branch coverage =="
  # pytest-randomly is auto-loaded and shuffles test order per run, exposing
  # hidden inter-test dependencies (state leaks across fixtures, shared module
  # globals, etc.). A consistent --randomly-seed is logged so failures can be
  # reproduced exactly.
  uv run pytest tests \
    --cov=libs/foundry_lite \
    --cov=apps/api \
    --cov=apps/cli \
    --cov=apps/worker \
    --cov-branch \
    --cov-fail-under=95 \
    --junitxml=artifacts/test-results/pytest.xml

  echo "== Dynamic: public callable smoke coverage gate =="
  uv run coverage json -o artifacts/coverage/coverage.json
  uv run python scripts/quality/check_tier_coverage_by_layer.py artifacts/coverage/coverage.json --threshold 95
  uv run python scripts/quality/check_public_api_coverage.py artifacts/coverage/coverage.json --threshold 95
}

run_flaky_gate() {
  maybe_run_testcontainers_preflight

  echo "== Dynamic: flaky pytest detector (3 repeated random + parallel runs) =="
  # Re-run the suite without coverage instrumentation under pytest-xdist three
  # times. pytest-randomly chooses a fresh seed each run, so order coupling,
  # shared-resource races, or unstable collection cannot be waved through as
  # "passed once".
  uv run python scripts/quality/check_flaky_detector.py \
    --iterations 3 \
    --command "uv run pytest tests -n auto --no-header -q"
}

run_runtime_gate() {
  echo "== Dynamic: supply-chain demo smoke =="
  rm -rf .foundry-lite-ci-smoke
  FOUNDRY_LITE_HOME=.foundry-lite-ci-smoke pnpm --silent demo:supply-chain --fresh > artifacts/demo/supply-chain.json
  uv run python -m json.tool artifacts/demo/supply-chain.json > /dev/null

  echo "== Dynamic: OpenLineage lineage consistency =="
  uv run python scripts/quality/check_openlineage_dynamic_lineage.py --storage-root .foundry-lite-ci-smoke

  echo "== Dynamic: runtime audit count consistency =="
  uv run python scripts/quality/check_audit_count_runtime.py --storage-root .foundry-lite-ci-smoke

  echo "== Dynamic: outbox consistency =="
  uv run python scripts/quality/check_outbox_consistency.py --storage-root .foundry-lite-ci-smoke

  echo "== Dynamic: MVP data correctness =="
  uv run python scripts/quality/check_mvp_data_correctness.py --storage-root .foundry-lite-ci-smoke

  echo "== Dynamic: MVP performance smoke =="
  uv run python scripts/quality/check_mvp_performance_smoke.py --profile ci

  echo "== Dynamic: trace continuity consistency =="
  uv run python scripts/quality/check_trace_continuity.py --storage-root .foundry-lite-trace-gate

  echo "== Dynamic: adapter error trace keys =="
  uv run python scripts/quality/check_adapter_error_trace_keys.py --storage-root .foundry-lite-adapter-error-gate

  echo "== Dynamic: failed mutation state consistency =="
  uv run python scripts/quality/check_failed_mutation_state_runtime.py --storage-root .foundry-lite-failure-state-gate

  echo "== Dynamic: runtime diagnostics =="
  rm -rf .foundry-lite-diagnostics
  uv run python scripts/diagnostics/run_runtime_diagnostics.py
}

run_e2e_gate() {
  echo "== Dynamic: Playwright E2E =="
  pnpm exec playwright test
}

run_all_gate() {
  maybe_run_testcontainers_preflight
  run_static_gate
  run_coverage_gate
  run_flaky_gate
  run_runtime_gate
  run_e2e_gate
}

main() {
  local lane="${1:-all}"

  if [[ "$lane" == "-h" || "$lane" == "--help" ]]; then
    usage
    return 0
  fi

  ensure_release_postgres_contracts_enabled

  case "$lane" in
    all)
      run_all_gate
      echo "Foundry-lite CI gate passed."
      ;;
    static)
      run_static_gate
      echo "Foundry-lite static quality lane passed."
      ;;
    coverage)
      run_coverage_gate
      echo "Foundry-lite coverage quality lane passed."
      ;;
    flaky)
      run_flaky_gate
      echo "Foundry-lite flaky quality lane passed."
      ;;
    runtime)
      run_runtime_gate
      echo "Foundry-lite runtime quality lane passed."
      ;;
    e2e)
      run_e2e_gate
      echo "Foundry-lite E2E quality lane passed."
      ;;
    *)
      usage >&2
      echo "ERROR: unknown ci gate lane: $lane" >&2
      return 2
      ;;
  esac
}

main "$@"
