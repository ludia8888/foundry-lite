import { defineConfig, devices } from "@playwright/test";

const configuredWorkers = Number.parseInt(process.env.E2E_FOUNDRY_WORKERS ?? "1", 10);
const workers =
  Number.isFinite(configuredWorkers) && configuredWorkers > 0 ? configuredWorkers : 1;

export default defineConfig({
  testDir: "tests/e2e-foundry",
  timeout: 45_000,
  workers,
  expect: {
    timeout: 10_000,
  },
  reporter: [
    ["list"],
    [
      "html",
      { outputFolder: "artifacts/playwright-foundry-report", open: "never" },
    ],
    ["json", { outputFile: "artifacts/playwright/foundry-results.json" }],
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
      command:
        'FOUNDRY_LITE_HOME="$PWD/.foundry-lite-foundry-e2e" bash scripts/e2e_start_api.sh',
      url: "http://127.0.0.1:8000/healthz",
      reuseExistingServer: !process.env.CI,
      timeout: 45_000,
    },
    {
      command: "pnpm --filter @foundry-lite/foundry dev --host 127.0.0.1",
      url: "http://127.0.0.1:4173",
      reuseExistingServer: !process.env.CI,
      timeout: 45_000,
    },
  ],
});
