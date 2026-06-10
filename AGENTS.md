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
- `FoundryLiteCore`는 얇은 Facade로 유지한다.
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
- Secret/credential 노출은 `.gitleaks.toml`로 차단한다. 새 false positive를 발견하면 allowlist에 사유와 함께 등록한다. 시크릿이 실제로 들어왔다면 즉시 revoke + git history 정리한다.
- Cross-function 데이터 흐름 (request → SQL, request body → service, mutation → audit, exception → request_id)은 CodeQL queries (`scripts/quality/codeql/queries/`)로 검사한다. 새 violation 패턴을 발견하면 동일 디렉터리에 `.ql` 쿼리를 추가하고 `tests/unit/test_quality_codeql_queries.py`에 § 인용·@id·@kind 메타 검증을 추가한다. 로컬에서는 codeql CLI가 없으면 WARN+skip, CI(`.github/workflows/codeql.yml`)에서 강제 실행된다.
- 테스트 순서 의존성과 race condition은 pytest-randomly + pytest-xdist 두 plugin이 동적으로 강제한다. unit/contract test는 cwd에 의존하지 않는다. assets path는 conftest의 `DEMO_ROOT` 헬퍼를 쓰거나 `Path(__file__).resolve().parents[N]` 휴리스틱과 `mutants/` 부모 fallback을 같이 박는다.
- 안전 표현식, parser, 비즈니스 규칙 같은 작은 함수에는 hypothesis property test를 추가한다 (`tests/unit/test_*_properties.py`). 예시 기반 테스트가 못 잡는 엣지 케이스를 강제로 검증한다.

## 금지

- 엔지니어링 문서를 읽지 않고 구조 변경을 시작하지 않는다.
- god-class, god-service, fat-router를 만들지 않는다.
- `core._...` private facade helper 테스트를 만들지 않는다. facade는 public forwarder만 제공하고, 내부 실패 경계는 public API, service 소유 module, port/adapter contract test 중 가장 좁고 정직한 경계에서 검증한다.
- 문서에 아직 구현되지 않은 PostgreSQL JSONB, real CEL, real ERP writeback, Temporal, Alembic을 구현 완료처럼 쓰지 않는다.
- 테스트/CI 실패를 skip, xfail, flaky 처리로 우회하지 않는다.
