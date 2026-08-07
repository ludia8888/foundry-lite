#!/usr/bin/env bash
set -euo pipefail

export PYTHONPATH=".:libs:apps/cli:apps/api:apps/worker"
mkdir -p artifacts/coverage artifacts/demo artifacts/test-results artifacts/quality

# CodeQL P7 is intentionally not run from this local/release shell gate.
# Fresh Python DB builds take minutes and would slow every local feedback loop;
# .github/workflows/codeql.yml owns that heavy data-flow gate and fails on SARIF
# findings on main/schedule. Pull requests use the bounded diff-security guard
# in pr_fast_gate.py so the merge path does not wait for a fresh CodeQL DB.

usage() {
  cat <<'EOF'
Usage: bash scripts/ci_gate.sh [pr|local|all|static|coverage|flaky|runtime|runtime-full|e2e|release]

Gate modes keep local feedback fast while preserving full release evidence:
  pr       budgeted PR lane: diff security, focused invariants, directly
           related tests, and affected frontend/SDK type contracts in parallel
  local    default developer gate: full static invariants plus Tach impact tests
  all      full serial rehearsal of the parallel CI lanes
  static   format, typing, architecture, security, complexity, doc drift gates
  coverage full pytest branch coverage plus layer/public API coverage gates
  flaky    three repeated random + parallel pytest runs
  runtime  PR runtime lane: ratchet manifest inventory plus demo, lineage,
           audit, outbox, correctness, trace, diagnostics gates and the
           proof-matrix / source-of-truth / operator-evidence contracts
  runtime-full
           full serial per-capability ratchet rehearsal (release / nightly)
  e2e      Playwright browser E2E
  release  full release evidence gate with heavier production-like checks
           (static + coverage + flaky + runtime + 100k/1m perf + contract gates)
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

RUNTIME_GATE_STEP=""
CODE_EXECUTION_IMAGE_READY=0
NODE_CODE_EXECUTION_IMAGE_READY=0
TRAINED_MODEL_IMAGE_READY=0

ensure_code_execution_image() {
  if [[ "${CODE_EXECUTION_IMAGE_READY}" == "1" ]]; then
    return
  fi

  local configured_image="${FOUNDRY_LITE_CODE_EXECUTION_IMAGE:-}"
  if [[ -n "${configured_image}" ]]; then
    echo "== Preflight: isolated Python image ${configured_image} =="
    if ! docker image inspect "${configured_image}" > /dev/null 2>&1; then
      echo "ERROR: configured FOUNDRY_LITE_CODE_EXECUTION_IMAGE is not available locally." >&2
      exit 1
    fi
  else
    echo "== Preflight: build isolated Python execution image =="
    pnpm --silent quality:code-execution-image
  fi
  CODE_EXECUTION_IMAGE_READY=1
}

ensure_node_code_execution_image() {
  if [[ "${NODE_CODE_EXECUTION_IMAGE_READY}" == "1" ]]; then
    return
  fi

  # A second image, not a second policy: TypeScript functions need a Node toolchain the pinned
  # python:slim image does not carry, and adding one would widen the attack surface of every
  # Python transform for a runtime they never use. Confinement flags come from one place.
  local configured_image="${FOUNDRY_LITE_NODE_CODE_EXECUTION_IMAGE:-}"
  if [[ -n "${configured_image}" ]]; then
    echo "== Preflight: isolated Node image ${configured_image} =="
    if ! docker image inspect "${configured_image}" > /dev/null 2>&1; then
      echo "ERROR: configured FOUNDRY_LITE_NODE_CODE_EXECUTION_IMAGE is not available locally." >&2
      exit 1
    fi
  else
    echo "== Preflight: build isolated Node execution image =="
    pnpm --silent quality:node-code-execution-image
  fi
  NODE_CODE_EXECUTION_IMAGE_READY=1
}

ensure_trained_model_image() {
  if [[ "${TRAINED_MODEL_IMAGE_READY}" == "1" ]]; then
    return
  fi

  local configured_image="${FOUNDRY_LITE_TRAINED_MODEL_IMAGE:-}"
  if [[ -n "${configured_image}" ]]; then
    echo "== Preflight: trained-model sidecar image ${configured_image} =="
    if ! docker image inspect "${configured_image}" > /dev/null 2>&1; then
      echo "ERROR: configured FOUNDRY_LITE_TRAINED_MODEL_IMAGE is not available locally." >&2
      exit 1
    fi
  else
    echo "== Preflight: build trained-model sidecar image =="
    pnpm --silent quality:trained-model-sidecar-image
  fi
  TRAINED_MODEL_IMAGE_READY=1
}

run_runtime_step() {
  RUNTIME_GATE_STEP="$1"
  shift
  echo "== Dynamic: ${RUNTIME_GATE_STEP} =="
  "$@"
}

run_runtime_root_cause_summary() {
  echo "== Dynamic: runtime root-cause summary =="
  pnpm --silent quality:runtime-root-cause || true
}

runtime_gate_failed() {
  local exit_code="$1"
  mkdir -p artifacts/quality
  cat > artifacts/quality/runtime_lane_failure.json <<EOF
{"gate":"runtime","status":"FAIL","failedStep":"${RUNTIME_GATE_STEP:-unknown}","exitCode":${exit_code}}
EOF
  run_runtime_root_cause_summary
  exit "${exit_code}"
}

run_runtime_contract_gates() {
  local exit_code=0
  run_runtime_step "proof matrix contract" pnpm --silent quality:proof-matrix || exit_code=$?
  run_runtime_step "source-of-truth contract" pnpm --silent quality:source-of-truth || exit_code=$?
  run_runtime_step "operator evidence contract" pnpm --silent quality:operator-evidence || exit_code=$?
  if [[ "${exit_code}" -ne 0 ]]; then
    RUNTIME_GATE_STEP="runtime evidence contracts"
    run_runtime_root_cause_summary
    exit "${exit_code}"
  fi
}

run_static_gate() {
  echo "== Static: parallel quality lane (scripts/quality/run_static_checks.py) =="
  # The check inventory lives in run_static_checks.py; every check that used to
  # run serially here now runs concurrently, and all failures are reported in
  # one pass instead of fail-fast hiding later breakage.
  uv run python scripts/quality/run_static_checks.py
}

run_coverage_gate() {
  maybe_run_testcontainers_preflight
  ensure_code_execution_image
  ensure_node_code_execution_image

  echo "== Dynamic: pytest with branch coverage =="
  # pytest-randomly is auto-loaded and shuffles test order per run, exposing
  # hidden inter-test dependencies (state leaks across fixtures, shared module
  # globals, etc.). A consistent --randomly-seed is logged so failures can be
  # reproduced exactly.
  #
  # This lane used to run serially, on the theory that sharding shifted per-layer coverage below
  # threshold because import-time-only lines are attributed to whichever worker imports the
  # module first. Measured on tests/unit (3071 tests), that is not what happens: pytest-cov
  # writes one data file per worker and combines them, and a combine is a union, so serial and
  # sharded runs agree exactly -- 64110/78271 lines, 10287/15966 branches, 2135 partial, and
  # identical per-file summaries across all 949 files. Wall clock went 5m30s -> 2m16s.
  #
  # The one line that did differ before this change was apps/api runtime.py:125, the engine
  # dispose inside reset_api_runtime_for_tests. It was never covered by a test: the lifespan
  # suite builds runtimes whose engine is a bare object() with no dispose, so the branch was
  # reached only when some unrelated test left a real engine in the process-global singleton.
  # Serial execution was reporting a test-isolation leak as coverage; sharding exposed it. A
  # test now covers it deliberately, so the difference is gone rather than tolerated.
  #
  # --dist loadfile, not the default per-test distribution: integration tests own module-scoped
  # testcontainers, and scattering one file's tests across workers would build a container per
  # worker. Keeping a file on one worker also measured fastest of the three configurations.
  # 93 is a RATCHET FLOOR, not a target. Measured 93.20% on af2e228a (4763 passed, 36m56s)
  # after PRs #163/#164 landed ~7k statements of Action/MCP/OSDK code without matching tests.
  # The floor exists so the debt cannot grow while it is being paid down; raise it as coverage
  # recovers rather than treating 93 as the standard. The per-layer and public-API gates below
  # still hold their own thresholds.
  uv run pytest tests \
    -n auto \
    --dist loadfile \
    --cov=libs/foundry_lite \
    --cov=apps/api \
    --cov=apps/cli \
    --cov=apps/worker \
    --cov-branch \
    --cov-fail-under=93 \
    --junitxml=artifacts/test-results/pytest.xml

  echo "== Dynamic: public callable smoke coverage gate =="
  uv run coverage json -o artifacts/coverage/coverage.json
  uv run python scripts/quality/check_tier_coverage_by_layer.py artifacts/coverage/coverage.json --threshold 95
  uv run python scripts/quality/check_public_api_coverage.py artifacts/coverage/coverage.json --threshold 95
}

run_flaky_gate() {
  maybe_run_testcontainers_preflight
  ensure_code_execution_image
  ensure_node_code_execution_image

  local iterations="${FOUNDRY_LITE_FLAKY_ITERATIONS:-3}"
  echo "== Dynamic: flaky pytest detector (${iterations} repeated random + parallel runs) =="
  # Re-run the suite without coverage instrumentation under pytest-xdist. The
  # detector injects a fresh pytest-randomly seed per iteration and shares that
  # seed across xdist workers, so order coupling, shared-resource races, or
  # unstable collection cannot be waved through as "passed once". Flakiness is
  # a property of the suite, not of one diff, so the nightly lane raises
  # FOUNDRY_LITE_FLAKY_ITERATIONS for more statistical power than any per-PR
  # rerun could afford (default remains --iterations 3 for release rehearsal).
  uv run python scripts/quality/check_flaky_detector.py \
    --iterations "${iterations}" \
    --command "uv run pytest tests -n auto --no-header -q"
}

run_impact_gate() {
  maybe_run_testcontainers_preflight
  ensure_code_execution_image
  ensure_node_code_execution_image

  echo "== Local: impact-scoped pytest via Tach =="
  # Local feedback should answer "did this change break its reachable tests?"
  # without replaying the full coverage, flaky, runtime, and browser lanes.
  # The full suite remains protected by parallel CI lanes and ci:gate:all.
  uv run pytest tests --tach --no-header -q
}

run_local_gate() {
  run_static_gate
  run_impact_gate
}

run_pr_gate() {
  echo "== Pull request: budgeted fast gate =="
  uv run --no-sync python scripts/quality/pr_fast_gate.py run
}

# PR runtime lane: the full suite already executes once in the coverage lane,
# so per-capability ratchet subsets are verified as *inventory* (collect-only)
# instead of being re-executed serially. The full execution rehearsal lives in
# run_runtime_full_gate (release / nightly).
run_runtime_gate() {
  maybe_run_testcontainers_preflight
  rm -f artifacts/quality/runtime_lane_failure.json
  run_runtime_contract_gates
  trap 'runtime_gate_failed "$?"' ERR

  run_runtime_step "ratchet manifest inventory" uv run python scripts/quality/check_ratchet_manifest.py

  run_runtime_step "SDK request contract" node --experimental-strip-types tests/sdk/request_contract.mjs

  run_runtime_dynamic_steps
  trap - ERR
}

run_runtime_full_gate() {
  maybe_run_testcontainers_preflight
  ensure_code_execution_image
  ensure_node_code_execution_image
  ensure_trained_model_image
  rm -f artifacts/quality/runtime_lane_failure.json
  run_runtime_contract_gates
  trap 'runtime_gate_failed "$?"' ERR

  run_runtime_step "Python code isolation live ratchet" pnpm --silent quality:pipeline-python-isolation-live
  run_runtime_step "Trained-model sidecar live ratchet" pnpm --silent quality:pipeline-trained-model-sidecar-live

  run_runtime_step "Record DLQ replay ratchet" pnpm --silent quality:record-dlq-replay

  run_runtime_step "Transform scheduler worker ratchet" pnpm --silent quality:transform-scheduler

  run_runtime_step "Late-data ratchet" pnpm --silent quality:late-data

  run_runtime_step "Watermark ratchet" pnpm --silent quality:watermark

  run_runtime_step "Multi-file dataset manifest ratchet" pnpm --silent quality:multi-file-dataset

  run_runtime_step "Partition pruning ratchet" pnpm --silent quality:partition-pruning

  run_runtime_step "CDC stream archive ratchet" pnpm --silent quality:cdc-stream-archive

  run_runtime_step "CDC object indexing ratchet" pnpm --silent quality:cdc-object-indexing

  run_runtime_step "CDC continuous worker ratchet" pnpm --silent quality:cdc-continuous-worker

  run_runtime_step "Outbox publisher worker ratchet" pnpm --silent quality:outbox-publisher

  run_runtime_step "Debezium live CDC ratchet" pnpm --silent quality:cdc-live-debezium

  run_runtime_step "Media live OCR ratchet" pnpm --silent quality:media-live-ocr

  run_runtime_step "Media live ASR ratchet" pnpm --silent quality:media-live-asr

  run_runtime_step "Media live video ratchet" pnpm --silent quality:media-live-video

  run_runtime_step "Media live video-frame OCR ratchet" pnpm --silent quality:media-live-video-frames

  run_runtime_step "Media live video-vision ratchet" pnpm --silent quality:media-live-video-vision

  run_runtime_step "Media live embeddings ratchet" pnpm --silent quality:media-live-embeddings

  run_runtime_step "Media live external connector ratchet" pnpm --silent quality:media-live-external

  run_runtime_step "Media active-covered golden pipeline" pnpm --silent quality:media-active-covered

  run_runtime_step "S3 storage ratchet" pnpm --silent quality:s3-storage

  run_runtime_step "Iceberg storage ratchet" pnpm --silent quality:iceberg

  run_runtime_step "Iceberg maintenance ratchet" pnpm --silent quality:iceberg-maintenance

  run_runtime_step "Spark compute ratchet" pnpm --silent quality:spark

  run_runtime_step "Temporal workflow ratchet" pnpm --silent quality:temporal

  run_runtime_step "Temporal engine integration ratchet" pnpm --silent quality:temporal-engine-integration

  run_runtime_step "Pipeline async DAG contract and determinism ratchet" pnpm --silent quality:pipeline-async-dag

  run_runtime_step "Pipeline async DAG two-worker Temporal fault ratchet" pnpm --silent quality:pipeline-async-dag-live

  run_runtime_step "Media workflow Temporal ratchet" pnpm --silent quality:media-workflow-temporal

  run_runtime_step "Action Types v2 IR and atomic edit-plan ratchet" pnpm --silent quality:action-types-v2

  run_runtime_step "Consumer Ontology MCP OAuth, app restriction, and Action approval ratchet" pnpm --silent quality:ontology-mcp

  run_runtime_step "Consumer Ontology MCP official-client PostgreSQL ratchet" pnpm --silent quality:ontology-mcp-live

  run_runtime_step "Action Types two-worker Temporal fault ratchet" pnpm --silent quality:action-types-palantir-live

  run_runtime_step "Action notification policy PostgreSQL and RLS ratchet" pnpm --silent quality:action-notification-policies-live

  run_runtime_step "Action effect two-worker takeover ratchet" pnpm --silent quality:action-effect-operations-live

  run_runtime_step "Action monitoring live Kafka alert ratchet" pnpm --silent quality:action-monitoring-live

  run_runtime_step "External writeback outcome ratchet" pnpm --silent quality:external-writeback

  run_runtime_step "Action writeback retryable ratchet" pnpm --silent quality:action-writeback-retryable

  run_runtime_step "Action writeback approval release ratchet" pnpm --silent quality:action-writeback-approval-release

  run_runtime_step "Saga reconciliation ratchet" pnpm --silent quality:saga-reconciliation

  run_runtime_step "Action external writeback live ratchet" pnpm --silent quality:action-writeback-live

  run_runtime_step "Data quality contract ratchet" pnpm --silent quality:data-contracts

  run_runtime_step "Dataset schema evolution ratchet" pnpm --silent quality:schema-evolution

  run_runtime_step "Ontology migration ratchet" pnpm --silent quality:ontology-migrations

  run_runtime_step "Observability detector ratchet" pnpm --silent quality:observability-detectors

  run_runtime_step "SLO contract ratchet" pnpm --silent quality:slo-contracts

  run_runtime_step "Backup/restore preflight ratchet" pnpm --silent quality:backup-restore

  run_runtime_step "Auth and secret provider ratchet" pnpm --silent quality:auth-secrets

  run_runtime_step "Privacy transform ratchet" pnpm --silent quality:privacy

  run_runtime_step "Right-to-erasure manifest ratchet" pnpm --silent quality:erasure

  run_runtime_step "AI run ledger ratchet" pnpm --silent quality:ai-ledger

  run_runtime_step "AIP Model Gateway ledger ratchet" pnpm --silent quality:model-gateway-ledger

  run_runtime_step "AIP encrypted prompt artifact ratchet" pnpm --silent quality:prompt-artifacts

  run_runtime_step "AIP prompt artifact access ratchet" pnpm --silent quality:prompt-artifact-access

  run_runtime_step "AIP context compiler ratchet" pnpm --silent quality:context-compiler

  run_runtime_step "AIP Retrieval Orchestrator ratchet" pnpm --silent quality:retrieval-orchestrator

  run_runtime_step "AIP retrieval document context ratchet" pnpm --silent quality:retrieval-document-context

  run_runtime_step "AIP tool broker ratchet" pnpm --silent quality:tool-broker

  run_runtime_step "AIP citation service ratchet" pnpm --silent quality:citation-service

  run_runtime_step "AIP action proposal ratchet" pnpm --silent quality:action-proposal

  run_runtime_step "AIP approval execution ratchet" pnpm --silent quality:approval-execution

  run_runtime_step "AIP AI operations ratchet" pnpm --silent quality:ai-operations

  run_runtime_step "AIP Logic Runtime ratchet" pnpm --silent quality:logic-runtime

  run_runtime_step "AIP evals ratchet" pnpm --silent quality:ai-evals

  run_runtime_step "AIP release guard ratchet" pnpm --silent quality:ai-release

  run_runtime_step "AIP Visual Builder ratchet" pnpm --silent quality:visual-builder

  run_runtime_step "AIP Builder runtime execution ratchet" pnpm --silent quality:builder-runtime

  run_runtime_step "AIP Agent Runtime readonly ratchet" pnpm --silent quality:agent-runtime

  run_runtime_step "AIP Agent Runtime tool loop ratchet" pnpm --silent quality:agent-tool-loop

  run_runtime_step "AIP Agent action proposal tool ratchet" pnpm --silent quality:agent-action-proposal-tool

  run_runtime_step "AIP Agent vendor egress ratchet" pnpm --silent quality:agent-vendor-egress

  run_runtime_step "AIP Agent approval execution API ratchet" pnpm --silent quality:agent-approval-execution-api

  run_runtime_step "AIP Agent approval execution idempotency ratchet" pnpm --silent quality:agent-approval-execution-idempotency

  run_runtime_step "AIP Agent Runtime citation rendering ratchet" pnpm --silent quality:agent-runtime-citations

  run_runtime_step "AIP Agent citation UI ratchet" pnpm --silent quality:agent-citation-ui

  run_runtime_step "AIP Agent source preview ratchet" pnpm --silent quality:agent-source-previews

  run_runtime_step "AIP Agent inline citation ratchet" pnpm --silent quality:agent-inline-citations

  run_runtime_step "AI evidence lineage ratchet" pnpm --silent quality:ai-evidence

  run_runtime_step "Insight review workspace ratchet" pnpm --silent quality:insight-review

  run_runtime_step "Operations recovery overview ratchet" pnpm --silent quality:operations-recovery

  run_runtime_step "Elasticsearch deployment ratchet" pnpm --silent quality:elasticsearch

  run_runtime_step "S3 + Iceberg + Spark composition ratchet" pnpm --silent quality:infra-composition

  run_runtime_step "distributed control-plane CAS/lease/orchestration ratchet" pnpm --silent quality:distributed-control-plane

  run_runtime_step "product E2E raw-to-AIP operations loop" pnpm --silent quality:product-e2e-loop
  run_runtime_root_cause_summary

  run_runtime_dynamic_steps
  trap - ERR
}

run_runtime_dynamic_steps() {
  RUNTIME_GATE_STEP="supply-chain demo smoke"
  echo "== Dynamic: ${RUNTIME_GATE_STEP} =="
  rm -rf .foundry-lite-ci-smoke
  FOUNDRY_LITE_HOME=.foundry-lite-ci-smoke pnpm --silent demo:supply-chain --fresh > artifacts/demo/supply-chain.json
  uv run python -m json.tool artifacts/demo/supply-chain.json > /dev/null

  run_runtime_step "OpenLineage lineage consistency" uv run python scripts/quality/check_openlineage_dynamic_lineage.py --storage-root .foundry-lite-ci-smoke

  run_runtime_step "runtime audit count consistency" uv run python scripts/quality/check_audit_count_runtime.py --storage-root .foundry-lite-ci-smoke

  run_runtime_step "outbox consistency" uv run python scripts/quality/check_outbox_consistency.py --storage-root .foundry-lite-ci-smoke

  run_runtime_step "MVP data correctness" uv run python scripts/quality/check_mvp_data_correctness.py --storage-root .foundry-lite-ci-smoke

  run_runtime_step "MVP performance smoke" uv run python scripts/quality/check_mvp_performance_smoke.py --profile ci

  run_runtime_step "trace continuity consistency" uv run python scripts/quality/check_trace_continuity.py --storage-root .foundry-lite-trace-gate

  run_runtime_step "adapter error trace keys" uv run python scripts/quality/check_adapter_error_trace_keys.py --storage-root .foundry-lite-adapter-error-gate

  run_runtime_step "failed mutation state consistency" uv run python scripts/quality/check_failed_mutation_state_runtime.py --storage-root .foundry-lite-failure-state-gate

  run_runtime_step "schema migration PostgreSQL contention" pnpm --silent quality:schema-migration-runner-live

  RUNTIME_GATE_STEP="runtime diagnostics"
  echo "== Dynamic: ${RUNTIME_GATE_STEP} =="
  rm -rf .foundry-lite-diagnostics
  uv run python scripts/diagnostics/run_runtime_diagnostics.py
}

run_e2e_gate() {
  echo "== Dynamic: Playwright E2E =="
  ensure_trained_model_image
  export FOUNDRY_LITE_SECRET_AIP_PROMPT_ARTIFACT_ENCRYPTION_KEY="${FOUNDRY_LITE_SECRET_AIP_PROMPT_ARTIFACT_ENCRYPTION_KEY:-ci-prompt-artifact-key}"
  pnpm exec playwright test -c playwright.foundry.config.ts
  pnpm --silent quality:pipeline-builder-e2e
}

run_all_gate() {
  maybe_run_testcontainers_preflight
  run_static_gate
  run_coverage_gate
  run_flaky_gate
  run_runtime_full_gate
  run_e2e_gate
}

run_release_gate() {
  maybe_run_testcontainers_preflight
  run_static_gate
  run_coverage_gate
  run_flaky_gate
  run_runtime_full_gate

  echo "== Release: 100k performance smoke =="
  pnpm --silent quality:mvp-performance-release-100k

  echo "== Release: 1m performance smoke =="
  pnpm --silent quality:mvp-performance-release-1m

  echo "== Release: proof matrix contract =="
  pnpm --silent quality:proof-matrix

  echo "== Release: source-of-truth contract =="
  pnpm --silent quality:source-of-truth

  echo "== Release: operator evidence contract =="
  pnpm --silent quality:operator-evidence
}

main() {
  local lane="${1:-local}"

  if [[ "$lane" == "-h" || "$lane" == "--help" ]]; then
    usage
    return 0
  fi

  ensure_release_postgres_contracts_enabled

  case "$lane" in
    pr)
      run_pr_gate
      echo "Foundry-lite pull-request fast gate passed."
      ;;
    local)
      run_local_gate
      echo "Foundry-lite local quality gate passed."
      ;;
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
    runtime-full)
      run_runtime_full_gate
      echo "Foundry-lite runtime-full quality lane passed."
      ;;
    e2e)
      run_e2e_gate
      echo "Foundry-lite E2E quality lane passed."
      ;;
    release)
      run_release_gate
      echo "Foundry-lite release evidence gate passed."
      ;;
    *)
      usage >&2
      echo "ERROR: unknown ci gate lane: $lane" >&2
      return 2
      ;;
  esac
}

main "$@"
