# Foundry-lite Quality Gate Roadmap

**작성일:** 2026-06-10
**문서 역할:** 엔지니어링 가이드 문서 → 정량 게이트 매핑의 단일 SSOT
**핵심 명제:** _문서가 철학이라면 게이트는 하네스다._ 모든 문서 조항은 정량 게이트로
번역 가능해야 하며, 게이트는 증상이 아니라 root cause를 차단해야 한다.

---

## 0. 게이트 설계 원칙

이 로드맵에서 새로 만들거나 강화하는 모든 게이트는 다음 6원칙을 따른다. 원칙 자체가
게이트의 1차 self-test 기준이다.

1. **정량 기준 1개**: violation 카운트 또는 baseline. 모호한 "code smell"은 게이트가 아니다.
2. **Root cause 차단**: 위반의 *개수*가 아니라 위반의 *코드 모양*을 잡는다.
   예) "router에 transaction.execute가 있다"는 사실 자체를 fail.
3. **Monotonic decrease 또는 0 baseline**: 새 violation을 추가할 수 없다.
4. **Self-test 동반**: 게이트가 fail해야 할 때 fail하는지 검증하는 단위 테스트가 같이 들어온다.
5. **JSON 리포트 산출**: `artifacts/quality/<gate>.json`. 트렌드 추적 가능.
6. **문서 매핑 주석**: 게이트 파일 docstring에 "Enforces guideline §X.Y" 명시. 문서 ↔ 게이트 양방향 추적성.
7. **Infra Ratchet 준수**: 새 production-style 인프라는 한 번에 하나씩 추가하고,
   정상/실패/동시성/재시도/부분 성공/복구/운영 증거가 CI와 문서에 고정되어야 다음
   인프라로 넘어간다.

---

## 1. 문서 조항 ↔ 게이트 매핑 (전수)

`foundry_lite_python_engineering_guidelines_ko.md` (이하 _가이드_)의 18개 섹션 중 핵심
조항 69개를 게이트와 매핑한다. 상태는 ✅ 강제, △ 부분, ❌ 미강제, ⏳ 미해당 (구현 전).

### §1 Python 환경과 기본 철학

| #   | 조항                                  | 게이트                                                                                                                                        | 정량                           | 상태 |
| --- | ------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------ | ---- |
| 1   | Python ≥ 3.12                         | `pyproject.toml` `requires-python`                                                                                                            | 정적                           | ✅   |
| 2   | ruff/mypy/pyright/pytest 사용         | `ci_gate.sh`                                                                                                                                  | pass/fail                      | ✅   |
| 3   | 함수 ≤ 40줄                           | `check_function_length.py`                                                                                                                    | max 40 + baseline 0            | ✅   |
| 4   | mutation은 transaction+audit+실패상태 | `check_audit_on_mutation.py` + `check_transaction_outbox_pair.py` + `check_audit_count_runtime.py` + `check_failed_mutation_state_runtime.py` | static + dynamic failure probe | ✅   |
| 5   | request_id/run_id 끊김 금지           | `check_trace_continuity.py` + `check_log_has_trace_keys.py`                                                                                   | static + dynamic               | ✅   |
| 6   | branch coverage ≥ 95%                 | `pytest --cov-branch`                                                                                                                         | 95                             | ✅   |
| 7   | 통합/스모크 100%                      | demo smoke + e2e                                                                                                                              | pass                           | ✅   |

### §2 Clean Code

| #   | 조항                            | 게이트                        | 정량                          | 상태 |
| --- | ------------------------------- | ----------------------------- | ----------------------------- | ---- |
| 8   | boolean naming (is*/has*/can\_) | `check_boolean_naming.py`     | bool arg/field violation 0    | △    |
| 9   | 함수 ≤ 40줄                     | `check_function_length.py`    | max 40 + baseline 0           | ✅   |
| 10  | guard clause 깊이               | Xenon B (간접)                | 복잡도 등급                   | △    |
| 11  | 조회/변경 부작용 분리           | `check_query_side_effects.py` | query side-effect violation 0 | ✅   |

### §3 SRP

| #   | 조항                                  | 게이트                            | 정량 | 상태 |
| --- | ------------------------------------- | --------------------------------- | ---- | ---- |
| 12  | API Router DB transaction 금지        | `check_router_layer_purity.py`    | 0건  | ✅   |
| 13  | Repository 비즈니스 판단 금지         | `check_repository_no_business.py` | 0건  | ✅   |
| 14  | Domain FastAPI/SQLAlchemy import 금지 | `check_dependency_graph`          | 0건  | ✅   |

### §4.1 의존성 방향

| #   | 조항                            | 게이트                                                   | 정량                  | 상태 |
| --- | ------------------------------- | -------------------------------------------------------- | --------------------- | ---- |
| 15  | domain → framework 0            | `check_dependency_graph` + `.importlinter` + `tach`      | 0건 + DAG pass        | ✅   |
| 16  | application → port만            | `check_infra_import_boundary` + `.importlinter` + `tach` | baseline 0 + DAG pass | ✅   |
| 17  | api → repository 직접 호출 금지 | `check_router_layer_purity.py`                           | 0건                   | ✅   |

### §4.2 디자인 패턴

| #   | 조항                            | 게이트                                  | 정량                         | 상태 |
| --- | ------------------------------- | --------------------------------------- | ---------------------------- | ---- |
| 18  | Facade 얇기                     | `check_application_module_size`         | ≤500줄                       | ✅   |
| 19  | required_dependencies 명시      | `check_service_dependencies`            | 선언/사용 일치               | ✅   |
| 20  | required_collaborators 명시     | `check_service_dependencies`            | 선언/사용 일치               | ✅   |
| 21  | 소유 service 명시 호출          | `check_service_call_graph`              | cycle 0, depth≤7, fan-out≤10 | ✅   |
| 22  | Strategy/Specification 테스트성 | `check_strategy_specification_tests.py` | missing direct test 0        | ✅   |
| 22.1 | 새 service/facade Mixin 성장 금지 | `check_no_service_mixin_growth.py`     | allowlist 밖 Mixin 0         | ✅   |
| 22.2 | Domain rule 직접 테스트성       | `check_domain_rule_tests.py`            | missing direct domain test 0 | ✅   |

### §4.3 Scale Foundation

| #    | 조항                                                               | 게이트                              | 정량                                                                    | 상태     |
| ---- | ------------------------------------------------------------------ | ----------------------------------- | ----------------------------------------------------------------------- | -------- |
| 23   | concrete SDK import 0                                              | `check_infra_import_boundary`       | 0건                                                                     | ✅       |
| 24   | adapter error에 trace key 유지                                     | `check_adapter_error_trace_keys.py` | FAILED run error trace key violation 0                                  | ✅       |
| 25   | fake/local adapter contract test 동일                              | contract tests                      | pass                                                                    | △ (수동) |
| 26   | trace key boundary 유지                                            | `check_trace_continuity.py`         | dynamic                                                                 | ✅       |
| 27   | 새 boundary에 contract test 동반                                   | `check_contract_test_per_port.py`   | 0개 누락                                                                | ✅       |
| 28   | adapter 실패 의미 표준화                                           | `check_adapter_failure_taxonomy.py` | 22 adapter profile                                                      | ✅       |
| 28.1 | 인프라는 한 번에 하나씩 실패/동시성/복구/조합 증거와 함께 추가     | `check_infra_ratchet.py`            | infra ratchet doc/CI/doc-sync violation 0                               | ✅       |
| 28.2 | MinIO/S3 storage ratchet                                           | `quality:s3-storage`                | S3 contract/failure/concurrency/retry/cleanup/footer/operator evidence 32 tests | ✅       |
| 28.3 | active 인프라 조합 ratchet                                         | `quality:infra-composition`         | S3+Iceberg+Spark end-to-end + failure-abort tests                       | ✅       |
| 28.4 | tricky checklist 완료 체크가 실제 테스트 증거와 일치               | `quality:checklist-evidence`        | checked `test_*` references missing from pytest collection 0            | ✅       |
| 28.5 | active 인프라가 관련 tricky 항목을 자동으로 proof/test/CI에 끌어옴 | `quality:infra-tricky-matrix`       | matrix item/proof/test/CI violations 0                                  | ✅       |

### §5 코드 컨벤션

| #   | 조항                        | 게이트                                      | 정량                                     | 상태 |
| --- | --------------------------- | ------------------------------------------- | ---------------------------------------- | ---- |
| 28  | `Any` boundary 외 금지      | `check_application_any_budget.py` + pyright | application/API/CLI/worker broad `Any` 0 | ✅   |
| 29  | `dict[str, Any]` 대신 model | `check_dict_any_budget.py`                  | signature baseline 0 + no growth         | ✅   |

### §6 API

| #   | 조항                        | 게이트                                   | 정량     | 상태 |
| --- | --------------------------- | ---------------------------------------- | -------- | ---- |
| 30  | mutation에 idempotency key  | `check_idempotency_on_action.py`         | 0건 누락 | ✅   |
| 31  | error response에 request_id | `check_error_response_has_request_id.py` | 0건 누락 | ✅   |

### §7 트랜잭션

| #    | 조항                                                       | 게이트                                                             | 정량                                    | 상태 |
| ---- | ---------------------------------------------------------- | ------------------------------------------------------------------ | --------------------------------------- | ---- |
| 32   | Repository 임의 commit 금지                                | `check_repository_no_business.py`                                  | 0건                                     | ✅   |
| 33   | outbox와 state change 같은 transaction                     | `check_transaction_outbox_pair.py` + `check_outbox_consistency.py` | static + dynamic                        | ✅   |
| 34   | COMMITTED dataset version immutable                        | DB schema (정적)                                                   | —                                       | △    |
| 34.1 | 성공 transform lineage는 input/output dataset version 단위 | `check_openlineage_dynamic_lineage.py`                             | violation 0 + OpenLineage RunEvent 산출 | ✅   |

### §8 에러 처리

| #   | 조항                             | 게이트                        | 정량   | 상태 |
| --- | -------------------------------- | ----------------------------- | ------ | ---- |
| 35  | broad `except Exception` 금지    | ruff `BLE001` (부분)          | 정적   | △    |
| 36  | `raise X from exc`               | ruff `B904`                   | 정적   | ✅   |
| 37  | secret/SQL/stack trace 노출 금지 | Bandit (부분)                 | 정적   | △    |
| 38  | 로그에 request_id 포함           | `check_log_has_trace_keys.py` | static | ✅   |

### §10 보안

| #   | 조항                             | 게이트                                                        | 정량                             | 상태 |
| --- | -------------------------------- | ------------------------------------------------------------- | -------------------------------- | ---- |
| 41  | `eval`/`exec` 금지               | Bandit                                                        | 0건                              | ✅   |
| 42  | raw SQL interpolation 금지       | Bandit                                                        | 0건                              | ✅   |
| 43  | secret hardcoding 금지           | Bandit                                                        | 0건                              | ✅   |
| 44  | tenant_id 없는 도메인 write 금지 | `check_tenant_write_guard.py`                                 | insert/update/delete violation 0 | ✅   |
| 45  | audit 없는 mutation 금지         | `check_audit_on_mutation.py` + `check_audit_count_runtime.py` | static + dynamic                 | ✅   |

### §11 테스트

| #   | 조항                                         | 게이트                                                            | 정량                         | 상태 |
| --- | -------------------------------------------- | ----------------------------------------------------------------- | ---------------------------- | ---- |
| 46  | test\_\*.py 명명                             | pytest 디스커버리                                                 | 정적                         | ✅   |
| 47  | flaky test pass 금지                         | `check_flaky_detector.py`                                         | 3회 반복 결과 변동 0         | ✅   |
| 48  | line 95%                                     | `pytest --cov`                                                    | 95                           | ✅   |
| 49  | branch 95%                                   | `pytest --cov-branch`                                             | 95                           | ✅   |
| 50  | function 95%                                 | `check_public_api_coverage`                                       | 95                           | ✅   |
| 51  | 영역별 (domain/app/infra/api/cli/worker) 95% | `check_tier_coverage_by_layer.py`                                 | 각 계층 95                   | ✅   |
| 52  | 통합 시나리오 7개 100%                       | `check_integration_scenario_markers.py` + marked pytest scenarios | 7/7                          | ✅   |
| 53  | `pragma: no cover` 남발 금지                 | `check_pragma_no_cover_budget.py`                                 | baseline 0 + reason required | ✅   |

### §12 관측성

| #   | 조항                               | 게이트                        | 정량                | 상태 |
| --- | ---------------------------------- | ----------------------------- | ------------------- | ---- |
| 54  | 로그에 request_id/tenant_id/run_id | `check_log_has_trace_keys.py` | static              | ✅   |
| 55  | 메트릭 7개 노출                    | `check_metrics_exposed.py`    | 7개 required metric | ✅   |

### §16 CI

| #    | 조항                                      | 게이트                          | 정량                    | 상태 |
| ---- | ----------------------------------------- | ------------------------------- | ----------------------- | ---- |
| 55.1 | PR required check wall time              | `quality-pr-fast`               | workflow timeout 5분    | ✅   |
| 56   | skip/flaky/xfail 우회 금지                | `check_no_test_bypasses`        | 0                       | ✅   |
| 57   | private test reference 부채               | `check_private_test_references` | 0                       | ✅   |

### §18 안티패턴

| #   | 조항                                  | 게이트                                                                   | 정량                                         | 상태 |
| --- | ------------------------------------- | ------------------------------------------------------------------------ | -------------------------------------------- | ---- |
| 58  | 증상 제거 패치 금지                   | `check_regression_test_per_bugfix.py` + `check_pr_root_cause_section.py` | bugfix missing test 0 + PR section missing 0 | ✅   |
| 59  | `except Exception: pass` 금지         | ruff `E722`+`BLE001` (부분)                                              | 정적                                         | △    |
| 60  | `sleep`/magic으로 race condition 덮기 | `check_no_test_sleep`                                                    | tests 0                                      | ✅   |
| 61  | `dict[str, Any]` 우회                 | `check_dict_any_budget.py`                                               | signature baseline 0 + no growth             | ✅   |
| 62  | migration 없이 DB 모양 가정           | `check_schema_revision_guard.py`                                         | schema fingerprint mismatch 0                | ✅   |
| 63  | Fat Router                            | `check_router_layer_purity.py`                                           | direct DB/repository 0건                     | △    |
| 64  | God Service                           | `check_service_call_graph`                                               | fan-out ≤10                                  | ✅   |
| 65  | Silent Failure                        | `check_audit_count_runtime.py`                                           | dynamic audit                                | ✅   |
| 66  | Log-only Audit                        | `check_audit_on_mutation.py` + `check_audit_count_runtime.py`            | static + dynamic                             | ✅   |
| 67  | Magic fallback                        | AST-grep `foundry-lite-no-facade-magic-dispatch`                         | 0건                                          | ✅   |

### 매핑 점수 요약

| 상태                | 개수   | 비율     |
| ------------------- | ------ | -------- |
| ✅ 강제             | 57     | 83%      |
| △ 부분              | 12     | 17%      |
| ❌ 미강제           | 0      | 0%       |
| ⏳ 미해당 (구현 전) | 0      | 0%       |
| **합계**            | **69** | **100%** |

**현재 게이트는 문서 약속의 약 83%를 정량적으로 완전 강제하고, 나머지 17%는
부분 강제한다.** 직접 미강제(❌) 조항은 0개이며, 다음 목표는 △ 부분 강제 항목을
실제 구현·정밀 게이트로 줄이는 것이다.

---

## 2. 신규 게이트 — Tier 분류

### Tier C1 — boolean naming clarity (△ 부분 완료 2026-06-11)

`scripts/quality/check_boolean_naming.py`는 `libs`, `apps`, `scripts`의 Python 파일을
AST로 읽고 boolean 인자와 annotated field 이름이 질문형 또는 명시적 상태명처럼 읽히는지
검사한다. 비개발자 관점으로 말하면, `retry=True`처럼 뜻이 애매한 스위치가 늘어나는 것을
막고 `include_items=True`, `has_permission=True`처럼 켜면 무엇이 달라지는지 이름만 봐도
알 수 있게 하는 게이트다.

검사 기준:

- boolean annotation이 붙은 function argument와 `AnnAssign` field/local 이름을 검사한다.
- `is_`, `has_`, `can_`, `should_`, `include_`, `allow_`, `enable_`, `require_`,
  `skip_`, `use_`, `simulate_`, `confirm_` prefix를 허용한다.
- `enabled`, `disabled`, `deleted`, `nullable`, `indexed`, `searchable`, `editable`,
  `fresh`, `gate_pass` 같은 명시적 상태명은 허용한다.
- 결과는 `artifacts/quality/boolean_naming.json`에 남긴다.

Self-test: `tests/unit/test_quality_boolean_naming.py`가 불명확한 bool 이름 실패,
질문형/상태형 이름 허용, optional bool annotation 처리, 현재 repo 0 violation,
JSON report 생성을 검증한다.

현재 보장 범위: 이 게이트는 새 boolean 인자/field debt를 차단하지만,
`def matches_filter(...) -> bool` 같은 boolean 반환 함수 이름 전체를 의미적으로 판정하지는
않는다. 그래서 §2.1은 ✅가 아니라 △ 부분 강제로 기록한다.

### Tier G1 — API router layer purity (✅ 완료 2026-06-11 G1)

`scripts/quality/check_router_layer_purity.py`는 `apps/api/**/*.py`를 AST로 읽고 API
router가 application service/facade 경계를 건너뛰어 DB나 repository에 직접 접근하는지
검사한다.

검사 기준:

- `core.*_repository.*` 직접 호출은 fail한다.
- `*.engine.begin()` 직접 transaction open은 fail한다.
- `conn.execute(...)`, `transaction.execute(...)`, `session.execute(...)` 같은 router 내부
  DB statement 실행은 fail한다.
- 결과는 `artifacts/quality/router_layer_purity.json`에 남긴다.

Self-test: `tests/unit/test_quality_router_layer_purity.py`가 core repository 직접 호출,
direct transaction/execute 실패, application facade 호출 허용, JSON report 생성을 검증한다.
이 게이트는 Fat Router 전체를 완전히 판정하지는 않지만, router가 DB/repository를 직접
소유하는 가장 위험한 root cause를 차단한다.

### Tier G1A — query side-effect boundary (✅ 완료 2026-06-11)

`scripts/quality/check_query_side_effects.py`는 application service의 public read/query
method를 AST로 읽고, 조회처럼 보이는 method가 상태 변경으로 이어지는지 검사한다.
비개발자 관점으로 말하면, “보기만 했는데 장부가 바뀌는” 사고를 막는 게이트다.

검사 기준:

- `get_*`, `list_*`, `query_*`, `preview_*`, `inspect_*`, `lineage_*`, `find_*`
  public service method가 대상이다.
- 대상 method의 reachable call tree 안에 repository write가 있으면 fail한다.
- audit/outbox/lineage write, 파일 write, write-oriented adapter 호출이 있으면 fail한다.
- public mutation collaborator(`apply_*`, `create_*`, `run_*` 등)로 이어지면 fail한다.
- 결과는 `artifacts/quality/query_side_effects.json`에 남긴다.

Self-test: `tests/unit/test_quality_query_side_effects.py`가 read-only repository 호출 허용,
repository write 실패, helper를 통한 audit write 실패, file write 실패, mutation
collaborator 실패, write-mode `open()` 실패, JSON report 생성을 검증한다. 실제 코드에서는
조회 method가 permission-deny audit row를 쓰던 경로를 제거했고,
`tests/unit/test_helpers_and_query.py::test_permission_deny_on_dataset_read_does_not_write_audit`로
그 회귀를 고정했다.

### Tier G10 — repository no-business boundary (✅ 완료 2026-06-11 G10)

`scripts/quality/check_repository_no_business.py`는
`libs/foundry_lite/infrastructure/repositories/**/*.py`를 AST로 읽고 repository가
application/domain이 맡아야 할 판단을 직접 소유하는지 검사한다.

검사 기준:

- `foundry_lite.domain.errors`의 `ValidationFailed`, `PermissionDenied`,
  `ConflictDetected`, `NotFound`, `InvariantViolation`, `ExternalSystemError`,
  `FoundryLiteError` import는 fail한다.
- repository 내부에서 위 도메인 에러를 직접 `raise`하면 fail한다.
- `savepoint`가 아닌 receiver의 `.commit()`/`.rollback()` 직접 호출은 fail한다.
- 결과는 `artifacts/quality/repository_no_business.json`에 남긴다.

Self-test: `tests/unit/test_quality_repository_no_business.py`가 domain error import/raise
실패, direct commit/rollback 실패, port-level error와 savepoint commit/rollback 허용,
JSON report 생성을 검증한다.

### Tier G11 — transaction outbox/audit pair (✅ 정적 완료 2026-06-11 G11)

`scripts/quality/check_transaction_outbox_pair.py`는 application service의
`with self.engine.begin() as transaction:` block을 AST로 읽고, 그 transaction handle을
사용하는 repository write가 같은 transaction call tree 안에서 audit/outbox 증거를
남기는지 검사한다. 비개발자 관점으로 말하면, 업무 장부의 상태 변경과 그 변경을
나중에 추적·재처리할 수 있는 영수증이 같은 봉투에 들어 있는지 확인하는 게이트다.

검사 기준:

- 대상은 `libs/foundry_lite/application/services/**/*.py`의 public mutation method다.
- mutation method prefix는 `create_`, `ensure_`, `upload_`, `register_`, `run_`,
  `apply_`, `materialize`, `index_`, `cleanup_`, `commit_`, `abort_`다.
- `self.engine.begin()` transaction block 안에서 transaction handle을 받는 repository
  write가 있으면 같은 call tree 안에 `_audit`, `_outbox`, `insert_audit_event`,
  `insert_outbox_event` 중 하나가 있어야 한다.
- 단순 lifecycle 준비 write인 `create_open_transaction`, `insert_sync_run`,
  `insert_transform_run`, `insert_materialization_run`, `create_index_run` 등은 allowlist로
  분리한다. 이들은 최종 상태 변경 proof가 아니라 run/transaction 시작 기록이다.
- 결과는 `artifacts/quality/transaction_outbox_pair.json`에 남긴다.

Self-test: `tests/unit/test_quality_transaction_outbox_pair.py`가 transaction write without
audit/outbox 실패, 같은 transaction의 direct audit 허용, delegated outbox helper 허용,
transaction 밖 audit 실패, preparatory lifecycle write 허용, JSON report 생성을 검증한다.

현재 보장 범위: G11은 정적 게이트다. demo smoke 후 실제 state change와 durable
`outbox_events` row가 일치하는지 보는 동적 검증은 G18이 담당한다. `tenant_id` 없는
도메인 write를 직접 탐지하는 게이트는 아직 별도 future gate로 남아 있다.

### Tier G12 — action idempotency contract (✅ 완료 2026-06-11 G12)

`scripts/quality/check_idempotency_on_action.py`는 Action apply mutation의 retry/replay
계약이 HTTP boundary에서 schema까지 끊기지 않는지 AST로 검사한다. 비개발자 관점으로
말하면, 같은 버튼 클릭이나 네트워크 재시도가 같은 업무 변경을 두 번 만들지 못하도록
하는 안전장치다.

검사 기준:

- `apps/api/foundry_lite_api/main.py`의 `apply_action` endpoint는
  `Idempotency-Key` header를 받아야 한다.
- API handler는 `core.apply_action(..., idempotency_key=...)`로 key를 전달해야 한다.
- `FoundryLite.apply_action`과 `ActionService.apply_action`은 `idempotency_key`를
  required keyword argument로 받아야 한다.
- `ActionService.apply_action`은 빈 key를 `ValidationFailed`로 거부해야 한다.
- 새 `action_run` insert 전에 기존 action_run을 idempotency key로 조회하고,
  있으면 replay response를 반환해야 한다.
- 기존 action_run replay는 저장된 `request_fingerprint`가 현재 canonical request와
  같을 때만 허용해야 한다.
- same-key/different-request reuse는 `idempotency key conflict`와
  `action.run.idempotency_conflict` audit evidence로 거부해야 한다.
- `ActionRunRow`와 `ActionRunRecord`에는 `request_fingerprint`가 포함되어야 한다.
- `ActionRunRecord`에는 idempotency key와 request fingerprint가 저장되어야 한다.
- `ActionRepository`는 `action_run_by_idempotency(..., idempotency_key=...)` lookup을
  제공해야 한다.
- `action_runs` schema에는 `tenant_id`, `action_type_id`, `actor_user_id`,
  `idempotency_key` unique constraint가 있어야 한다.
- `action_runs` schema에는 `request_fingerprint` column이 있어야 한다.
- 결과는 `artifacts/quality/action_idempotency.json`에 남긴다.

Self-test: `tests/unit/test_quality_action_idempotency.py`가 완전한 계약 통과,
API header 누락 실패, 기존 action_run lookup이 insert 뒤로 밀린 경우 실패,
request fingerprint guard/storage/schema 누락 실패, schema unique constraint 누락 실패,
JSON report 생성을 검증한다.

### Tier G13 — contract test per port (✅ 완료 2026-06-11 G13)

`scripts/quality/check_contract_test_per_port.py`는
`libs/foundry_lite/application/ports/*.py`와 `tests/contracts/test_*_contract.py`를
대조해 port/interface가 contract test 없이 추가되는지 검사한다.

검사 기준:

- `ports/__init__.py`를 제외한 모든 port 파일은 대응 contract test를 가져야 한다.
- 기본 대응 이름은 `test_{port_file_stem}_contract.py`다.
- 기존 이름 관례 때문에 `dataset_storage.py`는
  `test_dataset_storage_adapter_contract.py`도 허용한다.
- `runtime_repository_types.py`는 `runtime_repository.py` port의 타입 분리 파일이므로
  `test_runtime_repository_contract.py`가 커버한다.
- 결과는 `artifacts/quality/contract_test_per_port.json`에 남긴다.

Self-test: `tests/unit/test_quality_contract_test_per_port.py`가 missing contract 실패,
matching contract 허용, `dataset_storage`/`runtime_repository_types` alias 허용, JSON report 생성을 검증한다.
누락되어 있던 `MetadataRepository`에는
`tests/contracts/test_metadata_repository_contract.py`를 추가했다.

### Tier D1 — strategy/specification testability (✅ 완료 2026-06-11)

`scripts/quality/check_strategy_specification_tests.py`는
`libs/foundry_lite/application`에서 filter evaluator, precondition evaluator,
`*Strategy`, `*Specification`처럼 조건 판단 규칙을 담당하는 모듈을 찾고, 그 모듈이
직접 unit/property test import를 갖는지 검사한다.

검사 기준:

- `matches_*`, `evaluate_*`, `validate_*`, `precondition_*` public function은 규칙
  함수 후보로 본다.
- `*Strategy`, `*Specification`, `*Evaluator` type alias/class와
  `*OPERATIONS`/`*EVALUATORS` registry는 교체 가능한 전략 후보로 본다.
- 후보 모듈은 `tests/test_*.py`에서 모듈 자체 또는 public 규칙 symbol을 직접
  import해야 한다.
- `FoundryLite`나 API 서버를 통해 간접으로만 검증된 경우는 통과로 보지 않는다.
- 결과는 `artifacts/quality/strategy_specification_tests.json`에 남긴다.

Self-test: `tests/unit/test_quality_strategy_specification_tests.py`가 missing direct test
실패, 직접 module import 허용, parent package symbol import 허용, 일반 helper 제외,
JSON report 생성을 검증한다. 기존 object query filter 규칙은
`tests/unit/test_query_filters.py`에서 `matches_filter`를 직접 검증한다.

### Tier G15 — required integration scenario markers (✅ 완료 2026-06-11 G15)

`scripts/quality/check_integration_scenario_markers.py`는 Python 엔지니어링 가이드 §11.4의
MVP release 필수 통합 흐름을 pytest marker로 고정한다. 단일 demo smoke 하나가 넓게 성공했다는
사실만으로 7개 시나리오가 모두 검증됐다고 착각하는 문제를 차단한다.

Required scenarios:

- `connector_sync`: connector sync → raw dataset commit
- `transform_clean_dataset`: raw dataset → DuckDB transform → clean dataset commit
- `ontology_index`: ontology import → object index
- `object_action_audit`: object query → action apply → object edit/outbox/audit
- `materialization_downstream_transform`: action log/object snapshot materialization → downstream transform
- `permission_tenant_isolation`: permission denied와 tenant isolation
- `failed_run_replay_or_dlq`: failed run → retry/replay 또는 DLQ

Additional tracked scenario:

- `closed_loop_repeatability`: one-command closed-loop demo repeated twice from the same checkout
- `media-ocr`: real Tesseract OCR live — raw image → `ocr_v1` derivative → content index → search, plus a FAILED `media_processing_runs` row as operator-evidence (`pnpm quality:media-live-ocr`)

실행:

- `@pytest.mark.integration_scenario("<scenario>")` marker가 7개 required scenario를 모두
  포함해야 한다.
- 알 수 없는 scenario 이름이나 잘못된 marker shape는 fail한다.
- 결과는 `artifacts/quality/integration_scenario_markers.json`에 남긴다.
- package script: `pnpm quality:integration-scenarios`
- release/CI gate: `pnpm ci:gate:static`; full local rehearsal remains `pnpm ci:gate:all`

Self-test: `tests/unit/test_quality_integration_scenario_markers.py`가 7/7 정상 통과,
missing scenario 실패, unknown scenario 실패, invalid marker 실패, JSON report 생성을
검증한다.

현재 보장 범위: 시나리오 marker 존재를 정적으로 강제하고, marker가 붙은 통합 테스트는
raw commit, clean transform lineage, object action/outbox/audit, materialization downstream
transform, permission/tenant isolation, failed-run replay 증거를 직접 assert한다. 실제
실행/통과 여부는 pytest coverage run과 flaky detector가 함께 담당한다. 그래서 §11.4 #52는
7개 required scenario가 모두 release gate 안에 들어온 상태다.

### Tier G14 — current-state document drift (✅ 완료 2026-06-11 G14)

`scripts/quality/check_doc_drift.py`는 repo Markdown 문서와 `docs/*.json` 문서를 읽어
현재 구현처럼 언급된 source path, script, package script, FastAPI route, pytest node id가
실제 코드/테스트 트리에 존재하는지 검증하고, Markdown local link target과 heading/html anchor가
실제 문서에서 클릭 가능한지도 검증한다. Python class와 명시적 `Class.method` reference 검증은
current-state boundary 문서인 `AGENTS.md`와 `docs/implementation-status.md`에만 적용한다.
비개발자 관점으로 말하면, 전체 문서의 "파일/테스트/API 주소/실행 명령"은 CI가 확인하고,
Python 내부 심볼 검사는 과장 위험이 가장 큰 현재 구현 문서에서 더 엄격하게 확인하며, 문서
링크는 독자가 실제로 눌러 이동할 수 있어야 한다는 방식이다.

검사 기준:

- inline code span의 source path/script reference는 현재 repo에 존재해야 한다.
- 현재 명령처럼 적힌 `quality:*`, `ci:*`, `db:*`, `sdk:*` 등 package script reference와
  `pnpm --silent ...` command는 `package.json` scripts에 존재해야 한다. 제안 명령 섹션이나
  후속/future 문맥의 command family 예시는 제외한다.
- `/api/...`, `/healthz`, `/metrics` route reference는 FastAPI route inventory에 존재해야 한다.
- `tests/...::test_name` pytest node id는 해당 test file과 test function에 존재해야 한다.
- `[label](relative.md#anchor)` Markdown link는 target file과 heading/html anchor가 존재해야 한다.
- `FoundryLite`, `CoreDependencies` 같은 Python class reference는 current-state boundary 문서에서만
  AST symbol index에 존재해야 한다.
- `Class.method` reference는 current-state boundary 문서에서만 해당 class와 method가 모두 존재해야 한다.
- `아직`, `not implemented`, `remain unextracted`, `removed`, `금지`처럼 미래 목표,
  미구현 gap, 제거/금지 상태를 정직하게 말하는 문장은 검사 대상에서 제외한다.
- 결과는 `artifacts/quality/doc_drift.json`에 남긴다.

Self-test: `tests/unit/test_quality_doc_drift.py`가 missing script reference 실패,
missing package script 실패, package script wildcard/미래 문맥 제외, missing Python symbol 실패,
기존 path/symbol/method 허용, non-current 문서의 ontology 예시 심볼 허용, FastAPI route 존재/누락,
pytest node id 존재/누락, Markdown target/anchor 존재/누락, 미래·제거 문맥 제외, JSON report 생성을
검증한다. 이 게이트는 가이드 69개 조항의 직접
mapping 카운트에는 넣지 않는 meta gate지만, 문서가 현재 코드보다 앞서 “완료”처럼 말하거나
예전 파일/API/test 이름 또는 깨진 문서 링크를 남기는 root cause를 차단한다.

### Tier G14A — evidence ledger command references (✅ 완료 2026-06-19)

`scripts/quality/check_evidence_ledger_commands.py`는 `docs/sprint-evidence-ledger.md`의 proof
command가 실제 repo 안의 package script, Python/Node/bash script file, test path, pytest node
id를 가리키는지 검증한다. 비개발자 관점으로 말하면, evidence ledger가 “이 명령으로 증명했다”고
말하는데 그 명령이 더 이상 존재하지 않는 상황을 CI가 막는 것이다.

검사 기준:

- `pnpm --silent quality:*` 또는 `pnpm ci:*` command는 `package.json` scripts에 존재해야 한다.
- `uv run python ...`, `node ...`, `bash ...` command가 가리키는 script file은 repo에 존재해야 한다.
- `tests/...` path는 repo에 존재해야 하고, `tests/...::test_name` node id는 해당 test file AST에서
  collectable해야 한다.
- 결과는 `artifacts/quality/evidence_ledger_commands.json`에 남긴다.

Self-test: `tests/unit/test_quality_evidence_ledger_commands.py`가 현재 repo 통과, missing package
script 실패, missing Python script 실패, missing pytest node 실패, JSON report 생성을 검증한다.
`tests/unit/test_quality_ci_workflows.py`는 이 게이트가 doc-drift 이후, documentation-map 이전에
static/release lane에서 실행됨을 검증한다.

### Tier G14B — documentation operating map coverage (✅ 완료 2026-06-19)

`scripts/quality/check_documentation_map.py`는 `docs/documentation-map.md`를 문서 운영
source-of-truth로 검증한다. 비개발자 관점으로 말하면, 문서 지도 자체가 낡으면 README,
implementation status, evidence ledger, data-pattern matrix, infra matrix, frontend/backend
contract, commit-point risk register가 서로 다른 말을 하기 시작하므로, 이제 그 안내판도
CI가 검사한다.

검사 기준:

- repo 안의 Markdown 문서와 `docs/*.json` 운영 문서는 `Document Roles`에 등록되어야 하고,
  `check_doc_drift.py`의 scan inventory와 일치해야 한다.
- 각 `Document Roles` 행은 `entrypoint`, `source-of-truth`, `machine-registry`, `risk-registry`,
  `standard`, `template`, `reference`, `example` 중 하나의 MECE bucket으로
  분류되어야 한다. 비개발자식으로 말하면, README/원본 장부/기계 registry/위험 장부/예제/진단
  문서가 서로 같은 일을 하며 겹치지 않도록 CI가 문서의 "직업"을 검사한다.
- `MECE Documentation Taxonomy` 표에는 위 bucket들이 모두 정의되어야 하고, duplicate/stale bucket이나
  placeholder 의미/병합 규칙은 실패한다. 즉 문서 분류표 자체도 오래된 안내판이 될 수 없다.
- core source-of-truth 문서는 `Source Of Truth Rules`에 update trigger와 함께 등록되어야 하고,
  retired/stale source-of-truth row나 duplicate row는 없어야 한다.
- README를 제외한 core source-of-truth Markdown 문서는 상단 24줄 안에 `Status`, `Purpose`,
  `Scope`, `Audience`, `작성일`, `문서 상태`, `문서 역할` 같은 운영 맥락 marker가 있어야 한다.
  비개발자가 문서를 열었을 때 "이 문서는 현재 무엇을 책임지는가"를 바로 알 수 있어야 한다.
- `Source Of Truth Rules`와 `Document Roles`의 설명 칸은 비어 있거나 `role`/`note`/`governs`
  같은 placeholder 수준이면 안 된다. 비개발자가 봐도 "이 문서가 무엇을 책임지고 언제 고쳐야
  하는지"를 알 수 있어야 한다.
- `Update Order`에는 evidence ledger, implementation status, data-platform sprint plan/sprint plan,
  sprint breakdown, frontend/infra/quality/observability contract docs, and `README.md`가 있어야 한다.
  `README.md`는 원본 장부가 아니므로 이 목록의 마지막이어야 한다.
- README의 `문서 지도`에는 implementation status, documentation map, sprint evidence ledger,
  MVP scope, infra/source-of-truth matrices, frontend/backend contract, data-platform plans,
  quality docs, commit-point register 같은 핵심 진입점이 있어야 한다. 각 행의 역할 설명은
  `role` 같은 placeholder가 아니라 GitHub 독자가 왜 그 문서를 눌러야 하는지 알 수 있는
  설명이어야 하고, 같은 문서 링크가 중복되면 실패한다.
- README의 대표 gate briefing에는 active documentation/API/SDK surface gate인
  `check_documentation_map.py`, `check_frontend_backend_surface.py`, `quality:proof-matrix`,
  `quality:source-of-truth`, `quality:operator-evidence`, `quality:frontend-backend-surface`,
  `quality:sdk-request-contract`, `quality:frontend-foundation`이 `대표 gate` 표 안에 보여야 한다.
  각 행은 어떤 root-cause 위험을 막는지 설명해야 하며, 같은 gate reference가 중복되면 실패한다.
- `Cross-Check Commands`의 locked checklist에는 필수 command가 모두 있고, duplicate/stale command가
  없어야 한다. `pnpm` command는 실제 `package.json` script로 존재해야 하고, `node` command는 실제 파일을 가리켜야 한다.
- `AGENTS.md`에는 active documentation gate인 `check_doc_drift.py`와
  `check_documentation_map.py`뿐 아니라 proof matrix/source-of-truth/operator-evidence
  contract gate가 요약되어 있어야 한다.
- 결과는 `artifacts/quality/documentation_map.json`에 남긴다.

Self-test: `tests/unit/test_quality_documentation_map.py`가 현재 repo 통과, 누락 문서 role
실패, stale role 실패, MECE taxonomy 정의 누락/stale/thin row 실패, 누락/unknown document-role bucket 실패, thin role/source-of-truth row 실패, core 운영 문서 상단 context 누락 실패,
update-order reference 누락/README 순서 실패, doc-drift inventory mismatch 실패, source-of-truth rule 누락/stale/duplicate 실패, README source-of-truth 링크 누락/중복/placeholder 설명 실패,
README 대표 gate 표 누락/표 밖 reference/중복/placeholder 설명 실패, proof-matrix/source-of-truth/operator-evidence cross-check 누락 실패,
cross-check command 누락/중복/stale 실패, package script 누락 실패,
node target 누락 실패, AGENTS documentation/proof gate reference 누락 실패,
JSON report 생성을 검증한다. `tests/unit/test_quality_ci_workflows.py`는 이 게이트가
`check_doc_drift.py` 이후, tricky checklist evidence 이전에 static/release lane에서 실행됨을
검증한다.

### Tier G14C — data-platform sprint status consistency (✅ 완료 2026-06-19)

`scripts/quality/check_data_platform_sprint_status.py`는 S46-S64 data-platform expansion 상태가
detailed sprint plan, main sprint breakdown, README, implementation status에서 같은
경계를 말하는지 검증한다. 비개발자 관점으로 말하면, 어떤 문서는 "S63까지 부분 구현"이라고
하고 다른 문서는 "S64까지 완료"라고 말하는 상황을 CI가 막는 것이다.

검사 기준:

- `docs/data-platform-expansion-sprint-plan-ko.md`의 S46-S64 table은 expected token을 유지해야 한다.
- `foundry_lite_sprint_breakdown_ko.md`의 S46-S64 table은 expected token과 label을 함께 유지해야 한다.
- README, detailed sprint plan, implementation status는 S46 complete, S47-S64 partial
  boundary를 드러내는 high-level phrase를 유지해야 한다.
- 결과는 `artifacts/quality/data_platform_sprint_status.json`에 남긴다.

Self-test: `tests/unit/test_quality_data_platform_sprint_status.py`가 현재 repo 통과, token drift
실패, label drift 실패, high-level boundary phrase 누락 실패, JSON report 생성을 검증한다.
`tests/unit/test_quality_ci_workflows.py`는 이 게이트가 data-pattern matrix 이후, SDK generation
이전에 static/release lane에서 실행됨을 검증한다.

### Tier G14D — Pipeline Builder public-behavior parity matrix (✅ current async-DAG gate 2026-07-29)

`scripts/quality/check_pipeline_parity_matrix.py`는
`docs/pipeline-builder-parity-matrix.json`이 Palantir 공식 공개 문서의 동작과 현재 checkout의
코드·테스트·운영 증거를 과장 없이 연결하는지 검증한다. 비개발자식으로 말하면, Graph v2
타입이나 DB 테이블이 생겼다는 이유만으로 "멀티모달 Pipeline Builder가 완성됐다"고 부르는
문제를 막고, 실제 사용자 폐루프가 없는 항목은 `foundation` 또는 `planned`로 남기는
안전장치다.

검사 기준:

- status vocabulary는 `current`, `foundation`, `planned`로 고정한다.
- 외부 근거는 `https://www.palantir.com/docs/foundry/...` 공식 문서만 허용한다.
- MMDP, typed Graph v2, named artifact port, descriptor catalog, execution evidence,
  media processor registry, no-commit preview, async DAG, 다중 output, Document Intelligence,
  Ontology/AIP handoff, 협업, Python isolation, streaming/geospatial 핵심 capability row가
  모두 존재해야 한다.
- `current`와 `foundation`은 실제 implementation path와 collect 가능한 pytest proof를
  가져야 한다.
- API, SDK, UI surface를 연결한 row는 그 repo path가 실제로 존재해야 한다.
- `current`는 사용자가 확인할 durable/browser/operator evidence를 가져야 한다.
- `foundation`과 `planned`는 남은 gap을 명시해야 한다.
- `planned`는 구현 evidence를 current처럼 달 수 없다.
- streaming 구현 제약은 Kafka, CDC, WebSocket, checkpoint, lease, fencing을 모두 명시해야 한다.
- 활성 `libs`, `apps`, 핵심 Pipeline Builder 계획·상태·ADR 문서에는 선택 범위 밖 streaming engine이 다시 들어오면 안 된다.
- 모든 row는 1~12 rollout phase와 완료 판정 문장을 가진다.
- 결과는 `artifacts/quality/pipeline_builder_parity_matrix.json`에 남긴다.

Self-test:
`tests/unit/test_quality_pipeline_parity_matrix.py`가 현재 repo 통과, 필수 capability 누락,
비공식 URL, evidence 없는 current, stale implementation/API surface/test, 잘못된 list와
빈 완료 규칙, planned code claim, streaming boundary 누락, 제외된 engine 재도입,
JSON report 생성을 검증한다.
`tests/unit/test_quality_ci_workflows.py`는 이 게이트가
`scripts/quality/run_static_checks.py`의 실제 `ci:gate` static inventory에 포함됨을 검증한다.

G14D 실행 증거 inventory는 backend/static 9개
(`quality:pipeline-graph-v2`, `quality:pipeline-artifact-execution`,
`quality:pipeline-preview`, `quality:pipeline-multimodal`,
`quality:pipeline-scheduler`, `quality:pipeline-python-isolation`,
`quality:pipeline-builder-performance`, `quality:pipeline-async-dag`,
`quality:pipeline-async-dag-live`)와 browser E2E 1개
(`quality:pipeline-builder-e2e`)로 고정한다. E2E gate는 격리된 API `8016`과 Web `4186`을
새로 시작하고 기존 서버 재사용을 끄므로, 사용자가 작업 중인 기본 포트 `8000`/`4173`을
재사용하거나 종료하지 않는다.

`quality:pipeline-async-dag`는 SQLite/local 계약과 Temporal 결정성 테스트로 run/node/attempt
상태, retry 분류, event sequence, cancel/terminal CAS, lease takeover, stale fencing write
차단을 검증한다. `quality:pipeline-async-dag-live`는 Docker Compose의 PostgreSQL과 실제
Temporal을 사용하고 같은 task queue를 듣는 worker 프로세스 2개를 띄운다. 장시간 node의
소유 worker를 강제 종료해 두 번째 worker의 takeover와 Dataset·Media Set serving commit
각 1회를 증명하고, 별도 run의 취소가 격리 subprocess를 종료하며 downstream을 실행하지
않는지 확인한다. 이 live gate 없이 `async-dag-scheduler`를 current로 승격할 수 없다.

Python 격리는 static 계약만으로 완료라고 부르지 않는다.
`quality:code-execution-image`가 runner와 transforms SDK를 포함한 non-root 이미지를 만들고,
`quality:pipeline-python-isolation-live`가 실제 컨테이너에서 UID/GID, 환경 allowlist,
network deny, read-only root filesystem, capability drop, no-new-privileges, timeout과
typed/redacted failure를 검증한다. 이 live ratchet은 Docker가 준비되는 `runtime-full`
lane에서 실행되며, coverage/flaky/impact lane도 Python transform 회귀 테스트 전에 같은
이미지를 준비한다. production/staging runtime은 mutable tag를 거절하고
`@sha256:` digest로 고정된 이미지 참조만 허용한다.

성능 gate는 현재 실행 가능한 범위와 미래 기준을 섞지 않는다. 200-node/600-edge graph의
validate/compile과 실제 50-row no-commit preview의 acknowledgement/execution p95는
`current_enforced`로 측정·차단한다. 10,000 schedule `next_due_at` scan도 cron/timezone
계산 결과, active/paused 상태, 만료 lease, fencing claim 조건을 사용하는 실제 repository
query를 반복 측정해 p95 1초 미만을 강제한다. 따라서 report는
`completionStatus=current_enforced`, `unprovenTargets=[]`를 사용한다. 이 기준은 bounded
due scan의 DB query 성능 증거이며, 항상 실행되는 production worker 배포·장애 복구까지
증명하는 것으로 과장하지 않는다.

### Tier G14E — Action Types v2 parity evidence (✅ bounded current slices 2026-08-03)

`quality:action-types-v2`는 `docs/action-types-parity-matrix.json`에서 current로 표시한 세
capability가 단순 타입·문서가 아니라 실제 실행 경로에 연결되는지 검증한다. 비개발자식으로
말하면, 새 Action 문법을 읽을 수 있다는 사실만으로 “업무 변경이 된다”고 주장하지 않고,
컴파일된 계획이 실제 DB 원장까지 원자적으로 반영되는지를 한 번에 확인하는 게이트다.

검사 범위:

- eval-free value expression, Action IR v2 ordering, v1 `setProperty` normalization
- immutable EditPlan construction/validation, OCC와 link endpoint visibility
- public `foundry.actions.apply(...)`의 native `rulesV2` 실행과 idempotent replay
- heterogeneous object create/modify/delete와 many-to-many link create/delete의 원자적 commit
- object/link edit의 unified `object_edits` ledger, tenant/run ordering, latest-edit lookup
- fake, SQLite, PostgreSQL `ActionRepository` contract parity
- 존재하지 않는 link delete가 성공 로그를 남기지 않는 fail-closed behavior

이 gate는 `runtime-full`/release ratchet에 연결되고 PR coverage lane의 전체 pytest에서도 다시
실행된다. 현재 `action-ir-v2`, `edit-plan-engine`, `multi-object-link-atomic-commit`만 current다.
function-backed action, full parameter/form/criteria, side effect, Ontology-queryable action log,
revert, branch/interface action, complete SDK/UI/MCP는 이 v2 gate가 증명하지 않는다. 각 capability의
현재 상태는 전용 runtime/live/browser gate와 matrix evidence로만 승격한다.

### Tier G14F — Action Contract v3 planning and approval evidence (🟡 bounded slice 2026-08-03)

`quality:action-types-palantir`는 v1/v2/v3 정의가 하나의 canonical contract로 정규화되고,
그 계약에서 API·SDK용 schema와 실제 실행 전 계획이 같은 방식으로 만들어지는지 검증한다.
현재 게이트는 모든 v3 parameter type, deterministic defaults, first-match override, nested
submission criteria, immutable fingerprint/plan hash, before/after diff, 구조적 위험도 하한,
edit별 object/link/property 권한, AIP proposal의 plan/Action/object-version 고정, 승인 직전 drift
재검증, catalog/schema/plan/dry-run API와 생성 SDK를 포함한다. 직접 `apply`도 같은 resolved-plan
권한 검사를 다시 수행하므로 plan endpoint를 건너뛰어 권한을 우회할 수 없다. 같은 gate의
Action file-scanner contract는 attachment/media를 staging하기 전에 검사하고, 감염 input은 저장 없이
redacted deny audit만 남기며, protected runtime이 ClamAV 이외 profile을 거절하는지 검증한다.

이 증거는 18개 축 전체 current 증거와 구분한다. Temporal durable Action run과 function-backed
`OntologyEditBatch`는 `quality:action-runtime-async` 및 실제 PostgreSQL+Temporal 복수 worker의
`quality:action-types-palantir-live`로 active-covered가 되었다. `quality:action-side-effects`는
v3 effect의 inline URI 차단, 등록 connector/OSDK scope, before-effect 영수증 선기록과
ambiguous 무재호출, typed response field→rule assignment와 타입 불일치 commit 차단, after-effect outbox/receipt 원자 생성, retry/DLQ, lease takeover/fencing,
실제 HTTP POST idempotency/SSRF 차단, PostgreSQL tenant RLS를 검증한다. `quality:action-log-revert`는
성공한 제출당 정규 로그 하나, 모든 object/link edit 연결, 원 실행자·최신 편집·object version·link
상태 재검증, object/link 전체 원자 복구, revert 자체의 새 run/log/audit/outbox, 외부효과 보존,
SQLite/PostgreSQL repository 계약, RLS, API와 생성 TS/browser SDK를 검증한다. Python OSDK의
catalog/schema/plan/dry-run/branch/durable-run/log/revert 경로는 `quality:action-types-palantir`가,
실행 이력·SSE fallback·takeover/fencing·receipt·cancel·30일 failure taxonomy/alert monitoring·log·revert 브라우저 경로는
`quality:action-types-palantir-ui`가 검증한다. `quality:action-log-revert`는 virtual `[LOG] <Action>`의
Ontology catalog/get/filter/search/signed-cursor query/aggregation과 OSDK app-scope denial까지 검증한다.
`quality:action-runtime-async`는 typed adapter failure의 안전한 retry 분류와 Action control worker의 tenant별
alert evaluation까지 검증한다. `quality:action-monitoring-live`는 활성 경보가 정책·UTC hour 단위 idempotency로
outbox에 한 번만 쌓이고 실제 Kafka broker에 전달되는지 검증한다. 따라서 durable Action run state machine 축은
PostgreSQL+Temporal 복수 worker, browser runtime, external alert transport 증거를 모두 가진 `current`다.
Function-backed Action 축도 이제 전용 증거를 모두 가진 `current`다. canonical contract는 function
execution mode를 `per_request` 또는 `batched`로 고정하고 activation 때 pinned function input과 Action
parameter를 type-check한다. `quality:action-runtime-async`는 순차 per-request 호출, list-of-struct 단일 호출,
batched 단건의 one-item list 변환, 전체 OCC rollback, 최대 20/10,000 request 및 10,000 edit/50 object-type
한도를 검증한다. `quality:action-types-palantir-ui`는 Builder mode/input/limit authoring을,
`quality:sdk-generated`와 request-contract는 TypeScript/Python 및 `actions.runs.startBatch(...)`를 검증한다.
실제 PostgreSQL+Temporal live gate는 batched function activity를 소유한 worker를 종료한 뒤 새 worker가
fencing token 2로 takeover하여 두 객체를 한 번만 commit하는지 확인한다.
이 bounded proof는 DB-native 5,000행 초과 log query/aggregation과 Action Log edited-object→Object Explorer deep link까지 포함하며, virtual Ontology edited-object link type,
generic Workshop timeline, 실제 동시 revert 경합을 아직 증명하지 않는다. `quality:action-branch-interface`는 interface target을 concrete implementer로
해석하고 shared-property/OCC/권한을 재검증하는 경로, version-zero concrete create, typed reference,
interface link constraint의 target/cardinality/required conformance, create exactly-one/delete-all 해석, branch object/link overlay의
main 격리, base+overlay 합성 조회와 drift diff, 동일 요청 replay, SQLite/PostgreSQL concurrent CAS, API와 생성
TS/browser SDK를 검증한다. Action Builder browser proof는 Interface create/link-constraint canonical serialization과
object Action의 branch 저장→proposal→독립 승인→activation→SDK 재생성 없는 동적 실행→log→revert를 검증한다.
같은 UI gate의 Ontology MCP browser scenario는 외부 MCP가 만든 고위험 Action proposal을 Approvals에서
application/client/scope, plan hash, Action/object version, risk와 함께 검토하고, 사람이 assign/approve/execute한 뒤
MCP가 동일한 `action_run_id`와 terminal 상태를 다시 읽는 경로를 검증한다.
다만 Interface의 전체 proposal-to-execution, non-production effect opt-in, dedicated overlay-data diff와 현재
환경의 live PostgreSQL 경합을 증명하지 못했으므로 관련 matrix는 partial이다.
`quality:ontology-mcp`는 consumer Streamable HTTP endpoint가 app-granted object/action/function만
결정적으로 투영하는지, Authorization Code + PKCE와 Client Credentials service principal이
principal/token/application/resource 권한 교집합을 유지하는지, low-risk autonomous Action만 durable
run으로 보내는지, 그 밖의 Action은 immutable plan/version/evidence를 가진 AIP review 한 건으로 보내는지
검증한다. 같은 JSON-RPC 호출은 동일 run/review를 replay하고, 같은 좌표의 다른 payload는 충돌한다.
Developer Console enablement와 visual MCP Hub, 앱별 Origin allowlist, version-pinned query-function schema,
HTTP 권한 경계를 재사용하는 stdio proxy, DB session/event sequence, `Last-Event-ID` resume와 DELETE 후
session 재사용 차단도 검증한다. 표준 form OAuth, read-only approval status, 사람 승인 후 원래
application/client/scope 재검증, 철회된 scope의 실행 차단, 승인 Action run 조회도 같은 gate가 검증한다.
서비스 클라이언트의 one-time secret 발급, local encrypted vault version 좌표만 DB에 저장하는 회전,
replay 시 raw secret 비노출, 이전·폐기 secret의 신규 token 발급 거절, 안전한 이력 API와 Developer Console
브라우저 흐름도 검증한다. 회전·폐기·client deactivate가 이미 발급된 durable access session도 즉시
철회하고 모든 MCP 요청이 online session을 교차검증하며, client-credential 발급은 row lock으로 rotation
race를 차단한다. 이 backend gate와 `quality:action-types-palantir-ui`의 MCP approval scenario는 browser 승인
추적까지 증명한다. `quality:ontology-mcp-live`는 공식 Python MCP `ClientSession`이 별도
Uvicorn 프로세스의 Streamable HTTP endpoint에 접속하여 실제 PostgreSQL 객체 조회와
고위험 Action의 `approval_required` 분기까지 검증한다. 다만 hosted ChatGPT SaaS tenant와
production cloud secret manager/KMS는 아직 별도 운영 증거다.
`quality:action-notification-policies`는 no-code UI와 API/TypeScript/Python SDK가 사용하는 durable tenant policy registry의
create/update/disable, durable idempotency replay, config-fingerprint CAS, audit/outbox, disabled-policy fail-closed runtime 해석을 검증한다.
`quality:action-notification-policies-live`는 PostgreSQL concurrent create 단일 승자와 policy/idempotency table의 tenant RLS를 release lane에서 검증한다.
`quality:action-effect-operations`는 effect queue 조회, 취소, 동일 idempotency key 수동 재시도, 구조화된 provider 근거 조정과 API/SDK/UI 계약을 검증한다.
`quality:action-effect-operations-live`는 실제 PostgreSQL과 별도 worker 프로세스에서 dispatch 전후로 소유 worker를 강제 종료한다. dispatch 전 lease는 두 번째 worker가 fencing token을 올려 인계하고 provider를 정확히 한 번 호출하며, dispatch 후 응답 기록 전에 죽은 경우는 provider를 다시 호출하지 않고 `outcome_unknown`으로 격리한다.
Action 정의에는 임의 recipient 목록 대신 등록된 `notification-policy:<name>` 참조만 저장되며, 실행 시점의 active policy와 recipient별 object/row 접근권한을 다시 계산한다.
After-commit effect 축은 concurrent unordered fan-out과 pre-edit notification template/fingerprint/version 증거까지 닫혀 `current`다.
남은 범위는 live ClamAV, 위 Ontology MCP 미완성 경계이며, static generated Python Action type package는 `quality:sdk-generated`가 drift/mypy/Pyright로 강제하고,
이 증거가 추가되기 전에는 관련 matrix 항목을
`current`로 승격하지 않는다.

### Tier G15A — schema revision guard (✅ 완료 2026-06-11)

`scripts/quality/check_schema_revision_guard.py`는
`libs/foundry_lite/infrastructure/schema` 패키지의 SQLAlchemy metadata를 정규화해
fingerprint를 만들고, 최신 `infra/schema_revisions/*.json` snapshot과 비교한다.
비개발자 관점으로 말하면, DB 설계도를 바꿨는데 변경 이력 도장을 찍지 않는 일을
release gate에서 막는 장치다.

현재 구현은 SQLite/SQLAlchemy local bootstrap을 유지하면서 Alembic baseline
migration도 갖는다. `tests/integration/test_migrations.py`는 `alembic upgrade head`로
fresh DB를 만들고 SQLAlchemy metadata와 table/column shape가 같은지 확인한다. 이
게이트는 여전히 DB 테이블/컬럼/unique constraint 모양이 code-only assumption으로
바뀌는 것을 차단한다. 다만 multi-step upgrade/rollback, 운영 runbook, 배포 중 rollback
테스트는 별도 future scope다.

검사 기준:

- 최신 schema revision JSON이 존재해야 한다.
- revision id는 파일명 stem과 같아야 한다.
- revision은 description을 가져야 한다.
- 현재 metadata fingerprint와 revision snapshot fingerprint가 같아야 한다.
- 현재 table/column/unique constraint snapshot과 revision snapshot이 같아야 한다.
- 결과는 `artifacts/quality/schema_revision_guard.json`에 남긴다.

Self-test: `tests/unit/test_quality_schema_revision_guard.py`가 matching snapshot,
missing revision dir, fingerprint mismatch, schema snapshot mismatch, revision id
mismatch, JSON report 생성, 실제 metadata snapshot 생성을 검증한다.

### Tier S1 — tenant write guard (✅ 완료 2026-06-11)

`scripts/quality/check_tenant_write_guard.py`는
`libs/foundry_lite/infrastructure/repositories/**/*.py`의 SQLAlchemy write statement를
AST로 읽고 tenant-scoped table에 대한 insert/update/delete가 tenant boundary를 갖는지
검사한다. 비개발자 관점으로 말하면, “주문 번호가 같으면 수정”이 아니라 “이 고객사의
그 주문 번호가 맞을 때만 수정”하도록 DB 문장 자체에 안전벨트를 거는 게이트다.

검사 기준:

- `libs/foundry_lite/infrastructure/schema` 패키지에서 `Column("tenant_id", ...)`를 가진 table을 tenant-scoped table로 본다.
- tenant-scoped `insert(table)`는 `.values(tenant_id=...)`를 가져야 한다.
- `update(table)`와 `delete(table)`는 `.where(...)` 안에 `tenant_id` 조건을 가져야 한다.
- dynamic table update/delete도 tenant where guard가 없으면 fail한다.
- 결과는 `artifacts/quality/tenant_write_guard.json`에 남긴다.

Self-test: `tests/unit/test_quality_tenant_write_guard.py`가 tenant 없는 insert/update/delete
실패, tenant guard가 있는 write 허용, dynamic table update 실패, 현재 repo 0 violation,
JSON report 생성을 검증한다.

### Tier G2 — audit on public service mutations (✅ 정적 완료 2026-06-11 G2)

`scripts/quality/check_audit_on_mutation.py`는 application service의 public mutation
entrypoint를 AST로 읽고, 해당 entrypoint가 service/helper call tree를 통해 repository
write에 닿는지 확인한다. repository write에 닿는데 `runtime_service._audit`,
`runtime_service._outbox`, `insert_audit_event`, `insert_outbox_event` 중 하나도
도달하지 못하면 release gate를 실패시킨다.

검사 기준:

- 대상은 `libs/foundry_lite/application/services/**/*.py`의 public mutation method다.
- mutation method prefix는 `create_`, `ensure_`, `upload_`, `register_`, `run_`,
  `apply_`, `materialize`, `index_`, `cleanup_`, `commit_`, `abort_`다.
- repository write는 `*_repository.insert_/update_/delete_/create_/commit_/abort_/mark_/archive_/activate_`
  계열 호출로 본다.
- 같은 public entrypoint의 service/helper call tree 안에 audit/outbox proof가 없으면 fail한다.
- 결과는 `artifacts/quality/audit_on_mutation.json`에 남긴다.

Self-test: `tests/unit/test_quality_audit_on_mutation.py`가 public mutation violation,
direct audit 허용, delegated audit helper 허용, JSON report 생성을 검증한다.

현재 보장 범위: G2는 정적 게이트다. demo smoke 후 실제 `audit_events` row 수와
mutation 호출 수를 대조하는 동적 검증은 G17이 담당한다. 그래서 §10.2 audit
조항은 정적/동적 양쪽에서 강제되며, transaction/outbox 원자성은 G11/G18이 나눠서
검증한다.

### Tier G17 — runtime audit count validation (✅ 완료 2026-06-11 G17)

`scripts/quality/check_audit_count_runtime.py`는 supply-chain demo smoke가 만든 runtime
DB를 읽어 high-level mutation 증거와 durable `audit_events` row를 대조한다.

검사 기준:

- `datasets` row는 `dataset.created` audit를 가져야 한다.
- `dataset_versions` row는 `dataset.version.committed` audit를 가져야 한다.
- `transforms` row는 `transform.definition.created` 또는 `transform.definition.updated`
  audit를 가져야 한다.
- active `ontology_versions` row는 `ontology.version.activated` audit를 가져야 한다.
- succeeded `index_runs` row는 `object.index.rebuilt` audit를 가져야 한다.
- terminal `action_runs` row는 성공 시 `action.run.committed`, 실패/충돌 시
  `action.run.failed` audit를 가져야 한다.
- 전체 기대 mutation count와 `audit_events` row count의 차이는 `0`이어야 한다.
- 결과는 `artifacts/quality/audit_count_runtime.json`에 남긴다.

Self-test: `tests/unit/test_quality_audit_count_runtime.py`가 정상 runtime mutation/audit
매칭, missing audit row 실패, JSON report 생성을 검증한다. 실제 demo probe에서는
21개 기대 mutation과 21개 audit row가 일치했다.

### Tier G18 — outbox consistency validation (✅ 동적 완료 2026-06-11 G18)

`scripts/quality/check_outbox_consistency.py`는 supply-chain demo smoke가 만든 runtime
DB를 읽어 event-propagated state change와 durable `outbox_events` row를 대조한다.
이 게이트는 "업무 장부는 바뀌었는데 외부 전파/재처리 장부가 빠지는" 사고를 막는
동적 검증이다.

검사 기준:

- active `dataset_versions` row는 일반 commit이면 `dataset.version.committed`,
  materialization output이면 `materialization.completed` outbox row를 가져야 한다.
- active `ontology_versions` row는 `ontology.version.activated` outbox row와 비어 있지
  않은 correlation id를 가져야 한다.
- succeeded `action_runs` row는 `action.run.committed` outbox row를 가져야 한다.
- `object_edits` row는 같은 `action_run_id` 기준의 `object.edit.committed`와
  `object.changed` outbox row를 가져야 한다.
- succeeded `index_runs` row는 `objects_upserted + objects_deleted` 개수만큼
  source dataset version correlation을 가진 `object.changed` outbox row를 가져야 한다.
- 전체 기대 state-change count와 covered `outbox_events` row count의 차이는 `0`이어야 한다.
- 결과는 `artifacts/quality/outbox_consistency.json`에 남긴다.

Self-test: `tests/unit/test_quality_outbox_consistency.py`가 정상 state change/outbox
매칭, missing outbox row 실패, correlation id mismatch 실패, JSON report 생성을 검증한다.
실제 demo probe에서는 18개 기대 state change와 18개 outbox row가 일치했다.

현재 보장 범위: G18은 동적 게이트다. service mutation 함수 안에서 state change와
outbox insert가 같은 transaction scope에 있는지 정적으로 강제하는 검증은 G11이
담당한다. Action Runtime은 추가로
`test_action_commit_object_edit_audit_outbox_atomic`가 object record
update 이후, object edit insert, action terminal update, outbox insert, audit insert
실패를 주입해 같은 transaction rollback에 묶이는지 검증한다.

### Tier G19 — flaky detector (✅ 완료 2026-06-11 G19)

`scripts/quality/check_flaky_detector.py`는 같은 pytest 명령을 3회 반복 실행하고,
각 반복 결과를 `artifacts/quality/flaky_detector.json`에 남긴다. 한 번이라도 실패하거나
통과 요약이 달라지면 release gate가 실패한다. 비개발자 관점으로 말하면, "한 번은
우연히 초록불이었지만 다시 돌리면 깨지는 테스트"를 그대로 통과시키지 않는 안전장치다.
다만 이 게이트는 이미 suite에 들어온 테스트가 실행 순서/랜덤 seed/parallel worker 차이로
흔들리는지를 보는 장치다. 아직 테스트로 모델링하지 않은 동시성 interleaving, 예를 들어
두 shadow promotion이 같은 object type의 첫 `object_index_versions` pointer를 동시에 만드는
경우는 flaky detector가 자동으로 발명해서 잡지 못한다. 그런 문제는 별도의 contract/integration
test가 경합 스케줄을 직접 만들어야 하며, 그 테스트가 들어온 뒤에야 flaky detector가 반복 안정성을
감시할 수 있다.

검사 기준:

- 기본 명령은 `uv run pytest tests -n auto --no-header -q`이다.
- 반복 횟수는 `3`회이며, 각 회차는 pytest-randomly의 fresh seed와 pytest-xdist의
  병렬 실행을 사용한다. 단, 한 회차 안의 xdist worker들은 같은 `--randomly-seed`를
  공유해 테스트 수집 순서 자체가 흔들리지 않게 한다.
- 모든 회차 return code는 `0`이어야 한다.
- 모든 회차의 pytest summary는 같아야 한다.
- 결과는 `artifacts/quality/flaky_detector.json`에 남긴다.

Self-test: `tests/unit/test_quality_flaky_detector.py`가 안정적으로 통과하는 fake runner,
중간 1회 실패 runner, 통과하더라도 summary가 바뀌는 runner, pytest 명령의 회차별 seed
주입을 검증한다.

### Tier G16A — adapter error trace-key validation (✅ 완료 2026-06-11)

`scripts/quality/check_adapter_error_trace_keys.py`는 격리된 local core에 일부러 실패하는
compute adapter를 끼운 뒤, 실패한 `sync_runs.error.trace`가 운영 추적 키를 모두
보존하는지 검증한다. 비개발자 관점으로 말하면, "데이터 변환 부품이 고장났는데
누가, 어떤 요청에서, 어떤 실행이 실패했는지 모르는" 사고를 막는 장치다.

검사 기준:

- adapter 실패 후 run 상태는 `FAILED`로 남아야 한다.
- `error.trace`에는 `tenant_id`, `actor_user_id`, `request_id`, `run_id`,
  `correlation_id`, `adapter`가 있어야 한다.
- `run_id`와 `correlation_id`는 실패한 run id와 일치해야 한다.
- request/tenant/actor/adapter 값은 gate가 주입한 기대값과 일치해야 한다.
- 결과는 `artifacts/quality/adapter_error_trace_keys.json`에 남긴다.

Self-test: `tests/unit/test_quality_adapter_error_trace_keys.py`가 실제 exploding adapter
probe, trace 누락, 필수 키 누락, request context mismatch, JSON report 생성을 검증한다.

### Tier G16C — adapter failure taxonomy (✅ 완료 2026-06-14)

`scripts/quality/check_adapter_failure_taxonomy.py`는 현재 concrete adapter profile이
공통 `AdapterFailureContract`를 노출하는지 검증한다. 비개발자 관점으로 말하면,
"부품을 Kafka, Elasticsearch, Temporal, S3 같은 다른 인프라로 갈아끼웠는데 실패
메시지와 재시도 기준이 제각각이라 운영자가 판단할 수 없는" 문제를 막는다.

검사 기준:

- compute/storage/workflow/stream/search/connector/auth/secret/prompt-artifact adapter profile은 실패 계약을 가져야 한다.
- 각 실패 mode는 `operation`, 실패 `kind`, `is_retryable`, 운영자 메시지를 가져야 한다.
- timeout 실패는 retry 가능한 실패여야 하고, timeout 값은 양수여야 한다.
- 결과는 `artifacts/quality/adapter_failure_taxonomy.json`에 남긴다.

Self-test: `tests/unit/test_quality_adapter_failure_taxonomy.py`가 현재 profile 통과,
누락 profile 실패, 빈 운영자 메시지 실패, JSON report 생성을 검증한다. Contract proof는
`tests/contracts/test_adapter_failure_contract.py`가 모든 현재 adapter profile의
operator-safe failure mode를 검증한다.

### Tier G16B — failed mutation state validation (✅ 완료 2026-06-12)

`scripts/quality/check_failed_mutation_state_runtime.py`는 격리된 local core에 일부러
실패하는 CSV compute adapter를 끼운 뒤, 실패한 mutation이 런타임 DB에 모순 없이
남는지 검증한다. 비개발자 관점으로 말하면, "업무 변경이 중간에 실패했는데 장부는
성공처럼 보이거나, 실패 원인과 감사 기록이 빠지는" 사고를 막는 장치다.

검사 기준:

- 실패 probe는 최소 1개의 `FAILED` sync run을 만들어야 한다.
- 실패 run은 `completed_at`, 구조화된 `error`, `error.trace`를 가져야 한다.
- `error.trace`에는 `tenant_id`, `actor_user_id`, `request_id`, `run_id`,
  `correlation_id`, `adapter`가 있어야 한다.
- 실패 run의 `transaction_id`는 durable `dataset_transactions` row를 가리켜야 하고,
  해당 transaction은 `ABORTED`여야 한다.
- `ABORTED` transaction의 metadata error는 실패 run error와 같아야 한다.
- 실패/abort transaction은 committed `dataset_versions` row를 만들면 안 된다.
- `dataset.transaction.aborted` audit row는 같은 transaction id와 failed run correlation
  id를 가져야 하며, `after_ref.error`를 포함해야 한다.
- 결과는 `artifacts/quality/failed_mutation_state.json`에 남긴다.

Self-test: `tests/unit/test_quality_failed_mutation_state_runtime.py`가 실제 exploding
adapter probe, 완전한 실패 증거 통과, abort audit 누락 실패, failed transaction의
committed version 생성 실패, error trace 누락 실패, JSON report 생성을 검증한다.

2026-06-12 추가 진행: `DatasetTransactionService._abort_transaction_after_error`는
repository가 내부 transaction을 열게 하지 않고 application service가 연 transaction
안에서 failed run update, dataset transaction abort, `dataset.transaction.aborted`
audit를 함께 기록한다. `abort_open_transaction_and_fail_run`은 실제 OPEN transaction을
ABORTED로 바꿨는지 boolean으로 반환하므로, validation failure처럼 이미 abort audit가
남은 경로에서는 중복 audit를 만들지 않는다. adapter failure probe에서
`check_audit_count_runtime.py`를 다시 실행하면 2개 기대 mutation과 2개 durable audit
row가 일치한다.

### Tier G16 — trace continuity validation (✅ 완료 2026-06-11 G16)

`scripts/quality/check_trace_continuity.py`는 supply-chain demo를 in-memory
OpenTelemetry provider 아래에서 실행한다. 외부 Tempo/Grafana 서버 없이 synthetic
request span을 만들고, 그 아래에서 service span과 SQLAlchemy DB span이 같은 trace에
남는지 검증한다.

검사 기준:

- synthetic request span `foundry-lite.trace-continuity.demo-request`가 있어야 한다.
- request span은 gate가 주입한 `trace-gate-request` request id를 가져야 한다.
- `DemoService.run_supply_chain_demo`, `DatasetIngestService.upload_csv`,
  `TransformService.run_transform`, `OntologyService.apply_ontology`,
  `ObjectIndexingService.index_rebuild`, `ActionService.apply_action`,
  `MaterializationService.materialize` span이 있어야 한다.
- 위 service span은 같은 `foundry_lite.request_id`를 가져야 한다.
- span에는 raw `foundry_lite.tenant_id`나 `foundry_lite.actor_user_id`가 없어야 하며,
  correlation이 필요하면 bounded hash attribute만 사용해야 한다.
- SQLAlchemy DB span이 request trace 안에 1개 이상 있어야 한다.
- service span/DB span이 request trace 밖으로 새 trace를 만들면 fail한다.
- 결과는 `artifacts/quality/trace_continuity.json`에 남긴다.

Self-test: `tests/unit/test_quality_trace_continuity.py`가 missing request span,
service request_id 누락, raw tenant/user span attribute, DB trace mismatch, 정상 span set,
JSON report 생성을 검증한다.
실제 demo probe에서는 service span 13개와 DB span 651개가 하나의 request trace 안에
묶이는 것을 확인했다.

### Tier P8 — OpenLineage dynamic lineage validation (✅ 동적 완료 2026-06-11 P8)

`scripts/quality/check_openlineage_dynamic_lineage.py`는 supply-chain demo smoke가 만든
runtime DB를 읽어 `transform_runs.input_versions`, `transform_runs.output_version_id`,
`lineage_edges`가 같은 version 단위 계약을 가리키는지 검증한다.

검사 기준:

- 성공한 transform run이 1개 이상 있어야 한다.
- 성공한 transform run의 모든 input version은 output version으로 가는 `input_to`
  lineage edge를 가져야 한다.
- `input_to` edge는 반드시 `dataset_version → dataset_version`이어야 한다.
- 같은 transform run/input version/output version 조합의 lineage edge가 중복되면 fail한다.
- 실패한 transform run이 성공 lineage edge처럼 남으면 fail한다.
- 검증 결과는 `artifacts/quality/openlineage_dynamic_lineage.json`, OpenLineage-compatible
  RunEvent artifact는 `artifacts/quality/openlineage_events.json`에 남긴다.

Self-test: `tests/unit/test_quality_openlineage_dynamic_lineage.py`가 정상 version-bound
lineage 통과, missing edge 실패, duplicate edge 실패, non-version edge 실패,
failed-run edge 실패, JSON report 생성을 검증한다. 실제 OpenLineage CLI/server
전송은 아직 붙이지 않고, P8 1차에서는 외부 서비스 없이 재현 가능한 동적 검증과
이벤트 산출을 고정한다.

### Tier G3 — function length hard-limit guard (✅ baseline 0 완료 2026-06-11 G3)

`scripts/quality/check_function_length.py`는 `libs/foundry_lite/application` 아래
Python 함수를 AST로 읽어 함수 길이 부채가 더 커지지 않게 막는다. 가이드 §2.2는
40줄을 넘으면 책임이 섞였는지 확인하라고 말한다. 현재 application에는 40줄을
초과하는 기존 함수가 0개 남아 있으므로 G3는 새 40줄 초과 함수부터 차단한다.
따라서 함수 길이 기준은 더 이상 baseline no-growth가 아니라 40줄 hard limit으로
강제된다.

검사 기준:

- hard limit은 40줄이다.
- baseline은 0개다.
- 새 40줄 초과 함수는 실패한다.
- 40줄 초과 함수가 생기면 책임 분리나 record/payload/helper 분리를 먼저 검토한다.
- 결과는 `artifacts/quality/function_length.json`에 남긴다.

Self-test: `tests/unit/test_quality_function_length.py`가 새 over-limit 함수 실패,
baseline 함수 동일 길이 허용, baseline 함수 성장 실패, warning-only 함수 통과,
JSON report 생성을 검증한다.

### Tier G4 — dict[str, Any] signature budget (✅ baseline 완료 2026-06-11 G4)

`scripts/quality/check_dict_any_budget.py`는 `libs/foundry_lite/application` 아래
Python 함수 시그니처를 AST로 읽어 `dict[str, Any]`가 application schema drift 통로로
더 커지지 않게 막는다. 가이드 §5.2와 §18.3은 adapter/JSON boundary 밖에서
`dict[str, Any]`로 schema mismatch를 숨기지 말고 Pydantic model, dataclass,
TypedDict 같은 명시적 구조로 옮기라고 말한다. application 함수 시그니처의
기존 부채는 0으로 줄였으므로 G4는 baseline 0을 고정하고, 총량 또는 layer별
총량 증가를 release gate에서 차단한다.

검사 기준:

- 대상은 `libs/foundry_lite/application` 아래 함수 argument와 return annotation이다.
- `dict[str, Any]`, `Dict[str, Any]`, `typing.Dict[str, Any]`를 중첩 annotation까지 센다.
- 총 baseline은 0이다.
- layer baseline은 core facade 0, ports 0, application helpers 0, services 0이다.
- 총량 또는 layer별 총량이 baseline보다 늘면 fail한다.
- 결과는 `artifacts/quality/dict_any_budget.json`에 남긴다.

Self-test: `tests/unit/test_quality_dict_any_budget.py`가 중첩 signature 카운트,
총량 증가 실패, layer 증가 실패, baseline 이내 허용, JSON report 생성을 검증한다.

### Tier G5 — log trace-key validation (✅ 완료 2026-06-11 G5)

`scripts/quality/check_log_has_trace_keys.py`는 운영 로그가 추적 키 없이 남는 것을
막는다. 비개발자 관점으로 말하면, 장애가 난 뒤 로그 한 줄만 보고도 “어떤 요청,
어떤 tenant, 어떤 실행(run)에서 나온 일인지” 다시 찾을 수 있게 하는 게이트다.

검사 기준:

- `libs`, `apps`, `scripts` 아래 Python 파일에서 직접 `logger.info(...)`,
  `logger.error(...)`, `logging.warning(...)` 같은 logger 호출을 만들면 message,
  args, 또는 `extra=` 안에 `request_id`, `tenant_id`, run id 계열 키 중 하나가
  보여야 한다.
- `log_event(...)` 호출도 `request_id`, `tenant_id`, run id 계열 키 중 하나를
  keyword field로 넘겨야 한다.
- `foundry_lite.observability.logging.log_event` 자체도 같은 조건을 런타임에서
  검증하며, 추적 키가 없으면 `ValueError`로 즉시 실패한다.
- 결과는 `artifacts/quality/log_trace_keys.json`에 남긴다.

Self-test: `tests/unit/test_quality_log_trace_keys.py`가 추적 키 없는 direct logger
호출 실패, `extra={"request_id": ...}` 허용, 추적 키 없는 `log_event` 실패,
`tenant_id` 포함 `log_event` 허용, JSON report 생성을 검증한다.

### Tier M1 — required operational metrics exposure (✅ 완료 2026-06-11)

`scripts/quality/check_metrics_exposed.py`는 API의 Prometheus payload에 가이드 §12.2가
요구한 7개 운영 지표가 모두 노출되는지 검사한다. 비개발자 관점으로 말하면, 장애가
났을 때 운영자가 볼 계기판의 핵심 바늘이 배포 중 실수로 빠지지 않게 막는 게이트다.

검사 기준:

- `foundry_lite_dataset_commit_seconds`는 dataset commit duration을 나타낸다.
- `foundry_lite_transform_run_seconds`는 transform run duration을 나타낸다.
- `foundry_lite_action_apply_seconds`는 action apply latency를 나타낸다.
- `foundry_lite_object_query_seconds`는 object query latency를 나타낸다.
- `foundry_lite_outbox_publish_lag_seconds`는 outbox publish lag를 나타낸다.
- `foundry_lite_failed_runs_total`은 failed run count를 나타낸다.
- `foundry_lite_dlq_size`는 DLQ size를 나타낸다.
- 결과는 `artifacts/quality/metrics_exposed.json`에 남긴다.

Self-test: `tests/unit/test_quality_metrics_exposed.py`가 Prometheus HELP/TYPE/sample
파싱, required metric 누락 실패, 전체 metric 허용, 실제 `prometheus_payload()`
노출 확인, JSON report 생성을 검증한다.

### Tier G6 — pragma no cover budget (✅ 완료 2026-06-11 G6)

`scripts/quality/check_pragma_no_cover_budget.py`는 Python 주석 token만 읽어
`# pragma: no cover` 사용을 센다. 현재 코드 baseline은 `0`이고 `pnpm ci:gate`에서
`--baseline 0`으로 실행한다.

검사 기준:

- `libs`, `apps`, `scripts` 아래 Python 주석에 `pragma: no cover`가 있으면 count가 증가한다.
- 모든 exclusion은 `reason:` 주석을 가져야 한다.
- count가 baseline을 넘거나 reason이 없으면 fail한다.
- 결과는 `artifacts/quality/pragma_no_cover_budget.json`에 남긴다.

Self-test: `tests/unit/test_quality_pragma_no_cover_budget.py`가 missing reason,
reason 포함 허용, string literal 오탐 방지, budget 초과 report 생성을 검증한다.

### Tier G7 — error response request_id (✅ 완료 2026-06-11 G7)

`scripts/quality/check_error_response_has_request_id.py`는 `apps/api`의 FastAPI
error response 경로를 AST로 검사한다. 비개발자 관점으로 말하면, 사용자가 API 에러를
봤을 때 운영자가 trace/log/audit에서 같은 사건을 찾을 수 있는 추적번호가 빠지지
않게 하는 게이트다.

검사 기준:

- `HTTPException(...)`을 직접 만들면 `detail` 안에 `request_id` 또는 `requestId`가
  있어야 한다.
- 공통 `_handle_error(...)` helper를 호출할 때는 현재 `request`를 두 번째 인자 또는
  `request=` keyword로 넘겨야 한다.
- `_handle_error(exc)`처럼 request 없이 호출해 응답 `request_id`가 `None`이 될 수
  있는 코드는 fail한다.
- 결과는 `artifacts/quality/error_response_request_id.json`에 남긴다.

Self-test: `tests/unit/test_quality_error_response_request_id.py`가 request_id 없는
`HTTPException` 실패, request_id 포함 detail 허용, request 없는 `_handle_error`
호출 실패, request 포함 `_handle_error` 허용, JSON report 생성을 검증한다.

### Tier G8 — layer coverage floor (✅ 완료 2026-06-11 G8)

`scripts/quality/check_tier_coverage_by_layer.py`는 `coverage json` 산출물을
domain/application/infrastructure/API/CLI/worker 계층으로 나누어 각 계층의 combined
coverage가 95% 이상인지 검증한다. `ci_gate.sh`는 이제 coverage 수집 대상에
`libs/foundry_lite`, `apps/api`, `apps/cli`, `apps/worker`를 모두 포함한다.

검사 기준:

- 각 계층은 coverage JSON에 반드시 존재해야 한다.
- 계층별 covered units는 `covered_lines + covered_branches`, total units는
  `num_statements + num_branches`로 계산한다.
- 어느 한 계층이라도 95% 미만이면 fail한다.
- 결과는 `artifacts/quality/tier_coverage_by_layer.json`에 남긴다.

Self-test: `tests/unit/test_quality_tier_coverage_by_layer.py`가 전체 계층 통과,
낮은 계층 실패, 누락 계층 실패, JSON report 생성을 검증한다. API/CLI 스모크는
`tests/smoke/test_interfaces.py`에서 endpoint와 CLI command coverage를 보강한다.

### Tier P7 — CodeQL data-flow taint analysis (✅ 완료 2026-06-10 P7)

CodeQL은 정적 분석 중 유일하게 **interprocedural taint propagation**을
직접 모델링한다. Semgrep은 한 함수 안의 패턴만 보고, import-linter는
모듈 의존만 보지만, CodeQL은 "FastAPI Request의 헤더 값이 5단계 함수
호출을 거쳐 SQL `execute()`까지 흐르는지"를 그래프로 추적한다.

`scripts/quality/codeql/queries/` 4개 쿼리 + `qlpack.yml`:

| 쿼리                                   | 헌법 조항                                  | 추적하는 흐름                                                  |
| -------------------------------------- | ------------------------------------------ | -------------------------------------------------------------- |
| `header-flows-to-sql.ql`               | §10.2 (no raw SQL interpolation)           | Request → header → `text()`/`execute()`                        |
| `mutation-without-audit.ql`            | §10.2 (no mutation without audit)          | `session.execute(insert/update/delete)` 뒤에 audit 이벤트 없음 |
| `http-exception-without-request-id.ql` | §8.3 (error carries request_id)            | `HTTPException` 생성 시 detail에 request_id 없음               |
| `raw-json-to-service.ql`               | §5.2 / §6.3 (no dict[str,Any] passthrough) | Request body → service method (Pydantic 우회)                  |

실행:

- 로컬 `pnpm ci:gate`: 실행하지 않음. Python 코드베이스 기준 fresh DB build 3~5분 + analyze 1~2분이므로 매 로컬 피드백 루프에 넣지 않는다.
- 수동 디버그: `bash scripts/quality/codeql/run.sh` (로컬 codeql 미설치 시 WARN+exit 0, CI/strict mode에서는 missing tool이 실패)
- CI: `.github/workflows/codeql.yml`이 push/PR/weekly로 GitHub-hosted runner에서 실행 + SARIF 업로드 + `fail_on_sarif_findings.py`로 finding 1개 이상이면 hard failure

Self-test: `tests/unit/test_quality_codeql_queries.py`가 qlpack 매니페스트, § 인용, @id/@kind/@problem.severity 메타, run.sh 실행권한, codeql 미설치 시 graceful local skip, 알려진 CodeQL API 호환성(`getAnInstance`)을 검증한다. `tests/unit/test_quality_codeql_sarif_gate.py`는 SARIF finding을 workflow failure로 바꾸는 hard gate를 검증한다.

### Tier P6 — Pyright strict (✅ 부분 완료, #28 완료 2026-06-16 sync)

`pyright`는 디폴트 `basic` 모드로 전체 코드베이스를 보지만 `[tool.pyright]
strict = [...]` 리스트의 경로는 **strict 모드**로 격상된다. 이 리스트가
헌법화된다.

현재 strict 적용 경로 (모두 0 errors):

| 경로                                             | 의미                                             |
| ------------------------------------------------ | ------------------------------------------------ |
| `libs/foundry_lite/domain`                       | §4.1 framework 0 영역 — strict가 가장 자연스러움 |
| `libs/foundry_lite/application/ports`            | Protocol 정의 — strict로 Any 누출 자동 검출      |
| `libs/foundry_lite/security`                     | 보안 결정 영역 — Any 사용 금지                   |
| `libs/foundry_lite/application/services/base.py` | CoreService DI 토대                              |

전체 코드베이스를 한 번에 strict로 올리는 일은 아직 future expansion이다. 대신
현재 구현은 strict boundary와 별도로 **application/app broad `Any` 재도입 방지
게이트**를 완료했다. 즉, “Pyright strict 전체 전환”은 부분 완료지만, 로드맵
#28의 application/app `Any` cleanup 목표는 완료 상태다.

2026-06-12~2026-06-16 sync 결과:

- API/CLI JSON entrypoint의 `dict[str, Any]` 노출은 0이다.
- `libs/foundry_lite/application`, `apps/api`, `apps/cli`, `apps/worker`의 broad
  `Any` baseline은 0이다.
- `check_application_any_budget.py`가 application/app boundary에 broad `Any`가
  다시 들어오는 것을 차단한다.
- `check_dict_any_budget.py`가 `dict[str, Any]` function signature baseline 0을
  고정한다.
- action, object indexing, ontology, object set, transform, materialization,
  runtime, dataset, demo orchestration 경로는 typed row/helper/Protocol과
  `TransactionContext` 경계로 좁혀졌다.

인프라 repository/observability의 runtime edge `Any`는 #28의 application/app
boundary 밖 adapter 영역이다. 이후 strict 확장은 이 adapter edge를 대상으로
별도 이슈와 baseline을 세워 진행한다.

### Tier P11 — interrogate docstring coverage (✅ 완료 2026-06-10 P11)

`interrogate` measures the fraction of public functions, methods, and classes
with docstrings. ci_gate pins the current baseline (25.4%) at `--fail-under 25`
and we treat the threshold as **monotonic increasing only** — raising it
requires no doc amendment; lowering it requires editing this roadmap §5.

The point is not "documentation completeness" but **forcing the author to
write down intent at the point of definition**, which doubles as a
root-cause aid when later readers grep for "why does this exist".

### Tier P10 — vulture dead code (✅ 완료 2026-06-10 P10)

`vulture` walks the AST to find unreachable functions, unused variables,
and dead imports. We run at `--min-confidence 80` because the lower
thresholds (60–70%) flag Protocol method stubs and public-API surface that
look unused by static analysis but exist by design. Anything that fires at
80% is real dead code that must be removed or explicitly retained with a
documented reason.

### Tier P9 — gitleaks (✅ 완료 2026-06-10 P9)

`gitleaks` (Homebrew install) scans the working tree for strings that look
like API keys / tokens / credentials. Unlike Bandit (Python source) and
Semgrep (code shape), gitleaks inspects strings as data — so it catches
secrets pasted into docs, configs, scripts, YAML.

`.gitleaks.toml` extends the upstream default ruleset and documents
Foundry-lite specific allowlist entries with reasons (one entry for the
Idempotency-Key header example in the development plan).

ci_gate.sh skips gracefully only on local machines when gitleaks is not on PATH
(`WARN` with install hint). CI/release evidence sets `CI=true`, installs
gitleaks in `.github/workflows/ci.yml`, and missing gitleaks is a hard failure.

### Tier P5 — hypothesis (✅ 완료 2026-06-10 P5)

Property-based tests at `tests/unit/test_safe_expression_properties.py`
exercise the safe-expression evaluator with thousands of randomised inputs:
identifiers, values, IN lists, EQ comparisons, and the validate_action_request
parameter contract. The properties encode the invariants the evaluator must
hold under any input.

P5 surfaced one real root-cause defect on first run:
`precondition_expression` used `or` chaining (`a or b or c`), so an
intentional empty-string `safeExpression` silently fell back to `expression`
or `cel`. Hypothesis generated `safe=''`, `expr=''`, `cel='0'` and showed
the function returned `'0'` instead of `''`. Fix: explicit `in` membership
checks.

### Tier P4 — mutmut (🟡 staged, not yet in ci_gate)

`uv run mutmut run` is wired up via `[tool.mutmut]` in pyproject.toml and
the test tree is mutmut-friendly (REPO_ROOT heuristic in conftest.py + the
helpers module both honour the `mutants/` parent path and a
`FOUNDRY_LITE_REPO_ROOT` env override). Baseline pytest passes inside the
mutants tree.

mutmut 4.x's stat-collection step ("which test covers which mutant") reports
no coverage even though the same tests obviously exercise the modules, so
the mutation cycle exits before scoring. Upstream issue; gate is staged but
not yet in `ci_gate.sh`. Either a future mutmut release or a migration to
`cosmic-ray` will unblock without re-doing the conftest work done in P4.

### Tier P3 — pytest-randomly + pytest-xdist (✅ 완료 2026-06-10 P3)

두 plugin이 _동적_ root cause를 잡는다. 자체 self-test는
`tests/unit/test_quality_random_and_parallel.py`.

| Plugin            | 잡는 root cause                                                                                                               |
| ----------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `pytest-randomly` | **테스트 순서 의존성** — 한 테스트가 다른 테스트의 부작용에 암묵적으로 의존. seed가 매번 바뀌므로 PR에 들어오면 즉시 실패.    |
| `pytest-xdist`    | **race / 공유 자원 충돌** — 같은 파일 경로/env var/tmp 디렉토리에 두 테스트가 동시 쓰기. `-n auto`로 매 게이트 실행마다 검증. |

`ci_gate.sh`는 (a) coverage 측정용 pytest 1회 + (b) G19
`check_flaky_detector.py`가 소유한 `-n auto` parallel pytest 3회를 둘 다 보존한다.
다만 기본 로컬 `pnpm ci:gate`는 full release evidence를 직렬로 모두 재생하지 않고
정적 불변식과 Tach impact-scoped pytest를 먼저 실행한다. 전체 직렬 리허설은
`pnpm ci:gate:all`, release-grade 장시간 증거는 `pnpm ci:gate:release`가 맡는다.
GitHub Actions에서는 같은 스크립트를 `coverage` lane과 `flaky` lane으로 분리해 동시에
실행한다. 반복 횟수, coverage threshold, 실패 조건은 그대로 두며, 마지막 `quality-gate`
aggregate job이 두 lane을 포함한 모든 quality lane의 성공을 확인한다.

parallel run은 coverage 산출 없이 통과/실패와 반복 결과 안정성을 본다.

### Tier P2 — import-linter로 흡수된 게이트 (✅ 완료 2026-06-10 P2)

`.importlinter` 4 contracts가 import graph 자체를 강제한다. 도구는 module-level이
아니라 **transitive import 경로**를 본다 — `check_infra_import_boundary`가 놓친
function-local lazy import (application/core.py:53)를 P2가 첫 시도에 검출해 정통화.

| Contract                    | 강제 조항                                                                                       |
| --------------------------- | ----------------------------------------------------------------------------------------------- |
| `layered-core`              | §4.1 의존성 방향 (apps → infra → application → domain)                                          |
| `domain-purity`             | §4.1 domain은 framework 0개 (fastapi/pydantic/sqlalchemy/duckdb/boto3/kafka/pyspark/temporalio) |
| `application-no-vendor-sdk` | §4.3 application은 vendor SDK 0개 (위와 동일 + elasticsearch)                                   |
| `apps-no-direct-infra`      | §3.1 apps/\* 가 infrastructure.repositories 직접 import 금지 (composition root 예외만 명시)     |

`scripts/quality/check_infra_import_boundary.py` 와 `check_dependency_graph.py` 는
**보존**한다. import-linter는 transitive를 강제하고 우리 자체 게이트는
module-level baseline(0)을 강제 — 서로 다른 사각지대를 잡는 이중 망. 일반 모듈은
fan-out 10 이하를 유지하고, `CoreDependencies`/`ports`/`CoreService` 같은 명시적
composition/aggregation root만 현재 source/connector/auth/OSDK와 Pipeline DAG
port/adapter 경계까지 반영한 fan-out 37 budget을 쓴다. `RequestContext`, domain error,
primitive DTO, service base, ports aggregate처럼
대부분의 application service가 공통으로 가져야 하는 기반 모듈은 업무 결합도 fan-out 계산에서 제외하고,
service collaborator fan-out ≤10 게이트는 별도로 유지한다.

2026-07-29에는 `pipeline_async_run_service`, `pipeline_distributed_node_service`,
`pipeline_preview_service`를 Pipeline DAG의 명시적 orchestration/composition root로 등록했다.
이 예외는 일반 application service의 fan-out 10 제한을 넓히지 않으며, 세 service의 실제
collaborator 그래프는 기존 cycle 0, depth ≤7, service당 fan-out ≤10 게이트를 그대로 통과해야 한다.
또한 worker import 예외는 `pipeline_control`, `pipeline_dag`, `pipeline_node_subprocess` 세
composition entrypoint에서 `local_runtime`을 한 번 조립하는 경로로만 제한한다.

2026-07-16에는
`foundry_lite_worker.source_streaming -> foundry_lite.infrastructure.local_runtime` 한 경로만
worker composition-root 예외로 추가했다.
상시 Kraken/Kafka 프로세스가 다른 worker entrypoint와 동일하게 concrete local profile을 한 번
조립하기 위한 경계이며, worker 패키지 전체나 infrastructure 전체를 허용하지 않는다. 이 좁은
예외는 layered-core, `check_dependency_graph.py`, Tach DAG, 실제 broker 통합 테스트로 계속
보완하고, `tests/unit/test_quality_ci_workflows.py`가 broad worker 예외의 재도입을 차단한다.

### Tier P2.5 — Tach module DAG (✅ 완료 2026-06-11)

`tach.toml`은 import-linter보다 더 읽기 쉬운 **모듈 의존성 지도**를 제공한다.
import-linter가 "금지된 transitive import 경로"를 정밀 차단한다면, Tach는
`domain`, `application`, `infrastructure`, `apps/*`가 어떤 방향으로만 의존할 수
있는지 선언한다. 그래서 새 scale-foundation boundary가 추가될 때 "어느 층이 어느
층을 알아야 하는가"를 리뷰 감각이 아니라 DAG로 검증한다.

실행:

- 로컬/CI release gate: `uv run tach check --dependencies`
- package script: `pnpm quality:architecture`
- Self-test: `tests/unit/test_quality_ci_workflows.py`가 `ci_gate.sh`, `package.json`,
  `tach.toml`이 서로 끊기지 않았는지 검증한다.

### Tier P1 — Semgrep로 흡수된 게이트 (✅ 완료 2026-06-10 P1)

다음 게이트들은 `scripts/quality/semgrep-rules/foundry-lite.yml`의 9개 rule로 흡수되어
코드 모양 자체를 차단한다. 자체 self-test는 `tests/unit/test_quality_semgrep_rules.py`.
G9 (test sleep)는 Semgrep의 default tests 제외 동작 때문에 Semgrep으로 흡수하지 않고
`check_no_test_sleep.py` AST 게이트로 유지한다.

Semgrep은 제품·테스트 Python 환경과 의존성 해석을 공유하지 않는다. `click`처럼
정적 분석 도구의 상한 제약이 제품 런타임의 보안 업데이트를 막지 않도록
`uv tool run --from semgrep==1.169.0 semgrep ...` 격리 환경에서 실행하며,
게이트와 self-test가 같은 고정 버전 명령을 사용한다.

| 흡수된 게이트                        | Semgrep rule                                                                                                          |
| ------------------------------------ | --------------------------------------------------------------------------------------------------------------------- |
| G1 router transaction/repo 직접 호출 | `router-no-direct-transaction`, `router-no-repository-access`                                                         |
| G10 repository domain errors raise   | `repository-no-domain-errors`                                                                                         |
| §4.2 cross-service bare helper call  | `action-service-no-bare-audit`, `object-services-no-bare-runtime-helpers`, `dataset-services-no-bare-runtime-helpers` |
| §18.1 silent failure / bare except   | `no-bare-except-pass`                                                                                                 |
| §10.2 eval/exec                      | `no-eval-exec`                                                                                                        |
| §10.2 f-string SQL                   | `no-fstring-sql`                                                                                                      |
| §4.3 application/domain vendor SDK   | `application-no-vendor-sdk`                                                                                           |

### Tier P0.5 — AST-grep structural anti-magic (✅ 완료 2026-06-11)

AST-grep는 Semgrep보다 Python AST 패턴을 더 좁고 빠르게 검증하기 좋은 곳에 쓴다.
현재 첫 규칙은 `FoundryLite`가 다시 `__getattr__`/`__setattr__` 기반
method-registry magic dispatch로 돌아가는 것을 차단한다.

규칙:

- `scripts/quality/ast-grep-rules/no-facade-magic-dispatch.yml`
- 대상: `libs/foundry_lite/application/foundry.py` and `libs/foundry_lite/application/facades/*.py`
- 정량 기준: `__getattr__`/`__setattr__` 0건

실행:

- 로컬/CI release gate: `node scripts/quality/run_ast_grep.cjs scan -c sgconfig.yml`
- package script: `pnpm quality:ast-grep`
- Self-test: `tests/unit/test_quality_ci_workflows.py`가 임시 facade fixture에
  `__getattr__`를 심고, AST-grep가 실제 error finding을 내는지 검증한다.

### Tier 0 — 즉시 추출 가능 (정적 분석, 각 30~60분)

| ID  | 게이트                                   | 매핑 조항                             | 정량 기준                                                                                                                                       | Root cause                           |
| --- | ---------------------------------------- | ------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------ |
| G1  | `check_router_layer_purity.py`           | §3.1, §6.1, §18.3 Fat Router, §4.1 17 | `apps/api/**/*.py`에서 `core.<repo>_repository.*` 직접 호출 0, `engine.begin` 0, `transaction.execute` 0                                        | Fat Router 안티패턴 자체 차단        |
| G2  | `check_audit_on_mutation.py`             | §7, §10.2 45, §18.3 66                | mutation 메서드(`create_*`/`commit_*`/`apply_*`/`abort_*`)가 같은 메서드 body에서 `runtime_service._audit` 또는 `audit_repository` 접근. 위반 0 | mutation은 무조건 audit              |
| G3  | `check_function_length.py`               | §1.2 3, §2.2 9                        | `libs/foundry_lite/application` 함수 ≤ 40줄. baseline 0                                                                                         | 함수 거대화 차단                     |
| G4  | `check_dict_any_budget.py`               | §5.2 29, §18.3 61                     | `application/**/*.py` 함수 시그니처 `dict[str, Any]` total 0, layer baseline no growth                                                          | dict[str,Any]가 schema drift 통로    |
| G5  | `check_log_has_trace_keys.py`            | §8.3 38, §12.1 54                     | `logger.X(...)` 호출 시 `extra=` 또는 message에 `request_id`/`run_id`/`tenant_id` 중 1개 이상 포함. 위반 0                                      | 추적 키 끊김 차단                    |
| G6  | `check_pragma_no_cover_budget.py`        | §11.4 53                              | `# pragma: no cover` 총 카운트 baseline + monotonic decrease + 이유 주석 강제 (`# pragma: no cover  # reason: ...`)                             | 커버리지 우회 차단                   |
| G7  | `check_error_response_has_request_id.py` | §6.3 31, §8.3                         | FastAPI `HTTPException(detail=...)` / exception handler가 `request_id` 포함하는지 정적 검증                                                     | 사용자 응답에 추적 키 보존           |
| G8  | `check_tier_coverage_by_layer.py`        | §11.4 51                              | `artifacts/coverage/coverage.json` 파싱 후 `domain`/`application`/`infrastructure`/`apps/api`/`apps/cli` 각 영역 95%+                           | 평균 95%에 가려진 가난한 영역 노출   |
| G9  | `check_no_test_sleep.py`                 | §18.1 60                              | `tests/**/*.py` AST에서 `time.sleep`/`asyncio.sleep` 호출 0건                                                                                   | flaky 근원 차단                      |
| G10 | `check_repository_no_business.py`        | §3.1 13, §7.1 32, §18.3               | `infrastructure/repositories/*.py`에서 도메인 errors(`ValidationFailed`, `PermissionDenied`, `ConflictDetected`) raise 0건                      | Repository에 비즈니스 규칙 침투 차단 |
| G1A | `check_query_side_effects.py`            | §2.3 11                               | read/query service entrypoint side-effect violation 0                                                                                           | 조회가 상태를 바꾸는 root cause 차단 |
| C1  | `check_boolean_naming.py`                | §2.1 8                                | `libs`/`apps`/`scripts` boolean argument와 annotated field 이름 violation 0                                                                     | 뜻이 애매한 boolean 스위치 차단      |
| S1  | `check_tenant_write_guard.py`            | §10.2 44                              | tenant-scoped SQLAlchemy insert/update/delete tenant guard violation 0                                                                          | 다른 tenant 데이터 변경 차단         |
| M1  | `check_metrics_exposed.py`               | §12.2 55                              | Prometheus payload에 required operational metrics 7개 노출                                                                                      | 운영 계기판 누락 차단                |

### Tier 1 — 중간 (정적 + 마커, 각 1~3시간)

| ID   | 게이트                                  | 매핑 조항                                                                       | 정량 기준                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | Root cause                                                 |
| ---- | --------------------------------------- | ------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- | ------------------- |
| G11  | `check_transaction_outbox_pair.py`      | §7.1 33, §10.2 audit/outbox                                                     | service mutation transaction block에서 repository write와 audit/outbox proof를 같은 call tree에 강제                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | outbox/audit 누락 차단                                     |
| G12  | `check_idempotency_on_action.py`        | §6.3 30, §7.3                                                                   | API `Idempotency-Key` header, Core/Service required parameter, existing action_run replay, request fingerprint comparison, idempotency-conflict audit, schema unique constraint and fingerprint column. 위반 0                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | 중복 액션·잘못된 replay 차단                               |
| G13  | `check_contract_test_per_port.py`       | §4.3 27                                                                         | `libs/foundry_lite/application/ports/*.py`마다 `tests/contracts/test_*_contract.py` 1:1 존재. 누락 0                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | 새 boundary가 부정통하게 들어오는 것 차단                  |
| D1   | `check_strategy_specification_tests.py` | §4.2 22                                                                         | strategy/specification module missing direct test 0                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | 조건 규칙이 API/Core 테스트에 갇히는 문제 차단             |
| G14  | `check_doc_drift.py`                    | repo Markdown, `docs/*.json`, AGENTS.md, implementation-status.md, package.json | 문서에 명시된 source path/script/package script/API route/pytest node id/Markdown local link target과 anchor가 실재하고, current-state 문서의 클래스/메서드 이름이 코드에 실재하는지 AST 검증                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | 문서 과장·예전 파일/API/test/명령 이름·깨진 문서 링크 차단 |
| G14A | `check_evidence_ledger_commands.py`     | sprint-evidence-ledger.md, package.json, tests                                  | evidence ledger의 proof command가 실제 package script, script file, test path, pytest node id를 가리킴. 위반 0                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | 증거 장부 명령 stale 차단                                  |
| G14B | `check_documentation_map.py`            | documentation-map.md, README.md, AGENTS.md                                      | repo Markdown + `docs/*.json` role coverage와 doc-drift scan inventory 일치, MECE taxonomy bucket 정의 누락/stale/duplicate/thin row 0, Document Roles MECE bucket 누락/unknown 0, core source-of-truth rows 누락/stale/duplicate 0, core operating Markdown top-matter context 누락 0, source-of-truth/document-role placeholder row 0, update-order reference 누락 0 및 README-last 순서 보존, README source-of-truth links 누락/중복 0 및 placeholder 설명 0, README 대표 gate refs 누락/중복 0 및 placeholder 설명 0, proof-matrix/source-of-truth/operator-evidence/doc/API/SDK/product cross-check command 누락/stale/duplicate 0 및 target 존재, AGENTS active documentation/proof gate reference 존재. 위반 0 | 문서 지도 stale/MECE drift 차단                            |
| G14C | `check_data_platform_sprint_status.py`  | sprint plan, sprint breakdown, README.md, implementation-status.md              | S46-S64 status table token/label과 high-level current/future boundary가 문서 간 일치. 위반 0                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | 스프린트 상태 drift 차단                                   |
| G15A | `check_schema_revision_guard.py`        | §18.1 62                                                                        | SQLAlchemy metadata fingerprint와 최신 `infra/schema_revisions` snapshot 일치. mismatch 0                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | DB 모양 code-only 변경 차단                                |
| G15  | `check_integration_scenario_markers.py` | §11.4 52                                                                        | `@pytest.mark.integration_scenario("connector_sync"                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | ...)` 마커가 가이드 §11.4 7개 시나리오 모두 존재           | 통합 100% 약속 검증 |

### Tier 2 — 동적 / 메타 (런타임 분석, 각 3~8시간)

| ID   | 게이트                                      | 매핑 조항              | 정량 기준                                                                                    | Root cause                     |
| ---- | ------------------------------------------- | ---------------------- | -------------------------------------------------------------------------------------------- | ------------------------------ |
| G16  | `check_trace_continuity.py`                 | §4.3 24, 26, §12.3     | demo smoke 실행 중 OTel span 수집 → request span 안의 service span/DB span의 `trace_id` 동일 | trace key 끊김 검출            |
| G16A | `check_adapter_error_trace_keys.py`         | §4.3 24                | FAILED run error trace key violation 0                                                       | adapter 실패 추적 키 보존      |
| G16B | `check_failed_mutation_state_runtime.py`    | §1.2 4, §7.1, §10.2 45 | failed run + aborted transaction + abort audit violation 0                                   | 실패 mutation 장부 불일치 차단 |
| G17  | `check_audit_count_runtime.py`              | §10.2 45, §18.3 65     | demo smoke 후 `audit_events` row 수 ≥ mutation 호출 카운트. 차이 0                           | audit 누락 동적 검증           |
| G18  | `check_outbox_consistency.py`               | §7.1 33                | demo smoke 후 state change ↔ outbox event 1:1 매칭. 불일치 0                                 | outbox 약속 동적 검증          |
| G19  | `check_flaky_detector.py`                   | §11.4 47               | pytest 3회 반복 후 결과 변동 0                                                               | flaky를 통과로 보지 않음       |
| G20  | `check_gate_self_test.py` (future/deferred) | (메타)                 | 모든 quality 게이트가 자체 fixture로 violation 인공 생성 시 정확히 fail하는지 확인           | 게이트의 거짓 negative 차단    |

### Tier 3 — Root cause 메타 게이트 (PR/git 통합, 1일+)

| ID  | 게이트                                          | 매핑 조항             | 정량 기준                                                                                 | Root cause                   |
| --- | ----------------------------------------------- | --------------------- | ----------------------------------------------------------------------------------------- | ---------------------------- |
| G21 | `check_regression_test_per_bugfix.py`           | §11.3, §18.1, §18.2 4 | git log → "fix"/"bug"/"patch"/"regression" 커밋은 같은 커밋에 `tests/` 변경 동반          | 버그 수정에 회귀 테스트 강제 |
| G22 | `check_pr_root_cause_section.py`                | §18.1, §18.2          | PR description에 `Root Cause`, `Impact`, `Regression Test` 섹션 강제 (GitHub Actions)     | 증상 제거 패치 차단          |
| G23 | `check_anti_pattern_count.py` (future/deferred) | §18.3 표 12종         | 12개 안티패턴을 정적 패턴(AST/regex)으로 인코딩 → 총 카운트 baseline + monotonic decrease | 안티패턴 monotonic decrease  |

---

### Tier G21/G22 — root-cause patch prevention (✅ 완료 2026-06-11)

`scripts/quality/check_regression_test_per_bugfix.py`와
`scripts/quality/check_pr_root_cause_section.py`는 가이드 §18.1/§18.2의 “증상 제거
패치 금지”를 git/PR boundary에서 강제한다.

검사 기준:

- 커밋 subject/body에 `fix`, `bug`, `patch`, `regression` 계열 단어가 있으면 같은
  커밋에 `tests/` 변경이 있어야 한다.
- PR 본문에는 `Root Cause`, `Impact`, `Regression Test` 섹션이 있어야 한다.
- 로컬 non-PR 실행에서는 PR 본문 검사를 skip하고 JSON report에 `skipped: true`를
  남긴다.
- GitHub Actions는 `fetch-depth: 0`으로 checkout해 branch commit range를 볼 수
  있게 하고, pull request event에서는 PR 본문을 `GITHUB_EVENT_PATH`에서 읽는다.
- 결과는 `artifacts/quality/regression_test_per_bugfix.json`과
  `artifacts/quality/pr_root_cause_section.json`에 남긴다.

Self-test: `tests/unit/test_quality_regression_test_per_bugfix.py`가 bugfix commit without
tests 실패, bugfix commit with tests 허용, non-bugfix commit 제외, JSON report 생성을
검증한다. `tests/unit/test_quality_pr_root_cause_section.py`는 필수 섹션 통과, 누락
섹션 실패, GitHub event body load, non-PR event skip, JSON report 생성을 검증한다.
`.github/pull_request_template.md`는 새 PR이 원인/영향/회귀 테스트를 기본으로 쓰게 한다.

## 3. 우선순위 실행 순서

### 즉시 (다음 세션 첫 1~2시간)

**가장 ROI 높은 7개:**

1. **G1 `check_router_layer_purity`** — ✅ 완료 2026-06-11, API direct DB/repository 접근 차단
2. **G10 `check_repository_no_business`** — ✅ 완료 2026-06-11, Repository 비즈니스 침투 차단
3. **G13 `check_contract_test_per_port`** — ✅ 완료 2026-06-11, 새 boundary 정통화 강제
4. **G14 `check_doc_drift`** — ✅ 완료 2026-06-11, 문서가 코드보다 거짓말 못 함
5. **G14A `check_evidence_ledger_commands`** — ✅ 완료 2026-06-19, 증거 장부 명령 stale 차단
6. **G14B `check_documentation_map`** — ✅ 완료 2026-06-19, 문서 지도 자체가 낡는 문제 차단
7. **G14C `check_data_platform_sprint_status`** — ✅ 완료 2026-06-19, S46-S64 상태 drift 차단

각 게이트는 **반드시 self-test와 함께** 박는다 (G20 원칙).

### 중기 (다음 2~3 세션)

5. G8 영역별 95% — ✅ 완료 2026-06-11, 평균 게이트의 사각지대 청산
6. G2 audit on mutation — ✅ 정적 완료 2026-06-11, 보안 부채의 핵심
7. G6 pragma budget — ✅ 완료 2026-06-11, coverage 우회 차단 baseline 0
8. G17 runtime audit count — ✅ 완료 2026-06-11, 데모 mutation과 audit_events 대조
9. G16 trace continuity (동적) — ✅ 완료 2026-06-11, request/service/DB trace 연결 검증

### 장기 (Sprint 02A 마무리 후)

- G21 git hook (regression test per bugfix) — ✅ 완료 2026-06-11, release gate와 CI PR boundary에 연결
- G22 GitHub Action (PR Root Cause section) — ✅ 완료 2026-06-11, PR template과 event body 검사 연결
- G15 integration scenario marker — ✅ 완료 2026-06-11, MVP release 통합 시나리오 7/7 마커 강제
- G18 동적 outbox consistency — ✅ 완료 2026-06-11, state change와 outbox_events 동적 대조
- G16B failed mutation state — ✅ 완료 2026-06-12, FAILED run/ABORTED transaction/audit 동적 대조
- G19 flaky detector — ✅ 완료 2026-06-11, pytest-randomly + xdist 명령 3회 반복

---

## 4. 추가 도구 후보 (남은 것)

다음 도구들은 아직 gate로 도입하지 않은 후보들이다. 이미 도입한
CodeQL, Semgrep, AST-grep, Tach, import-linter, vulture, interrogate,
Pyright strict, pytest-randomly, pytest-xdist, gitleaks는 위 Tier 섹션으로
승격했다.

### S46 — Semantic SSOT / Data Pattern Matrix

[Data Platform Expansion Sprint Plan](./data-platform-expansion-sprint-plan-ko.md)의 첫 번째
post-MVP 작업은 문서와 machine-readable matrix가 서로 다른 현재 상태를 주장하지 못하게
하는 것이다. 2026-06-18 현재 S46 정적 게이트가 구현되어 static lane에 연결되었다.
S47 이후 product/data 기능은 이 matrix와 semantic-doc consistency gate를 먼저
통과해야 한다.

| 게이트                             | 명령                               | Root cause                                                                              |
| ---------------------------------- | ---------------------------------- | --------------------------------------------------------------------------------------- |
| Semantic documentation consistency | `quality:semantic-doc-consistency` | active-covered infra를 future라고 쓰거나 deferred 기능을 current로 쓰는 문서 drift 차단 |
| Data engineering pattern matrix    | `quality:data-pattern-matrix`      | 데이터 엔지니어링 패턴 gap이 owner/reason/future test 없이 사라지는 것 차단             |

### S47 — Record DLQ + Replay

S47의 첫 runtime ratchet은 stream CDC archive에서 poison CDC envelope 한 건이
전체 batch를 멈추거나 조용히 유실되는 문제를 막는다. `quality:record-dlq-replay`는
불량 record quarantine, 정상 record commit 지속, DLQ storage failure fail-closed
증거와 Operations API/typed generated SDK replay 증거를 runtime lane에서 확인한다.
현재 gate는 유효한 저장 DLQ payload가 원래 raw stream archive dataset으로 실제 APPEND
replay되고, 실패 payload는 `FAILED` replay result와 audit evidence로 남는지 확인한다.
Web Operations UI도 record DLQ 목록/상세/영향 미리보기/replay/discard 결과를 노출한다.
또한 source-side 오류율 threshold 초과, identity/ordering 오류 fail-closed, PostgreSQL 동시
replay request 한-winner proof를 runtime lane에서 확인한다. transform-level Record DLQ
policy는 transform record DLQ가 생길 때 future scope로 확장한다.

| 게이트                    | 명령                        | Root cause                                                                             |
| ------------------------- | --------------------------- | -------------------------------------------------------------------------------------- |
| Record DLQ replay ratchet | `quality:record-dlq-replay` | 입력 record DLQ 저장 실패나 poison record 때문에 정상 record가 함께 유실되는 문제 차단 |

### S48 — Late Data + Watermark

S48의 첫 runtime ratchet은 stream/source archive에서 event 발생 시간과 처리 시간을
섞어 늦은 데이터를 정상 데이터처럼 조용히 흘려보내거나, partition/source watermark를
과거로 되돌리는 문제를 막는다. `quality:late-data`는 source별 event-time/source-time
field, named timezone override, lateness threshold가 archive row 및 Record DLQ에 남는지 확인하고, 너무 오래된
event가 `TOO_LATE` Record DLQ로 격리되는지 검증한다. `quality:watermark`는 같은
stream/source/partition에서 더 오래된 accepted event가 들어와도
`dataset_transactions.metadata.lateDataWatermark.watermarkEventTime`이 뒤로 이동하지
않는지 확인하고, 느린 partition/source가 관련 없는 빠른 partition/source watermark 때문에
stale로 오분류되지 않는지, 그리고 이미 commit된 늦은 event가 재전달될 때 durable offset
이후부터 재개해 두 번째 dataset version을 만들지 않는지도 고정한다.
또한 늦은 event가 이전에 닫힌 archive dataset version에 영향을 주면
`lateDataReprocessingPlan`을 transaction metadata에 남기고, Operations run detail이
event-time lag summary, plan, 정규화된 `platformWatermark`를 보여주는지 확인한다. 그리고 CDC index run의
`LATE_REQUIRES_REPROCESS` cursor evidence가 다음 materialization commit metadata의
`materializationDetail.watermark`와 `reopen.reason=late_data_reprocess`로 이어져
Operations materialization detail에 보이는지도 고정한다. 이제 같은 late-data evidence가
object explain/materialization detail의 `lateDataBadge`와 Operations run detail의
`downstreamImpact` graph까지 이어지는지도 고정한다. 같은 gate는 materialization output
dataset transaction이 action-log/object-snapshot boundary를 top-level `platformWatermark`로
남기는지도 확인한다. 같은 gate는 snapshot transform output
commit이 입력 dataset transaction의 `platformWatermark`를 min-input event-time 정책으로
전파하고, 입력 version/reprocessing evidence를 output dataset version metadata에 남기는지도
확인한다.

| 게이트            | 명령                | Root cause                                                                                                                                                                                                                                                                                                               |
| ----------------- | ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Late-data ratchet | `quality:late-data` | event-time/source-time/process-time 혼동으로 too-late event가 정상 archive에 섞이는 문제 차단                                                                                                                                                                                                                            |
| Watermark ratchet | `quality:watermark` | 늦게 도착한 event 때문에 source/partition watermark가 과거로 되돌아가거나 source timezone 없는 timestamp가 UTC로 오분류되거나 다른 partition watermark가 느린 partition을 오분류하거나 중복 재전달이 두 번째 commit을 만들거나 normalized platform watermark/reprocessing/materialization boundary watermark/reopen/badge/downstream-impact/transform-output watermark evidence가 사라지는 문제 차단 |

### S49 — Multi-file Dataset + Partitioning

S49의 runtime ratchet은 dataset preview/read path가 manifest의 첫 data file만
읽거나, storage directory/bucket listing으로 manifest에 없는 orphan file을 serving data에
섞는 문제를 막는다. `quality:multi-file-dataset`은 storage adapter contract가 manifest-listed
files를 순서대로 resolve하는지, public `foundry.datasets.preview(...)`가 multi-file manifest를
같은 facade로 읽는지, 그리고 unlisted file을 listing으로 발견하지 않는지 확인한다.
같은 gate는 readable Parquet commit path가 file-level `column_stats` min/max/null_count와
dataset `sort_order` 기반 `sort_bounds`를 manifest에 남기는지, local/fake storage에서 여러
staged parquet part가 하나의 deterministic manifest/version/DB file-row set으로 commit되고
검증 후 part tamper와 stale retry orphan part가 차단되는지도 확인한다.
`quality:partition-pruning`은 local/fake read/preview path에서 `partition_filter`가
manifest file의 `partition_values`와 matching되어 실제 read file 수를 줄이는지 확인한다.
S3와 Iceberg의 profile-specific branch는 각각 `quality:s3-storage`와 `quality:iceberg`
ratchet에 포함한다. S3는 multi-part staged files가 timeout retry 뒤에도 exact
part set으로 커밋되고, Iceberg는 multi-part staged files가 하나의 pinned snapshot 안의
여러 data file로 커밋되며 duplicate version은 conflict로 남는지 확인한다. Transform
predicate pushdown, high-cardinality partition warning, Iceberg column-stat file-level
pruning은 S49 partial proof에 포함됐고, Iceberg maintenance execution은 S50 gate에서
별도 검증한다.

| 게이트                              | 명령                         | Root cause                                                                                    |
| ----------------------------------- | ---------------------------- | --------------------------------------------------------------------------------------------- |
| Multi-file dataset manifest ratchet | `quality:multi-file-dataset` | 첫 파일만 읽거나 bucket listing으로 manifest 밖 file을 serving data에 섞는 문제 차단          |
| Partition pruning ratchet           | `quality:partition-pruning`  | partition predicate를 결과 row filter로만 처리해 불필요한 parquet files를 계속 읽는 문제 차단 |

### S50 — Iceberg Maintenance Planning And Execution

S50 runtime ratchet은 운영자가 maintenance 전에 봐야 할 dry-run plan과 bounded execution
slice를 함께 고정한다. `quality:iceberg-maintenance`는
`foundry.operations.plan_iceberg_maintenance(...)`,
`foundry.operations.run_iceberg_maintenance(...)`, Operations API/SDK surface가 DB committed
dataset version이 참조하는 Iceberg snapshot을 삭제 후보에서 제외하고,
compaction candidate/orphan/protected/retained snapshot preview를 audit evidence와 함께
남기는지 검증한다. Maintenance run은 current snapshot을 compact snapshot으로 rewrite하고,
전후 logical row hash가 같은지 검증하며, 삭제 가능한 orphan snapshot만
`expire_snapshots().by_ids(...).commit()`으로 만료한 뒤 더 이상 reachable하지 않은 data/sidecar
file만 cleanup한다. Transform/materialization/backup pin 전체 보존 정책, compaction 실패 후
serving pointer rollback proof, concurrent maintenance fencing, ambiguous retry idempotency는 다음
S50 slice로 남긴다.

### S51 — Continuous CDC Worker Loop

S51의 current runtime ratchet은 full rebalance/fencing system이 아니라 bounded worker
loops를 고정한다. `quality:cdc-continuous-worker`는 stream archive worker가 기존
`archive_stream_events` transaction/checkpoint boundary를 여러 batch에 반복 적용하고,
configured empty poll 또는 stop callback에서 멈추며, committed cursor를 따라 no-gap/no-duplicate
archive result를 남기는지 검증한다. 현재 worker loop는 `workflow_runs` lease ledger를 통해
worker owner/token/expiry를 기록하고, lease를 잃은 old worker가 다음 batch를 archive하지
않고 `lease_lost`로 멈추는 proof까지 포함한다. 같은 gate는 `worker:cdc-object-indexer`가
committed CDC dataset version을 version 순서로 index하고, workflow output cursor
(`lastIndexedVersionId`/`lastIndexedVersionNumber`/`lastIndexRunId`)에서 재시작하며,
limit 초과 source version을 일부 처리로 성공시키지 않고 fail-closed 하는지도 검증한다.
`quality:kafka-rebalance`는 stream archive worker가 batch를 읽은 뒤 commit 직전
partition assignment guard를 다시 확인하고, revoke가 감지되면 dataset version/cursor를
만들지 않은 채 failed sync evidence와 `assignment_revoked` stop reason을 남기는지 검증한다.
Source managed Sync의 all-partition discovery, partition별 durable cursor map, standby lease takeover,
Dataset branch single-active guard, Data Connection health rule surface는 별도 product ratchet으로 active다.
Production daemon packaging, durable partition assignment history, parallel broker consumer threads,
real broker rebalance revoke callback integration, broker commit-unknown reconciliation,
durable incident/notification delivery, OS SIGTERM finish-or-abort proof는
다음 S51 slice로 남긴다.

### S52 — Temporal Engine Integration

S52의 runtime ratchet은 product workflow control-plane 계약과 worker-bound local connector
data-plane activity proof를 고정한다. `quality:temporal-engine-integration`은
`ConnectorSyncWorkflow`가 Operations facade/API/generated SDK에서 stable
`Idempotency-Key` 기반 workflow id로 시작되는지, local/fake workflow adapter와 Temporal
time-skipping worker가 같은 `ProductWorkflowRun` shape를 반환하는지, 그리고
Operations audit detail이 `workflowRunId`와 `foundryRunId`를 서로 연결해 Temporal 내부
로그 없이도 운영자가 추적할 수 있는지 검증한다. 같은 product connector test는
Temporal worker activity가 deterministic `syncRunId`로 local connector page fetch,
raw staging write, quality check, dataset commit, cursor advance, and
`dataset.version.committed` evidence를 normal Dataset ingest boundary로 남기는지도
검증한다. Post-commit activity retry는 같은 committed sync run을 재사용해 duplicate
dataset version/outbox를 만들지 않는다. Workflow input은 `workflowDefinitionVersion`을
포함하고, `start_unknown` 재시도는 처음 ledger에 저장된 input을 다시 adapter에 전달해
새 코드 기본값으로 조용히 바뀌지 않는지 검증한다. Workflow
adapter start exception 회귀 테스트는 retryable timeout/unavailable을 `start_unknown`으로
남겨 재시도 가능하게 만들고, permanent exception은 `failed`로 닫아 `starting` stuck을 막으며,
adapter failure evidence를 scrub한다. Cancel cleanup은 adapter-returned run id mismatch뿐 아니라
terminal non-cancelled adapter result도 fail-closed로 거부한다.
Production connector packaging/config persistence, continue-as-new, full old-history workflow code upgrade
replay, managed Temporal worker operations는 다음 S52 slice로 남긴다.

| 게이트                              | 명령                                  | Root cause                                                                                                                                                   |
| ----------------------------------- | ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Temporal engine integration ratchet | `quality:temporal-engine-integration` | workflow adapter는 통과하지만 실제 제품 Operations 경로가 Temporal profile과 다른 계약을 반환하거나 workflow run과 Foundry audit evidence가 끊기는 문제 차단 |

### S53 — External Writeback + Saga/Reconciliation

S53의 첫 runtime ratchet은 외부 응답 유실과 simulated external success/local mutation
failure를 안전한 first-class 상태로 고정했다. `quality:external-writeback`은
simulated before-commit writeback에서 response loss/timeout 성격의 상황이 `failed`로
오분류되지 않고 `outcome_unknown` action run, writeback row, error detail, audit evidence로
남는지 검증한다. 같은 `Idempotency-Key` replay는 새 writeback을 발행하지 않고 기존
unknown run을 반환해야 한다. `quality:saga-reconciliation`은 simulated external success 뒤
local mutation failure가 `compensation_required` action run/writeback/audit evidence로 남고
same-key replay가 두 번째 writeback을 만들지 않는지 검증한다. 같은 gate는 operator가
확인한 remote success evidence를 resolve API/SDK로 제출하면 원래 local object mutation을
따라잡고 action/writeback을 `reconciled`로 닫으며 concurrent resolve는 한 winner만
남기는지도 검증한다. 또한 sensitive action parameter가 reconciliation 처리에는 사용되지만
Operations/audit evidence에 raw로 노출되지 않는지 검증한다.

L8 runtime ratchet은 실제 CRM/ERP 전용 connector 패키지가 아니라 현재 repo의 real
external system proof로 `ExternalWritebackAdapter`와 `S3ExternalWritebackAdapter`를 추가했다.
`quality:action-writeback-live`는 live MinIO에 대한 real PUT/HEAD를 통해 connection timeout이
`outcome_unknown`으로 남는지, real write가 landed 된 뒤 local mutation commit이 실패하면
`compensation_required`로 남는지, 그리고 `reconcile_action_writeback`이 real `remote_lookup`으로
그 writeback을 닫고 local mutation을 한 번만 적용하는지 검증한다.

`quality:writeback-reconciliation-worker`는 unresolved writeback을 사람이 직접 하나씩
resolve하지 않아도 bounded worker tick이 stored `externalWritebackUri`로 remote lookup을 수행해
같은 reconciliation transaction을 타고 local mutation을 따라잡는지 검증한다. URI가 없는 row는
추측하지 않고 skip evidence로 남기며, tick summary는 `action.writeback.recovery_tick` audit과
worker JSON evidence로 남아야 한다. 민감 action parameter가 있는 row는 worker가 remote lookup
또는 local mutation을 자동 실행하지 않고 `operator_approval_required` skip item과
`approvalRequired`/`approvalReason` evidence를 남겨야 한다.

`quality:action-writeback-approval-release`는 approval-required로 멈춘 민감 writeback을
명시적 approval id/reason이 들어온 경우에만 같은 reconciliation transaction으로 복구하고,
`action.writeback.recovery_approved` audit evidence를 남기는지 검증한다. Retryable row는
외부 시스템이 바뀌지 않은 상태이므로 approval-recoverable 대상이 아니며 fail-closed로 남아야 한다.
ERP-specific connector packaging, always-on managed compensation daemon, reverse/compensation execution,
persistent reconciliation queue UI, full managed approval workflow/operator UI는 future scope다.

`quality:agent-vendor-egress`는 AI agent가 raw HTTP/vendor/webhook/generic executor tool을
published manifest와 agent allowlist에 넣어도 직접 실행하지 못하도록 고정한다. ToolBroker는
executor 호출 전에 `direct_vendor_tool_denied`로 닫고, Agent Runtime은 실패 run evidence를
남기며, Visual Builder는 같은 tool id가 release-ready draft가 되지 않게 막아야 한다.
Connector-backed vendor tool packaging과 full managed approval workflow/operator UI는 future scope다.

`quality:action-writeback-retryable`은 외부 시스템이 변경되지 않았다는 증거가 있는
before-commit 실패를 `retryable` action/writeback 상태로 구분한다. 이 gate는
`external_system_changed=false`, `last_observed_status=not_changed`, `retry_after_seconds`,
`action.run.retryable` audit evidence, same-key replay proof, unresolved queue filter/SDK type,
그리고 bounded recovery의 `retryable_external_not_changed` skip evidence를 요구한다. 자동
재발행 worker와 ERP별 retry policy는 아직 future scope다.

| 게이트                             | 명령                          | Root cause                                                                                                                                                                                                                                        |
| ---------------------------------- | ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| External writeback outcome ratchet | `quality:external-writeback`  | 외부 시스템에 이미 반영됐을 수도 있는 요청을 실패로 착각해 무작정 재시도하거나, outcome-unknown 증거 없이 local state만 남기는 문제 차단                                                                                                          |
| Action writeback retryable ratchet | `quality:action-writeback-retryable` | 외부 시스템이 바뀌지 않은 실패를 generic failed/outcome_unknown으로 뭉개거나 bounded recovery가 자동 reconcile/reissue하는 문제 차단 |
| Action writeback approval release ratchet | `quality:action-writeback-approval-release` | 민감/high-risk writeback을 approval evidence 없이 자동 복구하거나, retryable 외부 미변경 row를 승인으로 잘못 닫는 문제 차단 |
| Saga reconciliation ratchet        | `quality:saga-reconciliation` | 외부 시스템 성공 뒤 local state가 바뀌지 않은 divergence를 숨기거나 같은 idempotency key replay/동시 reconcile로 두 번째 writeback 또는 두 번째 local mutation을 만들거나, 민감 writeback parameter를 audit/Operations에 raw로 노출하는 문제 차단 |
| Live external writeback ratchet    | `quality:action-writeback-live` | simulated flag가 아니라 real adapter timeout/landed-write/local-failure/remote-lookup 경로를 live MinIO로 검증해 실제 외부 side effect가 실패/보상 상태로 오분류되지 않게 차단 |
| Writeback reconciliation worker ratchet | `quality:writeback-reconciliation-worker` | unresolved writeback batch를 추측으로 닫거나 worker 실패를 운영 증거 없이 삼키거나, remote lookup 없이 local mutation을 따라잡거나, 민감 writeback을 승인 증거 없이 자동 복구하는 문제 차단 |
| Agent vendor egress ratchet        | `quality:agent-vendor-egress`   | AI agent가 ToolBroker manifest/allowlist를 악용해 raw HTTP/vendor/webhook/generic executor를 직접 실행하거나, Builder가 그런 draft를 release-ready로 표시하는 문제 차단 |

### S54 — Data Quality Contracts

S54의 runtime ratchets는 완전한 DataContract policy surface가 아니라 현재
dataset quality check 결과가 어떤 staged candidate fingerprint와 schema row/version을
기준으로 생성됐는지 고정한다. `quality:data-contracts`는 dataset commit path가
`dataset_check_results`에 `checked_manifest_hash`,
`validated_against_schema_version_id`, `validated_against_schema_version`, 그리고 활성
default DataContract version이 있는 경우 `data_contract_version_id`를 저장하는지,
그리고 dataset quality repository contract가 같은 값을 fake/SQLite/PostgreSQL
profile에서 잃지 않는지 검증한다. 같은 gate는 이후 새 schema version이 생겨도
historical check result가 당시 schema row/version에 pinned되는지, 품질검사 후 staged candidate가
변경되면 final storage commit 전에 reject되는지, 성공 row가 `PASS`로 저장되는지,
warning severity failure가 non-blocking `WARN`으로 보이는지, commit-time hard
failure가 `BLOCK_COMMIT`으로 표면화되는지도 검증한다. 또한 row-level
`not_null`/`unique` quarantine check가 실패 record를 Record DLQ에
`DATA_QUALITY_CONTRACT`로 격리하고, 정상 record만 남긴 staged candidate를 재검증해
commit하는지도 검증한다. Operations run detail이 같은 transaction의 quality
summary/schema reference/checked manifest hash/check result와 data-quality quarantine
failed-row sample을 노출하는지도 검증한다.
Persisted quality contract check definition이 API/SDK에서 create/list/update되고 enabled/config 변경이
활성 contract snapshot이 없을 때 다음 dataset commit validation에 적용되는지도 검증한다.
Dataset quality result history와 summary가 dataset 단위 API/SDK surface에서 persisted commit-time
check result, manifest hash, schema-version reference, data contract version id, run id, transaction id,
status/check-type count evidence를 잃지 않는지도 검증한다.
Default DataContract version create/list/get/activate API/SDK는 `dataset_quality_contract_versions`
row의 check snapshot/schema reference/owner evidence를 만들고, 활성 version이 이후 commit validation을
live check row가 아니라 snapshot 기준으로 고정하는지 검증한다. Custom contract-key activation, full
DataContract policy surface, owner notification, dedicated failed-row sample UI, trend UI,
production DB schema race proof는 다음 S54 slice로 남긴다.

| 게이트                        | 명령                     | Root cause                                                                                                                                                                                                                                                                                                                                                                    |
| ----------------------------- | ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Data quality contract ratchet | `quality:data-contracts` | dataset quality check가 어떤 candidate/schema/contract version을 기준으로 통과했는지 잃어버리거나 historical run reference가 새 schema/check definition으로 오염되거나 warning을 hard failure처럼 막거나 검사 후 candidate tamper/hard failure를 commit하거나 row-level quarantine record를 조용히 유실하거나 Operations run detail에서 품질 리포트/contract version/failed-row sample 증거가 사라지거나 persisted check definition create/list/update API/SDK surface, default contract version create/list/get/activate API/SDK surface, active snapshot commit validation과 accepted-values policy 반영, dataset quality result history/summary API/SDK surface가 persisted run evidence나 status/check-type count evidence를 잃는 문제 차단 |

### S55 — DB/Dataset/Ontology Schema Migration

S55의 첫 static ratchet은 full migration product surface가 아니라 Alembic
migration history를 운영 친화적으로 만드는 안전장치다.
`quality:schema-migrations`는 migration file revision id가 filename prefix와 맞는지,
history가 하나의 root/head로 이어지는지, `down_revision`이 존재하는 migration을
가리키는지, 그리고 `downgrade()`가 destructive schema operation을 수행하지 않고
forward-fix/restore-runbook-first 정책으로 명시 실패하는지 검증한다.
Fresh-DB Alembic parity와 SQLAlchemy metadata fingerprint는 기존
`test_migrations_to_head_match_metadata_tables_and_columns`와
`quality:schema-revision`이 계속 맡는다. 두 번째 S55 slice는 전용 `db:migrate`
runner와 `quality:schema-migration-runner`/`quality:schema-migration-runner-live`를 추가해 앱/worker startup이 migration을
숨겨 실행하지 않고, 한 DB에서 동시에 뜬 migration job 중 하나만 Alembic을 실행하는지
검증한다. PostgreSQL은 advisory lock 경로를 쓰고, live PostgreSQL contention proof는 실제 Postgres 연결에서 한 runner만 migration callback을 실행하고 다른 runner가 `lock_busy` evidence를 남기는지 확인한다. 로컬 proof는 SQLite `BEGIN IMMEDIATE`
DB lock으로 같은 위험을 재현한다. 세 번째 S55 slice인 expand-contract guard도 같은
`quality:schema-migrations` 안에 들어간다. 모든 migration은 `migration_phase`와
`release_compatibility`를 선언해야 하고, expand 단계는 `old_and_new_app` window에서
compatible add/table/index/backfill SQL만 허용하며 기본값 없는 새 `NOT NULL` 컬럼을
막는다. contract 단계는 `new_app_only` window라도 old-writer reject와 release
window proof가 생기기 전까지 fail-closed로 차단된다. 같은 gate는 `tenant_id`를 가진
새 `ai_` 테이블이 PostgreSQL `ENABLE/FORCE ROW LEVEL SECURITY`와 tenant policy 없이
추가되는 것도 차단해, `create_database()` bootstrap과 Alembic upgrade 경로의 tenant
isolation drift를 막는다. 다음 S55 slice는
`quality:schema-evolution`으로 Dataset commit 직전 schema change를 compatible,
warning, blocked로 분류한다. Rename/drop/type narrowing/primary-key change는 blocking
change로 남고, numeric widening/deprecated field/non-null default backfill은 warning
또는 deterministic backfill progress metadata로 남는다. 다음 S55 slice는
`quality:ontology-migrations`로 active ontology와 candidate YAML을 비교해 property
rename/removal, object type removal/primary-key change, link endpoint/backing change,
required action parameter 추가 같은 consumer/SDK-breaking 변경을 activation 전에 막고,
deprecated property와 object reindex 필요 변경은 audit/outbox evidence로 남긴다.
Property mapping warning은 이전/다음 source column을 같이 남기고, additive Dataset schema
evolution 뒤 Ontology mapping을 새 column으로 옮긴 다음 latest dataset version 기준으로
bounded reindex가 object serving/property lineage를 갱신하는지 증명한다. Bounded object
reindex executor slice는 `object_reindex:*` key를 실제 index run으로 연결하고, 성공 뒤
active object type의 serving contract를 `object_reindex_complete`로 닫는다.
full old/new app deployment window, object/link remap executor, 실제 backfill worker,
progress update API, rollback/restore runbook은 후속 S55 slice다.
Runner evidence slice는 `db:migrate`가 성공, lock busy, 실패 결과를
password-masked JSON artifact로 남기게 해 operator가 실패 revision, lock 획득 여부,
에러 타입과 메시지를 확인할 수 있게 한다. 이는 restore runbook 자체가 아니라,
나중의 restore rehearsal이 참조할 최소 운영 증거다.

| 게이트                                 | 명령                              | Root cause                                                                                                                                                                           |
| -------------------------------------- | --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Schema migration safety ratchet        | `quality:schema-migrations`       | Alembic history가 여러 head로 갈라지거나 revision id가 drift되거나 destructive downgrade가 운영 rollback처럼 남아 production data loss 위험을 만드는 문제 차단                       |
| Schema migration singleton runner      | `quality:schema-migration-runner`; `quality:schema-migration-runner-live` | API/worker/app startup 여러 개가 동시에 migration을 실행해 schema lock, partial migration, app/schema mismatch를 만드는 문제 차단                                                    |
| Schema migration expand-contract guard | `quality:schema-migrations`       | expand 단계에서 old app/write path를 깨는 drop/alter/rename, 기본값 없는 NOT NULL 컬럼, 검토 불가능한 SQL, 준비 안 된 contract cleanup이 들어오는 문제 차단                          |
| Schema migration release-window guard  | `quality:schema-migrations`       | migration phase와 rolling-deploy compatibility window가 어긋나 old/new app 공존 기간을 리뷰할 기준이 사라지는 문제 차단                                                              |
| AI tenant RLS migration guard          | `quality:schema-migrations`       | 기존 DB가 Alembic upgrade로 새 AI tenant table을 받는 경로에서 PostgreSQL RLS/tenant policy 없이 테이블만 생성되어 tenant isolation 방어 밖에 남는 문제 차단                          |
| Ontology object reindex executor       | `quality:ontology-migrations`     | Ontology mapping 변경이 plan/audit에만 남고 실제 serving object index를 갱신하지 않아 새 ontology가 빈 index 상태로 방치되는 문제 차단                                               |
| Schema migration operator evidence     | `quality:schema-migration-runner` | migration 실패가 traceback으로만 사라져 어떤 revision/lock/error 상태였는지 운영자가 재현하지 못하는 문제 차단                                                                       |
| Dataset schema evolution ratchet       | `quality:schema-evolution`        | Dataset schema rename/drop/narrowing 같은 consumer-breaking 변경이 단순 schema drift 실패로만 보이거나, widening/backfill 영향이 transaction metadata 없이 merge되는 문제 차단       |
| Ontology migration ratchet             | `quality:ontology-migrations`     | Ontology property/object/link/action parameter 변경이 기존 object/query/action/generated SDK 소비자를 조용히 깨거나, reindex 필요성이 audit/outbox evidence 없이 merge되는 문제 차단 |

### S56 — Proactive Observability + SLO

S56의 runtime ratchet은 full incident-management product가 아니라 detector evidence와
저장형 incident lifecycle evidence다. `observability_detectors.py`는 저장된 runtime
source-of-truth를 수정하지 않고 `RuntimeRunSnapshot`과 versioned detector config만
비교하는 read-only path를 유지한다.
`quality:observability-detectors`는 FAILED run이 없어도 마지막 성공 이후 expected
cadence를 넘으면 missing-data incident가 생기는지, event time과 processing time이
분리된 lag evidence로 남는지, seasonality baseline에 포함된 partition을 skew false
positive에서 제외하는지, cooldown이 같은 dedupe key의 alert storm을 막는지,
detector failure가 source-of-truth를 바꾸지 않는지, 그리고
`POST /api/operations/observability/detect` 및 generated SDK surface가 유지되는지
검증한다. 같은 gate는 `POST /api/operations/observability/detect-and-record`가
active detector result를 `observability_incidents` row로 upsert하고, open →
acknowledged → resolved lifecycle, resolved incident reopen, occurrence count,
audit/outbox evidence, API/SDK surface를 잃지 않는지도 검증한다.
`quality:slo-contracts`는 SLO breach가 run/detail drill-down path와 dataset
reference를 함께 남기는지 검증한다. Notification delivery, dashboard/timeline UI,
standing incident scheduler, broker-offset/REST-cursor lag adapters, tenant/object-key
skew, object-index/workflow-action SLO adapters, persistent threshold registry는 후속
S56 slice다.

| 게이트                         | 명령                              | Root cause                                                                                                                                  |
| ------------------------------ | --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| Observability detector ratchet | `quality:observability-detectors` | 데이터 흐름이 멈췄는데 FAILED run이 없어 운영자가 뒤늦게 stale data를 발견하거나, lag/skew 알림과 stored incident 상태가 근거/audit 없이 noise로 쏟아지는 문제 차단 |
| SLO contract ratchet           | `quality:slo-contracts`           | SLO breach가 run/dataset reference 없이 숫자만 남아 operator가 어떤 run/detail을 봐야 할지 모르는 문제 차단                                 |

### S57 — Backup/Restore Commit-point Ratchet

S57의 현재 runtime ratchet은 full production backup/restore executor가 아니라 restore preflight,
immutable local backup artifact, verified artifact restore entrypoint, historical artifact dataset-head execution, restore-mode lockout, and operator resume approval evidence다. `backup_restore_service.py`는 metadata DB의 committed
dataset version을 source of truth로 보고, storage manifest와 data file byte/hash가 그 DB
commit point와 맞는지 검증한다. `quality:backup-restore`는 DB에는 committed version이
있는데 manifest/file이 빠지거나 깨진 상태를 `blocked` report issue로 남기는지, active
object index pointer와 action/outbox/audit/materialization high-watermark가 report에 들어가는지, search
projection을 restore truth로 쓰지 않고 rebuild marker로 남기는지, ready preflight만 hash-pinned
backup artifact로 저장되고 blocked preflight는 거부되며 artifact store가 같은 backup id의 다른 payload 덮어쓰기를 막는지, artifact restore entrypoint가 receipt hash/tenant/manifest mismatch와 current serving head drift를 fail-closed로 막고, execute path가 source artifact version의 byte/hash-clean files를 새 snapshot dataset versions로 복원해 latest serving head가 되는지, verified artifact에서만 restore mode
start/status가 `is_serving_traffic_open=false`와 `is_outbox_publisher_paused=true`를 남기는지,
같은 `restoreId` 재시도가 audit event를 중복 생성하지 않는지, restore mode 중 outbox
dead-letter retry/reprocess entry와 주요 platform write traffic이 차단되는지, post-restore 폐루프 증거가 없으면
`resume_approved`가 거절되고 검증 통과 후 현재 retry/reprocess entrypoint가 다시 열리는지,
그리고 generated SDK surface가 유지되는지 검증한다. full old/new runtime compatibility window,
external DB dump orchestration, always-on outbox publisher daemon pause/resume automation, 자동 restore smoke 실행은 후속 S57 slice다.

| 게이트                                                         | 명령                     | Root cause                                                                                                                                                                                                                                                                                                   |
| -------------------------------------------------------------- | ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Backup/restore preflight + restore-mode lockout/resume ratchet | `quality:backup-restore` | DB만 복구되거나 storage만 복구된 point-in-time mismatch가 serving truth처럼 보이고, search/object/outbox projection 상태가 복구 기준인지 재빌드 대상인지 불명확해지거나, restore 중 outbox 재전송이 외부 side effect를 중복 발생시키거나, post-restore 폐루프 증거 없이 publisher retry가 재개되는 문제 차단 |

### S58A — Auth/Secret Provider Ratchet

S58A의 현재 slice는 full identity lifecycle이 아니라 local JWT/OIDC verification,
secret-provider boundary, and local environment adapter proof다.
`JwtOidcAuthProvider`는 `Authorization: Bearer ...` token의 RS256 signature,
issuer, audience, expiry, tenant, subject/service-account, roles claim을 local
OIDC discovery/JWKS JSON 기준으로 검증하고, unknown `kid`에서 JWKS를 다시 읽으면서
기존 key cache를 유지한다. `SecretProvider`는 application port로 고정되고, `EnvSecretProvider`는
`FOUNDRY_LITE_WEBHOOK_SIGNING_KEY`와 `FOUNDRY_LITE_SECRET_<NAME>` alias를 통해
local/dev secret을 조회한다. `quality:auth-secrets`는 token 검증, tenant-scoped M2M
service-account mapping, local revoked-`jti` denylist,
JWKS refresh/cache,
secret redaction, webhook signing key provider boundary, REST connector secretRef
refresh-on-snapshot, strict user AuthProvider와 분리된 service-principal webhook ingest,
adapter failure taxonomy,
AuthProvider startup guard가 끊기지 않는지 함께 검증한다. Live OIDC discovery fetch,
JWKS URI polling/TTL/key retirement, service-account registry/scope policy,
IdP introspection/refresh-token revocation, cloud/Vault secret manager,
full workflow data-plane credential refresh와 previous/current dual-read rotation은
후속 S58A slice다.

| 게이트                       | 명령                   | Root cause                                                                                                                                                                                  |
| ---------------------------- | ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Auth/secret provider ratchet | `quality:auth-secrets` | API/webhook/connector code가 secret 값을 직접 환경변수에서 읽거나 operator evidence/error에 노출하고, production auth profile guard와 secret lookup boundary가 서로 따로 회귀하는 문제 차단 |

### S58B — Privacy Transform Ratchet

S58B의 현재 slice는 full anonymization platform이 아니라 deterministic privacy transform
core proof다. `PrivacyTransformPlan`과 `PrivacyFieldRule`은 tenant-scoped HMAC
pseudonymization, field-level irreversible placeholder anonymization, local regex text PII
redaction, protected in-memory reversible mapping proof, and production-to-nonprod
`PrivacyReplicationPolicy` proof를 제공한다. `quality:privacy`는 같은 tenant/scope/value가
같은 pseudonym으로 replay되고 tenant가 다르면 연결되지 않는지, transformed rows에 raw
email/SSN/phone sample이 남지 않는지, plan version과 lineage metadata가 replay-stable인지,
`is_reversible=True` rule이 protected mapping store 없이 실행되지 않는지, redacted mapping
evidence에 원본값이 남지 않는지, production에서 staging/analytics/AI 실험 환경으로 민감 필드를
복제할 때 privacy plan이 없거나 민감 필드가 누락/passthrough이면 차단되는지, source/target
dataset version lineage와 raw-value-free OpenLineage-compatible privacy event artifact가
replay-stable한지 검증한다.
Durable environment replication workflow, durable/encrypted reversible mapping backend,
ontology-driven privacy policy registry, runtime DB `lineage_edges`/outbox/OpenLineage
transport integration, and erasure lifecycle은 후속 slice다.

| 게이트                    | 명령              | Root cause                                                                                                                                                                                                                                                                                                                                                                                                          |
| ------------------------- | ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Privacy transform ratchet | `quality:privacy` | production 데이터를 staging/analytics/AI 실험으로 복제할 때 같은 사람을 재식별 가능한 원본 PII로 그대로 흘리거나, tenant 간 pseudonym이 연결되거나, transform replay마다 다른 privacy output/lineage가 생기거나, reversible mapping이 protected store 밖으로 새거나, 민감 필드가 privacy plan 없이 non-production 환경으로 복제되거나, anonymized dataset lineage/OpenLineage artifact에 raw PII가 섞이는 문제 차단 |

### S58C — Right-to-Erasure Manifest Ratchet

S58C의 현재 slice는 full erasure executor가 아니라 deletion request, subject resolution,
and manifest planning proof다. `ErasureRequest`는 raw subject를 내부 입력으로 받지만
operator-facing evidence에는 `subjectHash`와 `***PROTECTED***`만 남긴다.
`resolve_erasure_subject`는 candidate record를 tenant-scoped identity field로만 매칭해
다른 tenant의 같은 subject 값을 지우지 않는다. `ErasureManifest`는 object/search/materialized
row/Record DLQ/backup snapshot/audit surfaces를 stable action list로 만들고, backup retention
중인 항목은 `PENDING_RETENTION` 상태와 crypto-shredding key ref를 남긴다. `quality:erasure`는
serving-surface manifest action이 raw subject를 노출하지 않는지, tenant scope가 지켜지는지,
같은 request/idempotency key replay가 같은 manifest를 반환하는지, backup retention pending
state가 보이는지, and search rebuild exclusion proof가 erased search document를 재등장시키지
않는지 검증한다. Durable request table/API/workflow, ObjectStore/SearchAdapter/materialization/DLQ
executors, backup manifest rewrite, KMS/cloud crypto-shredding, and audit compaction executor는
후속 slice다.

| 게이트                            | 명령              | Root cause                                                                                                                                                                                                                                           |
| --------------------------------- | ----------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Right-to-erasure manifest ratchet | `quality:erasure` | 삭제 요청이 raw subject를 운영 증거에 노출하거나, 다른 tenant의 같은 subject 값을 같이 지우거나, 같은 idempotency key replay마다 다른 manifest를 만들거나, backup retention 상태를 숨기거나, search rebuild가 erased subject를 다시 살리는 문제 차단 |

### AIP P0c — AI Run Ledger Ratchet

P0c의 현재 slice는 full AIP trace UI가 아니라 AI 실행 장부의 DB/저장소 계약이다.
`AiRunRepository`는 session/message/run/event/model-call/context/tool/citation/usage row를
저장하되, raw prompt나 raw tool result를 일반 DB에 넣지 않고 ref/hash/redacted preview로 남긴다.
`quality:ai-ledger`는 정본 §10.2 컬럼 목록, message client idempotency, event sequence
idempotency, tenant scoping, PostgreSQL RLS migration DDL, SQLite/PostgreSQL round-trip, and
runtime-lane wiring을 검증한다.
P0i는 이 장부의 첫 Operations API/SDK read surface를 추가했고, P0u는 ModelGateway 자동
model-call 기록을 추가했다. P0v는 encrypted compiled-prompt artifact store를 추가했다.
ToolBroker execution and full trace UI는 후속 AIP slice다.

| 게이트                 | 명령                | Root cause                                                                                                                                                                           |
| ---------------------- | ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| AI run ledger ratchet  | `quality:ai-ledger` | AI 실행이 provider call 이후에만 휘발성 로그로 남거나, retry가 message/event를 중복 생성하거나, raw prompt/tool payload가 일반 DB/audit row에 저장되거나, 기존 DB migration 경로에서 새 AI 테이블이 PostgreSQL RLS 밖에 남거나, Postgres와 SQLite 동작이 갈라지는 문제 차단 |

### AIP P0u — Model Gateway Ledger Ratchet

P0u의 현재 slice는 live provider dashboard나 encrypted prompt artifact viewer가 아니라,
governed Model Gateway가 provider attempt accounting을 직접 남기는 backend contract다.
`ModelGatewayService.invoke(...)`는 `ModelRequest` `ai_run_id` field가 있는 경우 provider call 전에
tenant-scoped AI run이 이미 seed되어 있는지 확인한다. Seeded run이 없으면 provider adapter를
호출하지 않고 fail closed한다. Provider adapter가 성공하면 `ai_model_calls`에 succeeded row를
기록하고, provider adapter가 실패하면 raw prompt 없이 request/response hash와 redacted error
payload를 가진 failed row를 남긴 뒤 원래 오류를 다시 올린다.

Agent Runtime은 더 이상 model-call ledger row를 수동으로 쓰지 않고, compiled prompt hash를
`ModelRequest` `request_hash` field로 Gateway에 넘긴다. 따라서 모델 호출을 장부에 남기는 책임은
Agent Runtime의 후처리 관습이 아니라 Gateway boundary의 불변 조건이 된다.

| 게이트 | 명령 | Root cause |
|---|---|---|
| Model Gateway ledger ratchet | `quality:model-gateway-ledger` | Model Gateway를 통과한 provider call이 AI run ledger에 누락되거나, seeded run 없이 provider egress가 먼저 발생하거나, provider 실패가 raw prompt와 함께 로그/장부에 남거나, Agent Runtime이 Gateway 밖에서 model-call row를 수동으로 맞추는 문제 차단 |

### AIP P0v — Encrypted Prompt Artifact Ratchet

P0v의 현재 slice는 prompt artifact viewer나 full trace UI가 아니라, raw compiled prompt를
일반 DB/audit JSON 밖의 별도 암호화 artifact에 저장하는 backend privacy contract다.
`PromptArtifactStore`는 원문 prompt를 encrypted blob으로 쓰고, `PromptArtifactService`는
`ai_prompt_artifacts`에 `artifact_ref`, prompt `content_hash`, encrypted `artifact_hash`, byte size,
key ref, algorithm, retention, legal hold, erasure lineage, export marking만 기록한다. Raw read는
`aip_prompt_artifact_reader` 역할이 있는 caller에게만 허용된다.

Agent Runtime은 AI run을 seed한 뒤 Model Gateway 호출 전에 compiled prompt artifact를 기록한다.
Artifact write가 실패하면 seeded run을 failed로 닫고 provider/model adapter를 호출하지 않는다.
저장 plaintext는 `compiled_prompt_hash`와 같은 canonical `{"messages":[...]}` JSON이고, receipt
insert 실패 시 방금 쓴 encrypted artifact를 삭제해 retention/erasure 없는 orphan prompt를 남기지
않는다. Raw read는 ciphertext `artifact_hash`와 plaintext `content_hash`를 receipt와 다시 맞춰본다.
SecretProvider key 조회 실패 시 local-dev fallback은 `FOUNDRY_LITE_ALLOW_LOCAL_PROMPT_ARTIFACT_KEY=true`
명시 opt-in이 있을 때만 허용된다.
Alembic migration은 `ai_prompt_artifacts`에 PostgreSQL RLS/FORCE/policy를 적용해 기존 DB upgrade
경로도 bootstrap 경로와 같은 tenant isolation을 갖는다.

| 게이트 | 명령 | Root cause |
|---|---|---|
| Encrypted prompt artifact ratchet | `quality:prompt-artifacts` | raw compiled prompt가 일반 DB/Operations payload/파일에 평문으로 남거나, compiled_prompt_hash와 저장 plaintext가 갈라지거나, receipt 실패 뒤 orphan artifact가 남거나, 조용한 local-dev key fallback이 발동하거나, receipt hash 재검증 없이 raw prompt를 읽거나, 별도 reader role 없이 원문을 읽거나, prompt artifact write 실패 뒤에도 provider egress가 진행되거나, 기존 DB migration 경로에서 새 AI tenant table이 PostgreSQL RLS 밖에 남는 문제 차단 |

### AIP P0w — Prompt Artifact Failure Taxonomy Ratchet

P0w의 현재 slice는 key rotation이나 prompt artifact viewer가 아니라, P0v의 protected prompt-log
store가 실패할 때 운영자가 같은 adapter taxonomy 언어로 판단할 수 있게 만드는 backend 운영 계약이다.
`PromptArtifactStore` port는 `failure_contract()`를 요구하고, local encrypted adapter는
write/delete storage failure를 retryable `unavailable`, missing/inactive key를 retryable
`authentication`, receipt hash mismatch/corrupt artifact를 non-retryable `conflict`, tenant-scoped
artifact ref miss를 non-retryable `not_found`로 선언한다. 전역 `adapter-failure-taxonomy` gate는
`local-prompt-artifact-store` profile을 필수 concrete adapter profile로 포함한다.

| 게이트 | 명령 | Root cause |
|---|---|---|
| Prompt artifact failure taxonomy ratchet | `quality:prompt-artifacts`; `quality:adapter-failure-taxonomy` | prompt artifact store 장애가 일반 예외 문자열로만 남아 운영자가 key 문제인지, retry 가능한 storage 문제인지, hash/corruption conflict인지, missing ref인지 구분하지 못하거나, 새 prompt artifact adapter가 operator-safe failure contract 없이 추가되는 문제 차단 |

### AIP P0x — Prompt Artifact Historical Key Version Ratchet

P0x의 현재 slice는 prompt artifact viewer나 cloud KMS integration이 아니라, P0v receipt의
`encryption_key_ref`가 실제 복호화 경계가 되게 만드는 backend 보안 계약이다. `SecretProvider`
는 current secret뿐 아니라 `version`을 명시한 조회를 지원하고, `LocalPromptArtifactStore` read
path는 receipt의 `secret://name@version`을 파싱해 정확히 그 버전을 요청한다. 현재 키가 회전되어도
보관된 old key version이 있으면 과거 artifact를 읽을 수 있고, old key가 없으면 current key로
조용히 재시도하지 않고 fail closed된다. Local `EnvSecretProvider`는
`<CURRENT_SECRET_ENV>__VERSION_<NORMALIZED_VERSION>` 형식으로 retained historical key를 제공하며,
missing/mismatched version 에러는 secret value를 노출하지 않는다.

| 게이트 | 명령 | Root cause |
|---|---|---|
| Prompt artifact key-version ratchet | `quality:prompt-artifacts`; `uv run pytest tests/contracts/test_secret_provider_contract.py tests/contracts/test_prompt_artifact_store_contract.py -q` | prompt artifact receipt에 key version을 저장해도 read path가 현재 key만 사용해 rotation 뒤 과거 prompt audit을 잃거나, unavailable old key를 current key fallback으로 가려 integrity/audit 원인을 흐리는 문제 차단 |

### AIP P0y — Prompt Artifact Access API/SDK Ratchet

P0y의 현재 slice는 full visual trace explorer나 prompt artifact viewer UI가 아니라, P0v/P0x로
보호 저장된 raw compiled prompt를 **별도 권한 API/SDK surface**로만 여는 backend/operator-access
계약이다. 일반 Operations AI run detail은 계속 artifact refs/hash/retention/export marking metadata만
노출하고 plaintext를 포함하지 않는다. 새 `GET /api/operations/runs/ai/{run_id}/prompt-artifacts/{artifact_id}`
경로와 generated SDK `client.operations.runs.promptArtifact(runId, artifactId)`는
`aip_prompt_artifact_reader` role이 있는 caller에게만 plaintext를 반환한다.

Route `run_id`는 단순 장식이 아니다. `PromptArtifactService.read_prompt_artifact(..., ai_run_id=...)`
는 receipt의 `ai_run_id`가 URL의 run id와 다르면 encrypted store를 읽기 전에 `NOT_FOUND`로 fail
closed한다. 따라서 tenant-scoped artifact id를 알아도 다른 AI run URL 아래에서 prompt를 열 수 없고,
reader role이 없으면 API는 `PERMISSION_DENIED`를 반환한다.

| 게이트 | 명령 | Root cause |
|---|---|---|
| Prompt artifact access ratchet | `quality:prompt-artifact-access`; `pnpm --silent quality:sdk-request-contract` | encrypted prompt artifact는 저장되어 있지만 운영/API/SDK에서 원문 접근 경로가 없거나, 일반 Operations detail에 raw prompt가 섞이거나, reader role 없는 caller가 plaintext를 받거나, URL run id와 receipt run id가 달라도 복호화가 진행되는 문제 차단 |

### AIP P0d — Context Compiler Ratchet

P0d의 현재 slice는 full Agent Runtime이나 trace UI가 아니라 모델 호출 직전의 prompt assembly
계약이다. `ContextProvider`는 권한 검증이 끝난 `RetrievedContextItem`을 opaque `context_id`,
source/version/hash/security partition과 함께 넘긴다. `ContextCompilerService`는 정본 §8.6 순서로
platform safety policy, agent instruction, application state, tool definitions, retrieved context,
citation mapping, output schema, user message를 묶고 `compiled_prompt_hash`,
`context_manifest_hash`, `tool_manifest_hash`, `state_snapshot_hash`, `policy_snapshot_hash`를 만든다.
검색 문맥은 raw delimiter fence가 아니라 JSON string data로 인코딩되어 delimiter-like 문자열이
prompt section boundary로 승격되지 못하며, duplicate context id, non-allowlisted partition,
context hash mismatch는 provider call 전에 fail closed된다.
RetrievalOrchestrator, AgentRuntime loop, public API/SDK surfaces는 후속 AIP slice다.

| 게이트                    | 명령                       | Root cause                                                                                                                                                                                                    |
| ------------------------- | -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Context compiler ratchet  | `quality:context-compiler` | 검색 결과나 application state가 임의 순서로 prompt에 섞이거나, retrieved document 안의 delimiter/prompt-injection 문장이 system instruction처럼 승격되거나, 같은 tenant 안의 비허용 security partition이 섞이거나, context/tool/state/policy hash 없이 AI run ledger가 생성되는 문제 차단 |

### AIP P0e — Tool Broker Ratchet

P0e의 현재 slice는 full Agent Runtime이나 실제 ontology/content/action tool adapter가 아니라,
모델이 요청한 tool call을 제품 서버가 안전하게 심사하는 관문이다. `ToolExecutor`는 승인된
도구 실행 포트이고, `ToolBrokerService`는 agent allowlist, published tool version, input JSON
schema, invoking-user permission, object/property scope, masked property, model egress compatibility,
timeout/result budgets, and confirmation/review requirement를 통과한 read-only call만 executor에
넘긴다. 출력은 모델로 돌아가기 전에 masking/size limit을 거치고, argument/result는 `sha256:`
hash와 redacted preview, `AiToolCallRecord`로 남는다. 기본 로컬 executor는 fake라서 network,
SQL, shell, provider SDK를 열지 않는다.
AgentRuntime loop, real ontology/content/state/action adapters, approval bridge, public API/SDK
surfaces는 후속 AIP slice다.

| 게이트              | 명령                  | Root cause                                                                                                                                                                                                                          |
| ------------------- | --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Tool broker ratchet | `quality:tool-broker` | LLM이 generic SQL/shell/HTTP executor를 직접 호출하거나, agent manifest에 없는 도구를 실행하거나, 권한/마스킹/egress/예산/confirmation 검사를 건너뛰거나, raw tool result를 모델/장부에 그대로 돌려주는 문제 차단 |

### AIP P0f — Citation Service Ratchet

P0f의 현재 slice는 full Agent Runtime이나 visual trace UI가 아니라, 모델 응답의 citation을
제품 서버가 검증해서 렌더링/이동 참조로 바꾸는 관문이다. `CitationService`는 모델이 제안한
`context_id`와 claim span을 tenant-scoped AI run manifest에서 찾고, 선택된 context item인지
확인한 뒤, source type별 read permission을 검사한다. 그 다음 `CitationSourceVerifier`가 현재
source version/hash를 다시 확인하며, manifest와 맞을 때만 `AiCitationRecord`와 HMAC-signed
`flite-citation-nav.v1` navigation ref를 만든다. forged context id, unselected context, stale
source, invalid span/order, permission deny는 citation row 기록 전 fail closed된다.
AgentRuntime loop, real object/document/media source resolvers, public API/SDK surfaces는 후속
AIP slice다.

| 게이트                   | 명령                       | Root cause                                                                                                                                                                                                     |
| ------------------------ | -------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Citation service ratchet | `quality:citation-service` | LLM이 존재하지 않는 context id나 직접 만든 URL을 citation처럼 내보내거나, omitted context/stale source/권한 없는 source가 answer citation으로 렌더링되거나, citation ledger row가 검증 없이 생기는 문제 차단 |

### AIP P0g — Action Proposal Ratchet

P0g의 현재 slice는 ApprovalExecutionService가 아니라, AI가 제안한 Ontology Action을 사람 검토
큐에 넣기 전의 서버-side 관문이다. `ActionProposalService`는 model proposal을 곧바로 실행하지
않고, tenant-scoped AI run manifest에서 선택된 evidence context인지 확인하고, agent action
allowlist, user action permission, active Ontology action definition, current object version을 다시
읽는다. 그런 다음 action type, target, expected object version, canonical params, evidence refs,
agent version, policy version을 묶은 `sha256:` proposal fingerprint를 만들고, 그 fingerprint를
review create idempotency key로 사용한다. 같은 fingerprint retry는 같은 review를 재사용하고,
파라미터/근거/정책이 바뀐 proposal은 새 review가 필요하다. 이 slice는 `insight_reviews`에
정본 §10.3 proposal fields를 추가하지만, 승인 후 실제 `ActionService.apply_action` 실행은
후속 Approval-to-Action bridge에서 다룬다.

| 게이트                    | 명령                      | Root cause                                                                                                                                                                           |
| ------------------------- | ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Action proposal ratchet   | `quality:action-proposal` | LLM이 action을 직접 실행하거나, forged/unselected evidence로 review를 만들거나, agent allowlist/user permission/object version re-read 없이 approval queue에 action proposal을 넣는 문제 차단 |

### AIP P0h — Approval Execution Ratchet

P0h의 현재 slice는 visual proposal review workspace가 아니라, 이미 승인된 action proposal review를
실제 `ActionService.apply_action(...)` 호출로 연결하는 서버-side bridge다.
`ApprovalExecutionService`는 review가 approved 상태인지 확인하고, reviewer permission,
action execution permission, source evidence access, restore/write traffic gate, active action
definition, current object version, expiry, 그리고 originating AI run ledger로 재계산한 proposal
fingerprint를 다시 검증한다. 실행은 proposal fingerprint를 action idempotency key로 사용하므로
같은 승인 실행 retry가 새 action run을 만들지 않는다. 성공하면 review의 `execution_status`와
`approved_action_run_id`를 갱신하고, Operations가 따라갈 수 있도록
`runtime_run_relations`에 `insight_review --approved_as--> action` 링크를 남긴다.

| 게이트                       | 명령                         | Root cause                                                                                                                                                      |
| ---------------------------- | ---------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Approval execution ratchet   | `quality:approval-execution` | 승인되지 않은 review나 변조/만료/stale proposal이 action으로 실행되거나, 승인 retry가 중복 action run을 만들거나, review와 action run 사이 운영 추적이 끊기는 문제 차단 |

### AIP P0i — AI Operations Ratchet

P0i의 현재 slice는 full visual trace explorer가 아니라, 이미 저장된 canonical AI run ledger를
Operations 목록/상세/API/SDK에서 안전하게 볼 수 있게 하는 operator-facing surface다.
`RuntimeRunType`은 `ai`를 허용하고, run list는 `aiRuns`를 반환하며,
`GET /api/operations/runs/{run_type}/{run_id}` detail은 `run_type=ai`일 때 `ai` field에 run refs/hashes,
ordered events, model-call token/latency accounting, selected/omitted context metadata,
tool-call hashes/status, citations, usage/cost summary, and lightweight timeline을 담는다.
raw prompt, raw tool result, provider response body, authorization-bearing JSON 값은 detail payload에
노출하지 않는다. Source-chain lookup은 `ai_execution_runs`처럼 `created_at` 대신 `started_at`을
쓰는 runtime table도 안전하게 정렬해야 한다.

| 게이트                  | 명령                    | Root cause                                                                                                                                                     |
| ----------------------- | ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| AI operations ratchet   | `quality:ai-operations` | AI 실행 장부가 DB에만 있고 운영자가 목록/상세/API/SDK에서 찾지 못하거나, trace/detail 화면이 raw prompt/tool/provider/authorization payload를 그대로 노출하거나, AI run source-chain 조회가 잘못된 timestamp 컬럼 가정으로 깨지는 문제 차단 |

### AIP P0j — Logic Runtime Ratchet

P0j의 현재 slice는 full Temporal-backed Logic engine이나 visual DAG builder가 아니라, AIP Logic
블록 그래프를 typed/bounded DAG로 검증하고 안전한 기존 경계를 통해 실행하는 첫 runtime surface다.
`LogicRuntimeService`는 `Input`, `CallFunction`, `Condition`, `CreateActionProposal`,
`HumanApproval`, `ApplyAction`, `Output` block id/kind/dependency를 검증하고, duplicate id,
missing dependency, cycle, max block budget을 fail closed한다. `CallFunction`은 직접 executor를
부르지 않고 `ToolBrokerService`를 통과하며, tool call ledger row를 기록한다.
`CreateActionProposal`은 직접 Action을 실행하지 않고 `ActionProposalService`를 통해
`insight_reviews` pending proposal을 만든다. `ApplyAction`은 approved proposal signal이 없는
interactive Logic run에서 fail closed한다. Logic run start/completed/failed evidence는 기존
`ai_execution_events`에 ref/hash/redacted preview로 남기며, 같은 graph/input의 result hash는
deterministic replay proof로 고정된다.

| 게이트                  | 명령                    | Root cause                                                                                                                                           |
| ----------------------- | ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| Logic runtime ratchet   | `quality:logic-runtime` | Logic DAG가 cycle/무제한 loop/직접 Action 실행/ToolBroker 우회로 이어지거나, read tool/proposal 실행 흔적이 AI ledger에 남지 않는 문제 차단 |

### AIP P0k — AI Evals + Release Guard Ratchet

P0k의 현재 slice는 full eval workbench/Apollo release automation이 아니라, AI agent candidate를
운영 release channel로 올리기 전에 deterministic eval evidence를 요구하는 첫 guard다.
`EvalService`는 `ai_eval_suites`, `ai_eval_cases`, `ai_eval_runs`, `ai_eval_results`에
suite/case/run/result 장부를 남기고, 입력/기대값/실제값/result를 `sha256:` hash로 고정한다.
동일 case의 repeated-run actual hash가 흔들리거나, required axis가 빠지거나, suite/case
definition이 version bump 없이 바뀌면 fail closed한다. `promote_agent_release(...)`는 같은
agent version + target channel의 passed eval run이 있어야 `ai_agent_releases`를 쓸 수 있고,
`stable`은 Security + Action axis 통과와 zero variance를 추가로 요구한다.

| 게이트                    | 명령                 | Root cause                                                                                                                                               |
| ------------------------- | -------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| AI eval evidence ratchet  | `quality:ai-evals`   | agent/model/prompt 후보가 deterministic eval evidence 없이 운영 승격 후보가 되거나, eval suite/case drift가 version bump 없이 조용히 바뀌는 문제 차단 |
| AI release guard ratchet  | `quality:ai-release` | failed/mismatched/variance-bearing eval run으로 stable/canary release promotion 장부가 쓰이는 문제 차단 |

### AIP P0l — Visual Builder Ratchet

P0l의 현재 slice는 full drag-and-drop Agent Studio가 아니라, 이미 구현된 AIP backend
계약을 화면과 SDK에서 안전하게 조립하기 위한 read-only preflight다.
`VisualBuilderService`는 agent version, model alias version, prompt version, context
source, tool manifest, Logic DAG, eval axes를 하나의 draft로 받아 `sha256:` draft/graph
hash와 ready/blocked 결과를 반환한다. tenant security partition mismatch, unpublished or
dangerous tool, direct `ApplyAction`, missing Logic dependency/cycle, missing eval axis, and
stable release without Security+Action eval evidence를 fail closed로 표시한다. `/api/aip/builder/validate`,
generated `client.aip.builder.validate(...)`, and Web Operations AIP Builder panel은 이 preflight를
SDK-only path로 사용한다.

| 게이트                  | 명령                     | Root cause                                                                                                                                      |
| ----------------------- | ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| Visual builder ratchet  | `quality:visual-builder` | visual authoring 화면이 ToolBroker/ActionProposal/Eval/Release guard 경계를 우회하거나, 위험한 Logic/tool/action draft를 release-ready처럼 보여주는 문제 차단 |

### AIP P0m — Builder Runtime Execution Ratchet

P0m의 현재 slice는 full Agent Studio나 autonomous AgentRuntime이 아니라, P0l의 read-only
Builder preflight를 실제 bounded Logic 실행으로 연결하는 첫 product path다.
`BuilderRuntimeService`는 draft를 먼저 `VisualBuilderService`로 검증하고, 통과한 경우에만
tenant-scoped AI run ledger를 생성한다. 선택된 Builder context source는 AI run manifest에
selected context item으로 기록되므로 `CreateActionProposal`이 forged evidence 없이 같은
ledger를 재검증할 수 있다. `CallFunction`은 계속 ToolBroker를 통과하고, `CreateActionProposal`
은 pending Insight Review만 만들며, direct action side effect는 만들지 않는다. API/SDK/Web은
`POST /api/aip/builder/run`과 `client.aip.builder.run(...)`으로 이 흐름을 사용하고,
반환값은 Operations AI run detail로 바로 이어진다.
Full retrieval orchestration, model-call AgentRuntime loop, persisted multi-user Builder drafts,
drag-and-drop editing, visual debugger, eval workbench, and release dashboards는 후속 AIP slice다.

| 게이트                            | 명령                      | Root cause                                                                                                                                                       |
| --------------------------------- | ------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Builder runtime execution ratchet | `quality:builder-runtime` | Builder 화면이 validate만 통과한 draft를 런타임 장부 없이 실행하거나, ToolBroker/ActionProposal guard를 우회하거나, 실행 후 Operations trace로 돌아가지 못하는 문제 차단 |

### AIP P0n — Agent Runtime Readonly Ratchet

P0n의 현재 slice는 full autonomous multi-tool AgentRuntime이 아니라, 한 번의 사용자 메시지를
authorized context retrieval, deterministic context compiler, governed Model Gateway call, AI run
ledger, Operations trace로 연결하는 read-only runtime path다. 모델이 tool call을 반환해도 이
slice에서는 실행하지 않고 fail closed 한다.

아직 multi-iteration tool loop, visual debugger, eval workbench,
persisted Agent Studio definitions는 후속 slice다.

| Gate | Command | Blocks |
|---|---|---|
| Agent runtime readonly ratchet | `quality:agent-runtime` | Agent Runtime 화면/API가 ContextCompiler/ModelGateway/AI ledger를 우회하거나, 비허용 security partition을 모델 prompt에 넣거나, 모델 호출 뒤 Operations trace 없이 답변을 반환하거나, read-only slice에서 tool call을 실행하는 문제 차단 |

### AIP P0o — Retrieval Orchestrator Object Context Ratchet

P0o의 현재 slice는 full document/vector/reranking RetrievalOrchestrator가 아니라,
Agent Runtime의 첫 실제 컨텍스트 경로를 fake provider에서 object-authoritative re-read로
교체하는 baseline이다. `RetrievalOrchestrator`는 request tenant/actor/security partition을
fail closed로 확인하고, Agent Runtime state의 `objectType`/`objectId`가 있으면
`ObjectQueryService.get_object(...)`를 통해 권한/마스킹이 적용된 committed object를 다시 읽어
`RetrievedContextItem`으로 만든다. objectId가 없는 objectType query path도 검색 hit text를
그대로 쓰지 않고 hit의 object id를 다시 읽은 payload만 context text로 만든다. 각 item은
source ref/version, `sha256:` content hash, retrieval method, security partition, token estimate를
담고, max item/token budget에 맞게 dedupe/packing된다.

Beyond current P0o, AIP-specific document context packing is handled by P0p below. Already-covered
ontology/media retrieval outputs를 Agent context로 묶는 broader dense+lexical fusion, optional
reranking, visual retrieval debugging은 later slices다. Agent Runtime citation rendering is handled
by P0q below.

| Gate | Command | Blocks |
|---|---|---|
| Retrieval orchestrator object-context ratchet | `quality:retrieval-orchestrator` | Agent Runtime이 fake context나 stale search-index text를 모델 prompt에 넣거나, object read 권한/마스킹을 우회하거나, 다른 tenant security partition/context budget 초과를 조용히 통과시키는 문제 차단 |

### AIP P0p — Retrieval Document Context Ratchet

P0p의 현재 slice는 full ontology/media fusion이나 visual retrieval debugger가 아니라, 이미
구현된 media/content retrieval 결과를 Agent Runtime context로 안전하게 묶는 첫 문서 경로다.
`DefaultContentRetrievalService`는 검색 hit를 반환하기 전에 DB `content_units` row를 다시 읽고,
tenant/security envelope, classification pre-filter, text hash를 검증한 뒤 authoritative text를
`ContentSearchHit`에 채운다. `RetrievalOrchestrator`는 이 검증된 hit를 `kind=document`
`RetrievedContextItem`으로 변환하고, `content-unit://{source_media_item_version_id}/{content_unit_id}`
source ref, source media version, `sha256:` prompt-text hash, retrieval method, security partition,
token estimate를 담는다. Agent Runtime은 `modelAllowedClassifications`를 retrieval request까지
전달하므로 over-classified document가 ranking/prompt 전에 빠질 수 있다.

Broader object+media OAG fusion, cross-source diversity, optional reranking, visual retrieval
debugging은 later slices다. Agent Runtime citation rendering is handled by P0q below.

| Gate | Command | Blocks |
|---|---|---|
| Retrieval document context ratchet | `quality:retrieval-document-context` | Agent Runtime이 document/content search hit의 stale index text를 prompt에 넣거나, content unit DB re-read/hash/security/classification pre-filter를 건너뛰거나, document context가 AI run ledger에서 source/version/hash 없이 남는 문제 차단 |

### AIP P0q — Agent Runtime Citation Rendering Ratchet

P0q의 현재 slice는 full visual trace explorer나 multi-turn autonomous citation loop가 아니라,
P0f `CitationService`를 P0n/P0p Agent Runtime 성공 경로에 연결하는 첫 answer-citation
path다. Agent Runtime은 plain-text model output을 기존처럼 유지하되, model response가
structured JSON `answer` + `citations`를 반환하면 citation claim의 opaque `contextId`,
claim span, order만 받아들인다. 각 claim은 `CitationService`가 tenant-scoped AI run manifest,
selected context item, source read permission, current source version/hash를 다시 확인한 뒤에만
`AiCitationRecord`와 signed `flite-citation-nav.v1` navigation ref로 렌더링된다. forged context id,
malformed citation payload, stale source, permission deny는 run success 전에 fail closed되고,
Operations AI detail의 `citationCount`/`citations`에 검증된 citation만 남는다.

Full inline citation UI, source-specific rich preview, multi-turn citation repair, and visual trace
debugging은 later slices다.

| Gate | Command | Blocks |
|---|---|---|
| Agent runtime citation rendering ratchet | `quality:agent-runtime-citations` | Agent Runtime이 모델이 만든 URL/context id를 그대로 answer citation으로 노출하거나, CitationService 검증 없이 success run을 닫거나, 검증된 citation을 result/API/Operations 장부에 연결하지 못하는 문제 차단 |

### AIP P0r — Agent Citation UI Ratchet

P0r의 현재 slice는 full source preview나 visual trace explorer가 아니라, P0q에서 검증된
answer citation payload를 운영자가 화면에서 바로 읽을 수 있게 하는 첫 Web Operations
surface다. Agent Runtime은 `outputSchema`를 prompt에만 넣지 않고 `ModelRequest`의
`response_schema` field에도 전달한다. local fake model은 schema가 citations를 요구하고 compiled citation mapping이
있을 때만 structured JSON `answer` + opaque `contextId` citation claim을 반환하므로, Web
E2E가 실제 API/SDK/Agent Runtime/CitationService 경로를 지나 검증된 citation card와
Operations `citationCount`를 확인한다.

Rich source previews, inline answer-span anchors, multi-turn citation repair, and visual trace
debugging은 later slices다.

| Gate | Command | Blocks |
|---|---|---|
| Agent citation UI ratchet | `quality:agent-citation-ui` | Web Operations AIP Agent가 citation을 JSON payload에만 숨겨두거나, output schema를 모델 요청에 전달하지 않거나, 검증되지 않은 citation/text를 unsafe DOM으로 렌더링하거나, 브라우저 사용자 흐름에서 Operations citation summary까지 연결하지 못하는 문제 차단 |

### AIP P0s — Agent Source Preview Ratchet

P0s의 현재 slice는 full source viewer나 visual trace debugger가 아니라, P0r에서 화면에
나온 verified citation card에 안전한 source preview metadata를 붙이는 첫 operator-facing
출처 확인 경로다. `CitationService`는 model이 준 citation claim을 그대로 믿지 않고 기존처럼
tenant-scoped AI run manifest, selected context item, source permission, source version/hash를
검증한 뒤, resolved citation payload에 `sourcePreview`를 추가한다. 이 preview는 selected
AI run ledger context item에서 온 allowlisted metadata만 담는다: context item id, kind,
source resource type/id, source version, content hash, retrieval method, relevance score,
token estimate, security partition, selected flag. Raw retrieved text, prompt text, tool payload,
provider request/response body는 preview에 포함하지 않는다. Web Operations AIP Agent card는
이 preview를 `textContent`로 렌더링하고, browser E2E는 같은 user action 뒤 citation card와
Operations AI detail의 `citationCount`를 함께 확인한다.

Inline answer-span anchors, full source document preview, multi-turn citation repair, and visual
trace debugging은 later slices다.

| Gate | Command | Blocks |
|---|---|---|
| Agent source preview ratchet | `quality:agent-source-previews` | Citation card가 출처 metadata 없이 opaque label만 보여주거나, preview가 raw context/prompt/tool/provider body를 노출하거나, Web Operations 사용자 흐름에서 retrieval method/security partition/source version을 확인하지 못하는 문제 차단 |

### AIP P0t — Agent Inline Citation Anchor Ratchet

P0t의 현재 slice는 full source document preview, multi-turn citation repair, 또는 visual trace
debugger가 아니라, verified citation의 `claimSpan`을 운영자 답변 본문 안에서 바로 확인할 수
있게 하는 첫 inline citation surface다. Web Operations AIP Agent answer는 model output을
HTML로 신뢰하지 않고, answer string을 text node로 잘라 붙인 뒤 검증된 citation payload의
bounded `claimSpan` 위치에만 `[n]` citation button을 렌더링한다. Button click은 같은 API
응답에서 이미 `CitationService`가 검증한 citation card를 선택/focus한다. Invalid, out-of-range,
overlapping span은 inline anchor에서 제외되어 plain text answer와 verified citation cards가
유지된다.

Full source document preview, multi-turn citation repair, and visual trace debugging은 later
slices다.

| Gate | Command | Blocks |
|---|---|---|
| Agent inline citation ratchet | `quality:agent-inline-citations` | Web Operations가 검증된 `claimSpan`을 답변 본문에 연결하지 못하거나, model-produced HTML/URL을 신뢰하거나, citation marker 클릭이 verified citation card로 이어지지 않는 문제 차단 |

### S60 — AI Evidence Lineage Ratchet

S60의 현재 slice는 full AI/insight product가 아니라 object explain property-lineage와
immutable evidence reference proof다. Object explain은 `propertyLineage`를 통해
property별 source dataset version, source object version, source column, source hash,
property version, and masking state를 노출한다. `EvidenceReference`와 `EvidenceSourceSpan`
은 LLM/insight evidence가 source dataset/object version, extractor version, model version,
prompt version, parameter hash, source span, human review status를 함께 들고 있게 만든다.
`quality:ai-evidence`는 insight claim이 evidence object 없이 만들어지지 않는지, LLM
extraction evidence가 model/prompt version을 pinning하는지, reprocessing이 이전 evidence를
덮어쓰지 않고 새 revision을 만드는지, masked source span이 raw quote를 노출하지 않는지
검증한다. Durable AI evidence table, real LLM executor, insight evidence viewer, model diff
UI, and AI action policy enforcement는 후속 slice다. 별도의 S63 backend/API/SDK slice는
durable Insight Review queue state를 제공하지만, AI evidence table과 visual evidence viewer를
완성했다고 주장하지 않는다.

| 게이트                      | 명령                  | Root cause                                                                                                                                                                                                                                       |
| --------------------------- | --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| AI evidence lineage ratchet | `quality:ai-evidence` | object explain이 property별 source 좌표 없이 뭉뚱그린 lineage만 보여주거나, AI/insight claim이 source object/model/prompt version 없이 만들어지거나, 재처리가 기존 evidence를 덮어쓰거나, 권한 없는 caller에게 source quote가 노출되는 문제 차단 |

### S61 — Frontend Foundation SDK Contract

S61의 현재 slice는 full frontend workspace가 아니라 generated SDK가 프론트의 공통
요청/에러/요청 ID 경계를 맡고, 현재 frontend-consumable backend route와 SDK helper를
named SDK-only와 browser request/helper-contract로 잠그는 기반 proof다. `createFoundryLiteClient`는 공통 `request` escape hatch,
tenant/user/role context header, request-id factory, and response telemetry callback을
제공한다. SDK-generated TypeScript/browser output은 `FoundryLiteApiError`,
`createRequestId`, `requestContextHeaders`, `normalizeFoundryLiteError`,
`isRetryableFoundryLiteError`, `retryWithBackoff`, `collectCursorPages`,
`createInFlightActionLock`, `actionLockKey`, `classifyFoundryLiteError`와 함께
system, datasets, ontology catalog/validation, generic objects, objectSets, materializations,
operations, connector onboarding, Insight Review, and AIP Builder 하위 named method를 노출한다.
`docs/frontend-api-sdk-surface-matrix.json`은 FastAPI route/helper -> SDK method/helper ->
proof class -> proof test -> operator evidence mapping의 source of truth이며,
`tests/sdk/request_contract.mjs`는 browser SDK를 실제 import해 308개 frontend route surface의
method/path/query/header/body와 typed error metadata, 그리고 28개 SDK helper의 OSDK facade, TypeScript ObjectSet property-keyed filter/orderBy/page alias normalization, `$count` exact-groupBy aggregate over Object Query pages, fail-fast invalid property/operator/order/aggregate evidence, generated package manifest/fingerprint exposure, live-catalog SDK regeneration assertions, large ontology registry lookup/live-catalog search/action grouping/dynamic-only drift hint, session token provider, operation polling, operation event streaming, retry/backoff,
cursor collection, duplicate-action lock, request/context header, typed error normalization,
stale-version classification, permission-denied classification behavior, and missing idempotency-key
fail-fast for every `requiresIdempotencyKey` surface, and admin capability screen-model/preflight grouping을 fake fetch로 검증한다.
The same browser contract now covers the first TS instance relation DSL: `fetchOne` returns
an object with non-enumerable `$link` and `$actions` handles, `order.$link.customer.fetchPage(...)`
maps the generated alias to `OrderCustomer` and resolves linked target objects through object reads,
`order.$actions.approveOrder.validateAction(params)` sends the source object id/version and typed params to
the read-only action validation boundary without an `Idempotency-Key`, and
`order.$actions.approveOrder.applyAction(params, { idempotencyKey, onCacheRefresh })` sends the source object id,
expected object version, caller-provided idempotency key, and typed params while surfacing edit/cache-refresh hints without extra screen code.
`tests/unit/test_python_osdk.py` separately proves the first Python mirror slice: `FoundryLite.__call__`
accepts `foundry_lite.osdk.Order` and `ApproveOrder`, compiles Python property filters/order clauses to
the existing object query payload, fails closed for unknown static properties/operators/action params/missing
idempotency keys, and keeps link traversal, action apply, masking, tenant, and permission checks on the
existing object/action service path.
The optional React entrypoint also exposes live ontology catalog workspace state, cursor pagination,
start-and-poll job state, and admin console launch/task-plan actions that split browser-safe actions from
worker/CLI/runbook/future rows while carrying execution surface, approval, checklist, and blocking
reason fields for screen implementation.
The same gate now protects `docs/frontend-backend-surface-contract.md` as a frontend SDK recipe contract and
`packages/sdk-ts/src/screen-recipes.ts` as the typechecked source exported through
`@foundry-lite/sdk/screen-recipes`: session, dataset explorer, object/action workspace, large ontology lookup, media,
AIP, pipeline builder, long-running operation, admin console, and recovery/operations recipes must remain documented,
importable, and free of raw request paths when the SDK surface evolves.
`quality:sdk-typecheck`는 `packages/sdk-ts`를 `tsc --noEmit`으로 검사해 generated SDK와 optional
React entrypoint가 실제 TypeScript frontend 프로젝트에서 type import/use를 깨뜨리지 않는지
같은 frontend foundation gate 안에서 확인한다. `quality:foundry-typecheck`는
`apps/foundry` 전체를 strict TypeScript mode로 검사해 SDK의 nullable wire contract를 화면이
검증 없이 non-null로 소비하거나, 실제 SPA 조합에서 타입 경계가 깨지는 문제를 같은 gate에서
차단한다.
Web Operations는 현재 product controls에서 raw `/api/...` path를 직접 조립하지 않는다.
Login/session UI, screen-specific retry/backoff copy, visual cursor pagination components, server push route implementation, visual streaming timeline UX, duplicate-click
button state UX, broader stale-version compare/merge UX beyond current action-apply conflict refresh, permission-denied masking UX, full
catalog-driven workspace UX, direct migration execution, long-running worker daemon control, and infra bootstrap
browser execution은 후속 slice다.

| 게이트                                    | 명령                               | Root cause                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| ----------------------------------------- | ---------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Frontend backend API/SDK surface contract | `quality:frontend-backend-surface` | FastAPI route가 frontend/non-frontend로 분류되지 않거나, frontend-consumable route에 named SDK method/proofClass/request-contract proof test가 없거나, FastAPI `Idempotency-Key` route와 matrix `requiresIdempotencyKey` marker가 어긋나거나, 문서의 frontend route surface/helper count claim이 실제 matrix/generated SDK count와 어긋나거나, `SDK_CLIENT_SURFACE.helpers` helper가 matrix row/export/operator-evidence/helper-contract proof 없이 생기거나, `docs/frontend-backend-surface-contract.md`의 screen-level SDK recipe 계약 또는 `@foundry-lite/sdk/screen-recipes` typechecked recipe source/package export가 빠지거나, Web Operations/recipe source가 raw `/api/...` 호출로 SDK 계약을 우회하는 문제 차단 |
| AI FDE governed platform contract | `quality:ai-fde` | 9개 mode와 native tool adapter가 invoking-user permission을 우회하거나, lazy search가 server-owned catalog 밖 tool을 활성화하거나, 사용자 확인 없이 mutation을 실행하거나, Ontology/Pipeline branch를 벗어나 쓰거나, Builder MCP의 OAuth app scope·Origin·재전송·structured content 계약이 깨지거나, Pilot의 seed/branch/OSDK/React/CI bundle idempotency 및 API/SDK/UI/runtime evidence가 drift하는 문제 차단 |
| Browser SDK request/helper contract       | `quality:sdk-request-contract`     | named SDK method가 실제 browser SDK에서 잘못된 HTTP method/path/query/header/body/idempotency key/error metadata를 보내거나, SDK helper가 retry/backoff/cursor/duplicate-action/error classification/request context 계약에서 drift 나는 문제 차단                                                                                                                                                                                                                                                                                 |
| SDK TypeScript typecheck                  | `quality:sdk-typecheck`            | SDK package entrypoint, generated types, optional React helpers, and screen recipes가 TypeScript strict mode에서 깨져 프론트엔드가 SDK를 import하자마자 실패하는 문제 차단                                                                                                                                                                                                                                                                                                                                                         |
| Foundry SPA TypeScript typecheck          | `quality:foundry-typecheck`        | SDK wire response의 nullable/optional 상태를 화면이 검증 없이 강한 타입으로 가정하거나, 실제 `apps/foundry` 화면 조합이 strict TypeScript mode에서 깨지는 문제 차단                                                                                                                                                                                                                                                                                                                                                                 |
| Frontend foundation SDK/SPA contract      | `quality:frontend-foundation`      | generated SDK와 browser SDK helper surface가 달라지거나, Web Operations가 SDK request/error/request-id 경계를 우회하거나, frontend error가 request id/retryability 없이 표시되거나, Foundry SPA가 SDK 계약을 잘못 소비해 타입 검사가 깨지는 문제 차단                                                                                                                                                                                                                                                                                 |

### S63 — Insight Review Backend/API/SDK Contract

S63의 현재 slice는 full Insight/Action Workspace UI가 아니라, 화면이 붙을 수 있는
durable review queue backend contract다. `insight_reviews`는 tenant-scoped claim, evidence
refs, action proposal, assignment, decision, and idempotency keys를 저장한다.
`foundry.insights`, `/api/insights/reviews`, and generated `client.insights.reviews.*`는
list/create/get/assign/decide를 named SDK로 노출한다. `quality:insight-review`는 create replay가
같은 review를 반환하는지, status/assignee filtering이 tenant-scoped로 동작하는지, terminal
decision에 한 winner만 있는지, API create/assign/decision이 duplicate request를 안전하게
처리하는지, and `insight_review.created`, `insight_review.assigned`,
`insight_review.approved`, `insight_review.rejected` audit evidence가 남는지
검증한다.

| 게이트                                  | 명령                     | Root cause                                                                                                                                                                                 |
| --------------------------------------- | ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Insight Review backend/API/SDK contract | `quality:insight-review` | Insight queue가 화면-only 상태로 남거나, 같은 idempotency key가 중복 review를 만들거나, approve/reject가 덮어써지거나, 운영자가 audit에서 review mutation 원인을 추적하지 못하는 문제 차단 |

### S64 — Operations/Recovery Console Backend/API/SDK Contract

S64의 현재 slice는 full visual Operations/Recovery Console이 아니라, S57 restore
evidence를 화면이 읽을 수 있는 read model과 resume 전 검증 evidence로 묶는 backend/API/SDK
계약이다. `BackupRestoreService.recovery_overview`는 latest preflight summary, active
restore-mode traffic gate, latest restore status, latest post-restore validation, and
required operator next actions를 runtime/audit evidence에서 재구성한다.
`BackupRestoreService.run_post_restore_validation`은 같은 closed-loop 기준을 별도 durable
`backup_restore.post_restore_validated` audit evidence로 남긴 뒤 `approve_restore_resume`이
그 validation id를 요구하게 만든다. `GET /api/operations/recovery/overview`,
`POST /api/operations/backup-restore/restore-mode/{restore_id}/post-restore-validation`, and
generated `client.operations.backupRestore.recoveryOverview()` /
`client.operations.backupRestore.postRestoreValidation()`는 이 상태판과 검증을 named SDK-only
surface로 노출하고, `quality:operations-recovery`는 application method, API smoke, generated
SDK, browser request contract, and runtime lane wiring을 함께 검증한다. Foundry Run 조사
list/detail/investigation summary 화면은 current E2E로 검증하며, richer run console,
recovery dashboard, alert timeline, workflow cancel/reconcile executor는 후속 S64 slice다.

| 게이트                                  | 명령                          | Root cause                                                                                                                                              |
| --------------------------------------- | ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Operations Recovery validation/overview contract | `quality:operations-recovery` | restore/preflight/traffic-gate/validation evidence가 흩어져 운영자가 DB/audit logs를 직접 열어야 하거나, frontend가 raw API path로 복구 상태와 재개 검증을 조립하는 문제 차단 |

| 도구                             | 분류 | 효과                                                                           |
| -------------------------------- | ---- | ------------------------------------------------------------------------------ |
| `cosmic-ray`                     | 동적 | mutmut 4.x stat-collection 이슈가 계속되면 mutation testing 대체 엔진으로 검토 |
| OpenLineage CLI/server transport | 동적 | P8 local RunEvent artifact 이후 실제 backend/CLI 전송 검토                     |
| `git-secrets` 또는 `truffleHog`  | 정적 | secret 누출 사후 차단 (Bandit 보완)                                            |
| `safety`                         | 정적 | pip-audit 보완, CVE DB 다른 소스                                               |

---

## 5. 게이트 추가/변경 워크플로

새 게이트 박는 표준 절차 (자체 self-test 포함):

1. **§ 매핑 명시**: 게이트 파일 docstring 1줄에 "Enforces guideline §X.Y" 작성.
2. **JSON 리포트**: `artifacts/quality/<gate>.json` 산출. 다음 필드 필수: `count`, `violations`, `baseline`, `gate_pass`.
3. **단위 테스트**: `tests/unit/test_quality_<gate>.py` — 인공 violation 생성 시 fail, 정상 트리 통과 시 pass.
4. **`ci_gate.sh` 등록**: static 또는 dynamic 섹션에 추가.
5. **AGENTS.md 1줄**: 게이트 이름 + 역할.
6. **이 문서(`docs/quality-gate-roadmap.md`) 매핑표 갱신**: 상태 ❌ → ✅, 게이트 ID 박기.

게이트 약화/삭제도 같은 절차:

- "왜 약화/삭제하는가" 코드 주석 + 본 문서에 사유 1줄.
- 약화의 부작용을 보완하는 다른 게이트가 있는지 명시.
- 예: `check_service_method_conflicts` 삭제 시 — 명시적 collaborator 호출이 도입돼 글로벌 메서드 이름 충돌이 더 이상 문제 아님. `check_service_call_graph`가 보완.

### 5.1 5분 PR 병합 경로와 전체 증거 분리

2026-08-04 이전에는 모든 PR이 전체 branch coverage, 전체 runtime proof, 전체 Playwright,
fresh CodeQL DB를 기다려 required check가 약 40분까지 늘어났다. 이 검사는 강하지만 작은 문서나
UI 수정에도 똑같은 전체 시스템을 다시 세우므로 병합 피드백으로는 너무 늦었다.

현재 required PR 경로는 `quality-pr-fast` 하나로 합친다. 이 게이트는 변경 범위를 계산하고,
diff secret/위험 코드 검사, Ruff·mypy·pyright·Bandit·아키텍처/스키마/문서 불변식,
직접 변경되었거나 변경 모듈과 파일명이 연결된 pytest, 영향받은 TypeScript SDK/Foundry typecheck와
request contract를 병렬 실행한다. 새 Python source가 직접 또는 변경된 테스트와 연결되지 않으면
fail-closed하고, 변경된 fast test는 모두 실행하되 source-name 자동 연관 테스트는 전체 선택이 32개 파일을
넘지 않게 제한한다. `artifacts/quality/pr_fast_gate.json`에 선택된 파일·테스트·각 검사 시간·위반을
남긴다. GitHub job 자체가 5분 timeout이고 내부 검사 budget은 210초다.

이것은 전체 증거의 삭제가 아니라 실행 시점의 변경이다. 전체 coverage/runtime/E2E와
gitleaks/Semgrep/pip-audit/full CodeQL은 main push에서 실행되고, flaky x7과 full runtime rehearsal은
nightly, live/performance/release evidence는 tag 또는 수동 release gate에서 계속 강제된다. PR의
CodeQL required-check 이름은 호환을 위해 유지하지만 실제 PR 단계는 고신뢰 diff security guard이며,
로그가 full CodeQL이 main에서 이어진다는 경계를 명시한다. 부작용은 "merge 전에 전체 suite를
완주하지 않는다"는 점이고, 보완은 직접 테스트 fail-closed, 5분 gate artifact, main full gate,
nightly/release gate의 네 겹 계약이다.

## 6. Operational Evidence And Diagnostics

이 섹션은 삭제된 별도 관측성/진단 문서의 운영 진단 역할을 흡수한다. 별도 관측성/진단
README를 다시 만들지 않는다. 비개발자식으로 말하면,
품질 게이트가 실패했을 때 "어디서 왜 실패했는지"를 찾는 길은 이 문서와 CI artifact에서 시작한다.

### 6.1 Release And Runtime Lanes

| 실행 위치       | 명령/잡             | 역할                                                                                         |
| --------------- | ------------------- | -------------------------------------------------------------------------------------------- |
| 로컬             | `pnpm ci:gate`      | 정적 불변식과 Tach impact-scoped pytest를 실행해 빠른 피드백을 준다.                         |
| 로컬             | `pnpm ci:gate:pr`   | origin/main 대비 GitHub 5분 PR gate를 재현한다.                                              |
| 로컬             | `pnpm ci:gate:all`  | 전체 release evidence를 직렬로 리허설한다.                                                   |
| GitHub PR        | `quality-pr-fast`   | diff security, focused static/test/type contracts를 5분 안에 required evidence로 만든다.     |
| GitHub main push | `quality-static`    | 전체 정적 분석, 타입, 아키텍처, 문서 drift, 보안/복잡도 gate를 실행한다.                     |
| GitHub main push | `quality-coverage`  | 전체 pytest branch coverage, tier/public API coverage를 확인한다.                            |
| GitHub main push | `quality-runtime`   | demo, OpenLineage, audit/outbox, data correctness, trace, diagnostics를 확인한다.            |
| GitHub main push | `quality-e2e`       | 전체 Playwright browser E2E를 실행한다.                                                      |
| GitHub Actions   | `quality-gate`      | PR에서는 fast gate, main에서는 네 full lane 결과를 같은 required check 이름으로 집계한다.   |
| GitHub nightly   | `quality-flaky-nightly` | 전체 pytest suite를 7회 반복해 outcome 흔들림을 차단한다.                                |

### 6.2 Operator Evidence Rule

실패가 console log에만 있으면 proof가 불완전하다. 위험한 실패는 다음 중 하나 이상의 durable
payload에 남아야 한다.

- run/error payload
- dataset transaction metadata
- audit event
- outbox or DLQ record
- trace/span artifact
- GitHub Actions summary or uploaded quality artifact

운영자가 `request_id`, `tenant_id`, `actor_user_id`, `dataset_version_id`, `transform_run_id`,
`action_run_id`, `object_type`, `object_id`, `object_version` 중 관련 키를 따라가면 같은 실패를
다시 설명할 수 있어야 한다.

### 6.3 Runtime Diagnostics

데모나 runtime lane이 실패하면 아래 진단 스크립트가 Python runtime 상태를 `artifacts/diagnostics/`에
남긴다.

```bash
uv run python scripts/diagnostics/run_runtime_diagnostics.py
uv run python scripts/diagnostics/run_runtime_diagnostics.py --console-traces
```

진단 artifact는 다음을 확인한다.

| 도구                           | 남기는 증거                              |
| ------------------------------ | ---------------------------------------- |
| `faulthandler`                 | Python이 멈추거나 크래시가 날 때의 스택  |
| `tracemalloc`                  | 어떤 코드가 메모리를 많이 잡는지         |
| `cProfile`/`pstats`            | 어느 함수가 시간을 많이 쓰는지           |
| `gc`                           | 가비지 컬렉션 상태                       |
| `warnings`                     | 이후 장애가 될 수 있는 경고              |
| OpenTelemetry console exporter | 선택적으로 사람이 읽을 수 있는 span 출력 |
