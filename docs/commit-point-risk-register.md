# Commit-Point Risk Register

**Last updated:** 2026-06-15 KST  
**Scope:** Foundry-lite architecture only. Hermes is intentionally excluded.

This register tracks the dangerous class of bugs where one subsystem appears to
have succeeded while another durable state still points to a different reality.
The product loop under review is:

```text
CSV/local snapshot or PostgreSQL-backed repository proof -> DuckDB transform
-> Ontology/Object -> Action -> Materialization -> Downstream Transform
```

The most important question for every row is not "does the feature run?", but
"where is the single durable commit point, and can every cursor, offset,
watermark, manifest, action state, outbox event, and lineage edge be replayed
from that point?"

## Status Rules

| Status | Meaning |
|---|---|
| Covered | Current evidence directly proves the commit-point invariant. Keep it as a regression shield. |
| Partial | The normal path or a nearby invariant is implemented, but the exact failure/concurrency edge still needs a targeted proof. |
| Gap | The risk is in current or near-term scope and does not yet have enough evidence. |
| Future | The risk belongs to a future sprint or production adapter, so it must not be marked done today. |

Evidence ids such as `S36A-A1` and `VERIFY-FULL-CI-GATE` are defined in
[Sprint Evidence Ledger](./sprint-evidence-ledger.md).

## Tier 0 Summary

Tier 0 means silent data loss, permission leak, or real-world system divergence
that a user may not notice immediately and that may be hard or impossible to
repair later.

| Risk id | Commit point at risk | Sprint anchor | Current status | Evidence and gap |
|---|---|---|---|---|
| T0-01 | Stream offset vs raw archive dataset commit | Sprint 38 | Partial | `S38-A1`-`S38-A4`, `VERIFY-KAFKA-LIVE-BROKER`, `VERIFY-STREAM-REST-CURSOR-COMMIT-POINTS`, and `dataset_transactions.metadata.streamCursor` prove the archive path, committed cursor shape, and local failure-injection invariant: after a batch is read and Parquet write fails, retry resumes from the last committed offset rather than the failed batch's offset. Missing future proof: production worker/Kafka rebalance or commit-unknown failure injection around a live broker batch. |
| T0-02 | REST pagination cursor vs output dataset commit | Sprint 37 | Covered | `S37-A7`, `VERIFY-STREAM-REST-CURSOR-COMMIT-POINTS`, and `test_rest_mutable_pagination_detected_or_marked_non_replayable` prove committed connector syncs persist `connectorCursor`, use it as the next default, do not advance it when the next page is fetched but dataset commit fails, and reject page-number pagination as `non_replayable` instead of silently producing duplicate/missing rows. |
| T0-03 | Dataset DB commit vs object-storage manifest/files | Sprint 05, 36A, Infra Ratchet 01 | Covered | `S05-A1`, `S05-A2`, `S05-A3`, `S36A-A3`, `VERIFY-DATASET-STORAGE-SPLIT-BRAIN`, and `VERIFY-S3-STORAGE-RATCHET` prove both the local MVP path and the MinIO/S3-compatible adapter path. Storage promotion followed by DB file-row failure removes the promoted artifact and persists `orphan_cleanup` evidence on the FAILED sync run; committed DB versions with missing manifest/data raise operator-facing `committed_version_storage_missing`; local and S3 adapters verify manifest URI, byte size, and content hash after write; S3 partial multipart timeout cleanup prevents a partial object from becoming a committed version; retry with the same version id creates exactly one manifest/data pair; abort cleanup does not delete an earlier committed manifest; S3 storage failures keep `adapterFailure` evidence in Operations. |
| T0-04 | Dataset version allocation race | Sprint 36A | Covered | `S36A-A2`, `S36A-A3`, `PR-2`, and `VERIFY-DATASET-VERSION-CONCURRENCY` cover dataset-row locking, PostgreSQL concurrent commits allocating strictly increasing version numbers, duplicate metadata conflict mapping, no commit outbox/audit on conflict, and cleanup of already-promoted artifacts. Keep concurrent commit and no-outbox-on-conflict tests as regression shields. |
| T0-05 | Health check input candidate vs latest committed dataset | Sprint 08, S54 | Covered | `test_dataset_health_check_reads_candidate_not_latest` proves an invalid duplicate-key candidate upload is rejected even when the latest committed version is valid, so health checks inspect the staged candidate rather than `latest`. S54 adds `test_quality_check_pins_candidate_manifest_hash`, `test_candidate_tamper_between_check_and_commit_is_rejected`, `test_commit_dataset_version_aborts_when_primary_key_check_fails`, `test_quarantine_routes_bad_records_to_record_dlq`, `test_operations_run_detail_exposes_candidate_quality_report`, and `test_operations_run_detail_includes_failed_row_sample_for_quarantine`, proving dataset quality check results persist `checked_manifest_hash`, a staged candidate changed after quality checks is rejected before final storage commit, commit-time hard failures surface as `BLOCK_COMMIT` without creating a dataset version, row-level quarantine rewrites then revalidates the clean candidate before commit, and Operations run detail exposes the transaction-scoped quality report plus data-quality quarantine failed-row samples. |
| T0-06 | Schema compatibility TOCTOU | Sprint 08, 36A, S54 | Partial | `test_schema_compatibility_revalidates_if_latest_schema_changes` proves dataset finalization now takes the dataset version-allocation lock before schema compatibility validation, so schema validation and version allocation happen inside the same dataset lock window. S54 adds `test_schema_validation_records_reference_version` and `test_contract_version_is_pinned_to_run`, which prove commit-time dataset quality check results persist `validated_against_schema_version_id` and `validated_against_schema_version` for the schema row used by validation, and historical check results stay pinned after a later commit creates a new schema version. Remaining hardening: a richer integration race proof for production database isolation. |
| T0-07 | Transform input `latest` vs pinned dataset version | Sprint 11 | Covered | `test_transform_input_latest_is_pinned_to_version_id` proves transform planning pins input dataset version ids before execution: the test commits a newer input latest after planning but before SQL execution, and the transform output still reads the pinned earlier version. Transform runs record `input_versions`; failed-transform retry uses the original versions; `VERIFY-FULL-CI-GATE` includes OpenLineage P8 checks for successful transform input/output version evidence. Missing edge to consider later: direct SQL template paths must keep rejecting raw file/latest-only access. |
| T0-08 | Transform output commit vs lineage edge | Sprint 12 | Covered | `VERIFY-TRANSFORM-LINEAGE-ATOMIC` injects lineage-write failure after transform output storage promotion and proves the promoted artifact is removed, the output dataset has no committed version, no lineage edge is served, and the transform run is `FAILED` with `orphan_cleanup` evidence. OpenLineage P8 still blocks successful transform runs without input/output version lineage. |
| T0-09 | External before-commit success vs local action failure | Sprint 27, S53, L8 | Covered for the current real adapter | `VERIFY-DATA-PLATFORM-S53`, `quality:saga-reconciliation`, `quality:action-writeback-live`, `test_external_success_local_failure_requires_compensation`, `test_compensation_is_idempotent`, `test_action_external_success_local_failure_requires_real_compensation`, `test_reconciliation_resolves_remote_success`, and `test_concurrent_reconciliation_has_one_winner` prove both the simulated path and a real S3/MinIO before-commit write that lands before local mutation failure become `compensation_required` action/writeback/audit evidence, same-key replay does not issue another writeback, and reconciliation can close the divergence with one local mutation winner. Remaining future scope is not this commit-point invariant itself, but ERP-specific connector packaging, an autonomous compensation worker, persistent review queue, and approval UI. |
| T0-10 | External timeout outcome unknown | Sprint 27, S53, L8 | Covered for the current real adapter | `VERIFY-DATA-PLATFORM-S53`, `quality:external-writeback`, `quality:action-writeback-live`, `test_external_success_response_lost_becomes_outcome_unknown`, `test_outcome_unknown_is_not_blindly_retried`, `test_action_external_timeout_is_outcome_unknown_not_failed`, and `test_action_repository_contract_reconciles_outcome_unknown_writeback_once` prove both the simulated response-loss path and a real S3/MinIO connection timeout become `outcome_unknown` action/writeback/audit evidence, same-key replay does not issue another writeback, and the stored writeback can be closed through operator evidence or real adapter `remote_lookup` exactly once. Remaining future scope is persistent unresolved-writeback queueing/approval automation, not the timeout outcome classification. |
| T0-11 | Action idempotency key concurrent race | Sprint 26, 36A | Covered | `S36A-A1` and `PR-2` prove concurrent same-key action idempotency turns database unique races into replay through the action repository. |
| T0-12 | Same idempotency key with different request body | Sprint 26, 36A | Covered | `S26-A2` and `VERIFY-ACTION-IDEMPOTENCY-FINGERPRINT` prove each action_run stores a canonical `request_fingerprint`; same-key replay is allowed only when the fingerprint matches, and same-key/different-body reuse returns `ConflictDetected` with `action.run.idempotency_conflict` audit evidence instead of creating or replaying the wrong action. |
| T0-13 | Action commit across object record, object edit, audit, outbox | Sprint 25, 26 | Covered | `S25-A1`-`S25-A5` and `VERIFY-ACTION-COMMIT-ATOMICITY` prove the current internal DB action commit is atomic: injected failures after the object record update, after the object edit insert point, before action terminal success, during outbox insert, and during audit insert leave no partial object/action/writeback/edit/audit/outbox state and the same idempotency key can be retried successfully. The fix also replaces the action idempotency insert savepoint with dialect-native `ON CONFLICT DO NOTHING`, because the savepoint path could leave a durable `received` action_run after an outer rollback on SQLite. Real external writeback divergence is tracked separately as T0-09/T0-10 and is covered for the current S3/MinIO adapter, while autonomous compensation operations remain future scope. |
| T0-14 | Stale precondition read without expected object version | Sprint 25, 29, 35 | Covered | SDK and UI surfaces expose expected object version and generated helpers, the API request schema rejects missing `expectedObjectVersion` before core execution, `FoundryLite.apply_action` requires `expected_object_version`, and `test_action_precondition_stale_read_conflicts_on_commit` proves a stale version cannot commit after another action updates the object. |
| T0-15 | Action-log materialization cursor based on created time | Sprint 30 | Covered | `VERIFY-MATERIALIZATION-WATERMARKS` proves `action_log` now pins `completed_at` plus `action_run_id` and does not skip a late action committed after the first run captured its cursor. `VERIFY-MATERIALIZATION-COMMIT-FAILURE` proves a failed `action_log` materialization does not create an output dataset version or target version id before the run is marked `FAILED`. Keep both regression tests as release shields. |
| T0-16 | Object snapshot watermark based on wall-clock updated time | Sprint 31 | Covered | `VERIFY-MATERIALIZATION-WATERMARKS` proves the current run no longer relies on `updated_at`: object mutations allocate tenant-scoped `object_change_sequence`, the run stores `object_change_sequence_lte` plus `active_index_version`, and rows are captured at run start. `test_object_snapshot_fixed_watermark_hash_reproducible` adds the durable replay proof: object changes now append `object_record_versions`, so an old materialization run's stored watermark can be replayed after later in-place updates and still returns the original row hash/state. |
| T0-17 | Object snapshot reads a mixed logical point | Sprint 31 | Covered | `VERIFY-MATERIALIZATION-WATERMARKS` proves a mid-run ApproveOrder action does not partly enter the same `ops.order_current` output; the next run sees the new state. |
| T0-18 | CDC stale event overwrites newer object state | Sprint 39, 40 | Covered | `S40-A4` and `VERIFY-CDC-STALE-TOMBSTONE` prove lower ordering/LSN update and snapshot/read events are skipped while keeping the newer object version and state. |
| T0-19 | CDC delete tombstone resurrected by stale update | Sprint 40 | Covered | `S40-A2`, `S40-A4`, and `VERIFY-CDC-STALE-TOMBSTONE` prove tombstone delete handling plus stale ordering skip after deletion, so a late older update does not resurrect the deleted object into active query results or the `ops.order_current` materialized snapshot. |
| T0-20 | CDC primary-key update policy | Sprint 39, 40 | Covered | `VERIFY-CDC-PK-UPDATE-POLICY` freezes the current MVP policy as fail-closed: a CDC update where `before` and `after` primary keys identify different object ids raises `ValidationFailed` during parsing instead of silently creating a second active object, deleting/recreating identity, or breaking action/link history. |
| T0-21 | Shadow reindex switches without action-edit replay | Sprint 41 | Covered | `S41-A5` and `VERIFY-SHADOW-REINDEX` prove action edit properties replay into the shadow rebuild before switch. |
| T0-22 | Shadow alias/pointer switch mixes old/new cursor pages | Sprint 41 | Covered | `S41-H1`, `S41-H2`, `VERIFY-ACTIVE-INDEX-POINTER-FLAKE`, and `VERIFY-SHADOW-SEARCH-PROJECTION-EDGES` prove explicit active pointer persistence, compare-and-swap promotion, stable concurrent first-pointer creation, cursor tokens bound to `active_index_version`, and fail-safe cursor rejection after a shadow promotion changes the serving index. |
| T0-23 | Elasticsearch treated as source of truth | Sprint 42 | Covered | `S42-A3` and `VERIFY-SHADOW-SEARCH-PROJECTION-EDGES` prove search failure does not block get/basic filters, stale lower-version search updates cannot overwrite newer projection docs, and search-hit entry re-reads the object store while exposing both the stale projection version and current object version before action execution. |
| T0-24 | Masking applied only to display, not filter/sort/search | Sprint 34, 42, PR #43 | Partial | `S34-A3`, `test_policy_classification.py`, `test_masked_property_cannot_filter_sort_search`, `test_dataset_preview_masks_sensitive_columns_for_unprivileged_roles`, `test_explain_does_not_bypass_property_masking`, and `test_operations_read_is_operator_only_and_redacts_sensitive_params` now prove ontology-classified `finance`/`pii` properties and backing columns are masked for non-finance/non-admin users across object response, link/read, dataset preview, object query filter/sort, search mapping, dynamic object-set filter, explain, action audit, and Operations run/detail surfaces. Remaining future proof: aggregate/export/materialized-dataset paths must apply the same property-permission rule when those surfaces exist. |
| T0-25 | PostgreSQL RLS tenant context leaks through connection pool | Sprint 34 | Covered | `S34-A4` and `test_rls_tenant_context_reset_between_pooled_connections` prove RLS hides tenant rows with/without tenant context and that the same pooled PostgreSQL backend connection can be reused across tenant-demo, no-tenant, and tenant-other transactions without leaking the previous transaction's tenant context. The test asserts the same `pg_backend_pid()` is reused while `SET LOCAL` tenant context resets at transaction end. |
| T0-26 | Dev header-trust auth enabled in production | Sprint 36A | Covered | `S36A-A9`, `VERIFY-PRODUCTION-AUTH-GUARD`, and `test_production_refuses_dev_header_trust_auth` prove production runtime fails startup when `header-trust`, `local_header_trust`, `demo`, or `demo_admin` auth is selected. |
| T0-27 | Backup restore DB/object-storage point mismatch | Sprint 45 | Future | Sprint 45 remains future production scope. Required later: backup manifest, restore validation for every committed dataset version manifest, and post-restore dataset/object/materialization smoke. |
| T0-28 | Restore replays pending outbox side effects | Sprint 45 | Future | Sprint 45 remains future production scope. Required later: restore mode pauses outbox publishers until operator reconciliation confirms external side effects. |
| T0-29 | Webhook ACK before durable append | Sprint 37 | Covered | `test_webhook_ack_not_sent_before_append_commit_or_has_replay_strategy` proves the API does not return 2xx when webhook append persistence fails, and the attempted APPEND transaction remains `ABORTED` rather than looking committed. A future durable inbox can be added if immediate 2xx ACK behavior is introduced. |
| T0-30 | Webhook volatile payload dedupe | Sprint 37 | Covered | `test_webhook_same_event_id_different_payload_is_deduped` proves event-id replay ignores volatile timestamp changes, replays the existing committed version, and still returns `CONFLICT` for materially different payloads under the same event id. |
| T0-31 | REST redirect/DNS rebinding SSRF | Sprint 37 | Covered | `test_rest_redirect_to_private_ip_blocked`, `test_rest_dns_rebinding_to_private_ip_blocked`, and `test_rest_redirect_encoded_decimal_octal_private_hosts_blocked` prove every redirect target is revalidated, DNS rebinding to private/link-local addresses is blocked, and encoded/decimal/octal redirect-host variants are normalized before private-network checks. |
| T0-32 | CSV primary-key string preservation | Sprint 08 | Covered | `test_csv_primary_key_preserves_leading_zeroes` proves both fake and DuckDB compute adapters keep a CSV primary key value like `"00123"` as a string and infer the primary-key schema column as non-null string, preventing numeric coercion into `123`. |
| T0-33 | Transform retry duplicate output | Sprint 12 | Partial | `test_transform_retry_after_commit_does_not_create_second_output_version` proves a successful transform run cannot be retried into a second output version through the Operations retry path. Remaining hardening: inject crash/failure between output file promotion, metadata commit, lineage insert, and run terminal update. |
| T0-34 | Action audit exposes sensitive values | Sprint 26, 34, S53 | Covered | `test_action_audit_masks_sensitive_params` proves an action can update a sensitive `Order.margin` value while the durable `audit_events` before/after refs store `***MASKED***` instead of the raw previous value or raw patch value. `test_sensitive_writeback_payload_is_masked_in_audit` extends that proof to the S53 outcome-unknown/reconciliation path: the sensitive parameter remains usable for reconciliation, but Operations action-run/writeback surfaces and `action.run.outcome_unknown`/`action.run.reconciled`/reconciliation-time `action.run.committed` audit evidence do not expose the raw value. Future ontology-level parameter sensitivity metadata remains a refinement, but current action audit evidence no longer exposes masked object properties. |

## First Implementation Backlog

These are the first code/test PRs that should follow this audit. They are
ordered by the chance of silent data loss or misleading success.

1. Production storage split-brain extensions: carry the local split-brain proof
   into S3-like adapters with multipart/partial-object validation and cleanup
   reachability failure evidence.
2. Stream/REST cursor production edge proofs: carry the local cursor
   failure-injection proof into live broker rebalance/commit-unknown cases.

## Implemented Regression Tests

These tests now exist and should remain as regression shields:

```text
test_dataset_commit_storage_success_db_failure_creates_orphan_cleanup_evidence
test_dataset_commit_db_success_manifest_missing_marks_storage_corruption
test_dataset_preview_data_file_missing_marks_storage_corruption
test_concurrent_dataset_commits_allocate_strictly_increasing_versions
test_abort_cleanup_never_deletes_committed_manifest
test_s3_dataset_storage_adapter_contract
test_s3_partial_multipart_upload_never_becomes_committed_version
test_s3_commit_storage_success_db_failure_creates_orphan_cleanup_evidence
test_s3_committed_manifest_missing_marks_storage_corruption
test_s3_abort_cleanup_never_deletes_committed_manifest
test_s3_concurrent_dataset_commits_allocate_strictly_increasing_versions
test_s3_retry_after_storage_timeout_does_not_duplicate_version
test_s3_storage_failure_is_visible_in_operations
test_dataset_health_check_reads_candidate_not_latest
test_schema_compatibility_revalidates_if_latest_schema_changes
test_csv_primary_key_preserves_leading_zeroes
test_transform_input_latest_is_pinned_to_version_id
test_downstream_transform_consumes_materialized_version_id_not_latest
test_transform_retry_after_commit_does_not_create_second_output_version
test_transform_output_and_lineage_commit_atomically
test_failed_action_not_included_in_success_action_log_materialization
test_action_log_same_cursor_rerun_does_not_duplicate_rows
test_materialization_retry_after_commit_metadata_failure_does_not_duplicate_output
test_materialization_late_commit_action_not_skipped
test_object_snapshot_mid_run_action_not_mixed
test_object_snapshot_fixed_watermark_hash_reproducible
test_materialization_cursor_not_advanced_before_dataset_commit
test_latest_action_run_watermark_returns_completed_at_cursor
test_materialization_created_at_tie_does_not_skip_rows
test_latest_object_record_watermark_returns_max_change_sequence
test_action_apply_is_idempotent_and_rejects_stale_object_version
test_action_same_idempotency_key_concurrent_requests_replay_same_action_run
test_action_same_idempotency_key_different_body_returns_409
test_action_expected_object_version_required
test_action_precondition_stale_read_conflicts_on_commit
test_action_commit_object_edit_audit_outbox_atomic
test_sqlalchemy_action_run_insert_or_get_existing_rolls_back_with_outer_transaction
test_action_idempotency_gate_flags_missing_service_request_fingerprint
test_action_idempotency_gate_flags_missing_schema_request_fingerprint
test_rest_mutable_pagination_detected_or_marked_non_replayable
test_webhook_ack_not_sent_before_append_commit_or_has_replay_strategy
test_webhook_same_event_id_different_payload_is_deduped
test_rest_redirect_to_private_ip_blocked
test_rest_dns_rebinding_to_private_ip_blocked
test_cdc_object_indexing_updates_tombstones_and_skips_stale_events
test_cdc_pk_update_policy
test_masked_property_cannot_filter_sort_search
test_rls_tenant_context_reset_between_pooled_connections
```

## Regression Test Names To Reserve

Use these names when turning the backlog into implementation PRs. They are
future requirements, not claims that the tests already exist.

```text
test_stream_worker_crash_after_file_write_before_db_commit_replays_same_offsets
test_partial_multipart_upload_never_becomes_committed_version
```
