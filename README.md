# Foundry-lite

Foundry-lite is a small, replayable operational object system inspired by the local project
documents in this repository.

The MVP core proves this loop:

```text
CSV/local snapshot
-> raw dataset
-> DuckDB SQL transform
-> clean dataset
-> ontology activation
-> object index
-> ApproveOrder action
-> action_log/order_current materialization
-> downstream transform
-> Customer object refresh
```

Current implementation note: this commit is a local core vertical slice. It uses SQLite plus
filesystem storage; PostgreSQL JSONB storage, PostgreSQL snapshot ingest, Temporal, Alembic, real
CEL, and real external writeback are still future work.

Scale architecture note: the planning docs now treat `Scale Foundation / Infra Swap Boundary` as an
early foundation requirement. The intent is that storage, metadata DB, compute, event, search,
workflow, connector, and auth implementations can later move from local adapters to S3/PostgreSQL,
Spark/Flink, Kafka/Redpanda, OpenSearch, Temporal, and enterprise auth without rewriting the core
product logic. The current implementation has not completed that extraction yet; see
[docs/implementation-status.md](docs/implementation-status.md).

Local demo:

```bash
pnpm demo:supply-chain
```

The one-command demo uses an isolated `.foundry-lite-demo/` home and starts fresh by default, so it
can be run repeatedly without depending on a previous developer database state. If
`FOUNDRY_LITE_HOME` is set explicitly, the CLI respects that home and does not reset it unless
`demo run-supply-chain --fresh` is passed.

The local adapter uses SQLite plus local object storage. The domain and application contracts keep
the same boundaries described in the planning docs: dataset versions are immutable, writes are
audited, action apply requires `expectedObjectVersion`, and all outputs are committed through the
dataset transaction/manifest protocol.

Quality and observability:

```bash
pnpm ci:gate
docker compose -f infra/docker-compose.dev.yml up -d prometheus tempo grafana
```

See [docs/quality-observability.md](docs/quality-observability.md) for static analysis, dynamic
diagnostics, OpenTelemetry, Grafana, Playwright, and CI/CD gates.

Known limitations and v1.5 backlog:

- PostgreSQL JSONB object storage and PostgreSQL snapshot ingest are not productionized yet.
- Temporal workflow execution, Kafka/OpenSearch-scale adapters, and Alembic migrations remain backlog.
- CEL/JSON Logic and real external ERP/webhook writeback connectors remain future hardening work.
- Local performance smoke runs in CI with a fast profile; larger release measurements are available via
  `pnpm quality:mvp-performance-release-100k` and `pnpm quality:mvp-performance-release-1m`.

See [docs/implementation-status.md](docs/implementation-status.md) for the exact line between what
is implemented now and what remains a target.
