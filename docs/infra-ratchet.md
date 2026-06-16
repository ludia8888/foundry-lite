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
-> wire the proof into CI and docs
-> merge
-> only then pick the next infrastructure
```

## CI Contract

Every active infrastructure ratchet must have these proof classes before the
next infrastructure can move from `next` to `active`.

| Proof class         | Meaning                                                                                   | CI/document evidence                                  |
| ------------------- | ----------------------------------------------------------------------------------------- | ----------------------------------------------------- |
| `adapter-contract`  | The new adapter obeys the same application port contract as local/fake profiles.          | contract tests plus `check_contract_test_per_port.py` |
| `normal-path`       | A realistic happy path works through the public API/facade, not private helpers only.     | unit/integration/smoke test                           |
| `failure-injection` | At least one targeted failure is injected at the dangerous commit point.                  | named regression test                                 |
| `concurrency-race`  | Parallel or repeated operations cannot silently create duplicate/losing state.            | concurrent or repeated test                           |
| `retry-idempotency` | Retrying after an ambiguous or failed attempt does not create a second logical success.   | retry regression test                                 |
| `partial-success`   | If external storage/system succeeds but local metadata fails, serving state remains safe. | split-brain regression test                           |
| `recovery-cleanup`  | Cleanup is reachability-safe and never deletes committed evidence.                        | cleanup regression test                               |
| `operator-evidence` | Failure is visible in run/audit/transaction/error evidence, not only logs.                | runtime evidence assertion                            |
| `docs-sync`         | Current status, risk register, tricky checklist, and sprint evidence agree.               | `check_infra_ratchet.py` + `check_doc_drift.py`       |

The static CI lane runs `scripts/quality/check_infra_ratchet.py`, which verifies
that this document, the tricky failure checklist, commit-point risk register,
implementation status, package scripts, and `ci_gate.sh` still mention the
ratchet discipline.

## Active Ratchet Queue

| Order | Infrastructure                   | Status         | Why this order                                                                                                                | Cannot advance until                                                                                                                                          |
| ----- | -------------------------------- | -------------- | ----------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1     | MinIO/S3 DatasetStorageAdapter   | active-covered | Storage is the base layer for ingest, transform output, materialization output, stream archive, Iceberg, backup, and restore. | `quality:s3-storage` stays green in CI and S3 remains the only active production-style infra family in this ratchet.                                          |
| 2     | Iceberg Catalog/TableAdapter     | active-covered | Iceberg adds a table metadata/catalog commit point on top of object storage.                                                  | `quality:iceberg` stays green in CI; each dataset version pins an exact Iceberg snapshot id and the DB COMMITTED version remains the serving source of truth. |
| 3     | Spark ComputeAdapter             | active-next    | Spark should consume the same Dataset API and pinned versions without knowing storage internals.                              | Iceberg/S3 inputs can be previewed and transformed without API divergence.                                                                                    |
| 4     | Temporal WorkflowAdapter         | later          | Durable workflow execution changes retry/time semantics for long-running operations.                                          | Storage/table/compute commit points already have recovery evidence.                                                                                           |
| 5     | Managed Elasticsearch deployment | later          | The adapter/projection proof exists; deployment/operations should follow after storage commit points.                         | Search remains a rebuildable projection and live cluster failure evidence is added.                                                                           |

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
test_iceberg_engine_and_s3_warehouse_end_to_end
test_iceberg_snapshot_committed_db_failure_cleans_up_orphan_snapshot
test_iceberg_missing_table_on_read_marks_storage_corruption
test_iceberg_corrupted_data_file_surfaces_through_engine_as_corruption
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
duplicate-version guard rejects reuse of a committed version id.

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
[ ] operator evidence is asserted in run/audit/transaction/error payloads
[ ] docs/infra-ratchet.md is updated if active/next order changed
[ ] docs/commit-point-risk-register.md status/evidence is updated
[ ] docs/foundry_lite_tricky_failure_modes_checklist.md test names are updated
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
