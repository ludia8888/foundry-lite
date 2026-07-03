from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Coroutine, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, replace
from importlib import import_module
from pathlib import Path
from threading import Event
from time import monotonic
from typing import Any, cast
from urllib.error import URLError
from urllib.request import urlopen
from uuid import uuid4

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import ConnectorSnapshot, RuntimeRunDetail, StreamPublishRequest
from foundry_lite.application.services.outbox_publisher_service import DEFAULT_OUTBOX_STREAM_NAME
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.adapters import (
    ElasticsearchAdapter,
    ElasticsearchAdapterConfig,
    IcebergDatasetStorageAdapter,
    KafkaStreamAdapter,
    KafkaStreamAdapterConfig,
    KafkaStreamSubscription,
    SparkComputeAdapter,
)
from foundry_lite.infrastructure.adapters.s3_external_writeback import (
    S3ExternalWritebackAdapter,
    S3ExternalWritebackConfig,
)
from foundry_lite.infrastructure.adapters.scale_foundation import LocalConnectorAdapter
from foundry_lite.infrastructure.adapters.temporal_workflow import (
    TemporalWorkflowAdapter,
    TemporalWorkflowAdapterConfig,
)
from foundry_lite.infrastructure.adapters.temporal_workflows import (
    FOUNDRY_TASK_QUEUE,
    ConnectorSyncActivities,
    ConnectorSyncWorkflow,
    FoundryWorkflow,
    foundry_sandbox_runner,
    run_workflow_step,
)
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from foundry_lite_worker.stream_archive import StreamArchiveWorkerConfig, run_stream_archive_once
from sqlalchemy import create_engine
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from testcontainers.core.container import DockerContainer  # type: ignore[import-untyped]
from testcontainers.kafka import KafkaContainer  # type: ignore[import-untyped]
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from tests.conftest import DEMO_ROOT
from tests.integration.elasticsearch_live_lock import live_elasticsearch_container, live_elasticsearch_lock

MINIO_ACCESS_KEY = "foundry_lite"
MINIO_SECRET_KEY = "foundry_lite_password"
ELASTICSEARCH_IMAGE = "docker.elastic.co/elasticsearch/elasticsearch:9.0.0"


@dataclass(frozen=True)
class MinioServer:
    endpoint_url: str


@dataclass(frozen=True)
class PostgresServer:
    db_url: str


@dataclass(frozen=True)
class KafkaServer:
    bootstrap_servers: str


def test_product_loop_runs_on_postgres_minio_kafka_elasticsearch_temporal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _minio_server() as minio, _postgres_server() as postgres, _kafka_server() as kafka:
        with live_elasticsearch_lock():
            elasticsearch = _elasticsearch_container()
            elasticsearch.start()
            try:
                es_url = f"http://{elasticsearch.get_container_host_ip()}:{elasticsearch.get_exposed_port(9200)}"
                _run(
                    lambda: _all_infra_body(
                        tmp_path,
                        monkeypatch=monkeypatch,
                        minio=minio,
                        postgres=postgres,
                        kafka=kafka,
                        es_url=es_url,
                    )
                )
            finally:
                elasticsearch.stop()


async def _all_infra_body(
    tmp_path: Path,
    *,
    monkeypatch: pytest.MonkeyPatch,
    minio: MinioServer,
    postgres: PostgresServer,
    kafka: KafkaServer,
    es_url: str,
) -> None:
    ctx = demo_admin_context()
    foundry: FoundryLite | None = None

    def driver(payload: dict[str, Any]) -> dict[str, Any]:
        assert foundry is not None
        sync_name = payload.get("syncName")
        result = foundry.datasets.sync_connector_snapshot(
            str(payload["datasetRef"]),
            connector_name=str(payload["connectorName"]),
            resource_name=str(payload["resourceName"]),
            sync_name=sync_name if isinstance(sync_name, str) else None,
            ctx=ctx,
        )
        return {
            "workflowKind": "connector_sync",
            "datasetRef": result.dataset_ref,
            "committedVersionId": result.version_id,
            "rowCount": result.row_count,
        }

    async with _connector_sync_harness(driver) as temporal_adapter:
        raw_topic = f"flite-all-infra-raw-{uuid4().hex}"
        outbox_topic = f"flite-all-infra-outbox-{uuid4().hex}"
        bucket = f"flite-all-infra-{uuid4().hex}"
        _s3_client(minio.endpoint_url).create_bucket(Bucket=bucket)
        _set_all_infra_env(monkeypatch, tmp_path, minio, bucket)
        kafka_adapter = _kafka_adapter(kafka.bootstrap_servers, raw_topic=raw_topic, outbox_topic=outbox_topic)
        dependencies = create_local_core_dependencies(
            adapter_profile="iceberg",
            storage_root=tmp_path / "runtime",
            db_url=postgres.db_url,
        )
        assert isinstance(dependencies.dataset_storage, IcebergDatasetStorageAdapter)
        assert isinstance(dependencies.compute_adapter, SparkComputeAdapter)
        connector_adapter = LocalConnectorAdapter(
            {
                (ctx.tenant_id, "local", "customers"): ConnectorSnapshot(
                    connector_name="local",
                    resource_name="customers",
                    rows=(_customer_row(),),
                    schema={"columns": list(_customer_row())},
                    cursor={"cursor": "customers-1"},
                    source_watermark="all-infra-temporal",
                )
            }
        )
        foundry = FoundryLite(
            dependencies=replace(
                dependencies,
                connector_adapter=connector_adapter,
                search_adapter=_elasticsearch_adapter(es_url),
                stream_adapter=kafka_adapter,
                workflow_adapter=temporal_adapter,
            )
        )
        foundry.actions._action.set_external_writeback_adapter(_writeback_adapter(minio.endpoint_url, bucket))
        _ensure_datasets(foundry, ctx)
        _register_transforms(foundry, tmp_path, ctx)
        _publish_order(kafka_adapter)
        raw_orders = run_stream_archive_once(
            _stream_archive_config(tmp_path, postgres, kafka, raw_topic),
            stream_adapter=kafka_adapter,
        )
        workflow_run = await asyncio.to_thread(
            foundry.operations.start_connector_sync_workflow,
            "raw.crm_customers",
            connector_name="local",
            resource_name="customers",
            idempotency_key=f"all-infra-customers-{uuid4().hex}",
            ctx=ctx,
        )
        clean_orders = foundry.transforms.run("clean_orders_from_stream", ctx=ctx)
        clean_customers = foundry.transforms.run("clean_customers", ctx=ctx)
        clean_order_finance = foundry.transforms.run("clean_order_finance_from_stream", ctx=ctx)
        foundry.ontology.apply(str(DEMO_ROOT / "ontology" / "order-customer.yaml"), ctx=ctx)
        order_index = foundry.objects.reindex("Order", ctx=ctx)
        customer_index = foundry.objects.reindex("Customer", ctx=ctx)
        search_rebuild = foundry.objects.rebuild_search("Order", ctx=ctx)
        order = foundry.objects.get("Order", "O-INFRA-1", ctx=ctx, include_explain=True)
        action = foundry.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-INFRA-1",
            expected_object_version=order["objectVersion"],
            params={"reason": "All infra proof approved"},
            idempotency_key="all-infra-approve-order",
            external_writeback_uri=f"s3://{bucket}/orders/O-INFRA-1",
            ctx=ctx,
        )
        search_update = foundry.objects.consume_search_change("Order", "O-INFRA-1", ctx=ctx)
        search_hits = foundry.objects.query("Order", ctx=ctx, search_text="All infra proof approved", limit=5)
        action_log = foundry.materialization.run("action_log", ctx=ctx)
        order_current = foundry.materialization.run("order_current", ctx=ctx)
        agent = foundry.aip.run_agent_payload(payload=_agent_payload(), ctx=ctx)
        published = foundry.operations.publish_pending_outbox(ctx=ctx, limit=200)
        outbox_events = kafka_adapter.read_events(DEFAULT_OUTBOX_STREAM_NAME, limit=max(1, published["published"]))

    assert raw_orders is not None
    assert raw_orders.manifest_uri.startswith("iceberg://")
    assert clean_orders.manifest_uri.startswith("iceberg://")
    assert clean_customers.manifest_uri.startswith("iceberg://")
    assert clean_order_finance.manifest_uri.startswith("iceberg://")
    assert action_log.manifest_uri.startswith("iceberg://")
    assert order_current.manifest_uri.startswith("iceberg://")
    assert workflow_run["status"] == "succeeded"
    assert workflow_run["workflowProfile"] == "temporal"
    assert workflow_run["output"]["datasetRef"] == "raw.crm_customers"
    assert order_index["objects_upserted"] == 1
    assert customer_index["objects_upserted"] == 1
    assert search_rebuild["status"] == "consistent"
    assert order["explain"]["lineage"]
    assert action["status"] == "succeeded"
    assert str(_writeback_payload(minio.endpoint_url, bucket, "all-infra-approve-order")["uri"]).endswith(
        "/orders/O-INFRA-1"
    )
    assert search_update["status"] == "indexed"
    assert [item["objectId"] for item in search_hits["items"]] == ["O-INFRA-1"]
    assert _materialized_status(foundry, ctx, order_current.version_id)["O-INFRA-1"] == "APPROVED"
    assert agent.run_status == "succeeded"
    assert agent.citations
    assert published["failed"] == 0
    assert published["published"] > 0
    assert len(outbox_events) == published["published"]
    action_detail = foundry.operations.run_detail("action", str(action["actionRunId"]), ctx=ctx)
    materialization_detail = _materialization_detail(foundry, ctx, order_current.version_id)
    ai_detail = foundry.operations.run_detail("ai", str(agent.ai_run_id), ctx=ctx)
    assert _has_relation(action_detail, target_type="action_writeback", relation="writeback_attempt")
    assert _has_relation(action_detail, target_type="outbox", relation="emitted")
    assert _has_relation(materialization_detail, target_type="outbox", relation="emitted")
    assert _int(_mapping(_ai_payload(ai_detail), "summary"), "modelCallCount") == 1


@asynccontextmanager
async def _connector_sync_harness(
    driver: Callable[[dict[str, Any]], dict[str, Any]],
):
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=FOUNDRY_TASK_QUEUE,
            workflows=[FoundryWorkflow, ConnectorSyncWorkflow],
            activities=[run_workflow_step, ConnectorSyncActivities(driver).run_connector_sync_step],
            workflow_runner=foundry_sandbox_runner(),
        ):
            yield TemporalWorkflowAdapter(
                TemporalWorkflowAdapterConfig(task_queue=FOUNDRY_TASK_QUEUE),
                client=env.client,
            )


def _run(body: Callable[[], Coroutine[Any, Any, None]]) -> None:
    asyncio.run(body())


def _ensure_datasets(foundry: FoundryLite, ctx: RequestContext) -> None:
    foundry.datasets.ensure("raw.erp_orders", ctx=ctx, primary_key=["event_id"])
    foundry.datasets.ensure("raw.crm_customers", ctx=ctx, primary_key=["customer_id"])
    foundry.datasets.ensure("clean.orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("clean.customers", ctx=ctx, primary_key=["customer_id"])
    foundry.datasets.ensure("clean.order_finance", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("ops.action_log", ctx=ctx, primary_key=["action_run_id"])
    foundry.datasets.ensure("ops.order_current", ctx=ctx, primary_key=["orderId"])


def _register_transforms(foundry: FoundryLite, tmp_path: Path, ctx: RequestContext) -> None:
    clean_orders_sql = tmp_path / "clean_orders_from_stream.sql"
    clean_orders_sql.write_text(
        """
select
  get_json_object(payload_json, '$.order_id') as order_id,
  get_json_object(payload_json, '$.customer_id') as customer_id,
  get_json_object(payload_json, '$.source_status') as source_status,
  cast(get_json_object(payload_json, '$.amount') as double) as amount,
  cast(get_json_object(payload_json, '$.margin') as double) as margin,
  cast(get_json_object(payload_json, '$.order_ts') as timestamp) as order_ts,
  get_json_object(payload_json, '$.region') as region,
  case when cast(get_json_object(payload_json, '$.amount') as double) >= 1000 then 0.8 else 0.3 end as risk_score
from {{ input('raw.erp_orders') }}
where event_type = 'erp.order.upsert'
""".strip()
        + "\n",
        encoding="utf-8",
    )
    foundry.transforms.register(
        "clean_orders_from_stream",
        entrypoint=clean_orders_sql,
        inputs={"orders": "raw.erp_orders"},
        output_dataset_ref="clean.orders",
        checks=[
            {"type": "unique", "column": "order_id"},
            {"type": "not_null", "columns": ["order_id", "customer_id"]},
        ],
        ctx=ctx,
    )
    foundry.transforms.register(
        "clean_customers",
        entrypoint=DEMO_ROOT / "transforms" / "clean_customers.sql",
        inputs={"customers": "raw.crm_customers"},
        output_dataset_ref="clean.customers",
        checks=[
            {"type": "unique", "column": "customer_id"},
            {"type": "not_null", "columns": ["customer_id"]},
        ],
        ctx=ctx,
    )
    clean_order_finance_sql = tmp_path / "clean_order_finance_from_stream.sql"
    clean_order_finance_sql.write_text(
        """
select
  order_id,
  cast(margin as double) as margin
from {{ input('clean.orders') }}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    foundry.transforms.register(
        "clean_order_finance_from_stream",
        entrypoint=clean_order_finance_sql,
        inputs={"orders": "clean.orders"},
        output_dataset_ref="clean.order_finance",
        checks=[
            {"type": "unique", "column": "order_id"},
            {"type": "not_null", "columns": ["order_id"]},
        ],
        ctx=ctx,
    )


def _publish_order(kafka_adapter: KafkaStreamAdapter) -> None:
    kafka_adapter.publish_event(
        StreamPublishRequest(
            stream_name="erp_orders",
            event_type="erp.order.upsert",
            tenant_id="tenant-demo",
            request_id="req-all-infra-order",
            key="O-INFRA-1",
            payload={
                "order_id": "O-INFRA-1",
                "customer_id": "C-100",
                "source_status": "PENDING",
                "amount": 1250.0,
                "margin": 0.27,
                "order_ts": "2026-06-27T00:00:00",
                "region": "NA",
            },
        )
    )


def _stream_archive_config(
    tmp_path: Path,
    postgres: PostgresServer,
    kafka: KafkaServer,
    raw_topic: str,
) -> StreamArchiveWorkerConfig:
    return StreamArchiveWorkerConfig(
        dataset_ref="raw.erp_orders",
        stream_name="erp_orders",
        topic=raw_topic,
        bootstrap_servers=kafka.bootstrap_servers,
        storage_root=tmp_path / "runtime",
        db_url=postgres.db_url,
        adapter_profile="iceberg",
        consumer_group=f"all-infra-archive-{uuid4().hex}",
        poll_timeout_seconds=1.0,
        max_empty_polls=10,
    )


def _kafka_adapter(bootstrap_servers: str, *, raw_topic: str, outbox_topic: str) -> KafkaStreamAdapter:
    return KafkaStreamAdapter(
        KafkaStreamAdapterConfig(
            bootstrap_servers=bootstrap_servers,
            consumer_group=f"all-infra-{uuid4().hex}",
            subscriptions=(
                KafkaStreamSubscription("erp_orders", raw_topic),
                KafkaStreamSubscription(DEFAULT_OUTBOX_STREAM_NAME, outbox_topic),
            ),
            producer_flush_timeout_seconds=10.0,
            poll_timeout_seconds=1.0,
            max_empty_polls=10,
        )
    )


def _elasticsearch_adapter(es_url: str) -> ElasticsearchAdapter:
    adapter = ElasticsearchAdapter(
        ElasticsearchAdapterConfig(
            endpoint=es_url,
            index_prefix=f"allinfra{uuid4().hex[:8]}",
            request_timeout_seconds=30,
        )
    )
    cast(Any, adapter.client).cluster.health(wait_for_status="yellow", timeout="60s")
    return adapter


def _writeback_adapter(endpoint_url: str, bucket: str) -> S3ExternalWritebackAdapter:
    return S3ExternalWritebackAdapter(
        S3ExternalWritebackConfig(
            bucket=bucket,
            endpoint_url=endpoint_url,
            access_key_id=MINIO_ACCESS_KEY,
            secret_access_key=MINIO_SECRET_KEY,
        )
    )


def _agent_payload() -> dict[str, object]:
    return {
        "agentRunId": f"all-infra-agent-{uuid4().hex}",
        "agentVersionId": "agent.order-ops.v1",
        "modelAlias": "default-completion",
        "promptVersionId": "prompt-order-copilot@v1",
        "userMessage": "Explain Order O-INFRA-1 after the all-infra approval action.",
        "agentInstruction": "Answer as the Order Operations Copilot. Do not execute tools.",
        "securityPartition": "tenant-demo:internal",
        "allowedSecurityPartitions": ["tenant-demo:internal"],
        "stateJson": {"objectType": "Order", "objectId": "O-INFRA-1"},
        "outputSchema": {
            "type": "object",
            "properties": {
                "answer": {"type": "string"},
                "citations": {"type": "array", "items": {"type": "object"}},
            },
        },
        "dataClassification": "internal",
        "maxContextItems": 4,
        "maxContextTokens": 1200,
        "maxModelCalls": 1,
        "maxLoopIterations": 1,
        "maxOutputTokens": 512,
        "policyVersion": "policy-v1",
    }


def _customer_row() -> dict[str, object]:
    return {
        "customer_id": "C-100",
        "customer_name": "Acme Logistics",
        "segment": "enterprise",
        "region": "NA",
        "risk_score": 0.41,
        "approved_order_count": 7,
    }


def _materialization_detail(foundry: FoundryLite, ctx: RequestContext, version_id: str) -> RuntimeRunDetail:
    run = next(
        row
        for row in foundry.operations.query_runs(ctx=ctx, run_type="materialization")["materializationRuns"]
        if row["target_dataset_version_id"] == version_id
    )
    return foundry.operations.run_detail("materialization", str(run["id"]), ctx=ctx)


def _materialized_status(foundry: FoundryLite, ctx: RequestContext, version_id: str) -> dict[str, str]:
    rows = foundry.datasets.preview("ops.order_current", ctx=ctx, version=version_id)
    return {str(row["orderId"]): str(row["status"]) for row in rows}


def _has_relation(detail: RuntimeRunDetail, *, target_type: str, relation: str) -> bool:
    rows = cast(list[dict[str, object]], detail["runRelations"])
    return any(row["target_run_type"] == target_type and row["relation"] == relation for row in rows)


def _ai_payload(detail: RuntimeRunDetail) -> dict[str, object]:
    payload = cast(Mapping[str, object], detail).get("ai")
    assert isinstance(payload, dict)
    return cast(dict[str, object], payload)


def _mapping(payload: Mapping[str, object], key: str) -> dict[str, object]:
    value = payload[key]
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def _int(payload: Mapping[str, object], key: str) -> int:
    value = payload[key]
    assert isinstance(value, int)
    return value


def _writeback_payload(endpoint_url: str, bucket: str, idempotency_key: str) -> dict[str, object]:
    response = _s3_client(endpoint_url).get_object(Bucket=bucket, Key=f"external-writebacks/{idempotency_key}")
    return cast(dict[str, object], json.loads(response["Body"].read()))


def _set_all_infra_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    minio: MinioServer,
    bucket: str,
) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_COMPUTE_PROFILE", "spark")
    monkeypatch.setenv("FOUNDRY_LITE_ICEBERG_CATALOG_URI", f"sqlite:///{tmp_path / 'engine-catalog.db'}")
    monkeypatch.setenv("FOUNDRY_LITE_ICEBERG_WAREHOUSE", f"s3://{bucket}/warehouse")
    monkeypatch.setenv("FOUNDRY_LITE_ICEBERG_NAMESPACE", f"ns_{uuid4().hex[:8]}")
    monkeypatch.setenv("FOUNDRY_LITE_S3_BUCKET", bucket)
    monkeypatch.setenv("FOUNDRY_LITE_S3_ENDPOINT_URL", minio.endpoint_url)
    monkeypatch.setenv("FOUNDRY_LITE_S3_ACCESS_KEY_ID", MINIO_ACCESS_KEY)
    monkeypatch.setenv("FOUNDRY_LITE_S3_SECRET_ACCESS_KEY", MINIO_SECRET_KEY)
    monkeypatch.setenv("FOUNDRY_LITE_S3_REGION", "us-east-1")
    monkeypatch.setenv("FOUNDRY_LITE_SECRET_AIP_CITATION_NAVIGATION_SIGNER", "all-infra-citation-secret")
    monkeypatch.setenv("FOUNDRY_LITE_SECRET_AIP_PROMPT_ARTIFACT_ENCRYPTION_KEY", "all-infra-prompt-key")


def _s3_client(endpoint_url: str) -> Any:
    boto3 = import_module("boto3")
    botocore_config = import_module("botocore.config")
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
        region_name="us-east-1",
        config=botocore_config.Config(s3={"addressing_style": "path"}),
    )


def _elasticsearch_container() -> Any:
    return (
        live_elasticsearch_container(ELASTICSEARCH_IMAGE)
        .with_env("xpack.security.enabled", "false")
        .with_env("discovery.type", "single-node")
        .with_env("ES_JAVA_OPTS", "-Xms1g -Xmx1g")
        .with_kwargs(tmpfs={"/usr/share/elasticsearch/data": "rw,size=1g"})
    )


@contextmanager
def _minio_server() -> Iterator[MinioServer]:
    container = (
        DockerContainer("minio/minio")
        .with_env("MINIO_ROOT_USER", MINIO_ACCESS_KEY)
        .with_env("MINIO_ROOT_PASSWORD", MINIO_SECRET_KEY)
        .with_command("server /data --address :9000")
        .with_exposed_ports(9000)
    )
    container.start()
    try:
        endpoint = f"http://{container.get_container_host_ip()}:{container.get_exposed_port(9000)}"
        _wait_until(lambda: _minio_ready(endpoint))
        yield MinioServer(endpoint_url=endpoint)
    finally:
        container.stop()


@contextmanager
def _postgres_server() -> Iterator[PostgresServer]:
    container = PostgresContainer("postgres:16-alpine", driver="psycopg")
    container.start()
    try:
        db_url = _postgres_url(container)
        engine = create_engine(db_url, future=True)
        db.create_database(engine)
        engine.dispose()
        yield PostgresServer(db_url=db_url)
    finally:
        container.stop()


@contextmanager
def _kafka_server() -> Iterator[KafkaServer]:
    container = KafkaContainer().with_kraft()
    container.start(timeout=90)
    try:
        yield KafkaServer(bootstrap_servers=container.get_bootstrap_server())
    finally:
        container.stop()


def _postgres_url(container: PostgresContainer) -> str:
    url = container.get_connection_url()
    if url.startswith("postgresql://") and "+psycopg" not in url:
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def _wait_until(condition: Callable[[], bool], *, timeout_seconds: float = 60, interval_seconds: float = 1) -> None:
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        if condition():
            return
        Event().wait(interval_seconds)
    raise TimeoutError("service did not become ready before timeout")


def _minio_ready(endpoint_url: str) -> bool:
    try:
        with urlopen(f"{endpoint_url}/minio/health/ready", timeout=2) as response:  # noqa: S310
            response.read()
            return response.status == 200
    except (ConnectionError, TimeoutError, URLError, json.JSONDecodeError):
        return False
