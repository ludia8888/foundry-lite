# Foundry-lite MVP Scope

## Included In V1 Core

- CSV upload and PostgreSQL snapshot boundary for raw datasets.
- Immutable dataset transaction and manifest commit protocol.
- DuckDB SQL transforms with input version binding and lineage.
- Ontology YAML import, validation, and activation.
- Order and Customer object indexing.
- Object query, link traversal, and saved-set ready query model.
- ApproveOrder action with required parameters, safe precondition evaluation, idempotency, optimistic concurrency, audit, object edits, and outbox events.
- `ops.action_log` and `ops.order_current` materialization.
- Downstream `customer_risk` transform that consumes materialized operational state.

## Explicitly Deferred

- Kafka/Redpanda streaming ingest.
- Debezium CDC.
- OpenSearch production search.
- Iceberg production catalog.
- Spark runner.
- Full visual pipeline builder.
- Complex CBAC/ABAC security.
- Functions on Objects runtime.

