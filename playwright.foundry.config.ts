import { defineConfig, devices } from "@playwright/test";

const configuredWorkers = Number.parseInt(process.env.E2E_FOUNDRY_WORKERS ?? "1", 10);
const workers =
  Number.isFinite(configuredWorkers) && configuredWorkers > 0 ? configuredWorkers : 1;
const foundryBaseUrl = (
  process.env.FOUNDRY_LITE_E2E_WEB_BASE_URL ?? "http://127.0.0.1:4173"
).replace(/\/+$/, "");
const foundryApiBaseUrl = (
  process.env.FOUNDRY_LITE_E2E_API_BASE_URL ?? "http://127.0.0.1:8000"
).replace(/\/+$/, "");
const shouldReuseExistingServer =
  process.env.FOUNDRY_LITE_E2E_REUSE_EXISTING === "1";

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
    baseURL: foundryBaseUrl,
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
      url: `${foundryApiBaseUrl}/healthz`,
      reuseExistingServer: shouldReuseExistingServer,
      timeout: 45_000,
    },
    {
      command: "pnpm --filter @foundry-lite/foundry dev --host 127.0.0.1",
      url: foundryBaseUrl,
      reuseExistingServer: shouldReuseExistingServer,
      timeout: 45_000,
    },
  ],
});
