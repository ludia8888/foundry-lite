# Foundry-lite 데이터 플랫폼 확장 스프린트 플랜

**문서 상태:** Repo-integrated 확장 계획 / S46 완료, S47-S64 부분 구현
**기준일:** 2026-06-19
**대상 저장소:** `ludia8888/foundry-lite`  
**목표:** 현재의 강한 정합성·멱등성·커밋 안전성 코어를 유지하면서, 데이터 엔지니어링 패턴의 폭과 실제 제품 UI를 단계적으로 확장한다.  
**입력 근거:** `Data Engineering Design Patterns`, `docs/infra-tricky-matrix.json`, `docs/infra-ratchet.md`, `docs/foundry_lite_tricky_failure_modes_checklist.md`, `docs/implementation-status.md`, `docs/commit-point-risk-register.md`, `docs/quality-gate-roadmap.md`, `docs/sprint-evidence-ledger.md`.

> Repo integration note: 이 문서는 다운로드 폴더의 확장 PRD를 repo 안에 보존한 **상세 실행 계획**이다. 현재 구현 완료 여부의 source of truth는 [Implementation Status](./implementation-status.md), [Sprint Evidence Ledger](./sprint-evidence-ledger.md), [Infra Ratchet](./infra-ratchet.md), [Infra Tricky Matrix](./infra-tricky-matrix.json)다. S46 이후 항목은 실제 코드, 테스트, CI gate, operator evidence, ledger row가 같은 변경에 생긴 범위만 `[x]` 또는 `[~]`로 표시한다.
>
> 비개발자식으로 말하면, 이 문서는 “앞으로 어디까지 확장할지 적은 실행 설계서”이고, “이미 다 되었다는 영수증”은 아니다. 영수증 역할은 evidence ledger와 CI gate가 한다.

---

## 0. 프로그램 결론

이 문서는 이전 `docs/data-platform-expansion-sprint-plan-ko.md`의 요약 roadmap 역할까지 흡수한다. 별도
roadmap 문서를 다시 만들지 않는다. S46 이후 확장 순서, 공통 Definition of Done, sprint status,
cross-check summary, PR exit checklist는 이 문서가 소유하고, 실제 current/partial/future 경계는
항상 [Implementation Status](./implementation-status.md)와 [Sprint Evidence Ledger](./sprint-evidence-ledger.md)의
증거로 확정한다.

현재 Foundry-lite는 다음 코어가 이미 강하다.

- [x] S3/MinIO 저장 정합성
- [x] Iceberg snapshot/version pinning
- [x] Spark transform과 output abort
- [x] Debezium CDC archive/object indexing
- [x] Object Store, Action, Materialization 폐루프
- [x] Temporal WorkflowAdapter 기본 의미론
- [x] Elasticsearch rebuildable projection
- [x] proof matrix / source-of-truth / operator-evidence CI 하네스

### 0.1 Roadmap Cross-Check Summary

| Area | Current evidence boundary | Merge decision |
|---|---|---|
| S3/MinIO, Iceberg, Spark | `quality:s3-storage`, `quality:iceberg`, `quality:spark`, `quality:infra-composition` prove adapter/composition ratchets. | Treat as implemented ratchet proof, not full production platform packaging. |
| Temporal | `WorkflowAdapter`, `quality:temporal`, and S52 `ConnectorSyncWorkflow` control-plane plus worker-bound connector snapshot commit proof exist. | Treat start/status/audit linking and the local connector activity commit proof as partial; keep managed workers, cancellation/reconciliation, workflow upgrade replay, and production connector packaging future. |
| External writeback / saga | S53 simulated `outcome_unknown`, `compensation_required`, reconciliation resolve, masking, replay proofs, L8 real S3/MinIO adapter timeout/landed-write/`remote_lookup` proof, and unresolved writeback backend/API/SDK queue exist. | Treat core safety semantics and backend queue as covered for the current adapter; keep ERP-specific connector packaging, autonomous compensation workers, queue UI, and approval UI future. |
| Elasticsearch | Adapter/projection/live Testcontainers proof exists. | Keep search as rebuildable projection; managed cloud packaging and ops remain future. |
| CDC | Archive, live Debezium proof, CDC object indexing, bounded stream loop, and active-stack composition proof exist. | Treat bounded/archive/indexing slices as active-covered; keep production daemon lease/fencing/rebalance/commit-unknown edges future. |
| Backup/restore | S57 preflight, restore-mode status, DB/storage mismatch detection, retry lockout, post-restore validation/approval evidence, and core platform write-traffic lockout exist. | Treat current lockout and validation as service-boundary proof; keep real backup artifact creation, publisher daemon control, and restore executor packaging future. |
| Auth/privacy/erasure | S58A/S58B/S58C local JWT/OIDC, secret provider, privacy transform, replication policy, and erasure manifest proofs exist. | Treat local proof as partial; keep cloud/Vault, durable workflows, encrypted durable stores, and full executors future. |
| Frontend | S61/S62/S63/S64 backend/API/SDK surfaces, request/helper contracts, named SDK-only Web Operations, Insight Review queue proofs, and Operations Recovery overview/post-restore validation proof exist. | Treat backend/API/SDK foundation as partial; keep full visual workspace UX, evidence panels, action orchestration, and recovery console UI future. |

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
| S46 | P0 | Semantic SSOT + Data Pattern Matrix | 현재 CI 하네스 | [x] |
| S47 | P0 | Record DLQ + Replay | S46 | [~] |
| S48 | P1 | Late Data + Watermark | S47 | [~] |
| S49 | P1 | Multi-file Dataset + Partitioning | S46 | [~] |
| S50 | P1 | Iceberg Maintenance | S49 | [~] |
| S51 | P0 | Continuous CDC Worker + Rebalance Safety | S47 | [~] |
| S52 | P0 | Temporal Engine Integration | S51 | [~] |
| S53 | P0 | External Writeback + Saga/Reconciliation | S52 | [~] |
| S54 | P1 | Data Quality Contracts | S47, S48 | [~] |
| S55 | P1 | DB/Dataset/Ontology Schema Migration | S54 | [~] |
| S56 | P1 | Proactive Observability + SLO | S48, S51, S52 | [~] |
| S57 | P0 | Backup/Restore Commit-point Ratchet | S50, S52, S53 | [~] |
| S58A | P1 | OIDC/JWT + Secret Provider | 독립 가능 | [~] |
| S58B | P1 | Anonymization/Pseudonymization | S58A | [~] |
| S58C | P1 | Right-to-Erasure Lifecycle | S50, S57, S58B | [~] |
| S59 | P2 | Real Cluster/Cloud/Chaos Proofs | 관련 모든 sprint | [ ] |
| S60 | P1 | Fine-grained Lineage + AI Evidence | S54, S55 | [~] |
| S61 | Product | Frontend Foundation + Generated SDK | 현재 API | [~] |
| S62 | Product | Object/Dataset Explorer | S61 | [~] |
| S63 | Product | Insight/Action Workspace | S61, S53, S60 | [~] |
| S64 | Product | Operations/Recovery Console | S47, S51, S52, S56, S57 | [~] |

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

- [x] `infra-tricky-matrix.json`의 active family를 기준으로 문서 상태를 검증한다.
- [x] `implementation-status.md`가 모든 active family를 현재 기능으로 설명하는지 검사한다.
- [x] active family를 future라고 서술하면 CI를 실패시킨다.
- [x] deferred 기능을 구현 완료라고 서술하면 CI를 실패시킨다.
- [x] README capability table과 matrix 상태를 비교한다.

### B. Data engineering pattern matrix

- [x] `docs/data-engineering-pattern-matrix.json`을 추가한다.
- [x] 상태를 `enforced | partial | deferred | not-applicable`로 제한한다.
- [x] 모든 `deferred` 항목에 reason, riskTier, owner, futureTests, owningDoc를 요구한다.
- [x] 모든 `not-applicable` 항목에 적용하지 않는 이유를 요구한다.
- [x] 기존 infra matrix를 복제하지 않고 infra family ID를 참조한다.

### C. Proof level

- [x] 각 proof에 level을 붙인다.
  - [x] L0 registered gap
  - [x] L1 unit
  - [x] L2 deterministic integration
  - [x] L3 live Testcontainers
  - [x] L4 active-stack composition
  - [x] L5 staging/cloud
  - [x] L6 failover/chaos
- [x] `active-covered`가 어떤 proof level까지 의미하는지 문서화한다.

## 제안 명령

```text
quality:semantic-doc-consistency
quality:data-pattern-matrix
```

## 검증 테스트

- [x] `test_s46_expansion_gates_pass_current_repo`
- [x] `test_semantic_docs_reject_active_infra_described_as_future`
- [x] `test_implementation_status_lists_every_active_matrix_family`
- [x] `test_semantic_docs_allow_qualified_future_scope`
- [x] `test_readme_does_not_claim_deferred_feature_as_active`
- [x] `test_readme_must_mention_deferred_pattern_alias`
- [x] `test_non_readme_doc_cannot_claim_deferred_feature_as_active`
- [x] `test_data_pattern_matrix_requires_active_covered_meaning`
- [x] `test_data_pattern_matrix_requires_readme_alias_for_deferred`
- [x] `test_data_pattern_matrix_requires_reason_for_not_applicable`
- [x] `test_data_pattern_matrix_requires_future_test_for_deferred`
- [x] `test_data_pattern_matrix_rejects_unknown_active_infra_ref`
- [x] `test_proof_level_is_valid_and_monotonic`

## 완료 기준

- [x] 동일 기능의 current/future 상태가 모든 문서에서 일치한다.
- [x] 새로운 패턴 gap은 matrix에 등록되지 않으면 merge할 수 없다.
- [x] CI summary가 충돌 문장과 수정할 문서를 정확히 알려준다.

---

# Sprint 47 — Record DLQ + Replay Ratchet

## 목표

처리 불가능한 입력 데이터 한 건 때문에 전체 파이프라인이 멈추거나, 문제 record가 조용히 유실되는 일을 막는다.

> 현재 부분 구현 메모 (2026-06-18): stream CDC archive 경로에는 S47 quarantine slice가
> 들어갔고, Operations API/typed generated SDK에는 record DLQ 조회, 상세, retry request,
> bulk retry request, discard가 추가됐다. bulk retry는 item-level 실패를 전체 요청 실패로
> 숨기지 않고 `status`, `failedItems`, `succeededCount`, `failedCount`를 반환하며
> `dead_letter_record.bulk_replay_item_failed` audit evidence를 남긴다. `dead_letter_records`
> table과 `DeadLetterRecord`
> port model은 replay request idempotency key, replay run id, discard timestamp, downstream
> backfill plan을 저장한다. 잘못된 CDC envelope record는 tenant-scoped DLQ로 격리되며 정상
> record commit은 계속된다. DLQ 저장이 실패하면 main pipeline을 성공 처리하지 않고 FAILED
> sync run으로 남긴다. 유효한 저장 DLQ payload는 같은 dataset transaction finalization 경계로
> 원래 raw stream archive dataset에 APPEND replay되고 `replayDatasetVersionId`, `rowCount`,
> `dead_letter_record.replayed` audit evidence를 남긴다. replay payload가 여전히 유효하지 않으면
> DLQ는 `FAILED` replay result와 `dead_letter_record.replay_failed` audit evidence를 남기고
> quarantine 상태로 유지된다. Web Operations UI는 record DLQ 상태 필터, 목록, 상세/영향
> 미리보기, 단건 replay, bulk replay, discard, open/closed/top-error 요약을 노출한다. stream/source
> archive 경로는 `StreamArchiveConfig`의 `max_record_error_ratio`와 `fail_closed_error_kinds`로
> source별 오류율 threshold와 identity/ordering fail-closed 정책을 선언할 수 있고, PostgreSQL
> 동시 replay request proof는 한 record에 replay run이 하나만 남는지 검증한다. transform-level
> Record DLQ 오류 정책은 transform record DLQ가 생길 때 별도 확장한다.

## 핵심 구분

```text
Outbox DLQ = 나가는 이벤트 전송 실패
Record DLQ = 들어온 데이터 한 건 자체가 처리 불가
```

## 도메인 모델

- [x] `DeadLetterRecord` 모델을 추가한다.
- [x] 상태를 정의한다.
  - [x] `QUARANTINED`
  - [x] `REPLAY_REQUESTED`
  - [x] `REPLAYING`
  - [x] `RESOLVED`
  - [x] `DISCARDED`
- [x] 원본 payload는 inline 또는 immutable raw payload URI로 보존한다.
- [x] 다음 필드를 저장한다.
  - [x] `tenant_id`
  - [x] `source_event_id`
  - [x] `source_dataset_version_id`
  - [x] `source_run_id`
  - [x] `raw_payload_uri` 또는 payload
  - [x] `payload_hash`
  - [x] `schema_version`
  - [x] `transform_version`
  - [x] `error_kind`
  - [x] `error_message`
  - [x] `event_time`
  - [x] `ingested_at`
  - [x] `first_failed_at`
  - [x] `attempts`
  - [x] `replay_status`
  - [x] `replay_run_id`
  - [x] `affects_closed_partition`

## 처리 정책

- [x] stream/source 오류 정책을 source별로 선언 가능하게 한다.
- [ ] transform-level Record DLQ 오류 정책은 transform record DLQ가 생길 때 별도 확장한다.
- [x] 소수 오류는 quarantine하고 정상 record를 계속 처리한다.
- [x] 오류율이 threshold를 넘으면 전체 batch를 fail-closed한다.
- [x] identity/primary-key/ordering 오류는 기본적으로 fail-closed한다.
- [x] DLQ 저장 자체가 실패하면 main pipeline을 성공 처리하지 않는다.
- [x] replay transaction/result에는 origin DLQ ID와 replay metadata를 남긴다.
- [x] replay request가 downstream backfill을 요구하는지 계산한다.

## API/SDK

- [x] `GET /api/operations/dead-letter-records`
- [x] `GET /api/operations/dead-letter-records/{id}`
- [x] `POST /api/operations/dead-letter-records/{id}/retry`
- [x] `POST /api/operations/dead-letter-records/bulk-retry`
- [x] `POST /api/operations/dead-letter-records/{id}/discard`
- [x] generated TypeScript SDK에 typed DLQ operations를 추가한다.

## 운영 UI

- [x] Record DLQ 목록
- [x] source/error/status 필터
- [x] 원본 payload와 실패 이유 비교
- [x] replay 영향 범위 미리보기
- [x] 단건/일괄 재처리
- [x] open/closed/top error kind 요약

## Operator evidence

- [x] record quarantine run evidence
- [x] DLQ write failure evidence
- [x] replay request audit
- [x] replay result and downstream impact
- [x] `request_id`, `run_id`, `dataset_version_id`, `record_dlq_id` 연결

## 제안 명령

```text
quality:record-dlq
quality:record-dlq-replay
```

## 제안 테스트

- [x] `test_one_poison_record_does_not_stop_valid_records`
- [x] `test_error_ratio_above_threshold_fails_batch`
- [x] `test_identity_error_fails_closed`
- [x] `test_dead_letter_payload_is_replayable`
- [x] `test_replayed_record_is_marked_with_origin`
- [x] `test_replay_emits_downstream_backfill_plan`
- [x] `test_stream_archive_quarantines_poison_cdc_record_and_commits_valid_records`
- [x] `test_stream_archive_dead_letter_store_failure_fails_main_pipeline`
- [x] `test_dataset_transaction_repository_contract_dead_letter_record_is_tenant_scoped`
- [x] `test_dataset_transaction_repository_contract_dead_letter_record_replay_and_discard`
- [x] `test_operations_record_dead_letter_replay_request_is_idempotent_and_audited`
- [x] `test_operations_record_dead_letter_replay_failure_is_visible`
- [x] `test_api_operations_record_dead_letter_records_retry_bulk_and_discard`
- [x] `test_operations_ui_record_dlq_retry_shows_result`
- [x] `test_concurrent_replay_requests_create_one_replay_run`
- [x] `test_same_replay_idempotency_key_returns_existing_result`

## 완료 기준

- [x] 정상 record와 문제 record가 명확히 분리된다.
- [x] 문제 record가 조용히 삭제되지 않는다.
- [x] replay가 중복 logical output을 만들지 않는다.
- [x] Operations API/SDK에서 원인과 재처리 결과를 확인할 수 있다.

---

# Sprint 48 — Late Data + Watermark Ratchet

## 목표

늦게 도착한 소셜/API/stream 이벤트를 조용히 버리거나 현재 상태를 잘못 되돌리는 문제를 막는다.

> 2026-06-18 현재 첫 ratchet은 stream/source archive 경로에 한정해 구현되었다.
> `StreamArchiveConfig`가 source별 `event_time_field`, `source_time_field`,
> `time_zone`, `allowed_lateness_seconds`, `too_late_after_seconds`, `clock_skew_seconds`를 선언하고,
> 정상 archive row에는 `event_time`, `source_time`, `ingested_at`, `processed_at`,
> `late_data_status`, `event_time_lag_seconds`가 남는다. 너무 오래된 event는
> `TOO_LATE` Record DLQ로 격리된다. partition/source watermark는 같은
> stream/topic/partition/consumer group에만 적용되므로 느린 partition이 빠른 partition의
> watermark 때문에 조용히 유실되지 않고, 같은 늦은 event가 재전달되어도 committed offset
> 이후부터 다시 읽어 두 번째 dataset version을 만들지 않는다. stream commit metadata에는
> `lateDataSummary`와 이전에 닫힌 archive dataset version 대상 `lateDataReprocessingPlan`이
> 남고, Operations run detail은 event-time lag와 reprocessing plan을 노출한다. CDC index run이
> `LATE_REQUIRES_REPROCESS` event를 적용한 뒤 실행한 materialization commit metadata에는
> `materializationDetail.watermark`와 `reopen.reason=late_data_reprocess`가 남아 Operations
> materialization run detail에서 확인할 수 있다. 같은 증거는 object explain의
> `lateDataBadge`, materialization detail의 `lateDataBadge`, Operations run detail의
> `downstreamImpact` graph로 이어져 어떤 materialized output/insight가 다시 만들어졌는지
> 확인할 수 있다.

## 시간 모델

- [ ] 모든 event에 가능한 경우 아래 시간을 분리한다.
  - [x] stream archive row `event_time`
  - [x] stream archive row `source_time`
  - [x] stream archive row `ingested_at`
  - [x] stream archive row `processed_at`
- [x] stream source별 event-time field를 선언한다.
- [x] stream source별 clock-skew 정책을 선언한다.
- [x] stream source별 named timezone override 정책을 선언한다.

## 정책 모델

```yaml
lateData:
  eventTimeField: published_at
  allowedLateness: 48h
  tooLateAfter: 30d
  onLate: integrate_and_recompute
  onTooLate: dead_letter
```

- [x] 상태를 정의한다.
  - [x] `ON_TIME`
  - [x] `LATE_ACCEPTED`
  - [x] `LATE_REQUIRES_REPROCESS`
  - [x] `TOO_LATE`
- [x] stream archive watermark는 partition/source별 transaction metadata로 저장한다.
- [x] stream archive watermark는 뒤로 이동하지 않는다.
- [x] 늦은 데이터가 닫힌 stream archive output을 바꾸면 reprocessing plan을 생성한다.
- [x] too-late event는 Record DLQ와 연결한다.

## API/UI

- [x] Operations run detail에 event-time lag와 late-data reprocessing plan을 표시한다.
- [x] 객체/인사이트에 late-data badge를 표시한다.
- [x] materialization detail에 watermark와 reopen 여부를 표시한다.
- [x] 늦은 데이터로 인해 영향받는 downstream을 표시한다.

## 제안 명령

```text
quality:late-data
quality:watermark
```

## 제안 테스트

- [x] `test_watermark_never_moves_backward`
- [x] `test_slow_partition_does_not_silently_drop_data`
- [x] `test_late_event_reopens_expected_materialization`
- [x] `test_too_late_event_goes_to_record_dlq`
- [x] `test_event_time_and_processing_time_sla_are_separate`
- [x] `test_duplicate_late_event_is_idempotent`
- [x] `test_late_event_creates_reprocessing_plan_for_closed_archive_output`
- [x] `test_run_detail_shows_event_time_lag_and_reprocessing_plan`
- [x] `test_late_data_badge_marks_impacted_insight`
- [x] `test_late_data_downstream_impact_graph_lists_materializations`
- [x] `test_late_delete_does_not_resurrect_stale_object`
- [x] `test_late_data_named_timezone_override_applies_source_policy`

## 완료 기준

- [x] stream/source 늦은 데이터가 정책에 따라 처리되고 이유가 보인다.
- [x] stream/source 늦은 데이터가 이미 닫힌 archive/materialization output을 바꿀 때 downstream 영향이 추적된다.
- [x] 늦은 데이터 때문에 현재 객체가 과거 상태로 회귀하지 않는다.

---

# Sprint 49 — Multi-file Dataset + Partitioning Ratchet

## 목표

단일 Parquet 파일 중심 protocol을 multi-file manifest 기반으로 확장해 대용량 데이터의 병렬 처리와 partition pruning을 가능하게 한다.

> 현재 부분 구현 메모 (2026-06-18): S49 ratchet은 read/preview 경로에 한정한다.
> `DatasetStorageAdapter.data_file_paths()`가 manifest의 `files` 목록을 읽기 가능한 local path
> 목록으로 resolve하고, `foundry.datasets.preview(...)`는 첫 파일만 읽지 않고 manifest 순서대로
> 여러 parquet part를 읽는다. manifest에 없는 orphan file은 directory/bucket listing으로 발견하지
> 않으므로 serving truth는 계속 manifest다. Local/fake/S3 adapter는 manifest entry를 그대로
> 읽고, `partition_filter`가 주어지면 manifest의 `partition_values`와 맞는 file만 읽는다.
> S3 adapter도 같은 filter를 download 전에 적용하며, Iceberg adapter는 기존 snapshot materialization
> 계약을 유지하므로 no-match filter는 읽지 않지만 snapshot 내부 file-level pruning은 아직 future
> scope다. 여러 staged file을 하나의 dataset version으로 원자 commit하는 protocol도 아직 future scope다.

## Manifest v2

- [x] dataset version manifest가 여러 data file을 참조할 수 있게 한다.
- [x] file별 metadata를 저장한다.
  - [x] `partition_values`
  - [x] `row_count`
  - [x] `byte_size`
  - [x] `content_hash`
  - [ ] column min/max
  - [ ] null count
  - [ ] sort bounds
- [x] reader가 bucket listing이 아니라 manifest만 사용한다.
- [x] 기존 single-file manifest와 backward compatibility를 제공한다.

## Layout policy

- [x] dataset에 `partition_spec`을 선언한다.
- [x] `sort_order`를 선언한다.
- [x] target file size를 선언한다.
- [ ] high-cardinality partition을 validation에서 경고한다.
- [~] read/preview predicate를 storage adapter에 전달한다. transform predicate 전달은 future scope다.

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

## 연결/제안 명령

```text
quality:multi-file-dataset   # 연결됨
quality:partition-pruning    # 연결됨
```

## 제안 테스트

- [x] `test_dataset_storage_adapter_resolves_manifest_files_with_partition_filter_without_listing`
- [x] `test_partition_pruning_reads_only_required_manifest_files`
- [x] `test_s3_data_file_paths_apply_partition_filter_before_download`
- [x] `test_iceberg_data_file_paths_skip_snapshot_when_partition_filter_has_no_match`
- [ ] `test_multi_file_commit_is_all_or_nothing`
- [ ] `test_one_part_upload_failure_exposes_no_version`
- [ ] `test_manifest_lists_exact_committed_files`
- [ ] `test_reader_never_lists_bucket_to_discover_version`
- [ ] `test_transform_partition_predicate_reaches_storage_adapter`
- [ ] `test_same_version_retry_does_not_duplicate_parts`
- [ ] `test_single_file_versions_remain_readable`
- [ ] `test_concurrent_multi_file_commits_allocate_unique_versions`

## 완료 기준

- [x] 기존 API는 유지된다.
- [x] 단일 파일과 multi-file dataset을 같은 facade로 읽는다.
- [x] partition predicate가 read/preview path의 실제 read file 수를 줄인다.

---

# Sprint 50 — Iceberg Maintenance Ratchet

## 목표

작은 파일 누적, 오래된 snapshot, orphan object를 안전하게 관리한다.

## 기능

- [x] compaction candidate detector
- [ ] `rewrite_data_files` 실행기
- [ ] snapshot expiration policy
- [ ] orphan file cleanup
- [x] maintenance plan model
- [~] retention policy: `retention_min_snapshots`와 DB committed version 보호를 plan에 반영한다. 실제 snapshot expiration 실행은 아직 future다.

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
- [x] active DB COMMITTED version이 참조하는 metadata 삭제 금지: S50-A1 plan은 DB committed dataset version의 Iceberg snapshot을 `protected_by=committed_db_version:*`로 표시하고 삭제 후보에서 제외한다.
- [ ] 실패한 compaction이 serving pointer를 바꾸지 않음

## API/UI

- [x] `GET /api/operations/maintenance/iceberg`
- [x] `POST /api/operations/maintenance/iceberg/{dataset}/plan`
- [ ] `POST /api/operations/maintenance/iceberg/{dataset}/run`
- [x] 대상 snapshot, compaction candidate, orphan snapshot, 보존 snapshot을 preview한다. 예상 절감 용량은 실제 rewrite/orphan cleanup slice에서 확정한다.

## 제안 명령

```text
quality:iceberg-maintenance
```

## 제안 테스트

- [x] `test_iceberg_maintenance_plan_protects_committed_snapshots_and_audits`
- [ ] `test_compaction_preserves_row_hash`
- [ ] `test_old_pinned_snapshot_remains_readable_within_retention`
- [ ] `test_expiration_refuses_referenced_snapshot`
- [ ] `test_compaction_failure_does_not_move_serving_pointer`
- [ ] `test_orphan_cleanup_never_deletes_reachable_file`
- [ ] `test_concurrent_maintenance_runs_have_one_winner`
- [ ] `test_retry_after_ambiguous_compaction_is_idempotent`

## 완료 기준

- [~] maintenance plan 결과가 operator audit evidence로 남는다. 실제 run model은 다음 slice에서 별도 구현한다.
- [~] DB committed version snapshot은 plan에서 보호된다. transform/materialization/backup pin까지 포함한 time travel/restore 보존은 다음 slice다.
- [ ] compaction 전후 logical row hash가 동일하다.

---

# Sprint 51 — Continuous CDC Worker + Rebalance Safety

## 목표

현재 one-shot proof를 실제 지속 실행 worker로 확장하고, rebalance/commit-unknown에서 데이터 유실과 중복 logical commit을 막는다.

> S51 현재 구현 범위: bounded continuous stream archive loop가 추가되어 기존 `archive_stream_events` transaction/checkpoint 경계를 여러 batch에 반복 적용하고, configured empty poll 또는 stop callback에서 멈추며 loop summary를 반환한다. worker CLI는 env/schema/tenant 설정 실패를 stacktrace가 아닌 `CONFIGURATION_ERROR` JSON payload로 반환한다. PostgreSQL-backed live Kafka worker가 같은 offset을 동시에 읽어도 commit 직전 stream cursor CAS가 한쪽 commit을 막는 proof가 추가됐다. `pnpm fault:local:stress`는 이 노트북에서 Spark executor kill, live Kafka/PostgreSQL 8-worker same-offset storm, continuous worker stop/restart replay overload를 한 번에 실행하고 `artifacts/operations/local_fault_lab.json`에 증거를 남긴다. Lease/fencing, rebalance revoke, broker commit-unknown reconciliation, CDC object-indexer continuous worker, managed Temporal failover는 아직 future scope다.

## Worker 기능

- [~] continuously running stream archive worker: bounded loop, CLI/env option, configuration-error JSON payload는 active, production daemon packaging은 future.
- [ ] continuously running CDC object-index worker
- [~] graceful shutdown: stop callback boundary는 active, OS signal/SIGTERM finish-or-abort proof는 future.
- [ ] heartbeat/lease
- [ ] partition assignment tracking
- [ ] backpressure
- [ ] retry budget
- [~] lag reporting: existing stream lag metric과 loop summary count는 active, Operations assignment/lag surface는 future.

## Checkpoint protocol

- [~] partition별 checkpoint: existing `streamCursor` partition/offset metadata를 continuous loop가 재사용하고, live Kafka/PostgreSQL same-offset parallel commit은 cursor CAS로 fencing한다. lease/fencing token과 broker rebalance fencing은 future.
- [ ] batch transaction ID
- [ ] source epoch/fencing token
- [ ] revoke 이후 old worker의 commit 금지
- [ ] broker commit-unknown reconciliation
- [~] topic/partition/offset event dedupe: same-offset duplicate logical commit은 PostgreSQL cursor CAS proof가 active, cross-broker/topic recreation identity는 future

## 제안 명령

```text
quality:cdc-continuous-worker
quality:kafka-rebalance
pnpm fault:local:stress
```

## 제안 테스트

- [x] `test_stream_archive_worker_continuous_loop_archives_until_empty_poll`
- [x] `test_stream_archive_worker_continuous_loop_honors_stop_callback`
- [x] `test_stream_archive_worker_continuous_loop_stops_after_max_batches`
- [x] `test_stream_archive_worker_continuous_loop_counts_empty_polls_before_stop`
- [x] `test_stream_archive_worker_main_prints_continuous_result`
- [ ] `test_rebalance_during_dataset_commit_has_no_loss`
- [ ] `test_commit_unknown_replay_creates_one_dataset_version`
- [ ] `test_revoked_worker_cannot_advance_checkpoint`
- [ ] `test_multi_partition_batch_does_not_hide_partial_failure`
- [ ] `test_worker_sigterm_finishes_or_aborts_current_batch`
- [ ] `test_worker_lease_expiry_fences_stale_instance`
- [ ] `test_continuous_indexer_restarts_from_committed_watermark`

## 완료 기준

- [~] bounded continuous loop 안에서 committed cursor를 따라 no-gap/no-duplicate archive result가 증명된다. rebalance/restart/lease expiry proof는 future.
- [ ] lag와 assignment가 Operations에 보인다.
- [ ] timeout/commit-unknown이 generic failure로 뭉개지지 않는다.

---

# Sprint 52 — Temporal Engine Integration

## 목표

Temporal을 독립 adapter에서 실제 Foundry-lite 업무 오케스트레이터로 승격한다.

> 현재 부분 구현 메모 (2026-06-19): S52의 첫 slice는 실제 connector
> page fetch/commit/cursor advance 전체가 아니라 product workflow control-plane을
> 고정한다. `ConnectorSyncWorkflow`는 Operations facade/API/generated SDK에서
> 시작/조회할 수 있고, local/fake/Temporal profile이 같은 public
> `ProductWorkflowRun` shape를 반환하며, audit event의 `workflowRunId`와
> `foundryRunId`가 서로 연결된다. 실제 connector activity data-plane,
> cancel cleanup, response-loss reconciliation, continue-as-new, code upgrade
> replay는 아직 future scope다.

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

- [~] workflow ID는 stable idempotency key 기반: S52 첫 slice에서는
  `ConnectorSyncWorkflow` run id가 `Idempotency-Key`와 같도록 고정한다.
- [ ] activity ID와 domain transaction ID 연결
- [~] workflow history와 Foundry run evidence 연결: Operations audit detail이
  `workflowRunId`와 `foundryRunId`를 서로 참조한다.
- [ ] cancel 시 staging cleanup
- [ ] activity 완료 후 응답 유실 reconciliation
- [ ] bounded retry 정책
- [ ] continue-as-new
- [ ] workflow code versioning
- [ ] replay determinism

## API/UI

- [~] workflow start endpoint: 현재는
  `POST /api/operations/workflows/connector-sync/start`로 connector-sync workflow만 제공한다.
- [~] workflow status endpoint: 현재는
  `GET /api/operations/workflows/{workflow_run_id}`로 product workflow run을 조회한다.
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
- [x] `test_product_workflow_operations_contract_starts_connector_sync_and_audits`
- [x] `test_product_connector_sync_workflow_runs_through_temporal_and_audits`
- [x] `test_api_operations_workflow_start_status_and_audit`
- [ ] `test_temporal_and_foundry_run_ids_are_bidirectionally_linked`

## 완료 기준

- [~] 최소 하나의 실제 product workflow가 Temporal을 통해 실행된다:
  `ConnectorSyncWorkflow` control-plane 경로는 Temporal time-skipping worker로 실행된다.
- [~] 기존 local profile과 Temporal profile의 public contract가 같다:
  local/fake/Temporal profile이 `ProductWorkflowRun` 계약을 공유한다.
- [~] Operations에서 Temporal 내부 로그 없이 조사할 수 있다:
  workflow start audit event와 Operations audit detail link가 제공된다.

---

# Sprint 53 — External Writeback + Saga/Reconciliation

## 목표

AI Agent와 Action Type이 CRM/ERP/Slack/캠페인 시스템을 안전하게 움직이도록 한다.

> 현재 부분 구현 메모 (2026-06-26): S53의 fast slices는 기존
> `mock_erp_simulator` before-commit writeback 경로에서 외부 응답 유실/timeout 성격의
> 결과 미확인 상태를 first-class evidence로 고정한다. `quality:external-writeback`은
> `action_runs.status="outcome_unknown"`, `action_writebacks.status="outcome_unknown"`,
> `external_operation_id`, `idempotency_key`, `request_hash`, `last_observed_status`,
> `reconciliation_deadline`, audit event, 그리고 같은 idempotency key replay가 새
> writeback을 만들지 않는 것을 검증한다. `quality:saga-reconciliation`은 simulated
> external success 뒤 local mutation을 적용하지 못한 상태를 `compensation_required`
> action/writeback/audit evidence로 남기고 같은 idempotency key replay가 두 번째 writeback을
> 만들지 않는지 검증한다. L8의 `quality:action-writeback-live`는 `ExternalWritebackAdapter`
> + `S3ExternalWritebackAdapter`를 통해 live MinIO에 real PUT/HEAD를 수행한다. 이 gate는
> real connection timeout이 `outcome_unknown`으로 남는지, real write가 landed 된 뒤 local
> commit이 실패하면 `compensation_required`로 남는지, 그리고 real `remote_lookup`이
> action/writeback을 `reconciled`로 닫고 원래 local object mutation을 한 번만 적용하는지
> 검증한다. 같은 saga gate는 unresolved writeback row를 tenant-scoped backend/API/SDK
> reconciliation queue로 노출하고, queue payload에서도 민감 action parameter가 raw 값으로
> 새지 않는지 검증한다. Action apply는 요청
> object type이 action definition의 target과 다르면 idempotency key를 선점하거나
> action/writeback/object edit/outbox를 남기기 전에 거부하고, 같은 property 이름을 가진
> 다른 object type도 거부하며, 손상된 object record type id도 action run insert 전에 막는다.
> ERP-specific connector packaging, background compensation worker 실행, reconciliation
> queue UI, operator approval UI는 아직 future scope다.

## 상태 모델

- [~] `SUCCEEDED`: 기존 before-commit mock writeback 성공 row와 action success path가 있다.
- [~] `FAILED`: 기존 before-commit mock writeback 실패 row와 failed action run이 있다.
- [ ] `RETRYABLE`
- [~] `OUTCOME_UNKNOWN`: S53 첫 slice와 L8 real adapter slice에서 simulated response loss와 real external timeout을 별도 상태로 남긴다.
- [~] `COMPENSATION_REQUIRED`: S53 두 번째 slice와 L8 real adapter slice에서 simulated 또는 real external success/local failure를 별도 상태로 남긴다.
- [ ] `RECONCILING`
- [~] `RECONCILED`: S53 세 번째 slice에서 operator remote-success 확인 후 action/writeback을 reconciled로 닫는다.

## 데이터 모델

- [~] `external_operation_id`: S53 첫 slice는 writeback response JSON에 simulated operation id를 남긴다.
- [~] `idempotency_key`: `action_runs`/`action_writebacks` column과 writeback request JSON에 남긴다.
- [~] `request_hash`: action request fingerprint를 writeback request/error detail에 남긴다.
- [~] `remote_resource_id`: outcome-unknown 최초 기록은 unknown/null이며, operator reconciliation resolve 시 remote resource id를 기록한다.
- [~] `attempts`: `action_writebacks.attempts`로 남긴다.
- [~] `last_observed_status`: outcome-unknown response/error detail에는 `unknown`, reconciliation resolve 후에는 `succeeded`로 남긴다.
- [~] `compensation_action_type`: 현재는 `mock_reverse_writeback` evidence를 남기며 실제 compensation worker는 future다.
- [~] `reconciliation_deadline`: outcome-unknown evidence에 첫 reconciliation 기준 시각을 남긴다.
- [~] unresolved queue item: `GET /api/operations/reconciliation/writebacks`와 generated SDK가 tenant-scoped unresolved writeback id/action id/status/deadline/remote evidence를 반환한다.
- [~] before/after evidence: writeback request/response, action run error, reconciliation result, audit event가 남는다.

## 동작

- [ ] 외부 write는 outbox 또는 workflow activity로만 실행한다.
- [~] timeout을 곧바로 실패로 처리하지 않는다:
  S53 첫 slice는 응답 유실/timeout 성격의 mock writeback을 `failed`가 아니라
  `outcome_unknown`으로 잠근다.
- [~] remote lookup/reconciliation path를 제공한다:
  현재는 operator가 확인한 remote success payload 또는 L8 real adapter `remote_lookup`으로
  `POST /api/operations/reconciliation/{writeback_id}/resolve`와 generated SDK를 통해
  writeback/action/object edit을 한 transaction에서 닫는 경로를 제공한다.
- [~] compensation action은 idempotent하다:
  현재는 compensation-required action replay가 같은 idempotency key에서 두 번째
  writeback을 만들지 않는 것을 검증한다. 실제 compensation worker idempotency는 future다.
- [~] action은 선언된 target object type 밖으로 실행되지 않는다:
  현재는 target object type mismatch, 같은 property 이름을 가진 다른 object type 요청,
  그리고 손상된 object record type id를 action run 생성 전 거부한다.
- [ ] high-risk action에는 human approval을 요구한다.
- [ ] AI Agent가 직접 vendor API를 호출하지 못하게 한다.

## API/UI

- [~] reconciliation queue: backend/API/SDK queue는 active이며 persistent queue UI는 future다.
- [ ] compensation approval
- [ ] remote/local state 비교
- [ ] operator action history

## 제안 명령

```text
quality:external-writeback
quality:saga-reconciliation
```

현재 활성화된 명령은 `quality:external-writeback`, `quality:saga-reconciliation`, `quality:action-writeback-live`이다.
`quality:saga-reconciliation`은 compensation-required first-class state,
same-key replay proof, operator-provided remote-success resolve, 그리고 concurrent
reconciliation winner, unresolved backend/API/SDK queue, sensitive writeback audit masking을 검증한다. 실제 vendor SDK packaging,
autonomous compensation worker, queue UI, approval UI는 아직 future scope다.

## 제안 테스트

- [x] `test_external_success_response_lost_becomes_outcome_unknown`
- [x] `test_outcome_unknown_is_not_blindly_retried`
- [x] `test_external_success_local_failure_requires_compensation`
- [x] `test_compensation_is_idempotent`
- [x] `test_reconciliation_resolves_remote_success`
- [x] `test_concurrent_reconciliation_has_one_winner`
- [x] `test_sensitive_writeback_payload_is_masked_in_audit`
- [x] `test_action_rejects_target_object_type_mismatch_before_side_effects`
- [x] `test_action_rejects_same_property_target_type_mismatch`
- [x] `test_action_rejects_corrupt_target_record_type_before_action_run`

## 완료 기준

- [~] 외부 시스템과 local state divergence가 first-class 상태로 보인다:
  현재는 outcome-unknown 및 compensation-required divergence가 action run/writeback/audit
  evidence로 보인다.
- [~] operator가 추측 없이 reconcile/compensate할 수 있다:
  remote success reconciliation resolve는 가능하다. 실제 compensation worker와 approval UI는 future다.
- [~] action이 잘못된 aggregate에 적용되지 않는다:
  target object type invariant와 corrupt record type-id guard는 증명됐다. DB-level FK/constraint
  강화와 실제 vendor policy approval은 future다.
- [ ] AI Agent 권한은 Action Type과 approval policy로 제한된다.

---

# Sprint 54 — Data Quality Contract

## 목표

데이터 품질을 코드 안의 개별 validation이 아니라 versioned product contract로 관리한다.

> 현재 부분 구현 메모 (2026-06-19): S54의 첫 slices는 완전한
> `DataContract` CRUD나 QUARANTINE 정책 엔진이 아니라,
> 현재 dataset quality check 결과가 어떤 `dataset_schemas` row/version과 어떤 staged
> candidate fingerprint를 기준으로 검증됐는지 고정한다. `dataset_check_results`는
> `checked_manifest_hash`, `validated_against_schema_version_id`,
> `validated_against_schema_version`을 저장하며, `quality:data-contracts`는 dataset
> commit 경로와 repository contract가 이 값을 잃지 않는지 검증한다. 같은 gate는
> 이후 같은 dataset에 새 schema version이 생겨도 과거 check result가 당시 검증한
> schema row/version에 계속 pinned되는지 검증한다. 또한 final
> storage commit 직전에 staged candidate fingerprint를 다시 계산해 품질검사 뒤 파일이
> 바뀌면 commit을 거부한다. 성공한 check result는 `PASS`로, warning severity 실패는
> commit을 막지 않는 `WARN`으로, commit-time hard failure는 abort metadata/exception
> detail에서 `BLOCK_COMMIT`로 표면화한다. 또한 행 단위로 특정 가능한
> `not_null`/`unique` quarantine check는 실패 record를 tenant-scoped Record DLQ에
> `DATA_QUALITY_CONTRACT`로 격리하고, staged candidate를 정상 record만 남긴 parquet으로
> 다시 쓴 뒤 재검증된 candidate만 commit한다. Operations run detail은 같은
> transaction의 quality summary, schema reference, checked manifest hash, check result,
> `failedRowSampleCount`, 그리고 최대 5개의 `failedRowSamples`를 `quality` 섹션으로 노출해
> 후보 품질 리포트와 실패 행 샘플의 첫 운영 표면을 제공한다. 현재 추가 slice는
> `dataset_checks`를 persisted check-definition source of truth로 삼아
> `GET/POST/PATCH /api/datasets/{namespace}/{name}/quality-contract/checks`와
> `client.datasets.qualityChecks.list/create/update(...)`를 제공하고, enabled/config 변경이 이후
> dataset commit validation에 실제 적용되는지 검증한다. `GET /api/datasets/{namespace}/{name}/quality-contract/results`와
> `client.datasets.qualityResults.list(...)`는 persisted commit-time quality result history를 dataset 단위로 노출하고,
> `GET /api/datasets/{namespace}/{name}/quality-contract/results/summary`와
> `client.datasets.qualityResults.summary(...)`는 같은 persisted result를 status/check-type count와 latest evidence로 요약한다.
> Full versioned DataContract object CRUD,
> owner/policy UI, dedicated failed-row sample UI, trend UI, owner notification,
> production DB schema race proof는 아직 future scope다.

## 모델

- [~] `DataContract`: 현재는 full contract object/table이 아니라 persisted
  `dataset_checks` check-definition을 API/SDK로 create/list/update하고 commit validation에 적용한다.
- [~] contract version: 현재는 별도 DataContract version이 아니라
  `dataset_schemas.id`/`version` reference를 check result에 저장하고, run 이후 새
  schema version이 생겨도 과거 check result가 당시 reference에 pinned되는 증거를 남긴다.
- [ ] owner
- [~] schema: dataset schema registry row를 기준으로 check result reference를 남긴다.
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

- [x] `PASS`
- [x] `WARN`
- [x] `QUARANTINE`
- [x] `BLOCK_COMMIT`

## 기존 위험 보강

- [x] `checked_manifest_hash`를 저장한다.
- [x] final commit 직전에 candidate hash를 재검증한다.
- [x] `validated_against_schema_version_id`를 저장한다.
- [ ] schema version 변경 race를 production DB에서 증명한다.

## API/UI

- [~] contract CRUD/validate: 현재는 check-definition create/list/update API/SDK와 subsequent
  commit validation enforcement까지다. get/delete, versioned DataContract object,
  owner workflow는 future다.
- [~] candidate quality report: Operations run detail의 `quality` 섹션에서
  transaction별 summary/schema reference/result/failed row sample을 확인한다.
- [~] failed row sample: Operations run detail의 `quality.failedRowSamples`가
  data-quality quarantine Record DLQ row에서 최대 5개 샘플을 보여준다. Dedicated
  owner UI와 notification/policy workflow는 future다.
- [~] quality history/trend: 현재는 dataset 단위 API/SDK quality result history와
  status/check-type summary까지이며, trend UI는 future다.
- [ ] owner notification

## 제안 명령

```text
quality:data-contracts
quality:data-quality-runtime
```

현재 활성화된 명령은 `quality:data-contracts`이다. `quality:data-quality-runtime`은
owner notification, dedicated failed-row sample UI, versioned DataContract object, full
DataContract policy surface 같은 후속 runtime policy slice에서 활성화한다.

## 제안 테스트

- [x] `test_quality_check_pins_candidate_manifest_hash`
- [x] `test_candidate_tamper_between_check_and_commit_is_rejected`
- [x] `test_schema_validation_records_reference_version`
- [x] `test_commit_dataset_version_aborts_when_primary_key_check_fails`
- [x] `test_warn_does_not_block_commit_but_is_visible`
- [x] `test_quarantine_routes_bad_records_to_record_dlq`
- [x] `test_persisted_quality_contract_check_blocks_later_commit`
- [x] `test_updated_quality_contract_check_controls_later_commit`
- [x] `test_quality_result_history_lists_dataset_commit_results`
- [x] `test_check_result_summary_counts_are_tenant_dataset_scoped`
- [x] `test_api_dataset_object_action_and_metrics_smoke` covers quality contract check create/list/update, quality result history API, and quality result summary API.
- [x] `test_contract_version_is_pinned_to_run`
- [x] `test_operations_run_detail_exposes_candidate_quality_report`
- [x] `test_operations_run_detail_includes_failed_row_sample_for_quarantine`
- [x] `test_check_results_for_transaction_is_tenant_scoped`
- [x] `test_check_results_for_dataset_are_tenant_dataset_scoped_and_limited`

## 완료 기준

- [~] 데이터셋마다 품질 기대치와 owner가 보인다: 현재는 schema reference evidence만
  남기고 Operations run detail에서 transaction별 품질 리포트와 실패 행 샘플을 볼 수 있으며,
  owner/contract UI는 future다.
- [~] 품질 실패 정책이 명시적이며 조용히 유실하지 않는다: PASS/WARN/BLOCK_COMMIT와
  row-level QUARANTINE/failed row sample evidence는 active이나 full DataContract policy surface는 future다.
- [ ] downstream은 contract-failed version을 정상으로 소비하지 않는다.

---

# Sprint 55 — DB/Dataset/Ontology Schema Migration

## 목표

현재 schema snapshot guard를 실제 운영 migration 체계로 발전시킨다.

## DB migration

- [~] Alembic 도입: baseline migration과 후속 Alembic migration chain이 있으며,
  fresh DB `alembic upgrade head` metadata parity와 `quality:schema-migrations`로
  단일 head/forward-fix 정책을 검증한다. Runtime bootstrap은 아직 local
  `create_all` 경로도 유지한다.
- [~] migration singleton lock: `db:migrate` 전용 runner가 같은 DB connection 안에서
  Alembic을 실행하고, PostgreSQL은 advisory lock, SQLite/local proof는 `BEGIN IMMEDIATE`
  DB write lock으로 one-winner 실행을 보장한다. `quality:schema-migration-runner-live`는
  live PostgreSQL contention에서 한 runner만 migration callback을 실행하고 다른 runner는
  `lock_busy` evidence를 남기는지 검증한다. Runner는 성공, lock busy, 실패를
  password-masked operator evidence JSON으로 남긴다. Deployment migration job wiring은 future다.
- [~] expand-contract 규칙: Alembic migration은 `migration_phase`를
  `baseline`/`expand`/`contract`로 선언해야 하며, `quality:schema-migrations`가
  expand 단계의 drop/alter/rename류 operation, literal로 검토할 수 없는 SQL,
  그리고 기본값 없는 새 `NOT NULL` 컬럼을 차단한다. `contract` 단계는 old-writer
  reject와 release window 증거가 생기기 전까지 실패시킨다.
- [~] forward-fix 우선 정책: migration `downgrade()`는 destructive drop을 수행하지
  않고 명시적으로 실패하며, `quality:schema-migrations`가 이 정책을 막는다.
- [~] release compatibility window: migration마다 `release_compatibility`를
  `bootstrap`/`old_and_new_app`/`new_app_only`로 선언해야 하며,
  `quality:schema-migrations`가 phase와 window mismatch를 차단한다. 실제 rolling
  deploy old/new app 실행 테스트는 future다.
- [~] rollback/restore runbook: 실패 migration은 `artifacts/operations/migration_run.json`
  형태의 operator evidence를 남기지만, 실제 restore 절차, rehearsal, deployment rollback
  test는 future다.

## Dataset schema evolution

- [~] rename: `schema_evolution.py`가 explicit `renamedFrom`/`previousName` 변경을
  blocking change로 판정하고 consumer mapping이 없는 rename을 commit 전에 차단한다.
  실제 rename migration executor와 downstream mapping rewrite는 future다.
- [ ] split/merge field
- [~] type widening: `integer`/`long`/`float` 계열 widening은 commit을 허용하되
  `compatible_with_warning` schema evolution metadata로 남긴다.
- [~] deprecated field: next schema column에 `deprecated: true`가 표시되면 consumer
  migration warning으로 기록한다. UI/owner notification은 future다.
- [~] consumer compatibility: Dataset commit의 `dataset_transactions.metadata.schemaEvolution`
  안에 `consumerCompatibility`, source/target schema version, change list가 남는다.
  transform/ontology dependency graph 전체 차단은 future다.
- [~] backfill progress: non-null default나 nullability tighten처럼 backfill이 필요한
  변경은 deterministic `schema_backfill:*` key, step list, completed step resume metadata를
  생성한다. 실제 backfill worker와 progress update API는 future다.

## Ontology migration

- [~] property rename/deprecation: `ontology_migration.py`가 active ontology와 candidate
  YAML을 비교해 `renamedFrom`/`previousName`/`previousApiName` rename을 blocking
  change로 막고, `deprecated: true` property는 consumer warning으로 남긴다. 실제
  consumer mapping rewrite와 rename executor는 future다.
- [~] Object Type migration: object type removal과 primary key 변경은 activation 전에
  blocking change로 막고, backing mapping 변경은 object reindex warning/evidence로
  남긴다. 실제 object type split/merge executor는 future다.
- [~] Link Type migration: link removal, endpoint/cardinality/backing key 변경은 graph
  traversal API break로 보고 activation 전에 차단한다. 자동 link remap/backfill은 future다.
- [~] Action parameter compatibility: required parameter 추가, parameter 제거/type 변경,
  optional → required 변경은 generated SDK major-version 필요 변경으로 차단한다.
  optional parameter 추가나 required → optional 완화는 warning으로 남긴다.
- [~] Object reindex plan: object backing 또는 dataset-backed property mapping 변경은
  deterministic `object_reindex:<ObjectType>:<hash>` key와 changedFields를 audit/outbox
  payload의 `ontologyMigration.objectReindexPlan`에 남긴다. 실제 worker 실행은 future다.
- [~] generated SDK compatibility/versioning: blocking API shape change는
  `sdkCompatibility="major_version_required"`로 노출되고 `quality:ontology-migrations`가
  generated SDK drift check와 함께 실행된다. release/version bump 자동화는 future다.

## 제안 명령

```text
db:migrate
quality:schema-migrations
quality:schema-migration-runner
quality:schema-migration-runner-live
quality:schema-evolution
quality:ontology-migrations
```

현재 활성화된 명령은 `db:migrate`, `quality:schema-migrations`,
`quality:schema-migration-runner`, `quality:schema-migration-runner-live`, `quality:schema-evolution`,
`quality:ontology-migrations`이다.

## 제안 테스트

- [x] `test_old_app_reads_expand_phase_schema`
- [x] `test_schema_migration_gate_passes_linear_forward_fix_chain`
- [x] `test_schema_migration_gate_flags_multiple_heads`
- [x] `test_schema_migration_gate_flags_destructive_downgrade`
- [x] `test_schema_migration_gate_writes_json_report`
- [x] `test_schema_migration_guard_runs_after_schema_revision_guard`
- [x] `test_schema_migration_gate_requires_phase_metadata`
- [x] `test_release_compatibility_window_required`
- [x] `test_expand_phase_requires_old_and_new_app_window`
- [x] `test_expand_phase_blocks_destructive_upgrade`
- [x] `test_expand_phase_requires_default_for_not_null_column`
- [x] `test_migration_is_singleton_for_concurrent_sqlite_jobs`
- [x] `test_postgres_migration_runner_allows_one_live_advisory_lock_winner`
- [x] `test_migration_job_singleton_no_app_start_race`
- [x] `test_backfill_resumes_idempotently`
- [x] `test_schema_evolution_widening_is_visible_in_transaction_metadata`
- [x] `test_contract_phase_rejects_old_writer`
- [ ] `test_ontology_mapping_migrates_with_dataset_schema`
- [x] `test_sdk_breaking_change_requires_major_version`
- [x] `test_ontology_migration_apply_blocks_required_action_parameter`
- [x] `test_failed_migration_leaves_operator_evidence`

## 완료 기준

- [~] DB 변경을 `create_all`에만 의존하지 않는다: Alembic fresh-DB parity,
  schema revision guard, schema migration safety gate, singleton migration runner는
  active이나 local runtime bootstrap과 multi-step production upgrade 운영은 future다.
- [~] ontology/schema 변경의 영향과 backfill이 merge 전에 보인다: Dataset schema
  evolution은 `quality:schema-evolution`과 transaction metadata로 일부 보이고,
  ontology migration plan은 `quality:ontology-migrations`와 activation-time
  audit/outbox evidence로 일부 보인다. 실제 ontology migration executor,
  object reindex worker 실행, backfill worker/progress API는 future다.
- [~] old/new app version 공존 구간이 테스트된다: 현재는 expand-phase static
  compatibility gate, `release_compatibility="old_and_new_app"` metadata guard,
  contract-phase fail-closed proof가 active이고, 실제 배포 중 old/new app runtime
  window와 live PostgreSQL proof는 future다.
- [~] 실패한 DB migration은 운영자가 볼 수 있는 증거를 남긴다: 현재는 runner의
  password-masked JSON evidence가 active이고, 전체 restore runbook 검증은 future다.

---

# Sprint 56 — Proactive Observability + SLO

## 목표

실패가 난 뒤 설명하는 것을 넘어, 데이터가 멈추거나 느려지거나 치우치는 상태를 먼저 감지한다.

## Detector

### Flow Interruption

- [~] expected cadence: versioned detector config의 `expectedCadenceSeconds`로
  현재 runtime snapshot과 비교한다. 저장형 incident scheduler는 future다.
- [~] last successful event/version: `RuntimeRunSnapshot`에서 성공 row와
  version/resource reference를 읽어 evidence link를 만든다.
- [~] missing-data alert: `FAILED` run이 없어도 마지막 성공 이후 cadence를 넘으면
  active incident를 만든다.

### Lag

- [~] source event time ↔ processing time: detector가 `event_time`/`source_event_time`과
  `processed_at`/`completed_at`을 분리해 lag evidence로 남긴다.
- [ ] broker latest offset ↔ committed offset
- [ ] REST cursor observation lag

### Skew

- [~] partition size skew
- [~] file size skew
- [ ] tenant/object key skew

### SLA/SLO

- [~] raw ingest SLO
- [~] clean dataset SLO
- [ ] object index SLO
- [ ] workflow/action SLO

## Alert policy

- [~] dedupe
- [~] cooldown
- [~] severity
- [~] owner routing: config owner가 incident payload에 남는다. 실제 notification
  delivery/routing은 future다.
- [~] seasonality/baseline
- [~] alert evidence links

## API/UI

- [ ] SLO dashboard
- [~] active incidents: `POST /api/operations/observability/detect`와 generated SDK
  `operations.observability.detect(...)`가 read-only report를 반환한다.
- [ ] source health timeline
- [~] run/dataset/object drill-down: incident evidence link가
  `/api/operations/runs/{run_type}/{run_id}` 형태의 Operations path를 담는다.

## 제안 명령

```text
quality:observability-detectors
quality:slo-contracts
```

현재 활성화된 명령은 `quality:observability-detectors`와 `quality:slo-contracts`다.

## 제안 테스트

- [x] `test_missing_data_triggers_flow_interruption`
- [x] `test_lag_alert_uses_event_and_processing_time_separately`
- [x] `test_skew_detector_ignores_expected_seasonality`
- [x] `test_sla_alert_carries_run_and_dataset_refs`
- [x] `test_alert_dedup_prevents_alarm_storm`
- [x] `test_detector_failure_does_not_mutate_source_of_truth`
- [x] `test_api_observability_detect_returns_active_incident_report`
- [x] `test_observability_detector_gate_runs_after_ontology_migrations`

## 완료 기준

- [~] “FAILED run이 없음에도 데이터가 멈춘 상태”를 탐지한다: read-only
  detector report로 증명된다. 저장형 incident lifecycle은 future다.
- [~] alert에서 관련 run/dataset/worker로 바로 이동할 수 있다: 현재는
  Operations run-detail API path evidence link까지 제공한다. UI timeline은 future다.
- [~] threshold와 owner가 versioned configuration으로 남는다: detector config
  payload의 `configVersion`, `owner`, threshold fields로 증명된다. 영구 config registry는 future다.

---

# Sprint 57 — Backup/Restore Commit-point Ratchet

## 목표

PostgreSQL, S3/Iceberg, Object Store, outbox/action 상태를 일관된 시점으로 복구한다.

## Backup manifest

- [~] DB backup ID/time: `POST /api/operations/backup-restore/preflight` report가
  `backupId`와 `generatedAt`을 남긴다. 실제 DB backup artifact 생성은 future다.
- [~] dataset committed version inventory: tenant의 active dataset과 committed
  dataset version을 전수 inventory로 만든다.
- [~] S3/Iceberg manifest inventory: 현재 storage adapter가 제공하는 committed
  manifest와 data file hash/size를 검증한다. 별도 backup manifest artifact는 future다.
- [~] active object index pointers: runtime index run에서 object type을 찾아 현재
  serving `activeIndexVersion`을 report에 남긴다.
- [~] action/outbox/audit/materialization high-watermarks: action run, action
  writeback, outbox, audit, materialization row count와 max timestamp/status
  counts를 report에 남긴다.
- [~] Temporal namespace/history strategy: workflow profile과 복구 전 history
  reconcile 전략을 report에 남긴다. 실제 production Temporal namespace restore는 future다.
- [~] Elasticsearch rebuild marker: search projection은 truth가 아니며 restore 후
  rebuild 대상이라는 marker를 report에 남긴다. rebuild 실행은 future다.

## Restore mode

- [~] write traffic 차단: preflight report의 `restoreTrafficGate`가 restore 전에
  write traffic pause가 필요함을 fail-closed 조건으로 남기고, `start_restore_mode`
  status가 `is_serving_traffic_open=false`를 감사 증거로 남긴다. `runtime_restore_gates.py`와
  `write_traffic_gate.py`가 dataset create/upload, action apply, transform, ontology,
  object indexing/search/set, materialization, workflow start, Record DLQ, insight review
  같은 core service write path를 restore mode 중 차단한다. 별도 reverse proxy/Kubernetes
  traffic fencing은 future다.
- [~] outbox publisher 일시 중지: report가 outbox publisher를 reconcile 전까지
  resume하면 안 된다는 조건을 남기고, restore mode 중 outbox dead-letter retry와
  materialization reprocess 진입을 차단한다. 별도 publisher daemon pause executor는 future다.
- [~] committed manifest 전수 검증: active dataset의 committed version manifest와
  data file byte/hash를 전수 검증한다.
- [~] missing/corrupt version report: DB에는 committed인데 storage manifest/file이
  없거나 깨진 version을 `blocked` report issue로 남긴다.
- [~] search projection rebuild: projection은 restore truth가 아니고 rebuild 대상이라는
  marker를 남긴다. rebuild 실행은 future다.
- [~] post-restore closed-loop validation: `run_post_restore_validation`이
  dataset version inventory, active object index pointer, action run,
  materialization run 증거를 별도 `post_restore_validated` audit evidence로 남기고,
  `approve_restore_resume`은 통과한 validation id가 없으면 publisher resume 승인을
  거절한다. 실제 restore executor가 복구 후 이 smoke를 자동 실행하는 것은 future다.
- [~] operator approval 후 publisher resume: `approve_restore_resume`이 통과한
  validation id를 확인한 뒤 `resume_approved` audit evidence를 남기고 현재 outbox
  retry/reprocess 잠금을 해제한다. 실제 publisher daemon pause/resume executor는 future다.

## 명령

```text
quality:backup-restore
```

## 테스트

- [x] `test_restore_preflight_rejects_db_storage_point_mismatch`
- [x] `test_restore_preflight_validates_every_committed_manifest`
- [x] `test_restore_preflight_captures_index_pointers_and_runtime_high_watermarks`
- [x] `test_api_backup_restore_preflight_returns_commit_point_report`
- [x] `test_backup_restore_preflight_gate_runs_after_slo_contracts`
- [x] `test_api_backup_restore_mode_start_and_status_return_pause_gate`
- [x] `test_restore_pauses_outbox_until_reconciliation`
- [x] `test_restore_resume_requires_closed_loop_validation`
- [x] `test_post_restore_closed_loop_smoke`
- [x] `test_restore_retry_is_idempotent`
- [x] `test_restore_failure_never_opens_serving_traffic`
- [x] `test_restore_mode_blocks_platform_write_traffic`

## 완료 기준

- [~] DB만 또는 storage만 복구한 상태를 serving하지 않는다: 현재는 preflight가
  DB/storage mismatch를 `blocked`로 보고하고, restore mode status가 serving traffic을
  열지 않는 상태를 남기며 core service write path를 restore mode 중 차단한다. 별도
  edge traffic fencing과 Kubernetes packaging은 future다.
- [~] 외부 side effect 중복 전송을 방지한다: 현재는 outbox/action high-watermark와
  publisher pause 조건을 report에 남기고, restore mode 중 outbox retry/reprocess 진입을
  차단하며, post-restore 폐루프 검증 후 운영자 승인 상태에서만 현재 retry/reprocess
  entrypoint를 다시 연다. 실제 publisher daemon pause/resume executor는 future다.
- [~] 복구 후 데이터셋→오브젝트→액션 폐루프 smoke가 통과한다: 현재는 복구 후 상태를
  흉내낸 runtime snapshot에서 dataset inventory, object index pointer, action run,
  materialization run 증거가 모두 있어야 승인된다. 실제 backup artifact restore executor가
  smoke를 자동 실행하는 것은 future다.

---

# Sprint 58A — OIDC/JWT + Secret Provider

## 목표

개발용 header trust를 넘어 실제 production identity와 secret lifecycle을 제공한다.

## Auth

- [~] OIDC discovery: `FOUNDRY_LITE_OIDC_DISCOVERY_JSON`로 local discovery
  document의 issuer를 읽는다. Live `.well-known/openid-configuration` fetch,
  timeout/cache/error evidence는 future다.
- [x] JWT signature/issuer/audience 검증: `JwtOidcAuthProvider`가
  `Authorization: Bearer ...` token의 RS256 signature, `iss`, `aud`, `exp`,
  `sub`, tenant claim, roles claim을 검증해 `Principal`을 만든다.
- [~] JWKS rotation: unknown `kid`를 만나면 `FOUNDRY_LITE_OIDC_JWKS_JSON`을
  다시 읽고, 기존 key cache를 유지해 새 token과 이미 검증 가능한 기존 token을
  함께 통과시킨다. Live JWKS URI polling, TTL, key retirement policy는 future다.
- [~] service account/M2M: `sub`가 없는 JWT라도 configured service-account
  claim(기본 `client_id`)이 있으면 `service-account:<client_id>` actor로 인증한다.
  이 경우에도 tenant claim은 필수이며, header tenant override는 통하지 않는다.
  Service account registry, scope policy, credential rotation, revocation은 future다.
- [~] group/role mapping: 현재는 `roles` claim을 `Principal.roles`로 매핑한다.
  IdP group-to-role policy, SCIM/group sync, tenant별 role mapping은 future다.
- [~] session revocation policy: `FOUNDRY_LITE_OIDC_REVOKED_JTIS_JSON`로 local
  revoked JWT ID denylist를 읽고, token `jti`가 denylist에 있으면 서명/issuer/audience가
  맞아도 거부한다. IdP introspection, refresh-token revocation, distributed cache,
  tenant별 revocation registry는 future다.

## Secrets

- [x] `SecretProvider` port
- [x] local environment adapter: `EnvSecretProvider`가
  `FOUNDRY_LITE_WEBHOOK_SIGNING_KEY`와 `FOUNDRY_LITE_SECRET_<NAME>` alias를
  통해 secret을 조회한다.
- [ ] Vault/cloud secret manager adapter
- [~] secret version/rotation: local env adapter는 secret value hash 기반
  `version`을 제공하고, REST connector는 `token_secret_ref` 또는
  `header_value_secret_ref`가 있으면 snapshot 호출마다 `SecretProvider`에서 현재 값을
  다시 읽는다. rotation registry, previous/current dual-read, cloud manager version
  metadata는 future다.
- [~] long-running connector credential refresh: `RestPullConnectorAdapter`는 bearer/header
  credential을 raw config 값 대신 secretRef로 받을 수 있고, 같은 adapter/config로 반복
  snapshot을 실행해도 바뀐 env secret을 다시 조회해 새 credential을 사용한다. Full
  connector workflow data-plane, cloud secret manager watch/poll, refresh-token exchange,
  previous/current dual-read retry는 future다.
- [x] secret value가 logs/audit/error에 나타나지 않음: `SecretValue.redacted()`
  operator evidence와 missing-secret error details는 secret 값을 노출하지 않는다.

## 명령

```text
quality:auth-secrets
```

## 제안 테스트

- [x] `test_expired_or_wrong_audience_jwt_is_denied`
- [x] `test_jwks_rotation_keeps_valid_sessions`
- [x] `test_jwt_auth_provider_maps_verified_token_to_principal`
- [x] `test_oidc_profile_loads_discovery_and_jwks_from_env`
- [x] `test_service_account_is_tenant_scoped`
- [x] `test_service_account_claim_is_configurable_from_env`
- [x] `test_session_revocation_policy_denies_revoked_jti`
- [x] `test_revocation_list_loads_from_env`
- [x] `test_connector_refreshes_rotated_secret`
- [x] `test_secret_never_appears_in_operator_evidence`
- [x] `test_env_secret_provider_resolves_named_secret_from_alias`
- [x] `test_api_webhook_ingest_verifies_signature_and_appends_dataset`

---

# Sprint 58B — Anonymization/Pseudonymization

## 목표

production 데이터를 staging, 분석, AI 실험에 안전하게 사용할 수 있게 한다.

- [x] deterministic pseudonymization: `PrivacyTransformPlan`과
  `PrivacyFieldRule(mode="pseudonymize")`가 tenant-scoped HMAC pseudonym을 만든다.
  같은 tenant/scope/value는 같은 pseudonym으로 replay되고 tenant가 다르면 다른 값이 된다.
- [~] irreversible anonymization: `PrivacyFieldRule(mode="anonymize")`가 필드 값을
  `***ANONYMIZED***`로 바꾼다. 정책 registry, irreversible export job, column type별
  anonymization 전략은 future다.
- [~] raw text PII detection/redaction: `PrivacyFieldRule(mode="redact_text")`가 local
  regex로 email, SSN, US phone sample을 `***REDACTED_PII***`로 치환한다. ML/locale-aware
  detector, false-positive review, multilingual PII taxonomy는 future다.
- [~] per-property privacy policy: 현재는 field-name rule tuple로 privacy policy를
  versioned plan에 고정한다. Ontology classification-driven policy, tenant/environment별
  policy registry, approval workflow는 future다.
- [~] environment replication policy: `PrivacyReplicationPolicy`와
  `validate_privacy_replication_policy`가 production에서 staging/analytics/AI 실험 환경으로
  민감 필드를 복제할 때 `PrivacyTransformPlan`이 없거나 민감 필드가 `passthrough`/누락이면
  차단한다. 허용된 경우 `transform_privacy_rows(..., replication_policy=...)` lineage에
  source/target environment, reason, unprotected field, plan version 증거를 남긴다. 실제
  environment replication job, approval workflow, tenant/environment별 registry는 future다.
- [~] reversible mapping은 별도 protected store에 저장: `PrivacyFieldRule(is_reversible=True)`는
  protected mapping store가 없으면 실패하고, `InMemoryProtectedPrivacyMappingStore`에만
  pseudonym과 원본값 매핑을 남긴다. 변환된 row와 lineage, redacted evidence에는 원본값이
  남지 않는다. Durable encrypted store, approval workflow, access-control audit는 future다.
- [~] anonymized dataset lineage 유지: `PrivacyTransformResult.lineage`가 plan name,
  plan version, rule count, row count를 남기고, `PrivacyDatasetRef` source/target pair가
  있으면 원본 dataset version과 anonymized target dataset version을 `privacyDatasetLineage`로
  연결한다. `build_privacy_openlineage_event`는 이 lineage만 사용해 raw PII 값 없이
  OpenLineage-compatible RunEvent artifact를 만든다. Runtime DB `lineage_edges`, outbox,
  OpenLineage backend/CLI 전송 연결은 future다.

## 제안 테스트

- [x] `test_same_identifier_maps_to_same_pseudonym_within_scope`
- [x] `test_pseudonym_differs_across_tenants`
- [x] `test_anonymized_dataset_contains_no_raw_pii_samples`
- [x] `test_privacy_transform_is_versioned_and_replayable`
- [x] `test_reversible_mapping_requires_protected_store`
- [x] `test_reversible_mapping_is_stored_outside_rows_and_lineage`
- [x] `test_production_replication_requires_privacy_plan_for_sensitive_fields`
- [x] `test_replication_policy_rejects_unprotected_sensitive_fields`
- [x] `test_allowed_replication_policy_adds_lineage_without_raw_values`
- [x] `test_anonymized_dataset_lineage_links_source_and_target_versions`
- [x] `test_privacy_openlineage_event_excludes_raw_pii`
- [x] `test_privacy_openlineage_event_is_replay_stable`

---

# Sprint 58C — Right-to-Erasure Lifecycle

## 목표

삭제 요청이 raw/clean/object/search/materialization/DLQ/backup 정책 전체에 전파되게 한다.

- [~] deletion request object: `ErasureRequest`가 tenant, request id,
  subject kind/value, requester, idempotency key, reason, requested-at을 고정하고
  `subjectHash`와 redacted evidence만 노출한다. Durable request table/API는 future다.
- [~] subject identity resolution: `resolve_erasure_subject`가 candidate records를
  tenant-scoped identity field로 해석하고 다른 tenant record는 매칭하지 않는다. Ontology-driven
  subject graph, durable identity registry, cross-dataset resolver는 future다.
- [~] deletion manifest: `ErasureManifest`가 source request, subject hash, action list,
  ready/deferred status를 stable manifest id로 묶는다. 실제 executor/workflow는 future다.
- [~] Object Store tombstone: object record match는 `tombstone_object` manifest action으로
  남는다. ObjectStore write executor와 `object.changed` outbox는 future다.
- [~] Search removal: search document match는 `remove_search_document` action으로 남고
  `is_erased_resource`가 rebuild 중 재등장을 막을 exclusion proof를 제공한다. SearchAdapter
  delete/rebuild integration은 future다.
- [~] Materialized dataset handling: materialization row match는 `rebuild_materialization`
  action으로 남는다. 실제 materialization recompute executor는 future다.
- [~] Iceberg snapshot/retention policy: backup/snapshot match는 retention window 동안
  `defer_backup_expiration`으로 보류되고 retention-until/key-ref evidence를 남긴다. 실제
  production Iceberg snapshot expiration, crypto-shredding, backup manifest rewrite는 future다.
- [~] DLQ 처리: Record DLQ match는 `redact_dead_letter_payload` action으로 남는다. Durable
  DLQ payload redaction executor는 future다.
- [~] audit 최소 보존 정책: manifest는 `minimize_audit_evidence` action을 항상 포함하고
  legal basis와 minimum audit fields만 남긴다. Durable audit compaction executor는 future다.
- [~] backup expiration/crypto-shredding 정책: `ErasureRetentionPolicy`가
  `backup_retention_until`과 `crypto_shredding_key_ref`를 manifest evidence에 고정한다.
  실제 KMS/cloud key destruction은 future다.

## 명령

```text
quality:erasure
```

## 제안 테스트

- [x] `test_erasure_manifest_removes_subject_from_serving_surfaces_without_raw_values`
- [x] `test_erasure_resolution_is_tenant_scoped`
- [x] `test_erasure_manifest_retry_is_idempotent`
- [x] `test_backup_retention_exposes_pending_erasure_state`
- [x] `test_search_rebuild_does_not_resurrect_erased_subject`

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
- [~] object property ← dataset column/expression: `GET /api/objects/{type}/{id}?explain=true`
  경로의 `propertyLineage`가 object property별 source dataset version, source object
  version, source column, source hash, property version, masking state를 노출한다.
  Output-column-level transform lineage와 link source-key mapping은 future다.
- [ ] link ← source key mapping
- [~] insight claim ← evidence object IDs: `build_insight_claim_payload`는 evidence
  object id와 pinned evidence reference 없이 insight claim 생성을 거부한다. Durable
  AI evidence table과 viewer는 future다. 별도의 S63 slice는 Insight Review queue
  저장/API/SDK를 제공하지만, AI evidence table 자체를 대체하지 않는다.
- [~] LLM extraction ← source spans: `EvidenceSourceSpan`은 quote/timecode/bounding box
  좌표를 evidence payload로 만들 수 있고, masked caller에게는 raw quote를 내보내지
  않는다. 실제 LLM extraction executor는 future다.
- [~] prompt/model/version/parameters: `build_llm_extraction_evidence`가 extractor,
  model, prompt version과 model parameter hash를 evidence id에 고정한다.
- [~] confidence and reviewer decision: evidence reference payload가 confidence와
  `humanReviewStatus`를 보관한다. Review workflow와 approval UI는 future다.

## Evidence model

- [~] immutable evidence reference: `EvidenceReference`는 source/model/prompt/extractor
  payload에서 stable id를 만들고 reprocessing 시 새 revision을 만든다.
- [~] source dataset version
- [~] source object version
- [~] extractor version
- [~] model/prompt version
- [~] evidence quote/timecode/bounding box
- [~] human review status

## API/UI

- [~] `GET /api/objects/{type}/{id}?explain=true` 확장: Object explain payload에
  `propertyLineage`가 추가됐고 generated SDK type도 같은 필드를 가진다.
- [~] property별 “왜 이 값인가” 보기: 현재는 API/SDK payload evidence이며 전용 UI는
  future다.
- [ ] insight claim evidence viewer
- [~] LLM 모델 변경 전후 비교: `revise_evidence_reference`가 기존 evidence id를
  보존한 새 revision을 만들지만, UI diff viewer는 future다.

## CI lane

- [x] `quality:ai-evidence`

## 제안 테스트

- [x] `test_object_property_lineage_resolves_to_pinned_dataset_version`
- [x] `test_insight_claim_requires_evidence_objects`
- [x] `test_llm_extraction_pins_prompt_and_model_version`
- [x] `test_reprocessing_preserves_old_evidence_and_creates_new_revision`
- [x] `test_masked_source_span_is_not_exposed_to_unauthorized_user`

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

- [~] authenticated API client: `createFoundryLiteClient`가 bearer token, 공통
  context header, 공통 request wrapper를 받는다. 실제 login/session UI는 future다.
- [~] tenant/user context: `FoundryLiteRequestContext`와 `requestContextHeaders`가
  tenant/user/roles header를 만든다. role picker/session switcher는 future다.
- [x] request ID 표시: Web Operations 상단이 마지막 `X-Request-ID`를 보여준다.
- [x] typed error taxonomy: generated SDK가 `FoundryLiteApiError`로 HTTP status,
  error code, message, details, request id, retryable 여부를 표준화한다.
- [x] retry/backoff helper: `retryWithBackoff`가 retryable error만 bounded backoff로
  재시도한다. 화면별 retry button policy와 copy는 future다.
- [x] idempotency/action-lock helper: 기존 `idempotencyKey` helper, generated SDK의
  `requiresIdempotencyKey` mutation required-key header wiring, missing-key fail-fast,
  `actionLockKey`, `createInFlightActionLock`가 유지된다. Retry UX는 한 사용자 intent에서 만든
  같은 idempotency key를 재사용해야 하며, 동일 click 중복 잠금 버튼 UX는 future다.
- [x] expected object version helper
- [x] cursor pagination helper: `collectCursorPages`가 cursor 기반 API를 안전하게 끝까지
  수집한다. visual pagination/infinite scroll UX는 future다.
- [~] loading/empty/error/degraded state components: 현재는 error/degraded telemetry
  표면만 있고 shared component library는 future다.

## API 문서

- [x] `/docs`와 `/openapi.json` 접근 문서화
- [x] SDK regeneration CI 유지
- [~] frontend API compatibility test: `quality:frontend-backend-surface`,
  `quality:sdk-request-contract`, `quality:frontend-foundation`이 route/helper -> named SDK ->
  proofClass -> proof test -> operator evidence matrix, 114개 browser SDK route surface
  method/path/query/header/body, typed error metadata, 25개 SDK helper runtime behavior,
  Web Operations named-SDK-only 계약을 검증한다. Full browser compatibility matrix는 future다.

## 테스트

- [x] generated SDK surface parity
- [x] browser SDK request/helper-contract proof for current route and helper surfaces
- [x] ontology catalog API/SDK surface for active object/action/link metadata
- [x] dataset catalog/inspect/lineage API/SDK surface for Dataset Explorer start, committed manifest evidence, and lineage drill-down
- [x] Insight Review queue API/SDK surface for list/create/get/assign/decide
- [x] request ID visible on errors
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

- [~] namespace/name 탐색: `GET /api/datasets`와 `client.datasets.list()`는 current backend/API/SDK이고, visual browser UX는 future다.
- [~] version 목록: `GET /api/datasets/{namespace}/{name}/versions`와 `client.datasets.versions(...)`는 current backend/API/SDK이고, visual table/pin UX는 future다.
- [~] preview: `GET /api/datasets/{namespace}/{name}/preview`와 `client.datasets.preview(...)`는 current backend/API/SDK이고, visual preview grid는 future다.
- [~] raw/clean/ops 구분: dataset list가 namespace/classification/storage metadata를 제공하고, visual grouping UX는 future다.
- [~] version pin 표시: `GET /api/datasets/{namespace}/{name}/inspect?version=...`와 `client.datasets.inspect(...)`는 current backend evidence이고, visual pin state는 future다.
- [~] lineage 이동: `GET /api/operations/lineage?resourceId=...`와 `client.operations.lineage.get(...)`는 current backend/API/SDK이고, visual graph navigation UX는 future다.

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

- [~] Insight review queue: `insight_reviews` table, `foundry.insights`, `/api/insights/reviews`,
  and generated `client.insights.reviews.*` provide the backend/API/SDK queue surface. Visual
  queue UI remains future.
- [ ] evidence panel
- [~] approve/reject/assign: API/SDK supports idempotent assignment and terminal approve/reject
  decisions with audit evidence. Human-facing review UI remains future.
- [ ] Action Type 목록
- [ ] ontology metadata 기반 action form
- [ ] human approval policy
- [~] action result and audit: review decisions write `insight_review.created`,
  `insight_review.assigned`, `insight_review.approved`, and `insight_review.rejected`
  audit evidence. Actual action execution result orchestration remains future.
- [~] long-running workflow progress: React `useFoundryLiteLongRunningJob(...)` can start a backend job,
  poll snapshots, and expose phase/status/run id/timestamps/request-id/retryability for bounded screens.
  SDK `streamFoundryLiteOperationEvents(...)` and React `useFoundryLiteOperationEventStream(...)` can consume
  future server-sent event streams with auth/context/request-id headers. Visual progress components and server
  push route implementation remain future.
- [ ] writeback reconciliation 상태

## 안전 UX

- [~] idempotency key 자동 생성/재사용: create/assign/decision endpoints require `Idempotency-Key`
  and generated SDK methods require `{ idempotencyKey }`. Button-state UX remains future.
- [ ] expected object version 전달
- [~] precondition failure 설명: terminal review decisions return conflict instead of silently
  overwriting an approved/rejected review. Human copy/compare UX remains future.
- [ ] `OUTCOME_UNKNOWN`을 성공/실패로 오표시하지 않음
- [ ] high-risk action confirmation
- [ ] retryable action과 compensation action 구분

## 테스트

- [x] Insight Review create is idempotent by tenant and key
- [x] Insight Review list filters status and assignee
- [x] Insight Review decision has one terminal winner
- [x] API create/assign/decision writes durable audit evidence
- [ ] double click produces one action run
- [ ] stale object conflicts before mutation
- [ ] unauthorized action is hidden and denied server-side
- [ ] action evidence links to object/source data
- [ ] outcome unknown enters reconciliation queue

---

# Sprint 64 — Operations/Recovery Console

## 목표

운영자가 DB/버킷/로그를 직접 열지 않고 장애를 조사하고 복구한다.

> 현재 부분 구현 메모: S64 첫 slice는 full visual recovery console이 아니라
> `GET /api/operations/recovery/overview`, `POST /api/operations/backup-restore/restore-mode/{restore_id}/post-restore-validation`,
> generated `client.operations.backupRestore.recoveryOverview()`, and
> `client.operations.backupRestore.postRestoreValidation()`가 S57 preflight, active
> restore-mode traffic gate, latest restore status, latest post-restore validation, and
> required operator next actions를 한 read model과 재개 전 검증 evidence로 묶는
> backend/API/SDK 계약이다. `quality:operations-recovery`와
> `tests/sdk/request_contract.mjs`가 이 route/method를 named SDK-only surface로 검증한다.
> run console 화면, recovery UI, alert dashboard, workflow cancel/reconcile executor는 아직 future scope다.

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
- [~] restore mode status / recovery overview backend/API/SDK

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

현재 S61 slice는 단순 request wrapper 단계에서 한 단계 더 나아가, 프론트가 쓰는 현재
backend route와 SDK helper를 `docs/frontend-api-sdk-surface-matrix.json`에 route/helper ->
named SDK -> proofClass -> proof test -> operator evidence로 고정한다. `quality:frontend-backend-surface`는
FastAPI route가 분류되지 않았거나, frontend-consumable route에 generated SDK method나
request-contract proof가 없거나, SDK helper에 `sdkHelpers` row/export/operator-evidence/helper
proof가 없거나, Web Operations가 다시 raw `/api/...` path를 직접 조립하면 실패한다.
`quality:sdk-request-contract`는 실제 browser SDK를 import해 114개 frontend route surface의
method/path/query/header/body, request-id/context header, idempotency header, typed error metadata를
fake fetch로 검증하고, 25개 `SDK_CLIENT_SURFACE.helpers`의 런타임 동작도 함께 증명한다. 즉 S62-S64 화면을
올리기 전, 현재 사용 가능한 backend surface는 named SDK-only와 request-contract로 잠긴다.
React helper surface는 live ontology catalog workspace model, cursor/list screen state, start-and-poll job state,
그리고 admin console launch/preflight/task-plan model을 제공해 generated static type과 dynamic-only ontology row,
browser-safe action, operator command/runbook row, future row, approval/checklist/blocking evidence를 프론트
화면에서 구분할 수 있게 한다.
`docs/frontend-backend-surface-contract.md`의 Frontend SDK Recipes는 session, dataset explorer,
object/action workspace, large ontology lookup, media, AIP, pipeline builder, long-running operation, admin console,
recovery/operations 화면 조립법을 현재 SDK 표면에 맞춰 유지해야 한다. `docs/sdk-frontend-cookbook.md`와
`@foundry-lite/sdk/screen-recipes`는 같은 화면 조립법을 프론트 개발자가 바로 따라갈 수 있는 예제와
typechecked recipe builder로 제공하며, `quality:frontend-backend-surface`가 이 recipe 계약을 같이 검사한다.
S63의 현재 backend/API/SDK slice는 `client.insights.reviews.list/create/get/assign/decide`와
`quality:insight-review`로 create idempotency, assignment, terminal decision conflict, and audit
evidence를 잠근다. 다만 full login/session, screen-specific retry/backoff copy, visual cursor
pagination components, server push route implementation, visual streaming timeline UX, duplicate-click button state UX, stale-version compare/refresh UI,
permission-denied masking UX, direct migration execution, long-running worker daemon control,
infra bootstrap browser execution, full catalog-driven S62-S64 workspace UX, S63 evidence panel UI,
and action execution orchestration은 아직 후속 product slice다.
