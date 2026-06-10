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
- Scale foundation status: Sprint 02A implementation has started. `DatasetStorageAdapter` is now a real port with local and fake-storage adapters, shared contract tests, a fake-storage swap rehearsal for CSV commit/inspect/preview, and API/CLI composition-root selection. `MetadataRepository` now owns schema bootstrap/reset/default tenant-user DB writes, `DatasetRepository` owns dataset registry create/find DB reads/writes, `DatasetTransactionRepository` owns dataset transaction/version/file DB state changes plus best-effort run failure updates, `DatasetVersionRepository` owns committed version/schema DB reads, `RuntimeRepository` owns audit/outbox/lineage/list-runs DB boundaries, `ComputeAdapter` owns CSV/Parquet/SQL transform/health-check execution behind DuckDB local and fake compute adapters, `ObjectReadRepository` owns object record/link read DB boundaries, `ObjectIndexRepository` owns object indexing run/object record/link write DB boundaries, `ObjectSetRepository` owns object set row/membership metadata DB boundaries, `ActionRepository` owns action run/writeback/object edit/object target update DB boundaries, and `OntologyRepository` owns ontology version/object/property/link/action type metadata DB boundaries. The current code is still not fully port/adapter extracted.
- Infra boundary gates: CI now blocks domain concrete infra imports, application concrete infra imports above the current baseline `0`, scale SDK imports in domain/application, and service mixin method-name conflicts.
- Transaction boundary: `TransactionContext` is now an explicit opaque Protocol in `application/ports`. All repository ports take `transaction: TransactionContext` instead of `transaction: Any`, so future scale adapters (PostgreSQL test containers, in-memory fakes, transactional Kafka outbox writers) can supply their own handle types without changing repository signatures.
- Concrete infra imports in `application/`: now `0`. Every service module talks to repository ports only. The remaining concrete SQLAlchemy access lives behind `infrastructure/repositories/*`.

## Still Targeted, Not Yet Implemented

- PostgreSQL JSONB object store with production indexes and row-level security.
- PostgreSQL snapshot connector integration test.
- Real CEL or JSON Logic evaluator.
- Real ERP/webhook writeback connector and retry worker.
- Temporal workflow/worker execution.
- Alembic migration history and upgrade/rollback tests.
- Operations UI beyond the current object explorer/object-set controls, especially failed run retry and DLQ workflows.
- Sprint 02A Scale Foundation completion: WorkflowAdapter, StreamAdapter, SearchAdapter, ConnectorAdapter, and AuthProvider ports remain unextracted; the local repositories still rely on SQLAlchemy under the hood without a PostgreSQL contract-test pairing.
- Postgres testcontainer contract tests so that every repository contract suite runs against both SQLite and PostgreSQL, closing the "Repository pattern complete" gap.

## Quality Signal Boundaries

- Branch coverage is the main behavior gate.
- Public callable coverage means "a public callable was executed at least once"; it is not a substitute for branch/path coverage.
- Some unit tests still exercise private helpers to pin failure edges. CI records this as a baseline and blocks increases; these should be replaced with public API tests as modules are split out.
