import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

import { expect, type Page, type Request, test } from "@playwright/test";

const DEMO_HEADERS = {
  "X-Tenant-ID": "tenant-demo",
  "X-User-ID": "web-demo-operator",
  "X-Roles": "ops_manager,data_engineer,finance,aip_prompt_artifact_reader",
};
const DOCUMENT_REVIEWER_ID = "document-e2e-reviewer";
const REVIEWER_HEADERS = {
  ...DEMO_HEADERS,
  "X-User-ID": DOCUMENT_REVIEWER_ID,
};
const API_BASE_URL =
  process.env.FOUNDRY_LITE_E2E_API_BASE_URL ?? "http://127.0.0.1:8000";
const REPORT_PATH = resolve(
  process.cwd(),
  "docs/Foundry-lite_AIP_Architecture_Report.pdf",
);
const CANARY_PDF_BASE64 =
  "JVBERi0xLjQKMSAwIG9iago8PCAvVHlwZSAvQ2F0YWxvZyAvUGFnZXMgMiAwIFIgPj4KZW5kb2JqCjIgMCBvYmoKPDwgL1R5cGUgL1BhZ2VzIC9LaWRzIFszIDAgUl0gL0NvdW50IDEgPj4KZW5kb2JqCjMgMCBvYmoKPDwgL1R5cGUgL1BhZ2UgL1BhcmVudCAyIDAgUiAvTWVkaWFCb3ggWzAgMCAzMDAgMjAwXSAvUmVzb3VyY2VzIDw8IC9Gb250IDw8IC9GMSA0IDAgUiA+PiA+PiAvQ29udGVudHMgNSAwIFIgPj4KZW5kb2JqCjQgMCBvYmoKPDwgL1R5cGUgL0ZvbnQgL1N1YnR5cGUgL1R5cGUxIC9CYXNlRm9udCAvSGVsdmV0aWNhLUJvbGQgPj4KZW5kb2JqCjUgMCBvYmoKPDwgL0xlbmd0aCAxMDEgPj4Kc3RyZWFtCkJUIC9GMSAxNCBUZiAzMiAxNTAgVGQgKEludm9pY2Ugc3VtbWFyeSkgVGogMCAtMzAgVGQgL0YxIDExIFRmIChQYXltZW50IGlzIGR1ZSBpbiB0aGlydHkgZGF5cy4pIFRqIEVUCmVuZHN0cmVhbQplbmRvYmoKeHJlZgowIDYKMDAwMDAwMDAwMCA2NTUzNSBmIAowMDAwMDAwMDA5IDAwMDAwIG4gCjAwMDAwMDAwNTggMDAwMDAgbiAKMDAwMDAwMDExNSAwMDAwMCBuIAowMDAwMDAwMjQxIDAwMDAwIG4gCjAwMDAwMDAzMTYgMDAwMDAgbiAKdHJhaWxlcgo8PCAvU2l6ZSA2IC9Sb290IDEgMCBSID4+CnN0YXJ0eHJlZgo0NjgKJSVFT0YK";

type JsonRecord = Record<string, unknown>;

function e2eSlug(prefix: string): string {
  return `${prefix}_${Date.now()}_${Math.random().toString(16).slice(2, 8)}`;
}

async function apiPost<T>(
  page: Page,
  path: string,
  data: JsonRecord = {},
  extraHeaders: Record<string, string> = {},
): Promise<T> {
  const response = await page.request.post(`${API_BASE_URL}${path}`, {
    headers: {
      ...DEMO_HEADERS,
      ...extraHeaders,
      "Content-Type": "application/json",
    },
    data,
  });
  expect(response.ok(), `${path} should return ok`).toBe(true);
  return (await response.json()) as T;
}

async function apiGet<T>(page: Page, path: string): Promise<T> {
  const response = await page.request.get(`${API_BASE_URL}${path}`, {
    headers: DEMO_HEADERS,
  });
  expect(response.ok(), `${path} should return ok`).toBe(true);
  return (await response.json()) as T;
}

async function waitForPipelineRun(
  page: Page,
  runId: string,
): Promise<JsonRecord> {
  await expect
    .poll(
      async () =>
        (
          await apiGet<JsonRecord>(
            page,
            `/api/pipelines/runs/${encodeURIComponent(runId)}`,
          )
        ).status,
      { timeout: 60_000 },
    )
    .toMatch(/^(succeeded|partial|failed|cancelled)$/);
  return apiGet<JsonRecord>(
    page,
    `/api/pipelines/runs/${encodeURIComponent(runId)}`,
  );
}

async function seedCommittedReport(page: Page, isCanary = false): Promise<{
  mediaSetRef: string;
  mediaItemVersionId: string;
}> {
  const namespace = "e2e_documents";
  const name = e2eSlug(isCanary ? "canary_invoice" : "architecture_report");
  const fileName = isCanary
    ? "document_profile_canary.pdf"
    : "Foundry-lite_AIP_Architecture_Report.pdf";
  const pdfBuffer = isCanary
    ? Buffer.from(CANARY_PDF_BASE64, "base64")
    : await readFile(REPORT_PATH);
  const mediaSet = await apiPost<JsonRecord>(page, "/api/media/sets", {
    namespace,
    name,
    schemaType: "document",
    primaryFormat: "pdf",
    allowedInputFormats: ["pdf"],
    classification: "public",
    transactionPolicy: "transactional",
    storageProfile: "local",
    processingProfile: "local",
  });
  const mediaSetId = String(mediaSet.media_set_id);
  const transaction = await apiPost<{ mediaTransactionId: string }>(
    page,
    `/api/media/sets/${encodeURIComponent(mediaSetId)}/transactions`,
    { mode: "APPEND" },
    { "Idempotency-Key": `e2e-document-lab-${name}` },
  );
  const uploadResponse = await page.request.post(
    `${API_BASE_URL}/api/media/sets/${encodeURIComponent(mediaSetId)}/transactions/` +
      `${encodeURIComponent(transaction.mediaTransactionId)}/uploads`,
    {
      headers: DEMO_HEADERS,
      multipart: {
        logicalPath: `/reports/${fileName}`,
        schemaType: "document",
        format: "pdf",
        suppliedMimeType: "application/pdf",
        securityEnvelope: JSON.stringify({
          tenantId: "tenant-demo",
          classification: "public",
        }),
        file: {
          name: fileName,
          mimeType: "application/pdf",
          buffer: pdfBuffer,
        },
      },
    },
  );
  expect(uploadResponse.ok(), "real PDF upload should succeed").toBe(true);
  const staged = (await uploadResponse.json()) as JsonRecord;
  await apiPost(
    page,
    `/api/media/transactions/${encodeURIComponent(transaction.mediaTransactionId)}/commit`,
  );
  return {
    mediaSetRef: `${namespace}.${name}`,
    mediaItemVersionId: String(staged.media_item_version_id),
  };
}

async function seedPromotionTarget(
  page: Page,
  source: { mediaSetRef: string; mediaItemVersionId: string },
): Promise<{ branchId: string; nodeId: string; pipelineId: string }> {
  const pipelineId = e2eSlug("document_profile_target");
  const branch = await apiPost<JsonRecord>(
    page,
    "/api/pipelines/branches",
    { pipelineId, name: "draft" },
    { "Idempotency-Key": `e2e-document-target-${pipelineId}` },
  );
  const nodeId = "extract";
  const graph = {
    schemaVersion: 2,
    nodes: [
      {
        id: "media",
        kind: "source",
        descriptorId: "source.media_set",
        specVersion: 1,
        config: {
          label: "Committed PDF source",
          mediaSetRef: source.mediaSetRef,
          mediaItemVersionIds: [source.mediaItemVersionId],
        },
      },
      {
        id: nodeId,
        kind: "transform",
        descriptorId: "transform.document_extract",
        specVersion: 1,
        config: {
          label: "Profile target",
          processorId: "pdf_text_v1@1",
          profileName: "before-lab@1",
          extractionStrategy: "raw",
          parameters: { pageSelection: { start: 1, limit: 3 } },
        },
      },
      {
        id: "rows",
        kind: "transform",
        descriptorId: "bridge.content_units_to_dataset",
        specVersion: 1,
        config: {},
      },
      {
        id: "out",
        kind: "output",
        descriptorId: "output.dataset",
        specVersion: 1,
        config: { outputDatasetRef: `preview.${pipelineId}` },
      },
    ],
    edges: [
      {
        id: "media-extract",
        sourceNodeId: "media",
        sourcePortId: "media",
        targetNodeId: nodeId,
        targetPortId: "media",
      },
      {
        id: "extract-rows",
        sourceNodeId: nodeId,
        sourcePortId: "content",
        targetNodeId: "rows",
        targetPortId: "content",
      },
      {
        id: "rows-out",
        sourceNodeId: "rows",
        sourcePortId: "dataset",
        targetNodeId: "out",
        targetPortId: "input",
      },
    ],
    layout: {},
    outputContract: { columns: [] },
    tests: [],
    schedule: null,
  };
  await apiPost(
    page,
    `/api/pipelines/branches/${encodeURIComponent(String(branch.id))}/graph`,
    {
      graph,
      expectedFingerprint: String(branch.graphFingerprint),
    },
  );
  return { branchId: String(branch.id), nodeId, pipelineId };
}

test("Document Intelligence previews a real long PDF with layout evidence and no serving commit", async ({
  page,
}) => {
  test.setTimeout(120_000);
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  const source = await seedCommittedReport(page);
  const canarySource = await seedCommittedReport(page, true);
  const promotionTarget = await seedPromotionTarget(page, canarySource);
  await page.goto("/document-intelligence");

  await page.getByLabel("Media Set ref").fill(source.mediaSetRef);
  const sourceRequestPromise = page.waitForRequest(
    (request) =>
      request.url().includes(
        `/api/media/versions/${source.mediaItemVersionId}/content`,
      ) && request.method() === "GET",
  );
  await page
    .getByLabel("Media item version ID")
    .fill(source.mediaItemVersionId);
  const sourceRequest = await sourceRequestPromise;
  await expect(page.getByTitle("원본 PDF 1 페이지")).toHaveAttribute(
    "src",
    /^blob:.*#page=1&view=FitH&toolbar=0&navpanes=0&scrollbar=0$/,
  );
  expect(sourceRequest.headers()["x-tenant-id"]).toBe("tenant-demo");
  expect(sourceRequest.headers()["x-request-id"]).toMatch(/^sdk-/);
  await page
    .locator("aside")
    .getByRole("button", { name: "Layout-aware", exact: true })
    .click();

  const createResponse = page.waitForResponse(
    (response) =>
      response.url().includes("/preview-runs") &&
      response.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: "Run no-commit preview" })
    .click();
  const created = (await (await createResponse).json()) as JsonRecord;

  await expect(page.getByText("SUCCEEDED", { exact: true }).first()).toBeVisible({
    timeout: 30_000,
  });
  await expect(page.getByText("commitForbidden=true", { exact: true })).toBeVisible();
  await expect(page.getByText("serving=false", { exact: true })).toBeVisible();
  await expect(page.getByLabel("문서와 bounding box 미리보기")).toContainText(
    "page 1",
  );

  const runResponse = await page.request.get(
    `${API_BASE_URL}/api/pipelines/preview-runs/${encodeURIComponent(String(created.id))}`,
    { headers: DEMO_HEADERS },
  );
  expect(runResponse.ok()).toBe(true);
  const run = (await runResponse.json()) as JsonRecord;
  expect(run.commitForbidden).toBe(true);
  expect(run.servingVersionCreated).toBe(false);
  const outputs = run.outputs as JsonRecord[];
  const items = outputs[0]?.items as JsonRecord[];
  expect(items.length).toBeGreaterThan(0);
  expect(Math.max(...items.map((item) => Number(item.pageNumber)))).toBeLessThanOrEqual(3);
  expect(items.some((item) => typeof item.text === "string" && item.text.length > 0)).toBe(true);
  expect(
    items.some((item) => {
      const bbox = item.bbox as JsonRecord | null;
      return (
        bbox !== null &&
        Number(bbox.pageWidth) > 0 &&
        Number(bbox.pageHeight) > 0
      );
    }),
  ).toBe(true);

  const servingPreview = await page.request.get(
    `${API_BASE_URL}/api/datasets/preview/document_intelligence_lab/preview?limit=1`,
    { headers: DEMO_HEADERS },
  );
  expect(servingPreview.status()).toBe(404);

  await page.getByRole("button", { name: /Structured prompt/ }).click();
  const livePromptVersion = e2eSlug("document-structure");
  await page.getByLabel("Prompt version").fill(livePromptVersion);
  const semanticCreateResponse = page.waitForResponse(
    (response) =>
      response.url().includes("/preview-runs") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Run no-commit preview" }).click();
  const semanticResponse = await semanticCreateResponse;
  const semanticRequest = semanticResponse.request().postDataJSON() as JsonRecord;
  const semanticGraph = semanticRequest.graph as JsonRecord;
  const semanticNodes = semanticGraph.nodes as JsonRecord[];
  const semanticNode = semanticNodes.find(
    (node) => node.descriptorId === "transform.use_llm",
  ) as JsonRecord;
  const semanticConfig = semanticNode.config as JsonRecord;
  const modelParameters = semanticConfig.modelParameters as JsonRecord;
  const outputSchema = semanticConfig.outputSchema as JsonRecord;
  expect(modelParameters.thinkingMode).toBe("disabled");
  expect(modelParameters.maxOutputTokens).toBe(1024);
  expect(semanticConfig.trialCount).toBe(3);
  expect(outputSchema.required).toEqual(["role", "title", "meaning"]);
  expect(outputSchema.additionalProperties).toBe(false);
  const semanticCreated = (await semanticResponse.json()) as JsonRecord;
  await expect(page.getByText("SUCCEEDED", { exact: true }).first()).toBeVisible({
    timeout: 30_000,
  });
  const semanticRunResponse = await page.request.get(
    `${API_BASE_URL}/api/pipelines/preview-runs/${encodeURIComponent(String(semanticCreated.id))}`,
    { headers: DEMO_HEADERS },
  );
  expect(semanticRunResponse.ok()).toBe(true);
  const semanticRun = (await semanticRunResponse.json()) as JsonRecord;
  expect(semanticRun.commitForbidden).toBe(true);
  expect(semanticRun.servingVersionCreated).toBe(false);
  const semanticOutputs = semanticRun.outputs as JsonRecord[];
  const semanticItems = semanticOutputs[0]?.items as JsonRecord[];
  const firstSemanticItem = semanticItems[0] as JsonRecord;
  const interpretation = firstSemanticItem.interpretation as JsonRecord;
  const evidence = firstSemanticItem._pipelineModelEvidence as JsonRecord;
  expect(evidence.modelAlias).toBe("default-completion");
  expect(evidence.promptVersionId).toBe(livePromptVersion);
  expect(evidence.promptMode).toBe("text");
  expect(evidence.thinkingMode).toBe("disabled");
  expect(String(evidence.promptHash)).toMatch(/^sha256:/);
  expect(String(evidence.outputSchemaFingerprint)).toHaveLength(64);
  if (evidence.provider === "anthropic") {
    expect(interpretation.error).toBeNull();
    const output = interpretation.output as JsonRecord;
    expect(["H1", "H2", "body", "table", "figure"]).toContain(
      output.role,
    );
    expect(typeof output.title).toBe("string");
    expect(typeof output.meaning).toBe("string");
    expect(evidence.finishReason).toBe("end_turn");
  } else {
    const typedError = interpretation.error as JsonRecord;
    expect(typedError.code).toBe("PIPELINE_SEMANTIC_OUTPUT_INVALID");
    expect(evidence.outputError).toEqual(typedError);
  }

  const comparisonGraphs: JsonRecord[] = [];
  let activeComparisonCreates = 0;
  let maximumActiveComparisonCreates = 0;
  const isComparisonCreateRequest = (request: Request) =>
    request.method() === "POST" &&
    request.url().includes("/preview-runs") &&
    request
      .headers()
      ["idempotency-key"]?.startsWith("document-lab-comparison-");
  const captureComparisonRequest = (request: Request) => {
    if (!isComparisonCreateRequest(request)) return;
    activeComparisonCreates += 1;
    maximumActiveComparisonCreates = Math.max(
      maximumActiveComparisonCreates,
      activeComparisonCreates,
    );
    const payload = request.postDataJSON() as JsonRecord;
    comparisonGraphs.push(payload.graph as JsonRecord);
  };
  const completeComparisonRequest = (response: { request(): Request }) => {
    if (isComparisonCreateRequest(response.request())) {
      activeComparisonCreates -= 1;
    }
  };
  page.on("request", captureComparisonRequest);
  page.on("response", completeComparisonRequest);
  await page
    .getByRole("button", { name: "Compare Raw · OCR · Layout · VLM" })
    .click();
  await expect(
    page.getByLabel("Document extraction strategy comparison"),
  ).toBeVisible();
  for (const strategy of ["raw", "ocr", "layout", "vlm"]) {
    await expect(
      page.getByTestId(`document-comparison-card-${strategy}`),
    ).toContainText(/SUCCEEDED|PARTIAL/, { timeout: 45_000 });
  }
  await expect.poll(() => comparisonGraphs.length).toBe(4);
  page.off("request", captureComparisonRequest);
  page.off("response", completeComparisonRequest);
  expect(maximumActiveComparisonCreates).toBe(1);
  expect(activeComparisonCreates).toBe(0);
  const vlmGraph = comparisonGraphs.find((candidate) =>
    (candidate.nodes as JsonRecord[]).some((node) => {
      const config = node.config as JsonRecord;
      return config?.promptMode === "layout_aware_vision";
    }),
  ) as JsonRecord;
  const vlmNodes = vlmGraph.nodes as JsonRecord[];
  const vlmSemantic = vlmNodes.find(
    (node) => node.descriptorId === "transform.use_llm",
  ) as JsonRecord;
  const vlmConfig = vlmSemantic.config as JsonRecord;
  expect(vlmNodes.map((node) => node.descriptorId)).toEqual(
    expect.arrayContaining([
      "transform.document_extract",
      "bridge.content_units_to_dataset",
      "transform.use_llm",
    ]),
  );
  expect(vlmConfig.inputFields).toEqual([
    "mediaReference",
    "text",
    "structure",
    "sourceLocator",
  ]);
  expect(vlmConfig.mediaReferenceField).toBe("mediaReference");
  expect(String(vlmConfig.promptTemplate)).not.toContain("{{text}}");
  await expect(
    page.getByTestId("document-comparison-card-ocr"),
  ).toContainText(/Exact bbox\s*[1-9]\d*\/[1-9]\d*/);
  const layoutCard = page.getByTestId("document-comparison-card-layout");
  await layoutCard
    .getByRole("button", { name: /Show result on canvas|Shown on canvas/ })
    .click();
  await expect(layoutCard).toContainText(
    /Exact bbox\s*[1-9]\d*\/[1-9]\d*/,
  );
  await expect(page.getByLabel("문서와 bounding box 미리보기")).toContainText(
    "page 1",
  );
  const vlmCard = page.getByTestId("document-comparison-card-vlm");
  await vlmCard
    .getByRole("button", { name: "Show result on canvas" })
    .click();
  await expect(vlmCard.getByRole("button")).toHaveText("Shown on canvas");

  await page.getByLabel("Promotion target").click();
  await page
    .getByRole("option", {
      name: new RegExp(`${promotionTarget.pipelineId}.*Profile target`),
    })
    .click();
  const promotionResponse = page.waitForResponse(
    (response) =>
      response.url().includes(
        `/api/pipelines/branches/${promotionTarget.branchId}/graph`,
      ) && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Promote exact profile" }).click();
  expect((await promotionResponse).ok()).toBe(true);
  await expect(page.getByRole("status")).toContainText(
    "exact extraction + Use LLM profile",
  );

  const promotedResponse = await page.request.get(
    `${API_BASE_URL}/api/pipelines/branches/${encodeURIComponent(promotionTarget.branchId)}`,
    { headers: DEMO_HEADERS },
  );
  expect(promotedResponse.ok()).toBe(true);
  const promoted = (await promotedResponse.json()) as JsonRecord;
  const promotedGraph = promoted.graph as JsonRecord;
  const promotedNodes = promotedGraph.nodes as JsonRecord[];
  const promotedNode = promotedNodes.find(
    (node) => node.id === promotionTarget.nodeId,
  ) as JsonRecord;
  const promotedConfig = promotedNode.config as JsonRecord;
  const labProfile = promotedConfig.labProfile as JsonRecord;
  expect(promotedConfig.extractionStrategy).toBe("layout_aware_vision");
  expect(promotedConfig.processorId).toBe("pdf_layout_v1@1");
  expect(String(promotedConfig.profileVersion)).toMatch(/^pprev_/);
  expect(promotedConfig.promptTemplate).toBeUndefined();
  expect(promotedConfig.modelAlias).toBeUndefined();
  expect(labProfile.strategy).toBe("layout_aware_vision");
  expect(labProfile.extractionMode).toBe("layout");
  expect(labProfile.commitForbidden).toBe(true);
  expect(labProfile.servingVersionCreated).toBe(false);
  expect(String(labProfile.previewGraphFingerprint)).not.toBe("unavailable");
  const promotedChunk = promotedNodes.find(
    (node) => node.descriptorId === "transform.chunk",
  ) as JsonRecord;
  expect(promotedChunk.config).toMatchObject({
    chunkSize: 500,
    overlap: 50,
    profileVersion: expect.stringMatching(/^pprev_/),
  });
  const promotedSemantic = promotedNodes.find(
    (node) => node.descriptorId === "transform.use_llm",
  ) as JsonRecord;
  const promotedSemanticConfig = promotedSemantic.config as JsonRecord;
  expect(promotedSemanticConfig).toMatchObject({
    promptMode: "layout_aware_vision",
    modelAlias: "default-completion",
    promptVersionId: livePromptVersion,
    mediaReferenceField: "mediaReference",
    inputFields: [
      "mediaReference",
      "text",
      "structure",
      "sourceLocator",
    ],
    expectedModelId: expect.any(String),
    expectedModelRevision: expect.any(String),
    outputSchema: expect.objectContaining({
      type: "object",
      required: ["sections"],
    }),
    modelParameters: {
      temperature: 0,
      maxOutputTokens: 1024,
      thinkingMode: "disabled",
    },
  });
  const promotedEdges = promotedGraph.edges as JsonRecord[];
  const chunkId = String(promotedChunk.id);
  const semanticId = String(promotedSemantic.id);
  expect(promotedEdges).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        sourceNodeId: promotionTarget.nodeId,
        sourcePortId: "content",
        targetNodeId: chunkId,
        targetPortId: "content",
      }),
      expect.objectContaining({
        sourceNodeId: chunkId,
        sourcePortId: "content",
        targetNodeId: "rows",
        targetPortId: "content",
      }),
      expect.objectContaining({
        sourceNodeId: "rows",
        sourcePortId: "dataset",
        targetNodeId: semanticId,
        targetPortId: "input",
      }),
      expect.objectContaining({
        sourceNodeId: semanticId,
        sourcePortId: "dataset",
        targetNodeId: "out",
        targetPortId: "input",
      }),
    ]),
  );

  const validation = await apiGet<JsonRecord>(
    page,
    `/api/pipelines/branches/${encodeURIComponent(promotionTarget.branchId)}/validate`,
  );
  expect(validation).toMatchObject({ valid: true, errors: [] });
  const proposal = await apiPost<JsonRecord>(
    page,
    `/api/pipelines/branches/${encodeURIComponent(promotionTarget.branchId)}/propose`,
    {
      title: `Deploy ${promotionTarget.pipelineId} exact VLM profile`,
      description:
        "Document Lab exact extraction, chunk, prompt, schema, and model revision proof.",
    },
    {
      "Idempotency-Key": `e2e-document-vlm-proposal-${promotionTarget.pipelineId}`,
    },
  );
  const proposalId = String(proposal.id);
  await apiPost(
    page,
    `/api/pipelines/proposals/${encodeURIComponent(proposalId)}/assign`,
    { assigneeUserId: DOCUMENT_REVIEWER_ID },
  );
  await apiPost(
    page,
    `/api/pipelines/proposals/${encodeURIComponent(proposalId)}/decision`,
    { decision: "approve", comment: "Independent browser reviewer approval" },
    REVIEWER_HEADERS,
  );
  const version = await apiPost<JsonRecord>(
    page,
    `/api/pipelines/proposals/${encodeURIComponent(proposalId)}/execute`,
  );
  const versionId = String(version.id);
  await apiPost(
    page,
    `/api/pipelines/${encodeURIComponent(promotionTarget.pipelineId)}/deploy/${encodeURIComponent(versionId)}`,
    {},
    {
      "Idempotency-Key": `e2e-document-vlm-deploy-${promotionTarget.pipelineId}`,
    },
  );
  const queuedProductionRun = await apiPost<JsonRecord>(
    page,
    `/api/pipelines/${encodeURIComponent(promotionTarget.pipelineId)}/runs`,
    { versionId, parameters: {} },
    {
      "Idempotency-Key": `e2e-document-vlm-run-${promotionTarget.pipelineId}`,
    },
  );
  const productionDetail = await waitForPipelineRun(
    page,
    String(queuedProductionRun.id),
  );
  expect(productionDetail.status).toBe("succeeded");
  const artifacts = productionDetail.artifacts as JsonRecord[];
  const semanticArtifact = artifacts.find(
    (artifact) => artifact.nodeId === semanticId,
  ) as JsonRecord;
  const manifest = semanticArtifact.manifest as JsonRecord;
  const metadata = manifest.metadata as JsonRecord;
  const pins = metadata.pins as JsonRecord;
  expect(pins).toMatchObject({
    modelAlias: promotedSemanticConfig.modelAlias,
    expectedModelId: promotedSemanticConfig.expectedModelId,
    expectedModelRevision: promotedSemanticConfig.expectedModelRevision,
    promptVersionId: promotedSemanticConfig.promptVersionId,
    outputSchema: promotedSemanticConfig.outputSchema,
  });
  expect(consoleErrors).toEqual([]);
});
