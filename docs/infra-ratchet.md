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

| Order | Infrastructure                   | Status         | Why this order                                                                                                                | Cannot advance until                                                                                                                                                                                                                                                                                                                                                                   |
| ----- | -------------------------------- | -------------- | ----------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1     | MinIO/S3 DatasetStorageAdapter   | active-covered | Storage is the base layer for ingest, transform output, materialization output, stream archive, Iceberg, backup, and restore. | `quality:s3-storage` stays green in CI and S3 remains the only active production-style infra family in this ratchet.                                                                                                                                                                                                                                                                   |
| 2     | Iceberg Catalog/TableAdapter     | active-covered | Iceberg adds a table metadata/catalog commit point on top of object storage.                                                  | `quality:iceberg` stays green in CI; each dataset version pins an exact Iceberg snapshot id and the DB COMMITTED version remains the serving source of truth.                                                                                                                                                                                                                          |
| 3     | Spark ComputeAdapter             | active-covered | Spark should consume the same Dataset API and pinned versions without knowing storage internals.                              | `quality:spark` and `quality:infra-composition` stay green in CI; Spark runs transforms on local parquet materialized from pinned versions and the composition gate proves Iceberg-on-S3 input/output.                                                                                                                                                                                 |
| 4     | Temporal WorkflowAdapter         | active-covered | Durable workflow execution changes retry/time semantics for long-running operations.                                          | `quality:temporal` stays green in CI; workflow start is idempotent by idempotency_key and a workflow failure/timeout/cancel surfaces in a durable run error payload, proven on the time-skipping test server. S52 now uses this boundary for the `ConnectorSyncWorkflow` control-plane start/status/audit path, while full connector data-plane workflow execution remains a later slice. It is still a standalone family, not part of the S3+Iceberg+Spark composition stack.                   |
| 5     | Managed Elasticsearch deployment | active-covered | The adapter/projection proof existed; this ratchet adds live cluster failure evidence and the projection-rebuild contract.    | `quality:elasticsearch` stays green in CI; a cluster outage surfaces as a typed AdapterError (timeout/unavailable/rate_limited/validation), search stays a rebuildable projection, and the version guard holds under concurrent writers. Orthogonal projection (not a storage/compute commit point), so it is a standalone family, not part of the S3+Iceberg+Spark composition stack. |

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

This ratchet proves idempotent start-and-wait (workflow id = idempotency_key,
re-start/concurrent-start re-attaches with no duplicate run), activity retry,
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
| Operator evidence  | `pnpm quality:operator-evidence`  | every active infra declares durable payload paths, a run surface, and `testAssertions` mapping each path to collected operator-evidence tests — logs-only evidence is not enough.             |
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
  `runtime_root_cause_summary.md`.
- `pnpm ci:gate:release` (push to main, tags, manual dispatch via `release.yml`): the
  full runtime lane plus heavier 100k/1m performance smokes and the contract gates,
  with evidence artifacts uploaded.

**Debugging a failed gate:** read the step summary, then
`artifacts/quality/<gate>.json` / `<gate>_summary.md`
(`proof_matrix`, `source_of_truth_contract`, `operator_evidence`) and
`artifacts/quality/runtime_root_cause_summary.md`. Each finding names the likely
root cause and the files to inspect.

## S46+ Expansion Handoff

[Data Platform Expansion Roadmap](./data-platform-expansion-roadmap.md) extends
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
