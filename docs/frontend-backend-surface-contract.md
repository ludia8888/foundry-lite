# Frontend Backend Surface Contract

**Status:** S61 partial + S62/S63 backend/API/SDK slices / current backend + catalog + SDK safety-helper surface lock

이 문서는 프론트엔드를 올리기 전에 백엔드가 어떤 방식으로 프론트에 기능을 제공해야
하는지 정의한다. 비개발자식으로 말하면, 프론트와 백엔드 사이의 공식 메뉴판이다. 프론트는
이 메뉴판에 없는 내부 주소나 DB/vendor SDK를 직접 만지지 않는다.

## Source Of Truth

현재 source of truth는 다섯 가지다.

| Evidence | Role |
|---|---|
| `apps/api/foundry_lite_api/main.py` | 실제 FastAPI route 목록 |
| `packages/sdk-ts/src/generated.ts` | package용 generated TypeScript SDK |
| `apps/web/generated-sdk.js` | browser용 generated SDK |
| `docs/frontend-api-sdk-surface-matrix.json` | route -> SDK -> proof test -> operator evidence 매핑 |
| `tests/sdk/request_contract.mjs` | browser SDK가 실제로 보내는 method/path/header/body 계약 |

`scripts/quality/check_frontend_backend_surface.py`는 위 source of truth와 `apps/web/index.html`을
함께 검사한다. 실패하면 `artifacts/quality/frontend_backend_surface.json`과
`artifacts/quality/frontend_backend_surface.md`에 어떤 route, SDK method, proof test,
SDK helper, 파일을 봐야 하는지 남긴다.

## Locked Rule

- frontend-consumable API는 반드시 named generated SDK method가 있어야 한다.
- retry/backoff, cursor pagination, duplicate-action lock, stale-version conflict 분류,
  permission-denied 분류처럼 프론트에서 반복 구현하면 위험한 공통 로직은 SDK helper로 제공하고,
  `docs/frontend-api-sdk-surface-matrix.json`의 `sdkHelpers` row로 증명한다.
- Mutation SDK calls whose matrix row declares `requiresIdempotencyKey: true` require a caller-supplied
  `idempotencyKey`; the generated browser SDK raises `MISSING_IDEMPOTENCY_KEY` before the API call if a
  JavaScript caller forgets it. Retry UX must create one key for the user intent and reuse that same key across
  retry attempts.
- Web app은 `sdkClient().request("/api/...")`처럼 raw API path를 직접 조립하지 않는다.
- route가 프론트용이 아니라면 `nonFrontendRoutes`에 reason과 access class를 명시한다.
- 프론트용 surface는 `proofClass: "sdk-request-contract"`, proof test, operator evidence 설명을
  가져야 한다.
- 문서가 frontend route surface 수나 SDK helper 수를 손으로 적으면, 그 숫자는 실제
  `docs/frontend-api-sdk-surface-matrix.json`와 `SDK_CLIENT_SURFACE.helpers` count와 일치해야 한다.
- `tests/sdk/request_contract.mjs`는 browser SDK를 fake fetch로 실제 실행해 method, path,
  query string, context/request-id header, idempotency header, JSON body, typed error
  metadata, and frontend foundation helper behavior를 검증한다.
- 실패는 화면의 에러 문구에만 머물면 안 된다. request id, run detail, audit, transaction,
  outbox/error payload 중 적절한 곳에서 원인을 추적할 수 있어야 한다.

## Current Named SDK Surface

현재 S61 surface lock은 다음 현재 backend route를 named SDK로 고정한다.

| Area | SDK Surface |
|---|---|
| System | `client.system.health()` |
| Datasets | `client.datasets.list()`, `client.datasets.versions(...)`, `client.datasets.preview(...)`, `client.datasets.inspect(...)` |
| Ontology | `client.ontology.catalog()`, `client.ontology.validate(...)` |
| Objects | `client.objects.generic.get/query/links(...)`, generated `Order`/`Customer` clients |
| Object Sets | `client.objectSets.list/create/get(...)` |
| Actions | generated `client.actions.ApproveOrder.apply(...)` |
| Materializations | `client.materializations.run(...)` |
| Operations | run list/detail, AI prompt artifact access, lineage get, transform retry, index replay, outbox DLQ retry, Record DLQ controls |
| Platform Ops | observability detect, backup/restore, reconciliation, workflows, Iceberg maintenance `planReadOnly`/`plan` |
| Insights | `client.insights.reviews.list/create/get/assign/decide(...)` |
| AIP | `client.aip.builder.validate(...)`, `client.aip.builder.run(...)`, `client.aip.agent.run(...)` |
| Safety Helpers | `createFoundryLiteClient(...)`, `createRequestId(...)`, `requestContextHeaders(...)`, `normalizeFoundryLiteError(...)`, `isRetryableFoundryLiteError(...)`, `retryWithBackoff(...)`, `collectCursorPages(...)`, `createInFlightActionLock()`, `actionLockKey(...)`, `idempotencyKey(...)`, `expectedObjectVersion(...)`, `classifyFoundryLiteError(...)` |

The public `client.request(...)` escape hatch still exists inside the SDK package for advanced callers and future
generated methods, but the current Web app may not use it for `/api/...` product controls. Safety helpers are also
matrix-locked: a helper exposed in `SDK_CLIENT_SURFACE.helpers` must have an `sdkHelpers` row, TypeScript export,
operator-evidence note, and helper request-contract proof test.

## Still Future

이 contract는 현재 백엔드에 존재하는 route와 current Web Operations controls를 잠그는 단계다.
아래는 아직 full product workspace를 위해 남아 있다.

| Future Surface | Why It Is Not Claimed Current |
|---|---|
| Full login/session UI | 현재는 local/demo context header proof가 중심이다. |
| Automatic retry/backoff UX | SDK `retryWithBackoff(...)` is current; screen-specific retry policy, copy, and UX timing remain product work. |
| Cursor pagination UX | SDK `collectCursorPages(...)` is current; visual pagination, infinite scroll, and per-screen loading states remain product work. |
| Duplicate-click action UX | SDK `createInFlightActionLock()` and `actionLockKey(...)` are current; button disabled state and screen copy remain product work. |
| Stale-version conflict UI | SDK `classifyFoundryLiteError(...)` can identify `stale_object_version`; the human-facing compare/refresh flow remains product work. |
| Permission-denied masking UX | SDK `classifyFoundryLiteError(...)` can identify `permission_denied`; dedicated masked-field/role guidance UX remains product work. |
| Full catalog-driven workspace UX | `ontology.catalog()` and dataset list/inspect give the frontend active metadata entrypoints, but S62-S64 screens still need richer drill-down flows. |
| Insight review workspace UI | `insight_reviews` persistence, `/api/insights/reviews`, generated `client.insights.reviews.*`, idempotent create/assign/decision/approved execution, terminal decision conflict, API/SDK execute-action, and audit/AI-ledger linkage evidence are current. Evidence viewer UI, approval policy UI, rich execution controls, and full review workspace screens remain product work. |

## Completion Meaning

이 문서의 현재 의미는 "프론트 전체가 완성됐다"가 아니다. 현재 의미는 더 좁고 강하다:

```text
현재 존재하는 frontend-consumable backend API
-> generated SDK named method
-> browser SDK request-contract method/path/header/body proof for 47 frontend route surfaces
-> browser SDK helper-contract proof for 12 frontend foundation helpers
-> documentation count claims checked against the matrix and generated SDK helper list
-> Web named-SDK-only usage
-> proof test
-> CI gate
-> operator evidence 설명
```

이 사슬이 끊기면 PR은 실패해야 한다. 그래서 다음 프론트 작업은 raw API path를 새로 invent하지
않고, matrix와 generated SDK를 먼저 확장하는 방식으로 진행한다.
