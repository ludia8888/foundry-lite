# Sprint Evidence Ledger

**Last updated:** 2026-06-16 KST
**Purpose:** 스프린트 문서의 완료 체크박스가 실제 코드, 게이트, GitHub PR/merge commit과 서로 다르게 말하지 않도록 추적한다.

이 문서는 비개발자 관점의 “완료 영수증”이다. 스프린트 문서에서 `[x]`로 표시된 현재 상태 항목은 여기의 evidence id를 통해 PR, merge commit, 테스트, 품질 게이트 중 하나 이상과 연결되어야 한다.

## Checkbox Tracking Rules

- 상태 추적용 `[x]` 체크박스는 이 문서의 evidence id, GitHub PR, merge commit, 또는 검증 명령과 연결한다.
- 상태 추적용 `[ ]` 체크박스는 아직 완료로 보지 않는다. PR/merge evidence가 생기기 전까지는 계획 또는 남은 작업이다.
- `foundry_lite_python_engineering_guidelines_ko.md`의 코드 리뷰/장애 대응/금지 패턴 체크리스트처럼 “매 변경 때 사람이 확인하는 템플릿”은 제품 완료 상태가 아니다. 그런 체크박스는 일부러 `[ ]`로 남겨도 stale status가 아니다.
- 완료 체크박스를 바꾸는 PR은 이 문서도 같은 PR에서 갱신한다.

## Git Evidence

| Evidence id | Scope | GitHub PR / merge commit | Merge time |
|---|---|---|---|
| `PR-2` | Sprint 36A 운영 안정성: idempotency 동시성, dataset commit 경합/cleanup, operations paging, production auth fail-fast, SDK parity | [PR #2](https://github.com/ludia8888/foundry-lite/pull/2), [2c2ea0889b073fe1bfffd15179d5b3559e3ea1e0](https://github.com/ludia8888/foundry-lite/commit/2c2ea0889b073fe1bfffd15179d5b3559e3ea1e0) | 2026-06-13 20:46 KST |
| `PR-3` | Sprint 37 REST pull connector, webhook ingest, signed object cursor groundwork | [PR #3](https://github.com/ludia8888/foundry-lite/pull/3), [e5d306108115fe34987fd5a3a488fd05cb9549e7](https://github.com/ludia8888/foundry-lite/commit/e5d306108115fe34987fd5a3a488fd05cb9549e7) | 2026-06-13 22:49 KST |
| `PR-4` | Object Query keyset hardening: signed opaque cursor, sort key + `object_id`, query-shape checksum, numeric property cast, DB pushdown, max page size | [PR #4](https://github.com/ludia8888/foundry-lite/pull/4), [06a65886ea0522594d34d2929ce45d0505451551](https://github.com/ludia8888/foundry-lite/commit/06a65886ea0522594d34d2929ce45d0505451551) | 2026-06-13 23:33 KST |
| `PR-5` | Sprint 38 local/fake stream archive writer: `StreamAdapter` event to raw archive dataset, offset checkpoint, lag metric, failure trace | [PR #5](https://github.com/ludia8888/foundry-lite/pull/5), [a3113a0e65ad26e90f8966f8800b202be0eeb14c](https://github.com/ludia8888/foundry-lite/commit/a3113a0e65ad26e90f8966f8800b202be0eeb14c) | 2026-06-14 00:04 KST |
| `PR-6` | Sprint evidence ledger and REST connector hardening follow-up | [PR #6](https://github.com/ludia8888/foundry-lite/pull/6), [a4a1c4221c090409ebd5fd0def682a1ce2d59438](https://github.com/ludia8888/foundry-lite/commit/a4a1c4221c090409ebd5fd0def682a1ce2d59438) | 2026-06-14 KST |
| `PR-7` | Adapter failure taxonomy hardening and public API coverage protocol handling | [PR #7](https://github.com/ludia8888/foundry-lite/pull/7), [b40a3bcfbf4be0556d820e46ef162d5b25203ca5](https://github.com/ludia8888/foundry-lite/commit/b40a3bcfbf4be0556d820e46ef162d5b25203ca5) | 2026-06-14 KST |
| `PR-8` | Production-compatible Kafka stream adapter and worker composition root | [PR #8](https://github.com/ludia8888/foundry-lite/pull/8), [61ae5e31836ae25d75b9dccc42cab1f2ac326345](https://github.com/ludia8888/foundry-lite/commit/61ae5e31836ae25d75b9dccc42cab1f2ac326345) | 2026-06-14 KST |
| `PR-9` | Live Kafka-compatible broker smoke for Sprint 38 stream archive worker | [PR #9](https://github.com/ludia8888/foundry-lite/pull/9), [383bcad848afac8985fd0d500f499790bba1e063](https://github.com/ludia8888/foundry-lite/commit/383bcad848afac8985fd0d500f499790bba1e063) | 2026-06-14 KST |
| `PR-10` | Sprint 39 Debezium-shaped CDC envelope archive proof and CDC preview fields | [PR #10](https://github.com/ludia8888/foundry-lite/pull/10), [898689cb46ed5575ca9da69d6086cef3d7a141d3](https://github.com/ludia8888/foundry-lite/commit/898689cb46ed5575ca9da69d6086cef3d7a141d3) | 2026-06-14 KST |
| `PR-11` | Sprint 39 live Debezium PostgreSQL CDC topic proof plus Operations failure/lag evidence | [PR #11](https://github.com/ludia8888/foundry-lite/pull/11), [67eccb5824975d970c3aa64c2fcfd86ed49236fd](https://github.com/ludia8888/foundry-lite/commit/67eccb5824975d970c3aa64c2fcfd86ed49236fd) | 2026-06-14 KST |
| `PR-12` | Sprint 40 CDC object indexing, tombstone delete, replay/stale skip, and object.changed trigger proof | [PR #12](https://github.com/ludia8888/foundry-lite/pull/12), [f19bad9ad77f1cd8fea5de982bdafd5993c5099c](https://github.com/ludia8888/foundry-lite/commit/f19bad9ad77f1cd8fea5de982bdafd5993c5099c) | 2026-06-14 08:48 KST |
| `PR-14` | Sprint 41 shadow reindex, active index pointer, count/hash validation, and action-edit replay proof | [PR #14](https://github.com/ludia8888/foundry-lite/pull/14), [b292e63956a910b1614d66851edcd6489a36897b](https://github.com/ludia8888/foundry-lite/commit/b292e63956a910b1614d66851edcd6489a36897b), [5b00d9c027cc8d7669c4d4d309d924e0bdf2f791](https://github.com/ludia8888/foundry-lite/commit/5b00d9c027cc8d7669c4d4d309d924e0bdf2f791) | 2026-06-14 11:15 KST |
| `PR-16` | Sprint 42 OpenSearch-compatible search projection, query planner branch, rebuild CLI, and drift detection proof | [PR #16](https://github.com/ludia8888/foundry-lite/pull/16), [5b9b0cac557d6f90914070f54881a3ac35a26354](https://github.com/ludia8888/foundry-lite/commit/5b9b0cac557d6f90914070f54881a3ac35a26354), [72718f3ffbcfc85c962d3de00f67a885cde41ec1](https://github.com/ludia8888/foundry-lite/commit/72718f3ffbcfc85c962d3de00f67a885cde41ec1) | 2026-06-14 16:43 KST |
| `PR-22` | Action service workflow refactor: action command/idempotency, unit-of-work mutation commit, and writeback evidence split while keeping `action_service.py` below the application module cap | [PR #22](https://github.com/ludia8888/foundry-lite/pull/22), [86dd93d007ec5bfb506ecbf3093bb5eae640de26](https://github.com/ludia8888/foundry-lite/commit/86dd93d007ec5bfb506ecbf3093bb5eae640de26) | 2026-06-15 KST |
| `GATE-FOUNDATION` | 품질 게이트, release gate, infra boundary hardening | [e1fc49e81c2262c69321eaf6b991f425969b6e35](https://github.com/ludia8888/foundry-lite/commit/e1fc49e81c2262c69321eaf6b991f425969b6e35) | main history |
| `AUTH-PORT` | `AuthProvider` port, HeaderTrust/Demo local adapters | [b14c70f843dc0faedbde72f5639a28f15389de09](https://github.com/ludia8888/foundry-lite/commit/b14c70f843dc0faedbde72f5639a28f15389de09) | main history |
| `OBJECT-READ-PORT` | `ObjectReadRepository` boundary extraction | [87a08d7b79795354d3d8782981746978e6e1d07c](https://github.com/ludia8888/foundry-lite/commit/87a08d7b79795354d3d8782981746978e6e1d07c) | main history |
| `CORE-DI` | explicit service graph, dependency/collaborator declarations, no hidden facade registry | [44b9d926966d2656dcd650cda3d15b14d5f19137](https://github.com/ludia8888/foundry-lite/commit/44b9d926966d2656dcd650cda3d15b14d5f19137), [522ef32be290035521e8915e30ef2978e98069ad](https://github.com/ludia8888/foundry-lite/commit/522ef32be290035521e8915e30ef2978e98069ad), [edaad8cd1ad921c565d315f2e74dbc9cf0b9c91e](https://github.com/ludia8888/foundry-lite/commit/edaad8cd1ad921c565d315f2e74dbc9cf0b9c91e) | main history |
| `OBJECT-SETS` | Sprint 21 Object Sets implementation and tests | [ff05ab65ff91a56b7cbe6fcf84dd0d2a44777a3f](https://github.com/ludia8888/foundry-lite/commit/ff05ab65ff91a56b7cbe6fcf84dd0d2a44777a3f) | main history |
| `MVP-CORE` | Core MVP, operations/security/SDK/release-gate foundation | [0249af83f7cc7420def0892cb0d73e8faa342612](https://github.com/ludia8888/foundry-lite/commit/0249af83f7cc7420def0892cb0d73e8faa342612) | main history |
| `MVP-HONESTY` | MVP honesty and quality-gate hardening before PR workflow | [c96591dd6c056fac524bec51565b811d1a77614f](https://github.com/ludia8888/foundry-lite/commit/c96591dd6c056fac524bec51565b811d1a77614f), [d24ab67a786456cbb7fb97e0409ac66b865b0414](https://github.com/ludia8888/foundry-lite/commit/d24ab67a786456cbb7fb97e0409ac66b865b0414) | main history |

## Current Verification Evidence

<a id="verify-static"></a>
<a id="verify-full-ci-gate"></a>
<a id="verify-s22-web-dataset-object-refresh"></a>
<a id="verify-s22-web-ontology-validation"></a>
<a id="verify-mvp-web-object-link"></a>
<a id="verify-materialization-watermarks"></a>
<a id="verify-materialized-transform-pinning"></a>
<a id="verify-action-idempotency-fingerprint"></a>
<a id="verify-action-commit-atomicity"></a>

2026-06-15 KST에 현재 checkout에서 다시 확인한 명령이다.

| Evidence id | Command | Result |
|---|---|---|
| `VERIFY-DOC-DRIFT` | `pnpm --silent quality:doc-drift` | PASS. current-state docs reference existing code paths and symbols. |
| `VERIFY-INFRA-BOUNDARIES` | `pnpm --silent quality:infra-boundaries` | PASS. domain concrete infra import `0/0`, application concrete infra import `0/0`, service dependency declarations OK, service call graph cycles `0`, max depth `7/7`, max fan-out `8/10`. |
| `VERIFY-STATIC` | `pnpm --silent quality:static` | PASS. Ruff, format check, mypy, pyright, architecture, infra boundaries, module size, function length, boolean naming, typed boundary, router purity, query side-effect, repository boundaries, tenant write, contract-test-per-port, strategy/spec tests, integration markers, regression/root-cause local checks, docs, SDK generation, schema revision, audit/outbox/idempotency/request-id/log/metrics/adapter-failure-taxonomy/no-bypass/no-sleep/coverage/private facade gates all passed. |
| `VERIFY-FULL-CI-GATE` | `env DOCKER_HOST=unix:///Users/isihyeon/.colima/default/docker.sock TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock pnpm --silent ci:gate` | PASS. Testcontainers preflight reached Colima, full pytest `805 passed`, total coverage `96.13%`, flaky detector stable across `3` repeated random/parallel runs with `805 passed`, layer coverage passed with infrastructure at `97.65%`, public callable smoke coverage `100.00%`, OpenLineage/audit/outbox/MVP correctness/performance/trace/adapter-error/failed-mutation/runtime diagnostics/Playwright gates passed. Evidence log: `artifacts/test-results/ci-gate-20260615-tricky-failure-rerun.log`; layer report: `artifacts/quality/tier_coverage_by_layer.json`. |
| `VERIFY-INFRA-COVERAGE-EDGE-TESTS` | `DOCKER_HOST=unix:///Users/isihyeon/.colima/default/docker.sock TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock uv run pytest -q tests/unit/test_infrastructure_repository_edges.py tests/unit/test_opensearch_adapter_edges.py tests/contracts/test_object_set_repository_contract.py::test_object_set_repository_contract_empty_member_inputs_are_noops tests/contracts/test_rest_connector_adapter_contract.py::test_rest_url_validation_rejects_missing_host tests/contracts/test_rest_connector_adapter_contract.py::test_rest_legacy_ipv4_parser_rejects_invalid_private_host_spellings` | PASS. `16 passed`. This closes the previous infrastructure layer coverage gap by directly proving fail-closed local-runtime adapter profile selection, object change sequence impossible-state errors, malformed webhook metadata rejection, object-set empty-input no-op behavior, REST missing-host/legacy-IPv4 parser edge cases, and OpenSearch existing-mapping/lazy-client/malformed-hit defaults. |
| `VERIFY-TRICKY-FAILURE-FOCUSED` | `DOCKER_HOST=unix:///Users/isihyeon/.colima/default/docker.sock TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock uv run pytest tests/unit/test_dataset_storage_consistency.py tests/unit/test_dataset_transaction_commit_atomicity.py tests/contracts/test_dataset_storage_adapter_contract.py tests/unit/test_action_service_idempotency.py tests/contracts/test_action_repository_contract.py tests/integration/test_ontology_action_security.py tests/smoke/test_interfaces.py::test_action_expected_object_version_required tests/unit/test_quality_action_idempotency.py tests/contracts/test_materialization_repository_contract.py tests/integration/test_closed_loop.py tests/integration/test_stream_archive_ingest.py tests/integration/test_rest_connector_ingest.py -q -ra` | PASS. `102 passed`. This focused suite covers the tricky-failure checklist items hardened in the current checkout: stream/REST cursor commit points, local dataset/storage split-brain detection, action idempotency fingerprint conflict, same-key race replay, expected-object-version enforcement, internal action commit atomicity, and materialization cursor/watermark tie-breakers. |
| `VERIFY-REST-WEBHOOK-TRICKY-EDGES` | `uv run pytest tests/contracts/test_rest_connector_adapter_contract.py tests/smoke/test_interfaces.py::test_webhook_same_event_id_different_payload_is_deduped tests/smoke/test_interfaces.py::test_webhook_signature_replay_and_clock_skew_policy tests/smoke/test_interfaces.py::test_webhook_ack_not_sent_before_append_commit_or_has_replay_strategy -q -ra` | PASS. `39 passed`. This proves the REST connector rejects non-replayable page-number pagination, validates private-network redirects and DNS rebinding during redirect chains, normalizes encoded/decimal/octal redirect-host variants before private-network checks, replays same webhook event ids when only volatile timestamp fields change, rejects materially different duplicate event ids, rejects stale timestamp-bound webhook signatures before append, and does not return 2xx when webhook append persistence aborts. |
| `VERIFY-ACTION-AUDIT-MASKING` | `uv run pytest tests/integration/test_ontology_action_security.py::test_action_audit_masks_sensitive_params -q -ra` | PASS. `1 passed`. This proves an action can update the sensitive `Order.margin` property while durable `audit_events` store masked before/after references instead of the raw previous value or raw patch value. |
| `VERIFY-PRODUCTION-AUTH-GUARD` | `uv run pytest tests/unit/test_auth_profile_startup.py::test_production_refuses_dev_header_trust_auth -q -ra` | PASS. `4 passed`. This proves production startup refuses `header-trust`, `local_header_trust`, `demo`, and `demo_admin` auth profiles before request headers could become identity evidence. |
| `VERIFY-OBJECT-QUERY-CURSOR-GUARDS` | `uv run pytest tests/unit/test_object_query_service_paging.py::test_object_query_cursor_signed_tamper_proof_query_shape_bound tests/unit/test_object_query_service_paging.py::test_object_query_db_backed_keyset_no_memory_slice -q -ra` | PASS. `2 passed`. This proves object query cursors reject tampering, reject reuse under a different filter/order query shape, and request one-row-lookahead keyset pages from the repository instead of reading the full object row set into memory. |
| `VERIFY-OBJECT-QUERY-NUMERIC-CASTS` | `DOCKER_HOST=unix:///Users/isihyeon/.colima/default/docker.sock TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock uv run pytest tests/contracts/test_object_read_repository_contract.py::test_object_query_numeric_property_casts_for_sort_and_filter -q -ra` | PASS. `3 passed`. This proves fake, SQLite, and PostgreSQL object-read repositories cast JSON numeric properties consistently for filter and sort comparisons, so string-stored values like `"10"` do not sort before `"7"` or bypass numeric filters. |
| `VERIFY-DYNAMIC-OBJECT-SET-PAGE-LIMIT` | `uv run pytest tests/unit/test_object_sets.py::test_dynamic_object_set_cannot_bypass_page_limit -q -ra` | PASS. `1 passed`. This proves dynamic object-set membership collection pages through Object Query with the public 500-row page limit and advances by cursor instead of bypassing the bounded query path. |
| `VERIFY-STATIC-OBJECT-SET-PERMISSION` | `uv run pytest tests/unit/test_object_sets.py::test_static_object_set_rechecks_object_permission -q -ra` | PASS. `1 passed`. A public static object set created by an admin can be read by a viewer, but each member payload is rebuilt through the object read/query item boundary, so the viewer sees masked sensitive Order margin values instead of raw membership rows. |
| `VERIFY-WORKER-TENANT-CONTEXT` | `uv run pytest tests/contracts/test_kafka_stream_adapter_contract.py::test_worker_requires_tenant_context_for_background_jobs -q -ra` | PASS. `1 passed`. This proves the stream archive background worker refuses to build a request context from a blank tenant id, so worker writes cannot silently fall back to an empty tenant scope. |
| `VERIFY-LINK-TENANT-BOUNDARY` | `DOCKER_HOST=unix:///Users/isihyeon/.colima/default/docker.sock TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock uv run pytest tests/contracts/test_object_read_repository_contract.py::test_link_traversal_never_crosses_tenant_without_policy -q -ra` | PASS. `3 passed`. This proves fake, SQLite, and PostgreSQL link traversal reads require the caller tenant id, so a tenant-demo link query does not include tenant-other links with the same from object id. |
| `VERIFY-LINK-REVERSE-TRAVERSAL` | `DOCKER_HOST=unix:///Users/isihyeon/.colima/default/docker.sock TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock uv run pytest tests/contracts/test_object_read_repository_contract.py::test_object_read_repository_contract_lists_active_incoming_links tests/unit/test_helpers_and_query.py::test_link_reverse_traverses_customer_to_orders -q` | PASS. `4 passed`. This proves fake, SQLite, and PostgreSQL repositories can read active incoming `OrderCustomer` links by tenant/type/target object, and the public object link API returns `Customer/C-100 -> Order/O-1001,O-1003` without creating duplicate reverse link rows. |
| `VERIFY-LINK-MISSING-TARGET-WARNING` | `uv run pytest tests/unit/test_helpers_and_query.py::test_link_reports_missing_target_object -q` | PASS. `1 passed`. This proves an object link whose target object is no longer present in the active object index is not silently hidden: the traversal response keeps the target object type/id and returns `targetMissing=true` plus a `link_target_missing` warning without writing side effects from the query path. |
| `VERIFY-DATASET-HEALTH-CANDIDATE` | `uv run pytest tests/integration/test_dataset_quality.py::test_dataset_health_check_reads_candidate_not_latest -q -ra` | PASS. `1 passed`. This proves dataset health checks inspect the staged candidate upload, not the latest committed version: a valid latest remains the only committed version, while a duplicate-key candidate aborts with a `unique` check failure. |
| `VERIFY-DATASET-VERSION-CONCURRENCY` | `DOCKER_HOST=unix:///Users/isihyeon/.colima/default/docker.sock TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock uv run pytest -q tests/contracts/test_dataset_transaction_repository_contract.py::test_dataset_transaction_repository_contract_rejects_duplicate_dataset_version tests/contracts/test_dataset_transaction_repository_contract.py::test_concurrent_dataset_commits_allocate_strictly_increasing_versions tests/unit/test_dataset_transaction_commit_atomicity.py` | PASS. `7 passed`. PostgreSQL contract proof starts two concurrent commits against the same dataset and forces the second transaction to attempt the dataset lock while the first still holds it; committed versions allocate strictly increasing `version_number` values `1` and `2`. The companion tests prove duplicate version insertion becomes `DatasetVersionConflictError`, finalization maps that to `ConflictDetected`, emits no commit outbox/audit, and removes the already-promoted version artifact. |
| `VERIFY-DATASET-SAME-CONTENT-REATTACH` | `DOCKER_HOST=unix:///Users/isihyeon/.colima/default/docker.sock TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock uv run pytest tests/contracts/test_dataset_transaction_repository_contract.py::test_dataset_transaction_repository_contract_allows_same_content_hash_as_new_version -q` | PASS. `3 passed` across fake, SQLite, and PostgreSQL repository profiles. This freezes the MVP reattach policy for S05-A4: `content_hash` is a content verification and trace key, not a dedupe key. Reattaching the same file content creates a new transaction, new dataset version, and new file row while preserving the previous committed version unchanged. |
| `VERIFY-CSV-PK-STRING-PRESERVATION` | `uv run pytest tests/contracts/test_compute_adapter_contract.py::test_csv_primary_key_preserves_leading_zeroes -q -ra` | PASS. `2 passed`. Both fake and DuckDB compute adapters preserve CSV primary key value `"00123"` as a string and expose the key column as non-null string in schema inspection. |
| `VERIFY-SCHEMA-COMPATIBILITY-TOCTOU` | `uv run pytest tests/unit/test_dataset_transaction_commit_atomicity.py::test_schema_compatibility_revalidates_if_latest_schema_changes -q -ra` | PASS. `1 passed`. This proves dataset finalization takes the dataset lock before schema compatibility validation, so schema checking and version allocation share one dataset lock window and cannot be separated by a competing latest-schema commit in the local finalization path. |
| `VERIFY-TRANSFORM-INPUT-PINNING` | `uv run pytest tests/integration/test_closed_loop.py::test_transform_input_latest_is_pinned_to_version_id -q -ra` | PASS. `1 passed`. This proves a transform run pins input dataset version ids during planning: the test commits a newer input latest immediately before SQL execution, yet the output still reads the originally pinned input version and the transform run records that version id in `input_versions`. |
| `VERIFY-MATERIALIZED-TRANSFORM-PINNING` | `uv run pytest tests/integration/test_closed_loop.py::test_downstream_transform_consumes_materialized_version_id_not_latest -q -ra` | PASS. `1 passed`. This proves a downstream transform consumes the exact materialized `ops.order_current` dataset version captured during planning instead of resolving latest at SQL execution time. The test emits a `materialization.completed` outbox event with the first materialized `versionId`, creates a newer `ops.order_current` latest before SQL execution, and then proves the transform output still reads the first version while the newer materialization replay shows the approved state. |
| `VERIFY-TRANSFORM-RETRY-NO-DUPLICATE-OUTPUT` | `uv run pytest tests/integration/test_closed_loop.py::test_transform_retry_after_commit_does_not_create_second_output_version -q -ra` | PASS. `1 passed`. This proves a successful transform run cannot be retried through the Operations retry path, so the output dataset keeps exactly one version instead of creating a duplicate output for the same successful logical run. |
| `VERIFY-TRANSFORM-LINEAGE-ATOMIC` | `uv run pytest tests/integration/test_closed_loop.py::test_transform_output_and_lineage_commit_atomically -q -ra` | PASS. `1 passed`. The test injects a lineage-write failure after transform output storage promotion. The common dataset finalization boundary removes the promoted output artifact, leaves the output dataset with no committed version, keeps lineage empty, marks the transform run `FAILED`, and persists `orphan_cleanup` evidence on the run error. |
| `VERIFY-TRANSFORM-OOM-STAGING-CLEANUP` | `uv run pytest tests/integration/test_closed_loop.py::test_duckdb_oom_aborts_output_transaction tests/contracts/test_dataset_storage_adapter_contract.py::test_dataset_storage_adapter_contract -q -ra` | PASS. `3 passed`. The transform test uses a failing compute adapter that writes a partial staged output file and then raises `MemoryError`; the output dataset has no committed version, the transform run is `FAILED`, and the staged file is deleted. The storage adapter contract proves local and fake-storage profiles implement idempotent transaction-staging cleanup. |
| `VERIFY-SQL-TRANSFORM-FILESYSTEM-GUARD` | `uv run pytest tests/unit/test_transform_sql_guards.py tests/integration/test_closed_loop.py::test_sql_transform_cannot_read_arbitrary_filesystem_path tests/contracts/test_compute_adapter_contract.py::test_compute_adapter_contract_sql_transform_and_unresolved_inputs -q -ra` | PASS. `7 passed`. The direct guard tests cover declared-input SQL, raw filesystem read rejection, and comment handling. The integration proof rejects a transform SQL statement that calls a raw CSV path read directly, marks the transform run `FAILED`, and leaves no output dataset version. The compute-adapter contract still proves declared `{{ input('namespace.name') }}` transforms execute normally and unresolved declared inputs fail explicitly. |
| `VERIFY-PYTHON-TRANSFORM-FAIL-CLOSED` | `uv run pytest tests/integration/test_closed_loop.py::test_python_transform_cannot_access_raw_storage_path -q -ra` | PASS. `1 passed`. Python transforms are rejected at registration with `unsupported transform language`, so user Python code cannot receive storage credentials or raw storage paths before a sandboxed SDK input/output abstraction exists. The rejected definition is not persisted and cannot be run. |
| `VERIFY-CONTRACT-GATE` | `pnpm --silent quality:contract-tests` | PASS. every application port has a contract suite. |
| `VERIFY-SCALE-ADAPTERS` | `uv run pytest tests/contracts/test_auth_provider_contract.py tests/contracts/test_connector_adapter_contract.py tests/contracts/test_search_adapter_contract.py tests/contracts/test_stream_adapter_contract.py tests/contracts/test_workflow_adapter_contract.py tests/integration/test_scale_foundation.py tests/integration/test_stream_archive_ingest.py -q` | PASS. `29 passed in 0.38s`. |
| `VERIFY-TESTCONTAINERS-PREFLIGHT` | `pnpm --silent quality:testcontainers-preflight` | FAIL FAST as designed in a Docker-unreachable shell. The message tells the operator to set `DOCKER_HOST=unix:///Users/isihyeon/.colima/default/docker.sock` and `TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock` before rerunning `pnpm --silent ci:gate`. Unit proof: `uv run pytest tests/unit/test_quality_testcontainers_preflight.py -q`, `5 passed`. This preflight now protects both PostgreSQL and Kafka Testcontainers evidence. |
| `VERIFY-REST-SSRF` | `uv run pytest tests/integration/test_rest_connector_ingest.py tests/contracts/test_rest_connector_adapter_contract.py -q` | PASS outside the sandbox because the mock REST server needs local TCP bind permission. `24 passed in 7.35s`. |
| `VERIFY-REST-WEBHOOK-OPS` | `uv run pytest tests/integration/test_rest_connector_ingest.py tests/contracts/test_rest_connector_adapter_contract.py -q`; `uv run pytest tests/smoke/test_interfaces.py::test_api_webhook_ingest_verifies_signature_and_appends_dataset tests/unit/test_quality_testcontainers_preflight.py -q`; `FOUNDRY_LITE_SKIP_POSTGRES_CONTRACTS=1 uv run pytest tests/contracts/test_dataset_transaction_repository_contract.py::test_dataset_transaction_repository_contract_finds_committed_webhook_event -q` | PASS. REST cursor/adapter suite: `24 passed in 7.35s` outside the sandbox for local TCP bind permission. Webhook API duplicate replay plus preflight unit proof: `6 passed`. Webhook transaction lookup contract: `2 passed, 1 skipped` with local-only Postgres skip. |
| `VERIFY-ADAPTER-FAILURE-TAXONOMY` | `pnpm --silent quality:adapter-failure-taxonomy`; `uv run pytest tests/contracts/test_adapter_failure_contract.py tests/unit/test_quality_adapter_failure_taxonomy.py tests/contracts/test_rest_connector_adapter_contract.py tests/integration/test_rest_connector_ingest.py::test_rest_connector_rate_limit_failure_is_visible_in_operations -q` | PASS. `18` concrete adapter profiles expose `AdapterFailureContract`; targeted adapter taxonomy/REST failure payload tests cover local/fake, REST, auth, Kafka stream, Debezium CDC stream, and OpenSearch search failure contracts. |
| `VERIFY-KAFKA-STREAM-WORKER` | `uv run pytest tests/contracts/test_kafka_stream_adapter_contract.py tests/contracts/test_adapter_failure_contract.py tests/unit/test_quality_adapter_failure_taxonomy.py -q`; `pnpm --silent quality:adapter-failure-taxonomy` | PASS. Production-compatible `KafkaStreamAdapter` parses broker-shaped messages, the worker archives one micro-batch through `FoundryLiteCore.archive_stream_events`, and the adapter taxonomy gate covers every current concrete adapter profile. This focused contract proof is complemented by `VERIFY-KAFKA-LIVE-BROKER`. |
| `VERIFY-KAFKA-LIVE-BROKER` | `DOCKER_HOST=unix:///Users/isihyeon/.colima/default/docker.sock TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock uv run pytest tests/integration/test_kafka_live_broker_stream_archive.py -q` | PASS. `1 passed in 14.93s`. `KafkaContainer` boots a real Kafka-compatible broker, `KafkaStreamAdapter.publish_event` writes a shipment event to a live topic, and `foundry_lite_worker.stream_archive.run_stream_archive_once` reads the broker topic through the same application boundary and commits `raw.shipment_events` with event id `<topic>:0:0`. |
| `VERIFY-CDC-STREAM-ARCHIVE` | `pnpm --silent quality:cdc-stream-archive`; `pnpm --silent quality:adapter-failure-taxonomy` | PASS. `13 passed in 0.36s`; adapter taxonomy gate passed with `18` concrete adapter profiles. `DebeziumPostgresStreamAdapter` normalizes Debezium-shaped insert/update/delete payloads into the standard CDC envelope, rejects malformed envelopes as adapter validation failures, reports publish/read validation failures under the correct adapter operation, and `tests/integration/test_cdc_stream_archive.py` proves `StreamArchiveConfig(schema_strategy="cdc_envelope_json")` commits `raw_cdc.erp_orders` rows with top-level `op`, `pk_json`, `before_json`, `after_json`, and `ordering_json` preview fields. CDC stream lag updates `foundry_lite_stream_archive_lag_events`, CDC read failures now create FAILED sync runs with Debezium adapter failure payloads in Operations, and stream archive resume cursors require a matching `schemaStrategy`. |
| `VERIFY-STREAM-REST-CURSOR-COMMIT-POINTS` | `uv run pytest tests/integration/test_stream_archive_ingest.py::test_stream_offset_not_advanced_when_append_commit_fails tests/integration/test_rest_connector_ingest.py::test_rest_cursor_not_advanced_when_dataset_commit_fails -q` | PASS. `2 passed`. The stream proof commits offset `0`, reads offset `1`, forces Parquet write failure, then proves retry archives offset `1` instead of skipping it. The REST proof commits page 1 with `nextCursor=page-2`, fetches page 2 while forcing dataset write failure, then proves the next successful sync requests `page-2` again instead of treating the failed attempt's `nextCursor=None` as durable. |
| `VERIFY-DEBEZIUM-LIVE-CDC` | `DOCKER_HOST=unix:///Users/isihyeon/.colima/default/docker.sock TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock uv run pytest tests/integration/test_debezium_live_cdc.py -q` | PASS. `1 passed in 40.45s`. Testcontainers boots a real Kafka-compatible broker, PostgreSQL with `wal_level=logical`, and Debezium Connect `quay.io/debezium/connect:3.5`; the proof waits for a non-empty RUNNING connector task list and the logical replication slot before writing changes, so insert/update/delete against `public.orders` reliably appear on the Debezium topic and `foundry_lite_worker.stream_archive.run_stream_archive_once` commits three CDC changelog rows into `raw_cdc.erp_orders`. |
| `VERIFY-CDC-OBJECT-INDEXING` | `DOCKER_HOST=unix:///Users/isihyeon/.colima/default/docker.sock TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock uv run pytest tests/unit/test_cdc_indexing.py tests/contracts/test_object_index_repository_contract.py tests/integration/test_cdc_object_indexing.py -q`; `pnpm --silent quality:cdc-object-indexing` | PASS. Package script: `23 passed in 2.82s`; focused parser/integration workflow exposure: `15 passed in 0.31s`. `tests/unit/test_cdc_indexing.py` covers CDC envelope validation, JSON envelope parsing, primary-key fallback, stale ordering, and property-version metadata. `tests/integration/test_cdc_object_indexing.py` proves `backing.cdc` mapping, batch-rebuild-free CDC update, CDC-only object insert, tombstone delete handling, duplicate/stale ordering skip, `object.changed` outbox trigger, and `cdc_incremental` index run status. `tests/contracts/test_object_index_repository_contract.py` keeps the CDC object update repository contract aligned across fake, SQLite, and PostgreSQL. |
| `VERIFY-CDC-STALE-TOMBSTONE` | `uv run pytest -q tests/integration/test_cdc_object_indexing.py::test_cdc_object_indexing_updates_tombstones_and_skips_stale_events` | PASS. `1 passed`. This focused proof applies a newer CDC update, then verifies both a late stale update and a late snapshot/read event are skipped without changing the object version or status. It then applies a tombstone delete and verifies a later retry of the older update is skipped, leaving the object deleted and absent from active queries. The same proof materializes `ops.order_current` after deletion and verifies the snapshot also excludes the tombstoned object, including the valid zero-row materialization case. |
| `VERIFY-CDC-PK-UPDATE-POLICY` | `uv run pytest -q tests/unit/test_cdc_indexing.py tests/integration/test_cdc_object_indexing.py::test_cdc_object_indexing_updates_tombstones_and_skips_stale_events` | PASS. `14 passed`. `test_cdc_pk_update_policy` freezes the current MVP CDC primary-key update policy as fail-closed: if `before` and `after` identify different object ids, parsing raises `ValidationFailed` instead of silently creating a second active object or breaking action/link history. |
| `VERIFY-ACTIVE-INDEX-POINTER` | `uv run pytest tests/contracts/test_object_index_repository_contract.py::test_object_index_repository_contract_persists_empty_shadow_active_pointer tests/contracts/test_object_index_repository_contract.py::test_object_index_repository_contract_rejects_stale_shadow_pointer_switch tests/contracts/test_object_index_repository_contract.py::test_object_index_repository_contract_concurrent_first_pointer_switch_has_one_winner tests/contracts/test_object_index_repository_contract.py::test_object_index_repository_contract_rejects_unsupported_pointer_upsert_dialect tests/integration/test_cdc_object_indexing.py::test_empty_shadow_reindex_persists_active_pointer_for_next_cdc_insert -q`; `uv run python scripts/quality/check_schema_revision_guard.py` | PASS. [PR #18](https://github.com/ludia8888/foundry-lite/pull/18) hardens the active index version pointer, and [PR #19](https://github.com/ludia8888/foundry-lite/pull/19) upgrades promotion to compare-and-swap semantics. The repository contract proves fake, SQLite, and PostgreSQL can switch an object type to a new active index version even when there are no object rows to flip; stale switches fail if another promotion already changed the pointer; PostgreSQL allows only one winner when two connections concurrently create the first pointer for the same tenant/object type; and unsupported SQL dialects fail closed until they add their own native upsert contract. The integration proof creates an empty snapshot-backed object type, runs `index_shadow_rebuild`, then applies a CDC create event and verifies the inserted object row uses the shadow `indexVersion` recorded in `object_index_versions`. Schema revision `infra/schema_revisions/20260614_0002_object_index_active_pointer.json` freezes the active-pointer table. |
| `VERIFY-ACTIVE-INDEX-POINTER-FLAKE` | `DOCKER_HOST=unix:///Users/isihyeon/.colima/default/docker.sock TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock uv run python scripts/quality/check_flaky_detector.py --iterations 10 --output artifacts/quality/flaky_object_index_pointer.json --command 'uv run pytest tests/contracts/test_object_index_repository_contract.py::test_object_index_repository_contract_concurrent_first_pointer_switch_has_one_winner -q'` | PASS. `10` repeated PostgreSQL runs produced stable `1 passed`. This closes the primary-key-vs-natural-key race where two concurrent first pointer inserts could both generate `id=object_index_version:<tenant>:<object_type>` and one run could escape the `tenant_id` plus `object_type_id` upsert path as `object_index_versions_pkey` unique violation. The current insert row id is unique per attempt, while the upsert conflict target remains the natural pointer key `tenant_id` plus `object_type_id`. |
| `VERIFY-SHADOW-REINDEX` | `DOCKER_HOST=unix:///Users/isihyeon/.colima/default/docker.sock TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock pnpm --silent quality:shadow-reindex`; `pnpm --silent quality:static`; GitHub PR #14 `quality-gate` | PASS. Sprint 41 local verification: `quality:shadow-reindex` returned `38 passed`, covering fake/SQLite/Postgres object index/read/set repository contracts plus `tests/integration/test_shadow_reindex.py`. `quality:static` passed after schema revision `infra/schema_revisions/20260614_0001_shadow_reindex_index_versions.json`, proving schema drift, function length, module size, audit/transaction, tenant write, docs, and SDK gates still hold. GitHub PR #14 `quality-gate` passed in `10m13s`, then merged into main as `5b00d9c027cc8d7669c4d4d309d924e0bdf2f791`. |
| `VERIFY-SEARCH-INDEXING` | `pnpm --silent quality:search-indexing`; `pnpm --silent quality:adapter-failure-taxonomy`; `pnpm --silent quality:sdk-generated` | PASS. `quality:search-indexing` returned `8 passed`, covering local/fake/OpenSearch-compatible search adapter contract plus `tests/integration/test_search_indexing.py`. The integration proof marks Order `operatorNote` searchable, consumes an `object.changed` update into search, routes full-text query through the search adapter, verifies search rebuild count parity with active `object_records`, detects an OpenSearch-only orphan document, and proves a failing search adapter does not break `get_object` or basic Postgres-backed filters. Adapter taxonomy now covers `18` profiles including `opensearch`; SDK generated outputs include the `search` query payload field. |
| `VERIFY-SHADOW-SEARCH-PROJECTION-EDGES` | `uv run pytest tests/integration/test_shadow_reindex.py tests/contracts/test_search_adapter_contract.py tests/integration/test_search_indexing.py::test_search_stale_event_cannot_overwrite_newer_doc tests/integration/test_search_indexing.py::test_action_form_refetches_object_store_after_search_hit tests/unit/test_object_query_service_paging.py tests/unit/test_helpers_and_query.py::test_query_objects_filter_sort_cursor_and_invalid_op -q -ra` | PASS. `20 passed`. This closes the T0 shadow/search projection edges in the current checkout: `test_shadow_reindex_replays_action_edits` proves action edits survive shadow promotion, `test_shadow_reindex_alias_switch_cursor_version_safe` and `test_object_query_cursor_rejects_active_index_version_change` prove cursor tokens bind to the active index version and fail safely after a switch, `test_search_adapter_contract_ignores_stale_upsert` proves local/fake/OpenSearch-compatible adapters do not let an older document version overwrite a newer one, and `test_action_form_refetches_object_store_after_search_hit` proves search-hit entry re-reads the object store while exposing both `searchProjectionVersion` and stale status. |
| `VERIFY-MATERIALIZATION-WATERMARKS` | `DOCKER_HOST=unix:///Users/isihyeon/.colima/default/docker.sock TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock uv run pytest tests/contracts/test_materialization_repository_contract.py tests/integration/test_closed_loop.py tests/contracts/test_mvp_testcontainers_closed_loop.py -q`; `uv run python scripts/quality/check_schema_revision_guard.py` | PASS. Focused materialization/closed-loop coverage is now `45` tests across fake, SQLite, PostgreSQL, integration, and Testcontainers closed-loop paths. The proof includes `test_materialization_late_commit_action_not_skipped`, `test_materialization_created_at_tie_does_not_skip_rows`, `test_action_materialization_writes_dataset_versions_and_manifest_rows`, `test_object_snapshot_mid_run_action_not_mixed`, `test_object_records_at_watermark_returns_latest_version_per_object`, and `test_object_snapshot_fixed_watermark_hash_reproducible`. Object changes append `object_record_versions`, materialization runs store `object_change_sequence_lte` plus `active_index_version`, and replaying an old run's watermark after a later ApproveOrder action returns the original row hash/state while a new run sees the approved state. The integration proof also ties the `materializes_to` lineage edge to the materialization run id and target dataset version id. Schema revision `infra/schema_revisions/20260615_0001_object_record_versions.json` freezes the append-only object version table. |
| `VERIFY-MATERIALIZATION-COMMIT-FAILURE` | `uv run pytest tests/integration/test_closed_loop.py::test_materialization_cursor_not_advanced_before_dataset_commit -q -ra` | PASS. `1 passed`. The failure injection raises during `action_log` materialization row writing, then proves `ops.action_log` has no committed dataset version, the materialization run is marked `FAILED`, `target_dataset_version_id` remains empty, and the persisted error explains the write failure. |
| `VERIFY-ACTION-LOG-SUCCESS-ONLY` | `uv run pytest tests/integration/test_closed_loop.py::test_failed_action_not_included_in_success_action_log_materialization -q -ra` | PASS. `1 passed`. This proves a failed action run remains visible as operational failure evidence but is not copied into the successful `ops.action_log` materialization output. The test creates a simulated writeback failure, then a successful action for the same object version, materializes `action_log`, and replays the materialization rows to prove only the successful action id appears. |
| `VERIFY-ACTION-LOG-RERUN-NO-DUPLICATES` | `uv run pytest tests/integration/test_closed_loop.py::test_action_log_same_cursor_rerun_does_not_duplicate_rows -q -ra` | PASS. `1 passed`. This proves rerunning `action_log` materialization with the same completed-at/action id cursor produces a fresh snapshot with the same single action row instead of duplicating the row inside the output. The two materialization runs keep the same stored watermark. |
| `VERIFY-MATERIALIZATION-RETRY-NO-DUPLICATE-OUTPUT` | `uv run pytest tests/integration/test_closed_loop.py::test_materialization_retry_after_commit_metadata_failure_does_not_duplicate_output -q -ra` | PASS. `1 passed`. This injects a failure after the materialized file has been promoted but before metadata/outbox/lineage can commit. The failed run leaves no target dataset version and no visible `materialization.completed` outbox event, removes the promoted artifact, and a same-cursor retry commits exactly one `ops.order_current` output version with one completion event. |
| `VERIFY-DATASET-STORAGE-SPLIT-BRAIN` | `uv run pytest tests/unit/test_dataset_storage_consistency.py tests/unit/test_dataset_transaction_commit_atomicity.py tests/contracts/test_dataset_storage_adapter_contract.py -q` | PASS. `14 passed`. The focused tests force DB file-row persistence failure after storage promotion, prove the promoted version directory is removed, and prove the FAILED sync run keeps `orphan_cleanup` evidence. They also delete a committed manifest and committed data file to prove `inspect_dataset`/`preview_dataset` return an operator-facing `committed_version_storage_missing` invariant with dataset/version/manifest details. `test_abort_cleanup_never_deletes_committed_manifest` proves a later aborted upload does not remove the earlier committed manifest or data file. `test_schema_compatibility_revalidates_if_latest_schema_changes` proves schema compatibility validation runs under the dataset version-allocation lock. The local/fake storage contract still proves `manifest_uri` can restore the committed file list, and the local adapter verifies manifest file URI, byte size, and content hash after write, including empty-file-list, wrong-file-reference, byte-size, and content-hash rejection paths. |
| `VERIFY-ACTION-IDEMPOTENCY-FINGERPRINT` | `DOCKER_HOST=unix:///Users/isihyeon/.colima/default/docker.sock TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock uv run pytest tests/unit/test_action_service_idempotency.py tests/contracts/test_action_repository_contract.py tests/integration/test_ontology_action_security.py::test_action_apply_is_idempotent_and_rejects_stale_object_version tests/unit/test_quality_action_idempotency.py -q`; `uv run python scripts/quality/check_idempotency_on_action.py`; `uv run python scripts/quality/check_schema_revision_guard.py` | PASS. Focused action/idempotency suite now returns `24 passed` outside the sandbox with Docker socket access for the PostgreSQL repository contract. It proves same-key replay keeps one action run, concurrent same-key inserts replay the persisted winner, same-key/different-request reuse raises `ConflictDetected`, `action.run.idempotency_conflict` audit evidence is written, `action_runs.request_fingerprint` is stored across fake/SQLite/PostgreSQL repositories, and G12 blocks removal of the fingerprint storage, fingerprint guard, conflict audit, or schema column. Schema revision `infra/schema_revisions/20260614_0004_action_request_fingerprint.json` freezes the added column. |
| `VERIFY-ACTION-COMMIT-ATOMICITY` | `uv run pytest tests/contracts/test_action_repository_contract.py::test_sqlalchemy_action_run_insert_or_get_existing_rolls_back_with_outer_transaction tests/integration/test_ontology_action_security.py::test_action_commit_object_edit_audit_outbox_atomic -q` | PASS. `5 passed`. The proof injects failures at the action commit boundary after object-record mutation, at object-edit insertion, before terminal action success, during `object.edit.committed` outbox insertion, and during `action.run.committed` audit insertion. Each failure leaves the Order unchanged, writes no durable action run/writeback/object edit/action audit/action outbox evidence for the failed idempotency key, and allows the same idempotency key to succeed after the injected failure is disabled. The repository contract catches the root cause found during this work: savepoint-based idempotency winner insertion could escape an outer rollback on SQLite, so the implementation now uses dialect-native conflict-ignore insert semantics. |
| `VERIFY-S22-WEB-DATASET-OBJECT-REFRESH` | `uv run pytest tests/smoke/test_interfaces.py::test_api_dataset_object_action_and_metrics_smoke -q`; `pnpm exec playwright test tests/e2e/foundry-lite.spec.ts` | PASS. API smoke returned `1 passed`, and Playwright returned `1 passed`. This proves `GET /api/datasets/clean/orders/versions` exposes committed dataset version evidence, Web loads dataset versions plus preview rows for `clean.orders`, Web loads the pending Order list through object query, and a browser reload re-reads server state after `ApproveOrder` so the Object Explorer restores `Order/O-1001` as `APPROVED` instead of relying on stale client state. |
| `VERIFY-S22-WEB-ONTOLOGY-VALIDATION` | `uv run pytest tests/smoke/test_interfaces.py::test_api_dataset_object_action_and_metrics_smoke -q`; `pnpm exec playwright test tests/e2e/foundry-lite.spec.ts` | PASS. API smoke returned `1 passed`, and Playwright returned `1 passed`. This proves `POST /api/ontology/validate` validates ontology YAML against the current committed dataset schemas without activating a new ontology version, returns a structured `VALIDATION_FAILED` error for a missing primary-key column, and the Web Ontology Validation panel displays that error detail with the request id. |
| `VERIFY-MVP-WEB-OBJECT-LINK` | `uv run pytest tests/smoke/test_interfaces.py::test_api_dataset_object_action_and_metrics_smoke -q`; `pnpm exec playwright test tests/e2e/foundry-lite.spec.ts` | PASS. API smoke returned `1 passed`, and Playwright returned `1 passed`. This proves `GET /api/objects/Order/O-1001/links/OrderCustomer` exposes the Customer link through FastAPI, and the Web Object Explorer can load the same `OrderCustomer` link panel showing target `Customer` object id `C-100` before applying `ApproveOrder`. |

## Commit-Point Risk Tracking

The detailed Tier 0 audit lives in [Commit-Point Risk Register](./commit-point-risk-register.md).
This ledger keeps the short, evidence-oriented view so sprint checkboxes do not
claim more than the code and gates currently prove.

| Risk group | Register ids | Evidence accepted today | Current tracking result |
|---|---|---|---|
| Stream and connector cursor commit | T0-01, T0-02 | `S37-A7`, `S37-A9`, `S38-A1`-`S38-A4`, `VERIFY-REST-WEBHOOK-OPS`, `VERIFY-REST-WEBHOOK-TRICKY-EDGES`, `VERIFY-KAFKA-LIVE-BROKER`, `VERIFY-STREAM-REST-CURSOR-COMMIT-POINTS` | Partial. Normal committed cursor paths exist, local failure-injection proves stream offset / REST cursor do not advance when the next dataset write fails, and REST page-number pagination is rejected as non-replayable. Remaining edge proofs are live broker rebalance or commit-unknown failure around stream batches. |
| Dataset transaction, storage manifest, and candidate health check | T0-03, T0-04, T0-05, T0-06 | `S05-A1`, `S05-A2`, `S05-A3`, `S05-A5`, `S36A-A2`, `S36A-A3`, `VERIFY-DATASET-STORAGE-SPLIT-BRAIN`, `VERIFY-DATASET-VERSION-CONCURRENCY`, `VERIFY-DATASET-HEALTH-CANDIDATE`, `VERIFY-SCHEMA-COMPATIBILITY-TOCTOU` | Partial. Local MVP split-brain and version-race edges now have direct proofs: PostgreSQL concurrent commits allocate strictly increasing dataset version numbers under the dataset lock, duplicate version insertion maps to a domain conflict, promoted artifact cleanup runs after metadata conflict, FAILED run evidence is retained, missing manifest/data-file diagnosis works, manifest read verification works, abort cleanup does not delete committed artifacts, health checks inspect the candidate rather than latest, and schema validation runs under the dataset lock. Future production storage still needs multipart/partial-object validation, checked-manifest-hash persistence, and a richer production isolation race proof. |
| CSV primary-key normalization | T0-32 | `VERIFY-CSV-PK-STRING-PRESERVATION`; `tests/contracts/test_compute_adapter_contract.py::test_csv_primary_key_preserves_leading_zeroes` | Covered for the current CSV-to-Parquet compute boundary. Primary-key values with leading zeroes remain strings, and schema inspection keeps the key column non-null string. |
| Transform version pinning and lineage | T0-07, T0-08, T0-33 | `VERIFY-FULL-CI-GATE`, `VERIFY-TRANSFORM-INPUT-PINNING`, `VERIFY-MATERIALIZED-TRANSFORM-PINNING`, `VERIFY-TRANSFORM-RETRY-NO-DUPLICATE-OUTPUT`, `VERIFY-TRANSFORM-LINEAGE-ATOMIC`, `VERIFY-TRANSFORM-OOM-STAGING-CLEANUP`, `VERIFY-SQL-TRANSFORM-FILESYSTEM-GUARD`, `VERIFY-PYTHON-TRANSFORM-FAIL-CLOSED`, OpenLineage P8, failed-transform retry evidence | Partial. Input version pinning is covered even when latest changes before SQL execution, including the materialized `ops.order_current` downstream path where `materialization.completed` carries the exact `versionId` and a newer materialization becomes latest before SQL execution. Successful transform runs cannot be retried into duplicate outputs through the Operations retry path, lineage-write failure after output storage promotion now leaves no committed output version or lineage edge while cleaning the promoted artifact, compute failure after partial staging deletes the staged output, SQL transforms cannot directly read arbitrary filesystem paths, and Python transforms fail closed until a sandboxed SDK boundary exists. Remaining transform hardening is crash/process-kill proof after a successful output commit and before worker acknowledgement. |
| Action idempotency and atomic mutation | T0-11, T0-12, T0-13, T0-14, T0-34 | `S25-A1`-`S25-A5`, `S26-A1`-`S26-A3`, `S26-A5`, `S36A-A1`, `VERIFY-ACTION-IDEMPOTENCY-FINGERPRINT`, `VERIFY-ACTION-COMMIT-ATOMICITY`, `VERIFY-ACTION-AUDIT-MASKING`, transaction outbox/audit pair gate, MVP data-correctness gate, SDK generated helpers, `test_action_expected_object_version_required`, `test_action_precondition_stale_read_conflicts_on_commit` | Partial. Same-key race, same-key/different-request fingerprint conflict, expected-object-version enforcement, current internal DB action commit atomicity, and sensitive action audit masking are covered. Remaining action edge proofs are external writeback compensation and timeout outcome-unknown handling. |
| Materialization watermarks | T0-15, T0-16, T0-17 | `VERIFY-MATERIALIZATION-WATERMARKS`; `VERIFY-MATERIALIZATION-COMMIT-FAILURE`; `VERIFY-ACTION-LOG-SUCCESS-ONLY`; `VERIFY-ACTION-LOG-RERUN-NO-DUPLICATES`; `VERIFY-MATERIALIZATION-RETRY-NO-DUPLICATE-OUTPUT`; `S30-A1`-`S30-A4`; `S31-A1`-`S31-A3`; `S31-A5` | Covered for the current MVP materialization path. Late-committed actions and mid-run object edits are protected by completed-at/id cursors, tenant-scoped object change sequences, active-index-version pinning, run-start row capture, and append-only `object_record_versions` replay. Materialization output evidence now includes both the run watermark and a `materializes_to` lineage edge tied to the materialization run id and target dataset version id. Failed action runs stay available as operational evidence but are excluded from successful `ops.action_log` materialization rows, same-cursor reruns create fresh non-duplicated snapshots, metadata/outbox/lineage failure does not expose a downstream completion event before commit, and same-cursor retry after that failure commits exactly one output version. Failed `action_log` materialization still aborts before any output dataset version is visible with a `FAILED` run and no target version id. |
| CDC ordering and tombstones | T0-18, T0-19, T0-20 | `S40-A2`, `S40-A4`, `VERIFY-CDC-OBJECT-INDEXING`, `VERIFY-CDC-STALE-TOMBSTONE`, `VERIFY-CDC-PK-UPDATE-POLICY` | Covered for the current MVP CDC object-indexing path. Late stale update, late snapshot/read event, tombstone delete, late update-after-delete skip, query/materialization tombstone exclusion consistency, and primary-key update fail-closed policy are covered. |
| Shadow reindex and search projection | T0-21, T0-22, T0-23 | `S41-A5`, `S41-H1`, `S41-H2`, `S42-A3`, `VERIFY-ACTIVE-INDEX-POINTER`, `VERIFY-ACTIVE-INDEX-POINTER-FLAKE`, `VERIFY-OBJECT-QUERY-CURSOR-GUARDS`, `VERIFY-SEARCH-INDEXING`, `VERIFY-SHADOW-SEARCH-PROJECTION-EDGES` | Covered for the current T0 projection scope. Action-edit replay, active pointer safety, concurrent first-pointer stability, signed/query-shape cursor binding, active-index-version cursor fail-safe behavior, stale search document version no-op handling, and search-hit object-store refetch with projection/source version visibility are covered. Fallback degraded behavior remains a lower-tier backlog item, not a T0 source-of-truth blocker. |
| Security commit points | T0-24, T0-25, T0-26, T0-31, T0-34 | `S34-A3`, `S34-A4`, `S36A-A9`, `VERIFY-PRODUCTION-AUTH-GUARD`, `VERIFY-WORKER-TENANT-CONTEXT`, `VERIFY-LINK-TENANT-BOUNDARY`, `VERIFY-STATIC-OBJECT-SET-PERMISSION`, `VERIFY-STATIC` tenant/auth gates, `VERIFY-REST-WEBHOOK-TRICKY-EDGES`, `VERIFY-ACTION-AUDIT-MASKING`, `test_masked_property_cannot_filter_sort_search`, `test_rls_tenant_context_reset_between_pooled_connections` | Partial. Production header-trust fail-fast, worker tenant-context validation, link traversal tenant scoping, static object-set member permission/masking rechecks, REST private-network/redirect/DNS-rebinding SSRF guardrails, masked-property response/filter/sort/search denial, action audit masking, and pooled PostgreSQL RLS tenant-context reset are covered. Remaining future backlog: aggregate/export/materialized-dataset masking when those surfaces exist. |
| Backup, restore, and external replay | T0-09, T0-10, T0-27, T0-28 | Implementation-status marks real ERP writeback and Sprint 45 production backup/restore as future work | Future. Keep unchecked until real external connector and production restore evidence exists. |

## Sprint 05 - StorageAdapter and Manifest Commit Protocol

<a id="s05-a1"></a>
<a id="s05-a2"></a>
<a id="s05-a3"></a>
<a id="s05-a4"></a>
<a id="s05-a5"></a>

| Evidence id | Checkbox meaning | Git / test evidence | Current status |
|---|---|---|---|
| `S05-A1` | files are written to staging first and belong to a version only after manifest commit | `DatasetStorageAdapter.staging_file`; `LocalDatasetStorageAdapter.commit_staged_file`; `tests/contracts/test_dataset_storage_adapter_contract.py`; `VERIFY-DATASET-STORAGE-SPLIT-BRAIN` | Done for local/fake storage profiles |
| `S05-A2` | `manifest_uri` can restore the committed version file list | `DatasetTransactionService._load_manifest`; `DatasetRegistryService.inspect_dataset`; `tests/contracts/test_dataset_storage_adapter_contract.py`; `VERIFY-DATASET-STORAGE-SPLIT-BRAIN` | Done for local/fake storage profiles |
| `S05-A3` | retry/failure around commit does not create duplicate or orphan committed versions | `PR-2`; `S36A-A2`; `S36A-A3`; `test_dataset_commit_storage_success_db_failure_creates_orphan_cleanup_evidence`; `VERIFY-DATASET-STORAGE-SPLIT-BRAIN` | Done for local MVP metadata/storage split-brain |
| `S05-A4` | same `content_hash` reattach policy is explicit | `VERIFY-DATASET-SAME-CONTENT-REATTACH`; `tests/contracts/test_dataset_transaction_repository_contract.py::test_dataset_transaction_repository_contract_allows_same_content_hash_as_new_version` | Done. Same content is allowed only as a new committed transaction/version/file row; the existing committed version is not deduped, mutated, or reattached in place. |
| `S05-A5` | tests run without MinIO through a mocked/fake storage adapter | `FakeDatasetStorageAdapter`; `tests/contracts/test_dataset_storage_adapter_contract.py`; `tests/integration/test_scale_foundation.py`; `VERIFY-SCALE-ADAPTERS` | Done |

## Sprint 02A - Scale Foundation/Infra Swap Boundary

<a id="s02a-a1"></a>
<a id="s02a-a2"></a>
<a id="s02a-a3"></a>
<a id="s02a-a4"></a>
<a id="s02a-a5"></a>
<a id="s02a-a6"></a>
<a id="s02a-p1"></a>
<a id="s02a-p2"></a>
<a id="s02a-p3"></a>
<a id="s02a-p4"></a>
<a id="s02a-p5"></a>
<a id="s02a-o1"></a>
<a id="s02a-o2"></a>

### Completed Evidence Map

| Evidence id | Checkbox meaning | Git / test evidence | Current status |
|---|---|---|---|
| `S02A-A1` | Infra Swap Readiness Matrix exists with local/scale candidates and trace keys | Matrix in `foundry_lite_development_plan_ko_sprintified.md`; `GATE-FOUNDATION`; `VERIFY-INFRA-BOUNDARIES` | Done |
| `S02A-A2` | core use cases call ports/interfaces instead of concrete infra directly | `CORE-DI`; `GATE-FOUNDATION`; `VERIFY-INFRA-BOUNDARIES` | Done |
| `S02A-A3` | fake/local adapters share contract scenarios | `PR-5`; `VERIFY-CONTRACT-GATE`; `VERIFY-SCALE-ADAPTERS` | Done |
| `S02A-A4` | swapping adapter keeps public API and product response shape stable | `tests/integration/test_scale_foundation.py`; `VERIFY-SCALE-ADAPTERS` | Done |
| `S02A-A5` | adapter failure leaves traceable error/correlation evidence | `PR-2`; `PR-5`; `quality:adapter-error-trace`; `quality:failed-mutation-state`; `VERIFY-ADAPTER-FAILURE-TAXONOMY` | Done |
| `S02A-A6` | CI catches forbidden imports or boundary bypass | `GATE-FOUNDATION`; `VERIFY-INFRA-BOUNDARIES`; `VERIFY-CONTRACT-GATE` | Done |
| `S02A-P1` | Storage, metadata, dataset, transaction, version, runtime, compute, object, action, ontology ports are extracted | extraction commits through `OBJECT-READ-PORT`, `GATE-FOUNDATION`, and `PR-2`; `VERIFY-INFRA-BOUNDARIES` | Done |
| `S02A-P2` | Stream/Search/Workflow/Connector/Auth boundaries have local/fake contracts | `AUTH-PORT`; `PR-3`; `PR-5`; `VERIFY-SCALE-ADAPTERS` | Done |
| `S02A-P3` | application concrete infra import baseline is reduced to `0` | `GATE-FOUNDATION`; `VERIFY-INFRA-BOUNDARIES` | Done |
| `S02A-P4` | service graph is explicit and gated | `CORE-DI`; `VERIFY-INFRA-BOUNDARIES` | Done |
| `S02A-P5` | Postgres repository contract axis cannot be skipped in release/CI | `PR-2`; `GATE-FOUNDATION`; `pnpm ci:gate` policy | Done |
| `S02A-O1` | adapter failure contract standardizes typed error, retryability, timeout, idempotency, operator-facing message across all adapters | `libs/foundry_lite/application/ports/adapter_failure.py`; `scripts/quality/check_adapter_failure_taxonomy.py`; `VERIFY-ADAPTER-FAILURE-TAXONOMY`; `VERIFY-STATIC` | Done |
| `S02A-O2` | future scale adapter DTO/state/trace/failure contract is fully frozen | `AdapterFailureContract`; `tests/contracts/test_adapter_failure_contract.py`; `quality:adapter-failure-taxonomy`; `VERIFY-ADAPTER-FAILURE-TAXONOMY` | Done |

### Open Evidence Map

No Sprint 02A Scale Foundation evidence item remains open in the current checkout. Production Temporal/Kafka/OpenSearch/connector adapters are still later implementation work; this Sprint 02A item freezes the port, DTO, state, trace, and failure semantics they must preserve.

## Sprint 20 - Link Type and Link Traversal

<a id="s20-a1"></a>
<a id="s20-a2"></a>
<a id="s20-a3"></a>
<a id="s20-a4"></a>
<a id="s20-a5"></a>

| Evidence id | Checkbox meaning | Git / test evidence | Current status |
|---|---|---|---|
| `S20-A1` | `OrderCustomer` link is created from `clean.orders.customer_id` | `examples/supply-chain-demo/ontology/order-customer.yaml`; `tests/integration/test_closed_loop.py::test_supply_chain_closed_loop_updates_customer_risk_and_records_replay_state`; `tests/integration/test_ontology_action_security.py::test_ontology_import_indexes_order_customer_and_supports_object_query`; `tests/contracts/test_object_index_repository_contract.py::test_object_index_repository_contract_reads_link_types_and_upserts_links` | Done |
| `S20-A2` | an Order can traverse to its connected Customer through the public object link path | `foundry.objects.links("Order", "O-1001", "OrderCustomer")`; `flite object links Order O-1001 OrderCustomer`; `GET /api/objects/Order/O-1001/links/OrderCustomer`; `VERIFY-MVP-WEB-OBJECT-LINK` | Done |
| `S20-A3` | Customer-to-Order reverse traversal exists or a reverse link is explicitly modeled | `VERIFY-LINK-REVERSE-TRAVERSAL`; `tests/unit/test_helpers_and_query.py::test_link_reverse_traverses_customer_to_orders`; `ObjectLinksService` incoming active-link traversal through `ObjectReadRepository.active_links_to` | Done. The MVP keeps one durable forward `OrderCustomer` link row and supports Customer-to-Order traversal by reading that same active row from the target side, avoiding duplicate reverse write state. |
| `S20-A4` | missing target-object link policy is recorded as warning or error | `VERIFY-LINK-MISSING-TARGET-WARNING`; `tests/unit/test_helpers_and_query.py::test_link_reports_missing_target_object`; `ObjectLinksService._missing_target_payload` | Done. Missing target links return target object type/id with `targetMissing=true` and a `link_target_missing` warning instead of being silently hidden or mutating state during query. |
| `S20-A5` | link index run records `links_upserted` | `ObjectIndexRebuildCounts.links_upserted`; `IndexRunRecord.links_upserted`; `tests/contracts/test_object_index_repository_contract.py::test_object_index_repository_contract_reads_link_types_and_upserts_links` | Done |

## Sprint 21 - Object Sets

<a id="s21-a1"></a>
<a id="s21-a2"></a>
<a id="s21-a3"></a>
<a id="s21-a4"></a>
<a id="s21-a5"></a>
<a id="s21-a6"></a>

| Evidence id | Checkbox meaning | Git / test evidence | Current status |
|---|---|---|---|
| `S21-A1` | dynamic object set can save the current pending-orders query | `OBJECT-SETS`; `PR-4` for cursor paging hardening | Done |
| `S21-A2` | static set keeps ids from creation time | `OBJECT-SETS` | Done |
| `S21-A3` | dynamic set reflects latest object state at query time | `OBJECT-SETS`; `PR-4` | Done |
| `S21-A4` | unauthorized users cannot see another user's private set | `OBJECT-SETS`; `MVP-CORE` | Done |
| `S21-A5` | expired temporary sets are hidden or cleanup candidates | `OBJECT-SETS` | Done |
| `S21-A6` | dynamic set paging does not bypass Object Query page limit | `PR-4`; `S36A-A7`; `VERIFY-DYNAMIC-OBJECT-SET-PAGE-LIMIT`; `test_dynamic_object_set_cannot_bypass_page_limit` | Done |

## Sprint 22 - Dataset/Ontology/Object Minimal UI Vertical Slice

<a id="s22-a1"></a>
<a id="s22-a2"></a>
<a id="s22-a3"></a>
<a id="s22-a4"></a>
<a id="s22-a5"></a>

| Evidence id | Checkbox meaning | Git / test evidence | Current status |
|---|---|---|---|
| `S22-A1` | Web can confirm CSV upload or an existing dataset version | `GET /api/datasets/clean/orders/versions`; `apps/web/index.html` Dataset Versions panel; `VERIFY-S22-WEB-DATASET-OBJECT-REFRESH` | Done for existing committed dataset version and preview confirmation |
| `S22-A2` | Web can show ontology YAML validation errors | `POST /api/ontology/validate`; `apps/web/index.html` Ontology Validation panel; `VERIFY-S22-WEB-ONTOLOGY-VALIDATION` | Done for YAML validation error display without ontology activation |
| `S22-A3` | Web Object Explorer can load Order list and detail | `tests/e2e/foundry-lite.spec.ts`; `apps/web/index.html`; browser SDK `objects.Order.query`; browser SDK `objects.Order.get`; `VERIFY-S22-WEB-DATASET-OBJECT-REFRESH` | Done for the current MVP Object Explorer list/detail path |
| `S22-A4` | Object Explorer can show the Order to Customer link | `GET /api/objects/Order/O-1001/links/OrderCustomer`; `apps/web/index.html` Object Links panel; `VERIFY-MVP-WEB-OBJECT-LINK` | Done |
| `S22-A5` | refresh restores state from server | `tests/e2e/foundry-lite.spec.ts` reloads after `ApproveOrder` and expects server-restored `APPROVED` state plus fresh dataset/list/link panels; `VERIFY-S22-WEB-DATASET-OBJECT-REFRESH` | Done |

## Sprint 25 - Action Apply Transaction and Optimistic Concurrency

<a id="s25-a1"></a>
<a id="s25-a2"></a>
<a id="s25-a3"></a>
<a id="s25-a4"></a>
<a id="s25-a5"></a>

| Evidence id | Checkbox meaning | Git / test evidence | Current status |
|---|---|---|---|
| `S25-A1` | ApproveOrder changes `Order.status` to `APPROVED` | `tests/integration/test_ontology_action_security.py::test_action_apply_is_idempotent_and_rejects_stale_object_version`; `VERIFY-ACTION-COMMIT-ATOMICITY` retry proof | Done |
| `S25-A2` | `operatorNote` editable property is set from `params.reason` | `tests/integration/test_ontology_action_security.py::test_action_apply_is_idempotent_and_rejects_stale_object_version`; `VERIFY-ACTION-COMMIT-ATOMICITY` retry proof | Done |
| `S25-A3` | stale `expectedObjectVersion` does not silently overwrite a newer object | `tests/smoke/test_interfaces.py::test_action_expected_object_version_required`; `tests/integration/test_ontology_action_security.py::test_action_precondition_stale_read_conflicts_on_commit`; `tests/integration/test_ontology_action_security.py::test_action_apply_is_idempotent_and_rejects_stale_object_version`; `S26-A3` | Done for current API/Core/SDK/UI path; concurrent two-writer stress remains a useful future extension |
| `S25-A4` | failed action commit does not leave `object_edits` or partial action evidence | `tests/integration/test_ontology_action_security.py::test_action_commit_object_edit_audit_outbox_atomic`; `tests/contracts/test_action_repository_contract.py::test_sqlalchemy_action_run_insert_or_get_existing_rolls_back_with_outer_transaction`; `VERIFY-ACTION-COMMIT-ATOMICITY` | Done for internal DB commit path |
| `S25-A5` | succeeded action has action, object edit, audit, and outbox evidence tied by correlation/action id | `tests/integration/test_ontology_action_security.py::test_action_apply_is_idempotent_and_rejects_stale_object_version`; `scripts/quality/check_audit_count_runtime.py`; `scripts/quality/check_outbox_consistency.py`; `VERIFY-FULL-CI-GATE` | Done |

## Sprint 26 - Action Idempotency, Action Log, Audit

<a id="s26-a1"></a>
<a id="s26-a2"></a>
<a id="s26-a3"></a>
<a id="s26-a4"></a>
<a id="s26-a5"></a>

| Evidence id | Checkbox meaning | Git / test evidence | Current status |
|---|---|---|---|
| `S26-A1` | repeated or concurrent same-key action apply reuses one action_run and does not create extra object edits | `S36A-A1`; `VERIFY-ACTION-IDEMPOTENCY-FINGERPRINT`; `tests/unit/test_action_service_idempotency.py`; `tests/integration/test_ontology_action_security.py::test_action_apply_is_idempotent_and_rejects_stale_object_version` | Done |
| `S26-A2` | same Idempotency-Key with a different canonical request body conflicts instead of replaying the wrong action | `VERIFY-ACTION-IDEMPOTENCY-FINGERPRINT`; `ActionApplyCommand.request_fingerprint`; `ActionRunRecord.request_fingerprint`; `action.run.idempotency_conflict`; `check_idempotency_on_action.py` | Done |
| `S26-A3` | a different Idempotency-Key on the same object still obeys expectedObjectVersion conflict rules | `VERIFY-ACTION-IDEMPOTENCY-FINGERPRINT`; `test_action_precondition_stale_read_conflicts_on_commit`; `test_action_apply_is_idempotent_and_rejects_stale_object_version` | Done |
| `S26-A4` | action log shows actor, params subset, target, status, error, created/completed time | `MVP-CORE`; `S30-A2` for materialized action rows; no isolated action-log API evidence row yet | Partial |
| `S26-A5` | sensitive action parameters can be masked in audit evidence | `VERIFY-ACTION-AUDIT-MASKING`; `PolicyService.mask_sensitive_properties`; `test_action_audit_masks_sensitive_params` | Done for current object-property action audit refs; ontology-level parameter sensitivity metadata remains future refinement |

## Sprint 30 - Action Log Materialization

<a id="s30-a1"></a>
<a id="s30-a2"></a>
<a id="s30-a3"></a>
<a id="s30-a4"></a>
<a id="s30-a5"></a>

| Evidence id | Checkbox meaning | Git / test evidence | Current status |
|---|---|---|---|
| `S30-A1` | ApproveOrder followed by `action_log` materialization creates an `ops.action_log` dataset version | `tests/integration/test_closed_loop.py::test_action_materialization_writes_dataset_versions_and_manifest_rows`; `VERIFY-MATERIALIZATION-WATERMARKS` | Done |
| `S30-A2` | materialized action rows include action id, actor, target, status, parameters subset, and edit patch | `MaterializationService._action_log_row`; `tests/integration/test_closed_loop.py::test_action_materialization_writes_dataset_versions_and_manifest_rows`; `VERIFY-MATERIALIZATION-WATERMARKS` | Done |
| `S30-A3` | `action_log` source cursor is based on committed action time plus action id, not creation time alone | `MaterializationService._materialization_watermark`; `test_materialization_late_commit_action_not_skipped`; `VERIFY-MATERIALIZATION-WATERMARKS` | Done |
| `S30-A4` | failed materialization aborts the output dataset transaction | `MaterializationService._abort_materialization_run`; `tests/integration/test_closed_loop.py::test_materialization_cursor_not_advanced_before_dataset_commit`; `VERIFY-MATERIALIZATION-COMMIT-FAILURE` | Done |
| `S30-A5` | Dataset UI can preview `ops.action_log` | CLI/smoke coverage exists for materialize command; UI-specific proof is not yet isolated | Partial |

## Sprint 31 - Object Snapshot Materialization

<a id="s31-a1"></a>
<a id="s31-a2"></a>
<a id="s31-a3"></a>
<a id="s31-a4"></a>
<a id="s31-a5"></a>

| Evidence id | Checkbox meaning | Git / test evidence | Current status |
|---|---|---|---|
| `S31-A1` | Order current view is emitted as `ops.order_current` | `tests/integration/test_closed_loop.py::test_action_materialization_writes_dataset_versions_and_manifest_rows`; `VERIFY-MATERIALIZATION-WATERMARKS` | Done |
| `S31-A2` | ApproveOrder is reflected in the next object snapshot | `test_object_snapshot_mid_run_action_not_mixed`; `VERIFY-MATERIALIZATION-WATERMARKS` | Done |
| `S31-A3` | run metadata stores the object store watermark | `object_change_sequence_lte`, `active_index_version`; `test_latest_object_record_watermark_returns_max_change_sequence`; `VERIFY-MATERIALIZATION-WATERMARKS` | Done |
| `S31-A4` | the exact same old watermark can be replayed later with the same row count/hash | `tests/integration/test_closed_loop.py::test_object_snapshot_fixed_watermark_hash_reproducible`; `tests/contracts/test_materialization_repository_contract.py::test_object_records_at_watermark_returns_latest_version_per_object`; `VERIFY-MATERIALIZATION-WATERMARKS` | Done |
| `S31-A5` | a mid-run action does not produce a mixed logical snapshot | `test_object_snapshot_mid_run_action_not_mixed`; `VERIFY-MATERIALIZATION-WATERMARKS` | Done |

## Sprint 33 - Operations Surface

<a id="s33-a1"></a>
<a id="s33-a2"></a>
<a id="s33-a3"></a>
<a id="s33-a4"></a>
<a id="s33-a5"></a>

| Evidence id | Checkbox meaning | Git / test evidence | Current status |
|---|---|---|---|
| `S33-A1` | failed transform can be found and retried from UI/API/CLI | `MVP-CORE`; `PR-2` | Done |
| `S33-A2` | DLQ event can be reprocessed into successful materialization | `MVP-CORE`; `PR-2` | Done |
| `S33-A3` | object detail can navigate to source evidence/run chain | `MVP-CORE`; `PR-2` | Done |
| `S33-A4` | run list filters by status/type/date | `MVP-CORE`; `PR-2`; `S36A-A8` | Done |
| `S33-A5` | operator can investigate basic failures without direct DB access | `MVP-CORE`; `PR-2` | Done |

## Sprint 34 - Security/Governance

<a id="s34-a1"></a>
<a id="s34-a2"></a>
<a id="s34-a3"></a>
<a id="s34-a4"></a>
<a id="s34-a5"></a>

| Evidence id | Checkbox meaning | Git / test evidence | Current status |
|---|---|---|---|
| `S34-A1` | viewer can read datasets but cannot activate ontology | `MVP-CORE`; `PR-2` | Done |
| `S34-A2` | only ops_manager/admin can run ApproveOrder | `MVP-CORE`; `PR-2` | Done |
| `S34-A3` | non-finance/non-admin users receive masked sensitive Order properties and cannot use them in filter/sort/search query shapes | `MVP-CORE`; `PR-2`; `PolicyService.masked_property_names`; `tests/integration/test_search_indexing.py::test_masked_property_cannot_filter_sort_search`; `tests/integration/test_ontology_action_security.py::test_viewer_sees_masked_margin_and_cannot_approve_order` | Done |
| `S34-A4` | API and PostgreSQL RLS hide other-tenant dataset/object rows, including pooled connection reuse | `MVP-CORE`; `PR-2`; `tests/contracts/test_postgres_rls_contract.py::test_postgres_rls_hides_dataset_and_object_rows_between_tenants`; `tests/contracts/test_postgres_rls_contract.py::test_rls_tenant_context_reset_between_pooled_connections` | Done |
| `S34-A5` | permission denied writes audit evidence with `decision=deny` | `MVP-CORE`; `PR-2` | Done |

## Sprint 35 - Generated TypeScript SDK

<a id="s35-a1"></a>
<a id="s35-a2"></a>
<a id="s35-a3"></a>
<a id="s35-a4"></a>
<a id="s35-a5"></a>

| Evidence id | Checkbox meaning | Git / test evidence | Current status |
|---|---|---|---|
| `S35-A1` | generated client returns typed Order get result | `MVP-CORE`; `PR-2` | Done |
| `S35-A2` | generated ApproveOrder action apply has typed params | `MVP-CORE`; `PR-2` | Done |
| `S35-A3` | SDK generation detects ontology apiName drift | `MVP-CORE`; `PR-2`; `quality:sdk-generated` | Done |
| `S35-A4` | SDK smoke test executes an end-to-end generated-client action | `MVP-CORE`; `PR-2` | Done |
| `S35-A5` | Web Object Explorer uses browser SDK for at least one surface | `MVP-CORE`; `PR-2` | Done |

## Sprint 36 - MVP Release Gate Progress

<a id="s36-p1"></a>
<a id="s36-p2"></a>
<a id="s36-p3"></a>
<a id="s36-p4"></a>
<a id="s36-p5"></a>
<a id="s36-p6"></a>

| Evidence id | Checkbox meaning | Git / test evidence | Current status |
|---|---|---|---|
| `S36-P1` | supply-chain demo uses isolated fresh `.foundry-lite-demo/` by default | `MVP-HONESTY`; `GATE-FOUNDATION` | Done |
| `S36-P2` | CLI smoke regression parses two consecutive demo JSON outputs | `MVP-HONESTY`; `GATE-FOUNDATION` | Done |
| `S36-P3` | `pnpm ci:gate` rejects non-JSON demo smoke artifacts | `MVP-HONESTY`; `GATE-FOUNDATION` | Done |
| `S36-P4` | MVP data-correctness gate checks row count, uniqueness, reindex hash, action idempotency | `MVP-HONESTY`; `GATE-FOUNDATION` | Done |
| `S36-P5` | MVP performance smoke records fast and release-size profiles separately | `MVP-HONESTY`; `GATE-FOUNDATION` | Done |
| `S36-P6` | Testcontainers closed loop validates connector snapshot through materialization on PostgreSQL-backed repositories | `GATE-FOUNDATION`; `PR-2` | Done |

## Sprint 36A - Operational Hardening

<a id="s36a-a1"></a>
<a id="s36a-a2"></a>
<a id="s36a-a3"></a>
<a id="s36a-a4"></a>
<a id="s36a-a5"></a>
<a id="s36a-a6"></a>
<a id="s36a-a7"></a>
<a id="s36a-a8"></a>
<a id="s36a-a9"></a>
<a id="s36a-a10"></a>
<a id="s36a-a11"></a>

| Evidence id | Checkbox meaning | Git / test evidence | Current status |
|---|---|---|---|
| `S36A-A1` | concurrent same-key action idempotency replays one action run | `PR-2` | Done |
| `S36A-A2` | dataset version commit avoids duplicate/out-of-order versions | `PR-2` | Done |
| `S36A-A3` | promoted/orphan file cleanup is traceable after commit failure | `PR-2` | Done |
| `S36A-A4` | Object Query keyset cursor uses sort key + `object_id` | `PR-4`; `VERIFY-OBJECT-QUERY-CURSOR-GUARDS`; `test_object_query_db_backed_keyset_no_memory_slice` | Done |
| `S36A-A5` | Object Query rejects raw/tampered cursor payloads | `PR-4`; `VERIFY-OBJECT-QUERY-CURSOR-GUARDS`; `VERIFY-SHADOW-SEARCH-PROJECTION-EDGES`; `test_object_query_cursor_signed_tamper_proof_query_shape_bound` | Done for tamper, query-shape binding, and active-index-version cursor binding across shadow index switches |
| `S36A-A6` | Object Query validates missing properties and preserves numeric sort/filter parity across fake/SQLite/Postgres | `PR-4`; `VERIFY-OBJECT-QUERY-NUMERIC-CASTS`; `test_object_query_numeric_property_casts_for_sort_and_filter` | Done |
| `S36A-A7` | Dynamic Object Set reads membership through Object Query paging | `PR-2`; `PR-4` | Done |
| `S36A-A8` | Operations runs API/CLI/UI uses cursor paging and bounded response size | `PR-2` | Done |
| `S36A-A9` | production auth profile fails fast on header-trust/demo auth | `PR-2`; `AUTH-PORT`; `VERIFY-PRODUCTION-AUTH-GUARD`; `test_production_refuses_dev_header_trust_auth` | Done |
| `S36A-A10` | package/browser SDK outputs expose same method surface | `PR-2` | Done |
| `S36A-A11` | same Idempotency-Key cannot be reused with a different request body | `S26-A2`; `VERIFY-ACTION-IDEMPOTENCY-FINGERPRINT` | Done |

## MVP Core Completion Gate Evidence Map

<a id="mvp-core-raw-dataset"></a>
<a id="mvp-core-scale-foundation"></a>
<a id="mvp-core-transform"></a>
<a id="mvp-core-ontology"></a>
<a id="mvp-core-object-index"></a>
<a id="mvp-core-object-link-ui"></a>
<a id="mvp-core-action"></a>
<a id="mvp-core-mutation-ledger"></a>
<a id="mvp-core-materialization"></a>
<a id="mvp-core-downstream-transform"></a>
<a id="mvp-core-operations-replay"></a>
<a id="mvp-core-quality-gate"></a>
<a id="mvp-core-regression-trace"></a>
<a id="mvp-core-coverage"></a>
<a id="mvp-core-integration-smoke"></a>

| Gate item | Evidence accepted today | Current status |
|---|---|---|
| CSV or PostgreSQL snapshot commits raw dataset | `S36-P1`, `S36-P6`, `VERIFY-FULL-CI-GATE`, `test_mvp_testcontainers_closed_loop.py` | Done for current MVP local/PostgreSQL closed-loop path |
| Scale Foundation boundary keeps infra behind ports/adapters | `S02A-A1`-`S02A-O2`, `VERIFY-INFRA-BOUNDARIES`, `VERIFY-STATIC` | Done for Sprint 02A boundary scope |
| SQL/DuckDB transform creates clean dataset | `VERIFY-TRANSFORM-INPUT-PINNING`, `VERIFY-FULL-CI-GATE`, `test_supply_chain_closed_loop_updates_customer_risk_and_records_replay_state` | Done |
| Ontology draft validates and activates | `S20-A1`, `S22-A4`, `test_ontology_import_indexes_order_customer_and_supports_object_query` | Done |
| clean dataset rows index into Order/Customer objects | `S20-A1`, `S36-P6`, `VERIFY-FULL-CI-GATE`, `test_ontology_import_indexes_order_customer_and_supports_object_query` | Done |
| Web Object Explorer loads Order and shows Order to Customer link | `S22-A3`, `S22-A4`, `VERIFY-MVP-WEB-OBJECT-LINK` | Done |
| ApproveOrder action executes | `S25-A1`, `S35-A4`, `VERIFY-ACTION-IDEMPOTENCY-FINGERPRINT`, `VERIFY-MVP-WEB-OBJECT-LINK` | Done |
| object records, edits, action runs, audit, and outbox stay consistent | `S25-A4`, `S25-A5`, `VERIFY-ACTION-COMMIT-ATOMICITY`, `VERIFY-FULL-CI-GATE` audit/outbox gates | Done for internal DB mutation path; external ERP writeback remains future scope |
| action_log and object_snapshot materialize as datasets | `S30-A1`-`S30-A4`, `S31-A1`-`S31-A5`, `VERIFY-MATERIALIZATION-WATERMARKS` | Done |
| downstream transform reads materialized dataset and updates Customer object | `VERIFY-MATERIALIZED-TRANSFORM-PINNING`, `test_action_materialization_writes_dataset_versions_and_manifest_rows` | Done |
| lineage/audit/operations surfaces trace the path and replay MVP failures | `S33-A1`-`S33-A5`, `VERIFY-FULL-CI-GATE`, Playwright source run/retry controls | Done for MVP failed transform/index/DLQ paths; broader sync/action/materialization replay remains future Operations scope |
| Python backend quality gate passes | `VERIFY-STATIC`, `VERIFY-FULL-CI-GATE`, GitHub PR #22 required checks | Done |
| root-cause regression shields and traceable error state exist | `VERIFY-TRICKY-FAILURE-FOCUSED`, `VERIFY-ACTION-COMMIT-ATOMICITY`, `VERIFY-ACTION-IDEMPOTENCY-FINGERPRINT`, G21/G22 gates in `VERIFY-STATIC` | Done |
| line, branch, and public callable/function coverage are at least 95% | `VERIFY-FULL-CI-GATE` total coverage `96.13%`, layer coverage minimum `97.65%`, public callable smoke coverage `100.00%` | Done |
| required integration tests and smoke tests run and pass | `VERIFY-FULL-CI-GATE` full pytest `805 passed`, flaky detector `3` repeated random/parallel runs with `805 passed`, Playwright passed | Done |

## Sprint 37 - REST Pull Connector and Webhook Listener

<a id="s37-a1"></a>
<a id="s37-a2"></a>
<a id="s37-a3"></a>
<a id="s37-a4"></a>
<a id="s37-a5"></a>
<a id="s37-a6"></a>
<a id="s37-a7"></a>
<a id="s37-a8"></a>
<a id="s37-a9"></a>
<a id="s37-a10"></a>

| Evidence id | Checkbox meaning | Git / test evidence | Current status |
|---|---|---|---|
| `S37-A1` | mock REST API pulls orders into raw dataset | `PR-3` | Done |
| `S37-A2` | REST adapter returns `nextCursor` and sends a supplied cursor on the next request | `PR-3`; `tests/contracts/test_rest_connector_adapter_contract.py` | Done at adapter level |
| `S37-A3` | webhook event appends to raw dataset transaction | `PR-3` | Done |
| `S37-A4` | invalid webhook signature is rejected and audit deny remains | `PR-3` | Done |
| `S37-A5` | REST rate-limit failure and webhook signature deny appear in Operations surface | `PR-3` | Done |
| `S37-A6` | REST source URL validation blocks localhost/private/link-local/internal metadata addresses by default, including redirect targets and DNS rebinding during redirect chains | `VERIFY-REST-SSRF`; `VERIFY-REST-WEBHOOK-TRICKY-EDGES`; `libs/foundry_lite/infrastructure/adapters/rest_connector.py`; `tests/contracts/test_rest_connector_adapter_contract.py` | Done for current private-network coverage, including encoded, decimal, and octal redirect-host variants |
| `S37-A7` | REST pagination cursor is persisted only on committed dataset transactions and used as the next sync default | `VERIFY-REST-WEBHOOK-OPS`; `VERIFY-STREAM-REST-CURSOR-COMMIT-POINTS`; `tests/integration/test_rest_connector_ingest.py`; `libs/foundry_lite/application/services/dataset/connector_snapshot_ingest.py` | Done |
| `S37-A8` | duplicate webhook event delivery replays the existing committed version instead of creating another event row/version, including volatile timestamp-only payload changes | `VERIFY-REST-WEBHOOK-OPS`; `VERIFY-REST-WEBHOOK-TRICKY-EDGES`; `tests/smoke/test_interfaces.py`; `tests/contracts/test_dataset_transaction_repository_contract.py` | Done; timestamp-bound signature replay outside the accepted clock-skew window is rejected before append |
| `S37-A9` | REST page-number pagination is rejected as non-replayable rather than committed as if it were cursor-safe | `VERIFY-REST-WEBHOOK-TRICKY-EDGES`; `RestPaginationConfig.strategy`; `test_rest_mutable_pagination_detected_or_marked_non_replayable` | Done |
| `S37-A10` | webhook API does not return 2xx before append persistence succeeds | `VERIFY-REST-WEBHOOK-TRICKY-EDGES`; `test_webhook_ack_not_sent_before_append_commit_or_has_replay_strategy` | Done for synchronous append path |

## Sprint 38 - Redpanda/Kafka Stream Archive Writer

<a id="s38-a1"></a>
<a id="s38-a2"></a>
<a id="s38-a3"></a>
<a id="s38-a4"></a>
<a id="s38-a5"></a>
<a id="s38-a6"></a>

| Evidence id | Checkbox meaning | Git / test evidence | Current status |
|---|---|---|---|
| `S38-A1` | local/fake Kafka-compatible `StreamAdapter` events append to raw stream archive dataset | `PR-5`; `VERIFY-SCALE-ADAPTERS` | Done |
| `S38-A2` | production Kafka-compatible broker topic event appends to raw stream archive dataset | `PR-8`; `VERIFY-KAFKA-LIVE-BROKER`; `tests/integration/test_kafka_live_broker_stream_archive.py`; `libs/foundry_lite/infrastructure/adapters/kafka_stream.py`; `apps/worker/foundry_lite_worker/stream_archive.py` | Done |
| `S38-A3` | worker restart resumes after last committed offset | `PR-5`; `VERIFY-SCALE-ADAPTERS`; `VERIFY-STREAM-REST-CURSOR-COMMIT-POINTS`; `tests/integration/test_stream_archive_ingest.py` | Done for local/fake checkpoint path; live rebalance/commit-unknown proof remains future hardening |
| `S38-A4` | duplicate processing can be identified by event id or topic/partition/offset | `PR-5`; `VERIFY-SCALE-ADAPTERS` | Done |
| `S38-A5` | stream archive dataset preview works | `PR-5`; `VERIFY-SCALE-ADAPTERS` | Done |
| `S38-A6` | lag metric and stream writer failure are visible to Operations | `PR-5`; `VERIFY-SCALE-ADAPTERS` | Done |

## Sprint 39 - Debezium PostgreSQL CDC Connector

<a id="s39-a1"></a>
<a id="s39-a2"></a>
<a id="s39-a3"></a>
<a id="s39-a4"></a>
<a id="s39-a5"></a>

| Evidence id | Checkbox meaning | Git / test evidence | Current status |
|---|---|---|---|
| `S39-A1` | mock ERP orders row insert/update/delete appears on a CDC topic | `PR-11`; `VERIFY-DEBEZIUM-LIVE-CDC`; `tests/integration/test_debezium_live_cdc.py`; `infra/docker-compose.dev.yml` `cdc` profile | Done |
| `S39-A2` | CDC event appends to raw changelog dataset | `PR-10`; `VERIFY-CDC-STREAM-ARCHIVE`; `tests/integration/test_cdc_stream_archive.py` | Done for Debezium-shaped stream events |
| `S39-A3` | primary key and ordering metadata are visible in preview | `PR-10`; `VERIFY-CDC-STREAM-ARCHIVE`; `tests/integration/test_cdc_stream_archive.py` | Done |
| `S39-A4` | delete event is standardized as `after=null` or tombstone policy | `PR-10`; `VERIFY-CDC-STREAM-ARCHIVE`; `tests/contracts/test_debezium_cdc_adapter_contract.py` | Done for `after=null` delete envelopes |
| `S39-A5` | CDC connector failure/lag is visible in Operations | `PR-11`; `VERIFY-CDC-STREAM-ARCHIVE`; `test_cdc_stream_archive_read_failure_is_visible_in_operations`; `test_cdc_stream_archive_updates_unread_lag_metric` | Done |

## Sprint 40 - CDC Object Indexing and Delete/Tombstone

<a id="s40-a1"></a>
<a id="s40-a2"></a>
<a id="s40-a3"></a>
<a id="s40-a4"></a>
<a id="s40-a5"></a>

| Evidence id | Checkbox meaning | Git / test evidence | Current status |
|---|---|---|---|
| `S40-A1` | ERP DB row update changes the Order object without batch rebuild | `PR-12`; `VERIFY-CDC-OBJECT-INDEXING`; `tests/integration/test_cdc_object_indexing.py`; `tests/unit/test_cdc_indexing.py` | Done |
| `S40-A2` | delete event marks the object as deleted/tombstoned | `PR-12`; `VERIFY-CDC-OBJECT-INDEXING`; `deleted=true`; `deletionReason=source_deleted` | Done |
| `S40-A3` | replaying the same CDC event is idempotent | `PR-12`; `VERIFY-CDC-OBJECT-INDEXING`; duplicate event returns `events_skipped=1` | Done |
| `S40-A4` | stale CDC event does not overwrite current object state | `PR-12`; `VERIFY-CDC-OBJECT-INDEXING`; lower `ordering.lsn` event returns `events_skipped=1` and keeps `status=APPROVED` | Done |
| `S40-A5` | CDC update emits object.changed/materialization trigger evidence | `PR-12`; `VERIFY-CDC-OBJECT-INDEXING`; `object.changed` outbox count changes only for applied update/delete events | Done |

## Sprint 41 - Shadow Reindex and Hash Validation

<a id="s41-a1"></a>
<a id="s41-a2"></a>
<a id="s41-a3"></a>
<a id="s41-a4"></a>
<a id="s41-a5"></a>
<a id="s41-h1"></a>
<a id="s41-h2"></a>

| Evidence id | Checkbox meaning | Git / test evidence | Current status |
|---|---|---|---|
| `S41-A1` | live Object Query keeps serving the active index while shadow rows are present | `PR-14`; `VERIFY-SHADOW-REINDEX`; `tests/contracts/test_object_read_repository_contract.py::test_object_read_repository_contract_ignores_shadow_index_rows` | Done |
| `S41-A2` | validation success switches the active index version to the shadow namespace | `PR-14`; `VERIFY-SHADOW-REINDEX`; `tests/integration/test_shadow_reindex.py::test_shadow_reindex_replays_action_edits` | Done |
| `S41-A3` | validation failure leaves the previous active index serving | `PR-14`; `VERIFY-SHADOW-REINDEX`; `tests/integration/test_shadow_reindex.py::test_shadow_reindex_validation_failure_keeps_existing_active_index` | Done |
| `S41-A4` | object count/hash validation matches baseline before switch | `PR-14`; `VERIFY-SHADOW-REINDEX`; `ObjectIndexValidationResult`; `object_index_stats` | Done |
| `S41-A5` | action edits replay into the shadow rebuild so current view is preserved | `PR-14`; `VERIFY-SHADOW-REINDEX`; integration assertion keeps `status=APPROVED`, `operatorNote=Shadow validation proof`, and object version after switch | Done |
| `S41-H1` | zero-row object types keep an explicit active index version for future CDC/search workers | [PR #18](https://github.com/ludia8888/foundry-lite/pull/18); `VERIFY-ACTIVE-INDEX-POINTER`; `object_index_versions`; `test_empty_shadow_reindex_persists_active_pointer_for_next_cdc_insert` | Done |
| `S41-H2` | concurrent shadow promotions for the same object type cannot silently overwrite a previously validated active version | [PR #19](https://github.com/ludia8888/foundry-lite/pull/19); `VERIFY-ACTIVE-INDEX-POINTER`; `expected_previous_index_version`; `test_object_index_repository_contract_concurrent_first_pointer_switch_has_one_winner` | Done |

## Sprint 42 - OpenSearch Adapter for Search-heavy Object Types

<a id="s42-a1"></a>
<a id="s42-a2"></a>
<a id="s42-a3"></a>
<a id="s42-a4"></a>
<a id="s42-a5"></a>

| Evidence id | Checkbox meaning | Git / test evidence | Current status |
|---|---|---|---|
| `S42-A1` | searchable property full-text query executes through the OpenSearch-compatible search path | `PR-16`; `VERIFY-SEARCH-INDEXING`; `OpenSearchAdapter`; `ObjectQueryRequest.search`; `ObjectQueryService` search planner branch | Done in merged PR #16 |
| `S42-A2` | object edit after `object.changed` consumption updates the search index | `PR-16`; `VERIFY-SEARCH-INDEXING`; `FoundryLiteCore.index_search_object_changed`; `flite index consume-search-change`; `tests/integration/test_search_indexing.py` | Done in merged PR #16 |
| `S42-A3` | search index failure does not block get by id or basic Postgres filter | `PR-16`; `VERIFY-SEARCH-INDEXING`; `FailingSearchAdapter` integration proof | Done in merged PR #16 |
| `S42-A4` | search index rebuild result count matches active `object_records` | `PR-16`; `VERIFY-SEARCH-INDEXING`; `FoundryLiteCore.index_search_rebuild`; `flite index rebuild-search` | Done in merged PR #16 |
| `S42-A5` | search documents that exist only in OpenSearch are detected as orphan drift | `PR-16`; `VERIFY-SEARCH-INDEXING`; `orphanDocumentIds` assertion | Done in merged PR #16 |

## Open / Not Yet Merged Scope

These items intentionally remain unchecked until a later PR creates code and gate evidence.

| Scope | Current tracking note |
|---|---|
| OpenSearch live cluster deployment/managed operations, Iceberg, Spark, Kubernetes production adapters | Sprint 42 adds the OpenSearch-compatible adapter and projection proof; live cluster deployment and Sprint 43-45 production-scale adapters remain future work. |
