from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_ci_gate_does_not_run_heavy_codeql_locally() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")

    assert "scripts/quality/codeql/run.sh" not in script
    assert "CodeQL P7 is intentionally not run" in script


def test_gitleaks_missing_is_release_blocker() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")

    assert "FOUNDRY_LITE_STRICT_EXTERNAL_TOOLS" in script
    assert "${CI:-}" in script
    assert "CI/release evidence cannot skip the P9 secret scan" in script


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

    coverage_step = "uv run pytest tests \\\n  --cov=libs/foundry_lite"
    flaky_step = "scripts/quality/check_flaky_detector.py"
    assert flaky_step in script
    assert "--iterations 3" in script
    assert '--command "uv run pytest tests -n auto --no-header -q"' in script
    assert script.index(coverage_step) < script.index(flaky_step)
    assert '"quality:flaky-detector"' in package_json


def test_pragma_no_cover_budget_is_release_gate_step() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_pragma_no_cover_budget.py --baseline 0" in script
    assert '"quality:pragma-budget"' in package_json
    assert "pnpm quality:pragma-budget" in package_json


def test_audit_on_mutation_is_release_gate_step() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_audit_on_mutation.py" in script
    assert '"quality:audit-mutation"' in package_json
    assert "pnpm quality:audit-mutation" in package_json


def test_transaction_outbox_pair_is_release_gate_step() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_transaction_outbox_pair.py" in script
    assert '"quality:transaction-outbox"' in package_json
    assert "pnpm quality:transaction-outbox" in package_json


def test_action_idempotency_is_release_gate_step() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_idempotency_on_action.py" in script
    assert '"quality:action-idempotency"' in package_json
    assert "pnpm quality:action-idempotency" in package_json


def test_error_response_request_id_is_release_gate_step() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_error_response_has_request_id.py" in script
    assert '"quality:error-request-id"' in package_json
    assert "pnpm quality:error-request-id" in package_json


def test_function_length_is_release_gate_step() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_function_length.py" in script
    assert '"quality:function-length"' in package_json
    assert "pnpm quality:function-length" in package_json


def test_boolean_naming_is_release_gate_step() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_boolean_naming.py" in script
    assert '"quality:boolean-naming"' in package_json
    assert "pnpm quality:boolean-naming" in package_json


def test_dict_any_budget_is_release_gate_step() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_dict_any_budget.py" in script
    assert '"quality:dict-any-budget"' in package_json
    assert "pnpm quality:dict-any-budget" in package_json


def test_log_trace_keys_is_release_gate_step() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_log_has_trace_keys.py" in script
    assert '"quality:log-trace-keys"' in package_json
    assert "pnpm quality:log-trace-keys" in package_json


def test_metrics_exposed_is_release_gate_step() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_metrics_exposed.py" in script
    assert '"quality:metrics-exposed"' in package_json
    assert "pnpm quality:metrics-exposed" in package_json


def test_adapter_failure_taxonomy_is_release_gate_step() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_adapter_failure_taxonomy.py" in script
    assert '"quality:adapter-failure-taxonomy"' in package_json
    assert "pnpm quality:adapter-failure-taxonomy" in package_json


def test_router_layer_purity_is_release_gate_step() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_router_layer_purity.py" in script
    assert '"quality:router-purity"' in package_json
    assert "pnpm quality:router-purity" in package_json


def test_query_side_effects_is_release_gate_step() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_query_side_effects.py" in script
    assert '"quality:query-side-effects"' in package_json
    assert "pnpm quality:query-side-effects" in package_json


def test_repository_no_business_is_release_gate_step() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_repository_no_business.py" in script
    assert '"quality:repository-no-business"' in package_json
    assert "pnpm quality:repository-no-business" in package_json


def test_tenant_write_guard_is_release_gate_step() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_tenant_write_guard.py" in script
    assert '"quality:tenant-write"' in package_json
    assert "pnpm quality:tenant-write" in package_json


def test_contract_test_per_port_is_release_gate_step() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_contract_test_per_port.py" in script
    assert '"quality:contract-tests"' in package_json
    assert "pnpm quality:contract-tests" in package_json


def test_strategy_specification_tests_is_release_gate_step() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_strategy_specification_tests.py" in script
    assert '"quality:strategy-spec-tests"' in package_json
    assert "pnpm quality:strategy-spec-tests" in package_json


def test_integration_scenario_markers_are_release_gate_step() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_integration_scenario_markers.py" in script
    assert '"quality:integration-scenarios"' in package_json
    assert "pnpm quality:integration-scenarios" in package_json


def test_root_cause_meta_gates_are_release_gate_steps() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_regression_test_per_bugfix.py" in script
    assert "scripts/quality/check_pr_root_cause_section.py" in script
    assert '"quality:regression-bugfix"' in package_json
    assert '"quality:pr-root-cause"' in package_json
    assert "pnpm quality:regression-bugfix" in package_json
    assert "pnpm quality:pr-root-cause" in package_json


def test_github_ci_fetches_history_and_checks_pr_root_cause() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "fetch-depth: 0" in workflow
    assert "Check PR root-cause evidence" in workflow
    assert "scripts/quality/check_pr_root_cause_section.py --event-path" in workflow


def test_doc_drift_is_release_gate_step() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_doc_drift.py" in script
    assert '"quality:doc-drift"' in package_json
    assert "pnpm quality:doc-drift" in package_json


def test_generated_sdk_check_is_release_gate_step() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/generate_sdk_ts.py --check" in script
    assert '"quality:sdk-generated"' in package_json
    assert "scripts/generate_sdk_ts.py --check" in package_json
    assert "pnpm quality:sdk-generated" in package_json


def test_schema_revision_guard_is_release_gate_step() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")

    assert "scripts/quality/check_schema_revision_guard.py" in script
    assert '"quality:schema-revision"' in package_json
    assert "pnpm quality:schema-revision" in package_json


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


def test_github_ci_installs_gitleaks_before_release_gate() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "actions/setup-go" in workflow
    assert "go install github.com/zricethezav/gitleaks/v8@v8.30.1" in workflow
    assert workflow.index("Install gitleaks") < workflow.index("Run release gate")


def test_codeql_workflow_fails_on_sarif_findings() -> None:
    workflow = (ROOT / ".github" / "workflows" / "codeql.yml").read_text(encoding="utf-8")

    assert "github/codeql-action/init@v4" in workflow
    assert "github/codeql-action/analyze@v4" in workflow
    assert "python scripts/quality/codeql/fail_on_sarif_findings.py codeql-results" in workflow


def test_ast_grep_and_tach_are_release_gate_steps() -> None:
    script = (ROOT / "scripts" / "ci_gate.sh").read_text(encoding="utf-8")
    package_json = (ROOT / "package.json").read_text(encoding="utf-8")
    tach_config = (ROOT / "tach.toml").read_text(encoding="utf-8")

    assert "uv run tach check --dependencies" in script
    assert "pnpm exec sg scan -c sgconfig.yml" in script
    assert '"quality:ast-grep"' in package_json
    assert "pnpm quality:architecture" in package_json
    assert "forbid_circular_dependencies = true" in tach_config
    assert 'path = "foundry_lite.application"' in tach_config


def test_ast_grep_facade_magic_rule_has_a_failing_fixture(tmp_path: Path) -> None:
    core_path = tmp_path / "libs" / "foundry_lite" / "application" / "core.py"
    core_path.parent.mkdir(parents=True)
    core_path.write_text(
        "class FoundryLiteCore:\n    def __getattr__(self, name):\n        return None\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            str(ROOT / "node_modules" / ".bin" / "sg"),
            "scan",
            "--rule",
            str(ROOT / "scripts" / "quality" / "ast-grep-rules" / "no-facade-magic-dispatch.yml"),
            "libs/foundry_lite/application/core.py",
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
