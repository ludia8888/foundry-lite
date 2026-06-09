# Implementation Status

이 문서는 현재 커밋이 실제로 보장하는 것과, 계획 문서에 남아 있는 다음 목표를 구분한다.

## Current Reality

- Storage adapter: local SQLite database plus filesystem object storage.
- Object properties: SQLAlchemy `JSON` columns, not PostgreSQL `JSONB`.
- Ingestion: CSV upload is implemented. PostgreSQL snapshot is a documented boundary, not an implemented connector.
- CSV upload size: default local limit is 50 MiB via `FOUNDRY_LITE_MAX_CSV_UPLOAD_BYTES`.
- Preconditions: `safeExpression` supports a small safe subset only:
  - `object.status in ['PENDING', 'REVIEW']`
  - `object.status == 'PENDING'`
- Writeback: `mock_erp_simulator` records simulated before-commit writeback rows. It does not call an external ERP.
- Security default: API requests without role headers resolve to `viewer`. CLI and demo scripts use an explicit demo admin context.
- Object sets: static and dynamic saved object sets are implemented through core service, HTTP API, CLI, and Object Explorer list/create controls. Private owner visibility and temporary expiry are enforced.
- CLI: current implementation uses Python `argparse`; Typer is not installed in the current runtime.
- Worker: `apps/worker` is a placeholder for a future Temporal worker.
- Migrations: schema bootstraps through SQLAlchemy `metadata.create_all`; Alembic migrations are not implemented yet.
- Application structure: `FoundryLiteCore` is now a Facade. Dataset, Transform, Ontology, Object, Action, Materialization, runtime event, and demo orchestration logic live in focused service modules. CI blocks application modules above 500 lines.
- Scale foundation status: the planning docs now require an explicit Sprint 02A for infra-swap boundaries, contract tests, trace keys, and composition-root injection. The current code is not yet fully port/adapter extracted.

## Still Targeted, Not Yet Implemented

- PostgreSQL JSONB object store with production indexes and row-level security.
- PostgreSQL snapshot connector integration test.
- Real CEL or JSON Logic evaluator.
- Real ERP/webhook writeback connector and retry worker.
- Temporal workflow/worker execution.
- Alembic migration history and upgrade/rollback tests.
- Operations UI beyond the current object explorer/object-set controls, especially failed run retry and DLQ workflows.
- Sprint 02A Scale Foundation implementation: repository/port extraction beneath the current service modules, contract tests for fake/local adapters, and CI import guards for production PostgreSQL, S3/MinIO, Spark/Flink, Kafka/Redpanda, OpenSearch, Temporal, connector, and auth adapters.

## Quality Signal Boundaries

- Branch coverage is the main behavior gate.
- Public callable coverage means "a public callable was executed at least once"; it is not a substitute for branch/path coverage.
- Some unit tests still exercise private helpers to pin failure edges. CI records this as a baseline and blocks increases; these should be replaced with public API tests as modules are split out.
