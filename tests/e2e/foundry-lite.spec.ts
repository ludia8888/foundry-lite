import { expect, test } from "@playwright/test";

test("object explorer loads an order and applies ApproveOrder", async ({ page }) => {
  await page.goto("/");

  await expect(page.locator("#statusText")).toHaveText("ok");
  await expect(page.locator("#datasetResult")).toContainText('"dataset": "clean.orders"');
  await expect(page.locator("#datasetResult")).toContainText('"version_number": 1');
  await expect(page.locator("#datasetResult")).toContainText('"order_id": "O-1001"');
  await expect(page.locator("#queryResult")).toContainText('"objectId": "O-1001"');
  await page.locator("#ontologyBtn").click();
  await expect(page.locator("#ontologyResult")).toContainText('"code": "VALIDATION_FAILED"');
  await expect(page.locator("#ontologyResult")).toContainText('"message": "primary key column missing"');
  await expect(page.locator("#ontologyResult")).toContainText('"request_id"');
  await expect(page.locator("#metricObject")).toHaveText("O-1001");
  await expect(page.locator("#metricStatus")).toHaveText("PENDING");
  await expect(page.locator("#objectResult")).toContainText('"objectType": "Order"');
  await expect(page.locator("#objectResult")).toContainText('"sourceRunChain"');
  await expect(page.locator("#linkResult")).toContainText('"linkType": "OrderCustomer"');
  await expect(page.locator("#linkResult")).toContainText('"objectType": "Customer"');
  await expect(page.locator("#linkResult")).toContainText('"objectId": "C-100"');

  await page.locator("#sourceRunBtn").click();
  await expect(page.locator("#runResult")).toContainText('"sourceRun"');
  await expect(page.locator("#runResult")).toContainText('"runType": "index"');
  await expect(page.locator("#runResult")).toContainText('"runDetail"');
  await page.locator("#operationType").selectOption("");
  await page.locator("#operationRunId").fill("");

  await page.locator("#saveSetBtn").click();
  await expect(page.locator("#setResult")).toContainText('"name": "Pending Orders"');

  const generatedSdkAction = page.waitForRequest((request) => {
    return request.method() === "POST" && request.url().includes("/api/actions/ApproveOrder/apply");
  });
  await page.locator("#approveBtn").click();
  await expect((await generatedSdkAction).headers()["idempotency-key"]).toMatch(/^ApproveOrder-O-1001-/);
  await expect(page.locator("#metricStatus")).toHaveText("APPROVED");
  await expect(page.locator("#metricVersion")).toHaveText("2");
  await expect(page.locator("#setResult")).toContainText('"objectIds": []');
  await expect(page.locator("#runResult")).toContainText('"actionRuns"');
  await expect(page.locator("#runResult")).toContainText('"deadLetterEvents"');
  await expect(page.locator("#runResult")).toContainText('"target_object_id": "O-1001"');
  await expect(page.locator("#metricFailedRuns")).toHaveText("0");
  await expect(page.locator("#metricOutbox")).toHaveText(/^[1-9]\d*$/);
  await expect(page.locator("#metricDlq")).toHaveText("0");

  await page.locator("#materializeActionLogBtn").click();
  await expect(page.locator("#datasetResult")).toContainText('"dataset": "ops.action_log"');
  await expect(page.locator("#datasetResult")).toContainText('"action_run_id"');
  await expect(page.locator("#datasetResult")).toContainText('"actor_user_id": "web-demo-operator"');
  await expect(page.locator("#datasetResult")).toContainText('"parameters_json": "{\\"reason\\": \\"Inventory confirmed\\"}"');
  await expect(page.locator("#runResult")).toContainText('"lastMaterialization"');
  await expect(page.locator("#runResult")).toContainText('"dataset_ref": "ops.action_log"');

  await page.reload();
  await expect(page.locator("#statusText")).toHaveText("ok");
  await expect(page.locator("#datasetResult")).toContainText('"dataset": "clean.orders"');
  await expect(page.locator("#queryResult")).toContainText('"items": []');
  await expect(page.locator("#metricObject")).toHaveText("O-1001");
  await expect(page.locator("#metricStatus")).toHaveText("APPROVED");
  await expect(page.locator("#linkResult")).toContainText('"objectId": "C-100"');

  await page.locator("#operationType").selectOption("action");
  await page.locator("#operationStatus").fill("succeeded");
  await page.locator("#operationSince").fill("2000-01-01T00:00:00Z");
  await page.locator("#operationUntil").fill("2999-01-01T00:00:00Z");
  await page.locator("#runsBtn").click();
  await expect(page.locator("#runResult")).toContainText('"actionRuns"');
  await expect(page.locator("#runResult")).not.toContainText('"transformRuns": [\n    {');

  await page.locator("#runDetailBtn").click();
  await expect(page.locator("#runResult")).toContainText('"runDetail"');
  await expect(page.locator("#runResult")).toContainText('"runType": "action"');
  await expect(page.locator("#runResult")).toContainText('"relatedOutboxEvents"');
  await expect(page.locator("#runResult")).toContainText('"target_object_id": "O-1001"');

  await page.locator("#replayIndexBtn").click();
  await expect(page.locator("#runResult")).toContainText('"lastReplay"');
  await expect(page.locator("#runResult")).toContainText('"object_type": "Order"');
  await expect(page.locator("#runResult")).toContainText('"indexRuns"');

  await page.locator("#operationRunId").fill("");
  await page.locator("#retryTransformBtn").click();
  await expect(page.locator("#runResult")).toContainText('"lastTransformRetry"');
  await expect(page.locator("#runResult")).toContainText('"status": "skipped"');

  await page.locator("#operationRunId").fill("");
  await page.locator("#replayFailedIndexBtn").click();
  await expect(page.locator("#runResult")).toContainText('"lastFailedReplay"');
  await expect(page.locator("#runResult")).toContainText('"status": "skipped"');

  await page.locator("#retryDlqBtn").click();
  await expect(page.locator("#runResult")).toContainText('"lastRetry"');
  await expect(page.locator("#runResult")).toContainText('"status": "skipped"');
});

test("record dlq operations list detail replay and discard", async ({ page }) => {
  await page.goto("/");

  await expect(page.locator("#statusText")).toHaveText("ok");
  await expect(page.locator("#recordDlqResult")).toContainText('"id": "dlqr_web_retry"');
  await expect(page.locator("#recordDlqResult")).toContainText('"id": "dlqr_web_discard"');
  await expect(page.locator("#metricRecordDlqOpen")).toHaveText("2");
  await expect(page.locator("#metricRecordDlqClosed")).toHaveText("0");
  await expect(page.locator("#metricRecordDlqTopError")).toHaveText("VALIDATION_FAILED");

  await page.locator("#recordDlqStatus").selectOption("QUARANTINED");
  await page.locator("#loadRecordDlqBtn").click();
  await expect(page.locator("#recordDlqResult")).toContainText('"filters"');
  await expect(page.locator("#recordDlqResult")).toContainText('"status": "QUARANTINED"');

  await page.locator("#recordDlqId").fill("dlqr_web_retry");
  await page.locator("#recordDlqDetailBtn").click();
  await expect(page.locator("#recordDlqResult")).toContainText('"impactPreview"');
  await expect(page.locator("#recordDlqResult")).toContainText('"sourceEventId": "shipment_cdc_events:0:31"');
  await expect(page.locator("#recordDlqResult")).toContainText('"payload"');
  await expect(page.locator("#recordDlqResult")).toContainText('"error_message": "cdc envelope field must be an object"');

  await page.locator("#recordDlqStatus").selectOption("");
  await page.locator("#replayRecordDlqBtn").click();
  await expect(page.locator("#recordDlqResult")).toContainText('"lastRecordReplay"');
  await expect(page.locator("#recordDlqResult")).toContainText('"replayStatus": "SUCCEEDED"');
  await expect(page.locator("#recordDlqResult")).toContainText('"replayDatasetVersionId"');
  await expect(page.locator("#metricRecordDlqOpen")).toHaveText("1");
  await expect(page.locator("#metricRecordDlqClosed")).toHaveText("1");

  await page.locator("#recordDlqId").fill("dlqr_web_discard");
  await page.locator("#discardRecordDlqBtn").click();
  await expect(page.locator("#recordDlqResult")).toContainText('"lastRecordDiscard"');
  await expect(page.locator("#recordDlqResult")).toContainText('"status": "DISCARDED"');
  await expect(page.locator("#metricRecordDlqOpen")).toHaveText("0");
  await expect(page.locator("#metricRecordDlqClosed")).toHaveText("2");

  await page.locator("#bulkReplayRecordDlqBtn").click();
  await expect(page.locator("#recordDlqResult")).toContainText('"lastBulkRecordReplay"');
  await expect(page.locator("#recordDlqResult")).toContainText('"reason": "no quarantined records"');
});
