import { expect, test } from "@playwright/test";

const ROUTES: readonly { path: string; title: string | RegExp }[] = [
  { path: "/", title: "Foundry에 오신 것을 환영합니다" },
  { path: "/projects", title: "Files" },
  { path: "/data/connections", title: "Data Connection" },
  { path: "/datasets", title: "Dataset Preview" },
  { path: "/pipelines", title: "Pipeline Builder" },
  { path: "/lineage", title: "Data Lineage" },
  { path: "/code", title: "Code Repositories" },
  { path: "/ontology", title: "Ontology Manager" },
  { path: "/objects", title: "Object Explorer" },
  { path: "/actions", title: "Action Types / Functions" },
  { path: "/workshop", title: "Workshop" },
  { path: "/aip", title: "AIP" },
  { path: "/approvals", title: "Approvals" },
  { path: "/developer", title: "Developer Console" },
  { path: "/marketplace", title: "Marketplace" },
  { path: "/analytics", title: "Analytics" },
  { path: "/operations", title: "Platform Operations" },
  { path: "/security", title: "Security & Governance" },
];

test("all Foundry product routes render without browser runtime errors", async ({
  page,
}) => {
  const browserErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  page.on("pageerror", (error) => browserErrors.push(error.message));

  for (const route of ROUTES) {
    await page.goto(route.path);
    await expect(page.locator("body")).toContainText(route.title);
    await expect(page.locator("#vite-error-overlay")).toHaveCount(0);
    await expect(page.locator("body")).not.toContainText("plugin:vite");
    await expect(page.locator("main")).toBeVisible();
  }

  expect(browserErrors, "browser console/page errors").toEqual([]);
});
