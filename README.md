# Foundry-lite

![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/API-FastAPI-009688?logo=fastapi&logoColor=white)
![DuckDB](https://img.shields.io/badge/Compute-DuckDB-FFF000?logo=duckdb&logoColor=black)
![SQLAlchemy](https://img.shields.io/badge/DB-SQLAlchemy-D71F00?logo=sqlalchemy&logoColor=white)
![Pydantic v2](https://img.shields.io/badge/Schema-Pydantic%20v2-E92063?logo=pydantic&logoColor=white)
![OpenTelemetry](https://img.shields.io/badge/Tracing-OpenTelemetry-000000?logo=opentelemetry&logoColor=white)
![Prometheus](https://img.shields.io/badge/Metrics-Prometheus-E6522C?logo=prometheus&logoColor=white)
![Playwright](https://img.shields.io/badge/E2E-Playwright-2EAD33?logo=playwright&logoColor=white)
![pnpm](https://img.shields.io/badge/Package-pnpm%2010-F69220?logo=pnpm&logoColor=white)
![Release Gate](https://img.shields.io/badge/Gate-pnpm%20ci%3Agate-0B7285)

Foundry-lite는 **데이터가 들어와서, 정제되고, 업무 객체로 바뀌고, 사람이 액션을 실행하고, 그 결과가 다시 데이터 파이프라인으로 돌아가는 폐루프 운영 객체 시스템**입니다.

비개발자 관점으로 말하면, 일반적인 데이터 도구가 "엑셀 파일이나 테이블을 보기 좋게 정리하는 도구"에 가깝다면, Foundry-lite는 "회사의 주문, 고객, 재고 같은 현실 세계의 업무 대상을 객체로 만들고, 그 객체 위에서 안전하게 결정을 실행하고, 그 결정의 흔적을 다시 데이터로 남기는 작은 운영 플랫폼"입니다.

```text
CSV / connector / stream
-> raw dataset
-> DuckDB transform
-> clean dataset
-> ontology activation
-> Order / Customer object index
-> Object Explorer / SDK / API
-> ApproveOrder action
-> audit + outbox + materialization
-> downstream transform
-> refreshed Customer risk object
```

> 현재 구현 상태를 과장하지 않기 위해 이 README는 **구현 완료**, **로컬 MVP proof**, **미래 목표**를 구분합니다. 정확한 현재 상태 원본은 [docs/implementation-status.md](docs/implementation-status.md)입니다.

## Table Of Contents

- [30초 실행](#30초-실행)
- [프로젝트가 증명하는 것](#프로젝트가-증명하는-것)
- [현재 구현 상태](#현재-구현-상태)
- [큰 그림](#큰-그림)
- [아키텍처 원칙](#아키텍처-원칙)
- [코드 구조](#코드-구조)
- [런타임 흐름](#런타임-흐름)
- [도메인 모델](#도메인-모델)
- [포트와 어댑터](#포트와-어댑터)
- [API, CLI, Web, SDK](#api-cli-web-sdk)
- [운영과 관측성](#운영과-관측성)
- [품질 게이트](#품질-게이트)
- [보안과 거버넌스](#보안과-거버넌스)
- [로드맵](#로드맵)
- [문서 지도](#문서-지도)
- [GitHub README 시각화 방식](#github-readme-시각화-방식)

## 30초 실행

로컬 데모는 공급망 예제로 돌아갑니다. `Order`, `Customer`, `ApproveOrder`, `ops.action_log`, `ops.order_current`가 핵심입니다.

```bash
pnpm install
uv sync --all-groups
pnpm demo:supply-chain
```

API 서버:

```bash
pnpm dev
curl http://127.0.0.1:8000/healthz
```

정적 Web Object Explorer:

```bash
pnpm web:static
```

품질 게이트:

```bash
pnpm ci:gate
```

Docker/Testcontainers가 필요한 PostgreSQL 계약 테스트에서 Colima를 쓰는 로컬 환경은 보통 아래 환경 변수가 필요합니다.

```bash
export DOCKER_HOST=unix:///Users/isihyeon/.colima/default/docker.sock
export TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock
pnpm --silent ci:gate
```

## 프로젝트가 증명하는 것

Foundry-lite의 MVP는 "팔란티어 Foundry 전체 복제"가 아닙니다. 목표는 훨씬 좁고 분명합니다.

> **작지만 반복 가능한 하나의 업무 폐루프를 끝까지 증명한다.**

그 폐루프는 아래와 같습니다.

```mermaid
flowchart LR
    A["CSV / connector snapshot"] --> B["raw dataset version"]
    B --> C["DuckDB SQL transform"]
    C --> D["clean dataset version"]
    D --> E["ontology activation"]
    E --> F["Order / Customer object index"]
    F --> G["object query / object explorer"]
    G --> H["ApproveOrder action"]
    H --> I["object edit + action run"]
    I --> J["audit event + outbox event"]
    I --> K["materialized datasets"]
    K --> L["downstream transform"]
    L --> M["Customer risk refresh"]
    M -. "다시 운영 객체로 환류" .-> F
```

### 비개발자를 위한 한 문장 설명

Foundry-lite는 "원천 데이터에서 업무 객체를 만들고, 그 객체 위에서 사람이 결정을 실행하고, 그 결정까지 다시 데이터로 남겨 다음 분석과 운영에 쓰는 시스템"입니다.

### 개발자를 위한 한 문장 설명

Foundry-lite는 Python 기반 modular monolith 안에서 dataset transaction, transform lineage, ontology/object indexing, action runtime, materialization, outbox/audit, adapter boundary를 하나의 재현 가능한 vertical slice로 묶은 MVP core입니다.

## 현재 구현 상태

| 영역 | 현재 상태 | 중요한 주의 |
|---|---|---|
| 저장소 | SQLite + SQLAlchemy + 로컬 filesystem object storage | PostgreSQL JSONB production object store는 목표이지만 현재 기본 구현은 아닙니다. |
| Dataset | immutable dataset version, transaction, manifest commit, CSV upload, connector snapshot boundary | PostgreSQL snapshot connector production path는 아직 문서상 목표와 일부 adapter proof로 분리됩니다. |
| Transform | DuckDB SQL transform, input version pinning, lineage, output commit protocol | Python transform은 fail-closed 성격이며 sandboxed SDK abstraction은 미래 과제입니다. |
| Ontology | YAML import, object/property/link/action metadata, activation validation | 복잡한 visual ontology manager는 아직 없습니다. |
| Object Store | Order/Customer object indexing, query, link traversal, object sets, shadow reindex proof, CDC indexing proof | 대규모 production search/object serving 튜닝은 미래 과제입니다. |
| Action Runtime | `ApproveOrder`, expected object version, idempotency, audit, outbox, object edit | real ERP writeback은 mock/local proof 수준입니다. |
| Materialization | `ops.action_log`, `ops.order_current`, watermark/source version proof | 더 많은 materialization type은 v1.5 이후 영역입니다. |
| Search | local/fake search adapter, optional Elasticsearch adapter, rebuild/orphan proof | managed live Elasticsearch 운영 배포는 아직 미래 과제입니다. |
| Stream/CDC | local/fake stream adapter, Kafka-compatible worker proof, Debezium-shaped CDC proof | 계속 도는 production worker와 운영 패키징은 아직 미래 과제입니다. |
| Security | tenant context, RBAC, property masking, deny audit, Postgres RLS contract proof | real JWT/OIDC adapter는 아직 미래 과제입니다. |
| Observability | structured trace keys, OpenTelemetry, Prometheus metrics, Grafana compose profile | 운영 환경 전체 배포 runbook은 아직 확장 과제입니다. |
| Quality | `pnpm ci:gate`, static gates, contract tests, integration markers, Playwright E2E | CodeQL은 GitHub Actions 전용입니다. |

## 큰 그림

Foundry-lite는 데이터를 단순히 저장하고 보여주는 시스템이 아니라, **업무 객체의 현재 상태와 변경 이력**을 관리합니다.

```mermaid
flowchart TB
    subgraph Sources["외부 세계"]
        CSV["CSV files"]
        REST["REST / webhook"]
        KAFKA["Kafka-compatible stream"]
        CDC["Debezium CDC"]
    end

    subgraph South["South of Ontology: 데이터를 객체 세계로 끌어올리는 영역"]
        DS["Dataset Registry"]
        TX["Dataset Transaction + Manifest"]
        QUAL["Schema / quality checks"]
        TR["DuckDB Transform"]
        LIN["Lineage"]
        ONT["Ontology Metadata"]
        IDX["Funnel-lite Object Indexer"]
    end

    subgraph North["North of Ontology: 사람이 객체 위에서 일하는 영역"]
        OBJ["Object Store"]
        QUERY["Object Query"]
        SETS["Object Sets"]
        SDK["OSDK-lite / generated TypeScript SDK"]
        WEB["Object Explorer"]
        ACTION["Action Runtime"]
        MAT["Materialization"]
        OPS["Operations Console"]
    end

    subgraph Runtime["운영 안전장치"]
        AUDIT["Audit Events"]
        OUTBOX["Outbox / DLQ"]
        METRICS["Metrics"]
        TRACE["Trace / Request IDs"]
        POLICY["Policy / RBAC / Masking"]
    end

    CSV --> DS
    REST --> DS
    KAFKA --> DS
    CDC --> DS
    DS --> TX --> QUAL --> TR --> LIN --> ONT --> IDX --> OBJ
    OBJ --> QUERY --> WEB
    OBJ --> QUERY --> SDK
    QUERY --> SETS
    WEB --> ACTION
    SDK --> ACTION
    ACTION --> OBJ
    ACTION --> AUDIT
    ACTION --> OUTBOX
    ACTION --> MAT
    MAT --> DS
    OPS --> AUDIT
    OPS --> OUTBOX
    POLICY --> QUERY
    POLICY --> ACTION
    TRACE --> OPS
    METRICS --> OPS
```

### Control Plane, Data Plane, Event Plane

| Plane | 쉬운 설명 | 맡는 일 | 대표 코드 |
|---|---|---|---|
| Control Plane | 시스템의 장부와 규칙 | tenant, user, metadata, schema, ontology, run state | [libs/foundry_lite/infrastructure/schema.py](libs/foundry_lite/infrastructure/schema.py) |
| Data Plane | 실제 데이터 처리 흐름 | dataset version, storage file, transform output, materialization output | [libs/foundry_lite/application/services/dataset](libs/foundry_lite/application/services/dataset) |
| Event Plane | 변경 사실과 후속 처리 | audit, outbox, DLQ, event replay, operations investigation | [libs/foundry_lite/application/services/runtime_service.py](libs/foundry_lite/application/services/runtime_service.py) |

## 아키텍처 원칙

### 1. FoundryLite는 얇은 Facade입니다

`FoundryLite`는 모든 일을 직접 하는 거대한 클래스가 아닙니다. 바깥에서는 하나의 안정된 진입점처럼 보이지만, 내부에서는 `datasets`, `transforms`, `ontology`, `objects`, `actions`, `materialization`, `operations`, `demo`라는 작은 창구로 나뉩니다.

```mermaid
classDiagram
    class FoundryLite {
      +datasets
      +transforms
      +ontology
      +objects
      +actions
      +materialization
      +operations
      +demo
      +bootstrap()
      +reset(confirm_dev)
    }
    class DatasetWorkspace
    class TransformPipeline
    class OntologyRegistry
    class ObjectStore
    class ActionGateway
    class MaterializationRunner
    class OperationsConsole
    class SupplyChainDemo

    FoundryLite --> DatasetWorkspace
    FoundryLite --> TransformPipeline
    FoundryLite --> OntologyRegistry
    FoundryLite --> ObjectStore
    FoundryLite --> ActionGateway
    FoundryLite --> MaterializationRunner
    FoundryLite --> OperationsConsole
    FoundryLite --> SupplyChainDemo
```

### 2. 실제 일은 Application Service가 합니다

각 service는 직접 쓰는 dependency만 `required_dependencies`에 선언하고, 직접 호출하는 이웃 service만 `required_collaborators`에 선언합니다. 이렇게 하면 "어떤 기능이 어디에 기대고 있는지"가 코드에서 보입니다.

```mermaid
flowchart LR
    API["apps/api FastAPI"] --> F["FoundryLite facade"]
    CLI["apps/cli flite"] --> F
    WEB["apps/web Object Explorer"] --> API
    WORKER["apps/worker stream archive"] --> F

    F --> SVC["CoreServices"]

    subgraph Services["application/services"]
        DS["DatasetServices"]
        TS["TransformService"]
        OS["ObjectServices"]
        AS["ActionService"]
        MS["MaterializationService"]
        RS["RuntimeService"]
        ONS["OntologyService"]
    end

    SVC --> DS
    SVC --> TS
    SVC --> OS
    SVC --> AS
    SVC --> MS
    SVC --> RS
    SVC --> ONS

    Services --> PORTS["application/ports"]
    PORTS --> INFRA["infrastructure repositories/adapters"]
```

### 3. 핵심 제품 로직은 인프라와 분리됩니다

로컬에서는 SQLite, filesystem, DuckDB를 쓰지만, 핵심 규칙은 특정 장비에 묶이면 안 됩니다. 그래서 storage, metadata, compute, stream, search, workflow, connector, auth는 port/adapter 경계 뒤에 있습니다.

```mermaid
flowchart TB
    subgraph Core["Core product logic"]
        APP["Application Services"]
        DOMAIN["Domain / Policy / Validation"]
    end

    subgraph Ports["Ports: core가 보는 약속"]
        P1["DatasetStorageAdapter"]
        P2["MetadataRepository"]
        P3["ComputeAdapter"]
        P4["StreamAdapter"]
        P5["SearchAdapter"]
        P6["WorkflowAdapter"]
        P7["ConnectorAdapter"]
        P8["AuthProvider"]
    end

    subgraph Local["현재 local/fake 구현"]
        L1["Local filesystem"]
        L2["SQLite / SQLAlchemy"]
        L3["DuckDB"]
        L4["Local/Fake stream"]
        L5["Local/Fake search"]
        L6["Local workflow"]
        L7["Local/Fake connector"]
        L8["Header/Demo auth"]
    end

    subgraph Scale["나중에 교체 가능한 구현"]
        S1["S3 / GCS / Iceberg"]
        S2["PostgreSQL"]
        S3["Spark / Flink"]
        S4["Kafka / Redpanda"]
        S5["Elasticsearch"]
        S6["Temporal"]
        S7["SaaS / REST / CDC connectors"]
        S8["OIDC / SSO"]
    end

    APP --> DOMAIN
    APP --> Ports
    P1 --> L1
    P2 --> L2
    P3 --> L3
    P4 --> L4
    P5 --> L5
    P6 --> L6
    P7 --> L7
    P8 --> L8
    P1 -. future .-> S1
    P2 -. future .-> S2
    P3 -. future .-> S3
    P4 -. future .-> S4
    P5 -. optional .-> S5
    P6 -. future .-> S6
    P7 -. future .-> S7
    P8 -. future .-> S8
```

### 4. 모든 변경은 추적 가능해야 합니다

Foundry-lite에서 중요한 질문은 "성공했나?"만이 아닙니다. 더 중요한 질문은 "왜 이런 결과가 생겼나?", "어느 요청이 만들었나?", "다시 계산할 수 있나?"입니다.

그래서 주요 변경은 다음 중 하나의 durable record로 남습니다.

| 변경 종류 | 남는 기록 | 이유 |
|---|---|---|
| dataset 변경 | dataset transaction, manifest, dataset version | 어느 원천 데이터와 파일에서 왔는지 추적 |
| transform 실행 | transform run, lineage edge, output version | 어떤 input version으로 계산했는지 재현 |
| object 변경 | object record version, object edit | 현재 업무 상태와 과거 변경 구분 |
| action 실행 | action run, request fingerprint, idempotency key | 재시도와 중복 제출 방어 |
| side effect | outbox event, DLQ | 외부 발행 실패를 복구 가능하게 관리 |
| 운영 실패 | run status, error payload, trace keys | 운영자가 DB를 직접 열지 않고 조사 |
| 권한 거부 | permission deny audit | 보안 실패도 감사 가능하게 기록 |

## 코드 구조

```text
.
├── apps/
│   ├── api/                  # FastAPI HTTP entrypoint
│   ├── cli/                  # flite CLI
│   ├── web/                  # static Object Explorer and browser SDK bundle
│   └── worker/               # stream archive worker entrypoint
├── libs/foundry_lite/
│   ├── application/          # use cases, services, facades, ports
│   ├── domain/               # framework-free context/errors/domain primitives
│   ├── infrastructure/       # SQLAlchemy repositories and concrete adapters
│   ├── observability/        # logging, metrics, tracing
│   └── security/             # policy service and masking rules
├── packages/sdk-ts/          # generated TypeScript SDK package
├── examples/supply-chain-demo/
│   ├── data/                 # orders/customers CSV seed data
│   ├── ontology/             # Order/Customer ontology YAML
│   └── transforms/           # clean_orders, clean_customers, customer_risk SQL
├── scripts/
│   ├── diagnostics/          # runtime diagnostics
│   └── quality/              # static/dynamic quality gates
├── tests/
│   ├── unit/
│   ├── contracts/
│   ├── integration/
│   ├── smoke/
│   └── e2e/
├── infra/
│   ├── docker-compose.dev.yml
│   ├── observability/
│   └── schema_revisions/
└── docs/
```

### 주요 코드 지도

| 궁금한 것 | 먼저 볼 곳 |
|---|---|
| 플랫폼 루트 | [libs/foundry_lite/application/foundry.py](libs/foundry_lite/application/foundry.py) |
| 서비스 그래프 조립 | [libs/foundry_lite/application/core_services.py](libs/foundry_lite/application/core_services.py) |
| 의존성 주입 계약 | [libs/foundry_lite/application/dependencies.py](libs/foundry_lite/application/dependencies.py) |
| 서비스 dependency/collaborator 규칙 | [libs/foundry_lite/application/services/base.py](libs/foundry_lite/application/services/base.py) |
| Dataset use case | [libs/foundry_lite/application/services/dataset](libs/foundry_lite/application/services/dataset) |
| Transform use case | [libs/foundry_lite/application/services/transform_service.py](libs/foundry_lite/application/services/transform_service.py) |
| Object query/index/search/set | [libs/foundry_lite/application/services/object_store](libs/foundry_lite/application/services/object_store) |
| Action runtime | [libs/foundry_lite/application/services/action_service.py](libs/foundry_lite/application/services/action_service.py) |
| Materialization | [libs/foundry_lite/application/services/materialization_service.py](libs/foundry_lite/application/services/materialization_service.py) |
| Runtime audit/outbox/operations | [libs/foundry_lite/application/services/runtime_service.py](libs/foundry_lite/application/services/runtime_service.py) |
| 로컬 composition root | [libs/foundry_lite/infrastructure/local_runtime.py](libs/foundry_lite/infrastructure/local_runtime.py) |
| DB schema | [libs/foundry_lite/infrastructure/schema.py](libs/foundry_lite/infrastructure/schema.py) |
| API | [apps/api/foundry_lite_api/main.py](apps/api/foundry_lite_api/main.py) |
| CLI | [apps/cli/foundry_lite_cli/main.py](apps/cli/foundry_lite_cli/main.py) |
| Worker | [apps/worker/foundry_lite_worker/stream_archive.py](apps/worker/foundry_lite_worker/stream_archive.py) |
| SDK generator | [scripts/generate_sdk_ts.py](scripts/generate_sdk_ts.py) |
| 품질 게이트 | [scripts/ci_gate.sh](scripts/ci_gate.sh), [scripts/quality](scripts/quality) |

## 런타임 흐름

### 공급망 데모 시퀀스

```mermaid
sequenceDiagram
    participant User as 사용자 / CLI / Web
    participant API as API or CLI
    participant Core as FoundryLite
    participant Dataset as DatasetServices
    participant Compute as DuckDBComputeAdapter
    participant Ontology as OntologyService
    participant Objects as ObjectServices
    participant Action as ActionService
    participant Runtime as RuntimeService
    participant Materialization as MaterializationService
    participant Storage as DatasetStorageAdapter

    User->>API: run supply-chain demo
    API->>Core: demo.run()
    Core->>Dataset: upload raw orders/customers
    Dataset->>Storage: stage files + manifest
    Dataset->>Runtime: audit dataset commit
    Core->>Compute: run clean_orders / clean_customers
    Compute-->>Dataset: output clean versions
    Dataset->>Runtime: lineage edges
    Core->>Ontology: apply Order/Customer YAML
    Core->>Objects: rebuild object index
    User->>API: query Order O-1001
    API->>Objects: get/query active object
    User->>API: ApproveOrder
    API->>Action: apply expectedObjectVersion + idempotency key
    Action->>Objects: update object record
    Action->>Runtime: audit + outbox
    Core->>Materialization: action_log + order_current
    Materialization->>Dataset: commit ops datasets
    Core->>Compute: customer_risk downstream transform
    Core->>Objects: refresh Customer risk object
```

### Dataset transaction 상태

```mermaid
stateDiagram-v2
    [*] --> OPEN: transaction created
    OPEN --> COMMITTED: health checks pass + manifest promoted + version metadata committed
    OPEN --> ABORTED: validation/storage/compute failure
    OPEN --> ABORTED: watchdog aborts stale open transaction
    COMMITTED --> [*]
    ABORTED --> [*]
```

### Action 실행 상태

```mermaid
stateDiagram-v2
    [*] --> RECEIVED: API / CLI request
    RECEIVED --> VALIDATED: schema + permission + precondition
    VALIDATED --> CONFLICT: stale expectedObjectVersion
    VALIDATED --> IDEMPOTENT_REPLAY: same idempotency key + same fingerprint
    VALIDATED --> REJECTED: same idempotency key + different fingerprint
    VALIDATED --> APPLIED: object edit committed
    APPLIED --> AUDITED: audit event written
    AUDITED --> OUTBOXED: outbox event written
    OUTBOXED --> MATERIALIZED: action_log / object_snapshot dataset
    CONFLICT --> [*]
    IDEMPOTENT_REPLAY --> [*]
    REJECTED --> [*]
    MATERIALIZED --> [*]
```

## 도메인 모델

Foundry-lite의 핵심 명사는 아래와 같습니다.

| 개념 | 쉬운 설명 | 예시 |
|---|---|---|
| Dataset | 버전과 이력이 있는 데이터 묶음 | `raw.erp_orders`, `clean.orders` |
| Dataset Version | 한 번 commit되면 바뀌지 않는 데이터 스냅샷 | `dataset_version_id` |
| Dataset Transaction | 데이터 변경을 `OPEN -> COMMITTED/ABORTED`로 남기는 장부 | CSV upload, transform output |
| Transform | 특정 input version을 읽어 output dataset version을 만드는 계산 | `clean_orders`, `customer_risk` |
| Lineage | 어떤 데이터가 어떤 입력에서 나왔는지 보여주는 연결 | transform input/output edge |
| Ontology | 테이블을 업무 객체로 해석하는 지도 | `Order`, `Customer`, `OrderCustomer` |
| Object | 사용자가 실제로 다루는 업무 대상 | `Order O-1001` |
| Object Link | 객체 사이의 관계 | `Order -> Customer` |
| Object Set | 저장된 객체 묶음 | 승인 대기 주문 목록 |
| Action | 객체 위에서 실행되는 typed transaction | `ApproveOrder` |
| Audit Event | 누가 무엇을 바꿨는지 남기는 감사 기록 | `action.run.succeeded` |
| Outbox Event | 후속 작업을 안전하게 발행하기 위한 이벤트 장부 | `object.changed` |
| Materialization | 객체/액션 상태를 다시 dataset으로 내보내는 작업 | `ops.action_log`, `ops.order_current` |
| Run | 긴 작업의 상태 기록 | sync, transform, index, action, materialization |
| DLQ | 실패한 outbox event를 재처리하기 위해 보관하는 곳 | dead letter event |

### 핵심 엔티티 관계

```mermaid
erDiagram
    TENANT ||--o{ USER : owns
    TENANT ||--o{ DATASET : owns
    DATASET ||--o{ DATASET_VERSION : has
    DATASET ||--o{ DATASET_TRANSACTION : changes_through
    DATASET_VERSION ||--o{ LINEAGE_EDGE : input_or_output
    ONTOLOGY_VERSION ||--o{ OBJECT_TYPE : defines
    OBJECT_TYPE ||--o{ PROPERTY_TYPE : has
    OBJECT_TYPE ||--o{ OBJECT_RECORD : indexes
    OBJECT_RECORD ||--o{ OBJECT_RECORD_VERSION : versions
    OBJECT_RECORD ||--o{ OBJECT_LINK : connects
    OBJECT_RECORD ||--o{ OBJECT_EDIT : changes
    ACTION_TYPE ||--o{ ACTION_RUN : executes
    ACTION_RUN ||--o{ OBJECT_EDIT : creates
    ACTION_RUN ||--o{ AUDIT_EVENT : records
    ACTION_RUN ||--o{ OUTBOX_EVENT : emits
    MATERIALIZATION_RUN ||--o{ DATASET_VERSION : commits
    OUTBOX_EVENT ||--o{ DEAD_LETTER_EVENT : can_fail_into
```

## 포트와 어댑터

Scale Foundation의 핵심은 "지금은 작은 구현으로 돌리되, 제품 규칙을 바꾸지 않고 큰 인프라로 교체할 수 있게 경계를 고정하는 것"입니다.

| Boundary | 현재 local/fake 구현 | scale 또는 production 목표 | contract test |
|---|---|---|---|
| `MetadataRepository` | SQLAlchemy/SQLite, PostgreSQL contract coverage | PostgreSQL, partitioning, stronger migrations | `tests/contracts/test_metadata_repository_contract.py` |
| `DatasetStorageAdapter` | local filesystem, fake storage URI | S3/GCS/Azure Blob, Iceberg | `tests/contracts/test_dataset_storage_adapter_contract.py` |
| `DatasetRepository` | SQLAlchemy repositories | PostgreSQL optimized metadata | `tests/contracts/test_dataset_repository_contract.py` |
| `DatasetTransactionRepository` | SQLAlchemy transaction rows | stronger transactional DB semantics | `tests/contracts/test_dataset_transaction_repository_contract.py` |
| `ComputeAdapter` | DuckDB, fake compute | Spark/Flink/Ray-style runners | `tests/contracts/test_compute_adapter_contract.py` |
| `StreamAdapter` | local/fake stream, Kafka-compatible adapter proof | Kafka/Redpanda production profile | `tests/contracts/test_stream_adapter_contract.py` |
| `SearchAdapter` | local/fake, optional Elasticsearch adapter | managed Elasticsearch operations | `tests/contracts/test_search_adapter_contract.py` |
| `WorkflowAdapter` | local/fake workflow | Temporal | `tests/contracts/test_workflow_adapter_contract.py` |
| `ConnectorAdapter` | local/fake, REST adapter, Debezium wrapper proof | SaaS connectors, durable registry, retry workers | `tests/contracts/test_connector_adapter_contract.py` |
| `AuthProvider` | header trust/demo profiles | OIDC/SSO/JWT | `tests/contracts/test_auth_provider_contract.py` |
| `RuntimeRepository` | SQLAlchemy audit/outbox/lineage/run rows | partitioned audit/outbox, event publisher state | `tests/contracts/test_runtime_repository_contract.py` |
| `ObjectReadRepository` | SQLAlchemy object query/link reads | Postgres JSONB/read indexes | `tests/contracts/test_object_read_repository_contract.py` |
| `ObjectIndexRepository` | SQLAlchemy index writes/shadow pointer | large index build/promotion path | `tests/contracts/test_object_index_repository_contract.py` |
| `ActionRepository` | SQLAlchemy action/writeback/object edit writes | stronger concurrency and writeback proof | `tests/contracts/test_action_repository_contract.py` |

### Adapter failure contract

모든 adapter는 단순히 "실패했습니다"가 아니라 아래 정보를 잃지 않아야 합니다.

| 실패 정보 | 왜 필요한가 |
|---|---|
| failure kind | validation, timeout, unavailable, not found 등을 구분 |
| retryable 여부 | 재시도하면 되는지, 사용자 수정이 필요한지 판단 |
| timeout seconds | 운영자가 어느 정도 기다려야 하는지 판단 |
| idempotency key 필요 여부 | 재시도 중 중복 write 방지 |
| operator message | 운영자가 로그 없이도 다음 행동을 이해 |
| request/tenant/run/correlation key | 실패 위치를 audit, trace, run table로 연결 |

## API, CLI, Web, SDK

### FastAPI

대표 endpoint:

| Method | Path | 용도 |
|---|---|---|
| `GET` | `/healthz` | API health check |
| `GET` | `/metrics` | Prometheus metrics |
| `GET` | `/api/datasets/{namespace}/{name}/preview` | dataset preview |
| `GET` | `/api/datasets/{namespace}/{name}/versions` | committed dataset versions |
| `POST` | `/api/ontology/validate` | ontology YAML validation without activation |
| `GET` | `/api/objects/{object_type}/{object_id}` | object detail, optional source explanation |
| `GET` | `/api/objects/{object_type}/{object_id}/links/{link_type}` | object link traversal, for example Order to Customer |
| `POST` | `/api/objects/{object_type}/query` | filter/sort/page/search object query |
| `GET` | `/api/object-sets` | object set list |
| `POST` | `/api/object-sets` | static/dynamic object set create |
| `GET` | `/api/operations/runs` | operations run list |
| `GET` | `/api/operations/runs/{run_type}/{run_id}` | run detail and investigation |
| `POST` | `/api/operations/runs/transform/{run_id}/retry` | failed transform retry |
| `POST` | `/api/operations/index/{object_type}/replay` | object index replay |
| `POST` | `/api/operations/dead-letter-events/{event_id}/retry` | DLQ retry |
| `POST` | `/api/connectors/webhooks/{connector_name}/{resource_name}` | signed webhook ingest |
| `POST` | `/api/actions/{action_type}/apply` | action execution |

### CLI

`flite`는 사람이 직접 실행하는 운영/개발 명령입니다.

```bash
flite demo run-supply-chain
flite dataset preview raw.erp_orders
flite object get Order O-1001 --explain
flite action apply ApproveOrder --object Order/O-1001 --param reason='Inventory confirmed'
flite operations runs --status failed
flite operations run transform <run-id>
flite transform retry <transform-run-id>
flite index replay Order
flite outbox retry <dead-letter-event-id>
```

### Web

`apps/web`는 정적 Object Explorer입니다. 현재는 full SPA framework보다 가볍게 구성되어 있으며, API와 generated browser SDK를 통해 object load, action apply, object set, operations panel을 다룹니다.

```bash
pnpm dev
pnpm web:static
```

### TypeScript SDK

Ontology에서 TypeScript SDK를 생성합니다.

```bash
pnpm sdk:generate
```

생성 결과:

| 출력 | 용도 |
|---|---|
| [packages/sdk-ts/src/generated.ts](packages/sdk-ts/src/generated.ts) | 패키지용 generated SDK |
| [apps/web/generated-sdk.js](apps/web/generated-sdk.js) | 브라우저 Object Explorer용 SDK bundle |

SDK는 object get/query, typed action apply payload, idempotency key helper, expected object version helper를 제공합니다.

## 운영과 관측성

Foundry-lite는 운영 중 문제가 생겼을 때 아래 키로 이어서 추적하도록 설계되어 있습니다.

| Trace Key | 의미 |
|---|---|
| `request_id` | API/CLI 요청 하나를 따라가기 위한 ID |
| `tenant_id` | 어떤 tenant의 데이터인지 |
| `actor_user_id` | 어떤 사용자가 실행했는지 |
| `dataset_version_id` | 어떤 데이터 버전이 input/output인지 |
| `transform_run_id` | 어떤 transform 실행인지 |
| `index_run_id` | 어떤 object indexing 실행인지 |
| `action_run_id` | 어떤 action 실행인지 |
| `materialization_run_id` | 어떤 materialization 실행인지 |
| `object_type`, `object_id`, `object_version` | 어떤 업무 객체가 바뀌었는지 |
| `correlation_id` | 여러 작업을 하나의 흐름으로 묶는 ID |

### Observability stack

```bash
docker compose -f infra/docker-compose.dev.yml up -d prometheus tempo grafana
OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318/v1/traces pnpm dev
```

| 도구 | 역할 |
|---|---|
| OpenTelemetry | 요청, service, DB span을 하나의 trace로 연결 |
| Prometheus | dataset commit, transform, action, query, outbox lag, failed run, DLQ size 지표 수집 |
| Grafana | 로컬 dashboard 확인 |
| Tempo | distributed trace 저장 |
| Runtime diagnostics | faulthandler, tracemalloc, cProfile, warnings 수집 |

```bash
pnpm diagnostics
pnpm diagnostics:trace
```

주요 산출물:

```text
artifacts/diagnostics/runtime_diagnostics.json
artifacts/diagnostics/demo_profile.pstats
artifacts/diagnostics/demo_profile_top.txt
artifacts/diagnostics/faulthandler.log
artifacts/quality/*.json
artifacts/demo/supply-chain.json
```

## 품질 게이트

Foundry-lite는 "기능이 돌아간다"만으로 완료로 보지 않습니다. 데이터 정합성, 감사 가능성, 회귀 방지, 레이어 경계를 함께 봅니다.

```mermaid
flowchart TB
    LOCAL["local: pnpm ci:gate"] --> SERIAL["same gates, serial order"]

    PR["GitHub PR / push"] --> STATIC["quality-static"]
    PR --> COVERAGE["quality-coverage"]
    PR --> FLAKY["quality-flaky"]
    PR --> RUNTIME["quality-runtime"]
    PR --> E2E["quality-e2e"]

    STATIC --> TYPES["ruff + mypy + pyright"]
    STATIC --> ARCH["architecture/import/service DAG gates"]
    STATIC --> CONTRACT["port/repository/adapter contract presence"]
    STATIC --> SECURITY["Bandit + Semgrep + gitleaks + pip-audit"]
    COVERAGE --> UNIT["unit + integration + smoke tests"]
    COVERAGE --> COVERAGE_FLOORS["95% branch + tier/public API coverage"]
    FLAKY --> REPEAT["3 repeated random + xdist pytest runs"]
    RUNTIME --> RUNTIME_PROOFS["runtime correctness gates"]
    E2E --> BROWSER["Playwright browser E2E"]

    TYPES --> AGG["quality-gate aggregate"]
    ARCH --> AGG
    CONTRACT --> AGG
    SECURITY --> AGG
    UNIT --> AGG
    COVERAGE_FLOORS --> AGG
    REPEAT --> AGG
    RUNTIME_PROOFS --> AGG
    BROWSER --> AGG
    AGG --> REPORT["artifacts/quality reports"]

    STATIC --> G1["router purity"]
    STATIC --> G2["audit on mutation"]
    STATIC --> G3["function length <= 40"]
    STATIC --> G4["dict[str, Any] budget"]
    STATIC --> G5["log trace keys"]
    STATIC --> G6["no pragma no cover"]
    STATIC --> G7["error response request_id"]
    STATIC --> G8["tier coverage"]
    STATIC --> G9["infra ratchet"]
    RUNTIME --> R1["audit count"]
    RUNTIME --> R2["outbox consistency"]
    RUNTIME --> R3["OpenLineage"]
    RUNTIME --> R4["trace continuity"]
    RUNTIME --> R5["failed mutation state"]
```

로컬의 `pnpm ci:gate`는 사람이 한 번에 전체 release evidence를 확인하기 쉽도록 여전히 직렬로 돈다. GitHub Actions에서는 같은 스크립트를 `static`, `coverage`, `flaky`, `runtime`, `e2e` lane으로 나누어 동시에 실행하고, 마지막 `quality-gate` aggregate job이 모든 lane의 성공을 확인한다. 즉, branch protection이 보는 required check 이름은 유지하면서도 검사 강도는 낮추지 않는다.

### 대표 gate

| Gate | 막는 문제 |
|---|---|
| `check_infra_import_boundary.py` | application/domain이 concrete infra SDK에 묶이는 문제 |
| `check_service_dependencies.py` | service가 선언하지 않은 dependency/collaborator에 숨어 기대는 문제 |
| `check_service_call_graph.py` | service collaborator graph cycle/depth/fan-out 회귀 |
| `check_application_module_size.py` | application module이 다시 god file로 커지는 문제 |
| `check_function_length.py` | 40줄 초과 application 함수 재도입 |
| `check_application_any_budget.py` | application/app boundary에 broad `Any` 재도입 |
| `check_router_layer_purity.py` | API router가 repository/DB transaction 직접 접근 |
| `check_query_side_effects.py` | 조회 함수가 상태를 바꾸는 문제 |
| `check_repository_no_business.py` | repository가 business rule을 판단하는 문제 |
| `check_tenant_write_guard.py` | tenant-scoped write가 tenant guard 없이 실행되는 문제 |
| `check_contract_test_per_port.py` | port/interface에 contract test가 없는 문제 |
| `check_integration_scenario_markers.py` | 필수 MVP 통합 시나리오 7개 누락 |
| `check_regression_test_per_bugfix.py` | bugfix가 회귀 테스트 없이 들어오는 문제 |
| `check_pr_root_cause_section.py` | PR이 원인/영향/회귀 방지를 설명하지 않는 문제 |
| `check_doc_drift.py` | 현재 구현 문서가 실제 코드 경로/심볼과 어긋나는 문제 |
| `check_schema_revision_guard.py` | schema.py 변경이 revision snapshot 없이 들어오는 문제 |
| `check_action_idempotency.py` | action idempotency contract 회귀 |
| `check_metrics_exposed.py` | 필수 운영 metrics 누락 |
| `check_flaky_detector.py` | 반복 실행에서 흔들리는 test suite |
| `check_infra_ratchet.py` | 인프라를 한 번에 하나씩 추가하고 실패/동시성/재시도/부분 성공/복구/운영 증거를 문서·CI에 고정하지 않는 문제 |

### Infra Ratchet

새 인프라는 한 번에 하나씩만 추가한다. 각 인프라는 adapter contract, normal path,
failure injection, concurrency race, retry/idempotency, partial success,
recovery cleanup, operator evidence, docs sync를 모두 갖춘 뒤에야 다음 인프라로
넘어간다. 이 규율은 [docs/infra-ratchet.md](docs/infra-ratchet.md)에 정의되어
있고, `check_infra_ratchet.py`가 README, implementation status, tricky failure
checklist, commit-point risk register, `package.json`, `ci_gate.sh` 연결을 static
lane에서 검사한다.

### 필수 통합 시나리오

품질 게이트는 아래 7개 MVP release 시나리오가 pytest marker로 존재하는지 확인합니다.

```mermaid
journey
    title MVP release integration scenarios
    section Data comes in
      connector sync to raw dataset commit: 5: Test
      raw dataset to DuckDB transform to clean dataset: 5: Test
    section Ontology becomes operations
      ontology import activation to object index: 5: Test
      object query to action apply to audit/outbox: 5: Test
    section Loop closes
      materialization to downstream transform: 5: Test
      permission denied and tenant isolation: 5: Test
      failed run to retry/replay or DLQ: 5: Test
```

## 보안과 거버넌스

Foundry-lite의 보안 목표는 v1에서 모든 enterprise security를 끝내는 것이 아니라, **tenant isolation, role-based permission, property masking, audit-all-writes**를 일관되게 적용하는 것입니다.

| 영역 | 현재 원칙 |
|---|---|
| Tenant isolation | application query/write boundary와 PostgreSQL RLS contract proof에서 tenant 분리 |
| Auth | `AuthProvider` port, local/demo/header profile, production runtime에서 unsafe profile 차단 |
| RBAC | `admin`, `data_engineer`, `ops_manager`, `viewer`, `finance` role matrix |
| Permission checkpoint | dataset/object read, dataset write, ontology activation, action execution, materialization, operations retry |
| Property masking | Order margin 같은 민감 property를 non-finance/non-admin에게 숨김 |
| Audit | mutation, permission deny, action conflict 등 durable audit evidence |
| Request trace | API error response와 log/audit/run payload에 `request_id` 유지 |

### 보안 흐름

```mermaid
sequenceDiagram
    participant Client
    participant API
    participant Auth as AuthProvider
    participant Policy as PolicyService
    participant Service as Application Service
    participant Repo as Repository
    participant Runtime as Runtime/Audit

    Client->>API: request with tenant/user/roles headers
    API->>Auth: build RequestContext
    Auth-->>API: tenant_id + actor_user_id + roles
    API->>Service: call use case with context
    Service->>Policy: permission check
    alt allowed
        Service->>Repo: read/write tenant-scoped data
        Service->>Runtime: audit successful mutation
    else denied
        Service->>Runtime: audit permission.denied
        Service-->>API: permission error with request_id
    end
```

## 로드맵

Sprint 00-36은 MVP core, Sprint 02A는 scale-ready foundation, Sprint 36A는 MVP 운영 안정성 보강입니다. Sprint 37-42는 현재 checkout에 MVP 이후 확장 proof가 들어와 있고, Sprint 43-45의 Iceberg/Spark/Kubernetes 운영 패키지는 아직 future scope입니다.

```mermaid
gantt
    title Foundry-lite Roadmap Shape
    dateFormat  YYYY-MM-DD
    axisFormat  %m/%d
    section MVP Core
    Scaffold and control plane           :done, s01, 2026-06-01, 2d
    Dataset transaction and commit       :done, s03, 2026-06-03, 3d
    Transform and lineage                :done, s11, 2026-06-06, 3d
    Ontology and object index            :done, s15, 2026-06-08, 3d
    Action and materialization loop      :done, s24, 2026-06-10, 4d
    Operations security SDK release gate :done, s33, 2026-06-12, 3d
    section Scale Foundation
    Port adapter contract boundary       :done, s02a, 2026-06-09, 4d
    Operational hardening                :done, s36a, 2026-06-13, 1d
    section v1.5 Expansion
    REST webhook stream CDC search       :done, s37, 2026-06-14, 5d
    Iceberg Spark deployment hardening   :s43, 2026-06-19, 5d
```

> 위 Gantt는 이해를 돕기 위한 roadmap shape입니다. 실제 완료 증거는 [docs/sprint-evidence-ledger.md](docs/sprint-evidence-ledger.md), 현재 구현 상태는 [docs/implementation-status.md](docs/implementation-status.md)를 따릅니다.

### 현재 남은 큰 목표

| 목표 | 상태 |
|---|---|
| PostgreSQL JSONB production object store | 목표, 현재 local SQLite/SQLAlchemy 중심 |
| Alembic migration history | 목표, 현재 schema revision guard로 drift 차단 |
| Temporal workflow/worker execution | 목표, 현재 workflow port/local adapter proof |
| Real CEL/JSON Logic evaluator | 목표, 현재 safeExpression subset |
| Real external ERP/webhook writeback | 목표, 현재 mock/local proof |
| Production connector registry/retry workers | 목표 |
| Managed Elasticsearch deployment | 목표, adapter/projection proof는 존재 |
| Continuously running CDC/search workers | 목표 |
| Iceberg/Spark/Kubernetes production package | 목표, Sprint 43-45 future scope |
| Broader Operations UI | 목표, 기본 panel/detail/retry/replay는 존재 |

## 문서 지도

| 문서 | 역할 |
|---|---|
| [docs/implementation-status.md](docs/implementation-status.md) | 현재 커밋이 실제로 보장하는 것과 아직 목표인 것의 경계 |
| [docs/mvp-scope.md](docs/mvp-scope.md) | v1 core에 포함되는 것과 명시적으로 제외되는 것 |
| [docs/infra-ratchet.md](docs/infra-ratchet.md) | 인프라를 하나씩 추가하고 실패/동시성/재시도/부분 성공/복구/운영 증거를 CI에 고정하는 규칙 |
| [foundry_lite_development_plan_ko_sprintified.md](foundry_lite_development_plan_ko_sprintified.md) | 제품 목표와 설계 원본 |
| [foundry_lite_sprint_breakdown_ko.md](foundry_lite_sprint_breakdown_ko.md) | 스프린트 순서와 Must-Win Goal 원본 |
| [foundry_lite_python_engineering_guidelines_ko.md](foundry_lite_python_engineering_guidelines_ko.md) | Python 백엔드 구현 표준 |
| [docs/quality-observability.md](docs/quality-observability.md) | 품질 게이트, tracing, metrics, diagnostics |
| [docs/quality-gate-roadmap.md](docs/quality-gate-roadmap.md) | 품질 게이트 강화 로드맵 |
| [docs/commit-point-risk-register.md](docs/commit-point-risk-register.md) | commit point 위험과 회귀 테스트 목록 |
| [examples/supply-chain-demo/README.md](examples/supply-chain-demo/README.md) | 공급망 데모 설명 |
| [scripts/diagnostics/README.md](scripts/diagnostics/README.md) | 런타임 진단 도구 설명 |

## GitHub README 시각화 방식

이 README는 GitHub 첫 화면에서 바로 렌더링되는 시각화만 직접 사용합니다.

| 방식 | README에서 사용 | 설명 |
|---|---:|---|
| Shields.io badge | 예 | 기술 스택과 release gate를 첫 화면에서 빠르게 보여줍니다. |
| Mermaid flowchart | 예 | 전체 폐루프, 계층 구조, 품질 게이트를 보여줍니다. |
| Mermaid sequence diagram | 예 | 데모 실행, 보안 흐름처럼 시간 순서가 중요한 흐름을 보여줍니다. |
| Mermaid state diagram | 예 | dataset transaction/action 상태 전이를 보여줍니다. |
| Mermaid ER diagram | 예 | 핵심 DB/도메인 엔티티 관계를 보여줍니다. |
| Mermaid class diagram | 예 | `FoundryLite` facade와 public sub-facade 관계를 보여줍니다. |
| Mermaid journey | 예 | MVP 통합 시나리오를 사용자 여정처럼 보여줍니다. |
| Mermaid gantt | 예 | roadmap shape를 보여줍니다. |
| Markdown table | 예 | 구현 상태, 포트/어댑터, 품질 게이트를 비교합니다. |
| HTML details | 필요시 가능 | 너무 긴 섹션을 접을 때 쓸 수 있습니다. |
| SVG/PNG 이미지 | 가능 | 별도 산출물을 `docs/`나 `artifacts/`에 넣으면 README에서 참조할 수 있습니다. |
| D3.js / Chart.js / ECharts / Plotly / Vega-Lite | 직접 실행 불가 | GitHub README는 임의 JavaScript를 실행하지 않습니다. 필요하면 PNG/SVG로 export해서 포함해야 합니다. |
| PlantUML / Graphviz | 직접 실행 불가 | GitHub에서 바로 실행되지 않으므로 Mermaid 또는 export 이미지가 안전합니다. |

## 기여 원칙

이 프로젝트는 단순 패치보다 원인 제거와 회귀 방지를 중시합니다.

- 문서와 코드가 다르면 코드를 과장하지 말고 현재 구현 상태를 정직하게 적습니다.
- `FoundryLite`를 다시 god class로 만들지 않습니다.
- API router가 repository나 DB transaction을 직접 만지지 않습니다.
- Repository는 business rule을 판단하지 않습니다.
- 새 mutation은 transaction, audit, outbox, idempotency, error traceability를 함께 고려합니다.
- bugfix는 가능한 한 같은 변경 안에 regression test를 포함합니다.
- PR 설명에는 Root Cause, Impact, Regression Test를 적습니다.

```markdown
## Root Cause

## Impact

## Regression Test
```

Foundry-lite의 북극성은 하나입니다.

> **데이터 유입 -> 변환 -> 온톨로지/객체 -> 액션 -> 감사/아웃박스 -> 데이터셋 환류 -> 다시 객체 갱신**이 반복 가능하고, 추적 가능하고, 테스트로 증명되는 것.
