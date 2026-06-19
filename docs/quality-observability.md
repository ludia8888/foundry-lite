# Quality, Error Tracing, And Observability

이 문서는 Foundry-lite에서 에러를 추적하고, 코드 결합도를 확인하고, 출시 전 품질 게이트를 통과시키는 방법을 설명한다.

비개발자 관점으로 말하면, 아래 도구들은 “문제가 생겼을 때 어디서, 왜, 어떤 흐름 때문에 생겼는지 찾기 위한 블랙박스”다.

## 1. Local Release Gate

전체 게이트:

```bash
pnpm ci:gate
```

이 명령은 로컬에서 아래 검사를 순서대로 실행한다. GitHub Actions에서는 같은
`scripts/ci_gate.sh`를 `static`, `coverage`, `flaky`, `runtime`, `e2e` lane으로
나누어 동시에 실행한다. 마지막 `quality-gate` aggregate job은 모든 lane이 성공했는지
다시 확인하므로 branch protection이 요구하는 gate 이름은 유지되고, 검사 기준이나
반복 횟수는 낮아지지 않는다.

| 실행 위치 | 명령/잡 | 역할 |
|---|---|---|
| 로컬 | `pnpm ci:gate` | 전체 release evidence를 직렬로 실행 |
| GitHub Actions | `quality-static` | 정적 분석, 타입, 아키텍처, 문서 drift, 보안/복잡도 gate |
| GitHub Actions | `quality-coverage` | 전체 pytest branch coverage, 95% global/tier/public API coverage |
| GitHub Actions | `quality-flaky` | 전체 pytest suite를 `pytest-xdist`로 3회 반복해 flaky outcome 차단 |
| GitHub Actions | `quality-runtime` | 데모, OpenLineage, audit/outbox, data correctness, trace, diagnostics |
| GitHub Actions | `quality-e2e` | Playwright browser E2E |
| GitHub Actions | `quality-gate` | 위 모든 lane 결과를 required check 하나로 집계 |

전체 게이트가 포함하는 검사는 아래와 같다.

| 구분 | 도구 | 잡는 문제 |
|---|---|---|
| 포맷/린트 | Ruff | 잘못된 코드 스타일, import 문제, 흔한 버그 패턴 |
| 타입 검사 | mypy, pyright | 값의 타입이 맞지 않아 런타임에서 터질 문제 |
| 의존성 구조 | `scripts/quality/check_dependency_graph.py` | domain이 app을 import하는 식의 레이어 침범, 순환 의존성 |
| 인프라 경계 | `check_infra_import_boundary.py` | domain/application이 concrete infra SDK에 묶이는 문제. 현재 application baseline은 `0`이며 새 infra 접근은 repository/adapter port를 통과해야 한다. |
| service 의존성 | `check_service_dependencies.py` | service의 `required_dependencies`/`required_collaborators` 선언과 실제 dependency/collaborator 접근이 다르거나, 숨은 `self.<attr>`에 기대는 문제 |
| service call graph | `check_service_call_graph.py` | `self.runtime_service._audit(...)` 같은 명시적 collaborator call이 순환하거나 depth/fan-out 한계를 넘는 문제 |
| 모듈 크기 | `check_application_module_size.py` | application core/service 파일이 다시 god file로 커지는 문제 |
| 함수 길이 | `check_function_length.py` | application 함수가 40줄을 넘으면서 책임이 섞이는 문제 |
| boolean naming | `check_boolean_naming.py` | boolean 인자와 annotated field가 `is_`/`has_`/`can_`/`should_`/`include_` 같은 읽기 쉬운 이름을 벗어나는 문제 |
| dict-any signature budget | `check_dict_any_budget.py` | application 함수 시그니처에서 `dict[str, Any]`가 더 늘어나 schema drift를 숨기는 문제 |
| API router purity | `check_router_layer_purity.py` | API 라우터가 application service를 건너뛰고 repository나 DB transaction을 직접 만지는 문제 |
| query side-effect boundary | `check_query_side_effects.py` | 조회처럼 보이는 service method가 DB write, audit/outbox/lineage write, 파일 write, write adapter, mutation collaborator로 이어지는 문제 |
| repository no-business | `check_repository_no_business.py` | repository가 validation/permission/conflict 같은 비즈니스 판단을 직접 소유하거나 transaction을 직접 끝내는 문제 |
| tenant write guard | `check_tenant_write_guard.py` | tenant-scoped insert/update/delete가 `tenant_id` 없이 실행되어 다른 tenant 데이터를 건드릴 수 있는 문제 |
| contract test per port | `check_contract_test_per_port.py` | 새 port/interface가 contract test 없이 추가되어 fake/local/scale adapter 의미가 갈라지는 문제 |
| strategy/specification testability | `check_strategy_specification_tests.py` | filter/precondition 같은 조건 규칙이 API/Core 경유 테스트로만 검증되어 작은 규칙을 따로 고치기 어려워지는 문제 |
| integration scenario markers | `check_integration_scenario_markers.py` | MVP release 필수 통합 시나리오 7개 중 하나라도 테스트 마커로 증명되지 않는 문제 |
| regression test per bug-fix | `check_regression_test_per_bugfix.py` | fix/bug/patch/regression 커밋이 회귀 테스트 없이 증상만 덮고 들어오는 문제 |
| PR root-cause evidence | `check_pr_root_cause_section.py` | PR 설명에 원인, 영향 범위, 회귀 방지 증거가 없어 리뷰가 증상 제거 패치를 놓치는 문제 |
| current-state doc drift | `check_doc_drift.py` | 현재 구현처럼 적은 문서의 source path/script/Python symbol이 실제 코드에 없어 문서가 코드보다 앞서는 문제 |
| schema revision guard | `check_schema_revision_guard.py` | DB 테이블/컬럼/unique constraint 모양이 revision snapshot 없이 바뀌어 코드 가정만 남는 문제 |
| schema migration safety | `check_schema_migrations.py` | Alembic migration history가 여러 head로 갈라지거나 destructive downgrade가 운영 rollback처럼 남는 문제 |
| schema migration expand-contract guard | `check_schema_migrations.py` | expand 단계에서 old app/write path를 깨는 schema drop/alter/rename, 기본값 없는 NOT NULL 컬럼, 검토 불가능한 SQL, 준비 안 된 contract cleanup이 들어오는 문제 |
| schema migration release-window guard | `check_schema_migrations.py` | migration phase와 rolling-deploy compatibility window가 어긋나 old/new app 공존 검토 기준이 사라지는 문제 |
| schema migration singleton runner | `run_migrations.py` + `test_migration_runner.py` | API/worker/app startup 여러 개가 동시에 migration을 실행해 schema lock, partial migration, app/schema mismatch를 만드는 문제 |
| schema migration operator evidence | `run_migrations.py` + `test_failed_migration_leaves_operator_evidence` | 실패한 migration이 어느 revision/lock/error 상태였는지 운영자가 확인할 JSON 증거 없이 traceback으로만 끝나는 문제 |
| dataset schema evolution evidence | `schema_evolution.py` + `quality:schema-evolution` | rename/drop/narrowing 같은 schema-breaking 변경과 widening/backfill 영향이 Dataset commit metadata 없이 merge되는 문제 |
| mutation audit | `check_audit_on_mutation.py` | public service mutation이 repository write에 닿으면서 audit/outbox 증거가 없는 문제 |
| transaction outbox/audit pair | `check_transaction_outbox_pair.py` + `test_action_commit_object_edit_audit_outbox_atomic` | 같은 transaction 안에서 state write와 audit/outbox 증거가 함께 남지 않는 문제. 정적 게이트는 call tree를 막고, action 실패 주입 테스트는 object/action/writeback/edit/audit/outbox 중간 실패가 실제 DB rollback에 묶이는지 확인한다. |
| action idempotency | `check_idempotency_on_action.py` | Action API/Core/Service/schema 사이에서 `Idempotency-Key` 필수 계약, 기존 action_run 재사용 경로, same-key/different-request fingerprint conflict 방어가 끊기는 문제 |
| error response request_id | `check_error_response_has_request_id.py` | API 에러 응답에서 운영 추적용 `request_id`가 빠지는 문제 |
| log trace keys | `check_log_has_trace_keys.py` | 운영 로그가 `request_id`, `tenant_id`, run id 계열 키 없이 남아 trace/audit/API 에러와 이어지지 않는 문제 |
| required operational metrics | `check_metrics_exposed.py` | Prometheus payload에서 dataset commit, transform, action, query, outbox lag, failed run, DLQ size 지표가 빠지는 문제 |
| runtime audit count | `check_audit_count_runtime.py` | 데모 폐루프가 만든 상태 변경 수와 durable `audit_events` row 수가 어긋나는 문제 |
| runtime outbox consistency | `check_outbox_consistency.py` | 데모 폐루프가 만든 event-propagated 상태 변경과 durable `outbox_events` row가 어긋나는 문제 |
| OpenLineage dynamic lineage | `check_openlineage_dynamic_lineage.py` | transform run의 input/output dataset version과 durable lineage edge가 누락·중복·오연결되는 문제 |
| trace continuity | `check_trace_continuity.py` | request span, service span, SQLAlchemy DB span이 서로 다른 trace로 끊기는 문제 |
| adapter error trace keys | `check_adapter_error_trace_keys.py` | adapter 실패가 run error payload에 request/tenant/actor/run/correlation/adapter 키 없이 남는 문제 |
| adapter failure taxonomy | `check_adapter_failure_taxonomy.py` | 새 compute/storage/workflow/stream/search/connector/auth adapter가 retry 가능 여부, timeout, idempotency key, 운영자 메시지 없이 들어오는 문제 |
| flaky detector | `check_flaky_detector.py` | 이미 작성된 pytest suite가 한 번은 통과하지만 반복 실행에서 실패하거나 수집 결과가 흔들리는 문제. 새 동시성 interleaving을 자동 생성하지는 않으므로, dataset transaction/object/action/CDC/outbox/materialization/search/tenant 경합은 별도 contract/integration scenario로 명시해야 한다. |
| 테스트 우회 방지 | `check_no_test_bypasses.py` | `tests/**/*.py`에서 skip/xfail로 release gate를 우회하는 테스트. PostgreSQL 로컬 opt-out만 명시적으로 허용하고 `pnpm ci:gate`에서 차단한다. |
| 커버리지 제외 예산 | `check_pragma_no_cover_budget.py` | `# pragma: no cover`로 테스트 사각지대를 만드는 문제. 현재 baseline은 `0`이다. |
| Facade private 테스트 부채 | `check_private_test_references.py` | 테스트가 `core._...` private facade 위임에 다시 기대는 문제. 현재 baseline은 `0`이다. |
| 보안 정적 분석 | Bandit | 위험한 Python 코드 패턴 |
| 의존성 취약점 | pip-audit | 설치 패키지의 알려진 보안 취약점 |
| 복잡도 | Radon, Xenon | 너무 복잡해서 장애 추적이 어려운 함수. CI는 block complexity `B` 초과를 막는다. |
| 동적 테스트 | pytest + coverage | 실제 기능/실패 경로 검증, branch coverage 95% |
| 계층별 coverage | `check_tier_coverage_by_layer.py` | 평균 coverage가 domain/application/infrastructure/API/CLI/worker 중 약한 계층을 숨기는 문제 |
| public callable coverage | `check_public_api_coverage.py` | 공개 함수/메서드가 최소 한 번 실행됐는지. 분기 검증은 branch coverage가 담당한다. |
| 제품 데모 | `pnpm demo:supply-chain` | 문서의 MVP 폐루프가 실제로 끝까지 도는지. 기본 CLI 데모는 `.foundry-lite-demo/`에서 fresh 실행되어 반복 실행이 로컬 DB 상태에 의존하지 않는다. |
| 런타임 진단 | `run_runtime_diagnostics.py` | 메모리, 프로파일, 경고, Python 장애 로그 |
| 브라우저 E2E | Playwright | 화면, API, core가 함께 동작하는지 |

## 2. Error Tracing

Foundry-lite는 다음 ID를 최대한 끊기지 않게 남긴다.

- `request_id`: API 요청 단위 추적
- `tenant_id`: 어떤 tenant에서 발생했는지
- `actor_user_id`: 어떤 사용자가 실행했는지
- `dataset_version_id`: 어떤 데이터 버전에서 결과가 왔는지
- `transform_run_id`: 어떤 변환 실행이 결과를 만들었는지
- `action_run_id`: 어떤 액션 실행이 객체 변경을 만들었는지
- `object_type`, `object_id`, `object_version`: 어떤 운영 객체가 바뀌었는지

API 에러 응답에는 `request_id`가 들어간다. 운영자는 이 값을 기준으로 trace, metrics, audit event를 이어서 볼 수 있다.

현재 API는 `AuthProvider` port를 통해 local/demo/header-trust/jwt/oidc profile을
선택한다. local/default profile은 `X-Tenant-ID`, `X-User-ID`, `X-Roles` header를
신뢰하지만, `FOUNDRY_LITE_RUNTIME_PROFILE=production`에서는 header-trust/demo auth
profile을 startup에서 거부한다. `JwtOidcAuthProvider`는 local OIDC discovery/JWKS
JSON과 `Authorization: Bearer ...` token으로 RS256 signature, issuer, audience,
expiry, tenant/subject-or-service-account/roles claim을 검증한다. `sub`가 없는 M2M
token은 configured service-account claim(기본 `client_id`)으로
`service-account:<client_id>` actor가 되지만 tenant claim 없이는 거부된다.
`FOUNDRY_LITE_OIDC_REVOKED_JTIS_JSON`에 token `jti`가 있으면 서명/issuer/audience가
맞아도 거부된다. JWKS는 unknown `kid`에서 다시 읽고 기존 key cache를 유지한다.
Web/CLI demo는 명시적인 demo admin context를 사용한다.
S58A secret-provider slice는 웹훅 서명키를 직접 `os.getenv`로 읽지 않고
`foundry.secret_provider`를 통해 조회한다. 현재 local adapter인 `EnvSecretProvider`는
`FOUNDRY_LITE_WEBHOOK_SIGNING_KEY`와 `FOUNDRY_LITE_SECRET_<NAME>` alias를 지원하고,
REST connector는 bearer/header credential secretRef를 snapshot 호출마다 다시 조회한다.
operator evidence/error에서는 secret 값을 `***REDACTED***` 형태로 가린다. Live
OIDC discovery fetch, JWKS polling/TTL/key retirement, IdP introspection,
refresh-token revocation, service-account registry, scope policy, cloud/Vault secret manager,
full connector workflow credential refresh는 future work다.

## 3. OpenTelemetry

OpenTelemetry는 “요청 하나가 어떤 함수와 DB 작업을 거쳐 갔는지”를 span으로 남긴다.

로컬에서 Tempo로 trace를 보내려면:

```bash
docker compose -f infra/docker-compose.dev.yml up -d tempo grafana prometheus
OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318/v1/traces pnpm dev
```

콘솔에 trace를 직접 보고 싶으면:

```bash
FOUNDRY_LITE_OTEL_CONSOLE=1 pnpm demo:supply-chain
pnpm diagnostics:trace
```

CI의 demo smoke는 `artifacts/demo/supply-chain.json`을 남긴 뒤 `python -m json.tool`로 다시 파싱한다. 따라서 데모가 성공하더라도 산출물에 `pnpm` 실행 로그나 사람이 읽는 문구가 섞이면 release gate에서 실패한다.

## 4. Metrics And Grafana

API 서버는 Prometheus 형식의 metrics를 제공한다.

```bash
curl http://127.0.0.1:8000/metrics
```

Grafana:

- URL: `http://127.0.0.1:3000`
- user/password: `admin/admin`
- dashboard: `Foundry-lite / Foundry-lite Overview`

주요 지표:

- `foundry_lite_http_requests_total`
- `foundry_lite_http_request_seconds`
- `foundry_lite_core_operations_total`
- `foundry_lite_core_operation_seconds`
- `foundry_lite_dataset_commit_seconds`
- `foundry_lite_transform_run_seconds`
- `foundry_lite_action_apply_seconds`
- `foundry_lite_object_query_seconds`
- `foundry_lite_outbox_publish_lag_seconds`
- `foundry_lite_failed_runs_total`
- `foundry_lite_dlq_size`

## 5. Code Coupling And Relationship Reports

코드간 연관성 리포트:

```bash
pnpm quality:architecture
```

결과:

- `artifacts/quality/dependency_graph.json`
- `artifacts/quality/dependency_graph.md`
- `artifacts/quality/infra_import_boundary.json`
- `artifacts/quality/application_module_size.json`
- `artifacts/quality/function_length.json`
- `artifacts/quality/boolean_naming.json`
- `artifacts/quality/dict_any_budget.json`
- `artifacts/quality/query_side_effects.json`
- `artifacts/quality/tenant_write_guard.json`
- `artifacts/quality/strategy_specification_tests.json`
- `artifacts/quality/integration_scenario_markers.json`
- `artifacts/quality/testcontainers_preflight.json`
- `artifacts/quality/regression_test_per_bugfix.json`
- `artifacts/quality/pr_root_cause_section.json`
- `artifacts/quality/doc_drift.json`
- `artifacts/quality/schema_revision_guard.json`
- `artifacts/quality/action_idempotency.json`
- `artifacts/quality/transaction_outbox_pair.json`
- `artifacts/quality/error_response_request_id.json`
- `artifacts/quality/log_trace_keys.json`
- `artifacts/quality/metrics_exposed.json`
- `artifacts/quality/outbox_consistency.json`
- `artifacts/quality/openlineage_dynamic_lineage.json`
- `artifacts/quality/openlineage_events.json`
- `artifacts/quality/trace_continuity.json`
- `artifacts/quality/adapter_error_trace_keys.json`
- `artifacts/quality/flaky_detector.json`
- `artifacts/quality/radon_cc.json`
- `artifacts/quality/private_test_references.json`

여기에서 볼 수 있는 것:

- 어떤 모듈이 어떤 모듈에 의존하는지
- fan-in: 이 모듈을 얼마나 많이 참조하는지
- fan-out: 이 모듈이 얼마나 많은 모듈을 참조하는지
- 순환 의존성 여부
- 문서의 레이어 규칙 위반 여부

CI에서는 fan-out이 `10`을 넘는 내부 모듈을 막는다. 쉽게 말해, 한 파일이 너무 많은 내부 파일을 직접 알고 있으면 구조가 커질수록 고치기 어려워지기 때문이다.

CI에서는 application module이 `500`줄을 넘는 것도 막는다. `FoundryLite`는 Facade로 유지하고, 실제 구현은 Dataset, Transform, Ontology, Object, Action, Materialization, Runtime event, Demo orchestration service로 나눈다.

현재 CI는 `core._...` private facade 위임 테스트를 `0`개로 강제한다. 내부 service/helper 테스트는 필요한 경우 책임 소유 module을 직접 대상으로 삼되, `FoundryLite`가 숨은 private delegation layer로 되살아나면 안 된다.

Repository contract test 중 PostgreSQL testcontainer 축은 release/CI evidence에 반드시 포함되어야 한다. Docker가 꺼진 로컬 개발 환경에서는 `FOUNDRY_LITE_SKIP_POSTGRES_CONTRACTS=1`로 임시 우회할 수 있지만, `pnpm ci:gate`는 이 값을 발견하면 즉시 실패한다. 즉, “로컬 편의”와 “출시 증거”를 분리한다.

`pnpm ci:gate`는 pytest를 시작하기 전에 `scripts/quality/check_testcontainers_preflight.py`를 실행한다. Docker/Testcontainers가 현재 셸에서 보이지 않으면 긴 Postgres container traceback까지 기다리지 않고, Colima 사용자를 위해 아래 환경 변수를 바로 안내한다.

```bash
export DOCKER_HOST=unix:///Users/isihyeon/.colima/default/docker.sock
export TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock
pnpm --silent ci:gate
```

복잡도 기준:

```bash
pnpm quality:complexity
```

이 명령은 Radon으로 복잡도 리포트를 만들고, Xenon으로 `C`, `D`, `E`, `F` 등급 함수가 들어오지 못하게 막는다.

## 6. Python Runtime Debugging

런타임 진단:

```bash
pnpm diagnostics
```

콘솔에도 OpenTelemetry span을 전부 출력하는 초상세 모드:

```bash
pnpm diagnostics:trace
```

결과:

- `artifacts/diagnostics/runtime_diagnostics.json`
- `artifacts/diagnostics/demo_profile.pstats`
- `artifacts/diagnostics/demo_profile_top.txt`
- `artifacts/diagnostics/faulthandler.log`

사용하는 Python 표준 도구:

- `faulthandler`: 크래시/멈춤 스택
- `tracemalloc`: 메모리 사용 추적
- `cProfile`/`pstats`: 함수별 실행 시간
- `gc`: 가비지 컬렉션 상태
- `warnings`: 경고 수집

수동 디버깅이 필요하면 Python 표준 도구도 그대로 사용할 수 있다.

```bash
uv run python -m pdb -m foundry_lite_cli.main demo run-supply-chain
uv run python -m trace --count --summary apps/cli/foundry_lite_cli/main.py demo run-supply-chain
```

## 7. Current Implementation Limits

현재 커밋은 로컬 core slice다.

- object store는 PostgreSQL JSONB가 아니라 SQLite + SQLAlchemy JSON column이다.
- PostgreSQL snapshot connector production implementation은 아직 없다. 다만
  PostgreSQL-backed repository closed-loop proof는 테스트 증거로 존재한다.
- action precondition은 CEL이 아니라 제한된 `safeExpression` subset이다.
- ERP writeback은 실제 외부 호출이 아니라 `mock_erp_simulator` 기록이다.
- Alembic baseline migration은 현재 브랜치에 존재한다. `tests/integration/test_migrations.py`가 fresh DB에 `alembic upgrade head`를 적용하고 SQLAlchemy metadata와 table/column shape가 같은지 검증한다. S55의 migration safety slice는 `check_schema_migrations.py`로 Alembic history가 단일 head인지, revision id가 filename과 맞는지, destructive downgrade operation이 남아 있지 않은지 확인한다. 같은 gate는 `migration_phase`와 `release_compatibility` 선언을 요구하고, expand 단계에서 compatible add/table/index/backfill SQL만 허용하며 기본값 없는 새 `NOT NULL` 컬럼, phase/window mismatch, 준비 안 된 contract cleanup을 차단한다. Runner slice는 `scripts/operations/run_migrations.py`의 `db:migrate` runner와 `tests/unit/test_migration_runner.py`로 전용 migration job이 DB-level singleton lock 아래에서만 Alembic을 실행하는지, 그리고 실패한 migration이 password-masked operator evidence JSON을 남기는지 검증한다. Dataset schema evolution slice는 `schema_evolution.py`와 `quality:schema-evolution`으로 Dataset commit 직전 rename/drop/narrowing/primary-key change를 blocking evidence로 만들고, numeric widening/deprecated field/backfill-needed change를 transaction metadata로 남긴다. Ontology migration slice는 `ontology_migration.py`와 `quality:ontology-migrations`로 active ontology와 candidate YAML을 비교해 property/object/link/action parameter breaking change를 activation 전에 막고, deprecated property warning, object reindex plan, generated SDK compatibility evidence를 audit/outbox와 gate에 남긴다. S56 proactive observability slice는 `observability_detectors.py`, `quality:observability-detectors`, and `quality:slo-contracts`로 versioned detector config를 현재 `RuntimeRunSnapshot`에 read-only로 적용해 missing-data, lag, skew, cooldown dedupe, SLO evidence, API/SDK surface를 검증한다. S57 backup/restore preflight slice는 `backup_restore_service.py`, `quality:backup-restore`, and `POST /api/operations/backup-restore/preflight`로 metadata DB의 committed dataset version과 storage manifest/data file byte/hash를 비교하고, missing/corrupt version을 `blocked` issue로 남기며, active object index pointer, action/outbox/audit high-watermark, Temporal strategy, search rebuild marker, generated SDK surface를 검증한다. 다만 live PostgreSQL contention proof, full old/new app deployment window, actual ontology migration executor/reindex worker, real backfill worker/progress API, multi-step upgrade/rollback, 운영 runbook, 배포 중 rollback 테스트, full backup artifact creation, restore-mode traffic gate enforcement, outbox publisher pause/resume executor, post-restore closed-loop validation, stored incident lifecycle, notification delivery, dashboard/timeline UI, broker-offset/REST-cursor lag adapters, tenant/object-key skew, persistent threshold registry는 아직 future scope다. `check_schema_revision_guard.py`도 계속 SQLAlchemy metadata와 `infra/schema_revisions` snapshot drift를 차단한다.
- S57 restore-mode lockout/resume slice는 `start_restore_mode`, `restore_mode_status`, `approve_restore_resume`, and `RuntimeService.dead_letter_event_retry_plan`으로 restore mode audit evidence, same-`restoreId` idempotency, `is_serving_traffic_open=false`, `is_outbox_publisher_paused=true`, restore mode 중 outbox retry/reprocess 진입 차단, post-restore 폐루프 증거 없는 resume 거절, and `resume_approved` 후 현재 retry/reprocess entrypoint 재개를 검증한다. 이 증거는 현재 operator retry path의 중복 side effect와 무검증 resume을 막는 범위이며, real publisher daemon pause/resume executor와 platform-wide write traffic gate는 아직 future scope다.
- S58A auth/secret-provider slice는 `JwtOidcAuthProvider`, `SecretProvider`/`SecretValue`, `EnvSecretProvider`, `RestPullConnectorAdapter`, `quality:auth-secrets`로 인증/비밀값 조회 경계를 application port에 고정한다. JWT/OIDC profile은 `Authorization: Bearer ...` token의 signature, issuer, audience, expiry, tenant/subject-or-service-account/roles claim을 local discovery/JWKS JSON 기준으로 검증하고, unknown `kid`에서 JWKS를 다시 읽으며 기존 key cache를 유지한다. `sub`가 없는 M2M token은 configured service-account claim으로 tenant-scoped `service-account:*` actor가 된다. Local revoked-`jti` denylist에 들어간 token은 signature가 맞아도 거부된다. 웹훅 서명키 lookup은 `foundry.secret_provider`를 통하고, REST connector bearer/header credential secretRef는 snapshot 호출마다 `SecretProvider`에서 다시 조회하며, secret value는 repr, redacted operator evidence, missing-secret error detail에 노출되지 않는다. Live OIDC discovery fetch, JWKS polling/TTL/key retirement, IdP introspection/refresh-token revocation, service-account registry/scope policy, cloud/Vault secret manager, full connector workflow credential refresh, full rotation lifecycle은 아직 future scope다.
- S58B privacy transform slice는 `PrivacyTransformPlan`, `PrivacyFieldRule`, `PrivacyDatasetRef`, `transform_privacy_rows`, `build_privacy_openlineage_event`, `quality:privacy`로 production row를 staging/analytics/AI 실험에 넘기기 전 바꾸는 privacy transform proof다. 현재는 tenant-scoped HMAC pseudonymization, field-level `***ANONYMIZED***` replacement, local regex email/SSN/US-phone redaction, replay-stable plan version, lineage metadata, protected in-memory reversible mapping proof, production-to-nonprod `PrivacyReplicationPolicy` proof, source/target dataset version lineage, and raw-value-free OpenLineage-compatible privacy event artifact를 제공한다. Reversible mapping rule은 protected store 없이 실행되지 않고, redacted mapping evidence에는 원본값 대신 `***PROTECTED***`와 hash만 남는다. Replication policy는 production에서 staging/analytics/AI 실험 환경으로 민감 필드를 복제할 때 privacy plan이 없거나 민감 필드가 누락/passthrough이면 차단하고, 허용된 transform lineage에 source/target environment와 plan version evidence를 남긴다. Durable environment replication workflow, durable/encrypted protected mapping backend, ontology-driven privacy policy registry, runtime DB `lineage_edges`/outbox/OpenLineage transport integration, approval workflow, and right-to-erasure lifecycle은 아직 future scope다.
- Temporal은 adapter/time-skipping ratchet, unavailable/timeout/cancel/error-payload proof, and xdist-safe test-server warmup guard를 갖는다. S52 첫 slice에서는 `ConnectorSyncWorkflow`가 Operations/API/generated SDK를 통해 시작/조회되고, local/fake/Temporal profile이 같은 `ProductWorkflowRun` shape와 audit-linked `workflowRunId`/`foundryRunId` evidence를 남긴다. 다만 실제 connector page fetch/staging/commit/cursor advance activity chain, cancel cleanup, response-loss reconciliation, continue-as-new, workflow upgrade replay, and managed worker operations는 아직 future scope다.
- Scale Foundation은 문서상 Sprint 02A 목표로 명시되었고, 현재 로컬 slice는 storage/metadata/compute/event/search/workflow/connector/auth boundary를 port/adapter 계약으로 노출한다. stream은 local/fake proof에 더해 production-compatible `KafkaStreamAdapter`, worker composition root, Testcontainers 기반 live Kafka-compatible broker proof, Debezium-shaped CDC envelope archive proof, live Debezium Connect/PostgreSQL CDC topic proof를 갖는다. object indexing은 `backing.cdc` mapping과 `cdc_incremental` index run proof를 통해 CDC update/delete가 batch rebuild 없이 object base layer와 `object.changed` trigger로 이어지는 경로를 갖는다. active object index version은 row에서만 추론하지 않고 `object_index_versions` registry에 저장하므로, row가 0개인 object type도 이후 CDC/search worker가 쓸 serving version을 잃지 않는다. shadow promotion은 이전 active version과 일치할 때만 pointer를 바꾸는 compare-and-swap 규칙을 사용하므로, 같은 object type의 동시 promotion이 silent last-writer-wins로 끝나지 않는다. search는 `ElasticsearchAdapter`, ontology-derived mapping, rebuild consistency, orphan detection, live Testcontainers Elasticsearch outage/concurrent-writer proof, 그리고 failed `indexRuns` + audit operator evidence proof를 갖는다. storage/compute ratchet은 MinIO/S3, Iceberg snapshot/version pinning, Spark SQL transform/abort, and S3+Iceberg+Spark+CDC composition proof를 갖는다. Managed cloud Elasticsearch packaging, production Spark cluster failure modes, production Iceberg catalog operations, full Temporal product workflow data-plane execution, and external connector replacement remain future scope; 이후 구현에서도 trace key, source-of-truth, and operator-evidence contracts를 유지해야 한다.

## 8. S46+ Gate Expansion

S46 이후 로드맵은 [Data Platform Expansion Roadmap](./data-platform-expansion-roadmap.md)을 따른다. S46 static gate로 `quality:semantic-doc-consistency`와 `quality:data-pattern-matrix`가 추가되어 active-covered infra를 범위 설명 없이 future로 되돌려 쓰는 문서 drift, deferred/partial 패턴을 current처럼 쓰는 README/status drift, owner/reason/future test 없는 데이터 패턴 gap을 차단한다.

S47 runtime gate인 `quality:record-dlq-replay`는 stream CDC archive에서 bad record를 `dead_letter_records`로 격리하고, 정상 record commit을 계속하며, DLQ 저장 실패가 main pipeline 성공으로 숨지 않게 검증한다. 또한 Operations API, typed generated SDK, Web Operations UI가 record DLQ 목록/상세, 단건·bulk replay idempotency, replay result, discard, replay/discard audit evidence를 유지하고, source-side 오류율 threshold 초과와 identity/ordering 오류를 fail-closed하며, PostgreSQL 동시 replay request가 한 replay run만 남기는지 확인한다. S49 runtime gate인 `quality:multi-file-dataset`은 manifest-listed data files를 순서대로 읽고 directory/bucket listing으로 unlisted file을 serving data에 섞지 않는지 확인한다. `quality:partition-pruning`은 local/fake read/preview path에서 partition predicate가 실제 read file 수를 줄이는지 확인하며, S3/Iceberg profile-specific branch는 `quality:s3-storage`와 `quality:iceberg` ratchet에 포함된다.

S50 runtime gate인 `quality:iceberg-maintenance`는 Iceberg maintenance dry-run plan이 DB committed version snapshot을 protected로 표시하고 삭제 후보에서 제외하며, candidate/orphan/retained snapshot preview와 audit evidence를 유지하는지 확인한다. S51 runtime gate인 `quality:cdc-continuous-worker`는 stream archive worker의 bounded continuous mode가 기존 transaction/checkpoint boundary를 반복 사용하고, empty poll 또는 stop callback에서 멈추며, batch 사이 committed cursor를 따라 no-gap/no-duplicate archive result를 남기는지 확인한다. S52 runtime gate인 `quality:temporal-engine-integration`은 `ConnectorSyncWorkflow`가 Operations/API/SDK에서 stable idempotency-key workflow id로 시작되고, local/fake/Temporal profile이 같은 `ProductWorkflowRun` 계약과 audit-linked Foundry run evidence를 유지하는지 확인한다.

S53 runtime gates인 `quality:external-writeback`과 `quality:saga-reconciliation`은 simulated before-commit writeback response loss가 `failed`가 아니라 `outcome_unknown` action/writeback/audit evidence로 남는지, simulated external success 뒤 local mutation failure가 `compensation_required` evidence로 남는지, 같은 idempotency key replay가 새 writeback을 만들지 않는지, operator-provided remote-success resolve가 원래 local object mutation을 따라잡고 concurrent resolve 중 한 winner만 남기는지, 그리고 sensitive writeback parameter가 Operations/audit evidence에 raw로 노출되지 않는지 확인한다. 실제 compaction rewrite, snapshot expiration, orphan cleanup execution, CDC object-indexer daemon, lease/fencing, rebalance revoke, commit-unknown reconciliation, full connector-sync data-plane execution, cancel cleanup, response-loss reconciliation, continue-as-new, workflow upgrade replay, real vendor writeback lookup, compensation worker execution, persistent reconciliation queue, and approval UI는 아직 future scope다.

S54 runtime gate인 `quality:data-contracts`는 dataset quality check result가 어떤 staged candidate fingerprint와 `dataset_schemas` row/version을 기준으로 검증됐는지 `checked_manifest_hash`, `validated_against_schema_version_id`, `validated_against_schema_version`으로 남기는지 확인한다. 이후 같은 dataset에 새 schema version이 생겨도 historical check result가 당시 schema row/version에 pinned되는지도 확인한다. 또한 성공한 check result가 `PASS`로 저장되는지, warning severity 실패가 commit을 막지 않는 `WARN`으로 보이는지, 품질검사 후 staged candidate가 바뀌거나 hard failure가 나면 final storage commit 전에 `BLOCK_COMMIT`으로 거부되는지 확인한다. Row-level `not_null`/`unique` quarantine check는 실패 record를 `DATA_QUALITY_CONTRACT` Record DLQ로 격리하고, 정상 record만 남긴 candidate를 재검증한 뒤 commit해야 한다. Operations run detail은 같은 transaction의 `quality` summary, schema reference, checked manifest hash, check result, `failedRowSampleCount`, `failedRowSamples`를 노출해야 한다. 이 slice는 schema race, candidate tamper, contract drift, row-level quarantine evidence, candidate quality report, failed-row sample evidence를 나중에 설명할 수 있게 만드는 첫 증거이며, full DataContract CRUD, owner notification, dedicated failed-row sample UI, quality history/trend, and production DB schema-race proof는 아직 future scope다.

S58A runtime gate인 `quality:auth-secrets`는 AuthProvider production guard, JWT/OIDC bearer-token 검증, tenant-scoped service-account token mapping, local revoked-`jti` denylist, JWKS refresh/cache, SecretProvider contract, REST connector secretRef refresh, adapter failure taxonomy, webhook signature verification, and CI lane wiring을 함께 확인한다. 비개발자식으로 말하면, "사용자나 서버 계정이 누구인지 확인하는 입구"와 "비밀값을 코드가 마음대로 집어 읽는 길"을 둘 다 좁힌 것이다. 이 gate는 full login system이나 cloud secret manager가 아니라 local JWT/OIDC verification, local secret-provider boundary, REST connector local secretRef refresh, and redaction proof만 보장한다.

S58B runtime gate인 `quality:privacy`는 같은 identifier가 같은 tenant/scope 안에서는 같은 pseudonym으로 replay되고, tenant가 다르면 서로 연결되지 않으며, anonymized rows에 raw PII samples가 남지 않고, plan version/lineage metadata가 stable한지 확인한다. 또한 reversible mapping이 명시된 rule은 protected store 없이는 실패하고, protected store의 operator-facing evidence가 원본값을 노출하지 않는지 확인한다. Production-to-nonprod replication policy도 같은 gate에서 확인한다. 즉 privacy plan이 없거나 민감 필드가 보호되지 않으면 staging/analytics/AI 실험 복제 policy가 fail-closed하고, 허용된 경우에는 lineage에 source/target environment와 plan version evidence가 남는다. 새 dataset-lineage proof는 source dataset version과 anonymized target dataset version을 연결하고, OpenLineage-compatible event artifact가 raw PII 없이 replay-stable하게 만들어지는지도 확인한다. 이 gate는 full privacy platform이 아니라 deterministic pseudonymization, basic anonymization, local text PII redaction, protected in-memory reversible mapping, replication policy, replayable lineage metadata, and raw-value-free OpenLineage artifact의 첫 증거다.

S58C runtime gate인 `quality:erasure`는 deletion request, tenant-scoped subject resolution,
deletion manifest, backup-retention pending state, audit-minimization evidence, and search
rebuild exclusion proof를 확인한다. 비개발자식으로 말하면, 아직 실제 데이터 저장소 전체에서
삭제 작업자가 뛰는 단계는 아니지만, "어떤 사람의 어떤 데이터가 어느 표면에서 지워져야 하는지"와
"백업 보존 때문에 아직 지울 수 없는 항목은 언제까지 보류되는지"를 raw subject 값 없이
작업 지시서로 남기는 단계다. Durable request table/API/workflow, ObjectStore/SearchAdapter
delete executor, materialization recompute, DLQ redaction executor, backup manifest rewrite,
KMS/cloud crypto-shredding, and audit compaction executor는 아직 future scope다.

S60 runtime gate인 `quality:ai-evidence`는 object explain의 property-level lineage와
AI/insight evidence reference를 확인한다. 현재 slice는 `propertyLineage`가 object property별
source dataset version, source object version, source column, source hash, property version,
and masking state를 노출하는지, insight claim이 evidence object 없이 만들어지지 않는지, LLM
extraction evidence가 extractor/model/prompt version과 model parameter hash를 pinning하는지,
reprocessing이 이전 evidence를 덮어쓰지 않고 새 revision을 만드는지, masked source span이 raw
quote를 노출하지 않는지 검증한다. Durable evidence table, 실제 LLM extraction executor,
insight evidence viewer, model-change diff UI, and AI action policy enforcement는 아직 future
scope다.

S61 static gate인 `quality:frontend-foundation`은 generated SDK와 Web Operations가 request id,
tenant/user context, typed error code, retryability를 잃지 않는지 확인한다. 비개발자식으로
말하면, 화면에서 "실패했다"만 보이는 것이 아니라 "어떤 요청이 실패했고, 어떤 종류의 실패이며,
다시 시도해볼 수 있는지"가 같은 SDK 규칙으로 보이게 만든다. 현재는 request/error telemetry와
SDK helper surface proof이며, full login/session UI, automatic retry/backoff, cursor helper,
duplicate-click lock, stale-version conflict UI, and permission-denied masking UX는 future scope다.

새 runtime gate를 추가할 때는 runtime lane과 release lane을 분리한다. PR에서 빠르게 root cause를 잡아야 하는 source-of-truth, adapter contract, failure injection, concurrency/race, retry/idempotency, partial-success cleanup, operator evidence, composition compatibility proof는 runtime lane에 둔다. 큰 데이터/클라우드/chaos 성격의 느린 proof는 release lane에 두고 JSON/Markdown artifact를 업로드한다.

비개발자 관점에서 가장 중요한 규칙은 단순하다. 실패가 로그에만 남으면 부족하다. 실패한 run, audit event, dataset transaction, outbox/error payload, trace summary 중 적절한 곳에 "무슨 계약이 깨졌고 어디를 보면 되는지"가 남아야 한다.

## 9. Playwright E2E

브라우저 테스트:

```bash
pnpm test:e2e
```

이 테스트는 다음을 실제 브라우저에서 검증한다.

- API health check
- `Order O-1001` object load
- `ApproveOrder` action apply
- object version 증가
- status가 `PENDING`에서 `APPROVED`로 변경

실패 시 Playwright artifact는 `artifacts/playwright-report`와 `test-results`에 남는다.
