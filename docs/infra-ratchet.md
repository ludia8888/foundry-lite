# Foundry-lite Infra Ratchet

**Purpose:** Add production-style infrastructure one adapter at a time, then
stress that adapter until its normal path, failure path, concurrency behavior,
retry semantics, partial-success recovery, and operator evidence are all pinned
by regression tests.

This is a release discipline, not a roadmap slogan. A new infrastructure
dependency is not considered "added" when it connects once. It is considered
added only when it becomes a regression shield for future infrastructure.

## One Infrastructure At A Time

Foundry-lite must not introduce multiple new production infrastructure families
in the same implementation step. For example, do not add S3, Iceberg, Spark, and
Temporal in one PR series and then try to debug their failures together.

The required loop is:

```text
pick one infrastructure
-> add or extend the adapter boundary
-> prove the normal path
-> inject failure before, during, and after commit
-> run concurrency and retry probes
-> prove partial-success cleanup and recovery
-> expose operator evidence
-> run composition tests with every already-active infrastructure family
-> wire the proof into CI and docs
-> merge
-> only then pick the next infrastructure
```

## CI Contract

Every active infrastructure ratchet must have these proof classes before the
next infrastructure can move from `next` to `active`.

| Proof class                 | Meaning                                                                                           | CI/document evidence                                                            |
| --------------------------- | ------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| `adapter-contract`          | The new adapter obeys the same application port contract as local/fake profiles.                  | contract tests plus `check_contract_test_per_port.py`                           |
| `normal-path`               | A realistic happy path works through the public API/facade, not private helpers only.             | unit/integration/smoke test                                                     |
| `failure-injection`         | At least one targeted failure is injected at the dangerous commit point.                          | named regression test                                                           |
| `concurrency-race`          | Parallel or repeated operations cannot silently create duplicate/losing state.                    | concurrent or repeated test                                                     |
| `retry-idempotency`         | Retrying after an ambiguous or failed attempt does not create a second logical success.           | retry regression test                                                           |
| `partial-success`           | If external storage/system succeeds but local metadata fails, serving state remains safe.         | split-brain regression test                                                     |
| `recovery-cleanup`          | Cleanup is reachability-safe and never deletes committed evidence.                                | cleanup regression test                                                         |
| `composition-compatibility` | The new infrastructure works with every already-active infrastructure family, not only by itself. | focused cross-infra regression test                                             |
| `operator-evidence`         | Failure is visible in run/audit/transaction/error evidence, not only logs.                        | runtime evidence assertion                                                      |
| `docs-sync`                 | Current status, risk register, tricky checklist, and sprint evidence agree.                       | `check_infra_ratchet.py` + `check_doc_drift.py` + `check_checklist_evidence.py` |

The static CI lane runs `scripts/quality/check_infra_ratchet.py`, which verifies
that this document, the tricky failure checklist, commit-point risk register,
implementation status, package scripts, and `ci_gate.sh` still mention the
ratchet discipline. It also requires `quality:checklist-evidence`, so checked
tricky-failure test names cannot drift away from pytest collection while an
infra family is being advanced.

## Infra Tricky Matrix

`docs/infra-tricky-matrix.json` is the machine-readable shield that makes the
ratchet automatic. It maps every active infrastructure family and active
composition stack to:

```text
active infra id
-> related tricky checklist item ids
-> required proof classes
-> pytest test names
-> CI quality command
```

The static CI lane runs `quality:infra-tricky-matrix`
(`scripts/quality/check_infra_tricky_matrix.py`). If a new infrastructure family
is marked active without a matrix entry, if a related tricky item is still
unchecked, if the item does not cite the required test, if pytest cannot collect
that test, or if CI does not run the relevant quality command, the gate fails.
This keeps infra hardening from becoming a hand-maintained checklist that can
silently drift.

## Self And Composition Tests

Every infrastructure ratchet has two required layers:

1. **Self ratchet:** prove the new adapter/profile by itself against its own
   dangerous commit points: normal path, failure injection, concurrency,
   retry/idempotency, partial success, recovery cleanup, and operator evidence.
2. **Composition ratchet:** prove the new adapter/profile while all already
   active infrastructure families are also in the stack.

For example, after S3 is active, Iceberg cannot be considered covered by a
catalog-only test; it must prove Iceberg metadata and data files on S3/MinIO.
After Iceberg is active, Spark cannot be considered covered only by a local
filesystem test; it must also prove Spark transforms over Iceberg-on-S3 pinned
dataset versions and commits output back through Iceberg-on-S3. This is the
ratchet rule for every future infrastructure family: self tests plus composition
tests against the currently active stack.

## Active Ratchet Queue

| Order | Infrastructure                   | Status         | Why this order                                                                                                                                                                                                                 | Cannot advance until                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| ----- | -------------------------------- | -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1     | MinIO/S3 DatasetStorageAdapter   | active-covered | Storage is the base layer for ingest, transform output, materialization output, stream archive, Iceberg, backup, and restore.                                                                                                  | `quality:s3-storage` stays green in CI and S3 remains the only active production-style infra family in this ratchet.                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| 2     | Iceberg Catalog/TableAdapter     | active-covered | Iceberg adds a table metadata/catalog commit point on top of object storage.                                                                                                                                                   | `quality:iceberg` stays green in CI; each dataset version pins an exact Iceberg snapshot id and the DB COMMITTED version remains the serving source of truth.                                                                                                                                                                                                                                                                                                                                                                                                               |
| 3     | Spark ComputeAdapter             | active-covered | Spark should consume the same Dataset API and pinned versions without knowing storage internals.                                                                                                                               | `quality:spark` and `quality:infra-composition` stay green in CI; Spark runs transforms on local parquet materialized from pinned versions and the composition gate proves Iceberg-on-S3 input/output.                                                                                                                                                                                                                                                                                                                                                                      |
| 4     | Temporal WorkflowAdapter         | active-covered | Durable workflow execution changes retry/time semantics for long-running operations.                                                                                                                                           | `quality:temporal` stays green in CI; workflow start is idempotent by tenant/workflow/idempotency namespace and a workflow failure/timeout/cancel surfaces in a durable run error payload, proven on the time-skipping test server. S52 now uses this boundary for the `ConnectorSyncWorkflow` control-plane start/status/audit path, while full connector data-plane workflow execution remains a later slice. It is still a standalone family, not part of the S3+Iceberg+Spark composition stack.                                                                        |
| 5     | Managed Elasticsearch deployment | active-covered | The adapter/projection proof existed; this ratchet adds live cluster failure evidence and the projection-rebuild contract.                                                                                                     | `quality:elasticsearch` stays green in CI; a cluster outage surfaces as a typed AdapterError (timeout/unavailable/rate_limited/validation), search stays a rebuildable projection, and the version guard holds under concurrent writers. Orthogonal projection (not a storage/compute commit point), so it is a standalone family, not part of the S3+Iceberg+Spark composition stack.                                                                                                                                                                                      |
| 6     | Media/Content Plane              | active-covered | Unstructured media is a first-class plane (Foundry-style media set/transform/ontology media-reference/semantic search); L9 promotes it to active-covered with a golden end-to-end live pipeline across all nine proof classes. | `quality:media-active-covered` stays green in CI; the golden pipeline proves raw upload (live MinIO) -> real OCR/ASR -> content_units + SUCCEEDED `media_processing_runs` -> ontology `media_reference` binding -> live Elasticsearch index -> real hybrid/semantic search (cited `text_hash` + ACL) -> real Pillow preview cache, with one `media_item_version_id` threading upstream->downstream and the DB COMMITTED version remaining the only serving truth. Orthogonal media plane, so it is a standalone family, not part of the S3+Iceberg+Spark composition stack. |

## Active Ratchet: MinIO/S3 DatasetStorageAdapter

The first infrastructure ratchet is a MinIO-backed S3-compatible
`DatasetStorageAdapter`. It must keep the existing Dataset API and transaction
shape intact. The application must still treat the metadata DB `COMMITTED`
dataset version as the serving source of truth; an object existing in S3 is not
enough to serve it.

Required first-run evidence:

```text
test_s3_dataset_storage_adapter_contract
test_s3_partial_multipart_upload_never_becomes_committed_version
test_s3_commit_storage_success_db_failure_creates_orphan_cleanup_evidence
test_s3_committed_manifest_missing_marks_storage_corruption
test_s3_abort_cleanup_never_deletes_committed_manifest
test_s3_concurrent_dataset_commits_allocate_strictly_increasing_versions
test_s3_retry_after_storage_timeout_does_not_duplicate_version
test_s3_storage_failure_is_visible_in_operations
```

Current CI binding:

```text
pnpm --silent quality:s3-storage
```

This focused gate runs `tests/integration/test_s3_dataset_storage_adapter.py`
against MinIO/Testcontainers. The tests use deterministic fault injection on
the S3 client for timeout/partial-upload edges while still proving the adapter
against a live S3-compatible object store.

Required implementation constraints:

| Constraint                                                | Reason                                                                                  |
| --------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| Optional dependency only, for example `foundry-lite[s3]`. | Non-S3 local/dev deployments should not inherit cloud SDK requirements.                 |
| MinIO/Testcontainers before AWS S3.                       | We need deterministic failure injection before real cloud operations.                   |
| No S3 rename-as-commit assumption.                        | S3 object writes and metadata DB commits are separate failure domains.                  |
| Manifest verification after upload.                       | Writer row count alone is not proof that the readable object is complete.               |
| Reachability-safe cleanup.                                | Prefix delete must not remove committed artifacts.                                      |
| Operator evidence on every failed promotion/cleanup.      | A failed object-storage action must be diagnosable without opening the bucket manually. |

## Iceberg After S3

Iceberg must not be treated as "just another file format." It introduces a
catalog/table snapshot commit point. The dangerous split-brain is:

```text
Iceberg catalog snapshot committed
but Foundry dataset_versions commit failed
```

or the reverse:

```text
Foundry dataset_versions committed
but Iceberg metadata location/snapshot is missing or unreadable
```

Therefore Iceberg should add a catalog/table boundary above the S3 storage
ratchet. The exit criteria are:

| Requirement              | Meaning                                                                                                       |
| ------------------------ | ------------------------------------------------------------------------------------------------------------- |
| Same Dataset API         | Parquet manifest datasets and Iceberg datasets are read through the same dataset facade.                      |
| Pinned snapshot metadata | `dataset_versions` stores the Iceberg metadata location and snapshot id used by transforms.                   |
| Schema policy parity     | Existing compatible/breaking schema rules apply to Iceberg evolution.                                         |
| Split-brain evidence     | Catalog commit success plus DB failure leaves recoverable operator evidence, not silent serving drift.        |
| Transform ignorance      | DuckDB/Spark-compatible readers consume the dataset without application services branching on infrastructure. |

### Active Ratchet: Iceberg (covered)

`IcebergDatasetStorageAdapter` is wired behind the same `DatasetStorageAdapter`
port (profile `iceberg`), selected by `FOUNDRY_LITE_ADAPTER_PROFILE=iceberg`.
Each Foundry dataset version is committed as one Iceberg table snapshot; the
`manifest_uri` pins an exact `snapshot_id`, so reads stay isolated from later
commits and the DB COMMITTED version remains the serving source of truth. Data
files and table metadata live in object storage (S3/MinIO); a SQL catalog tracks
the metadata pointer. `quality:iceberg` runs the proof suite against MinIO and is
wired into the `ci_gate.sh` runtime lane after the S3 ratchet.

Reserved Iceberg ratchet tests (must stay green):

```text
test_iceberg_adapter_normal_path_commits_loads_and_isolates_snapshots
test_iceberg_duplicate_version_commit_is_rejected
test_iceberg_duplicate_guard_transient_catalog_error_is_retryable
test_iceberg_engine_and_s3_warehouse_end_to_end
test_iceberg_snapshot_committed_db_failure_cleans_up_orphan_snapshot
test_iceberg_missing_table_on_read_marks_storage_corruption
test_iceberg_corrupted_data_file_surfaces_through_engine_as_corruption
test_iceberg_manifest_hash_is_real_object_bytes_and_catches_same_size_tamper
test_iceberg_catalog_failure_is_visible_in_operations
test_iceberg_corrupt_table_metadata_is_corruption_not_retryable
test_iceberg_retry_after_ambiguous_commit_does_not_duplicate_version
test_iceberg_compatible_schema_evolution_across_versions
test_iceberg_pinned_snapshot_survives_out_of_band_pointer_change
test_iceberg_repeated_commits_keep_every_version_independently_readable
```

Concurrency note: the dataset version allocator (`SELECT ... FOR UPDATE`)
serializes commits to one dataset, so concurrent same-table Iceberg commits do
not occur in the engine; Iceberg optimistic concurrency is the backstop and the
duplicate-version guard rejects reuse of a committed version id. The guard is
fail-closed: transient catalog lookup failures are retryable `AdapterError`s, not
"not found" answers. Iceberg data-file integrity uses a Foundry sidecar manifest
stored beside the table data; read paths compare the current object bytes against
the commit-time hash so same-size/same-row-count parquet replacement is
corruption, not a silent update.

### Active Ratchet: Spark (covered)

`SparkComputeAdapter` implements the same `ComputeAdapter` port (profile `spark`),
selected independently of storage by `FOUNDRY_LITE_COMPUTE_PROFILE=spark` so a
Spark runner can transform datasets backed by any storage profile. The engine
materializes each pinned dataset version through the storage adapter to a local
parquet path before calling compute, so Spark stays unaware of S3/Iceberg
internals (the exit criteria hold structurally). Spark runs CSV ingest and SQL
transforms; its directory output is coalesced to one part file and promoted to
the single-file target the dataset transaction protocol expects. Local parquet
reads (preview/inspect/check) reuse the shared reader. `quality:spark` runs the
proof suite and is wired into the `ci_gate.sh` runtime lane after Iceberg.

Reserved Spark ratchet tests (must stay green):

```text
test_spark_compute_adapter_contract_parity
test_spark_transform_substitutes_inputs_and_writes_single_parquet_file
test_spark_and_duckdb_produce_equivalent_transform_output
test_spark_unsupported_plan_kind_is_validation_error
test_spark_invalid_csv_input_is_validation_error
test_spark_rows_to_parquet_preserves_quoted_json_strings
test_spark_engine_transform_commits_with_lineage_and_health
test_spark_transform_failure_aborts_output_transaction
test_spark_concurrent_transforms_use_isolated_temp_views
```

Scope note: this ratchet proves the Spark adapter contract, SQL-semantics parity
with DuckDB, single-file output promotion, engine-level transform commit with
lineage/health, and failure → output-transaction abort with FAILED-run evidence,
plus same-session concurrent transform temp-view isolation. Genuinely distributed failure modes —
speculative-execution double-write (T1-010), driver-success-but-executor-output-
missing (C9), timeout-then-cluster-cancel (C10), and shuffle/dynamic-allocation
failures (C11) — are not reproducible in local mode and require a real Spark
cluster; they are deferred (documented, not silently skipped), analogous to the
real-AWS deferral on the S3 ratchet.

### Active Composition Ratchet: S3 + Iceberg + Spark (covered)

The first cross-infrastructure composition gate proves that the active storage,
table, and compute ratchets work together in one engine process:

```text
FOUNDRY_LITE_ADAPTER_PROFILE=iceberg
FOUNDRY_LITE_COMPUTE_PROFILE=spark
MinIO/S3 warehouse
```

The engine uploads raw CSV data through Spark CSV ingest, commits the raw dataset
as an Iceberg snapshot whose data and metadata live in MinIO/S3, materializes
that pinned Iceberg version back to local parquet for Spark, executes the SQL
transform, and commits the output dataset back through Iceberg-on-S3. The same
composition has a failure proof: a Spark transform failure aborts the output
transaction and leaves no committed output version.

This composition gate also locks the already-shipped Debezium CDC flow against
the active S3/Iceberg/Spark stack. A Debezium-normalized CDC stream is archived
as an Iceberg-on-S3 raw changelog dataset through Spark row writing, replayed
from the committed dataset rows into CDC object indexing, and materialized into
`ops.order_current`. The regression intentionally includes a late stale update
after a delete tombstone so the object cannot resurrect, and a writer failure
proof so an archive batch that was read but not committed leaves no raw CDC
dataset version.

Current CI binding:

```text
pnpm --silent quality:infra-composition
```

Reserved composition ratchet tests (must stay green):

```text
test_iceberg_s3_storage_with_spark_compute_end_to_end
test_iceberg_s3_spark_failure_aborts_without_output_version
test_debezium_cdc_iceberg_s3_spark_archives_indexes_and_materializes_end_to_end
test_debezium_cdc_iceberg_s3_spark_archive_failure_aborts_without_dataset_version
```

### Temporal WorkflowAdapter scope note

This ratchet proves idempotent start-and-wait (workflow id is a
tenant/workflow/idempotency-key namespace, so cross-tenant same-key starts do
not collide; re-start/concurrent-start re-attaches with no duplicate run), activity retry,
execution-timeout → retryable timeout, service-unavailable → typed retryable
payload/AdapterError, business failure and cancellation → durable classified run
error payload, on Temporal's time-skipping test server plus fast fake-client
transport-failure tests. Distributed worker crash mid-activity, signals/queries,
continue-as-new, and real Temporal cluster failover are not reproducible on a
single time-skipping worker and are deferred (documented, not silently skipped).
Temporal is an orthogonal Scale-Foundation boundary the engine does not yet
drive, so it is a standalone family, not part of the S3+Iceberg+Spark composition
stack.

### Managed Elasticsearch deployment scope note

This ratchet proves that a live cluster outage surfaces as the typed AdapterError
the failure contract promises (timeout/unavailable/rate_limited/validation/conflict
classified from the real `elastic_transport`/`elasticsearch` exception taxonomy),
that the version guard keeps the highest version under real concurrent writers,
that an already-exists create and a stale-version upsert are idempotent, that one
document's failure does not lose the others, that the index is a rebuildable
projection after loss (search is never serving truth), and that failed search
projection rebuild/object-change work persists durable operator evidence in
Operations `indexRuns` plus related audit events.

Three complementary test layers: a real testcontainers Elasticsearch cluster
(`test_elasticsearch_live_cluster.py`) proves the round-trip, the real painless
version-guard script, concurrent writers, and a real cluster outage → typed
retryable AdapterError;
an in-memory client (`test_elasticsearch_deployment_ratchet.py`) models the
version-guarded update contract and raises the real `elastic_transport`/
`elasticsearch` exception types to cover the full classification matrix
(timeout/unavailable/5xx/429/4xx/409/unknown) deterministically and fast; the
application-level search indexing tests (`test_search_indexing.py`) prove
Elasticsearch adapter failures are visible later through failed `indexRuns`
instead of only as adapter exceptions or log lines.

vz/virtiofs note: Elasticsearch's refresh fsync on Colima's virtiofs disk is slow
enough to exceed client timeouts, which made naive live round-trips hang for
minutes locally (the request completes server-side, only the response is late).
Mounting the ES data directory on tmpfs (memory) keeps index/refresh I/O fast, so
the live round-trip is reliable both locally and on CI — no port-forward/keep-alive
workaround is needed. Elasticsearch is an orthogonal rebuildable projection, not a
storage/compute commit point, so it is a standalone family, not part of the
S3+Iceberg+Spark composition stack.

## Runtime Evidence Gates And Lanes

CI is an automated reviewer, not just a test runner: it explains _which_ contract
failed and _what to inspect_. Three contract gates read the single matrix
(`docs/infra-tricky-matrix.json`) and emit actionable, root-cause-style output
plus JSON + Markdown artifacts (and a GitHub step summary):

| Gate               | Command                           | Enforces                                                                                                                                                                                     |
| ------------------ | --------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Proof matrix       | `pnpm quality:proof-matrix`       | every active infra maps all proof classes to tests that exist; focused gate is in `package.json` and wired into `ci_gate.sh`; matrix agrees with `docs/infra-ratchet.md` and `package.json`. |
| Source of truth    | `pnpm quality:source-of-truth`    | every serving-truth rule is enforced (named, existing test) or explicitly deferred (reason, risk tier, future test, owning doc).                                                             |
| Operator evidence  | `pnpm quality:operator-evidence`  | every active infra declares durable payload paths, a run surface, and `testAssertions` mapping each path to collected operator-evidence tests — logs-only evidence is not enough.            |
| Root-cause summary | `pnpm quality:runtime-root-cause` | aggregates the above plus the failed runtime lane step marker into one step summary with suggested files.                                                                                    |

**Source-of-truth rules** (the DB COMMITTED version is serving truth; object/snapshot
existence and search/UI/SDK caches are not; runs pin versions and do not re-resolve
latest; external timeouts are `outcome_unknown`) live in `sourceOfTruthRules` in the
matrix — never only in prose. A rule that cannot be enforced yet must carry an
explicit `deferral` block, so deferral is visible, not silent.

**Operator-evidence rule:** a dangerous runtime failure must be visible in durable
evidence (run/audit/transaction/error/outbox/trace payloads), never only in a log
line, a stack trace, or a generic FAILED status. Each active infra's
`operatorEvidence.requiredPayloadPaths` are asserted at runtime by named
integration tests (injecting the failure). `operatorEvidence.testAssertions`
maps each required path to the proving test, and the gate rejects paths without a
mapping, mappings to tests outside the operator-evidence proof class, or tests
that pytest cannot collect.

**Runtime lane vs release lane:**

- `pnpm ci:gate:runtime` (PR feedback): proof-matrix / source-of-truth /
  operator-evidence contracts run first; then focused infra ratchets +
  composition ratchet + runtime diagnostics. A failure trap always writes
  `artifacts/quality/runtime_lane_failure.json` and refreshes
  `artifacts/quality/runtime_root_cause_summary.md`.
- `pnpm ci:gate:release` (push to main, tags, manual dispatch via `release.yml`): the
  full runtime lane plus heavier 100k/1m performance smokes and the contract gates,
  with evidence artifacts uploaded.

**Debugging a failed gate:** read the step summary, then
`artifacts/quality/<gate>.json` / `<gate>_summary.md`
(`proof_matrix`, `source_of_truth_contract`, `operator_evidence`) and
`artifacts/quality/runtime_root_cause_summary.md`. Each finding names the likely
root cause and the files to inspect.

## S46+ Expansion Handoff

[Data Platform Expansion Sprint Plan](./data-platform-expansion-sprint-plan-ko.md) extends
this same ratchet discipline beyond vendor infrastructure. Semantic SSOT,
record-level DLQ, late-data watermarks, continuous CDC workers, Temporal product
workflow integration, external saga/reconciliation, data quality contracts,
schema migration, privacy lifecycle, backup/restore, and product UI work must
all follow the same rule:

```text
new capability self proof
+ composition proof against already-active infra/platform families
+ source-of-truth proof
+ operator-evidence proof
+ docs/matrix/checklist/evidence-ledger sync
```

In other words, adding S47 Record DLQ after S46 does not only prove "DLQ works."
It must prove DLQ with the active S3/Iceberg/Spark/CDC/search/Temporal adapter
surface where relevant. S51 continuous CDC must retest CDC plus the active object
indexing, Iceberg-on-S3 archive, Spark materialization, and operator evidence
contracts. S52 Temporal product workflow integration must retest the workflow
against active data writes, idempotency, retries, and compensation evidence.
No roadmap PR may graduate by testing only its own happy path.

## Pull Request Exit Checklist

Every infrastructure ratchet PR must include:

```text
[ ] exactly one new infrastructure family is active in this PR
[ ] adapter contract test passes for local/fake/new profile
[ ] normal path is covered through public facade/API/CLI where applicable
[ ] failure injection covers the most dangerous commit point
[ ] concurrency/race probe exists or the PR explains why no shared mutable state was introduced
[ ] retry/idempotency proof exists
[ ] partial-success cleanup proof exists
[ ] composition test exists against every already-active infrastructure family
[ ] operator evidence is asserted in run/audit/transaction/error payloads
[ ] docs/infra-ratchet.md is updated if active/next order changed
[ ] docs/commit-point-risk-register.md status/evidence is updated
[ ] docs/foundry_lite_tricky_failure_modes_checklist.md test names are updated and pass quality:checklist-evidence
[ ] docs/infra-tricky-matrix.json pulls the related tricky items into proof classes, pytest tests, and CI commands
[ ] docs/implementation-status.md current/future wording is updated
[ ] CI script/package script includes the new focused quality gate when the proof becomes reusable
```

## What Does Not Count

- A docker-compose service that starts but is not used through an application
  port.
- A live-infra smoke test without failure injection.
- A fake-only test claiming production readiness.
- Cleanup that is described in a runbook but not tested.
- A projection or external system becoming the source of truth without a
  replay path.
- A broad "infra integration" PR that makes failures harder to localize.

## Media/Content Plane Ratchet (M0–M9)

The Media/Content Plane (ADR-0001) is added as a separate bounded context, one
ratchet at a time, each adding at most one new external failure domain.

- **M0 — Contract/schema/local (done):** media domain types, ports
  (`media_storage`, `media_repository`, `media_processor`, `content_index`,
  `media_policy`, `content_retrieval`), SQLAlchemy schema/repositories, local
  filesystem adapter, the atomic metadata-pointer transaction protocol, and the
  serving-truth `sourceOfTruthRules` proven by the local core (now `enforced`).
  No new external infra. PR1 = ADR + contracts; PR2 = local MediaSet transaction core.
- **M1 — S3 `MediaStorageAdapter` (adapter shipped):** direct + multipart upload,
  range read, tenant/version-scoped expiring read grant, interrupted-multipart
  cleanup, typed retryable upload failure, concurrent same-path uploads, orphan
  cleanup — all proven on MinIO (`tests/integration/test_s3_media_storage_adapter.py`),
  reachable via `FOUNDRY_LITE_MEDIA_STORAGE_PROFILE=s3-media` and the shared
  `build_s3_client` helper. The `infra-tricky-matrix` `active-covered` family
  promotion (operator-evidence proof class) is deferred until a media Operations/run
  surface exists.
- **M2 — PDF raw-text processor (processor shipped):** local `pypdf` `PdfTextProcessorAdapter`
  (profile `pdf-pypdf`) with encrypted/corrupt-PDF + page-limit validation failures, a
  wall-clock timeout that fails closed, and deterministic per-page text hashes. The
  `media_derivatives` + `content_units` schema, a `MediaProcessingService` that commits the
  derivative + content units + audit + outbox atomically (DB COMMITTED is the only serving
  truth), idempotency on the derivative spec key, inherited security envelopes, and staged-
  orphan sweep all ship and are tested. The Temporal workflow wrapper + `infra-tricky-matrix`
  active-covered family (operator-evidence proof) are deferred with M1 until a media
  Operations/run surface exists; `derivatives_inherit_source_security` is now `enforced`.
- **M3 — Content units + Elasticsearch lexical projection (shipped):** a
  `ContentIndexAdapter` (in-memory local + Elasticsearch, lexical-first) with
  generation-scoped indexes, version-guarded upserts, partial-bulk-failure reporting,
  and shadow-then-switch alias promotion; a `MediaIndexingService` that projects
  committed content units (and rebuilds purely from DB truth); and a
  `DefaultContentRetrievalService` that re-reads authoritative content units to enforce
  citation integrity (text_hash match), tenant ACL (no cross-tenant leakage), and stale-
  source-unit dropping. `content_unit_artifact_is_truth_search_index_is_projection` is
  now `enforced`. Live-Elasticsearch round-trip + active-covered family (operator-evidence)
  are deferred with M1/M2 until a media Operations surface exists; dense/hybrid is M8.
- **M4 — Ontology `MediaReference` + Action-bound upload (shipped):** the ontology gains a
  `media_reference` object-property type (validated for an edit-layer `namespace.name` media-set
  binding, doc §1.6). A `MediaReferenceBindingService` binds an immutable media version onto an
  object property as one atomic unit of work — only a COMMITTED version binds, the binding row +
  audit + outbox commit together, and a rejected bind writes nothing (the staged blob stays a
  sweepable orphan). The bind is idempotent; a reused key with a different version conflicts; a
  re-bind re-points the property while each prior reference stays pinned to its version; reads
  mask the reference when the caller's clearance does not cover its classification (§12.1, no
  ACL leakage). Tighter ActionService/object-record coupling + active-covered family are deferred
  with M1–M3 (operator-evidence needs a media Operations surface).
- **M5 — OCR (processor shipped):** one family — an `OcrProcessorAdapter` (profile
  `ocr-tesseract`) with an **injectable OCR engine** (the default lazily imports the real
  engine but is never invoked in CI; tests inject a fake), so no system OCR binary is
  required to test. OCR is a distinct `ocr_v1` derivative family (it never overwrites
  embedded `pdf_text`), pins the OCR model version (a model upgrade re-processes rather
  than silently reusing), inherits the source security envelope, and fails closed on an
  undecodable image (validation) or a wall-clock timeout. Live OCR engine + active-covered
  family are deferred with M1–M4.
- **M6 — image/video (processors shipped):** two families plug into the same
  `MediaProcessingService`. An `ImageProcessorAdapter` (profile `image-pillow`) uses **real
  Pillow** (a pure-Python wheel — no system binary, so the real engine runs in CI): it reads
  image metadata (format/mode/dimensions) and a deterministic thumbnail spec into a
  `thumbnail` derivative. A pixel-count cap is a decompression-bomb resource guard
  (M-T3-001 → typed validation), an undecodable image is a typed validation failure, and a
  hung decode fails closed on a wall-clock timeout (M-T3-002, `shutdown(wait=False)`). A
  `VideoProbeProcessorAdapter` (profile `ffprobe`) probes container/stream metadata
  (duration/codec/resolution) into a `video_probe` derivative via an **injectable probe
  runner** — the default reports `probe_engine_unavailable` (a real profile injects a
  subprocess runner that SIGTERMs its process group on timeout), so no ffprobe binary is
  required to test. Each failure records FAILED durable evidence and commits no derivative —
  nothing is ever partially visible. Real transcode/HLS/waveform/scene-frames + active-covered
  family are deferred with M1–M5 (operator-evidence needs a media Operations surface).
- **M7 — ASR (processor shipped):** one family — an `AsrProcessorAdapter` (profile
  `asr-whisper`) with an **injectable speech engine** (the default raises
  `asr_engine_unavailable`; a real profile injects Whisper/faster-whisper — live ASR is
  deferred like live-OCR, so no speech model is required to test). Transcription is a distinct
  `asr_v1` derivative family (it never overwrites `pdf_text`/`ocr_v1`) and pins the model
  version (a model upgrade re-processes rather than silently reusing). Each transcript segment
  becomes an `audio_segment` content unit carrying its time code (`start_ms`/`end_ms`) and
  optional speaker/language (`ProcessedContentUnit` extended additively; page-based processors
  leave these `None`), inherits the source security envelope, and fails closed on undecodable
  audio (validation) or a wall-clock timeout. Live ASR engine + active-covered family are
  deferred with M1–M6.
- **M8 — Embedding + hybrid retrieval (shipped):** an `EmbeddingModelAdapter` port +
  `LocalEmbeddingAdapter` with an **injectable embedding engine** (the default raises
  `embedding_model_unavailable`; a real profile injects sentence-transformers / a managed
  provider — live embeddings deferred like live-OCR, so no heavy ML dependency enters CI).
  An embedding is a derived **projection** of a content unit's text, not a separate truth:
  vectors ride the existing content index (no new schema/table), and `MediaIndexingService`
  attaches them at projection time, pinning the embedding model version + chunk spec onto each
  indexed unit. Retrieval embeds the query and fuses lexical + cosine-kNN ranks with Reciprocal
  Rank Fusion; a query whose embedding model version does not match the generation's **fails
  closed** (no silent mixing of vector spaces — `embedding_artifact_pins_model_version_and_chunk_spec`
  now `enforced`). Elasticsearch reuses its `dense_vector` + `script_score` (no separate vector
  DB); the local adapter does pure-Python cosine + RRF. Live embeddings + active-covered family
  deferred with M1–M7.
- **M9 — Access patterns / virtual media sets (shipped):** two halves on the media plane.
  (a) An on-demand **preview cache** — a `MediaAccessPatternService` renders a thumbnail/preview
  via an injectable `MediaPreviewRendererAdapter` (image thumbnails use real Pillow, so the
  render path runs in CI; audio/video patterns report `preview_renderer_unavailable` because
  FFmpeg is a deferred system binary). The cache (`media_access_caches`) is **never serving
  truth**: every preview verifies the COMMITTED source first, serves a cache hit only when it is
  unexpired AND its pinned `source_content_hash` still matches AND its blob is present, and
  otherwise re-renders from the source; references/retrieval read the source, never the cache; an
  unrenderable source fails closed (`access_pattern_cache_is_not_truth` promoted to `enforced`).
  (b) **Virtual media sets** — a `media_sets.is_virtual` set whose external versions reuse
  `media_item_versions.source_ref`. A `VirtualMediaSetService` rejects an external version that
  neither pins an etag/version nor is marked `is_mutable_external_reference`; an etag-pinned
  version is re-validated on resolve through an injectable `ExternalMediaReader` (a drift fails
  closed), while a mutable reference surfaces freshness rather than failing
  (`external_virtual_object_requires_version_or_mutable_marker` promoted to `enforced`). The real
  preview engine + external connector are deferred (no new env vars). At M9 `workflow_status_does_not_
replace_domain_commit` (now closed by L5), retention purge, and external-writeback compensation were
  deferred because their proof surfaces (a media Temporal workflow / purge engine / real connector) did
  not exist yet; retention purge is now closed by L7, and external-writeback compensation remains deferred.

The **L-series** closes those deferrals — turning the seams into real engines and the
missing proof surfaces into evidence, so the media families can finally be promoted to
`active-covered`:

- **L0 — Media Operations / run surface (shipped):** a `media_processing_runs` table +
  repository (on the existing `MediaDerivativeRepository`) record every processing attempt as
  RUNNING → SUCCEEDED/FAILED, with the typed `failure_kind`/`failure_reason` as durable
  **operator evidence**; `MediaProcessingService` opens the run, then marks it SUCCEEDED (with the
  committed derivative id) or FAILED **in the same transaction as the derivative/audit outcome**,
  and exposes `list_media_runs` / `media_run_detail` on the `MediaWorkspace` facade so a failed
  attempt is visible to an operator. The run row is operations evidence, **never serving truth** —
  a FAILED/RUNNING run never makes an uncommitted derivative resolvable, and run status never
  substitutes for the COMMITTED derivative (this is the surface the deferred
  `workflow_status_does_not_replace_domain_commit` rule will be proven against once the media
  Temporal workflow lands at L5). This is the foundation for the `operator-evidence` proof class
  that active-covered promotion requires.

- **L1 — Real Tesseract OCR (shipped):** the `OcrProcessorAdapter` injectable engine seam now
  has a real implementation — `_tesseract_ocr_engine` lazily imports `pytesseract` + Pillow,
  opens the image, and runs `image_to_string`, so the `ocr-tesseract` profile in
  `local_runtime` finally OCRs for real (the in-process default still raises
  `ocr_engine_unavailable` so the deterministic fake-injected unit tests stay covered). A
  PIL/Tesseract decode error becomes a typed `undecodable_image` **validation** failure.
  `tests/integration/test_media_ocr_live.py` proves both shapes against a system Tesseract
  binary (installed in the `quality_coverage`/`quality_flaky`/`quality_runtime` CI jobs and
  invoked by `pnpm quality:media-live-ocr`): the **normal path** (a rendered "HELLO FOUNDRY"
  PNG → `ocr_v1` derivative + content units commit with the recognized text, a SUCCEEDED
  `media_processing_runs` row, then projection + search returns the doc) and **operator
  evidence** (an undecodable image records a FAILED run with `failure_kind == validation`
  visible through `list_media_runs`/`media_run_detail`, with no derivative committed). No
  family promotion happens at L1 — `infra-tricky-matrix.json` is untouched; promotion is the
  L9 capstone.
- **L2 — Real faster-whisper ASR (shipped):** the `AsrProcessorAdapter` injectable engine
  seam now has a real implementation — `_faster_whisper_asr_engine` lazily imports
  `faster_whisper`, builds a `WhisperModel("tiny", device="cpu", compute_type="int8")` once
  (a module-level cache via `_load_whisper_model`, so repeated calls don't reload the ~75MB
  weights), runs `transcribe(path, language="en")` (the language is pinned because auto-detect
  misclassifies short clips), and maps each segment to a time-coded `TranscriptSegment`
  (`start_ms`/`end_ms` rounded from seconds), so the `asr-whisper` profile in `local_runtime`
  finally transcribes for real (the in-process default still raises `asr_engine_unavailable`
  so the deterministic fake-injected unit tests stay covered). faster-whisper uses the
  ctranslate2 backend (no torch — a pure-wheel install, no apt package). A decode/load error
  becomes a typed `undecodable_audio` **validation** failure. `tests/integration/test_media_asr_live.py`
  proves both shapes against the real Whisper `tiny` model (fetched/cached in the
  `quality_coverage`/`quality_flaky`/`quality_runtime` CI jobs via an `actions/cache` keyed on
  `~/.cache/huggingface` and invoked by `pnpm quality:media-live-asr`): the **normal path**
  (a committed audio clip → `asr_v1` derivative + ordered time-coded `audio_segment` content
  units commit with the transcribed text, a SUCCEEDED `media_processing_runs` row, then
  projection + search returns the doc) and **operator evidence** (undecodable audio records a
  FAILED run with `failure_kind == validation` visible through `list_media_runs`/`media_run_detail`,
  with no derivative committed). No family promotion happens at L2 — `infra-tricky-matrix.json`
  is untouched; promotion is the L9 capstone.

- **L3 — Real FFmpeg/ffprobe video (shipped):** the `VideoProbeProcessorAdapter` injectable
  probe-runner seam now has a real implementation — `_ffprobe_video_probe_runner` runs system
  `ffprobe -v quiet -print_format json -show_format -show_streams <path>` via `subprocess.Popen`
  with a **fixed arg list and no shell** and a wall-clock timeout; on timeout it SIGTERMs the
  whole process group (`start_new_session=True` + `os.killpg(os.getpgid(pid), SIGTERM)`, the
  documented M-T3-002 process-group-kill, so a hung ffprobe never leaks). It parses the JSON into
  the existing `VideoProbe` (duration/container/video codec/width/height; a `has_audio` flag is
  added so the probe records whether an audio track exists), so the `ffprobe` profile in
  `local_runtime` finally probes for real (the in-process default still raises
  `probe_engine_unavailable`, keeping the deterministic fake-injected unit tests covered). A
  missing/corrupt file or non-zero ffprobe exit becomes a typed `unprobeable_media` **validation**
  failure. `tests/integration/test_media_video_live.py` (`pnpm quality:media-live-video`, system
  `ffmpeg` installed in the `quality_coverage`/`quality_flaky`/`quality_runtime` CI jobs) proves
  three shapes against the real fixture: **real probe metadata** (a committed mp4 → `video_probe`
  derivative carrying real duration ≈ 2.737s / `h264` / 320×240 / audio-present, a SUCCEEDED
  `media_processing_runs` row), the **video → subtitles → searchable downstream flow** (the SAME
  mp4 transcribed by the real `asr-whisper` engine — faster-whisper/PyAV decodes the video's audio
  track directly — into `audio_segment` units containing "quick brown fox", then projected and
  found by searching "fox"), and **operator evidence** (a corrupt video records a FAILED run with
  `failure_kind == validation` visible through `list_media_runs`/`media_run_detail`, with no
  derivative committed). No family promotion happens at L3 — `infra-tricky-matrix.json` is
  untouched; promotion is the L9 capstone.
- **L4 — Real embeddings + semantic/hybrid search (shipped):** the M8 `LocalEmbeddingAdapter`
  injectable embedding-engine seam now has a real implementation — `_fastembed_embedding_engine`
  builds a fastembed `TextEmbedding("BAAI/bge-small-en-v1.5")` ONNX model once (module-level
  cache, onnxruntime — **no torch**) and returns one 384-dim `EmbeddingVector` per text. fastembed
  downloads the model to `~/.cache/huggingface` on first use (cached/pre-fetched in CI like the
  Whisper model), so the in-process default still raises `embedding_model_unavailable`, keeping
  the M8 deterministic fake-injected unit tests covered. `local_runtime` now wires
  `LocalEmbeddingAdapter(embedding_engine=_fastembed_embedding_engine, model_version="bge-small-en-v1.5")`
  so the composed runtime does REAL dense + hybrid retrieval (`is_available` True), with the model
  version pinned into each index generation (a model upgrade re-projects rather than silently
  mixing vector spaces — the same `nearestNeighbors(...)`-with-pinned-model contract Palantir
  Foundry "Text → Embeddings" uses, reusing the Elasticsearch vector capability).
  `tests/integration/test_media_embeddings_live.py` (`pnpm quality:media-live-embeddings`, model
  pre-fetched in the `quality_coverage`/`quality_flaky`/`quality_runtime` CI jobs) proves three
  shapes with the real model: **real semantic ranking** (three committed docs — dog/car/plant —
  projected via the real `MediaIndexingService`; the query "puppy and canine pets" — which shares
  NO lexical token with "A golden retriever is a friendly dog breed." — ranks the dog doc FIRST
  through `DefaultContentRetrievalService`, so only the dense path can drive it; cosine ≈ 0.718 dog
  vs ≈ 0.43/0.49 car/plant), **model-version pinning fail-closed** (a real query vector carrying a
  different `embedding_model_version` raises a typed `conflict`, no silent vector-space mix), and
  **live Elasticsearch dense_vector** (the real-embedding content units are indexed into a LIVE
  Elasticsearch `dense_vector` index via testcontainers and the same semantic query ranks the dog
  doc first through `ElasticsearchContentIndexAdapter`'s real `script_score` cosine similarity). No
  family promotion happens at L4 — `infra-tricky-matrix.json` is untouched; promotion is the L9
  capstone.
- **L5 — Media Temporal workflow + the workflow-status invariant (shipped):** the first
  product-driven Temporal use case. A `MediaProcessingWorkflow` (`@workflow.defn`, registered
  alongside `ConnectorSyncWorkflow`/`FoundryWorkflow` on the worker + sandbox runner) runs a single
  `run_media_processing_step` activity that drives a real `MediaProcessingService.process`;
  `WorkflowOrchestrationService.start_media_processing_workflow` (surfaced on `foundry.operations`)
  mirrors `start_connector_sync_workflow` — validate permission, fingerprint the request, idempotent-
  insert a `workflow_runs` ledger row (replay/concurrent-start returns the same run), start/await via
  the `workflow_adapter`, audit through `runtime_service`. The invariant `workflow_status_does_not_
replace_domain_commit` is now `enforced`: the Temporal `workflow_runs` status and the L0
  `media_processing_runs` status are orchestration/operations evidence and do NOT gate
  `resolve_derivative` (which returns only a COMMITTED derivative). This mirrors Palantir Foundry
  Builds, where a Build/Job-Status check "succeeds if the target dataset successfully builds" — the
  committed dataset/transaction is the success signal and the build/schedule status merely orchestrates
  it. Proven by `test_media_workflow_status_does_not_replace_domain_commit` (deterministic: a workflow
  run marked succeeded never makes a failed-processing derivative resolvable; a committed derivative is
  still served when the workflow status is failed/unknown — the DB commit wins) and the time-skipping
  `test_media_processing_workflow_runs_through_temporal_and_commits` (`pnpm quality:media-workflow-temporal`,
  wired into the `ci_gate.sh` runtime lane). No media _family_ promotion happens at L5 — that is the L9
  capstone; only the deferred source-of-truth rule is promoted.

- **L6 — Real S3 external connector for virtual media sets (shipped):** M9 shipped the virtual
  media set contract + the `external_virtual_object_requires_version_or_mutable_marker` invariant
  with the connector deferred (the default `LocalExternalMediaReader` reports
  `external_reader_unavailable`). L6 ships the first **real** connector: `S3ExternalMediaReader`
  (profile `s3-external`, selected via the existing adapter profile, reusing the existing
  `FOUNDRY_LITE_S3_*` connection env — no new env var) HEADs a real `s3://<bucket>/<key>` object and
  returns its current `ETag` (quotes stripped) + `ContentLength` as an `ExternalObjectStat` **without
  copying the bytes into Foundry** — a virtual media set stays a pointer to the external source, exactly
  like a Palantir virtual table/media set. A missing object reports `is_present=False` (so a mutable
  reference can surface `is_stale`); a real connection/credentials error fails closed as a typed
  `ExternalReadError`. The default profile still raises (`local-external`), keeping the connector seam
  injectable. Because a virtual set "is not aware of source updates/deletions", `resolve_external_version`
  re-validates the pinned ETag on every read and a drift fails closed
  (`virtual_external_version_etag_drifted`); a mutable reference surfaces freshness instead. Proven live
  against MinIO by `tests/integration/test_media_external_connector_live.py`
  (`pnpm quality:media-live-external`, wired into the `ci_gate.sh` runtime lane after embeddings): a real
  object is HEAD'd + its ETag pinned (resolve returns the matching ETag, no byte copy), an overwrite
  (new ETag) under a pinned version fails closed, a mutable reference resolves fresh while present and
  surfaces `is_stale` after delete, and an unversioned register is rejected. This strengthens the already-
  `enforced` `external_virtual_object_requires_version_or_mutable_marker` rule with live-path tests (its
  status is unchanged). The L8 external-writeback rules (`external_timeout_is_outcome_unknown_not_failure`
  - compensation) are now closed by L8 below.

- **L7 — Media retention / legal-hold purge engine (shipped):** M0–M9 deferred
  `retention_never_purges_reachable_or_legal_hold_version` because only logical delete existed —
  there was no purge engine to prove it against. L7 builds that engine, mirroring Palantir Foundry
  retention (**mark → grace → sweep**, an irreversible physical delete) plus the two protections
  that make a sweep fail-safe. Two columns land on `media_item_versions` (migration `b3d5f7a9c1e2`):
  `retention_marked_at` (set when a policy marks a version as a sweep candidate) and `legal_hold`
  (positive boolean, default false). A `MediaRetentionService` (composed into `MediaServices` +
  the `MediaWorkspace` facade) exposes `mark_media_versions_for_retention` (audited),
  `place_legal_hold`/`release_legal_hold` (audited), and the sweep
  `purge_marked_media_versions(now, grace)`. The sweep fetches versions whose
  `retention_marked_at <= now - grace` and, for each, **NEVER purges** a version under `legal_hold`
  OR still reachable by a `media_reference_bindings` row targeting it (the reachability source) —
  exactly like Foundry refusing to delete a still-referenced view. An eligible version is purged
  physically in one transaction: its `media_derivatives` + `content_units` + `media_access_caches`
  rows are hard-deleted, the blob is removed via the storage adapter, the `media_item_versions` row
  is deleted, and the purge is audited; the engine returns a summary (purged + skipped-reachable +
  skipped-held). The clock is injected (`now`/`grace`) — never read inside — so the sweep is
  deterministic (no `time.sleep`). Fail-safe: any inability to determine reachability rolls the
  transaction back, so nothing is purged on doubt. Proven by
  `tests/unit/test_media_retention_purge.py` (`test_media_retention_skips_reachable_or_legal_hold`
  sets up A reachable / B held / C eligible and asserts only C is purged with its whole footprint +
  blob while A/B survive untouched, plus the not-past-grace and audited-purge cases) and the new
  `MediaRepository` retention-contract tests (sqlite + Postgres). This promotes
  `retention_never_purges_reachable_or_legal_hold_version` `deferred → enforced`. (The L8 external-
  writeback rules are closed by L8 below; promoting a media family to `active-covered` remains
  the L9 capstone.)

- **L8 — Real external writeback + compensation for ontology actions (shipped):** The action
  external-writeback surface (S53) already implemented outcome*unknown + compensation semantics, but
  only ever through the `simulate_writeback*_`flags — the two matrix rules`external*timeout_is_outcome_unknown_not_failure`and`external_failure_requires_compensation_not_silent_local_rollback`stayed`deferred`waiting for a
real external system. L8 makes the before-commit writeback **real** against live MinIO and promotes
both rules to`enforced`. A new application port `ExternalWritebackAdapter`
(`write(target, payload) -> WriteReceipt`, `remote_lookup(target) -> RemoteOutcome`) models the three
reach-out shapes (LANDED / AMBIGUOUS / ABSENT); the default `UnavailableExternalWritebackAdapter`
raises (no vendor bundled), so the simulated path stays the only one exercised until a real adapter is
injected (`ActionService.set_external_writeback_adapter`). The first real adapter,
`S3ExternalWritebackAdapter`(profile`s3-external-writeback`, reusing the existing `FOUNDRY_LITE_S3*_`connection through`build*s3_client`+`is_not_found_error`— no new env var, mirroring`S3ExternalMediaReader`), PUTs the writeback payload to `s3://<bucket>/external-writebacks/<idempotency_key>`(so a replay PUTs the same object — idempotent, not a blind new write); a connection/read timeout maps
to an AMBIGUOUS receipt and`remote_lookup`HEADs the same key (present → LANDED, real 404 → ABSENT).
When an action carries a real`external_writeback_uri`, the service routes through the real adapter
additively (the `simulate*\*`flags still work): a real timeout → the existing`outcome_unknown`recording path (NOT failure, idempotent replay); a real write that LANDED followed by a local-mutation
failure → the transaction rolls back and`compensation_required`is recorded in a fresh transaction
(never a silent local-only rollback).`reconcile_action_writeback`now resolves an unresolved writeback
(outcome_unknown **or** compensation_required) either by an operator-provided`remote_status`(kept) or
by a real`remote_lookup`when given an`external_writeback_uri`(HEAD finds the landed object →`succeeded`); the `ACTION_RUN_RECONCILED`/`ACTION_WRITEBACK_RECONCILED`transitions were broadened to
accept`compensation_required`as well as`outcome_unknown`. Proven live against MinIO by
`tests/integration/test_action_external_writeback_live.py`
(`test_action_external_timeout_is_outcome_unknown_not_failed`: a dead-endpoint write times out →
outcome_unknown + idempotent replay; `test_action_external_success_local_failure_requires_real_compensation`:
a real write LANDS in MinIO, the local commit fails → compensation_required, then a real HEAD remote_lookup
resolves it to a committed object edit), the per-port contract
`tests/contracts/test_external_writeback_adapter_contract.py`, and the new
`pnpm quality:action-writeback-live`runtime lane (after`quality:saga-reconciliation`). This promotes
both `external_timeout_is_outcome_unknown_not_failure`and`external_failure_requires_compensation_not_silent_local_rollback` `deferred → enforced`. **Deferred
remainder:** a standing **manual-review queue / dashboard** for unresolved compensations (a reconciliation
driver still requires an operator/automation to call `reconcile_action_writeback`); the writeback path is
  closed, but no autonomous review-queue worker ships in L8.

- **L9 — Media/Content Plane promoted to active-covered + golden end-to-end pipeline (shipped):**
  With every media source-of-truth rule now `enforced` (0 deferred) after L0–L8, L9 promotes the
  media plane to a standalone `active-covered` infra family (Order 6 in the queue above), exactly
  like the orthogonal `elasticsearch`/`temporal` families — it is NOT added to `activeStack` and
  joins NO composition proof (the storage/compute commit-point composition is S3+Iceberg+Spark).
  A `media-plane` family entry lands in `infra-tricky-matrix.json` `families[]` mapping all nine
  proof classes (adapter-contract / normal-path / failure-injection / concurrency-race /
  retry-idempotency / partial-success / recovery-cleanup / operator-evidence / docs-sync) to real
  collectable media tests, pulls checked tricky items M1–M9 (checklist section C-MEDIA), declares
  the eleven enforced media `sourceOfTruthRules`, and declares an `operatorEvidence` block keyed on
  the `mediaProcessingRuns` run surface (status / failureKind / failureReason / mediaDerivativeId,
  asserted by `test_failure_records_a_failed_run_and_no_committed_derivative_is_visible`). The
  headline proof is a GOLDEN end-to-end LIVE pipeline
  (`tests/integration/test_media_golden_pipeline_live.py`, scenario `media-golden-pipeline`): on the
  real composed runtime + real engines + live MinIO (`s3-media`) + live Elasticsearch
  (testcontainers), a real document image is committed → real Tesseract OCR commits an `ocr_v1`
  derivative + `page` content units + a SUCCEEDED `media_processing_runs` row → the immutable media
  version is bound onto an Ontology object via a `media_reference` property (M4) → the content units
  project into a live ES `dense_vector` index → real fastembed hybrid/semantic search returns the doc
  with a verified `text_hash` citation and tenant ACL (cross-tenant search recalls nothing) → a real
  Pillow thumbnail preview is served from cache on the second call without re-rendering and never
  treated as truth; a second case threads the audio path (committed WAV → real faster-whisper ASR →
  `audio_segment` units → live ES → searchable). The same `media_item_version_id` is asserted as the
  join key across derivative → content_unit → ontology binding → ES index → search hit, proving raw
  data flows consistently upstream→downstream on the invariant that the DB COMMITTED version is the
  only serving truth. Wired into the `ci_gate.sh` runtime lane via `pnpm quality:media-active-covered`.

- **L10 — Real FFmpeg scene-frame + Tesseract OCR for video (shipped):** today video yields only
  ffprobe metadata (L3) + an audio-track transcript (L2); L10 adds the VISUAL text — on-screen text
  in a video becomes searchable content. A `VideoSceneFrameProcessorAdapter` (profile
  `video-scene-frames`, processor `video_frames_v1`, derivative kind `video_scene_frames`, housed in
  `video_probe_processor.py` to stay within the adapters-barrel fan-out budget) extracts **scene
  frames** (Foundry's video→media `extract_scene_frames` primitive: a media→media transform producing
  a derived image set, each frame pinned to the immutable source version + carrying a timestamp /
  sceneScore — palantir.com/docs/foundry/transforms-python/media-set-transforms-api,
  /pipeline-builder/transforms-transform-media) and OCRs each frame (`imageOcrV1`, media→tabular —
  /pb-functions-expression/imageOcrV1). The frame extraction is an injectable seam
  (`scene_frame_extractor: (str) -> list[(start_ms, ocr_text)]`) whose default raises
  `frame_extractor_unavailable` (mirroring the L1–L3 default-raise so unit tests inject a fake and
  need no binary). The real `_ffmpeg_scene_frame_extractor` runs system `ffmpeg -hide_banner -i <video>
-vf "select='eq(n,0)+gt(scene,T)',showinfo" -vsync vfr f_%03d.png` into a `tempfile.TemporaryDirectory`
  (fixed arg list, no shell, `start_new_session=True` + `os.killpg(...SIGTERM)` on timeout, the
  M-T3-002 process-group-kill), parses the per-frame `pts_time:<seconds>` timecodes from the `showinfo`
  filter's STDERR (so the info log level is kept — never `-loglevel error`), OCRs each PNG by reusing
  the existing `_tesseract_ocr_engine`, and returns `(round(pts*1000), text)` for frames with
  recognized text. `scene_sensitivity` (MORE_SENSITIVE≈0.1 / STANDARD≈0.3 / LESS_SENSITIVE≈0.5) maps to
  the ffmpeg scene threshold. Each scene frame is a time-coded `video_frame` content unit
  (`start_ms`, `text`, sha256 `text_hash`); a corrupt/undecodable video is a typed
  `unprocessable_video` validation failure and a hung extraction fails closed as a typed timeout, both
  recording FAILED durable evidence with no derivative. The derived frames live off the immutable
  source version (never the source) and inherit the source security envelope.
  `tests/integration/test_media_video_frame_ocr_live.py` (scenario `media-video-frame-ocr`, `pnpm
quality:media-live-video-frames`, system `ffmpeg` + `tesseract-ocr` installed in CI) proves the
  normal video→frame→OCR→`video_scene_frames`→content-index→search path on a committed mp4 whose three
  on-screen cards ("INVOICE 2026" / "TOTAL 4242 USD" / "NET 30 DAYS") are extracted at ordered
  timecodes and found by searching "invoice"/"4242", plus the operator-evidence path (a corrupt video
  records a FAILED `media_processing_runs` row with `failure_kind == validation` and commits no
  derivative). No new source-of-truth rule (scene-frame OCR is covered by the already-enforced
  `derivatives_inherit_source_security` + `content_unit_artifact_is_truth_search_index_is_projection`);
  the `media-plane` family stays `active-covered`, strengthened with the new live test in its
  normal-path + operator-evidence proofs and testPaths.

- **L11 — Real CLIP scene-frame VISUAL search for video (shipped):** L10 makes on-screen _text_
  searchable; L11 adds VISUAL understanding — "is there a car in this video?" answered zero-shot,
  open-vocabulary, with no object labels. This mirrors Foundry's documented embedding/semantic-search
  vision mode exactly: video → `extract_scene_frames`
  ([transforms-python/media-set-transforms-api](https://www.palantir.com/docs/foundry/transforms-python/media-set-transforms-api/))
  → per-frame `imageToEmbeddingsV1` ("Image to embeddings",
  [pb-functions-expression/imageToEmbeddingsV1](https://www.palantir.com/docs/foundry/pb-functions-expression/imageToEmbeddingsV1/))
  producing a Vector property → semantic search via
  `nearestNeighbors(o => o.embeddings.near(v,{kValue})).orderByRelevance()`
  ([ontology/using-palantir-provided-models-to-create-a-semantic-search-workflow](https://www.palantir.com/docs/foundry/ontology/using-palantir-provided-models-to-create-a-semantic-search-workflow/)),
  with the index-time and query-time embedding model PINNED to the same contrastive model (cross-modal
  text→image only works inside one model's shared space). Palantir's provided image-embedding model is
  SigLIP2; we use the open-source equivalent **CLIP via fastembed** (ONNX, no torch) — same mechanism. A
  `VideoSceneVisionProcessorAdapter` (profile `FOUNDRY_LITE_MEDIA_PROCESSOR_PROFILE=video-scene-vision`,
  processor `video_vision_v1`, derivative kind `video_scene_vision`, housed in `video_probe_processor.py`
  to stay within the adapters-barrel fan-out budget) reuses the L10 ffmpeg scene-select extraction
  (`_ffmpeg_scene_frame_paths` returns frame image paths + `pts_time` timecodes instead of OCR) and
  computes a CLIP IMAGE embedding per frame via an injected `VisionEmbeddingModelAdapter`
  (`LocalVisionEmbeddingAdapter`: `Qdrant/clip-ViT-B-32-vision` image tower +
  `Qdrant/clip-ViT-B-32-text` text tower, a matched pair sharing ONE 512-dim space; the default engines
  raise `vision_model_unavailable` so deterministic unit tests inject a fake and need no model download).
  Each scene frame is a time-coded `video_frame_visual` content unit carrying its CLIP vector (the vector,
  not text, is the searchable content — persisted on the unit so the visual index is rebuildable from
  committed truth). A `MediaVisualSearchService` projects those vectors AS-IS into a CLIP-pinned index
  generation (`embedding_model_version = "clip-ViT-B-32"`, separate from the bge text generation) and
  answers a natural-language query by embedding it with the CLIP **text** tower and running the existing
  dense kNN — the content index's model-version guard fails closed against a bge-pinned generation (and
  vice-versa), so the two vector spaces never mix; the bge text/hybrid retrieval path is untouched. A
  corrupt/undecodable video is a typed `unprocessable_video` validation failure and a hung extraction
  fails closed as a typed timeout, both recording FAILED evidence with no derivative.
  `tests/integration/test_media_video_vision_live.py` (scenario `media-video-vision`,
  `pnpm quality:media-live-video-vision`, system `ffmpeg` in CI + the CLIP model pre-fetched/cached on
  `~/.cache/huggingface`) proves the normal video→scene-frames→CLIP-image→`video_scene_vision`→visual-kNN
  path on the committed `video_scenes.mp4` (a car / a tree / a dog scene at 0/1/2s): a real CLIP-text
  query "a photo of a car" ranks the CAR frame first and "a photo of a dog" ranks the DOG frame first
  (real cross-modal visual understanding, not labels), plus the operator-evidence FAILED-run path. No new
  source-of-truth rule (covered by the already-enforced `derivatives_inherit_source_security` +
  `content_unit_artifact_is_truth_search_index_is_projection` + `embedding_artifact_pins_model_version_and_chunk_spec`);
  the `media-plane` family stays `active-covered`, strengthened with the new live test in its normal-path
  - operator-evidence proofs and testPaths. This is the embedding/semantic-search vision mode only;
    object-detection (counts/bounding boxes) via a custom CV model or a VLM (`useLlmV3`) is a DIFFERENT
    Foundry mode and a separate future ratchet (a possible L11b).

Media processing is the first product-driven Temporal use case (the L5 media workflow). A media
family becomes `active-covered` only when its proof-class tests collect and it is
registered in `infra-tricky-matrix.json` `families`/`activeStack`; until then it
stays in this queue and as `sourceOfTruthRules` deferrals only. L9 closes this for the
Media/Content Plane (standalone family, not in `activeStack`).

## Object Semantic Search (L12a)

- **L12a — Object semantic search (`Vector` object property, shipped):** the OBJECT-STORE core
  (not the media plane) gains semantic search, mirroring Palantir's `Vector` property on object
  types + `Objects.search().nearestNeighbors(o => o.embeddings.near(v,{kValue:k})).orderByRelevance()`.
  It REUSES the existing media bge embedding engine (`LocalEmbeddingAdapter` /
  `_fastembed_embedding_engine`, `bge-small-en-v1.5`, 384-dim ONNX, no torch) — no new model and no new
  env var — by injecting the SAME composed `embedding_model_adapter` into `ObjectSearchService`. The
  vendor-neutral `SearchDocument`/`SearchQuery` gain optional `embedding`/`embedding_model_version` and
  `query_vector`/`embedding_model_version` (defaulting empty → pure keyword/structured exactly as before;
  default-off when no model is wired). At index time, when the adapter `is_available`, the object's OWN
  `searchable_properties` text (not media) is concatenated and embedded into the projection document with
  the model version pinned; the `LocalSearchAdapter` adds a pure-python cosine `nearestNeighbors` path and
  the `ElasticsearchAdapter` adds the `dense_vector` + `script_score` cosine path mirroring the
  content-index ES adapter. A query whose `embedding_model_version` differs from an indexed object vector's
  FAILS CLOSED (typed `AdapterError` `conflict`) — index-time = query-time model pinning, never silently
  mixing vector spaces. `ObjectQueryService.query_objects(..., semantic_text=...)` embeds the query with
  the same model, runs nearestNeighbors, and returns a relevance-ordered Object Set; ACL/policy/masking
  are enforced exactly as the existing object query (the index re-reads the committed object record —
  object = unit of truth + permission). The object embedding + search index are DERIVED projections; the
  committed object version stays the serving truth and the index rebuilds from committed records. This is
  additive: keyword `search_text` and structured `.filter`/`orderBy`/cursor are unchanged, and a semantic
  query cannot combine with a keyword query in one call. No new source-of-truth rule — the object vector is
  model-pinned the same way as the media embedding, so this REUSES the already-enforced
  `embedding_artifact_pins_model_version_and_chunk_spec` rule (its `tests` now also list
  `test_object_embedding_pins_model_version_when_adapter_available` +
  `test_nearest_neighbors_fails_closed_on_model_version_mismatch`). Deterministic unit tests
  (`tests/unit/test_object_semantic_search.py`) inject a fake bge adapter; a real-bge integration test
  (`test_real_bge_semantic_object_search_ranks_closest_object_first`, added to the `elasticsearch` family's
  normal-path proofs) proves real retrieval — two orders described "a refrigerated truck delivery" vs
  "an invoice dispute" with the query "cold chain logistics" (no lexical overlap) rank the truck order
  first via real fastembed, while keyword search for the same phrase finds nothing.
  STRUCTURED-FILTER + SEMANTIC-ORDERING fusion (the Palantir hybrid Object-Set pipeline) and a live ES
  object dense_vector round-trip are deferred to L12b; object semantic search is local-cosine + ES-dense
  here (consistent with prior sections). No family promotion happens at L12a; object search stays in the
  existing `elasticsearch` family (`infra-tricky-matrix.json` `families` unchanged except the new
  normal-path proof and the reused rule's test list).

- **L12b — Ontology-anchored unified search (OAG, shipped):** L12a left structured-object search and
  unstructured-media-content search as two disconnected surfaces; L12b joins them into ONE
  ontology-anchored unified search that mirrors Palantir's **Ontology-Augmented Generation (OAG)** — a
  single natural-language query returns ranked **ontology objects** (the result unit is the OBJECT, "OAG
  retrieves the objects and the data that define them," not a bag of text). `OntologySearchService.search`
  composes: (a) the object's OWN keyword/structured + L12a semantic match via `ObjectQueryService`
  (`.filter()` + `.nearestNeighbors().orderByRelevance()` — the documented Object-Set composition), and
  (b) the unstructured-media match — bge text (L4) + best-effort CLIP visual (L11) over content_units —
  each carrying its `source_media_item_version_id`. A new reverse media-reference lookup
  (`MediaReferenceBindingRepository.bindings_for_media_versions`, batched/N+1-safe, tenant-scoped) is the
  media→object edge: each content hit is **lifted to its owning object** (chunk-object → owning object);
  a media hit no object references is dropped (the object is the result unit). The two ranked lists fuse
  via **reciprocal rank fusion** (`score = Σ 1/(RRF_K + rank)`, the documented OAG hybrid technique), so
  an object matched BOTH by its own properties AND its bound media ranks higher. **Permission is anchored
  at the object**: every surfaced object is re-read through the object query ACL (`object:read` + masking)
  and a media hit only lifts to an object the caller may read — cross-tenant content never leaks (the
  media-content retrieval already re-reads DB truth for text_hash citation + tenant ACL). The
  embeddings/index stay DERIVED projections; the committed object/media version remains serving truth.
  This is ADDITIVE — existing object query (keyword/semantic/structured) and media `search_content`/
  `search_visual` are unchanged. No new env var, no new model (reuses the already-wired bge + CLIP), and
  **no new source-of-truth rule**: the object-as-truth/permission unit + projection invariants already
  cover it, and the model-pinned embedding REUSES the enforced
  `embedding_artifact_pins_model_version_and_chunk_spec` rule. Deterministic unit tests
  (`tests/unit/test_ontology_unified_search.py`) prove RRF ranking, the both-signals-outrank-one ordering,
  the unbound-media drop, the unreadable-owner exclusion, and the pure-object / pure-media paths with a
  reverse-lookup contract test (`tests/contracts/...binding_repository_contract.py`, sqlite + Postgres). A
  real end-to-end test (`tests/integration/test_ontology_unified_search_live.py`, scenario
  `ontology-unified-search`) seeds two Orders + a real PDF bound to one Order, processes (pypdf) + projects
  its content with real fastembed bge, and a single query "cold chain refrigerated truck" returns the
  OWNING object lifted through the media edge (carrying the content citation) alongside the object matched
  by its own semantic note. No family change in `infra-tricky-matrix.json` (object/media search stay in
  their existing families); the new live scenario is registered in the integration-scenario gate.

- **L13 — Query-side HyDE + query distillation (OAG query-side, shipped):** prior sections gave us hybrid
  lexical+dense retrieval fused with **reciprocal rank fusion** — which IS Palantir's documented
  "reranking" (the OAG page's fused output is literally `rerankedResults`; Palantir ships NO cross-encoder
  reranker, so a post-retrieval cross-encoder is intentionally **beyond-Palantir BYO and not built**). The
  Palantir-faithful retrieval-quality techniques we still lacked are the **query-side LLM** ones, both from
  the OAG page (`palantir.com/docs/foundry/ontology/ontology-augmented-generation`): **HyDE (Hypothetical
  Document Embeddings)** — "instead of embedding the query directly, you first ask an LLM to produce a
  hypothetical chunk that answers this question, which you then embed" (the hypothetical is _search-bait_;
  real citations still come from the retrieved real chunks) — and **query distillation** — "injecting an LLM
  step between the user query and what is passed to the keyword search allows the possibility of distilling
  the query" (e.g. extract the key user actions, drop stop words). Both run QUERY-SIDE, before retrieval; RRF
  stays the fusion step. A new injectable `CompletionModelAdapter` port (`complete(prompt) -> str`,
  `model_version`/`is_available`/`failure_contract()`, mirroring the embedding/vision ports) + a
  `LocalCompletionAdapter` whose default engine raises `completion_model_unavailable` is the seam — the **real
  local LLM is DEFERRED** (much heavier than bge/whisper; a live quality proof needs it), exactly like
  live-OCR / live-ASR were contract-first then made live. A small `QueryEnrichmentService` helper
  (`enrich_query`) returns `(dense_text, keyword_text)`: when a completion model is wired it embeds the LLM
  hypothetical answer (HyDE) as the dense `query_vector` via the existing pinned **bge** embedder (the HyDE
  vector is still bge-pinned — only the text fed to the embedder changes) and feeds the distilled terms to the
  lexical leg; when NOT wired it returns the raw query, so `DefaultContentRetrievalService._with_query_vector`
  is **byte-for-byte unchanged** (default-off — `LocalCompletionAdapter()` is unavailable until a real engine
  is injected, no behaviour change anywhere). This is ADDITIVE; no new env var, **no new model in CI** (the
  completion default raises; bge is already cached). Deterministic unit tests (`tests/unit/test_query_enrichment.py`)
  with a FAKE completion engine prove HyDE embeds the hypothetical (not the raw query), distillation feeds the
  cleaned keyword text, the default adapter is the raw query (regression guard), and the default engine raises
  `completion_model_unavailable`; a **REAL fastembed bge + fake-LLM** improvement test proves HyDE measurably
  ranks the cold-chain target higher than the short raw question (bge: raw scores the target 0.619 / margin
  0.145; the HyDE hypothetical scores it 0.919 / margin 0.394 — target ranks first). A contract test
  (`tests/contracts/test_completion_model_contract.py`) locks the new port. **No new source-of-truth rule**:
  the projection-is-derived invariant + the enforced `embedding_artifact_pins_model_version_and_chunk_spec`
  rule already cover it (HyDE re-uses the pinned bge space). No family change in `infra-tricky-matrix.json`
  (media/object search stay in their existing families); the bge improvement test rides the existing
  `media-embeddings` scenario.

## AIP P0 (LLM-egress security)

- **P0a — Content search classification PRE-filter (shipped):** before any AIP/LLM egress, the
  content/media search path must enforce classification security AT RETRIEVAL — it previously did
  not. The content_units row carries a `security_envelope` with a `classification`, but the search
  CONTRACT (`IndexedContentUnit`, `HybridContentQuery`) did not, and `search_content` only re-validated
  tenant + text_hash — so a unit classified above the caller's clearance could be retrieved, ranked, and
  (later) fed to an LLM. P0a closes this, mirroring Palantir exactly: **one model, per-request,
  as-the-user** — every access path re-evaluates the same access model; the index/embeddings are DERIVED
  projections, never an independent authority (palantir.com/docs/foundry/object-backend/overview,
  /aip/aip-security). **Mandatory control property** — `IndexedContentUnit` gains `classification: str = ""`,
  copied at projection time from the source content_unit's `security_envelope` and stored AS a required
  gate property ON the indexed record (palantir.com/.../mandatory-control-properties). **Granular policy →
  query template (PRE-filter)** — `HybridContentQuery` gains `allowed_classifications: tuple[str, ...] | None`;
  the security predicate is compiled into the query so the engine "returns only" permitted rows. Both index
  adapters PRE-filter: `LocalContentIndexAdapter.search` drops uncleared candidates BEFORE lexical/dense
  ranking + RRF; `ElasticsearchContentIndexAdapter` adds a `terms` classification filter to the query body
  (lexical + dense paths). Unauthorized candidates are NEVER retrieved, so they cannot influence the
  kNN/ranking or reach an LLM — this **closes the ranking/count side-channel**
  (palantir.com/.../platform-security-management/manage-granular-policies, /security/restricted-views).
  **Re-apply at the authoritative re-read** — `DefaultContentRetrievalService._is_authoritative` re-applies
  the SAME classification gate against the committed DB row (defense-in-depth: even a stale/over-broad index
  hit is dropped), keeping the existing tenant + text_hash checks
  (palantir.com/.../object-permissioning/object-security-policies). The single classification-access rule
  (`is_classification_cleared`) is REUSED across both adapters and the re-read, mirroring the established
  media-plane `MediaReferenceBindingService.resolve(..., allowed_classifications=...)` mechanism — `None`
  = full clearance, so empty-classification rows and uncleared callers are byte-for-byte unchanged (additive,
  no schema change — classification already lives on `content_units.security_envelope`; no new env var, no
  new model). New enforced source-of-truth rule
  `content_search_pre_filters_by_classification_not_post_filter` names the deterministic tests:
  `test_pre_filter_excludes_over_classified_unit_from_ranking` (local index: the over-classified unit that
  would otherwise be the TOP lexical hit never enters the scored set),
  `test_search_pre_filters_over_classified_unit_end_to_end` + `test_search_returns_unit_when_caller_is_cleared`
  (service end-to-end), `test_search_pre_filters_by_classification_term` (ES terms PRE-filter),
  `test_authoritative_re_read_drops_over_classified_leaked_by_stale_index` (defense-in-depth re-read), and
  `test_empty_classification_back_compat_unchanged` (back-compat). No family change in
  `infra-tricky-matrix.json` (content search stays in its existing family).

- **P0b — governed Model Gateway + registry/alias + egress (shipped):** all LLM access flows through
  ONE boundary so no provider SDK leaks into app logic. New ports `LanguageModelAdapter`
  (`complete(ModelRequest) -> ModelResponse` + `stream(ModelRequest) -> Iterable[ModelEvent]`:
  messages/params/tools → content + tokenUsage + finish_reason + `normalized_tool_calls`) and
  `ModelRegistryRepository` (catalog: `ai_model_providers`/`ai_models`/`ai_model_aliases`, canonical
  §10.1 columns). `ModelGatewayService.invoke` (1) **resolves a stable alias →
  provider/model/revision** (pinned to the alias's `version` if set, else the model's `revision`;
  the resolved provider comes from the joined provider row's `provider_type`/`profile_name`),
  (2) **egress-gates BEFORE any provider call** — a request whose `data_classification` is not in
  the resolved model's `allowed_classifications`, or whose `region_requirement` disagrees with the
  provider's region, is DENIED typed (`AdapterError` with `details.reason=egress_denied`) and the
  adapter is never reached, (3) **never silently falls back** — a sunset/deprecated model OR an
  alias whose `status` is not `enabled` fails typed rather than swapping in another model, (4) calls the wired
  adapter with credentials referenced by NAME/version via `SecretProvider` (raw key never in app
  logic/response/trace), and (5) returns the response with provider/resolved id+revision + per-call
  `model_hash`/`prompt_hash` (raw prompt never logged). The model-registry Alembic upgrade path now
  applies PostgreSQL `ENABLE ROW LEVEL SECURITY`, `FORCE ROW LEVEL SECURITY`, and tenant policies to
  `ai_model_providers`/`ai_models`/`ai_model_aliases`, so existing DBs upgraded by Alembic match the
  `create_database()` bootstrap RLS path. `FakeLanguageModel` (deterministic echo) is
  the SAFE composition default — no Foundry token, no network, existing tests unaffected;
  `ProviderCompatibleLanguageModel` is the real provider-compatible-proxy adapter whose default
  raises `language_model_unavailable` (real provider DEFERRED, mirroring the deferred-engine
  pattern; the seam + contract is what P0b proves). New enforced source-of-truth rule
  `model_access_is_gateway_governed_with_egress_and_no_silent_fallback` names the tests
  (`test_gateway_resolves_pinned_revision`, `test_gateway_resolves_floating_latest_when_unpinned`,
  `test_gateway_denies_egress_when_classification_exceeds_allowance`,
  `test_gateway_denies_egress_when_region_not_permitted`,
  `test_gateway_does_not_silently_fall_back_on_deprecated_model`,
  `test_gateway_does_not_serve_a_non_enabled_alias`, plus the canonical §15.2 deterministic security
  tests `test_provider_egress_denied_before_network_call` + `test_secret_value_never_logged`,
  `test_deferred_provider_references_secret_by_name_and_raises_unavailable`).
  **Documented Palantir** (mirrored): Model Catalog model types (completion/embedding/vision) +
  lifecycle status + provider/context-window/region; provider-compatible proxy (Foundry token, never
  a provider API key; `model` = concrete Catalog RID); export-control markings + network egress +
  georestriction gating classification/region; pin-revision recommended for production
  (palantir.com/docs/foundry/model-catalog/overview, /aip/llm-provider-compatible-apis,
  /administration/configure-egress, /integrate-models/transform-model-input).
  **OUR extension** (clearly NOT a copy of a documented proxy feature): the alias→provider/model/
  revision table extends the SDK `ModelInput.alias` indirection to the gateway (Palantir has NO alias
  indirection at the proxy layer — the proxy binds to a concrete RID); per-call model/prompt hash
  logging; and the no-silent-fallback invariant on write-producing paths. HyDE's existing minimal
  `CompletionModelAdapter` (L13) is left AS-IS; unifying it onto the gateway is a later P1 step.
  Unifying tool execution server-side as the invoking user is P1's ToolBroker (the LLM only REQUESTS
  tools here); the full call ledger is P0c (P0b just computes/returns the hashes).

- **P0c — AI run/event ledger schema + repository (shipped as the first ledger slice):** the
  canonical §10.2 runtime-plane tables now exist in SQLAlchemy metadata, Alembic, and schema-revision
  evidence: `ai_sessions`, `ai_session_state_versions`, `ai_messages`, `ai_execution_runs`,
  `ai_execution_events`, `ai_model_calls`, `ai_context_items`, `ai_tool_calls`, `ai_citations`, and
  `ai_usage_ledger`. The `AiRunRepository` contract persists the run intent, session state, idempotent
  messages, sequenced events, provider call accounting, selected/omitted context, tool authorization
  decisions, citations, and usage/cost rows without storing raw prompts or tool results in the general
  DB (`*_ref`, `*_hash`, and `redacted_preview` carry the durable trace). This mirrors Palantir's
  documented AIP observability/run-history shape: executions have trace ids, timeline events, model
  call accounting, errors, user/session attribution, and access-controlled exported logs; our schema is
  the local durable subset, not a managed Foundry trace backend. `runtime_run_relations` can now name
  `ai` as a run type so later AI runs can link to action/workflow/outbox/materialization evidence.
  `quality:ai-ledger` proves exact canonical columns, `UNIQUE(tenant_id, session_id, client_message_id)`,
  `UNIQUE(ai_run_id, sequence)`, PostgreSQL RLS DDL in the Alembic upgrade path, SQLite + PostgreSQL
  round-trip behavior, message retry idempotency, event sequence idempotency, tenant scoping, and
  runtime-lane wiring. P0i now adds the first generated Operations API/SDK read surface for these
  rows; ModelGateway auto-recording, prompt artifact encryption, full trace UI, and ToolBroker
  execution remain later AIP slices.

- **P0d — Context compiler + retrieval context contract (shipped as the first prompt assembly slice):**
  `ContextProvider` and `RetrievedContextItem` now define the authorized retrieval boundary that hands
  opaque `context_id` items to the AIP compiler. `ContextCompilerService` compiles model messages in the
  canonical §8.6 order — platform safety policy, agent instruction, application state, tool definitions,
  retrieved context, citation mapping, output schema, then the user message — and emits
  `compiled_prompt_hash`, `context_manifest_hash`, `tool_manifest_hash`, `state_snapshot_hash`, and
  `policy_snapshot_hash` for the AI ledger, using `sha256:`-prefixed digest strings. Retrieved text is
  encoded as untrusted JSON string data so delimiter-like strings cannot become prompt section
  boundaries; duplicate context ids fail closed, non-allowlisted security partitions fail closed, and a
  context text/hash mismatch fails closed before any model call. `quality:context-compiler` proves the
  port contract, deterministic section ordering, delimiter-escape resistance, opaque citation mapping,
  fail-closed hash/partition checks, and runtime-lane wiring. Retrieval orchestration, AgentRuntime
  looping, public API/SDK surfaces, and the visual trace UI remain later AIP slices.

- **P0e — read-only Tool Broker + executor port (shipped as the first tool-execution slice):**
  the LLM still does **not** execute tools directly. `ToolBrokerService` treats a model-requested
  tool call as untrusted input and validates the canonical §8.8/§9.5 broker chain before any
  server-side executor is reached: agent allowlist, published `ToolSpec` version, input JSON schema,
  invoking-user permission, object/property allowlists, masked-property rejection, model-egress
  compatibility, timeout/result budgets, and confirmation/review requirement for non-read effects.
  `ToolExecutor` is the product boundary that runs the approved call with the same `RequestContext`
  as the caller; `FakeToolExecutor` is the safe local default and never opens network, SQL, shell, or
  provider SDK paths. The broker returns only masked/bounded output, `sha256:` argument/result hashes,
  a redacted preview, and an `AiToolCallRecord` ready for the P0c ledger. This mirrors Palantir's
  documented tool model: AIP Logic/Chatbot tools let an LLM ask for ontology reads/actions/functions,
  but the platform executes those calls within the invoking user's permissions and write actions can
  be configured for user confirmation or review (palantir.com/docs/foundry/logic/blocks,
  /chatbot-studio/tools, /logic/execution-mode-settings, /action-types/permissions). New enforced
  source-of-truth rule `tool_calls_are_brokered_as_user_scoped_bounded_requests` names the contract
  and unit tests proving invoking-user execution, generic executor deny, no pre-check executor call,
  non-read confirmation fail-closed, egress incompatibility, output masking, result limits, ledger
  hashes, local runtime composition, and CI runtime-lane wiring through `quality:tool-broker`.
  AgentRuntime looping, actual ontology/content/state/action tool adapters, approval bridge, public
  API/SDK surfaces, and the visual trace UI remain later AIP slices.

- **P0f — Citation Service + source verifier port (shipped as the first citation integrity slice):**
  the model still does **not** emit trusted source URLs. `CitationService` accepts model-proposed
  `context_id` + claim-span pairs, looks up the selected context item in the tenant-scoped
  `AiRunRepository` manifest, requires caller read permission for the source type, asks
  `CitationSourceVerifier` to re-read the current source version/hash, and only then writes an
  `AiCitationRecord` plus returns a `flite-citation-nav.v1` HMAC-signed navigation reference. Forged
  context ids, omitted/unselected context, stale version/hash, invalid spans, duplicate orders, and
  missing source permission all fail closed before a citation row is written. This mirrors Palantir's
  documented AIP Chatbot Studio citation pattern: document/Ontology citations are rendered as clickable
  source references, object citations can update Workshop variables, and the default behavior opens the
  corresponding object or document (palantir.com/docs/foundry/chatbot-studio/citations). OUR
  hardening is that model output supplies only an opaque context id; source display and navigation are
  server-resolved and signed from the run ledger. `FakeCitationSourceVerifier` is the local default, and
  `quality:citation-service` proves the port contract, local runtime composition, selected-context
  lookup, source permission, stale-source rejection, signed-ref generation, raw-secret non-leakage,
  ledger persistence, and CI runtime-lane wiring. AgentRuntime looping, real object/document/media
  source resolvers, public API/SDK surfaces, and the visual trace UI remain later AIP slices.

- **P0g — Action Proposal Service + review-queue fingerprint (shipped as the first human-review action
  slice):** the model still does **not** execute Ontology Actions. `ActionProposalService` accepts a
  model-proposed action, then re-reads the tenant-scoped AI run ledger to require selected evidence
  context ids, checks the agent action allowlist, requires `insight:create` plus
  `action:execute:<ActionType>` for the invoking user, re-reads the active Ontology action definition,
  and re-reads the current object version before creating a pending `insight_reviews` row. The
  canonical proposal fingerprint covers action type, target type/id, expected object version,
  canonical parameters, evidence refs, agent version, and policy version; that fingerprint is also the
  review-create idempotency key, so exact retries replay while parameter/evidence/policy changes require
  a new review. `insight_reviews` now carries the canonical §10.3 fields
  `proposal_type`, `proposal_fingerprint`, `originating_ai_run_id`, `originating_tool_call_id`,
  `expires_at`, `execution_status`, `approved_action_run_id`, and `approval_policy_version`, with a
  forward-safe Alembic expand migration and schema snapshot. This mirrors Palantir's documented AIP
  Logic + Automate proposal flow, where generated Actions can be staged for human review, proposal
  details show the reason/decision log, and accepting a proposal executes the Action
  (palantir.com/docs/foundry/logic/aip-logic-integration-automate). It also follows Palantir Action
  Type semantics: an Action is the transaction that changes Ontology objects/properties/links, and
  applying an Action depends on action permissions/submission criteria
  (palantir.com/docs/foundry/action-types/overview, /action-types/permissions). P0g stops before
  execution; the Approval-to-Action bridge is the next slice. Public API/SDK action-proposal routes and
  the visual proposal review workspace remain later AIP slices. `quality:action-proposal` proves user-facing
  `foundry.aip.propose_action(...)`, local runtime composition, selected-evidence enforcement, forged
  context rejection before review creation, policy/agent/object-version fail-closed checks, exact retry
  replay, changed-fingerprint new review creation, no `action_runs` side effect, and CI runtime-lane
  wiring.

- **P0h — Approval Execution Service + approved-as action linkage (shipped as the Approval-to-Action
  bridge):** `ApprovalExecutionService` executes only an already-approved action proposal review through
  `foundry.aip.execute_approved_action(...)`. The service requires reviewer permission, requires
  `action:execute:<ActionType>`, re-checks source evidence access, re-loads the originating AI run ledger
  and recomputes the canonical proposal fingerprint, rejects expired reviews, re-reads the active action
  definition, and re-reads the current target object version before claiming execution. Execution then
  calls `ActionService.apply_action(...)` with the proposal fingerprint as the action idempotency key, so
  the existing action transaction, object mutation, outbox, audit, and optimistic-concurrency guarantees
  remain the write path. After success, `insight_reviews.execution_status` moves to `executed`,
  `approved_action_run_id` is filled, and `runtime_run_relations` records
  `insight_review --approved_as--> action`. This mirrors Palantir's documented proposal review flow where
  accepting a proposal executes the generated Action, while still enforcing Foundry Action permissions and
  submission-time checks. `quality:approval-execution` proves approved-only execution, fingerprint
  mismatch rejection, expired-review rejection, reviewer-time object-version recheck, exactly-once replay
  after execution, durable review-to-action linkage, and CI runtime-lane wiring. Public API/SDK routes,
  a visual evidence/proposal review workspace, Temporal-backed long-running human approval, and broader
  external side-effect compensation UI remain later AIP slices.

- **P0i — AI Operations run/detail surface (shipped as the first operator-facing AI trace slice):**
  Operations now treats canonical AI executions as first-class run rows. `RuntimeRunType` and generated
  SDK types include `ai`, `RuntimeRunQueryResult` includes `aiRuns`, and
  `GET /api/operations/runs/{run_type}/{run_id}` returns an `ai` detail field for `run_type=ai` built from
  `AiRunRepository.ledger_for_run(...)`. The payload exposes run refs and hashes, ordered events,
  model-call request/response hashes plus token and latency accounting, selected/omitted context
  metadata, tool-call argument/result hashes and status, citations, usage/cost rows, derived summary
  totals, and a lightweight timeline. It does not expose raw prompt text, raw tool results, provider
  response bodies, or authorization-bearing JSON values; the detail payload masks those values if they
  are accidentally present in JSON evidence. This mirrors the documented Palantir AIP observability
  pattern of run history, trace view, metrics, and access-controlled logs while keeping Foundry-lite's
  canonical §9.7 privacy boundary: the general DB/API surface carries refs, hashes, redacted previews,
  counts, and ids, not raw prompt artifacts. `quality:ai-operations` proves SQL-backed list/detail,
  API smoke behavior, raw AI payload non-leakage, generated SDK type drift, and CI runtime-lane wiring.
  A full visual trace explorer, separate encrypted prompt artifact access, log-access/marking
  administration, and live provider usage dashboards remain later AIP slices.

- **P0j — Logic Runtime typed DAG + safe boundary execution (shipped as the first Logic slice):**
  `LogicRuntimeService` validates a typed, bounded AIP Logic DAG before any block runs. Duplicate block
  ids, missing dependencies, cycles, unsupported block kinds, and max-block budget violations fail closed.
  The first executable blocks are deliberately narrow: `CallFunction` delegates to `ToolBrokerService`
  and records the returned `AiToolCallRecord`, while `CreateActionProposal` delegates to
  `ActionProposalService` so write intent becomes a pending human-review proposal instead of an action
  side effect. `ApplyAction` is rejected until a later approved-proposal signal path exists. Logic
  start/completed/failed evidence is appended to `ai_execution_events` with payload refs, `sha256:`
  hashes, and redacted previews, and same-graph replay keeps the same graph/result hash. This mirrors
  Palantir AIP Logic's block-oriented execution model while preserving Foundry-lite's AIP invariant that
  tools and writes must pass through the governed broker/proposal boundaries. `quality:logic-runtime`
  proves brokered read-tool execution, deterministic replay hashing, direct action rejection, review-queue
  proposal creation with no `action_runs` side effect, cycle rejection, and CI runtime-lane wiring.
  Temporal async start/signal/cancel/query, crash-safe pause/resume, schedule/event triggers, model-call
  blocks, visual DAG authoring, and workflow-status-vs-domain-commit proofs remain later AIP slices.

- **P0k — AI Evals + Release Guard evidence (shipped as the first eval/release slice):**
  `EvalService` records deterministic eval evidence before a candidate agent version can be promoted to
  an operational release channel. The new tenant-scoped `ai_eval_suites`, `ai_eval_cases`,
  `ai_eval_runs`, `ai_eval_results`, and `ai_agent_releases` tables capture suite/case definitions,
  per-case input/expected/actual/result `sha256:` hashes, run summaries, and eval-backed promotion
  decisions. The scoring slice is deliberately local and mutation-free: exact-subset observations prove
  whether expected safety/action/answer/tool outcomes appeared, repeated runs of the same case must keep
  the same actual hash, and suite/case definition drift without a version bump fails closed. Promotion
  to a release channel requires a passed eval run for the same agent version and channel; `stable`
  additionally requires passing Security and Action axes plus zero repeated-run variance. This mirrors
  Palantir AIP Evals' test-case/evaluator/run-result pattern and the documented practice of evaluating
  action-affecting logic in simulation rather than mutating production Ontology data, while keeping
  Foundry-lite's release gate local and inspectable. The Alembic upgrade path applies PostgreSQL
  `ENABLE ROW LEVEL SECURITY`, `FORCE ROW LEVEL SECURITY`, and tenant policies to all new eval/release
  tables. `quality:ai-evals` and `quality:ai-release` prove repository round-trip, migration RLS DDL,
  failed-eval release rejection, stable Security+Action gate, repeated-run variance rejection,
  case-definition version drift rejection, matching-channel enforcement, and CI runtime-lane wiring.
  Visual eval workbench, generated eval datasets, LLM-as-judge scoring, baseline diff dashboards,
  Apollo rollout/rollback integration, and live-provider eval smoke remain later AIP slices.

- **P0l — Visual Builder preflight (shipped as the first Builder slice):**
  `VisualBuilderService` gives the Web Operations AIP Builder panel and generated SDK a read-only way to
  validate an authored agent/model/prompt/context/tool/Logic/eval draft before runtime execution or
  release promotion. The service returns `sha256:` draft and graph hashes, ready/blocked status, blocking
  issues, and safe-boundary labels without writing DB state. It fails closed on tenant partition mismatch,
  missing or unpublished governed tools, dangerous generic executors, direct `ApplyAction`, missing Logic
  dependencies/cycles, missing eval axes, and stable release drafts without Security+Action eval axes.
  `POST /api/aip/builder/validate`, generated `client.aip.builder.validate(...)`, and
  `quality:visual-builder` prove the service/API/SDK/Web contract and CI runtime-lane wiring. Persisted
  Builder definitions, drag-and-drop canvas editing, full Agent Studio, visual eval dashboard, visual AI
  run debugger, and visual release promotion UI remain later AIP slices.
