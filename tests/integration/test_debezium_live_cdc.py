from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from threading import Event
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from foundry_lite.application.core import FoundryLiteCore
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from foundry_lite_worker.stream_archive import StreamArchiveWorkerConfig, run_stream_archive_once
from sqlalchemy import create_engine, text
from testcontainers.core.container import DockerContainer
from testcontainers.core.network import Network
from testcontainers.kafka import KafkaContainer
from testcontainers.postgres import PostgresContainer

DEBEZIUM_CONNECT_IMAGE = "quay.io/debezium/connect:3.5"


def test_live_debezium_postgres_changes_archive_through_worker(tmp_path: Path) -> None:
    suffix = uuid4().hex
    stream_name = "erp_orders_cdc"
    topic_prefix = f"foundry_lite_{suffix}"
    topic = f"{topic_prefix}.public.orders"
    consumer_group = f"foundry-lite-cdc-archive-{suffix}"
    storage_root = tmp_path / "flite"
    with Network() as network:
        kafka = _kafka_container(network)
        postgres = _postgres_container(network)
        connect = _connect_container(network)
        started: list[DockerContainer] = []
        try:
            kafka.start(timeout=90)
            started.append(kafka)
            postgres.start()
            started.append(postgres)
            _create_orders_table(postgres)
            connect.start()
            started.append(connect)
            connector_name = f"foundry-lite-orders-{suffix}"
            _wait_for_connect(connect)
            _register_postgres_connector(connect, name=connector_name, topic_prefix=topic_prefix, suffix=suffix)
            _wait_for_connector_running(connect, connector_name)
            _wait_for_replication_slot(postgres, _replication_slot_name(suffix))
            _write_order_changes(postgres)
            _wait_for_cdc_events(kafka.get_bootstrap_server(), topic)

            result = run_stream_archive_once(
                StreamArchiveWorkerConfig(
                    dataset_ref="raw_cdc.erp_orders",
                    stream_name=stream_name,
                    topic=topic,
                    bootstrap_servers=kafka.get_bootstrap_server(),
                    storage_root=storage_root,
                    consumer_group=consumer_group,
                    poll_timeout_seconds=1.0,
                    max_empty_polls=20,
                    schema_strategy="cdc_envelope_json",
                    cdc_primary_key=("order_id",),
                )
            )

            preview = _preview_dataset(storage_root, "raw_cdc.erp_orders")
            assert result is not None
            assert result.row_count == 3
            assert [row["op"] for row in preview] == ["c", "u", "d"]
            assert preview[0]["pk_json"] == '{"order_id":"O-CDC-1001"}'
            assert preview[2]["after_json"] == "null"
        finally:
            for container in reversed(started):
                container.stop()


def _kafka_container(network: Network) -> KafkaContainer:
    return KafkaContainer().with_kraft().with_network(network).with_network_aliases("kafka")


def _postgres_container(network: Network) -> PostgresContainer:
    return (
        PostgresContainer(
            "postgres:16-alpine",
            username="postgres",
            password="postgres",
            dbname="inventory",
            driver="psycopg",
        )
        .with_network(network)
        .with_network_aliases("postgres")
        .with_command(
            [
                "postgres",
                "-c",
                "wal_level=logical",
                "-c",
                "max_replication_slots=4",
                "-c",
                "max_wal_senders=4",
            ]
        )
    )


def _connect_container(network: Network) -> DockerContainer:
    return (
        DockerContainer(DEBEZIUM_CONNECT_IMAGE)
        .with_network(network)
        .with_network_aliases("connect")
        .with_exposed_ports(8083)
        .with_env("BOOTSTRAP_SERVERS", "kafka:9092")
        .with_env("GROUP_ID", f"foundry-lite-connect-{uuid4().hex}")
        .with_env("CONFIG_STORAGE_TOPIC", f"foundry-lite-connect-configs-{uuid4().hex}")
        .with_env("OFFSET_STORAGE_TOPIC", f"foundry-lite-connect-offsets-{uuid4().hex}")
        .with_env("STATUS_STORAGE_TOPIC", f"foundry-lite-connect-status-{uuid4().hex}")
        .with_env("CONNECT_REST_ADVERTISED_HOST_NAME", "connect")
    )


def _create_orders_table(postgres: PostgresContainer) -> None:
    engine = create_engine(postgres.get_connection_url(), future=True)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE public.orders (
                        order_id TEXT PRIMARY KEY,
                        status TEXT NOT NULL,
                        amount INTEGER NOT NULL
                    )
                    """
                )
            )
            conn.execute(text("ALTER TABLE public.orders REPLICA IDENTITY FULL"))
    finally:
        engine.dispose()


def _write_order_changes(postgres: PostgresContainer) -> None:
    engine = create_engine(postgres.get_connection_url(), future=True)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO public.orders (order_id, status, amount) VALUES (:id, 'PENDING', 700)"),
                {"id": "O-CDC-1001"},
            )
            conn.execute(
                text("UPDATE public.orders SET status = 'APPROVED', amount = 725 WHERE order_id = :id"),
                {"id": "O-CDC-1001"},
            )
            conn.execute(text("DELETE FROM public.orders WHERE order_id = :id"), {"id": "O-CDC-1001"})
    finally:
        engine.dispose()


def _register_postgres_connector(
    connect: DockerContainer,
    *,
    name: str,
    topic_prefix: str,
    suffix: str,
) -> None:
    _post_json(
        connect,
        "/connectors",
        {
            "name": name,
            "config": {
                "connector.class": "io.debezium.connector.postgresql.PostgresConnector",
                "tasks.max": "1",
                "database.hostname": "postgres",
                "database.port": "5432",
                "database.user": "postgres",
                "database.password": "postgres",
                "database.dbname": "inventory",
                "topic.prefix": topic_prefix,
                "table.include.list": "public.orders",
                "plugin.name": "pgoutput",
                "slot.name": _replication_slot_name(suffix),
                "publication.name": _publication_name(suffix),
                "publication.autocreate.mode": "filtered",
                "snapshot.mode": "no_data",
                "tombstones.on.delete": "false",
            },
        },
    )


def _wait_for_connect(connect: DockerContainer) -> None:
    _wait_until(lambda: _connect_ready(connect))


def _wait_for_connector_running(connect: DockerContainer, name: str) -> None:
    def running() -> bool:
        status = _get_json(connect, f"/connectors/{name}/status")
        tasks = status.get("tasks")
        return (
            status.get("connector", {}).get("state") == "RUNNING"
            and isinstance(tasks, list)
            and bool(tasks)
            and all(isinstance(task, Mapping) and task.get("state") == "RUNNING" for task in tasks)
        )

    _wait_until(running)


def _wait_for_replication_slot(postgres: PostgresContainer, slot_name: str) -> None:
    def slot_exists() -> bool:
        engine = create_engine(postgres.get_connection_url(), future=True)
        try:
            with engine.begin() as conn:
                return bool(
                    conn.execute(
                        text("SELECT 1 FROM pg_replication_slots WHERE slot_name = :slot_name"),
                        {"slot_name": slot_name},
                    ).scalar()
                )
        finally:
            engine.dispose()

    _wait_until(slot_exists)


def _wait_for_cdc_events(bootstrap_servers: str, topic: str) -> None:
    from foundry_lite.infrastructure.adapters import (
        DebeziumPostgresSourceConfig,
        DebeziumPostgresStreamAdapter,
        KafkaStreamAdapter,
        KafkaStreamAdapterConfig,
        KafkaStreamSubscription,
    )

    def has_expected_events() -> bool:
        adapter = DebeziumPostgresStreamAdapter(
            KafkaStreamAdapter(
                KafkaStreamAdapterConfig(
                    bootstrap_servers=bootstrap_servers,
                    consumer_group=f"foundry-lite-cdc-probe-{uuid4().hex}",
                    subscriptions=(KafkaStreamSubscription("erp_orders_cdc", topic),),
                    poll_timeout_seconds=1.0,
                    max_empty_polls=10,
                )
            ),
            DebeziumPostgresSourceConfig(primary_key=("order_id",)),
        )
        events = adapter.read_events("erp_orders_cdc", limit=3)
        return [event.payload["op"] for event in events] == ["c", "u", "d"]

    _wait_until(has_expected_events)


def _replication_slot_name(suffix: str) -> str:
    return f"foundry_lite_{suffix[:24]}"


def _publication_name(suffix: str) -> str:
    return f"foundry_lite_pub_{suffix[:20]}"


def _connect_ready(connect: DockerContainer) -> bool:
    _get_json(connect, "/connectors")
    return True


def _wait_until(condition: Callable[[], bool], *, timeout_seconds: float = 120, interval_seconds: float = 1) -> None:
    deadline = monotonic() + timeout_seconds
    wrapped = _condition_without_transient_errors(condition)
    while monotonic() < deadline:
        if wrapped():
            return
        Event().wait(interval_seconds)
    raise TimeoutError("condition was not satisfied before timeout")


def _condition_without_transient_errors(condition: Callable[[], bool]) -> Callable[[], bool]:
    def wrapped() -> bool:
        try:
            return condition()
        except (ConnectionError, HTTPError, TimeoutError, URLError):
            return False

    return wrapped


def _get_json(connect: DockerContainer, path: str) -> Mapping[str, object]:
    with urlopen(_request(connect, path), timeout=5) as response:
        payload = response.read().decode("utf-8")
    parsed = json.loads(payload)
    return parsed if isinstance(parsed, Mapping) else {}


def _post_json(connect: DockerContainer, path: str, payload: Mapping[str, object]) -> None:
    body = json.dumps(dict(payload), sort_keys=True).encode("utf-8")
    with urlopen(_request(connect, path, method="POST", body=body), timeout=10) as response:
        response.read()


def _request(connect: DockerContainer, path: str, *, method: str = "GET", body: bytes | None = None) -> Request:
    return Request(
        _connect_url(connect, path),
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )


def _connect_url(connect: DockerContainer, path: str) -> str:
    host = connect.get_container_host_ip()
    port = connect.get_exposed_port(8083)
    return f"http://{host}:{port}{path}"


def _preview_dataset(storage_root: Path, dataset_ref: str) -> list[dict[str, object]]:
    core = FoundryLiteCore(dependencies=create_local_core_dependencies(storage_root=storage_root))
    return [dict(row) for row in core.preview_dataset(dataset_ref, ctx=demo_admin_context())]
