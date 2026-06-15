# AGENTS.md instructions for Foundry-lite

## 필수 시작 절차

이 저장소에서 코드, 테스트, 문서, CI, 아키텍처 변경을 시작하기 전에 반드시 먼저 아래 문서를 읽는다.

1. `foundry_lite_python_engineering_guidelines_ko.md`
2. 변경 범위가 제품/스프린트 목표와 연결되면 `foundry_lite_development_plan_ko_sprintified.md`
3. 변경 범위가 sprint acceptance나 MVP core 완료 조건과 연결되면 `foundry_lite_sprint_breakdown_ko.md`
4. 현재 구현 상태와 문서상 목표를 구분해야 하면 `docs/implementation-status.md`
5. 새 정적/동적 분석 도구를 추가하거나 기존 게이트를 약화/삭제하려 하면 `docs/quality-gate-roadmap.md`

특히 `foundry_lite_python_engineering_guidelines_ko.md`는 이 repo의 Python 백엔드 구현 표준 원본이다. 개발 시작 전에 읽지 않은 상태로 구현을 진행하면 안 된다.

## 개발 원칙

- 비개발자도 이해할 수 있도록 항상 친절하고 자세히 설명한다.
- 문서와 코드가 다르면 코드를 과장하지 말고 현재 구현 상태를 정직하게 문서화한다.
- `FoundryLite`는 얇은 Facade로 유지한다.
- Dataset, Transform, Ontology, Object, Action, Materialization, Runtime event, Demo orchestration 책임을 다시 한 파일로 합치지 않는다.
- application module은 500줄 이하를 유지한다. 필요하면 책임별 service, strategy, specification, adapter, repository로 분리한다.
- 디자인 패턴은 `foundry_lite_python_engineering_guidelines_ko.md`의 “디자인 패턴 적용 원칙”을 따른다.
- infra와 닿는 변경은 Sprint 02A Scale Foundation 원칙을 먼저 확인한다. storage, metadata DB, compute, event, search, workflow, connector, auth 변경은 port/interface, adapter, contract test, trace key를 함께 고려한다.
- 새 mutation은 transaction, audit, outbox, idempotency, error traceability를 함께 고려한다.
- 새 infra dependency는 항상 `CoreDependencies` (`libs/foundry_lite/application/dependencies.py`)에 먼저 선언하고, 실제로 그 dependency를 직접 쓰는 service의 `required_dependencies`에만 추가한다. service가 선언하지 않은 CoreDependencies 필드에 접근하면 안 된다 — `scripts/quality/check_service_dependencies.py`가 이를 강제한다.
- service 사이의 collaborator call은 `self.runtime_service._audit(...)`처럼 소유 service가 보이는 명시적 attribute를 통해 호출한다. 각 service는 실제로 직접 호출하는 collaborator만 `required_collaborators`에 선언한다. `self._audit(...)`처럼 글로벌 method registry에 기대는 숨은 호출을 만들지 않는다. collaborator 그래프는 DAG로 유지한다. cycle 0, depth ≤ 7, service당 fan-out ≤ 10 — `scripts/quality/check_service_call_graph.py`와 `check_service_dependencies.py`가 강제한다.
- 변경 후에는 최소한 관련 테스트와 정적 검사를 실행하고, 가능하면 `pnpm ci:gate`로 전체 품질 게이트를 확인한다.
- 디자인 패턴/안티패턴 위반은 Semgrep rule (`scripts/quality/semgrep-rules/foundry-lite.yml`)로 코드 모양 자체를 차단한다. 위반을 우회하려고 `# nosemgrep:` 주석을 다는 경우 같은 줄에 정직한 사유와 미래 제거 조건을 적는다.
- Layer/import 경계는 `.importlinter` 4 contracts가 transitive 그래프로 강제한다. function-local lazy import도 잡힌다. contract 변경/약화는 `docs/quality-gate-roadmap.md` §5 워크플로를 따른다.
- Module dependency DAG는 `tach.toml`과 `uv run tach check --dependencies`가 강제한다. 새 app/library/boundary를 추가하면 먼저 어느 layer에 속하는지 `tach.toml`에 명시하고, circular dependency 0을 유지한다.
- Function length G3 게이트(`scripts/quality/check_function_length.py`)는 `libs/foundry_lite/application` 함수가 40줄을 넘으면 release gate를 차단한다. 현재 baseline은 0개이며, 새 40줄 초과 함수는 먼저 책임 분리나 helper/payload 분리를 검토해야 한다.
- Boolean naming C1 게이트(`scripts/quality/check_boolean_naming.py`)는 `libs`, `apps`, `scripts`의 boolean 인자와 annotated field가 `is_`/`has_`/`can_`/`should_`/`include_`/`allow_` 같은 질문형 또는 명시적 상태명으로 읽히지 않으면 release gate를 차단한다. 현재는 함수 반환 bool 이름까지 완전 판정하지 않는 부분 강제 게이트다.
- Dict-any G4 게이트(`scripts/quality/check_dict_any_budget.py`)는 application 함수 시그니처의 `dict[str, Any]` 총량과 layer별 총량이 현재 baseline보다 늘어나면 release gate를 차단한다. 남은 `dict[str, Any]`는 model/TypedDict/dataclass로 줄여야 할 schema drift 부채로 추적한다.
- Router layer purity G1 게이트(`scripts/quality/check_router_layer_purity.py`)는 `apps/api`가 `core.*_repository`나 DB transaction/execute를 직접 호출하면 release gate를 차단한다.
- Query side-effect 게이트(`scripts/quality/check_query_side_effects.py`)는 `get_*`, `list_*`, `query_*`, `preview_*`, `inspect_*`, `lineage_*`, `find_*` public service method가 repository write, audit/outbox/lineage write, 파일 write, write adapter, mutation collaborator로 이어지면 release gate를 차단한다.
- Repository no-business G10 게이트(`scripts/quality/check_repository_no_business.py`)는 `libs/foundry_lite/infrastructure/repositories`가 도메인 에러를 import/raise하거나 savepoint가 아닌 transaction을 직접 commit/rollback하면 release gate를 차단한다.
- Tenant write guard S1 게이트(`scripts/quality/check_tenant_write_guard.py`)는 tenant-scoped SQLAlchemy insert/update/delete가 `tenant_id` values 또는 `tenant_id` where guard 없이 실행되는 것을 release gate에서 차단한다.
- Contract-test-per-port G13 게이트(`scripts/quality/check_contract_test_per_port.py`)는 `libs/foundry_lite/application/ports`의 모든 port 파일이 `tests/contracts/test_*_contract.py` 대응 suite를 갖도록 강제한다.
- Strategy/Specification testability D1 게이트(`scripts/quality/check_strategy_specification_tests.py`)는 filter evaluator, precondition evaluator, `*Strategy`, `*Specification` 규칙 모듈이 직접 unit/property test import 없이 추가되면 release gate를 차단한다.
- Integration scenario G15 게이트(`scripts/quality/check_integration_scenario_markers.py`)는 MVP release 필수 통합 시나리오 7개가 `@pytest.mark.integration_scenario(...)`로 모두 표시되어 있지 않으면 release gate를 차단한다.
- Root-cause patch prevention G21/G22 게이트(`scripts/quality/check_regression_test_per_bugfix.py`, `scripts/quality/check_pr_root_cause_section.py`)는 fix/bug/patch/regression 커밋이 같은 커밋의 `tests/` 변경 없이 들어오거나, PR 본문에 Root Cause, Impact, Regression Test 섹션이 없으면 release gate를 차단한다.
- Document drift G14 게이트(`scripts/quality/check_doc_drift.py`)는 `AGENTS.md`와 `docs/implementation-status.md`가 현재 구현처럼 언급한 source path, script, Python class/method가 실제 코드에 없으면 release gate를 차단한다. 미래 목표나 미구현 gap이라고 정직하게 적은 문장은 검사 대상에서 제외한다.
- Schema revision guard 게이트(`scripts/quality/check_schema_revision_guard.py`)는 `libs/foundry_lite/infrastructure/schema.py`의 SQLAlchemy metadata fingerprint가 `infra/schema_revisions/*.json` 최신 snapshot과 다르면 release gate를 차단한다. Alembic runtime은 아직 미구현이므로, DB 모양을 바꾸는 변경은 최소한 schema revision snapshot을 같은 변경에 포함해야 한다.
- Facade magic fallback은 AST-grep rule (`scripts/quality/ast-grep-rules/no-facade-magic-dispatch.yml`)이 차단한다. `FoundryLite`에 `__getattr__`/`__setattr__` method-registry dispatch를 다시 넣지 않는다.
- Secret/credential 노출은 `.gitleaks.toml`로 차단한다. 로컬에서는 gitleaks 미설치 시 경고만 가능하지만, CI/release evidence에서는 미설치가 곧 실패다. 새 false positive를 발견하면 allowlist에 사유와 함께 등록한다. 시크릿이 실제로 들어왔다면 즉시 revoke + git history 정리한다.
- Cross-function 데이터 흐름 (request → SQL, request body → service, mutation → audit, exception → request_id)은 CodeQL queries (`scripts/quality/codeql/queries/`)로 검사한다. CodeQL DB build는 Python 코드베이스 기준 fresh build 3~5분 + analyze 1~2분이므로 로컬 `pnpm ci:gate`에서는 돌리지 않는다. CI(`.github/workflows/codeql.yml`)가 push/PR마다 강제 실행하고, SARIF finding은 `scripts/quality/codeql/fail_on_sarif_findings.py`가 hard failure로 바꾼다. 새 violation 패턴을 발견하면 동일 디렉터리에 `.ql` 쿼리를 추가하고 `tests/unit/test_quality_codeql_queries.py`에 § 인용·@id·@kind 메타와 알려진 CodeQL API 호환성 검증을 추가한다.
- Audit-on-mutation G2 게이트(`scripts/quality/check_audit_on_mutation.py`)는 public application service mutation entrypoint가 repository write에 닿으면서 audit/outbox 경로가 없으면 release gate를 차단한다.
- Transaction outbox/audit pair G11 게이트(`scripts/quality/check_transaction_outbox_pair.py`)는 service transaction block 안에서 repository write가 일어날 때 같은 transaction call tree 안에 audit/outbox 증거가 없으면 release gate를 차단한다.
- Action idempotency G12 게이트(`scripts/quality/check_idempotency_on_action.py`)는 Action API의 `Idempotency-Key` header, Core/Service 전달, 기존 action_run 재사용 조회, action_runs unique key가 끊기면 release gate를 차단한다.
- Error response request-id G7 게이트(`scripts/quality/check_error_response_has_request_id.py`)는 FastAPI error detail 또는 `_handle_error` 호출에서 `request_id`가 빠지면 release gate를 차단한다.
- Log trace-key G5 게이트(`scripts/quality/check_log_has_trace_keys.py`)는 직접 logger 호출이나 `log_event` 호출이 `request_id`, `tenant_id`, run id 계열 추적 키 없이 남는 것을 release gate에서 차단한다.
- Metrics exposure 게이트(`scripts/quality/check_metrics_exposed.py`)는 가이드 §12.2의 dataset commit, transform run, action apply, object query, outbox lag, failed run, DLQ size 지표 7개가 Prometheus payload에 없으면 release gate를 차단한다.
- Runtime audit-count G17 게이트(`scripts/quality/check_audit_count_runtime.py`)는 supply-chain demo smoke 후 runtime DB의 high-level mutation 증거와 durable `audit_events` row 수가 정확히 일치하는지 검증한다.
- Outbox consistency G18 게이트(`scripts/quality/check_outbox_consistency.py`)는 supply-chain demo smoke 후 event-propagated state change와 durable `outbox_events` row가 event type, aggregate, idempotency key, correlation key 기준으로 1:1 또는 run-count 기준에 맞는지 검증한다.
- OpenLineage P8 동적 lineage 게이트(`scripts/quality/check_openlineage_dynamic_lineage.py`)는 supply-chain demo smoke 후 `transform_runs`와 `lineage_edges`가 dataset version 단위로 일치하는지 검증하고, 누락/중복/비-version/실패 run lineage를 차단하며 OpenLineage-compatible RunEvent artifact를 남긴다.
- MVP performance smoke 게이트(`scripts/quality/check_mvp_performance_smoke.py`)는 CSV ingest, object index, object query, no-writeback action apply 시간을 JSON 리포트로 남긴다. `ci` profile은 빠른 release gate 증거이고, 100k/1M profile은 릴리스 직전 장시간 측정용이다.
- Trace continuity G16 게이트(`scripts/quality/check_trace_continuity.py`)는 supply-chain demo를 in-memory OpenTelemetry provider 아래에서 실행해 synthetic request span, required service spans, SQLAlchemy DB spans가 같은 trace에 있고 service span이 같은 `request_id`를 들고 있는지 검증한다.
- Adapter error trace-key 게이트(`scripts/quality/check_adapter_error_trace_keys.py`)는 adapter 실패가 FAILED run의 error payload에 `tenant_id`, `actor_user_id`, `request_id`, `run_id`, `correlation_id`, `adapter`를 남기는지 동적으로 검증한다.
- Flaky detector G19 게이트(`scripts/quality/check_flaky_detector.py`)는 release gate에서 pytest-randomly + pytest-xdist 테스트 명령을 3회 반복해 한 번이라도 실패하거나 결과 요약이 달라지면 차단한다.
- Coverage exclusion G6 게이트(`scripts/quality/check_pragma_no_cover_budget.py`)는 `# pragma: no cover`를 baseline 0으로 차단한다. 예외가 필요하면 이유 주석과 함께 baseline/문서 갱신이 필요하다.
- Layer coverage G8 게이트(`scripts/quality/check_tier_coverage_by_layer.py`)는 coverage JSON을 domain/application/infrastructure/API/CLI/worker 계층별로 나누어 각 계층 95% 이상을 강제한다.
- 테스트 순서 의존성과 race condition은 pytest-randomly + pytest-xdist 두 plugin이 동적으로 강제한다. unit/contract test는 cwd에 의존하지 않는다. assets path는 conftest의 `DEMO_ROOT` 헬퍼를 쓰거나 `Path(__file__).resolve().parents[N]` 휴리스틱과 `mutants/` 부모 fallback을 같이 박는다.
- 안전 표현식, parser, 비즈니스 규칙 같은 작은 함수에는 hypothesis property test를 추가한다 (`tests/unit/test_*_properties.py`). 예시 기반 테스트가 못 잡는 엣지 케이스를 강제로 검증한다.

## 금지

- 엔지니어링 문서를 읽지 않고 구조 변경을 시작하지 않는다.
- god-class, god-service, fat-router를 만들지 않는다.
- `core._...` private facade helper 테스트를 만들지 않는다. facade는 public forwarder만 제공하고, 내부 실패 경계는 public API, service 소유 module, port/adapter contract test 중 가장 좁고 정직한 경계에서 검증한다.
- 문서에 아직 구현되지 않은 PostgreSQL JSONB, real CEL, real ERP writeback, Temporal, Alembic을 구현 완료처럼 쓰지 않는다.
- 테스트/CI 실패를 skip, xfail, flaky 처리로 우회하지 않는다.
