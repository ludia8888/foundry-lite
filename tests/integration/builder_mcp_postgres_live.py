"""Official MCP client -> strict OAuth API process -> PostgreSQL Builder MCP proof."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import socket
import subprocess
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from threading import Event
from typing import cast
from urllib.parse import quote, urlsplit
from urllib.request import urlopen
from uuid import uuid4

import httpx2
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


@pytest.fixture
def builder_mcp_live_database() -> Iterator[tuple[Engine, str]]:
    admin_url = os.getenv(
        "FOUNDRY_LITE_BUILDER_MCP_LIVE_ADMIN_DB_URL",
        "postgresql+psycopg://foundry_lite:foundry_lite@127.0.0.1:15433/postgres",
    )
    endpoint = urlsplit(admin_url)
    _wait_port(endpoint.hostname or "127.0.0.1", endpoint.port or 15433)
    database_name = f"foundry_lite_builder_mcp_{uuid4().hex[:12]}"
    admin = create_engine(admin_url, future=True, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    db_url = admin_url.rsplit("/", 1)[0] + f"/{database_name}"
    engine = create_engine(db_url, future=True)
    try:
        yield engine, db_url
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :database_name AND pid <> pg_backend_pid()"
                ),
                {"database_name": database_name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
        admin.dispose()


def test_official_mcp_client_uses_strict_oauth_and_postgres_branch_workflows(
    builder_mcp_live_database: tuple[Engine, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _engine, db_url = builder_mcp_live_database
    from foundry_lite.application.foundry import FoundryLite
    from foundry_lite.infrastructure.local_runtime import create_runtime_core_dependencies

    runtime_root = tmp_path / "runtime"
    port = _free_port()
    public_base_url = f"http://127.0.0.1:{port}"
    monkeypatch.setenv("FOUNDRY_LITE_OAUTH_ISSUER", public_base_url)
    foundry = FoundryLite(dependencies=create_runtime_core_dependencies(db_url=db_url, storage_root=runtime_root))
    workspace = _prepare_workspace(foundry, tmp_path)
    oauth = _create_oauth_application(foundry, public_base_url)
    process = _start_api(db_url, runtime_root, port, oauth)
    try:
        _wait_api(port, process)
        evidence = asyncio.run(
            _exercise_official_client(
                f"http://127.0.0.1:{port}",
                str(oauth["applicationId"]),
                str(oauth["accessToken"]),
                str(oauth["humanAccessToken"]),
                workspace,
            )
        )
    except BaseException as exc:
        _stop_process(process)
        raise AssertionError(_process_error(process)) from exc
    else:
        _stop_process(process)

    evidence.update(_runtime_counts(_engine))

    assert evidence["serverName"] == "foundry-lite-builder-mcp"
    assert evidence["datasetRef"] == "raw.builder_mcp_orders"
    assert evidence["pipelineInitialNodeCount"] == 0
    assert evidence["pipelineValidation"] is True
    assert evidence["pipelineProofKind"] == "static_graph_output_contract"
    assert evidence["isPipelineDataExecution"] is False
    assert evidence["pipelineProposalStatus"] == "submitted"
    assert evidence["ontologyResourceCount"] == 1
    assert evidence["ontologyValidationStatus"] == "valid"
    assert evidence["ontologyValidatedObjectTypeCount"] == 1
    assert evidence["ontologyMigrationStatus"] == "compatible"
    assert evidence["ontologyProposalStatus"] == "open"
    assert evidence["activeObjectTypeCount"] == 0
    assert evidence["pipelineDeploymentCount"] == 0
    assert evidence["pipelineProposalCount"] == 1
    assert evidence["ontologyProposalCount"] == 1
    assert evidence["oauthBearerOnly"] is True
    assert evidence["humanBearerOnly"] is True
    assert evidence["officialClientSessionCount"] == 1
    assert evidence["approvalChallengeCount"] == 5
    assert evidence["uniqueConfirmationReceiptCount"] == 5
    assert evidence["confirmationReceiptReuseDenied"] is True


def _prepare_workspace(foundry: object, tmp_path: Path) -> dict[str, object]:
    from foundry_lite.domain.context import RequestContext

    ctx = RequestContext(
        tenant_id="tenant-demo",
        actor_user_id="builder-mcp-live-user",
        roles=("admin", "data_engineer", "ops_manager"),
        request_id="builder-mcp-live-seed",
    )
    csv_path = tmp_path / "builder-mcp-orders.csv"
    csv_path.write_text("order_id,amount\nORDER-1,10\nORDER-2,20\n", encoding="utf-8")
    foundry.datasets.ensure("raw.builder_mcp_orders", primary_key=["order_id"], ctx=ctx)  # type: ignore[attr-defined]
    foundry.datasets.upload_csv("raw.builder_mcp_orders", str(csv_path), ctx=ctx)  # type: ignore[attr-defined]
    foundry.ontology.apply_text("objectTypes: []\nactionTypes: []\nlinkTypes: []\n", ctx=ctx)  # type: ignore[attr-defined]
    pipeline_branch = foundry.pipelines.create_branch(  # type: ignore[attr-defined]
        pipeline_id="builder_mcp_orders",
        name="builder-mcp-live-pipeline",
        idempotency_key="builder-mcp-live-pipeline-branch",
        ctx=ctx,
    )
    ontology_branch = foundry.ontology.create_branch(  # type: ignore[attr-defined]
        name="builder-mcp-live-ontology",
        idempotency_key="builder-mcp-live-ontology-branch",
        ctx=ctx,
    )
    return {
        "pipelineBranchId": str(pipeline_branch["id"]),
        "ontologyBranchId": str(ontology_branch["id"]),
        "pipelineGraph": _pipeline_graph(),
        "ontologyPatch": _ontology_patch(),
    }


def _create_oauth_application(foundry: object, public_base_url: str) -> dict[str, object]:
    from foundry_lite.domain.context import RequestContext

    ctx = RequestContext(
        tenant_id="tenant-demo",
        actor_user_id="builder-mcp-live-user",
        roles=("admin", "data_engineer", "ops_manager"),
        request_id="builder-mcp-live-oauth",
    )
    scopes = tuple(
        f"osdk:connector:fde_{mode}:execute" for mode in ("exploration", "data_integration", "ontology_editing")
    )
    console = foundry.developer_console  # type: ignore[attr-defined]
    created = console.create_osdk_application(
        app_api_name="BuilderMcpPostgresLive",
        display_name="Builder MCP PostgreSQL Live",
        resources=[
            {
                "resourceType": "connector",
                "resourceApiName": f"fde_{mode}",
                "scopes": [scope],
            }
            for mode, scope in zip(("exploration", "data_integration", "ontology_editing"), scopes, strict=True)
        ],
        idempotency_key="builder-mcp-live-application",
        ctx=ctx,
    )
    application = cast(Mapping[str, object], created["application"])
    application_id = str(application["id"])
    client_id = "builder-mcp-live-client"
    redirect_uri = "https://builder-mcp-live.invalid/oauth/callback"
    console.create_osdk_application_client(
        application_id,
        client_id=client_id,
        redirect_uris=(redirect_uri,),
        allowed_scopes=scopes,
        idempotency_key="builder-mcp-live-client",
        ctx=ctx,
    )
    verifier = "builder-mcp-postgres-live-verifier"
    resource = f"{public_base_url}/mcp/builder/{application_id}"
    authorized = foundry.auth.osdk_oauth_authorize(  # type: ignore[attr-defined]
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=_s256(verifier),
        scopes=scopes,
        resource=resource,
        resource_application_id=application_id,
        ctx=ctx,
    )
    token = foundry.auth.osdk_oauth_token(  # type: ignore[attr-defined]
        client_id=client_id,
        code=str(authorized["code"]),
        redirect_uri=redirect_uri,
        code_verifier=verifier,
        resource=resource,
        resource_application_id=application_id,
        ctx=ctx,
    )
    issuer = foundry._services.osdk_oauth_sessions.oauth_token_issuer  # type: ignore[attr-defined]
    human = issuer.issue_access_token(
        {
            "tenant_id": "tenant-demo",
            "actor_user_id": "builder-mcp-live-user",
            "roles": ["admin", "data_engineer", "ops_manager"],
            "application_id": "",
            "client_id": "",
            "scopes": [],
            "session_id": "builder-mcp-live-human-control",
        },
        ttl_seconds=300,
    )
    return {
        "applicationId": application_id,
        "accessToken": token["accessToken"],
        "humanAccessToken": human["accessToken"],
        "issuer": issuer.issuer,
        "audience": issuer.audience,
        "jwks": issuer.public_jwks(),
    }


async def _exercise_official_client(
    api_base_url: str,
    application_id: str,
    access_token: str,
    human_access_token: str,
    workspace: Mapping[str, object],
) -> dict[str, object]:
    pipeline_branch_id = str(workspace["pipelineBranchId"])
    ontology_branch_id = str(workspace["ontologyBranchId"])
    url = f"{api_base_url}/mcp/builder/{quote(application_id, safe='')}"
    headers = _oauth_headers(access_token, "builder-mcp-live-session")
    receipts: list[str] = []
    challenges: list[str] = []
    async with httpx2.AsyncClient(headers=headers, timeout=15, trust_env=False) as http_client:
        async with streamable_http_client(url, http_client=http_client) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                discovery = await _discover(session, headers, pipeline_branch_id, ontology_branch_id)
                pipeline = await _exercise_pipeline(
                    session,
                    api_base_url,
                    application_id,
                    human_access_token,
                    pipeline_branch_id,
                    workspace["pipelineGraph"],
                    discovery,
                    challenges,
                    receipts,
                )
                ontology = await _exercise_ontology(
                    session,
                    api_base_url,
                    application_id,
                    human_access_token,
                    ontology_branch_id,
                    workspace["ontologyPatch"],
                    challenges,
                    receipts,
                )
    return {
        **discovery,
        **pipeline,
        **ontology,
        "humanBearerOnly": True,
        "officialClientSessionCount": 1,
        "approvalChallengeCount": len(set(challenges)),
        "uniqueConfirmationReceiptCount": len(set(receipts)),
    }


async def _discover(
    session: ClientSession,
    headers: Mapping[str, str],
    pipeline_branch_id: str,
    ontology_branch_id: str,
) -> dict[str, object]:
    initialized = await session.initialize()
    listed = await session.list_tools()
    dataset = await session.call_tool(
        "get_foundry_dataset_schema",
        _tool_arguments(
            "exploration",
            "dataset:raw.builder_mcp_orders",
            {"datasetRef": "raw.builder_mcp_orders"},
        ),
    )
    pipeline = await session.call_tool(
        "pipeline.branch.inspect",
        _tool_arguments("data_integration", f"pipeline-branch:{pipeline_branch_id}", {}),
    )
    ontology = await session.call_tool(
        "ontology.branch.inspect",
        _tool_arguments("ontology_editing", f"ontology-branch:{ontology_branch_id}", {}),
    )
    names = {tool.name for tool in listed.tools}
    assert {
        "get_foundry_dataset_schema",
        "pipeline.branch.update_graph",
        "pipeline.branch.run_tests",
        "pipeline.branch.propose",
        "ontology.branch.apply_patch",
        "ontology.branch.propose",
    }.issubset(names)
    dataset_payload = _structured(dataset)
    pipeline_payload = _structured(pipeline)
    ontology_payload = _structured(ontology)
    branch = cast(Mapping[str, object], pipeline_payload["branch"])
    graph = cast(Mapping[str, object], branch["graph"])
    return {
        "serverName": initialized.server_info.name,
        "datasetRef": dataset_payload["datasetRef"],
        "pipelineInitialNodeCount": len(cast(list[object], graph["nodes"])),
        "pipelineFingerprint": branch["graphFingerprint"],
        "ontologyInitialResourceCount": len(cast(list[object], ontology_payload["resources"])),
        "oauthBearerOnly": set(headers) == {"Authorization", "X-Request-ID"},
    }


async def _exercise_pipeline(
    session: ClientSession,
    api_base_url: str,
    application_id: str,
    human_access_token: str,
    branch_id: str,
    graph: object,
    discovery: Mapping[str, object],
    challenges: list[str],
    receipts: list[str],
) -> dict[str, object]:
    workspace_ref = f"pipeline-branch:{branch_id}"
    _, confirmation_receipt_reuse_denied = await _call_confirmed_tool(
        session,
        api_base_url,
        application_id,
        human_access_token,
        "pipeline.branch.update_graph",
        _tool_arguments(
            "data_integration",
            workspace_ref,
            {"graph": graph, "expectedFingerprint": discovery["pipelineFingerprint"]},
        ),
        request_id="builder-mcp-live-pipeline-update",
        challenges=challenges,
        receipts=receipts,
        should_verify_receipt_reuse=True,
    )
    validated = _structured(
        await session.call_tool(
            "pipeline.branch.validate",
            _tool_arguments("data_integration", workspace_ref, {}),
        )
    )
    tested, _ = await _call_confirmed_tool(
        session,
        api_base_url,
        application_id,
        human_access_token,
        "pipeline.branch.run_tests",
        _tool_arguments("data_integration", workspace_ref, {}),
        request_id="builder-mcp-live-pipeline-tests",
        challenges=challenges,
        receipts=receipts,
    )
    proposed, _ = await _call_confirmed_tool(
        session,
        api_base_url,
        application_id,
        human_access_token,
        "pipeline.branch.propose",
        _tool_arguments(
            "data_integration",
            workspace_ref,
            {
                "title": "Builder MCP live Pipeline proposal",
                "description": "Strict OAuth official-client branch proof.",
                "idempotencyKey": "builder-mcp-live-pipeline-proposal",
            },
        ),
        request_id="builder-mcp-live-pipeline-propose",
        challenges=challenges,
        receipts=receipts,
    )
    return {
        "pipelineValidation": validated["valid"],
        "pipelineProofKind": tested["proofKind"],
        "isPipelineDataExecution": tested["isDataExecution"],
        "pipelineProposalStatus": proposed["status"],
        "confirmationReceiptReuseDenied": confirmation_receipt_reuse_denied,
    }


async def _exercise_ontology(
    session: ClientSession,
    api_base_url: str,
    application_id: str,
    human_access_token: str,
    branch_id: str,
    patch: object,
    challenges: list[str],
    receipts: list[str],
) -> dict[str, object]:
    workspace_ref = f"ontology-branch:{branch_id}"
    applied, _ = await _call_confirmed_tool(
        session,
        api_base_url,
        application_id,
        human_access_token,
        "ontology.branch.apply_patch",
        _tool_arguments("ontology_editing", workspace_ref, cast(Mapping[str, object], patch)),
        request_id="builder-mcp-live-ontology-patch",
        challenges=challenges,
        receipts=receipts,
    )
    validated = _structured(
        await session.call_tool(
            "ontology.branch.validate",
            _tool_arguments("ontology_editing", workspace_ref, {}),
        )
    )
    proposed, _ = await _call_confirmed_tool(
        session,
        api_base_url,
        application_id,
        human_access_token,
        "ontology.branch.propose",
        _tool_arguments(
            "ontology_editing",
            workspace_ref,
            {
                "title": "Builder MCP live Ontology proposal",
                "description": "Strict OAuth official-client branch proof.",
                "idempotencyKey": "builder-mcp-live-ontology-proposal",
            },
        ),
        request_id="builder-mcp-live-ontology-propose",
        challenges=challenges,
        receipts=receipts,
    )
    applied_diff = cast(Mapping[str, object], applied["diff"])
    validation_result = cast(Mapping[str, object], validated["validation"])
    migration_plan = cast(Mapping[str, object], validation_result["migration_plan"])
    return {
        "ontologyResourceCount": len(cast(list[object], applied_diff["resources"])),
        "ontologyValidationStatus": validation_result["status"],
        "ontologyValidatedObjectTypeCount": validation_result["object_type_count"],
        "ontologyMigrationStatus": migration_plan["status"],
        "ontologyProposalStatus": proposed["status"],
    }


async def _call_confirmed_tool(
    session: ClientSession,
    api_base_url: str,
    application_id: str,
    human_access_token: str,
    tool_name: str,
    arguments: Mapping[str, object],
    *,
    request_id: str,
    challenges: list[str],
    receipts: list[str],
    should_verify_receipt_reuse: bool = False,
) -> tuple[dict[str, object], bool]:
    challenge = _structured(await session.call_tool(tool_name, dict(arguments)))
    assert challenge["status"] == "approval_required"
    assert challenge["toolId"] == tool_name
    challenge_id = str(challenge["challengeId"])
    approval = await _approve_confirmation(
        api_base_url,
        application_id,
        challenge_id,
        human_access_token,
        request_id,
    )
    receipt = str(approval["confirmationReceipt"])
    approved_arguments = {**arguments, "confirmationReceipt": receipt}
    completed = _structured(await session.call_tool(tool_name, approved_arguments))
    is_receipt_reuse_denied = False
    if should_verify_receipt_reuse:
        exact_replay = _structured(await session.call_tool(tool_name, approved_arguments))
        assert exact_replay == completed
        different_inner = dict(cast(Mapping[str, object], arguments["arguments"]))
        different_inner["expectedFingerprint"] = "different-receipt-use"
        different_arguments = {**arguments, "arguments": different_inner, "confirmationReceipt": receipt}
        with pytest.raises(MCPError, match="confirmation challenge or receipt cannot be used"):
            await session.call_tool(tool_name, different_arguments)
        is_receipt_reuse_denied = True
    challenges.append(challenge_id)
    receipts.append(receipt)
    return completed, is_receipt_reuse_denied


async def _approve_confirmation(
    api_base_url: str,
    application_id: str,
    challenge_id: str,
    human_access_token: str,
    request_id: str,
) -> dict[str, object]:
    headers = _oauth_headers(human_access_token, f"{request_id}-human-approval")
    path = f"/api/aip/fde/mcp/{quote(application_id, safe='')}/confirmations/{quote(challenge_id, safe='')}/approve"
    async with httpx2.AsyncClient(base_url=api_base_url, headers=headers, timeout=15, trust_env=False) as client:
        response = await client.post(path)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert isinstance(payload, dict)
    assert payload["status"] == "approved"
    assert payload["challengeId"] == challenge_id
    assert set(headers) == {"Authorization", "X-Request-ID"}
    return {str(key): value for key, value in payload.items()}


def _structured(result: object) -> dict[str, object]:
    payload = getattr(result, "structured_content", None)
    if not isinstance(payload, Mapping):
        raise AssertionError("Builder MCP tool did not return structured content")
    return {str(key): value for key, value in payload.items()}


def _tool_arguments(mode: str, workspace_ref: str, arguments: Mapping[str, object]) -> dict[str, object]:
    return {"mode": mode, "workspaceRef": workspace_ref, "arguments": dict(arguments)}


def _oauth_headers(access_token: str, request_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}", "X-Request-ID": request_id}


def _pipeline_graph() -> dict[str, object]:
    columns = [
        {"name": "order_id", "type": "string", "nullable": False},
        {"name": "amount", "type": "integer", "nullable": True},
    ]
    return {
        "schemaVersion": 2,
        "nodes": [
            {
                "id": "raw_orders",
                "kind": "source",
                "descriptorId": "source.dataset",
                "specVersion": 1,
                "config": {"datasetRef": "raw.builder_mcp_orders"},
            },
            {
                "id": "select",
                "kind": "transform",
                "descriptorId": "transform.select_cast",
                "specVersion": 1,
                "config": {
                    "columns": [
                        {"source": "order_id", "name": "order_id", "type": "string"},
                        {"source": "amount", "name": "amount", "type": "integer"},
                    ],
                    "outputDatasetRef": "work.builder_mcp_orders",
                },
            },
            {
                "id": "out",
                "kind": "output",
                "descriptorId": "output.dataset",
                "specVersion": 1,
                "config": {"outputDatasetRef": "clean.builder_mcp_orders"},
            },
        ],
        "edges": [
            {
                "id": "raw-select",
                "sourceNodeId": "raw_orders",
                "sourcePortId": "dataset",
                "targetNodeId": "select",
                "targetPortId": "input",
            },
            {
                "id": "select-out",
                "sourceNodeId": "select",
                "sourcePortId": "dataset",
                "targetNodeId": "out",
                "targetPortId": "input",
            },
        ],
        "layout": {},
        "outputContract": {"columns": columns},
        "tests": [{"name": "declared schema contract", "expected": {"columns": columns}}],
        "schedule": None,
    }


def _ontology_patch() -> dict[str, object]:
    return {
        "upsertResources": [
            {
                "kind": "objectType",
                "definition": {
                    "apiName": "BuilderOrder",
                    "primaryKey": "orderId",
                    "backing": {"dataset": "raw.builder_mcp_orders"},
                    "properties": [
                        {
                            "apiName": "orderId",
                            "column": "order_id",
                            "type": "string",
                            "nullable": False,
                            "indexed": True,
                        }
                    ],
                },
            }
        ],
        "deleteResources": [],
        "changeSummary": "Add BuilderOrder on the isolated Ontology branch",
    }


def _runtime_counts(engine: Engine) -> dict[str, object]:
    with engine.connect() as connection:
        return {
            "activeObjectTypeCount": connection.execute(text("SELECT COUNT(*) FROM object_types")).scalar_one(),
            "pipelineDeploymentCount": connection.execute(
                text("SELECT COUNT(*) FROM pipeline_deployments")
            ).scalar_one(),
            "pipelineProposalCount": connection.execute(text("SELECT COUNT(*) FROM pipeline_proposals")).scalar_one(),
            "ontologyProposalCount": connection.execute(text("SELECT COUNT(*) FROM insight_reviews")).scalar_one(),
        }


def _start_api(
    db_url: str,
    runtime_root: Path,
    port: int,
    oauth: Mapping[str, object],
) -> subprocess.Popen[bytes]:
    env = {
        **os.environ,
        "FOUNDRY_LITE_DB_URL": db_url,
        "FOUNDRY_LITE_HOME": str(runtime_root),
        "FOUNDRY_LITE_OTEL_DISABLED": "1",
        "FOUNDRY_LITE_AUTH_PROFILE": "oidc",
        "FOUNDRY_LITE_RUNTIME_PROFILE": "test",
        "FOUNDRY_LITE_OIDC_ISSUER": str(oauth["issuer"]),
        "FOUNDRY_LITE_OAUTH_ISSUER": str(oauth["issuer"]),
        "FOUNDRY_LITE_OIDC_AUDIENCE": str(oauth["audience"]),
        "FOUNDRY_LITE_OIDC_JWKS_JSON": json.dumps(oauth["jwks"]),
        "PYTHONPATH": ".:libs:apps/api",
    }
    return subprocess.Popen(  # nosec B603 - fixed local integration-test argv.
        [sys.executable, "-m", "uvicorn", "foundry_lite_api.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=Path(__file__).parents[2],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _s256(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_port(host: str, port: int) -> None:
    for _ in range(300):
        try:
            with socket.create_connection((host, port), timeout=0.25):
                return
        except OSError:
            Event().wait(0.1)
    raise AssertionError(f"timed out waiting for {host}:{port}")


def _wait_api(port: int, process: subprocess.Popen[bytes]) -> None:
    for _ in range(200):
        if process.poll() is not None:
            raise AssertionError(_process_error(process))
        try:
            with urlopen(f"http://127.0.0.1:{port}/healthz", timeout=0.25) as response:  # nosec B310
                if response.status == 200:
                    return
        except OSError:
            Event().wait(0.1)
    raise AssertionError("Builder MCP API process did not become ready")


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _process_error(process: subprocess.Popen[bytes]) -> str:
    stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
    return f"Builder MCP API process exited early: {stderr}"
