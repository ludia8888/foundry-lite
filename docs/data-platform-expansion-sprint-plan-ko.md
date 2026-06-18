# Foundry-lite 데이터 플랫폼 확장 스프린트 플랜

**문서 상태:** Proposed / 구현 전 계획  
**기준일:** 2026-06-18  
**대상 저장소:** `ludia8888/foundry-lite`  
**목표:** 현재의 강한 정합성·멱등성·커밋 안전성 코어를 유지하면서, 데이터 엔지니어링 패턴의 폭과 실제 제품 UI를 단계적으로 확장한다.  
**입력 근거:** `Data Engineering Design Patterns`, `docs/infra-tricky-matrix.json`, `docs/infra-ratchet.md`, `docs/foundry_lite_tricky_failure_modes_checklist.md`, `docs/implementation-status.md`, `docs/commit-point-risk-register.md`, `docs/quality-gate-roadmap.md`, `docs/quality-observability.md`, `docs/sprint-evidence-ledger.md`.

> Repo integration note: 이 문서는 다운로드 폴더의 확장 PRD를 repo 안에 보존한 **상세 proposed plan**이다. 현재 구현 완료 여부의 source of truth는 [Implementation Status](./implementation-status.md), [Sprint Evidence Ledger](./sprint-evidence-ledger.md), [Infra Ratchet](./infra-ratchet.md), [Infra Tricky Matrix](./infra-tricky-matrix.json)다. S46 이후 항목은 실제 코드, 테스트, CI gate, operator evidence, ledger row가 같은 PR에서 생기기 전까지 `[ ]` 상태를 유지한다.
>
> 비개발자식으로 말하면, 이 문서는 “앞으로 어디까지 확장할지 적은 실행 설계서”이고, “이미 다 되었다는 영수증”은 아니다. 영수증 역할은 evidence ledger와 CI gate가 한다.

---

## 0. 프로그램 결론

현재 Foundry-lite는 다음 코어가 이미 강하다.

- [x] S3/MinIO 저장 정합성
- [x] Iceberg snapshot/version pinning
- [x] Spark transform과 output abort
- [x] Debezium CDC archive/object indexing
- [x] Object Store, Action, Materialization 폐루프
- [x] Temporal WorkflowAdapter 기본 의미론
- [x] Elasticsearch rebuildable projection
- [x] proof matrix / source-of-truth / operator-evidence CI 하네스

다음 확장은 무작정 기능을 늘리는 방식이 아니라 아래 순서로 진행한다.

```text
패턴과 위험 정의
→ commit point 정의
→ spike
→ port/adapter 또는 domain model
→ 정상 경로
→ 실패 주입
→ 동시성
→ 멱등성
→ partial-success/cleanup
→ operator evidence
→ active stack 조합
→ CI/docs 연결
→ merge
```

---

# 1. 프로그램 불변식

모든 스프린트는 다음 불변식을 보존해야 한다.

- [ ] cursor/offset/watermark는 durable commit보다 먼저 전진하지 않는다.
- [ ] retry는 두 번째 논리적 성공을 만들지 않는다.
- [ ] DB `COMMITTED` dataset version이 serving truth다.
- [ ] S3 object, Iceberg snapshot, Elasticsearch, UI/SDK cache는 단독 source of truth가 아니다.
- [ ] run 시작 시 입력 version과 logical watermark를 고정한다.
- [ ] 외부 timeout은 자동으로 `FAILED`라고 단정하지 않는다.
- [ ] 외부 성공 + local 실패는 compensation/reconciliation 대상으로 남긴다.
- [ ] 실패는 로그에만 존재하면 안 된다.
- [ ] 모든 실패는 run/error/transaction/audit/outbox/trace 중 적절한 durable payload에 남는다.
- [ ] fake-only proof로 production readiness를 주장하지 않는다.
- [ ] 재현하지 못한 분산 실패는 명시적 deferral로 남긴다.
- [ ] UI와 AI Agent는 DB나 vendor SDK를 직접 호출하지 않고 public API/SDK/Action Type을 사용한다.

---

# 2. 공통 Definition of Done

각 스프린트는 아래 항목을 모두 충족해야 완료다.

## 2.1 기능 및 아키텍처

- [ ] public facade/API/CLI/SDK 중 해당 표면을 통해 정상 경로가 동작한다.
- [ ] application layer가 concrete vendor SDK를 직접 import하지 않는다.
- [ ] 새 boundary가 있다면 port와 adapter contract test가 존재한다.
- [ ] source of truth와 projection의 경계가 문서와 테스트에 명시된다.
- [ ] tenant scope, permission, masking 규칙이 적용된다.

## 2.2 Ratchet proof class

- [ ] `adapter-contract`
- [ ] `normal-path`
- [ ] `failure-injection`
- [ ] `concurrency-race`
- [ ] `retry-idempotency`
- [ ] `partial-success`
- [ ] `recovery-cleanup`
- [ ] `composition-compatibility`
- [ ] `operator-evidence`
- [ ] `docs-sync`

## 2.3 CI 및 증거

- [ ] focused command가 `package.json`에 추가된다.
- [ ] focused command가 `scripts/ci_gate.sh`의 적절한 lane에 연결된다.
- [ ] runtime과 release lane의 역할을 구분한다.
- [ ] JSON artifact를 생성한다.
- [ ] Markdown summary artifact를 생성한다.
- [ ] `$GITHUB_STEP_SUMMARY`에 root-cause-style 결과를 출력한다.
- [ ] 실패 메시지가 contract, missing evidence, likely root cause, suggested files를 포함한다.
- [ ] `docs/infra-tricky-matrix.json` 또는 pattern matrix에 proof가 연결된다.
- [ ] tricky checklist, risk register, implementation status, sprint evidence ledger를 함께 갱신한다.

---

# 3. 전체 스프린트 순서

| Sprint | 우선순위 | 핵심 결과 | 주요 의존성 | 상태 |
|---|---:|---|---|---|
| S46 | P0 | Semantic SSOT + Data Pattern Matrix | 현재 CI 하네스 | [ ] |
| S47 | P0 | Record DLQ + Replay | S46 | [ ] |
| S48 | P1 | Late Data + Watermark | S47 | [ ] |
| S49 | P1 | Multi-file Dataset + Partitioning | S46 | [ ] |
| S50 | P1 | Iceberg Maintenance | S49 | [ ] |
| S51 | P0 | Continuous CDC Worker + Rebalance Safety | S47 | [ ] |
| S52 | P0 | Temporal Engine Integration | S51 | [ ] |
| S53 | P0 | External Writeback + Saga/Reconciliation | S52 | [ ] |
| S54 | P1 | Data Quality Contracts | S47, S48 | [ ] |
| S55 | P1 | DB/Dataset/Ontology Schema Migration | S54 | [ ] |
| S56 | P1 | Proactive Observability + SLO | S48, S51, S52 | [ ] |
| S57 | P0 | Backup/Restore Commit-point Ratchet | S50, S52, S53 | [ ] |
| S58A | P1 | OIDC/JWT + Secret Provider | 독립 가능 | [ ] |
| S58B | P1 | Anonymization/Pseudonymization | S58A | [ ] |
| S58C | P1 | Right-to-Erasure Lifecycle | S50, S57, S58B | [ ] |
| S59 | P2 | Real Cluster/Cloud/Chaos Proofs | 관련 모든 sprint | [ ] |
| S60 | P1 | Fine-grained Lineage + AI Evidence | S54, S55 | [ ] |
| S61 | Product | Frontend Foundation + Generated SDK | 현재 API | [ ] |
| S62 | Product | Object/Dataset Explorer | S61 | [ ] |
| S63 | Product | Insight/Action Workspace | S61, S53, S60 | [ ] |
| S64 | Product | Operations/Recovery Console | S47, S51, S52, S56, S57 | [ ] |

---

# Sprint 46 — Semantic SSOT와 Data Engineering Pattern Matrix

## 목표

문서마다 서로 다른 현재 상태를 주장하지 못하게 하고, 책의 데이터 엔지니어링 패턴을 machine-readable 상태로 관리한다.

## 사용자 가치

- 개발자는 어떤 기능이 진짜 구현됐고 어떤 기능이 deferred인지 즉시 안다.
- AI 코딩 에이전트가 잘못된 문서를 근거로 구현하지 않는다.
- PR 리뷰에서 “문서에는 완료인데 실제로는 future” 같은 오판을 막는다.

## 범위

### A. Semantic documentation consistency

- [ ] `infra-tricky-matrix.json`의 active family를 기준으로 문서 상태를 검증한다.
- [ ] `implementation-status.md`가 모든 active family를 현재 기능으로 설명하는지 검사한다.
- [ ] active family를 future라고 서술하면 CI를 실패시킨다.
- [ ] deferred 기능을 구현 완료라고 서술하면 CI를 실패시킨다.
- [ ] README capability table과 matrix 상태를 비교한다.

### B. Data engineering pattern matrix

- [ ] `docs/data-engineering-pattern-matrix.json`을 추가한다.
- [ ] 상태를 `enforced | partial | deferred | not-applicable`로 제한한다.
- [ ] 모든 `deferred` 항목에 reason, riskTier, owner, futureTests, owningDoc를 요구한다.
- [ ] 모든 `not-applicable` 항목에 적용하지 않는 이유를 요구한다.
- [ ] 기존 infra matrix를 복제하지 않고 infra family ID를 참조한다.

### C. Proof level

- [ ] 각 proof에 level을 붙인다.
  - [ ] L1 unit
  - [ ] L2 deterministic integration
  - [ ] L3 live Testcontainers
  - [ ] L4 active-stack composition
  - [ ] L5 staging/cloud
  - [ ] L6 failover/chaos
- [ ] `active-covered`가 어떤 proof level까지 의미하는지 문서화한다.

## 제안 명령

```text
quality:semantic-doc-consistency
quality:data-pattern-matrix
```

## 제안 테스트

- [ ] `test_semantic_docs_reject_active_infra_described_as_future`
- [ ] `test_implementation_status_lists_every_active_matrix_family`
- [ ] `test_readme_does_not_claim_deferred_feature_as_active`
- [ ] `test_data_pattern_matrix_requires_reason_for_not_applicable`
- [ ] `test_data_pattern_matrix_requires_future_test_for_deferred`
- [ ] `test_proof_level_is_valid_and_monotonic`

## 완료 기준

- [ ] 동일 기능의 current/future 상태가 모든 문서에서 일치한다.
- [ ] 새로운 패턴 gap은 matrix에 등록되지 않으면 merge할 수 없다.
- [ ] CI summary가 충돌 문장과 수정할 문서를 정확히 알려준다.

---

# Sprint 47 — Record DLQ + Replay Ratchet

## 목표

처리 불가능한 입력 데이터 한 건 때문에 전체 파이프라인이 멈추거나, 문제 record가 조용히 유실되는 일을 막는다.

## 핵심 구분

```text
Outbox DLQ = 나가는 이벤트 전송 실패
Record DLQ = 들어온 데이터 한 건 자체가 처리 불가
```

## 도메인 모델

- [ ] `DeadLetterRecord` 모델을 추가한다.
- [ ] 상태를 정의한다.
  - [ ] `QUARANTINED`
  - [ ] `REPLAY_REQUESTED`
  - [ ] `REPLAYING`
  - [ ] `RESOLVED`
  - [ ] `DISCARDED`
- [ ] 원본 payload는 inline 또는 immutable raw payload URI로 보존한다.
- [ ] 다음 필드를 저장한다.
  - [ ] `tenant_id`
  - [ ] `source_event_id`
  - [ ] `source_dataset_version_id`
  - [ ] `source_run_id`
  - [ ] `raw_payload_uri` 또는 payload
  - [ ] `payload_hash`
  - [ ] `schema_version`
  - [ ] `transform_version`
  - [ ] `error_kind`
  - [ ] `error_message`
  - [ ] `event_time`
  - [ ] `ingested_at`
  - [ ] `first_failed_at`
  - [ ] `attempts`
  - [ ] `replay_status`
  - [ ] `replay_run_id`
  - [ ] `affects_closed_partition`

## 처리 정책

- [ ] 오류 정책을 source/transform별로 선언 가능하게 한다.
- [ ] 소수 오류는 quarantine하고 정상 record를 계속 처리한다.
- [ ] 오류율이 threshold를 넘으면 전체 batch를 fail-closed한다.
- [ ] identity/primary-key/ordering 오류는 기본적으로 fail-closed한다.
- [ ] DLQ 저장 자체가 실패하면 main pipeline을 성공 처리하지 않는다.
- [ ] replay된 record에는 origin DLQ ID와 replay metadata를 남긴다.
- [ ] replay가 downstream backfill을 요구하는지 계산한다.

## API/SDK

- [ ] `GET /api/operations/dead-letter-records`
- [ ] `GET /api/operations/dead-letter-records/{id}`
- [ ] `POST /api/operations/dead-letter-records/{id}/retry`
- [ ] `POST /api/operations/dead-letter-records/bulk-retry`
- [ ] `POST /api/operations/dead-letter-records/{id}/discard`
- [ ] generated TypeScript SDK에 typed DLQ operations를 추가한다.

## 운영 UI

- [ ] Record DLQ 목록
- [ ] source/error/status 필터
- [ ] 원본 payload와 실패 이유 비교
- [ ] replay 영향 범위 미리보기
- [ ] 단건/일괄 재처리
- [ ] 해결률·오류율·top error kind 차트

## Operator evidence

- [ ] record quarantine run evidence
- [ ] DLQ write failure evidence
- [ ] replay request audit
- [ ] replay result and downstream impact
- [ ] `request_id`, `run_id`, `dataset_version_id`, `record_dlq_id` 연결

## 제안 명령

```text
quality:record-dlq
quality:record-dlq-replay
```

## 제안 테스트

- [ ] `test_one_poison_record_does_not_stop_valid_records`
- [ ] `test_error_ratio_above_threshold_fails_batch`
- [ ] `test_identity_error_fails_closed`
- [ ] `test_dead_letter_payload_is_replayable`
- [ ] `test_replayed_record_is_marked_with_origin`
- [ ] `test_replay_emits_downstream_backfill_plan`
- [ ] `test_dlq_store_failure_fails_main_pipeline`
- [ ] `test_record_dlq_is_tenant_scoped_and_masked`
- [ ] `test_concurrent_replay_requests_create_one_replay_run`
- [ ] `test_same_replay_idempotency_key_returns_existing_result`

## 완료 기준

- [ ] 정상 record와 문제 record가 명확히 분리된다.
- [ ] 문제 record가 조용히 삭제되지 않는다.
- [ ] replay가 중복 logical output을 만들지 않는다.
- [ ] Operations에서 원인과 재처리 결과를 확인할 수 있다.

---

# Sprint 48 — Late Data + Watermark Ratchet

## 목표

늦게 도착한 소셜/API/stream 이벤트를 조용히 버리거나 현재 상태를 잘못 되돌리는 문제를 막는다.

## 시간 모델

- [ ] 모든 event에 가능한 경우 아래 시간을 분리한다.
  - [ ] `event_time`
  - [ ] `source_time`
  - [ ] `ingested_at`
  - [ ] `processed_at`
- [ ] source별 event-time field를 선언한다.
- [ ] source별 timezone과 clock-skew 정책을 선언한다.

## 정책 모델

```yaml
lateData:
  eventTimeField: published_at
  allowedLateness: 48h
  tooLateAfter: 30d
  onLate: integrate_and_recompute
  onTooLate: dead_letter
```

- [ ] 상태를 정의한다.
  - [ ] `ON_TIME`
  - [ ] `LATE_ACCEPTED`
  - [ ] `LATE_REQUIRES_REPROCESS`
  - [ ] `TOO_LATE`
- [ ] watermark는 partition/source별로 저장한다.
- [ ] watermark는 뒤로 이동하지 않는다.
- [ ] 늦은 데이터가 닫힌 output을 바꾸면 reprocessing plan을 생성한다.
- [ ] too-late event는 Record DLQ와 연결한다.

## API/UI

- [ ] dataset/run detail에 event-time lag를 표시한다.
- [ ] 객체/인사이트에 late-data badge를 표시한다.
- [ ] materialization detail에 watermark와 reopen 여부를 표시한다.
- [ ] 늦은 데이터로 인해 영향받는 downstream을 표시한다.

## 제안 명령

```text
quality:late-data
quality:watermark
```

## 제안 테스트

- [ ] `test_watermark_never_moves_backward`
- [ ] `test_slow_partition_does_not_silently_drop_data`
- [ ] `test_late_event_reopens_expected_materialization`
- [ ] `test_too_late_event_goes_to_record_dlq`
- [ ] `test_event_time_and_processing_time_sla_are_separate`
- [ ] `test_duplicate_late_event_is_idempotent`
- [ ] `test_late_delete_does_not_resurrect_stale_object`

## 완료 기준

- [ ] 늦은 데이터가 정책에 따라 처리되고 이유가 보인다.
- [ ] 이미 닫힌 데이터셋을 바꿀 때 downstream 영향이 추적된다.
- [ ] 늦은 데이터 때문에 현재 객체가 과거 상태로 회귀하지 않는다.

---

# Sprint 49 — Multi-file Dataset + Partitioning Ratchet

## 목표

단일 Parquet 파일 중심 protocol을 multi-file manifest 기반으로 확장해 대용량 데이터의 병렬 처리와 partition pruning을 가능하게 한다.

## Manifest v2

- [ ] dataset version이 여러 data file을 참조할 수 있게 한다.
- [ ] file별 metadata를 저장한다.
  - [ ] `partition_values`
  - [ ] `row_count`
  - [ ] `byte_size`
  - [ ] `content_hash`
  - [ ] column min/max
  - [ ] null count
  - [ ] sort bounds
- [ ] reader가 bucket listing이 아니라 manifest만 사용한다.
- [ ] 기존 single-file manifest와 backward compatibility를 제공한다.

## Layout policy

- [ ] dataset에 `partition_spec`을 선언한다.
- [ ] `sort_order`를 선언한다.
- [ ] target file size를 선언한다.
- [ ] high-cardinality partition을 validation에서 경고한다.
- [ ] query/transform가 partition predicate를 storage adapter에 전달한다.

## Commit protocol

```text
여러 staged file 생성
→ file hash/metadata 검증
→ manifest 생성
→ DB version/file rows 원자 commit
→ COMMITTED serving pointer 노출
```

- [ ] part 하나 실패 시 전체 version을 abort한다.
- [ ] retry는 동일 logical version의 file set을 중복 생성하지 않는다.
- [ ] cleanup은 reachable file을 삭제하지 않는다.

## 제안 명령

```text
quality:multi-file-dataset
quality:partition-pruning
```

## 제안 테스트

- [ ] `test_multi_file_commit_is_all_or_nothing`
- [ ] `test_one_part_upload_failure_exposes_no_version`
- [ ] `test_manifest_lists_exact_committed_files`
- [ ] `test_reader_never_lists_bucket_to_discover_version`
- [ ] `test_partition_pruning_reads_only_required_files`
- [ ] `test_same_version_retry_does_not_duplicate_parts`
- [ ] `test_single_file_versions_remain_readable`
- [ ] `test_concurrent_multi_file_commits_allocate_unique_versions`

## 완료 기준

- [ ] 기존 API는 유지된다.
- [ ] 단일 파일과 multi-file dataset을 같은 facade로 읽는다.
- [ ] partition predicate가 실제 read file 수를 줄인다.

---

# Sprint 50 — Iceberg Maintenance Ratchet

## 목표

작은 파일 누적, 오래된 snapshot, orphan object를 안전하게 관리한다.

## 기능

- [ ] compaction candidate detector
- [ ] `rewrite_data_files` 실행기
- [ ] snapshot expiration policy
- [ ] orphan file cleanup
- [ ] maintenance run model
- [ ] retention policy

## 기본 정책

```text
average_file_size < 32 MiB
또는 file_count > threshold
또는 read_amplification > threshold
→ compaction candidate
```

## 안전 조건

- [ ] pinned transform input snapshot 삭제 금지
- [ ] materialization watermark가 참조하는 snapshot 삭제 금지
- [ ] backup retention보다 이른 snapshot 삭제 금지
- [ ] active DB COMMITTED version이 참조하는 metadata 삭제 금지
- [ ] 실패한 compaction이 serving pointer를 바꾸지 않음

## API/UI

- [ ] `GET /api/operations/maintenance/iceberg`
- [ ] `POST /api/operations/maintenance/iceberg/{dataset}/plan`
- [ ] `POST /api/operations/maintenance/iceberg/{dataset}/run`
- [ ] 예상 절감 용량, 대상 파일, 보존 snapshot을 preview한다.

## 제안 명령

```text
quality:iceberg-maintenance
```

## 제안 테스트

- [ ] `test_compaction_preserves_row_hash`
- [ ] `test_old_pinned_snapshot_remains_readable_within_retention`
- [ ] `test_expiration_refuses_referenced_snapshot`
- [ ] `test_compaction_failure_does_not_move_serving_pointer`
- [ ] `test_orphan_cleanup_never_deletes_reachable_file`
- [ ] `test_concurrent_maintenance_runs_have_one_winner`
- [ ] `test_retry_after_ambiguous_compaction_is_idempotent`

## 완료 기준

- [ ] maintenance 결과가 run/operator evidence로 남는다.
- [ ] time travel/restore에 필요한 snapshot이 보존된다.
- [ ] compaction 전후 logical row hash가 동일하다.

---

# Sprint 51 — Continuous CDC Worker + Rebalance Safety

## 목표

현재 one-shot proof를 실제 지속 실행 worker로 확장하고, rebalance/commit-unknown에서 데이터 유실과 중복 logical commit을 막는다.

## Worker 기능

- [ ] continuously running stream archive worker
- [ ] continuously running CDC object-index worker
- [ ] graceful shutdown
- [ ] heartbeat/lease
- [ ] partition assignment tracking
- [ ] backpressure
- [ ] retry budget
- [ ] lag reporting

## Checkpoint protocol

- [ ] partition별 checkpoint
- [ ] batch transaction ID
- [ ] source epoch/fencing token
- [ ] revoke 이후 old worker의 commit 금지
- [ ] commit-unknown reconciliation
- [ ] topic/partition/offset event dedupe

## 제안 명령

```text
quality:cdc-continuous-worker
quality:kafka-rebalance
```

## 제안 테스트

- [ ] `test_rebalance_during_dataset_commit_has_no_loss`
- [ ] `test_commit_unknown_replay_creates_one_dataset_version`
- [ ] `test_revoked_worker_cannot_advance_checkpoint`
- [ ] `test_multi_partition_batch_does_not_hide_partial_failure`
- [ ] `test_worker_sigterm_finishes_or_aborts_current_batch`
- [ ] `test_worker_lease_expiry_fences_stale_instance`
- [ ] `test_continuous_indexer_restarts_from_committed_watermark`

## 완료 기준

- [ ] worker restart/rebalance 후 no-gap/no-duplicate logical result가 증명된다.
- [ ] lag와 assignment가 Operations에 보인다.
- [ ] timeout/commit-unknown이 generic failure로 뭉개지지 않는다.

---

# Sprint 52 — Temporal Engine Integration

## 목표

Temporal을 독립 adapter에서 실제 Foundry-lite 업무 오케스트레이터로 승격한다.

## 1차 workflow

### ConnectorSyncWorkflow

- [ ] fetch page
- [ ] raw staging write
- [ ] quality check
- [ ] dataset commit
- [ ] cursor advance
- [ ] `dataset.version.committed` emit

### 후속 workflow

- [ ] TransformWorkflow
- [ ] ObjectReindexWorkflow
- [ ] MaterializationWorkflow
- [ ] OutboxDeliveryWorkflow
- [ ] BackfillWorkflow

## 의미론

- [ ] workflow ID는 stable idempotency key 기반
- [ ] activity ID와 domain transaction ID 연결
- [ ] workflow history와 Foundry run evidence 연결
- [ ] cancel 시 staging cleanup
- [ ] activity 완료 후 응답 유실 reconciliation
- [ ] bounded retry 정책
- [ ] continue-as-new
- [ ] workflow code versioning
- [ ] replay determinism

## API/UI

- [ ] workflow start endpoint
- [ ] workflow status endpoint
- [ ] cancel/retry/reconcile action
- [ ] progress, current activity, next retry, failure classification 표시

## 제안 명령

```text
quality:temporal-engine-integration
```

## 제안 테스트

- [ ] `test_connector_sync_workflow_commits_then_advances_cursor`
- [ ] `test_worker_crash_mid_activity_replays_without_duplicate_commit`
- [ ] `test_activity_completion_response_loss_reattaches_to_domain_result`
- [ ] `test_cancel_cleans_staging_and_persists_cancel_evidence`
- [ ] `test_continue_as_new_preserves_business_idempotency`
- [ ] `test_workflow_upgrade_replays_old_history`
- [ ] `test_temporal_and_foundry_run_ids_are_bidirectionally_linked`

## 완료 기준

- [ ] 최소 하나의 실제 product workflow가 Temporal을 통해 실행된다.
- [ ] 기존 local profile과 Temporal profile의 public contract가 같다.
- [ ] Operations에서 Temporal 내부 로그 없이 조사할 수 있다.

---

# Sprint 53 — External Writeback + Saga/Reconciliation

## 목표

AI Agent와 Action Type이 CRM/ERP/Slack/캠페인 시스템을 안전하게 움직이도록 한다.

## 상태 모델

- [ ] `SUCCEEDED`
- [ ] `FAILED`
- [ ] `RETRYABLE`
- [ ] `OUTCOME_UNKNOWN`
- [ ] `COMPENSATION_REQUIRED`
- [ ] `RECONCILING`
- [ ] `RECONCILED`

## 데이터 모델

- [ ] `external_operation_id`
- [ ] `idempotency_key`
- [ ] `request_hash`
- [ ] `remote_resource_id`
- [ ] `attempts`
- [ ] `last_observed_status`
- [ ] `compensation_action_type`
- [ ] `reconciliation_deadline`
- [ ] before/after evidence

## 동작

- [ ] 외부 write는 outbox 또는 workflow activity로만 실행한다.
- [ ] timeout을 곧바로 실패로 처리하지 않는다.
- [ ] remote lookup/reconciliation path를 제공한다.
- [ ] compensation action은 idempotent하다.
- [ ] high-risk action에는 human approval을 요구한다.
- [ ] AI Agent가 직접 vendor API를 호출하지 못하게 한다.

## API/UI

- [ ] reconciliation queue
- [ ] compensation approval
- [ ] remote/local state 비교
- [ ] operator action history

## 제안 명령

```text
quality:external-writeback
quality:saga-reconciliation
```

## 제안 테스트

- [ ] `test_external_success_response_lost_becomes_outcome_unknown`
- [ ] `test_outcome_unknown_is_not_blindly_retried`
- [ ] `test_external_success_local_failure_requires_compensation`
- [ ] `test_compensation_is_idempotent`
- [ ] `test_reconciliation_resolves_remote_success`
- [ ] `test_concurrent_reconciliation_has_one_winner`
- [ ] `test_sensitive_writeback_payload_is_masked_in_audit`

## 완료 기준

- [ ] 외부 시스템과 local state divergence가 first-class 상태로 보인다.
- [ ] operator가 추측 없이 reconcile/compensate할 수 있다.
- [ ] AI Agent 권한은 Action Type과 approval policy로 제한된다.

---

# Sprint 54 — Data Quality Contract

## 목표

데이터 품질을 코드 안의 개별 validation이 아니라 versioned product contract로 관리한다.

## 모델

- [ ] `DataContract`
- [ ] contract version
- [ ] owner
- [ ] schema
- [ ] primary key
- [ ] nullability
- [ ] accepted values
- [ ] referential constraints
- [ ] freshness
- [ ] expected volume
- [ ] duplicate rate
- [ ] late-data policy
- [ ] severity

## 결과 상태

- [ ] `PASS`
- [ ] `WARN`
- [ ] `QUARANTINE`
- [ ] `BLOCK_COMMIT`

## 기존 위험 보강

- [ ] `checked_manifest_hash`를 저장한다.
- [ ] final commit 직전에 candidate hash를 재검증한다.
- [ ] `validated_against_schema_version_id`를 저장한다.
- [ ] schema version 변경 race를 production DB에서 증명한다.

## API/UI

- [ ] contract CRUD/validate
- [ ] candidate quality report
- [ ] failed row sample
- [ ] quality history/trend
- [ ] owner notification

## 제안 명령

```text
quality:data-contracts
quality:data-quality-runtime
```

## 제안 테스트

- [ ] `test_quality_check_pins_candidate_manifest_hash`
- [ ] `test_candidate_tamper_between_check_and_commit_is_rejected`
- [ ] `test_schema_validation_records_reference_version`
- [ ] `test_warn_does_not_block_commit_but_is_visible`
- [ ] `test_quarantine_routes_bad_records_to_record_dlq`
- [ ] `test_block_commit_exposes_no_dataset_version`
- [ ] `test_contract_version_is_pinned_to_run`

## 완료 기준

- [ ] 데이터셋마다 품질 기대치와 owner가 보인다.
- [ ] 품질 실패 정책이 명시적이며 조용히 유실하지 않는다.
- [ ] downstream은 contract-failed version을 정상으로 소비하지 않는다.

---

# Sprint 55 — DB/Dataset/Ontology Schema Migration

## 목표

현재 schema snapshot guard를 실제 운영 migration 체계로 발전시킨다.

## DB migration

- [ ] Alembic 도입
- [ ] migration singleton lock
- [ ] expand-contract 규칙
- [ ] forward-fix 우선 정책
- [ ] release compatibility window
- [ ] rollback/restore runbook

## Dataset schema evolution

- [ ] rename
- [ ] split/merge field
- [ ] type widening
- [ ] deprecated field
- [ ] consumer compatibility
- [ ] backfill progress

## Ontology migration

- [ ] property rename/deprecation
- [ ] Object Type migration
- [ ] Link Type migration
- [ ] Action parameter compatibility
- [ ] Object reindex plan
- [ ] generated SDK compatibility/versioning

## 제안 명령

```text
quality:schema-migrations
quality:ontology-migrations
```

## 제안 테스트

- [ ] `test_old_app_reads_expand_phase_schema`
- [ ] `test_migration_is_singleton`
- [ ] `test_backfill_resumes_idempotently`
- [ ] `test_contract_phase_rejects_old_writer`
- [ ] `test_ontology_mapping_migrates_with_dataset_schema`
- [ ] `test_sdk_breaking_change_requires_major_version`
- [ ] `test_failed_migration_leaves_operator_evidence`

## 완료 기준

- [ ] DB 변경을 `create_all`에 의존하지 않는다.
- [ ] ontology/schema 변경의 영향과 backfill이 merge 전에 보인다.
- [ ] old/new app version 공존 구간이 테스트된다.

---

# Sprint 56 — Proactive Observability + SLO

## 목표

실패가 난 뒤 설명하는 것을 넘어, 데이터가 멈추거나 느려지거나 치우치는 상태를 먼저 감지한다.

## Detector

### Flow Interruption

- [ ] expected cadence
- [ ] last successful event/version
- [ ] missing-data alert

### Lag

- [ ] source event time ↔ processing time
- [ ] broker latest offset ↔ committed offset
- [ ] REST cursor observation lag

### Skew

- [ ] partition size skew
- [ ] file size skew
- [ ] tenant/object key skew

### SLA/SLO

- [ ] raw ingest SLO
- [ ] clean dataset SLO
- [ ] object index SLO
- [ ] workflow/action SLO

## Alert policy

- [ ] dedupe
- [ ] cooldown
- [ ] severity
- [ ] owner routing
- [ ] seasonality/baseline
- [ ] alert evidence links

## API/UI

- [ ] SLO dashboard
- [ ] active incidents
- [ ] source health timeline
- [ ] run/dataset/object drill-down

## 제안 명령

```text
quality:observability-detectors
quality:slo-contracts
```

## 제안 테스트

- [ ] `test_missing_data_triggers_flow_interruption`
- [ ] `test_lag_alert_uses_event_and_processing_time_separately`
- [ ] `test_skew_detector_ignores_expected_seasonality`
- [ ] `test_sla_alert_carries_run_and_dataset_refs`
- [ ] `test_alert_dedup_prevents_alarm_storm`
- [ ] `test_detector_failure_does_not_mutate_source_of_truth`

## 완료 기준

- [ ] “FAILED run이 없음에도 데이터가 멈춘 상태”를 탐지한다.
- [ ] alert에서 관련 run/dataset/worker로 바로 이동할 수 있다.
- [ ] threshold와 owner가 versioned configuration으로 남는다.

---

# Sprint 57 — Backup/Restore Commit-point Ratchet

## 목표

PostgreSQL, S3/Iceberg, Object Store, outbox/action 상태를 일관된 시점으로 복구한다.

## Backup manifest

- [ ] DB backup ID/time
- [ ] dataset committed version inventory
- [ ] S3/Iceberg manifest inventory
- [ ] active object index pointers
- [ ] action/outbox/audit high-watermarks
- [ ] Temporal namespace/history strategy
- [ ] Elasticsearch rebuild marker

## Restore mode

- [ ] write traffic 차단
- [ ] outbox publisher 일시 중지
- [ ] committed manifest 전수 검증
- [ ] missing/corrupt version report
- [ ] search projection rebuild
- [ ] post-restore closed-loop validation
- [ ] operator approval 후 publisher resume

## 제안 명령

```text
quality:backup-restore
```

## 제안 테스트

- [ ] `test_restore_rejects_db_storage_point_mismatch`
- [ ] `test_restore_validates_every_committed_manifest`
- [ ] `test_restore_pauses_outbox_until_reconciliation`
- [ ] `test_search_is_rebuilt_not_restored_as_truth`
- [ ] `test_post_restore_closed_loop_smoke`
- [ ] `test_restore_retry_is_idempotent`
- [ ] `test_restore_failure_never_opens_serving_traffic`

## 완료 기준

- [ ] DB만 또는 storage만 복구한 상태를 serving하지 않는다.
- [ ] 외부 side effect 중복 전송을 방지한다.
- [ ] 복구 후 데이터셋→오브젝트→액션 폐루프 smoke가 통과한다.

---

# Sprint 58A — OIDC/JWT + Secret Provider

## 목표

개발용 header trust를 넘어 실제 production identity와 secret lifecycle을 제공한다.

## Auth

- [ ] OIDC discovery
- [ ] JWT signature/issuer/audience 검증
- [ ] JWKS rotation
- [ ] service account/M2M
- [ ] group/role mapping
- [ ] session revocation policy

## Secrets

- [ ] `SecretProvider` port
- [ ] local environment adapter
- [ ] Vault/cloud secret manager adapter
- [ ] secret version/rotation
- [ ] long-running connector credential refresh
- [ ] secret value가 logs/audit/error에 나타나지 않음

## 제안 테스트

- [ ] `test_expired_or_wrong_audience_jwt_is_denied`
- [ ] `test_jwks_rotation_keeps_valid_sessions`
- [ ] `test_service_account_is_tenant_scoped`
- [ ] `test_connector_refreshes_rotated_secret`
- [ ] `test_secret_never_appears_in_operator_evidence`

---

# Sprint 58B — Anonymization/Pseudonymization

## 목표

production 데이터를 staging, 분석, AI 실험에 안전하게 사용할 수 있게 한다.

- [ ] deterministic pseudonymization
- [ ] irreversible anonymization
- [ ] raw text PII detection/redaction
- [ ] per-property privacy policy
- [ ] environment replication policy
- [ ] reversible mapping은 별도 protected store에 저장
- [ ] anonymized dataset lineage 유지

## 제안 테스트

- [ ] `test_same_identifier_maps_to_same_pseudonym_within_scope`
- [ ] `test_pseudonym_differs_across_tenants`
- [ ] `test_anonymized_dataset_contains_no_raw_pii_samples`
- [ ] `test_privacy_transform_is_versioned_and_replayable`

---

# Sprint 58C — Right-to-Erasure Lifecycle

## 목표

삭제 요청이 raw/clean/object/search/materialization/DLQ/backup 정책 전체에 전파되게 한다.

- [ ] deletion request object
- [ ] subject identity resolution
- [ ] deletion manifest
- [ ] Object Store tombstone
- [ ] Search removal
- [ ] Materialized dataset handling
- [ ] Iceberg snapshot/retention policy
- [ ] DLQ 처리
- [ ] audit 최소 보존 정책
- [ ] backup expiration/crypto-shredding 정책

## 제안 테스트

- [ ] `test_erasure_removes_subject_from_serving_surfaces`
- [ ] `test_erasure_does_not_delete_other_tenant_data`
- [ ] `test_erasure_retry_is_idempotent`
- [ ] `test_backup_retention_exposes_pending_erasure_state`
- [ ] `test_search_rebuild_does_not_resurrect_erased_subject`

---

# Sprint 59 — Real Cluster/Cloud/Chaos Proofs

## 목표

Testcontainers/local proof를 실제 분산 환경과 managed service 조건으로 확장한다.

## S59A — AWS S3

- [ ] SlowDown/throttling
- [ ] IAM drift
- [ ] KMS rotation
- [ ] lifecycle policy conflict
- [ ] region/network fault
- [ ] object lock

## S59B — Spark Cluster

- [ ] executor loss
- [ ] driver crash
- [ ] speculative execution double-write
- [ ] shuffle loss
- [ ] dynamic allocation
- [ ] cancel timeout

## S59C — Temporal HA

- [ ] worker crash mid-activity
- [ ] server failover
- [ ] workflow code upgrade/replay
- [ ] signal/query race
- [ ] continue-as-new history control

## S59D — Managed Elasticsearch

- [ ] shard relocation
- [ ] red/yellow health
- [ ] mapping migration
- [ ] ILM
- [ ] snapshot/restore
- [ ] rolling upgrade
- [ ] capacity exhaustion

## CI lane

- [ ] `staging-canary`
- [ ] `nightly-soak`
- [ ] `weekly-chaos`
- [ ] cloud 비용 제한과 자동 cleanup

## 완료 기준

- [ ] fake/Testcontainers proof와 cloud proof level을 명확히 구분한다.
- [ ] 실패 시 cloud/staging artifact와 operator evidence가 남는다.
- [ ] chaos 실패를 product correctness failure로 오판하지 않는다.

---

# Sprint 60 — Fine-grained Lineage + AI Evidence

## 목표

AI Agent와 온글림 인사이트가 “왜 이런 판단을 했는가”를 property/claim 단위로 설명할 수 있게 한다.

## Lineage level

- [ ] output column ← input columns
- [ ] object property ← dataset column/expression
- [ ] link ← source key mapping
- [ ] insight claim ← evidence object IDs
- [ ] LLM extraction ← source spans
- [ ] prompt/model/version/parameters
- [ ] confidence and reviewer decision

## Evidence model

- [ ] immutable evidence reference
- [ ] source dataset version
- [ ] source object version
- [ ] extractor version
- [ ] model/prompt version
- [ ] evidence quote/timecode/bounding box
- [ ] human review status

## API/UI

- [ ] `GET /api/objects/{type}/{id}?explain=true` 확장
- [ ] property별 “왜 이 값인가” 보기
- [ ] insight claim evidence viewer
- [ ] LLM 모델 변경 전후 비교

## 제안 테스트

- [ ] `test_object_property_lineage_resolves_to_pinned_dataset_version`
- [ ] `test_insight_claim_requires_evidence_objects`
- [ ] `test_llm_extraction_pins_prompt_and_model_version`
- [ ] `test_reprocessing_preserves_old_evidence_and_creates_new_revision`
- [ ] `test_masked_source_span_is_not_exposed_to_unauthorized_user`

## 완료 기준

- [ ] 인사이트에서 원본 게시글/댓글/영상 구간까지 내려갈 수 있다.
- [ ] AI가 근거 없는 action을 자동 실행하지 못한다.
- [ ] model/prompt 변경이 실제 시장 변화처럼 보이지 않게 구분한다.

---

# 4. Product Surface Track

현재 API와 generated SDK를 제품 화면으로 연결하는 병렬 트랙이다. 플랫폼 ratchet과 동일하게 권한, 상태, idempotency, operator evidence를 보존한다.

---

# Sprint 61 — Frontend Foundation + Generated SDK

## 목표

프론트가 raw `fetch` 대신 generated SDK와 공통 상태 모델로 모든 API를 호출하게 한다.

## 기반

- [ ] authenticated API client
- [ ] tenant/user context
- [ ] request ID 표시
- [ ] typed error taxonomy
- [ ] retryable/non-retryable UX
- [ ] idempotency key helper
- [ ] expected object version helper
- [ ] cursor pagination helper
- [ ] loading/empty/error/degraded state components

## API 문서

- [ ] `/docs`와 `/openapi.json` 접근 문서화
- [ ] SDK regeneration CI 유지
- [ ] frontend API compatibility test

## 테스트

- [ ] generated SDK surface parity
- [ ] request ID visible on errors
- [ ] same action click does not duplicate result
- [ ] stale object version shows conflict UI
- [ ] permission denied does not leak sensitive fields

---

# Sprint 62 — Object/Dataset Explorer

## Object Explorer

- [ ] Object Type 선택
- [ ] 객체 목록/query/filter/sort
- [ ] cursor pagination
- [ ] 객체 상세
- [ ] links traversal
- [ ] source run/evidence
- [ ] stale projection badge
- [ ] permission/masking UI

## Object Sets

- [ ] 저장된 세그먼트 목록
- [ ] static/dynamic set 생성
- [ ] private/shared 표시
- [ ] expiry 표시

## Dataset Explorer

- [ ] namespace/name 탐색
- [ ] version 목록
- [ ] preview
- [ ] raw/clean/ops 구분
- [ ] version pin 표시
- [ ] lineage 이동

## 테스트

- [ ] object query shape/cursor 유지
- [ ] link target missing warning 표시
- [ ] dataset version 변경 시 preview pin 유지
- [ ] masked property가 filter UI에 노출되지 않음

---

# Sprint 63 — Insight/Action Workspace

## 목표

AI/분석 결과를 검토하고 허용된 Action Type으로 실제 업무를 실행한다.

## 화면

- [ ] Insight review queue
- [ ] evidence panel
- [ ] approve/reject/assign
- [ ] Action Type 목록
- [ ] ontology metadata 기반 action form
- [ ] human approval policy
- [ ] action result and audit
- [ ] long-running workflow progress
- [ ] writeback reconciliation 상태

## 안전 UX

- [ ] idempotency key 자동 생성/재사용
- [ ] expected object version 전달
- [ ] precondition failure 설명
- [ ] `OUTCOME_UNKNOWN`을 성공/실패로 오표시하지 않음
- [ ] high-risk action confirmation
- [ ] retryable action과 compensation action 구분

## 테스트

- [ ] double click produces one action run
- [ ] stale object conflicts before mutation
- [ ] unauthorized action is hidden and denied server-side
- [ ] action evidence links to object/source data
- [ ] outcome unknown enters reconciliation queue

---

# Sprint 64 — Operations/Recovery Console

## 목표

운영자가 DB/버킷/로그를 직접 열지 않고 장애를 조사하고 복구한다.

## Run Console

- [ ] sync/transform/index/action/materialization/workflow 목록
- [ ] status/time/type filters
- [ ] run detail
- [ ] normalized error
- [ ] likely root cause
- [ ] suggested actions
- [ ] related audit/outbox/lineage/object edit

## Recovery

- [ ] transform retry
- [ ] index replay
- [ ] Record DLQ retry
- [ ] Outbox DLQ retry
- [ ] workflow cancel/reconcile
- [ ] compaction/maintenance run
- [ ] restore mode status

## Monitoring

- [ ] `/healthz`
- [ ] `/metrics`
- [ ] lag/SLO/skew dashboards
- [ ] active incident list
- [ ] alert dedupe/cooldown state

## 테스트

- [ ] retry uses original pinned versions
- [ ] replay does not duplicate logical output
- [ ] operator evidence is enough without raw logs
- [ ] unauthorized operator cannot retry another tenant run
- [ ] root-cause links point to existing run/resources

---

# 5. 새 API 후보 목록

아래는 기존 endpoint 위에 추가될 가능성이 높은 API다.

## Record DLQ

```text
GET  /api/operations/dead-letter-records
GET  /api/operations/dead-letter-records/{id}
POST /api/operations/dead-letter-records/{id}/retry
POST /api/operations/dead-letter-records/bulk-retry
POST /api/operations/dead-letter-records/{id}/discard
```

## Late data / Quality

```text
GET  /api/data-quality/contracts
POST /api/data-quality/contracts
POST /api/data-quality/contracts/{id}/validate
GET  /api/data-quality/runs/{id}
GET  /api/operations/late-data
```

## Temporal / Workflow

```text
POST /api/workflows/{workflow_name}/start
GET  /api/workflows/runs/{id}
POST /api/workflows/runs/{id}/cancel
POST /api/workflows/runs/{id}/reconcile
```

## Writeback / Saga

```text
GET  /api/operations/reconciliation
GET  /api/operations/reconciliation/{id}
POST /api/operations/reconciliation/{id}/retry
POST /api/operations/reconciliation/{id}/compensate
POST /api/operations/reconciliation/{id}/resolve
```

## Maintenance / Backup

```text
GET  /api/operations/maintenance/iceberg
POST /api/operations/maintenance/iceberg/{dataset}/plan
POST /api/operations/maintenance/iceberg/{dataset}/run
POST /api/operations/backups
POST /api/operations/restores
GET  /api/operations/restores/{id}
```

---

# 6. Program-level 위험과 방어

## 위험 1 — CI가 지나치게 느려짐

- [ ] PR lane에는 focused proof만 유지한다.
- [ ] cloud/chaos/soak는 별도 schedule lane으로 분리한다.
- [ ] 동일 테스트 중복 실행을 matrix에서 감지한다.
- [ ] 실패 artifact는 항상 업로드한다.

## 위험 2 — Matrix가 또 다른 문서 부채가 됨

- [ ] 문서와 matrix 양방향 consistency gate를 둔다.
- [ ] 가능한 표는 matrix에서 생성한다.
- [ ] owner와 stale date를 기록한다.

## 위험 3 — DLQ가 데이터 쓰레기통이 됨

- [ ] retention과 owner를 강제한다.
- [ ] unresolved age SLO를 둔다.
- [ ] error ratio가 높으면 source를 fail-closed한다.
- [ ] 반복 오류는 schema/data contract incident로 승격한다.

## 위험 4 — Retry가 비용과 중복을 키움

- [ ] 모든 retry에 max attempts/time budget을 요구한다.
- [ ] idempotency key 없는 외부 mutation을 금지한다.
- [ ] outcome unknown은 blind retry하지 않는다.

## 위험 5 — AI Agent가 너무 강한 권한을 가짐

- [ ] AI는 Object Query와 Action Type만 사용한다.
- [ ] 외부 writeback은 approval/policy를 통과한다.
- [ ] destructive action은 기본 금지한다.
- [ ] evidence 없는 insight/action은 자동 실행하지 않는다.

---

# 7. 모든 PR 공통 Exit Checklist

- [ ] 한 PR에서 하나의 주요 ratchet만 active로 만든다.
- [ ] Root Cause, Impact, Regression Tests 섹션이 있다.
- [ ] public surface 정상 경로가 있다.
- [ ] 가장 위험한 commit point에 failure injection이 있다.
- [ ] concurrency/race test가 있다.
- [ ] retry/idempotency test가 있다.
- [ ] partial-success cleanup이 있다.
- [ ] operator evidence payload가 assert된다.
- [ ] source of truth가 명확하다.
- [ ] fallback이 의미를 바꾸면 degraded 또는 hard failure로 보인다.
- [ ] tenant/permission/masking test가 있다.
- [ ] generated SDK/OpenAPI 변화가 검증된다.
- [ ] focused quality command가 runtime/release lane에 연결된다.
- [ ] JSON/Markdown/GitHub Step Summary가 생성된다.
- [ ] tricky checklist와 risk register가 업데이트된다.
- [ ] implementation status의 current/future 문구가 정확하다.
- [ ] sprint evidence ledger에 PR/commit/test/gate 증거가 추가된다.
- [ ] 재현하지 못한 분산 실패는 scope note와 future test name으로 남긴다.

---

# 8. 최종 프로그램 완료 조건

이 프로그램은 아래 상태가 되었을 때 완료로 본다.

- [ ] 잘못된 record가 조용히 유실되지 않고 격리·재처리된다.
- [ ] late data가 event-time 정책에 따라 통합된다.
- [ ] dataset version이 multi-file/partitioned layout을 지원한다.
- [ ] Iceberg maintenance와 retention이 자동화된다.
- [ ] CDC가 continuous worker와 rebalance safety를 갖는다.
- [ ] Temporal이 실제 product workflow를 구동한다.
- [ ] 외부 writeback이 outcome unknown/Saga/reconciliation을 지원한다.
- [ ] data quality가 versioned contract로 관리된다.
- [ ] DB/Dataset/Ontology migration이 운영 가능하다.
- [ ] lag/skew/interruption/SLA가 선제 탐지된다.
- [ ] DB와 object storage의 일관된 backup/restore가 증명된다.
- [ ] production auth/secrets/privacy/erasure lifecycle이 존재한다.
- [ ] 실제 cluster/cloud failure proof가 문서화된다.
- [ ] AI/Insight가 property/claim-level evidence를 가진다.
- [ ] 프론트가 Object/Dataset/Action/Operations 표면을 generated SDK로 사용한다.
- [ ] 모든 완료 주장이 test→CI→artifact→docs로 추적된다.

---

# 9. 권장 첫 실행 순서

바로 시작할 순서는 다음과 같다.

```text
S46 Semantic SSOT
→ S47 Record DLQ
→ S48 Late Data
→ S51 Continuous CDC
→ S52 Temporal Engine Integration
→ S53 External Saga
```

scale 경로는 병렬로 다음과 같이 진행한다.

```text
S49 Multi-file Dataset
→ S50 Iceberg Maintenance
→ S57 Backup/Restore
```

제품 UI는 S61을 먼저 열고, 각 backend sprint가 완료될 때 해당 화면을 점진적으로 연결한다.

```text
S61 Frontend Foundation
→ S62 Object/Dataset Explorer
→ S63 Insight/Action Workspace
→ S64 Operations/Recovery Console
```
