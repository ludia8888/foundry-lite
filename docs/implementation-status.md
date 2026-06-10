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
- Demo execution: `pnpm demo:supply-chain` uses an isolated `.foundry-lite-demo/` home and fresh execution by default when `FOUNDRY_LITE_HOME` is not explicitly set. This keeps the one-command demo repeatable and prevents old local action/object state from blocking the closed loop. Explicit `FOUNDRY_LITE_HOME` runs are preserved unless the caller passes `demo run-supply-chain --fresh`.
- CLI: current implementation uses Python `argparse`; Typer is not installed in the current runtime.
- Worker: `apps/worker` is a placeholder for a future Temporal worker.
- Migrations: schema bootstraps through SQLAlchemy `metadata.create_all`; Alembic migrations are not implemented yet.
- Application structure: `FoundryLiteCore` is now a Facade. Dataset, Transform, Ontology, Object, Action, Materialization, runtime event, and demo orchestration logic live in focused service modules. Each service declares the exact `CoreDependencies` fields it directly uses through `required_dependencies` and the exact service collaborators it directly calls through `required_collaborators`; cross-service calls use explicit collaborator attributes such as `runtime_service` or `dataset_registry_service`. CI blocks application modules above 500 lines.
- Scale foundation status: Sprint 02A implementation has started. `DatasetStorageAdapter` is now a real port with local and fake-storage adapters, shared contract tests, a fake-storage swap rehearsal for CSV commit/inspect/preview, and API/CLI composition-root selection. `MetadataRepository` now owns schema bootstrap/reset/default tenant-user DB writes, `DatasetRepository` owns dataset registry create/find DB reads/writes, `DatasetTransactionRepository` owns dataset transaction/version/file DB state changes plus best-effort run failure updates, `DatasetVersionRepository` owns committed version/schema DB reads, `RuntimeRepository` owns audit/outbox/lineage/list-runs DB boundaries, `ComputeAdapter` owns CSV/Parquet/SQL transform/health-check execution behind DuckDB local and fake compute adapters, `ObjectReadRepository` owns object record/link read DB boundaries, `ObjectIndexRepository` owns object indexing run/object record/link write DB boundaries, `ObjectSetRepository` owns object set row/membership metadata DB boundaries, `ActionRepository` owns action run/writeback/object edit/object target update DB boundaries, and `OntologyRepository` owns ontology version/object/property/link/action type metadata DB boundaries. The current code is still not fully port/adapter extracted.
- Infra boundary gates: CI now blocks domain concrete infra imports, application concrete infra imports above the current baseline `0`, scale SDK imports in domain/application, undeclared/unused service dependencies and collaborators, hidden service attribute access, explicit collaborator call graph cycles/depth/fan-out regressions, and any return of `core._...` private facade tests.
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
- Sprint 02A Scale Foundation completion: WorkflowAdapter, StreamAdapter, SearchAdapter, ConnectorAdapter, and AuthProvider ports remain unextracted; the local repositories still rely on SQLAlchemy under the hood.
- Postgres testcontainer contract pairing now covers `dataset_transaction`, `dataset_quality`, `runtime`, and `object_index` repositories (Sprint 9.4). The remaining repository contract suites still run against SQLite + fake only. The local escape hatch `FOUNDRY_LITE_SKIP_POSTGRES_CONTRACTS=1` exists only for Docker-unavailable developer machines; `pnpm ci:gate` rejects it so release/CI evidence cannot skip PostgreSQL parity.
- The closed-loop demo is repeatable as a fresh one-command smoke, but replay operations such as retrying failed runs, DLQ replay, and action-edit reindex replay remain future Operations UI/CLI work.

## Refactor Pointers

These are known design debts that are currently gated (so they cannot regress) but
should be paid down on a planned timeline. They are not bugs; they are deliberate
intermediate states.

- **Application Service canonicalization.**
  `FoundryLiteCore` no longer composes service capabilities through Python
  multiple inheritance. It is a thin Facade with explicit public forwarders,
  while `CoreServices` constructs `ActionService`, Dataset service group,
  Object service group, Transform, Ontology, Materialization, Runtime, and Demo
  services with service-specific dependency injection. A service receives only
  the `CoreDependencies` fields listed in its `required_dependencies`; for
  example, `ActionService` receives `engine`, `policy`, and `action_repository`,
  not storage or transform adapters. A service also receives only the
  collaborator services listed in its `required_collaborators`; for example,
  `ActionService` receives runtime, ontology, object-record, and object-indexing
  collaborators, not all 16 services. Cross-service helper access now uses
  explicit collaborator attributes such as `runtime_service._audit(...)` rather
  than `__getattr__` method lookup. These rules are held in check by static gates:
  `check_service_dependencies.py`, `check_service_call_graph.py`, and the
  application-size cap. This keeps the
  public API stable while removing facade-level MRO risk before Workflow /
  Stream / Search / Connector / Auth boundaries are extracted.
- **Remaining Facade compatibility debt.**
  The flat service method registry and `FoundryLiteCore.__getattr__` /
  `FoundryLiteCore.__setattr__` monkeypatch bridge have been removed. The
  remaining compatibility cost is the explicit public forwarder list in
  `FoundryLiteCore`, which keeps API/CLI/test entrypoints stable while services
  continue to evolve independently.

## Quality Signal Boundaries

- Branch coverage is the main behavior gate.
- Public callable coverage means "a public callable was executed at least once"; it is not a substitute for branch/path coverage.
- CI enforces `0` `core._...` private facade references in tests. Some lower-level tests still target service/helper modules directly to pin failure edges, but the public facade no longer exposes or forwards private helper methods.
