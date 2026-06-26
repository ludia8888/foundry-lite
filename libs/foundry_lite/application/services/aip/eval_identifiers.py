from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence


def ai_eval_suite_id(tenant_id: str, suite_api_name: str, version: str) -> str:
    return f"aip-eval-suite-{_stable_token([tenant_id, suite_api_name, version])}"


def ai_eval_case_id(suite_id: str, case_api_name: str) -> str:
    return f"aip-eval-case-{_stable_token([suite_id, case_api_name])}"


def ai_eval_result_id(eval_run_id: str, case_api_name: str, sample_index: int, axis: str) -> str:
    return f"aip-eval-result-{_stable_token([eval_run_id, case_api_name, str(sample_index), axis])}"


def ai_agent_release_id(tenant_id: str, agent_version_id: str, release_channel: str) -> str:
    return f"aip-agent-release-{_stable_token([tenant_id, agent_version_id, release_channel])}"


def hash_json(value: object) -> str:
    return f"sha256:{hashlib.sha256(_json_block(value).encode()).hexdigest()}"


def _stable_token(parts: Sequence[str]) -> str:
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()[:24]


def _json_block(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
