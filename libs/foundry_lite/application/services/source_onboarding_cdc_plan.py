"""Debezium CDC source operation plan view helpers."""

from __future__ import annotations

from collections.abc import Mapping
from shlex import quote

from foundry_lite.application.ports import SourceConnectionRow
from foundry_lite.application.services.source_onboarding_views import source_view
from foundry_lite.domain.errors import ValidationFailed


def debezium_operation_plan(
    row: SourceConnectionRow,
    *,
    object_type_api_name: str = "Order",
    object_indexing_status: Mapping[str, object] | None = None,
) -> dict[str, object]:
    summary = row["config_summary"]
    dataset_ref = _required_text(summary, "datasetRef")
    stream_name = _required_text(summary, "streamName")
    topic = _required_text(summary, "topic")
    consumer_group = _required_text(summary, "consumerGroup")
    primary_key = _string_sequence(summary.get("primaryKey"))
    return {
        "source": source_view(row),
        "readiness": _readiness(primary_key),
        "sync": _sync_payload(row, dataset_ref, stream_name, topic, consumer_group),
        "workerCommands": _worker_commands(
            dataset_ref,
            stream_name,
            topic,
            consumer_group,
            primary_key,
            object_type_api_name,
        ),
        "objectIndexing": _object_indexing(dataset_ref, object_type_api_name, primary_key),
        "objectIndexingStatus": object_indexing_status or {},
        "operatorChecklist": _operator_checklist(primary_key),
    }


def _readiness(primary_key: list[str]) -> dict[str, object]:
    return {
        "status": "ready_for_cdc_workers" if primary_key else "needs_primary_key",
        "canStartArchiveFromBrowser": True,
        "canProvisionDebeziumConnectorFromBrowser": False,
        "requiresExternalConnectorRegistration": True,
        "requiresWorkerProcess": True,
        "blockingReasons": [] if primary_key else ["primary_key_required_for_debezium_normalization"],
    }


def _sync_payload(
    row: SourceConnectionRow,
    dataset_ref: str,
    stream_name: str,
    topic: str,
    consumer_group: str,
) -> dict[str, object]:
    source_name = row["source_name"]
    return {
        "sourceName": source_name,
        "datasetRef": dataset_ref,
        "streamName": stream_name,
        "topic": topic,
        "consumerGroup": consumer_group,
        "expectedConfigFingerprint": row["config_fingerprint"],
        "startPath": f"/api/sources/cdc/debezium/{source_name}/sync/start",
    }


def _worker_commands(
    dataset_ref: str,
    stream_name: str,
    topic: str,
    consumer_group: str,
    primary_key: list[str],
    object_type_api_name: str,
) -> dict[str, object]:
    primary_key_arg = ",".join(primary_key) if primary_key else "<primary-key-columns>"
    return {
        "streamArchive": _shell_command(
            _stream_archive_command(dataset_ref, stream_name, topic, consumer_group, primary_key_arg)
        ),
        "objectIndexer": _shell_command(_object_indexer_command(object_type_api_name, dataset_ref)),
    }


def _stream_archive_command(
    dataset_ref: str,
    stream_name: str,
    topic: str,
    consumer_group: str,
    primary_key_arg: str,
) -> list[str]:
    return [
        "pnpm",
        "worker:stream-archive",
        "--dataset-ref",
        dataset_ref,
        "--stream-name",
        stream_name,
        "--topic",
        topic,
        "--bootstrap-servers",
        "localhost:19092",
        "--consumer-group",
        consumer_group,
        "--schema-strategy",
        "cdc_envelope_json",
        "--cdc-primary-key",
        primary_key_arg,
        "--continuous",
    ]


def _object_indexer_command(object_type_api_name: str, dataset_ref: str) -> list[str]:
    return [
        "pnpm",
        "worker:cdc-object-indexer",
        "--object-type",
        object_type_api_name,
        "--source-dataset",
        dataset_ref,
        "--continuous",
    ]


def _object_indexing(dataset_ref: str, object_type_api_name: str, primary_key: list[str]) -> dict[str, object]:
    return {
        "objectTypeApiName": object_type_api_name,
        "sourceDatasetRef": dataset_ref,
        "cdcPrimaryKey": primary_key,
        "requiresActiveOntologyCdcBacking": True,
        "expectedOntologyBacking": {
            "backing": {
                "cdc": {
                    "dataset": dataset_ref,
                    "primaryKeyColumns": primary_key,
                    "deletePolicy": "tombstone",
                }
            }
        },
    }


def _operator_checklist(primary_key: list[str]) -> list[dict[str, object]]:
    primary_key_status = "ready" if primary_key else "required"
    return [
        _check("logical_replication", "external_required", "Enable logical replication on the source database."),
        _check("debezium_connector", "external_required", "Register the Debezium connector against Kafka Connect."),
        _check("kafka_topic", "external_required", "Confirm the Debezium topic is receiving c/u/d/r envelope events."),
        _check("cdc_primary_key", primary_key_status, "Store primary-key columns for deterministic object ids."),
        _check(
            "stream_archive_worker",
            "worker_required",
            "Run the stream archive worker to commit CDC dataset versions.",
        ),
        _check(
            "cdc_object_indexer",
            "worker_required",
            "Run the CDC object-indexer worker after an active CDC-backed object type exists.",
        ),
    ]


def _check(key: str, status: str, label: str) -> dict[str, object]:
    return {"key": key, "status": status, "label": label}


def _required_text(summary: Mapping[str, object], field: str) -> str:
    value = summary.get(field)
    if isinstance(value, str) and value.strip():
        return value
    raise ValidationFailed("source config is missing required field", details={"field": field})


def _string_sequence(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValidationFailed("source config primaryKey must be a list of strings")
    return [item for item in value if item.strip()]


def _shell_command(parts: list[str]) -> str:
    return " ".join(quote(part) for part in parts)
