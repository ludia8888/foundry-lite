import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

import { expect, test } from "@playwright/test";

const NAVIGATION_REF =
  "flite-citation-nav.v1.verified-pdf-evidence.signature";
const REPORT_PATH = resolve(
  process.cwd(),
  "docs/Foundry-lite_AIP_Architecture_Report.pdf",
);
const E2E_SEED_PATH = "/tmp/foundry-lite-e2e-seed.json";
const API_BASE_URL =
  process.env.FOUNDRY_LITE_E2E_API_BASE_URL ?? "http://127.0.0.1:8000";
const DEMO_HEADERS = {
  "X-Tenant-ID": "tenant-demo",
  "X-User-ID": "web-demo-operator",
  "X-Roles": "admin,data_engineer,ops_manager,finance",
};

const CITATION_EVIDENCE = {
  mediaItemVersionId: "miv-citation-pdf-1",
  mediaDerivativeId: "mder-citation-layout-1",
  contentUnitId: "cu-citation-page-2-heading",
  pageNumber: 2,
  bbox: {
    left: 60,
    top: 160,
    width: 180,
    height: 80,
    pageWidth: 600,
    pageHeight: 800,
  },
  timecode: null,
  sourceLocator: {
    pageNumber: 2,
    bbox: {
      left: 60,
      top: 160,
      width: 180,
      height: 80,
      pageWidth: 600,
      pageHeight: 800,
    },
    coordinateSystem: "pdf_top_left_points",
  },
  derivativeKind: "pdf_layout",
  processorName: "pdf_layout_v1",
  processorVersion: "1",
  processorSpecHash: "sha256:processor-spec",
  modelName: null,
  modelVersion: null,
  paramsHash: "sha256:params",
  securityEnvelope: {
    tenantId: "tenant-demo",
    classification: "internal",
  },
};

const RESOLUTION = {
  navigationPath: "/document-intelligence",
  sourceResourceType: "media",
  sourceResourceId: "media://architecture-report",
  sourceVersion: "miv-citation-pdf-1",
  contentHash: "sha256:verified-pdf-content",
  displayLabel: "Architecture report · page 2 heading",
  evidence: CITATION_EVIDENCE,
};

test("AIP citation opens only by signed token and renders server-verified PDF coordinates", async ({
  page,
}) => {
  const pdfBytes = await readFile(REPORT_PATH);
  let allowResolution: (() => void) | null = null;
  const resolutionGate = new Promise<void>((resolveGate) => {
    allowResolution = resolveGate;
  });
  const resolveBodies: unknown[] = [];

  await page.route("**/api/aip/agent/run", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        agentRunId: "agent-citation-browser-run",
        aiRunId: "ai-citation-browser-run",
        sessionId: "session-citation-browser-run",
        runStatus: "succeeded",
        answer: "The architecture report defines a governed evidence plane.",
        contextIds: ["ctx-citation-pdf"],
        citations: [
          {
            citationOrder: 1,
            claimSpan: { start: 0, end: 31 },
            contextId: "ctx-citation-pdf",
            sourceResourceType: "media",
            sourceResourceId: "media://architecture-report",
            displayLabel: "Architecture report · page 2 heading",
            contentHash: "sha256:verified-pdf-content",
            renderedRef: "[1] Architecture report · page 2 heading",
            navigationRef: NAVIGATION_REF,
            sourcePreview: {
              contextItemId: "context-item-citation-pdf",
              kind: "media_content",
              sourceVersion: "miv-citation-pdf-1",
              retrievalMethod: "hybrid",
              tokenEstimate: 48,
              securityPartition: "tenant-demo:internal",
              selected: true,
              contentHash: "sha256:verified-pdf-content",
            },
            evidence: CITATION_EVIDENCE,
          },
        ],
        operations: null,
      }),
    });
  });
  await page.route(
    "**/api/aip/citations/navigation/resolve",
    async (route) => {
      resolveBodies.push(route.request().postDataJSON());
      await resolutionGate;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(RESOLUTION),
      });
    },
  );
  await page.route(
    "**/api/media/versions/miv-citation-pdf-1/content",
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/pdf",
        headers: {
          "Content-Length": String(pdfBytes.byteLength),
          ETag: '"sha256:verified-pdf-content"',
        },
        body: pdfBytes,
      });
    },
  );

  await page.goto("/aip");
  await page.getByRole("button", { name: "실행", exact: true }).click();
  const evidenceLink = page.getByRole("link", { name: "원본 근거 열기" });
  await expect(evidenceLink).toBeVisible();
  await evidenceLink.click();

  await expect(page).toHaveURL(/\/document-intelligence\?citation=/);
  const openedUrl = new URL(page.url());
  expect(openedUrl.searchParams.get("citation")).toBe(NAVIGATION_REF);
  expect(openedUrl.searchParams.has("pageNumber")).toBe(false);
  expect(openedUrl.searchParams.has("bbox")).toBe(false);
  await expect(
    page.getByText("서버가 signed citation과 immutable Content Unit을 다시 검증하고 있습니다."),
  ).toBeVisible();
  await expect(page.getByTestId("citation-evidence-status")).toHaveText(
    "verifying",
  );
  await expect(page.getByTestId("citation-header-evidence-status")).toHaveText(
    "verifying evidence",
  );
  await expect(page.getByTitle(/검증된 원본 PDF/)).toHaveCount(0);
  await expect(page.getByTestId("verified-citation-overlay")).toHaveCount(0);

  allowResolution?.();
  await expect(page.getByText("VERIFIED", { exact: true }).first()).toBeVisible();
  await expect(page.getByTestId("citation-evidence-status")).toHaveText(
    "committed",
  );
  await expect(page.getByTestId("citation-header-evidence-status")).toHaveText(
    "verified committed evidence",
  );
  await expect(page.getByTitle("검증된 원본 PDF 2 페이지")).toBeVisible();
  const overlay = page.getByTestId("verified-citation-overlay");
  await expect(overlay).toHaveAttribute("data-page-number", "2");
  await expect(overlay).toHaveAttribute(
    "style",
    /left: 10%; top: 20%; width: 30%; height: 10%/,
  );
  await expect(page.getByTestId("verified-citation-passport")).toContainText(
    "cu-citation-page-2-heading",
  );
  expect(resolveBodies[0]).toEqual({ navigationRef: NAVIGATION_REF });

  await page.goto(
    `/document-intelligence?citation=${encodeURIComponent(NAVIGATION_REF)}` +
      "&pageNumber=999&bbox=forged",
  );
  await expect(page.getByText("VERIFIED", { exact: true }).first()).toBeVisible();
  await expect(page.getByTestId("verified-citation-overlay")).toHaveAttribute(
    "data-page-number",
    "2",
  );
  expect(resolveBodies[1]).toEqual({ navigationRef: NAVIGATION_REF });
});

test("citation verification failure never falls back to URL coordinates or source rendering", async ({
  page,
}) => {
  let sourceReadCount = 0;
  await page.route(
    "**/api/aip/citations/navigation/resolve",
    async (route) => {
      await route.fulfill({
        status: 403,
        contentType: "application/json",
        body: JSON.stringify({
          detail: {
            code: "CITATION_SOURCE_STALE",
            message: "The cited source version or hash is no longer valid.",
            request_id: "req-citation-stale",
            retryable: false,
          },
        }),
      });
    },
  );
  await page.route("**/api/media/versions/**/content", async (route) => {
    sourceReadCount += 1;
    await route.abort();
  });

  await page.goto(
    "/document-intelligence?citation=forged-navigation-ref" +
      "&pageNumber=999&bbox=forged",
  );

  await expect(
    page.getByText("검증에 실패해 원문과 bounding box를 표시하지 않습니다."),
  ).toBeVisible();
  await expect(page.getByTestId("citation-evidence-status")).toHaveText(
    "blocked",
  );
  await expect(page.getByTestId("citation-header-evidence-status")).toHaveText(
    "verification blocked",
  );
  await expect(page.getByTitle(/검증된 원본 PDF/)).toHaveCount(0);
  await expect(page.getByTestId("verified-citation-overlay")).toHaveCount(0);
  expect(sourceReadCount).toBe(0);
});

test("real AIP run resolves a committed PDF Content Unit through the live backend", async ({
  page,
}) => {
  const seed = JSON.parse(
    await readFile(E2E_SEED_PATH, "utf8"),
  ) as Record<string, unknown>;
  const mediaItemVersionId = String(seed.citationMediaItemVersionId);
  const runResponse = await page.request.post(`${API_BASE_URL}/api/aip/agent/run`, {
    headers: {
      ...DEMO_HEADERS,
      "Content-Type": "application/json",
    },
    data: {
      agentRunId: `e2e-pdf-citation-${Date.now()}`,
      agentVersionId: "agent.document-evidence.v1",
      modelAlias: "default-completion",
      promptVersionId: "prompt-document-evidence@v1",
      userMessage: "AIP",
      agentInstruction: "Answer using the selected document citation.",
      securityPartition: "tenant-demo:internal",
      allowedSecurityPartitions: ["tenant-demo:internal"],
      stateJson: {},
      outputSchema: {
        type: "object",
        properties: {
          answer: { type: "string" },
          citations: {
            type: "array",
            items: {
              type: "object",
              properties: {
                contextId: { type: "string" },
                claimSpan: { type: "object" },
                citationOrder: { type: "integer" },
              },
            },
          },
        },
      },
      dataClassification: "internal",
      modelAllowedClassifications: ["public", "internal"],
      maxContextItems: 4,
      maxContextTokens: 1600,
      maxModelCalls: 1,
      maxLoopIterations: 1,
      maxOutputTokens: 512,
      policyVersion: "policy-v1",
    },
  });
  const runBody = await runResponse.text();
  expect(runResponse.ok(), `real AIP run failed: ${runBody}`).toBe(true);
  const run = JSON.parse(runBody) as {
    runStatus: string;
    citations: Array<Record<string, unknown>>;
  };
  expect(run.runStatus).toBe("succeeded");
  expect(run.citations).toHaveLength(1);
  const citation = run.citations[0];
  const evidence = citation.evidence as Record<string, unknown>;
  expect(evidence.mediaItemVersionId).toBe(mediaItemVersionId);
  expect(evidence.pageNumber).toEqual(expect.any(Number));
  expect(evidence.bbox).toEqual(expect.any(Object));
  expect(evidence.sourceLocator).toEqual(expect.any(Object));
  expect(evidence.modelVersion).toBeNull();
  const navigationRef = String(citation.navigationRef);

  await page.goto(
    `/document-intelligence?citation=${encodeURIComponent(navigationRef)}` +
      "&pageNumber=999&bbox=forged",
  );

  await expect(page.getByTestId("citation-evidence-status")).toHaveText(
    "committed",
  );
  await expect(page.getByText("VERIFIED", { exact: true }).first()).toBeVisible();
  await expect(
    page.getByTitle(`검증된 원본 PDF ${String(evidence.pageNumber)} 페이지`),
  ).toBeVisible();
  await expect(page.getByTestId("verified-citation-overlay")).toHaveAttribute(
    "data-page-number",
    String(evidence.pageNumber),
  );
  await expect(page.getByTestId("verified-citation-passport")).toContainText(
    String(evidence.contentUnitId),
  );
});
