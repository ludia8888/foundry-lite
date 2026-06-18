# Foundry-lite MVP Scope

## Included In V1 Core

- CSV upload for raw datasets.
- PostgreSQL snapshot connector boundary is documented but not implemented in the current local slice.
- SQLite plus local filesystem storage for the current local adapter.
- Scale Foundation is part of the v1 architecture target: storage, metadata, compute, event, search, workflow, connector, and auth boundaries must be defined early enough that future infra can be swapped behind ports/adapters.
- Immutable dataset transaction and manifest commit protocol.
- DuckDB SQL transforms with input version binding and lineage.
- Ontology YAML import, validation, and activation.
- Order and Customer object indexing.
- Object query, link traversal, and saved-set ready query model.
- ApproveOrder action with required parameters, safe precondition evaluation, idempotency, optimistic concurrency, audit, object edits, and outbox events.
- `ops.action_log` and `ops.order_current` materialization.
- Downstream `customer_risk` transform that consumes materialized operational state.

## Implemented Outside The V1 Core Gate

These items are not required to claim the Sprint 00-36 MVP core complete, but
they do have code and test evidence in the current checkout. Their production
packaging, always-on workers, and managed infrastructure are still separate
future work.

- REST pull connector adapter and signed webhook append ingest.
- Local/fake stream archive writer plus Kafka-compatible adapter and one-shot worker proof.
- Debezium-shaped CDC archive proof plus live Debezium/PostgreSQL topic proof.
- CDC object indexing for upsert, tombstone delete, idempotent replay, stale-event skip, and `object.changed` trigger evidence.
- Elasticsearch-compatible search projection, rebuild, orphan-drift detection, and object-store fallback proof.
- Shadow reindex, active index pointer, count/hash validation, and action-edit replay proof.
- Iceberg dataset storage ratchet: dataset versions pin Iceberg snapshot metadata behind the existing Dataset API, with MinIO/S3-backed proof and failure evidence.
- Spark compute ratchet: Spark implements the `ComputeAdapter` boundary for CSV ingest and SQL transforms, with lineage/health/abort proof and S3+Iceberg+Spark composition evidence.
- Temporal workflow adapter ratchet: idempotent start, timeout/unavailable/cancel/error-payload semantics are covered at the adapter boundary, but product workflows do not yet run through Temporal.
- Ontology-driven sensitive classification: `finance`/`pii` classifications mask object properties, dataset preview columns, object-query/search/object-set inference surfaces, explain payloads, action audit refs, and Operations run/detail payloads for unprivileged roles.
- Runtime profile packaging extras: S3, Iceberg, Spark, Temporal, Elasticsearch, Kafka, and PostgreSQL profile libraries are guarded as installable optional extras.

## Explicitly Deferred

- PostgreSQL JSONB production object store.
- PostgreSQL snapshot connector implementation.
- Alembic migration history.
- Real CEL or JSON Logic evaluator.
- Real external ERP/webhook writeback.
- Continuously running Kafka/Redpanda stream workers and deployment-specific broker packaging beyond the current adapter/one-shot worker proof.
- Continuously running CDC object-indexing workers and production CDC deployment packaging beyond the current archive/indexing proof.
- Managed Elasticsearch deployment and operations beyond the current adapter/projection/live-cluster proof.
- Iceberg maintenance, production catalog operations, and managed retention/compaction beyond the current storage ratchet.
- Real Spark cluster deployment and distributed failure modes beyond the current local adapter/composition ratchet.
- Temporal product workflow execution and managed worker operations beyond the current adapter ratchet.
- Kubernetes Helm, backup, restore, and operational runbooks.
- Production Flink runner.
- Full infra-swap implementation for every boundary; v1 first fixes the boundary and contract tests, then later sprints add production-scale implementations.
- Full visual pipeline builder.
- Complex CBAC/ABAC security.
- Functions on Objects runtime.
