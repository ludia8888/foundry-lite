import { expect, type Page, test } from "@playwright/test";

const SUCCEEDED_RUN_ID = "action_run_ui_takeover";
const RUNNING_RUN_ID = "action_run_ui_cancel";

function effectReceipt(effectId: string, status: string) {
  return {
    receiptId: `${SUCCEEDED_RUN_ID}:${effectId}`,
    effectId,
    phase: "after_commit",
    kind: "notification",
    status,
    targetRef: "notification-policy:operations",
    idempotencyKey: `action-effect:${SUCCEEDED_RUN_ID}:${effectId}`,
    attemptCount: status === "pending" ? 0 : 1,
    maxAttempts: 3,
    workerId: status === "pending" ? null : "effect-worker-a",
    fencingToken: status === "pending" ? 0 : 1,
    heartbeatAt: status === "pending" ? null : "2026-08-04T00:00:03Z",
    dispatchStartedAt: status === "outcome_unknown" ? "2026-08-04T00:00:03Z" : null,
    retryAt: null,
    cancelRequestedAt: null,
    cancelReason: null,
    cancellationDisposition: null,
    externalExecutionId: status === "succeeded" ? "notification:1" : null,
    outboxEventId: `outbox-${effectId}`,
    response: status === "succeeded" ? {} : null,
    error: status === "dead_letter" || status === "outcome_unknown" ? { kind: status } : null,
    reconciledAt: null,
    reconciledByUserId: null,
    reconciliation: null,
    notificationRendering: {
      phase: "pre_commit",
      sourceObjectType: "Order",
      sourceObjectId: "O-1001",
      sourceObjectVersion: 4,
      templateFingerprint: "sha256:notification-template",
      renderedPayloadFingerprint: "sha256:notification-rendered",
    },
    createdAt: "2026-08-04T00:00:03Z",
    updatedAt: "2026-08-04T00:00:04Z",
    completedAt: ["succeeded", "dead_letter", "outcome_unknown"].includes(status)
      ? "2026-08-04T00:00:04Z"
      : null,
  };
}

function actionRun(runId: string, status: string) {
  const isSucceeded = status === "succeeded";
  return {
    actionRunId: runId,
    actionApiName: isSucceeded ? "ApproveOrder" : "CancelInventoryHold",
    status,
    target: { objectType: "Order", objectId: isSucceeded ? "O-1001" : "O-1002" },
    expectedObjectVersion: 4,
    parameters: { reason: "operator request" },
    planHash: "sha256:action-runtime-plan",
    definitionVersion: "sha256:action-runtime-definition",
    orchestration: { workflowRunId: `workflow:${runId}`, dispatchStatus: "dispatched" },
    steps: [
      {
        stepKey: "commit",
        kind: "commit",
        status,
        attemptCount: isSucceeded ? 2 : 1,
        output: {},
        error: null,
        startedAt: "2026-08-04T00:00:00Z",
        completedAt: isSucceeded ? "2026-08-04T00:00:03Z" : null,
      },
    ],
    attempts: isSucceeded
      ? [
          {
            stepId: `${runId}:commit`,
            attemptNumber: 1,
            status: "lost",
            workerId: "worker-a",
            fencingToken: 1,
            heartbeatAt: "2026-08-04T00:00:01Z",
            retryAt: "2026-08-04T00:00:02Z",
            errorKind: "worker_lost",
            externalExecutionId: null,
            output: {},
            error: null,
            startedAt: "2026-08-04T00:00:00Z",
            completedAt: "2026-08-04T00:00:01Z",
          },
          {
            stepId: `${runId}:commit`,
            attemptNumber: 2,
            status: "succeeded",
            workerId: "worker-b",
            fencingToken: 2,
            heartbeatAt: "2026-08-04T00:00:03Z",
            retryAt: null,
            errorKind: null,
            externalExecutionId: null,
            output: {},
            error: null,
            startedAt: "2026-08-04T00:00:02Z",
            completedAt: "2026-08-04T00:00:03Z",
          },
        ]
      : [
          {
            stepId: `${runId}:commit`,
            attemptNumber: 1,
            status: "running",
            workerId: "worker-c",
            fencingToken: 1,
            heartbeatAt: "2026-08-04T00:00:03Z",
            retryAt: null,
            errorKind: null,
            externalExecutionId: null,
            output: {},
            error: null,
            startedAt: "2026-08-04T00:00:02Z",
            completedAt: null,
          },
        ],
    effects: isSucceeded
      ? [
          effectReceipt("notify-operator", "succeeded"),
          effectReceipt("cancel-pending", "pending"),
          effectReceipt("retry-dead-letter", "dead_letter"),
          effectReceipt("reconcile-unknown", "outcome_unknown"),
        ]
      : [],
    cancel: { requestedAt: status === "cancelled" ? "2026-08-04T00:00:04Z" : null, reason: null },
    result: isSucceeded ? { editCount: 1 } : null,
    error: null,
    eventSequence: isSucceeded ? 9 : 4,
    createdAt: "2026-08-04T00:00:00Z",
    startedAt: "2026-08-04T00:00:00Z",
    completedAt: isSucceeded || status === "cancelled" ? "2026-08-04T00:00:04Z" : null,
  };
}

async function mockActionRuntime(page: Page) {
  let isCancelled = false;
  await page.route("**/api/actions/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path === "/api/actions/runs") {
      await route.fulfill({ json: { items: [actionRun(SUCCEEDED_RUN_ID, "succeeded"), actionRun(RUNNING_RUN_ID, isCancelled ? "cancelled" : "running")], nextCursor: null } });
      return;
    }
    if (path === "/api/actions/logs") {
      await route.fulfill({
        json: {
          items: [
            {
              logEntryId: "log-ui-1",
              logObject: { objectType: "[LOG] ApproveOrder", objectId: SUCCEEDED_RUN_ID },
              actionRunId: SUCCEEDED_RUN_ID,
              actionApiName: "ApproveOrder",
              definitionVersion: "sha256:action-runtime-definition",
              actorUserId: "operator-a",
              status: "succeeded",
              parameters: {},
              result: {},
              branchId: null,
              planHash: "sha256:action-runtime-plan",
              approvalId: null,
              revert: { isAllowed: true, status: "eligible", revertedByRunId: null },
              effectReceiptCount: 1,
              editedObjects: [{ objectEditId: "edit-1", objectType: "Order", objectId: "O-1001", operation: "modify_object", ordinal: 0 }],
              createdAt: "2026-08-04T00:00:00Z",
              completedAt: "2026-08-04T00:00:03Z",
            },
          ],
          nextCursor: null,
          monitoring: {
            window: { days: 30, startsAt: "2026-07-05T00:00:00Z", endsAt: "2026-08-04T00:00:00Z", maxRuns: 10000, observedRuns: 2, isTruncated: false },
            durationMs: { p95: 3000, terminalSample: 2 },
            failure: { count: 0, rate: 0, terminalSample: 2, byStatus: {}, byErrorKind: {} },
            effects: { deliveryBacklog: 0, deadLetter: 0, outcomeUnknown: 0 },
            alerts: { policies: [], active: [] },
          },
        },
      });
      return;
    }
    if (path === `/api/actions/runs/${SUCCEEDED_RUN_ID}/revert-eligibility`) {
      await route.fulfill({ json: { actionRunId: SUCCEEDED_RUN_ID, isEligible: true, reason: null, editCount: 1, hasPreservedExternalEffects: true, logEntryId: "log-ui-1" } });
      return;
    }
    if (path === `/api/actions/runs/${SUCCEEDED_RUN_ID}/revert` && request.method() === "POST") {
      expect(request.headers()["idempotency-key"]).toBe(`action-runtime-revert:${SUCCEEDED_RUN_ID}`);
      await route.fulfill({ json: { actionRunId: "action_run_ui_revert", revertOfActionRunId: SUCCEEDED_RUN_ID, status: "succeeded", editCount: 1, hasPreservedExternalEffects: true } });
      return;
    }
    if (path === `/api/actions/runs/${RUNNING_RUN_ID}/cancel` && request.method() === "POST") {
      expect(request.headers()["idempotency-key"]).toBe(`action-runtime-cancel:${RUNNING_RUN_ID}`);
      isCancelled = true;
      await route.fulfill({ json: actionRun(RUNNING_RUN_ID, "cancelling") });
      return;
    }
    if (path === `/api/actions/effects/${SUCCEEDED_RUN_ID}:cancel-pending/cancel` && request.method() === "POST") {
      expect(request.headers()["idempotency-key"]).toBe(
        `action-effect-cancel:${SUCCEEDED_RUN_ID}:cancel-pending:0`,
      );
      await route.fulfill({ json: effectReceipt("cancel-pending", "cancelled") });
      return;
    }
    if (path === `/api/actions/effects/${SUCCEEDED_RUN_ID}:retry-dead-letter/retry` && request.method() === "POST") {
      expect(request.headers()["idempotency-key"]).toBe(
        `action-effect-retry:${SUCCEEDED_RUN_ID}:retry-dead-letter:1`,
      );
      await route.fulfill({ json: effectReceipt("retry-dead-letter", "retry_wait") });
      return;
    }
    if (path === `/api/actions/effects/${SUCCEEDED_RUN_ID}:reconcile-unknown/reconcile` && request.method() === "POST") {
      expect(request.headers()["idempotency-key"]).toMatch(/^action-effect-reconcile:/);
      expect(await request.postDataJSON()).toMatchObject({
        resolution: "confirmed_delivered",
        evidence: {
          verificationMethod: "provider_query",
          providerReference: "provider-case-42",
          externalExecutionId: "delivery-42",
        },
      });
      await route.fulfill({ json: effectReceipt("reconcile-unknown", "succeeded") });
      return;
    }
    if (path.endsWith("/events")) {
      await route.fulfill({ status: 200, contentType: "text/event-stream", body: "" });
      return;
    }
    if (path === `/api/actions/runs/${SUCCEEDED_RUN_ID}`) {
      await route.fulfill({ json: actionRun(SUCCEEDED_RUN_ID, "succeeded") });
      return;
    }
    if (path === `/api/actions/runs/${RUNNING_RUN_ID}`) {
      await route.fulfill({ json: actionRun(RUNNING_RUN_ID, isCancelled ? "cancelled" : "running") });
      return;
    }
    await route.continue();
  });
}

test("Action runtime shows takeover, receipts, logs, revert and cancellation evidence", async ({ page }) => {
  await mockActionRuntime(page);
  await page.goto("/actions");
  await page.getByRole("tab", { name: "실행·로그" }).click();

  await expect(page.getByText("Durable 실행 이력")).toBeVisible();
  await expect(page.getByText("worker takeover")).toBeVisible();
  await expect(page.getByText("#2 · worker-b · fence 2")).toBeVisible();
  await expect(page.getByText("notify-operator")).toBeVisible();
  await expect(page.getByText("알림 문구 · 편집 전 v4 고정").first()).toBeVisible();
  await page.getByRole("button", { name: "효과 취소" }).click();
  await page.getByRole("button", { name: "동일 키로 1회 재시도" }).click();
  await page.getByLabel("Provider 근거 번호").fill("provider-case-42");
  await page.getByLabel("외부 실행 ID").fill("delivery-42");
  await page.getByRole("button", { name: "전달 확인", exact: true }).click();
  await expect(page.getByText("3000 ms")).toBeVisible();
  await expect(page.getByText("modify_object · Order/O-1001")).toBeVisible();

  await page.getByRole("button", { name: "되돌리기" }).click();
  const eventRequest = page.waitForRequest((request) =>
    request.url().endsWith(`/api/actions/runs/${RUNNING_RUN_ID}/events`),
  );
  await page.getByRole("button", { name: new RegExp(RUNNING_RUN_ID) }).click();
  expect((await eventRequest).headers()["last-event-id"]).toBe("4");
  await expect(page.getByRole("button", { name: "취소" })).toBeVisible();
  await page.getByRole("button", { name: "취소" }).click();
  await expect(page.getByText("cancelled", { exact: true }).first()).toBeVisible();
});
