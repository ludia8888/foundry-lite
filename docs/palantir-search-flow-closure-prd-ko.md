# Foundry-lite 검색 사용자 흐름 폐쇄 PRD

**문서 상태:** 승인 대상 제품 요구사항 / 구현 완료 증거가 아님
**기준일:** 2026-08-05
**제품 범위:** 미디어 인덱스 세대 활성화, 영상 의미 검색 표면, Media Set 스코프, 통합 검색 노출
**현재 상태 원본:** [Implementation Status](./implementation-status.md), [Action Types 비교표](./action-types-parity-matrix.json)
**증거 원본:** [Sprint Evidence Ledger](./sprint-evidence-ledger.md)
**선행 PRD:** [Palantir Action/MCP PRD](./palantir-action-mcp-prd-ko.md)

> 이 문서는 Palantir 공개 문서에서 확인 가능한 **동작 계약**을 Foundry-lite 요구사항으로 번역한 것이다.
> Palantir의 비공개 소스, 내부 프롬프트, 운영 인프라를 복제했다는 뜻이 아니다.
> 여기 적힌 목표를 현재 구현으로 읽으면 안 된다.

---

## 1. 문제 정의

비정형 콘텐츠가 **업로드 → 처리 → 인덱싱 → 활성 세대 전환**을 모두 통과해야 검색된다.
현재 네 지점에서 흐름이 끊겨 있고, 사용자 관점에서는 "올렸는데 안 찾아진다"로 나타난다.

| # | 끊긴 지점 | 코드 증거 |
|---|---|---|
| G1 | 브라우저 파이프라인이 업로드마다 `gen-<시간값>` 세대를 만들어 인덱싱하지만 텍스트 인덱스의 활성 세대 전환을 호출하지 않는다 | `apps/foundry/src/features/datasets/MediaPipelinePanel.tsx:80`이 `indexGeneration: gen-${Date.now()}`를 생성. `ContentIndexAdapter.promote_generation` (`libs/foundry_lite/application/ports/content_index.py:141`)은 존재하나 호출부 없음 |
| G2 | 영상 의미 검색이 API·SDK에는 있으나 Media 화면에 검색 UI가 없다 | `POST /api/media/visual/search` (`apps/api/foundry_lite_api/routers/media.py:293`) 존재. `MediaSearchPanel`은 `client.media.content.search`만 호출 |
| G3 | Media 화면 검색이 선택한 Media Set으로 제한되지 않고 테넌트 전체 인덱스를 검색한다 | `MediaSearchPanel.tsx:68` 페이로드가 `{text, topK}` — `mediaSetId` 없음 |
| G4 | 객체와 연결 문서를 한꺼번에 찾는 통합 검색 엔진이 일반 검색창·Ontology MCP에 노출되지 않았다 | `OntologySearchService.unified_search` (`libs/foundry_lite/application/services/ontology_search.py:114`) 존재. `ontology_mcp_tools.object_tools`는 `search`(lexical)만 노출하고 `semantic_text`를 게이트웨이가 넘기지 않음 (`ontology_mcp_gateway.py:348`) |

---

## 2. Palantir는 어떻게 구현했는가

모든 설계 결정의 판단 기준. 각 항목은 공개 문서에서 확인한 동작 계약이다.

### 2.1 인덱싱 완료 전에는 질의 대상이 되지 않는다 → G1

Palantir는 인덱싱 작업이 끝나야 객체 타입이 질의 가능해진다. Ontology Manager의 파이프라인 그래프가
작업 상태를 보여주고, **Object Storage v2 노드에 녹색 체크가 떠야** "indexing is complete and the object
type is ready to be queried from OSv2"다. 사용자가 절반만 만들어진 인덱스를 보는 상태가 존재하지 않는다.

→ **요구사항:** 인덱싱과 "검색 가능"은 분리된 두 상태다. 세대에 쓰는 것만으로 검색 가능해지면 안 되고,
완료 판정 후 활성 전환이 일어나야 한다. 우리 포트가 이미 이 모델을 문서화하고 있다 —
`ContentIndexAdapter` docstring: *"generation promotion is shadow-then-switch"* (ADR-0001 invariant 5).
Palantir의 녹색 체크에 대응하는 것이 우리의 `promote_generation` 호출이다.

### 2.2 object set을 먼저 좁히고 그 안에서 kNN → G3

Palantir의 의미 검색 스코프 방식은 명확하다. object set에 필터를 먼저 적용하고 그 결과 위에서
최근접 이웃을 찾는다.

```typescript
Objects.search().objectType()
  .filter(obj => obj.category.exactMatch(category))
  .nearestNeighbors(obj => obj.embedding.near(vector, { kValue }))
  .orderByRelevance()
  .take(kValue)
```

Python OSDK V2도 동일하다 — "filter object sets using `nearest_neighbors` to find the set of objects whose
specified vector property is nearest to a provided query vector or text."

→ **요구사항:** Media Set 스코프는 검색 결과를 사후 필터링하는 것이 아니라 **질의 시점에 후보 집합을
좁히는 것**이다. topK가 전체 테넌트에서 뽑힌 뒤 걸러지면 해당 Media Set의 상위 결과를 놓친다.

### 2.3 정형 객체와 비정형 청크를 한 질의로, RRF로 융합 → G4

Ontology-Augmented Generation(OAG)이 이 계약이다. 문서는 청킹 전략에 따라 청크로 나뉘고, 각 청크는
**media reference를 가진 온톨로지 객체**로 저장된다. 질의는 임베딩으로 변환되어 청크 임베딩과 매칭되고,
벡터 검색과 키워드 검색 결과를 **Reciprocal Rank Fusion**으로 합친다 — `1 / (k + r(d))`, `k`는 균형을
조절하는 정규화 상수.

노출 표면은 Workshop, AIP Logic, Functions(`Objects.search()`), API Gateway다. Palantir 자체 MCP 서버도
`search_foundry_ontology`, `query_ontology_objects`를 도구로 노출한다.

→ **요구사항:** 통합 검색은 내부 엔진으로만 존재하면 안 되고 사람이 쓰는 검색창과 에이전트가 쓰는 MCP
양쪽에 같은 계약으로 나와야 한다.

### 2.4 미디어는 온톨로지 객체에 media reference로 붙어 UI에서 미리보기된다 → G2

Media reference 객체 속성은 온톨로지 기반 애플리케이션에서 미디어를 효율적으로 표시하는 데 쓰이며,
Workshop과 Object Explorer의 빠른 대화형 미리보기, Map의 지리공간 이미지 타일링에 최적화된다.
검색 결과는 원본 객체·미디어로 다시 연결된다.

→ **요구사항:** 영상 의미 검색 결과는 단순 목록이 아니라 원본 미디어 아이템과 타임코드로 되돌아가는
인용(citation)을 가져야 한다. 우리 `ContentSearchHit`은 이미 `start_ms`/`end_ms`/`timecode`/`bbox`/
`source_locator`를 들고 있다.

---

## 3. 요구사항

### R1 — 업로드가 검색 가능 상태로 끝난다 (G1)

브라우저 업로드 파이프라인은 인덱싱 후 텍스트 인덱스의 활성 세대 전환까지 수행하고, 그 결과를 사용자에게
보여준다.

- 파이프라인 응답에 `indexGeneration`과 **활성 여부**가 함께 담긴다.
- 전환은 `promote_generation(expected_active, shadow)` 계약을 그대로 쓴다 — `expected_active` 불일치는
  실패로 처리한다(동시 업로드 경합에서 한쪽만 이긴다).
- 전환 전에는 UI가 "인덱싱됨 / 아직 검색 불가"를 구분해 표시한다. Palantir의 녹색 체크에 대응한다.
- 전환 실패 시 이전 활성 세대가 그대로 유지된다. 절반 상태가 노출되지 않는다.

### R2 — Media 화면에서 영상을 의미로 검색한다 (G2)

Media 화면에 검색 모드를 둔다: **텍스트 콘텐츠 검색**과 **시각 의미 검색**.

- 시각 검색은 `POST /api/media/visual/search`를 호출한다.
- 결과는 미디어 아이템, 타임코드(`start_ms`/`end_ms`), 프레임 인용을 보여준다.
- 결과에서 원본 미디어로 이동할 수 있다.

### R3 — Media 검색은 선택한 Media Set으로 제한된다 (G3)

- 검색 페이로드에 `mediaSetId`를 필수로 싣는다.
- 스코프는 질의 시점에 적용한다(사후 필터 금지). §2.2 계약.
- 스코프 해제는 명시적 선택이어야 한다 — 기본값이 테넌트 전체가 되면 안 된다.

### R4 — 통합 검색을 사람과 에이전트 양쪽에 노출한다 (G4)

- **브라우저:** 일반 검색창이 `unified_search`를 호출해 객체와 연결 문서를 한 목록으로 보여준다.
  각 결과는 객체 링크와 `MediaCitation`을 함께 보여준다.
- **Ontology MCP:** 두 가지를 연다.
  1. `object.{타입}.search`에 `semanticText` 프로퍼티를 추가하고 게이트웨이가 `semantic_text`로 전달한다.
     런타임 계약(`OntologyMcpObjectRuntime.query`)은 이미 이 인자를 받는다 — 배관만 잇는다.
  2. 통합 검색 도구를 추가한다. Palantir MCP의 `search_foundry_ontology` 계약을 따른다.
- 두 표면 모두 기존 권한 투영을 그대로 통과한다. 검색이 권한 우회 경로가 되면 안 된다.

---

## 4. 비목표

- 새 임베딩 엔진·모델 도입. 기존 fastembed/CLIP 경로를 쓴다.
- 인덱스 세대의 자동 청소·보존 정책. 별건이다.
- Workshop 수준의 완전한 미디어 위젯 세트.
- 비주얼 파이프라인 빌더 UI.

---

## 5. 증거 계획

| 요구사항 | 증거 |
|---|---|
| R1 | 업로드 → 인덱싱 → 전환 → 검색이 한 흐름에서 이어지는 통합 테스트. 전환 전 검색 결과 0건, 전환 후 조회됨을 같은 테스트에서 확인. `expected_active` 경합 시 한쪽만 승리 |
| R2 | 영상 의미 검색 UI의 브라우저 E2E. 질의 → 타임코드 인용 표시 → 원본 이동 |
| R3 | 두 Media Set에 같은 텍스트를 인덱싱하고 한쪽으로 스코프한 질의가 다른 쪽 히트를 반환하지 않음. 사후 필터가 아니라 질의 시점 스코프임을 topK 경계에서 확인 |
| R4 | MCP `semanticText` 전달 단위 테스트, 통합 검색 도구의 권한 투영 테스트, 브라우저 검색창 E2E |

---

## 6. 참고 문헌

- [Indexing • Overview](https://www.palantir.com/docs/foundry/object-indexing/overview)
- [Indexing • FAQ](https://www.palantir.com/docs/foundry/object-indexing/faq)
- [Semantic search • Ontology augmented generation](https://www.palantir.com/docs/foundry/ontology/ontology-augmented-generation)
- [Semantic search • Document processing](https://www.palantir.com/docs/foundry/ontology/document-processing)
- [Functions • Semantic search workflow (Palantir-provided models)](https://www.palantir.com/docs/foundry/functions/using-palantir-provided-models-to-create-a-semantic-search-workflow/)
- [Media sets • Using media in the Ontology](https://www.palantir.com/docs/foundry/media-sets-advanced-formats/media-in-ontology)
- [The Multimodal Data Plane](https://www.palantir.com/docs/foundry/architecture-center/multimodal-data-plane)
- [Building with Palantir AIP: Semantic Search](https://blog.palantir.com/building-with-palantir-aip-semantic-search-dc3adf40f6a6)
