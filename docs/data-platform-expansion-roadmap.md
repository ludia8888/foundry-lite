# Foundry-lite Data Platform Expansion Roadmap

**Status:** Active post-MVP roadmap / S46 complete, S47-S61 partial
**Baseline date:** 2026-06-18  
**Imported from:** `/Users/isihyeon/Downloads/foundry_lite_data_platform_expansion_sprint_plan_ko.md`  
**Repository:** `ludia8888/foundry-lite`

This document is the checked-in, cross-verified version of the Downloads sprint
plan. It does not override current implementation status. For current truth,
use [Implementation Status](./implementation-status.md), [Infra Ratchet](./infra-ratchet.md),
and [Sprint Evidence Ledger](./sprint-evidence-ledger.md).

## 0. Cross-Check Result

The Downloads plan is directionally useful, but several statements needed to be
merged carefully so future work does not look already done.

| Area | Current evidence | Merge decision |
|---|---|---|
| S3/MinIO, Iceberg, Spark | Active-covered in `docs/infra-ratchet.md` and `docs/infra-tricky-matrix.json`; `quality:s3-storage`, `quality:iceberg`, `quality:spark`, and `quality:infra-composition` exist. | Treat as implemented ratchet proof, not full production platform packaging. |
| Temporal | `WorkflowAdapter` and `quality:temporal` ratchet exist, and S52 now has a partial `ConnectorSyncWorkflow` control-plane path through Operations/API/SDK with local/fake/Temporal contract proof and audit-linked `ProductWorkflowRun` evidence. | Treat product workflow start/status/audit linking as partial; keep real connector activity data-plane, cancel cleanup, response-loss reconciliation, continue-as-new, workflow upgrade replay, and managed worker operations as future work. |
| External writeback / saga | `mock_erp_simulator` before-commit writeback now records `outcome_unknown` as a first-class action/writeback/audit state when the external response is lost, records `compensation_required` when simulated external success is followed by local mutation failure, and lets an operator-provided remote-success resolve catch up the local object mutation and close the action/writeback as `reconciled`. `quality:external-writeback` and `quality:saga-reconciliation` prove same-key replay does not blindly issue another writeback, concurrent reconcile has one winner, and sensitive writeback action parameters stay masked in Operations/audit surfaces. | Treat outcome-unknown, compensation-required, operator-provided remote-success resolve, sensitive writeback audit masking, and retry lockout as partial S53 proof; keep real vendor APIs, vendor remote lookup, compensation worker execution, persistent reconciliation queue, and operator approval UI as future work. |
| Elasticsearch | Adapter/projection and live Testcontainers proof exist, but managed cloud packaging/ops are future. | Keep search as rebuildable projection; do not call managed deployment complete. |
| CDC | Archive, live Debezium proof, CDC object indexing, active-stack composition proof, and S51 bounded continuous stream archive loop proof exist. | Treat the bounded stream archive loop as active-covered; keep CDC object-indexer daemon, lease/fencing, rebalance revoke, and commit-unknown reconciliation as future S51 work. |
| Kubernetes / backup / restore | Helm and full restore execution remain future. S57 now has partial backup/restore preflight and restore-mode status proof through Operations/API/SDK: it inventories committed dataset versions, validates storage manifests/files, captures active index pointers and runtime high-watermarks, marks search projection rebuild/restore traffic pause requirements, records serving traffic closed status, blocks outbox retry/reprocess entry while restore mode is active, and approves retry/reprocess resume only after post-restore closed-loop evidence is present. | Treat S57 evidence as active-covered for DB/storage mismatch detection, restore-mode outbox retry lockout, and operator approval status for the current retry/reprocess entrypoints only; keep actual backup creation, platform-wide write traffic gate, real publisher daemon pause/resume executor, automatic restore smoke execution, and Kubernetes packaging as future operational work. |
| Record DLQ / late data / watermarks | S47 has a partial stream CDC quarantine and replay slice through `dead_letter_records`, `quality:record-dlq-replay`, DLQ write fail-closed evidence, Operations API/typed SDK replay, valid-payload APPEND replay result, replay failure result evidence, Web Operations record DLQ controls, source-side error-threshold fail-closed policy, identity/ordering fail-closed policy, and PostgreSQL concurrent replay request proof. S48 now has a partial stream/source late-data ratchet through `quality:late-data` and `quality:watermark`: stream archive rows separate event/source/ingest/process time, source policies declare event-time/source-time fields, named timezone override, plus lateness thresholds, too-late events route to Record DLQ, stream transaction metadata keeps a partition/source watermark from moving backward, slow partitions do not inherit unrelated fast-partition watermarks, duplicate delivery of an already committed late event does not create a second dataset version, Operations run detail exposes `lateDataSummary` plus a reprocessing plan for the previously closed archive dataset version, materialization detail exposes watermark/reopen evidence when a `LATE_REQUIRES_REPROCESS` CDC index run causes the next closed output to be regenerated, object/materialization explain surfaces late-data badges, Operations detail exposes a downstream impact graph for the regenerated materialization, and stale late CDC deletes cannot remove or resurrect the current active object. Transform-level Record DLQ policy remains future. | Treat S47 stream/source Record DLQ replay and the S48 stream/source late-data watermark/materialization-detail/badge/downstream-impact/stale-delete slice as active-covered; do not call transform-level DLQ policy or full watermark platform complete. |
| Multi-file dataset manifests | S49 now has a partial read/preview slice: `DatasetStorageAdapter.data_file_paths()` resolves manifest-listed files in order, `foundry.datasets.preview(...)` reads multi-file manifests through the same public facade, `quality:multi-file-dataset` proves unlisted files are not discovered by directory/bucket listing, `quality:partition-pruning` proves local/fake manifest-entry partition filters reduce actual read files through the public facade, and the S3/Iceberg storage ratchets cover their profile-specific partition-filter behavior. Dataset rows can declare `partition_spec`, `sort_order`, and `target_file_size_bytes`. | Treat manifest-listed multi-file reading, local/fake/S3 read/preview partition filtering, and Iceberg no-match skip behavior as active-covered; keep multi-part atomic commit, transform predicate pushdown, high-cardinality warnings, and Iceberg snapshot file-level pruning as future S49 work. |
| Iceberg maintenance | S50 now has a partial planning slice: `foundry.operations.plan_iceberg_maintenance(...)`, `GET /api/operations/maintenance/iceberg`, and `POST /api/operations/maintenance/iceberg/{dataset}/plan` return candidate/protected/orphan snapshot previews, and `quality:iceberg-maintenance` proves DB committed version snapshots are protected from deletion candidates with audit evidence. | Treat maintenance planning and committed-version protection as active-covered; keep actual `rewrite_data_files`, snapshot expiration, orphan cleanup execution, compaction row-hash proof, and run-state model as future S50 work. |
| Auth/privacy/erasure | Header trust, local policy, and S58A JWT/OIDC plus secret-provider slices exist. API requests use `AuthProvider` profile selection, production refuses header-trust/demo auth, `FOUNDRY_LITE_AUTH_PROFILE=jwt|oidc` verifies bearer tokens against local OIDC discovery/JWKS JSON, tenant-scopes local M2M service-account tokens, blocks locally revoked JWT IDs, webhook signing reads through `SecretProvider`/`EnvSecretProvider` instead of direct `os.getenv`, REST connector secretRefs are re-resolved through `SecretProvider` on each snapshot, S58B adds a versioned privacy transform core for tenant-scoped pseudonymization, basic anonymization, local text PII redaction, protected in-memory reversible mapping proof, production-to-nonprod replication policy proof, anonymized source/target dataset version lineage metadata, and raw-value-free OpenLineage-compatible privacy event artifacts, and S58C adds an erasure request/resolution/manifest proof with backup-retention and audit-minimization policy evidence. | Treat S58A/S58B/S58C as partial for local JWT/OIDC verification, JWKS refresh-on-unknown-`kid`, service-account claim mapping, local `jti` denylist revocation, SecretProvider/local-env/redaction, REST connector local secretRef refresh, local privacy transform/protected mapping/replication policy/dataset-lineage artifact proof, and local erasure manifest proof only; keep live OIDC discovery fetch, JWKS polling/TTL/key retirement, IdP introspection/refresh-token revocation, service-account registry/scope policy, cloud/Vault secret managers, full connector workflow credential refresh, durable environment replication workflow, durable/encrypted reversible mapping backend, runtime DB/outbox/OpenLineage transport integration, durable erasure request table/API/workflow, object/search/materialization/DLQ/backup executors, and full right-to-erasure lifecycle as future work. |
| Frontend | Object Explorer, generated SDK, and the first S61 frontend foundation slice exist: SDK request/context/error helpers, request-id telemetry, retryability display, and a focused frontend foundation gate. Full workspaces are not complete. | Treat S61 as partial; keep S62-S64 as product surface track. |

## 1. Program Invariants

Every sprint in this roadmap must preserve these invariants.

- [ ] Cursor, offset, and watermark never advance before a durable commit.
- [ ] Retry never creates a second logical success.
- [ ] A `COMMITTED` dataset version in the metadata DB remains the serving truth.
- [ ] S3 objects, Iceberg snapshots, Elasticsearch documents, UI state, and SDK
      caches are projections or artifacts, not standalone truth.
- [ ] A run pins input versions and the logical watermark at start.
- [ ] External timeout is not automatically treated as confirmed failure.
- [ ] External success plus local failure becomes compensation or reconciliation
      work, not a hidden success.
- [ ] Failures must be durable in run/error/transaction/audit/outbox/trace
      payloads, not only in logs.
- [ ] Fake-only proof cannot be described as production readiness.
- [ ] Distributed failures that are not reproduced must stay explicit deferrals
      with named future tests.
- [ ] UI and AI agents use public API, generated SDK, or Action Types instead of
      directly calling databases or vendor SDKs.

## 2. Common Definition of Done

Each sprint below is complete only when all relevant items are true.

- [ ] The normal path works through a public facade, API, CLI, SDK, or UI surface.
- [ ] Application code does not import concrete vendor SDKs directly.
- [ ] New boundaries have ports and adapter contract tests.
- [ ] Source-of-truth and projection boundaries are documented and tested.
- [ ] Tenant scope, permission, and masking rules are applied.
- [ ] The sprint has the needed proof classes: adapter contract, normal path,
      failure injection, concurrency/race, retry/idempotency, partial success,
      recovery cleanup, composition compatibility, operator evidence, and docs sync.
- [ ] A focused command is added to `package.json` and wired into the right
      `scripts/ci_gate.sh` lane.
- [ ] JSON and Markdown artifacts are produced where a quality/runtime gate is
      added.
- [ ] Failure output names the contract, missing evidence, likely root cause, and
      suggested files.
- [ ] `docs/implementation-status.md`, `docs/sprint-evidence-ledger.md`, and
      the relevant matrix/checklist docs are updated in the same change.

### 2.1 Proof Class And CI Lane Contract

S46 turns this table into machine-readable gate data through
`docs/data-engineering-pattern-matrix.json` and the semantic documentation gate.
Keep this table as the review contract for every later roadmap PR.

| Proof class | What it proves | Default lane | Required operator evidence |
|---|---|---|---|
| `source-of-truth` | DB committed version, run state, outbox/audit, or declared external state remains the serving truth; projections cannot silently become truth. | runtime | Artifact or summary names the source-of-truth rule and the projection that tried to drift. |
| `adapter-contract` | A new vendor/profile implementation obeys the same port contract as local/fake. | runtime | Typed adapter failure payload includes kind, retryability, timeout/idempotency context, and operator message. |
| `normal-path` | The feature works through public facade/API/CLI/SDK/UI, not a private repository shortcut. | runtime | Run/audit/transaction payload carries tenant, actor, request id, and output identity. |
| `failure-injection` | The riskiest commit point fails safely when storage/catalog/network/DB/action side effect breaks. | runtime | Failure is visible in run/error/transaction/audit/outbox/trace payloads, not only logs. |
| `concurrency-race` | Parallel writers/workers/rebalances cannot corrupt source-of-truth or produce stale last-writer-wins output. | runtime | Winning/losing actor and conflict reason are durable enough for an operator to explain. |
| `retry-idempotency` | Retrying after timeout/unknown/duplicate delivery creates one logical success or a clear conflict. | runtime | Idempotency key, request fingerprint, cursor/offset/watermark, or external correlation id is recorded. |
| `partial-success-cleanup` | A half-written object/file/snapshot/run cannot become serving state or orphaned silent debt. | runtime | Cleanup result or orphan evidence is recorded with the failed run/transaction. |
| `composition-compatibility` | The new feature still works with every already-active infra family and active stack. | runtime for focused composition, release for expensive/cloud variants | Summary names every active stack combination tested or explicitly deferred. |
| `performance-scale` | Larger data sizes do not break the contract under production-like limits. | release | Uploaded artifact includes profile, row/object counts, duration, and failing threshold. |
| `staging-cloud-chaos` | Real cluster/cloud/failover behavior matches local/Testcontainers proof. | release | Uploaded artifact includes environment, injected fault, recovery result, and residual risk. |
| `docs-sync` | README/status/sprint/risk/checklist/matrix docs say the same thing as code and gates. | static/runtime depending on gate | Failure message points to the conflicting sentence and owning doc. |

Operator-evidence rule: if a developer has to inspect only console logs to
understand a dangerous failure, the proof is incomplete. The payload must leave
enough durable evidence for a PR reviewer, GitHub Actions summary, or on-call
operator to identify the root cause and the file/contract likely responsible.

## 3. Sprint Order

| Sprint | Priority | Outcome | Depends on | Status |
|---|---:|---|---|---|
| S46 | P0 | Semantic SSOT + Data Pattern Matrix | Current CI harness | Complete |
| S47 | P0 | Record DLQ + Replay | S46 | Partial |
| S48 | P1 | Late Data + Watermark | S47 | Partial |
| S49 | P1 | Multi-file Dataset + Partitioning | S46 | Partial |
| S50 | P1 | Iceberg Maintenance | S49 | Partial |
| S51 | P0 | Continuous CDC Worker + Rebalance Safety | S47 | Partial |
| S52 | P0 | Temporal Engine Integration | S51 | Partial |
| S53 | P0 | External Writeback + Saga/Reconciliation | S52 | Partial |
| S54 | P1 | Data Quality Contracts | S47, S48 | Partial |
| S55 | P1 | DB/Dataset/Ontology Schema Migration | S54 | Partial |
| S56 | P1 | Proactive Observability + SLO | S48, S51, S52 | Partial |
| S57 | P0 | Backup/Restore Commit-point Ratchet | S50, S52, S53 | Partial |
| S58A | P1 | OIDC/JWT + Secret Provider | Independent | Partial |
| S58B | P1 | Anonymization/Pseudonymization | S58A | Partial |
| S58C | P1 | Right-to-Erasure Lifecycle | S50, S57, S58B | Partial |
| S59 | P2 | Real Cluster/Cloud/Chaos Proofs | Related sprint families | Proposed |
| S60 | P1 | Fine-grained Lineage + AI Evidence | S54, S55 | Partial |
| S61 | Product | Frontend Foundation + Generated SDK | Current API | Partial |
| S62 | Product | Object/Dataset Explorer | S61 | Proposed |
| S63 | Product | Insight/Action Workspace | S61, S53, S60 | Proposed |
| S64 | Product | Operations/Recovery Console | S47, S51, S52, S56, S57 | Proposed |

Recommended first execution path:

```text
S46 Semantic SSOT
-> S47 Record DLQ
-> S48 Late Data
-> S51 Continuous CDC
-> S52 Temporal Engine Integration
-> S53 External Saga
```

Scale path:

```text
S49 Multi-file Dataset
-> S50 Iceberg Maintenance
-> S57 Backup/Restore
```

Product surface path:

```text
S61 Frontend Foundation
-> S62 Object/Dataset Explorer
-> S63 Insight/Action Workspace
-> S64 Operations/Recovery Console
```

## 4. Sprint 46 - Semantic SSOT And Data Engineering Pattern Matrix

### Goal

Prevent existing docs from disagreeing about what is current, partial, deferred,
or not applicable. Also track data-engineering design patterns as machine-readable
state so a future PR cannot claim a pattern is covered without evidence.

### Scope

Semantic documentation consistency:

- [x] Use `docs/infra-tricky-matrix.json` active families as one input source.
- [x] Check that `docs/implementation-status.md` describes every active family
      as current evidence.
- [x] Fail CI if an active family is described as future without a clear scope
      qualifier.
- [x] Fail CI if a deferred feature is described as implemented.
- [x] Compare README capability tables with matrix/status documents.

Data engineering pattern matrix:

- [x] Add `docs/data-engineering-pattern-matrix.json`.
- [x] Restrict status values to `enforced`, `partial`, `deferred`, and
      `not-applicable`.
- [x] Require `reason`, `riskTier`, `owner`, `futureTests`, and `owningDoc` for
      every deferred item.
- [x] Require a clear rationale for every not-applicable item.
- [x] Reference existing infra family IDs instead of duplicating the infra
      ratchet matrix.

Proof level:

- [x] Add proof levels: L0 registered gap, L1 unit, L2 deterministic integration, L3 live
      Testcontainers, L4 active-stack composition, L5 staging/cloud, and
      L6 failover/chaos.
- [x] Document what `active-covered` means in terms of proof level.

`active-covered` means the repo has focused adapter/runtime proof and durable
operator/source-of-truth evidence for the declared local or Testcontainers
profile. It does not mean managed cloud operations, real cluster failover, or
L5/L6 chaos proof is complete. In this roadmap's proof scale, a single active
infra family must reach at least L3, while active-stack composition proof is L4.

### Commands

```text
quality:semantic-doc-consistency
quality:data-pattern-matrix
```

### Tests

- [x] `test_semantic_docs_reject_active_infra_described_as_future`
- [x] `test_implementation_status_lists_every_active_matrix_family`
- [x] `test_readme_does_not_claim_deferred_feature_as_active`
- [x] `test_readme_must_mention_deferred_pattern_alias`
- [x] `test_non_readme_doc_cannot_claim_deferred_feature_as_active`
- [x] `test_data_pattern_matrix_requires_active_covered_meaning`
- [x] `test_data_pattern_matrix_requires_readme_alias_for_deferred`
- [x] `test_data_pattern_matrix_requires_reason_for_not_applicable`
- [x] `test_data_pattern_matrix_requires_future_test_for_deferred`
- [x] `test_proof_level_is_valid_and_monotonic`

### Done

- [x] Current/future/partial wording agrees across README, development plan,
      sprint breakdown, implementation status, infra ratchet, and matrix docs.
- [x] A new data-engineering pattern gap cannot merge without being registered
      in the matrix.
- [x] CI output points to the conflicting sentence and the document to edit.

## 5. Later Sprint Intake Summary

S47 Record DLQ + Replay: partial. Stream CDC archive now quarantines bad input
records into tenant-scoped `dead_letter_records` without stopping valid records,
audits `dead_letter_record.quarantined`, and fails closed if DLQ storage fails.
Operations API, typed generated SDK, and Web Operations UI coverage now support
list/detail, status filtering, single and bulk replay with idempotency keys,
discard, downstream backfill impact preview, replay result materialization,
source-side error-threshold fail-closed policy, identity/ordering fail-closed
policy, PostgreSQL concurrent replay request proof, and replay/discard audit
evidence. Transform-level Record DLQ policy remains future work.

S48 Late Data + Watermark: partial. Stream archive now separates event time,
source time, ingest time, and process time in committed rows, declares
source-specific event-time/source-time fields, named timezone override, and
lateness thresholds through `StreamArchiveConfig`, routes too-late records to Record DLQ, keeps
partition/source watermark metadata from moving backward without letting a fast
partition watermark classify a slower partition as stale, and treats duplicate
delivery after a committed late event as idempotent by resuming from the durable
stream offset. Stream commits also persist `lateDataSummary` and
`lateDataReprocessingPlan`, and Operations run detail exposes that evidence for
operator follow-up. CDC index runs now preserve `LATE_REQUIRES_REPROCESS`
evidence in their cursor, and the next `order_current` materialization commit
stores `materializationDetail.watermark` plus `reopen.reason=late_data_reprocess`
so Operations materialization detail shows which previous output was regenerated.
Object explain and materialization detail now expose `lateDataBadge`, and
Operations run detail exposes `downstreamImpact` roots, badges, and affected run
links for the regenerated materialization. Late stale CDC deletes are skipped
rather than deleting or resurrecting the current active object. Transform-level
Record DLQ policy remains future work.

S49 Multi-file Dataset + Partitioning: partial. Dataset storage adapters now
resolve all manifest-listed data files through `data_file_paths()`, and
`foundry.datasets.preview(...)` reads those files in manifest order without
bucket/directory listing. Local/fake/S3 preserve manifest-listed file identity
and reduce actual reads when a `partition_filter` matches file-level
`partition_values`; dataset rows can declare `partition_spec`, `sort_order`,
and `target_file_size_bytes`. Iceberg keeps its snapshot materialization
contract and skips reads when no manifest file matches, so S49 Iceberg-backed snapshot file-level partition pruning remains future work. Multi-part
commit atomicity, transform predicate pushdown, and high-cardinality partition
warnings also remain future work.

S50 Iceberg Maintenance: partial. Operations can now create an Iceberg
maintenance plan that reports compaction candidates, orphan snapshots, protected
DB committed-version snapshots, and retained snapshots, with audit evidence.
Actual compaction rewrite, snapshot expiration, orphan cleanup execution, and
run-state modeling remain future work.

S51 Continuous CDC Worker + Rebalance Safety: partial. The stream archive worker
now has a bounded continuous mode that repeatedly uses the existing
`archive_stream_events` transaction/checkpoint boundary, stops on configured
empty polls or a stop callback, and reports loop summary counts. CDC object-index
continuous workers, lease/fencing, rebalance revoke safety, and commit-unknown
reconciliation remain future work.

S52 Temporal Engine Integration: partial. `ConnectorSyncWorkflow` can now be
started and queried through Operations facade/API/generated SDK, with stable
idempotency-key workflow ids and an audit event that links `workflowRunId` to
`foundryRunId`. Local/fake adapters and the Temporal time-skipping worker expose
the same `ProductWorkflowRun` public contract. The actual connector page
fetch/staging/commit/cursor-advance activity chain, cancellation cleanup,
activity response-loss reconciliation, continue-as-new, workflow upgrade replay,
and managed Temporal worker operations remain future work.

S53 External Writeback + Saga/Reconciliation: partial. The first slices keep the
existing simulated before-commit writeback boundary but stop treating response
loss/timeout as ordinary failure, model simulated external success followed by
local mutation failure as `compensation_required`, and provide an
operator-provided remote-success resolve path that catches up the local object
mutation and marks action/writeback evidence as `reconciled`. `action_runs`,
`action_writebacks`, and audit evidence now preserve `outcome_unknown`,
`compensation_required`, `reconciled`, `external_operation_id`,
`idempotency_key`, `request_hash`, `remote_resource_id`,
`last_observed_status`, `compensation_action_type`, and
`reconciliation_deadline`. Sensitive action parameters remain available to apply
the reconciliation mutation, but Operations/audit evidence masks them. Same-key
replay returns the existing unresolved run instead of issuing a second writeback,
and concurrent reconciliation has one winner. Real vendor writeback, vendor remote lookup, compensation worker
execution, persistent reconciliation queue, approval UI, and full saga worker
execution remain future work.

S54 Data Quality Contracts: partial. The first slices keep the existing dataset
quality check boundary but record exactly which staged candidate fingerprint and
which `dataset_schemas` row/version each `dataset_check_results` row was
validated against. `quality:data-contracts` proves the dataset commit path and
dataset quality repository contract preserve `checked_manifest_hash`,
`validated_against_schema_version_id`, and `validated_against_schema_version`.
The same gate proves historical check results stay pinned to the schema
row/version used by that run after a later dataset commit creates a new schema
version.
The same gate proves a staged candidate changed after quality checks is rejected
before storage commit, records successful check rows as `PASS`, keeps warning
severity failures visible as non-blocking `WARN`, and surfaces commit-time hard
failures as `BLOCK_COMMIT` evidence. The same lane now proves row-level
`not_null`/`unique` quarantine checks route failed records to tenant-scoped Record
DLQ with `DATA_QUALITY_CONTRACT`, rewrite the staged candidate to clean records,
and revalidate that cleaned candidate before commit. Operations run detail now
exposes a transaction-scoped `quality` report with summary, schema references,
checked manifest hashes, check results, and up to 5 failed-row samples sourced
from data-quality Record DLQ rows. Full `DataContract` CRUD, owner/policy
surfaces, dedicated failed-row sample UI, quality history/trend, owner
notification, and production DB schema-race proof remain future work.

S55 DB/Dataset/Ontology Schema Migration: partial. The first slices graduate
the schema revision guard into Alembic migration-history and migration-runner
safety gates: `quality:schema-migrations` proves the migration chain has one
root/head, revision ids match filenames, destructive `downgrade()` schema
operations are blocked, and rollback policy is forward-fix/restore-runbook-first
rather than silent table drops. The same gate now requires `migration_phase` and
`release_compatibility`, keeps expand migrations in the `old_and_new_app` window
to compatible add/table/index/backfill SQL, blocks default-less new `NOT NULL`
columns for old-writer safety, rejects phase/window mismatch, and rejects contract
cleanup until old-writer/release-window proof exists. `db:migrate` plus
`quality:schema-migration-runner` prove Alembic runs through a dedicated
singleton DB lock instead of app/worker startup races, and failed migration
attempts leave password-masked operator evidence with revision, lock, and error
details. Fresh-DB Alembic parity and schema revision fingerprints remain active.
Dataset schema evolution is now partial as well: `quality:schema-evolution`
checks rename/drop/type-narrowing/primary-key-change blocking evidence,
records numeric widening and deprecated-field consumer warnings, and creates
deterministic backfill progress metadata for later backfill workers. Ontology
migration planning is now partial through `quality:ontology-migrations`: active
ontology and candidate YAML are compared before activation, property/object/link
and action-parameter breaking changes are blocked with generated SDK
compatibility evidence, and object reindex plans are written into activation
audit/outbox payloads. Live PostgreSQL contention proof, full old/new app
compatibility windows, actual ontology migration executors/reindex workers,
actual backfill execution/progress APIs, and rollback/restore runbooks remain
future work.

S56 Proactive Observability + SLO: partial. The first slice adds
`quality:observability-detectors` and `quality:slo-contracts`. A read-only
detector report compares a versioned config with the current
`RuntimeRunSnapshot`, detects missing-data flow interruption even when no run is
`FAILED`, separates source event time from processing time for lag evidence,
ignores expected seasonal partitions for skew detection, emits SLO breaches with
run/dataset references, suppresses duplicate alerts during cooldown, and exposes
the report through `POST /api/operations/observability/detect` plus generated
SDK `operations.observability.detect(...)`. Stored incident lifecycle,
notification delivery, dashboard/timeline UI, broker-offset and REST-cursor lag
adapters, object-key skew, and persistent threshold registry remain future work.

S57 Backup/Restore Commit-point Ratchet: partial. The current slices add
`quality:backup-restore`, `POST /api/operations/backup-restore/preflight`,
`POST /api/operations/backup-restore/restore-mode/start`,
`GET /api/operations/backup-restore/restore-mode/{restore_id}`,
`POST /api/operations/backup-restore/restore-mode/{restore_id}/approve-resume`,
and generated SDK `operations.backupRestore.*` calls. The report checks tenant active
datasets and committed versions against storage manifests/data files, marks
DB/storage point mismatches as `blocked`, captures active object index pointers,
action/outbox/audit/materialization high-watermarks, Temporal restore strategy, and search
projection rebuild markers, and leaves audit evidence. Restore mode start/status
now records `is_serving_traffic_open=false`, keeps the outbox publisher paused in the
operator-facing status, blocks outbox dead-letter retry/reprocess entry while
restore mode is active, and is idempotent per `restoreId`. Resume approval now
requires post-restore dataset inventory, object index pointer, action run, and
materialization run evidence before writing `resume_approved` audit evidence and
reopening the current outbox retry/reprocess entrypoints. Actual backup artifact
creation, platform-wide write traffic enforcement, real publisher daemon
pause/resume execution, automatic restore smoke execution, and Kubernetes
packaging remain future work.

S58A Security and Privacy: partial. The current slices add `SecretProvider`,
`EnvSecretProvider`, `JwtOidcAuthProvider`, `quality:auth-secrets`, adapter
failure taxonomy coverage, and route webhook signing key lookup through
`foundry.secret_provider` while keeping secret values out of repr/operator
evidence/missing-secret errors. JWT/OIDC auth now supports local discovery JSON,
RS256 bearer-token signature/issuer/audience/expiry validation, tenant/subject/
roles claim mapping, tenant-scoped M2M service-account tokens, local revoked
JWT ID denylist, and JWKS refresh-on-unknown-`kid` while keeping cached keys
valid during rotation. REST connector auth config can now use
SecretProvider-backed bearer/header secretRefs and re-resolve them on each
snapshot so a rotated local env secret is used by the next connector call. Live
OIDC discovery fetch, JWKS URI polling/TTL/key retirement, service-account
registry/scope policy, IdP introspection, refresh-token revocation, IdP
group-to-role policy, cloud/Vault secret managers, full connector workflow
credential refresh, previous/current dual-read retry, and full secret rotation
lifecycle remain future work.

S58B/S58C Privacy and Erasure: partial. The current privacy slice adds
`PrivacyTransformPlan`, `PrivacyFieldRule`, `transform_privacy_rows`, and
`quality:privacy` for tenant-scoped deterministic pseudonymization, field-level
anonymization, local regex PII redaction for text samples, versioned replay
lineage metadata, protected in-memory reversible mapping proof that keeps raw
values out of transformed rows, lineage, and redacted evidence, and
production-to-nonprod `PrivacyReplicationPolicy` proof that blocks missing or
incomplete privacy transforms for sensitive fields. `PrivacyDatasetRef` and
`build_privacy_openlineage_event` add source/target dataset version lineage and
raw-value-free OpenLineage-compatible privacy event artifacts. The current erasure
slice adds `ErasureRequest`, `resolve_erasure_subject`, `ErasureManifest`, and
`quality:erasure` for tenant-scoped subject resolution, stable idempotent erasure
manifests, serving-surface action planning, backup-retention pending state,
audit-minimization evidence, and search rebuild exclusion proof without raw subject
values. Durable environment replication workflow, durable/encrypted reversible
mapping backend, runtime `lineage_edges`/outbox/OpenLineage transport integration,
approval workflow, durable erasure request table/API/workflow, and right-to-erasure
executors across serving surfaces and retention boundaries remain future work.

S59 Real Cluster/Cloud/Chaos Proofs: separate Testcontainers/local proof from
staging/cloud/failover/chaos proof.

S60 Fine-grained Lineage + AI Evidence: the first slice extends object explain
with `propertyLineage` coordinates and adds `EvidenceReference`,
`EvidenceSourceSpan`, `build_insight_claim_payload`,
`build_llm_extraction_evidence`, and `revise_evidence_reference` for pinned
source dataset/object versions, extractor/model/prompt versions, source span
redaction, human review status, and immutable evidence revisions. Durable
insight storage, real LLM extraction executors, source-span viewer UI, and
model-change diff UI remain future work.

S61-S64 Product Surface Track: connect generated SDK and typed errors to
Object/Dataset Explorer, Insight/Action Workspace, and Operations/Recovery
Console without bypassing permission, idempotency, or evidence rules.

S61 Frontend Foundation + Generated SDK: the first slice extends generated SDK
outputs with `FoundryLiteApiError`, `createRequestId`, `requestContextHeaders`,
`normalizeFoundryLiteError`, `isRetryableFoundryLiteError`, and a public SDK
`request` wrapper. The Web Operations console now routes its remaining generic
API calls through that SDK wrapper and displays the latest request id, error
code, and retryability. `quality:frontend-foundation` keeps the SDK/browser
surface and static Web Operations contract aligned. Login/session UI, automatic
retry/backoff, cursor helper, duplicate-click locking, stale-version conflict UI,
and permission-denied masking UX remain future work.

## 6. PR Exit Checklist For This Roadmap

- [ ] One PR activates one major ratchet only.
- [ ] PR body has Root Cause, Impact, and Regression Tests.
- [ ] A public surface proves the normal path.
- [ ] The riskiest commit point has failure injection.
- [ ] Concurrency/race and retry/idempotency tests exist where relevant.
- [ ] Partial-success cleanup is asserted.
- [ ] Operator evidence payload is asserted.
- [ ] Source of truth is explicit.
- [ ] Fallbacks that change meaning are visible as degraded or hard failure.
- [ ] Tenant, permission, and masking tests exist.
- [ ] Generated SDK/OpenAPI changes are checked when API shape changes.
- [ ] Focused quality command is wired into runtime or release lane.
- [ ] JSON/Markdown/GitHub summary artifacts exist when a new gate is added.
- [ ] Tricky checklist, risk register, implementation status, and sprint ledger
      are updated together.
- [ ] Unreproduced distributed failures remain as scope notes with named future
      tests.
