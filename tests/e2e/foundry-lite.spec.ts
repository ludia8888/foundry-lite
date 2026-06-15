import { expect, test } from "@playwright/test";

test("object explorer loads an order and applies ApproveOrder", async ({ page }) => {
  await page.goto("/");

  await expect(page.locator("#statusText")).toHaveText("ok");
  await expect(page.locator("#datasetResult")).toContainText('"dataset": "clean.orders"');
  await expect(page.locator("#datasetResult")).toContainText('"version_number": 1');
  await expect(page.locator("#datasetResult")).toContainText('"order_id": "O-1001"');
  await expect(page.locator("#queryResult")).toContainText('"objectId": "O-1001"');
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
