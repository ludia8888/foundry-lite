"""Isolated Pipeline DAG node runner used for cooperative hard cancellation."""

from __future__ import annotations

import json
import os
import sys

from foundry_lite.application.foundry import FoundryLite
from foundry_lite.domain.context import DEMO_ADMIN_ROLES, RequestContext
from foundry_lite.infrastructure.local_runtime import create_runtime_core_dependencies

_RESULT_PREFIX = "FOUNDRY_LITE_PIPELINE_RESULT="


def main() -> None:
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict):
        raise TypeError("pipeline node payload must be an object")
    foundry = FoundryLite(
        dependencies=create_runtime_core_dependencies(
            db_url=os.getenv("FOUNDRY_LITE_DB_URL"),
            storage_root=os.getenv("FOUNDRY_LITE_STORAGE_ROOT"),
        )
    )
    try:
        result = _drive(foundry, payload)
        sys.stdout.write(f"{_RESULT_PREFIX}{json.dumps(result, separators=(',', ':'))}\n")
    finally:
        foundry.close()


def _drive(foundry: FoundryLite, payload: dict[str, object]) -> dict[str, object]:
    if payload.get("is_commit_forbidden") is not True:
        return foundry._services.pipelines.distributed_node.drive(payload)
    ctx = RequestContext(
        tenant_id=str(payload["tenant_id"]),
        actor_user_id="pipeline-preview-worker",
        request_id=str(payload["request_id"]),
        roles=DEMO_ADMIN_ROLES,
    )
    preview_run_id = str(payload["run_id"])
    return foundry._services.pipelines.preview.execute_preview_run(preview_run_id, ctx=ctx)


if __name__ == "__main__":
    main()
