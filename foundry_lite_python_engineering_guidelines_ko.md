# Foundry-lite Python 백엔드 엔지니어링 가이드

**작성일:** 2026-06-09  
**문서 역할:** Foundry-lite의 Python 백엔드 구현 표준 원본  
**목표:** 개발자가 같은 기준으로 읽기 쉽고, 고치기 쉽고, 테스트 가능하며, 운영 중 추적 가능한 코드를 작성하게 만든다.

---

## 문서 지도

이 문서는 Foundry-lite 문서 체계의 **프로그래밍 원칙과 코드 품질 원본**이다. 제품이 무엇을 해야 하는지는 개발 기획서가 정하고, 어떤 순서로 만들지는 스프린트 문서가 정하며, 실제 Python 백엔드 코드를 어떤 기준으로 작성할지는 이 문서가 정한다.

- [ ] 제품 목표와 설계 원본은 [Foundry-lite 개발 기획서](./foundry_lite_development_plan_ko_sprintified.md)를 따른다.
- [ ] 구현 순서와 Acceptance Gate 원본은 [스프린트 실행 계획](./foundry_lite_sprint_breakdown_ko.md)을 따른다.
- [ ] Foundry 공개 문서 기반 근거는 [Palantir Foundry 심층 분석](./deep-research-report.md)을 따른다.
- [ ] Python 백엔드 코드의 구조, 이름, 테스트, 에러 처리, 트랜잭션, 운영 로그 기준은 이 문서를 따른다.

### 함께 읽을 문서

- [Foundry-lite 개발 기획서](./foundry_lite_development_plan_ko_sprintified.md): 왜 이런 모듈과 기능이 필요한지 확인한다.
- [스프린트 실행 계획](./foundry_lite_sprint_breakdown_ko.md): 이 코드 기준을 어느 스프린트에서 적용하고 검증할지 확인한다.
- [Palantir Foundry 심층 분석](./deep-research-report.md): 재현성, lineage, health checks, audit 같은 원칙의 외부 근거를 확인한다.

### 완료 판단 기준

- [ ] 새 Python 백엔드 코드는 이 문서의 SRP, 의존성 방향, 명명 규칙을 따른다.
- [ ] 모든 public API, worker activity, CLI command는 테스트와 운영 로그 기준을 만족한다.
- [ ] 모든 데이터 변경은 transaction, audit, idempotency, replay 가능성을 고려한다.
- [ ] 단순 증상 제거용 패치가 아니라 원인, 영향 범위, 회귀 방지책이 함께 정리되어 있다.
- [ ] 에러가 발생하면 request/run/action/dataset/object 단위로 추적 가능한 코드 구조를 가진다.
- [ ] [안티패턴 방지와 강제 대응 원칙](#18-안티패턴-방지와-강제-대응-원칙)을 위반한 변경은 완료로 보지 않는다.
- [ ] Python 백엔드 전체 테스트 커버리지는 line, branch, function 기준 모두 95% 이상이어야 한다.
- [ ] 필수 통합 테스트와 필수 스모크 테스트는 시나리오 수행률과 통과율 모두 100%여야 한다.
- [ ] CI에서 `ruff`, `mypy` 또는 `pyright`, `pytest`가 통과해야 한다.
- [ ] 예외적으로 기준을 어기는 경우에는 코드 주석이 아니라 설계 문서 또는 ADR에 이유를 남긴다.

---

## 1. 백엔드 언어와 기본 철학

Foundry-lite의 v1 백엔드는 **Python**을 기본 언어로 한다. 프론트엔드와 브라우저 SDK는 TypeScript를 사용할 수 있지만, API 서버, Worker, CLI, Dataset/Transform/Object/Action 핵심 백엔드 로직은 Python 기준으로 설계한다.

비개발자 관점에서 말하면, 이 문서의 목적은 “개발자가 마음대로 각자 다른 방식으로 만들지 않게 하는 약속”이다. Foundry-lite는 데이터와 운영 변경을 다루기 때문에, 코드는 단순히 작동하는 것을 넘어 “나중에 왜 이런 결과가 나왔는지 추적 가능해야” 한다.

### 1.1 v1 Python 기준

- [ ] Python 버전은 `3.12` 이상을 기본으로 한다.
- [ ] API 서버는 `FastAPI`와 `Pydantic v2`를 사용한다.
- [ ] DB migration은 `Alembic`을 사용한다.
- [ ] DB 접근은 `SQLAlchemy 2.x`를 기본으로 하되, transaction 경계는 application service에서 명시한다.
- [ ] Worker는 `Temporal Python SDK`를 사용한다.
- [ ] CLI는 `Typer`를 사용한다.
- [ ] 데이터 처리 runner는 `DuckDB`, `Polars`, `Pandas`를 상황에 맞게 사용하되, Dataset commit protocol은 공통으로 지킨다.
- [ ] 코드 품질 도구는 `ruff`, `mypy` 또는 `pyright`, `pytest`를 사용한다.

### 1.2 가장 중요한 원칙

- [ ] 코드는 짧게 쓰는 것보다 **명확하게 읽히는 것**을 우선한다.
- [ ] 한 함수, 한 클래스, 한 모듈은 **한 가지 이유로만 변경**되어야 한다.
- [ ] 데이터 변경은 반드시 **트랜잭션, 감사 로그, 실패 상태**를 남긴다.
- [ ] 외부 시스템 호출은 반드시 **재시도, 멱등성, timeout**을 고려한다.
- [ ] 도메인 규칙은 API 라우터나 DB 모델이 아니라 **application/domain layer**에 둔다.
- [ ] 테스트 없이 중요한 동작을 “완료”로 보지 않는다.
- [ ] 장애나 버그는 “눈앞의 에러를 없애는 패치”가 아니라 **원인 제거, 추적 가능성, 회귀 방지**까지 포함해야 완료로 본다.
- [ ] 코드 스타일 자체가 에러 추적을 가능하게 해야 한다. 즉, request id, run id, domain id, state transition, error type이 코드 흐름에서 끊기면 안 된다.
- [ ] 테스트 커버리지 95%는 평균 숫자 맞추기가 아니라 핵심 도메인, application service, repository, worker activity 각각에서 지켜야 하는 하한선이다.
- [ ] 통합 테스트와 스모크 테스트의 100% 기준은 “모든 필수 시나리오가 실행되고 모두 통과한다”는 뜻이다.

---

## 2. Clean Code 원칙

Clean Code는 예쁜 코드를 뜻하지 않는다. “다른 사람이 읽고, 고치고, 장애가 났을 때 원인을 찾을 수 있는 코드”를 뜻한다.

### 2.1 읽히는 이름

- [ ] 변수명은 데이터의 의미를 드러낸다.
- [ ] 함수명은 동작을 드러낸다.
- [ ] Boolean은 질문처럼 읽히게 짓는다: `is_committed`, `has_permission`, `can_execute`.
- [ ] 축약어는 팀 전체가 이미 아는 것만 쓴다.
- [ ] Foundry-lite 도메인 이름은 문서와 동일하게 유지한다.

좋은 예:

```python
def commit_dataset_version(transaction_id: DatasetTransactionId) -> DatasetVersion:
    ...
```

나쁜 예:

```python
def do_commit(id: str) -> dict:
    ...
```

### 2.2 작고 명확한 함수

- [ ] 한 함수는 하나의 결과를 만든다.
- [ ] 함수 안에서 validation, DB write, event publish, response formatting을 모두 처리하지 않는다.
- [ ] 함수가 40줄을 넘으면 책임이 섞였는지 확인한다.
- [ ] 조건문이 깊어지면 guard clause로 먼저 실패를 반환한다.
- [ ] 반환 타입을 명확히 적는다.

### 2.3 숨은 부작용 금지

부작용은 DB write, 파일 write, 외부 API 호출, queue publish, audit write처럼 시스템 상태를 바꾸는 동작이다.

- [ ] 조회 함수는 상태를 바꾸지 않는다.
- [ ] 상태 변경 함수는 함수명에 변경 의도를 드러낸다: `create_`, `commit_`, `abort_`, `apply_`, `publish_`.
- [ ] 외부 호출은 application service나 infrastructure adapter에 둔다.
- [ ] 테스트에서 부작용을 쉽게 대체할 수 있게 port/interface를 둔다.

### 2.4 주석보다 구조

- [ ] 코드가 복잡해서 주석이 필요하다면 먼저 함수/클래스 분리를 검토한다.
- [ ] 주석은 “무엇을 하는지”보다 “왜 이렇게 해야 하는지”를 설명한다.
- [ ] 비즈니스 규칙은 주석이 아니라 테스트 이름과 도메인 함수명으로 드러낸다.

---

## 3. SRP와 모듈 책임

SRP, Single Responsibility Principle은 “한 단위는 하나의 책임만 가져야 한다”는 원칙이다. 더 쉽게 말하면, 코드가 바뀌는 이유가 하나여야 한다.

### 3.1 책임 분리 기준

| 단위 | 맡아야 하는 일 | 맡으면 안 되는 일 |
|---|---|---|
| API Router | HTTP 요청/응답 변환, 인증 context 주입 | DB transaction 직접 제어, 도메인 규칙 판단 |
| Application Service | use case 실행, transaction 경계, 권한/검증 조합 | HTTP status formatting, raw SQL 세부 구현 |
| Domain Model | 불변 규칙, 상태 전이 규칙, 값 객체 | FastAPI/Pydantic/SQLAlchemy import |
| Repository | DB read/write 구현 | 비즈니스 의사결정 |
| Worker Activity | 재시도 가능한 외부 작업 실행 | workflow history에 비결정적 로직 넣기 |
| CLI Command | 사람이 실행하는 명령 입출력 | 핵심 도메인 로직 직접 구현 |

### 3.2 SRP 체크리스트

- [ ] 이 함수가 바뀌는 이유를 한 문장으로 설명할 수 있다.
- [ ] 이 클래스가 API, DB, 도메인 규칙을 동시에 알지 않는다.
- [ ] 테스트할 때 불필요한 외부 시스템을 많이 띄우지 않아도 된다.
- [ ] 실패했을 때 어느 책임에서 실패했는지 로그와 에러 타입으로 구분된다.
- [ ] 같은 규칙이 라우터, 서비스, worker에 중복 구현되어 있지 않다.

---

## 4. Python 프로젝트 구조

Foundry-lite는 백엔드 모듈을 기능별로 나누되, 의존성 방향은 항상 안쪽으로 향하게 한다.

```text
apps/
  api/                         # FastAPI 진입점
  worker/                      # Temporal worker 진입점
  cli/                         # Typer CLI 진입점
  web/                         # Next.js frontend

libs/
  foundry_lite/
    domain/                    # 순수 도메인 규칙
      dataset/
      ontology/
      object_store/
      action_runtime/
    application/               # use case와 transaction orchestration
    infrastructure/            # DB, object storage, Temporal, external adapter
    interfaces/                # API schemas, CLI adapters, generated contracts
    observability/             # logging, metrics, tracing helpers
    security/                  # auth context, RBAC, tenant isolation helpers
tests/
  unit/
  integration/
  e2e/
```

### 4.1 의존성 방향

- [ ] `domain`은 어떤 framework도 import하지 않는다.
- [ ] `application`은 `domain`과 port/interface를 사용한다.
- [ ] `infrastructure`는 port/interface를 구현한다.
- [ ] `api`, `worker`, `cli`는 application service를 호출한다.
- [ ] `api`가 repository를 직접 호출하지 않는다.
- [ ] `domain`이 SQLAlchemy, FastAPI, Temporal, MinIO/S3 SDK를 알지 않는다.

의존성 방향:

```text
api / worker / cli
→ application
→ domain

infrastructure
→ application ports
→ domain
```

### 4.2 디자인 패턴 적용 원칙

디자인 패턴은 “멋있어 보이는 구조”가 아니라, 변경 이유를 분리하고 장애 추적 경계를 선명하게 만드는 도구다. Foundry-lite에서는 아래 패턴을 적극적으로 사용하되, 패턴 이름을 붙이기 위해 불필요한 계층을 만들지 않는다.

| 패턴 | Foundry-lite에서 쓰는 위치 | 왜 쓰는가 |
|---|---|---|
| Facade | `FoundryLiteCore` 같은 public application entrypoint | API/CLI/test가 보는 입구는 안정적으로 유지하고 내부 구현은 service로 분해한다. |
| Application Service / Use Case Service | Dataset, Transform, Ontology, Object, Action, Materialization service | use case별 transaction, 권한, audit, run state를 한 책임 안에 묶는다. |
| Repository | dataset version, object record, action run 같은 DB read/write 경계 | SQLAlchemy 세부 구현과 비즈니스 판단을 분리한다. |
| Unit of Work | dataset commit, action apply, materialization commit | 함께 성공해야 하는 DB write, audit, outbox를 하나의 transaction 경계에 둔다. |
| Adapter | storage, compute, connector, writeback, auth provider | SQLite/PostgreSQL, local filesystem/S3, mock/real ERP처럼 구현체를 교체 가능하게 한다. |
| Strategy | filter evaluator, precondition evaluator, connector sync mode | 조건 평가나 실행 방식을 if/else 덩어리 대신 교체 가능한 전략으로 둔다. |
| Specification | object query filter, action precondition, permission condition | “이 객체가 조건을 만족하는가”를 테스트 가능한 규칙 객체/함수로 분리한다. |
| Template Method | dataset transaction protocol, materialization commit protocol | staging → check → manifest → version commit → audit/outbox 순서를 고정하고 세부 단계만 바꾼다. |
| Outbox | state change와 event publish 경계 | DB 변경과 이벤트 발행의 불일치를 막고 replay 가능성을 확보한다. |
| DTO / Command Object | API request, CLI command, worker input | raw dict가 시스템 안쪽으로 흘러 규칙을 흐리는 것을 막는다. |

패턴 적용 체크리스트:

- [ ] Facade는 얇아야 한다. public 호환성을 지키는 입구 역할을 하되, 실제 로직은 책임별 service에 둔다.
- [ ] Service는 use case 하나 또는 매우 가까운 use case 묶음만 맡는다.
- [ ] Repository는 DB read/write만 맡고 permission, precondition, mutation policy를 판단하지 않는다.
- [ ] Adapter는 외부 시스템 세부를 숨기되, 실패/timeout/retry/idempotency 정보를 application layer로 돌려준다.
- [ ] Strategy나 Specification은 테스트가 쉬워야 한다. 조건 하나를 검증하기 위해 전체 API 서버를 띄우게 만들지 않는다.
- [ ] Template Method는 순서를 고정해야 할 때만 쓴다. 순서가 중요하지 않은 단순 helper에 억지로 적용하지 않는다.
- [ ] 패턴을 적용한 뒤에도 trace id, run id, audit event, outbox event가 끊기면 실패한 설계로 본다.

### 4.3 Scale Foundation과 Infra Swap 규칙

Scale Foundation은 “처음부터 Spark, Flink, Kafka, S3를 모두 붙인다”는 뜻이 아니다. 뜻은 더 실용적이다. 작은 로컬 구현으로 시작하더라도, 나중에 큰 인프라로 바꿀 때 업무 규칙과 추적 체계가 흔들리지 않도록 경계를 먼저 고정한다.

비개발자에게 설명하면, Foundry-lite의 핵심 제품 로직은 “운영 규칙과 감사 장부”이고 인프라는 “장비”다. 장비가 바뀌어도 운영 규칙과 감사 장부는 바뀌면 안 된다.

| Boundary | 현재 작은 구현 | 나중의 큰 구현 | code가 지켜야 하는 규칙 |
|---|---|---|---|
| MetadataRepository | SQLite/SQLAlchemy local DB | PostgreSQL, partitioned tables | service는 DB dialect 세부를 알지 않는다. |
| DatasetStorageAdapter | local filesystem, MinIO | S3/GCS/Azure Blob, Iceberg storage | dataset commit protocol은 storage 종류와 무관해야 한다. |
| DatasetTransactionRepository | 단일 DB transaction | PostgreSQL transaction + Alembic | OPEN/COMMITTED/ABORTED 상태 전이는 동일해야 한다. |
| RuntimeRepository | SQLAlchemy audit/outbox/lineage/run table | PostgreSQL partitioned audit/outbox, later stream publisher state | audit, outbox, lineage, run state의 key 의미가 동일해야 한다. |
| ComputeAdapter | DuckDB | Spark, Flink bounded job, Ray | input version, output staging, lineage, health gate는 동일해야 한다. |
| EventPublisher/StreamAdapter | PostgreSQL outbox | Kafka/Redpanda | event idempotency, DLQ, replay cursor는 동일해야 한다. |
| SearchAdapter | PostgreSQL JSON/generated column | OpenSearch | search는 projection이고 object store가 source of truth다. |
| WorkflowAdapter | direct call/local worker | Temporal | retry, timeout, run state, replay key를 잃지 않는다. |
| ConnectorAdapter | CSV/local/mock connector | REST, webhook, CDC, SaaS connector | sync run lifecycle과 cursor/checkpoint 의미가 동일해야 한다. |
| AuthProvider/PolicyAdapter | dev header + RBAC | OIDC/SSO, ABAC/CBAC | permission decision과 audit deny 형식이 동일해야 한다. |

Infra swap 체크리스트:

- [ ] application/domain layer는 concrete infra SDK를 직접 import하지 않는다.
- [ ] infrastructure adapter만 concrete SDK, filesystem, DB dialect, external API client를 안다.
- [ ] concrete implementation 선택은 composition root에서만 한다: `apps/api`, `apps/worker`, `apps/cli`, test fixture.
- [ ] port/interface input/output DTO는 vendor-specific 필드를 숨긴다.
- [ ] adapter error는 typed error로 변환하되, retryability, timeout, idempotency key, external reference, correlation id를 버리지 않는다.
- [ ] fake adapter와 local adapter가 같은 contract test suite를 통과한다.
- [ ] adapter를 교체해도 public API response, audit event, outbox event, lineage edge의 의미가 바뀌지 않는다.
- [ ] trace key는 boundary를 넘을 때 유지된다: `tenant_id`, `actor_user_id`, `request_id`, `run_id`, `correlation_id`, domain id, cursor/checkpoint.
- [ ] 새 infra boundary를 추가하면 문서에 local implementation, scale implementation, owner, failure mode, contract test를 같이 추가한다.
- [ ] “나중에 바꿀 예정”이라는 말만 있고 port/interface와 contract test가 없으면 완료로 보지 않는다.

Scale Foundation 이후 금지:

- [ ] application service가 `boto3`, `pyspark`, Kafka client, OpenSearch client, Temporal client, vendor SaaS SDK를 직접 import한다.
- [ ] repository가 permission, precondition, merge policy 같은 비즈니스 판단을 한다.
- [ ] adapter가 외부 실패를 숨기고 성공처럼 반환한다.
- [ ] Spark/Kafka/S3 같은 대형 도구를 붙였지만 dataset transaction, audit, lineage, replay contract를 우회한다.
- [ ] vendor-specific payload가 core DTO로 흘러들어 다른 adapter 구현을 어렵게 만든다.

금지하는 패턴 오남용:

- [ ] 이름만 Repository이고 실제로는 비즈니스 규칙을 판단하는 DB 클래스.
- [ ] 모든 일을 Manager, Handler, Processor 하나에 넣는 가짜 service.
- [ ] 상속 계층이 깊어져서 어느 메서드가 실행되는지 추적하기 어려운 구조.
- [ ] Adapter가 외부 실패를 숨기고 성공처럼 반환하는 구조.
- [ ] Strategy를 만들었지만 실제 구현체가 하나뿐이고 테스트성도 좋아지지 않는 구조.
- [ ] DTO 없이 `dict[str, Any]`를 계속 전달하면서 패턴 이름만 붙이는 구조.

---

## 5. 코드 컨벤션

### 5.1 파일과 이름

- [ ] 패키지와 모듈은 `snake_case`를 쓴다.
- [ ] 클래스는 `PascalCase`를 쓴다.
- [ ] 함수와 변수는 `snake_case`를 쓴다.
- [ ] 상수는 `UPPER_SNAKE_CASE`를 쓴다.
- [ ] DB 테이블과 컬럼은 `lower_snake_case`를 쓴다.
- [ ] dataset 이름은 문서와 같이 `raw.erp_orders`, `clean.orders`처럼 쓴다.
- [ ] object type은 `Order`, `Customer`처럼 단수 PascalCase를 쓴다.
- [ ] action type은 `ApproveOrder`처럼 동사+대상 형태로 쓴다.

### 5.2 타입 힌트

- [ ] public 함수에는 parameter와 return type을 모두 적는다.
- [ ] `Any`는 adapter boundary나 JSON passthrough 외에는 사용하지 않는다.
- [ ] `dict[str, Any]` 대신 가능한 Pydantic model, dataclass, TypedDict를 사용한다.
- [ ] `Optional[T]`는 값이 정말 없을 수 있을 때만 사용한다.
- [ ] list/dict는 `list[T]`, `dict[str, T]`처럼 구체적으로 적는다.

### 5.3 Pydantic 사용 기준

- [ ] API request/response schema는 Pydantic model로 정의한다.
- [ ] domain entity를 API response model로 직접 노출하지 않는다.
- [ ] DB row model을 API response로 직접 반환하지 않는다.
- [ ] validation error는 사용자에게 이해 가능한 field 단위 에러로 바꾼다.

### 5.4 Import 규칙

- [ ] 표준 라이브러리, 외부 패키지, 내부 패키지 순서로 정렬한다.
- [ ] 순환 import가 생기면 구조가 잘못된 신호로 본다.
- [ ] `from module import *`는 사용하지 않는다.
- [ ] module-level에서 무거운 외부 연결을 만들지 않는다.

---

## 6. API 작성 가이드

### 6.1 FastAPI Router 원칙

- [ ] Router는 HTTP 계약만 다룬다.
- [ ] Router에서 비즈니스 규칙을 판단하지 않는다.
- [ ] Router는 application service를 호출하고 결과를 response schema로 바꾼다.
- [ ] 모든 mutation endpoint는 actor, tenant, request id를 application layer로 넘긴다.
- [ ] 모든 mutation endpoint는 audit 대상인지 확인한다.

### 6.2 Endpoint naming

- [ ] REST resource는 복수형을 쓴다: `/datasets`, `/ontology-drafts`, `/objects`.
- [ ] action 실행은 동사를 endpoint에 넣을 수 있다: `/actions/{action_type}/apply`.
- [ ] run 시작은 `/runs` 또는 `/.../{id}/runs` 형태로 둔다.
- [ ] 내부 식별자는 response에 명확히 노출한다: `datasetId`, `versionId`, `runId`.

### 6.3 API 체크리스트

- [ ] request schema가 명확하다.
- [ ] response schema가 명확하다.
- [ ] 권한 실패와 validation 실패가 구분된다.
- [ ] mutation은 idempotency key 또는 재시도 전략을 갖는다.
- [ ] 에러 응답에 `request_id`가 포함된다.
- [ ] API 테스트가 성공/실패 경로를 모두 검증한다.

---

## 7. 트랜잭션과 데이터 변경

Foundry-lite는 데이터 변경 이력이 핵심이다. “DB에 저장됐다”만으로 충분하지 않다. 어떤 사용자가, 어떤 요청으로, 어떤 상태를, 왜 바꿨는지 남아야 한다.

### 7.1 트랜잭션 경계

- [ ] DB transaction은 application service에서 시작하고 끝낸다.
- [ ] Repository가 임의로 commit하지 않는다.
- [ ] 하나의 use case에서 반드시 같이 성공해야 하는 write는 하나의 transaction에 넣는다.
- [ ] 외부 API 호출은 DB transaction 안에서 오래 붙잡지 않는다.
- [ ] outbox event는 state change와 같은 DB transaction에 기록한다.

### 7.2 Dataset commit 원칙

- [ ] `COMMITTED` dataset version은 immutable이다.
- [ ] staging path에 먼저 쓰고, health check 통과 후 manifest pointer를 commit한다.
- [ ] 실패한 run은 committed version을 만들지 않는다.
- [ ] schema, lineage, health result는 version과 함께 추적 가능해야 한다.

### 7.3 Action Runtime 원칙

- [ ] Action 실행은 `expectedObjectVersion`을 검증한다.
- [ ] 같은 idempotency key로 재시도해도 같은 결과가 나와야 한다.
- [ ] object edit, action run, audit event, outbox event는 일관되게 남아야 한다.
- [ ] permission denied도 audit event로 남긴다.

---

## 8. 에러 처리

에러 처리는 사용자와 운영자 모두를 위한 언어다. 사용자는 “무엇을 고치면 되는지”를 알아야 하고, 운영자는 “어디서 왜 실패했는지”를 추적할 수 있어야 한다.

### 8.1 에러 분류

| 분류 | 예시 | 처리 |
|---|---|---|
| ValidationError | 잘못된 request body, schema mismatch | 사용자에게 수정 가능한 메시지 반환 |
| PermissionError | 권한 없음, tenant mismatch | audit deny 기록 |
| ConflictError | object version 충돌, duplicate idempotency key | 재조회/재시도 안내 |
| NotFoundError | dataset/action/object 없음 | 404와 request_id 반환 |
| ExternalSystemError | PostgreSQL snapshot 실패, webhook 실패 | retry 또는 DLQ |
| InternalError | 예상하지 못한 버그 | 상세 내부 정보 숨기고 trace/log로 추적 |

### 8.2 에러 처리 체크리스트

- [ ] broad `except Exception`으로 에러를 삼키지 않는다.
- [ ] retry 가능한 에러와 retry하면 안 되는 에러를 구분한다.
- [ ] 사용자 응답에 secret, SQL, stack trace를 노출하지 않는다.
- [ ] 로그에는 `request_id`, `tenant_id`, `run_id` 같은 추적 키를 넣는다.
- [ ] 실패 상태는 DB에 durable하게 남긴다.

### 8.3 에러 추적 가능한 코드 스타일

에러 추적 가능성이란 “로그를 많이 찍는다”는 뜻이 아니다. 어떤 요청이 어떤 run을 만들었고, 어떤 dataset/object/action을 건드렸으며, 어느 책임 계층에서 실패했는지 이어서 볼 수 있어야 한다는 뜻이다.

- [ ] 모든 entrypoint는 `RequestContext` 또는 동등한 context 객체를 만든다.
- [ ] context에는 가능한 한 `request_id`, `tenant_id`, `actor_user_id`, `trace_id`를 포함한다.
- [ ] long-running 작업은 `run_id`를 만들고 API 응답, DB run table, worker log에 같은 id를 남긴다.
- [ ] dataset 변경은 `dataset_id`, `transaction_id`, `version_id`를 함께 남긴다.
- [ ] object/action 변경은 `object_type`, `object_id`, `object_version`, `action_run_id`를 함께 남긴다.
- [ ] 에러 타입은 domain error, application error, infrastructure error로 구분한다.
- [ ] 예외를 재포장할 때 원래 예외를 잃지 않는다. Python에서는 `raise NewError(...) from exc`를 사용한다.
- [ ] 실패를 로그에만 남기지 않고, 필요한 경우 `runs`, `audit_events`, `dead_letter_events`, `action_runs` 같은 durable state에 남긴다.

좋은 예:

```python
try:
    committed_version = await service.commit_dataset_transaction(ctx, command)
except StorageWriteError as exc:
    raise DatasetCommitFailed(
        dataset_id=command.dataset_id,
        transaction_id=command.transaction_id,
        request_id=ctx.request_id,
    ) from exc
```

나쁜 예:

```python
try:
    committed_version = await service.commit(command)
except Exception:
    logger.error("commit failed")
    raise
```

---

## 9. Worker와 비동기 작업

### 9.1 Temporal 원칙

- [ ] Workflow는 결정적이어야 한다.
- [ ] 외부 API 호출, DB write, 파일 write는 Activity에서 수행한다.
- [ ] Activity는 timeout, retry policy, idempotency를 가진다.
- [ ] Workflow input/output은 versioned schema로 관리한다.
- [ ] 장기 작업은 API request 안에서 직접 실행하지 않는다.

### 9.2 Async 원칙

- [ ] I/O 작업은 async를 사용하되, CPU-bound 작업은 worker/process로 분리한다.
- [ ] FastAPI event loop에서 blocking DB/file/network 작업을 오래 수행하지 않는다.
- [ ] background task로 중요한 작업을 fire-and-forget 처리하지 않는다.
- [ ] 상태를 바꾸는 비동기 작업은 run table이나 workflow history에 남긴다.

---

## 10. 보안과 거버넌스

### 10.1 기본 보안 원칙

- [ ] tenant isolation은 application layer와 DB layer에서 모두 지킨다.
- [ ] secret은 코드, test fixture, 로그에 남기지 않는다.
- [ ] 사용자가 입력한 문자열을 SQL, Python eval, shell command로 직접 실행하지 않는다.
- [ ] property masking은 API response의 모든 경로에서 적용한다.
- [ ] 권한 실패도 audit에 남긴다.

### 10.2 금지 목록

- [ ] `eval`, `exec` 사용 금지
- [ ] raw SQL string interpolation 금지
- [ ] secret hardcoding 금지
- [ ] tenant_id 없는 도메인 write 금지
- [ ] audit 없는 mutation 금지
- [ ] DB transaction 밖에서 object state와 outbox event를 따로 쓰기 금지

---

## 11. 테스트 전략

테스트는 개발자를 괴롭히기 위한 절차가 아니라, 나중에 안심하고 고치기 위한 안전장치다.

### 11.1 테스트 종류

| 테스트 | 목적 | 예시 |
|---|---|---|
| Unit test | 작은 규칙 검증 | schema compatibility, action precondition |
| Integration test | DB/Storage/Worker 포함 흐름 검증 | dataset commit, transform run |
| Contract test | adapter/API 계약 검증 | connector response, SDK schema |
| E2E test | 사용자 관점 폐루프 검증 | upload → transform → action → materialization |
| Regression test | 한번 난 버그 재발 방지 | idempotency duplicate submit |

### 11.2 pytest 컨벤션

- [ ] 테스트 파일은 `test_*.py`로 작성한다.
- [ ] 테스트 이름은 기대 결과를 문장처럼 쓴다.
- [ ] fixture는 필요한 범위로만 둔다.
- [ ] 외부 서비스가 필요한 테스트는 integration marker를 붙인다.
- [ ] flaky test는 성공으로 보지 않는다.

좋은 테스트 이름:

```python
def test_commit_dataset_version_aborts_when_primary_key_check_fails() -> None:
    ...
```

### 11.3 테스트 체크리스트

- [ ] 성공 경로가 있다.
- [ ] 실패 경로가 있다.
- [ ] 권한 실패가 있다.
- [ ] idempotency/retry 경로가 있다.
- [ ] audit/log/run state 검증이 있다.
- [ ] 중요한 버그 수정에는 regression test가 있다.

### 11.4 테스트 커버리지와 통합/스모크 기준

Foundry-lite의 테스트 기준은 “대충 주요 부분만 테스트했다”가 아니라, 운영 폐루프를 안전하게 고칠 수 있는 수준을 요구한다.

- [ ] Python 백엔드 전체 line coverage는 95% 이상이어야 한다.
- [ ] Python 백엔드 전체 branch coverage는 95% 이상이어야 한다.
- [ ] Python 백엔드 public function/method coverage는 95% 이상이어야 한다.
- [ ] domain, application, infrastructure, API, worker, CLI 영역 중 어느 하나도 95% 기준을 크게 밑돌면 평균으로 덮지 않는다.
- [ ] 통합 테스트는 필수 통합 시나리오 목록의 100%를 실행하고 100% 통과해야 한다.
- [ ] 스모크 테스트는 릴리스 전 필수 스모크 체크리스트의 100%를 실행하고 100% 통과해야 한다.
- [ ] skipped test는 기본적으로 통과로 보지 않는다. 의도적 skip은 이슈/ADR/스프린트 backlog와 연결되어야 한다.
- [ ] flaky test는 통과로 보지 않는다. 재시도 후 통과하는 테스트도 원인을 추적해야 한다.
- [ ] 커버리지 제외(`pragma: no cover`)는 framework glue, defensive branch, impossible branch처럼 명확한 이유가 있는 경우에만 허용한다.

필수 통합 테스트 시나리오:

- [ ] connector sync → raw dataset commit
- [ ] raw dataset → DuckDB transform → clean dataset commit
- [ ] ontology import/activation → object index
- [ ] object query → action apply → object edit/outbox/audit
- [ ] action log/object snapshot materialization → downstream transform
- [ ] permission denied와 tenant isolation
- [ ] failed run → retry/replay 또는 DLQ 확인

필수 스모크 테스트 시나리오:

- [ ] API `/healthz`
- [ ] DB migration 적용
- [ ] Worker heartbeat
- [ ] CLI 기본 명령 실행
- [ ] seed demo 생성
- [ ] dataset upload 또는 sync run
- [ ] transform run
- [ ] ontology activate
- [ ] object query
- [ ] `ApproveOrder` action apply
- [ ] materialization run
- [ ] Web Home 또는 Object Explorer 최소 진입

---

## 12. 관측성: 로그, 메트릭, 트레이스

운영 중 장애가 나면 코드를 쓴 사람이 옆에 없을 수 있다. 그래서 시스템은 스스로 설명할 수 있어야 한다.

### 12.1 로그 원칙

- [ ] 로그는 structured JSON을 기본으로 한다.
- [ ] 모든 로그에는 가능한 한 `request_id`, `tenant_id`, `actor_user_id`, `run_id`를 넣는다.
- [ ] password, token, credential, raw secret은 로그에 남기지 않는다.
- [ ] 에러 로그는 원인과 영향 범위를 드러낸다.

### 12.2 메트릭 예시

- [ ] dataset commit duration
- [ ] transform run duration
- [ ] action apply latency
- [ ] object query latency
- [ ] outbox publish lag
- [ ] failed run count
- [ ] DLQ size

### 12.3 Trace 원칙

- [ ] API request에서 worker activity까지 trace context를 이어간다.
- [ ] 긴 폐루프는 run id와 trace id를 함께 남긴다.
- [ ] replay/reindex 작업은 일반 요청과 구분되는 operation id를 가진다.

---

## 13. 코드 리뷰 체크리스트

Pull Request를 리뷰할 때는 취향보다 위험을 먼저 본다.

- [ ] 이 변경이 어떤 사용자/운영자 문제를 해결하는지 명확하다.
- [ ] 책임이 router, service, domain, repository 사이에 올바르게 나뉘어 있다.
- [ ] 새 mutation은 transaction, audit, idempotency를 고려했다.
- [ ] tenant/RBAC/property masking 경로가 빠지지 않았다.
- [ ] 실패 상태가 DB/run/log에서 추적 가능하다.
- [ ] 테스트가 성공 경로와 실패 경로를 모두 검증한다.
- [ ] public API나 schema 변경은 문서와 예제를 업데이트했다.
- [ ] 새 의존성이 꼭 필요한 이유가 있다.
- [ ] 임시 코드는 TODO만 남기지 않고 추적 가능한 backlog나 ADR로 연결했다.

---

## 14. 구현 전 체크리스트

새 기능을 코딩하기 전에 아래를 확인한다.

- [ ] 이 기능이 어느 스프린트의 Acceptance Gate를 통과시키는지 안다.
- [ ] 관련 설계 섹션을 읽었다.
- [ ] input/output schema가 정해졌다.
- [ ] state transition이 정해졌다.
- [ ] 실패 상태가 정해졌다.
- [ ] audit 대상인지 정했다.
- [ ] permission check 위치를 정했다.
- [ ] 테스트 종류를 정했다.
- [ ] 운영자가 실패를 어디에서 볼 수 있는지 정했다.

---

## 15. Foundry-lite 도메인 명명 사전

모든 문서와 코드에서 같은 이름을 사용한다.

| 개념 | 코드/문서 이름 |
|---|---|
| 주문 원천 데이터 | `raw.erp_orders` |
| 고객 원천 데이터 | `raw.crm_customers` |
| 정제 주문 데이터 | `clean.orders` |
| 정제 고객 데이터 | `clean.customers` |
| 액션 로그 데이터셋 | `ops.action_log` |
| 현재 주문 스냅샷 | `ops.order_current` |
| 주문 객체 | `Order` |
| 고객 객체 | `Customer` |
| 주문 승인 액션 | `ApproveOrder` |

---

## 16. CI 품질 게이트

Python 백엔드 PR은 최소한 아래 명령을 통과해야 한다.

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy libs apps/api apps/worker apps/cli
uv run pytest tests/unit tests/contract \
  --cov=libs \
  --cov=apps/api \
  --cov=apps/worker \
  --cov=apps/cli \
  --cov-branch \
  --cov-fail-under=95
uv run pytest tests/integration
uv run pytest tests/smoke
```

프로젝트에서 `pyright`를 선택하면 `mypy` 대신 아래를 사용한다.

```bash
uv run pyright
```

### CI 통과 기준

- [ ] lint 오류가 없다.
- [ ] formatter가 적용되어 있다.
- [ ] type check가 통과한다.
- [ ] unit/contract test가 통과한다.
- [ ] Python 백엔드 line/branch/function coverage가 모두 95% 이상이다.
- [ ] 필수 integration test 시나리오가 100% 실행되고 100% 통과한다.
- [ ] 필수 smoke test 시나리오가 100% 실행되고 100% 통과한다.
- [ ] API 계약 변경이면 contract test 또는 generated schema snapshot이 갱신되어 있다.
- [ ] skip/flaky/xfail 테스트가 release gate를 우회하지 않는다.

---

## 17. 이 문서의 적용 범위

- [ ] API 서버 Python 코드
- [ ] Worker Python 코드
- [ ] CLI Python 코드
- [ ] Dataset/Transform/Object/Action domain Python 코드
- [ ] Python Transform SDK 코드
- [ ] 테스트 코드

프론트엔드 TypeScript와 generated SDK는 별도 frontend/codegen 규칙을 둘 수 있지만, API 계약, naming, audit, idempotency, 권한 원칙은 이 문서와 일치해야 한다.

---

## 18. 안티패턴 방지와 강제 대응 원칙

안티패턴은 “당장은 빨라 보이지만 나중에 장애, 중복, 추적 불가, 재작업을 만드는 습관”이다. Foundry-lite는 데이터 변경과 운영 액션을 다루기 때문에, 안티패턴을 방치하면 단순 버그가 아니라 “왜 이런 데이터가 생겼는지 모르는 상태”가 된다.

### 18.1 절대 금지 원칙

- [ ] 에러 메시지만 없애는 단순 패치 금지
- [ ] 원인 분석 없이 조건문을 추가해 증상만 피하는 방식 금지
- [ ] 테스트 없이 hotfix를 완료로 선언 금지
- [ ] audit, run state, trace id 없이 mutation을 추가하는 것 금지
- [ ] router, service, repository에 같은 비즈니스 규칙을 중복 구현 금지
- [ ] 실패를 로그에만 남기고 DB 상태로 남기지 않는 방식 금지
- [ ] `except Exception: pass` 또는 의미 없는 재시도 금지
- [ ] 임의 `sleep`, magic number, hidden global state로 race condition을 덮는 방식 금지
- [ ] schema mismatch를 `dict[str, Any]`로 우회하는 방식 금지
- [ ] migration 없이 DB 모양을 코드 가정으로만 바꾸는 방식 금지

### 18.2 간단 패치 금지 기준

간단한 패치가 모두 나쁜 것은 아니다. 하지만 아래 중 하나라도 해당하면 “간단 패치로 처리”하면 안 된다.

- [ ] 데이터 정합성, transaction, commit protocol에 영향을 준다.
- [ ] object state, action run, audit event, outbox event에 영향을 준다.
- [ ] tenant isolation, permission, property masking에 영향을 준다.
- [ ] retry, idempotency, concurrency와 관련이 있다.
- [ ] 같은 문제가 두 번 이상 반복되었다.
- [ ] 실패 원인을 로그나 run table에서 추적할 수 없다.
- [ ] 테스트가 없어서 변경의 안전성을 설명할 수 없다.

이 경우에는 반드시 아래 순서로 대응한다.

1. 재현 조건을 기록한다.
2. 원인 계층을 분리한다: API, application, domain, repository, infrastructure, worker.
3. 영향을 받는 상태를 확인한다: DB row, dataset version, object record, action run, outbox event.
4. 수정 전에 실패 테스트 또는 regression test를 만든다.
5. 코드 수정은 가장 작은 범위로 하되, 책임 경계를 흐리지 않는다.
6. 로그, trace, audit, run state 중 어떤 경로로 추적되는지 확인한다.
7. 문서나 체크리스트가 바뀌어야 하면 함께 수정한다.

### 18.3 대표 안티패턴과 대응 원칙

| 안티패턴 | 왜 위험한가 | 강제 대응 |
|---|---|---|
| 증상 제거 패치 | 원인은 남고 다른 경로에서 재발한다. | 실패 테스트를 먼저 만들고 원인 계층을 찾아 수정한다. |
| Fat Router | HTTP layer에 비즈니스 규칙이 섞여 재사용과 테스트가 어려워진다. | Router는 schema/context 변환만 하고 application service로 이동한다. |
| God Service | 하나의 service가 dataset, object, action, audit를 모두 직접 만진다. | use case 단위 service와 domain policy로 분리한다. |
| Repository에 비즈니스 규칙 넣기 | DB 접근 코드가 정책 판단까지 하게 되어 규칙 중복이 생긴다. | Repository는 read/write만 맡고 규칙은 domain/application에 둔다. |
| Silent Failure | 실패가 사용자나 운영자에게 보이지 않는다. | 실패 상태를 DB에 저장하고 error type, run id, trace id를 남긴다. |
| Log-only Audit | 로그 보관 정책에 따라 감사 근거가 사라질 수 있다. | mutation은 durable `audit_events`를 남긴다. |
| Any 남발 | schema drift를 놓치고 런타임 장애가 늘어난다. | Pydantic model, dataclass, TypedDict로 계약을 고정한다. |
| 중복 validation | 경로마다 다른 판정이 생긴다. | domain validator 또는 application policy로 한 곳에 모은다. |
| Transaction 쪼개기 | object state와 event/audit가 불일치할 수 있다. | 함께 성공해야 하는 write는 하나의 DB transaction에 둔다. |
| Fire-and-forget 작업 | 실패해도 추적할 수 없다. | Temporal workflow, run table, outbox/DLQ로 durable하게 관리한다. |
| 임의 재시도 | 중복 write, 중복 action, 외부 시스템 오염이 생긴다. | idempotency key와 retry policy를 함께 설계한다. |
| Magic fallback | 잘못된 데이터가 조용히 정상처럼 흐른다. | fallback 조건을 명시하고 audit/log/metric으로 관측한다. |

### 18.4 PR 차단 체크리스트

아래 중 하나라도 `예`이면 PR은 통과하면 안 된다.

- [ ] 이 변경은 실패 원인을 설명하지 못한다.
- [ ] 이 변경은 재현 테스트 없이 증상만 없앤다.
- [ ] 이 변경은 에러 추적 키를 끊는다.
- [ ] 이 변경은 audit 대상 mutation인데 audit를 남기지 않는다.
- [ ] 이 변경은 run state 없이 장기 작업을 실행한다.
- [ ] 이 변경은 transaction 경계를 불명확하게 만든다.
- [ ] 이 변경은 `Any`, raw dict, broad exception으로 schema/error 문제를 숨긴다.
- [ ] 이 변경은 기존 설계 문서와 다른 방향인데 ADR이나 문서 수정이 없다.

### 18.5 운영 장애 대응 원칙

운영 장애를 고칠 때는 “서비스가 다시 켜졌다”만으로 완료하지 않는다.

- [ ] 장애 타임라인을 남긴다.
- [ ] 영향받은 tenant, dataset, object, action, run 범위를 확인한다.
- [ ] 재처리/replay가 필요한 데이터를 식별한다.
- [ ] 재발 방지 테스트를 추가한다.
- [ ] 로그/trace/audit/run state 중 어느 정보가 부족했는지 확인한다.
- [ ] 부족한 추적 정보가 있으면 코드 스타일 또는 관측성 기준을 보강한다.
- [ ] 사용자 데이터가 바뀐 경우 감사 가능한 복구 기록을 남긴다.
