# Foundry-lite Quality Gate Roadmap

**작성일:** 2026-06-10
**문서 역할:** 엔지니어링 가이드 문서 → 정량 게이트 매핑의 단일 SSOT
**핵심 명제:** *문서가 철학이라면 게이트는 하네스다.* 모든 문서 조항은 정량 게이트로
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

---

## 1. 문서 조항 ↔ 게이트 매핑 (전수)

`foundry_lite_python_engineering_guidelines_ko.md` (이하 *가이드*)의 18개 섹션 중 핵심
조항 ~67개를 게이트와 매핑한다. 상태는 ✅ 강제, △ 부분, ❌ 미강제, ⏳ 미해당 (구현 전).

### §1 Python 환경과 기본 철학

| # | 조항 | 게이트 | 정량 | 상태 |
|---|---|---|---|---|
| 1 | Python ≥ 3.12 | `pyproject.toml` `requires-python` | 정적 | ✅ |
| 2 | ruff/mypy/pyright/pytest 사용 | `ci_gate.sh` | pass/fail | ✅ |
| 3 | 함수 ≤ 40줄 | (예정 G3) | LoC | ❌ |
| 4 | mutation은 transaction+audit+실패상태 | (예정 G2/G11) | static + dynamic | ❌ |
| 5 | request_id/run_id 끊김 금지 | (예정 G5/G16) | static + dynamic | ❌ |
| 6 | branch coverage ≥ 95% | `pytest --cov-branch` | 95 | ✅ |
| 7 | 통합/스모크 100% | demo smoke + e2e | pass | ✅ |

### §2 Clean Code

| # | 조항 | 게이트 | 정량 | 상태 |
|---|---|---|---|---|
| 8 | boolean naming (is_/has_/can_) | — | — | ❌ |
| 9 | 함수 ≤ 40줄 | (예정 G3) | LoC | ❌ |
| 10 | guard clause 깊이 | Xenon B (간접) | 복잡도 등급 | △ |
| 11 | 조회/변경 부작용 분리 | — | — | ❌ |

### §3 SRP

| # | 조항 | 게이트 | 정량 | 상태 |
|---|---|---|---|---|
| 12 | API Router DB transaction 금지 | (예정 G1) | 0건 | ❌ |
| 13 | Repository 비즈니스 판단 금지 | (예정 G10) | 0건 | ❌ |
| 14 | Domain FastAPI/SQLAlchemy import 금지 | `check_dependency_graph` | 0건 | ✅ |

### §4.1 의존성 방향

| # | 조항 | 게이트 | 정량 | 상태 |
|---|---|---|---|---|
| 15 | domain → framework 0 | `check_dependency_graph` + `.importlinter` + `tach` | 0건 + DAG pass | ✅ |
| 16 | application → port만 | `check_infra_import_boundary` + `.importlinter` + `tach` | baseline 0 + DAG pass | ✅ |
| 17 | api → repository 직접 호출 금지 | (예정 G1) | 0건 | ❌ |

### §4.2 디자인 패턴

| # | 조항 | 게이트 | 정량 | 상태 |
|---|---|---|---|---|
| 18 | Facade 얇기 | `check_application_module_size` | ≤500줄 | ✅ |
| 19 | required_dependencies 명시 | `check_service_dependencies` | 선언/사용 일치 | ✅ |
| 20 | required_collaborators 명시 | `check_service_dependencies` | 선언/사용 일치 | ✅ |
| 21 | 소유 service 명시 호출 | `check_service_call_graph` | cycle 0, depth≤7, fan-out≤10 | ✅ |
| 22 | Strategy/Specification 테스트성 | — | — | ❌ |

### §4.3 Scale Foundation

| # | 조항 | 게이트 | 정량 | 상태 |
|---|---|---|---|---|
| 23 | concrete SDK import 0 | `check_infra_import_boundary` | 0건 | ✅ |
| 24 | adapter error에 trace key 유지 | (예정 G16) | dynamic | ❌ |
| 25 | fake/local adapter contract test 동일 | contract tests | pass | △ (수동) |
| 26 | trace key boundary 유지 | (예정 G16) | dynamic | ❌ |
| 27 | 새 boundary에 contract test 동반 | (예정 G13) | 0개 누락 | ❌ |

### §5 코드 컨벤션

| # | 조항 | 게이트 | 정량 | 상태 |
|---|---|---|---|---|
| 28 | `Any` boundary 외 금지 | pyright (감지만) | — | △ |
| 29 | `dict[str, Any]` 대신 model | (예정 G4) | baseline + decrease | ❌ |

### §6 API

| # | 조항 | 게이트 | 정량 | 상태 |
|---|---|---|---|---|
| 30 | mutation에 idempotency key | (예정 G12) | static | ❌ |
| 31 | error response에 request_id | (예정 G7) | 0건 누락 | ❌ |

### §7 트랜잭션

| # | 조항 | 게이트 | 정량 | 상태 |
|---|---|---|---|---|
| 32 | Repository 임의 commit 금지 | (예정 G10) | 0건 | ❌ |
| 33 | outbox와 state change 같은 transaction | (예정 G11/G18) | static + dynamic | ❌ |
| 34 | COMMITTED dataset version immutable | DB schema (정적) | — | △ |

### §8 에러 처리

| # | 조항 | 게이트 | 정량 | 상태 |
|---|---|---|---|---|
| 35 | broad `except Exception` 금지 | ruff `BLE001` (부분) | 정적 | △ |
| 36 | `raise X from exc` | ruff `B904` | 정적 | ✅ |
| 37 | secret/SQL/stack trace 노출 금지 | Bandit (부분) | 정적 | △ |
| 38 | 로그에 request_id 포함 | (예정 G5) | static | ❌ |

### §10 보안

| # | 조항 | 게이트 | 정량 | 상태 |
|---|---|---|---|---|
| 41 | `eval`/`exec` 금지 | Bandit | 0건 | ✅ |
| 42 | raw SQL interpolation 금지 | Bandit | 0건 | ✅ |
| 43 | secret hardcoding 금지 | Bandit | 0건 | ✅ |
| 44 | tenant_id 없는 도메인 write 금지 | (예정 G11) | static | ❌ |
| 45 | audit 없는 mutation 금지 | (예정 G2/G17) | static + dynamic | ❌ |

### §11 테스트

| # | 조항 | 게이트 | 정량 | 상태 |
|---|---|---|---|---|
| 46 | test_*.py 명명 | pytest 디스커버리 | 정적 | ✅ |
| 47 | flaky test pass 금지 | (예정 G19) | 3회 반복 동일 결과 | ❌ |
| 48 | line 95% | `pytest --cov` | 95 | ✅ |
| 49 | branch 95% | `pytest --cov-branch` | 95 | ✅ |
| 50 | function 95% | `check_public_api_coverage` | 95 | ✅ |
| 51 | 영역별 (domain/app/infra/api) 95% | (예정 G8) | 영역별 95 | ❌ |
| 52 | 통합 시나리오 7개 100% | demo smoke (1개) | 1/7 | △ |
| 53 | `pragma: no cover` 남발 금지 | (예정 G6) | baseline + decrease | ❌ |

### §12 관측성

| # | 조항 | 게이트 | 정량 | 상태 |
|---|---|---|---|---|
| 54 | 로그에 request_id/tenant_id/run_id | (예정 G5) | static | ❌ |
| 55 | 메트릭 7개 노출 | (예정 — `check_metrics_exposed.py`) | metric count | ❌ |

### §16 CI

| # | 조항 | 게이트 | 정량 | 상태 |
|---|---|---|---|---|
| 56 | skip/flaky/xfail 우회 금지 | `check_no_test_bypasses` | 0 | ✅ |
| 57 | private test reference 부채 | `check_private_test_references` | 0 | ✅ |

### §18 안티패턴

| # | 조항 | 게이트 | 정량 | 상태 |
|---|---|---|---|---|
| 58 | 증상 제거 패치 금지 | (예정 G21/G22) | PR diff + git log | ❌ |
| 59 | `except Exception: pass` 금지 | ruff `E722`+`BLE001` (부분) | 정적 | △ |
| 60 | `sleep`/magic으로 race condition 덮기 | `check_no_test_sleep` | tests 0 | ✅ |
| 61 | `dict[str, Any]` 우회 | (예정 G4) | baseline + decrease | ❌ |
| 62 | migration 없이 DB 모양 가정 | (예정 — `check_alembic_revision_on_schema.py`) | static | ❌ |
| 63 | Fat Router | (예정 G1) | 0건 | ❌ |
| 64 | God Service | `check_service_call_graph` | fan-out ≤10 | ✅ |
| 65 | Silent Failure | (예정 G17) | dynamic audit | ❌ |
| 66 | Log-only Audit | (예정 G2/G17) | static + dynamic | ❌ |
| 67 | Magic fallback | AST-grep `foundry-lite-no-facade-magic-dispatch` | 0건 | ✅ |

### 매핑 점수 요약

| 상태 | 개수 | 비율 |
|---|---|---|
| ✅ 강제 | 28 | 42% |
| △ 부분 | 6 | 9% |
| ❌ 미강제 | 31 | 46% |
| ⏳ 미해당 (구현 전) | 2 | 3% |
| **합계** | **67** | **100%** |

**현재 게이트는 문서 약속의 ~51%를 정량적으로 강제한다.** 나머지는 PR 리뷰어와
AGENTS.md 정독에 의존한다. 이 로드맵의 목표는 그 의존을 줄이는 것이다.

---

## 2. 신규 게이트 — Tier 분류

### Tier P7 — CodeQL data-flow taint analysis (✅ 완료 2026-06-10 P7)

CodeQL은 정적 분석 중 유일하게 **interprocedural taint propagation**을
직접 모델링한다. Semgrep은 한 함수 안의 패턴만 보고, import-linter는
모듈 의존만 보지만, CodeQL은 "FastAPI Request의 헤더 값이 5단계 함수
호출을 거쳐 SQL `execute()`까지 흐르는지"를 그래프로 추적한다.

`scripts/quality/codeql/queries/` 4개 쿼리 + `qlpack.yml`:

| 쿼리 | 헌법 조항 | 추적하는 흐름 |
|---|---|---|
| `header-flows-to-sql.ql` | §10.2 (no raw SQL interpolation) | Request → header → `text()`/`execute()` |
| `mutation-without-audit.ql` | §10.2 (no mutation without audit) | `session.execute(insert/update/delete)` 뒤에 audit 이벤트 없음 |
| `http-exception-without-request-id.ql` | §8.3 (error carries request_id) | `HTTPException` 생성 시 detail에 request_id 없음 |
| `raw-json-to-service.ql` | §5.2 / §6.3 (no dict[str,Any] passthrough) | Request body → service method (Pydantic 우회) |

실행:
- 로컬 `pnpm ci:gate`: 실행하지 않음. Python 코드베이스 기준 fresh DB build 3~5분 + analyze 1~2분이므로 매 로컬 피드백 루프에 넣지 않는다.
- 수동 디버그: `bash scripts/quality/codeql/run.sh` (로컬 codeql 미설치 시 WARN+exit 0, CI/strict mode에서는 missing tool이 실패)
- CI: `.github/workflows/codeql.yml`이 push/PR/weekly로 GitHub-hosted runner에서 실행 + SARIF 업로드 + `fail_on_sarif_findings.py`로 finding 1개 이상이면 hard failure

Self-test: `tests/unit/test_quality_codeql_queries.py`가 qlpack 매니페스트, § 인용, @id/@kind/@problem.severity 메타, run.sh 실행권한, codeql 미설치 시 graceful local skip, 알려진 CodeQL API 호환성(`getAnInstance`)을 검증한다. `tests/unit/test_quality_codeql_sarif_gate.py`는 SARIF finding을 workflow failure로 바꾸는 hard gate를 검증한다.

### Tier P6 — Pyright strict (✅ 부분 완료 2026-06-10 P6)

`pyright`는 디폴트 `basic` 모드로 전체 코드베이스를 보지만 `[tool.pyright]
strict = [...]` 리스트의 경로는 **strict 모드**로 격상된다. 이 리스트가
헌법화된다.

현재 strict 적용 경로 (모두 0 errors):

| 경로 | 의미 |
|---|---|
| `libs/foundry_lite/domain` | §4.1 framework 0 영역 — strict가 가장 자연스러움 |
| `libs/foundry_lite/application/ports` | Protocol 정의 — strict로 Any 누출 자동 검출 |
| `libs/foundry_lite/security` | 보안 결정 영역 — Any 사용 금지 |
| `libs/foundry_lite/application/services/base.py` | CoreService DI 토대 |

남은 application/services/* 와 infrastructure 어댑터는 점진적으로 strict
리스트에 추가. 새 boundary 추출 시 ports/* 가 strict이므로 Protocol 위반이
즉시 fail — 정통화 강제 메커니즘.

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

두 plugin이 *동적* root cause를 잡는다. 자체 self-test는
`tests/unit/test_quality_random_and_parallel.py`.

| Plugin | 잡는 root cause |
|---|---|
| `pytest-randomly` | **테스트 순서 의존성** — 한 테스트가 다른 테스트의 부작용에 암묵적으로 의존. seed가 매번 바뀌므로 PR에 들어오면 즉시 실패. |
| `pytest-xdist` | **race / 공유 자원 충돌** — 같은 파일 경로/env var/tmp 디렉토리에 두 테스트가 동시 쓰기. `-n auto`로 매 게이트 실행마다 검증. |

`ci_gate.sh`는 (a) coverage 측정용 serial pytest 1회 + (b) `-n auto` parallel
pytest 1회를 둘 다 돈다. parallel run은 coverage 산출 없이 통과/실패만 본다.

### Tier P2 — import-linter로 흡수된 게이트 (✅ 완료 2026-06-10 P2)

`.importlinter` 4 contracts가 import graph 자체를 강제한다. 도구는 module-level이
아니라 **transitive import 경로**를 본다 — `check_infra_import_boundary`가 놓친
function-local lazy import (application/core.py:53)를 P2가 첫 시도에 검출해 정통화.

| Contract | 강제 조항 |
|---|---|
| `layered-core` | §4.1 의존성 방향 (apps → infra → application → domain) |
| `domain-purity` | §4.1 domain은 framework 0개 (fastapi/pydantic/sqlalchemy/duckdb/boto3/kafka/pyspark/temporalio) |
| `application-no-vendor-sdk` | §4.3 application은 vendor SDK 0개 (위와 동일 + opensearchpy/elasticsearch) |
| `apps-no-direct-infra` | §3.1 apps/* 가 infrastructure.repositories 직접 import 금지 (composition root 예외만 명시) |

`scripts/quality/check_infra_import_boundary.py` 와 `check_dependency_graph.py` 는
**보존**한다. import-linter는 transitive를 강제하고 우리 자체 게이트는
module-level baseline(0)을 강제 — 서로 다른 사각지대를 잡는 이중 망.

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

| 흡수된 게이트 | Semgrep rule |
|---|---|
| G1 router transaction/repo 직접 호출 | `router-no-direct-transaction`, `router-no-repository-access` |
| G10 repository domain errors raise | `repository-no-domain-errors` |
| §4.2 cross-service bare helper call | `action-service-no-bare-audit`, `object-services-no-bare-runtime-helpers`, `dataset-services-no-bare-runtime-helpers` |
| §18.1 silent failure / bare except | `no-bare-except-pass` |
| §10.2 eval/exec | `no-eval-exec` |
| §10.2 f-string SQL | `no-fstring-sql` |
| §4.3 application/domain vendor SDK | `application-no-vendor-sdk` |

### Tier P0.5 — AST-grep structural anti-magic (✅ 완료 2026-06-11)

AST-grep는 Semgrep보다 Python AST 패턴을 더 좁고 빠르게 검증하기 좋은 곳에 쓴다.
현재 첫 규칙은 `FoundryLiteCore`가 다시 `__getattr__`/`__setattr__` 기반
method-registry magic dispatch로 돌아가는 것을 차단한다.

규칙:
- `scripts/quality/ast-grep-rules/no-facade-magic-dispatch.yml`
- 대상: `libs/foundry_lite/application/core.py`
- 정량 기준: `__getattr__`/`__setattr__` 0건

실행:
- 로컬/CI release gate: `pnpm exec sg scan -c sgconfig.yml`
- package script: `pnpm quality:ast-grep`
- Self-test: `tests/unit/test_quality_ci_workflows.py`가 임시 `core.py` fixture에
  `__getattr__`를 심고, AST-grep가 실제 error finding을 내는지 검증한다.

### Tier 0 — 즉시 추출 가능 (정적 분석, 각 30~60분)

| ID | 게이트 | 매핑 조항 | 정량 기준 | Root cause |
|---|---|---|---|---|
| G1 | `check_router_layer_purity.py` | §3.1, §6.1, §18.3 Fat Router, §4.1 17 | `apps/api/**/*.py`에서 `core.<repo>_repository.*` 직접 호출 0, `engine.begin` 0, `transaction.execute` 0 | Fat Router 안티패턴 자체 차단 |
| G2 | `check_audit_on_mutation.py` | §7, §10.2 45, §18.3 66 | mutation 메서드(`create_*`/`commit_*`/`apply_*`/`abort_*`)가 같은 메서드 body에서 `runtime_service._audit` 또는 `audit_repository` 접근. 위반 0 | mutation은 무조건 audit |
| G3 | `check_function_length.py` | §1.2 3, §2.2 9 | `libs/foundry_lite/application` 함수 ≤ 50줄 (warning 40). baseline 후 monotonic decrease | 함수 거대화 차단 |
| G4 | `check_dict_any_budget.py` | §5.2 29, §18.3 61 | `application/**/*.py` 함수 시그니처에 `dict[str, Any]` 카운트 → baseline + monotonic decrease | dict[str,Any]가 schema drift 통로 |
| G5 | `check_log_has_trace_keys.py` | §8.3 38, §12.1 54 | `logger.X(...)` 호출 시 `extra=` 또는 message에 `request_id`/`run_id`/`tenant_id` 중 1개 이상 포함. 위반 0 | 추적 키 끊김 차단 |
| G6 | `check_pragma_no_cover_budget.py` | §11.4 53 | `# pragma: no cover` 총 카운트 baseline + monotonic decrease + 이유 주석 강제 (`# pragma: no cover  # reason: ...`) | 커버리지 우회 차단 |
| G7 | `check_error_response_has_request_id.py` | §6.3 31, §8.3 | FastAPI `HTTPException(detail=...)` / exception handler가 `request_id` 포함하는지 정적 검증 | 사용자 응답에 추적 키 보존 |
| G8 | `check_tier_coverage_by_layer.py` | §11.4 51 | `coverage.json` 파싱 후 `domain`/`application`/`infrastructure`/`apps/api`/`apps/cli` 각 영역 95%+ | 평균 95%에 가려진 가난한 영역 노출 |
| G9 | `check_no_test_sleep.py` | §18.1 60 | `tests/**/*.py` AST에서 `time.sleep`/`asyncio.sleep` 호출 0건 | flaky 근원 차단 |
| G10 | `check_repository_no_business.py` | §3.1 13, §7.1 32, §18.3 | `infrastructure/repositories/*.py`에서 도메인 errors(`ValidationFailed`, `PermissionDenied`, `ConflictDetected`) raise 0건 | Repository에 비즈니스 규칙 침투 차단 |

### Tier 1 — 중간 (정적 + 마커, 각 1~3시간)

| ID | 게이트 | 매핑 조항 | 정량 기준 | Root cause |
|---|---|---|---|---|
| G11 | `check_transaction_outbox_pair.py` | §7.1 33, §10.2 44 | service mutation 메서드에서 `engine.begin()`/`transaction.execute` 호출 시 같은 함수에서 outbox/audit 함께 호출 강제 | outbox/audit 누락 차단 |
| G12 | `check_idempotency_on_action.py` | §6.3 30, §7.3 | `apps/api/**/actions/**/apply` endpoint와 `apply_action` service가 `idempotency_key` 파라미터 + `existing` 체크 정적 검증 | 중복 액션 차단 |
| G13 | `check_contract_test_per_port.py` | §4.3 27 | `libs/foundry_lite/application/ports/*.py`마다 `tests/contracts/test_*_contract.py` 1:1 존재. 누락 0 | 새 boundary가 부정통하게 들어오는 것 차단 |
| G14 | `check_doc_drift.py` | AGENTS.md, implementation-status.md | 문서에 명시된 클래스/모듈/메서드 이름이 코드에 실재하는지 grep + AST 검증 | 문서 과장 차단 |
| G15 | `check_integration_scenario_markers.py` | §11.4 52 | `@pytest.mark.integration_scenario("connector_sync"|...)` 마커가 가이드 §11.4 7개 시나리오 모두 존재 | 통합 100% 약속 검증 |

### Tier 2 — 동적 / 메타 (런타임 분석, 각 3~8시간)

| ID | 게이트 | 매핑 조항 | 정량 기준 | Root cause |
|---|---|---|---|---|
| G16 | `check_trace_continuity.py` | §4.3 24, 26, §12.3 | demo smoke 실행 중 OTel span 수집 → request span 안의 service span/DB span의 `trace_id` 동일 | trace key 끊김 검출 |
| G17 | `check_audit_count_runtime.py` | §10.2 45, §18.3 65 | demo smoke 후 `audit_events` row 수 ≥ mutation 호출 카운트. 차이 0 | audit 누락 동적 검증 |
| G18 | `check_outbox_consistency.py` | §7.1 33 | demo smoke 후 state change ↔ outbox event 1:1 매칭. 불일치 0 | outbox 약속 동적 검증 |
| G19 | `check_flaky_detector.py` | §11.4 47 | pytest 3회 반복 후 결과 변동 0 | flaky를 통과로 보지 않음 |
| G20 | `check_gate_self_test.py` | (메타) | 모든 quality 게이트가 자체 fixture로 violation 인공 생성 시 정확히 fail하는지 확인 | 게이트의 거짓 negative 차단 |

### Tier 3 — Root cause 메타 게이트 (PR/git 통합, 1일+)

| ID | 게이트 | 매핑 조항 | 정량 기준 | Root cause |
|---|---|---|---|---|
| G21 | `check_regression_test_per_bugfix.py` | §18.1, §18.2 4 | git log → "fix"/"bug"/"patch" 단어 들어간 커밋은 같은 커밋에 `tests/` 변경 동반 | 버그 수정에 회귀 테스트 강제 |
| G22 | `check_pr_root_cause_section.py` | §18.1 안티패턴 1 | PR description에 `## Root Cause` 섹션 강제 (GitHub Actions) | 증상 제거 패치 차단 |
| G23 | `check_anti_pattern_count.py` | §18.3 표 12종 | 12개 안티패턴을 정적 패턴(AST/regex)으로 인코딩 → 총 카운트 baseline + monotonic decrease | 안티패턴 monotonic decrease |

---

## 3. 우선순위 실행 순서

### 즉시 (다음 세션 첫 1~2시간)

**가장 ROI 높은 4개:**

1. **G1 `check_router_layer_purity`** — Fat Router 영구 차단 (~30분)
2. **G10 `check_repository_no_business`** — Repository 비즈니스 침투 차단 (~30분)
3. **G13 `check_contract_test_per_port`** — 새 boundary 정통화 강제. 다음 4개 boundary (Workflow/Stream/Search/Connector) 들어올 때 자동 검증 (~30분)
4. **G14 `check_doc_drift`** — 문서가 코드보다 거짓말 못 함 (~30분)

각 게이트는 **반드시 self-test와 함께** 박는다 (G20 원칙).

### 중기 (다음 2~3 세션)

5. G8 영역별 95% — 평균 게이트의 사각지대 청산
6. G2 audit on mutation — 보안 부채의 핵심
7. G6 pragma budget — coverage 우회 차단
8. G16 trace continuity (동적) — 관측성 약속 진짜 검증

### 장기 (Sprint 02A 마무리 후)

- G21 git hook (regression test per bugfix)
- G22 GitHub Action (PR Root Cause section)
- G17/G18 동적 audit/outbox consistency
- G19 flaky detector (3회 반복)

---

## 4. 추가 도구 후보 (남은 것)

다음 도구들은 아직 gate로 도입하지 않은 후보들이다. 이미 도입한
CodeQL, Semgrep, AST-grep, Tach, import-linter, vulture, interrogate,
Pyright strict, pytest-randomly, pytest-xdist, gitleaks는 위 Tier 섹션으로
승격했다.

| 도구 | 분류 | 효과 |
|---|---|---|
| `cosmic-ray` | 동적 | mutmut 4.x stat-collection 이슈가 계속되면 mutation testing 대체 엔진으로 검토 |
| OpenLineage CLI | 동적 | lineage 일관성 검증 (Foundry 차별점) |
| `git-secrets` 또는 `truffleHog` | 정적 | secret 누출 사후 차단 (Bandit 보완) |
| `safety` | 정적 | pip-audit 보완, CVE DB 다른 소스 |

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
