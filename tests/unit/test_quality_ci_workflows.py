from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]


def _load_python_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _static_lane_text() -> str:
    """ci_gate.sh + parallel static driver: the static gate wiring surface.

    Static checks moved from serial ci_gate.sh lines into
    scripts/quality/run_static_checks.py and run concurrently; inventory
    order inside the driver is documentation order, not execution order.
    """
    gate = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    driver = (ROOT / "scripts" / "quality" / "run_static_checks.py").read_text(encoding="utf-8")
    return f"{gate}\n{driver}"


def test_ci_gate_does_not_run_heavy_codeql_locally() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")

    assert "scripts/quality/codeql/run.sh" not in script
    assert "CodeQL P7 is intentionally not run" in script


def test_gitleaks_missing_is_release_blocker() -> None:
    script = _static_lane_text()

    assert "FOUNDRY_LITE_STRICT_EXTERNAL_TOOLS" in script
    assert 'os.environ.get("CI") == "true"' in script
    assert "CI/release evidence cannot skip the P9 secret scan" in script


def test_parallel_static_checks_get_isolated_foundry_home(monkeypatch) -> None:
    driver = _load_python_module(ROOT / "scripts" / "quality" / "run_static_checks.py", "run_static_checks_test")
    captured: dict[str, str] = {}

    def fake_run(command, *, cwd, env, capture_output, text):  # noqa: ANN001
        del cwd, capture_output, text
        captured["command"] = " ".join(command)
        captured["home"] = env["FOUNDRY_LITE_HOME"]
        Path(captured["home"], "foundry-lite.db").write_text("created during static check", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(driver.subprocess, "run", fake_run)

    result = driver._run_check("infra-tricky-matrix", ["python", "check.py"], {"PYTHONPATH": "."})

    assert result.returncode == 0
    assert captured["home"]
    assert Path(captured["home"]).name.startswith("foundry-lite-static-infra-tricky-matrix-")
    assert not Path(captured["home"]).exists()


def test_parallel_static_checks_honor_explicit_foundry_home(monkeypatch, tmp_path: Path) -> None:
    driver = _load_python_module(ROOT / "scripts" / "quality" / "run_static_checks.py", "run_static_checks_explicit")
    explicit_home = tmp_path / "explicit-home"
    explicit_home.mkdir()
    captured: dict[str, str] = {}

    def fake_run(command, *, cwd, env, capture_output, text):  # noqa: ANN001
        del cwd, capture_output, text
        captured["home"] = env["FOUNDRY_LITE_HOME"]
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(driver.subprocess, "run", fake_run)

    result = driver._run_check(
        "infra-tricky-matrix",
        ["python", "check.py"],
        {"PYTHONPATH": ".", "FOUNDRY_LITE_HOME": str(explicit_home)},
    )

    assert result.returncode == 0
    assert captured["home"] == str(explicit_home)
    assert explicit_home.exists()


def test_openlineage_dynamic_lineage_runs_after_demo_smoke() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    demo_step = "FOUNDRY_LITE_HOME=.foundry-lite-ci-smoke pnpm --silent demo:supply-chain --fresh"
    openlineage_step = "scripts/quality/check_openlineage_dynamic_lineage.py --storage-root .foundry-lite-ci-smoke"
    assert openlineage_step in script
    assert script.index(demo_step) < script.index(openlineage_step)
    assert '"quality:openlineage"' in package_json


def test_audit_count_runtime_runs_after_demo_smoke() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    demo_step = "FOUNDRY_LITE_HOME=.foundry-lite-ci-smoke pnpm --silent demo:supply-chain --fresh"
    audit_step = "scripts/quality/check_audit_count_runtime.py --storage-root .foundry-lite-ci-smoke"
    assert audit_step in script
    assert script.index(demo_step) < script.index(audit_step)
    assert '"quality:audit-runtime"' in package_json


def test_outbox_consistency_runs_after_demo_smoke() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    demo_step = "FOUNDRY_LITE_HOME=.foundry-lite-ci-smoke pnpm --silent demo:supply-chain --fresh"
    outbox_step = "scripts/quality/check_outbox_consistency.py --storage-root .foundry-lite-ci-smoke"
    assert outbox_step in script
    assert script.index(demo_step) < script.index(outbox_step)
    assert '"quality:outbox-runtime"' in package_json


def test_mvp_data_correctness_runs_after_outbox_consistency() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    outbox_step = "scripts/quality/check_outbox_consistency.py --storage-root .foundry-lite-ci-smoke"
    data_step = "scripts/quality/check_mvp_data_correctness.py --storage-root .foundry-lite-ci-smoke"
    assert data_step in script
    assert script.index(outbox_step) < script.index(data_step)
    assert '"quality:mvp-data-correctness"' in package_json


def test_mvp_performance_smoke_runs_after_data_correctness() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    data_step = "scripts/quality/check_mvp_data_correctness.py --storage-root .foundry-lite-ci-smoke"
    performance_step = "scripts/quality/check_mvp_performance_smoke.py --profile ci"
    assert performance_step in script
    assert script.index(data_step) < script.index(performance_step)
    assert '"quality:mvp-performance-smoke"' in package_json
    assert '"quality:mvp-performance-release-100k"' in package_json
    assert '"quality:mvp-performance-release-1m"' in package_json


def test_trace_continuity_runs_after_demo_smoke() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    demo_step = "FOUNDRY_LITE_HOME=.foundry-lite-ci-smoke pnpm --silent demo:supply-chain --fresh"
    trace_step = "scripts/quality/check_trace_continuity.py --storage-root .foundry-lite-trace-gate"
    assert trace_step in script
    assert script.index(demo_step) < script.index(trace_step)
    assert '"quality:trace-continuity"' in package_json


def test_adapter_error_trace_key_gate_runs_after_trace_continuity() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    trace_step = "scripts/quality/check_trace_continuity.py --storage-root .foundry-lite-trace-gate"
    adapter_step = "scripts/quality/check_adapter_error_trace_keys.py --storage-root .foundry-lite-adapter-error-gate"
    assert adapter_step in script
    assert script.index(trace_step) < script.index(adapter_step)
    assert '"quality:adapter-error-trace"' in package_json


def test_failed_mutation_state_gate_runs_after_adapter_error_trace() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    adapter_step = "scripts/quality/check_adapter_error_trace_keys.py --storage-root .foundry-lite-adapter-error-gate"
    failure_state_step = (
        "scripts/quality/check_failed_mutation_state_runtime.py --storage-root .foundry-lite-failure-state-gate"
    )
    assert failure_state_step in script
    assert script.index(adapter_step) < script.index(failure_state_step)
    assert '"quality:failed-mutation-state"' in package_json


def test_flaky_detector_repeats_parallel_pytest_three_times() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")
    nightly = (ROOT / ".github" / "workflows" / "nightly.yml").read_text(encoding="utf-8")

    # Flakiness is a suite property, not a diff property: the detector keeps
    # its 3-iteration default for release rehearsal, and the nightly lane
    # raises the iteration count for more statistical power than any per-PR
    # rerun could afford.
    assert "scripts/quality/check_flaky_detector.py" in script
    assert '"${FOUNDRY_LITE_FLAKY_ITERATIONS:-3}"' in script
    assert '--command "uv run pytest tests -n auto --no-header -q"' in script
    assert 'FOUNDRY_LITE_FLAKY_ITERATIONS: "7"' in nightly
    assert '"quality:flaky-detector"' in package_json


def test_github_flaky_lane_keeps_strong_detector_with_realistic_timeout() -> None:
    nightly = (ROOT / ".github" / "workflows" / "nightly.yml").read_text(encoding="utf-8")

    flaky_job = nightly.split("quality_flaky_nightly:", maxsplit=1)[1].split("quality_runtime_full:", maxsplit=1)[0]
    assert "timeout-minutes: 120" in flaky_job
    assert "run: pnpm ci:gate:flaky" in flaky_job
    assert 'FOUNDRY_LITE_FLAKY_ITERATIONS: "7"' in flaky_job


def test_github_e2e_lane_keeps_browser_install_from_timing_out_before_tests() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    e2e_job = workflow.split("quality_e2e:", maxsplit=1)[1].split("quality_gate:", maxsplit=1)[0]
    assert "timeout-minutes: 20" in e2e_job
    assert "run: pnpm exec playwright install --with-deps chromium" in e2e_job
    assert "run: pnpm ci:gate:e2e" in e2e_job


def test_github_coverage_lane_keeps_serial_layer_coverage_from_timing_out() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    coverage_job = workflow.split("quality_coverage:", maxsplit=1)[1].split("quality_runtime:", maxsplit=1)[0]
    assert "timeout-minutes: 45" in coverage_job
    assert "run: pnpm ci:gate:coverage" in coverage_job
    assert "Coverage runs the full backend suite serially" in coverage_job


def test_context_compiler_gate_runs_after_ai_ledger_before_ai_evidence() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    ledger_step = "pnpm --silent quality:ai-ledger"
    context_step = "pnpm --silent quality:context-compiler"
    tool_broker_step = "pnpm --silent quality:tool-broker"
    evidence_step = "pnpm --silent quality:ai-evidence"
    assert context_step in script
    assert script.index(ledger_step) < script.index(context_step)
    assert script.index(context_step) < script.index(tool_broker_step) < script.index(evidence_step)
    assert '"quality:context-compiler"' in package_json


def test_tool_broker_gate_runs_after_context_compiler_before_ai_evidence() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    context_step = "pnpm --silent quality:context-compiler"
    tool_broker_step = "pnpm --silent quality:tool-broker"
    citation_step = "pnpm --silent quality:citation-service"
    evidence_step = "pnpm --silent quality:ai-evidence"
    assert tool_broker_step in script
    assert script.index(context_step) < script.index(tool_broker_step) < script.index(citation_step)
    assert script.index(citation_step) < script.index(evidence_step)
    assert '"quality:tool-broker"' in package_json
    assert "tests/contracts/test_tool_executor_contract.py" in package_json
    assert "tests/unit/test_tool_broker.py" in package_json


def test_citation_service_gate_runs_after_tool_broker_before_ai_evidence() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    tool_broker_step = "pnpm --silent quality:tool-broker"
    citation_step = "pnpm --silent quality:citation-service"
    evidence_step = "pnpm --silent quality:ai-evidence"
    assert citation_step in script
    assert script.index(tool_broker_step) < script.index(citation_step) < script.index(evidence_step)
    assert '"quality:citation-service"' in package_json
    assert "tests/contracts/test_citation_source_contract.py" in package_json
    assert "tests/unit/test_citation_service.py" in package_json


def test_action_proposal_gate_runs_after_citation_before_ai_evidence() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    citation_step = "pnpm --silent quality:citation-service"
    action_proposal_step = "pnpm --silent quality:action-proposal"
    evidence_step = "pnpm --silent quality:ai-evidence"
    assert action_proposal_step in script
    assert script.index(citation_step) < script.index(action_proposal_step) < script.index(evidence_step)
    assert '"quality:action-proposal"' in package_json
    assert "tests/unit/test_action_proposal_service.py" in package_json


def test_approval_execution_gate_runs_after_action_proposal_before_ai_evidence() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    action_proposal_step = "pnpm --silent quality:action-proposal"
    approval_execution_step = "pnpm --silent quality:approval-execution"
    evidence_step = "pnpm --silent quality:ai-evidence"
    assert approval_execution_step in script
    assert script.index(action_proposal_step) < script.index(approval_execution_step) < script.index(evidence_step)
    assert '"quality:approval-execution"' in package_json
    assert "tests/unit/test_approval_execution_service.py" in package_json


def test_ci_gate_exposes_parallel_lanes_without_weakening_default_gate() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    for lane in ("local", "all", "static", "coverage", "flaky", "runtime", "runtime-full", "e2e", "release"):
        assert f'"ci:gate:{lane}": "bash scripts/ci_gate.sh {lane}"' in package_json
        assert f"{lane})" in script

    assert "Usage: bash scripts/ci_gate.sh [local|all|static|coverage|flaky|runtime|runtime-full|e2e|release]" in script
    assert '"ci:gate": "bash scripts/ci_gate.sh local"' in package_json
    assert "run_local_gate()" in script
    assert "run_all_gate()" in script
    assert "run_static_gate" in script
    assert "run_coverage_gate" in script
    assert "run_flaky_gate" in script
    assert "run_runtime_gate" in script
    assert "run_runtime_full_gate" in script
    assert "run_e2e_gate" in script
    assert "run_release_gate" in script
    assert "Foundry-lite CI gate passed." in script


def test_e2e_seed_prepares_mdo_finance_datasource_before_ontology_apply() -> None:
    script = (ROOT / "scripts" / "e2e_start_api.sh").read_text(encoding="utf-8")
    finance_dataset = 'core.datasets.ensure("clean.order_finance"'
    finance_transform = 'core.transforms.run("clean_order_finance"'
    ontology_apply = "ontology = core.ontology.apply(str(margin_action_ontology_path()), ctx=ctx)"

    assert finance_dataset in script
    assert finance_transform in script
    assert '"cleanOrderFinanceVersion": clean_order_finance.version_id' in script
    assert script.index(finance_dataset) < script.index(finance_transform) < script.index(ontology_apply)


def test_pragma_no_cover_budget_is_release_gate_step() -> None:
    script = _static_lane_text()
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert '"scripts/quality/check_pragma_no_cover_budget.py", "--baseline", "0"' in script
    assert '"quality:pragma-budget"' in package_json
    assert "pnpm quality:pragma-budget" in package_json


def test_audit_on_mutation_is_release_gate_step() -> None:
    script = _static_lane_text()
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_audit_on_mutation.py" in script
    assert '"quality:audit-mutation"' in package_json
    assert "pnpm quality:audit-mutation" in package_json


def test_transaction_outbox_pair_is_release_gate_step() -> None:
    script = _static_lane_text()
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_transaction_outbox_pair.py" in script
    assert '"quality:transaction-outbox"' in package_json
    assert "pnpm quality:transaction-outbox" in package_json


def test_action_idempotency_is_release_gate_step() -> None:
    script = _static_lane_text()
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_idempotency_on_action.py" in script
    assert '"quality:action-idempotency"' in package_json
    assert "pnpm quality:action-idempotency" in package_json


def test_error_response_request_id_is_release_gate_step() -> None:
    script = _static_lane_text()
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_error_response_has_request_id.py" in script
    assert '"quality:error-request-id"' in package_json
    assert "pnpm quality:error-request-id" in package_json


def test_function_length_is_release_gate_step() -> None:
    script = _static_lane_text()
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_function_length.py" in script
    assert '"quality:function-length"' in package_json
    assert "pnpm quality:function-length" in package_json


def test_boolean_naming_is_release_gate_step() -> None:
    script = _static_lane_text()
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_boolean_naming.py" in script
    assert '"quality:boolean-naming"' in package_json
    assert "pnpm quality:boolean-naming" in package_json


def test_dict_any_budget_is_release_gate_step() -> None:
    script = _static_lane_text()
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_dict_any_budget.py" in script
    assert '"quality:dict-any-budget"' in package_json
    assert "pnpm quality:dict-any-budget" in package_json


def test_log_trace_keys_is_release_gate_step() -> None:
    script = _static_lane_text()
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_log_has_trace_keys.py" in script
    assert '"quality:log-trace-keys"' in package_json
    assert "pnpm quality:log-trace-keys" in package_json


def test_metrics_exposed_is_release_gate_step() -> None:
    script = _static_lane_text()
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_metrics_exposed.py" in script
    assert '"quality:metrics-exposed"' in package_json
    assert "pnpm quality:metrics-exposed" in package_json


def test_adapter_failure_taxonomy_is_release_gate_step() -> None:
    script = _static_lane_text()
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_adapter_failure_taxonomy.py" in script
    assert '"quality:adapter-failure-taxonomy"' in package_json
    assert "pnpm quality:adapter-failure-taxonomy" in package_json


def test_infra_ratchet_is_release_gate_step_after_adapter_taxonomy() -> None:
    script = _static_lane_text()
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    taxonomy_step = "scripts/quality/check_adapter_failure_taxonomy.py"
    ratchet_step = "scripts/quality/check_infra_ratchet.py"
    assert ratchet_step in script
    assert script.index(taxonomy_step) < script.index(ratchet_step)
    assert '"quality:infra-ratchet"' in package_json
    assert "pnpm quality:infra-ratchet" in package_json


def test_s3_storage_ratchet_is_runtime_gate_step() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert '"quality:s3-storage"' in package_json
    assert "tests/integration/test_s3_dataset_storage_adapter.py" in package_json
    assert "pnpm --silent quality:s3-storage" in script
    assert script.index("maybe_run_testcontainers_preflight") < script.index("pnpm --silent quality:s3-storage")


def test_stream_archive_worker_script_is_exposed() -> None:
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert '"worker:stream-archive"' in package_json
    assert "python -m foundry_lite_worker.stream_archive" in package_json
    assert '"quality:kafka-live-broker"' in package_json
    assert "tests/integration/test_kafka_live_broker_stream_archive.py" in package_json
    assert '"quality:cdc-stream-archive"' in package_json
    assert "tests/integration/test_cdc_stream_archive.py" in package_json
    assert '"quality:cdc-live-debezium"' in package_json
    assert "tests/integration/test_debezium_live_cdc.py" in package_json
    assert '"quality:cdc-object-indexing"' in package_json
    assert "tests/unit/test_cdc_indexing.py" in package_json
    assert "tests/integration/test_cdc_object_indexing.py" in package_json
    assert '"quality:shadow-reindex"' in package_json
    assert "tests/integration/test_shadow_reindex.py" in package_json
    assert '"quality:search-indexing"' in package_json
    assert "tests/integration/test_search_indexing.py" in package_json


def test_transform_scheduler_worker_script_is_exposed() -> None:
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert '"worker:transform-scheduler"' in package_json
    assert "python -m foundry_lite_worker.transform_scheduler" in package_json
    assert '"quality:transform-scheduler"' in package_json
    assert "tests/unit/test_transform_scheduler_worker.py" in package_json
    assert "tests/integration/test_closed_loop.py" in package_json


def test_temporal_engine_integration_ratchet_is_runtime_gate_step() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert '"quality:temporal-engine-integration"' in package_json
    assert "test_product_connector_sync_workflow_runs_through_temporal_and_audits" in package_json
    assert "test_api_operations_workflow_start_status_and_audit" in package_json
    assert "tests/unit/test_workflow_orchestration_start_recovery.py" in package_json
    assert "test_retryable_workflow_start_exception_records_start_unknown" in package_json
    assert "test_permanent_workflow_start_exception_records_failed_not_starting" in package_json
    assert "pnpm --silent quality:temporal-engine-integration" in script
    assert script.index("pnpm --silent quality:temporal") < script.index(
        "pnpm --silent quality:temporal-engine-integration"
    )


def test_external_writeback_ratchet_is_runtime_gate_step() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert '"quality:external-writeback"' in package_json
    assert "test_external_success_response_lost_becomes_outcome_unknown" in package_json
    assert "test_outcome_unknown_is_not_blindly_retried" in package_json
    assert "test_protected_runtime_blocks_action_failure_injection_before_run_insert" in package_json
    assert "pnpm --silent quality:external-writeback" in script
    assert script.index("pnpm --silent quality:temporal-engine-integration") < script.index(
        "pnpm --silent quality:external-writeback"
    )
    assert script.index("pnpm --silent quality:external-writeback") < script.index(
        "pnpm --silent quality:elasticsearch"
    )


def test_action_writeback_retryable_ratchet_is_runtime_gate_step() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert '"quality:action-writeback-retryable"' in package_json
    assert "test_retryable_writeback_failure_is_visible_without_object_edit" in package_json
    assert "test_action_repository_contract_persists_retryable_writeback_fields" in package_json
    assert "test_recovery_marks_retryable_writeback_as_retry_candidate" in package_json
    assert "test_action_writeback_retryable_ratchet_is_runtime_gate_step" in package_json
    assert "pnpm --silent quality:action-writeback-retryable" in script
    assert script.index("pnpm --silent quality:external-writeback") < script.index(
        "pnpm --silent quality:action-writeback-retryable"
    )
    assert script.index("pnpm --silent quality:action-writeback-retryable") < script.index(
        "pnpm --silent quality:action-writeback-approval-release"
    )


def test_action_writeback_approval_release_ratchet_is_runtime_gate_step() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert '"quality:action-writeback-approval-release"' in package_json
    assert "test_operator_approval_releases_sensitive_writeback_recovery" in package_json
    assert "test_api_operations_reconciliation_approval_releases_sensitive_writeback" in package_json
    assert "tests/sdk/request_contract.mjs" in package_json
    assert "pnpm --silent quality:action-writeback-approval-release" in script
    assert script.index("pnpm --silent quality:action-writeback-retryable") < script.index(
        "pnpm --silent quality:action-writeback-approval-release"
    )
    assert script.index("pnpm --silent quality:action-writeback-approval-release") < script.index(
        "pnpm --silent quality:saga-reconciliation"
    )


def test_saga_reconciliation_ratchet_is_runtime_gate_step() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert '"quality:saga-reconciliation"' in package_json
    assert "test_external_success_local_failure_requires_compensation" in package_json
    assert "test_compensation_is_idempotent" in package_json
    assert "test_reconciliation_resolves_remote_success" in package_json
    assert "test_concurrent_reconciliation_has_one_winner" in package_json
    assert "test_sensitive_writeback_payload_is_masked_in_audit" in package_json
    assert "test_bounded_recovery_requires_operator_approval_for_sensitive_writeback" in package_json
    assert "test_api_operations_reconciliation_resolves_action_writeback" in package_json
    assert "test_action_repository_contract_lists_unresolved_writebacks" in package_json
    assert "test_api_operations_reconciliation_queue_lists_unresolved_writebacks" in package_json
    assert "test_reconciliation_queue_item_masks_sensitive_payload" in package_json
    assert "pnpm --silent quality:saga-reconciliation" in script
    assert script.index("pnpm --silent quality:action-writeback-approval-release") < script.index(
        "pnpm --silent quality:saga-reconciliation"
    )
    assert script.index("pnpm --silent quality:saga-reconciliation") < script.index(
        "pnpm --silent quality:elasticsearch"
    )


def test_data_contracts_ratchet_is_runtime_gate_step() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert '"quality:data-contracts"' in package_json
    assert "test_commit_dataset_version_aborts_when_primary_key_check_fails" in package_json
    assert "test_schema_validation_records_reference_version" in package_json
    assert "test_contract_version_is_pinned_to_run" in package_json
    assert "test_quality_result_history_lists_dataset_commit_results" in package_json
    assert "test_persisted_quality_contract_check_blocks_later_commit" in package_json
    assert "test_updated_quality_contract_check_controls_later_commit" in package_json
    assert "test_operations_run_detail_exposes_candidate_quality_report" in package_json
    assert "test_operations_run_detail_includes_failed_row_sample_for_quarantine" in package_json
    assert "test_quality_check_pins_candidate_manifest_hash" in package_json
    assert "test_candidate_tamper_between_check_and_commit_is_rejected" in package_json
    assert "test_warn_does_not_block_commit_but_is_visible" in package_json
    assert "test_quarantine_routes_bad_records_to_record_dlq" in package_json
    assert "test_insert_check_result_persists" in package_json
    assert "test_checks_for_dataset_are_tenant_scoped_and_ordered" in package_json
    assert "test_update_check_is_tenant_scoped_and_rewrites_definition" in package_json
    assert "test_check_results_for_transaction_is_tenant_scoped" in package_json
    assert "test_check_results_for_dataset_are_tenant_dataset_scoped_and_limited" in package_json
    assert "test_check_result_summary_counts_are_tenant_dataset_scoped" in package_json
    assert "test_api_dataset_object_action_and_metrics_smoke" in package_json
    assert "pnpm --silent quality:data-contracts" in script
    assert script.index("pnpm --silent quality:saga-reconciliation") < script.index(
        "pnpm --silent quality:data-contracts"
    )
    assert script.index("pnpm --silent quality:data-contracts") < script.index("pnpm --silent quality:elasticsearch")


def test_router_layer_purity_is_release_gate_step() -> None:
    script = _static_lane_text()
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_router_layer_purity.py" in script
    assert '"quality:router-purity"' in package_json
    assert "pnpm quality:router-purity" in package_json


def test_query_side_effects_is_release_gate_step() -> None:
    script = _static_lane_text()
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_query_side_effects.py" in script
    assert '"quality:query-side-effects"' in package_json
    assert "pnpm quality:query-side-effects" in package_json


def test_repository_no_business_is_release_gate_step() -> None:
    script = _static_lane_text()
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_repository_no_business.py" in script
    assert '"quality:repository-no-business"' in package_json
    assert "pnpm quality:repository-no-business" in package_json


def test_tenant_write_guard_is_release_gate_step() -> None:
    script = _static_lane_text()
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_tenant_write_guard.py" in script
    assert '"quality:tenant-write"' in package_json
    assert "pnpm quality:tenant-write" in package_json


def test_status_transition_cas_is_release_gate_step() -> None:
    script = _static_lane_text()
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_status_transition_cas.py" in script
    assert '"quality:status-cas"' in package_json
    assert "pnpm quality:status-cas" in package_json
    assert script.index("scripts/quality/check_tenant_write_guard.py") < script.index(
        "scripts/quality/check_status_transition_cas.py"
    )


def test_contract_test_per_port_is_release_gate_step() -> None:
    script = _static_lane_text()
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_contract_test_per_port.py" in script
    assert '"quality:contract-tests"' in package_json
    assert "pnpm quality:contract-tests" in package_json


def test_strategy_specification_tests_is_release_gate_step() -> None:
    script = _static_lane_text()
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_strategy_specification_tests.py" in script
    assert '"quality:strategy-spec-tests"' in package_json
    assert "pnpm quality:strategy-spec-tests" in package_json


def test_integration_scenario_markers_are_release_gate_step() -> None:
    script = _static_lane_text()
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_integration_scenario_markers.py" in script
    assert '"quality:integration-scenarios"' in package_json
    assert "pnpm quality:integration-scenarios" in package_json


def test_root_cause_meta_gates_are_release_gate_steps() -> None:
    script = _static_lane_text()
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_regression_test_per_bugfix.py" in script
    assert "scripts/quality/check_pr_root_cause_section.py" in script
    assert '"quality:regression-bugfix"' in package_json
    assert '"quality:pr-root-cause"' in package_json
    assert "pnpm quality:regression-bugfix" in package_json
    assert "pnpm quality:pr-root-cause" in package_json


def test_documentation_map_gate_runs_after_doc_drift() -> None:
    script = _static_lane_text()
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    doc_drift_step = "scripts/quality/check_doc_drift.py"
    doc_map_step = "scripts/quality/check_documentation_map.py"
    checklist_step = "scripts/quality/check_checklist_evidence.py"
    assert doc_map_step in script
    assert script.index(doc_drift_step) < script.index(doc_map_step) < script.index(checklist_step)
    assert '"quality:documentation-map"' in package_json
    assert "pnpm quality:documentation-map" in package_json


def test_evidence_ledger_command_gate_runs_between_doc_drift_and_documentation_map() -> None:
    script = _static_lane_text()
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    doc_drift_step = "scripts/quality/check_doc_drift.py"
    evidence_command_step = "scripts/quality/check_evidence_ledger_commands.py"
    doc_map_step = "scripts/quality/check_documentation_map.py"
    assert evidence_command_step in script
    assert script.index(doc_drift_step) < script.index(evidence_command_step) < script.index(doc_map_step)
    assert '"quality:evidence-ledger-commands"' in package_json
    assert "pnpm quality:evidence-ledger-commands" in package_json


def test_data_platform_sprint_status_gate_runs_after_data_pattern_matrix() -> None:
    script = _static_lane_text()
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    pattern_step = "scripts/quality/check_data_pattern_matrix.py"
    status_step = "scripts/quality/check_data_platform_sprint_status.py"
    assert status_step in script
    assert script.index(pattern_step) < script.index(status_step)
    assert '"quality:data-platform-sprint-status"' in package_json
    assert "pnpm quality:data-platform-sprint-status" in package_json


def test_pipeline_parity_matrix_is_static_gate_step() -> None:
    script = _static_lane_text()
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    parity_step = "scripts/quality/check_pipeline_parity_matrix.py"
    assert parity_step in script
    assert '"quality:pipeline-parity-matrix"' in package_json
    assert "pnpm quality:pipeline-parity-matrix" in package_json


def test_pipeline_builder_quality_gates_execute_real_evidence_in_ci_lanes() -> None:
    static_driver = (ROOT / "scripts" / "quality" / "run_static_checks.py").read_text(encoding="utf-8")
    e2e_driver = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_scripts = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["scripts"]
    static_gates = (
        "quality:pipeline-graph-v2",
        "quality:pipeline-artifact-execution",
        "quality:pipeline-preview",
        "quality:pipeline-multimodal",
        "quality:pipeline-scheduler",
        "quality:pipeline-collaboration",
        "quality:pipeline-python-isolation",
        "quality:pipeline-trained-model-sidecar",
        "quality:pipeline-builder-performance",
    )

    for gate in static_gates:
        command = package_scripts[gate]
        assert "pytest" in command or "scripts/quality/check_pipeline_builder_performance.py" in command
        assert f'("{gate}", "static")' in static_driver
        assert f'["pnpm", "--silent", "{gate}"]' in static_driver
        assert f"pnpm {gate}" in package_scripts["quality:static"]

    assert "infra/code_execution/Dockerfile" in package_scripts["quality:code-execution-image"]
    isolation_live_gate = "quality:pipeline-python-isolation-live"
    assert "tests/integration/python_code_execution_container_live.py" in package_scripts[isolation_live_gate]
    assert "ensure_code_execution_image" in e2e_driver
    assert f"pnpm --silent {isolation_live_gate}" in e2e_driver
    assert "infra/trained_model_sidecar/Dockerfile" in package_scripts["quality:trained-model-sidecar-image"]
    model_live_gate = "quality:pipeline-trained-model-sidecar-live"
    assert "tests/integration/trained_model_sidecar_container_live.py" in package_scripts[model_live_gate]
    assert "ensure_trained_model_image" in e2e_driver
    assert f"pnpm --silent {model_live_gate}" in e2e_driver

    e2e_gate = "quality:pipeline-builder-e2e"
    assert "tests/e2e-foundry/pipeline-builder-flow.spec.ts" in package_scripts[e2e_gate]
    assert "playwright.foundry.config.ts" in package_scripts[e2e_gate]
    assert "FOUNDRY_LITE_API_PORT=8016" in package_scripts[e2e_gate]
    assert "FOUNDRY_LITE_FRONTEND_PORT=4186" in package_scripts[e2e_gate]
    assert "FOUNDRY_LITE_E2E_API_BASE_URL=http://127.0.0.1:8016" in package_scripts[e2e_gate]
    assert "FOUNDRY_LITE_E2E_WEB_BASE_URL=http://127.0.0.1:4186" in package_scripts[e2e_gate]
    assert "FOUNDRY_LITE_API_PROXY_TARGET=http://127.0.0.1:8016" in package_scripts[e2e_gate]
    assert "VITE_FOUNDRY_LITE_API_URL=http://127.0.0.1:4186" in package_scripts[e2e_gate]
    assert "FOUNDRY_LITE_E2E_REUSE_EXISTING=0" in package_scripts[e2e_gate]
    assert "FOUNDRY_LITE_TRAINED_MODEL_PROFILE=container" in package_scripts[e2e_gate]
    assert f'("{e2e_gate}", "e2e")' in static_driver
    assert f"pnpm --silent {e2e_gate}" in e2e_driver

    e2e_api = (ROOT / "scripts" / "e2e_start_api.sh").read_text(encoding="utf-8")
    foundry_config = (ROOT / "playwright.foundry.config.ts").read_text(encoding="utf-8")
    assert 'os.environ.get("FOUNDRY_LITE_API_PORT", "8000")' in e2e_api
    assert "foundryApiBaseUrl" in foundry_config
    assert "shouldReuseExistingServer" in foundry_config
    assert 'process.env.FOUNDRY_LITE_E2E_REUSE_EXISTING === "1"' in foundry_config
    assert "!process.env.CI" not in foundry_config


def test_runtime_lane_writes_root_cause_summary_from_failure_trap() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    summary_script = (ROOT / "scripts" / "quality" / "write_runtime_root_cause_summary.py").read_text(encoding="utf-8")

    assert "trap 'runtime_gate_failed \"$?\"' ERR" in script
    assert "runtime_lane_failure.json" in script
    assert "run_runtime_contract_gates" in script
    assert script.index("run_runtime_contract_gates") < script.index("quality:cdc-stream-archive")
    assert "runtime_lane_failure.json" in summary_script
    assert "failed step:" in summary_script


def test_runtime_root_cause_summary_preserves_runtime_failure_evidence(tmp_path, monkeypatch) -> None:
    module = _load_python_module(
        ROOT / "scripts" / "quality" / "write_runtime_root_cause_summary.py",
        "write_runtime_root_cause_summary_test",
    )
    artifacts = tmp_path / "quality"
    artifacts.mkdir()
    (artifacts / "runtime_lane_failure.json").write_text(
        json.dumps({"failedStep": "Temporal workflow ratchet", "exitCode": 17}),
        encoding="utf-8",
    )
    for gate in ("source_of_truth_contract", "operator_evidence"):
        (artifacts / f"{gate}.json").write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
    (artifacts / "proof_matrix.json").write_text(
        json.dumps(
            {
                "status": "FAIL",
                "findings": [
                    {
                        "problem": "missing operator evidence proof",
                        "why": "failure must remain inspectable after the log line is gone",
                        "suggestedFiles": ["docs/infra-tricky-matrix.json"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(module, "ARTIFACTS", artifacts)
    summary, any_fail = module.build_summary()

    assert any_fail
    assert "failed step: Temporal workflow ratchet (exit 17)" in summary
    assert "missing operator evidence proof" in summary
    assert "failure must remain inspectable after the log line is gone" in summary
    assert "`docs/infra-tricky-matrix.json`" in summary


def test_proof_matrix_pytest_collection_subprocess_import_is_bandit_justified() -> None:
    helper = (ROOT / "scripts" / "quality" / "_proof_matrix_lib.py").read_text(encoding="utf-8")

    assert "import subprocess  # nosec B404" in helper
    assert "subprocess.run(command" in helper


def test_github_ci_fetches_history_and_checks_pr_root_cause() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "fetch-depth: 0" in workflow
    assert "Check PR root-cause evidence" in workflow
    assert "scripts/quality/check_pr_root_cause_section.py --event-path" in workflow


def test_github_workflows_use_node24_action_versions() -> None:
    ci_workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    codeql_workflow = (ROOT / ".github" / "workflows" / "codeql.yml").read_text(encoding="utf-8")

    node24_ci_actions = (
        "actions/checkout@v6",
        "astral-sh/setup-uv@v8.2.0",
        "actions/setup-python@v6",
        "pnpm/action-setup@v6",
        "actions/setup-node@v6",
        "actions/setup-go@v6",
        "actions/upload-artifact@v7",
    )
    node24_codeql_actions = (
        "actions/checkout@v6",
        "actions/setup-python@v6",
        "github/codeql-action/init@v4",
        "github/codeql-action/analyze@v4",
        "actions/upload-artifact@v7",
    )
    deprecated_node20_actions = (
        "actions/checkout@v4",
        "astral-sh/setup-uv@v5",
        "actions/setup-python@v5",
        "pnpm/action-setup@v4",
        "actions/setup-node@v4",
        "actions/setup-go@v5",
        "actions/upload-artifact@v4",
        "FORCE_JAVASCRIPT_ACTIONS_TO_NODE24",
    )

    for action_ref in node24_ci_actions:
        assert action_ref in ci_workflow

    for action_ref in node24_codeql_actions:
        assert action_ref in codeql_workflow

    for action_ref in deprecated_node20_actions:
        assert action_ref not in ci_workflow
        assert action_ref not in codeql_workflow


def test_github_ci_parallelizes_quality_lanes_behind_required_aggregate_check() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    nightly = (ROOT / ".github" / "workflows" / "nightly.yml").read_text(encoding="utf-8")

    for job_name in ("quality-static", "quality-coverage", "quality-runtime", "quality-e2e"):
        assert f"name: {job_name}" in workflow

    assert "group: foundry-lite-ci-${{ github.workflow }}-${{ github.ref }}" in workflow
    assert "cancel-in-progress: true" in workflow
    assert "name: quality-gate" in workflow
    assert "needs:" in workflow
    assert "quality_static" in workflow
    assert "quality_coverage" in workflow
    assert "quality_runtime" in workflow
    assert "quality_e2e" in workflow

    # Suite-property lanes moved off the PR path into the nightly gate with
    # more statistical power (flaky x7, full ratchet rehearsal).
    assert "quality_flaky" not in workflow
    assert "quality-flaky-nightly" in nightly
    assert "quality-runtime-full" in nightly
    assert "pnpm ci:gate:runtime-full" in nightly


def test_release_gate_installs_live_media_prerequisites_before_release_gate() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    required_steps = (
        "Install Tesseract OCR, Poppler, and FFmpeg",
        "Cache faster-whisper and fastembed models",
        "Pre-fetch faster-whisper tiny model",
        "Pre-fetch fastembed embedding model",
        "Pre-fetch fastembed CLIP vision/text models",
        "Run release evidence gate",
    )
    for step in required_steps:
        assert step in workflow

    assert "sudo apt-get update && sudo apt-get install -y tesseract-ocr poppler-utils ffmpeg" in workflow
    assert "hf-models-whisper-tiny-bge-small-clip-vit-b32-v1" in workflow
    assert workflow.index("Install Tesseract OCR, Poppler, and FFmpeg") < workflow.index("Run release evidence gate")
    assert workflow.index("Pre-fetch fastembed CLIP vision/text models") < workflow.index("Run release evidence gate")


def test_media_active_covered_gate_runs_after_live_media_lanes() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    external_step = "pnpm --silent quality:media-live-external"
    active_step = "pnpm --silent quality:media-active-covered"
    assert external_step in script
    assert active_step in script
    assert script.index(external_step) < script.index(active_step)
    assert '"quality:media-active-covered"' in package_json
    assert "tests/unit/test_media_transaction_service.py" in package_json


def test_e2e_gate_sets_prompt_artifact_test_secret_without_local_fallback() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")

    e2e_gate = script.split("run_e2e_gate() {", maxsplit=1)[1].split("run_all_gate() {", maxsplit=1)[0]
    assert "FOUNDRY_LITE_SECRET_AIP_PROMPT_ARTIFACT_ENCRYPTION_KEY" in e2e_gate
    assert "ci-prompt-artifact-key" in e2e_gate
    assert "FOUNDRY_LITE_ALLOW_LOCAL_PROMPT_ARTIFACT_KEY" not in e2e_gate


def test_local_dev_api_sets_aip_secrets_without_local_fallback() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

    dev_script = package["scripts"]["dev"]
    assert "FOUNDRY_LITE_CONNECTOR_PROFILE" in dev_script
    assert ":-rest" in dev_script
    assert "FOUNDRY_LITE_SECRET_AIP_PROMPT_ARTIFACT_ENCRYPTION_KEY" in dev_script
    assert "local-dev-prompt-artifact-key" in dev_script
    assert "FOUNDRY_LITE_SECRET_AIP_CITATION_NAVIGATION_SIGNER" in dev_script
    assert "local-dev-citation-navigation-signer" in dev_script
    assert "FOUNDRY_LITE_ALLOW_LOCAL_PROMPT_ARTIFACT_KEY" not in dev_script


def test_e2e_start_api_sets_aip_secrets_without_local_fallback() -> None:
    script = (ROOT / "scripts" / "e2e_start_api.sh").read_text(encoding="utf-8")

    assert "FOUNDRY_LITE_CONNECTOR_PROFILE" in script
    assert ":-rest" in script
    assert "FOUNDRY_LITE_SECRET_AIP_PROMPT_ARTIFACT_ENCRYPTION_KEY" in script
    assert "e2e-prompt-artifact-key" in script
    assert "FOUNDRY_LITE_SECRET_AIP_CITATION_NAVIGATION_SIGNER" in script
    assert "e2e-citation-navigation-signer" in script
    assert "FOUNDRY_LITE_ALLOW_LOCAL_PROMPT_ARTIFACT_KEY" not in script


def test_doc_drift_is_release_gate_step() -> None:
    script = _static_lane_text()
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_doc_drift.py" in script
    assert '"quality:doc-drift"' in package_json
    assert "pnpm quality:doc-drift" in package_json


def test_checklist_evidence_is_release_gate_step_after_doc_drift() -> None:
    script = _static_lane_text()
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    doc_drift_step = "scripts/quality/check_doc_drift.py"
    checklist_step = "scripts/quality/check_checklist_evidence.py"
    assert checklist_step in script
    assert script.index(doc_drift_step) < script.index(checklist_step)
    assert '"quality:checklist-evidence"' in package_json
    assert "pnpm quality:checklist-evidence" in package_json


def test_infra_tricky_matrix_is_release_gate_step_after_checklist_evidence() -> None:
    script = _static_lane_text()
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    checklist_step = "scripts/quality/check_checklist_evidence.py"
    matrix_step = "scripts/quality/check_infra_tricky_matrix.py"
    assert matrix_step in script
    assert script.index(checklist_step) < script.index(matrix_step)
    assert '"quality:infra-tricky-matrix"' in package_json
    assert "pnpm quality:infra-tricky-matrix" in package_json


def test_generated_sdk_check_is_release_gate_step() -> None:
    script = _static_lane_text()
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert '"scripts/generate_sdk_ts.py", "--check"' in script
    assert '"quality:sdk-generated"' in package_json
    assert "scripts/generate_sdk_ts.py --check" in package_json
    assert "pnpm quality:sdk-generated" in package_json


def test_frontend_backend_surface_gate_runs_after_sdk_generation() -> None:
    script = _static_lane_text()
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    sdk_step = '"scripts/generate_sdk_ts.py", "--check"'
    surface_step = "scripts/quality/check_frontend_backend_surface.py"
    frontend_step = "quality:frontend-foundation"
    schema_step = "scripts/quality/check_schema_revision_guard.py"
    assert sdk_step in script
    assert surface_step in script
    assert frontend_step in script
    assert schema_step in script
    assert '"quality:frontend-backend-surface"' in package_json
    assert "pnpm quality:frontend-backend-surface" in package_json
    assert '"quality:sdk-request-contract"' in package_json
    assert "pnpm --silent quality:sdk-request-contract" in package_json
    assert '"quality:sdk-typecheck"' in package_json
    assert "pnpm --silent quality:sdk-typecheck" in package_json
    assert '"quality:frontend-foundation"' in package_json
    assert "tests/unit/test_quality_frontend_backend_surface.py" in package_json
    assert "tests/unit/test_sdk_ts_generation.py" in package_json
    assert "tests/unit/test_web_operations_ui.py" in package_json
    assert "tests/unit/test_python_osdk.py" in package_json


def test_schema_revision_guard_is_release_gate_step() -> None:
    script = _static_lane_text()
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_schema_revision_guard.py" in script
    assert '"quality:schema-revision"' in package_json
    assert "pnpm quality:schema-revision" in package_json


def test_schema_migration_guard_runs_after_schema_revision_guard() -> None:
    script = _static_lane_text()
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    revision_step = "scripts/quality/check_schema_revision_guard.py"
    migration_step = "scripts/quality/check_schema_migrations.py"
    assert migration_step in script
    assert script.index(revision_step) < script.index(migration_step)
    assert '"quality:schema-migrations"' in package_json
    assert "pnpm quality:schema-migrations" in package_json


def test_schema_migration_singleton_runner_is_release_gate_step() -> None:
    script = _static_lane_text()
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    safety_step = "scripts/quality/check_schema_migrations.py"
    runner_step = "tests/unit/test_migration_runner.py"
    live_runner_step = "pnpm --silent quality:schema-migration-runner-live"
    assert runner_step in script
    assert safety_step in script
    assert '"db:migrate"' in package_json
    assert "scripts/operations/run_migrations.py" in package_json
    assert '"quality:schema-migration-runner"' in package_json
    assert '"quality:schema-migration-runner-live"' in package_json
    assert "tests/contracts/test_migration_runner_postgres_contract.py" in package_json
    assert "pnpm quality:schema-migration-runner" in package_json
    assert live_runner_step in script


def test_schema_evolution_gate_runs_after_data_contracts() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    data_contracts_step = "pnpm --silent quality:data-contracts"
    schema_evolution_step = "pnpm --silent quality:schema-evolution"
    assert schema_evolution_step in script
    assert script.index(data_contracts_step) < script.index(schema_evolution_step)
    assert '"quality:schema-evolution"' in package_json
    assert "tests/unit/test_dataset_schema_evolution.py" in package_json
    assert "tests/integration/test_dataset_quality.py" in package_json


def test_ontology_migration_gate_runs_after_schema_evolution() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    schema_evolution_step = "pnpm --silent quality:schema-evolution"
    ontology_migration_step = "pnpm --silent quality:ontology-migrations"
    assert ontology_migration_step in script
    assert script.index(schema_evolution_step) < script.index(ontology_migration_step)
    assert '"quality:ontology-migrations"' in package_json
    assert "tests/unit/test_ontology_migration.py" in package_json
    assert "tests/integration/test_ontology_action_security.py" in package_json
    assert "tests/unit/test_sdk_ts_generation.py" in package_json


def test_observability_detector_gate_runs_after_ontology_migrations() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    ontology_step = "pnpm --silent quality:ontology-migrations"
    detector_step = "pnpm --silent quality:observability-detectors"
    slo_step = "pnpm --silent quality:slo-contracts"
    assert detector_step in script
    assert slo_step in script
    assert script.index(ontology_step) < script.index(detector_step) < script.index(slo_step)
    assert '"quality:observability-detectors"' in package_json
    assert '"quality:slo-contracts"' in package_json
    assert "tests/unit/test_observability_detectors.py" in package_json
    assert "tests/smoke/test_interfaces.py" in package_json


def test_backup_restore_preflight_gate_runs_after_slo_contracts() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    slo_step = "pnpm --silent quality:slo-contracts"
    backup_restore_step = "pnpm --silent quality:backup-restore"
    elasticsearch_step = "pnpm --silent quality:elasticsearch"
    assert backup_restore_step in script
    assert script.index(slo_step) < script.index(backup_restore_step) < script.index(elasticsearch_step)
    assert '"quality:backup-restore"' in package_json
    assert "tests/unit/test_backup_restore_preflight.py" in package_json
    assert "tests/smoke/test_interfaces.py" in package_json
    assert "tests/unit/test_sdk_ts_generation.py" in package_json
    assert "post_restore_closed_loop" in package_json
    assert "restore_resume_requires_closed_loop_validation" in package_json


def test_auth_secret_gate_follows_restore_gate() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    backup_restore_step = "pnpm --silent quality:backup-restore"
    auth_secret_step = "pnpm --silent quality:auth-secrets"
    privacy_step = "pnpm --silent quality:privacy"
    assert auth_secret_step in script
    assert script.index(backup_restore_step) < script.index(auth_secret_step) < script.index(privacy_step)
    assert '"quality:auth-secrets"' in package_json
    assert "tests/contracts/test_secret_provider_contract.py" in package_json
    assert "tests/contracts/test_rest_connector_adapter_contract.py" in package_json
    assert "tests/contracts/test_auth_provider_contract.py" in package_json
    assert "tests/smoke/test_interfaces.py" in package_json
    assert "webhook_ingest_service_principal" in package_json


def test_privacy_gate_runs_after_auth_secret_gate() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    auth_secret_step = "pnpm --silent quality:auth-secrets"
    privacy_step = "pnpm --silent quality:privacy"
    erasure_step = "pnpm --silent quality:erasure"
    assert privacy_step in script
    assert script.index(auth_secret_step) < script.index(privacy_step) < script.index(erasure_step)
    assert '"quality:privacy"' in package_json
    assert "replication_policy" in package_json
    assert "reversible_mapping" in package_json
    assert "openlineage" in package_json
    assert "tests/unit/test_privacy_transform.py" in package_json


def test_erasure_gate_runs_after_privacy_gate() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    privacy_step = "pnpm --silent quality:privacy"
    erasure_step = "pnpm --silent quality:erasure"
    ai_ledger_step = "pnpm --silent quality:ai-ledger"
    assert erasure_step in script
    assert script.index(privacy_step) < script.index(erasure_step) < script.index(ai_ledger_step)
    assert '"quality:erasure"' in package_json
    assert "tests/unit/test_erasure_lifecycle.py" in package_json


def test_ai_ledger_gate_runs_after_erasure_gate() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    erasure_step = "pnpm --silent quality:erasure"
    ai_ledger_step = "pnpm --silent quality:ai-ledger"
    model_gateway_ledger_step = "pnpm --silent quality:model-gateway-ledger"
    assert ai_ledger_step in script
    assert script.index(erasure_step) < script.index(ai_ledger_step) < script.index(model_gateway_ledger_step)
    assert '"quality:ai-ledger"' in package_json
    assert "tests/contracts/test_ai_run_repository_contract.py" in package_json


def test_model_gateway_ledger_gate_runs_after_ai_ledger_before_prompt_artifacts() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    ai_ledger_step = "pnpm --silent quality:ai-ledger"
    model_gateway_ledger_step = "pnpm --silent quality:model-gateway-ledger"
    prompt_artifacts_step = "pnpm --silent quality:prompt-artifacts"
    assert model_gateway_ledger_step in script
    assert script.index(ai_ledger_step) < script.index(model_gateway_ledger_step) < script.index(prompt_artifacts_step)
    assert '"quality:model-gateway-ledger"' in package_json
    assert "gateway_records_model_call" in package_json
    assert "gateway_requires_seeded_ai_run" in package_json
    assert "gateway_records_failed_provider_attempt" in package_json


def test_prompt_artifacts_gate_runs_after_model_gateway_before_context_compiler() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    model_gateway_ledger_step = "pnpm --silent quality:model-gateway-ledger"
    prompt_artifacts_step = "pnpm --silent quality:prompt-artifacts"
    prompt_artifact_access_step = "pnpm --silent quality:prompt-artifact-access"
    context_compiler_step = "pnpm --silent quality:context-compiler"
    assert prompt_artifacts_step in script
    assert (
        script.index(model_gateway_ledger_step)
        < script.index(prompt_artifacts_step)
        < script.index(prompt_artifact_access_step)
        < script.index(context_compiler_step)
    )
    assert '"quality:prompt-artifacts"' in package_json
    assert '"quality:prompt-artifact-access"' in package_json
    assert "tests/contracts/test_prompt_artifact_store_contract.py" in package_json
    assert "tests/unit/test_prompt_artifact_service.py" in package_json
    assert "tests/unit/test_infrastructure_repository_edges.py" in package_json
    assert "agent_runtime_fails_before_model_when_prompt_artifact_write_fails" in package_json
    assert "api_aip_agent_run_calls_model_and_links_operations_detail" in package_json


def test_aip_roadmap_quality_gates_are_package_and_ci_covered() -> None:
    roadmap = (ROOT / "docs" / "quality-gate-roadmap.md").read_text(encoding="utf-8")
    ci_gate = _static_lane_text()
    scripts = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["scripts"]
    aip_section = roadmap.split("### AIP P0c", maxsplit=1)[1].split("### S60", maxsplit=1)[0]

    referenced_gates = sorted(set(re.findall(r"`(?:pnpm --silent |pnpm )?(quality:[a-z0-9-]+)`", aip_section)))
    missing_package_scripts = [gate for gate in referenced_gates if gate not in scripts]

    covered_gates = set(re.findall(r"quality:[a-z0-9-]+", ci_gate))
    changed = True
    while changed:
        changed = False
        for gate in list(covered_gates):
            for nested_gate in re.findall(r"quality:[a-z0-9-]+", scripts.get(gate, "")):
                if nested_gate in scripts and nested_gate not in covered_gates:
                    covered_gates.add(nested_gate)
                    changed = True

    missing_ci_coverage = []
    for gate in referenced_gates:
        command = scripts.get(gate, "")
        command_paths = re.findall(r"(?:scripts|tests)/[A-Za-z0-9_./-]+", command)
        has_direct_command = any(path in ci_gate for path in command_paths)
        if gate not in covered_gates and not has_direct_command:
            missing_ci_coverage.append(gate)

    assert not missing_package_scripts
    assert not missing_ci_coverage


def test_ai_evidence_gate_runs_after_ai_ledger_gate() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    ai_ledger_step = "pnpm --silent quality:ai-ledger"
    model_gateway_ledger_step = "pnpm --silent quality:model-gateway-ledger"
    ai_operations_step = "pnpm --silent quality:ai-operations"
    logic_runtime_step = "pnpm --silent quality:logic-runtime"
    ai_evidence_step = "pnpm --silent quality:ai-evidence"
    insight_review_step = "pnpm --silent quality:insight-review"
    assert ai_evidence_step in script
    assert script.index(ai_ledger_step) < script.index(model_gateway_ledger_step)
    assert script.index(model_gateway_ledger_step) < script.index(ai_operations_step) < script.index(logic_runtime_step)
    assert script.index(logic_runtime_step) < script.index(ai_evidence_step)
    assert script.index(ai_evidence_step) < script.index(insight_review_step)
    assert '"quality:ai-evidence"' in package_json
    assert "tests/unit/test_ai_evidence.py" in package_json
    assert "object_property_lineage" in package_json


def test_ai_operations_gate_runs_after_approval_execution_gate() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    approval_execution_step = "pnpm --silent quality:approval-execution"
    ai_operations_step = "pnpm --silent quality:ai-operations"
    logic_runtime_step = "pnpm --silent quality:logic-runtime"
    assert ai_operations_step in script
    assert script.index(approval_execution_step) < script.index(ai_operations_step) < script.index(logic_runtime_step)
    assert '"quality:ai-operations"' in package_json
    assert "tests/unit/test_ai_operations_detail.py" in package_json
    assert "tests/smoke/test_interfaces.py" in package_json
    assert "test_ai_operations_run_list_and_detail_expose_safe_ledger_trace" in package_json
    assert "api_ai_operations" in package_json
    assert "runtime_run_cursor_is_signed_scoped_expiring_and_key_rotatable" in package_json
    assert "protected_runtime_profile_requires_operations_cursor_secret" in package_json


def test_logic_runtime_gate_runs_after_ai_operations_before_ai_evidence() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    ai_operations_step = "pnpm --silent quality:ai-operations"
    logic_runtime_step = "pnpm --silent quality:logic-runtime"
    ai_evals_step = "pnpm --silent quality:ai-evals"
    assert logic_runtime_step in script
    assert script.index(ai_operations_step) < script.index(logic_runtime_step) < script.index(ai_evals_step)
    assert '"quality:logic-runtime"' in package_json
    assert "tests/unit/test_logic_runtime.py" in package_json


def test_ai_evals_and_release_gates_run_after_logic_runtime_before_ai_evidence() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    logic_runtime_step = "pnpm --silent quality:logic-runtime"
    ai_evals_step = "pnpm --silent quality:ai-evals"
    ai_release_step = "pnpm --silent quality:ai-release"
    visual_builder_step = "pnpm --silent quality:visual-builder"
    assert ai_evals_step in script
    assert ai_release_step in script
    assert script.index(logic_runtime_step) < script.index(ai_evals_step) < script.index(ai_release_step)
    assert script.index(ai_release_step) < script.index(visual_builder_step)
    assert '"quality:ai-evals"' in package_json
    assert '"quality:ai-release"' in package_json
    assert "tests/contracts/test_ai_eval_repository_contract.py" in package_json
    assert "tests/unit/test_ai_eval_service.py" in package_json


def test_visual_builder_gate_runs_after_ai_release_before_ai_evidence() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    ai_release_step = "pnpm --silent quality:ai-release"
    visual_builder_step = "pnpm --silent quality:visual-builder"
    builder_runtime_step = "pnpm --silent quality:builder-runtime"
    assert visual_builder_step in script
    assert script.index(ai_release_step) < script.index(visual_builder_step) < script.index(builder_runtime_step)
    assert '"quality:visual-builder"' in package_json
    assert "tests/unit/test_visual_builder.py" in package_json
    assert "api_aip_builder" in package_json


def test_builder_runtime_gate_runs_after_visual_builder_before_ai_evidence() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    visual_builder_step = "pnpm --silent quality:visual-builder"
    builder_runtime_step = "pnpm --silent quality:builder-runtime"
    ai_evidence_step = "pnpm --silent quality:ai-evidence"
    assert builder_runtime_step in script
    assert script.index(visual_builder_step) < script.index(builder_runtime_step) < script.index(ai_evidence_step)
    assert '"quality:builder-runtime"' in package_json
    assert "tests/unit/test_builder_runtime_execution.py" in package_json
    assert "api_aip_builder_run" in package_json


def test_agent_runtime_gate_runs_after_builder_runtime_before_ai_evidence() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    builder_runtime_step = "pnpm --silent quality:builder-runtime"
    retrieval_orchestrator_step = "pnpm --silent quality:retrieval-orchestrator"
    agent_runtime_step = "pnpm --silent quality:agent-runtime"
    ai_evidence_step = "pnpm --silent quality:ai-evidence"
    assert agent_runtime_step in script
    assert script.index(builder_runtime_step) < script.index(agent_runtime_step) < script.index(ai_evidence_step)
    assert script.index(retrieval_orchestrator_step) < script.index(agent_runtime_step)
    assert '"quality:agent-runtime"' in package_json
    assert "tests/unit/test_agent_runtime.py" in package_json
    assert "api_aip_agent_run" in package_json


def test_agent_tool_loop_gate_runs_after_agent_runtime_before_citations() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    agent_runtime_step = "pnpm --silent quality:agent-runtime"
    agent_tool_loop_step = "pnpm --silent quality:agent-tool-loop"
    agent_action_proposal_step = "pnpm --silent quality:agent-action-proposal-tool"
    agent_citation_step = "pnpm --silent quality:agent-runtime-citations"
    assert agent_tool_loop_step in script
    assert (
        script.index(agent_runtime_step) < script.index(agent_tool_loop_step) < script.index(agent_action_proposal_step)
    )
    assert script.index(agent_action_proposal_step) < script.index(agent_citation_step)
    assert '"quality:agent-tool-loop"' in package_json
    assert "agent_runtime_executes_one_model_tool_call_through_broker_and_finishes" in package_json
    assert "api_aip_agent_run_executes_brokered_tool_loop" in package_json


def test_agent_action_proposal_tool_gate_runs_after_tool_loop_before_citations() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    agent_tool_loop_step = "pnpm --silent quality:agent-tool-loop"
    agent_action_proposal_step = "pnpm --silent quality:agent-action-proposal-tool"
    agent_approval_execution_step = "pnpm --silent quality:agent-approval-execution-api"
    agent_citation_step = "pnpm --silent quality:agent-runtime-citations"
    assert agent_action_proposal_step in script
    assert (
        script.index(agent_tool_loop_step)
        < script.index(agent_action_proposal_step)
        < script.index(agent_approval_execution_step)
        < script.index(agent_citation_step)
    )
    assert '"quality:agent-action-proposal-tool"' in package_json
    assert "agent_runtime_action_propose_tool" in package_json
    assert "agent_runtime_direct_write_tool_is_denied" in package_json
    assert "api_aip_agent_run_proposes_action_for_human_review" in package_json


def test_agent_vendor_egress_gate_runs_after_action_proposal_before_approval() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    action_proposal_step = "pnpm --silent quality:agent-action-proposal-tool"
    vendor_egress_step = "pnpm --silent quality:agent-vendor-egress"
    approval_execution_step = "pnpm --silent quality:agent-approval-execution-api"
    assert vendor_egress_step in script
    assert script.index(action_proposal_step) < script.index(vendor_egress_step)
    assert script.index(vendor_egress_step) < script.index(approval_execution_step)
    assert '"quality:agent-vendor-egress"' in package_json
    assert "test_tool_broker_rejects_direct_vendor_api_tool_even_when_manifest_published" in package_json
    assert "test_agent_runtime_rejects_direct_vendor_api_tool_without_executor" in package_json
    assert "test_visual_builder_blocks_direct_vendor_tool_ids" in package_json


def test_agent_approval_execution_api_gate_runs_after_action_proposal_before_citations() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    agent_action_proposal_step = "pnpm --silent quality:agent-action-proposal-tool"
    agent_approval_execution_step = "pnpm --silent quality:agent-approval-execution-api"
    agent_citation_step = "pnpm --silent quality:agent-runtime-citations"
    assert agent_approval_execution_step in script
    assert script.index(agent_action_proposal_step) < script.index(agent_approval_execution_step)
    assert script.index(agent_approval_execution_step) < script.index(agent_citation_step)
    assert '"quality:agent-approval-execution-api"' in package_json
    assert "approval_execution_links_originating_agent_tool_call" in package_json
    assert "api_aip_agent_run_proposal_can_be_approved_and_executed" in package_json


def test_agent_approval_execution_idempotency_gate_runs_after_api_before_citations() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    agent_approval_execution_step = "pnpm --silent quality:agent-approval-execution-api"
    agent_idempotency_step = "pnpm --silent quality:agent-approval-execution-idempotency"
    agent_citation_step = "pnpm --silent quality:agent-runtime-citations"
    assert agent_idempotency_step in script
    assert script.index(agent_approval_execution_step) < script.index(agent_idempotency_step)
    assert script.index(agent_idempotency_step) < script.index(agent_citation_step)
    assert '"quality:agent-approval-execution-idempotency"' in package_json
    assert "approval_execution_runs_approved_proposal_once_and_links_review" in package_json
    assert "api_aip_agent_run_proposal_can_be_approved_and_executed" in package_json


def test_agent_runtime_citation_gate_runs_after_agent_runtime_before_ai_evidence() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    citation_service_step = "pnpm --silent quality:citation-service"
    agent_runtime_step = "pnpm --silent quality:agent-runtime"
    agent_tool_loop_step = "pnpm --silent quality:agent-tool-loop"
    agent_action_proposal_step = "pnpm --silent quality:agent-action-proposal-tool"
    agent_approval_execution_step = "pnpm --silent quality:agent-approval-execution-api"
    agent_citation_step = "pnpm --silent quality:agent-runtime-citations"
    ai_evidence_step = "pnpm --silent quality:ai-evidence"
    assert agent_citation_step in script
    assert script.index(citation_service_step) < script.index(agent_citation_step)
    assert script.index(agent_runtime_step) < script.index(agent_tool_loop_step)
    assert script.index(agent_tool_loop_step) < script.index(agent_action_proposal_step)
    assert script.index(agent_action_proposal_step) < script.index(agent_approval_execution_step)
    assert script.index(agent_approval_execution_step) < script.index(agent_citation_step)
    assert script.index(agent_citation_step) < script.index(ai_evidence_step)
    assert '"quality:agent-tool-loop"' in package_json
    assert '"quality:agent-action-proposal-tool"' in package_json
    assert '"quality:agent-approval-execution-api"' in package_json
    assert "agent_runtime_executes_one_model_tool_call_through_broker_and_finishes" in package_json
    assert "api_aip_agent_run_executes_brokered_tool_loop" in package_json
    assert '"quality:agent-runtime-citations"' in package_json
    assert "agent_runtime_resolves_model_citations" in package_json
    assert "agent_runtime_rejects_forged_model_citation" in package_json


def test_agent_citation_ui_gate_runs_after_agent_runtime_citations_before_ai_evidence() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    agent_citation_step = "pnpm --silent quality:agent-runtime-citations"
    agent_citation_ui_step = "pnpm --silent quality:agent-citation-ui"
    ai_evidence_step = "pnpm --silent quality:ai-evidence"
    assert agent_citation_ui_step in script
    assert script.index(agent_citation_step) < script.index(agent_citation_ui_step) < script.index(ai_evidence_step)
    assert '"quality:agent-citation-ui"' in package_json
    assert "schema_requested_citation_claim" in package_json
    assert "valid_citation_mapping" in package_json
    assert "citation_schema_inside_lists" in package_json
    assert "agent_runtime_forwards_output_schema_to_model_request" in package_json
    assert "operations_ui_record_dlq_retry_shows_result" in package_json


def test_agent_source_preview_gate_runs_after_agent_citation_ui_before_ai_evidence() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    agent_citation_ui_step = "pnpm --silent quality:agent-citation-ui"
    agent_source_preview_step = "pnpm --silent quality:agent-source-previews"
    ai_evidence_step = "pnpm --silent quality:ai-evidence"
    assert agent_source_preview_step in script
    assert script.index(agent_citation_ui_step) < script.index(agent_source_preview_step)
    assert script.index(agent_source_preview_step) < script.index(ai_evidence_step)
    assert '"quality:agent-source-previews"' in package_json
    assert "source_preview" in package_json
    assert "agent_runtime_resolves_model_citations" in package_json
    assert "operations_ui_record_dlq_retry_shows_result" in package_json


def test_agent_inline_citation_gate_runs_after_source_preview_before_ai_evidence() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    agent_source_preview_step = "pnpm --silent quality:agent-source-previews"
    agent_inline_citation_step = "pnpm --silent quality:agent-inline-citations"
    ai_evidence_step = "pnpm --silent quality:ai-evidence"
    assert agent_inline_citation_step in script
    assert script.index(agent_source_preview_step) < script.index(agent_inline_citation_step)
    assert script.index(agent_inline_citation_step) < script.index(ai_evidence_step)
    assert '"quality:agent-inline-citations"' in package_json
    assert "citationAnchorButton" in (ROOT / "apps" / "web" / "index.html").read_text(encoding="utf-8")
    assert "agent_inline_citation_gate" in package_json


def test_retrieval_orchestrator_gate_runs_after_context_compiler_before_agent_runtime() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    context_compiler_step = "pnpm --silent quality:context-compiler"
    retrieval_orchestrator_step = "pnpm --silent quality:retrieval-orchestrator"
    agent_runtime_step = "pnpm --silent quality:agent-runtime"
    assert retrieval_orchestrator_step in script
    assert script.index(context_compiler_step) < script.index(retrieval_orchestrator_step)
    assert script.index(retrieval_orchestrator_step) < script.index(agent_runtime_step)
    assert '"quality:retrieval-orchestrator"' in package_json
    assert "tests/unit/test_retrieval_orchestrator.py" in package_json
    assert "retrieval_orchestrator_gate" in package_json


def test_retrieval_document_context_gate_runs_after_object_orchestrator_before_agent_runtime() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    retrieval_orchestrator_step = "pnpm --silent quality:retrieval-orchestrator"
    retrieval_document_step = "pnpm --silent quality:retrieval-document-context"
    agent_runtime_step = "pnpm --silent quality:agent-runtime"
    assert retrieval_document_step in script
    assert script.index(retrieval_orchestrator_step) < script.index(retrieval_document_step)
    assert script.index(retrieval_document_step) < script.index(agent_runtime_step)
    assert '"quality:retrieval-document-context"' in package_json
    assert "tests/unit/test_media_content_search.py" in package_json
    assert "retrieval_document_context_gate" in package_json


def test_insight_review_gate_runs_after_ai_evidence_gate() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    ai_evidence_step = "pnpm --silent quality:ai-evidence"
    insight_review_step = "pnpm --silent quality:insight-review"
    operations_recovery_step = "pnpm --silent quality:operations-recovery"
    elasticsearch_step = "pnpm --silent quality:elasticsearch"
    assert insight_review_step in script
    assert operations_recovery_step in script
    assert (
        script.index(ai_evidence_step)
        < script.index(insight_review_step)
        < script.index(operations_recovery_step)
        < script.index(elasticsearch_step)
    )
    assert '"quality:insight-review"' in package_json
    assert "tests/contracts/test_insight_review_repository_contract.py" in package_json
    assert "test_insight_review_create_is_idempotent_by_tenant_and_key" in package_json
    assert "test_insight_review_decision_has_one_terminal_winner" in package_json
    assert "test_api_insight_reviews_create_assign_and_decide_with_audit_evidence" in package_json


def test_operations_recovery_gate_runs_after_insight_review_gate() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    insight_review_step = "pnpm --silent quality:insight-review"
    operations_recovery_step = "pnpm --silent quality:operations-recovery"
    elasticsearch_step = "pnpm --silent quality:elasticsearch"
    assert operations_recovery_step in script
    assert script.index(insight_review_step) < script.index(operations_recovery_step) < script.index(elasticsearch_step)
    assert '"quality:operations-recovery"' in package_json
    assert "test_backup_restore_recovery_overview_summarizes_active_restore_and_preflight" in package_json
    assert "test_restore_resume_requires_closed_loop_validation" in package_json
    assert "test_post_restore_closed_loop_smoke" in package_json
    assert "test_api_backup_restore_mode_start_and_status_return_pause_gate" in package_json
    assert "tests/sdk/request_contract.mjs" in package_json
    assert "operations_recovery_gate" in package_json


def test_distributed_control_plane_gate_runs_after_infra_composition() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    infra_composition_step = "pnpm --silent quality:infra-composition"
    distributed_step = "pnpm --silent quality:distributed-control-plane"
    assert distributed_step in script
    assert script.index(infra_composition_step) < script.index(distributed_step)
    assert '"quality:distributed-control-plane"' in package_json
    assert "test_postgres_control_plane_iceberg_s3_spark_failure_aborts_once" in package_json
    assert "test_kafka_live_broker_event_archives_through_worker" in package_json
    assert "test_product_connector_sync_workflow_runs_through_temporal_and_audits" in package_json
    assert "test_runtime_repository_contract_workflow_run_ledger_status_cas" in package_json
    replay_lease_test = "test_dataset_transaction_repository_contract_dead_letter_replay_lease_fences_terminal_updates"
    assert replay_lease_test in package_json


def test_tier_coverage_gate_includes_app_layers() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "--cov=libs/foundry_lite" in script
    assert "--cov=apps/api" in script
    assert "--cov=apps/cli" in script
    assert "--cov=apps/worker" in script
    assert "scripts/quality/check_tier_coverage_by_layer.py artifacts/coverage/coverage.json --threshold 95" in script
    assert '"quality:tier-coverage"' in package_json
    assert "--cov=apps/api --cov=apps/cli --cov=apps/worker" in package_json


def test_default_ci_gate_is_fast_local_feedback_not_full_serial_release() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert '"ci:gate": "bash scripts/ci_gate.sh local"' in package_json
    assert '"ci:gate:local": "bash scripts/ci_gate.sh local"' in package_json
    assert '"ci:gate:all": "bash scripts/ci_gate.sh all"' in package_json
    assert 'local lane="${1:-local}"' in script
    assert "run_local_gate()" in script
    assert "uv run pytest tests --tach --no-header -q" in script
    assert "run_static_gate\n  run_impact_gate" in script
    assert "run_coverage_gate\n  run_flaky_gate\n  run_runtime_full_gate\n  run_e2e_gate" in script


def test_github_ci_installs_gitleaks_before_release_gate() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "actions/setup-go" in workflow
    assert "go install github.com/zricethezav/gitleaks/v8@v8.30.1" in workflow
    assert workflow.index("Install gitleaks") < workflow.index("Run static quality lane")


def test_codeql_workflow_fails_on_sarif_findings() -> None:
    workflow = (ROOT / ".github" / "workflows" / "codeql.yml").read_text(encoding="utf-8")

    assert "github/codeql-action/init@v4" in workflow
    assert "github/codeql-action/analyze@v4" in workflow
    assert "python scripts/quality/codeql/fail_on_sarif_findings.py codeql-results" in workflow


def test_ast_grep_and_tach_are_release_gate_steps() -> None:
    script = _static_lane_text()
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")
    tach_config = (ROOT / "tach.toml").read_text(encoding="utf-8")

    assert '"tach", "check", "--dependencies"' in script
    assert "scripts/quality/run_ast_grep.cjs" in script
    assert "sgconfig.yml" in script
    assert '"quality:ast-grep"' in package_json
    assert "pnpm quality:architecture" in package_json
    assert "forbid_circular_dependencies = true" in tach_config
    assert 'path = "foundry_lite.application"' in tach_config
    assert 'path = "foundry_lite_worker"' in tach_config
    assert '"foundry_lite.infrastructure"' in tach_config


def test_worker_composition_root_is_import_linter_allowlisted() -> None:
    import_linter_config = (ROOT / ".importlinter").read_text(encoding="utf-8")

    assert "foundry_lite_worker.stream_archive -> foundry_lite.infrastructure.local_runtime" in import_linter_config
    assert "foundry_lite_worker.source_streaming -> foundry_lite.infrastructure.local_runtime" in import_linter_config
    assert "foundry_lite_worker -> foundry_lite.infrastructure" not in import_linter_config


def test_ast_grep_facade_magic_rule_has_a_failing_fixture(tmp_path: Path) -> None:
    core_path = tmp_path / "libs" / "foundry_lite" / "application" / "facades" / "action_gateway.py"
    core_path.parent.mkdir(parents=True)
    core_path.write_text(
        "class ActionGateway:\n    def __getattr__(self, name):\n        return None\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            "node",
            str(ROOT / "scripts" / "quality" / "run_ast_grep.cjs"),
            "scan",
            "--rule",
            str(ROOT / "scripts" / "quality" / "ast-grep-rules" / "no-facade-magic-dispatch.yml"),
            "libs/foundry_lite/application/facades/action_gateway.py",
            "--json=compact",
        ],
        check=False,
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    findings = json.loads(result.stdout)
    assert findings[0]["ruleId"] == "foundry-lite-no-facade-magic-dispatch"
