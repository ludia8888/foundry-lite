import { expect, test } from "@playwright/test";

test("object explorer loads an order and applies ApproveOrder", async ({ page }) => {
  await page.goto("/");

  await expect(page.locator("#statusText")).toHaveText("ok");

  await page.locator("#loadBtn").click();
  await expect(page.locator("#metricObject")).toHaveText("O-1001");
  await expect(page.locator("#metricStatus")).toHaveText("PENDING");
  await expect(page.locator("#objectResult")).toContainText('"objectType": "Order"');

  await page.locator("#approveBtn").click();
  await expect(page.locator("#metricStatus")).toHaveText("APPROVED");
  await expect(page.locator("#metricVersion")).toHaveText("2");
});
