# AGENTS.md instructions for Foundry-lite

## 필수 시작 절차

이 저장소에서 코드, 테스트, 문서, CI, 아키텍처 변경을 시작하기 전에 반드시 먼저 아래 문서를 읽는다.

1. `foundry_lite_python_engineering_guidelines_ko.md`
2. 변경 범위가 제품/스프린트 목표와 연결되면 `foundry_lite_development_plan_ko_sprintified.md`
3. 변경 범위가 sprint acceptance나 MVP core 완료 조건과 연결되면 `foundry_lite_sprint_breakdown_ko.md`
4. 현재 구현 상태와 문서상 목표를 구분해야 하면 `docs/implementation-status.md`

특히 `foundry_lite_python_engineering_guidelines_ko.md`는 이 repo의 Python 백엔드 구현 표준 원본이다. 개발 시작 전에 읽지 않은 상태로 구현을 진행하면 안 된다.

## 개발 원칙

- 비개발자도 이해할 수 있도록 항상 친절하고 자세히 설명한다.
- 문서와 코드가 다르면 코드를 과장하지 말고 현재 구현 상태를 정직하게 문서화한다.
- `FoundryLiteCore`는 얇은 Facade로 유지한다.
- Dataset, Transform, Ontology, Object, Action, Materialization, Runtime event, Demo orchestration 책임을 다시 한 파일로 합치지 않는다.
- application module은 500줄 이하를 유지한다. 필요하면 책임별 service, strategy, specification, adapter, repository로 분리한다.
- 디자인 패턴은 `foundry_lite_python_engineering_guidelines_ko.md`의 “디자인 패턴 적용 원칙”을 따른다.
- 새 mutation은 transaction, audit, outbox, idempotency, error traceability를 함께 고려한다.
- 변경 후에는 최소한 관련 테스트와 정적 검사를 실행하고, 가능하면 `pnpm ci:gate`로 전체 품질 게이트를 확인한다.

## 금지

- 엔지니어링 문서를 읽지 않고 구조 변경을 시작하지 않는다.
- god-class, god-service, fat-router를 만들지 않는다.
- private helper 직접 테스트를 늘리지 않는다.
- 문서에 아직 구현되지 않은 PostgreSQL JSONB, real CEL, real ERP writeback, Temporal, Alembic을 구현 완료처럼 쓰지 않는다.
- 테스트/CI 실패를 skip, xfail, flaky 처리로 우회하지 않는다.
