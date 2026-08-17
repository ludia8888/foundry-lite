# Foundry-lite Documentation Map

**Status:** Current documentation operating model / source-of-truth map
**Audience:** Maintainers, reviewers, and future frontend/backend implementers

이 문서는 repo 안의 문서들이 서로 어떤 책임을 갖는지 정리한다. 비개발자식으로 말하면,
문서들이 모두 같은 일을 하는 것이 아니라 "원본 장부", "요약판", "증거 장부", "위험
체크리스트", "CI 규칙"으로 역할이 나뉘어 있다. 이 지도를 먼저 보면 어떤 문서를 고쳐야
하는지, 어떤 문서는 그대로 두어야 하는지, 그리고 코드와 문서가 맞는지 어떻게 확인해야
하는지 알 수 있다.

## Source Of Truth Rules

| Source of truth                                       | Governs                                                                                      | When to update                                                                     |
| ----------------------------------------------------- | -------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| `docs/documentation-map.md`                           | 문서별 역할, source-of-truth 규칙, update order, cross-check command, README gate briefing   | 문서가 추가/삭제되거나 README 문서 지도, 품질 게이트, 문서 운영 규칙이 바뀔 때     |
| `docs/implementation-status.md`                       | 현재 코드가 실제로 보장하는 current/partial/future 경계                                      | 코드, API, SDK, infra, gate가 새 보장을 만들거나 기존 보장을 제거할 때             |
| `docs/sprint-evidence-ledger.md`                      | 어떤 claim이 어떤 테스트/게이트/PR evidence로 증명되는지                                     | 새 proof, 새 gate, 새 sprint evidence, 또는 proof command가 바뀔 때                |
| `foundry_lite_development_plan_ko_sprintified.md`     | 제품/아키텍처 목표와 장기 설계 원본                                                          | 제품 목표 자체가 바뀌거나 current/future 경계를 명확히 해야 할 때                  |
| `foundry_lite_sprint_breakdown_ko.md`                 | 스프린트 순서, Must-Win Goal, 전체 체크박스 원본                                             | 스프린트 상태가 Done/Partial/Future로 바뀔 때                                      |
| `docs/data-platform-expansion-sprint-plan-ko.md`      | S46 이후 데이터 플랫폼 확장 roadmap, 상세 체크리스트, sprint-by-sprint 계획                  | 다운로드 확장 계획, 체크박스 단위 상태, current/partial/future 경계가 바뀔 때      |
| `docs/data-engineering-pattern-matrix.json`           | 데이터 엔지니어링 패턴별 current/partial/deferred 상태, evidence, future test registry       | 패턴 status, proof level, owning doc, infra reference, future test가 바뀔 때       |
| `docs/frontend-api-sdk-surface-matrix.json`           | frontend-consumable API route/helper -> named SDK -> proof mapping                           | 프론트가 쓸 API route, SDK method, SDK helper가 추가/삭제/변경될 때                |
| `docs/frontend-backend-surface-contract.md`           | 프론트가 백엔드를 붙이는 공식 계약                                                           | SDK safety helper, named SDK rule, frontend route policy가 바뀔 때                 |
| `docs/infra-ratchet.md`                               | 인프라 추가 순서와 ratchet proof 방법론                                                      | 새 인프라 profile이나 active composition rule이 바뀔 때                            |
| `docs/infra-tricky-matrix.json`                       | 인프라별 tricky failure proof registry                                                       | 새 infra proof class, source-of-truth rule, operator-evidence rule이 추가될 때     |
| `docs/foundry_lite_tricky_failure_modes_checklist.md` | future hardening backlog와 failure-mode 후보 장부                                            | 새로운 실패 모드가 발견되거나 체크리스트 증거 이름이 바뀔 때                       |
| `docs/quality-gate-roadmap.md`                        | 품질 게이트의 의도, coverage, release/runtime lane, operator evidence, diagnostics 운영 규칙 | 새 gate, gate root cause, runtime/release lane, diagnostic artifact 규칙이 바뀔 때 |
| `docs/commit-point-risk-register.md`                  | commit point, idempotency, partial failure, cleanup risk registry                            | transaction boundary, retry, cleanup, rollback 위험이나 회귀 테스트가 바뀔 때      |
| `README.md`                                           | GitHub 첫 화면용 요약과 문서 진입점                                                          | 위 원본 문서들이 바뀐 뒤, 외부 독자가 볼 요약만 갱신할 때                          |

## Update Order

문서와 코드를 같이 고칠 때는 아래 순서를 따른다.

1. Code/API/SDK/schema/gate를 먼저 실제 source of truth로 만든다.
2. `docs/sprint-evidence-ledger.md`에 어떤 명령과 테스트가 그 사실을 증명하는지 남긴다.
3. `docs/implementation-status.md`에서 current/partial/future 경계를 업데이트한다.
4. `docs/data-platform-expansion-sprint-plan-ko.md`, `foundry_lite_sprint_breakdown_ko.md`의
   스프린트 상태를 맞춘다.
5. 필요하면 `docs/frontend-backend-surface-contract.md`, `docs/infra-ratchet.md`,
   `docs/infrastructure-swapability-matrix.json`,
   `docs/quality-gate-roadmap.md`, `docs/pipeline-builder-parity-matrix.json`,
   `docs/functions-object-set-parity-matrix.json` 같은 계약·패리티
   문서를 업데이트한다.
6. 마지막에 `README.md`를 GitHub 첫 화면용 요약으로 맞춘다.

이 순서가 중요한 이유는 README가 가장 잘 보이는 문서이지만, 원본 장부는 아니기 때문이다.
README는 현재 상태를 빠르게 보여주는 창이고, 실제 증명은 code, tests, gates, and evidence
ledger에 남아야 한다.

## MECE Documentation Taxonomy

문서는 아래 bucket 중 하나에만 속한다. 비개발자식으로 말하면, 같은 이야기를 여러 문서가
조금씩 다르게 반복하지 않도록 "문서의 직업"을 한 번 정해두는 것이다.

| Bucket             | Meaning                                                                                                   | Merge/retire rule                                                                                               |
| ------------------ | --------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `entrypoint`       | 사람이 가장 먼저 여는 진입점이다. `README.md`는 GitHub 첫 화면, `AGENTS.md`는 작업 시작 전 운영 규칙이다. | 상세 상태를 새로 만들지 않는다. 원본 문서가 바뀐 뒤 마지막에 요약과 링크만 갱신한다.                            |
| `source-of-truth`  | 제품/아키텍처/스프린트/운영 계약의 원본 장부다.                                                           | 같은 scope를 두 문서가 동시에 설명하면 더 세부적인 원본 하나로 합치고, 나머지는 링크와 요약만 남긴다.           |
| `machine-registry` | CI가 읽는 JSON registry다. 사람용 설명보다 status/proof/test mapping을 기계적으로 잠그는 역할이다.        | Markdown claim과 JSON registry가 충돌하면 JSON을 고치거나 Markdown claim을 낮춘 뒤 gate evidence를 맞춘다.      |
| `risk-registry`    | 실패 모드, commit point, partial failure, retry, cleanup 같은 위험 장부다.                                | 구현 상태 문서와 겹치면 위험 설명은 여기 남기고, 완료/부분/미래 상태는 implementation/evidence 문서로 넘긴다.   |
| `standard`         | 코드 작성 방식과 engineering rule을 정하는 기준 문서다.                                                   | 제품 상태나 sprint 완료 claim을 만들지 않는다. 구현 규칙만 남기고 상태 claim은 source-of-truth 문서로 이동한다. |
| `template`         | PR이나 반복 작업에 쓰는 형식 문서다.                                                                      | 정책의 원본이 아니다. template이 정책을 설명해야 하면 documentation map이나 quality roadmap으로 링크한다.       |
| `reference`        | 조사/배경/의사결정 참고 자료다.                                                                           | current implementation claim의 원본이 될 수 없다. 필요한 결정만 source-of-truth 문서로 승격한다.                |
| `example`          | 데모나 사용 예시다.                                                                                       | 예시는 동작 설명만 한다. 제품 scope나 운영 보장은 implementation/evidence 문서에서만 말한다.                    |

구조화 원칙:

1. 새 문서를 만들면 `Document Roles`에 bucket, 역할, 현재 refactor note를 먼저 등록한다.
2. 새 문서가 기존 문서와 같은 scope를 설명하면 새 문서를 늘리지 않고 기존 source-of-truth에 병합한다.
3. README는 절대 원본 장부가 아니다. GitHub 첫 화면용 요약과 링크만 가진다.
4. 예제와 연구 문서는 current 구현 상태를 단독으로 주장하지 않는다. 진단 안내는 `docs/quality-gate-roadmap.md` 안의 운영 섹션으로 합친다.
5. `machine-registry`와 Markdown 설명이 충돌하면 CI가 읽는 registry와 evidence ledger를 먼저 맞춘 뒤 사람용 문서를 갱신한다.
6. 문서 삭제/병합은 `quality:doc-drift`, `quality:evidence-ledger-commands`, `quality:documentation-map`
   통과로 증명한다.

## Document Roles

| Document                                                  | MECE bucket        | Role                                           | Current refactor note                                                                                                                                                          |
| --------------------------------------------------------- | ------------------ | ---------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `.github/pull_request_template.md`                        | `template`         | Pull request evidence template                 | PR이 root-cause/evidence/change summary를 남기도록 기본 reviewer shape를 제공한다.                                                                                             |
| `AGENTS.md`                                               | `entrypoint`       | Workspace instruction and active gate briefing | 비개발자도 이해할 수 있게 친절하고 자세히 설명해야 한다는 repo-level instruction이며, active documentation/quality gate 요약을 개발 시작 전에 보이게 한다.                     |
| `README.md`                                               | `entrypoint`       | GitHub first-screen summary and document index | S61/S62/S63 API/SDK proof를 짧게 요약하고 이 map으로 연결한다.                                                                                                                 |
| `docs/documentation-map.md`                               | `source-of-truth`  | Documentation operating map                    | 문서 역할, MECE taxonomy, source-of-truth, update order, cross-check command를 관리한다.                                                                                       |
| `docs/implementation-status.md`                           | `source-of-truth`  | Current implementation boundary                | S61 frontend surface lock, S62 Dataset Explorer backend/API/SDK start, S63 Insight Review queue를 current partial로 구분한다.                                                  |
| `docs/sprint-evidence-ledger.md`                          | `source-of-truth`  | Evidence ledger                                | 각 sprint claim은 command, test, and remaining future scope를 함께 남긴다.                                                                                                     |
| `docs/governed-release-hosted-staging-runbook.md`         | `reference`        | Hosted staging bootstrap operations runbook    | 보호형 staging bootstrap의 Render 설정, 외부 의존성, 비용 승인, migration/readiness 확인 순서를 설명하며 current/future 경계 원본은 implementation status와 evidence ledger로 연결한다. |
| `docs/macmini-enterprise-qa-runbook.md`                    | `reference`        | Mac mini Enterprise QA operations runbook      | `sean1234` 전용 Colima/k3s의 설치, 장애, 복원, hosted 확인, 24시간 소크와 단일-node `notProven` 경계를 설명한다. current/live 결과 원본은 implementation status와 evidence ledger다. |
| `docs/infra-ratchet.md`                                   | `source-of-truth`  | Infra ratchet methodology                      | 새 인프라는 self test plus active-infra composition test를 요구한다.                                                                                                           |
| `docs/infra-tricky-matrix.json`                           | `machine-registry` | Machine-readable infra proof matrix            | source-of-truth and operator-evidence gates가 읽는 registry다.                                                                                                                 |
| `docs/infrastructure-swapability-matrix.json`              | `machine-registry` | Infrastructure swapability registry            | 11개 핵심 인프라군의 port, composition selector, 구현, contract test, stateful cutover 미증명 경계를 잠근다.                                                                   |
| `docs/foundry_lite_tricky_failure_modes_checklist.md`     | `risk-registry`    | Failure-mode backlog and partial shield        | `[ ]`는 대부분 future hardening backlog이고, 완료 증거는 ledger와 gate로 확인한다.                                                                                             |
| `docs/aip-lite-canonical-spec.md`                         | `reference`        | AIP-lite design spec and Palantir cross-check  | Model Gateway/registry/egress/run-event ledger/context compiler 등 AIP-lite 섹션의 설계 원본과 documented-Palantir vs our-extension 대조를 담는다.                             |
| `docs/ai-fde-research.md`                                 | `reference`        | AI FDE official-behavior research              | Palantir AI FDE, Palantir MCP, Pilot 공개 문서에서 도출한 실행·권한·branch·proposal 계약과 Foundry-lite 이식 경계를 설명한다.                                                   |
| `docs/ai-fde-parity-matrix.json`                          | `machine-registry` | AI FDE public-behavior parity matrix            | 9개 governed mode, multi-tool loop, Builder MCP, Pilot을 코드·API·SDK·UI evidence와 사람 승인 경계에 연결한다.                                                               |
| `docs/palantir-action-mcp-prd-ko.md`                      | `reference`        | Agent-native operations target PRD              | Action Types, AI FDE, builder/consumer MCP, Pilot의 공식 공개 동작을 하나의 목표 제품 경계, 요구사항, acceptance, delivery 순서로 번역한다. current 증거와 장기 목표 정본은 implementation status, matrices, development plan이 소유한다. |
| `docs/palantir-search-flow-closure-prd-ko.md`             | `reference`        | Search flow closure target PRD                  | 인덱스 세대 활성화, 영상 의미 검색 표면, Media Set 스코프, 통합 검색 노출의 목표 계약을 Palantir 공개 동작에서 번역한다. current 증거는 implementation status와 sprint evidence ledger가 소유한다. |
| `docs/palantir-virtual-tables-prd-ko.md`                  | `reference`        | Virtual tables target PRD                       | 외부 테이블을 복사 없이 포인터로 등록하고 push-down으로 읽는 목표 계약을 Palantir 공개 동작에서 번역한다. 온톨로지 백킹과 update detection은 V2 범위다. current 증거는 implementation status와 evidence ledger가 소유한다. |
| `docs/Foundry-lite_AIP_Architecture_Report.pdf`           | `reference`        | AIP-lite authoritative architecture report     | `docs/aip-lite-canonical-spec.md`가 인코딩하는 정본 PDF. 충돌 시 PDF가 정본이다.                                                                                               |
| `docs/data-engineering-pattern-matrix.json`               | `machine-registry` | Data-pattern current/partial/deferred matrix   | S46 semantic consistency gate의 입력이다.                                                                                                                                      |
| `docs/data-platform-expansion-sprint-plan-ko.md`          | `source-of-truth`  | S46+ expansion roadmap and sprint checklist    | roadmap summary, detailed sprint checklist, and current/partial/future status live in one document.                                                                            |
| `docs/frontend-api-sdk-surface-matrix.json`               | `machine-registry` | API/SDK proof registry                         | frontend route나 SDK helper가 request/helper contract 없이 생기는 것을 막는다.                                                                                                 |
| `docs/pipeline-builder-parity-matrix.json`                | `machine-registry` | Pipeline Builder public-behavior parity matrix | 공식 Palantir 공개 동작과 현재 Graph v2, Media registry, execution evidence, UI foundation 및 planned gap을 `current/foundation/planned`로 잠근다.                             |
| `docs/functions-object-set-parity-matrix.json`            | `machine-registry` | Functions/ObjectSet public-behavior parity matrix | 공식 Palantir 공개 동작과 lazy ObjectSet, Python/TypeScript OSDK, Domain OS Function 생성 및 남은 gap을 `current/partial/planned`로 잠근다. |
| `docs/action-types-parity-matrix.json`                    | `machine-registry` | Action Types v3 public-behavior parity matrix  | 공식 Palantir Action Type의 rule, parameter/form/upload, function/batch, permission/effect, log/revert, branch/interface, monitoring/scale/MCP 동작과 현재 증거를 `missing/foundation/partial/current`로 잠근다. |
| `docs/frontend-backend-surface-contract.md`               | `source-of-truth`  | Frontend/backend contract                      | 프론트는 named generated SDK를 통해서만 current API를 사용한다.                                                                                                                |
| `docs/osdk-security-threat-model.md`                      | `risk-registry`    | OSDK browser/OAuth/subscription threat model   | local OAuth, WebSocket/SSE subscription, CORS/CSRF/XSS/token leakage, and future external IdP/package lifecycle boundary를 current/future로 분리한다.                          |
| `docs/sdk-frontend-cookbook.md`                           | `example`          | SDK frontend cookbook                          | SDK만으로 핵심 화면을 조립하는 예제 모음이다. current/future 경계 원본은 아니며 status/evidence 문서와 frontend contract를 따른다.                                             |
| `docs/source-streaming-palantir-gap-matrix.md`            | `reference`        | Palantir streaming contract cross-analysis     | 공식 공개 문서와 현재 Kafka Source 증거를 대조하며 current claim 원본은 implementation status와 evidence ledger로 연결한다.                                                    |
| `docs/quality-gate-roadmap.md`                            | `source-of-truth`  | Gate roadmap and operational diagnostics       | 각 gate가 막는 위험, release/runtime lane, operator evidence, diagnostics artifact 해석법을 함께 설명한다.                                                                     |
| `docs/commit-point-risk-register.md`                      | `risk-registry`    | Commit-point risks                             | commit, idempotency, partial failure, cleanup 위험을 추적한다.                                                                                                                 |
| `docs/adr/README.md`                                      | `source-of-truth`  | Architecture Decision Record index             | ADR 색인. 새 ADR이 추가/supersede될 때 갱신한다.                                                                                                                               |
| `docs/adr/0001-media-plane-parallel-to-dataset-plane.md`  | `source-of-truth`  | Media Plane architecture decision              | Media/Content Plane을 Dataset Plane과 동급 bounded context로 두는 결정과 7개 제품 불변식을 기록한다.                                                                           |
| `docs/adr/0002-public-behavior-mmdp-pipeline-graph-v2.md` | `source-of-truth`  | Pipeline Graph v2 architecture decision        | 공개 동작 패리티 범위, Graph v2, named artifact ports, plane별 commit, no-commit preview, exact processor resolution, 단계적 rollout 결정을 기록한다.                          |
| `docs/adr/0003-palantir-public-behavior-is-design-authority.md` | `source-of-truth` | Palantir public-behavior design authority | 주요 설계 결정이 공식 Palantir 문서, 공개 동작, 구현 증거, 명시적 gap을 함께 가져야 한다는 공통 규칙을 기록한다. |
| `docs/backend-findings-crosscheck-2026-06-22.md`          | `risk-registry`    | Backend review finding cross-check             | 외부 backend review finding을 현재 코드, tests, gates와 대조해 fixed/partial/still-valid 위험으로 분류한다.                                                                    |
| `foundry_lite_development_plan_ko_sprintified.md`         | `source-of-truth`  | Product and architecture plan                  | 장기 목표와 설계 방향의 가장 큰 원본이다.                                                                                                                                      |
| `foundry_lite_sprint_breakdown_ko.md`                     | `source-of-truth`  | Sprint breakdown                               | 스프린트별 scope and status table을 관리한다.                                                                                                                                  |
| `foundry_lite_python_engineering_guidelines_ko.md`        | `standard`         | Python/backend engineering standards           | 구현 방식, 테스트 기준, anti-pattern을 통제한다.                                                                                                                               |
| `deep-research-report.md`                                 | `reference`        | Research context                               | 제품/시장/아키텍처 배경 참고 자료다.                                                                                                                                           |
| `examples/supply-chain-demo/README.md`                    | `example`          | Demo guide                                     | 공급망 데모 실행과 예제 흐름을 설명한다.                                                                                                                                       |
| `examples/media-multimodal-demo/README.md`                | `example`          | Demo guide                                     | 미디어 멀티모달(OCR/ASR/영상/의미검색) 실엔진 데모 실행을 설명한다.                                                                                                            |

## Cross-Check Commands

문서 리팩토링 뒤에는 최소한 아래 명령을 확인한다.

```bash
pnpm --silent quality:doc-drift
pnpm --silent quality:evidence-ledger-commands
pnpm --silent quality:documentation-map
pnpm --silent quality:semantic-doc-consistency
pnpm --silent quality:data-pattern-matrix
pnpm --silent quality:data-platform-sprint-status
pnpm --silent quality:proof-matrix
pnpm --silent quality:source-of-truth
pnpm --silent quality:operator-evidence
pnpm --silent quality:frontend-backend-surface
node --disable-warning=ExperimentalWarning --experimental-strip-types tests/sdk/request_contract.mjs
pnpm --silent quality:sdk-generated
pnpm --silent quality:frontend-foundation
pnpm --silent quality:insight-review
pnpm --silent ci:gate:static
```

변경 범위가 새 product surface를 포함하면 해당 focused runtime gate와 smoke/contract tests도 같이
실행한다. 예를 들어 S63 Insight Review surface는 `pnpm --silent quality:insight-review`가
backend/API/SDK/audit evidence를 함께 확인한다.

## Current Refactor Findings

- G14/G14B는 문서 리팩토링의 운영 안전망이다. `check_doc_drift.py`는 repo 문서의
  파일/package script/API/test/link 참조와 current-state 문서의 Python symbol 참조가 코드보다 앞서지 못하게 막고,
  `check_evidence_ledger_commands.py`는 sprint evidence ledger의 proof command가 실제 package
  script, script file, test path, pytest node id를 가리키는지 확인하며,
  `check_documentation_map.py`는 문서 역할표와 doc-drift scan inventory의 일치,
  MECE taxonomy 정의표의 bucket 누락/중복/stale/placeholder와 Document Roles의 MECE bucket 누락/오분류,
  source-of-truth rows의 누락/중복/stale 상태, core 운영 Markdown 문서의 상단 context marker,
  source-of-truth/document-role 행의 실질적 설명,
  evidence/status/roadmap/contract 문서가 README보다 먼저 갱신되도록 하는 Update Order, README 문서 지도,
  README 문서 지도의 필수 링크/중복/placeholder 설명, README 대표 gate 표의 필수 reference/중복/placeholder 설명,
  cross-check command의 누락/중복/stale 상태와 target 존재,
  proof-matrix/source-of-truth/operator-evidence command, API/SDK/product surface proof command,
  AGENTS gate briefing이 빠지지
  않게 막는다.
- `check_data_platform_sprint_status.py`는 S46-S64 status table과 README/roadmap/status 문서의
  high-level boundary가 서로 달라지지 않게 막는다. 현재 잠긴 의미는 S46 complete, S47-S64
  partial, S59 proposed/future다.
- S61은 더 이상 단순 request wrapper만이 아니다. 현재는 named SDK namespaces, Ontology-style
  object/action/media OSDK facade, large ontology registry lookup, session token provider,
  shared React screen status, Provider-backed operator app shell, bounded operation polling,
  fetch-based operation event streaming,
  Generic REST connector onboarding API/SDK, admin readiness overview/task plan plus internal operations workbench,
  bounded outbox publish admin start, 81개 route
  surface, `requiresIdempotencyKey` mutation marker, 25개 matrix-locked helpers, browser
  request/helper contract, documentation count-claim guard, and Web named-SDK-only rule까지 포함하는
  partial frontend foundation이다.
- S62는 full visual Object/Dataset Explorer가 아니다. 하지만 dataset list, versions, preview,
  inspect, and operations lineage API/SDK start point는 current partial이다.
- S63은 full Insight/Action Workspace가 아니다. 하지만 durable Insight Review queue storage,
  idempotent create/assign/decision, terminal decision conflict, generated SDK, and audit evidence는
  current partial이다.
- Temporal은 adapter ratchet plus S52 `ConnectorSyncWorkflow` control-plane proof와 worker-bound local connector snapshot commit proof가 current다.
  Managed worker operations, cancellation/reconciliation, workflow upgrade replay, and production connector packaging remain future.
