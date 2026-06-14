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

## Explicitly Deferred

- PostgreSQL JSONB production object store.
- PostgreSQL snapshot connector implementation.
- Alembic migration history.
- Temporal worker execution.
- Real CEL or JSON Logic evaluator.
- Real external ERP/webhook writeback.
- Kafka/Redpanda streaming ingest.
- Debezium CDC.
- OpenSearch live cluster deployment and managed operations beyond the Sprint 42 adapter/projection proof.
- Iceberg production catalog.
- Spark runner.
- Production Flink runner.
- Full infra-swap implementation for every boundary; v1 first fixes the boundary and contract tests, then later sprints add production-scale implementations.
- Full visual pipeline builder.
- Complex CBAC/ABAC security.
- Functions on Objects runtime.
