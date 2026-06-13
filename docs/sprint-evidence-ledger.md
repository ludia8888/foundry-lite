# Sprint Evidence Ledger

**Last updated:** 2026-06-14 KST  
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
| `GATE-FOUNDATION` | 품질 게이트, release gate, infra boundary hardening | [e1fc49e81c2262c69321eaf6b991f425969b6e35](https://github.com/ludia8888/foundry-lite/commit/e1fc49e81c2262c69321eaf6b991f425969b6e35) | main history |
| `AUTH-PORT` | `AuthProvider` port, HeaderTrust/Demo local adapters | [b14c70f843dc0faedbde72f5639a28f15389de09](https://github.com/ludia8888/foundry-lite/commit/b14c70f843dc0faedbde72f5639a28f15389de09) | main history |
| `OBJECT-READ-PORT` | `ObjectReadRepository` boundary extraction | [87a08d7b79795354d3d8782981746978e6e1d07c](https://github.com/ludia8888/foundry-lite/commit/87a08d7b79795354d3d8782981746978e6e1d07c) | main history |
| `CORE-DI` | explicit service graph, dependency/collaborator declarations, no hidden facade registry | [44b9d926966d2656dcd650cda3d15b14d5f19137](https://github.com/ludia8888/foundry-lite/commit/44b9d926966d2656dcd650cda3d15b14d5f19137), [522ef32be290035521e8915e30ef2978e98069ad](https://github.com/ludia8888/foundry-lite/commit/522ef32be290035521e8915e30ef2978e98069ad), [edaad8cd1ad921c565d315f2e74dbc9cf0b9c91e](https://github.com/ludia8888/foundry-lite/commit/edaad8cd1ad921c565d315f2e74dbc9cf0b9c91e) | main history |
| `OBJECT-SETS` | Sprint 21 Object Sets implementation and tests | [ff05ab65ff91a56b7cbe6fcf84dd0d2a44777a3f](https://github.com/ludia8888/foundry-lite/commit/ff05ab65ff91a56b7cbe6fcf84dd0d2a44777a3f) | main history |
| `MVP-CORE` | Core MVP, operations/security/SDK/release-gate foundation | [0249af83f7cc7420def0892cb0d73e8faa342612](https://github.com/ludia8888/foundry-lite/commit/0249af83f7cc7420def0892cb0d73e8faa342612) | main history |
| `MVP-HONESTY` | MVP honesty and quality-gate hardening before PR workflow | [c96591dd6c056fac524bec51565b811d1a77614f](https://github.com/ludia8888/foundry-lite/commit/c96591dd6c056fac524bec51565b811d1a77614f), [d24ab67a786456cbb7fb97e0409ac66b865b0414](https://github.com/ludia8888/foundry-lite/commit/d24ab67a786456cbb7fb97e0409ac66b865b0414) | main history |

## Current Verification Evidence

2026-06-14 KST에 현재 checkout에서 다시 확인한 명령이다.

| Evidence id | Command | Result |
|---|---|---|
| `VERIFY-DOC-DRIFT` | `pnpm --silent quality:doc-drift` | PASS. current-state docs reference existing code paths and symbols. |
| `VERIFY-INFRA-BOUNDARIES` | `pnpm --silent quality:infra-boundaries` | PASS. domain concrete infra import `0/0`, application concrete infra import `0/0`, service dependency declarations OK, service call graph cycles `0`, max depth `7/7`, max fan-out `8/10`. |
| `VERIFY-STATIC` | `pnpm --silent quality:static` | PASS. Ruff, format check, mypy, pyright, architecture, infra boundaries, module size, function length, boolean naming, typed boundary, router purity, query side-effect, repository boundaries, tenant write, contract-test-per-port, strategy/spec tests, integration markers, regression/root-cause local checks, docs, SDK generation, schema revision, audit/outbox/idempotency/request-id/log/metrics/adapter-failure-taxonomy/no-bypass/no-sleep/coverage/private facade gates all passed. |
| `VERIFY-FULL-CI-GATE` | `env DOCKER_HOST=unix:///Users/isihyeon/.colima/default/docker.sock TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock pnpm --silent ci:gate` | PASS. Testcontainers preflight reached Colima, full pytest `673 passed`, coverage `95.69%`, flaky detector stable, layer/public callable coverage passed, OpenLineage/audit/outbox/MVP correctness/performance/trace/adapter-error/failed-mutation/runtime diagnostics/Playwright gates passed. |
| `VERIFY-CONTRACT-GATE` | `pnpm --silent quality:contract-tests` | PASS. every application port has a contract suite. |
| `VERIFY-SCALE-ADAPTERS` | `uv run pytest tests/contracts/test_auth_provider_contract.py tests/contracts/test_connector_adapter_contract.py tests/contracts/test_search_adapter_contract.py tests/contracts/test_stream_adapter_contract.py tests/contracts/test_workflow_adapter_contract.py tests/integration/test_scale_foundation.py tests/integration/test_stream_archive_ingest.py -q` | PASS. `29 passed in 0.38s`. |
| `VERIFY-TESTCONTAINERS-PREFLIGHT` | `pnpm --silent quality:testcontainers-preflight` | FAIL FAST as designed in a Docker-unreachable shell. The message tells the operator to set `DOCKER_HOST=unix:///Users/isihyeon/.colima/default/docker.sock` and `TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock` before rerunning `pnpm --silent ci:gate`. Unit proof: `uv run pytest tests/unit/test_quality_testcontainers_preflight.py -q`, `5 passed`. This preflight now protects both PostgreSQL and Kafka Testcontainers evidence. |
| `VERIFY-REST-SSRF` | `uv run pytest tests/integration/test_rest_connector_ingest.py tests/contracts/test_rest_connector_adapter_contract.py -q` | PASS outside the sandbox because the mock REST server needs local TCP bind permission. `24 passed in 7.35s`. |
| `VERIFY-REST-WEBHOOK-OPS` | `uv run pytest tests/integration/test_rest_connector_ingest.py tests/contracts/test_rest_connector_adapter_contract.py -q`; `uv run pytest tests/smoke/test_interfaces.py::test_api_webhook_ingest_verifies_signature_and_appends_dataset tests/unit/test_quality_testcontainers_preflight.py -q`; `FOUNDRY_LITE_SKIP_POSTGRES_CONTRACTS=1 uv run pytest tests/contracts/test_dataset_transaction_repository_contract.py::test_dataset_transaction_repository_contract_finds_committed_webhook_event -q` | PASS. REST cursor/adapter suite: `24 passed in 7.35s` outside the sandbox for local TCP bind permission. Webhook API duplicate replay plus preflight unit proof: `6 passed`. Webhook transaction lookup contract: `2 passed, 1 skipped` with local-only Postgres skip. |
| `VERIFY-ADAPTER-FAILURE-TAXONOMY` | `pnpm --silent quality:adapter-failure-taxonomy`; `uv run pytest tests/contracts/test_adapter_failure_contract.py tests/unit/test_quality_adapter_failure_taxonomy.py tests/contracts/test_rest_connector_adapter_contract.py tests/integration/test_rest_connector_ingest.py::test_rest_connector_rate_limit_failure_is_visible_in_operations -q` | PASS. `17` concrete adapter profiles expose `AdapterFailureContract`; targeted adapter taxonomy/REST failure payload tests cover local/fake, REST, auth, Kafka stream, and Debezium CDC stream failure contracts. |
| `VERIFY-KAFKA-STREAM-WORKER` | `uv run pytest tests/contracts/test_kafka_stream_adapter_contract.py tests/contracts/test_adapter_failure_contract.py tests/unit/test_quality_adapter_failure_taxonomy.py -q`; `pnpm --silent quality:adapter-failure-taxonomy` | PASS. Production-compatible `KafkaStreamAdapter` parses broker-shaped messages, the worker archives one micro-batch through `FoundryLiteCore.archive_stream_events`, and the adapter taxonomy gate covers every current concrete adapter profile. This focused contract proof is complemented by `VERIFY-KAFKA-LIVE-BROKER`. |
| `VERIFY-KAFKA-LIVE-BROKER` | `DOCKER_HOST=unix:///Users/isihyeon/.colima/default/docker.sock TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock uv run pytest tests/integration/test_kafka_live_broker_stream_archive.py -q` | PASS. `1 passed in 14.93s`. `KafkaContainer` boots a real Kafka-compatible broker, `KafkaStreamAdapter.publish_event` writes a shipment event to a live topic, and `foundry_lite_worker.stream_archive.run_stream_archive_once` reads the broker topic through the same application boundary and commits `raw.shipment_events` with event id `<topic>:0:0`. |
| `VERIFY-CDC-STREAM-ARCHIVE` | `pnpm --silent quality:cdc-stream-archive`; `pnpm --silent quality:adapter-failure-taxonomy` | PASS. `13 passed in 0.32s`; adapter taxonomy gate passed with `17` concrete adapter profiles. `DebeziumPostgresStreamAdapter` normalizes Debezium-shaped insert/update/delete payloads into the standard CDC envelope, rejects malformed envelopes as adapter validation failures, reports publish/read validation failures under the correct adapter operation, and `tests/integration/test_cdc_stream_archive.py` proves `StreamArchiveConfig(schema_strategy="cdc_envelope_json")` commits `raw_cdc.erp_orders` rows with top-level `op`, `pk_json`, `before_json`, `after_json`, and `ordering_json` preview fields. CDC stream lag updates `foundry_lite_stream_archive_lag_events`, CDC read failures now create FAILED sync runs with Debezium adapter failure payloads in Operations, and stream archive resume cursors require a matching `schemaStrategy`. |
| `VERIFY-DEBEZIUM-LIVE-CDC` | `DOCKER_HOST=unix:///Users/isihyeon/.colima/default/docker.sock TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock uv run pytest tests/integration/test_debezium_live_cdc.py -q` | PASS. `1 passed in 42.08s`. Testcontainers boots a real Kafka-compatible broker, PostgreSQL with `wal_level=logical`, and Debezium Connect `quay.io/debezium/connect:3.5`; insert/update/delete against `public.orders` appear on the Debezium topic, and `foundry_lite_worker.stream_archive.run_stream_archive_once` commits three CDC changelog rows into `raw_cdc.erp_orders`. |

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
| `S21-A6` | dynamic set paging does not bypass Object Query page limit | `PR-4`; `S36A-A7` | Done |

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
| `S34-A3` | non-finance/non-admin users receive masked sensitive Order properties | `MVP-CORE`; `PR-2` | Done |
| `S34-A4` | API and PostgreSQL RLS hide other-tenant dataset/object rows | `MVP-CORE`; `PR-2` | Done |
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

| Evidence id | Checkbox meaning | Git / test evidence | Current status |
|---|---|---|---|
| `S36A-A1` | concurrent same-key action idempotency replays one action run | `PR-2` | Done |
| `S36A-A2` | dataset version commit avoids duplicate/out-of-order versions | `PR-2` | Done |
| `S36A-A3` | promoted/orphan file cleanup is traceable after commit failure | `PR-2` | Done |
| `S36A-A4` | Object Query keyset cursor uses sort key + `object_id` | `PR-4` | Done |
| `S36A-A5` | Object Query rejects raw/tampered cursor payloads | `PR-4` | Done |
| `S36A-A6` | Object Query validates missing properties and preserves numeric sort parity across fake/SQLite/Postgres | `PR-4` | Done |
| `S36A-A7` | Dynamic Object Set reads membership through Object Query paging | `PR-2`; `PR-4` | Done |
| `S36A-A8` | Operations runs API/CLI/UI uses cursor paging and bounded response size | `PR-2` | Done |
| `S36A-A9` | production auth profile fails fast on header-trust/demo auth | `PR-2`; `AUTH-PORT` | Done |
| `S36A-A10` | package/browser SDK outputs expose same method surface | `PR-2` | Done |

## Sprint 37 - REST Pull Connector and Webhook Listener

<a id="s37-a1"></a>
<a id="s37-a2"></a>
<a id="s37-a3"></a>
<a id="s37-a4"></a>
<a id="s37-a5"></a>
<a id="s37-a6"></a>
<a id="s37-a7"></a>
<a id="s37-a8"></a>

| Evidence id | Checkbox meaning | Git / test evidence | Current status |
|---|---|---|---|
| `S37-A1` | mock REST API pulls orders into raw dataset | `PR-3` | Done |
| `S37-A2` | REST adapter returns `nextCursor` and sends a supplied cursor on the next request | `PR-3`; `tests/contracts/test_rest_connector_adapter_contract.py` | Done at adapter level |
| `S37-A3` | webhook event appends to raw dataset transaction | `PR-3` | Done |
| `S37-A4` | invalid webhook signature is rejected and audit deny remains | `PR-3` | Done |
| `S37-A5` | REST rate-limit failure and webhook signature deny appear in Operations surface | `PR-3` | Done |
| `S37-A6` | REST source URL validation blocks localhost/private/link-local/internal metadata addresses by default | `VERIFY-REST-SSRF`; `libs/foundry_lite/infrastructure/adapters/rest_connector.py`; `tests/contracts/test_rest_connector_adapter_contract.py` | Done |
| `S37-A7` | REST pagination cursor is persisted on committed dataset transactions and used as the next sync default | `VERIFY-REST-WEBHOOK-OPS`; `tests/integration/test_rest_connector_ingest.py`; `libs/foundry_lite/application/services/dataset/ingest.py` | Done |
| `S37-A8` | duplicate webhook event delivery replays the existing committed version instead of creating another event row/version | `VERIFY-REST-WEBHOOK-OPS`; `tests/smoke/test_interfaces.py`; `tests/contracts/test_dataset_transaction_repository_contract.py` | Done |

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
| `S38-A3` | worker restart resumes after last committed offset | `PR-5`; `VERIFY-SCALE-ADAPTERS` | Done for local/fake checkpoint path |
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
| `S39-A1` | mock ERP orders row insert/update/delete appears on a CDC topic | `VERIFY-DEBEZIUM-LIVE-CDC`; `tests/integration/test_debezium_live_cdc.py`; `infra/docker-compose.dev.yml` `cdc` profile | Done |
| `S39-A2` | CDC event appends to raw changelog dataset | `PR-10`; `VERIFY-CDC-STREAM-ARCHIVE`; `tests/integration/test_cdc_stream_archive.py` | Done for Debezium-shaped stream events |
| `S39-A3` | primary key and ordering metadata are visible in preview | `PR-10`; `VERIFY-CDC-STREAM-ARCHIVE`; `tests/integration/test_cdc_stream_archive.py` | Done |
| `S39-A4` | delete event is standardized as `after=null` or tombstone policy | `PR-10`; `VERIFY-CDC-STREAM-ARCHIVE`; `tests/contracts/test_debezium_cdc_adapter_contract.py` | Done for `after=null` delete envelopes |
| `S39-A5` | CDC connector failure/lag is visible in Operations | `VERIFY-CDC-STREAM-ARCHIVE`; `test_cdc_stream_archive_read_failure_is_visible_in_operations`; `test_cdc_stream_archive_updates_unread_lag_metric` | Done |

## Open / Not Yet Merged Scope

These items intentionally remain unchecked until a later PR creates code and gate evidence.

| Scope | Current tracking note |
|---|---|
| CDC object indexing | Sprint 40 remains future work after Sprint 39 live Debezium topic/source evidence. |
| OpenSearch, Iceberg, Spark, Kubernetes production adapters | Sprint 42-45 remain future work. |
