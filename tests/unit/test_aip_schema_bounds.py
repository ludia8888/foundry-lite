"""Unit tests for AIP request-model input bounds (DoS hardening)."""

from __future__ import annotations

import pytest
from foundry_lite_api.schemas import AipBuilderValidateRequest, AipEvalRunRequest
from pydantic import ValidationError


def _eval_case(index: int) -> dict[str, object]:
    return {"caseApiName": f"case-{index}", "axis": "security"}


def _eval_payload(case_count: int) -> dict[str, object]:
    return {
        "evalRunId": "run-1",
        "suiteApiName": "suite",
        "suiteVersion": "v1",
        "agentVersionId": "agent-v1",
        "candidateReleaseChannel": "stable",
        "cases": [_eval_case(index) for index in range(case_count)],
    }


def _builder_payload(block_count: int, *, max_logic_blocks: int = 25) -> dict[str, object]:
    return {
        "agentVersionId": "agent-v1",
        "releaseChannel": "dev",
        "modelAliasVersion": "gpt@v1",
        "promptVersionId": "prompt@v1",
        "contextSources": [],
        "toolManifest": [],
        "logicBlocks": [{"blockId": f"b{index}", "kind": "Input"} for index in range(block_count)],
        "evalAxes": ["security"],
        "maxLogicBlocks": max_logic_blocks,
    }


def test_eval_run_request_accepts_up_to_the_case_cap() -> None:
    AipEvalRunRequest.model_validate(_eval_payload(500))


def test_eval_run_request_rejects_too_many_cases() -> None:
    with pytest.raises(ValidationError):
        AipEvalRunRequest.model_validate(_eval_payload(501))


def test_builder_validate_request_rejects_too_many_logic_blocks() -> None:
    with pytest.raises(ValidationError):
        AipBuilderValidateRequest.model_validate(_builder_payload(501))


def test_builder_validate_request_rejects_out_of_range_max_logic_blocks() -> None:
    with pytest.raises(ValidationError):
        AipBuilderValidateRequest.model_validate(_builder_payload(1, max_logic_blocks=0))
    with pytest.raises(ValidationError):
        AipBuilderValidateRequest.model_validate(_builder_payload(1, max_logic_blocks=501))
