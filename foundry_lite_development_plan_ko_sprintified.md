# Foundry-lite 개발 기획서

**작성일:** 2026-06-09  
**개정 상태:** 2026-06-09 심층리뷰 반영본  
**목표:** Palantir Foundry 전체를 복제하는 것이 아니라, 핵심 철학인 “데이터 유입 → 변환 → 온톨로지 인덱싱 → 운영 객체 조회 → 액션 실행 → 데이터셋으로 환류”가 실제로 반복 실행되는 **재현 가능한 운영 폐루프 MVP**를 단일 모노레포 안에 구현한다.  
**개정 원칙:** v1은 기능 나열식 MVP가 아니라, replay 가능한 최소 폐루프를 안정적으로 구현하는 vertical slice로 제한한다. Kafka/CDC/Elasticsearch/Spark/복잡한 보안 모델은 MVP core 완료 조건에서 제외하되, 나중에 쉽게 갈아끼울 수 있는 port/interface, adapter contract, trace key, composition root 경계는 Sprint 02A Scale Foundation에서 먼저 고정한다.

> 현재 구현 상태 주의: 2026-06-18 기준 현재 구현이 실제로 보장하는 범위는 [Implementation Status](./docs/implementation-status.md)를 원본으로 본다. 완료 체크박스가 `[x]`인 상태 추적 항목은 [Sprint Evidence Ledger](./docs/sprint-evidence-ledger.md)에 PR, merge commit, 테스트, 품질 게이트 근거가 있어야 한다. 개발 가이드용 체크리스트는 제품 완료 상태가 아니라 매 변경 때 확인하는 템플릿으로 본다.
>
> 구현 동기화 메모: 현재 checkout은 Sprint 00~36, Sprint 02A, Sprint 36A의 MVP core/운영 안정성 체크를 완료한 상태다. Sprint 37~42의 REST/Webhook, stream archive, Debezium CDC, CDC object indexing, Elasticsearch-compatible search projection은 MVP 이후 확장 proof로 구현 증거가 있다. Sprint 43 Iceberg ratchet과 Sprint 44 Spark ratchet은 `docs/infra-ratchet.md`와 `docs/infra-tricky-matrix.json` 기준 active-covered proof가 있으며, Sprint 45 Kubernetes/backup-restore 운영 패키지는 아직 future scope다. S46 이후 확장 순서는 [Data Platform Expansion Roadmap](./docs/data-platform-expansion-roadmap.md)을 따르되, 현재 구현 완료 여부는 항상 [Implementation Status](./docs/implementation-status.md)를 원본으로 본다. PostgreSQL snapshot connector production implementation, multi-step Alembic upgrade/rollback operations, Temporal product workflow execution, executable Python transform runner는 MVP core 완료 조건에서 제외되며 현재 status 문서의 future/deferred 경계를 따른다.

---

## 문서 지도

이 문서는 Foundry-lite 문서 체계의 **제품 목표와 설계 원본**이다. 무엇을 만들지, 왜 그 구조가 필요한지, 어떤 범위까지 v1에 포함할지를 설명한다.

- 이 문서는 제품 목표, v1 범위, 아키텍처, 데이터/객체/액션/운영 설계를 정의한다.
- 실행 순서와 스프린트별 완료 조건은 [스프린트 실행 계획](./foundry_lite_sprint_breakdown_ko.md)을 원본으로 본다.
- Foundry 공개 문서에서 가져온 외부 근거는 [Palantir Foundry 심층 분석](./deep-research-report.md)을 원본으로 본다.
- Python 백엔드 구현 원칙과 코드 품질 기준은 [Python 백엔드 엔지니어링 가이드](./foundry_lite_python_engineering_guidelines_ko.md)를 원본으로 본다.
- 네 문서는 모두 같은 v1 폐루프와 Python 백엔드 품질 기준을 기준으로 연결된다: `CSV/local snapshot 또는 PostgreSQL-backed repository proof → DuckDB transform → Ontology/Object → Action → Materialization → Downstream Transform`.

### 함께 읽을 문서

- [스프린트 실행 계획](./foundry_lite_sprint_breakdown_ko.md): 이 설계를 어떤 순서로 구현하고, 각 단계가 무엇을 통과해야 완료인지 확인한다.
- [Data Platform Expansion Roadmap](./docs/data-platform-expansion-roadmap.md): S46 이후 post-MVP 데이터 플랫폼 확장 순서와 공통 Exit Checklist를 확인한다.
- [Palantir Foundry 심층 분석](./deep-research-report.md): Ontology, Dataset, Action, Materialization 같은 설계 결정이 어떤 공개 근거에서 왔는지 확인한다.
- [Python 백엔드 엔지니어링 가이드](./foundry_lite_python_engineering_guidelines_ko.md): 백엔드를 Python으로 구현할 때 지켜야 할 Clean Code, SRP, 테스트, 트랜잭션, 운영 로그 기준을 확인한다.

### Goal Clarity Criteria

- v1에서 반드시 증명할 폐루프가 명시되어 있다.
- v1에서 하지 않을 일이 명시되어 있다.
- 성공 기준이 CLI/API/UI/테스트 중 하나로 검증 가능하다.
- 각 설계 섹션이 실행 스프린트와 연결되어 있다.
- 외부 근거가 심층 분석 보고서와 연결되어 있다.
- 구현 품질 기준이 Python 백엔드 엔지니어링 가이드와 연결되어 있다.
- 안티패턴 방지, 단순 패치 금지, 에러 추적 가능성 기준이 구현 완료 조건에 포함되어 있다.
- 테스트 커버리지 95% 이상, 필수 통합/스모크 테스트 100% 통과 기준이 구현 완료 조건에 포함되어 있다.
- Scale Foundation이 v1 초기에 고정되어 storage/compute/event/search/workflow/auth 인프라를 나중에 교체해도 core 제품 로직을 대수술하지 않는 구조를 요구한다.

---

## 0. 결론 요약

Foundry-lite는 단순 BI/ETL 툴이 아니다. 이 시스템의 정체성은 **운영 객체 시스템(Operational Object System)** 이다. 외부 시스템의 데이터는 raw dataset/stream으로 들어오고, transform을 통해 clean dataset이 되며, clean dataset은 Ontology의 object/link/action 모델로 인덱싱된다. 사용자는 테이블을 직접 수정하는 것이 아니라 **객체 위에서 액션을 실행**한다. 액션은 object state, action log, side effect, writeback, materialized dataset을 만든다. 이 결과가 다시 pipeline input으로 들어가면서 조직의 운영 상태가 지속적으로 학습·갱신된다.

가장 작은 완성형은 다음 v1 폐루프가 end-to-end로 돌아가는 상태다. 이 폐루프는 intentionally small이다. Kafka/CDC/REST/Webhook/Elasticsearch path는 MVP core 완료 조건 밖의 post-MVP 확장으로 분리하며, 현재 checkout에는 Sprint 37~42 proof가 들어와 있다.

```text
Files / local snapshot
→ Connector / Sync
→ Raw Dataset
→ DuckDB SQL Transform
→ Clean Dataset
→ Ontology Mapping
→ Funnel-lite Snapshot Indexer
→ SQLAlchemy Object Store / Object Query Service
→ Object Explorer / Action Form
→ Action Runtime
→ Object Edit / Action Log / Outbox
→ Materialized Dataset: object_snapshot + action_log
→ Downstream Transform
```

### 0.1 리뷰 반영 핵심 결정

이번 수정본은 Foundry-lite의 장기 비전과 v1 구현 범위를 분리한다. Foundry-lite의 장기 방향은 Ontology 중심 폐루프 플랫폼이지만, v1은 다음 수직 slice를 안정적으로 완성하는 데 집중한다.

```text
CSV/local snapshot 또는 PostgreSQL-backed repository proof
→ immutable raw dataset version
→ DuckDB SQL transform
→ immutable clean dataset version
→ Ontology object/link/action mapping
→ SQLAlchemy object store
→ object query / object explorer
→ action runtime with optimistic concurrency
→ action_log + object_snapshot materialization
→ downstream transform
```

P0 결정:

- v1 필수 범위에서 Kafka/CDC/Webhook/REST sync/Elasticsearch/Spark/Iceberg day-1 강제/React hooks를 제외한다. 단, REST/Webhook/Kafka/CDC/Elasticsearch proof가 존재하면 post-MVP 증거로 따로 기록한다.
- `COMMITTED` dataset version은 immutable이며, 실패한 run은 staging/manifest를 commit하지 않는다.
- v1 branch는 `main`과 `dev` namespace 및 `dev → main promotion`만 지원한다.
- Ontology activation 전에 dataset/schema/property/link/action/writeback/security reference를 검증한다.
- Action API는 `expectedObjectVersion`을 받아 optimistic concurrency를 강제한다.
- Reindex/replay를 위해 `index_runs`, cursor, count/hash validation, shadow swap 전략을 명시한다.
- Materialization은 v1에서 `action_log`와 `object_snapshot` 두 종류만 필수로 구현한다.
- 보안은 v1에서 tenant isolation, RBAC, object read/action execute, property masking, audit-all-writes로 제한한다.
- Scale Foundation은 Sprint 02A에서 먼저 고정한다. Spark/Flink/Kafka/Iceberg/Elasticsearch를 production infrastructure로 바로 강제하지 않더라도, `StorageAdapter`, `MetadataRepository`, `ComputeAdapter`, `EventPublisher`, `WorkflowAdapter`, `SearchAdapter`, `ConnectorAdapter`, `AuthProvider`의 port/contract/test boundary는 MVP 초기에 만든다.

### 0.2 설계-스프린트 연결표

아래 표는 이 기획서의 설계 구간이 [스프린트 실행 계획](./foundry_lite_sprint_breakdown_ko.md)의 어느 단계에서 구현되는지 보여준다.

| 설계 구간 | 실행 스프린트 | 외부 근거 |
|---|---|---|
| [데이터 저장 계층 설계](#5-데이터-저장-계층-설계) | [Sprint 03~08](./foundry_lite_sprint_breakdown_ko.md#sprint-03--dataset-논리-자산-crud) | [Dataset/Data as Code 근거](./deep-research-report.md#기술-스택) |
| [Scale Foundation / Infra Swap Boundary](#35-v1-adapter-boundary) | [Sprint 02A](./foundry_lite_sprint_breakdown_ko.md#sprint-02a--scale-foundationinfra-swap-boundary) | 유지보수성, traceability, scale-out workers, adapter/port 교체 가능성 |
| [Data Connection-lite 설계](#6-data-connection-lite-설계) | [Sprint 09~10](./foundry_lite_sprint_breakdown_ko.md#sprint-09--sourcesyncrun-framework) | [Data Connection 근거](./deep-research-report.md#종단간-데이터-흐름) |
| [Transform Engine 설계](#7-transform-engine-설계) | [Sprint 11~14](./foundry_lite_sprint_breakdown_ko.md#sprint-11--transform-registry와-sqlduckdb-runner) | [Pipeline/compute 근거](./deep-research-report.md#기술-스택) |
| [Ontology Metadata Service-lite](#8-ontology-metadata-service-lite) | [Sprint 15~16](./foundry_lite_sprint_breakdown_ko.md#sprint-15--ontology-draftobjectproperty-yaml-import) | [Ontology 중심성 근거](./deep-research-report.md#공개문서-기반-참조-아키텍처) |
| [Object Store/Funnel/Object Query](#9-object-store-설계) | [Sprint 17~23](./foundry_lite_sprint_breakdown_ko.md#sprint-17--object-store-core와-merge-policy) | [Object backend/indexing 근거](./deep-research-report.md#종단간-데이터-흐름) |
| [Action Runtime 설계](#12-action-runtime-설계) | [Sprint 24~29](./foundry_lite_sprint_breakdown_ko.md#sprint-24--action-dslparameter-validationprecondition-engine) | [Actions/writeback 근거](./deep-research-report.md#종단간-데이터-흐름) |
| [Materialization / Writeback 설계](#13-materialization--writeback-설계) | [Sprint 30~32](./foundry_lite_sprint_breakdown_ko.md#sprint-30--action-log--dataset-materialization) | [Materialization 근거](./deep-research-report.md#종단간-데이터-흐름) |
| [Security / Governance 설계](#14-security--governance-설계) | [Sprint 34](./foundry_lite_sprint_breakdown_ko.md#sprint-34--v1-rbacdatasetobject-permissionproperty-masking) | [보안/거버넌스 근거](./deep-research-report.md#엔지니어링-프랙티스) |
| [운영 원칙과 테스트 전략](#23-운영-원칙) | [Sprint 33, 36](./foundry_lite_sprint_breakdown_ko.md#sprint-33--runsqueuesreplay-operations-uicli) | [관측성/Health Checks 근거](./deep-research-report.md#엔지니어링-프랙티스) |
| [Python 백엔드 구현 원칙](./foundry_lite_python_engineering_guidelines_ko.md) | [Sprint 01~02, 14, 33~36](./foundry_lite_sprint_breakdown_ko.md#sprint-01--모노레포와-로컬-런타임-골격) | Clean Code, SRP, 테스트, 트랜잭션, 관측성 품질 기준 |

---

## 1. Foundry에서 가져와야 할 핵심 철학

### 1.1 데이터가 아니라 “의사결정 세계”를 모델링한다

Foundry의 Ontology는 단순 semantic layer가 아니라 enterprise decision model이다. Foundry-lite도 테이블명을 그대로 노출하지 않는다. 사용자가 다루는 단위는 `orders` 테이블이 아니라 `Order` 객체, `Customer` 객체, `Order -> Customer` 링크, 그리고 `ApproveOrder`, `HoldShipment`, `ReallocateInventory` 같은 Action이다.

**설계 원칙:**

- 데이터 모델의 중심은 Dataset이 아니라 Ontology다.
- Dataset은 Ontology object를 만들기 위한 source of evidence다.
- Action은 UI 버튼이 아니라 typed transaction이다.
- 분석 결과는 dashboard에서 끝나지 않고 operation state를 바꾼다.
- 모든 변경은 action log와 lineage를 남긴다.

### 1.2 “South of Ontology”와 “North of Ontology”를 분리한다

Foundry 관점으로 보면 raw data를 Ontology로 끌어올리는 하단 영역과, Ontology 위에서 사용자가 앱·액션·에이전트로 일하는 상단 영역이 나뉜다.

Foundry-lite도 이 구분을 명확히 둔다.

```text
South of Ontology
- Ingestion
- Dataset Registry
- Raw/Clean pipeline
- Transform engine
- Data quality
- Lineage
- Ontology indexing

North of Ontology
- Object Query
- Object Sets
- Operational UI
- Action Runtime
- Derived Properties
- Functions on Objects, v2
- Writeback
- Materialization
```

### 1.3 Read architecture와 Write architecture를 분리한다

운영 앱은 대량 lake table을 매번 직접 scan하면 안 된다. 읽기는 object index/object store에서 빠르게 처리하고, 쓰기는 action runtime과 object edit/event log를 통해 durable하게 처리해야 한다.

**읽기 경로:**

```text
Clean dataset snapshot / stream archive
→ Object Indexer
→ Object Store + Search Index
→ Object Query API
→ UI / SDK
```

**쓰기 경로:**

```text
UI / SDK
→ Action Runtime
→ Validation / Permission / Preconditions
→ Object Edit Transaction
→ Outbox Event
→ Search/Object index update
→ Materialization / Writeback / Pipeline trigger
```

### 1.4 모든 것은 replay 가능해야 한다

장기적으로 유지보수 가능한 Foundry-lite의 핵심은 replayability다.

어떤 객체 상태가 이상하면 다음 질문에 답할 수 있어야 한다.

- 이 object property는 어떤 dataset version에서 왔는가?
- 어떤 transform run이 이 dataset을 만들었는가?
- 어떤 source sync에서 raw data가 들어왔는가?
- 어떤 user action이 object edit을 만들었는가?
- 외부 시스템 writeback은 성공했는가?
- 같은 시점으로 다시 재계산할 수 있는가?

따라서 모든 state transition은 다음 셋 중 하나로 기록되어야 한다.

1. Dataset transaction
2. Stream event offset/checkpoint
3. Action edit/event log

---

## 2. 제품 범위


### 2.1 v1 필수 기능

v1은 “Foundry 전체 기능 축소판”이 아니라 **재현 가능한 최소 폐루프**다. 아래 범위를 넘는 기능은 설계 인터페이스만 남기고 구현은 v1.5 이후로 미룬다.

1. **Data Connection-lite**
   - CSV 파일 업로드
   - Parquet 파일 read/import는 adapter interface만 열어두고, v1 필수 acceptance에서는 제외
   - PostgreSQL snapshot connector는 boundary와 PostgreSQL-backed repository closed-loop proof를 갖지만, production snapshot connector implementation은 현재 MVP core 완료 조건에서 제외한다.
   - REST/Webhook/Kafka/CDC는 v1 core 필수가 아니다. 다만 현재 checkout에는 Sprint 37~40의 REST/Webhook, stream archive, Debezium CDC, CDC object indexing proof가 존재한다.

2. **Dataset Registry**
   - dataset 생성/조회
   - schema 관리
   - immutable version/transaction 관리
   - `SNAPSHOT`, `APPEND` transaction
   - `main`, `dev` namespace와 `dev → main` promotion
   - storage path/manifest 관리
   - 기본 data quality check: schema, row count, primary key uniqueness, not-null
   - lineage graph: source/sync/transform/materialization edge

3. **Transform Engine**
   - canonical runner: SQL + DuckDB
   - Python transform SDK는 skeleton/fail-closed boundary로 제한하며, executable Python runner는 future scope다.
   - Polars는 Python runner 내부 선택지
   - Spark는 interface만 정의하고 Phase 7에서 구현
   - append-only incremental mode만 허용
   - transform run tracking
   - health check failure 시 output commit 차단

4. **Ontology Metadata Service-lite**
   - object type 정의
   - property 정의
   - simple link type 정의
   - action type 정의
   - dataset backing mapping
   - ontology draft/validate/activate
   - activation validation: backing dataset/schema/property/link/action reference 검증
   - Functions on Objects는 v1 제외, derived property expression만 허용

5. **Object Indexer / Funnel-lite**
   - dataset snapshot을 object store로 index
   - action edit committed event를 받아 search/materialization trigger 처리
   - CDC/stream indexing은 Phase 6
   - Elasticsearch는 adapter interface만 유지하고 v1은 PostgreSQL index 사용
   - object_changed event는 Postgres outbox에 기록
   - reindex/replay CLI와 `index_runs` 상태 기록

6. **Object Store / Object Query Service**
   - PostgreSQL JSONB 기반 object store
   - object 조회
   - filter/search/sort/aggregation의 최소 subset
   - link traversal
   - object sets: static/dynamic
   - tenant/RBAC/property-mask aware query
   - optimistic concurrency: `expectedObjectVersion`

7. **Operational App / OSDK-lite**
   - Object Explorer UI
   - object detail page
   - action form 실행
   - dataset/version/lineage 최소 화면
   - generated TypeScript SDK는 v1 후반
   - React hooks는 v1.5

8. **Action Runtime**
   - action type DSL
   - parameter validation
   - permission check
   - precondition check with safe expression language
   - object edit transaction
   - action log
   - optimistic concurrency check
   - local outbox/idempotency
   - generalized before-commit writeback은 v1.5, v1 demo에서는 mock REST adapter 1개만 허용
   - after-commit side effect는 outbox 기반 webhook/event 1종부터 구현

9. **Materialization / Writeback**
   - v1 materialization은 두 종류만 필수
     - `object_snapshot → dataset`
     - `action_log → dataset`
   - object_delta/link_snapshot/external_export는 v1.5 이후
   - Kafka topic publish는 Phase 6

10. **Governance / Observability**
    - tenant isolation
    - user identity
    - RBAC
    - dataset read/write permission
    - ontology edit permission
    - object read permission
    - action execute permission
    - property mask on read
    - audit all writes
    - run monitoring: sync/transform/index/action/materialization
    - retries/DLQ/replay


### 2.2 v1에서 하지 않을 것

아래는 v1 MVP core 완료 조건에서 의도적으로 제외한다. 일부 항목은 현재
checkout에 post-MVP proof가 이미 들어와 있지만, always-on production worker,
managed infrastructure, 광범위한 SaaS 일반화까지 v1 core 성공 조건으로 요구하지는
않는다.

- Kafka/Redpanda stream ingest의 continuously running production worker와 deployment packaging
- Debezium CDC production deployment와 continuously running CDC object-indexing worker
- REST pull sync의 durable connector registry, retry worker, 광범위한 SaaS connector 일반화
- Webhook listener의 durable inbox, retry worker, 광범위한 SaaS event 일반화
- Palantir 수준의 multi-tenant enterprise security 완전체
- mandatory markings, CBAC, cross-organization collaboration
- 수십억~수백억 object indexing 최적화
- Elasticsearch live cluster deployment와 managed operations
- Spark/Flink runner 구현
- full visual pipeline builder
- full visual ontology manager
- zero-downtime multi-region deployment automation
- 대규모 모델/LLM agent platform
- 복잡한 geospatial/time-series/media ontology
- 완전한 Iceberg catalog UI
- Functions on Objects runtime

단, 설계는 이 기능들이 나중에 추가될 수 있게 adapter/interface와 metadata boundary를 남긴다.

### 2.3 Post-MVP Proof / Future Boundary

| 기능 | 현재 checkout 상태 | MVP core 밖에 남는 것 |
|---|---|---|
| REST pull sync | `RestPullConnectorAdapter`와 cursor/rate-limit/SSRF guard proof 존재 | durable connector registry, retry workers, SaaS별 production adapter |
| Webhook push ingest | timestamp-bound HMAC signed append ingest proof 존재 | durable inbox, retry workers, SaaS event 일반화 |
| Kafka/Redpanda ingest | local/fake stream archive, Kafka-compatible adapter, one-shot worker, live broker proof 존재 | continuously running worker, rebalance/commit-unknown hardening, deployment packaging |
| Debezium CDC | Debezium-shaped archive, live Debezium/PostgreSQL topic, CDC object indexing proof 존재 | production CDC deployment, always-on CDC object-indexing worker |
| Elasticsearch | Elasticsearch-compatible adapter, projection, rebuild, orphan drift proof와 live Testcontainers proof 존재 | managed cloud packaging, deployment operations |
| Spark runner | `SparkComputeAdapter` ratchet와 S3+Iceberg+Spark composition proof 존재 | real cluster deployment, distributed Spark failure modes |
| Iceberg storage/catalog ratchet | Iceberg snapshot/version pinning proof와 MinIO/S3-backed composition proof 존재 | maintenance, retention, managed catalog operations |
| Generated TS SDK | package/browser generated SDK surface 존재 | richer generated hooks/client ergonomics |
| React hooks | future | SDK package boundary |
| Functions on Objects | future | derived property expression only |
| Complex ABAC/CBAC | future | policy DSL extension point |

### 2.4 v1 성공 기준

v1은 다음 acceptance를 모두 만족해야 한다.

```text
CSV/local snapshot 또는 PostgreSQL-backed repository closed-loop proof
→ raw dataset committed
→ DuckDB SQL transform
→ clean dataset committed
→ ontology validate/activate
→ clean rows become Order objects
→ Object Explorer에서 query/detail 확인
→ ApproveOrder action 실행
→ object_version 기반 optimistic concurrency 통과
→ object_edits/action_runs/outbox/audit 기록
→ object_snapshot/action_log materialization 생성
→ downstream transform이 action_log를 input으로 사용
```

## 3. 추천 기술 스택

### 3.1 기본 방향

운영 난이도를 줄이기 위해 “처음부터 수십 개 마이크로서비스”로 가지 않는다. 단일 모노레포 안에 **modular monolith + scale-out workers** 구조를 둔다.

**개발 경험:**

- Python 백엔드 중심
- TypeScript는 Web UI와 generated SDK에 사용
- SQL/DuckDB transform을 canonical path로 두고, Python transform은 SDK skeleton/fail-closed boundary로 제한
- Docker Compose로 로컬 완전 실행
- Kubernetes로 scale-out 가능
- storage/queue/compute는 interface로 추상화
- infra swap 가능성은 나중으로 미루지 않고 Foundation 단계에서 port, adapter, contract test, composition root로 고정
- Clean Code, SRP, 테스트, 트랜잭션 기준은 [Python 백엔드 엔지니어링 가이드](./foundry_lite_python_engineering_guidelines_ko.md)를 따른다.


### 3.2 v1 기본 스택

v1은 선택지를 줄여 구현 속도와 디버깅 가능성을 우선한다.

| 영역 | v1 확정 선택 | 이유 |
|---|---|---|
| Monorepo | pnpm workspace + Turborepo + uv workspace | Web/SDK와 Python 백엔드를 한 저장소에서 관리 |
| API | **Python 3.12 + FastAPI + Pydantic v2** | Python 백엔드 기준을 고정하고 request/response validation을 명확히 함 |
| Backend persistence | SQLAlchemy 2.x + schema revision guard + Alembic baseline migration | 현재는 SQLAlchemy metadata bootstrap, frozen schema revision, and Alembic fresh-DB metadata parity test로 drift를 막고, multi-step upgrade/rollback 운영은 future scope |
| Worker | local/direct workflow adapter + stream archive worker entrypoint + Temporal adapter ratchet | Temporal product workflow execution은 future scope이고, 현재는 port/adapter contract, one-shot stream worker proof, Temporal adapter proof를 사용 |
| CLI | Typer | 운영자와 개발자가 같은 Python service를 명령어로 실행 |
| Web | Next.js + TanStack Query + shadcn/ui | 운영 UI 빠르게 개발 |
| Metadata DB | SQLite/local SQLAlchemy + PostgreSQL contract coverage | schema, transaction, object store, audit 의미를 repository port 뒤에 고정 |
| Object Storage | local filesystem + fake storage URI + S3/MinIO adapter + Iceberg storage ratchet | lake 파일 저장 contract를 유지하고 managed catalog/maintenance 운영은 future scope |
| Lake format | Parquet manifest first + Iceberg snapshot/version pinning proof | v1 commit protocol은 유지하고 managed Iceberg operations는 이후 확장 |
| Event | SQLAlchemy outbox first | Kafka 없이도 MVP core 폐루프와 replay를 구현 |
| Stream | local/fake stream + Kafka-compatible adapter/one-shot worker proof | v1 core 필수는 아니지만 Sprint 38 proof는 존재, continuously running production worker는 future scope |
| CDC | Debezium-shaped archive + live Debezium/PostgreSQL topic proof | v1 core 필수는 아니지만 Sprint 39~40 proof는 존재, production CDC worker packaging은 future scope |
| Batch compute | DuckDB canonical runner | local/small production에서 transaction/lineage 단순화 |
| Python compute | transforms SDK skeleton + fail-closed registration | executable Python runner와 sandboxed SDK IO abstraction은 future scope |
| Spark | Spark ComputeAdapter ratchet | v1 core 필수는 아니지만 post-MVP proof는 존재, real cluster 운영은 future scope |
| Workflow | local/fake WorkflowAdapter contract + Temporal adapter ratchet | Temporal이 product workflow/action/writeback/retry/replay를 실제 구동하는 것은 future scope |
| Search index | local/fake + Elasticsearch-compatible adapter/projection proof | object store가 source of truth이고 managed Elasticsearch deployment는 future scope |
| Auth | `AuthProvider` local/demo/header-trust profile + production unsafe-profile guard | 로컬 개발은 단순하게 유지하되, production에서 demo/header-trust 인증이 켜지는 실수를 startup에서 차단 |
| Observability | Structured logs + OpenTelemetry interface | Prometheus/Grafana/Loki는 docker-compose full profile |
| Schema validation | Pydantic + JSON Schema + Zod for Web boundary | Python API 계약을 원본으로 두고 Web/SDK와 연결 |
| Python quality gate | ruff + mypy 또는 pyright + pytest | Clean Code, 타입 안정성, 회귀 방지를 CI에서 확인 |
| Test coverage gate | line/branch/function coverage 95%+, integration/smoke 100% pass | 코드가 작동하는지뿐 아니라 안전하게 바꿀 수 있는지 확인 |

### 3.3 왜 Temporal product workflow orchestration을 S52 target으로 두는가

Foundry-lite에는 ETL schedule뿐 아니라 action writeback, side effect, index replay, materialization retry가 필요하다. 단순 queue는 실패 복구와 장기 workflow 추적이 약하다. Temporal은 워크플로우를 코드로 정의하면서 event history 기반으로 재시작/복구할 수 있으므로 action runtime과 pipeline runner 양쪽에 적합하다.

다만 데이터 asset catalog UI가 필요해지면 나중에 Dagster를 transform authoring layer로 붙일 수 있다. 현재 MVP core는 자체 Dataset Registry + local/direct workflow boundary로 닫고, Temporal은 adapter ratchet 증거까지만 current로 본다. 실제 product workflow/action/writeback/retry/replay를 Temporal worker가 구동하는 범위는 S52 future scope다.

### 3.4 Scale path

초기에는 다음처럼 실행한다.

```text
Current local checkout
- SQLite/local SQLAlchemy metadata and object store
- local filesystem object storage
- local/direct workflow adapter
- API, Web, CLI, and one-shot stream archive worker entrypoint
- PostgreSQL/Kafka/Debezium proofs through focused Testcontainers paths

Target local / small production profile
- PostgreSQL
- MinIO or S3-compatible object storage
- Temporal adapter/profile proof, with product workflow execution deferred to S52
- Redpanda/Kafka stream profile
- Elasticsearch live cluster profile
- API/Web/Worker containers
```

데이터가 커지면 다음으로 확장한다.

```text
Scale Production
- S3 / GCS / Azure Blob
- Iceberg REST Catalog / Nessie / Polaris
- Spark cluster for batch
- Flink for streaming
- Kafka/Redpanda multi-broker
- Elasticsearch cluster
- Kubernetes HPA for workers
- PostgreSQL primary/replica, partitioned audit/action/object tables
```

중요한 것은 v1부터 `StorageAdapter`, `ComputeAdapter`, `StreamAdapter`, `CatalogAdapter` interface를 두어 전환 시 core logic을 바꾸지 않는 것이다.

---

### 3.5 v1 adapter boundary

v1에서 반드시 지켜야 할 boundary는 다음이다.

```text
DatasetStorageAdapter
- 현재 local filesystem/fake-storage parquet manifest, S3/MinIO adapter, Iceberg storage ratchet 구현
- managed Iceberg catalog operations와 maintenance는 future scope

ComputeAdapter
- 현재 DuckDB SQL runner와 Spark ComputeAdapter ratchet 구현
- real Spark cluster operations와 분산 장애 proof는 future scope

SearchAdapter
- 현재 local/fake + Elasticsearch-compatible adapter/projection proof 구현
- managed Elasticsearch deployment는 future scope

EventPublisher
- 현재 SQLAlchemy outbox/DLQ/replay cursor 구현
- Kafka-compatible stream archive proof는 존재하지만 full publisher/always-on worker는 future scope

AuthProvider
- 현재 local/demo/header-trust profile과 production unsafe-profile guard 구현
- OIDC/JWT provider는 future scope
```

domain/application layer는 adapter interface만 알고, concrete implementation은 `apps/api` 또는 `apps/worker` composition root에서 주입한다.

#### Scale Foundation 의도

Scale Foundation은 “대규모 인프라를 지금 모두 붙인다”는 뜻이 아니다. 의미는 더 작고 더 중요하다. v1 초기에 core 제품 로직과 concrete infrastructure를 분리해서, 데이터가 커졌을 때 아래 교체가 제품 로직 대수술이 아니라 adapter 교체와 contract test 확장으로 끝나게 만드는 것이다.

비개발자 관점으로 말하면, Foundry-lite의 업무 규칙은 “주방 레시피”이고 인프라는 “주방 장비”다. 장비가 가정용 오븐에서 공장용 오븐으로 바뀌어도 레시피와 품질 검사표가 유지되어야 한다.

#### Infra Swap Readiness Matrix

| Boundary | Local/MVP implementation | Scale implementation | 반드시 유지할 product contract | 필수 trace key |
|---|---|---|---|---|
| MetadataRepository | SQLite 또는 local SQLAlchemy | PostgreSQL primary/replica, partitioned tables | tenant, dataset, ontology, action, audit metadata 의미 불변 | `tenant_id`, `request_id`, `resource_id` |
| DatasetStorageAdapter | local filesystem / fake storage manifest | MinIO/S3/GCS/Azure Blob + Iceberg catalog | staging → manifest → committed version protocol 불변 | `dataset_id`, `transaction_id`, `version_id` |
| DatasetTransactionRepository | SQLAlchemy transaction + schema revision guard + Alembic baseline parity | PostgreSQL transaction + multi-step migration/rollback operations | OPEN → COMMITTED/ABORTED 상태 전이 불변 | `transaction_id`, `run_id` |
| DatasetVersionRepository | SQLAlchemy version/schema reads | PostgreSQL indexed version/schema reads | 최신 버전, 특정 버전, schema version 조회 의미 불변 | `dataset_id`, `version_id`, `schema_version` |
| RuntimeRepository | SQLAlchemy audit/outbox/lineage/run table | PostgreSQL partitioned audit/outbox, future publisher state | audit, outbox, lineage, run state의 key 의미 불변 | `tenant_id`, `request_id`, `run_id`, `correlation_id` |
| ComputeAdapter | DuckDB SQL runner | Spark batch, later Flink bounded job | input version binding, output staging, health gate, lineage 불변 | `transform_run_id`, `input_version_id`, `output_version_id` |
| StreamAdapter/EventPublisher | SQLAlchemy outbox + local/fake stream + Kafka-compatible one-shot worker proof | Kafka/Redpanda publisher and continuously running consumer | event idempotency, DLQ, replay cursor 의미 불변 | `event_id`, `correlation_id`, `cursor` |
| SearchAdapter | local/fake + Elasticsearch-compatible projection proof | managed Elasticsearch projection | object store가 source of truth이고 search는 재생성 가능한 projection | `object_type`, `object_id`, `index_version` |
| WorkflowAdapter | direct call or local worker skeleton | Temporal workflow/activity | retry, timeout, durable run state, replay 가능성 불변 | `workflow_id`, `run_id`, `attempt` |
| ConnectorAdapter | CSV/local file, REST pull, signed webhook, Debezium wrapper proof | durable connector registry, retry workers, SaaS connector | sync run lifecycle, cursor, transaction commit protocol 불변 | `source_id`, `sync_run_id`, `cursor` |
| AuthProvider/PolicyAdapter | local/demo/header-trust profile + RBAC + production unsafe-profile guard | OIDC/SSO, ABAC/CBAC extension | tenant isolation, permission decision, audit deny 의미 불변 | `actor_user_id`, `tenant_id`, `policy_decision_id` |

#### Scale Foundation Checklist

- [x] 각 boundary는 `Protocol` 또는 명시적 interface로 정의되고, application service는 concrete SDK가 아니라 이 boundary를 호출한다. ([S02A-A2](./docs/sprint-evidence-ledger.md#s02a-a2))
  - [x] `DatasetStorageAdapter`는 `Protocol`로 정의했고 dataset staging/manifest/version file 경로는 adapter를 통한다. ([S02A-P1](./docs/sprint-evidence-ledger.md#s02a-p1))
  - [x] `MetadataRepository`는 schema bootstrap/reset/default tenant-user DB write 경계를 맡는다. ([S02A-P1](./docs/sprint-evidence-ledger.md#s02a-p1))
  - [x] `DatasetRepository`는 dataset registry create/find DB read/write 경계를 맡고 local/fake contract test를 통과한다. ([S02A-P1](./docs/sprint-evidence-ledger.md#s02a-p1))
  - [x] `DatasetTransactionRepository`는 dataset transaction/version/file DB state change와 run failure update 경계를 맡고 local/fake contract test를 통과한다. ([S02A-P1](./docs/sprint-evidence-ledger.md#s02a-p1))
  - [x] `DatasetVersionRepository`는 committed version/schema DB read 경계를 맡고 local/fake contract test를 통과한다. ([S02A-P1](./docs/sprint-evidence-ledger.md#s02a-p1))
  - [x] `RuntimeRepository`는 audit/outbox/lineage/list-runs DB 경계를 맡고 local/fake contract test를 통과한다. ([S02A-P1](./docs/sprint-evidence-ledger.md#s02a-p1))
  - [x] `ComputeAdapter`는 CSV/Parquet/SQL transform/health-check 실행 경계를 맡고 DuckDB local/fake contract test를 통과한다. ([S02A-P1](./docs/sprint-evidence-ledger.md#s02a-p1))
  - [x] `StreamAdapter`, `SearchAdapter`, `WorkflowAdapter`, `ConnectorAdapter`, `AuthProvider`는 port와 local/fake adapter contract를 갖는다. Kafka-compatible stream worker proof와 Elasticsearch-compatible projection proof는 존재하고, production Kafka publisher/managed Elasticsearch/Temporal/connector/OIDC 구현은 이후 스프린트 범위다. ([S02A-P2](./docs/sprint-evidence-ledger.md#s02a-p2))
- [x] concrete 구현 선택은 `apps/api`, `apps/worker`, `apps/cli` 같은 composition root에서만 한다. ([S02A-P4](./docs/sprint-evidence-ledger.md#s02a-p4))
  - [x] API와 CLI는 `create_local_core_dependencies(...)` composition root에서 adapter profile을 선택한다. ([S02A-P4](./docs/sprint-evidence-ledger.md#s02a-p4))
  - [x] DB schema bootstrap/reset 구현 선택은 `SqlAlchemyMetadataRepository`를 통해 local runtime에 주입된다. ([S02A-P1](./docs/sprint-evidence-ledger.md#s02a-p1))
  - [x] `FoundryLiteCore`의 flat method registry, `__getattr__`, `__setattr__` fallback bridge는 제거했다. Public API forwarder는 하위 호환을 위한 의도적 facade다. ([S02A-P4](./docs/sprint-evidence-ledger.md#s02a-p4))
- [x] local adapter와 fake adapter가 같은 contract test suite를 통과한다. ([S02A-A3](./docs/sprint-evidence-ledger.md#s02a-a3))
  - [x] `DatasetStorageAdapter`의 local/fake 구현은 `tests/contracts/test_dataset_storage_adapter_contract.py`의 같은 시나리오를 통과한다. ([S02A-A3](./docs/sprint-evidence-ledger.md#s02a-a3))
  - [x] `DatasetRepository`의 local/fake 구현은 `tests/contracts/test_dataset_repository_contract.py`의 같은 시나리오를 통과한다. ([S02A-A3](./docs/sprint-evidence-ledger.md#s02a-a3))
  - [x] `DatasetTransactionRepository`의 local/fake 구현은 `tests/contracts/test_dataset_transaction_repository_contract.py`의 같은 시나리오를 통과한다. ([S02A-A3](./docs/sprint-evidence-ledger.md#s02a-a3))
  - [x] `DatasetVersionRepository`의 local/fake 구현은 `tests/contracts/test_dataset_version_repository_contract.py`의 같은 시나리오를 통과한다. ([S02A-A3](./docs/sprint-evidence-ledger.md#s02a-a3))
  - [x] `RuntimeRepository`의 local/fake 구현은 `tests/contracts/test_runtime_repository_contract.py`의 같은 시나리오를 통과한다. ([S02A-A3](./docs/sprint-evidence-ledger.md#s02a-a3))
  - [x] `ComputeAdapter`의 DuckDB/fake 구현은 `tests/contracts/test_compute_adapter_contract.py`의 같은 시나리오를 통과한다. ([S02A-A3](./docs/sprint-evidence-ledger.md#s02a-a3))
  - [x] `ObjectReadRepository`의 local/fake 구현은 `tests/contracts/test_object_read_repository_contract.py`의 같은 시나리오를 통과한다. ([S02A-A3](./docs/sprint-evidence-ledger.md#s02a-a3))
  - [x] `ObjectIndexRepository`의 local/fake 구현은 `tests/contracts/test_object_index_repository_contract.py`의 같은 시나리오를 통과한다. ([S02A-A3](./docs/sprint-evidence-ledger.md#s02a-a3))
  - [x] `ObjectSetRepository`의 local/fake 구현은 `tests/contracts/test_object_set_repository_contract.py`의 같은 시나리오를 통과한다. ([S02A-A3](./docs/sprint-evidence-ledger.md#s02a-a3))
  - [x] `ActionRepository`의 local/fake 구현은 `tests/contracts/test_action_repository_contract.py`의 같은 시나리오를 통과한다. ([S02A-A3](./docs/sprint-evidence-ledger.md#s02a-a3))
  - [x] `OntologyRepository`의 local/fake 구현은 `tests/contracts/test_ontology_repository_contract.py`의 같은 시나리오를 통과한다. ([S02A-A3](./docs/sprint-evidence-ledger.md#s02a-a3))
  - [x] `AuthProvider`, `ConnectorAdapter`, `SearchAdapter`, `StreamAdapter`, `WorkflowAdapter`도 각각 shared contract suite를 통과한다. ([S02A-P2](./docs/sprint-evidence-ledger.md#s02a-p2))
- [x] adapter error는 trace key와 FAILED mutation state뿐 아니라 retryability/timeout/idempotency/operator message taxonomy까지 모든 현재 adapter profile에 표준화되어 있다. ([S02A-O1](./docs/sprint-evidence-ledger.md#s02a-o1))
- [x] adapter를 교체해도 audit/outbox/lineage/run state의 key 이름과 의미가 바뀌지 않는다. ([S02A-A4](./docs/sprint-evidence-ledger.md#s02a-a4))
- [x] CI는 domain/application이 금지된 concrete infra SDK를 직접 import하면 실패한다. ([S02A-A6](./docs/sprint-evidence-ledger.md#s02a-a6))
  - [x] 현재 CI는 domain concrete infra import 0개, application concrete infra import 0개, scale SDK 직접 import, service dependency/collaborator 우회, call graph cycle/depth/fan-out 회귀를 잡는다. ([S02A-P3](./docs/sprint-evidence-ledger.md#s02a-p3))
  - [x] application concrete infra import baseline을 37에서 32로 낮췄다. ([S02A-P3](./docs/sprint-evidence-ledger.md#s02a-p3))
  - [x] application concrete infra import baseline을 32에서 30으로 낮췄다. ([S02A-P3](./docs/sprint-evidence-ledger.md#s02a-p3))
  - [x] application concrete infra import baseline을 30에서 28로 낮췄다. ([S02A-P3](./docs/sprint-evidence-ledger.md#s02a-p3))
  - [x] application concrete infra import baseline을 28에서 25로 낮췄다. ([S02A-P3](./docs/sprint-evidence-ledger.md#s02a-p3))
  - [x] application concrete infra import baseline을 25에서 20으로 낮췄다. ([S02A-P3](./docs/sprint-evidence-ledger.md#s02a-p3))
  - [x] application concrete infra import baseline을 20에서 15로 낮췄다. ([S02A-P3](./docs/sprint-evidence-ledger.md#s02a-p3))
  - [x] application concrete infra import baseline을 15에서 13으로 낮췄다. ([S02A-P3](./docs/sprint-evidence-ledger.md#s02a-p3))
  - [x] application concrete infra import baseline을 13에서 11로 낮췄다. ([S02A-P3](./docs/sprint-evidence-ledger.md#s02a-p3))
  - [x] application concrete infra import baseline을 11에서 9로 낮췄다. ([S02A-P3](./docs/sprint-evidence-ledger.md#s02a-p3))
  - [x] application concrete infra import baseline을 9에서 7로 낮췄다. ([S02A-P3](./docs/sprint-evidence-ledger.md#s02a-p3))
  - [x] application concrete infra import baseline을 7에서 0으로 낮췄다. ([S02A-P3](./docs/sprint-evidence-ledger.md#s02a-p3))
- [x] 최소 하나의 swap rehearsal test가 있다. 예: local filesystem adapter 대신 fake/S3-compatible adapter를 끼워도 dataset commit use case가 같은 결과를 만든다. ([S02A-A4](./docs/sprint-evidence-ledger.md#s02a-a4))
  - [x] `tests/integration/test_scale_foundation.py`가 fake-storage profile로 CSV commit, inspect, preview public API가 같은 shape로 동작함을 검증한다. ([S02A-A4](./docs/sprint-evidence-ledger.md#s02a-a4))
- [x] scale adapter를 아직 구현하지 않았더라도, 미래 구현이 따라야 할 DTO, state transition, trace key, retryability/timeout/idempotency/operator message failure taxonomy는 문서와 테스트로 고정했다. ([S02A-O2](./docs/sprint-evidence-ledger.md#s02a-o2))

#### 이러면 Scale Foundation으로 치지 않는다

- interface 이름만 만들고 실제 application service가 여전히 SQLite/file/DuckDB/Kafka/Spark SDK를 직접 호출한다.
- adapter 교체 테스트 없이 “나중에 교체 가능”이라고 문서에만 적는다.
- Spark, Kafka, S3 같은 대형 도구를 바로 붙였지만 dataset transaction, lineage, audit, replay contract가 깨진다.
- adapter가 실패를 성공처럼 반환하거나 retry/DLQ 판단에 필요한 정보를 버린다.
- vendor-specific 필드가 core DTO 안쪽으로 새어 들어와 다른 인프라 구현을 막는다.


## 4. 시스템 아키텍처


### 4.1 전체 컴포넌트

```text
┌─────────────────────────────────────────────────────────────┐
│                      Web / OSDK-lite                         │
│  Object Explorer, Action Forms, Dataset UI, Ontology UI       │
└──────────────────────────────┬──────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────┐
│                         API Gateway                          │
│ Auth, REST/OpenAPI, RBAC, Request Audit, Tenant Context       │
└──────────────┬───────────────┬───────────────┬───────────────┘
               │               │               │
┌──────────────▼──────┐ ┌──────▼────────────┐ ┌▼────────────────┐
│ Dataset Registry    │ │ Ontology Metadata │ │ Object Query     │
│ Sources, Syncs,     │ │ Object/Link/      │ │ Object Sets,     │
│ Versions, Lineage   │ │ Action types      │ │ Links, Search    │
└──────────────┬──────┘ └──────┬────────────┘ └┬────────────────┘
               │               │               │
               │       ┌───────▼───────────────▼──────┐
               │       │        Object Store            │
               │       │ objects, links, edits, index   │
               │       └───────────────┬───────────────┘
               │                       │
┌──────────────▼───────────────────────▼──────────────────────┐
│                       Workflow Boundary                       │
│ local/direct today, Temporal workflow execution future         │
└───────┬──────────────┬──────────────┬──────────────┬────────┘
        │              │              │              │
┌───────▼─────┐ ┌──────▼──────┐ ┌─────▼────────┐ ┌──▼──────────┐
│ Connectors  │ │ Transform   │ │ Funnel-lite  │ │ Action/     │
│ Workers     │ │ Workers     │ │ Indexer      │ │ Materialize │
└───────┬─────┘ └──────┬──────┘ └─────┬────────┘ └──┬──────────┘
        │              │              │             │
┌───────▼──────────────▼──────────────▼─────────────▼──────────┐
│                          Data Plane                            │
│ local/S3-ready manifests, SQLAlchemy object store              │
└───────────────────────────────────────────────────────────────┘
┌───────────────────────────────────────────────────────────────┐
│                          Event Plane                           │
│ SQLAlchemy outbox, DLQ, replay cursors, Kafka-compatible proof  │
└───────────────────────────────────────────────────────────────┘
```


### 4.2 Control plane, data plane, event plane 분리

**Control plane**은 metadata와 orchestration을 담당한다.

- source definitions
- dataset definitions
- transform definitions
- ontology definitions
- action definitions
- permissions
- run state
- lineage

**Data plane**은 실제 데이터를 저장·처리한다.

- object storage files
- Parquet manifest / Iceberg table ratchet / future managed catalog operations
- object store tables
- Elasticsearch-compatible search projection proof / future managed cluster
- external systems

**Event plane**은 durable event와 replay 경계를 담당한다. 현재 MVP core에서는 Kafka 없이 SQLAlchemy outbox를 canonical event log로 사용한다. Kafka-compatible stream archive proof는 존재하지만, full publisher와 continuously running worker는 future scope다.

- outbox_events
- dead_letter_events
- index cursors
- materialization cursors
- Kafka/Redpanda topics for post-MVP stream/CDC paths
- `dataset.version.committed`, `object.changed`, `action.run.committed`, `materialization.requested` events

이 분리가 있어야 scale-out이 가능하다. API는 metadata와 user-facing transaction을 처리하고, 대량 파일 처리와 index rebuild는 worker가 한다. Event plane은 data plane과 control plane 사이의 신뢰 가능한 연결 조직이다.

### 4.3 Modular monolith 의존성 규칙

modular monolith가 실패하지 않으려면 module boundary를 코드로 강제해야 한다.

```text
apps/api, apps/worker, apps/cli
→ may depend on libs/foundry_lite/application
→ may depend on libs/foundry_lite/interfaces

apps/web
→ may depend on generated TypeScript SDK

libs/foundry_lite/domain/*
→ must not depend on FastAPI, Next.js, SQLAlchemy, PostgreSQL client, Temporal client directly

libs/foundry_lite/infrastructure/*
→ implement application ports and domain interfaces
→ may depend on concrete infra libraries
```

금지 규칙:

```text
domain/ontology must not import apps/api
domain/object_store must not import SQLAlchemy repository directly
domain/action_runtime must not call HTTP/writeback directly
security/policy must not know UI/session implementation
```

이 규칙을 지키면 나중에 storage/search/compute/auth adapter를 바꿔도 core model은 유지된다.

## 5. 데이터 저장 계층 설계

### 5.1 Dataset은 “파일 묶음 + transaction + schema + lineage”다

Dataset은 단순 테이블이 아니다.

```text
Dataset = logical asset
Dataset Version = committed view
Dataset Transaction = atomic mutation to files
Dataset Files = physical files in object storage
Dataset Schema = typed contract
Dataset Lineage = input/output dependency graph
```


### 5.1.1 Dataset immutability contract

Dataset version은 commit 이후 절대 변경하지 않는다.

```text
- COMMITTED dataset_version은 immutable하다.
- committed version에 속한 dataset_files는 append/delete/update 불가하다.
- 잘못된 version은 삭제하지 않고 deprecated/superseded metadata만 붙인다.
- rollback은 과거 version을 복사하는 것이 아니라, 이전 version을 새 HEAD로 지정하는 transaction으로 기록한다.
- lineage는 항상 version 단위로 연결한다.
```

이 contract가 깨지면 transform 재현성, object reindex, materialization replay가 모두 깨진다.

### 5.2 Dataset transaction types

Foundry와 유사하게 다음 transaction type을 둔다.

| Type | 의미 | 사용처 |
|---|---|---|
| SNAPSHOT | 현재 view 전체 교체 | batch pipeline |
| APPEND | 새 파일만 추가 | incremental pipeline, event archive |
| UPDATE | 기존 row/file 변경 | Iceberg row-level update 지원 시 |
| DELETE | row/file 삭제 | CDC delete, retention |

v1에서는 `SNAPSHOT`, `APPEND`만 필수 구현한다. `UPDATE`, `DELETE`는 Iceberg 도입 또는 object store 쪽에서 먼저 구현한다.

### 5.3 Dataset storage path 규칙

```text
s3://foundry-lite/{tenant_id}/datasets/{dataset_rid}/
  _schemas/{schema_version}.json
  _transactions/{transaction_id}.json
  branch=main/
    version={version_id}/
      part-00000.parquet
      part-00001.parquet
  branch=dev/{branch_id}/
    version={version_id}/...
```

Iceberg 사용 시:

```text
s3://foundry-lite/warehouse/{namespace}/{table}/
  data/...
  metadata/...
```

Dataset Registry는 Iceberg metadata location 또는 자체 transaction manifest 둘 다 참조할 수 있어야 한다.


### 5.3.1 v1 branch model

v1은 full Git-like branching을 구현하지 않는다. 두 namespace만 지원한다.

```text
main
- production-visible committed versions
- ontology backing과 transform production run이 기본 참조

dev
- user/team-local experimental versions
- merge/rebase 없음
- promotion만 허용: dev version → new main version
```

즉, v1의 branch operation은 `promote` 하나다. conflict merge는 구현하지 않는다.

### 5.4 Dataset metadata schema

핵심 테이블:

```sql
create table datasets (
  id uuid primary key,
  tenant_id uuid not null,
  name text not null,
  namespace text not null,
  description text,
  storage_kind text not null, -- parquet_manifest | iceberg | virtual_table
  storage_uri text,
  owner_team text,
  classification text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (tenant_id, namespace, name)
);

create table dataset_schemas (
  id uuid primary key,
  dataset_id uuid not null references datasets(id),
  version int not null,
  schema_json jsonb not null,
  schema_hash text not null,
  created_at timestamptz not null default now(),
  unique(dataset_id, version)
);

create table dataset_transactions (
  id uuid primary key,
  dataset_id uuid not null references datasets(id),
  branch text not null default 'main',
  tx_type text not null, -- SNAPSHOT | APPEND | UPDATE | DELETE
  status text not null, -- OPEN | COMMITTED | ABORTED
  base_version_id uuid,
  committed_version_id uuid,
  schema_version int,
  created_by uuid,
  created_at timestamptz not null default now(),
  committed_at timestamptz,
  metadata jsonb not null default '{}'
);

create table dataset_versions (
  id uuid primary key,
  dataset_id uuid not null references datasets(id),
  branch text not null default 'main',
  version_number bigint not null,
  transaction_id uuid not null references dataset_transactions(id),
  schema_version int not null,
  manifest_uri text,
  row_count bigint,
  byte_size bigint,
  status text not null default 'active', -- active | deprecated | superseded
  superseded_by_version_id uuid,
  created_at timestamptz not null default now(),
  unique(dataset_id, branch, version_number)
);

create table dataset_files (
  id uuid primary key,
  dataset_version_id uuid not null references dataset_versions(id),
  uri text not null,
  format text not null, -- parquet | csv | json | avro
  row_count bigint,
  byte_size bigint,
  content_hash text,
  partition_values jsonb not null default '{}'
);
```


### 5.5 Schema contract

Schema는 JSON Schema/Arrow Schema 형태를 모두 지원할 수 있게 한다.

```json
{
  "columns": [
    {"name": "order_id", "type": "string", "nullable": false},
    {"name": "customer_id", "type": "string", "nullable": false},
    {"name": "order_status", "type": "string", "nullable": false},
    {"name": "order_ts", "type": "timestamp", "nullable": false}
  ],
  "primary_key": ["order_id"],
  "cdc": {
    "enabled": false,
    "ordering_columns": [],
    "delete_column": null
  }
}
```

### 5.5.1 Schema compatibility policy

Ontology backing과 transform output이 dataset schema에 의존하므로 schema evolution은 명시적으로 판정한다.

| 변경 | 기본 판정 | 처리 |
|---|---|---|
| nullable column 추가 | compatible | warning 없이 허용 |
| non-null column 추가 | incompatible unless default exists | default 또는 backfill 요구 |
| column 삭제 | breaking | dependent transform/ontology 차단 |
| int → long/float 등 widening | compatible with warning | dependent schema hash 갱신 |
| type narrowing | breaking | 새 column 또는 transform 수정 요구 |
| primary key 변경 | breaking | 새 dataset/object type 권장 |
| nullable → non-null | breaking unless validation passes | full validation 필요 |
| non-null → nullable | compatible with warning | downstream check 권장 |

Schema compatibility check는 transaction commit 전 `VALIDATING` 단계에서 실행한다.

### 5.6 Dataset health checks

기본 health check:

- schema compatibility
- non-null check
- primary key uniqueness
- row count min/max/freshness
- referential integrity against another dataset
- custom SQL check
- custom Python check

테이블:

```sql
create table dataset_checks (
  id uuid primary key,
  dataset_id uuid not null references datasets(id),
  name text not null,
  check_type text not null,
  config jsonb not null,
  severity text not null default 'error',
  enabled boolean not null default true
);

create table dataset_check_results (
  id uuid primary key,
  check_id uuid not null references dataset_checks(id),
  run_id uuid not null,
  status text not null, -- passed | failed | warning
  details jsonb not null,
  created_at timestamptz not null default now()
);
```

---


### 5.7 Dataset commit protocol

v1은 object storage rename의 원자성에 의존하지 않고 manifest commit을 사용한다.

```text
1. Open dataset transaction
2. Write files to _staging/{transaction_id}/
3. Infer/validate schema
4. Run output checks against staging files
5. Write transaction manifest
6. Commit dataset transaction in PostgreSQL
7. Emit dataset.version.committed into outbox
8. Cleanup staging asynchronously
```

실패 처리:

```text
- VALIDATING 이전 실패: transaction ABORTED, staging cleanup
- COMMITTING 중 DB 실패: transaction remains OPEN/FAILED_COMMIT, retry commit or abort manually
- outbox publish 실패: outbox row remains pending, publisher retries
```

## 6. Data Connection-lite 설계

### 6.1 Source, Sync, Connector

```text
Source = 외부 시스템 연결 정의
Connector = source type별 실행 코드
Sync = source에서 dataset/stream으로 데이터를 가져오는 job 정의
```

예시:

```yaml
source:
  id: pg_erp
  type: postgres
  connection:
    host: ${ERP_DB_HOST}
    database: erp
    secretRef: erp_db_password

sync:
  id: sync_orders
  source: pg_erp
  mode: snapshot
  schedule: "0 * * * *"
  query: "select * from public.orders"
  outputDataset: raw.erp_orders
```

### 6.2 Connector interface

Python connector protocol:

```python
from typing import Protocol, TypeVar

ConfigT = TypeVar("ConfigT")


class Connector(Protocol[ConfigT]):
    type: str

    async def test_connection(self, config: ConfigT) -> ConnectionTestResult:
        ...

    async def infer_schema(
        self,
        config: ConfigT,
        locator: SourceLocator,
    ) -> DatasetSchema:
        ...

    async def run_sync(
        self,
        ctx: SyncContext,
        config: ConfigT,
        sync: SyncDefinition,
    ) -> SyncResult:
        ...
```

외부 언어 connector를 허용하려면 gRPC/HTTP worker interface를 별도 adapter로 둔다.


### 6.3 지원 connector v1

현재 MVP core 필수 connector는 CSV/local snapshot path다. PostgreSQL snapshot은 설계 목표와 repository/Testcontainers proof는 있지만 production connector implementation은 future/deferred다.

- File upload connector: CSV
- PostgreSQL snapshot sync: production implementation future/deferred

MVP 이후 현재 proof:

- REST API paginated sync
- Webhook listener connector
- Kafka-compatible stream archive
- Debezium CDC archive/indexing

v1 optional/prototype:

- Parquet import reader
- Local/S3 directory connector

추가 future:

- production connector registry/retry workers
- continuously running stream/CDC workers

### 6.4 Sync run lifecycle

```text
CREATED
→ PLANNING
→ EXTRACTING
→ WRITING_TRANSACTION
→ VALIDATING
→ COMMITTING
→ COMMITTED
or FAILED / ABORTED
```

Sync는 dataset transaction을 연 뒤 파일을 쓴다. health check 통과 후 commit한다. 실패 시 abort한다.


### 6.5 CDC ingest, Phase 6

CDC는 v1 MVP core 필수 기능이 아니다. 다만 현재 checkout에는 Debezium-shaped archive, live Debezium/PostgreSQL topic, CDC object indexing proof가 존재한다. production CDC deployment와 continuously running object-indexing worker는 future scope다.

CDC event envelope 표준:

```json
{
  "source": "pg_erp.orders",
  "op": "c|u|d|r",
  "pk": {"order_id": "O-1001"},
  "before": {},
  "after": {},
  "ordering": {
    "source_ts_ms": 1780990000000,
    "lsn": "123456789"
  },
  "ingested_at": "2026-06-09T12:00:00Z"
}
```

Post-MVP proof/target flow:

```text
Debezium → Kafka topic raw.pg_erp.orders.cdc
→ Stream archive writer → raw_cdc.erp_orders
→ Funnel-lite stream consumer → Order object upsert/delete
```

MVP core 완료 조건은 이 경로를 요구하지 않는다. 현재 증거는 Sprint 39~40 post-MVP proof로 기록한다.


### 6.6 Virtual Table-lite

Virtual table은 source에 있는 외부 table을 복사하지 않고 등록하는 개념이다. 현재 MVP core에서는 metadata schema와 transform input adapter boundary만 둔다. production object backing direct virtual table은 future scope다.

```sql
create table virtual_tables (
  id uuid primary key,
  tenant_id uuid not null,
  source_id uuid not null references sources(id),
  namespace text not null,
  name text not null,
  locator jsonb not null, -- database/schema/table/query
  schema_json jsonb not null,
  capabilities jsonb not null, -- pushdown, incremental, versioning
  created_at timestamptz not null default now()
);
```

v1 constraint:

```text
- virtual table metadata registration 가능
- DuckDB transform input으로 읽는 prototype 가능
- Ontology backing direct virtual table 금지
- Action/writeback 대상 금지
```

## 7. Transform Engine 설계

### 7.1 Transform의 개념

Transform은 input datasets/virtual tables/streams를 받아 output dataset/object/link dataset을 만드는 typed function이다.

```text
Inputs → Transform Logic → Outputs
```


### 7.2 Transform definition

v1의 canonical runner는 SQL + DuckDB다. Python transform은 현재 SDK skeleton과 fail-closed registration boundary까지만 둔다. executable Python runner와 sandboxed DatasetInput/DatasetOutput API는 future scope이며, 그때도 output commit은 반드시 DatasetOutput API를 통해서만 허용한다.

```yaml
id: clean_orders
name: Clean Orders
language: sql # sql | python
entrypoint: transforms/clean_orders.sql
mode: snapshot # snapshot | incremental
inputs:
  orders: raw.erp_orders
  customers: raw.crm_customers
outputs:
  clean_orders: clean.orders
checks:
  - type: unique
    column: order_id
  - type: not_null
    columns: [order_id, customer_id]
resources:
  engine: duckdb # v1 canonical. spark is adapter only
  memory: 2Gi
  timeout: 600s
```

v1 constraints:

```text
- streaming transform 금지
- v1 core 완료 조건으로 Spark runner를 요구하지 않음
- transform output은 Dataset transaction으로만 commit
- runner가 직접 dataset_files를 수정하면 안 됨
```

### 7.3 Python Transform SDK

```python
from foundry_lite.transforms import transform, Input, Output

@transform(
    orders=Input("raw.erp_orders"),
    customers=Input("raw.crm_customers"),
    out=Output("clean.orders"),
)
def compute(ctx, orders, customers, out):
    orders_df = orders.read_polars()
    customers_df = customers.read_polars()

    df = (
        orders_df
        .join(customers_df.select(["customer_id", "customer_segment"]), on="customer_id", how="left")
        .with_columns(...)
    )

    out.write_polars(df, mode="snapshot")
```

### 7.4 SQL Transform

```sql
-- transforms/clean_orders.sql
select
  o.order_id,
  o.customer_id,
  c.segment as customer_segment,
  cast(o.created_at as timestamp) as order_ts,
  o.status as source_status
from {{ input('raw.erp_orders') }} o
left join {{ input('raw.crm_customers') }} c
  on o.customer_id = c.customer_id
```


### 7.5 Transform run lifecycle

```text
QUEUED
→ RESOLVING_INPUTS
→ STARTING_ENGINE
→ RUNNING
→ WRITING_OUTPUTS_TO_STAGING
→ VALIDATING_OUTPUTS
→ COMMITTING_OUTPUTS
→ EMITTING_EVENTS
→ SUCCESS
or FAILED
```

### 7.5.1 Transform output commit protocol

```text
1. Resolve input dataset versions
2. Open output dataset transaction
3. Execute transform into _staging/{run_id}/
4. Run output checks against staging files
5. Commit output transaction manifest
6. Record input_version → output_version lineage edges
7. Emit transform.run.completed and dataset.version.committed
8. Advance incremental cursor only after commit
```

Partial output files are never visible as committed dataset versions.


### 7.6 Incremental mode

Incremental transform은 input dataset의 last processed version 이후 추가분만 처리한다.

```sql
create table transform_cursors (
  transform_id uuid not null,
  output_dataset_id uuid not null,
  input_dataset_id uuid not null,
  last_processed_version_id uuid,
  last_processed_stream_offset jsonb,
  updated_at timestamptz not null default now(),
  primary key(transform_id, output_dataset_id, input_dataset_id)
);
```

v1 incremental constraints:

```text
- input dataset transaction type must be APPEND only
- UPDATE/DELETE semantics 금지
- transform must be deterministic over new input versions
- output mode는 APPEND 또는 SNAPSHOT 가능
- cursor는 output commit 성공 후에만 advance
- failed run은 cursor를 변경하지 않음
```

UPDATE/DELETE CDC 처리는 object indexer와 Iceberg 도입 후 확장한다.

### 7.7 Build graph and lineage

Lineage edge:

```sql
create table lineage_edges (
  id uuid primary key,
  tenant_id uuid not null,
  from_resource_type text not null,
  from_resource_id uuid not null,
  to_resource_type text not null,
  to_resource_id uuid not null,
  relation text not null, -- input_to | backs_object | materializes_to | writes_to
  created_by_run_id uuid,
  created_at timestamptz not null default now()
);
```

Transform run마다 input dataset version과 output dataset version을 기록한다.

---

## 8. Ontology Metadata Service-lite


### 8.1 Ontology의 역할

Ontology는 다음을 정의한다.

- Object Type: 조직 세계의 명사
- Property: object의 속성
- Link Type: object 간 관계
- Action Type: object에 대해 할 수 있는 동사
- Security: object/property/action 접근 정책
- Backing: dataset에서 object를 만드는 mapping

v1에서 **Function runtime은 제외**한다. 대신 단순 derived property expression만 허용한다.

```text
v1 allowed:
- static property mapping
- editable property
- simple derived property expression

v1 not allowed:
- arbitrary TypeScript/Python function on object
- external call inside derived property
- async function result caching
```

### 8.2 Metadata schema

```sql
create table ontology_versions (
  id uuid primary key,
  tenant_id uuid not null,
  version_number bigint not null,
  status text not null, -- draft | active | archived
  created_by uuid,
  created_at timestamptz not null default now(),
  activated_at timestamptz
);

create table object_types (
  id uuid primary key,
  tenant_id uuid not null,
  ontology_version_id uuid not null references ontology_versions(id),
  api_name text not null,
  display_name text not null,
  description text,
  primary_key_property text not null,
  icon text,
  config jsonb not null default '{}',
  unique(tenant_id, ontology_version_id, api_name)
);

create table property_types (
  id uuid primary key,
  object_type_id uuid not null references object_types(id),
  api_name text not null,
  display_name text not null,
  data_type text not null,
  nullable boolean not null default true,
  indexed boolean not null default false,
  searchable boolean not null default false,
  editable boolean not null default false,
  classification text,
  derivation jsonb,
  unique(object_type_id, api_name)
);

create table link_types (
  id uuid primary key,
  tenant_id uuid not null,
  ontology_version_id uuid not null references ontology_versions(id),
  api_name text not null,
  from_object_type_id uuid not null references object_types(id),
  to_object_type_id uuid not null references object_types(id),
  cardinality text not null, -- one_to_one | one_to_many | many_to_many
  backing jsonb,
  unique(tenant_id, ontology_version_id, api_name)
);
```

### 8.3 Object type DSL

```yaml
objectTypes:
  - apiName: Order
    displayName: Order
    primaryKey: order_id
    backing:
      dataset: clean.orders
      mode: snapshot
      primaryKeyColumns: [order_id]
    properties:
      - apiName: orderId
        column: order_id
        type: string
        indexed: true
      - apiName: customerId
        column: customer_id
        type: string
        indexed: true
      - apiName: status
        column: source_status
        type: string
        indexed: true
      - apiName: riskScore
        column: risk_score
        type: float
        indexed: true
      - apiName: operatorNote
        type: string
        editable: true
        source: edit_layer
```

### 8.4 Link type DSL

```yaml
linkTypes:
  - apiName: OrderCustomer
    displayName: Order belongs to Customer
    from: Order
    to: Customer
    cardinality: many_to_one
    backing:
      dataset: clean.orders
      fromKey: order_id
      toKey: customer_id
```

### 8.5 Action type DSL

```yaml
actionTypes:
  - apiName: ApproveOrder
    displayName: Approve order
    target: Order
    parameters:
      - apiName: reason
        type: string
        required: true
      - apiName: approvedQuantity
        type: integer
        required: false
    permissions:
      allowedRoles: [ops_manager]
    preconditions:
      - safeExpression: "object.status in ['PENDING', 'REVIEW']"
        message: "Only pending/review orders can be approved"
    mutations:
      - type: setProperty
        property: status
        value: "APPROVED"
      - type: setProperty
        property: operatorNote
        valueFrom: "params.reason"
    writebacks:
      - apiName: erpApproveOrder
        mode: beforeCommit
        connector: erp_rest
        request:
          method: POST
          path: "/orders/{{object.orderId}}/approve"
          body:
            reason: "{{params.reason}}"
    sideEffects:
      - type: event
        topic: ops.order.approved
      - type: webhook
        mode: afterCommit
        connector: slack_webhook
```

---


### 8.6 Ontology activation validation

Ontology draft를 active로 전환하기 전 다음 검증을 반드시 통과해야 한다.

```text
- object type apiName uniqueness
- object/property/action apiName immutability check
- primary key property existence
- backing dataset exists
- backing dataset active main version exists
- primary key column exists
- property column existence/type compatibility
- link from/to object type existence
- link key column compatibility
- action target object type existence
- action mutation property existence/editability
- writeback connector reference validity, if any
- security policy references valid roles/groups/properties
```

검증 실패 시 activation은 불가능하다. 이미 active인 ontology version은 수정하지 않고 새 draft version을 만든다.

### 8.7 API name immutability

`apiName`은 SDK와 API contract다. display name은 바꿀 수 있지만 apiName은 생성 후 변경하지 않는다.

```text
- objectType.apiName immutable
- property.apiName immutable
- linkType.apiName immutable
- actionType.apiName immutable
- rename은 create new + deprecate old로 처리
- deprecated property/action은 read 가능하되 new writes 금지 가능
```

### 8.8 Safe expression language

Action precondition, policy condition, derived property는 임의 JS/Python eval을 쓰지 않는다. 현재 로컬 slice는 `safeExpression`이라는 제한된 subset을 쓰며, v1 target은 CEL 또는 JSON Logic 중 하나를 채택하는 것이다.

권장 기본값:

```yaml
preconditions:
  - safeExpression: "object.status in ['PENDING', 'REVIEW']"
```

expression evaluator는 pure function이어야 하며 network/file/database access를 금지한다.

## 9. Object Store 설계

### 9.1 왜 Object Store가 필요한가

Dataset lake는 대량 분석과 재현성에 좋지만, 운영 앱의 object query latency에는 맞지 않는다. Object Store는 Ontology object의 current operational view를 serving하기 위한 저장소다.

### 9.2 저장 전략

현재 MVP는 SQLAlchemy 기반 object store와 JSON column을 사용한다. PostgreSQL JSONB object store와 production index/RLS 운영은 future target이다.

- 기본: generic object table + SQLAlchemy JSON properties
- 자주 쓰는 indexed property: generated column or 별도 typed index table
- 검색/대규모 필터: object store가 source of truth이고, Elasticsearch-compatible projection proof는 존재하지만 managed cluster 운영은 future scope
- 대규모 object type: object type별 physical table로 승격 가능

### 9.3 Core tables

```sql
create table object_records (
  tenant_id uuid not null,
  object_type_id uuid not null,
  object_id text not null,
  properties jsonb not null default '{}',
  base_properties jsonb not null default '{}',
  edit_properties jsonb not null default '{}',
  property_versions jsonb not null default '{}',
  source_dataset_version_id uuid,
  source_hash text,
  object_version bigint not null default 1,
  deleted boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, object_type_id, object_id)
);

create index object_records_props_gin
  on object_records using gin (properties jsonb_path_ops);

create table object_links (
  tenant_id uuid not null,
  link_type_id uuid not null,
  from_object_type_id uuid not null,
  from_object_id text not null,
  to_object_type_id uuid not null,
  to_object_id text not null,
  properties jsonb not null default '{}',
  source_dataset_version_id uuid,
  link_version bigint not null default 1,
  deleted boolean not null default false,
  updated_at timestamptz not null default now(),
  primary key (tenant_id, link_type_id, from_object_id, to_object_id)
);

create table object_edits (
  id uuid primary key,
  tenant_id uuid not null,
  action_run_id uuid,
  object_type_id uuid not null,
  object_id text not null,
  edit_type text not null, -- set_property | delete_property | create_object | delete_object | create_link | delete_link
  patch jsonb not null,
  previous_values jsonb,
  actor_user_id uuid,
  idempotency_key text,
  created_at timestamptz not null default now()
);
```


### 9.3.1 Optimistic concurrency control

Action Runtime은 object update 시 `expectedObjectVersion`을 요구한다.

```http
POST /api/actions/ApproveOrder/apply
Idempotency-Key: approve-O-1001-001

{
  "target": {"objectType": "Order", "objectId": "O-1001"},
  "expectedObjectVersion": 17,
  "params": {"reason": "Inventory confirmed"}
}
```

Object update는 compare-and-swap 방식으로 처리한다.

```sql
update object_records
set object_version = object_version + 1,
    edit_properties = $new_edit_properties,
    properties = $new_current_properties,
    updated_at = now()
where tenant_id = $tenant_id
  and object_type_id = $object_type_id
  and object_id = $object_id
  and object_version = $expected_object_version;
```

업데이트 row count가 0이면 `409 conflict`를 반환한다. silent last-write-wins는 허용하지 않는다.

### 9.4 Current view 계산

Object current view는 다음과 같다.

```text
current_properties = merge(base_properties, edit_properties according to property policy)
```

Property policy:

| Policy | 의미 |
|---|---|
| source_wins | dataset update가 user edit을 덮음 |
| edit_wins | user edit이 source update보다 우선 |
| edit_only | source에는 없고 action으로만 생성 |
| conflict_requires_review | 충돌 시 conflict object 생성 |

예시:

```yaml
properties:
  - apiName: status
    source: dataset
    column: source_status
    editPolicy: conflict_requires_review
  - apiName: operatorNote
    source: edit_layer
    editPolicy: edit_only
```


### 9.4.1 Conflict records

`conflict_requires_review` 정책을 실제로 구현하려면 conflict를 저장해야 한다.

```sql
create table object_conflicts (
  id uuid primary key,
  tenant_id uuid not null,
  object_type_id uuid not null,
  object_id text not null,
  property_api_name text not null,
  source_value jsonb,
  edit_value jsonb,
  source_dataset_version_id uuid,
  edit_id uuid,
  status text not null, -- open | resolved_source | resolved_edit | resolved_custom
  resolved_by uuid,
  resolved_at timestamptz,
  created_at timestamptz not null default now()
);
```

Conflict가 open이면 해당 property는 query 결과에서 conflict metadata를 포함한다.

### 9.4.2 Deletion semantics

`deleted boolean` 하나만으로는 lineage와 materialization 의미가 부족하다. v1은 다음 deletion reason을 구분한다.

| 종류 | 의미 |
|---|---|
| source_deleted | 원천 데이터에서 사라짐 |
| action_deleted | 사용자 action으로 삭제 |
| tombstoned | CDC delete 또는 retention에 의해 tombstone 처리 |
| hidden_by_policy | 삭제가 아니라 권한상 비가시 |

```sql
alter table object_records add column deletion_reason text;
alter table object_links add column deletion_reason text;
```

### 9.5 Object Set

```sql
create table object_sets (
  id uuid primary key,
  tenant_id uuid not null,
  name text,
  object_type_id uuid not null,
  set_type text not null, -- static | dynamic
  definition jsonb not null, -- ids or query filter AST
  visibility text not null, -- temporary | permanent
  owner_user_id uuid,
  expires_at timestamptz,
  created_at timestamptz not null default now()
);
```

Static object set:

```json
{"ids": ["O-1001", "O-1002"]}
```

Dynamic object set:

```json
{
  "filter": {
    "and": [
      {"property": "status", "op": "eq", "value": "PENDING"},
      {"property": "riskScore", "op": "gte", "value": 0.8}
    ]
  }
}
```

---

## 10. Funnel-lite: Ontology Indexer 설계

### 10.1 역할

Funnel-lite는 Foundry Object Data Funnel의 축소 버전이다.

입력:

- dataset committed event, v1
- action edit event, v1
- ontology mapping change event, v1
- stream/CDC event, Phase 6

출력:

- object store upsert/delete
- object link upsert/delete
- search index update
- object_changed event
- lineage edge
- materialization trigger

### 10.2 Indexing event types

```json
{
  "type": "dataset.version.committed",
  "datasetId": "...",
  "versionId": "...",
  "branch": "main",
  "committedAt": "..."
}
```

```json
{
  "type": "object.edit.committed",
  "editId": "...",
  "objectType": "Order",
  "objectId": "O-1001",
  "actionRunId": "..."
}
```

```json
{
  "type": "stream.cdc.record",
  "source": "pg_erp.orders",
  "topic": "raw.pg_erp.orders.cdc",
  "partition": 1,
  "offset": 238923,
  "payload": {...}
}
```


### 10.2.1 Index run state

Indexing은 반드시 run state와 cursor를 남긴다.

```sql
create table index_runs (
  id uuid primary key,
  tenant_id uuid not null,
  object_type_id uuid not null,
  trigger_type text not null, -- dataset_commit | cdc | action_edit | reindex
  source_ref jsonb not null,
  status text not null, -- queued | running | succeeded | failed | cancelled
  cursor jsonb,
  rows_read bigint default 0,
  objects_upserted bigint default 0,
  objects_deleted bigint default 0,
  links_upserted bigint default 0,
  error jsonb,
  started_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz not null default now()
);
```

Operations UI와 replay CLI는 이 테이블을 기준으로 동작한다.

### 10.3 Dataset snapshot indexing algorithm

Pseudo-code:

```ts
async function indexDatasetVersion(event) {
  const objectTypes = await ontology.findObjectTypesBackedByDataset(event.datasetId);

  for (const objectType of objectTypes) {
    const mapping = objectType.backing;
    const reader = await datasetReader.open(event.versionId);

    for await (const batch of reader.readBatches()) {
      const objects = batch.map(row => mapRowToObject(row, objectType));
      await objectStore.bulkUpsertBaseLayer(objects, {
        sourceDatasetVersionId: event.versionId,
        conflictPolicy: objectType.conflictPolicy,
      });
      await searchIndex.bulkIndex(objects);
      await outbox.publishObjectChanged(objects);
    }
  }
}
```

### 10.4 CDC indexing algorithm

```ts
async function indexCdcEvent(event) {
  const mapping = await ontology.findMappingBySource(event.source);
  const objectId = buildObjectId(mapping.primaryKey, event.payload.pk);

  if (event.payload.op === 'd') {
    await objectStore.markDeleted(mapping.objectTypeId, objectId, event.ordering);
    await searchIndex.delete(mapping.objectTypeId, objectId);
  } else {
    const basePatch = mapAfterToBaseProperties(event.payload.after, mapping);
    await objectStore.upsertBasePatch(mapping.objectTypeId, objectId, basePatch, {
      ordering: event.payload.ordering,
      sourceEvent: event,
    });
    await searchIndex.update(mapping.objectTypeId, objectId);
  }

  await outbox.publish('object.changed', { objectTypeId: mapping.objectTypeId, objectId });
}
```

### 10.5 Action edit indexing

Action Runtime이 object store를 transactionally update한다면 indexer는 search index/materialization/outbox 중심으로 처리한다. 반대로 action runtime이 edit log만 쓰고 object store update를 indexer가 처리하는 event-sourcing 방식도 가능하다.

v1 추천:

- Action Runtime이 PostgreSQL transaction 안에서 `object_edits`와 `object_records`를 함께 업데이트한다.
- Indexer는 `object.edit.committed` outbox를 소비해 search index와 materialization을 업데이트한다.

이유: 사용자 액션 후 UI가 즉시 current object state를 볼 수 있어야 한다.

### 10.6 Reindex

Reindex는 반드시 지원해야 한다.

```bash
foundry-lite index rebuild --object-type Order --from-dataset-version latest
foundry-lite index replay-actions --object-type Order --from 2026-06-01
foundry-lite index rebuild-search --object-type Order
```

Reindex 전략:

1. 새 shadow index/table 생성
2. dataset snapshot + action edits replay
3. count/hash validation
4. alias switch
5. old index/table retention 후 삭제

---


### 10.7 Ontology mapping change reindex policy

Ontology activation이 항상 full reindex를 의미하지는 않는다.

| 변경 | 처리 |
|---|---|
| displayName 변경 | reindex 불필요 |
| property 추가, existing column | partial reindex 가능 |
| property column mapping 변경 | object type full reindex |
| primary key 변경 | breaking, new object type 권장 |
| link backing 변경 | link reindex |
| action DSL 변경 | reindex 불필요 |
| security policy 변경 | query/search cache invalidation |

### 10.8 Reindex correctness contract

Reindex는 다음 순서로만 완료 처리한다.

```text
1. shadow table/index 생성
2. base dataset snapshot index
3. action edits replay
4. object count/hash validation
5. sampled object comparison
6. alias/view switch
7. old table/index retention
```

count/hash validation이 실패하면 shadow 결과는 폐기하고 active view를 유지한다.

## 11. Object Query Service 설계

### 11.1 Query DSL

```json
{
  "objectType": "Order",
  "filter": {
    "and": [
      {"property": "status", "op": "eq", "value": "PENDING"},
      {"property": "riskScore", "op": "gte", "value": 0.7}
    ]
  },
  "orderBy": [{"property": "orderTs", "direction": "desc"}],
  "select": ["orderId", "customerId", "status", "riskScore"],
  "page": {"limit": 50, "cursor": null}
}
```

### 11.2 Link traversal

```json
{
  "from": {"objectType": "Customer", "objectId": "C-100"},
  "link": "CustomerOrders",
  "toFilter": {"property": "status", "op": "eq", "value": "PENDING"}
}
```

### 11.3 Aggregation

```json
{
  "objectType": "Order",
  "filter": {"property": "status", "op": "in", "value": ["PENDING", "APPROVED"]},
  "groupBy": ["status"],
  "metrics": [{"type": "count", "as": "count"}, {"type": "sum", "property": "amount", "as": "amountSum"}]
}
```

### 11.4 Execution strategy

| Query type | Engine |
|---|---|
| get by id | SQLAlchemy object store primary key lookup |
| simple filter | SQLAlchemy JSON/object property query path |
| full-text search | Elasticsearch-compatible projection if enabled, object store remains source of truth |
| large aggregation | materialized dataset first, later Trino/DuckDB/Spark over materialized dataset |
| link traversal | object_links table + object_records join |


### 11.5 Permission-aware query

Object Query Service는 모든 query에 tenant/user/policy context를 주입한다.

```ts
const policyContext = {
  tenantId,
  userId,
  groups,
  roles,
  purpose,
  objectType,
};

const rewrittenQuery = policyEngine.applyObjectPolicies(query, policyContext);
const maskedResult = policyEngine.applyPropertyMasks(result, policyContext);
```

v1 policy split:

```text
PostgreSQL RLS
- tenant isolation
- coarse object row visibility

Application policy
- property masking
- action execute permission
- dynamic object conditions
- audit decision reason
```

RLS만으로 JSONB property masking을 처리하려 하지 않는다. property-level masking은 application layer에서 처리하고 audit에 decision reason을 남긴다.

## 12. Action Runtime 설계

### 12.1 Action은 typed transaction이다

Action은 단순 API endpoint가 아니다. Action은 다음을 한 번에 정의한다.

- 누가 실행 가능한가
- 어떤 object를 대상으로 하는가
- 어떤 parameter를 받는가
- 실행 전 조건은 무엇인가
- 어떤 object edit을 만드는가
- 어떤 외부 시스템 writeback을 하는가
- 실패 시 어떻게 보상/재시도하는가
- 어떤 audit/event를 남기는가


### 12.2 Action execution lifecycle

```text
RECEIVED
→ LOAD_ACTION_TYPE
→ AUTHZ_CHECK
→ PARAM_VALIDATION
→ LOAD_TARGET_OBJECTS
→ VERSION_CHECK
→ PRECONDITION_CHECK
→ BEFORE_COMMIT_WRITEBACK optional
→ OPEN_OBJECT_TRANSACTION
→ APPLY_MUTATIONS
→ WRITE_ACTION_LOG
→ WRITE_OUTBOX
→ COMMIT
→ AFTER_COMMIT_SIDE_EFFECTS
→ MATERIALIZATION_TRIGGER
→ SUCCESS
or FAILED / CONFLICT / COMPENSATION_REQUIRED
```

Before-commit writeback의 상세 상태는 별도로 추적한다.

```text
received
validating
writeback_in_progress
writeback_succeeded
local_commit_in_progress
succeeded
failed
compensation_required
reconciled
```


### 12.3 Action tables

```sql
create table action_types (
  id uuid primary key,
  tenant_id uuid not null,
  ontology_version_id uuid not null,
  api_name text not null,
  display_name text not null,
  target_object_type_id uuid,
  parameter_schema jsonb not null,
  definition jsonb not null,
  enabled boolean not null default true,
  unique(tenant_id, ontology_version_id, api_name)
);

create table action_runs (
  id uuid primary key,
  tenant_id uuid not null,
  action_type_id uuid not null references action_types(id),
  actor_user_id uuid not null,
  target_object_type_id uuid,
  target_object_id text,
  expected_object_version bigint,
  parameters jsonb not null,
  status text not null,
  idempotency_key text not null,
  error jsonb,
  created_at timestamptz not null default now(),
  completed_at timestamptz,
  unique(tenant_id, action_type_id, actor_user_id, idempotency_key)
);

create table action_writebacks (
  id uuid primary key,
  action_run_id uuid not null references action_runs(id),
  mode text not null, -- before_commit | after_commit
  connector_id uuid,
  request jsonb not null,
  response jsonb,
  status text not null,
  idempotency_key text,
  attempts int not null default 0,
  created_at timestamptz not null default now(),
  completed_at timestamptz
);
```

같은 actor가 같은 action type에 같은 idempotency key를 재사용하면 기존 action_run을 반환한다. target 기준 idempotency가 필요한 action은 definition에서 stricter key를 요구할 수 있다.

### 12.4 Transaction boundary

외부 시스템과 local object store 사이에 완전한 distributed transaction은 피한다. 대신 saga/idempotency/outbox를 쓴다.

**Before-commit writeback:**

```text
1. external system call with idempotency key
2. if external fails → action fails, no object edit
3. if external succeeds → local object edit transaction
4. if local transaction fails after external success → compensation_required 기록, reconciliation worker 실행
```

**After-commit side effect:**

```text
1. local object edit transaction commit
2. user sees success
3. side effect/webhook/event is retried asynchronously
4. side effect failure does not roll back object edit
```

### 12.5 Object edit mutation types

```yaml
mutations:
  - type: setProperty
  - type: unsetProperty
  - type: incrementProperty
  - type: createObject
  - type: deleteObject
  - type: createLink
  - type: deleteLink
```


### 12.6 Idempotency

모든 action request는 idempotency key를 가져야 한다.

```http
POST /api/actions/ApproveOrder/apply
Idempotency-Key: approve-order-O-1001-20260609-001
```

동일 actor/action/idempotency key 재요청 시 기존 action_run을 반환한다.

권장 unique key:

```sql
unique(tenant_id, action_type_id, actor_user_id, idempotency_key)
```

대량 자동화 클라이언트는 UUID idempotency key를 생성한다. 사람이 쓰는 deterministic key는 target object id를 포함한다.

### 12.6.1 Action concurrency contract

Action apply payload는 `expectedObjectVersion`을 포함한다.

```json
{
  "target": {"objectType": "Order", "objectId": "O-1001"},
  "expectedObjectVersion": 17,
  "params": {"reason": "Inventory confirmed"}
}
```

서버가 현재 version과 다르다고 판단하면 action은 실행하지 않고 `409 conflict`를 반환한다. conflict response는 current object version과 변경된 property를 포함한다.

### 12.7 Undo/Revert

v1에서는 두 종류만 지원한다.

1. property edit revert: 이전 값이 `object_edits.previous_values`에 있으면 되돌림
2. action-specific compensating action: Action Type에 `compensatingAction` 정의

---

## 13. Materialization / Writeback 설계

### 13.1 왜 필요한가

Object state와 action log가 object store 안에만 있으면 폐루프가 완성되지 않는다. Action으로 바뀐 운영 상태가 다시 dataset/stream/external system으로 나가야 transform과 외부 시스템이 이를 사용할 수 있다.


### 13.2 Materialization types

v1에서 구현하는 materialization은 두 개만 필수다.

| Type | v1 여부 | 설명 |
|---|---|---|
| object_snapshot | 필수 | object current view를 dataset으로 출력 |
| action_log | 필수 | action_run/action_edits를 dataset으로 출력 |
| object_delta | post-MVP/future | object_changed event만 dataset/stream으로 출력 |
| link_snapshot | post-MVP/future | object links를 dataset으로 출력 |
| external_export | post-MVP/future | dataset/object를 외부 DB/S3/API로 export |

v1 closed-loop demo는 `object_snapshot`과 `action_log`만 사용한다.

### 13.3 Materialization definition

```yaml
materializations:
  - apiName: order_current_dataset
    type: object_snapshot
    objectType: Order
    targetDataset: ops.order_current
    trigger:
      type: schedule
      cron: "*/15 * * * *"

  - apiName: action_log_dataset
    type: action_log
    targetDataset: ops.action_log
    trigger:
      type: on_action_committed
```

### 13.4 Materialization tables

```sql
create table materializations (
  id uuid primary key,
  tenant_id uuid not null,
  api_name text not null,
  materialization_type text not null,
  source_ref jsonb not null,
  target_ref jsonb not null,
  trigger_config jsonb not null,
  enabled boolean not null default true,
  unique(tenant_id, api_name)
);

create table materialization_runs (
  id uuid primary key,
  materialization_id uuid not null references materializations(id),
  status text not null,
  source_cursor jsonb,
  target_dataset_version_id uuid,
  row_count bigint,
  error jsonb,
  created_at timestamptz not null default now(),
  completed_at timestamptz
);
```

### 13.5 Closed-loop example

```text
1. User executes ApproveOrder on Order O-1001.
2. Action Runtime updates Order.status = APPROVED.
3. action_log row is created.
4. object_changed and action_committed events are published.
5. Materialization writes ops.order_current and ops.action_log datasets.
6. Transform clean.customer_risk consumes ops.action_log.
7. New risk score is computed and written to clean.customer_risk.
8. Funnel-lite indexes Customer.riskScore.
9. Customer object page updates.
```

### 13.6 Materialization consistency contract

Materialization은 어느 시점의 object state를 출력했는지 명확히 기록해야 한다.

```text
- materialization_run starts at object_store_watermark = X
- include object_records where update event <= X
- action_log materialization cursor <= X
- output dataset metadata includes source_cursor and object_store_watermark
- failed materialization does not advance cursor
```

```sql
alter table materialization_runs add column object_store_watermark jsonb;
alter table materialization_runs add column consistency_level text default 'watermark';
```

이 contract가 없으면 object_snapshot과 action_log가 서로 다른 시점의 상태를 나타낼 수 있다.


## 14. Security / Governance 설계

### 14.1 v1 security contract

v1은 enterprise-grade security 전체를 구현하지 않는다. 대신 다음 보안 계약은 반드시 지킨다.

```text
v1 must-have:
- tenant_id isolation
- user identity
- role-based permission
- dataset read/write permission
- source credential usage permission
- transform run permission
- ontology edit/activate permission
- object read/query permission
- action execute permission
- property mask on read
- writeback connector usage permission
- audit all writes

v1 explicitly not:
- mandatory markings
- CBAC/classification hierarchy
- purpose-based access everywhere
- cross-organization collaboration
- full cell-level security
```

### 14.2 Security layers

```text
Infrastructure security
- container isolation
- secret management
- network policy
- TLS

Platform security
- tenant isolation
- RBAC
- object/property/action policies
- audit
- lineage

Data security
- dataset ACL
- object visibility
- property masking
- row-level restrictions where simple
```

### 14.3 Policy model

v1 policy DSL:

```yaml
policies:
  - apiName: order_ops_visibility
    appliesTo: Order
    effect: allow
    actions: [read]
    condition:
      safeExpression: "user.role == 'admin' || object.region in user.allowedRegions"

  - apiName: order_margin_masking
    appliesTo: Order.margin
    effect: mask
    condition:
      safeExpression: "!(user.role in ['finance', 'admin'])"
```

Expression은 CEL/JSON Logic 같은 safe expression engine만 허용한다.

### 14.4 Permission checkpoints

- Dataset read/write
- Source credential usage
- Transform run
- Ontology edit/activate
- Object read/search
- Property read/mask
- Action execute
- Writeback connector usage
- Materialization target write

### 14.5 RLS와 application policy의 역할 분리

```text
PostgreSQL RLS:
- tenant isolation
- coarse row visibility

Application policy engine:
- property masking
- dynamic object conditions
- action execute permission
- writeback permission
- policy decision audit
```

JSONB property masking은 RLS만으로 처리하지 않는다. 모든 read API는 policy engine을 통과한 뒤 결과를 반환한다.

### 14.6 Audit events

```sql
create table audit_events (
  id uuid primary key,
  tenant_id uuid not null,
  actor_user_id uuid,
  event_type text not null,
  resource_type text not null,
  resource_id text,
  action text,
  decision text,
  policy_decision jsonb,
  before_ref jsonb,
  after_ref jsonb,
  correlation_id text,
  request_id text,
  ip inet,
  metadata jsonb not null default '{}',
  created_at timestamptz not null default now()
);
```

Writes는 전부 audit한다. Reads는 기본적으로 object/query level에서 audit하고, 민감 리소스는 row/object ids까지 audit한다.

필수 correlation:

```text
action_run_id = correlation_id
object_edit.action_run_id = action_run_id
outbox_event.aggregate_id = action_run_id or object_id
audit_events.correlation_id = action_run_id or request_id
```

## 15. API 설계

### 15.1 Dataset API

```http
POST /api/datasets
GET /api/datasets
GET /api/datasets/{datasetId}
GET /api/datasets/{datasetId}/versions
POST /api/datasets/{datasetId}/transactions
POST /api/datasets/{datasetId}/transactions/{txId}/files
POST /api/datasets/{datasetId}/transactions/{txId}/commit
POST /api/datasets/{datasetId}/transactions/{txId}/abort
GET /api/datasets/{datasetId}/lineage
```

### 15.2 Source / Sync API

```http
POST /api/sources
GET /api/sources
POST /api/sources/{sourceId}/test
POST /api/syncs
POST /api/syncs/{syncId}/run
GET /api/syncs/{syncId}/runs
POST /api/listeners/{listenerId}/events
```

### 15.3 Transform API

```http
POST /api/transforms
GET /api/transforms
POST /api/transforms/{transformId}/run
GET /api/transforms/{transformId}/runs
GET /api/lineage/resources/{resourceId}
```

### 15.4 Ontology API

```http
GET /api/ontology/active
POST /api/ontology/drafts
PUT /api/ontology/drafts/{draftId}/object-types/{apiName}
PUT /api/ontology/drafts/{draftId}/link-types/{apiName}
PUT /api/ontology/drafts/{draftId}/action-types/{apiName}
POST /api/ontology/drafts/{draftId}/validate
POST /api/ontology/drafts/{draftId}/activate
```

### 15.5 Object API

```http
GET /api/objects/{objectType}/{objectId}
POST /api/objects/{objectType}/query
POST /api/objects/{objectType}/aggregate
POST /api/objects/{objectType}/{objectId}/links/{linkType}
POST /api/object-sets
GET /api/object-sets/{objectSetId}
POST /api/object-sets/{objectSetId}/query
```

### 15.6 Action API

```http
POST /api/actions/{actionTypeApiName}/apply
GET /api/actions/runs/{actionRunId}
GET /api/actions/runs
GET /api/actions/logs
POST /api/actions/runs/{actionRunId}/retry-side-effects
POST /api/actions/runs/{actionRunId}/revert
```

Action apply request는 idempotency key와 expected object version을 포함한다.

```json
{
  "target": {"objectType": "Order", "objectId": "O-1001"},
  "expectedObjectVersion": 17,
  "params": {"reason": "Inventory confirmed"}
}
```

Conflict response:

```json
{
  "error": "OBJECT_VERSION_CONFLICT",
  "currentObjectVersion": 18,
  "message": "Object was updated after the client loaded it. Reload and retry."
}
```

### 15.7 Materialization API

```http
POST /api/materializations
GET /api/materializations
POST /api/materializations/{materializationId}/run
GET /api/materializations/{materializationId}/runs
```

---

## 16. OSDK-lite 설계


### 16.1 목표

프론트엔드/외부 앱 개발자가 raw REST endpoint를 직접 다루지 않고 Ontology 중심으로 개발하게 한다.

현재 checkout에는 ontology metadata에서 생성되는 TypeScript package/browser SDK surface가 있다. React hooks와 더 풍부한 generated client ergonomics는 future scope다.

### 16.2 Generated TypeScript SDK

Ontology metadata에서 타입을 생성한다.

```ts
export type Order = {
  orderId: string;
  customerId: string;
  status: 'PENDING' | 'APPROVED' | 'REJECTED';
  riskScore?: number;
  operatorNote?: string;
};

export const client = createFoundryLiteClient({ baseUrl, token });

const order = await client.objects.Order.get('O-1001');
const pendingOrders = await client.objects.Order.query({
  where: { status: { eq: 'PENDING' }, riskScore: { gte: 0.7 } },
  limit: 50,
});

await client.actions.ApproveOrder.apply({
  object: order,
  params: { reason: 'Inventory confirmed' },
  idempotencyKey: 'approve-O-1001-001',
});
```


### 16.3 React hooks, future

React hooks는 future 기능이다. 현재 UI는 TanStack Query와 generated browser SDK boundary를 사용한다.

```ts
const { data: order } = useObject('Order', orderId);
const approve = useAction('ApproveOrder');

await approve.apply({
  objectId: orderId,
  expectedObjectVersion: order.objectVersion,
  params: { reason }
});
```

## 17. Web UI 설계

### 17.1 필수 화면

1. **Home / Workspace**
   - 최근 dataset, object type, failed run, pending actions

2. **Sources**
   - source 생성/test
   - sync 생성/run
   - sync history

3. **Datasets**
   - dataset list
   - schema view
   - versions
   - preview
   - health checks
   - lineage graph

4. **Transforms**
   - transform list
   - run history
   - logs
   - input/output graph

5. **Ontology Manager-lite**
   - object type editor YAML first
   - link type editor
   - action type editor
   - validate/activate

6. **Object Explorer**
   - object type 선택
   - filters/search
   - saved object sets
   - object detail
   - links
   - actions

7. **Actions / Audit**
   - action logs
   - writeback status
   - failed side effects retry

8. **Operations**
   - worker health
   - queues
   - failed runs
   - DLQ
   - reindex/replay tools


### 17.1.1 Operations v1 최소 화면

Closed-loop system은 Operations 화면이 없으면 디버깅이 어렵다. v1에서 최소한 다음을 보여준다.

```text
Runs
- sync runs
- transform runs
- index runs
- action runs
- materialization runs

Queues
- pending outbox events
- failed outbox events
- DLQ

Replay
- retry sync
- retry transform
- replay index event
- retry side effect
- rerun materialization

Health
- stale datasets
- failed checks
- failed writebacks
- index lag
```

### 17.2 UI 우선순위

v1에서는 visual builder보다 YAML/JSON editor가 낫다. 복잡한 UI보다 모델이 먼저 안정되어야 한다.

---

## 18. Monorepo 구조

```text
foundry-lite/
  README.md
  pyproject.toml
  uv.lock
  pnpm-workspace.yaml
  turbo.json
  package.json

  apps/
    api/
      foundry_lite_api/
        main.py
        routers/
        dependencies/
        schemas/
      tests/

    web/
      app/
      components/
      lib/
      generated/

    worker/
      foundry_lite_worker/
        temporal/
        connectors/
        transforms/
        indexer/
        actions/
        materializations/
        outbox/
      Dockerfile

    cli/
      foundry_lite_cli/
        commands/
          dataset.py
          transform.py
          ontology.py
          index.py
          action.py

  libs/
    foundry_lite/
      domain/
        dataset/
        ontology/
        object_store/
        action_runtime/
        policy/
        lineage/
      application/
        datasets/
        transforms/
        ontology/
        objects/
        actions/
        materializations/
      infrastructure/
        db/
        storage/
        temporal/
        connectors/
        outbox/
      interfaces/
        api_schemas/
        cli_schemas/
        openapi/
      observability/
      security/
      transforms_sdk/

  packages/
    sdk-ts/
      src/
        client.ts
        objects.ts
        actions.ts

  infra/
    docker-compose.dev.yml
    docker-compose.full.yml
    helm/
      foundry-lite/
    terraform/

  examples/
    supply-chain-demo/
      data/
      sources/
      transforms/
      ontology/
      actions/
      materializations/

  docs/
    architecture.md
    data-flow.md
    ontology.md
    action-runtime.md
    operations.md
    foundry_lite_python_engineering_guidelines_ko.md
```

---

## 19. 이벤트 / Outbox 설계


### 19.1 왜 Outbox가 필요한가

DB transaction과 Kafka publish는 원자적으로 묶기 어렵다. 따라서 현재 MVP core는 DB transaction 안에서 outbox row를 쓰고, 이후 publisher가 외부 event bus 또는 internal worker queue로 발행할 수 있는 경계를 둔다.

현재 checkout에서는 SQLAlchemy outbox가 canonical event plane이다. Kafka-compatible stream archive proof는 존재하지만, Kafka/Redpanda outbox publisher와 continuously running worker는 future scope다.

```sql
create table outbox_events (
  id uuid primary key,
  tenant_id uuid not null,
  event_type text not null,
  aggregate_type text not null,
  aggregate_id text not null,
  payload jsonb not null,
  status text not null default 'pending', -- pending | published | failed | dead_lettered
  attempts int not null default 0,
  idempotency_key text,
  correlation_id text,
  created_at timestamptz not null default now(),
  published_at timestamptz,
  unique(tenant_id, event_type, idempotency_key)
);
```

Publisher contract:

```text
pending → published
pending → failed → pending retry
failed attempts exceeded → dead_lettered + dead_letter_events row
```

### 19.2 주요 이벤트

```text
dataset.version.committed
sync.run.completed
transform.run.completed
ontology.version.activated
object.index.requested
object.changed
action.run.submitted
action.run.committed
action.writeback.failed
materialization.requested
materialization.completed
```

### 19.3 DLQ

```sql
create table dead_letter_events (
  id uuid primary key,
  source_event_id uuid,
  event_type text,
  payload jsonb,
  error jsonb,
  failed_at timestamptz not null default now(),
  retry_after timestamptz
);
```

---

## 20. Closed-loop 데모 시나리오

### 20.1 도메인

공급망 운영 예시.

Sources:

- ERP PostgreSQL: orders
- CRM CSV: customers
- WMS CSV: inventory
- Shipment events: post-MVP Kafka-compatible stream archive proof / future production topic
- Mock external ERP REST API: order approval writeback demo adapter

Ontology:

- Customer
- Order
- Product
- InventoryItem
- Shipment
- Warehouse

Links:

- Order -> Customer
- Order -> Shipment
- InventoryItem -> Warehouse

Actions:

- ApproveOrder
- HoldShipment
- ReallocateInventory
- FlagCustomerRisk

### 20.2 End-to-end flow

```text
1. ERP orders table sync → raw.erp_orders dataset
2. CRM customers CSV upload → raw.crm_customers dataset
3. Transform clean_orders → clean.orders dataset
4. Transform clean_customers → clean.customers dataset
5. Ontology maps clean.orders → Order object
6. Ontology maps clean.customers → Customer object
7. Funnel-lite indexes Order and Customer
8. Object Explorer shows pending high-risk orders
9. User runs ApproveOrder action on O-1001
10. Action Runtime optionally calls mock ERP REST writeback before local commit in demo profile
11. Action updates Order.status = APPROVED and creates action log
12. Materialization writes ops.order_current and ops.action_log
13. Transform customer_risk consumes ops.action_log
14. Customer risk score changes
15. Funnel-lite indexes updated Customer
16. UI shows changed Customer risk and order history
```

### 20.3 Acceptance criteria

- `raw.erp_orders` dataset has committed version.
- `clean.orders` transform output has lineage to `raw.erp_orders`.
- `Order` object type has indexed records.
- `GET /api/objects/Order/O-1001` returns current object.
- `ApproveOrder` action changes object state.
- Failed writeback prevents local object edit when configured as beforeCommit.
- Side effect failure does not roll back object edit when configured as afterCommit.
- `ops.action_log` dataset receives action record.
- Downstream transform uses action log and updates another object.
- Full path is observable in lineage/audit.
- One-command demo is repeatable from a clean isolated demo home and does not depend on a specific developer's local DB state.

---


## 21. 개발 로드맵 현재 동기화

상세 실행 순서와 체크박스의 원본은 [스프린트 실행 계획](./foundry_lite_sprint_breakdown_ko.md)이다. 이 섹션은 오래된 Phase 할 일 목록이 아니라, 2026-06-18 현재 checkout 기준으로 “무엇이 완료되었고 무엇이 proof/future인지”를 요약한다.

| 범위 | 현재 상태 | 남은 것 |
|---|---|---|
| Scaffold / API / Web / CLI / Worker skeleton | 완료. 모노레포, FastAPI, Web, CLI, worker entrypoint, shared config/logging/error boundary가 있다. | production packaging polish |
| Dataset transaction vertical slice | 완료. CSV/local snapshot, immutable dataset version, staging/manifest commit, schema/health guard, preview, sync run tracking이 있다. S3/Iceberg storage ratchet은 post-MVP proof로 active-covered다. | PostgreSQL snapshot connector production implementation, future Iceberg maintenance/catalog operations, managed retention/compaction |
| Transform vertical slice | 완료. DuckDB SQL transform, input/output version binding, lineage, health gate, failed-run cleanup이 있다. | executable Python runner, sandboxed SDK IO, Temporal scheduling |
| Ontology / Object vertical slice | 완료. YAML import/validate/activate, object/link/action definitions, object indexing, query, links, object explorer, shadow reindex proof가 있다. | very large object-type serving optimization |
| Action vertical slice | 완료. `ApproveOrder`, safeExpression subset, permission/precondition, expectedObjectVersion, idempotency, action log, outbox, audit, UI action form이 있다. | future real external ERP/webhook writeback and compensation worker |
| Materialization / closed loop | 완료. `object_snapshot`, `action_log`, watermark/source version proof, downstream transform, lineage/audit/operations tracing이 있다. | additional materialization types and external export |
| Streaming / CDC post-MVP proof | 부분 완료. REST/Webhook, Kafka-compatible stream archive, live broker proof, Debezium archive/live topic, CDC object indexing proof가 있다. | continuously running workers, rebalance/commit-unknown failure injection, production deployment packaging |
| Search post-MVP proof | 부분 완료. Elasticsearch-compatible adapter/projection/rebuild/orphan drift proof가 있다. | managed live Elasticsearch cluster deployment |
| Scale hardening | 일부 proof. active index pointer, shadow swap, PostgreSQL contract coverage, RLS contract proof, S3/Iceberg/Spark/infra-composition ratchet이 있다. | Kubernetes/Helm, backup/restore, managed operations, real cluster/cloud/chaos evidence |

## 22. Performance targets

### v1 local/small prod targets

| Operation | Target |
|---|---:|
| Object get by id | p95 < 100ms |
| Object filtered query, 100k objects | p95 < 500ms |
| Action apply no external writeback | p95 < 300ms |
| CSV ingest 1M rows | < 5 minutes local dependent |
| Snapshot index 1M objects | < 10 minutes local dependent |
| Materialize 1M object rows | < 10 minutes local dependent |

### scale mode targets

| Operation | Target |
|---|---:|
| Object get by id | p95 < 50ms |
| Object query 10M objects with indexed filters | p95 < 1s |
| Action apply no external writeback | p95 < 250ms |
| CDC object update latency | p95 < 5s |
| Snapshot index 10M objects | parallelized, < 30 min target |

---

## 23. 운영 원칙

### 23.1 모든 작업은 idempotent

- sync run 재시도 가능
- transform run 재시도 가능
- index event 재처리 가능
- action request 중복 처리 방지
- webhook idempotency key 전달
- materialization cursor 기반 재실행

### 23.2 모든 대량 작업은 checkpoint를 가진다

- dataset transaction id
- stream topic/partition/offset
- object index cursor
- materialization cursor
- transform input version cursor

### 23.3 모든 side effect는 outbox를 거친다

외부 시스템 호출은 반드시 outbox/action_writebacks에 기록한다. 네트워크 호출 성공/실패는 audit 가능해야 한다.

### 23.4 replay-first debugging

운영 장애 대응 CLI:

```bash
flite sync retry <run-id>
flite transform retry <run-id>
flite dataset inspect <dataset> --version latest
flite ontology validate ontology.yaml
flite object get Order O-1001 --explain
flite action replay <action-run-id>
flite index replay --object-type Order --from-event <event-id>
flite index replay-run <index-run-id>
flite materialize run order_current_dataset
```

---

## 24. 테스트 전략

### 24.1 Unit tests

- DSL parser/validator
- property mapping
- object merge policy
- action precondition evaluator
- policy evaluator
- dataset transaction state machine
- Python backend line/branch/function coverage 95% 이상

### 24.2 Integration tests

Focused Testcontainers proof에서 다음을 띄운다.

- Postgres
- Kafka-compatible broker
- Debezium Connect/PostgreSQL CDC path

테스트:

- connector sync → dataset commit
- transform run → output dataset
- ontology import → object index
- action apply → object edit + outbox
- materialization → dataset output
- one-command closed-loop demo repeated twice from the same checkout
- 필수 integration scenario 100% 실행/100% 통과

### 24.3 End-to-end tests

Playwright:

- dataset upload
- transform run
- ontology activate
- object query
- action apply
- action log 확인
- demo smoke output is parseable JSON release evidence
- smoke checklist 100% 실행/100% 통과

### 24.4 Data correctness tests

- row count equality
- primary key uniqueness
- CDC ordering resolution
- action idempotency

### 24.5 Coverage and release gates

아래 기준은 권장값이 아니라 release gate다. 비개발자 관점으로 말하면, “테스트를 어느 정도 했다”가 아니라 “출시해도 추적과 복구가 가능한 수준까지 검증했다”는 뜻이다.

- [x] Python 백엔드 line coverage는 95% 이상이다. ([VERIFY-FULL-CI-GATE](./docs/sprint-evidence-ledger.md#verify-full-ci-gate))
- [x] Python 백엔드 branch coverage는 95% 이상이다. ([quality gate roadmap G6/G8](./docs/quality-gate-roadmap.md#tier-g8--layer-coverage-floor--완료-2026-06-11-g8))
- [x] Python 백엔드 public function/method coverage는 95% 이상이다. ([MVP Core Coverage](./docs/sprint-evidence-ledger.md#mvp-core-coverage))
- [x] domain/application/infrastructure/API/worker/CLI 영역별 커버리지가 평균 수치로 가려지지 않는다. ([Tier G8](./docs/quality-gate-roadmap.md#tier-g8--layer-coverage-floor--완료-2026-06-11-g8))
- [x] 필수 integration test는 100% 실행되고 100% 통과한다. ([MVP Core Integration/Smoke](./docs/sprint-evidence-ledger.md#mvp-core-integration-smoke))
- [x] 필수 smoke test는 100% 실행되고 100% 통과한다. ([VERIFY-FULL-CI-GATE](./docs/sprint-evidence-ledger.md#verify-full-ci-gate))
- [x] skipped/flaky/xfail 테스트는 release gate 통과 근거로 쓰지 않는다. ([flaky detector evidence](./docs/sprint-evidence-ledger.md#verify-full-ci-gate))
- [x] reindex result hash comparison은 shadow reindex/count-hash validation proof로 검증한다. ([VERIFY-SHADOW-REINDEX](./docs/sprint-evidence-ledger.md#verify-shadow-reindex))

---

## 25. 주요 리스크와 완화

### 25.1 v1 scope creep 리스크

완화:

- v1 scope guardrail을 문서에 고정
- Kafka/CDC/Elasticsearch/REST/Webhook과 Spark/Iceberg는 MVP core 밖 post-MVP proof로 기록하고, Kubernetes/backup-restore와 managed production operations는 future scope로 분리
- 각 sprint는 하나의 수직 slice exit criteria로 종료
- demo에 필요 없는 connector/visual builder 개발 금지

### 25.2 Ontology가 너무 빨리 복잡해지는 리스크

완화:

- v1은 YAML 기반 schema-first
- visual editor는 나중
- property type을 제한
- link cardinality를 명확히 제한
- Functions on Objects arbitrary execution 제외
- activation validation을 강제

### 25.3 Object Store generic JSON/JSONB 성능 리스크

완화:

- index profile 도입
- generated column promotion
- object type별 physical table 승격
- Elasticsearch-compatible projection proof
- v1 query contract와 scale-mode performance target을 분리

### 25.4 External writeback consistency 리스크

완화:

- idempotency key
- beforeCommit/afterCommit mode 명확화
- compensation_required 상태
- reconciliation worker
- action log immutable
- correlation_id로 audit/writeback/action_run 연결

### 25.5 Action concurrency 리스크

완화:

- 모든 action request에 expectedObjectVersion 요구
- object_version optimistic concurrency
- conflict 시 409 반환
- object_conflicts table로 source/edit conflict 추적

### 25.6 Materialization consistency 리스크

완화:

- source_cursor/object_store_watermark 저장
- failed materialization은 dataset transaction abort
- action_log/object_snapshot 시점 계약 명시
- downstream transform은 materialized dataset version을 명시적으로 참조

### 25.7 Pipeline runner를 직접 만드는 리스크

완화:

- 현재 MVP core는 local/direct workflow boundary를 사용하고, Temporal adapter proof만 current로 본다. Product workflow execution through Temporal은 S52 future scope로 둔다.
- DuckDB-first runner로 commit/lineage/check contract 고정
- 복잡한 asset orchestration은 Dagster integration optional
- transform SDK interface는 외부 orchestrator로도 호출 가능하게 설계

### 25.8 Iceberg day 1 복잡도 리스크

완화:

- `DatasetStorageAdapter`로 Parquet manifest와 Iceberg를 추상화
- MVP는 Parquet manifest로 개발
- dataset transaction model은 Iceberg 전환을 염두에 둔다

## 26. 첫 번째 구현 묶음: Scaffold + Dataset Commit 데모

이 섹션은 공식 스프린트 번호를 새로 정의하지 않는다. 공식 실행 순서는 [스프린트 실행 계획](./foundry_lite_sprint_breakdown_ko.md)을 원본으로 보며, 여기서는 개발자가 초기에 한 번에 체감할 수 있는 첫 데모 묶음만 설명한다.

### 구현 묶음 목표

“로컬 런타임을 띄우고, CSV 파일 하나를 `raw.erp_orders` dataset으로 commit하며, dataset version/transaction/preview가 안정적으로 동작한다.”

이 묶음은 [Sprint 00](./foundry_lite_sprint_breakdown_ko.md#sprint-00--제품-경계데모-도메인성공-정의-고정)부터 [Sprint 08](./foundry_lite_sprint_breakdown_ko.md#sprint-08--health-checks와-commit-차단)까지의 일부를 실제 데모가 가능한 순서로 압축해 확인하는 용도다. transform, ontology, object indexing까지 욕심내지 않고 Dataset transaction vertical slice를 먼저 안정화한다.

### 작업 목록

- [x] Monorepo scaffold. ([S01-A1](./docs/sprint-evidence-ledger.md#s01-a1))
- [x] FastAPI API skeleton. ([S01-A2](./docs/sprint-evidence-ledger.md#s01-a2))
- [x] SQLAlchemy schema bootstrap, schema revision guard, and Alembic baseline fresh-DB parity guard. PostgreSQL production migration operations and multi-step upgrade/rollback은 future/deferred다. ([VERIFY-STATIC](./docs/sprint-evidence-ledger.md#verify-static))
- [x] Tables: datasets, dataset_schemas, dataset_transactions, dataset_versions, dataset_files. ([MVP-RAW](./docs/sprint-evidence-ledger.md#mvp-core-raw-dataset))
- [x] Local filesystem storage adapter와 fake storage swap proof. 이후 S3-compatible storage와 Iceberg ratchet proof는 post-MVP 증거로 active-covered이며, managed retention/catalog 운영은 future scope다. ([S02A-P1](./docs/sprint-evidence-ledger.md#s02a-p1))
- [x] Dataset transaction state machine. ([VERIFY-FAILED-MUTATION-STATE](./docs/sprint-evidence-ledger.md#verify-failed-mutation-state))
- [x] CSV upload endpoint/path. ([MVP-RAW](./docs/sprint-evidence-ledger.md#mvp-core-raw-dataset))
- [x] staging path writer. ([VERIFY-DATASET-STORAGE-SPLIT-BRAIN](./docs/sprint-evidence-ledger.md#verify-dataset-storage-split-brain))
- [x] schema inference. ([MVP-RAW](./docs/sprint-evidence-ledger.md#mvp-core-raw-dataset))
- [x] commit manifest writer. ([VERIFY-DATASET-STORAGE-SPLIT-BRAIN](./docs/sprint-evidence-ledger.md#verify-dataset-storage-split-brain))
- [x] health checks: row count, primary key uniqueness optional. ([VERIFY-DATASET-HEALTH-CANDIDATE](./docs/sprint-evidence-ledger.md#verify-dataset-health-candidate))
- [x] dataset preview endpoint with DuckDB. ([S22-A1](./docs/sprint-evidence-ledger.md#s22-a1))
- [x] minimal dataset list/detail UI. ([S22-A1](./docs/sprint-evidence-ledger.md#s22-a1))
- [x] audit write for dataset transaction. ([VERIFY-FULL-CI-GATE](./docs/sprint-evidence-ledger.md#verify-full-ci-gate))
- [x] outbox event: dataset.version.committed. ([G18 outbox consistency](./docs/quality-gate-roadmap.md))

### 첫 데모

```bash
pnpm dev
flite dataset create raw.erp_orders
flite dataset upload raw.erp_orders examples/supply-chain-demo/data/orders.csv
flite dataset versions raw.erp_orders
flite dataset preview raw.erp_orders --version latest
flite dataset inspect raw.erp_orders --version latest
```

### 다음 구현 묶음 예고

다음 묶음은 이미 MVP core에 포함되어 완료되었다. 현재 DuckDB SQL transform, lineage, materialization, downstream transform까지 폐루프 증거가 있다.

```bash
flite transform run clean_orders
flite lineage dataset clean.orders
```


## 27. 구현 전 P0 체크리스트

이 체크리스트는 구현 전 P0였고, 2026-06-18 현재는 아래 상태로 동기화한다.

- [x] v1 MVP core 필수 connector는 CSV/local snapshot path로 제한하고, PostgreSQL-backed repository proof는 테스트 증거로 분리한다. PostgreSQL snapshot production connector는 future/deferred다.
- [x] `COMMITTED` dataset version immutability가 repository method와 release gate에서 강제된다. ([MVP-RAW](./docs/sprint-evidence-ledger.md#mvp-core-raw-dataset))
- [x] staging → manifest commit protocol이 sync/transform/materialization에 공통 적용된다. ([VERIFY-DATASET-STORAGE-SPLIT-BRAIN](./docs/sprint-evidence-ledger.md#verify-dataset-storage-split-brain), [VERIFY-MATERIALIZATION-COMMIT-FAILURE](./docs/sprint-evidence-ledger.md#verify-materialization-commit-failure))
- [x] ontology activation validation checklist가 validator 단위로 구현되어 있다. ([MVP-ONTOLOGY](./docs/sprint-evidence-ledger.md#mvp-core-ontology))
- [x] Action API request schema에 `expectedObjectVersion`이 포함되어 있다. ([S25-A3](./docs/sprint-evidence-ledger.md#s25-a3))
- [x] `action_runs` idempotency key/request fingerprint replay/conflict guard가 있다. ([VERIFY-ACTION-IDEMPOTENCY-FINGERPRINT](./docs/sprint-evidence-ledger.md#verify-action-idempotency-fingerprint))
- [x] `index_runs` 테이블과 reindex/replay CLI/API/Web path가 있다. ([S33-A5](./docs/sprint-evidence-ledger.md#s33-a5))
- [x] materialization run이 source cursor와 object store watermark를 기록한다. ([VERIFY-MATERIALIZATION-WATERMARKS](./docs/sprint-evidence-ledger.md#verify-materialization-watermarks))
- [x] security v1 boundary가 tenant/RBAC/property masking/audit-all-writes로 제한되어 있다. ([Security commit points](./docs/sprint-evidence-ledger.md#security-commit-points))
- [x] Operations UI/API/CLI가 failed sync/transform/index/action/materialization 계열 run을 추적하고, current MVP 실패 경로 replay/retry proof를 제공한다. ([MVP-OPERATIONS](./docs/sprint-evidence-ledger.md#mvp-core-operations-replay))

---

## 28. 개정 반영 요약

이번 개정에서 반영한 핵심 변경은 다음이다.

| 항목 | 변경 내용 |
|---|---|
| v1 범위 | Kafka/CDC/Elasticsearch/Spark/복잡 보안을 MVP core 완료 조건에서 제외하고 CSV/local snapshot 또는 PostgreSQL-backed repository proof → DuckDB transform → Ontology/Object → Action → Materialization 폐루프로 축소 |
| 스택 | Python 3.12 + FastAPI + Pydantic v2 + SQLAlchemy schema revision guard + Alembic baseline parity, DuckDB canonical, SQLAlchemy outbox first로 확정. Multi-step migration operations와 production PostgreSQL outbox 운영은 future scope |
| Architecture | Control/Data/Event plane 분리, module dependency rule 추가 |
| Dataset | immutable version, staging + manifest commit protocol, schema compatibility, dev→main promotion 추가 |
| Transform | SQL/DuckDB-first, output commit protocol, append-only incremental constraint 추가 |
| Ontology | activation validation, apiName immutability, safe expression language, Function runtime v1 제외 |
| Object Store | expectedObjectVersion, optimistic concurrency, conflict table, deletion semantics 추가 |
| Funnel-lite | index_runs, reindex correctness, mapping change reindex policy 추가 |
| Action Runtime | detailed lifecycle, idempotency unique key 보완, conflict contract 추가 |
| Materialization | v1 type 2개로 축소, watermark/source_cursor consistency 추가 |
| Security | v1 security contract로 범위 축소, RLS/application policy 역할 분리, audit schema 강화 |
| Roadmap | Sprint/Phase를 vertical slice 기준으로 재조정 |

## 29. 참고자료

- Palantir Foundry Architecture Center: Ontology system
- Palantir Foundry Multimodal Data Plane
- Palantir Foundry Data Connection
- Palantir Foundry Pipeline Builder
- Palantir Foundry Object Backend / Object Storage V2 / Object Data Funnel
- Palantir Foundry Action Types / Webhooks
- Palantir Foundry Ontology SDK
- Apache Iceberg documentation
- Apache Kafka documentation
- Debezium documentation
- Temporal documentation
- PostgreSQL Row-Level Security documentation

---

## 30. Sprint Breakdown 요약 및 링크

스프린트 실행 계획의 원본은 [Foundry-lite Sprint Breakdown & Must-Win Goals](./foundry_lite_sprint_breakdown_ko.md)이다. 이 기획서에는 중복 전문을 두지 않고, 전체 설계와 실행 계획이 어떻게 연결되는지만 요약한다.

### 문서 연결

- 제품 목표와 설계 원본: [Foundry-lite 개발 기획서](./foundry_lite_development_plan_ko_sprintified.md)
- 실행 순서와 스프린트별 완료 조건 원본: [스프린트 실행 계획](./foundry_lite_sprint_breakdown_ko.md)
- 외부 근거와 Foundry 공개 문서 분석 원본: [Palantir Foundry 심층 분석](./deep-research-report.md)
- Python 백엔드 코드 품질 기준 원본: [Python 백엔드 엔지니어링 가이드](./foundry_lite_python_engineering_guidelines_ko.md)

### 스프린트 단계 요약

- Sprint 00~02: 제품 경계, 로컬 런타임, 테넌트/감사 기반을 고정한다.
- Sprint 02A: Scale Foundation으로 infra swap boundary, contract test, trace key, composition root를 고정한다.
- Sprint 03~10: Dataset Registry와 Data Connection-lite로 raw dataset commit 경로를 완성한다.
- Sprint 11~14: DuckDB SQL transform과 lineage로 clean dataset 생성 경로를 완성한다.
- Sprint 15~23: Ontology, Object Store, Object Query, Outbox로 운영 객체 조회 기반을 만든다.
- Sprint 24~32: Action Runtime, side effect, materialization으로 운영 변경이 다시 dataset으로 돌아오는 폐루프를 완성한다.
- Sprint 33~36: Operations, Security, SDK, E2E release gate로 v1 MVP를 검증한다.
- Sprint 37~42: REST/Webhook, Kafka-compatible stream archive, Debezium CDC, CDC object indexing, Elasticsearch-compatible search projection은 MVP 이후 확장 proof로 구현 증거가 있다.
- Sprint 43~44: Iceberg와 Spark ratchet은 active-covered proof가 있다. Production cluster 운영, Spark 분산 장애, catalog 운영 runbook 같은 범위는 별도 future scope다.
- Sprint 45: Kubernetes/backup-restore 운영 패키지는 아직 future scope로 둔다.
- Sprint 46~64: post-MVP 데이터 플랫폼 확장 순서는 [Data Platform Expansion Roadmap](./docs/data-platform-expansion-roadmap.md)을 원본으로 본다. 첫 실행은 S46 Semantic SSOT + Data Engineering Pattern Matrix다.
- 모든 Python 백엔드 스프린트는 Clean Code, SRP, 타입 검사, 테스트 기준을 [Python 백엔드 엔지니어링 가이드](./foundry_lite_python_engineering_guidelines_ko.md)에 맞춘다.
- 모든 Python 백엔드 스프린트는 [안티패턴 방지와 강제 대응 원칙](./foundry_lite_python_engineering_guidelines_ko.md#18-안티패턴-방지와-강제-대응-원칙)을 통과해야 한다.
- 모든 Python 백엔드 스프린트는 line/branch/function coverage 95% 이상과 필수 integration/smoke 100% 통과 기준을 만족해야 한다.

### MVP Core Completion Gate

Sprint 00~36, Sprint 02A, Sprint 36A가 끝났을 때 아래가 모두 가능해야 MVP core 완료다. 상세 acceptance는 [스프린트 실행 계획의 MVP Core Completion Gate](./foundry_lite_sprint_breakdown_ko.md#mvp-core-completion-gate)를 원본으로 보고, 항목별 증거는 [Sprint Evidence Ledger의 MVP Core Completion Gate Evidence Map](./docs/sprint-evidence-ledger.md#mvp-core-completion-gate-evidence-map)에 둔다. real ERP writeback, production backup/restore, Kubernetes, managed Iceberg/Spark operations 같은 확장은 future backlog로 남긴다. Sprint 37~44는 MVP 이후 확장 proof로 구현 증거가 있지만, MVP core 완료 조건을 넓히지는 않는다.

- [x] CSV/local snapshot 또는 PostgreSQL-backed repository closed-loop path로 raw dataset을 commit한다.
- [x] Scale Foundation boundary가 있어 storage/metadata/compute/event/search/workflow/connector/auth infra를 port/adapter 뒤에서 교체할 수 있다.
- [x] SQL/DuckDB transform으로 clean dataset을 만든다. Python transform execution은 fail-closed future scope다.
- [x] Ontology draft를 validate/activate한다.
- [x] clean dataset rows를 Order/Customer objects로 index한다.
- [x] Object Explorer에서 Order를 조회하고 Order -> Customer link를 본다.
- [x] ApproveOrder action을 실행한다.
- [x] object_records, object_edits, action_runs, audit_events, outbox_events가 모두 일관되게 남는다.
- [x] action_log와 object_snapshot을 dataset으로 materialize한다.
- [x] downstream transform이 materialized dataset을 읽고 Customer object를 갱신한다.
- [x] 전체 경로를 lineage/audit/operations UI에서 추적하고 MVP 실패 경로를 replay할 수 있다.
- [x] 주요 실패 경로는 단순 패치가 아니라 regression test, error type, request/run trace로 재발 방지된다.
- [x] Python 백엔드 line/branch/function coverage가 모두 95% 이상이다.
- [x] 필수 integration test와 smoke test가 100% 실행되고 100% 통과한다.

### Recommended First Demo Command Shape

```bash
pnpm dev
flite demo seed supply-chain
flite sync run sync_orders_pg
flite dataset upload raw.crm_customers examples/supply-chain-demo/data/customers.csv
flite transform run clean_orders
flite transform run clean_customers
flite ontology apply examples/supply-chain-demo/ontology/order-customer.yaml
flite index rebuild Order
flite index rebuild Customer
flite action apply ApproveOrder --object Order/O-1001 --param reason="Inventory confirmed"
flite materialize run action_log
flite materialize run order_current
flite transform run customer_risk
flite index rebuild Customer
flite object get Customer C-100 --explain
```
