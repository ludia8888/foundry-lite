import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "tests/e2e",
  timeout: 30_000,
  expect: {
    timeout: 10_000,
  },
  reporter: [
    ["list"],
    ["html", { outputFolder: "artifacts/playwright-report", open: "never" }],
    ["json", { outputFile: "artifacts/playwright/playwright-results.json" }],
  ],
  use: {
    baseURL: "http://127.0.0.1:4173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: [
    {
      command: "bash scripts/e2e_start_api.sh",
      url: "http://127.0.0.1:8000/healthz",
      reuseExistingServer: !process.env.CI,
      // API startup seeds the full demo runtime and initializes local model
      // adapters. Keep this separate from the static web server budget so a
      // cold CI runner cannot fail before browser assertions begin.
      timeout: 120_000,
    },
    {
      command: "uv run python -m http.server 4173 --directory apps/web",
      url: "http://127.0.0.1:4173",
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
    },
  ],
});
