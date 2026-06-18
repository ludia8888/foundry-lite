# Foundry-lite Sprint Breakdown & Must-Win Goals

**작성일:** 2026-06-09

이 문서는 Foundry-lite 문서 체계의 **스프린트 실행 계획 원본**이다. [Foundry-lite 개발 기획서](./foundry_lite_development_plan_ko_sprintified.md)를 실제 구현 가능한 작은 스프린트 단위로 나누고, 각 스프린트가 반드시 통과해야 하는 제품/기술 Goal을 정의한다.

> 현재 구현 상태 주의: 2026-06-18 기준 현재 구현이 실제로 보장하는 범위는 [Implementation Status](./docs/implementation-status.md)를 따른다. 체크박스가 `[x]`인 상태 추적 항목은 [Sprint Evidence Ledger](./docs/sprint-evidence-ledger.md)에 PR, merge commit, 테스트, 품질 게이트 근거가 있어야 한다. 개발 가이드용 체크리스트는 제품 완료 상태가 아니라 매 변경 때 확인하는 템플릿으로 본다.

## 문서 지도

- 이 문서는 스프린트 순서, Must-Win Goal, Acceptance Gate의 원본이다.
- 제품 목표와 시스템 설계는 [Foundry-lite 개발 기획서](./foundry_lite_development_plan_ko_sprintified.md)를 원본으로 본다.
- Foundry 공개 문서에서 가져온 외부 근거는 [Palantir Foundry 심층 분석](./deep-research-report.md)을 원본으로 본다.
- Python 백엔드 구현 원칙과 코드 품질 기준은 [Python 백엔드 엔지니어링 가이드](./foundry_lite_python_engineering_guidelines_ko.md)를 원본으로 본다.
- v1 필수 범위는 네 문서 모두 `CSV/local snapshot 또는 PostgreSQL-backed repository proof → DuckDB transform → Ontology/Object → Action → Materialization → Downstream Transform`으로 통일한다.
- commit point가 하나의 진실로 유지되는지에 대한 위험 판정은 [Commit-Point Risk Register](./docs/commit-point-risk-register.md)를 따른다.

### 함께 읽을 문서

- [Foundry-lite 개발 기획서](./foundry_lite_development_plan_ko_sprintified.md): 이 스프린트들이 구현해야 하는 전체 제품 목표와 설계를 확인한다.
- [Palantir Foundry 심층 분석](./deep-research-report.md): Ontology, Dataset, Action, Materialization이 왜 핵심인지 외부 근거를 확인한다.
- [Python 백엔드 엔지니어링 가이드](./foundry_lite_python_engineering_guidelines_ko.md): 각 스프린트의 Python 코드가 지켜야 할 Clean Code, SRP, 테스트, 트랜잭션, 운영 로그 기준을 확인한다.

### 완료 판단 기준

- 각 스프린트의 성공은 코드 완료가 아니라 CLI/API/UI/테스트 중 하나로 증명된다.
- 각 스프린트는 이전 스프린트의 결과를 실제로 사용한다.
- 모든 write는 audit 가능하다.
- 모든 state transition은 dataset transaction, stream offset/checkpoint, action edit/event log 중 하나로 replay 가능하다.
- 기능이 동작해도 cursor, offset, watermark, manifest, lineage, outbox, action state가 durable commit point보다 앞서가면 완료로 보지 않는다.
- Sprint 00~36은 MVP core, Sprint 02A는 scale-ready foundation 보강, Sprint 36A는 MVP 운영 안정성 보강, Sprint 37 이후는 MVP 이후 확장으로 구분된다.
- Python 백엔드 코드는 `ruff`, `mypy` 또는 `pyright`, `pytest` 품질 게이트를 통과한다.
- 안티패턴 금지 기준을 위반한 단순 패치는 완료로 보지 않는다.
- 에러 발생 시 request/run/dataset/object/action 단위로 원인을 추적할 수 있다.
- Python 백엔드 테스트 커버리지는 line, branch, function 기준 모두 95% 이상이어야 한다.
- 필수 통합 테스트와 필수 스모크 테스트는 100% 실행되고 100% 통과해야 한다.

### 2026-06-18 상세 체크박스 동기화 기준

- Sprint 00~36, Sprint 02A, Sprint 36A의 상세 체크박스는 현재 구현, [Sprint Evidence Ledger](./docs/sprint-evidence-ledger.md), [Implementation Status](./docs/implementation-status.md), 최신 `main` CI 결과를 기준으로 다시 동기화했다.
- `[x]`는 둘 중 하나를 뜻한다: 현재 MVP 구현과 테스트 증거로 완료되었거나, 현 MVP scope에서 명시적으로 future/deferred로 재분류되어 더 이상 Sprint 00~36 완료 조건으로 요구하지 않는다는 결정이 끝났다는 뜻이다.
- future/deferred로 재분류된 항목은 문장 안에 그 사실을 명시한다. 구현 완료와 scope 제외를 섞어 말하지 않는다.
- Sprint 43 Iceberg와 Sprint 44 Spark 항목은 현재 `docs/infra-ratchet.md`, `docs/infra-tricky-matrix.json`, `quality:iceberg`, `quality:spark`, `quality:infra-composition` 증거 기준으로 다시 동기화한다. 단, production cluster 운영과 분산 장애/운영 runbook은 별도 future scope다.
- Sprint 45 Kubernetes/backup-restore 항목은 아직 구현 증거가 없으므로 `[ ]`로 남긴다. 이 미체크 상태 자체가 최신 구현 상태와 동기화된 것이다.
- Sprint 46 이후 post-MVP 확장 순서는 [Data Platform Expansion Roadmap](./docs/data-platform-expansion-roadmap.md)을 따른다. 첫 실행 단위는 S46 Semantic SSOT + Data Engineering Pattern Matrix다.

모든 스프린트는 다음 원칙을 따른다.

1. 매 스프린트는 하나의 명확한 increment를 만든다.
2. 다음 스프린트가 이전 스프린트의 결과를 실제로 사용해야 한다.
3. 성공은 코드 완료가 아니라 CLI/API/UI/테스트 중 하나로 증명해야 한다.
4. 모든 write는 audit 가능해야 한다.
5. 모든 state transition은 dataset transaction, stream offset/checkpoint, action edit/event log 중 하나로 replay 가능해야 한다.
6. MVP core에서는 CSV/local snapshot 또는 PostgreSQL-backed repository proof, SQL/DuckDB transform, Ontology/Object, Action, Materialization 폐루프에 집중한다. CDC, Kafka streaming, Elasticsearch, Iceberg, Spark, Kubernetes production hardening은 MVP 이후 스프린트로 둔다. 단, 이것들을 나중에 쉽게 붙이기 위한 port/adapter boundary는 Sprint 02A에서 먼저 고정하고, Sprint 37~44의 post-MVP proof 중 현재 증거가 있는 항목은 active-covered로 따로 기록한다.
7. 장애나 버그는 간단한 증상 제거 패치로 끝내지 않고, 원인 분석, 추적 가능성, regression test까지 포함해 해결한다.

---

## Global Definition of Done

한 스프린트는 아래 조건을 모두 만족해야 완료로 본다.

- API/Worker/Web/CLI 중 해당 스프린트에 관련된 public surface가 동작한다.
- DB schema bootstrap, schema revision guard, and Alembic baseline migration parity가 빈 DB에서 재현 가능해야 한다. Multi-step Alembic upgrade/rollback 운영은 future/deferred 범위다.
- 핵심 state transition은 DB에 durable하게 기록된다.
- 실패 상태가 성공 상태와 구분되어 저장된다.
- unit test 또는 integration test가 핵심 성공/실패 경로를 검증한다.
- seed demo 또는 example이 업데이트된다.
- 운영자가 실패 원인을 audit/run/log에서 추적할 수 있다.
- 문서 또는 README에 사용법과 제한 사항이 반영된다.
- Python 백엔드 변경은 [Python 백엔드 엔지니어링 가이드](./foundry_lite_python_engineering_guidelines_ko.md)의 SRP, typing, transaction, test checklist를 통과한다.
- [안티패턴 방지와 강제 대응 원칙](./foundry_lite_python_engineering_guidelines_ko.md#18-안티패턴-방지와-강제-대응-원칙)을 위반하지 않는다.
- 실패 케이스는 request id, run id, domain id, error type 중 필요한 추적 키를 남긴다.
- storage, metadata DB, compute, event, search, workflow, connector, auth 같은 인프라와 닿는 변경은 port/interface, adapter, contract test, trace key를 함께 정의한다.
- Python 백엔드 line/branch/function coverage가 모두 95% 이상이다.
- 해당 스프린트의 필수 integration/smoke 시나리오가 100% 실행되고 100% 통과한다.

---

## Sprint Map

| Sprint | Phase | Sprint | Must-Win Goal 요약 | 관련 설계 섹션 |
|---:|---|---|---|---|
| 00 | Foundation | 제품 경계·데모 도메인·성공 정의 고정 | Foundry-lite를 단순 ETL/BI가 아니라 운영 객체 시스템으로 만들기 위한 제품 경계, 데모 도메인, MVP 합격 기준을 고정한다. | [제품 범위](./foundry_lite_development_plan_ko_sprintified.md#2-제품-범위) |
| 01 | Scaffold | 모노레포와 로컬 런타임 골격 | 개발자가 저장소를 clone한 뒤 하나의 명령으로 API, Web, Worker, DB, Object Storage를 띄울 수 있게 한다. | [Monorepo 구조](./foundry_lite_development_plan_ko_sprintified.md#18-monorepo-구조), [Python 가이드](./foundry_lite_python_engineering_guidelines_ko.md) |
| 02 | Scaffold | DB 마이그레이션·테넌트·인증 Stub·감사 골격 | 모든 리소스가 `tenant_id`, `actor_user_id`, `request_id`를 기준으로 기록되는 최소 control plane 기반을 만든다. | [Security / Governance](./foundry_lite_development_plan_ko_sprintified.md#14-security--governance-설계) |
| 02A | Foundation | Scale Foundation·Infra Swap Boundary | 유지보수성, traceability, scale-out을 위해 storage/metadata/compute/event/search/workflow/connector/auth 경계를 port와 adapter contract로 먼저 고정한다. | [v1 adapter boundary](./foundry_lite_development_plan_ko_sprintified.md#35-v1-adapter-boundary), [Python 가이드](./foundry_lite_python_engineering_guidelines_ko.md#42-디자인-패턴-적용-원칙) |
| 03 | Dataset Registry | Dataset 논리 자산 CRUD | Dataset을 단순 파일 업로드가 아니라 namespace/name/schema/storage_kind를 가진 논리 자산으로 등록한다. | [데이터 저장 계층](./foundry_lite_development_plan_ko_sprintified.md#5-데이터-저장-계층-설계) |
| 04 | Dataset Registry | Dataset Transaction 상태 머신 | Dataset 변경을 반드시 `OPEN → COMMITTED | ABORTED` transaction으로만 일어나게 만든다. | [Dataset transaction](./foundry_lite_development_plan_ko_sprintified.md#52-dataset-transaction-types) |
| 05 | Dataset Registry | StorageAdapter와 Manifest Commit Protocol | S3/MinIO 같은 object storage에서 원자적 rename에 의존하지 않고, staging path와 manifest pointer를 통해 안전하게 dataset version을 commit한다. | [Dataset commit protocol](./foundry_lite_development_plan_ko_sprintified.md#57-dataset-commit-protocol) |
| 06 | Dataset Registry | CSV Upload → Raw Dataset Commit | 사용자가 CSV 파일 하나를 업로드하면 Foundry-lite의 공식 dataset transaction protocol을 거쳐 `raw.erp_orders` 같은 raw dataset의 committed version을 만든다. | [Data Connection-lite](./foundry_lite_development_plan_ko_sprintified.md#6-data-connection-lite-설계) |
| 07 | Dataset Registry | Schema Registry·Compatibility·Preview | Dataset version마다 schema contract가 기록되고, 사용자가 최신 version을 preview할 수 있게 한다. | [Schema contract](./foundry_lite_development_plan_ko_sprintified.md#55-schema-contract) |
| 08 | Dataset Registry | Health Checks와 Commit 차단 | Dataset이 형식상 commit되는 것만으로 성공으로 보지 않고, primary key uniqueness, not-null, row count 같은 품질 조건을 통과해야 downstream build로 넘어갈 수 있게 한다. | [Dataset health checks](./foundry_lite_development_plan_ko_sprintified.md#56-dataset-health-checks) |
| 09 | Data Connection-lite | Source·Sync·Run Framework | CSV upload를 특수 endpoint가 아니라 Data Connection-lite의 한 sync 유형으로 일반화한다. | [Source, Sync, Connector](./foundry_lite_development_plan_ko_sprintified.md#61-source-sync-connector) |
| 10 | Data Connection-lite | PostgreSQL Snapshot Connector | 외부 운영 DB에서 query snapshot을 가져와 raw dataset으로 commit한다. | [지원 connector v1](./foundry_lite_development_plan_ko_sprintified.md#63-지원-connector-v1) |
| 11 | Transform Engine | Transform Registry와 SQL/DuckDB Runner | raw dataset version을 명시적으로 입력으로 묶고 SQL/DuckDB로 clean dataset을 생성할 수 있게 한다. | [Transform Engine](./foundry_lite_development_plan_ko_sprintified.md#7-transform-engine-설계) |
| 12 | Transform Engine | Transform Output Commit Protocol과 Lineage | Transform이 만든 output dataset이 어떤 input dataset version에서 왔는지 lineage로 재현 가능하게 만든다. | [Build graph and lineage](./foundry_lite_development_plan_ko_sprintified.md#77-build-graph-and-lineage) |
| 13 | Transform Engine | Transform Health Gates와 Temporal 실행 | Transform output도 dataset health check를 통과해야만 성공으로 인정하고, run/retry/schedule이 Temporal worker에서 안정적으로 관리되도록 한다. | [Transform run lifecycle](./foundry_lite_development_plan_ko_sprintified.md#75-transform-run-lifecycle) |
| 14 | Transform Engine | Python Transform SDK Prototype | Python 사용자가 Dataset Registry의 input/output abstraction을 통해 transform을 작성할 수 있게 한다. | [Python Transform SDK](./foundry_lite_development_plan_ko_sprintified.md#73-python-transform-sdk), [Python 가이드](./foundry_lite_python_engineering_guidelines_ko.md) |
| 15 | Ontology | Ontology Draft·Object/Property YAML Import | Dataset을 사용자에게 직접 노출하지 않고 `Order`, `Customer` 같은 object type으로 끌어올릴 수 있는 최소 ontology metadata를 만든다. | [Ontology 역할](./foundry_lite_development_plan_ko_sprintified.md#81-ontology의-역할) |
| 16 | Ontology | Ontology Activation Validation과 Versioning | Ontology draft를 active로 전환할 때 backing dataset, schema, primary key, property mapping을 엄격히 검증한다. | [Activation validation](./foundry_lite_development_plan_ko_sprintified.md#86-ontology-activation-validation) |
| 17 | Object Store | Object Store Core와 Merge Policy | Ontology object의 current operational view를 저장할 PostgreSQL 기반 object store를 만든다. | [Object Store](./foundry_lite_development_plan_ko_sprintified.md#9-object-store-설계) |
| 18 | Funnel-lite | Snapshot Indexer: Clean Dataset → Object Records | active ontology mapping에 따라 clean dataset snapshot을 object store로 인덱싱한다. | [Funnel-lite](./foundry_lite_development_plan_ko_sprintified.md#10-funnel-lite-ontology-indexer-설계) |
| 19 | Object Query | Object Get/Filter/Sort/Page API | 운영 앱이 lake table을 직접 scan하지 않고 object store에서 object를 조회하도록 한다. | [Object Query](./foundry_lite_development_plan_ko_sprintified.md#11-object-query-service-설계) |
| 20 | Object Query | Link Type과 Link Traversal | `Order → Customer` 같은 운영 관계를 object graph로 조회할 수 있게 한다. | [Link traversal](./foundry_lite_development_plan_ko_sprintified.md#112-link-traversal) |
| 21 | Object Query | Object Sets: Static/Dynamic Saved Sets | 사용자가 object query 결과를 운영 작업 단위로 저장하고 재사용할 수 있게 한다. | [Object Set](./foundry_lite_development_plan_ko_sprintified.md#95-object-set) |
| 22 | Web UI | Dataset·Ontology·Object 최소 UI Vertical Slice | CLI/API만 있는 플랫폼에서 벗어나, 사용자가 Web에서 dataset version, ontology active state, object query 결과를 한 흐름으로 볼 수 있게 한다. | [Web UI](./foundry_lite_development_plan_ko_sprintified.md#17-web-ui-설계) |
| 23 | Event Plane | Outbox·DLQ·Event Publisher | DB transaction과 외부 event publish를 직접 묶지 않고 outbox pattern으로 안정화한다. | [이벤트 / Outbox](./foundry_lite_development_plan_ko_sprintified.md#19-이벤트--outbox-설계) |
| 24 | Action Runtime | Action DSL·Parameter Validation·Precondition Engine | Action을 UI 버튼이 아니라 typed transaction definition으로 등록한다. | [Action Runtime](./foundry_lite_development_plan_ko_sprintified.md#12-action-runtime-설계) |
| 25 | Action Runtime | Action Apply Transaction과 Optimistic Concurrency | 사용자가 Action을 실행하면 object_records와 object_edits가 하나의 PostgreSQL transaction 안에서 갱신된다. | [Action concurrency](./foundry_lite_development_plan_ko_sprintified.md#1261-action-concurrency-contract) |
| 26 | Action Runtime | Action Idempotency·Action Log·Audit | 네트워크 재시도나 프론트엔드 중복 제출이 같은 Action을 여러 번 적용하지 못하게 하고, 모든 action execution이 감사·조회 가능한 action log로 남게 한다. | [Idempotency](./foundry_lite_development_plan_ko_sprintified.md#126-idempotency) |
| 27 | Writeback | Before-Commit Writeback과 Compensation 상태 | 외부 운영 시스템 writeback이 성공해야 local object edit을 commit하는 beforeCommit 모드를 구현한다. | [Transaction boundary](./foundry_lite_development_plan_ko_sprintified.md#124-transaction-boundary) |
| 28 | Side Effects | After-Commit Side Effects와 Retry | local object edit 성공 후 실행되는 webhook/event 같은 side effect를 object transaction과 분리한다. | [Event / Outbox](./foundry_lite_development_plan_ko_sprintified.md#19-이벤트--outbox-설계) |
| 29 | Web UI | Object Explorer Action Form | 사용자가 Object Explorer에서 object를 보고 바로 Action을 실행할 수 있게 한다. | [Web UI](./foundry_lite_development_plan_ko_sprintified.md#17-web-ui-설계) |
| 30 | Materialization | Action Log → Dataset Materialization | Action Runtime 안에 갇힌 운영 변경 기록을 dataset 세계로 되돌린다. | [Materialization](./foundry_lite_development_plan_ko_sprintified.md#13-materialization--writeback-설계) |
| 31 | Materialization | Object Snapshot → Dataset Materialization with Watermark | object store의 current operational view를 특정 watermark 기준으로 snapshot dataset으로 출력한다. | [Consistency contract](./foundry_lite_development_plan_ko_sprintified.md#136-materialization-consistency-contract) |
| 32 | Closed Loop | Downstream Transform이 Materialized Action/Object를 소비 | 폐루프의 마지막 고리를 완성한다. | [Closed-loop demo](./foundry_lite_development_plan_ko_sprintified.md#20-closed-loop-데모-시나리오) |
| 33 | Operations | Runs·Queues·Replay Operations UI/CLI | 폐루프 시스템은 반드시 실패를 볼 수 있고 재시도할 수 있어야 한다. | [운영 원칙](./foundry_lite_development_plan_ko_sprintified.md#23-운영-원칙) |
| 34 | Security/Governance | v1 RBAC·Dataset/Object Permission·Property Masking | v1에서 필요한 tenant isolation, RBAC, read/action permission, property masking을 일관된 policy service로 구현한다. | [Security / Governance](./foundry_lite_development_plan_ko_sprintified.md#14-security--governance-설계) |
| 35 | OSDK-lite | Generated TypeScript SDK와 Web SDK 전환 | 프론트엔드와 외부 앱이 raw REST endpoint를 직접 다루지 않고 ontology 타입과 action 메서드로 Foundry-lite를 사용하게 한다. | [OSDK-lite](./foundry_lite_development_plan_ko_sprintified.md#16-osdk-lite-설계) |
| 36 | MVP Release Hardening | MVP E2E·성능·데이터 정합성 Release Gate | MVP 폐루프가 문서가 아니라 반복 가능한 자동 테스트와 데모 스크립트로 증명되게 한다. | [테스트 전략](./foundry_lite_development_plan_ko_sprintified.md#24-테스트-전략), [Python 품질 게이트](./foundry_lite_python_engineering_guidelines_ko.md#16-ci-품질-게이트) |
| 37 | v1.5 Data Connection | REST Pull Connector와 Webhook Listener | 파일/DB snapshot 외에 API 기반 유입과 push ingest를 추가한다. | [v1.5 이후 기능](./foundry_lite_development_plan_ko_sprintified.md#23-v15-이후로-이관한-기능) |
| 38 | v1.5 Streaming | Redpanda/Kafka Stream Archive Writer | Kafka-compatible stream event를 raw archive dataset으로 남겨 replay 가능한 stream ingestion 기반을 만든다. | [CDC ingest, Phase 6](./foundry_lite_development_plan_ko_sprintified.md#65-cdc-ingest-phase-6) |
| 39 | v1.5 CDC | Debezium PostgreSQL CDC Connector | PostgreSQL row 변경을 Debezium envelope로 받아 raw changelog dataset과 object indexing input으로 사용할 수 있게 한다. | [CDC ingest, Phase 6](./foundry_lite_development_plan_ko_sprintified.md#65-cdc-ingest-phase-6) |
| 40 | v1.5 CDC | CDC Object Indexing과 Delete/Tombstone 처리 | 배치 rebuild 없이 CDC event가 object store의 base layer를 갱신하게 한다. | [CDC indexing](./foundry_lite_development_plan_ko_sprintified.md#104-cdc-indexing-algorithm) |
| 41 | Scale/Reindex | Shadow Reindex와 Hash Validation | Ontology mapping 변경이나 index corruption 상황에서 live read를 막지 않고 object index를 재생성할 수 있게 한다. | [Reindex](./foundry_lite_development_plan_ko_sprintified.md#106-reindex) |
| 42 | Scale/Search | Elasticsearch Adapter for Search-heavy Object Types | PostgreSQL JSONB query의 한계를 넘는 full-text/search-heavy object type을 위해 Elasticsearch adapter를 추가한다. | [Object Query execution](./foundry_lite_development_plan_ko_sprintified.md#114-execution-strategy) |
| 43 | Scale/Lakehouse | Iceberg StorageAdapter Prototype | Parquet manifest 기반 Dataset transaction 모델을 Iceberg table로 확장할 수 있음을 증명한다. | [Scale path](./foundry_lite_development_plan_ko_sprintified.md#34-scale-path) |
| 44 | Scale/Compute | Spark Runner Adapter Skeleton | 대용량 batch transform을 위해 Spark runner로 확장 가능한 compute adapter를 만든다. | [v1 adapter boundary](./foundry_lite_development_plan_ko_sprintified.md#35-v1-adapter-boundary) |
| 45 | Deployment/Operations | Kubernetes Helm·Backup/Restore·Operational Runbook | 로컬 Docker Compose를 넘어 small production 형태로 배포 가능한 운영 패키지를 만든다. | [Scale hardening](./foundry_lite_development_plan_ko_sprintified.md#phase-7--scale-hardening) |

---

## Detailed Sprint Goals

### Sprint 00 — 제품 경계·데모 도메인·성공 정의 고정

**Phase:** Foundation

**문서 연결:** [제품 범위](./foundry_lite_development_plan_ko_sprintified.md#2-제품-범위), [Foundry 분석](./deep-research-report.md#실행-요약)

**무조건 성공시켜야 하는 Goal**

Foundry-lite를 단순 ETL/BI가 아니라 운영 객체 시스템으로 만들기 위한 제품 경계, 데모 도메인, MVP 합격 기준을 고정한다. 이후 스프린트에서 기능 욕심으로 범위가 퍼지지 않도록, 'Dataset → Transform → Ontology → Object → Action → Materialization → Downstream Transform' 폐루프를 유일한 북극성으로 둔다.

**반드시 완성해야 하는 것**

- 공급망 데모 도메인의 최소 객체를 `Order`, `Customer` 두 개로 고정한다.
- 최소 raw dataset은 `raw.erp_orders`, `raw.crm_customers`로 고정한다.
- 최소 clean dataset은 `clean.orders`, `clean.customers`로 고정한다.
- 최소 action은 `ApproveOrder` 하나로 고정한다.
- 최소 materialization은 `ops.action_log`, `ops.order_current` 두 개로 고정한다.
- v1 MVP core에서 제외하고 post-MVP 또는 future scope로 분리할 기능을 명확히 적는다: CDC, Kafka streaming, Elasticsearch, Iceberg, Spark, visual builder, 복잡한 policy engine.
- 모든 스프린트의 Definition of Done에 audit, lineage, replay 가능성 원칙을 넣는다.

**Acceptance Gate**

- [x] `examples/supply-chain-demo/README.md`에 end-to-end 데모 목표가 적혀 있다. ([MVP-CORE](./docs/sprint-evidence-ledger.md#mvp-core-completion-gate-evidence-map))
- [x] `examples/supply-chain-demo/data/*.csv` seed 파일이 있다. (`examples/supply-chain-demo/data/orders.csv`, `customers.csv`)
- [x] `examples/supply-chain-demo/ontology/order-customer.yaml` 초안이 있다. (`examples/supply-chain-demo/ontology/order-customer.yaml`)
- [x] `docs/mvp-scope.md`에 v1 포함/제외 범위가 있다. ([docs/mvp-scope.md](./docs/mvp-scope.md))
- [x] 팀원이 새 기능을 제안해도 `docs/mvp-scope.md`, `docs/implementation-status.md`, 이 문서의 Sprint 00~36/Sprint 37 이후 경계로 v1/v1.5/v2를 판정할 수 있다.

**Demo / Proof**

문서와 seed data만으로 `Order`와 `Customer` 폐루프가 무엇을 증명해야 하는지 설명할 수 있어야 한다.

**이러면 성공으로 치지 않는다**

- MVP 도메인이 세 개 이상으로 늘어난다.
- 첫 데모에 CDC/Kafka/Elasticsearch/Iceberg/Spark가 필수로 들어간다.
- Action 없이 object current state만 조회하는 데모로 MVP를 정의한다.

---

### Sprint 01 — 모노레포와 로컬 런타임 골격

**Phase:** Scaffold

**문서 연결:** [Monorepo 구조](./foundry_lite_development_plan_ko_sprintified.md#18-monorepo-구조), [시스템 아키텍처](./foundry_lite_development_plan_ko_sprintified.md#4-시스템-아키텍처), [Python 백엔드 엔지니어링 가이드](./foundry_lite_python_engineering_guidelines_ko.md)

**무조건 성공시켜야 하는 Goal**

개발자가 저장소를 clone한 뒤 하나의 명령으로 API, Web, Worker, DB, Object Storage를 띄울 수 있게 한다. 아직 도메인 기능은 없어도, 이후 모든 스프린트가 같은 실행·테스트·마이그레이션 체계 위에서 진행되어야 한다.

**반드시 완성해야 하는 것**

- pnpm workspace, uv workspace, Turborepo 기본 구조를 만든다.
- Python 백엔드 앱 `apps/api`, `apps/worker`, `apps/cli`와 TypeScript Web 앱 `apps/web`을 생성한다.
- Python 라이브러리 `libs/foundry_lite/domain`, `application`, `infrastructure`, `interfaces`, `observability`, `security` 골격을 생성한다.
- TypeScript generated SDK 패키지 `packages/sdk-ts` 골격을 생성한다.
- Docker Compose로 PostgreSQL, MinIO, Temporal dev server를 띄운다. Redpanda/Elasticsearch는 full profile에만 둔다.
- API `/healthz`, Worker heartbeat, Web home page를 구현한다.
- 공통 config, logger, error type, request id/correlation id 유틸을 만든다.
- Python 품질 도구 `ruff`, `mypy` 또는 `pyright`, `pytest`를 CI placeholder에 연결한다.

**Acceptance Gate**

- [x] `uv sync && pnpm install && pnpm dev`가 성공한다. 최신 로컬 확인에서 API는 `127.0.0.1:8000`, Web은 `127.0.0.1:4173`로 실행된다.
- [x] `curl localhost:8000/healthz`가 `{"status":"ok"}`를 반환한다. (로컬 E2E 확인, 2026-06-16)
- [x] Temporal worker 연결은 MVP core 구현 완료 조건에서 제외하고 future/deferred로 재분류했다. 현재 local MVP는 synchronous job과 `worker:stream-archive` entrypoint로 검증한다. ([docs/mvp-scope.md](./docs/mvp-scope.md), [Implementation Status](./docs/implementation-status.md#still-targeted-not-yet-implemented))
- [x] Web이 API health 상태를 화면에 표시한다. (`apps/web/index.html`, `#healthBtn`, `#statusText`; 최신 로컬 E2E 확인)
- [x] CI에서 `ruff`, `mypy` 또는 `pyright`, `pytest`가 성공한다. ([VERIFY-STATIC](./docs/sprint-evidence-ledger.md#verify-static), 최신 `main` Foundry-lite CI)

**Demo / Proof**

`pnpm dev` 실행 후 Web Home에서 API/Worker/DB/Storage 상태가 green으로 표시된다.

**이러면 성공으로 치지 않는다**

- 개발자가 로컬에서 별도 수동 설정 없이 실행하지 못한다.
- API와 Worker가 서로 다른 config 체계를 쓴다.
- 패키지 간 의존 방향이 무너져 core가 app layer를 import한다.

---

### Sprint 02 — DB 마이그레이션·테넌트·인증 Stub·감사 골격

**Phase:** Scaffold

**문서 연결:** [Monorepo 구조](./foundry_lite_development_plan_ko_sprintified.md#18-monorepo-구조), [시스템 아키텍처](./foundry_lite_development_plan_ko_sprintified.md#4-시스템-아키텍처)

**무조건 성공시켜야 하는 Goal**

모든 리소스가 `tenant_id`, `actor_user_id`, `request_id`를 기준으로 기록되는 최소 control plane 기반을 만든다. 이 스프린트의 목적은 보안 완전체가 아니라, 나중에 보안·감사·멀티테넌시를 끼워 넣을 수 있는 데이터 모양을 처음부터 고정하는 것이다.

**반드시 완성해야 하는 것**

- Alembic baseline migration을 선택하고 API/Worker가 같은 schema source-of-truth를 사용한다.
- `tenants`, `users`, `teams`, `roles`, `user_roles` 최소 테이블을 만든다.
- 개발용 auth stub을 만들어 `x-user-id`, `x-tenant-id`로 context를 주입한다.
- API request middleware에서 request id/correlation id를 생성한다.
- `audit_events` 테이블과 `audit.write()` 공통 함수를 만든다.
- 모든 새 테이블에는 `tenant_id` 또는 명시적 전역 테이블 여부를 표시한다.

**Acceptance Gate**

- [x] Alembic baseline migration parity는 현재 증거가 있고, multi-step migration history/rollback operations는 MVP core에서 future/deferred로 재분류했다. 현재는 SQLAlchemy schema bootstrap + schema revision guard + Alembic fresh-DB parity guard로 DB shape drift를 차단한다. ([docs/mvp-scope.md](./docs/mvp-scope.md), [VERIFY-STATIC](./docs/sprint-evidence-ledger.md#verify-static))
- [x] 개발용 seed tenant/user가 생성된다. (`SupplyChainDemo`, demo admin context, [VERIFY-FULL-CI-GATE](./docs/sprint-evidence-ledger.md#verify-full-ci-gate))
- [x] 인증 없는 local/demo 요청은 명시적 local/demo auth profile 또는 header-trust context로 tenant/user에 귀속되며, production profile에서는 header-trust/demo auth가 startup에서 거부된다. ([VERIFY-PRODUCTION-AUTH-GUARD](./docs/sprint-evidence-ledger.md#verify-production-auth-guard))
- [x] mutation 테스트/API 호출은 audit row를 남긴다. ([VERIFY-FULL-CI-GATE](./docs/sprint-evidence-ledger.md#verify-full-ci-gate), [VERIFY-ACTION-COMMIT-ATOMICITY](./docs/sprint-evidence-ledger.md#verify-action-commit-atomicity))
- [x] API 로그, audit/error evidence, trace span은 request id/run id 계열 correlation key를 보존한다. ([VERIFY-STATIC](./docs/sprint-evidence-ledger.md#verify-static), [VERIFY-FULL-CI-GATE](./docs/sprint-evidence-ledger.md#verify-full-ci-gate))

**Demo / Proof**

`flite dev seed` 후 `/api/me`가 tenant/user/roles를 반환하고, 호출 기록이 `audit_events`에 남는다.

**이러면 성공으로 치지 않는다**

- 도메인 테이블에 tenant_id가 빠진다.
- audit가 각 모듈에서 제각각 구현된다.
- request id가 API, Worker, audit 사이에서 이어지지 않는다.

---

### Sprint 02A — Scale Foundation·Infra Swap Boundary

**Phase:** Foundation

**문서 연결:** [v1 adapter boundary](./foundry_lite_development_plan_ko_sprintified.md#35-v1-adapter-boundary), [시스템 아키텍처](./foundry_lite_development_plan_ko_sprintified.md#4-시스템-아키텍처), [Python 백엔드 엔지니어링 가이드](./foundry_lite_python_engineering_guidelines_ko.md#42-디자인-패턴-적용-원칙)

**무조건 성공시켜야 하는 Goal**

초기 MVP가 작더라도 나중에 팔란티어급 대용량 데이터, Spark/Flink/Kafka/S3/Iceberg/Elasticsearch 같은 인프라로 확장할 수 있게 core 제품 로직과 concrete infrastructure를 분리한다. 이 스프린트의 목표는 대형 인프라를 지금 모두 붙이는 것이 아니라, 나중에 갈아끼울 때 유지보수 비용과 추적 손실이 폭발하지 않도록 port/interface, adapter contract, composition root, trace key, contract test를 먼저 고정하는 것이다.

비개발자 관점으로 말하면, Foundry-lite의 업무 규칙은 “운영 매뉴얼”이고 인프라는 “장비”다. 장비가 작은 장비에서 공장 장비로 바뀌어도 운영 매뉴얼, 품질 검사, 사고 추적표는 유지되어야 한다.

**반드시 완성해야 하는 것**

- Infra Swap Readiness Matrix를 작성한다: boundary, local implementation, scale implementation, product contract, trace key, owner를 명시한다.
- `MetadataRepository`, `DatasetStorageAdapter`, `DatasetTransactionRepository`, `DatasetVersionRepository`, `RuntimeRepository`, `ComputeAdapter`, `StreamAdapter/EventPublisher`, `SearchAdapter`, `WorkflowAdapter`, `ConnectorAdapter`, `AuthProvider` boundary를 정의한다.
- application service가 concrete infra SDK를 직접 import하지 않고 port/interface를 통하도록 하는 의존성 규칙을 정한다.
- concrete 구현 선택은 `apps/api`, `apps/worker`, `apps/cli` 같은 composition root에서만 하도록 한다.
- local adapter와 fake adapter가 같은 contract test를 통과하는 구조를 만든다.
- adapter error contract를 정의한다: retryable 여부, timeout 여부, idempotency key, external reference, raw error masking, operator-facing message.
- 모든 boundary에 trace key contract를 둔다: `tenant_id`, `request_id`, `run_id`, `correlation_id`, domain id, cursor/checkpoint 중 필요한 값을 잃지 않는다.
- CI gate에 architecture import rule을 둔다. domain/application이 금지된 concrete infra SDK를 직접 import하면 실패해야 한다.
- 최소 하나의 swap rehearsal test를 만든다. 예: local filesystem storage 대신 fake/S3-compatible adapter를 끼워도 같은 dataset commit use case가 통과한다.
- 구현 현황 문서에 “정의된 boundary”와 “아직 local-only인 implementation”을 분리해서 기록한다.

**Acceptance Gate**

- [x] Infra Swap Readiness Matrix가 문서에 있고, 각 boundary의 local/scale 구현 후보와 trace key가 적혀 있다. ([S02A-A1](./docs/sprint-evidence-ledger.md#s02a-a1))
- [x] core use case가 infra를 직접 부르지 않고 port/interface를 통해 호출한다는 architecture rule이 있다. ([S02A-A2](./docs/sprint-evidence-ledger.md#s02a-a2))
- [x] fake adapter contract test와 local adapter contract test가 같은 테스트 시나리오를 공유한다. ([S02A-A3](./docs/sprint-evidence-ledger.md#s02a-a3))
- [x] adapter를 하나 교체해도 application service public API와 product response shape이 바뀌지 않는다. ([S02A-A4](./docs/sprint-evidence-ledger.md#s02a-a4))
- [x] adapter 실패가 run/audit/outbox/diagnostics 중 적절한 곳에 추적 가능한 error type과 correlation id로 남는다. ([S02A-A5](./docs/sprint-evidence-ledger.md#s02a-a5))
- [x] CI가 금지 import 또는 boundary 우회를 잡는다. ([S02A-A6](./docs/sprint-evidence-ledger.md#s02a-a6))

**Sprint 02A 구현 진행 체크**

- [x] `DatasetStorageAdapter` port를 추가하고 dataset staging/manifest/version file 접근을 adapter 경계로 이동했다. ([S02A-P1](./docs/sprint-evidence-ledger.md#s02a-p1))
- [x] `LocalDatasetStorageAdapter`와 `FakeDatasetStorageAdapter`를 추가했다. fake profile은 파일은 local에 두되 `fake-storage://...` logical URI를 노출해 S3류 object storage 교체 감각을 검증한다. ([S02A-P1](./docs/sprint-evidence-ledger.md#s02a-p1))
- [x] API와 CLI가 `create_local_core_dependencies(...)` composition root에서 adapter profile을 선택한다. ([S02A-P4](./docs/sprint-evidence-ledger.md#s02a-p4))
- [x] `tests/contracts/test_dataset_storage_adapter_contract.py`가 local/fake adapter에 같은 contract test를 적용한다. ([S02A-A3](./docs/sprint-evidence-ledger.md#s02a-a3))
- [x] `tests/integration/test_scale_foundation.py`가 `fake-storage` profile로 CSV commit, inspect, preview public API가 유지되는지 검증한다. ([S02A-A4](./docs/sprint-evidence-ledger.md#s02a-a4))
- [x] `scripts/quality/check_infra_import_boundary.py`를 application baseline `0`으로 CI와 로컬 `pnpm quality:infra-boundaries`에 연결했다. ([S02A-P3](./docs/sprint-evidence-ledger.md#s02a-p3))
- [x] `check_service_dependencies.py`, `check_service_call_graph.py`를 CI와 로컬 `pnpm quality:infra-boundaries`에 연결했다. 과거 flat method registry용 service method conflict gate는 registry 제거와 함께 폐기했다. ([S02A-P4](./docs/sprint-evidence-ledger.md#s02a-p4))
- [x] application concrete infra import baseline을 `37`에서 `32`로 낮췄다. ([S02A-P3](./docs/sprint-evidence-ledger.md#s02a-p3))
- [x] core bootstrap/reset DB write를 `MetadataRepository` port로 이동했다. ([S02A-P1](./docs/sprint-evidence-ledger.md#s02a-p1))
- [x] dataset registry create/find DB read/write를 `DatasetRepository` port로 이동하고 local/fake contract test를 추가했다. ([S02A-P1](./docs/sprint-evidence-ledger.md#s02a-p1))
- [x] Dataset transaction DB state change와 run failure update를 `DatasetTransactionRepository` port로 이동하고 local/fake contract test를 추가했다. ([S02A-P1](./docs/sprint-evidence-ledger.md#s02a-p1))
- [x] Dataset committed version/schema DB read를 `DatasetVersionRepository` port로 이동하고 local/fake contract test를 추가했다. ([S02A-P1](./docs/sprint-evidence-ledger.md#s02a-p1))
- [x] Audit/outbox/lineage/list-runs DB 경계를 `RuntimeRepository` port로 이동하고 local/fake contract test를 추가했다. ([S02A-P1](./docs/sprint-evidence-ledger.md#s02a-p1))
- [x] CSV/Parquet/SQL transform/health-check 실행 경계를 `ComputeAdapter` port로 이동하고 DuckDB/fake contract test를 추가했다. ([S02A-P1](./docs/sprint-evidence-ledger.md#s02a-p1))
- [x] Object record/link read DB 경계를 `ObjectReadRepository` port로 이동하고 local/fake contract test를 추가했다. ([S02A-P1](./docs/sprint-evidence-ledger.md#s02a-p1))
- [x] Object index run/object record conflict/link write DB 경계를 `ObjectIndexRepository` port로 이동하고 local/fake contract test를 추가했다. ([S02A-P1](./docs/sprint-evidence-ledger.md#s02a-p1))
- [x] Object set row/membership metadata DB 경계를 `ObjectSetRepository` port로 이동하고 local/fake contract test를 추가했다. ([S02A-P1](./docs/sprint-evidence-ledger.md#s02a-p1))
- [x] Action run/writeback/object edit/object target DB 경계를 `ActionRepository` port로 이동하고 local/fake contract test를 추가했다. ([S02A-P1](./docs/sprint-evidence-ledger.md#s02a-p1))
- [x] Ontology version/object/property/link/action type metadata DB 경계를 `OntologyRepository` port로 이동하고 local/fake contract test를 추가했다. ([S02A-P1](./docs/sprint-evidence-ledger.md#s02a-p1))
- [x] application concrete infra import baseline을 `32`에서 `30`으로 낮췄다. ([S02A-P3](./docs/sprint-evidence-ledger.md#s02a-p3))
- [x] application concrete infra import baseline을 `30`에서 `28`로 낮췄다. ([S02A-P3](./docs/sprint-evidence-ledger.md#s02a-p3))
- [x] application concrete infra import baseline을 `28`에서 `25`로 낮췄다. ([S02A-P3](./docs/sprint-evidence-ledger.md#s02a-p3))
- [x] application concrete infra import baseline을 `25`에서 `20`으로 낮췄다. ([S02A-P3](./docs/sprint-evidence-ledger.md#s02a-p3))
- [x] application concrete infra import baseline을 `20`에서 `15`로 낮췄다. ([S02A-P3](./docs/sprint-evidence-ledger.md#s02a-p3))
- [x] application concrete infra import baseline을 `15`에서 `13`으로 낮췄다. ([S02A-P3](./docs/sprint-evidence-ledger.md#s02a-p3))
- [x] application concrete infra import baseline을 `13`에서 `11`로 낮췄다. ([S02A-P3](./docs/sprint-evidence-ledger.md#s02a-p3))
- [x] application concrete infra import baseline을 `11`에서 `9`로 낮췄다. ([S02A-P3](./docs/sprint-evidence-ledger.md#s02a-p3))
- [x] application concrete infra import baseline을 `9`에서 `7`로 낮췄다. ([S02A-P3](./docs/sprint-evidence-ledger.md#s02a-p3))
- [x] application concrete infra import baseline을 `7`에서 `0`으로 낮췄다. ([S02A-P3](./docs/sprint-evidence-ledger.md#s02a-p3))
- [x] PostgreSQL repository contract testcontainer 축은 로컬 opt-out만 허용하고, `pnpm ci:gate`에서는 `FOUNDRY_LITE_SKIP_POSTGRES_CONTRACTS=1`을 실패 처리한다. ([S02A-P5](./docs/sprint-evidence-ledger.md#s02a-p5))
- [x] `FoundryLiteCore`의 facade-level service multiple inheritance를 제거하고 `CoreServices` constructor-injected service graph로 전환했다. Public API forwarder는 유지하되, flat method registry와 `__getattr__`/`__setattr__` private helper delegation bridge는 제거했다. ([S02A-P4](./docs/sprint-evidence-ledger.md#s02a-p4))
- [x] 각 application service가 직접 쓰는 `CoreDependencies` 필드만 `required_dependencies`로 선언하고 주입받게 했다. `check_service_dependencies.py`는 선언 누락과 불필요 선언을 모두 실패 처리한다. ([S02A-P4](./docs/sprint-evidence-ledger.md#s02a-p4))
- [x] 각 application service가 직접 호출하는 collaborator만 `required_collaborators`로 선언하고 주입받게 했다. `check_service_dependencies.py`는 undeclared/unused collaborator도 실패 처리한다. ([S02A-P4](./docs/sprint-evidence-ledger.md#s02a-p4))
- [x] service 내부 cross-service 호출을 `self.runtime_service._audit(...)`처럼 명시적 collaborator attribute로 바꿨다. `check_service_call_graph.py`는 이 명시적 호출 그래프를 기준으로 cycle/depth/fan-out을 검증한다. ([S02A-P4](./docs/sprint-evidence-ledger.md#s02a-p4))
- [x] Event/Search/Workflow/Connector/Auth boundary에도 fake/local contract test를 붙였다: `test_stream_adapter_contract.py`, `test_search_adapter_contract.py`, `test_workflow_adapter_contract.py`, `test_connector_adapter_contract.py`, `test_auth_provider_contract.py`. ([S02A-P2](./docs/sprint-evidence-ledger.md#s02a-p2))
- [x] adapter failure contract는 trace key, FAILED mutation state, retryability/timeout/idempotency/operator message taxonomy를 모든 현재 adapter profile에 표준화한다. ([S02A-O1](./docs/sprint-evidence-ledger.md#s02a-o1))

**Demo / Proof**

`flite --adapter-profile fake-storage demo run-supply-chain`처럼 adapter profile만 바꾸거나, 테스트에서 composition root만 바꿔 같은 dataset commit 또는 transform use case가 통과하는 것을 증명한다.

**이러면 성공으로 치지 않는다**

- interface 파일만 만들고 실제 service는 여전히 SQLite/file/DuckDB/Spark/Kafka SDK를 직접 호출한다.
- adapter 교체 테스트 없이 “나중에 교체 가능”이라고 문서에만 적는다.
- Spark, Kafka, S3 같은 대형 도구를 붙였지만 dataset transaction, lineage, audit, replay contract가 깨진다.
- adapter가 외부 실패를 숨기거나 모든 실패를 같은 generic error로 반환한다.
- vendor-specific 필드가 core DTO 안쪽으로 흘러들어 다른 구현체를 막는다.

---

### Sprint 03 — Dataset 논리 자산 CRUD

**Phase:** Dataset Registry

**문서 연결:** [데이터 저장 계층 설계](./foundry_lite_development_plan_ko_sprintified.md#5-데이터-저장-계층-설계), [Data as Code 근거](./deep-research-report.md#엔지니어링-프랙티스)

**무조건 성공시켜야 하는 Goal**

Dataset을 단순 파일 업로드가 아니라 namespace/name/schema/storage_kind를 가진 논리 자산으로 등록한다. 아직 파일 commit은 하지 않지만, 이후 transaction과 lineage가 붙을 안정적인 asset registry를 완성한다.

**반드시 완성해야 하는 것**

- `datasets` 테이블과 repository/service/API를 구현한다.
- namespace/name unique 제약을 tenant 단위로 적용한다.
- dataset 생성, 목록, 상세, 수정, soft delete 또는 archived status를 구현한다.
- classification, owner_team, description, storage_kind 필드를 받는다.
- Dataset API의 Zod schema와 OpenAPI 또는 typed route 계약을 만든다.
- CLI `flite dataset create/list/get`을 만든다.

**Acceptance Gate**

- [x] 같은 tenant 안에서 같은 namespace/name 중복 생성이 실패한다. (`DatasetRepository` unique contract, [VERIFY-FULL-CI-GATE](./docs/sprint-evidence-ledger.md#verify-full-ci-gate))
- [x] 다른 tenant에서는 같은 namespace/name을 만들 수 있다. (`DatasetRepository` tenant-scoped contract, [VERIFY-FULL-CI-GATE](./docs/sprint-evidence-ledger.md#verify-full-ci-gate))
- [x] Dataset 상세/inspect path는 committed version이 없거나 storage edge가 있을 때도 operator-facing payload/error로 응답한다. ([VERIFY-TRICKY-FAILURE-FOCUSED](./docs/sprint-evidence-ledger.md#verify-tricky-failure-focused))
- [x] Dataset mutation은 audit/outbox/transaction evidence와 연결된다. ([VERIFY-FULL-CI-GATE](./docs/sprint-evidence-ledger.md#verify-full-ci-gate), G11/G18 gates)
- [x] unit/contract/integration test가 repository와 API/CLI 경로를 검증한다. ([VERIFY-STATIC](./docs/sprint-evidence-ledger.md#verify-static), [VERIFY-FULL-CI-GATE](./docs/sprint-evidence-ledger.md#verify-full-ci-gate))

**Demo / Proof**

`flite dataset create raw.erp_orders --storage parquet_manifest` 후 Web Dataset 목록에서 확인한다.

**이러면 성공으로 치지 않는다**

- Dataset이 파일 경로 문자열만으로 취급된다.
- namespace/name 충돌 제약이 없다.
- 테넌트 격리가 테스트되지 않는다.

---

### Sprint 04 — Dataset Transaction 상태 머신

**Phase:** Dataset Registry

**문서 연결:** [데이터 저장 계층 설계](./foundry_lite_development_plan_ko_sprintified.md#5-데이터-저장-계층-설계), [Data as Code 근거](./deep-research-report.md#엔지니어링-프랙티스)

**무조건 성공시켜야 하는 Goal**

Dataset 변경을 반드시 `OPEN → COMMITTED | ABORTED` transaction으로만 일어나게 만든다. 이 스프린트가 성공해야 이후 CSV ingest, transform output, materialization output이 모두 같은 commit contract를 쓴다.

**반드시 완성해야 하는 것**

- `dataset_transactions`, `dataset_versions`, `dataset_files` 테이블을 구현한다.
- SNAPSHOT, APPEND transaction type을 지원한다.
- `openTransaction`, `attachFile`, `commit`, `abort` service 함수를 만든다.
- COMMITTED version은 immutable하다는 DB/service guard를 둔다.
- version_number 증가 규칙을 tenant/dataset/branch 단위로 고정한다.
- abort된 transaction은 version을 만들지 못하게 한다.

**Acceptance Gate**

- [x] OPEN transaction에만 file을 attach할 수 있다. (`DatasetTransactionRepository` contract, [VERIFY-TRICKY-FAILURE-FOCUSED](./docs/sprint-evidence-ledger.md#verify-tricky-failure-focused))
- [x] COMMITTED transaction을 다시 commit하거나 abort할 수 없다. (`DatasetTransactionRepository` contract, [VERIFY-TRICKY-FAILURE-FOCUSED](./docs/sprint-evidence-ledger.md#verify-tricky-failure-focused))
- [x] COMMITTED version의 file 목록을 수정하지 않고 새 transaction/version으로만 기록한다. ([S05-A4](./docs/sprint-evidence-ledger.md#s05-a4), [VERIFY-DATASET-SAME-CONTENT-REATTACH](./docs/sprint-evidence-ledger.md#verify-dataset-same-content-reattach))
- [x] SNAPSHOT commit은 새 version을 HEAD/latest로 만든다. ([MVP-RAW](./docs/sprint-evidence-ledger.md#mvp-core-raw-dataset))
- [x] 동시 commit 시 version_number 충돌이 생기지 않는다. ([VERIFY-DATASET-VERSION-CONCURRENCY](./docs/sprint-evidence-ledger.md#verify-dataset-version-concurrency), [S36A-A2](./docs/sprint-evidence-ledger.md#s36a-a2))

**Demo / Proof**

`flite dataset tx open/commit/abort`로 transaction 상태 전이를 보여준다.

**이러면 성공으로 치지 않는다**

- 파일 업로드가 transaction 없이 dataset_version을 직접 만든다.
- commit된 version의 manifest/file row가 수정 가능하다.
- 실패한 transaction의 staging file 정리 정책이 없다.

---

### Sprint 05 — StorageAdapter와 Manifest Commit Protocol

**Phase:** Dataset Registry

**문서 연결:** [데이터 저장 계층 설계](./foundry_lite_development_plan_ko_sprintified.md#5-데이터-저장-계층-설계), [Data as Code 근거](./deep-research-report.md#엔지니어링-프랙티스)

**무조건 성공시켜야 하는 Goal**

S3/MinIO 같은 object storage에서 원자적 rename에 의존하지 않고, staging path와 manifest pointer를 통해 안전하게 dataset version을 commit한다. 이후 모든 writer는 이 commit protocol을 재사용해야 한다.

**반드시 완성해야 하는 것**

- `StorageAdapter` interface를 정의한다: put, get, list, delete, stat, signedUrl optional.
- MinIO adapter를 구현한다.
- staging path 규칙을 만든다: `_staging/{transaction_id}/...`.
- version manifest JSON 포맷을 정의한다.
- commit 시 file metadata, content_hash, row_count, byte_size를 기록한다.
- abort/failed transaction cleanup command를 만든다.

**Acceptance Gate**

- [x] 파일은 먼저 staging에 쓰이고 commit 후 manifest에 의해 version에 귀속된다. ([S05-A1](./docs/sprint-evidence-ledger.md#s05-a1))
- [x] manifest_uri만으로 dataset version의 파일 목록을 복원할 수 있다. ([S05-A2](./docs/sprint-evidence-ledger.md#s05-a2))
- [x] 중간 실패 후 재시도해도 중복 version이 생기지 않는다. ([S05-A3](./docs/sprint-evidence-ledger.md#s05-a3))
- [x] content_hash가 같은 파일 재첨부 정책이 명확하다. 같은 내용도 새 transaction/version으로 기록하고 기존 committed version을 dedupe/수정하지 않는다. ([S05-A4](./docs/sprint-evidence-ledger.md#s05-a4))
- [x] MinIO 없이 mocked adapter로 unit test가 돈다. ([S05-A5](./docs/sprint-evidence-ledger.md#s05-a5))

**Demo / Proof**

transaction을 열고 staging에 파일을 쓴 뒤 commit하면 manifest가 생성되고 version 상세에서 확인된다.

**이러면 성공으로 치지 않는다**

- S3 rename을 원자적 commit처럼 가정한다.
- manifest 없이 DB row만 보고 파일을 신뢰한다.
- abort cleanup이 수동 운영 절차로만 남는다.

---

### Sprint 06 — CSV Upload → Raw Dataset Commit

**Phase:** Dataset Registry

**문서 연결:** [데이터 저장 계층 설계](./foundry_lite_development_plan_ko_sprintified.md#5-데이터-저장-계층-설계), [Data as Code 근거](./deep-research-report.md#엔지니어링-프랙티스)

**무조건 성공시켜야 하는 Goal**

사용자가 CSV 파일 하나를 업로드하면 Foundry-lite의 공식 dataset transaction protocol을 거쳐 `raw.*` dataset의 committed version이 만들어진다. 이때 첫 번째 실제 데이터 유입 경로가 완성된다.

**반드시 완성해야 하는 것**

- multipart upload API 또는 CLI file upload를 구현한다.
- CSV reader로 header, delimiter, encoding 기본 처리를 한다.
- CSV를 Parquet으로 변환해 staging에 쓴다.
- row_count, byte_size, content_hash를 계산한다.
- SNAPSHOT transaction으로 committed version을 만든다.
- upload run 상태를 `sync_runs` 또는 `ingest_runs`로 기록한다.

**Acceptance Gate**

- [x] `examples/supply-chain-demo/data/orders.csv` 업로드 후 `raw.erp_orders` latest version이 생긴다. ([MVP-RAW](./docs/sprint-evidence-ledger.md#mvp-core-raw-dataset), [VERIFY-FULL-CI-GATE](./docs/sprint-evidence-ledger.md#verify-full-ci-gate))
- [x] 업로드 실패 시 transaction은 ABORTED가 되고 committed version이 생기지 않는다. ([VERIFY-TRICKY-FAILURE-FOCUSED](./docs/sprint-evidence-ledger.md#verify-tricky-failure-focused), [VERIFY-FULL-CI-GATE](./docs/sprint-evidence-ledger.md#verify-full-ci-gate))
- [x] 빈 CSV, header/schema mismatch, invalid CSV edge는 validation/storage/compute error payload로 사용자에게 명확히 반환된다. ([VERIFY-TRICKY-FAILURE-FOCUSED](./docs/sprint-evidence-ledger.md#verify-tricky-failure-focused), [VERIFY-CSV-PK-STRING-PRESERVATION](./docs/sprint-evidence-ledger.md#verify-csv-pk-string-preservation))
- [x] 업로드 결과가 Dataset UI/CLI에서 version_number, row_count와 함께 보인다. ([S22-A1](./docs/sprint-evidence-ledger.md#s22-a1), [MVP-RAW](./docs/sprint-evidence-ledger.md#mvp-core-raw-dataset))
- [x] 동일 파일 재업로드 시 새 SNAPSHOT version이 생기며 이전 version은 immutable하게 남는다. ([VERIFY-DATASET-SAME-CONTENT-REATTACH](./docs/sprint-evidence-ledger.md#verify-dataset-same-content-reattach))

**Demo / Proof**

`flite dataset upload raw.erp_orders examples/orders.csv` → `flite dataset versions raw.erp_orders`.

**이러면 성공으로 치지 않는다**

- CSV가 DB 테이블에 직접 적재되고 dataset file/manifest가 없다.
- 실패한 업로드가 partial committed version을 남긴다.
- row_count나 schema 없이 파일만 저장된다.

---

### Sprint 07 — Schema Registry·Compatibility·Preview

**Phase:** Dataset Registry

**문서 연결:** [데이터 저장 계층 설계](./foundry_lite_development_plan_ko_sprintified.md#5-데이터-저장-계층-설계), [Data as Code 근거](./deep-research-report.md#엔지니어링-프랙티스)

**무조건 성공시켜야 하는 Goal**

Dataset version마다 schema contract가 기록되고, 사용자가 최신 version을 preview할 수 있게 한다. Ontology와 transform은 이 schema contract에 의존하므로, schema drift를 조기에 감지하는 기반을 만든다.

**반드시 완성해야 하는 것**

- CSV/Parquet에서 schema를 infer한다.
- `dataset_schemas`에 schema_json, schema_hash, version을 저장한다.
- nullable column 추가, column 삭제, type 변경 등 compatibility 판정 함수를 만든다.
- DuckDB 기반 dataset preview API를 만든다.
- preview는 limit, selected columns, latest/specific version을 지원한다.
- schema view UI를 만든다.

**Acceptance Gate**

- [x] 같은 schema의 새 upload는 동일 schema hash/schema version evidence로 인식된다. ([VERIFY-FULL-CI-GATE](./docs/sprint-evidence-ledger.md#verify-full-ci-gate), schema revision/schema compatibility gates)
- [x] breaking schema change는 commit/finalization 전에 warning/error로 잡힌다. ([VERIFY-SCHEMA-COMPATIBILITY-TOCTOU](./docs/sprint-evidence-ledger.md#verify-schema-compatibility-toctou))
- [x] `GET /api/datasets/{namespace}/{name}/preview?limit=...`가 latest rows를 반환한다. (`apps/api/foundry_lite_api/main.py`, 최신 로컬 E2E 확인)
- [x] Preview는 object storage의 committed file/manifest를 읽고 DB에 별도 적재하지 않는다. ([VERIFY-DATASET-STORAGE-SPLIT-BRAIN](./docs/sprint-evidence-ledger.md#verify-dataset-storage-split-brain))
- [x] schema compatibility unit test가 주요 케이스를 커버한다. ([VERIFY-SCHEMA-COMPATIBILITY-TOCTOU](./docs/sprint-evidence-ledger.md#verify-schema-compatibility-toctou))

**Demo / Proof**

Dataset UI에서 `raw.erp_orders` schema와 preview를 확인하고, breaking schema CSV 업로드가 차단되는 것을 보여준다.

**이러면 성공으로 치지 않는다**

- schema가 dataset 전체에 하나만 있고 version별 schema가 없다.
- preview를 위해 전체 데이터를 DB에 복사한다.
- breaking change와 compatible change를 구분하지 않는다.

---

### Sprint 08 — Health Checks와 Commit 차단

**Phase:** Dataset Registry

**문서 연결:** [데이터 저장 계층 설계](./foundry_lite_development_plan_ko_sprintified.md#5-데이터-저장-계층-설계), [Data as Code 근거](./deep-research-report.md#엔지니어링-프랙티스)

**무조건 성공시켜야 하는 Goal**

Dataset이 형식상 commit되는 것만으로 성공으로 보지 않고, primary key uniqueness, not-null, row count 같은 품질 조건을 통과해야 downstream build로 넘어갈 수 있게 한다.

**반드시 완성해야 하는 것**

- `dataset_checks`, `dataset_check_results` 테이블을 구현한다.
- not_null, unique, row_count_min/max, custom_sql 최소 check를 지원한다.
- commit 전 staging/version candidate에 check를 실행한다. `VERIFY-DATASET-HEALTH-CANDIDATE`가 latest가 아니라 후보 파일을 검사함을 증명한다.
- severity `error` check 실패 시 transaction을 abort한다.
- warning check는 commit은 허용하되 결과에 표시한다.
- Dataset UI에 check 결과를 표시한다.

**Acceptance Gate**

- [x] `order_id` unique check 실패 CSV는 committed version을 만들지 않는다. 증거: `tests/integration/test_dataset_quality.py::test_dataset_health_check_reads_candidate_not_latest`, `VERIFY-DATASET-HEALTH-CANDIDATE`.
- [x] not_null/unique/check failure details는 run/transaction error payload로 추적된다. ([VERIFY-DATASET-HEALTH-CANDIDATE](./docs/sprint-evidence-ledger.md#verify-dataset-health-candidate), [VERIFY-FULL-CI-GATE](./docs/sprint-evidence-ledger.md#verify-full-ci-gate))
- [x] check 결과가 run_id와 transaction_id로 추적된다. ([VERIFY-DATASET-HEALTH-CANDIDATE](./docs/sprint-evidence-ledger.md#verify-dataset-health-candidate))
- [x] 사용자는 어떤 check가 실패했는지 API/CLI/Operations error payload에서 볼 수 있다. ([S33-A5](./docs/sprint-evidence-ledger.md#s33-a5), [VERIFY-TRICKY-FAILURE-FOCUSED](./docs/sprint-evidence-ledger.md#verify-tricky-failure-focused))
- [x] Transform output은 dataset finalization/commit boundary와 같은 failure cleanup protocol을 사용한다. ([VERIFY-TRANSFORM-LINEAGE-ATOMIC](./docs/sprint-evidence-ledger.md#verify-transform-lineage-atomic), [VERIFY-TRANSFORM-OOM-STAGING-CLEANUP](./docs/sprint-evidence-ledger.md#verify-transform-oom-staging-cleanup))

**Demo / Proof**

중복 order_id 파일 업로드 → validation failed → transaction ABORTED → Dataset UI에서 실패 원인 확인.

**이러면 성공으로 치지 않는다**

- check 실패 후에도 committed version이 생성된다.
- check 결과가 로그에만 있고 DB에 남지 않는다.
- check runner가 upload 전용으로만 구현되어 transform에서 재사용할 수 없다.

---

### Sprint 09 — Source·Sync·Run Framework

**Phase:** Data Connection-lite

**문서 연결:** [Data Connection-lite 설계](./foundry_lite_development_plan_ko_sprintified.md#6-data-connection-lite-설계), [Foundry 데이터 흐름 근거](./deep-research-report.md#종단간-데이터-흐름)

**무조건 성공시켜야 하는 Goal**

CSV upload를 특수 endpoint가 아니라 Data Connection-lite의 한 sync 유형으로 일반화한다. 이후 PostgreSQL, REST, Webhook, CDC가 같은 run lifecycle과 dataset transaction protocol을 사용하게 만든다.

**반드시 완성해야 하는 것**

- `sources`, `syncs`, `sync_runs` 테이블을 만든다.
- Connector interface를 Python `Protocol`로 정의한다.
- sync run lifecycle을 `CREATED → PLANNING → EXTRACTING → WRITING_TRANSACTION → VALIDATING → COMMITTING → COMMITTED | FAILED | ABORTED`로 구현한다.
- CSV/File connector를 기존 upload flow 위에 얹는다.
- Temporal workflow 또는 worker job으로 sync run을 실행한다.
- run logs와 error payload를 저장한다.

**Acceptance Gate**

- [x] CSV upload와 connector/file sync가 같은 Operations run surface에 보인다. ([S33-A4](./docs/sprint-evidence-ledger.md#s33-a4), [VERIFY-FULL-CI-GATE](./docs/sprint-evidence-ledger.md#verify-full-ci-gate))
- [x] sync retry/idempotency 정책은 current MVP connector/webhook/stream commit-point proof와 future connector boundary로 분리되어 있다. ([VERIFY-REST-WEBHOOK-TRICKY-EDGES](./docs/sprint-evidence-ledger.md#verify-rest-webhook-tricky-edges), [VERIFY-STREAM-REST-CURSOR-COMMIT-POINTS](./docs/sprint-evidence-ledger.md#verify-stream-rest-cursor-commit-points))
- [x] sync 실패 시 dataset transaction은 abort되거나 committed cursor를 앞당기지 않는다. ([VERIFY-TRICKY-FAILURE-FOCUSED](./docs/sprint-evidence-ledger.md#verify-tricky-failure-focused))
- [x] run 상세에서 source, output dataset, transaction id, committed version id를 조사할 수 있다. ([S33-A5](./docs/sprint-evidence-ledger.md#s33-a5))
- [x] 새 connector를 추가할 때 core dataset code를 수정하지 않도록 connector adapter boundary가 고정되어 있다. ([S02A-P2](./docs/sprint-evidence-ledger.md#s02a-p2), [S37-A1](./docs/sprint-evidence-ledger.md#s37-a1))

**Demo / Proof**

Sources UI에서 file source를 만들고 `sync_orders`를 실행해 raw dataset version을 만든다.

**이러면 성공으로 치지 않는다**

- CSV upload와 sync framework가 분리된 별도 경로로 남는다.
- sync run이 dataset transaction과 연결되지 않는다.
- connector별로 run 상태 문자열이 제각각이다.

---

### Sprint 10 — PostgreSQL Snapshot Connector

**Phase:** Data Connection-lite

**문서 연결:** [Data Connection-lite 설계](./foundry_lite_development_plan_ko_sprintified.md#6-data-connection-lite-설계), [Foundry 데이터 흐름 근거](./deep-research-report.md#종단간-데이터-흐름)

**무조건 성공시켜야 하는 Goal**

외부 운영 DB에서 query snapshot을 가져와 raw dataset으로 commit한다. 이 스프린트는 파일 기반 데모를 실제 source DB sync 데모로 확장하는 첫 번째 단계다.

**반드시 완성해야 하는 것**

- PostgreSQL source config와 secretRef placeholder를 구현한다.
- testConnection API를 만든다.
- query/table locator 기반 inferSchema를 구현한다.
- snapshot query 결과를 Parquet 파일로 streaming write한다.
- row_count, schema, content_hash, transaction commit을 CSV와 같은 protocol로 처리한다.
- examples에 mock ERP Postgres container와 seed table을 추가한다.

**Acceptance Gate**

- [x] `sync_orders_pg` 데모 경로는 현재 MVP local snapshot path로 `raw.erp_orders` version을 생성한다. real PostgreSQL snapshot connector implementation은 future/deferred다. ([MVP-RAW](./docs/sprint-evidence-ledger.md#mvp-core-raw-dataset), [docs/mvp-scope.md](./docs/mvp-scope.md))
- [x] source/connector 실패는 sync run FAILED 또는 aborted transaction evidence로 남긴다. 실제 PostgreSQL source failure edge는 future connector scope로 재분류했다. ([VERIFY-REST-WEBHOOK-TRICKY-EDGES](./docs/sprint-evidence-ledger.md#verify-rest-webhook-tricky-edges), [docs/mvp-scope.md](./docs/mvp-scope.md))
- [x] large result streaming/batch PostgreSQL snapshot implementation은 MVP core에서 future/deferred로 재분류했다. 현재 MVP는 CSV/local connector snapshot과 adapter boundary를 검증한다. ([docs/mvp-scope.md](./docs/mvp-scope.md), [S02A-P2](./docs/sprint-evidence-ledger.md#s02a-p2))
- [x] query/schema breaking 차단은 현재 dataset schema compatibility/finalization guard로 검증하고, PostgreSQL query snapshot 전용 구현은 future/deferred로 둔다. ([VERIFY-SCHEMA-COMPATIBILITY-TOCTOU](./docs/sprint-evidence-ledger.md#verify-schema-compatibility-toctou))
- [x] Source UI의 PostgreSQL testConnection은 MVP core에서 future/deferred로 재분류했고, current Web은 dataset/object/operations proof를 제공한다. ([docs/mvp-scope.md](./docs/mvp-scope.md), [S22-A1](./docs/sprint-evidence-ledger.md#s22-a1))

**Demo / Proof**

mock ERP DB의 `orders` table → `raw.erp_orders` dataset SNAPSHOT commit.

**이러면 성공으로 치지 않는다**

- Postgres 결과를 API 서버 메모리에 전체 적재한다.
- secret/config가 코드에 하드코딩된다.
- Postgres sync가 CSV와 다른 commit/check 흐름을 쓴다.

---

### Sprint 11 — Transform Registry와 SQL/DuckDB Runner

**Phase:** Transform Engine

**문서 연결:** [Transform Engine 설계](./foundry_lite_development_plan_ko_sprintified.md#7-transform-engine-설계), [기술 스택 근거](./deep-research-report.md#기술-스택), [Python 백엔드 엔지니어링 가이드](./foundry_lite_python_engineering_guidelines_ko.md)

**무조건 성공시켜야 하는 Goal**

raw dataset version을 명시적으로 입력으로 묶고 SQL/DuckDB로 clean dataset을 생성할 수 있게 한다. 이 스프린트의 성공 기준은 transform이 임의 파일 경로를 읽는 것이 아니라 Dataset Registry의 committed version만 읽는 것이다.

**반드시 완성해야 하는 것**

- `transforms`, `transform_runs`, `transform_inputs`, `transform_outputs` 테이블을 만든다.
- SQL transform definition YAML/JSON을 등록한다.
- `{{ input('raw.erp_orders') }}` 템플릿을 DuckDB readable relation으로 resolve한다.
- input dataset은 latest 또는 특정 version으로 binding한다.
- output dataset transaction을 열고 SQL 결과를 Parquet으로 쓴다.
- transform run lifecycle을 구현한다.

**Acceptance Gate**

- [x] `clean_orders.sql` 실행으로 `clean.orders` committed version이 생성된다. ([MVP-TRANSFORM](./docs/sprint-evidence-ledger.md#mvp-core-transform), [VERIFY-FULL-CI-GATE](./docs/sprint-evidence-ledger.md#verify-full-ci-gate))
- [x] transform run 상세에 input dataset version id가 남는다. ([VERIFY-TRANSFORM-INPUT-PINNING](./docs/sprint-evidence-ledger.md#verify-transform-input-pinning))
- [x] input dataset이 없거나 committed version이 없으면 run이 시작되지 않거나 validation error로 실패한다. ([VERIFY-FULL-CI-GATE](./docs/sprint-evidence-ledger.md#verify-full-ci-gate))
- [x] SQL/compute 오류는 run FAILED로 기록되고 output transaction/staging artifact가 cleanup된다. ([VERIFY-TRANSFORM-OOM-STAGING-CLEANUP](./docs/sprint-evidence-ledger.md#verify-transform-oom-staging-cleanup))
- [x] 동일 input version으로 rerun/retry해도 결과 version 생성 정책이 명확하다. 성공 run retry는 duplicate output을 만들 수 없다. ([VERIFY-TRANSFORM-RETRY-NO-DUPLICATE-OUTPUT](./docs/sprint-evidence-ledger.md#verify-transform-retry-no-duplicate-output))

**Demo / Proof**

`flite transform run clean_orders_sql` → `clean.orders` version 생성 → preview 확인.

**이러면 성공으로 치지 않는다**

- SQL이 `/tmp/*.csv` 같은 파일 경로를 직접 읽는다.
- transform output이 Dataset transaction을 거치지 않는다.
- input version binding 없이 latest를 실행 시점마다 암묵적으로 읽는다.

---

### Sprint 12 — Transform Output Commit Protocol과 Lineage

**Phase:** Transform Engine

**문서 연결:** [Transform Engine 설계](./foundry_lite_development_plan_ko_sprintified.md#7-transform-engine-설계), [기술 스택 근거](./deep-research-report.md#기술-스택)

**무조건 성공시켜야 하는 Goal**

Transform이 만든 output dataset이 어떤 input dataset version에서 왔는지 lineage로 재현 가능하게 만든다. 이후 object property의 source evidence를 추적하려면 이 스프린트가 반드시 안정적이어야 한다.

**반드시 완성해야 하는 것**

- `lineage_edges` 테이블과 lineage service를 구현한다.
- transform run commit 시 input version → transform run → output version edge를 기록한다.
- output commit은 staging → check → manifest commit 순서를 따른다.
- failed run의 staging cleanup을 worker/CLI에서 실행할 수 있게 한다.
- lineage API `GET /api/lineage/resources/{resourceId}`를 만든다.
- Dataset UI에서 upstream/downstream 최소 그래프 또는 목록을 보여준다.

**Acceptance Gate**

- [x] `clean.orders` lineage 조회 시 `raw.erp_orders` input version이 보인다. ([VERIFY-TRANSFORM-INPUT-PINNING](./docs/sprint-evidence-ledger.md#verify-transform-input-pinning), OpenLineage P8)
- [x] transform run id로 input/output version을 모두 조회할 수 있다. ([VERIFY-FULL-CI-GATE](./docs/sprint-evidence-ledger.md#verify-full-ci-gate), [S33-A5](./docs/sprint-evidence-ledger.md#s33-a5))
- [x] output/lineage 실패 시 lineage edge가 committed output처럼 남지 않는다. ([VERIFY-TRANSFORM-LINEAGE-ATOMIC](./docs/sprint-evidence-ledger.md#verify-transform-lineage-atomic))
- [x] 재실행한 transform은 새 run id와 새 output version을 갖거나, 성공 run retry는 duplicate output을 만들지 않는 정책으로 차단된다. ([VERIFY-TRANSFORM-RETRY-NO-DUPLICATE-OUTPUT](./docs/sprint-evidence-ledger.md#verify-transform-retry-no-duplicate-output))
- [x] object indexing 전에 object backing dataset의 lineage/source run chain을 추적할 수 있다. ([S33-A3](./docs/sprint-evidence-ledger.md#s33-a3), [MVP-OPERATIONS](./docs/sprint-evidence-ledger.md#mvp-core-operations-replay))

**Demo / Proof**

`raw.erp_orders → clean.orders` lineage를 CLI와 UI에서 확인한다.

**이러면 성공으로 치지 않는다**

- lineage가 dataset 이름 문자열만 저장하고 version을 저장하지 않는다.
- 실패한 transform이 downstream graph에 성공 edge처럼 보인다.
- output file commit과 lineage commit이 서로 다른 transaction boundary로 불일치한다.

---

### Sprint 13 — Transform Health Gates와 Temporal 실행

**Phase:** Transform Engine

**문서 연결:** [Transform Engine 설계](./foundry_lite_development_plan_ko_sprintified.md#7-transform-engine-설계), [기술 스택 근거](./deep-research-report.md#기술-스택)

**무조건 성공시켜야 하는 Goal**

Transform output도 dataset health check를 통과해야만 성공으로 인정하고, run/retry/schedule이 Temporal worker에서 안정적으로 관리되도록 한다.

**반드시 완성해야 하는 것**

- transform definition의 checks를 dataset check runner에 연결한다.
- Temporal workflow로 transform run lifecycle을 실행한다.
- retry policy와 timeout을 transform definition에서 받는다.
- scheduled run 최소 기능을 구현한다.
- bad output일 때 downstream build를 막는 `blocked_by_check` 상태를 정의한다.
- run logs를 API/UI에서 조회 가능하게 한다.

**Acceptance Gate**

- [x] primary key uniqueness/health failure는 SUCCESS로 인정하지 않고 failed/aborted evidence로 남긴다. ([VERIFY-DATASET-HEALTH-CANDIDATE](./docs/sprint-evidence-ledger.md#verify-dataset-health-candidate), [VERIFY-FULL-CI-GATE](./docs/sprint-evidence-ledger.md#verify-full-ci-gate))
- [x] Temporal retry는 MVP core에서 future/deferred로 재분류했고, 현재 retry duplicate-output 위험은 Operations retry guard로 차단한다. ([VERIFY-TRANSFORM-RETRY-NO-DUPLICATE-OUTPUT](./docs/sprint-evidence-ledger.md#verify-transform-retry-no-duplicate-output), [docs/mvp-scope.md](./docs/mvp-scope.md))
- [x] run 상세에서 created/completed time, status, error, correlation id, related evidence를 볼 수 있다. ([S33-A5](./docs/sprint-evidence-ledger.md#s33-a5))
- [x] scheduled run/Temporal execution은 MVP core에서 future/deferred로 재분류했다. 수동/API/CLI retry path는 current Operations proof로 검증한다. ([docs/mvp-scope.md](./docs/mvp-scope.md), [S33-A1](./docs/sprint-evidence-ledger.md#s33-a1))
- [x] health/failure gate 결과는 run error, audit/outbox/lineage absence proof로 남는다. ([VERIFY-TRANSFORM-LINEAGE-ATOMIC](./docs/sprint-evidence-ledger.md#verify-transform-lineage-atomic), [VERIFY-FULL-CI-GATE](./docs/sprint-evidence-ledger.md#verify-full-ci-gate))

**Demo / Proof**

정상 SQL은 clean dataset을 만들고, 중복 key SQL은 check failure로 output commit이 abort된다.

**이러면 성공으로 치지 않는다**

- Temporal retry가 같은 run에서 여러 committed output version을 만든다.
- check 실패가 warning처럼 취급되어 downstream으로 흘러간다.
- 수동 실행과 scheduled 실행의 code path가 다르다.

---

### Sprint 14 — Python Transform SDK Prototype

**Phase:** Transform Engine

**문서 연결:** [Transform Engine 설계](./foundry_lite_development_plan_ko_sprintified.md#7-transform-engine-설계), [기술 스택 근거](./deep-research-report.md#기술-스택)

**무조건 성공시켜야 하는 Goal**

Python 사용자가 Dataset Registry의 input/output abstraction을 통해 transform을 작성할 수 있게 한다. 단, Python runner도 SQL runner와 같은 transaction, lineage, health gate를 반드시 사용해야 한다.

**반드시 완성해야 하는 것**

- `foundry_lite.transforms` Python package 골격을 만든다.
- `Input.read_polars/read_pandas`, `Output.write_polars/write_pandas` 최소 API를 만든다.
- Python runner가 input version manifest를 받아 읽도록 한다.
- Python output은 staging path로만 쓰고 API/worker가 commit한다.
- Python exception을 transform run error payload로 표준화한다.
- 예제 `clean_customers.py`를 만든다.

**Acceptance Gate**

- [x] `clean_customers.py` Python runner는 MVP core에서 future/deferred로 재분류했고, current MVP는 DuckDB SQL transform으로 `clean.customers`를 만든다. ([MVP-TRANSFORM](./docs/sprint-evidence-ledger.md#mvp-core-transform), [docs/mvp-scope.md](./docs/mvp-scope.md))
- [x] Python transform lineage/output commit은 sandboxed SDK abstraction 이전까지 fail-closed future scope로 재분류했다. SQL transform lineage/output은 현재 증명되어 있다. ([VERIFY-TRANSFORM-LINEAGE-ATOMIC](./docs/sprint-evidence-ledger.md#verify-transform-lineage-atomic), [docs/implementation-status.md](./docs/implementation-status.md))
- [x] Python transform 실패 transaction path는 future/deferred로 재분류했고, current compute failure cleanup은 DuckDB/failing compute adapter proof로 검증한다. ([VERIFY-TRANSFORM-OOM-STAGING-CLEANUP](./docs/sprint-evidence-ledger.md#verify-transform-oom-staging-cleanup))
- [x] SDK 사용자 코드가 raw storage path를 직접 받지 않는 정책은 current SQL transform guard와 Python transform fail-closed 정책으로 고정했다. ([VERIFY-SQL-TRANSFORM-FILESYSTEM-GUARD](./docs/sprint-evidence-ledger.md#verify-sql-transform-filesystem-guard), [VERIFY-PYTHON-TRANSFORM-FAIL-CLOSED](./docs/sprint-evidence-ledger.md#verify-python-transform-fail-closed))
- [x] Python transforms SDK package skeleton은 존재하며, executable Python runner unit path는 future/deferred로 재분류했다. (`libs/foundry_lite/transforms_sdk/__init__.py`, [docs/mvp-scope.md](./docs/mvp-scope.md))

**Demo / Proof**

`flite transform run clean_customers_py` → `clean.customers` version 생성 → lineage 확인.

**이러면 성공으로 치지 않는다**

- Python 코드가 MinIO credentials나 storage path를 직접 조작한다.
- Python output commit이 SQL output commit과 다르다.
- SDK가 transform run context 없이 독립 스크립트처럼만 동작한다.

---

### Sprint 15 — Ontology Draft·Object/Property YAML Import

**Phase:** Ontology

**문서 연결:** [Ontology Metadata Service-lite](./foundry_lite_development_plan_ko_sprintified.md#8-ontology-metadata-service-lite), [Ontology 근거](./deep-research-report.md#공개문서-기반-참조-아키텍처)

**무조건 성공시켜야 하는 Goal**

Dataset을 사용자에게 직접 노출하지 않고 `Order`, `Customer` 같은 object type으로 끌어올릴 수 있는 최소 ontology metadata를 만든다. 이 Sprint 11 단계에서는 indexing 전 단계로, object/property/backing mapping을 draft로 저장할 수 있어야 한다.

**반드시 완성해야 하는 것**

- `ontology_versions`, `object_types`, `property_types` 테이블을 구현한다.
- draft ontology 생성 API를 만든다.
- YAML import parser를 구현한다.
- Object Type: apiName, displayName, primaryKey, backing dataset을 저장한다.
- Property: apiName, type, nullable, indexed, searchable, editable, source, column을 저장한다.
- 기본 DSL validation error format을 만든다.

**Acceptance Gate**

- [x] `Order` object type YAML을 import/validate/apply할 수 있다. ([MVP-ONTOLOGY](./docs/sprint-evidence-ledger.md#mvp-core-ontology), [S20-A1](./docs/sprint-evidence-ledger.md#s20-a1))
- [x] 존재하지 않는 property type, 중복 apiName, primaryKey 누락은 validation error로 반환한다. ([S22-A2](./docs/sprint-evidence-ledger.md#s22-a2), [VERIFY-FULL-CI-GATE](./docs/sprint-evidence-ledger.md#verify-full-ci-gate))
- [x] draft/import validation은 active serving ontology를 바로 바꾸지 않으며, activation/apply path에서만 serving 계약이 갱신된다. ([MVP-ONTOLOGY](./docs/sprint-evidence-ledger.md#mvp-core-ontology))
- [x] Ontology API는 validate/apply/active metadata 경계를 제공하며 Web에서 YAML validation 결과를 확인할 수 있다. ([S22-A2](./docs/sprint-evidence-ledger.md#s22-a2))
- [x] YAML import 결과가 ontology/object/property/link/action type DB row로 정규화되어 저장된다. ([S20-A1](./docs/sprint-evidence-ledger.md#s20-a1), [MVP-ONTOLOGY](./docs/sprint-evidence-ledger.md#mvp-core-ontology))

**Demo / Proof**

`flite ontology draft create && flite ontology import order-customer.yaml` 후 draft object types를 조회한다.

**이러면 성공으로 치지 않는다**

- YAML을 blob으로만 저장하고 object/property 테이블로 정규화하지 않는다.
- draft 변경이 즉시 serving object query에 영향을 준다.
- apiName 중복을 허용한다.

---

### Sprint 16 — Ontology Activation Validation과 Versioning

**Phase:** Ontology

**문서 연결:** [Ontology Metadata Service-lite](./foundry_lite_development_plan_ko_sprintified.md#8-ontology-metadata-service-lite), [Ontology 근거](./deep-research-report.md#공개문서-기반-참조-아키텍처)

**무조건 성공시켜야 하는 Goal**

Ontology draft를 active로 전환할 때 backing dataset, schema, primary key, property mapping을 엄격히 검증한다. 활성화된 ontology version은 SDK/API/object indexing의 계약이므로 임의 수정되지 않아야 한다.

**반드시 완성해야 하는 것**

- `validateDraft`에서 backing dataset existence를 확인한다.
- active/latest dataset schema와 property column mapping 호환성을 확인한다.
- primary key column 존재와 nullable=false 여부를 확인한다.
- editable property는 source edit_layer 또는 edit policy가 있어야 한다.
- activation 시 기존 active version을 archived 또는 superseded로 전환한다.
- apiName immutability 정책을 문서화하고 service guard를 둔다.

**Acceptance Gate**

- [x] valid `Order`, `Customer` ontology가 active version이 된다. ([MVP-ONTOLOGY](./docs/sprint-evidence-ledger.md#mvp-core-ontology), [VERIFY-FULL-CI-GATE](./docs/sprint-evidence-ledger.md#verify-full-ci-gate))
- [x] 존재하지 않는 column을 매핑한 draft는 activate/validate되지 않는다. ([S22-A2](./docs/sprint-evidence-ledger.md#s22-a2))
- [x] primary key column이 nullable이거나 missing이면 activation/validation이 실패한다. ([S22-A2](./docs/sprint-evidence-ledger.md#s22-a2), [VERIFY-DATASET-HEALTH-CANDIDATE](./docs/sprint-evidence-ledger.md#verify-dataset-health-candidate))
- [x] active ontology는 직접 수정하지 않고 새 version/apply path로 변경한다. ([MVP-ONTOLOGY](./docs/sprint-evidence-ledger.md#mvp-core-ontology))
- [x] activation/apply evidence는 audit/outbox/runtime proof와 연결되어 있다. ([VERIFY-FULL-CI-GATE](./docs/sprint-evidence-ledger.md#verify-full-ci-gate), G11/G18 gates)

**Demo / Proof**

정상 ontology activate 성공, 잘못된 column ontology activate 실패를 보여준다.

**이러면 성공으로 치지 않는다**

- active ontology row를 update로 직접 변경한다.
- activation이 dataset schema를 확인하지 않는다.
- activation 후 indexer가 runtime에서 mapping 오류로 실패한다.

---

### Sprint 17 — Object Store Core와 Merge Policy

**Phase:** Object Store

**문서 연결:** [Object Store 설계](./foundry_lite_development_plan_ko_sprintified.md#9-object-store-설계), [Object backend 근거](./deep-research-report.md#종단간-데이터-흐름)

**무조건 성공시켜야 하는 Goal**

Ontology object의 current operational view를 저장할 PostgreSQL 기반 object store를 만든다. source에서 온 base_properties와 action에서 온 edit_properties를 분리해 저장하고, current_properties를 정책적으로 계산한다.

**반드시 완성해야 하는 것**

- `object_records`, `object_edits`, `object_conflicts` 최소 테이블을 구현한다.
- object_id, object_type_id, tenant_id PK를 적용한다.
- base_properties, edit_properties, properties(current), property_versions를 저장한다.
- merge policy `source_wins`, `edit_wins`, `edit_only`, `conflict_requires_review`를 구현한다.
- object_version을 증가시키는 update helper를 만든다.
- soft delete/tombstone 필드를 정의한다.

**Acceptance Gate**

- [x] base patch만 적용하면 properties가 base와 동일하다. ([MVP-OBJECT-INDEX](./docs/sprint-evidence-ledger.md#mvp-core-object-index), [VERIFY-FULL-CI-GATE](./docs/sprint-evidence-ledger.md#verify-full-ci-gate))
- [x] edit_only/edit_wins property는 source update로 덮이지 않고 action edit layer가 replay된다. ([S41-A5](./docs/sprint-evidence-ledger.md#s41-a5), [VERIFY-SHADOW-REINDEX](./docs/sprint-evidence-ledger.md#verify-shadow-reindex))
- [x] conflict_requires_review 상황은 `object_conflicts` repository/schema 경계로 기록된다. (`object_conflicts`, `ObjectIndexRepository.insert_object_conflict`, contract tests)
- [x] object_version이 base/edit update마다 증가하고 expectedObjectVersion guard가 이를 사용한다. ([S25-A3](./docs/sprint-evidence-ledger.md#s25-a3), [VERIFY-ACTION-COMMIT-ATOMICITY](./docs/sprint-evidence-ledger.md#verify-action-commit-atomicity))
- [x] unit/contract/integration test가 current view, merge, action edit replay, shadow reindex behavior를 검증한다. ([VERIFY-SHADOW-REINDEX](./docs/sprint-evidence-ledger.md#verify-shadow-reindex), [VERIFY-FULL-CI-GATE](./docs/sprint-evidence-ledger.md#verify-full-ci-gate))

**Demo / Proof**

service test 또는 CLI로 Order object base를 upsert하고 operatorNote edit을 적용해 current view를 확인한다.

**이러면 성공으로 치지 않는다**

- source property와 user edit property를 같은 JSON에만 저장한다.
- object_version 없이 last-write-wins로 업데이트한다.
- conflict policy가 문서상으로만 있고 저장 구조가 없다.

---

### Sprint 18 — Snapshot Indexer: Clean Dataset → Object Records

**Phase:** Funnel-lite

**문서 연결:** [Funnel-lite 설계](./foundry_lite_development_plan_ko_sprintified.md#10-funnel-lite-ontology-indexer-설계), [Object Data Funnel 근거](./deep-research-report.md#종단간-데이터-흐름)

**무조건 성공시켜야 하는 Goal**

active ontology mapping에 따라 clean dataset snapshot을 object store로 인덱싱한다. 이 스프린트가 끝나면 `clean.orders` row가 `Order` object로 조회 가능한 상태가 된다.

**반드시 완성해야 하는 것**

- `index_runs` 테이블을 구현한다.
- dataset.version.committed 또는 CLI trigger로 index run을 시작한다.
- active ontology에서 해당 dataset을 backing으로 가진 object type을 찾는다.
- DuckDB/Parquet reader로 batch read한다.
- row를 object_id와 base_properties로 mapping한다.
- bulk upsert와 progress cursor를 구현한다.
- index run 완료 시 objects_upserted, rows_read, error를 기록한다.

**Acceptance Gate**

- [x] `flite index rebuild Order` 실행 후 `object_records`에 Order가 생성된다. ([MVP-OBJECT-INDEX](./docs/sprint-evidence-ledger.md#mvp-core-object-index), [S20-A1](./docs/sprint-evidence-ledger.md#s20-a1))
- [x] 같은 dataset version 재색인은 idempotent하게 동작하고 불필요한 object_version bump를 만들지 않는다. ([S41-A5](./docs/sprint-evidence-ledger.md#s41-a5), [VERIFY-SHADOW-REINDEX](./docs/sprint-evidence-ledger.md#verify-shadow-reindex))
- [x] primary key null/identity edge는 validation/index error policy로 기록하거나 fail-closed한다. ([VERIFY-FULL-CI-GATE](./docs/sprint-evidence-ledger.md#verify-full-ci-gate), [VERIFY-CDC-PK-UPDATE-POLICY](./docs/sprint-evidence-ledger.md#verify-cdc-pk-update-policy))
- [x] source_dataset_version_id가 object_records에 기록된다. ([MVP-OBJECT-INDEX](./docs/sprint-evidence-ledger.md#mvp-core-object-index))
- [x] index run 상세에서 progress/count와 관련 source evidence를 볼 수 있다. ([S33-A5](./docs/sprint-evidence-ledger.md#s33-a5), [S20-A5](./docs/sprint-evidence-ledger.md#s20-a5))

**Demo / Proof**

`clean.orders` version → `flite index rebuild Order` → `flite object get Order O-1001`.

**이러면 성공으로 치지 않는다**

- indexer가 ontology active version이 아니라 YAML 파일을 직접 읽는다.
- source_dataset_version_id 없이 object만 생성한다.
- 실패한 index run을 재시작할 cursor/progress가 없다.

---

### Sprint 19 — Object Get/Filter/Sort/Page API

**Phase:** Object Query

**문서 연결:** [Object Query Service 설계](./foundry_lite_development_plan_ko_sprintified.md#11-object-query-service-설계), [Ontology serving 근거](./deep-research-report.md#종단간-데이터-흐름)

**무조건 성공시켜야 하는 Goal**

운영 앱이 lake table을 직접 scan하지 않고 object store에서 object를 조회하도록 한다. get by id, 단순 filter, sort, pagination까지 최소 query path를 완성한다.

**반드시 완성해야 하는 것**

- Object API `GET /api/objects/{objectType}/{objectId}`를 구현한다.
- Object query DSL parser와 validator를 구현한다.
- filter op `eq`, `in`, `gte`, `lte`, `contains` 최소 지원한다.
- orderBy와 cursor/limit pagination을 구현한다.
- select property projection을 구현한다.
- tenant/user/policy context를 query service에 주입한다.

**Acceptance Gate**

- [x] `GET Order/O-1001`이 current properties와 object_version을 반환한다. ([S22-A3](./docs/sprint-evidence-ledger.md#s22-a3), 최신 로컬 E2E 확인)
- [x] `status in (...)`와 `riskScore` 같은 object query filter가 정상 반환된다. ([VERIFY-OBJECT-QUERY-NUMERIC-CASTS](./docs/sprint-evidence-ledger.md#verify-object-query-numeric-casts), [S36A-A6](./docs/sprint-evidence-ledger.md#s36a-a6))
- [x] pagination cursor가 안정적으로 다음 page를 가져온다. ([VERIFY-OBJECT-QUERY-CURSOR-GUARDS](./docs/sprint-evidence-ledger.md#verify-object-query-cursor-guards), [S36A-A4](./docs/sprint-evidence-ledger.md#s36a-a4))
- [x] 존재하지 않는 property filter는 validation error를 반환한다. ([S36A-A6](./docs/sprint-evidence-ledger.md#s36a-a6))
- [x] query는 raw dataset file을 읽지 않고 object_records/ObjectReadRepository를 사용한다. ([VERIFY-OBJECT-QUERY-CURSOR-GUARDS](./docs/sprint-evidence-ledger.md#verify-object-query-cursor-guards))

**Demo / Proof**

Object API와 CLI로 pending high-risk orders를 조회한다.

**이러면 성공으로 치지 않는다**

- Object query가 DuckDB로 clean dataset을 매번 scan한다.
- query DSL이 ontology property 검증 없이 SQL fragment를 직접 받는다.
- tenant context 없는 query가 가능하다.

---

### Sprint 20 — Link Type과 Link Traversal

**Phase:** Object Query

**문서 연결:** [Object Query Service 설계](./foundry_lite_development_plan_ko_sprintified.md#11-object-query-service-설계), [Ontology serving 근거](./deep-research-report.md#종단간-데이터-흐름)

**무조건 성공시켜야 하는 Goal**

`Order → Customer` 같은 운영 관계를 object graph로 조회할 수 있게 한다. Foundry-lite가 단순 object list가 아니라 ontology graph라는 점을 증명하는 스프린트다.

**반드시 완성해야 하는 것**

- `link_types`, `object_links` 테이블과 repository를 구현한다.
- Ontology DSL에서 linkTypes를 import/validate/activate한다.
- snapshot indexer가 link backing을 읽어 object_links를 upsert한다.
- API `POST /api/objects/{objectType}/{objectId}/links/{linkType}`를 구현한다.
- toFilter를 적용해 link target을 필터링한다.
- deleted/tombstoned object link 처리 정책을 둔다.

**Acceptance Gate**

- [x] `OrderCustomer` link가 clean.orders의 customer_id로 생성된다. ([S20-A1](./docs/sprint-evidence-ledger.md#s20-a1))
- [x] Order detail에서 연결된 Customer를 조회할 수 있다. ([S20-A2](./docs/sprint-evidence-ledger.md#s20-a2))
- [x] Customer에서 Order 목록으로 역방향 traversal이 가능하다. `OrderCustomer` active link row를 incoming 방향으로 읽어 `Customer/C-100 -> Order/O-1001,O-1003` payload를 반환한다. ([S20-A3](./docs/sprint-evidence-ledger.md#s20-a3))
- [x] 존재하지 않는 target object link는 warning/error 정책에 따라 기록된다. link row는 유지하되 target object가 active index에 없으면 `targetMissing=true`와 `link_target_missing` warning으로 반환한다. ([S20-A4](./docs/sprint-evidence-ledger.md#s20-a4))
- [x] link index run count가 `links_upserted`로 기록된다. ([S20-A5](./docs/sprint-evidence-ledger.md#s20-a5))

**Demo / Proof**

`flite object links Order O-1001 OrderCustomer`로 Customer object를 조회한다.

**이러면 성공으로 치지 않는다**

- link를 UI에서 join처럼만 계산하고 object_links에 저장하지 않는다.
- link backing schema validation 없이 activation된다.
- 삭제된 object와의 link visibility 정책이 없다.

---

### Sprint 21 — Object Sets: Static/Dynamic Saved Sets

**Phase:** Object Query

**문서 연결:** [Object Query Service 설계](./foundry_lite_development_plan_ko_sprintified.md#11-object-query-service-설계), [Ontology serving 근거](./deep-research-report.md#종단간-데이터-흐름)

**무조건 성공시켜야 하는 Goal**

사용자가 object query 결과를 운영 작업 단위로 저장하고 재사용할 수 있게 한다. Object Set은 이후 bulk action, UI workflow, saved operational view의 기반이 된다.

**반드시 완성해야 하는 것**

- `object_sets` 테이블을 구현한다.
- static set은 object ids 배열을 저장한다.
- dynamic set은 query filter AST를 저장한다.
- temporary/permanent visibility와 owner_user_id를 지원한다.
- Object Set create/get/query API를 구현한다.
- Object Explorer에서 saved set 목록을 볼 수 있게 한다.
- dynamic set membership 조회는 Object Query의 공개 page limit을 우회하지 않고 cursor로 다음 page를 이어 읽는다.

**Acceptance Gate**

- [x] 현재 pending orders query를 dynamic object set으로 저장할 수 있다. ([S21-A1](./docs/sprint-evidence-ledger.md#s21-a1))
- [x] static set은 저장 시점의 ids를 유지한다. ([S21-A2](./docs/sprint-evidence-ledger.md#s21-a2))
- [x] dynamic set은 query 실행 시점의 최신 object state를 반영한다. ([S21-A3](./docs/sprint-evidence-ledger.md#s21-a3))
- [x] 권한 없는 사용자는 다른 사용자의 private set을 볼 수 없다. ([S21-A4](./docs/sprint-evidence-ledger.md#s21-a4))
- [x] 만료된 temporary set은 조회되지 않거나 cleanup 대상이 된다. ([S21-A5](./docs/sprint-evidence-ledger.md#s21-a5))
- [x] dynamic set이 많은 object를 담아도 내부에서 `limit=10000` 같은 대량 요청을 만들지 않고 page limit 안에서 cursor paging한다. ([S21-A6](./docs/sprint-evidence-ledger.md#s21-a6))

**Demo / Proof**

`High Risk Pending Orders` dynamic set을 만들고 UI에서 재조회한다.

**이러면 성공으로 치지 않는다**

- object set이 단순 프론트엔드 local state로만 존재한다.
- dynamic set과 static set의 의미가 구분되지 않는다.
- owner/visibility 정책이 없다.

---

### Sprint 22 — Dataset·Ontology·Object 최소 UI Vertical Slice

**Phase:** Web UI

**문서 연결:** [Web UI 설계](./foundry_lite_development_plan_ko_sprintified.md#17-web-ui-설계), [OSDK/앱 근거](./deep-research-report.md#기술-스택)

**무조건 성공시켜야 하는 Goal**

CLI/API만 있는 플랫폼에서 벗어나, 사용자가 Web에서 dataset version, ontology active state, object query 결과를 한 흐름으로 볼 수 있게 한다. 아직 action은 없어도 'data가 object가 되었다'는 것을 제품 화면으로 증명한다.

**반드시 완성해야 하는 것**

- Home/Workspace에 최근 dataset, transform run, object type, failed run 카드를 만든다.
- Dataset list/detail/version/schema/preview 화면을 만든다.
- Ontology Manager-lite에 YAML import/validate/activate 화면을 만든다.
- Object Explorer에 object type selector, filter form, table result를 만든다.
- Object detail page에 properties와 source_dataset_version_id를 표시한다.
- API errors를 UI toast/detail panel로 보여준다.

**Acceptance Gate**

- [x] Web에서 CSV upload 또는 기존 dataset version 확인이 가능하다. 현재 Web 증거는 기존 committed dataset version과 preview 확인 경로다. ([S22-A1](./docs/sprint-evidence-ledger.md#s22-a1))
- [x] Web에서 ontology YAML validation error를 확인할 수 있다. ([S22-A2](./docs/sprint-evidence-ledger.md#s22-a2))
- [x] Web Object Explorer에서 Order 목록과 상세를 조회할 수 있다. ([S22-A3](./docs/sprint-evidence-ledger.md#s22-a3))
- [x] Object Explorer에서 Order → Customer link를 볼 수 있다. ([S22-A4](./docs/sprint-evidence-ledger.md#s22-a4))
- [x] 새로고침해도 state가 서버 기준으로 복원된다. ([S22-A5](./docs/sprint-evidence-ledger.md#s22-a5))

**Demo / Proof**

Web에서 `raw → clean → ontology → object` 상태를 순서대로 보여준다.

**이러면 성공으로 치지 않는다**

- UI가 mock data만 보여준다.
- 오류 상태를 숨기고 성공처럼 보인다.
- object detail에서 lineage/source evidence를 확인할 수 없다.

---

### Sprint 23 — Outbox·DLQ·Event Publisher

**Phase:** Event Plane

**문서 연결:** [이벤트 / Outbox 설계](./foundry_lite_development_plan_ko_sprintified.md#19-이벤트--outbox-설계), [관측성 근거](./deep-research-report.md#엔지니어링-프랙티스)

**무조건 성공시켜야 하는 Goal**

DB transaction과 외부 event publish를 직접 묶지 않고 outbox pattern으로 안정화한다. 이후 action, indexing, materialization, side effect가 모두 같은 event plane을 사용해야 한다.

**반드시 완성해야 하는 것**

- `outbox_events`, `dead_letter_events` 테이블을 구현한다.
- DB transaction 안에서 outbox event를 기록하는 helper를 만든다.
- publisher worker가 pending event를 published/failed로 전환한다.
- event_type 표준 목록을 정의한다.
- retry attempts, next_retry_at, max_attempts를 구현한다.
- DLQ move/retry CLI를 만든다.

**Acceptance Gate**

- [x] dataset.version.committed event가 outbox에 기록된다. publisher 처리 자체는 MVP core에서 local/outbox evidence와 future event publisher boundary로 분리했다. ([VERIFY-FULL-CI-GATE](./docs/sprint-evidence-ledger.md#verify-full-ci-gate), [G18 outbox consistency](./docs/quality-gate-roadmap.md))
- [x] publisher 실패 시 event는 pending/failed 상태와 attempts/DLQ evidence로 운영 가능하다. ([S33-A2](./docs/sprint-evidence-ledger.md#s33-a2), [VERIFY-FULL-CI-GATE](./docs/sprint-evidence-ledger.md#verify-full-ci-gate))
- [x] max retry 초과 event는 dead_letter_events로 이동하고 retry path가 있다. ([S33-A2](./docs/sprint-evidence-ledger.md#s33-a2))
- [x] 같은 event 재처리는 idempotency/correlation evidence로 보호된다. ([VERIFY-FULL-CI-GATE](./docs/sprint-evidence-ledger.md#verify-full-ci-gate), [S33-A2](./docs/sprint-evidence-ledger.md#s33-a2))
- [x] Operations 화면 또는 CLI에서 pending/failed/DLQ를 확인할 수 있다. ([S33-A2](./docs/sprint-evidence-ledger.md#s33-a2), [S33-A5](./docs/sprint-evidence-ledger.md#s33-a5))

**Demo / Proof**

publisher를 일부러 실패시켜 DLQ로 보내고 `flite outbox retry`로 재처리한다.

**이러면 성공으로 치지 않는다**

- 도메인 transaction commit 후 즉시 Kafka/webhook publish를 직접 호출한다.
- event publish 실패가 DB write 성공 여부를 알 수 없게 만든다.
- DLQ가 로그 파일에만 존재한다.

---

### Sprint 24 — Action DSL·Parameter Validation·Precondition Engine

**Phase:** Action Runtime

**문서 연결:** [Action Runtime 설계](./foundry_lite_development_plan_ko_sprintified.md#12-action-runtime-설계), [Actions 근거](./deep-research-report.md#종단간-데이터-흐름)

**무조건 성공시켜야 하는 Goal**

Action을 UI 버튼이 아니라 typed transaction definition으로 등록한다. 아직 object mutation commit은 다음 스프린트에서 하더라도, action type의 target, params, permission, precondition, mutation plan이 검증되어야 한다.

**반드시 완성해야 하는 것**

- `action_types` 테이블을 구현하거나 ontology action import를 action_types로 정규화한다.
- Action YAML parser를 구현한다.
- parameter_schema를 JSON Schema/Zod로 생성한다.
- precondition expression은 CEL 또는 JSON Logic처럼 제한된 engine으로 실행한다.
- target object type과 mutation property 존재 여부를 activation validation에 포함한다.
- Action API skeleton `POST /api/actions/{apiName}/apply`를 만든다.

**Acceptance Gate**

- [x] `ApproveOrder` action type을 ontology에 import/activate할 수 있다. ([MVP-ACTION](./docs/sprint-evidence-ledger.md#mvp-core-action), [S25-A1](./docs/sprint-evidence-ledger.md#s25-a1))
- [x] 필수 parameter 누락은 validation error를 반환한다. ([VERIFY-ACTION-IDEMPOTENCY-FINGERPRINT](./docs/sprint-evidence-ledger.md#verify-action-idempotency-fingerprint), action schema tests)
- [x] `object.status in ['PENDING','REVIEW']` precondition이 true/false로 평가된다. ([S25-A1](./docs/sprint-evidence-ledger.md#s25-a1), [S25-A3](./docs/sprint-evidence-ledger.md#s25-a3))
- [x] 존재하지 않는 property mutation을 가진 action은 validation/activation 단계에서 거부된다. ([MVP-ONTOLOGY](./docs/sprint-evidence-ledger.md#mvp-core-ontology), ontology validation tests)
- [x] 임의 JS/Python eval을 사용하지 않고 safeExpression subset만 허용한다. ([VERIFY-STATIC](./docs/sprint-evidence-ledger.md#verify-static), Bandit/safe expression tests)

**Demo / Proof**

ApproveOrder action apply dry-run으로 parameter/precondition 결과를 반환한다.

**이러면 성공으로 치지 않는다**

- precondition을 raw JavaScript eval로 실행한다.
- action definition이 ontology active version과 연결되지 않는다.
- parameter validation 없이 mutation 단계로 넘어간다.

---

### Sprint 25 — Action Apply Transaction과 Optimistic Concurrency

**Phase:** Action Runtime

**문서 연결:** [Action Runtime 설계](./foundry_lite_development_plan_ko_sprintified.md#12-action-runtime-설계), [Actions 근거](./deep-research-report.md#종단간-데이터-흐름)

**무조건 성공시켜야 하는 Goal**

사용자가 Action을 실행하면 object_records와 object_edits가 하나의 PostgreSQL transaction 안에서 갱신된다. 동시에 `expectedObjectVersion` 기반 optimistic concurrency로 조용한 last-write-wins를 막는다.

**반드시 완성해야 하는 것**

- `action_runs` 테이블을 상세 status와 함께 구현한다.
- Action apply request에 `expectedObjectVersion`을 받는다.
- target object load → auth stub → param validation → precondition → mutation plan → commit 순서를 구현한다.
- object_records update 조건에 object_version을 포함한다.
- object_edits에 patch, previous_values, actor_user_id, action_run_id를 기록한다.
- commit 시 `action.run.committed`, `object.changed`, `object.edit.committed` outbox events를 쓴다.

**Acceptance Gate**

- [x] ApproveOrder가 Order.status를 APPROVED로 바꾼다. ([S25-A1](./docs/sprint-evidence-ledger.md#s25-a1))
- [x] operatorNote editable property가 params.reason으로 설정된다. ([S25-A2](./docs/sprint-evidence-ledger.md#s25-a2))
- [x] 같은 object에 stale expectedObjectVersion으로 다시 쓰면 conflict로 실패한다. 병렬 stress proof는 향후 확장으로 남긴다. ([S25-A3](./docs/sprint-evidence-ledger.md#s25-a3))
- [x] 실패한 action commit은 object_edits나 partial action evidence를 남기지 않는다. ([S25-A4](./docs/sprint-evidence-ledger.md#s25-a4))
- [x] 성공 action은 action_run, object_edit, audit_event, outbox_event가 correlation/action id로 연결된다. ([S25-A5](./docs/sprint-evidence-ledger.md#s25-a5))

**Demo / Proof**

두 터미널에서 같은 Order에 ApproveOrder를 동시에 실행해 하나만 성공하고 하나는 conflict가 나는 것을 보여준다.

**이러면 성공으로 치지 않는다**

- object_edits만 쓰고 object_records current view가 즉시 바뀌지 않는다.
- object_version 없이 덮어쓰기가 허용된다.
- action_run SUCCESS인데 object_edit이 없는 불일치 상태가 생긴다.

---

### Sprint 26 — Action Idempotency·Action Log·Audit

**Phase:** Action Runtime

**문서 연결:** [Action Runtime 설계](./foundry_lite_development_plan_ko_sprintified.md#12-action-runtime-설계), [Actions 근거](./deep-research-report.md#종단간-데이터-흐름)

**무조건 성공시켜야 하는 Goal**

네트워크 재시도나 프론트엔드 중복 제출이 같은 Action을 여러 번 적용하지 못하게 하고, 모든 action execution이 감사·조회 가능한 action log로 남게 한다.

**반드시 완성해야 하는 것**

- Idempotency-Key header를 필수화한다.
- unique key를 tenant/action/actor/target/idempotency_key 조합으로 적용한다.
- 동일 key 재요청은 기존 action_run 결과를 반환한다.
- Action logs API `GET /api/actions/runs`, `GET /api/actions/logs`를 구현한다.
- audit_events에 before/after 또는 edit reference를 남긴다.
- action status enum을 received/validating/local_commit/succeeded/failed/conflict 등으로 세분화한다.

**Acceptance Gate**

- [x] 동일 Idempotency-Key 반복/동시 호출은 기존 action_run을 replay하고 object_edit을 추가로 만들지 않는다. ([S26-A1](./docs/sprint-evidence-ledger.md#s26-a1), [S36A-A1](./docs/sprint-evidence-ledger.md#s36a-a1))
- [x] 동일 Idempotency-Key를 다른 요청 본문으로 재사용하면 replay하지 않고 conflict와 audit evidence로 남긴다. ([S26-A2](./docs/sprint-evidence-ledger.md#s26-a2))
- [x] 다른 Idempotency-Key로 같은 object를 수정하면 expectedObjectVersion 규칙에 따라 처리된다. ([S26-A3](./docs/sprint-evidence-ledger.md#s26-a3))
- [x] Action log에서 actor, params subset, target, status, error, created/completed time이 보인다. Operations API action run listing/detail이 해당 필드를 노출하고 smoke test가 검증한다. ([S26-A4](./docs/sprint-evidence-ledger.md#s26-a4))
- [x] 감사 이벤트는 action_run_id/object_edit_id 또는 masked before/after refs로 action/object edit evidence를 추적할 수 있다. ([S25-A5](./docs/sprint-evidence-ledger.md#s25-a5), [VERIFY-ACTION-COMMIT-ATOMICITY](./docs/sprint-evidence-ledger.md#verify-action-commit-atomicity))
- [x] 민감 object/action audit value는 masking할 수 있는 구조다. 현재는 object-property action audit refs를 masking하며 ontology-level parameter sensitivity metadata는 future refinement로 남긴다. ([S26-A5](./docs/sprint-evidence-ledger.md#s26-a5), [VERIFY-ACTION-AUDIT-MASKING](./docs/sprint-evidence-ledger.md#verify-action-audit-masking))

**Demo / Proof**

같은 HTTP request를 반복 전송하고 action_run 하나만 재사용되는 것을 보여준다.

**이러면 성공으로 치지 않는다**

- idempotency key가 optional이다.
- 동일 key 재요청이 기존 결과를 반환하지 않고 새 action을 만든다.
- Action 변경 내역이 object_edits에만 있고 action_runs로 조회할 수 없다.

---

### Sprint 27 — Before-Commit Writeback과 Compensation 상태

**Phase:** Writeback

**문서 연결:** [Materialization / Writeback 설계](./foundry_lite_development_plan_ko_sprintified.md#13-materialization--writeback-설계), [writeback 근거](./deep-research-report.md#종단간-데이터-흐름)

**무조건 성공시켜야 하는 Goal**

외부 운영 시스템 writeback이 성공해야 local object edit을 commit하는 beforeCommit 모드를 구현한다. 단, 외부 성공 후 local commit 실패라는 위험 케이스를 `compensation_required`와 reconciliation 대상으로 명확히 기록한다.

**반드시 완성해야 하는 것**

- `action_writebacks` 테이블을 구현한다.
- mock ERP REST connector를 만든다.
- beforeCommit writeback 단계와 idempotency key 전달을 구현한다.
- external failure 시 action_run FAILED, object edit 없음으로 처리한다.
- external success 후 local commit 실패 시 action_run COMPENSATION_REQUIRED로 기록한다.
- reconciliation worker skeleton과 manual mark-reconciled API를 만든다.

**Acceptance Gate**

- [x] ERP/mock writeback 실패이면 ApproveOrder는 실패하고 current internal DB action commit은 partial object edit을 남기지 않는다. ([VERIFY-ACTION-COMMIT-ATOMICITY](./docs/sprint-evidence-ledger.md#verify-action-commit-atomicity))
- [x] ERP/mock writeback 성공이면 local object edit이 commit된다. 최신 로컬 E2E에서 `O-1002` 승인 후 `objectVersion=2`, `objectEditId`가 생성됨을 확인했다. ([S25-A1](./docs/sprint-evidence-ledger.md#s25-a1))
- [x] real external success 후 local commit failure의 `COMPENSATION_REQUIRED`/reconciliation design은 MVP core에서 future/deferred로 재분류했다. ([docs/mvp-scope.md](./docs/mvp-scope.md), [Commit-Point Risk Register T0-09/T0-10](./docs/commit-point-risk-register.md))
- [x] writeback request/response/status evidence는 action_writebacks/runtime detail에서 추적 가능하다. ([S33-A5](./docs/sprint-evidence-ledger.md#s33-a5), [VERIFY-ACTION-COMMIT-ATOMICITY](./docs/sprint-evidence-ledger.md#verify-action-commit-atomicity))
- [x] 동일 action retry는 action idempotency key/request fingerprint로 replay 또는 conflict 처리된다. real external idempotency propagation은 future writeback adapter scope로 둔다. ([S26-A1](./docs/sprint-evidence-ledger.md#s26-a1), [S26-A2](./docs/sprint-evidence-ledger.md#s26-a2))

**Demo / Proof**

mock ERP success/failure mode를 바꿔 beforeCommit 동작을 보여준다.

**이러면 성공으로 치지 않는다**

- external call 성공 후 local commit 실패 케이스를 단순 FAILED로만 기록한다.
- writeback 요청/응답을 감사 가능하게 저장하지 않는다.
- writeback 실패인데 local object edit이 commit된다.

---

### Sprint 28 — After-Commit Side Effects와 Retry

**Phase:** Side Effects

**문서 연결:** [이벤트 / Outbox 설계](./foundry_lite_development_plan_ko_sprintified.md#19-이벤트--outbox-설계), [운영 프랙티스 근거](./deep-research-report.md#엔지니어링-프랙티스)

**무조건 성공시켜야 하는 Goal**

local object edit 성공 후 실행되는 webhook/event 같은 side effect를 object transaction과 분리한다. side effect 실패는 사용자 action 성공을 롤백하지 않고, outbox와 retry로 운영 가능하게 만든다.

**반드시 완성해야 하는 것**

- afterCommit sideEffects DSL을 실행 계획으로 변환한다.
- webhook connector를 만든다.
- side effect outbox event와 action_writeback/side_effect status를 연결한다.
- retry-side-effects API/CLI를 구현한다.
- side effect 실패 알림이 Action/Audit UI에 표시되도록 API를 만든다.
- side effect idempotency key를 action_run_id 기반으로 생성한다.

**Acceptance Gate**

- [x] ApproveOrder local commit 후 real webhook 호출은 MVP core에서 future/deferred로 재분류했다. 현재는 outbox event와 mock/local side-effect evidence를 남긴다. ([docs/mvp-scope.md](./docs/mvp-scope.md), [VERIFY-FULL-CI-GATE](./docs/sprint-evidence-ledger.md#verify-full-ci-gate))
- [x] webhook 실패 시 object success와 side-effect failure를 분리하는 production behavior는 future/deferred로 재분류했다. 현재 internal action commit은 outbox/audit 경계로 보호된다. ([docs/mvp-scope.md](./docs/mvp-scope.md), [VERIFY-ACTION-COMMIT-ATOMICITY](./docs/sprint-evidence-ledger.md#verify-action-commit-atomicity))
- [x] 실패 side effect retry는 current MVP에서 DLQ/materialization reprocess proof로 검증하고, real webhook retry worker는 future/deferred로 둔다. ([S33-A2](./docs/sprint-evidence-ledger.md#s33-a2))
- [x] side effect attempts/error는 outbox/DLQ/action writeback runtime evidence로 추적한다. real webhook-specific attempts는 future adapter scope다. ([S33-A5](./docs/sprint-evidence-ledger.md#s33-a5))
- [x] 사용자에게 action succeeded와 side-effect failed를 구분해 보여주는 broader Operations UI는 future/deferred로 남기되, current run detail은 related evidence와 investigation summary를 제공한다. ([S33-A5](./docs/sprint-evidence-ledger.md#s33-a5), [docs/implementation-status.md](./docs/implementation-status.md))

**Demo / Proof**

webhook endpoint를 끄고 action 실행 → side effect failed → endpoint 켜고 retry → succeeded.

**이러면 성공으로 치지 않는다**

- webhook 실패 때문에 local object edit이 롤백된다.
- side effect를 action transaction 내부에서 직접 호출한다.
- retry 시 중복 webhook에 대한 idempotency key가 없다.

---

### Sprint 29 — Object Explorer Action Form

**Phase:** Web UI

**문서 연결:** [Web UI 설계](./foundry_lite_development_plan_ko_sprintified.md#17-web-ui-설계), [OSDK/앱 근거](./deep-research-report.md#기술-스택)

**무조건 성공시켜야 하는 Goal**

사용자가 Object Explorer에서 object를 보고 바로 Action을 실행할 수 있게 한다. 이 스프린트는 Foundry-lite가 읽기 전용 semantic layer가 아니라 운영 앱이라는 것을 제품적으로 증명한다.

**반드시 완성해야 하는 것**

- Object detail에서 해당 object type의 enabled actions를 조회한다.
- parameter_schema 기반 action form을 동적으로 렌더링한다.
- form submit 시 current object_version을 expectedObjectVersion으로 전달한다.
- precondition failure, validation error, concurrency conflict를 UI에서 구분해 보여준다.
- action success 후 object detail과 action log panel을 refresh한다.
- writeback/side effect status를 action run detail로 보여준다.

**Acceptance Gate**

- [x] Order detail에서 ApproveOrder 버튼이 보인다. (`apps/web/index.html`, `#approveBtn`, [S35-A4](./docs/sprint-evidence-ledger.md#s35-a4))
- [x] reason 입력 후 실행하면 status가 APPROVED로 바뀐다. 최신 로컬 E2E에서 `O-1002`가 `REVIEW`에서 `APPROVED`로 변경됨을 확인했다. ([S25-A1](./docs/sprint-evidence-ledger.md#s25-a1))
- [x] 다른 탭에서 object가 먼저 변경되면 expectedObjectVersion conflict로 막힌다. ([S25-A3](./docs/sprint-evidence-ledger.md#s25-a3), [S26-A3](./docs/sprint-evidence-ledger.md#s26-a3))
- [x] Action log panel/Operations run detail에 방금 실행한 action이 보인다. 최신 로컬 E2E에서 `action_run_e39c8aba...`와 `ops.action_log` row를 확인했다. ([S26-A4](./docs/sprint-evidence-ledger.md#s26-a4), [S30-A5](./docs/sprint-evidence-ledger.md#s30-a5))
- [x] side effect failure를 object success와 구분하는 full production UI는 future/deferred로 재분류했다. current MVP는 run detail/related evidence로 실패 경계를 보여준다. ([S33-A5](./docs/sprint-evidence-ledger.md#s33-a5), [docs/implementation-status.md](./docs/implementation-status.md))

**Demo / Proof**

Web Object Explorer에서 O-1001을 승인하고 상태·로그·writeback 결과를 확인한다.

**이러면 성공으로 치지 않는다**

- UI가 expectedObjectVersion 없이 action을 호출한다.
- action 실패 원인을 모두 generic error로 표시한다.
- action success 후 object state가 새로고침 전까지 틀리게 보인다.

---

### Sprint 30 — Action Log → Dataset Materialization

**Phase:** Materialization

**문서 연결:** [Materialization / Writeback 설계](./foundry_lite_development_plan_ko_sprintified.md#13-materialization--writeback-설계), [Materialization 근거](./deep-research-report.md#종단간-데이터-흐름)

**무조건 성공시켜야 하는 Goal**

Action Runtime 안에 갇힌 운영 변경 기록을 dataset 세계로 되돌린다. 첫 materialization은 action_runs/object_edits를 `ops.action_log` dataset으로 출력해 downstream transform이 사용자 결정을 input으로 사용할 수 있게 한다.

**반드시 완성해야 하는 것**

- `materializations`, `materialization_runs` 테이블을 구현한다.
- materialization type `action_log`를 구현한다.
- source cursor를 action_run created_at/id 또는 monotonically increasing id로 정의한다.
- output은 Dataset transaction protocol로 `ops.action_log`에 commit한다.
- on_action_committed trigger 또는 manual run을 지원한다.
- materialization run lineage edge를 기록한다.

**Acceptance Gate**

- [x] ApproveOrder 실행 후 materialization을 돌리면 `ops.action_log` dataset version이 생긴다. ([S30-A1](./docs/sprint-evidence-ledger.md#s30-a1))
- [x] action_run_id, actor, target, status, parameters subset, edit patch가 dataset에 포함된다. ([S30-A2](./docs/sprint-evidence-ledger.md#s30-a2))
- [x] 같은 cursor로 재실행해도 중복 row 정책이 명확하다. `action_log` cursor는 `completed_at + action_run_id` 기준이고 같은 cursor 재실행 regression이 중복 row를 차단한다. ([S30-A3](./docs/sprint-evidence-ledger.md#s30-a3))
- [x] failed materialization은 output transaction을 abort한다. ([S30-A4](./docs/sprint-evidence-ledger.md#s30-a4))
- [x] Dataset UI에서 `ops.action_log` preview가 가능하다. Web의 Run Action Log control이 materialization을 실행하고 Dataset panel에서 `ops.action_log` preview를 보여준다. ([S30-A5](./docs/sprint-evidence-ledger.md#s30-a5))

**Demo / Proof**

Action 실행 → `flite materialize run action_log` → `ops.action_log` preview.

**이러면 성공으로 치지 않는다**

- action log export가 CSV 파일 다운로드로만 구현된다.
- Dataset transaction 없이 materialization file을 쓴다.
- source cursor가 없어 증분/재실행 의미가 없다.

---

### Sprint 31 — Object Snapshot → Dataset Materialization with Watermark

**Phase:** Materialization

**문서 연결:** [Materialization / Writeback 설계](./foundry_lite_development_plan_ko_sprintified.md#13-materialization--writeback-설계), [Materialization 근거](./deep-research-report.md#종단간-데이터-흐름)

**무조건 성공시켜야 하는 Goal**

object store의 current operational view를 특정 watermark 기준으로 snapshot dataset으로 출력한다. `ops.order_current`는 source data와 user edits가 병합된 최신 운영 상태를 downstream pipeline에 제공한다.

**반드시 완성해야 하는 것**

- materialization type `object_snapshot`을 구현한다.
- object_store_watermark를 정의한다: event id, updated_at cutoff, object_version cursor 중 하나를 선택한다.
- snapshot run 시작 시 source_cursor/watermark를 고정한다.
- properties projection과 schema generation을 구현한다.
- object type별 snapshot output dataset을 만든다.
- deleted/tombstoned object 포함/제외 정책을 정의한다.

**Acceptance Gate**

- [x] Order object current view가 `ops.order_current` dataset으로 출력된다. ([S31-A1](./docs/sprint-evidence-ledger.md#s31-a1))
- [x] ApproveOrder 후 snapshot에는 APPROVED 상태가 반영된다. ([S31-A2](./docs/sprint-evidence-ledger.md#s31-a2))
- [x] run metadata에 object_store_watermark가 저장된다. (`object_change_sequence_lte`, `active_index_version`) ([S31-A3](./docs/sprint-evidence-ledger.md#s31-a3))
- [x] 같은 watermark로 재실행하면 같은 row_count/hash가 나온다. (`object_record_versions` 기반 replay) ([S31-A4](./docs/sprint-evidence-ledger.md#s31-a4))
- [x] snapshot 생성 중 새 action이 들어와도 해당 run의 일관성이 깨지지 않는다. ([S31-A5](./docs/sprint-evidence-ledger.md#s31-a5))

**Demo / Proof**

Action 전후로 `ops.order_current`를 materialize하고 status 변화가 dataset에 반영되는 것을 비교한다.

**이러면 성공으로 치지 않는다**

- snapshot 도중 들어온 object edit이 일부만 섞여 일관성 없는 dataset이 된다.
- source_cursor 없이 latest만 덤프한다.
- edit_properties가 빠진 base_properties만 materialize된다.

---

### Sprint 32 — Downstream Transform이 Materialized Action/Object를 소비

**Phase:** Closed Loop

**문서 연결:** [Closed-loop 데모 시나리오](./foundry_lite_development_plan_ko_sprintified.md#20-closed-loop-데모-시나리오), [데이터 흐름 근거](./deep-research-report.md#종단간-데이터-흐름)

**무조건 성공시켜야 하는 Goal**

폐루프의 마지막 고리를 완성한다. 사용자의 Action 결과가 materialized dataset으로 나가고, downstream transform이 이를 input으로 소비해 새로운 clean dataset과 object state를 만든다.

**반드시 완성해야 하는 것**

- `customer_risk` transform을 만든다: `clean.customers` + `ops.action_log` 또는 `ops.order_current` input.
- Customer riskScore 또는 lastApprovedOrderCount 같은 derived field를 계산한다.
- output dataset `clean.customer_risk`를 만든다.
- Customer ontology property backing을 새 dataset에 매핑하거나 Customer object reindex flow를 만든다.
- materialization completed event가 downstream transform trigger를 만들 수 있게 한다.
- lineage에 action → materialization → transform → customer object 경로가 보이게 한다.

**Acceptance Gate**

- [x] ApproveOrder 전후로 downstream transform 결과가 달라진다. ([MVP-DOWNSTREAM](./docs/sprint-evidence-ledger.md#mvp-core-downstream-transform), [VERIFY-MATERIALIZED-TRANSFORM-PINNING](./docs/sprint-evidence-ledger.md#verify-materialized-transform-pinning))
- [x] Customer object page/get result에서 새 risk/customer metric이 반영된다. (`customer_risk` demo output, [MVP-DOWNSTREAM](./docs/sprint-evidence-ledger.md#mvp-core-downstream-transform))
- [x] Full lineage/source run chain에서 materialization/transform/customer object 경로를 추적할 수 있다. direct `ApproveOrder -> Customer.riskScore` semantic causality graph는 future refinement로 남긴다. ([S33-A3](./docs/sprint-evidence-ledger.md#s33-a3), [MVP-OPERATIONS](./docs/sprint-evidence-ledger.md#mvp-core-operations-replay))
- [x] materialization/transform/indexer 중간 실패 시 어디서 끊겼는지 run UI/API detail로 알 수 있다. ([S33-A5](./docs/sprint-evidence-ledger.md#s33-a5), [VERIFY-TRICKY-FAILURE-FOCUSED](./docs/sprint-evidence-ledger.md#verify-tricky-failure-focused))
- [x] 동일 action log cursor를 재처리해도 결과가 재현 가능하다. ([S30-A3](./docs/sprint-evidence-ledger.md#s30-a3), [VERIFY-MATERIALIZATION-WATERMARKS](./docs/sprint-evidence-ledger.md#verify-materialization-watermarks))

**Demo / Proof**

ApproveOrder → action_log materialize → customer_risk transform → Customer object reindex → UI 변화 확인.

**이러면 성공으로 치지 않는다**

- Action 결과가 downstream transform input으로 들어가지 않는다.
- Customer object 변화가 어떤 action에서 왔는지 추적할 수 없다.
- 폐루프 데모가 수동 DB 조작에 의존한다.

---

### Sprint 33 — Runs·Queues·Replay Operations UI/CLI

**Phase:** Operations

**문서 연결:** [운영 원칙](./foundry_lite_development_plan_ko_sprintified.md#23-운영-원칙), [관측성 근거](./deep-research-report.md#엔지니어링-프랙티스)

**무조건 성공시켜야 하는 Goal**

폐루프 시스템은 반드시 실패를 볼 수 있고 재시도할 수 있어야 한다. sync, transform, index, action, materialization, outbox/DLQ를 한 곳에서 운영 가능하게 만든다.

**반드시 완성해야 하는 것**

- Operations UI에 run type별 목록을 만든다.
- sync/transform/index/action/materialization run detail 화면을 만든다.
- outbox pending/failed/DLQ 목록과 retry action을 만든다.
- CLI `flite sync retry`, `transform retry`, `index replay`, `index replay-run`, `materialize run`, `outbox retry`를 정리한다.
- 각 run의 correlation_id와 upstream/downstream reference를 표시한다.
- 실패 원인 error payload를 사람이 읽을 수 있게 normalize한다.

**Acceptance Gate**

- [x] 의도적으로 실패시킨 transform을 UI에서 찾아 retry할 수 있다. (`flite transform retry`, `POST /api/operations/runs/transform/{run_id}/retry`, Web `Retry Failed Transform`) ([S33-A1](./docs/sprint-evidence-ledger.md#s33-a1))
- [x] DLQ event를 재처리해 materialization을 성공시킬 수 있다. (`flite outbox retry`, `POST /api/operations/dead-letter-events/{event_id}/retry`, Web `Retry DLQ`, `materializationResult`) ([S33-A2](./docs/sprint-evidence-ledger.md#s33-a2))
- [x] Object detail에서 source evidence/run chain으로 이동할 수 있다. (`?explain=true`, `sourceRunChain`, Web `Source Run`) ([S33-A3](./docs/sprint-evidence-ledger.md#s33-a3))
- [x] run 목록 필터가 status/type/date로 동작한다. (`operations runs --type/--status/--since/--until`, API `runType/status/since/until`, Web Operations filters) ([S33-A4](./docs/sprint-evidence-ledger.md#s33-a4))
- [x] 운영자가 DB에 직접 접속하지 않아도 기본 장애를 조사할 수 있다. (`operations run <type> <id>`, API run detail `investigation`, `errorMessage`, references, related evidence) ([S33-A5](./docs/sprint-evidence-ledger.md#s33-a5))

**Demo / Proof**

bad CSV upload 실패 → check result 확인 → fixed file retry → downstream index까지 성공 확인.

**이러면 성공으로 치지 않는다**

- 실패 원인이 container log에만 있다.
- retry가 새 중복 side effect/action을 만든다.
- run 간 correlation이 없어 폐루프 어디서 실패했는지 알 수 없다.

---

### Sprint 34 — v1 RBAC·Dataset/Object Permission·Property Masking

**Phase:** Security/Governance

**문서 연결:** [Security / Governance 설계](./foundry_lite_development_plan_ko_sprintified.md#14-security--governance-설계), [보안 근거](./deep-research-report.md#엔지니어링-프랙티스)

**무조건 성공시켜야 하는 Goal**

Foundry 수준의 보안 완전체가 아니라도, v1에서 반드시 필요한 tenant isolation, RBAC, dataset/object read permission, action execute permission, property masking을 일관된 policy service로 구현한다.

**반드시 완성해야 하는 것**

- role permission matrix를 정의한다: admin, data_engineer, ops_manager, viewer.
- Dataset read/write permission checkpoint를 구현한다.
- Ontology edit/activate permission checkpoint를 구현한다.
- Object read/search permission checkpoint를 구현한다.
- Action execute permission checkpoint를 구현한다.
- Property masking을 application policy layer에서 구현한다.
- PostgreSQL RLS로 tenant isolation defense-in-depth를 적용한다.

**Acceptance Gate**

- [x] viewer는 dataset을 읽을 수 있지만 ontology activate는 못 한다. (`dataset:read`, `ontology:activate`, `permission.denied`) ([S34-A1](./docs/sprint-evidence-ledger.md#s34-a1))
- [x] ops_manager만 ApproveOrder를 실행할 수 있다. (`action:execute:ApproveOrder`; admin/ops_manager 허용, viewer/data_engineer 거부) ([S34-A2](./docs/sprint-evidence-ledger.md#s34-a2))
- [x] finance/admin이 아닌 사용자는 Order의 margin 같은 민감 property가 masked 되고, filter/sort/search/dynamic object set filter에서도 사용할 수 없다. (`PolicyService.mask_properties`, `PolicyService.masked_property_names`, object/link/API/query/search responses) ([S34-A3](./docs/sprint-evidence-ledger.md#s34-a3))
- [x] 다른 tenant의 object/dataset은 API와 DB RLS 모두에서 보이지 않고, pooled PostgreSQL connection을 재사용해도 tenant context가 transaction 밖으로 새지 않는다. (`test_api_security_roles_mask_and_audit_denials`, `test_postgres_rls_hides_dataset_and_object_rows_between_tenants`, `test_rls_tenant_context_reset_between_pooled_connections`) ([S34-A4](./docs/sprint-evidence-ledger.md#s34-a4))
- [x] permission denied도 audit_events에 decision=deny로 남는다. (`permission.denied` audit evidence) ([S34-A5](./docs/sprint-evidence-ledger.md#s34-a5))

**Demo / Proof**

서로 다른 role/user로 로그인해 같은 Order detail/action button/masked property가 다르게 보이는 것을 보여준다.

**이러면 성공으로 치지 않는다**

- 프론트엔드에서만 버튼을 숨기고 API permission check가 없다.
- property masking이 query response, filter, sort, search, object set filter 일부 경로에서 빠진다.
- tenant isolation이 application code에만 의존하고 DB guard나 pooled connection reset proof가 없다.

---

### Sprint 35 — Generated TypeScript SDK와 Web SDK 전환

**Phase:** OSDK-lite

**문서 연결:** [OSDK-lite 설계](./foundry_lite_development_plan_ko_sprintified.md#16-osdk-lite-설계), [SDK 근거](./deep-research-report.md#기술-스택)

**무조건 성공시켜야 하는 Goal**

프론트엔드와 외부 앱이 raw REST endpoint를 직접 다루지 않고 ontology 타입과 action 메서드로 Foundry-lite를 사용하게 한다. SDK는 active ontology apiName을 계약으로 삼는다.

**반드시 완성해야 하는 것**

- active ontology에서 TypeScript types를 생성한다.
- objects get/query client를 생성한다.
- actions apply client를 생성한다.
- idempotencyKey와 expectedObjectVersion helper를 제공한다.
- generated SDK를 `packages/sdk-ts`에 두고 examples app에서 사용한다.
- Web Object Explorer 일부 코드를 SDK 기반으로 전환한다.

**Acceptance Gate**

- [x] `client.objects.Order.get('O-1001')`가 typed Order를 반환한다. (`packages/sdk-ts/src/generated.ts`, `tests/unit/test_sdk_ts_generation.py`) ([S35-A1](./docs/sprint-evidence-ledger.md#s35-a1))
- [x] `client.actions.ApproveOrder.apply(...)`가 parameter type check를 받는다. (`ApproveOrderParams`, `ApproveOrderApplyRequest`, `examples/sdk-demo.ts`) ([S35-A2](./docs/sprint-evidence-ledger.md#s35-a2))
- [x] ontology apiName 변경/삭제 시 SDK generation이 breaking change를 감지한다. (`pnpm quality:sdk-generated`, `test_sdk_generator_check_detects_api_name_drift`) ([S35-A3](./docs/sprint-evidence-ledger.md#s35-a3))
- [x] SDK smoke test가 generated client로 end-to-end action을 실행한다. (`tests/e2e/foundry-lite.spec.ts`) ([S35-A4](./docs/sprint-evidence-ledger.md#s35-a4))
- [x] Web이 최소 한 화면에서 raw fetch 대신 SDK를 사용한다. (`apps/web/index.html`, `apps/web/generated-sdk.js`) ([S35-A5](./docs/sprint-evidence-ledger.md#s35-a5))

**Demo / Proof**

examples/sdk-demo.ts에서 Order query와 ApproveOrder action을 타입 안정적으로 실행한다.

**이러면 성공으로 치지 않는다**

- SDK가 hand-written endpoint wrapper에 머무르고 ontology metadata에서 생성되지 않는다.
- Action params 타입이 any로 노출된다.
- SDK가 idempotency/concurrency 규칙을 숨기거나 생략한다.

---

### Sprint 36 — MVP E2E·성능·데이터 정합성 Release Gate

**Phase:** MVP Release Hardening

**문서 연결:** [테스트 전략](./foundry_lite_development_plan_ko_sprintified.md#24-테스트-전략), [Python 품질 게이트](./foundry_lite_python_engineering_guidelines_ko.md#16-ci-품질-게이트), [MVP Core Completion Gate](#mvp-core-completion-gate)

**무조건 성공시켜야 하는 Goal**

MVP 폐루프가 문서가 아니라 반복 가능한 자동 테스트와 데모 스크립트로 증명되게 한다. 이 스프린트를 통과하면 Foundry-lite v1 core MVP라고 부를 수 있다.

**반드시 완성해야 하는 것**

- Playwright E2E: dataset upload, transform run, ontology activate, object query, action apply, materialization 확인을 자동화한다.
- Integration test: connector sync → dataset commit → transform → index → action → materialization을 Testcontainers로 실행한다.
- Data correctness test: row count, primary key uniqueness, reindex hash comparison, action idempotency를 검증한다.
- local performance smoke: CSV 100k/1M rows, object query 100k rows target을 측정한다.
- Python 백엔드 품질 게이트 `ruff`, `mypy` 또는 `pyright`, `pytest`를 release gate에 포함한다.
- Python 백엔드 line/branch/function coverage 95% 이상을 release gate에 포함한다.
- 필수 integration test와 smoke test의 100% 실행/100% 통과를 release gate에 포함한다.
- README에 one-command demo를 작성한다.
- known limitations와 v1.5 backlog를 정리한다.

**Acceptance Gate**

- [x] 새 환경에서 seed부터 closed-loop demo까지 명령 하나 또는 명확한 runbook으로 재현된다. ([S36-P1](./docs/sprint-evidence-ledger.md#s36-p1), [S36-P2](./docs/sprint-evidence-ledger.md#s36-p2), [VERIFY-FULL-CI-GATE](./docs/sprint-evidence-ledger.md#verify-full-ci-gate))
- [x] E2E test가 CI에서 통과한다. ([VERIFY-FULL-CI-GATE](./docs/sprint-evidence-ledger.md#verify-full-ci-gate), [VERIFY-MVP-WEB-OBJECT-LINK](./docs/sprint-evidence-ledger.md#verify-mvp-web-object-link))
- [x] reindex 결과 hash가 initial index 결과와 일치한다. ([S36-P4](./docs/sprint-evidence-ledger.md#s36-p4), [VERIFY-FULL-CI-GATE](./docs/sprint-evidence-ledger.md#verify-full-ci-gate))
- [x] Action apply no external writeback p95 목표에 대한 최소 측정 리포트가 있다. ([S36-P5](./docs/sprint-evidence-ledger.md#s36-p5), [VERIFY-FULL-CI-GATE](./docs/sprint-evidence-ledger.md#verify-full-ci-gate))
- [x] Python 백엔드 release quality gate가 모두 통과한다. ([VERIFY-STATIC](./docs/sprint-evidence-ledger.md#verify-static), [VERIFY-FULL-CI-GATE](./docs/sprint-evidence-ledger.md#verify-full-ci-gate))
- [x] Python 백엔드 line/branch/function coverage가 모두 95% 이상이다. ([VERIFY-FULL-CI-GATE](./docs/sprint-evidence-ledger.md#verify-full-ci-gate))
- [x] 필수 integration test와 smoke test가 100% 실행되고 100% 통과한다. ([VERIFY-FULL-CI-GATE](./docs/sprint-evidence-ledger.md#verify-full-ci-gate))
- [x] MVP release tag를 찍을 수 있는 상태다. 실제 태그 생성은 릴리스 버전 결정 시 별도 release action으로 수행한다. ([VERIFY-FULL-CI-GATE](./docs/sprint-evidence-ledger.md#verify-full-ci-gate))

**Sprint 36 구현 진행 체크**

- [x] `pnpm demo:supply-chain`은 `FOUNDRY_LITE_HOME`이 명시되지 않은 경우 `.foundry-lite-demo/` 격리 저장소에서 fresh 실행되어, 이전 로컬 DB 상태에 의존하지 않는다. ([S36-P1](./docs/sprint-evidence-ledger.md#s36-p1))
- [x] CLI smoke regression test가 같은 supply-chain demo 명령을 두 번 연속 실행하고 두 출력 모두 JSON으로 파싱되는지 검증한다. ([S36-P2](./docs/sprint-evidence-ledger.md#s36-p2))
- [x] `pnpm ci:gate`는 supply-chain demo smoke 산출물 `artifacts/demo/supply-chain.json`을 `python -m json.tool`로 다시 파싱해, 로그가 섞인 가짜 JSON 산출물을 release evidence로 인정하지 않는다. ([S36-P3](./docs/sprint-evidence-ledger.md#s36-p3))
- [x] `check_mvp_data_correctness.py`가 demo DB의 row count, object primary key uniqueness, Order reindex source hash, ApproveOrder idempotency evidence를 release gate에서 검증한다. ([S36-P4](./docs/sprint-evidence-ledger.md#s36-p4))
- [x] `check_mvp_performance_smoke.py`가 CSV ingest, object index, object query, no-writeback action apply 측정 리포트를 남기며, CI fast profile과 100k/1M release profile 명령을 분리한다. ([S36-P5](./docs/sprint-evidence-ledger.md#s36-p5))
- [x] `tests/contracts/test_mvp_testcontainers_closed_loop.py`가 Testcontainers PostgreSQL 저장소 위에서 connector snapshot → dataset commit → transform → index → action → materialization 폐쇄루프를 검증한다. ([S36-P6](./docs/sprint-evidence-ledger.md#s36-p6))

**Demo / Proof**

`pnpm demo:supply-chain` 실행 후 Web에서 full closed-loop 결과를 확인한다.

**이러면 성공으로 치지 않는다**

- 데모가 특정 개발자 로컬 DB 상태에 의존한다.
- E2E는 통과하지만 data correctness/replay 검증이 없다.
- 성능 목표를 측정하지 않고 감으로만 통과시킨다.

---

### Sprint 36A — MVP 운영 안정성 보강

**Phase:** MVP Core Hardening

**문서 연결:** [Implementation Status](./docs/implementation-status.md#still-targeted-not-yet-implemented), [Action Runtime 설계](./foundry_lite_development_plan_ko_sprintified.md#12-action-runtime-설계), [Object Query execution](./foundry_lite_development_plan_ko_sprintified.md#114-execution-strategy)

**무조건 성공시켜야 하는 Goal**

Sprint 00~36으로 닫은 MVP 폐루프를 바로 v1.5 connector/streaming 확장으로 밀어붙이기 전에, 운영 중 실제로 자주 터지는 동시성, 페이징, 인증 프로필, 생성 코드 중복 리스크를 먼저 줄인다. 이 스프린트는 새 기능을 크게 늘리는 것이 아니라, 이미 있는 action/dataset/object/operations/SDK 경로가 많은 요청과 재시도 상황에서도 예측 가능하게 동작하도록 만드는 안정화 단계다.

**반드시 완성해야 하는 것**

- 같은 actor/action/target/idempotency key가 거의 동시에 들어와 데이터베이스 unique 충돌이 발생해도, 두 번째 요청은 500 또는 새 실행이 아니라 기존 action run replay로 귀결되게 한다.
- Dataset commit은 version 번호 배정 경합을 명확히 막고, 파일 복사 후 DB commit이 실패한 경우 이미 promote된 artifact를 자동 정리하며 실패 payload/audit로 추적 가능한 orphan cleanup evidence를 남긴다.
- Object Query 목록은 메모리 정렬/슬라이스에 의존하지 않고 DB에서 filter, sort, limit을 수행하며, signed opaque cursor token, sort key, query shape checksum, `object_id` tie-breaker가 포함된 안정적인 keyset cursor를 사용한다.
- Object Query는 ontology property metadata로 filter/order property 존재 여부를 서비스에서 검증하고, repository는 숫자 property JSON 값이 문자열로 들어와도 SQL `CAST` 기반 정렬/비교를 사용한다.
- Dynamic Object Set membership도 Object Query의 page limit과 cursor를 그대로 사용해 내부 기능이 대량 limit으로 query cap을 우회하지 못하게 한다.
- Operations run 목록도 `created_at` 또는 `failed_at`과 run id를 기준으로 한 DB-backed cursor paging을 사용해 run 수가 늘어나도 한 번에 전체 row를 읽지 않는다.
- 운영 모드에서는 header-trust 인증 프로필이 선택되면 앱이 시작 단계에서 실패해야 한다. 로컬/demo 모드에서만 명시적으로 허용한다.
- Generated TypeScript package output과 browser-ready SDK output은 같은 생성 템플릿 또는 같은 intermediate model에서 나오며, parity test가 두 출력의 API drift를 막는다.

**Acceptance Gate**

- [x] 같은 Idempotency-Key 요청 2개를 동시에 보내도 action_run은 1개만 남고 두 응답은 같은 실행 결과를 가리킨다. ([S36A-A1](./docs/sprint-evidence-ledger.md#s36a-a1))
- [x] dataset version commit을 동시에 시도해도 version_number 중복이나 순서 역전이 생기지 않는다. ([S36A-A2](./docs/sprint-evidence-ledger.md#s36a-a2))
- [x] commit 실패로 생긴 promoted/orphan file은 failed error details 또는 abort audit evidence로 찾을 수 있고 자동 cleanup execute 경로가 있다. ([S36A-A3](./docs/sprint-evidence-ledger.md#s36a-a3))
- [x] Object Query는 sort key와 object_id tie-breaker를 포함한 cursor로 다음 page를 안정적으로 반환한다. ([S36A-A4](./docs/sprint-evidence-ledger.md#s36a-a4))
- [x] Object Query cursor는 raw object_id나 변조된 base64 payload를 `ValidationFailed`로 거절한다. ([S36A-A5](./docs/sprint-evidence-ledger.md#s36a-a5))
- [x] Object Query는 존재하지 않는 filter/order property를 `ValidationFailed`로 거절하고, 숫자 property 문자열 값도 fake/SQLite/Postgres contract에서 같은 순서로 page 처리한다. ([S36A-A6](./docs/sprint-evidence-ledger.md#s36a-a6))
- [x] Dynamic Object Set은 `Object Query` page limit 안에서 cursor로 전체 membership을 이어 읽는다. ([S36A-A7](./docs/sprint-evidence-ledger.md#s36a-a7))
- [x] Operations runs API/CLI/UI는 cursor 기반으로 page를 나누고 대량 run fixture에서도 일정한 응답 크기를 유지한다. ([S36A-A8](./docs/sprint-evidence-ledger.md#s36a-a8))
- [x] production auth profile에서 header-trust provider를 쓰면 startup이 실패하고, local/demo profile에서는 명시적으로만 허용된다. ([S36A-A9](./docs/sprint-evidence-ledger.md#s36a-a9))
- [x] SDK package output과 browser output이 같은 object/action method surface를 노출하는지 테스트가 검증한다. ([S36A-A10](./docs/sprint-evidence-ledger.md#s36a-a10))
- [x] 동일 Idempotency-Key라도 요청 본문이 다르면 기존 action_run replay가 아니라 conflict로 막는다. ([S36A-A11](./docs/sprint-evidence-ledger.md#s36a-a11))

**Demo / Proof**

동시 action replay, dataset version allocation lock, metadata persistence failure cleanup, DB-backed object query keyset paging, API/CLI operations cursor paging, production auth profile startup, SDK generation parity를 각각 작은 재현 테스트로 보여준다.

**이러면 성공으로 치지 않는다**

- idempotency unique 충돌을 일반 DB 에러 또는 임시 retry로만 처리한다.
- dataset commit이 version_number를 max+1로 계산하면서 lock/unique conflict cleanup 전략이 없다.
- object query나 operations 목록이 DB에서 page를 자르지 않고 애플리케이션 메모리에서 전체 목록을 자른다.
- operations cursor가 timestamp와 run id tie-breaker 없이 단순 run id만 담거나, query shape이 다른 요청에 재사용된다.
- dynamic object set이 Object Query page limit을 피하려고 내부에서 큰 limit 값을 직접 요청한다.
- object query cursor가 sort key, query shape checksum, `object_id` tie-breaker, tamper check 없이 단순 object id만 담거나 raw object_id cursor를 허용한다.
- 운영 배포에서 header-trust 인증이 실수로 켜질 수 있다.
- SDK 출력 2개가 서로 다른 문자열 템플릿에서 만들어지는데 parity test가 없다.

---

### Sprint 37 — REST Pull Connector와 Webhook Listener

**Phase:** v1.5 Data Connection

**문서 연결:** [v1.5 이후 기능](./foundry_lite_development_plan_ko_sprintified.md#23-v15-이후로-이관한-기능), [Data Connection 근거](./deep-research-report.md#종단간-데이터-흐름)

**무조건 성공시켜야 하는 Goal**

파일/DB snapshot 외에 API 기반 유입과 push ingest를 추가한다. 단, MVP core를 흔들지 않고 Source/Sync/Transaction framework 위에 connector만 추가하는 방식으로 구현한다.

**반드시 완성해야 하는 것**

- REST source config: baseUrl, auth, pagination, cursor, rate limit을 정의한다.
- REST pull sync가 response를 Parquet dataset으로 commit한다.
- Webhook listener endpoint와 secret verification을 구현한다.
- Webhook payload를 raw event dataset 또는 append dataset으로 저장한다.
- error/retry/rate-limit 상태를 sync_runs에 기록한다.
- connector별 contract test를 작성한다.

**Acceptance Gate**

- [x] mock REST API에서 orders를 pull해 raw dataset으로 commit한다. ([S37-A1](./docs/sprint-evidence-ledger.md#s37-a1))
- [x] REST adapter는 응답의 `nextCursor`를 반환하고, 전달받은 cursor를 다음 요청에 실어 보낼 수 있다. ([S37-A2](./docs/sprint-evidence-ledger.md#s37-a2))
- [x] 실패/중단된 REST sync가 운영자가 cursor를 다시 입력하지 않아도 durable state에서 이어받을 수 있고, 실패한 page fetch의 cursor가 committed cursor보다 앞서가지 않는다. ([S37-A7](./docs/sprint-evidence-ledger.md#s37-a7))
- [x] webhook event가 append transaction으로 raw dataset에 쌓인다. ([S37-A3](./docs/sprint-evidence-ledger.md#s37-a3))
- [x] 같은 webhook event가 두 번 들어와도 중복 dataset row/version을 만들지 않는다. ([S37-A8](./docs/sprint-evidence-ledger.md#s37-a8))
- [x] 잘못된 signature webhook은 거부되고 audit deny가 남는다. ([S37-A4](./docs/sprint-evidence-ledger.md#s37-a4))
- [x] REST source URL은 localhost/private/link-local/internal metadata 주소를 차단한다. ([S37-A6](./docs/sprint-evidence-ledger.md#s37-a6))
- [x] REST rate-limit 실패와 Webhook signature deny가 기존 Operations 조회 표면에서 보인다. ([S37-A5](./docs/sprint-evidence-ledger.md#s37-a5))

**Demo / Proof**

mock SaaS REST source와 webhook source를 만들어 raw datasets로 유입한다.

**이러면 성공으로 치지 않는다**

- REST/Webhook connector가 dataset transaction protocol을 우회한다.
- pagination cursor가 없어 실패 후 처음부터만 재시작한다.
- webhook secret 검증이 없다.

---

### Sprint 38 — Redpanda/Kafka Stream Archive Writer

**Phase:** v1.5 Streaming

**문서 연결:** [CDC ingest, Phase 6](./foundry_lite_development_plan_ko_sprintified.md#65-cdc-ingest-phase-6), [streaming 근거](./deep-research-report.md#종단간-데이터-흐름)

**무조건 성공시켜야 하는 Goal**

Kafka-compatible stream event를 raw archive dataset으로 남겨 replay 가능한 stream ingestion 기반을 만든다. 이 Sprint 38 단계에서는 object CDC indexing 전 단계로 stream offset/checkpoint를 안정적으로 저장하는 것이 핵심이며, 현재 checkout에는 Sprint 40 CDC object-indexing proof가 따로 존재한다.

**반드시 완성해야 하는 것**

- Stream source와 topic subscription config를 정의한다.
- consumer worker가 topic/partition/offset cursor를 저장한다.
- messages를 micro-batch로 Parquet append transaction에 쓴다.
- schema strategy를 정의한다: envelope JSON 또는 inferred schema.
- at-least-once archive writer와 idempotent file/offset commit protocol을 구현한다.
- stream lag metrics를 기록한다.

**Acceptance Gate**

- [x] local/fake Kafka-compatible `StreamAdapter` event를 raw stream archive dataset에 append한다. ([S38-A1](./docs/sprint-evidence-ledger.md#s38-a1))
- [x] production Kafka-compatible broker topic에 event를 넣으면 raw stream archive dataset에 append된다. ([S38-A2](./docs/sprint-evidence-ledger.md#s38-a2))
- [x] worker restart 후 마지막 committed offset 이후부터 재개하며, 실패한 archive write의 offset이 durable cursor로 먼저 저장되지 않는다. ([S38-A3](./docs/sprint-evidence-ledger.md#s38-a3))
- [x] 중복 처리 가능성은 event id 또는 topic/partition/offset으로 식별 가능하다. ([S38-A4](./docs/sprint-evidence-ledger.md#s38-a4))
- [x] stream archive dataset preview가 가능하다. ([S38-A5](./docs/sprint-evidence-ledger.md#s38-a5))
- [x] lag metric과 stream writer 실패가 Operations에 보인다. ([S38-A6](./docs/sprint-evidence-ledger.md#s38-a6))

**Demo / Proof**

현재 증명은 local/fake `StreamAdapter.publish_event` → `FoundryLiteCore.archive_stream_events` → `raw.shipment_events` append version 생성에 더해, 실패한 archive write가 offset cursor를 먼저 전진시키지 않는 regression까지 포함한다. 또한 `tests/integration/test_kafka_live_broker_stream_archive.py`가 `KafkaContainer` live broker를 띄우고 `KafkaStreamAdapter.publish_event`로 실제 topic에 event를 넣은 뒤 `foundry_lite_worker.stream_archive.run_stream_archive_once`가 같은 `archive_stream_events` application boundary를 통해 raw archive dataset version을 commit하는 경로까지 확인한다.

**이러면 성공으로 치지 않는다**

- offset commit과 dataset commit이 불일치해 유실이 가능하다.
- stream event를 object store에만 반영하고 archive dataset을 남기지 않는다.
- restart 후 replay/skip 정책이 없다.
- local/fake stream adapter proof만으로 production Kafka-compatible worker까지 완료했다고 표시한다.

---

### Sprint 39 — Debezium PostgreSQL CDC Connector

**Phase:** v1.5 CDC

**문서 연결:** [CDC ingest, Phase 6](./foundry_lite_development_plan_ko_sprintified.md#65-cdc-ingest-phase-6), [CDC/stream 근거](./deep-research-report.md#종단간-데이터-흐름)

**무조건 성공시켜야 하는 Goal**

PostgreSQL row 변경을 Debezium envelope로 받아 raw changelog dataset과 object indexing input으로 사용할 수 있게 한다. 이 스프린트의 핵심은 CDC envelope 표준화와 ordering metadata 보존이다.

**반드시 완성해야 하는 것**

- Debezium Postgres docker profile을 추가한다.
- CDC source/sync definition을 만든다.
- CDC envelope 표준 `op`, `pk`, `before`, `after`, `ordering`, `ingested_at`으로 normalize한다.
- CDC topic을 stream archive writer와 연결한다.
- lsn/source_ts_ms ordering metadata를 저장한다.
- delete event representation을 명확히 한다.

**Acceptance Gate**

- [x] mock ERP orders row insert/update/delete가 CDC topic으로 나온다. ([S39-A1](./docs/sprint-evidence-ledger.md#s39-a1))
- [x] CDC event가 raw changelog dataset에 append된다. ([S39-A2](./docs/sprint-evidence-ledger.md#s39-a2))
- [x] pk와 ordering metadata가 preview에서 확인된다. ([S39-A3](./docs/sprint-evidence-ledger.md#s39-a3))
- [x] delete event는 after=null 또는 tombstone 정책으로 표준화된다. ([S39-A4](./docs/sprint-evidence-ledger.md#s39-a4))
- [x] CDC connector 실패/lag가 Operations에서 보인다. ([S39-A5](./docs/sprint-evidence-ledger.md#s39-a5))

**Demo / Proof**

현재 증명은 두 층이다. 빠른 계약 증명은 Debezium-shaped insert/update/delete stream event를 `DebeziumPostgresStreamAdapter`가 표준 CDC envelope `op`, `pk`, `before`, `after`, `ordering`으로 normalize하고, `StreamArchiveConfig(schema_strategy="cdc_envelope_json")`가 `FoundryLiteCore.archive_stream_events` application boundary를 통해 `raw_cdc.erp_orders` append version을 commit하는 경로다. Live 증명은 Testcontainers로 Kafka-compatible broker, logical replication PostgreSQL, Debezium Connect를 띄운 뒤 `public.orders` insert/update/delete가 Debezium topic에 나오고 worker가 같은 application boundary로 raw CDC changelog를 commit하는 경로다. CDC read 실패는 Operations의 FAILED sync run에 Debezium adapter failure payload로 남고, unread CDC event 수는 `foundry_lite_stream_archive_lag_events` metric으로 보인다. CDC event가 object store base layer를 직접 갱신하는 작업은 Sprint 40에서 `index_cdc_events` 증분 object indexing proof로 이어졌다.

**이러면 성공으로 치지 않는다**

- Debezium raw payload를 표준 envelope 없이 그대로 downstream에 노출한다.
- ordering metadata가 사라져 out-of-order resolution이 불가능하다.
- delete event 의미가 connector별로 다르다.

---

### Sprint 40 — CDC Object Indexing과 Delete/Tombstone 처리

**Phase:** v1.5 CDC

**문서 연결:** [CDC ingest, Phase 6](./foundry_lite_development_plan_ko_sprintified.md#65-cdc-ingest-phase-6), [CDC/stream 근거](./deep-research-report.md#종단간-데이터-흐름)

**무조건 성공시켜야 하는 Goal**

배치 rebuild 없이 CDC event가 object store의 base layer를 갱신하게 한다. update ordering, delete semantics, idempotent event processing을 명확히 구현한다.

**반드시 완성해야 하는 것**

- Ontology backing에 CDC source mapping을 추가한다.
- Funnel-lite CDC consumer가 mapping으로 object_id를 생성한다.
- op c/u/r은 base patch upsert로 처리한다.
- op d는 source_deleted/tombstoned 상태로 처리한다.
- ordering metadata를 property_versions 또는 object source cursor에 저장한다.
- out-of-order/stale event는 skip하거나 conflict로 기록한다.
- CDC index run/event processing status를 기록한다.

**Acceptance Gate**

- [x] ERP DB row update 후 Order object가 batch rebuild 없이 바뀐다. ([S40-A1](./docs/sprint-evidence-ledger.md#s40-a1))
- [x] delete event 후 object는 정책에 따라 deleted/tombstoned로 표시된다. ([S40-A2](./docs/sprint-evidence-ledger.md#s40-a2))
- [x] 같은 CDC event 재처리는 idempotent하다. ([S40-A3](./docs/sprint-evidence-ledger.md#s40-a3))
- [x] stale CDC event가 최신 object state를 덮지 않는다. ([S40-A4](./docs/sprint-evidence-ledger.md#s40-a4))
- [x] CDC update도 object.changed event와 materialization trigger를 만든다. ([S40-A5](./docs/sprint-evidence-ledger.md#s40-a5))

**Demo / Proof**

`backing.cdc` source mapping이 있는 `Order` ontology에서 초기 snapshot index 후 `index_cdc_events`가 CDC `u/d` event를 object base layer에 증분 반영한다. update event는 batch rebuild 없이 `Order/O-1001` status를 바꾸고 `object.changed` outbox trigger를 남긴다. 같은 event 재처리와 더 낮은 ordering의 stale event는 skip되어 최신 object state를 덮지 않는다. delete event는 object record를 `source_deleted` tombstone으로 표시하고 active object query에서 제외한다.

**이러면 성공으로 치지 않는다**

- CDC update마다 전체 dataset snapshot reindex를 요구한다.
- out-of-order event가 최신 object를 과거 값으로 덮는다.
- delete를 단순 row 삭제로 처리해 audit/replay가 불가능하다.

---

### Sprint 41 — Shadow Reindex와 Hash Validation

**Phase:** Scale/Reindex

**문서 연결:** [Reindex 설계](./foundry_lite_development_plan_ko_sprintified.md#106-reindex), [운영 프랙티스 근거](./deep-research-report.md#엔지니어링-프랙티스)

**무조건 성공시켜야 하는 Goal**

Ontology mapping 변경이나 index corruption 상황에서 live read를 막지 않고 object index를 재생성할 수 있게 한다. 이 스프린트는 replay-first 운영 능력의 핵심이다.

**반드시 완성해야 하는 것**

- reindex run mode를 full/shadow로 구분한다.
- shadow object table 또는 shadow index namespace 전략을 구현한다.
- dataset snapshot + action edits replay 순서를 고정한다.
- count/hash validation을 구현한다.
- alias switch 또는 active_index_version pointer를 구현한다.
- old index retention/cleanup 정책을 만든다.

**Acceptance Gate**

- [x] live Object Query 중 shadow reindex를 실행해도 reads가 계속 된다. ([S41-A1](./docs/sprint-evidence-ledger.md#s41-a1))
- [x] validation 성공 후 alias switch로 새 index가 serving된다. ([S41-A2](./docs/sprint-evidence-ledger.md#s41-a2))
- [x] validation 실패 시 기존 index가 유지된다. ([S41-A3](./docs/sprint-evidence-ledger.md#s41-a3))
- [x] reindex 후 object count/hash가 baseline과 일치한다. ([S41-A4](./docs/sprint-evidence-ledger.md#s41-a4))
- [x] action edits replay 후 current view가 기존과 같다. ([S41-A5](./docs/sprint-evidence-ledger.md#s41-a5))
- [x] object row가 0개인 object type도 active index version을 잃지 않는다. ([S41-H1](./docs/sprint-evidence-ledger.md#s41-h1))
- [x] 같은 object type의 shadow promotion이 겹치면 stale promotion은 조용히 덮어쓰지 않고 실패한다. ([S41-H2](./docs/sprint-evidence-ledger.md#s41-h2))

**Demo / Proof**

Order shadow reindex 실행 중 UI query 지속 → validation → switch → old cleanup.
추가 hardening proof로, 빈 snapshot object type에서 shadow reindex가 `expected=0`, `actual=0`으로 성공한 뒤에도 `object_index_versions` registry에 새 active index version이 남고, 다음 CDC insert가 그 버전으로 기록된다.
동시성 proof로, 두 PostgreSQL connection이 같은 tenant/object type의 첫 pointer를 동시에 만들려고 해도 하나만 promotion에 성공하고 다른 하나는 stale switch로 거절된다.

**이러면 성공으로 치지 않는다**

- reindex가 기존 object_records를 먼저 truncate한다.
- action edits replay 없이 base dataset만 재색인한다.
- validation 없이 새 index로 switch한다.

---

### Sprint 42 — Elasticsearch Adapter for Search-heavy Object Types

**Phase:** Scale/Search

**문서 연결:** [Object Query execution](./foundry_lite_development_plan_ko_sprintified.md#114-execution-strategy), [검색/서빙 근거](./deep-research-report.md#종단간-데이터-흐름)

**무조건 성공시켜야 하는 Goal**

PostgreSQL JSONB query의 한계를 넘는 full-text/search-heavy object type을 위해 Elasticsearch adapter를 추가한다. 단, source of truth는 object store이며 search index는 재생성 가능한 projection으로 유지한다.

**반드시 완성해야 하는 것**

- SearchIndexAdapter interface를 정의한다.
- Elasticsearch adapter를 구현한다.
- object.changed event를 search index update로 소비한다.
- index mapping은 ontology indexed/searchable property에서 생성한다.
- search index rebuild CLI를 구현한다.
- Postgres-only와 Elasticsearch-enabled query planner를 분기한다.

**Acceptance Gate**

- [x] searchable property full-text query가 Elasticsearch로 실행된다. ([S42-A1](./docs/sprint-evidence-ledger.md#s42-a1))
- [x] object edit 후 search index가 업데이트된다. ([S42-A2](./docs/sprint-evidence-ledger.md#s42-a2))
- [x] Elasticsearch 장애 시 get by id와 basic Postgres filter는 계속 동작한다. ([S42-A3](./docs/sprint-evidence-ledger.md#s42-a3))
- [x] search index rebuild 결과가 object_records count와 맞는다. ([S42-A4](./docs/sprint-evidence-ledger.md#s42-a4))
- [x] Elasticsearch에만 있고 object store에 없는 document를 detect할 수 있다. ([S42-A5](./docs/sprint-evidence-ledger.md#s42-a5))

**Demo / Proof**

Order `operatorNote`를 searchable property로 표시하고, `ObjectQueryService`가 `search` payload를 `SearchIndexAdapter` 경로로 분기한다. `ElasticsearchAdapter`는 ontology indexed/searchable mapping으로 index mapping을 만들며, `flite index rebuild-search Order`와 `FoundryLiteCore.index_search_rebuild`는 object store의 active `object_records`를 검색 projection으로 다시 쓴 뒤 count 및 orphan document drift를 확인한다. `object.changed` 소비 경로는 `FoundryLiteCore.index_search_object_changed`와 `flite index consume-search-change Order <object-id>`로 증명한다.

**이러면 성공으로 치지 않는다**

- Elasticsearch를 source of truth처럼 사용한다.
- ontology property 변경 시 search mapping 갱신/재색인 전략이 있어야 한다.
- search index 실패가 action commit을 막지 않고, source of truth인 object store와 Operations evidence로 원인을 추적할 수 있다.

---

### Sprint 43 — Iceberg StorageAdapter Prototype

**Phase:** Scale/Lakehouse

**문서 연결:** [Scale path](./foundry_lite_development_plan_ko_sprintified.md#34-scale-path), [MMDP 근거](./deep-research-report.md#기술-스택)

**무조건 성공시켜야 하는 Goal**

Parquet manifest 기반 Dataset transaction 모델을 Iceberg table로 확장할 수 있음을 증명한다. v1 core logic은 유지하고 storage adapter만 교체 가능한지 검증한다.

**반드시 완성해야 하는 것**

- CatalogAdapter/StorageAdapter의 Iceberg-specific 확장 지점을 정의한다.
- Iceberg REST/Nessie/Polaris 중 하나를 local profile로 선택한다.
- dataset storage_kind `iceberg`를 지원한다.
- SNAPSHOT/APPEND commit을 Iceberg metadata commit으로 연결한다.
- schema evolution mapping을 Dataset Schema Registry와 연결한다.
- DuckDB 또는 compatible reader로 Iceberg preview를 구현한다.

**Acceptance Gate**

- [x] 새 iceberg dataset을 만들고 append/snapshot commit할 수 있다. ([VERIFY-ICEBERG-RATCHET](./docs/sprint-evidence-ledger.md#verify-iceberg-ratchet))
- [x] 기존 transform runner가 storage_kind 차이를 몰라도 input을 읽는다. ([VERIFY-INFRA-COMPOSITION-RATCHET](./docs/sprint-evidence-ledger.md#verify-infra-composition-ratchet))
- [x] dataset_versions가 Iceberg metadata location/snapshot id를 참조한다. ([VERIFY-ICEBERG-RATCHET](./docs/sprint-evidence-ledger.md#verify-iceberg-ratchet))
- [x] schema evolution compatible/breaking 판정이 기존 policy와 일치한다. ([VERIFY-ICEBERG-RATCHET](./docs/sprint-evidence-ledger.md#verify-iceberg-ratchet))
- [x] Parquet manifest dataset과 Iceberg dataset이 같은 Dataset API로 조회된다. ([VERIFY-ICEBERG-RATCHET](./docs/sprint-evidence-ledger.md#verify-iceberg-ratchet))

**Demo / Proof**

`raw.erp_orders_iceberg` sync → transform input으로 사용 → preview/lineage 확인. 현재 evidence는 `quality:iceberg`와 `quality:infra-composition`이며, production Iceberg catalog operations와 managed maintenance는 post-MVP future scope다.

**이러면 성공으로 치지 않는다**

- Iceberg 지원을 위해 Dataset API와 Transform API가 별도로 갈라진다.
- Iceberg metadata commit과 dataset_versions commit이 불일치한다.
- schema registry와 Iceberg schema가 따로 논다.

---

### Sprint 44 — Spark Runner Adapter Skeleton

**Phase:** Scale/Compute

**문서 연결:** [v1 adapter boundary](./foundry_lite_development_plan_ko_sprintified.md#35-v1-adapter-boundary), [compute 근거](./deep-research-report.md#기술-스택)

**무조건 성공시켜야 하는 Goal**

대용량 batch transform을 위해 Spark runner로 확장 가능한 compute adapter를 만든다. 즉시 완성형 Spark platform이 아니라, DuckDB runner와 같은 transform contract를 Spark에서도 지키는 것이 목적이다.

**반드시 완성해야 하는 것**

- ComputeAdapter interface를 정리한다: prepareInputs, run, collectOutputs, commitOutputs.
- Spark runner config와 job submission abstraction을 만든다.
- local Spark 또는 containerized Spark profile을 제공한다.
- Spark SQL transform 최소 실행을 구현한다.
- Spark output도 Dataset transaction commit protocol을 사용한다.
- resource config memory/cores/timeout을 transform definition에서 받는다.

**Acceptance Gate**

- [x] 같은 `clean_orders.sql`을 DuckDB와 Spark runner 중 하나로 실행할 수 있다. ([VERIFY-SPARK-RATCHET](./docs/sprint-evidence-ledger.md#verify-spark-ratchet))
- [x] Spark 실패 시 output transaction이 abort된다. ([VERIFY-SPARK-RATCHET](./docs/sprint-evidence-ledger.md#verify-spark-ratchet))
- [x] Spark run도 lineage와 health checks를 남긴다. ([VERIFY-SPARK-RATCHET](./docs/sprint-evidence-ledger.md#verify-spark-ratchet))
- [x] ComputeAdapter 교체로 transform service core를 수정하지 않는다. ([VERIFY-SPARK-RATCHET](./docs/sprint-evidence-ledger.md#verify-spark-ratchet))
- [x] 작은 fixture data로 Spark runner integration test가 통과한다. ([VERIFY-SPARK-RATCHET](./docs/sprint-evidence-ledger.md#verify-spark-ratchet))

**Demo / Proof**

`quality:spark`와 `quality:infra-composition`으로 DuckDB/Spark parity, Spark output commit/abort, lineage/health, S3+Iceberg+Spark composition을 확인한다. Spark cluster deployment, speculative execution, executor-output-missing 같은 분산 클러스터 전용 failure는 future scope다.

**이러면 성공으로 치지 않는다**

- Spark runner가 별도 transform registry나 별도 lineage 모델을 쓴다.
- Spark output이 object storage path를 직접 최종 위치에 쓴다.
- resource/timeout 실패가 run state에 반영되지 않는다.

---

### Sprint 45 — Kubernetes Helm·Backup/Restore·Operational Runbook

**Phase:** Deployment/Operations

**문서 연결:** [Scale hardening](./foundry_lite_development_plan_ko_sprintified.md#phase-7--scale-hardening), [배포/운영 근거](./deep-research-report.md#엔지니어링-프랙티스)

**무조건 성공시켜야 하는 Goal**

로컬 Docker Compose를 넘어 small production 형태로 배포 가능한 운영 패키지를 만든다. 이 스프린트는 기능 개발보다 장애 복구와 배포 재현성에 초점을 둔다.

**반드시 완성해야 하는 것**

- Helm chart skeleton을 만든다: api, web, worker, migrations job.
- Postgres/MinIO/Temporal은 external dependency 또는 dev subchart 옵션으로 둔다.
- config/secrets/env var 계약을 문서화한다.
- DB backup/restore 절차를 문서화한다.
- object storage manifest/file consistency check CLI를 만든다.
- deployment health/readiness/liveness probe를 정의한다.
- upgrade migration runbook을 작성한다.

**Acceptance Gate**

- [ ] kind/minikube 또는 dev cluster에 Helm install이 성공한다.
- [ ] migration job이 idempotent하게 실행된다.
- [ ] backup DB + object storage에서 demo tenant를 복원할 수 있다.
- [ ] readiness fail 시 traffic을 받지 않는다.
- [ ] 운영자가 runbook만 보고 기본 배포/복구를 수행할 수 있다.

**Demo / Proof**

깨끗한 k8s namespace에 install → seed demo → backup → restore → object query 성공.

**이러면 성공으로 치지 않는다**

- 운영 배포가 개발자 로컬 `.env`에 의존한다.
- migration이 앱 시작 시 임의로 실행되어 race condition이 생긴다.
- DB backup만 있고 object storage manifest/file 복구 검증이 없다.

---

## Post-MVP Data Platform Expansion Roadmap

Sprint 46 이후의 상세 순서와 공통 Exit Checklist는 [Data Platform Expansion Roadmap](./docs/data-platform-expansion-roadmap.md)을 원본으로 본다. 이 섹션은 기존 Sprint Breakdown이 S45에서 끊겨 보이지 않도록 연결하는 요약이다. 각 항목은 proposed 상태이며, 체크박스는 실제 코드/테스트/CI/docs 증거가 생길 때만 `[x]`로 바꾼다.

| Sprint | 우선순위 | 핵심 결과 | 의존성 | 상태 |
|---|---:|---|---|---|
| S46 | P0 | Semantic SSOT + Data Pattern Matrix | 현재 CI 하네스 | [ ] Proposed |
| S47 | P0 | Record DLQ + Replay | S46 | [ ] Proposed |
| S48 | P1 | Late Data + Watermark | S47 | [ ] Proposed |
| S49 | P1 | Multi-file Dataset + Partitioning | S46 | [ ] Proposed |
| S50 | P1 | Iceberg Maintenance | S49 | [ ] Proposed |
| S51 | P0 | Continuous CDC Worker + Rebalance Safety | S47 | [ ] Proposed |
| S52 | P0 | Temporal Engine Integration | S51 | [ ] Proposed |
| S53 | P0 | External Writeback + Saga/Reconciliation | S52 | [ ] Proposed |
| S54 | P1 | Data Quality Contracts | S47, S48 | [ ] Proposed |
| S55 | P1 | DB/Dataset/Ontology Schema Migration | S54 | [ ] Proposed |
| S56 | P1 | Proactive Observability + SLO | S48, S51, S52 | [ ] Proposed |
| S57 | P0 | Backup/Restore Commit-point Ratchet | S50, S52, S53 | [ ] Proposed |
| S58A | P1 | OIDC/JWT + Secret Provider | 독립 가능 | [ ] Proposed |
| S58B | P1 | Anonymization/Pseudonymization | S58A | [ ] Proposed |
| S58C | P1 | Right-to-Erasure Lifecycle | S50, S57, S58B | [ ] Proposed |
| S59 | P2 | Real Cluster/Cloud/Chaos Proofs | 관련 sprint | [ ] Proposed |
| S60 | P1 | Fine-grained Lineage + AI Evidence | S54, S55 | [ ] Proposed |
| S61 | Product | Frontend Foundation + Generated SDK | 현재 API | [ ] Proposed |
| S62 | Product | Object/Dataset Explorer | S61 | [ ] Proposed |
| S63 | Product | Insight/Action Workspace | S61, S53, S60 | [ ] Proposed |
| S64 | Product | Operations/Recovery Console | S47, S51, S52, S56, S57 | [ ] Proposed |

첫 실행 순서는 `S46 -> S47 -> S48 -> S51 -> S52 -> S53`이다. Scale path는 `S49 -> S50 -> S57`, Product surface path는 `S61 -> S62 -> S63 -> S64`로 병렬 진행한다.

---

## MVP Core Completion Gate

Sprint 00~36과 Sprint 02A가 끝났을 때 아래가 모두 가능해야 MVP core 완료다. 각 완료 표시는 [MVP Core Completion Gate Evidence Map](./docs/sprint-evidence-ledger.md#mvp-core-completion-gate-evidence-map)의 현재 증거 범위 안에서만 의미한다. real ERP writeback, production backup/restore, Kafka/CDC/Iceberg 같은 확장은 Sprint 37 이후 또는 future backlog로 남긴다.

- [x] CSV/local snapshot 또는 PostgreSQL-backed repository closed-loop path로 raw dataset을 commit한다. ([MVP-RAW](./docs/sprint-evidence-ledger.md#mvp-core-raw-dataset))
- [x] Scale Foundation boundary가 있어 storage/metadata/compute/event/search/workflow/connector/auth infra를 port/adapter 뒤에서 교체할 수 있다. ([MVP-SCALE](./docs/sprint-evidence-ledger.md#mvp-core-scale-foundation))
- [x] SQL/DuckDB transform으로 clean dataset을 만든다. Python transform execution은 fail-closed future scope다. ([MVP-TRANSFORM](./docs/sprint-evidence-ledger.md#mvp-core-transform))
- [x] Ontology draft를 validate/activate한다. ([MVP-ONTOLOGY](./docs/sprint-evidence-ledger.md#mvp-core-ontology))
- [x] clean dataset rows를 Order/Customer objects로 index한다. ([MVP-OBJECT-INDEX](./docs/sprint-evidence-ledger.md#mvp-core-object-index))
- [x] Object Explorer에서 Order를 조회하고 Order -> Customer link를 본다. ([MVP-OBJECT-LINK-UI](./docs/sprint-evidence-ledger.md#mvp-core-object-link-ui), [VERIFY-MVP-WEB-OBJECT-LINK](./docs/sprint-evidence-ledger.md#verify-mvp-web-object-link))
- [x] ApproveOrder action을 실행한다. ([MVP-ACTION](./docs/sprint-evidence-ledger.md#mvp-core-action))
- [x] object_records, object_edits, action_runs, audit_events, outbox_events가 모두 일관되게 남는다. ([MVP-MUTATION-LEDGER](./docs/sprint-evidence-ledger.md#mvp-core-mutation-ledger))
- [x] action_log와 object_snapshot을 dataset으로 materialize한다. ([MVP-MATERIALIZATION](./docs/sprint-evidence-ledger.md#mvp-core-materialization))
- [x] downstream transform이 materialized dataset을 읽고 Customer object를 갱신한다. ([MVP-DOWNSTREAM](./docs/sprint-evidence-ledger.md#mvp-core-downstream-transform))
- [x] 전체 경로를 lineage/audit/operations UI에서 추적하고 MVP 실패 경로를 replay할 수 있다. ([MVP-OPERATIONS](./docs/sprint-evidence-ledger.md#mvp-core-operations-replay))
- [x] Python 백엔드 품질 게이트 `ruff`, `mypy` 또는 `pyright`, `pytest`가 통과한다. ([MVP-QUALITY](./docs/sprint-evidence-ledger.md#mvp-core-quality-gate))
- [x] 단순 패치성 수정 없이 주요 실패 경로에 regression test와 추적 가능한 error state가 남는다. ([MVP-REGRESSION](./docs/sprint-evidence-ledger.md#mvp-core-regression-trace))
- [x] Python 백엔드 line/branch/function coverage가 모두 95% 이상이다. ([MVP-COVERAGE](./docs/sprint-evidence-ledger.md#mvp-core-coverage))
- [x] 필수 integration test와 smoke test가 100% 실행되고 100% 통과한다. ([MVP-INTEGRATION](./docs/sprint-evidence-ledger.md#mvp-core-integration-smoke))

## Recommended First Demo Command Shape

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
flite action apply ApproveOrder --object Order/O-1001 --param reason='Inventory confirmed'
flite materialize run action_log
flite materialize run order_current
flite transform run customer_risk
flite index rebuild Customer
flite object get Customer C-100 --explain
```
