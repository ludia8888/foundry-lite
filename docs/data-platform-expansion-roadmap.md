# Foundry-lite Data Platform Expansion Roadmap

**Status:** Proposed / post-MVP expansion plan  
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
| Temporal | `WorkflowAdapter` and `quality:temporal` ratchet exist, but the engine does not yet drive product workflows through Temporal. | Keep S52 as future product workflow integration. |
| Elasticsearch | Adapter/projection and live Testcontainers proof exist, but managed cloud packaging/ops are future. | Keep search as rebuildable projection; do not call managed deployment complete. |
| CDC | Archive, live Debezium proof, CDC object indexing, and active-stack composition proof exist. | Keep S51 as future continuously running worker and rebalance-safety sprint. |
| Kubernetes / backup / restore | No Helm, backup, or restore implementation files are present. | Keep S45/S57 as future operational work. |
| Record DLQ / late data / watermarks | Existing outbox/materialization DLQ and cursor guards are not a record-level DLQ. | Keep S47/S48 as future product/data correctness work. |
| Auth/privacy/erasure | Header trust and local policy exist; OIDC/JWT, secret provider, privacy lifecycle remain future. | Keep S58A-S58C as future security/privacy work. |
| Frontend | Object Explorer and generated SDK exist, but full frontend foundation/workspaces are not complete. | Keep S61-S64 as product surface track. |

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

S46 must turn this table into machine-readable gate data. Until then, it is the
review contract for every roadmap PR.

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
| S46 | P0 | Semantic SSOT + Data Pattern Matrix | Current CI harness | Proposed |
| S47 | P0 | Record DLQ + Replay | S46 | Proposed |
| S48 | P1 | Late Data + Watermark | S47 | Proposed |
| S49 | P1 | Multi-file Dataset + Partitioning | S46 | Proposed |
| S50 | P1 | Iceberg Maintenance | S49 | Proposed |
| S51 | P0 | Continuous CDC Worker + Rebalance Safety | S47 | Proposed |
| S52 | P0 | Temporal Engine Integration | S51 | Proposed |
| S53 | P0 | External Writeback + Saga/Reconciliation | S52 | Proposed |
| S54 | P1 | Data Quality Contracts | S47, S48 | Proposed |
| S55 | P1 | DB/Dataset/Ontology Schema Migration | S54 | Proposed |
| S56 | P1 | Proactive Observability + SLO | S48, S51, S52 | Proposed |
| S57 | P0 | Backup/Restore Commit-point Ratchet | S50, S52, S53 | Proposed |
| S58A | P1 | OIDC/JWT + Secret Provider | Independent | Proposed |
| S58B | P1 | Anonymization/Pseudonymization | S58A | Proposed |
| S58C | P1 | Right-to-Erasure Lifecycle | S50, S57, S58B | Proposed |
| S59 | P2 | Real Cluster/Cloud/Chaos Proofs | Related sprint families | Proposed |
| S60 | P1 | Fine-grained Lineage + AI Evidence | S54, S55 | Proposed |
| S61 | Product | Frontend Foundation + Generated SDK | Current API | Proposed |
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

- [ ] Use `docs/infra-tricky-matrix.json` active families as one input source.
- [ ] Check that `docs/implementation-status.md` describes every active family
      as current evidence.
- [ ] Fail CI if an active family is described as future without a clear scope
      qualifier.
- [ ] Fail CI if a deferred feature is described as implemented.
- [ ] Compare README capability tables with matrix/status documents.

Data engineering pattern matrix:

- [ ] Add `docs/data-engineering-pattern-matrix.json`.
- [ ] Restrict status values to `enforced`, `partial`, `deferred`, and
      `not-applicable`.
- [ ] Require `reason`, `riskTier`, `owner`, `futureTests`, and `owningDoc` for
      every deferred item.
- [ ] Require a clear rationale for every not-applicable item.
- [ ] Reference existing infra family IDs instead of duplicating the infra
      ratchet matrix.

Proof level:

- [ ] Add proof levels: L1 unit, L2 deterministic integration, L3 live
      Testcontainers, L4 active-stack composition, L5 staging/cloud, and
      L6 failover/chaos.
- [ ] Document what `active-covered` means in terms of proof level.

### Proposed Commands

```text
quality:semantic-doc-consistency
quality:data-pattern-matrix
```

### Proposed Tests

- [ ] `test_semantic_docs_reject_active_infra_described_as_future`
- [ ] `test_implementation_status_lists_every_active_matrix_family`
- [ ] `test_readme_does_not_claim_deferred_feature_as_active`
- [ ] `test_data_pattern_matrix_requires_reason_for_not_applicable`
- [ ] `test_data_pattern_matrix_requires_future_test_for_deferred`
- [ ] `test_proof_level_is_valid_and_monotonic`

### Done

- [ ] Current/future/partial wording agrees across README, development plan,
      sprint breakdown, implementation status, infra ratchet, and matrix docs.
- [ ] A new data-engineering pattern gap cannot merge without being registered
      in the matrix.
- [ ] CI output points to the conflicting sentence and the document to edit.

## 5. Later Sprint Intake Summary

S47 Record DLQ + Replay: quarantine bad input records without stopping valid
records, then replay them idempotently with tenant/masking/operator evidence.

S48 Late Data + Watermark: separate event time, source time, ingest time, and
process time; never move watermarks backward; route too-late records to Record
DLQ.

S49 Multi-file Dataset + Partitioning: move from one-file dataset versions to a
manifest of committed data files with partition metadata and pruning evidence.

S50 Iceberg Maintenance: add compaction, snapshot retention, and orphan cleanup
without deleting pinned or committed truth.

S51 Continuous CDC Worker + Rebalance Safety: turn one-shot CDC proof into
always-running workers with checkpoint fencing, rebalance safety, and
commit-unknown reconciliation.

S52 Temporal Engine Integration: make at least one real product workflow execute
through Temporal while preserving the existing public contract.

S53 External Writeback + Saga/Reconciliation: model external outcome unknown,
compensation required, and reconciliation as first-class operator states.

S54 Data Quality Contracts: make data quality versioned, owned, and policy-driven
instead of scattered validations.

S55 DB/Dataset/Ontology Schema Migration: graduate schema revision guard into an
operational migration and compatibility system.

S56 Proactive Observability + SLO: detect flow interruption, lag, skew, and SLO
violations before users discover stale data.

S57 Backup/Restore Commit-point Ratchet: restore DB, object storage, object
index pointers, audit/outbox/action state, and search projections consistently.

S58A-S58C Security and Privacy: add OIDC/JWT, secret lifecycle, anonymization,
pseudonymization, and right-to-erasure across serving surfaces and retention
boundaries.

S59 Real Cluster/Cloud/Chaos Proofs: separate Testcontainers/local proof from
staging/cloud/failover/chaos proof.

S60 Fine-grained Lineage + AI Evidence: explain object properties, insight
claims, and AI extractions at source-span/model/prompt level.

S61-S64 Product Surface Track: connect generated SDK and typed errors to
Object/Dataset Explorer, Insight/Action Workspace, and Operations/Recovery
Console without bypassing permission, idempotency, or evidence rules.

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
