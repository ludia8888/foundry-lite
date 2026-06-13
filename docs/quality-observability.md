# Quality, Error Tracing, And Observability

이 문서는 Foundry-lite에서 에러를 추적하고, 코드 결합도를 확인하고, 출시 전 품질 게이트를 통과시키는 방법을 설명한다.

비개발자 관점으로 말하면, 아래 도구들은 “문제가 생겼을 때 어디서, 왜, 어떤 흐름 때문에 생겼는지 찾기 위한 블랙박스”다.

## 1. Local Release Gate

전체 게이트:

```bash
pnpm ci:gate
```

이 명령은 아래를 순서대로 실행한다.

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
| mutation audit | `check_audit_on_mutation.py` | public service mutation이 repository write에 닿으면서 audit/outbox 증거가 없는 문제 |
| transaction outbox/audit pair | `check_transaction_outbox_pair.py` | 같은 transaction 안에서 state write와 audit/outbox 증거가 함께 남지 않는 문제 |
| action idempotency | `check_idempotency_on_action.py` | Action API/Core/Service/schema 사이에서 `Idempotency-Key` 필수 계약과 기존 action_run 재사용 경로가 끊기는 문제 |
| error response request_id | `check_error_response_has_request_id.py` | API 에러 응답에서 운영 추적용 `request_id`가 빠지는 문제 |
| log trace keys | `check_log_has_trace_keys.py` | 운영 로그가 `request_id`, `tenant_id`, run id 계열 키 없이 남아 trace/audit/API 에러와 이어지지 않는 문제 |
| required operational metrics | `check_metrics_exposed.py` | Prometheus payload에서 dataset commit, transform, action, query, outbox lag, failed run, DLQ size 지표가 빠지는 문제 |
| runtime audit count | `check_audit_count_runtime.py` | 데모 폐루프가 만든 상태 변경 수와 durable `audit_events` row 수가 어긋나는 문제 |
| runtime outbox consistency | `check_outbox_consistency.py` | 데모 폐루프가 만든 event-propagated 상태 변경과 durable `outbox_events` row가 어긋나는 문제 |
| OpenLineage dynamic lineage | `check_openlineage_dynamic_lineage.py` | transform run의 input/output dataset version과 durable lineage edge가 누락·중복·오연결되는 문제 |
| trace continuity | `check_trace_continuity.py` | request span, service span, SQLAlchemy DB span이 서로 다른 trace로 끊기는 문제 |
| adapter error trace keys | `check_adapter_error_trace_keys.py` | adapter 실패가 run error payload에 request/tenant/actor/run/correlation/adapter 키 없이 남는 문제 |
| adapter failure taxonomy | `check_adapter_failure_taxonomy.py` | 새 compute/storage/workflow/stream/search/connector/auth adapter가 retry 가능 여부, timeout, idempotency key, 운영자 메시지 없이 들어오는 문제 |
| flaky detector | `check_flaky_detector.py` | 테스트가 한 번은 통과하지만 반복 실행에서 실패하거나 수집 결과가 흔들리는 문제 |
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

현재 API는 실제 인증 시스템을 갖고 있지 않다. 헤더가 없으면 `viewer` 역할로 처리되며, Web/CLI 데모는 명시적인 demo role header/context를 사용한다.

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

CI에서는 application module이 `500`줄을 넘는 것도 막는다. `FoundryLiteCore`는 Facade로 유지하고, 실제 구현은 Dataset, Transform, Ontology, Object, Action, Materialization, Runtime event, Demo orchestration service로 나눈다.

현재 CI는 `core._...` private facade 위임 테스트를 `0`개로 강제한다. 내부 service/helper 테스트는 필요한 경우 책임 소유 module을 직접 대상으로 삼되, `FoundryLiteCore`가 숨은 private delegation layer로 되살아나면 안 된다.

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
- PostgreSQL snapshot connector는 아직 없다.
- action precondition은 CEL이 아니라 제한된 `safeExpression` subset이다.
- ERP writeback은 실제 외부 호출이 아니라 `mock_erp_simulator` 기록이다.
- Alembic migration과 Temporal worker는 아직 구현되지 않았다. 현재 release gate는 `check_schema_revision_guard.py`로 SQLAlchemy metadata와 `infra/schema_revisions` snapshot이 어긋나는지만 차단한다.
- Scale Foundation은 문서상 Sprint 02A 목표로 명시되었고, 현재 로컬 slice는 storage/metadata/compute/event/search/workflow/connector/auth boundary를 port/adapter 계약으로 노출한다. stream은 local/fake proof에 더해 production-compatible `KafkaStreamAdapter`, worker composition root, Testcontainers 기반 live Kafka-compatible broker proof를 갖는다. search/workflow/connector의 production OpenSearch/Temporal/외부 connector adapter 교체도 이후 구현에서 trace key와 contract test를 유지해야 한다.

## 8. Playwright E2E

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
