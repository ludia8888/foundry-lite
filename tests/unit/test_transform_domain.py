from __future__ import annotations

import pytest
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.domain.transform.graph import validate_transform_graph_bounds
from foundry_lite.domain.transform.rules import (
    normalize_transform_language,
    normalize_transform_output_mode,
    output_transaction_type_for_mode,
    safe_transform_path_token,
)
from foundry_lite.domain.transform.scheduler import (
    TransformScheduleSpec,
    decide_transform_schedule,
)


def test_transform_language_mode_and_path_token_rules_are_domain_owned() -> None:
    assert normalize_transform_language(" SQL ") == "sql"
    assert normalize_transform_language("python") == "python"
    assert normalize_transform_output_mode("incremental") == "append"
    assert output_transaction_type_for_mode("append") == "APPEND"
    assert output_transaction_type_for_mode("snapshot") == "SNAPSHOT"
    assert safe_transform_path_token("tenant/demo", "tenant_id") == "tenant_demo"
    with pytest.raises(ValidationFailed, match="unsupported transform language"):
        normalize_transform_language("java")
    with pytest.raises(ValidationFailed, match="unsupported transform output mode"):
        normalize_transform_output_mode("streaming")
    with pytest.raises(ValidationFailed, match="must contain at least one safe character"):
        safe_transform_path_token("!!!", "api_name")


def test_transform_scheduler_specification_reasons() -> None:
    assert _decision(mode="append").reason == "append_mode_requires_manual_run"
    assert _decision(active_run_id="run_active").reason == "active_run_in_progress"
    assert _decision(missing_input_refs=("raw.orders",)).reason == "input_version_missing"
    assert _decision(last_successful_run_id=None).reason == "never_built"
    changed = _decision(
        last_successful_run_id="run_old",
        previous={"raw.orders": "v1"},
        latest={"raw.orders": "v2"},
    )
    assert changed.reason == "input_version_changed"
    assert changed.changed_input_refs == ("raw.orders",)
    up_to_date = _decision(
        last_successful_run_id="run_old",
        previous={"raw.orders": "v1"},
        latest={"raw.orders": "v1"},
    )
    assert up_to_date.reason == "up_to_date"


def test_transform_graph_bounds_are_domain_owned() -> None:
    assert validate_transform_graph_bounds(7, 50) == (7, 50)
    with pytest.raises(ValidationFailed, match="transform graph bounds"):
        validate_transform_graph_bounds(0, 1)
    with pytest.raises(ValidationFailed, match="transform graph bounds"):
        validate_transform_graph_bounds(1, 51)


def _decision(
    *,
    mode: str = "snapshot",
    active_run_id: str | None = None,
    missing_input_refs: tuple[str, ...] = (),
    last_successful_run_id: str | None = "run_previous",
    previous: dict[str, str] | None = None,
    latest: dict[str, str] | None = None,
):
    return decide_transform_schedule(
        TransformScheduleSpec(
            api_name="clean_orders",
            transform_id="tf_clean_orders",
            output_dataset_ref="clean.orders",
            mode=mode,
            latest_input_versions=latest or {"raw.orders": "v1"},
            missing_input_refs=missing_input_refs,
            last_successful_input_versions=previous or {},
            active_run_id=active_run_id,
            last_successful_run_id=last_successful_run_id,
        )
    )
