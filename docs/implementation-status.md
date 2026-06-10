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
- Infra boundary gates: CI now blocks domain concrete infra imports, application concrete infra imports above the current baseline `0`, scale SDK imports in domain/application, service mixin method-name conflicts, hidden service-mixin dependency access, and cross-mixin call graph cycles/depth/fan-out regressions.
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

## Refactor Pointers

These are known design debts that are currently gated (so they cannot regress) but
should be paid down on a planned timeline. They are not bugs; they are deliberate
intermediate states.

- **Mixin → constructor injection (Application Service canonicalization).**
  Today `FoundryLiteCore` composes 8 service capabilities via Python multiple
  inheritance (`ServiceMixin` classes) sharing a single `CoreDependencies` bag.
  This is a Python idiom, not a name in the GoF / PEAA / DDD canon. Our
  engineering guideline (`foundry_lite_python_engineering_guidelines_ko.md`
  §197–256) promises Application Service as a standard pattern, and 8 of the 9
  promised patterns (Facade, Repository, Unit of Work, Adapter, Strategy,
  Specification, Template Method, Outbox, DTO) are implemented canonically.
  Only Application Service is implemented as mixins. Risks (call-graph cycles,
  fan-out explosion, depth blowup, method-name conflicts, hidden self.X
  dependencies) are currently held in check by 4 static gates
  (`check_mixin_method_conflicts`, `check_service_mixin_dependencies`,
  `check_mixin_call_graph`, application-size cap). The longest mixin call chain
  is already at the depth ceiling (7/7) with zero margin. Target state: each
  service becomes a class with explicit constructor injection
  (`class ActionService: def __init__(self, action_repository, ...)`), and
  `FoundryLiteCore` wires them as attributes instead of inheriting from them.
  This is a mechanical 1-week refactor with no public API change. Recommended
  window: after Sprint 9.4 (Postgres testcontainer) lands and before the next
  4 Sprint 02A boundaries (Workflow / Stream / Search / Connector / Auth) are
  extracted, so the new ports land in canonical-shape services from day one.

## Quality Signal Boundaries

- Branch coverage is the main behavior gate.
- Public callable coverage means "a public callable was executed at least once"; it is not a substitute for branch/path coverage.
- Some unit tests still exercise private helpers to pin failure edges. CI records this as a baseline and blocks increases; these should be replaced with public API tests as modules are split out.
