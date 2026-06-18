# Foundry-lite Tricky Failure Modes & Race Condition Checklist

**작성일:** 2026-06-15  
**범위:** Foundry-lite Sprint 문서 기반.
**목적:** 스프린트 문서의 `Dataset → Transform → Ontology → Object → Action → Materialization → Downstream Transform` 폐루프에서 발생 가능한 tricky bug, race condition, fallback bug, 장애 조건, 하드웨어/네트워크/OOM 계열 failure mode를 티어별 체크리스트로 관리한다.

> 체크박스 해석 주의: 이 문서의 `[ ]`는 대부분 “아직 남겨둔 위험/테스트 후보”다. 최신 구현 완료 상태와 직접 동기화되는 체크박스는 [Sprint Evidence Ledger](./sprint-evidence-ledger.md)와 [Commit-Point Risk Register](./commit-point-risk-register.md)의 evidence/status를 따른다. 따라서 이 문서의 미체크 항목은 자동으로 stale 문서가 아니라, future hardening backlog 또는 아직 별도 proof가 필요한 실패 모드다.

---

## 0. 이 문서를 쓰는 방법

- [ ] 새 기능을 구현하기 전, 관련 subsystem 항목을 먼저 읽는다.
- [ ] PR마다 해당 subsystem의 failure mode checklist 중 영향받는 항목을 체크한다.
- [ ] P0/Tier 0 항목은 “구현 완료”가 아니라 “regression test 존재”를 완료 기준으로 본다.
- [ ] 장애 주입 테스트는 정상 E2E보다 우선순위가 높다.
- [ ] 새 production-style 인프라는 [Infra Ratchet](./infra-ratchet.md)을 따라 하나씩만 추가한다.
- [ ] 인프라 ratchet PR은 normal path뿐 아니라 failure-injection, concurrency-race, retry-idempotency, partial-success, recovery-cleanup, operator-evidence를 모두 확인한다.
- [ ] 인프라 ratchet PR은 자기 adapter/profile 테스트와 함께 이미 active인 인프라들과의 조합 테스트를 추가한다. 새 인프라는 단독 green만으로 active-covered가 될 수 없다.
- [x] `[x]`가 붙은 줄의 `test_*` 증거명은 실제 pytest 수집 결과에 있어야 한다. `quality:checklist-evidence` / `check_checklist_evidence.py`가 거짓 완료 체크를 CI에서 차단한다.
- [x] active 인프라와 조합 stack은 관련 tricky item id를 `docs/infra-tricky-matrix.json`에 등록하고 proof class, pytest test, CI command까지 연결해야 한다. `quality:infra-tricky-matrix` / `check_infra_tricky_matrix.py`가 새 인프라의 자동 pull-through shield를 강제한다.
- [ ] 모든 write path는 아래 질문에 답해야 한다.
  - [ ] 진짜 commit point는 어디인가?
  - [ ] cursor, offset, watermark는 언제 전진하는가?
  - [ ] process kill/OOM/network timeout이 commit 직전/직후에 발생하면 어떻게 복구되는가?
  - [ ] retry하면 중복 성공하지 않는가?
  - [ ] 실패가 성공처럼 보이지 않는가?
  - [ ] projection이 stale해도 source of truth를 오염시키지 않는가?
  - [ ] audit/run/action/dataset/object/transaction id로 원인을 추적할 수 있는가?

---

## 1. 티어 정의

### Tier 0 — P0 / 치명적, 조용한 데이터 유실·권한 누출·현실 시스템 불일치

**특징:** 사용자가 즉시 모를 수 있지만, 나중에 복구가 어렵거나 불가능하다.

- [ ] cursor/offset/watermark가 durable commit보다 먼저 전진한다.
- [ ] 외부 시스템은 성공했는데 local object/action state는 실패한다.
- [ ] DB에는 committed version이 있는데 object storage manifest/file이 없다.
- [ ] 다른 tenant 데이터가 한 번이라도 노출된다.
- [ ] CDC stale event가 최신 object를 과거로 되돌린다.
- [ ] backup/restore 후 DB와 object storage가 서로 다른 시점을 본다.
- [ ] action은 성공했는데 audit/action log/object edit/outbox 중 하나가 누락된다.

### Tier 1 — P1 / durable inconsistency, 재처리 가능하지만 운영 사고

**특징:** 데이터 유실은 아닐 수 있으나, run/replay/cleanup 없이는 운영자가 수동 복구해야 한다.

- [ ] sync_run status와 dataset_transaction status가 불일치한다.
- [ ] OPEN transaction이 OOM/crash 이후 방치된다.
- [ ] transform retry가 output version을 두 개 만든다.
- [ ] object reindex progress cursor가 실제 commit보다 먼저 전진한다.
- [ ] outbox/DLQ 상태가 source object version과 맞지 않는다.
- [ ] shadow reindex가 current view를 정확히 재현하지 못한다.

### Tier 2 — P2 / projection stale, fallback semantic drift, UX/API/SDK 의미 불일치

**특징:** source of truth는 살아 있지만, UI/search/SDK/materialization/object set이 다른 의미를 보여준다.

- [ ] Elasticsearch fallback이 원래 검색 의미와 다른 결과를 정상처럼 보여준다.
- [ ] SDK가 idempotency/concurrency 규칙을 숨긴다.
- [ ] UI가 stale object state를 보여준다.
- [x] masking이 display에는 적용되지만 filter/sort/search에는 빠진다.
- [ ] cursor pagination이 query shape/index version과 묶이지 않는다.

### Tier 3 — P3 / 하드웨어·네트워크·리소스·k8s 장애

**특징:** 데이터 정합성보다 작업 중단/장애 전파가 중심이다. 단, commit point와 엮이면 Tier 0/1로 승격한다.

- [ ] OOMKilled가 finally/cleanup을 건너뛴다.
- [ ] SIGTERM/rolling deploy 중 partial work가 남는다.
- [ ] disk full/inode full/WAL full이 partial artifact를 남긴다.
- [ ] DB failover 후 commit outcome이 unknown인데 retry가 중복을 만든다.
- [ ] liveness probe가 healthy long-running worker를 죽인다.
- [ ] network partition에서 DB와 object storage 중 한쪽만 성공한다.

### Tier 4 — P4 / 관측성·테스트 착시·운영자 오판

**특징:** 실제 버그를 발견하지 못하게 만드는 버그다.

- [ ] 실패 원인이 로그에만 있고 DB evidence가 없다.
- [ ] CI smoke가 substring만 보고 통과한다.
- [ ] fake adapter는 통과하지만 production semantics에서 실패한다.
- [ ] error taxonomy가 generic 500으로 뭉개진다.
- [ ] audit에 민감 값이 raw로 저장된다.

---

## 2. 최상위 불변식

- [ ] **Invariant 1 — cursor/offset/watermark는 durable commit보다 먼저 전진하지 않는다.**
- [ ] **Invariant 2 — retry는 같은 논리 결과를 만들어야 하며, 중복 성공을 만들면 안 된다.**
- [ ] **Invariant 3 — Search index, materialized dataset, UI cache, SDK cache는 source of truth가 아니다.**
- [ ] **Invariant 4 — run 내부에서 `latest`를 암묵적으로 다시 resolve하지 않는다.**
- [ ] **Invariant 5 — run 시작 시 `dataset_version_id`, `ontology_version_id`, `object_store_watermark`를 pin 한다.**
- [ ] **Invariant 6 — external side effect는 local DB transaction과 같은 원자성을 갖지 않는다.**
- [ ] **Invariant 7 — external timeout은 failure가 아니라 `outcome_unknown`일 수 있다.**
- [ ] **Invariant 8 — fallback은 성능만 바꾸어야 하며, 의미가 바뀌면 `degraded=true` 또는 hard failure로 표시한다.**
- [ ] **Invariant 9 — 모든 실패는 `request_id`, `run_id`, `dataset_id`, `transaction_id`, `object_id`, `action_run_id` 중 필요한 키로 추적 가능해야 한다.**
- [ ] **Invariant 10 — audit, lineage, action log, outbox, materialization cursor 중 하나라도 누락되면 성공으로 보지 않는다.**

---

# 3. Tier 0 — P0 Critical Checklist

## 3.1 Stream / Kafka Archive

### T0-001 — Stream offset이 archive dataset commit보다 먼저 전진

- [x] **Trigger:** consumer가 offset 100까지 읽고 cursor를 저장한 뒤, Parquet append 또는 dataset commit이 실패한다.
- [x] **Failure:** offset 1~100 event가 raw archive dataset에 영구히 남지 않는다.
- [ ] **Impact:** replay 불가, CDC 증명 불가, object state 누락.
- [x] **Guardrail:** durable offset은 committed dataset version에 포함된 offset만 인정한다.
- [ ] **Guardrail:** topic/partition/offset 범위를 manifest에 저장한다.
- [x] **Guardrail:** cursor update와 dataset version commit을 같은 metadata DB transaction에서 처리한다.
- [ ] **Guardrail:** worker local memory offset은 source of truth로 쓰지 않는다.
- [x] **Regression Test:** `test_stream_offset_not_advanced_when_append_commit_fails`
- [ ] **Regression Test:** `test_stream_worker_crash_after_file_write_before_db_commit_replays_same_offsets`
- [ ] **Regression Test:** `test_stream_duplicate_topic_partition_offset_is_deduped`

### T0-002 — Stream partition partial commit

- [ ] **Trigger:** partition 0 offset 100까지 성공, partition 1 write 실패, global cursor가 전진한다.
- [ ] **Failure:** 특정 partition event가 영구 누락된다.
- [ ] **Guardrail:** micro-batch manifest에 partition별 min/max offset을 저장한다.
- [ ] **Guardrail:** partial failure면 전체 dataset transaction을 abort한다.
- [ ] **Guardrail:** partition별 commit을 허용한다면 manifest와 cursor도 partition별 atomicity를 가져야 한다.
- [ ] **Regression Test:** `test_stream_partial_partition_batch_abort_does_not_advance_any_cursor`

### T0-003 — Kafka consumer rebalance mid-batch

- [ ] **Trigger:** worker A가 offset 100~200 처리 중 rebalance가 일어나 worker B가 같은 partition을 받는다.
- [ ] **Failure:** duplicate processing 또는 offset loss.
- [ ] **Guardrail:** at-least-once + topic/partition/offset dedupe를 기본으로 한다.
- [ ] **Guardrail:** offset commit은 archive dataset commit 후에만 가능하다.
- [ ] **Guardrail:** batch lease fencing을 둔다.
- [ ] **Regression Test:** `test_stream_rebalance_mid_batch_dedupes_offsets`

---

## 3.2 REST / Webhook / Source Sync

### T0-004 — REST pagination cursor가 dataset commit보다 먼저 전진

- [x] **Trigger:** REST pull sync가 page1~page5를 수집하고 nextCursor를 저장했지만 dataset commit이 실패한다.
- [x] **Failure:** page1~page5 데이터가 영구 유실된다.
- [ ] **Guardrail:** `pending_cursor`와 `committed_cursor`를 분리한다.
- [x] **Guardrail:** source cursor advancement는 output dataset commit 성공 후에만 수행한다.
- [ ] **Guardrail:** `sync_run`에는 `extracted_cursor`, `committed_cursor`, `failed_cursor`를 모두 기록한다.
- [x] **Regression Test:** `test_rest_cursor_not_advanced_when_dataset_commit_fails`

### T0-005 — REST mutable pagination drift

- [x] **Trigger:** source API가 page number 기반 pagination만 제공하고, 수집 중 새 row가 삽입된다.
- [x] **Failure:** 중복 또는 누락 row가 발생한다.
- [x] **Guardrail:** production connector는 stable cursor 기반만 허용한다.
- [x] **Guardrail:** page-number connector는 `non_replayable`로 표시한다.
- [ ] **Guardrail:** run 시작 시 `source_window_start`와 `source_window_end`를 고정한다.
- [x] **Regression Test:** `test_rest_mutable_pagination_detected_or_marked_non_replayable`

### T0-006 — Webhook ACK before durable append

- [x] **Trigger:** webhook event를 받은 뒤 dataset append commit 전에 provider에게 2xx를 반환한다.
- [x] **Failure:** commit 실패 시 provider는 retry하지 않고 event가 유실된다.
- [x] **Guardrail:** durable append 또는 durable inbox commit 전에는 2xx를 반환하지 않는다.
- [ ] **Guardrail:** immediate 2xx가 필요하면 `webhook_inbox` durable table을 먼저 commit한다.
- [x] **Regression Test:** `test_webhook_ack_not_sent_before_append_commit_or_has_replay_strategy`

### T0-007 — Webhook dedupe key가 payload hash만 사용

- [x] **Trigger:** 같은 provider event가 volatile timestamp 때문에 다른 payload hash를 가진다.
- [x] **Failure:** duplicate append 또는 duplicate action.
- [x] **Guardrail:** dedupe key 우선순위는 `provider_event_id > delivery_id > stable canonical hash`로 둔다.
- [x] **Guardrail:** canonical hash 계산 시 volatile field를 제거한다.
- [x] **Guardrail:** signature timestamp는 HMAC 대상에 포함하고 replay window 밖이면 거부한다.
- [x] **Regression Test:** `test_webhook_same_event_id_different_payload_is_deduped`
- [x] **Regression Test:** `test_webhook_signature_replay_and_clock_skew_policy`

### T0-008 — REST connector SSRF via redirect/DNS rebinding

- [x] **Trigger:** allowed host가 302 redirect로 `169.254.169.254` 또는 internal/private IP를 가리킨다.
- [x] **Failure:** internal metadata/secret leakage.
- [x] **Guardrail:** initial URL뿐 아니라 every redirect target을 검증한다.
- [x] **Guardrail:** resolved IP가 private/link-local/localhost면 차단한다.
- [x] **Guardrail:** IPv6, decimal IP, octal IP, encoded hostname 테스트를 포함한다.
- [x] **Regression Test:** `test_rest_redirect_to_private_ip_blocked`
- [x] **Regression Test:** `test_rest_dns_rebinding_to_private_ip_blocked`
- [x] **Regression Test:** `test_rest_redirect_encoded_decimal_octal_private_hosts_blocked`

---

## 3.3 Dataset / Storage / Manifest

### T0-009 — Manifest는 storage에 있지만 DB commit 실패

- [x] **Trigger:** staging file write 성공, manifest write 성공, DB `dataset_versions` insert 실패.
- [x] **Failure:** orphan artifact가 남고, cleanup이 잘못되면 committed file을 삭제할 수 있다.
- [x] **Guardrail:** serving은 DB `COMMITTED` version만 인정한다.
- [ ] **Guardrail:** manifest에는 `transaction_id`, `run_id`, `attempt_no`, `dataset_id`, `content_hash`를 포함한다.
- [x] **Guardrail:** orphan cleanup은 reachability check 후 실행한다.
- [x] **Regression Test:** `test_dataset_commit_storage_success_db_failure_creates_orphan_cleanup_evidence`
- [x] **Regression Test (S3 ratchet):** `test_s3_commit_storage_success_db_failure_creates_orphan_cleanup_evidence`

### T0-010 — DB에는 COMMITTED인데 manifest/file이 없음

- [x] **Trigger:** DB commit은 성공했지만 object storage put/read verify가 실패했거나 나중에 파일이 사라진다.
- [x] **Failure:** latest version은 존재하지만 preview/transform이 실패한다.
- [x] **Guardrail:** DB commit 전 manifest stat + read verify를 수행한다.
- [x] **Guardrail:** manifest content hash를 검증한다.
- [x] **Guardrail:** commit 후 storage missing은 `committed_version_storage_missing`으로 진단한다.
- [x] **Guardrail:** committed data object 손상(같은 길이, 다른 내용)은 매 read의 hash 검증으로 `committed_version_storage_corrupt`로 진단한다.
- [x] **Guardrail:** 일시적 read 장애(timeout/403)는 corruption이 아니라 retryable adapter failure로 분류한다.
- [x] **Regression Test:** `test_dataset_commit_db_success_manifest_missing_marks_storage_corruption`
- [x] **Regression Test (S3 ratchet):** `test_s3_committed_manifest_missing_marks_storage_corruption`
- [x] **Regression Test (엔진 경유):** `test_s3_corrupted_data_object_surfaces_through_engine_as_storage_corruption`
- [x] **Regression Test (엔진 경유):** `test_s3_transient_read_during_inspect_surfaces_retryable_not_corruption`

### T0-011 — Multipart upload partial object

- [x] **Trigger:** multipart upload 중 network timeout 또는 process crash.
- [x] **Failure:** Parquet footer가 깨지거나 일부 row만 존재한다.
- [x] **Guardrail:** upload complete 후 byte_size/content_hash를 재검증한다.
- [ ] **Guardrail:** Parquet footer validation을 수행한다. _(범위 결정: storage 계약은 byte-fidelity(size+content_hash)이며 truncation/손상은 매 read hash 검증으로 잡힌다. parquet 구조(footer/magic bytes) 유효성은 compute/read 레이어(DuckDB `read_parquet`) 소관 — S3 storage ratchet 밖, 별도 compute hardening으로 이관. 조용히 건너뛴 것이 아니라 명시적 deferral.)_
- [x] **Guardrail:** writer row_count만 믿지 않고 S3 read-back byte_size/content_hash를 검증한다 (commit뿐 아니라 매 read마다 `_verify_local_copy`).
- [x] **Guardrail:** 진짜 multipart 중단 시 `list_objects_v2`에 안 잡히는 orphan part를 `_abort_multipart_uploads`로 정리한다.
- [x] **Regression Test:** `test_s3_partial_multipart_upload_never_becomes_committed_version`
- [x] **Regression Test:** `test_s3_real_multipart_upload_failure_aborts_orphaned_parts`
- [x] **Regression Test:** `test_s3_read_path_detects_truncated_data_object`
- [x] **Regression Test:** `test_s3_retry_after_storage_timeout_does_not_duplicate_version`
- [x] **Regression Test (엔진 경유 운영 노출):** `test_s3_real_multipart_interrupt_during_upload_is_visible_in_operations`

### T0-012 — Dataset version_number 경합

- [x] **Trigger:** worker A/B가 동시에 latest version 10을 읽고 둘 다 version 11 commit을 시도한다.
- [x] **Failure:** duplicate version, orphan staging, outbox duplicate, lineage 오류.
- [ ] **Guardrail:** tenant_id + dataset_id + branch 단위 advisory lock을 사용한다.
- [x] **Guardrail:** 현재 MVP metadata DB에서는 dataset row `FOR UPDATE` lock 아래에서 version number를 할당한다.
- [x] **Guardrail:** unique violation은 domain conflict로 변환한다.
- [x] **Guardrail:** 실패 attempt artifact cleanup을 수행한다.
- [x] **Regression Test:** `test_concurrent_dataset_commits_allocate_strictly_increasing_versions`
- [x] **Regression Test (S3 ratchet):** `test_s3_concurrent_dataset_commits_allocate_strictly_increasing_versions`
- [x] **Regression Test:** `test_dataset_finalize_cleans_orphan_artifacts_after_version_conflict`

### T0-013 — Abort cleanup이 committed artifact 삭제

- [x] **Trigger:** cleanup이 prefix 기준으로 delete하고 committed version path와 겹친다.
- [x] **Failure:** 기존 dataset version corruption.
- [ ] **Guardrail:** cleanup 대상은 transaction_id manifest로만 enumerate한다.
- [x] **Guardrail:** COMMITTED dataset_versions가 참조하는 manifest/file은 절대 삭제하지 않는다.
- [ ] **Guardrail:** cleanup dry-run 모드를 제공한다.
- [x] **Regression Test:** `test_abort_cleanup_never_deletes_committed_manifest`
- [x] **Regression Test (S3 ratchet):** `test_s3_abort_cleanup_never_deletes_committed_manifest`

---

## 3.4 Schema / Health Check

### T0-014 — Health check가 candidate가 아니라 latest를 검사

- [x] **Trigger:** upload candidate에는 duplicate key가 있지만, check runner가 latest committed version을 읽는다.
- [x] **Failure:** invalid dataset이 committed 된다.
- [x] **Guardrail:** check input은 `transaction_id` 또는 `candidate_manifest_uri`만 허용한다.
- [ ] **Guardrail:** check_result에 `checked_manifest_hash`를 저장한다.
- [ ] **Guardrail:** commit 직전 candidate hash와 check hash 일치를 검증한다.
- [x] **Regression Test:** `test_dataset_health_check_reads_candidate_not_latest`

### T0-015 — Schema compatibility TOCTOU

- [x] **Trigger:** upload A가 schema v3 기준으로 compatibility check를 통과한 뒤, upload B가 v4를 먼저 commit한다.
- [x] **Failure:** schema chain이 논리적으로 깨진다.
- [ ] **Guardrail:** `validated_against_schema_version_id`를 저장한다.
- [x] **Guardrail:** commit 직전 latest schema가 그대로인지 재확인한다.
- [ ] **Guardrail:** latest가 바뀌었으면 compatibility check를 재실행한다.
- [x] **Regression Test:** `test_schema_compatibility_revalidates_if_latest_schema_changes`

### T0-016 — CSV primary key string coercion

- [x] **Trigger:** `order_id` 값 `"00123"`이 numeric inference로 `123`이 된다.
- [x] **Failure:** object_id mismatch, duplicate merge, link break.
- [x] **Guardrail:** primary key column은 string-preserving policy를 적용한다.
- [x] **Guardrail:** schema inference가 key column을 numeric으로 강제하지 못하게 한다.
- [x] **Regression Test:** `test_csv_primary_key_preserves_leading_zeroes`

---

## 3.5 Transform / Lineage

### T0-017 — Transform input latest가 run 중 바뀜

- [x] **Trigger:** transform이 `latest`를 암묵 resolve하고, run 중 input dataset latest가 v11에서 v12로 바뀐다.
- [x] **Failure:** replay 결과가 달라지고 lineage가 거짓말한다.
- [x] **Guardrail:** run planning 단계에서 input `dataset_version_id`를 pin 한다.
- [x] **Guardrail:** SQL template resolve 결과도 version_id 기준으로 저장한다.
- [x] **Regression Test:** `test_transform_input_latest_is_pinned_to_version_id`

### T0-018 — Transform retry가 output version을 두 개 생성

- [ ] **Trigger:** output commit 성공 후 worker crash, workflow retry가 다시 output을 commit한다.
- [ ] **Failure:** 같은 logical run이 multiple output versions를 만든다.
- [ ] **Guardrail:** `transform_run_id + output_dataset_id + logical_step` unique key를 둔다.
- [x] **Guardrail:** retry 전 existing committed output을 확인한다.
- [x] **Regression Test:** `test_transform_retry_after_commit_does_not_create_second_output_version`

### T0-019 — Transform output과 lineage edge 불일치

- [x] **Trigger:** output version commit 성공, lineage edge insert 실패.
- [x] **Failure:** output은 존재하지만 source evidence를 추적할 수 없다.
- [x] **Guardrail:** output version commit과 lineage edge insert를 같은 metadata DB transaction에 묶는다.
- [x] **Guardrail:** serving lineage graph는 committed edge만 노출한다.
- [x] **Regression Test:** `test_transform_output_and_lineage_commit_atomically`

---

## 3.6 Action / Idempotency / Writeback

### T0-020 — Same idempotency key concurrent request race

- [x] **Trigger:** 같은 `Idempotency-Key` 요청 2개가 동시에 들어오고 둘 다 “없음”으로 판단한다.
- [x] **Failure:** action_run 두 개, object_edit 두 개, 또는 unique violation 500.
- [x] **Guardrail:** insert-on-conflict-get-existing 패턴을 사용한다.
- [x] **Guardrail:** idempotency record 생성과 action_run 생성 순서를 고정한다.
- [x] **Regression Test:** `test_action_same_idempotency_key_concurrent_requests_replay_same_action_run`

### T0-021 — Same idempotency key, different body

- [x] **Trigger:** 같은 key로 다른 params/reason/target/version을 제출한다.
- [x] **Failure:** 두 번째 요청이 첫 번째 결과를 받거나 새 action이 생성된다.
- [x] **Guardrail:** canonical request fingerprint를 저장한다.
- [x] **Guardrail:** 같은 key + 다른 fingerprint는 409 `IdempotencyKeyConflict`를 반환한다.
- [x] **Regression Test:** `test_action_same_idempotency_key_different_body_returns_409`

### T0-022 — Action commit atomicity failure

- [x] **Trigger:** object_records update 성공, object_edits insert 실패, action_run succeeded 저장.
- [x] **Failure:** object는 바뀌었지만 action log/audit/outbox가 없다.
- [x] **Guardrail:** action_run status, object_records, object_edits, audit_events, outbox_events를 같은 DB transaction에 묶는다.
- [x] **Regression Test:** `test_action_commit_object_edit_audit_outbox_atomic`

### T0-023 — expectedObjectVersion 누락

- [x] **Trigger:** UI/SDK/API가 current object version 없이 action을 호출한다.
- [x] **Failure:** stale precondition read가 last-write-wins로 이어진다.
- [x] **Guardrail:** server-side에서 `expectedObjectVersion`을 필수로 요구한다.
- [x] **Guardrail:** update는 `WHERE object_version = expectedObjectVersion` 조건을 포함한다.
- [x] **Regression Test:** `test_action_expected_object_version_required`
- [x] **Regression Test:** `test_action_precondition_stale_read_conflicts_on_commit`

### T0-024 — External success + local commit failure

- [ ] **Trigger:** ERP/API writeback 200 OK 후 local DB commit이 deadlock/timeout으로 실패한다.
- [ ] **Failure:** external system은 APPROVED, local object는 PENDING.
- [ ] **Guardrail:** 이 상태는 `FAILED`가 아니라 `COMPENSATION_REQUIRED`로 기록한다.
- [ ] **Guardrail:** `action_writebacks`에 external_ref/request/response/status를 저장한다.
- [ ] **Guardrail:** reconciliation worker와 manual review flow를 둔다.
- [ ] **Regression Test:** `test_action_external_success_local_failure_compensation_required`

### T0-025 — External timeout treated as failure

- [ ] **Trigger:** 외부 writeback call timeout. 실제 외부 시스템은 성공했을 수도 있다.
- [ ] **Failure:** retry가 duplicate side effect를 만든다.
- [ ] **Guardrail:** write attempt 이후 timeout은 `outcome_unknown`으로 분류한다.
- [ ] **Guardrail:** external idempotency key를 반드시 propagate한다.
- [ ] **Regression Test:** `test_action_external_timeout_is_outcome_unknown_not_failed`
- [ ] **Regression Test:** `test_action_retry_reuses_external_idempotency_key`

---

## 3.7 Outbox / DLQ

### T0-026 — Outbox event publish before DB commit

- [x] **Trigger:** object update transaction이 commit되기 전 webhook/Kafka/Slack publish를 직접 호출한다.
- [x] **Failure:** DB rollback 후 외부 시스템만 변경을 관측한다.
- [ ] **Guardrail:** external publish는 outbox worker만 수행한다.
- [x] **Guardrail:** outbox row는 domain DB transaction 안에서 insert한다.
- [x] **Regression Test:** `test_outbox_event_not_published_before_domain_commit`

### T0-027 — Duplicate outbox publish

- [ ] **Trigger:** worker A/B가 같은 pending event를 동시에 pick한다.
- [ ] **Failure:** external event가 중복 전송된다.
- [ ] **Guardrail:** `SELECT FOR UPDATE SKIP LOCKED` 또는 equivalent lease를 사용한다.
- [ ] **Guardrail:** external idempotency key를 사용한다.
- [ ] **Regression Test:** `test_outbox_duplicate_workers_do_not_double_publish`

### T0-028 — DLQ stale event retry

- [ ] **Trigger:** object.changed version 10이 DLQ에 있다가 version 12 처리 후 재시도된다.
- [ ] **Failure:** search/materialization projection이 과거로 회귀한다.
- [ ] **Guardrail:** event payload에 object_version/source_ordering을 포함한다.
- [ ] **Guardrail:** consumer는 stale event를 no-op으로 처리한다.
- [ ] **Regression Test:** `test_dlq_stale_event_retry_noops_if_object_version_old`

---

## 3.8 Materialization / Closed Loop

### T0-029 — Materialization cursor uses created_at instead of committed_at

- [x] **Trigger:** long-running action A는 created_at이 빠르지만 commit이 늦고, action B가 먼저 commit된다.
- [x] **Failure:** cursor가 B까지 전진해 A가 영구 누락된다.
- [x] **Guardrail:** cursor는 `committed_at + action_run_id` 또는 monotonic commit_sequence를 사용한다.
- [x] **Regression Test:** `test_materialization_late_commit_action_not_skipped`

### T0-030 — created_at tie-breaker 누락

- [x] **Trigger:** 여러 action_run이 같은 created_at timestamp를 가진다.
- [x] **Failure:** `WHERE created_at > cursor` 쿼리에서 일부 action이 누락된다.
- [x] **Guardrail:** cursor는 `(committed_at, action_run_id)` tuple을 사용한다.
- [x] **Regression Test:** `test_materialization_created_at_tie_does_not_skip_rows`

### T0-031 — Object snapshot watermark가 wall-clock updated_at

- [x] **Trigger:** server clock skew 또는 transaction commit order mismatch.
- [x] **Failure:** snapshot이 하나의 논리 시점이 아니라 섞인 상태가 된다.
- [x] **Guardrail:** object_change_sequence 또는 DB commit sequence를 사용한다.
- [x] **Guardrail:** snapshot 시작 시 max_object_change_id를 고정한다.
- [x] **Regression Test:** `test_object_snapshot_fixed_watermark_hash_reproducible`

### T0-032 — Snapshot materialization 중 새 action이 반만 섞임

- [x] **Trigger:** snapshot run이 O-1을 읽은 뒤 action commit, 이후 O-2를 읽는다.
- [x] **Failure:** materialized dataset이 mixed logical time을 가진다.
- [x] **Guardrail:** repeatable read transaction 또는 fixed watermark를 사용한다.
- [x] **Regression Test:** `test_object_snapshot_mid_run_action_not_mixed`

### T0-033 — Downstream transform consumes latest instead of materialized version_id

- [x] **Trigger:** materialization.completed event가 version_id 없이 발생하고 downstream이 latest를 resolve한다.
- [x] **Failure:** 다른 run의 output을 읽거나 race로 previous latest를 읽는다.
- [x] **Guardrail:** event payload에 exact materialized dataset_version_id를 포함한다.
- [x] **Regression Test:** `test_downstream_transform_consumes_materialized_version_id_not_latest`

---

## 3.9 CDC / Incremental Object Indexing

### T0-034 — CDC snapshot event overwrites later update

- [x] **Trigger:** lsn=200 update가 먼저 반영되고, 늦게 도착한 snapshot/read event lsn=150이 반영된다.
- [x] **Failure:** object가 과거 상태로 회귀한다.
- [x] **Guardrail:** object/property별 `last_source_ordering`을 저장한다.
- [x] **Guardrail:** incoming ordering <= stored ordering이면 skip한다.
- [x] **Regression Test:** `test_cdc_object_indexing_updates_tombstones_and_skips_stale_events`

### T0-035 — CDC delete tombstone resurrected by stale update

- [x] **Trigger:** delete event lsn=300 이후 late update lsn=250이 도착한다.
- [x] **Failure:** 삭제된 object가 active query에 다시 나타난다.
- [x] **Guardrail:** tombstone도 ordering을 가진다.
- [x] **Guardrail:** stale update는 tombstone 해제 불가.
- [x] **Regression Test:** `test_cdc_object_indexing_updates_tombstones_and_skips_stale_events`

### T0-036 — CDC primary key update

- [x] **Trigger:** before.pk와 after.pk가 다르다.
- [x] **Failure:** old/new object가 모두 active이거나 link/action history가 끊긴다.
- [x] **Guardrail:** PK update policy를 명시한다. 현재 MVP는 `reject/conflict`로 fail-closed 한다.
- [x] **Regression Test:** `test_cdc_pk_update_policy`

### T0-037 — CDC multi-row transaction partial apply

- [x] **Trigger:** source DB transaction 안의 여러 row 변경이 CDC로 들어오고 일부만 object store에 반영된다.
- [x] **Failure:** source transaction atomicity가 object world에서 깨진다.
- [ ] **Guardrail:** source transaction id/lsn group을 기록한다.
- [x] **Guardrail:** partial failure는 retry 가능한 group status로 남긴다.
- [x] **Regression Test:** `test_cdc_source_transaction_group_not_partially_committed_without_status`

---

## 3.10 Shadow Reindex / Search

### T0-038 — Shadow reindex가 action edits replay 없이 alias switch

- [x] **Trigger:** base dataset만 재색인하고 action edits를 반영하지 않은 채 alias switch.
- [x] **Failure:** user action으로 APPROVED된 object가 base PENDING으로 회귀한다.
- [x] **Guardrail:** reindex input은 base dataset version + action_edit cursor range를 포함한다.
- [x] **Guardrail:** validation hash는 current_properties 기준이어야 한다.
- [x] **Regression Test:** `test_shadow_reindex_replays_action_edits`

### T0-039 — Shadow reindex alias switch 중 cursor pagination 혼합

- [x] **Trigger:** page 1은 old index, page 2는 new index에서 조회된다.
- [x] **Failure:** duplicate/missing rows.
- [x] **Guardrail:** cursor token에 active_index_version을 포함한다.
- [x] **Guardrail:** active_index_version이 바뀐 커서는 조용히 섞어 읽지 않고 fail-safe로 거절한다. old index row는 switch 직후 삭제하지 않고 inactive로 남긴다.
- [x] **Regression Test:** `test_shadow_reindex_alias_switch_cursor_version_safe`

### T0-040 — Search stale event overwrites newer document

- [x] **Trigger:** Elasticsearch doc version 12 이후, old object.changed version 11이 retry된다.
- [x] **Failure:** search projection이 과거 상태로 회귀한다.
- [x] **Guardrail:** Elasticsearch doc에 object_version을 저장한다.
- [x] **Guardrail:** update script는 incoming_version > existing_version일 때만 update한다.
- [x] **Regression Test:** `test_search_stale_event_cannot_overwrite_newer_doc`

### T0-041 — Elasticsearch used as source of truth

- [x] **Trigger:** action form이 search hit의 stale state를 그대로 사용한다.
- [x] **Failure:** stale object state 기준으로 action이 실행된다.
- [x] **Guardrail:** action form 진입 시 object store에서 fresh read한다.
- [x] **Guardrail:** search hit에 projection_version/object_version을 표시한다.
- [x] **Regression Test:** `test_action_form_refetches_object_store_after_search_hit`

---

## 3.11 Security / Tenant / Masking

### T0-042 — RLS tenant context leaks across connection pool

- [x] **Trigger:** tenant A request가 connection에 tenant context를 설정하고 pool에 반환. tenant B가 같은 connection 재사용.
- [x] **Failure:** cross-tenant data exposure.
- [x] **Guardrail:** transaction-local `SET LOCAL`을 사용한다.
- [x] **Guardrail:** pool checkout/checkin 시 tenant context reset을 검증한다.
- [x] **Regression Test:** `test_rls_tenant_context_reset_between_pooled_connections`

### T0-043 — Production에서 dev header-trust auth enabled

- [x] **Trigger:** production 환경에 `AUTH_PROFILE=dev_header_trust`.
- [x] **Failure:** x-user-id/x-tenant-id header 조작으로 권한 우회.
- [x] **Guardrail:** production config에서 dev auth는 startup hard fail.
- [ ] **Guardrail:** audit에 auth_provider를 기록한다.
- [x] **Regression Test:** `test_production_refuses_dev_header_trust_auth`

### T0-044 — Masking is display-only

- [x] **Trigger:** margin property는 response에서 masked되지만 filter/sort/search에는 사용 가능.
- [x] **Failure:** 반복 쿼리로 민감 값을 추론할 수 있다.
- [x] **Guardrail:** response/filter/sort/search/dynamic object set filter에 property permission을 적용한다.
- [ ] **Guardrail:** future aggregate/export/materialized dataset 경로에도 같은 property permission을 적용한다.
- [x] **Regression Test:** `test_masked_property_cannot_filter_sort_search`

### T0-045 — Audit reveals sensitive params

- [x] **Trigger:** action params에 token/PII/sensitive note가 있고 audit_events에 raw JSON 저장.
- [x] **Failure:** masking을 우회해 audit에서 노출된다.
- [ ] **Guardrail:** action parameter schema에 sensitivity metadata를 둔다.
- [x] **Guardrail:** audit에는 masked params subset만 저장한다.
- [x] **Regression Test:** `test_action_audit_masks_sensitive_params`

---

## 3.12 Backup / Restore / Deployment

### T0-046 — DB and object storage restore point mismatch

- [ ] **Trigger:** DB backup은 10:05, object storage backup은 10:00.
- [ ] **Failure:** DB에는 committed version이 있지만 manifest/file이 없다.
- [ ] **Guardrail:** backup manifest에 DB snapshot id와 object storage checkpoint를 함께 저장한다.
- [ ] **Guardrail:** restore 후 all COMMITTED dataset_versions manifest reachability check를 수행한다.
- [ ] **Regression Test:** `test_restore_db_object_storage_consistency_check`

### T0-047 — Restore 후 outbox replay duplicates external side effects

- [ ] **Trigger:** restore된 DB에서 outbox event가 pending이지만, 실제 external system에는 이미 publish된 상태.
- [ ] **Failure:** webhook/Slack/ERP side effect 중복.
- [ ] **Guardrail:** restore mode에서 outbox worker는 paused 상태로 시작한다.
- [ ] **Guardrail:** operator reconciliation 전 publish 금지.
- [ ] **Regression Test:** `test_restore_outbox_paused_until_reconciliation`

### T0-048 — Migration job race

- [ ] **Trigger:** API pod 여러 개와 worker가 동시에 migration 실행.
- [ ] **Failure:** schema lock, partial migration, app/schema mismatch.
- [ ] **Guardrail:** dedicated migration job만 migration을 수행한다.
- [ ] **Guardrail:** migration lock을 사용한다.
- [ ] **Regression Test:** `test_migration_job_singleton_no_app_start_race`

---

# 4. Tier 1 — Durable Inconsistency / High Severity Checklist

## 4.1 Sync / Dataset Run State

### T1-001 — sync_run status와 dataset_transaction status 불일치

- [ ] **Condition:** sync_run=COMMITTED인데 dataset_transaction=ABORTED.
- [ ] **Condition:** sync_run=FAILED인데 dataset_version=COMMITTED.
- [ ] **Guardrail:** terminal state reconciliation job을 둔다.
- [ ] **Guardrail:** impossible-state checker를 CI/ops command로 제공한다.
- [ ] **Regression Test:** `test_sync_run_and_dataset_transaction_terminal_states_consistent`

### T1-002 — OOM/crash after upload leaves OPEN transaction

- [x] **Condition:** CSV/Parquet 변환 중 process OOMKilled.
- [ ] **Guardrail:** heartbeat/lease 기반 stale OPEN transaction detector를 둔다.
- [x] **Guardrail:** old OPEN tx는 `STALE` 또는 `ABORTED_BY_WATCHDOG`로 수습한다.
- [x] **Regression Test:** `test_failed_upload_oom_leaves_recoverable_aborted_or_stale_open_tx`

### T1-003 — Disk full / inode full during staging

- [ ] **Condition:** DuckDB temp spill, Parquet temp file, MinIO local volume, Postgres WAL 중 하나가 full.
- [ ] **Guardrail:** temp directory와 durable storage directory를 분리한다.
- [ ] **Guardrail:** low disk watermark readiness fail을 둔다.
- [ ] **Guardrail:** inode monitoring을 추가한다.
- [ ] **Regression Test:** `test_disk_full_during_staging_does_not_commit_partial_output`

---

## 4.2 CSV / PostgreSQL Snapshot

### T1-004 — CSV dialect edge case

- [ ] **Check:** BOM 포함 UTF-8 header.
- [ ] **Check:** CRLF/LF 혼합.
- [ ] **Check:** quoted delimiter.
- [ ] **Check:** multi-line field.
- [ ] **Check:** duplicate column names.
- [ ] **Check:** empty string vs null policy.
- [ ] **Check:** numeric-like string `"00123"`.
- [ ] **Check:** timezone 없는 timestamp.
- [ ] **Regression Test:** `test_csv_dialect_edge_cases_are_stable`

### T1-005 — PostgreSQL snapshot loads entire result into memory

- [ ] **Condition:** `SELECT * FROM orders` large result를 Python list/dataframe에 전부 적재.
- [ ] **Guardrail:** server-side cursor/batch fetch를 사용한다.
- [ ] **Guardrail:** streaming Parquet writer를 사용한다.
- [ ] **Guardrail:** row_group size cap을 둔다.
- [ ] **Regression Test:** `test_postgres_snapshot_streams_without_full_memory_load`

### T1-006 — PostgreSQL snapshot schema changes mid-stream

- [ ] **Condition:** source table schema migration 중 snapshot query 실행.
- [ ] **Guardrail:** repeatable read snapshot 또는 source watermark를 사용한다.
- [ ] **Guardrail:** schema infer 결과와 actual row schema를 마지막에 재검증한다.
- [ ] **Regression Test:** `test_postgres_snapshot_schema_change_mid_stream_detected`

---

## 4.3 Transform / Compute

### T1-007 — DuckDB OOM leaves partial output

- [x] **Condition:** join/sort/group-by 대형 transform 중 OOM 또는 temp disk full.
- [x] **Guardrail:** output은 staging에만 쓴다.
- [x] **Guardrail:** failure 시 output transaction abort + staging cleanup.
- [x] **Regression Test:** `test_duckdb_oom_aborts_output_transaction`

### T1-008 — SQL transform reads raw file path

- [x] **Condition:** SQL이 `read_csv('/tmp/orders.csv')` 같은 raw path를 직접 읽는다.
- [x] **Guardrail:** SQL parser/templating guard로 raw filesystem access를 막는다.
- [x] **Guardrail:** `{{ input('namespace.name') }}` relation만 허용한다.
- [x] **Regression Test:** `test_sql_transform_cannot_read_arbitrary_filesystem_path`

### T1-009 — Python transform accesses raw storage credentials/path

- [x] **Condition:** Python transform 사용자 코드가 S3/MinIO path를 직접 조작한다.
- [x] **Guardrail:** 안전한 Python SDK/sandbox가 생기기 전까지 Python transform은 fail-closed로 거부한다.
- [ ] **Guardrail:** SDK Input/Output abstraction만 제공한다.
- [x] **Guardrail:** storage credential을 사용자 코드에 노출하지 않는다.
- [x] **Regression Test:** `test_python_transform_cannot_access_raw_storage_path`

### T1-010 — Spark speculative execution double-writes output

- [ ] **Condition:** Spark task speculative execution 또는 driver retry.
- [ ] **Guardrail:** attempt-specific staging path를 사용한다.
- [ ] **Guardrail:** driver가 collectOutputs 후 Foundry commit만 finalizes한다.
- [ ] **Regression Test:** `test_spark_speculative_execution_does_not_double_commit_output`

---

## 4.4 Ontology / Activation

### T1-011 — Ontology activation and index run race

- [ ] **Condition:** index run 시작 후 active ontology가 v3에서 v4로 바뀐다.
- [ ] **Guardrail:** index_run에 ontology_version_id를 pin 한다.
- [ ] **Guardrail:** run 중 active latest 재조회 금지.
- [ ] **Regression Test:** `test_index_run_pins_ontology_version`

### T1-012 — Active ontology schema drift

- [ ] **Condition:** ontology activation 후 backing dataset latest schema가 breaking change.
- [ ] **Guardrail:** ontology mapping은 compatible schema contract와 연결한다.
- [ ] **Guardrail:** latest schema drift 시 ontology health degraded.
- [ ] **Regression Test:** `test_ontology_health_degraded_when_backing_schema_breaks`

### T1-013 — Draft import partial success

- [ ] **Condition:** YAML import 중 일부 object/property row만 저장되고 validation fail.
- [ ] **Guardrail:** draft import는 transaction으로 처리한다.
- [ ] **Regression Test:** `test_ontology_import_failure_rolls_back_partial_rows`

---

## 4.5 Object Store / Index

### T1-014 — Base/edit merge policy regression

- [x] **Condition:** edit_only property가 source update로 덮인다.
- [x] **Guardrail:** base_properties와 edit_properties를 물리 분리한다.
- [x] **Guardrail:** current_properties는 merge policy로만 계산한다.
- [x] **Regression Test:** `test_object_merge_edit_only_not_overwritten_by_source`

### T1-015 — object_version increment missing

- [x] **Condition:** base/edit/link/tombstone/conflict resolve 중 하나에서 object_version 증가 누락.
- [x] **Guardrail:** current view에 영향을 주는 모든 mutation은 object_version을 증가시킨다.
- [ ] **Guardrail:** DB trigger 또는 invariant checker를 둔다.
- [x] **Regression Test:** `test_object_version_increments_for_base_and_edit_updates`

### T1-016 — Snapshot indexer progress cursor advances before upsert commit

- [x] **Condition:** rows 1~1000 read 후 progress=1000 저장, bulk upsert 실패.
- [x] **Guardrail:** progress cursor는 successful DB commit 이후 전진한다.
- [x] **Regression Test:** `test_index_progress_cursor_advances_only_after_bulk_upsert_commit`

### T1-017 — Same dataset version reindex is not idempotent

- [x] **Condition:** 같은 dataset_version을 재색인했는데 object_version이 불필요하게 증가한다.
- [x] **Guardrail:** unchanged base patch는 no-op으로 처리한다.
- [x] **Regression Test:** `test_reindex_same_dataset_version_idempotent`

---

## 4.6 Object Query / Object Set / Link

### T1-018 — Object Query cursor is not signed

- [ ] **Condition:** cursor token에 HMAC이 없고 user가 object_id/offset을 조작한다.
- [ ] **Guardrail:** cursor는 signed opaque token으로 만든다.
- [ ] **Regression Test:** `test_object_query_cursor_signed_tamper_proof_query_shape_bound`

### T1-019 — Object Query uses memory slicing

- [ ] **Condition:** DB에서 전체 row를 load하고 앱 메모리에서 sort/slice.
- [ ] **Guardrail:** DB-backed keyset pagination을 사용한다.
- [ ] **Regression Test:** `test_object_query_db_backed_keyset_no_memory_slice`

### T1-020 — Dynamic Object Set bypasses page limit

- [ ] **Condition:** internal query가 `limit=1_000_000`으로 object set을 평가한다.
- [ ] **Guardrail:** object set evaluation도 public query service와 pagination contract를 따른다.
- [ ] **Regression Test:** `test_dynamic_object_set_cannot_bypass_page_limit`

### T1-021 — Static Object Set permission drift

- [x] **Condition:** admin이 만든 static set을 viewer가 조회할 때 object-level permission 재검사 누락.
- [x] **Guardrail:** membership과 read permission은 별도다.
- [x] **Regression Test:** `test_static_object_set_rechecks_object_permission`

### T1-022 — Link traversal cross-tenant join

- [ ] **Condition:** Order.customer_id와 다른 tenant Customer.object_id가 같은 값.
- [ ] **Guardrail:** 모든 link query에 tenant_id와 ontology_version_id를 포함한다.
- [ ] **Regression Test:** `test_link_traversal_never_crosses_tenant_without_policy`

---

## 4.7 Search / Reindex

### T1-023 — Shadow reindex validation hash too weak

- [x] **Condition:** count/object_id hash만 비교하고 current_properties/tombstone/link를 비교하지 않는다.
- [x] **Guardrail:** canonical JSON projection hash를 사용한다.
- [x] **Regression Test:** `test_shadow_reindex_validation_hash_includes_current_properties_and_tombstone`

### T1-024 — Reindex catch-up misses edits after watermark

- [x] **Condition:** reindex watermark 이후 들어온 action edits가 alias switch 전 반영되지 않는다.
- [x] **Guardrail:** switch 직전 delta edits > watermark를 replay한다.
- [x] **Regression Test:** `test_shadow_reindex_catches_up_delta_edits_before_switch`

### T1-025 — Old index cleanup before cursor TTL

- [ ] **Condition:** alias switch 후 old index를 즉시 삭제하고 기존 cursor page 2 요청이 실패한다.
- [ ] **Guardrail:** old index는 cursor TTL 동안 유지한다.
- [ ] **Regression Test:** `test_old_search_index_retained_until_cursor_ttl`

---

## 4.8 Operations / Run / Replay

### T1-026 — Retry creates new side effect

- [ ] **Condition:** retry command가 같은 logical run이 아니라 새 run/action/side effect를 만든다.
- [ ] **Guardrail:** retry target에는 original run_id/action_run_id/outbox_event_id를 포함한다.
- [ ] **Regression Test:** `test_operations_retry_does_not_create_duplicate_side_effect`

### T1-027 — Failure reason only in pod logs

- [ ] **Condition:** run detail에는 generic failed만 있고 stack/error payload는 pod log에만 있다.
- [ ] **Guardrail:** normalized error payload를 DB에 저장한다.
- [ ] **Regression Test:** `test_run_failure_persists_operator_facing_error_payload`

---

# 5. Tier 2 — Projection / UX / SDK / Fallback Checklist

## 5.1 SDK / Generated Client

### T2-001 — SDK hides idempotency/concurrency

- [ ] **Condition:** generated SDK action apply가 idempotencyKey/expectedObjectVersion 없이 호출 가능.
- [ ] **Guardrail:** SDK 타입에서 두 값을 required로 노출한다.
- [ ] **Regression Test:** `test_sdk_apply_requires_expected_object_version_and_idempotency_key`

### T2-002 — Browser SDK and package SDK drift

- [ ] **Condition:** browser SDK와 npm SDK가 다른 template에서 생성되어 타입이 다르다.
- [ ] **Guardrail:** single schema IR에서 생성한다.
- [ ] **Regression Test:** `test_generated_sdk_browser_and_package_outputs_have_parity`

### T2-003 — SDK generated from draft ontology

- [ ] **Condition:** active가 아닌 draft ontology로 SDK가 생성된다.
- [ ] **Guardrail:** SDK generator는 active ontology_version_id만 입력으로 받는다.
- [ ] **Regression Test:** `test_sdk_generation_refuses_draft_ontology`

---

## 5.2 UI

### T2-004 — Action success 후 stale object state 표시

- [ ] **Condition:** success toast 후 object detail은 여전히 PENDING.
- [ ] **Guardrail:** action response에 new object_version/current_properties를 포함하거나 mandatory re-fetch.
- [ ] **Regression Test:** `test_ui_refreshes_object_detail_after_action_success`

### T2-005 — Side effect failure hidden as success

- [ ] **Condition:** local object edit은 성공, Slack/webhook은 실패, UI는 green success만 표시.
- [ ] **Guardrail:** `local_commit_status`, `writeback_status`, `side_effect_status`를 분리 표시한다.
- [ ] **Regression Test:** `test_ui_distinguishes_side_effect_failure_from_object_success`

### T2-006 — Generic error hides root cause

- [ ] **Condition:** validation/precondition/concurrency/permission/writeback failure가 모두 generic error.
- [ ] **Guardrail:** UI error category를 구분한다.
- [ ] **Regression Test:** `test_ui_distinguishes_action_validation_precondition_conflict_writeback_errors`

---

## 5.3 Search / Fallback

### T2-007 — Search fallback changes semantics without degraded flag

- [ ] **Condition:** Elasticsearch down 후 Postgres ILIKE fallback을 정상 결과처럼 보여준다.
- [ ] **Guardrail:** response에 `degraded=true`, `planner=postgres_fallback`을 포함한다.
- [ ] **Regression Test:** `test_search_fallback_marks_degraded`

### T2-008 — Full-text fallback creates saved set

- [ ] **Condition:** degraded search result로 dynamic/static object set 생성 허용.
- [ ] **Guardrail:** degraded result로 저장 시 warning 또는 hard block.
- [ ] **Regression Test:** `test_degraded_search_result_cannot_silently_create_object_set`

---

## 5.4 Pagination / Query

### T2-009 — Mutable sort key changes between pages

- [ ] **Condition:** riskScore desc 정렬 중 riskScore가 바뀌어 중복/누락.
- [ ] **Guardrail:** snapshot watermark 또는 cursor에 sort key + object_id tie-breaker 포함.
- [ ] **Regression Test:** `test_object_query_mutable_sort_key_does_not_duplicate_or_skip`

### T2-010 — JSON numeric sort is lexicographic

- [ ] **Condition:** `"100" < "20"`처럼 문자열 정렬된다.
- [ ] **Guardrail:** ontology type 기반 SQL CAST를 사용한다.
- [ ] **Regression Test:** `test_object_query_numeric_property_casts_for_sort_and_filter`

---

# 6. Tier 3 — Hardware / Network / Resource Failure Checklist

## 6.1 OOM / Memory

- [ ] **T3-001:** CSV upload OOMKilled recovery test 존재.
- [ ] **T3-002:** DuckDB transform OOMKilled recovery test 존재.
- [ ] **T3-003:** Python transform MemoryError vs container OOMKilled 구분.
- [ ] **T3-004:** Elasticsearch bulk indexing memory budget test 존재.
- [ ] **T3-005:** Materialization snapshot memory budget test 존재.
- [ ] **T3-006:** API/worker node pool 또는 resource isolation 계획 존재.
- [x] **T3-007:** OOMKilled 후 stale run/transaction detector 존재.

## 6.2 Disk / Inode / WAL

- [ ] **T3-008:** temp directory와 durable storage directory 분리.
- [ ] **T3-009:** low disk watermark readiness fail.
- [ ] **T3-010:** inode exhaustion alert.
- [ ] **T3-011:** Postgres WAL full / CDC slot lag alert.
- [ ] **T3-012:** DuckDB temp spill quota.
- [ ] **T3-013:** object storage volume full failure injection test.
- [ ] **T3-014:** cleanup job이 committed artifact 삭제하지 않는 reachability check.

## 6.3 Network

- [ ] **T3-015:** DB reachable / object storage unreachable condition handled.
- [ ] **T3-016:** object storage reachable / DB unreachable condition handled.
- [ ] **T3-017:** DB failover after commit request creates `outcome_unknown` handling.
- [ ] **T3-018:** DNS flapping retry with jitter.
- [ ] **T3-019:** TLS certificate rotation mid-run handling.
- [ ] **T3-020:** external REST timeout classification.
- [ ] **T3-021:** Kafka rebalance mid-batch test.
- [ ] **T3-022:** Elasticsearch timeout does not block source of truth commit.

## 6.4 Kubernetes / Deployment

- [ ] **T3-023:** SIGTERM during dataset commit recovery test.
- [ ] **T3-024:** SIGTERM during action commit recovery test.
- [ ] **T3-025:** preStop hook and terminationGracePeriod configured.
- [ ] **T3-026:** liveness probe does not kill long-running healthy worker.
- [ ] **T3-027:** readiness probe checks required dependencies.
- [ ] **T3-028:** app starts only after migrations complete.
- [ ] **T3-029:** rolling deploy schema version skew test.
- [ ] **T3-030:** migration job is singleton.

## 6.5 Clock / Time

- [ ] **T3-031:** correctness cursor does not use wall-clock.
- [ ] **T3-032:** NTP/clock skew alert.
- [x] **T3-033:** signature timestamp validation has skew policy.
- [ ] **T3-034:** TTL cleanup does not use local pod time as source of truth.
- [ ] **T3-035:** timezone UTC fixed for transform/materialization.

## 6.6 File Descriptor / Process

- [ ] **T3-036:** DuckDB connections closed.
- [ ] **T3-037:** Parquet file handles closed.
- [ ] **T3-038:** HTTP clients closed/reused safely.
- [ ] **T3-039:** fd metrics and stress test exist.
- [ ] **T3-040:** worker crash loop backoff configured.

---

# 7. Tier 4 — Observability / Testing Illusion Checklist

## 7.1 Observability

- [ ] **T4-001:** run failure stores operator-facing error payload in DB.
- [ ] **T4-002:** raw external error is masked but traceable.
- [ ] **T4-003:** retryable/timeout/outcome_unknown/idempotency_conflict fields exist.
- [ ] **T4-004:** request_id, run_id, transaction_id, action_run_id, object_id correlation exists.
- [ ] **T4-005:** outbox/DLQ events visible in Operations UI/CLI.
- [ ] **T4-006:** orphan cleanup results are auditable.
- [ ] **T4-007:** impossible-state checker exists.

## 7.2 Testing Illusion

- [ ] **T4-008:** CLI smoke output is strict JSON parsed, not substring checked.
- [ ] **T4-009:** fast CI profile and release profile are separated.
- [ ] **T4-010:** release profile includes 100k/1M row performance smoke.
- [ ] **T4-011:** fake adapter contract test includes failure injection.
- [ ] **T4-012:** production-like adapter smoke exists for Postgres/MinIO/Kafka/Elasticsearch.
- [ ] **T4-013:** coverage excludes generated/mocked-only paths from false confidence.
- [ ] **T4-014:** E2E verifies data correctness, not only HTTP 200.
- [ ] **T4-015:** hash comparison includes current_properties, tombstone, links, not only count.

## 7.3 Error Taxonomy

- [ ] **T4-016:** `RETRYABLE_TIMEOUT` defined.
- [ ] **T4-017:** `OUTCOME_UNKNOWN` defined.
- [ ] **T4-018:** `CONFLICT_IDEMPOTENCY` defined.
- [ ] **T4-019:** `CONFLICT_OBJECT_VERSION` defined.
- [ ] **T4-020:** `SCHEMA_INCOMPATIBLE` defined.
- [ ] **T4-021:** `PERMISSION_DENIED` defined.
- [ ] **T4-022:** `STORAGE_MISSING` defined.
- [ ] **T4-023:** `COMPENSATION_REQUIRED` defined.
- [ ] **T4-024:** `DEGRADED_PROJECTION` defined.
- [ ] **T4-025:** `STALE_EVENT_SKIPPED` defined.

---

# 8. Subsystem-by-Subsystem Additional Checklist

## A. Dataset / Storage / Manifest

- [x] A1. manifest JSON은 valid하지만 file list 중 하나가 없는 경우를 검증한다.
- [x] A2. file byte_size/hash는 맞지만 row_count metadata가 잘못된 경우를 검증한다. (DuckDB가 권위 있는 count, post-commit gate가 DB 메타 대조; `test_s3_committed_manifest_row_count_matches_actual_parquet_rows`가 manifest row_count == 실제 parquet rows 불변식 고정)
- [ ] A3. Parquet schema와 dataset*schemas schema_hash가 다른 경우를 검증한다. *(범위: schema 검증은 compute/check 레이어 — S3 storage ratchet 밖. `VERIFY-SCHEMA-COMPATIBILITY-TOCTOU` 참고)\_
- [x] A4. manifest_uri가 같은데 content overwrite가 불가능하게 한다. (`_guard_version_not_committed`가 committed version 재사용을 non-retryable conflict로 거부; `test_s3_duplicate_version_commit_is_rejected_without_destroying_existing`)
- [x] A5. staging path transaction*id collision을 방지한다. *(transaction*id는 engine `_new_id("dstx")` uuid로 생성돼 충돌 불가; 경로 격리는 `test_s3_staging_cleanup_is_isolated_per_transaction`이 증명)*
- [x] A6. abort cleanup이 retry 중인 attempt file을 삭제하지 않는다. (commit-실패 cleanup은 version-key, staging cleanup은 transaction-key로 분리; `test_s3_staging_cleanup_is_isolated_per_transaction`)
- [x] A7. signedUrl expired를 dataset corruption으로 오판하지 않는다. (S3 어댑터는 presigned URL이 아니라 boto3 자격증명 사용; 403/만료는 non-404 → retryable AdapterError, corruption 아님; `test_s3_access_expiry_on_read_is_retryable_not_corruption`)
- [x] A8. object storage list incomplete 상황에서도 cleanup이 안전하다. (`test_s3_failed_commit_cleanup_uses_known_keys_not_listing` — failed-commit cleanup은 list 대신 known key로 삭제)
- [x] A9. APPEND transaction 동시 commit ordering을 검증한다. (APPEND는 구현·사용됨(webhook/stream); dataset FOR UPDATE lock + strictly-increasing version_number로 SNAPSHOT과 동일 경로에서 순서 보장; `VERIFY-DATASET-VERSION-CONCURRENCY`, `test_s3_concurrent_dataset_commits_allocate_strictly_increasing_versions`)
- [x] A10. SNAPSHOT commit 중 downstream transform이 previous latest를 읽지 않는다.
- [ ] A11. branch HEAD와 global latest를 혼동하지 않는다.
- [x] A12. content_hash 기준을 compressed bytes/logical rows 중 하나로 고정한다. (압축 parquet 파일 바이트 hash(`_file_hash`)로 고정; dedup 키가 아니라 verify/trace 키로 사용 — `VERIFY-DATASET-SAME-CONTENT-REATTACH`, S05-A4)
- [ ] A13. CSV null/empty string policy가 schema/check/ontology에서 일관된다. _(범위: CSV 파싱/check 정책은 compute/check 레이어 — S3 storage ratchet 밖)_
- [ ] A14. not*null check가 whitespace-only string을 어떻게 볼지 명시한다. *(범위: check 레이어 — S3 storage ratchet 밖)\_
- [ ] A15. unique check case-sensitive/case-insensitive policy를 명시한다. _(범위: check 레이어 — S3 storage ratchet 밖)_
- [x] A16. 제품이 `FOUNDRY_LITE_ADAPTER_PROFILE=s3-storage` 설정으로 부팅될 때 composition-root가 실제로 S3 어댑터를 선택·구동한다 (어댑터 직접 주입이 아니라 선택 경로 + HTTP 엔트리포인트 e2e). (`test_s3_composition_root_selects_adapter_from_profile_and_runs_full_cycle`, `test_s3_api_end_to_end_preview_reads_through_s3_and_surfaces_corruption`)

### A-ICE. Iceberg table catalog (2번째 인프라 ratchet, profile `iceberg`)

각 dataset version = Iceberg table snapshot 1개, `manifest_uri`에 `snapshot_id` 핀. 데이터/메타데이터는 object storage(S3/MinIO), SQL catalog가 포인터 추적.

- [x] AI1. catalog 스냅샷 commit 후 DB dataset_versions commit 실패 시 orphan 스냅샷을 정리한다(DB가 SoT). (`test_iceberg_snapshot_committed_db_failure_cleans_up_orphan_snapshot`)
- [x] AI2. DB version은 COMMITTED인데 Iceberg 테이블/메타데이터가 사라진 경우를 `committed_version_storage_missing`으로 진단한다. (`test_iceberg_missing_table_on_read_marks_storage_corruption`)
- [x] AI3. metadata.json이 파싱 불가(손상)면 retryable이 아니라 `committed_version_storage_corrupt`로 진단한다. (`test_iceberg_corrupt_table_metadata_is_corruption_not_retryable`)
- [x] AI4. catalog가 가리키는 S3 데이터파일 손상을 corruption으로 진단한다. (`test_iceberg_corrupted_data_file_surfaces_through_engine_as_corruption`)
- [x] AI5. catalog outage(commit 실패)가 FAILED run + retryable adapterFailure로 Operations에 노출된다. (`test_iceberg_catalog_failure_is_visible_in_operations`)
- [x] AI6. 모호한 commit(timeout) 후 retry가 중복 version을 만들지 않는다. (`test_iceberg_retry_after_ambiguous_commit_does_not_duplicate_version`)
- [x] AI7. 같은 version id 재사용을 conflict로 거부하고, duplicate guard의 catalog timeout을 "없는 version"으로 오판하지 않는다. (`test_iceberg_duplicate_version_commit_is_rejected`, `test_iceberg_duplicate_guard_transient_catalog_error_is_retryable`)
- [x] AI8. 호환 스키마 진화(컬럼 추가)를 지원하고 옛 버전 핀 읽기는 옛 내용을 유지한다. (`test_iceberg_compatible_schema_evolution_across_versions`)
- [x] AI9. 스냅샷 격리 — 나중 commit/out-of-band head 변경이 이미 commit된 version의 핀 읽기를 바꾸지 않는다. (`test_iceberg_adapter_normal_path_commits_loads_and_isolates_snapshots`, `test_iceberg_pinned_snapshot_survives_out_of_band_pointer_change`)
- [x] AI10. 코어 엔진 + S3 warehouse end-to-end: composition-root가 `iceberg` 프로필 선택→ensure/upload/preview가 MinIO 위 Iceberg로 라운드트립. (`test_iceberg_engine_and_s3_warehouse_end_to_end`)
- [x] AI11. 반복 commit이 모든 version의 핀 내용을 무손실로 유지한다. 동시 same-table commit은 version allocator(SELECT FOR UPDATE)가 직렬화; Iceberg optimistic concurrency는 backstop. (`test_iceberg_repeated_commits_keep_every_version_independently_readable`)
- [x] AI12. Iceberg manifest의 `content_hash`는 URI/size/row_count 토큰이 아니라 실제 object bytes hash이며, 같은 크기 tamper도 corruption으로 진단한다. (`test_iceberg_manifest_hash_is_real_object_bytes_and_catches_same_size_tamper`)
- [x] AI13. `load_manifest()`가 public manifest metadata인 `branch`와 `created_at`을 commit 시점 값으로 복원한다. (`test_iceberg_adapter_normal_path_commits_loads_and_isolates_snapshots`)

## B. Source / Sync / Connector

- [ ] B1. testConnection 성공과 actual query 권한 차이를 검증한다.
- [ ] B2. secretRef rotation 중 old credential retry를 처리한다.
- [ ] B3. REST 429 Retry-After를 준수한다.
- [ ] B4. REST retry가 non-idempotent endpoint를 다시 호출하지 않는다.
- [x] B5. REST redirect private IP SSRF를 막는다.
- [x] B6. DNS rebinding을 막는다.
- [x] B7. webhook signature timestamp clock skew policy를 둔다.
- [x] B8. webhook dedupe key에서 volatile field를 제거한다.
- [x] B9. webhook event_id uniqueness scope를 provider/source별로 잡는다.
- [ ] B10. webhook append transaction 실패 후 provider retry 전략이 있다.
- [x] B11. provider retry를 원하면 durable commit 전 2xx를 주지 않는다.
- [ ] B12. source schema inference sample에 없는 later page column을 처리한다.
- [ ] B13. Postgres snapshot source schema change를 감지한다.
- [ ] B14. long-running snapshot이 source DB vacuum bloat를 만들지 않게 한다.
- [ ] B15. connector timeout retry storm을 backoff/jitter로 제어한다.

## C. Transform / Compute

- [x] C1. SQL template injection으로 unauthorized dataset read가 불가능하다.
- [ ] C2. transform YAML이 tenant namespace를 누락하지 않는다.
- [ ] C3. input/output dataset이 같은 self-overwrite case를 처리한다.
- [x] C4. transform output health check failed 시 lineage edge가 committed처럼 남지 않는다.
- [ ] C5. failed transform staging cleanup이 previous successful output을 삭제하지 않는다.
- [ ] C6. pandas object dtype output으로 schema instability가 생기지 않는다.
- [ ] C7. timezone parsing이 DuckDB/Python/Spark에서 일관된다.
- [ ] C8. DuckDB version upgrade가 schema/hash를 바꾸는지 테스트한다.
- [ ] C9. Spark driver success but executor output missing case를 검증한다. _(분산 클러스터 전용 — local[1] 재현 불가, 실 클러스터 필요로 deferred. infra-ratchet.md Spark scope note)_
- [ ] C10. Spark timeout 후 cluster job이 계속 실행되지 않게 cancel한다. _(분산 전용 — deferred; local[1] timeout은 `_compute_error` timeout으로 분류돼 output transaction abort됨)_
- [ ] C11. Spark dynamic allocation/shuffle failure를 처리한다. _(분산 전용 — deferred)_
- [ ] C12. Spark speculative task double-write를 막는다. _(= T1-010, 분산 전용 — deferred; local single-file 출력은 `_write_single_parquet`가 1개 part만 promote)_
- [x] C13. Spark도 Foundry lineage model을 사용한다. (`test_spark_engine_transform_commits_with_lineage_and_health` — 엔진이 Spark로 transform 실행, version+lineage 커밋, transform-service-core 수정 0)
- [x] C14. DuckDB/Spark fallback이 SQL semantics를 바꾸지 않는다. (`test_spark_and_duckdb_produce_equivalent_transform_output`)
- [ ] C15. compute resource config default가 unbounded memory가 아니다.

### C-SPARK. Spark ComputeAdapter (3번째 인프라 ratchet, profile `spark`)

`FOUNDRY_LITE_COMPUTE_PROFILE=spark`로 storage와 독립 선택. 엔진이 pinned version을 로컬 parquet로 materialize → Spark는 storage 내부 모름. local[1] 세션.

- [x] CS1. adapter-contract + normal-path: CSV→parquet→preview/inspect/check가 Spark 프로필로 동작. (`test_spark_compute_adapter_contract_parity`)
- [x] CS2. Spark 디렉터리 출력을 단일 parquet 파일 target으로 promote(part 1개만), 잔여 dir 없음. (`test_spark_transform_substitutes_inputs_and_writes_single_parquet_file`)
- [x] CS3. SQL semantics가 DuckDB와 동일(parity). (`test_spark_and_duckdb_produce_equivalent_transform_output`)
- [x] CS4. unsupported plan/invalid CSV는 non-retryable validation으로 분류. (`test_spark_unsupported_plan_kind_is_validation_error`, `test_spark_invalid_csv_input_is_validation_error`)
- [x] CS5. 엔진이 Spark로 transform 실행, transform-service-core 수정 없이 version+lineage 커밋. (`test_spark_engine_transform_commits_with_lineage_and_health`)
- [x] CS6. Spark job 실패 시 output transaction abort — 커밋된 output version 없음 + FAILED transform run. (`test_spark_transform_failure_aborts_output_transaction`)
- [x] CS7. 같은 SparkSession에서 동시에 transform이 실행돼도 session-scoped temp view가 서로 덮어쓰지 않는다. (`test_spark_concurrent_transforms_use_isolated_temp_views`)
- [x] CS8. S3+Iceberg+Spark 조합: `adapter_profile=iceberg` + `FOUNDRY_LITE_COMPUTE_PROFILE=spark`에서 Spark가 Iceberg-on-S3 pinned input을 읽어 transform하고 output을 Iceberg-on-S3로 커밋한다. (`test_iceberg_s3_storage_with_spark_compute_end_to_end`)
- [x] CS9. S3+Iceberg+Spark 조합에서 Spark transform 실패가 output transaction을 abort하고 committed output version을 남기지 않는다. (`test_iceberg_s3_spark_failure_aborts_without_output_version`)
- [ ] CS10. 분산 클러스터 전용 모드(speculative double-write/executor-output-missing/timeout-cancel/shuffle failure)는 local[1] 재현 불가 — 실 클러스터 필요로 deferred (C9~C12, infra-ratchet.md Spark scope note).

### C-TEMPORAL. Temporal WorkflowAdapter (4번째 인프라 ratchet, profile `temporal`)

`FOUNDRY_LITE_WORKFLOW_PROFILE=temporal`로 storage/compute와 독립 선택. 어댑터는 workflow를 등록된 string 타입명으로 시작하므로 workflow 정의를 import하지 않는다(worker만 등록). async 코어 + sync 브리지. time-skipping 테스트 서버로 retry backoff/execution timeout을 결정론적으로 fast-forward. Scale Foundation 경계(엔진 미사용)라 독립 family — S3/Iceberg/Spark composition stack에는 들어가지 않는다.

- [x] WF1. adapter-contract: profile/failure taxonomy(timeout·unavailable·unknown·not_found, idempotency-key required) 선언. (`test_temporal_adapter_declares_failure_taxonomy`)
- [x] WF2. normal-path: start-and-wait가 입력을 처리한 output을 반환하고 run_id=workflow id. (`test_start_workflow_returns_processed_output`)
- [x] WF3. retry-idempotency: activity가 두 번 실패 후 성공할 때까지 재시도(backoff는 time-skipping이 건너뜀), partial-success로 run 성공. (`test_flaky_activity_is_retried_until_success`)
- [x] WF4. recovery-cleanup: 같은 idempotency_key로 재시작하면 중복 run을 만들지 않고 기존 완료 run에 재접속해 동일 결과 반환(REJECT_DUPLICATE→AlreadyStarted→handle.result). (`test_same_idempotency_key_returns_existing_run_without_duplicate`)
- [x] WF5. concurrency-race: 같은 key로 동시에 두 번 start해도 하나의 run만 생기고 둘 다 같은 결과를 받는다. (`test_concurrent_starts_on_one_key_produce_one_run`)
- [x] WF6. failure-injection + operator-evidence: 비즈니스 실패(non-retryable ApplicationError)는 silent success가 아니라 status=failed + 내구성 있는 error payload(adapterProfile/operation/kind/retryable/operatorMessage/workflowId/temporalRunId)로 표면화된다. (`test_business_failure_returns_durable_error_payload`)
- [x] WF7. failure-injection(timeout): execution timeout을 초과한 run은 status=failed + kind=timeout + retryable=true + timeoutSeconds로 보고된다. (`test_execution_timeout_is_reported_as_retryable_timeout`)
- [x] WF8. failure-injection(cancel): 취소된 run은 status=cancelled로 분류되고(workflow_run describe 매핑 + start 경로 재접속 분류 모두), 다른 terminal 상태와 구분된다. (`test_cancelled_workflow_is_reported_as_cancelled`)
- [x] WF9. service-unavailable: Temporal start/lookup transport 장애는 raw Temporal 예외가 아니라 retryable unavailable payload/AdapterError로 표면화된다. (`test_start_workflow_temporal_unavailable_returns_retryable_error_payload`, `test_workflow_run_temporal_unavailable_raises_retryable_adapter_error`)
- [ ] WF10. 분산 worker crash mid-activity / signal·query / continue-as-new / 실 Temporal 클러스터 failover는 time-skipping 단일 worker로 재현 불가 — 실 클러스터 필요로 deferred (infra-ratchet.md Temporal scope note).

### C-ELASTICSEARCH. Managed Elasticsearch deployment (5번째 인프라 ratchet, profile `elasticsearch`)

ElasticsearchAdapter는 이전부터 존재(profile + projection 계약). 이 ratchet은 live-cluster 갭을 닫는다: 실 클러스터 장애가 어댑터가 약속한 **타입화된 failure**로 표면화되고, search는 재구축 가능한 projection으로 남으며, version guard가 동시 writer에서 유지됨. 검증은 ① 실 testcontainers ES 9.x(`test_elasticsearch_live_cluster.py`, ES 데이터 디렉터리를 tmpfs로 마운트 — vz virtiofs 디스크의 refresh fsync가 느려 client timeout을 넘기던 것을 우회, 로컬+CI 모두 안정)와 ② in-memory fake(version-guard script 의미 모델링 + 실제 `elastic_transport`/`elasticsearch` 예외 타입 발생, 분류 매트릭스 전체를 deterministic·고속 검증)로 한다. 직교 projection이라 독립 family — S3/Iceberg/Spark composition stack에 들어가지 않는다.

- [x] ES1. adapter-contract: search failure taxonomy(configure/upsert/delete/document_ids/search, 전부 retryable) 선언. (`test_elasticsearch_adapter_contract_declares_search_failure_modes`)
- [x] ES2. normal-path: configure_index → upsert → search/document_ids round-trip이 동작. (`test_elasticsearch_normal_path_indexes_and_searches`)
- [x] ES3. failure-injection: 클러스터 timeout→timeout(retryable)/connection→unavailable(retryable)/5xx→unavailable/429→rate_limited/4xx→validation(non-retryable)/409→conflict가 raw 누수 아니라 타입화된 AdapterError로 분류. (`test_elasticsearch_cluster_failures_map_to_typed_adapter_error`)
- [x] ES4. concurrency-race: version-guarded update가 동시 writer에서 더 높은 version만 반영(stale writer가 fresher projection을 덮어쓰지 않음). (`test_elasticsearch_version_guard_keeps_highest_version_under_concurrent_upserts`, `test_elasticsearch_live_version_guard_keeps_highest_version_under_concurrent_writers`)
- [x] ES5. retry-idempotency: 응답 드롭 후 재시도된 create가 already_exists여도 멱등 성공(retry_on_timeout + 멱등 create). (`test_elasticsearch_configure_index_is_idempotent_on_already_exists`)
- [x] ES6. partial-success: 한 문서 upsert 실패(timeout→AdapterError)가 이미 색인된 다른 문서를 잃게 하지 않는다. (`test_elasticsearch_one_document_failure_does_not_lose_others`)
- [x] ES7. recovery-cleanup: 인덱스 유실(클러스터 wipe) 후 Object Store source-of-truth에서 재색인하면 projection이 재구축된다 — search는 truth가 아니다. (`test_elasticsearch_index_is_rebuildable_projection_after_loss`)
- [x] ES8. operator-evidence: 실패가 로그 한 줄이나 adapter exception에만 남지 않고 operations의 failed indexRuns + related audit에 내구성 있는 분류 payload(adapterProfile/operation/kind/retryable/trace)로 표면화된다. (`test_elasticsearch_rebuild_failure_is_visible_in_index_run_operations`, `test_elasticsearch_object_change_failure_is_visible_in_index_run_operations`, `test_elasticsearch_consistency_failure_is_visible_in_index_run_operations`)
- [x] ES9. live-cluster: 실 testcontainers ES에 대해 round-trip(색인/검색/full-text)·version-guard(실 painless script)·실 클러스터 정지→타입화된 retryable AdapterError가 동작한다. ES 데이터 디렉터리를 tmpfs로 올려 vz virtiofs 느린 디스크를 우회(로컬+CI 안정). (`test_elasticsearch_live_round_trip_indexes_and_searches`, `test_elasticsearch_live_version_guard_rejects_stale_writer`, `test_elasticsearch_live_cluster_outage_is_typed_adapter_error`)

## D. Ontology / Schema / SDK

- [ ] D1. draft import partial failure가 rollback된다.
- [ ] D2. apiName case-insensitive duplicate를 검증한다.
- [ ] D3. active ontology row direct update가 불가능하다.
- [ ] D4. property type cast가 precision loss를 만들지 않는다.
- [ ] D5. primaryKey nullable=false와 actual data health check가 연결된다.
- [ ] D6. editable property는 edit layer/policy가 있어야 한다.
- [ ] D7. linkType target rename 시 old link row migration/invalid state를 처리한다.
- [ ] D8. ontology activation event가 outbox에 남는다.
- [x] D9. SDK는 draft가 아니라 active에서 생성된다.
- [ ] D10. SDK ontology version과 server serving ontology version mismatch를 감지한다.
- [ ] D11. action param rename/delete breaking change를 감지한다.
- [x] D12. browser SDK와 npm SDK parity test가 있다.

## E. Object Store / Index / Merge

- [x] E1. bulk upsert partial success가 index_run success로 보이지 않는다.
- [x] E2. object_id generation이 leading zero를 보존한다.
- [ ] E3. CSV와 CDC object_id normalization이 같다.
- [x] E4. source_dataset_version_id가 object_records에 저장된다.
- [ ] E5. property_versions가 base/edit 모두에 대해 갱신된다.
- [ ] E6. conflict_requires_review가 conflict row를 만들고 current choice를 명확히 한다.
- [ ] E7. edit_wins property를 source value로 되돌리는 clear/edit reset policy가 있다.
- [ ] E8. tombstoned object가 link traversal에 어떻게 보이는지 명시한다.
- [x] E9. tombstone include/exclude가 query/materialization에서 일관된다.
- [x] E10. same dataset version reindex는 불필요한 object_version 증가를 만들지 않는다.
- [x] E11. reindex idempotency가 action expectedObjectVersion을 깨지 않는다.
- [ ] E12. index progress cursor는 deterministic input ordering을 사용한다.
- [ ] E13. primary key null row skip/fail 정책과 count가 기록된다.

## F. Object Query / Object Set / Link

- [x] F1. cursor token은 HMAC으로 tamper-proof이다.
- [ ] F2. cursor token은 tenant/user/query_shape/index_version에 묶인다.
- [ ] F3. cursor from old ontology version reuse를 처리한다.
- [ ] F4. cursor from old search index reuse를 처리한다.
- [ ] F5. mutable sort key pagination 전략이 있다.
- [x] F6. offset pagination을 production path에서 금지한다.
- [x] F7. missing filter property를 silently ignore하지 않는다.
- [x] F8. JSON numeric property는 typed cast로 비교한다.
- [x] F9. masked property contains/filter/sort/search를 막는다.
- [ ] F10. static object set에 deleted object가 있을 때 표시 정책이 있다.
- [ ] F11. dynamic set query는 time-dependent 조건을 watermark/pinning한다.
- [ ] F12. private set id enumeration을 막는다.
- [ ] F13. reverse link traversal에도 tenant condition이 있다.
- [ ] F14. dangling link target missing을 warning/error로 추적한다.

## G. Action / Writeback / Outbox

- [x] G1. same idempotency key different body는 conflict다.
- [ ] G2. idempotency unique scope에 tenant/action/actor/target/key가 포함된다.
- [x] G3. idempotency unique scope에 attempt/run id가 들어가지 않는다.
- [x] G4. client timeout retry가 같은 key를 재사용한다.
- [x] G5. action_run succeeded는 object_edit commit 후에만 가능하다.
- [x] G6. object_edit만 있고 action_run이 없는 상태가 불가능하다.
- [ ] G7. action_run failed인데 object가 바뀐 상태가 impossible-state로 감지된다.
- [x] G8. precondition stale read는 commit conflict로 막는다.
- [x] G9. expectedObjectVersion은 optional이 아니다.
- [x] G10. object_version은 base/edit 모두에서 증가한다.
- [ ] G11. external timeout은 outcome_unknown이다.
- [ ] G12. external idempotency key를 propagate한다.
- [ ] G13. compensation worker는 자동 rollback보다 manual review를 기본으로 한다.
- [ ] G14. after-commit side effect duplicate publish를 막는다.
- [ ] G15. poison outbox event가 queue head를 영구 block하지 않는다.
- [ ] G16. DLQ stale event retry는 no-op 가능하다.
- [x] G17. audit에 raw sensitive params를 저장하지 않는다.
- [ ] G18. action log에는 previous_values가 있다.
- [ ] G19. action status enum은 conflict/failed/unknown/compensation을 구분한다.
- [ ] G20. side effect failure를 local action failure와 구분한다.

## H. Materialization / Closed Loop

- [x] H1. action_log materialization cursor는 committed sequence 기반이다.
- [x] H2. late-committed action이 누락되지 않는다.
- [x] H3. failed action은 success materialization에 포함되지 않는다.
- [x] H4. same cursor rerun이 duplicate rows를 만들지 않는다.
- [x] H5. source cursor는 dataset commit 전 전진하지 않는다.
- [x] H6. object_snapshot은 fixed watermark를 사용한다.
- [x] H7. snapshot은 base_properties만이 아니라 current_properties를 반영한다.
- [x] H8. tombstone policy가 object query와 snapshot에서 일관된다.
- [x] H9. downstream transform은 materialized version_id를 소비한다.
- [x] H10. materialization commit visible 전 downstream trigger가 발생하지 않는다.
- [x] H11. closed-loop retry가 same cursor에 대해 duplicate output을 만들지 않는다.
- [x] H12. lineage edge에 materialization run과 exact watermark가 남는다.

## I. Streaming / CDC

- [x] I1. offset commit은 archive dataset commit 후에만 가능하다.
- [x] I2. dataset commit 후 offset commit 실패 시 duplicate-safe하다.
- [ ] I3. consumer rebalance mid-batch를 테스트한다.
- [ ] I4. partition별 partial batch policy가 있다.
- [ ] I5. event id 없을 때 dedupe fallback이 안정적이다.
- [ ] I6. Kafka compaction/tombstone과 archive lag을 고려한다.
- [x] I7. raw Debezium payload를 그대로 downstream에 노출하지 않는다.
- [x] I8. ordering metadata가 사라지지 않는다.
- [x] I9. source_ts_ms만으로 ordering하지 않는다.
- [x] I10. initial snapshot event가 later update를 덮지 않는다.
- [x] I11. delete tombstone이 stale update로 resurrect되지 않는다.
- [x] I12. primary key update policy가 있다.
- [ ] I13. source transaction boundary를 추적한다.
- [ ] I14. schema change event를 처리한다.
- [ ] I15. replication slot lag/WAL alert가 있다.
- [ ] I16. CDC lag metric은 archived committed offset 기준이다.
- [ ] I17. high-volume CDC event coalescing/backpressure가 있다.
- [x] I18. CDC indexer가 materialization trigger를 누락하지 않는다. (`test_debezium_cdc_iceberg_s3_spark_archives_indexes_and_materializes_end_to_end`, `test_debezium_cdc_iceberg_s3_spark_archive_failure_aborts_without_dataset_version`)

## J. Search / Reindex

- [x] J1. Elasticsearch는 source of truth가 아니다.
- [x] J2. stale event가 newer index doc을 덮지 않는다.
- [ ] J3. fallback은 degraded flag를 표시한다.
- [x] J4. masked property는 indexed/searchable이 아니다.
- [ ] J5. ontology activation 후 search mapping drift를 감지한다.
- [ ] J6. orphan Elasticsearch docs를 drift detection으로 찾는다.
- [ ] J7. search rebuild가 모든 objects를 메모리에 올리지 않는다.
- [ ] J8. shadow reindex는 existing object_records를 truncate하지 않는다.
- [x] J9. shadow reindex는 action edits를 replay한다.
- [x] J10. validation hash는 count만 보지 않는다.
- [ ] J11. validation hash는 tombstone/link/current_properties를 포함한다.
- [x] J12. alias switch 중 cursor pagination 안전성이 있다.
- [ ] J13. old index cleanup은 cursor TTL 이후다.
- [x] J14. reindex delta catch-up이 있다.
- [ ] J15. full/shadow mode config typo를 막는다.

## K. Security / Governance

- [x] K1. RLS tenant context가 pooled connection에서 reset된다.
- [x] K2. dev header auth는 production에서 hard fail한다.
- [x] K3. worker/background job도 tenant context가 필수다.
- [ ] K4. API뿐 아니라 CLI에도 permission check가 있다.
- [x] K5. object get/search 모두 permission check가 있다.
- [x] K6. masking은 response/filter/sort/search 모두에 적용된다.
- [ ] K7. materialized dataset에도 masking/export policy가 있다.
- [x] K8. audit이 masked property를 노출하지 않는다.
- [ ] K9. signed URL이 cross-tenant object read를 허용하지 않는다.
- [ ] K10. object storage path direct access로 API를 우회할 수 없다.
- [ ] K11. webhook signature secret이 로그에 남지 않는다.
- [x] K12. REST connector SSRF를 막는다.
- [ ] K13. private object set membership이 object ids를 leak하지 않는다.
- [ ] K14. role cache stale after permission revocation을 처리한다.
- [ ] K15. admin action이 accidentally global로 실행되지 않는다.

## L. Deployment / Backup / Upgrade

- [ ] L1. migration은 singleton job만 실행한다.
- [ ] L2. worker v1/API v2 schema mismatch를 감지한다.
- [ ] L3. rollback after migration runbook이 있다.
- [ ] L4. DB restore without object storage를 검증한다.
- [ ] L5. object storage restore without DB를 검증한다.
- [ ] L6. outbox replay after restore를 pause한다.
- [ ] L7. stream cursor restore 뒤 archived dataset max offset과 reconcile한다.
- [ ] L8. secret rotation mid-run을 처리한다.
- [ ] L9. readiness는 migration 완료 전 traffic을 받지 않는다.
- [ ] L10. liveness는 long-running worker를 죽이지 않는다.
- [ ] L11. transform이 API resource를 starve하지 않는다.
- [ ] L12. node disk pressure eviction during commit을 복구한다.
- [ ] L13. backup에는 encryption keys/secrets restore 계획이 포함된다.
- [ ] L14. Helm values default가 dev auth/profile이 아니다.
- [ ] L15. runbook에는 object storage consistency check가 포함된다.

---

# 9. Regression Test Top 80

- [x] `test_dataset_commit_storage_success_db_failure_creates_orphan_cleanup_evidence`
- [x] `test_dataset_commit_db_success_manifest_missing_marks_storage_corruption`
- [x] `test_concurrent_dataset_commits_allocate_strictly_increasing_versions`
- [x] `test_dataset_health_check_reads_candidate_not_latest`
- [x] `test_schema_compatibility_revalidates_if_latest_schema_changes`
- [x] `test_failed_upload_oom_leaves_recoverable_aborted_or_stale_open_tx`
- [x] `test_abort_cleanup_never_deletes_committed_manifest`
- [x] `test_s3_dataset_storage_adapter_contract`
- [x] `test_s3_partial_multipart_upload_never_becomes_committed_version`
- [x] `test_s3_commit_storage_success_db_failure_creates_orphan_cleanup_evidence`
- [x] `test_s3_committed_manifest_missing_marks_storage_corruption`
- [x] `test_s3_abort_cleanup_never_deletes_committed_manifest`
- [x] `test_s3_concurrent_dataset_commits_allocate_strictly_increasing_versions`
- [x] `test_s3_retry_after_storage_timeout_does_not_duplicate_version`
- [x] `test_s3_storage_failure_is_visible_in_operations`
- [x] `test_csv_primary_key_preserves_leading_zeroes`
- [x] `test_transform_input_latest_is_pinned_to_version_id`
- [x] `test_transform_retry_after_commit_does_not_create_second_output_version`
- [x] `test_transform_output_and_lineage_commit_atomically`
- [x] `test_duckdb_oom_aborts_output_transaction`
- [x] `test_python_transform_cannot_access_raw_storage_path`
- [x] `test_rest_cursor_not_advanced_when_dataset_commit_fails`
- [x] `test_rest_mutable_pagination_detected_or_marked_non_replayable`
- [x] `test_webhook_ack_not_sent_before_append_commit_or_has_replay_strategy`
- [x] `test_webhook_same_event_id_different_payload_is_deduped`
- [x] `test_webhook_signature_replay_and_clock_skew_policy`
- [x] `test_rest_redirect_to_private_ip_blocked`
- [x] `test_rest_dns_rebinding_to_private_ip_blocked`
- [ ] `test_postgres_snapshot_streams_without_full_memory_load`
- [ ] `test_postgres_snapshot_schema_change_mid_stream_detected`
- [x] `test_action_same_idempotency_key_concurrent_requests_replay_same_action_run`
- [x] `test_action_same_idempotency_key_different_body_returns_409`
- [x] `test_action_commit_object_edit_audit_outbox_atomic`
- [x] `test_action_expected_object_version_required`
- [x] `test_action_precondition_stale_read_conflicts_on_commit`
- [ ] `test_action_external_success_local_failure_compensation_required`
- [ ] `test_action_external_timeout_is_outcome_unknown_not_failed`
- [ ] `test_action_retry_reuses_external_idempotency_key`
- [x] `test_outbox_event_not_published_before_domain_commit`
- [ ] `test_outbox_duplicate_workers_do_not_double_publish`
- [ ] `test_dlq_stale_event_retry_noops_if_object_version_old`
- [x] `test_materialization_created_at_tie_does_not_skip_rows`
- [x] `test_materialization_late_commit_action_not_skipped`
- [x] `test_object_snapshot_fixed_watermark_hash_reproducible`
- [x] `test_object_snapshot_mid_run_action_not_mixed`
- [x] `test_materialization_cursor_not_advanced_before_dataset_commit`
- [x] `test_downstream_transform_consumes_materialized_version_id_not_latest`
- [x] `test_cdc_object_indexing_updates_tombstones_and_skips_stale_events`
- [x] `test_cdc_pk_update_policy`
- [x] `test_cdc_duplicate_event_idempotent`
- [x] `test_cdc_source_transaction_group_not_partially_committed_without_status`
- [x] `test_debezium_cdc_iceberg_s3_spark_archives_indexes_and_materializes_end_to_end`
- [x] `test_debezium_cdc_iceberg_s3_spark_archive_failure_aborts_without_dataset_version`
- [x] `test_spark_rows_to_parquet_preserves_quoted_json_strings`
- [x] `test_stream_offset_not_advanced_when_append_commit_fails`
- [ ] `test_stream_rebalance_mid_batch_dedupes_offsets`
- [ ] `test_stream_partial_partition_batch_abort_policy`
- [x] `test_object_merge_edit_only_not_overwritten_by_source`
- [x] `test_object_version_increments_for_base_and_edit_updates`
- [x] `test_index_progress_cursor_advances_only_after_bulk_upsert_commit`
- [x] `test_reindex_same_dataset_version_idempotent`
- [x] `test_shadow_reindex_replays_action_edits`
- [x] `test_shadow_reindex_alias_switch_cursor_version_safe`
- [x] `test_shadow_reindex_validation_hash_includes_current_properties_and_tombstone`
- [x] `test_shadow_reindex_catches_up_delta_edits_before_switch`
- [ ] `test_old_search_index_retained_until_cursor_ttl`
- [x] `test_search_stale_event_cannot_overwrite_newer_doc`
- [x] `test_action_form_refetches_object_store_after_search_hit`
- [ ] `test_search_fallback_marks_degraded`
- [ ] `test_degraded_search_result_cannot_silently_create_object_set`
- [x] `test_masked_property_cannot_filter_sort_search`
- [x] `test_action_audit_masks_sensitive_params`
- [x] `test_rls_tenant_context_reset_between_pooled_connections`
- [x] `test_production_refuses_dev_header_trust_auth`
- [x] `test_worker_requires_tenant_context_for_background_jobs`
- [x] `test_object_query_cursor_signed_tamper_proof_query_shape_bound`
- [x] `test_object_query_db_backed_keyset_no_memory_slice`
- [x] `test_object_query_numeric_property_casts_for_sort_and_filter`
- [ ] `test_object_query_mutable_sort_key_does_not_duplicate_or_skip`
- [x] `test_dynamic_object_set_cannot_bypass_page_limit`
- [x] `test_static_object_set_rechecks_object_permission`
- [x] `test_link_traversal_never_crosses_tenant_without_policy`
- [ ] `test_migration_job_singleton_no_app_start_race`
- [ ] `test_restore_db_object_storage_consistency_check`
- [ ] `test_restore_outbox_paused_until_reconciliation`
- [ ] `test_k8s_sigterm_during_commit_recovers_or_aborts_cleanly`
- [ ] `test_liveness_probe_does_not_kill_healthy_long_running_worker`
- [ ] `test_readiness_probe_fails_when_required_dependency_unavailable`
- [ ] `test_rolling_deploy_schema_version_skew_safe`
- [ ] `test_backup_restore_reconciles_stream_cursor_against_archived_offsets`

---

# 10. Failure Injection Matrix

## Commit Boundary Injection

- [ ] Kill process after staging file write, before manifest write.
- [ ] Kill process after manifest write, before DB commit.
- [ ] Kill process after DB commit, before run status update.
- [ ] Kill process after DB commit, before outbox worker sees event.
- [ ] Kill process after external writeback success, before local DB commit.
- [ ] Kill process after source cursor pending update, before dataset commit.
- [ ] Kill process after stream batch read, before archive commit.
- [ ] Kill process after archive commit, before offset cursor commit.
- [ ] Kill process during materialization file write.
- [ ] Kill process after materialization commit, before downstream trigger.

## Resource Injection

- [ ] OOM during CSV → Parquet.
- [ ] OOM during DuckDB transform.
- [ ] OOM during materialization snapshot.
- [ ] OOM during Elasticsearch bulk indexing.
- [ ] Disk full during staging write.
- [ ] Disk full during DuckDB temp spill.
- [ ] Inode full during many small staging files.
- [ ] Postgres WAL full due CDC slot lag.
- [ ] File descriptor exhaustion during preview storm.

## Network Injection

- [ ] Object storage timeout during put.
- [ ] Object storage timeout during get/stat after DB commit.
- [ ] DB failover during commit.
- [ ] DB connection lost after commit request, before client receives result.
- [ ] Kafka rebalance mid-batch.
- [ ] Kafka broker unavailable after polling but before archive commit.
- [ ] Elasticsearch unavailable after object commit.
- [ ] External REST writeback timeout after server processed request.
- [ ] DNS rebinding during REST connector request.
- [ ] TLS cert rotation during long-running source sync.

## Concurrency Injection

- [ ] Two dataset commits to same dataset concurrently.
- [ ] Two action applies with same idempotency key concurrently.
- [ ] Two action applies with same object_version concurrently.
- [ ] Ontology activation during index run.
- [ ] Shadow reindex during live object query pagination.
- [ ] CDC update and user action edit on same property concurrently.
- [ ] Materialization snapshot during action commit.
- [ ] Object set evaluation during object updates.
- [ ] Permission revocation during paginated query.
- [ ] SDK generated while ontology activation is in progress.

---

# 11. 실제 우선순위

## 1순위 — Commit/Cursor 불변식

- [x] dataset commit
- [x] manifest commit
- [x] stream offset
- [x] REST cursor
- [x] materialization cursor
- [x] index progress cursor

**완료 기준:** commit 전후 process kill을 주입해도 데이터 유실/중복 성공이 없어야 한다.

## 2순위 — Action/Writeback/Idempotency

- [x] same idempotency key race
- [x] same key different body
- [x] expectedObjectVersion
- [ ] external timeout unknown
- [ ] compensation_required
- [ ] outbox duplicate publish

**완료 기준:** 네트워크 timeout/retry/concurrent request에도 object/action/external side effect가 중복 성공하지 않아야 한다.

## 3순위 — CDC/Ordering/Tombstone

- [x] ordering metadata
- [x] snapshot event vs update event
- [x] delete tombstone
- [x] PK update
- [x] duplicate event
- [ ] lag/slot/WAL

**완료 기준:** out-of-order/stale/duplicate CDC event가 object current state를 과거로 되돌리지 않아야 한다.

## 4순위 — Projection/Reindex/Search

- [x] shadow reindex action edits replay
- [x] alias switch cursor
- [x] Elasticsearch stale event
- [ ] fallback degraded
- [x] masked search

**완료 기준:** projection이 stale하거나 재구성 중이어도 source of truth를 오염시키지 않고, 사용자에게 degraded/stale 상태를 명확히 표시해야 한다.

## 5순위 — k8s/Backup/Restore/OOM

- [x] OOMKilled cleanup
- [ ] SIGTERM recovery
- [ ] migration race
- [ ] DB/object storage restore mismatch
- [ ] outbox replay after restore
- [ ] readiness/liveness

**완료 기준:** 운영 장애 후에도 impossible-state checker와 restore validation으로 시스템 일관성을 검증할 수 있어야 한다.

---

# 12. 최종 원칙

- [x] retry해도 중복 성공하지 않는다.
- [x] 실패해도 성공처럼 보이지 않는다.
- [x] projection이 틀려도 source of truth를 오염시키지 않는다.
- [x] 어느 시점으로 replay해도 같은 결과가 나온다.
- [x] 모든 write에는 commit point가 있다.
- [x] 모든 cursor/offset/watermark는 durable commit 이후에만 전진한다.
- [ ] 모든 external side effect에는 idempotency key와 unknown outcome handling이 있다.
- [x] 모든 stale event는 skip/no-op/conflict로 처리된다.
- [x] 모든 P0는 regression test 없이 완료로 보지 않는다.
- [x] 모든 운영 장애는 run/audit/error payload로 추적 가능해야 한다.
