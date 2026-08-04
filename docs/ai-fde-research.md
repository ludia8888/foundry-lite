# AI FDE 공개 동작 연구와 Foundry-lite 이식 결과

기준일은 2026-08-04입니다. 이 문서는 Palantir가 공개한 제품 문서를 동작 계약으로 분석하고 Foundry-lite의 실제 제품 경로와 대조한 결과입니다. 비공개 소스 코드나 내부 구현을 복제했다는 뜻은 아닙니다. 세부 상태와 증거는 [AI FDE 비교표](./ai-fde-parity-matrix.json)가 기준입니다.

## 공식 문서에서 확인한 핵심

- AI FDE는 단순 질의응답기가 아니라 사용자의 의도를 native operation으로 바꾸고 실행 결과를 관찰해 다음 행동을 정하는 대화형 운영자입니다. [AI FDE overview](https://www.palantir.com/docs/foundry/ai-fde/overview)
- mode는 작업별 문서·capability·tool 묶음이며 data integration, data connection, ontology, functions, exploration, governance, ML, OSDK React, platform Q&A로 나뉩니다. [Modes and capabilities](https://www.palantir.com/docs/foundry/ai-fde/modes-and-capabilities)
- AI는 현재 사용자의 권한으로 동작하고, 읽기 도구와 달리 mutation은 명시적인 사용자 확인을 요구합니다. [Security and governance](https://www.palantir.com/docs/foundry/ai-fde/security-and-governance)
- 리소스 변경은 branch에서 만들고 proposal 또는 code review로 검토하는 것이 기본 운영 경계입니다. 작은 context/tool 집합으로 결과를 검증하며 반복하는 방식이 권장됩니다. [Best practices](https://www.palantir.com/docs/foundry/ai-fde/best-practices)
- Palantir MCP는 build-time platform tool plane이고 Ontology MCP는 허용된 object/action/query를 소비하는 plane입니다. [Palantir MCP overview](https://www.palantir.com/docs/foundry/palantir-mcp/overview), [MCP security](https://www.palantir.com/docs/foundry/palantir-mcp/security)
- Pilot은 ontology design, seed data, React UI, CI, Developer Console application을 한 흐름으로 생성합니다. [Pilot overview](https://www.palantir.com/docs/foundry/pilot/overview)

## Foundry-lite에 적용한 실행 계약

```mermaid
flowchart LR
    U["사용자 자연어 요구"] --> I["현재 사용자와 앱 권한 교집합"]
    I --> M["9개 mode 중 작업 mode 선택"]
    M --> W["명시적 workspace와 첨부 리소스"]
    W --> S["lazy tool search 또는 eager catalog"]
    S --> P["구조화 plan 또는 clarification"]
    P --> T["server-owned native tool 실행"]
    T --> C{"mutation인가?"}
    C -- "아니오" --> O["결과 관찰 후 다음 step"]
    C -- "예" --> X{"명명된 tool 확인이 있는가?"}
    X -- "없음" --> D["fail closed + durable evidence"]
    X -- "있음" --> B["Ontology/Pipeline branch CAS 변경"]
    B --> V["validation + diff + test evidence"]
    V --> H["사람 검토 proposal"]
    O --> E["session/run/tool/result 원장"]
    H --> E
    E --> Z["AI에게 approve/merge/deploy/activate 미노출"]
```

현재 제품 경로는 다음과 같습니다.

- `GET /api/aip/fde/catalog`는 호출자에게 실제 허용된 mode와 tool만 반환합니다.
- `POST /api/aip/fde/run`은 최대 8회의 bounded execute-observe-adjust loop, lazy tool search, 구조화 plan/clarification을 실행합니다.
- current mode는 `exploration`, `ontology_editing`, `data_integration`, `data_connection`, `functions_editing`, `governance`, `ml`, `osdk_react`, `platform_qa`입니다.
- Dataset, Ontology/Pipeline branch, Source, Function, OSDK app, Project, Resource, Model context는 정상 서비스 권한으로 다시 읽고 버전·해시·token budget이 붙은 evidence로 저장합니다.
- Ontology와 Pipeline 변경은 선택한 branch에만 쓰며 proposal까지 만들 수 있습니다. AI에게 승인·merge·deploy·activation tool은 제공하지 않습니다.
- 모델·도구·입력·구조화 결과·사용량·상태 이벤트는 `ai_sessions`, `ai_execution_runs`, `ai_tool_calls` 원장에 남습니다.
- `/mcp/builder/{application_id}`는 Streamable HTTP, OAuth Authorization Code + PKCE, app/client/resource-scope 제한, Origin 검증, native structured content, JSON-RPC 재전송 idempotency, out-of-band mutation 확인을 제공합니다.
- Pilot API와 AIP UI는 Project, replay-safe seed Dataset, Ontology branch, OSDK application, 실제 OSDK query를 사용하는 React source, CI workflow, durable resource, stable preview path를 한 번의 명시적 생성으로 만듭니다.
- generated TypeScript/browser SDK와 AIP/Pilot 화면은 같은 named API 계약을 사용합니다.

## 완성 경계

비교표의 16개 공개 동작 축은 모두 `current`입니다. 여기서 “완성”은 AI가 production 통제를 우회한다는 뜻이 아닙니다. 오히려 아래의 제한이 완성 조건입니다.

- 사용자와 애플리케이션의 권한 교집합 밖 도구는 보이지도 실행되지도 않습니다.
- mutation은 매 실행의 명시적 확인 없이는 실패합니다.
- Ontology와 Pipeline 변경은 branch와 사람 검토를 통과해야 합니다.
- Pilot 생성 직후 화면은 안전한 preview이며 production 객체·Action 사용은 Ontology 활성화 뒤에만 가능합니다.
- 공개 문서에서 확인할 수 없는 Palantir 내부 구현과 proprietary prompting은 동일 구현이라고 주장하지 않습니다.
