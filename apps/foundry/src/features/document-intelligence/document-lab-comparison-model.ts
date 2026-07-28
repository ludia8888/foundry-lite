import type {
  MediaProcessorDescriptor,
  PipelineBranch,
  PipelineGraphV2,
  PipelinePreviewRun,
} from "@foundry-lite/sdk";

import {
  documentLabBlocks,
  documentLabMetrics,
  documentLabSemanticConfig,
  effectiveSystemPrompt,
  effectiveUserPrompt,
  isDefaultDocumentSchema,
  outputSchemaForStrategy,
  parseOutputSchema,
  processorForMode,
  promptsForStrategy,
  type DocumentLabConfig,
} from "./document-lab-model";

type GraphNode = PipelineGraphV2["nodes"][number];
type GraphEdge = PipelineGraphV2["edges"][number];

export type DocumentComparisonKind = "raw" | "ocr" | "layout" | "vlm";

export interface DocumentComparisonCandidate {
  id: DocumentComparisonKind;
  label: string;
  detail: string;
  config: DocumentLabConfig | null;
  unavailableReason: string | null;
}

export interface DocumentComparisonResult extends DocumentComparisonCandidate {
  run: PipelinePreviewRun | null;
  error: string | null;
}

export interface DocumentComparisonEvidence {
  pageNumbers: number[];
  blockCount: number;
  bboxCount: number;
  latencyMs: number | null;
  inputTokens: number;
  outputTokens: number;
  estimatedCostUsd: number | null;
  provider: string | null;
  model: string | null;
  promptVersionId: string | null;
  sample: string;
}

export interface DocumentExtractPromotionTarget {
  id: string;
  branchId: string;
  pipelineId: string;
  nodeId: string;
  label: string;
}

const COMPARISON_LABELS: Record<
  DocumentComparisonKind,
  { label: string; detail: string }
> = {
  raw: { label: "Raw", detail: "Embedded PDF text" },
  ocr: { label: "OCR", detail: "Rasterized page recognition" },
  layout: { label: "Layout", detail: "Block roles + bounding boxes" },
  vlm: { label: "VLM", detail: "Prompted whole-document interpretation" },
};

export function documentComparisonCandidates(
  config: DocumentLabConfig,
  processors: readonly MediaProcessorDescriptor[],
): DocumentComparisonCandidate[] {
  const pdfProcessorIds = new Set(
    processors
      .filter((processor) => processor.inputFormats.includes("pdf"))
      .map((processor) => processor.processorId),
  );
  return (["raw", "ocr", "layout", "vlm"] as const).map((kind) =>
    comparisonCandidate(config, pdfProcessorIds, kind),
  );
}

export function emptyComparisonResults(
  candidates: readonly DocumentComparisonCandidate[],
): DocumentComparisonResult[] {
  return candidates.map((candidate) => ({
    ...candidate,
    run: null,
    error: null,
  }));
}

export function documentComparisonEvidence(
  result: DocumentComparisonResult,
): DocumentComparisonEvidence {
  const blocks = documentLabBlocks(result.run);
  const metrics = documentLabMetrics(blocks);
  const evidence = blocks
    .map((block) => block.evidence)
    .find((item) => item !== null);
  return {
    pageNumbers: Array.from(
      new Set(blocks.map((block) => block.pageNumber)),
    ).sort((left, right) => left - right),
    blockCount: blocks.length,
    bboxCount: blocks.filter((block) => block.bbox !== null).length,
    latencyMs: previewLatencyMs(result.run),
    inputTokens: metrics.inputTokens,
    outputTokens: metrics.outputTokens,
    estimatedCostUsd: estimatedCost(blocks),
    provider: optionalText(evidence?.provider),
    model:
      optionalText(evidence?.resolvedModelRevision) ??
      optionalText(evidence?.resolvedModelId),
    promptVersionId: optionalText(evidence?.promptVersionId),
    sample: blocks[0]?.text ?? "",
  };
}

export function documentExtractPromotionTargets(
  branches: readonly PipelineBranch[],
): DocumentExtractPromotionTarget[] {
  return branches.flatMap((branch) => {
    const graph = pipelineGraphV2(branch.graph);
    if (!graph) return [];
    return graph.nodes
      .filter((node) => node.descriptorId === "transform.document_extract")
      .map((node) => ({
        id: `${branch.id}::${node.id}`,
        branchId: branch.id,
        pipelineId: branch.pipelineId,
        nodeId: node.id,
        label: `${branch.pipelineId} · ${nodeLabel(node.config, node.id)}`,
      }));
  });
}

export function promoteDocumentExtractProfile(
  branch: PipelineBranch,
  targetNodeId: string,
  config: DocumentLabConfig,
  run: PipelinePreviewRun,
): PipelineGraphV2 {
  const graph = pipelineGraphV2(branch.graph);
  if (!graph) {
    throw new Error("Graph v2 draft만 extraction profile을 받을 수 있습니다.");
  }
  if (config.strategy === "basic_vision") {
    throw new Error(
      "Basic Vision은 document.extract target에 승격할 수 없습니다. Layout-aware Vision 또는 Pipeline Builder의 Media rows → Use LLM 흐름을 사용하세요.",
    );
  }
  const target = graph.nodes.find(
    (node) =>
      node.id === targetNodeId &&
      node.descriptorId === "transform.document_extract",
  );
  if (!target) {
    throw new Error("선택한 draft에서 document.extract 노드를 찾을 수 없습니다.");
  }
  const extractionNode = {
    ...target,
    config: {
      ...target.config,
      ...promotedExtractionConfig(config, run, target.config),
    },
  };
  const initialNodes = graph.nodes.map((node) =>
    node.id === targetNodeId ? extractionNode : node,
  );
  const chunkPath = ensureChunkPath(
    initialNodes,
    graph.edges,
    targetNodeId,
    config,
    run,
  );
  if (config.strategy === "traditional") {
    return {
      ...graph,
      nodes: chunkPath.nodes,
      edges: chunkPath.edges,
    };
  }
  const semanticPath = ensureSemanticPath(
    chunkPath.nodes,
    chunkPath.edges,
    targetNodeId,
    chunkPath.rowsNodeId,
    config,
    run,
  );
  return {
    ...graph,
    nodes: semanticPath.nodes,
    edges: semanticPath.edges,
  };
}

function comparisonCandidate(
  config: DocumentLabConfig,
  availableProcessorIds: ReadonlySet<string>,
  kind: DocumentComparisonKind,
): DocumentComparisonCandidate {
  const presentation = COMPARISON_LABELS[kind];
  if (kind === "vlm") {
    const layoutProcessorId =
      processorForMode("layout", availableProcessorIds) ?? config.processorId;
    const strategy = "layout_aware_vision";
    return {
      id: kind,
      ...presentation,
      config: {
        ...config,
        strategy,
        extractionMode: "layout",
        processorId: layoutProcessorId,
        ...promptsForStrategy(strategy, config),
        outputSchemaText: isDefaultDocumentSchema(config.outputSchemaText)
          ? outputSchemaForStrategy(strategy)
          : config.outputSchemaText,
      },
      unavailableReason: null,
    };
  }
  const mode = kind === "layout" ? "layout" : kind;
  const processorId = processorForMode(mode, availableProcessorIds);
  if (!processorId) {
    return {
      id: kind,
      ...presentation,
      config: null,
      unavailableReason:
        kind === "ocr"
          ? "현재 runtime에는 PDF rasterization + OCR processor가 없습니다. 이미지 OCR을 PDF에 우회 적용하지 않습니다."
          : `현재 runtime에 ${presentation.label} PDF processor가 없습니다.`,
    };
  }
  return {
    id: kind,
    ...presentation,
    config: {
      ...config,
      strategy: "traditional",
      extractionMode: mode,
      processorId,
    },
    unavailableReason: null,
  };
}

function promotedExtractionConfig(
  config: DocumentLabConfig,
  run: PipelinePreviewRun,
  existing: Record<string, unknown>,
): Record<string, unknown> {
  const graphFingerprint =
    optionalText(run.graphFingerprint) ?? "unavailable";
  return {
    processorId:
      config.processorId || optionalText(existing.processorId) || "unresolved",
    profileName: `document-lab/${profileStrategy(config)}/${run.id}`,
    profileVersion: run.id,
    extractionStrategy: profileStrategy(config),
    outputFormat: config.extractionMode === "layout" ? "layout_json" : "text",
    parameters: {
      pageSelection: {
        start: config.pageStart,
        limit: config.pageLimit,
      },
    },
    labProfile: labProfile(config, run, graphFingerprint),
  };
}

function ensureChunkPath(
  nodes: readonly GraphNode[],
  edges: readonly GraphEdge[],
  targetNodeId: string,
  config: DocumentLabConfig,
  run: PipelinePreviewRun,
): { nodes: GraphNode[]; edges: GraphEdge[]; rowsNodeId: string } {
  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const outgoing = edges.filter(
    (edge) =>
      edge.sourceNodeId === targetNodeId && edge.sourcePortId === "content",
  );
  const chunkEdge = outgoing.find(
    (edge) =>
      nodeById.get(edge.targetNodeId)?.descriptorId === "transform.chunk",
  );
  if (chunkEdge) {
    return existingChunkPath(nodes, edges, chunkEdge, config, run, nodeById);
  }
  const rowsEdge = outgoing.find(
    (edge) =>
      nodeById.get(edge.targetNodeId)?.descriptorId ===
      "bridge.content_units_to_dataset",
  );
  if (!rowsEdge) {
    throw new Error(
      "document.extract 뒤에 Content Units → Table bridge가 있어야 exact profile을 승격할 수 있습니다.",
    );
  }
  const chunkId = uniqueId(`${targetNodeId}-chunk`, new Set(nodeById.keys()));
  const chunkNode = promotedChunkNode(chunkId, config, run);
  const bridgeEdgeId = uniqueId(
    `${chunkId}-rows`,
    new Set(edges.map((edge) => edge.id)),
  );
  return {
    nodes: [...nodes, chunkNode],
    edges: [
      ...edges.map((edge) =>
        edge.id === rowsEdge.id
          ? { ...edge, targetNodeId: chunkId, targetPortId: "content" }
          : edge,
      ),
      {
        id: bridgeEdgeId,
        sourceNodeId: chunkId,
        sourcePortId: "content",
        targetNodeId: rowsEdge.targetNodeId,
        targetPortId: "content",
      },
    ],
    rowsNodeId: rowsEdge.targetNodeId,
  };
}

function existingChunkPath(
  nodes: readonly GraphNode[],
  edges: readonly GraphEdge[],
  chunkEdge: GraphEdge,
  config: DocumentLabConfig,
  run: PipelinePreviewRun,
  nodeById: ReadonlyMap<string, GraphNode>,
): { nodes: GraphNode[]; edges: GraphEdge[]; rowsNodeId: string } {
  const rowsEdge = edges.find(
    (edge) =>
      edge.sourceNodeId === chunkEdge.targetNodeId &&
      edge.sourcePortId === "content" &&
      nodeById.get(edge.targetNodeId)?.descriptorId ===
        "bridge.content_units_to_dataset",
  );
  if (!rowsEdge) {
    throw new Error(
      "document.extract의 chunk 뒤에 Content Units → Table bridge가 필요합니다.",
    );
  }
  return {
    nodes: nodes.map((node) =>
      node.id === chunkEdge.targetNodeId
        ? {
            ...node,
            config: {
              ...node.config,
              ...promotedChunkConfig(config, run),
            },
          }
        : node,
    ),
    edges: [...edges],
    rowsNodeId: rowsEdge.targetNodeId,
  };
}

function promotedChunkNode(
  id: string,
  config: DocumentLabConfig,
  run: PipelinePreviewRun,
): GraphNode {
  return {
    id,
    kind: "transform",
    descriptorId: "transform.chunk",
    specVersion: 1,
    config: promotedChunkConfig(config, run),
  };
}

function promotedChunkConfig(
  config: DocumentLabConfig,
  run: PipelinePreviewRun,
): Record<string, unknown> {
  return {
    label: "Document Lab exact chunk profile",
    chunkSize: config.chunkSize,
    overlap: config.overlap,
    profileName: `document-lab/chunk/${run.id}`,
    profileVersion: run.id,
  };
}

function ensureSemanticPath(
  nodes: readonly GraphNode[],
  edges: readonly GraphEdge[],
  targetNodeId: string,
  rowsNodeId: string,
  config: DocumentLabConfig,
  run: PipelinePreviewRun,
): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const existingEdge = edges.find(
    (edge) =>
      edge.sourceNodeId === rowsNodeId &&
      edge.sourcePortId === "dataset" &&
      nodeById.get(edge.targetNodeId)?.descriptorId === "transform.use_llm",
  );
  const semanticConfig = promotedSemanticConfig(config, run);
  if (existingEdge) {
    return {
      nodes: nodes.map((node) =>
        node.id === existingEdge.targetNodeId
          ? { ...node, config: { ...node.config, ...semanticConfig } }
          : node,
      ),
      edges: [...edges],
    };
  }
  const downstream = edges.filter(
    (edge) =>
      edge.sourceNodeId === rowsNodeId && edge.sourcePortId === "dataset",
  );
  if (downstream.length === 0) {
    throw new Error(
      "Content Units → Table bridge 뒤에 연결된 output이 있어야 Use LLM을 원자적으로 삽입할 수 있습니다.",
    );
  }
  const semanticNodeId = uniqueId(
    `${targetNodeId}-semantic`,
    new Set(nodeById.keys()),
  );
  const semanticEdgeId = uniqueId(
    `${rowsNodeId}-${semanticNodeId}`,
    new Set(edges.map((edge) => edge.id)),
  );
  const downstreamIds = new Set(downstream.map((edge) => edge.id));
  return {
    nodes: [
      ...nodes,
      {
        id: semanticNodeId,
        kind: "transform",
        descriptorId: "transform.use_llm",
        specVersion: 1,
        config: semanticConfig,
      },
    ],
    edges: [
      ...edges
        .filter((edge) => !downstreamIds.has(edge.id))
        .concat(
          downstream.map((edge) => ({
            ...edge,
            sourceNodeId: semanticNodeId,
            sourcePortId: "dataset",
          })),
        ),
      {
        id: semanticEdgeId,
        sourceNodeId: rowsNodeId,
        sourcePortId: "dataset",
        targetNodeId: semanticNodeId,
        targetPortId: "input",
      },
    ],
  };
}

function promotedSemanticConfig(
  config: DocumentLabConfig,
  run: PipelinePreviewRun,
): Record<string, unknown> {
  const isLayoutVision = config.strategy === "layout_aware_vision";
  const modelPin = promotedModelPin(run);
  return {
    label: "Document Lab exact semantic profile",
    ...documentLabSemanticConfig(config),
    ...modelPin,
    inputFields: isLayoutVision
      ? ["mediaReference", "text", "structure", "sourceLocator"]
      : ["text", "structure", "sourceLocator"],
    ...(isLayoutVision
      ? { mediaReferenceField: "mediaReference" }
      : { mediaReferenceField: undefined }),
    profileName: `document-lab/semantic/${run.id}`,
    profileVersion: run.id,
    previewGraphFingerprint:
      optionalText(run.graphFingerprint) ?? "unavailable",
  };
}

function promotedModelPin(
  run: PipelinePreviewRun,
): { expectedModelId: string; expectedModelRevision: string } {
  for (const output of run.outputs) {
    const items = Array.isArray(output.items) ? output.items : [];
    for (const item of items) {
      if (!isRecord(item) || !isRecord(item._pipelineModelEvidence)) continue;
      const expectedModelId = optionalText(
        item._pipelineModelEvidence.resolvedModelId,
      );
      const expectedModelRevision = optionalText(
        item._pipelineModelEvidence.resolvedModelRevision,
      );
      if (expectedModelId && expectedModelRevision) {
        return { expectedModelId, expectedModelRevision };
      }
    }
  }
  throw new Error(
    "성공한 preview의 resolved model ID와 revision이 없어 exact semantic profile을 승격할 수 없습니다.",
  );
}

function labProfile(
  config: DocumentLabConfig,
  run: PipelinePreviewRun,
  graphFingerprint: string,
): Record<string, unknown> {
  return {
    schemaVersion: 2,
    previewRunId: run.id,
    previewGraphFingerprint: graphFingerprint,
    commitForbidden: run.commitForbidden,
    servingVersionCreated: run.servingVersionCreated,
    processorId: config.processorId,
    extractionMode: config.extractionMode,
    strategy: config.strategy,
    chunkSize: config.chunkSize,
    overlap: config.overlap,
    modelAlias: config.modelAlias,
    promptVersionId: config.promptVersionId,
    systemPrompt: config.systemPrompt,
    userPrompt: config.userPrompt,
    effectiveSystemPrompt: effectiveSystemPrompt(config),
    effectiveUserPrompt: effectiveUserPrompt(config),
    outputSchema: parseOutputSchema(config.outputSchemaText),
    dataClassification: config.dataClassification,
    includeErrors: config.includeErrors,
    trialCount: config.trialCount,
    modelParameters: {
      temperature: config.temperature,
      maxOutputTokens: config.maxOutputTokens,
      thinkingMode: config.thinkingMode,
    },
  };
}

function uniqueId(base: string, existing: ReadonlySet<string>): string {
  if (!existing.has(base)) return base;
  let index = 2;
  while (existing.has(`${base}-${index}`)) index += 1;
  return `${base}-${index}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function profileStrategy(config: DocumentLabConfig): string {
  if (config.strategy !== "traditional") return config.strategy;
  return config.extractionMode === "layout"
    ? "layout_aware"
    : config.extractionMode;
}

function pipelineGraphV2(
  graph: PipelineBranch["graph"],
): PipelineGraphV2 | null {
  if (
    typeof graph !== "object" ||
    graph === null ||
    !("schemaVersion" in graph) ||
    graph.schemaVersion !== 2
  ) {
    return null;
  }
  return graph as PipelineGraphV2;
}

function nodeLabel(config: Record<string, unknown>, nodeId: string): string {
  return optionalText(config.label) ?? nodeId;
}

function previewLatencyMs(run: PipelinePreviewRun | null): number | null {
  if (!run) return null;
  const startedAt = timestamp(run.startedAt);
  const completedAt = timestamp(run.completedAt);
  if (startedAt === null || completedAt === null) return null;
  return Math.max(0, completedAt - startedAt);
}

function estimatedCost(
  blocks: ReturnType<typeof documentLabBlocks>,
): number | null {
  let total = 0;
  let hasCost = false;
  for (const block of blocks) {
    const value =
      numericValue(block.evidence?.estimatedCostUsd) ??
      numericValue(block.evidence?.costUsd);
    if (value === null) continue;
    total += value;
    hasCost = true;
  }
  return hasCost ? total : null;
}

function numericValue(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value !== "string" || !value.trim()) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function optionalText(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function timestamp(value: unknown): number | null {
  if (typeof value !== "string") return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}
