# Foundry-lite Virtual Tables PRD

**문서 상태:** 승인 대상 제품 요구사항 / 구현 완료 증거가 아님
**기준일:** 2026-08-05
**제품 범위:** 외부 테이블 등록, push-down 읽기, 온톨로지 객체 백킹, update detection
**현재 상태 원본:** [Implementation Status](./implementation-status.md)
**증거 원본:** [Sprint Evidence Ledger](./sprint-evidence-ledger.md)

> 이 문서는 Palantir 공개 문서에서 확인 가능한 **동작 계약**을 Foundry-lite 요구사항으로 번역한 것이다.
> 여기 적힌 목표를 현재 구현으로 읽으면 안 된다.

---

## 1. 문제 정의

원장(source of truth)이 Foundry-lite 밖에 있는데 데이터를 가져와야 하는 상황에서, 현재 선택지는
**복사뿐**이다. `SqlAlchemySourceDatabaseAdapter`의 배치 읽기와 Debezium CDC 모두 사본을 만든다.

사본은 원장 결정을 무너뜨린다. 같은 데이터가 두 곳에 존재하고, 지연·순서·유실 창이 생기며, 스키마
변경 시 두 곳을 고쳐야 하고, 무엇보다 **"어느 쪽이 truth인가"가 매 질문마다 애매해진다.**

`source.virtual_table` / `output.virtual_table` 노드 서술자는 이미 선언돼 있으나
`_planned_descriptor`이므로 `PipelineNodeAvailability.VALIDATION_ONLY`다. 그래프 검증만 통과하고
실행기가 없다 — `virtual_table_runtime` capability를 구현한 코드는 한 줄도 없다.

---

## 2. Palantir는 어떻게 구현했는가

모든 설계 결정의 판단 기준.

### 2.1 포인터이지 복사본이 아니다

Virtual Table은 "a pointer to a table in a source system outside of Foundry"이며,
"query and write to tables in supported data platforms **without storing the data in Foundry**"를
가능하게 한다. 목적은 "register new data sources without having to create redundant copies of the
associated data or pipelining logic"다.

→ **요구사항:** 등록은 메타데이터만 저장한다. 행을 우리 저장소로 옮기는 순간 이 기능이 아니다.

### 2.2 리소스 계약

`POST /connections/{connectionRid}/virtualTables`

| 필드 | 필수 | 의미 |
|---|---|---|
| `parentRid` | ✅ | 등록될 폴더 RID |
| `name` | ✅ | Virtual Table 이름 |
| `config` | ✅ | union — 소스별 테이블 식별 정보 |
| `markings` | | 접근 마킹 |

응답은 `rid`, `name`, `parentRid`, `config`, `markings`. 스코프는
`api:connectivity-virtual-table-write`.

→ **요구사항:** 필드명을 그대로 쓴다. `config`가 union인 것이 중요하다 — 소스 타입마다 테이블 식별
방식이 다르므로(Postgres는 schema+table, 객체 스토리지는 경로+포맷) 하나의 평평한 스키마로 눌러선 안 된다.

### 2.3 컴퓨트 푸시다운

소스가 지원하는 연산은 소스의 네이티브 쿼리로 번역해 **거기서 실행**하고, 지원하지 않으면 데이터를
가져와 Foundry/Spark에서 처리한다. predicate pushdown은 행이 외부 시스템을 떠나기 전에 거른다.
"harnesses the native features across different data platforms ... which results in better
performance."

→ **요구사항:** 읽기는 filter/projection/limit을 SQL로 번역해 원격에서 실행한다. 번역 불가한 연산이
있으면 **가져와서 로컬 처리하되 그 사실을 증거로 남긴다.** 조용히 전체 스캔으로 떨어지면 안 된다.

### 2.4 온톨로지 객체를 직접 백킹할 수 있다

Ontology Manager에서 virtual table로 백킹되는 객체를 구성할 수 있다. 단, **"If the backing virtual
table is updated outside of Foundry, you should enable update detection on the virtual table to
ensure the objects receive regular updates from the source system."**

→ **요구사항:** 외부 writer가 계속 쓰는 소스에는 update detection이 필수다. 이것이 없으면 객체가
조용히 낡는다.

### 2.5 재현성 — 핀을 만들지 않고 없다고 말한다

> "Virtual tables do not benefit from Foundry dataset capabilities such as **dataset versioning
> or branching**."

이것이 이 기능에서 가장 중요한 판단이다. Palantir는 virtual table을 transform 입력으로
**허용하면서**, 버저닝이 없다는 사실을 문서에 명시한다. 없는 재현성을 합성 핀으로 흉내내지도
않고, 그렇다고 파이프라인에서 배제하지도 않는다.

관련 제약도 같은 맥락이다 — `@incremental`은 compute pushdown과 함께 쓸 수 없고
("not currently supported when using compute pushdown"), 트랜잭션은 append-only다. 둘 다
"이전 실행 상태와 비교"를 전제하는데 핀이 없으면 성립하지 않기 때문이다.

→ **요구사항:** 실행 계획은 핀의 **부재를 기록**한다. 합성 핀은 replay·late-data 경로에
재현 가능한 것처럼 보이면서 실제로는 재현 불가능하므로 더 나쁘다. 라이브 소스를 읽은 실행은
표시되고, 핀이 있는 소스와 조용히 동일 취급되지 않는다.

### 2.6 Palantir가 명시한 제약

- **MDO 불가** — "only Foundry datasets or restricted views can be used for MDOs"
- **버저닝·브랜칭 없음** (§2.5)
- **incremental + compute pushdown 동시 사용 불가**
- **append-only 트랜잭션**
- **Ontology-as-Code / Marketplace 경로 미지원**
- Palantir 자신의 권고: "virtual tables vs. sync ... depends on your architecture goals and the
  target workflow ... on a workflow-by-workflow basis"

→ **요구사항:** 같은 제약을 그대로 승계한다. 우리가 Palantir보다 더 해주겠다고 넓히지 않는다.

---

## 3. 요구사항

### R1 — 등록 (V1)

- `VirtualTableRepository` 포트: `rid` · `name` · `parent_rid` · `connection_rid` · `config` ·
  `markings`. Palantir 필드명 유지
- 등록 시 소스에서 스키마를 읽어 **핀한다**. 이후 소스 스키마가 달라지면 조용히 따라가지 않고
  드리프트로 실패시킨다 — 핀 없는 포인터는 계약이 아니다
- 테넌트 스코프. 등록·삭제는 감사 원장에 남는다

### R2 — Push-down 읽기 (V1)

- `VirtualTableReader` 포트: filter AST · projection · limit을 받아 **원격에서 실행**한 행을 반환
- Postgres 어댑터: 기존 `SqlAlchemySourceDatabaseAdapter`의 식별자 방어(`_require_safe_identifier`)와
  실패 taxonomy(`failure_contract`)를 재사용한다
- **push-down 실패는 증거를 남긴다.** 번역 불가 술어가 있으면 남은 술어를 로컬에서 적용하되,
  응답에 `pushedDownPredicates` / `localPredicates`를 실어 무엇이 어디서 걸러졌는지 보이게 한다
- 결과 상한을 강제한다. 원격 테이블이 크다는 이유로 무한 스캔이 되면 안 된다

### R3 — 라이브 소스 계약 (V1)

- `PipelineSourceContract.is_live_source`: 소스는 커밋된 버전에 핀되거나 라이브로 선언되거나
  **둘 중 하나**다. 라이브 소스가 핀을 들고 있으면 검증 실패 — replay가 신뢰할 허구가 된다
- 실행 계획 payload에 `isLiveSource`를 노출해 증거 독자가 재현 가능한 실행과 아닌 실행을
  구분할 수 있게 한다
- 기본값은 핀이다. 기존 계획의 보장은 그대로 유지된다

### R3b — 노드 승격 (V1 잔여)

- `source.virtual_table`을 `_planned_descriptor` → `_graph_v2_descriptor`로 올리고 executor 분기 추가
- 이 노드는 `is_live_source=True` 계약을 발급한다
- `output.virtual_table`은 **planned로 유지한다** — write-back은 이 범위 밖이다

### R4 — 온톨로지 백킹 (V2)

- 객체 타입이 virtual table을 datasource로 지정할 수 있다. 인덱싱이 아니라 **질의 시점 위임**
- 이것은 `active_index_version` 세대 모델과 다른 경로다. 두 모델이 공존해야 하며, 객체 타입마다
  어느 쪽인지 명시적으로 선언한다
- MDO에는 쓸 수 없다 (§2.5)

### R5 — Update detection (V2)

- 외부 writer가 있는 소스는 update detection 없이 백킹할 수 없다 — 등록 시 강제한다
- 체크포인트 컬럼(예: `updated_at`) 폴링으로 시작한다. `read_table_batch`가 이미
  `checkpoint_column`을 받는다
- 탐지 실패·지연은 운영자 증거로 노출한다. 객체가 낡았는데 아무도 모르는 상태를 만들지 않는다

### R6 — 거버넌스 (V3)

- `markings`를 원격 행에 적용한다. 분류 PRE-filter와 같은 패턴 — 질의에 컴파일하고 사후 필터로 붙이지 않는다
- 커넥션 실패는 타입화된 `AdapterError`로 나온다 (기존 failure_contract 재사용)

---

## 4. 비목표

- write-back (`output.virtual_table`) — planned 유지
- incremental / branching — Palantir도 미지원
- MDO 백킹 — Palantir가 명시적으로 금지
- Postgres 외 소스 — V1은 Postgres 하나. 포트는 열어두되 어댑터는 하나
- 소셜 API 직접 연결 — virtual table 대상이 아니다 (수집된 DB만)

---

## 5. 증거 계획

| 요구사항 | 증거 |
|---|---|
| R1 | 등록 후 조회 왕복, 스키마 핀 저장, 소스 스키마 변경 시 드리프트 실패, 테넌트 격리 |
| R2 | filter가 **원격 SQL에 나타남**을 확인(로컬 필터였다면 안 나타남), limit 경계에서 스코프 밖 행이 반환되지 않음, 번역 불가 술어가 `localPredicates`로 보고됨 |
| R3 | 승격된 노드가 그래프 실행에서 실제로 행을 반환, `output.virtual_table`은 여전히 VALIDATION_ONLY |
| R4 | 객체 질의가 색인이 아니라 원격을 탐, 같은 질의가 소스 변경을 즉시 반영 |
| R5 | update detection 없는 소스는 백킹 등록이 거부됨, 체크포인트 전진이 관측됨 |
| R6 | 마킹 밖 행이 랭킹 전에 제외됨 |

de-risk: 실 Postgres(testcontainers)로 push-down이 정말 원격에서 도는지 먼저 증명한다. 이것이 안
되면 나머지 설계가 의미 없다.

---

## 6. 참고 문헌

- [Core concepts • Virtual tables](https://www.palantir.com/docs/foundry/data-integration/virtual-tables)
- [Create Virtual Table • API Reference](https://www.palantir.com/docs/foundry/api/connectivity-v2-resources/virtual-tables/create-virtual-table)
- [Virtual Table basics • API Reference](https://www.palantir.com/docs/foundry/api/connectivity-v2-resources/virtual-tables/virtual-table-basics)
- [Virtual tables and compute pushdown](https://www.palantir.com/docs/foundry/transforms-python/tables-overview)
- [Multi-datasource object types](https://www.palantir.com/docs/foundry/object-permissioning/multi-datasource-objects)
- [AIP Virtual Tables](https://blog.palantir.com/aip-virtual-tables-5094b5e4b3bd)
