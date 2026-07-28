import type {
  PipelineEdge,
  PipelineGraph,
  PipelineGraphV2,
  PipelineGraphV2Edge,
  PipelineGraphV2Node,
  PipelineNode,
  PipelineNodeDescriptorPayload,
  PipelineNodeType,
  PipelineSchemaColumn,
} from "@foundry-lite/sdk";

export type NodePosition = { x: number; y: number };
export type PositionsByNodeId = Record<string, NodePosition>;

export type PipelineCanvasNodeType =
  | PipelineNodeType
  | "unknown_v2"
  | "source_media_set"
  | "source_stream"
  | "source_geospatial"
  | "media_transform"
  | "document_extract"
  | "chunk"
  | "embedding_text"
  | "embedding_vision"
  | "media_to_table_rows"
  | "content_units_to_dataset"
  | "stream_to_dataset"
  | "use_llm"
  | "trained_model"
  | "output_media_set"
  | "output_virtual_table"
  | "output_semantic_index"
  | "output_ontology"
  | "output_geospatial";

export type PipelineReadOnlyReason =
  | "unknown_descriptor"
  | "unsupported_spec_version"
  | "descriptor_contract_missing"
  | "descriptor_kind_mismatch";

export type PipelineCanvasNode = Omit<PipelineNode, "type"> & {
  type: PipelineCanvasNodeType;
  kind: "source" | "transform" | "output";
  descriptorId: string;
  specVersion: number;
  descriptor?: PipelineNodeDescriptorPayload;
  isReadOnly?: boolean;
  readOnlyReason?: PipelineReadOnlyReason;
  preservedInputPortIds?: string[];
  preservedOutputPortIds?: string[];
  rawV2Node?: Record<string, unknown>;
};

export type PipelineCanvasEdge = PipelineEdge & {
  rawV2Edge?: Record<string, unknown>;
};

export type PipelineCanvasGraph = Omit<
  PipelineGraph,
  "nodes" | "edges"
> & {
  schemaVersion: 2;
  nodes: PipelineCanvasNode[];
  edges: PipelineCanvasEdge[];
  metadata?: Record<string, unknown>;
  rawV2Graph?: Record<string, unknown>;
};

export type PersistedPipelineGraph = PipelineGraph | PipelineGraphV2;

export type PipelineConnection = Pick<
  PipelineEdge,
  "source" | "target" | "sourceHandle" | "targetHandle"
>;

export type SelectCastColumn = {
  source: string;
  name: string;
  type: string;
};

export type PipelineValidationIssue = Record<string, unknown> & {
  code: string;
};

export type PipelineValidationResult = {
  valid: boolean;
  errors: PipelineValidationIssue[];
  warnings: PipelineValidationIssue[];
  fingerprint: string;
};

export type NodeTypeMeta = {
  label: string;
  shortLabel: string;
  headerClassName: string;
  iconCellClassName: string;
  iconClassName: string;
  isSolidHeader: boolean;
};

export const NODE_TYPE_META: Record<PipelineCanvasNodeType, NodeTypeMeta> = {
  unknown_v2: nodeMeta(
    "지원되지 않는 Graph v2 노드",
    "Read only",
    "bg-[#5F6B7C] text-white",
    "bg-[#EDEFF2]",
    "text-[#5F6B7C]",
    true,
  ),
  dataset: nodeMeta(
    "데이터셋 입력",
    "데이터셋",
    "bg-[#F5F6F7] text-foreground",
    "bg-[#F5F6F7]",
    "text-[#4C90F0]",
    false,
  ),
  source_media_set: nodeMeta(
    "Media Set 입력",
    "Media Set",
    "bg-[#F5F6F7] text-foreground",
    "bg-[#E7F4F2]",
    "text-[#147D75]",
    false,
  ),
  source_stream: nodeMeta(
    "Stream / CDC 입력",
    "Stream",
    "bg-[#F5F6F7] text-foreground",
    "bg-[#EAF2FC]",
    "text-[#2D72D2]",
    false,
  ),
  source_geospatial: nodeMeta(
    "Geospatial 입력",
    "Geospatial",
    "bg-[#F5F6F7] text-foreground",
    "bg-[#E7F4F2]",
    "text-[#147D75]",
    false,
  ),
  media_transform: nodeMeta(
    "Transform media",
    "Media",
    "bg-[#2D72D2] text-white",
    "bg-[#EAF2FC]",
    "text-[#2D72D2]",
    true,
  ),
  sql: transformMeta("SQL 변환", "SQL"),
  python: transformMeta("Python 변환", "Python"),
  join: nodeMeta(
    "조인",
    "조인",
    "bg-[#7BC3BB] text-white",
    "bg-[#EFEBFA]",
    "text-[#A48BE8]",
    true,
  ),
  union: nodeMeta(
    "유니온",
    "유니온",
    "bg-[#7BC3BB] text-white",
    "bg-[#EFEBFA]",
    "text-[#A48BE8]",
    true,
  ),
  select_cast: nodeMeta(
    "컬럼 선택/캐스트",
    "선택/캐스트",
    "bg-[#DCBC79] text-white",
    "bg-[#EDF1F7]",
    "text-[#4C90F0]",
    true,
  ),
  document_extract: nodeMeta(
    "Document extract",
    "문서 추출",
    "bg-[#4C90F0] text-white",
    "bg-[#EAF2FC]",
    "text-[#2D72D2]",
    true,
  ),
  chunk: nodeMeta(
    "Chunk content",
    "청크",
    "bg-[#C9973E] text-white",
    "bg-[#FFF4D6]",
    "text-[#9B6D14]",
    true,
  ),
  embedding_text: nodeMeta(
    "Text embedding",
    "임베딩",
    "bg-[#7D63C7] text-white",
    "bg-[#EFEBFA]",
    "text-[#7961DB]",
    true,
  ),
  embedding_vision: nodeMeta(
    "Vision embedding",
    "Vision",
    "bg-[#7961DB] text-white",
    "bg-[#EFEBFA]",
    "text-[#7961DB]",
    true,
  ),
  media_to_table_rows: bridgeMeta("Media → Table rows", "Media bridge"),
  content_units_to_dataset: bridgeMeta(
    "Content Units → Dataset",
    "Content bridge",
  ),
  stream_to_dataset: bridgeMeta("Stream checkpoint → Dataset rows", "Stream bridge"),
  use_llm: nodeMeta(
    "Use LLM",
    "Use LLM",
    "bg-[#7961DB] text-white",
    "bg-[#EFEBFA]",
    "text-[#7961DB]",
    true,
  ),
  trained_model: nodeMeta(
    "Trained Model",
    "Model",
    "bg-[#5F6B7C] text-white",
    "bg-[#EDF1F7]",
    "text-[#394B59]",
    true,
  ),
  output_dataset: outputMeta("데이터셋 출력", "Dataset"),
  output_media_set: outputMeta("Media Set 출력", "Media Set"),
  output_virtual_table: outputMeta("Virtual Table 출력", "Virtual Table"),
  output_semantic_index: outputMeta("Semantic index 출력", "Index"),
  output_ontology: outputMeta("Ontology mapping 출력", "Ontology"),
  output_geospatial: outputMeta("Geospatial 출력", "Geospatial"),
};

const DESCRIPTOR_BY_LEGACY_TYPE: Record<PipelineNodeType, string> = {
  dataset: "source.dataset",
  sql: "transform.sql",
  python: "transform.python",
  join: "transform.join",
  union: "transform.union",
  select_cast: "transform.select_cast",
  output_dataset: "output.dataset",
};

const CANVAS_TYPE_BY_DESCRIPTOR: Record<string, PipelineCanvasNodeType> = {
  "source.dataset": "dataset",
  "source.media_set": "source_media_set",
  "source.stream": "source_stream",
  "source.geospatial": "source_geospatial",
  "transform.media": "media_transform",
  "transform.sql": "sql",
  "transform.python": "python",
  "transform.join": "join",
  "transform.union": "union",
  "transform.select_cast": "select_cast",
  "transform.document_extract": "document_extract",
  "transform.chunk": "chunk",
  "transform.embedding.text": "embedding_text",
  "transform.embedding.vision": "embedding_vision",
  "transform.use_llm": "use_llm",
  "transform.trained_model": "trained_model",
  "bridge.media_to_table_rows": "media_to_table_rows",
  "bridge.content_units_to_dataset": "content_units_to_dataset",
  "bridge.stream_to_dataset": "stream_to_dataset",
  "output.dataset": "output_dataset",
  "output.media_set": "output_media_set",
  "output.virtual_table": "output_virtual_table",
  "output.semantic_index": "output_semantic_index",
  "output.ontology": "output_ontology",
  "output.geospatial": "output_geospatial",
};

const SUPPORTED_SPEC_VERSIONS_BY_DESCRIPTOR: Record<
  string,
  readonly number[]
> = Object.fromEntries(
  Object.keys(CANVAS_TYPE_BY_DESCRIPTOR).map((descriptorId) => [
    descriptorId,
    [1],
  ]),
);

const LEGACY_PORTS: Record<
  string,
  { inputs: readonly string[]; outputs: readonly string[] }
> = {
  "source.dataset": { inputs: [], outputs: ["dataset"] },
  "source.media_set": { inputs: [], outputs: ["media"] },
  "source.stream": { inputs: [], outputs: ["stream"] },
  "source.geospatial": { inputs: [], outputs: ["series"] },
  "transform.media": { inputs: ["media"], outputs: ["derivatives"] },
  "transform.sql": { inputs: ["inputs"], outputs: ["dataset"] },
  "transform.python": { inputs: ["inputs"], outputs: ["dataset"] },
  "transform.join": { inputs: ["left", "right"], outputs: ["dataset"] },
  "transform.union": { inputs: ["inputs"], outputs: ["dataset"] },
  "transform.select_cast": { inputs: ["input"], outputs: ["dataset"] },
  "transform.document_extract": { inputs: ["media"], outputs: ["content"] },
  "transform.chunk": { inputs: ["content"], outputs: ["content"] },
  "transform.embedding.text": { inputs: ["content"], outputs: ["index"] },
  "transform.embedding.vision": { inputs: ["media"], outputs: ["index"] },
  "transform.use_llm": { inputs: ["input"], outputs: ["dataset"] },
  "transform.trained_model": { inputs: ["input"], outputs: ["dataset"] },
  "bridge.media_to_table_rows": { inputs: ["media"], outputs: ["dataset"] },
  "bridge.content_units_to_dataset": {
    inputs: ["content"],
    outputs: ["dataset"],
  },
  "bridge.stream_to_dataset": { inputs: ["stream"], outputs: ["dataset"] },
  "output.dataset": { inputs: ["input"], outputs: ["dataset"] },
  "output.media_set": { inputs: ["media"], outputs: ["media"] },
  "output.virtual_table": { inputs: ["input"], outputs: ["table"] },
  "output.semantic_index": { inputs: ["index"], outputs: ["index"] },
  "output.ontology": { inputs: ["input"], outputs: ["mapping"] },
  "output.geospatial": { inputs: ["input"], outputs: ["series"] },
};

const EXECUTION_FIELDS_BY_NODE_TYPE: Record<
  PipelineCanvasNodeType,
  readonly string[]
> = {
  unknown_v2: [],
  dataset: ["datasetRef"],
  source_media_set: ["mediaSetRef", "mediaItemVersionIds"],
  source_stream: ["sourceRef"],
  source_geospatial: [
    "resourceRef",
    "geometryField",
    "longitudeField",
    "latitudeField",
    "timeField",
  ],
  media_transform: ["processorId", "parameters"],
  sql: ["outputDatasetRef", "sql"],
  python: ["outputDatasetRef", "sourceCode", "functionName"],
  join: [
    "outputDatasetRef",
    "leftKey",
    "rightKey",
    "joinType",
    "leftNodeId",
    "rightNodeId",
  ],
  union: ["outputDatasetRef"],
  select_cast: ["outputDatasetRef", "columns"],
  document_extract: [
    "processorId",
    "profileName",
    "extractionStrategy",
    "outputFormat",
    "promptMode",
    "promptTemplate",
    "systemPrompt",
    "parameters",
  ],
  chunk: ["chunkSize", "overlap"],
  embedding_text: ["modelRef"],
  embedding_vision: ["modelRef"],
  media_to_table_rows: [],
  content_units_to_dataset: [],
  stream_to_dataset: [],
  use_llm: [
    "templateId",
    "modelAlias",
    "expectedModelId",
    "expectedModelRevision",
    "promptVersionId",
    "promptTemplate",
    "systemPrompt",
    "inputFields",
    "outputColumn",
    "outputSchema",
    "dataClassification",
    "outputMode",
    "skipRecomputingRows",
    "cacheGeneration",
    "modelParameters",
    "mediaReferenceField",
    "environment",
    "regionRequirement",
    "trialCount",
    "cachePolicy",
  ],
  trained_model: [
    "modelRef",
    "modelBranch",
    "fallbackBranches",
    "inputMappings",
    "outputMappings",
  ],
  output_dataset: ["outputDatasetRef"],
  output_media_set: ["mediaSetRef"],
  output_virtual_table: ["virtualTableRef"],
  output_semantic_index: ["indexRef"],
  output_ontology: ["mappingRef"],
  output_geospatial: [
    "resourceRef",
    "geometryField",
    "longitudeField",
    "latitudeField",
    "timeField",
  ],
};

export function emptyGraphDoc(): PipelineCanvasGraph {
  return {
    schemaVersion: 2,
    nodes: [],
    edges: [],
    layout: {},
    outputContract: { columns: [] },
    tests: [],
    schedule: null,
  };
}

export function normalizeGraphDoc(
  graph: PersistedPipelineGraph | null | undefined,
  descriptors: readonly PipelineNodeDescriptorPayload[] = [],
): PipelineCanvasGraph {
  if (!graph) return emptyGraphDoc();
  const descriptorById = currentDescriptorsById(descriptors);
  const descriptorByKey = new Map(
    descriptors.map((descriptor) => [
      descriptorKey(descriptor.descriptorId, descriptor.specVersion),
      descriptor,
    ]),
  );
  const raw = graph as unknown as Record<string, unknown>;
  return raw.schemaVersion === 2
    ? normalizeV2Graph(raw, descriptorByKey)
    : normalizeV1Graph(graph as PipelineGraph, descriptorById);
}

export function nodeLabel(node: PipelineCanvasNode): string {
  const label = nodeDataOf(node).label;
  if (typeof label === "string" && label.length > 0) return label;
  const ref = nodeDatasetRef(node);
  if (ref) return ref.split(/[/.]/).pop() ?? ref;
  return NODE_TYPE_META[node.type].label;
}

export function splitDatasetRef(ref: string | null): [string, string] {
  if (!ref) return ["", ""];
  const separator = ref.includes(".") ? "." : "/";
  const index = ref.indexOf(separator);
  if (index < 0) return [ref, ""];
  return [ref.slice(0, index), ref.slice(index + 1)];
}

export function nodeDataOf(
  node: PipelineCanvasNode,
): Record<string, unknown> {
  return { ...(node.data ?? {}), ...(node.config ?? {}) };
}

export function canonicalizeGraphForSave(
  graph: PipelineCanvasGraph,
): PipelineGraphV2 {
  const normalized = normalizeJoinEdges(graph);
  const nodes = normalized.nodes.map((node) =>
    canonicalizeNodeForSave(node, normalized.edges),
  );
  const edges = normalized.edges.map((edge) =>
    canonicalizeEdgeForSave(edge, normalized.nodes),
  );
  return {
    ...cloneRecord(normalized.rawV2Graph ?? {}),
    schemaVersion: 2,
    nodes,
    edges: orderNamedJoinInputEdges(nodes, edges),
    layout: normalized.layout,
    outputContract: normalized.outputContract,
    tests: normalized.tests,
    schedule: normalized.schedule,
    metadata: normalized.metadata,
  };
}

export function importedTrainedModelRefs(
  graph: Pick<PipelineCanvasGraph, "metadata"> | PipelineGraphV2 | null,
): string[] {
  const metadata = recordValue(graph?.metadata);
  const reusables = recordValue(metadata?.reusables);
  const refs = reusables?.trainedModels;
  if (!Array.isArray(refs)) return [];
  return [
    ...new Set(
      refs.filter(
        (ref): ref is string =>
          typeof ref === "string" && ref.trim().length > 0,
      ),
    ),
  ].sort();
}

export function withImportedTrainedModel(
  graph: PipelineCanvasGraph,
  modelRef: string,
): PipelineCanvasGraph {
  const metadata = cloneRecord(graph.metadata ?? {});
  const reusables = cloneRecord(recordValue(metadata.reusables) ?? {});
  reusables.trainedModels = [
    ...new Set([...importedTrainedModelRefs(graph), modelRef]),
  ].sort();
  metadata.reusables = reusables;
  return { ...graph, metadata };
}

export function trainedModelUsageNodeIds(
  graph: Pick<PipelineCanvasGraph, "nodes"> | null,
  modelRef: string,
): string[] {
  if (!graph) return [];
  return graph.nodes
    .filter(
      (node) =>
        node.descriptorId === "transform.trained_model" &&
        nodeDataOf(node).modelRef === modelRef,
    )
    .map((node) => node.id)
    .sort();
}

export function withoutImportedTrainedModel(
  graph: PipelineCanvasGraph,
  modelRef: string,
): PipelineCanvasGraph {
  const metadata = cloneRecord(graph.metadata ?? {});
  const reusables = cloneRecord(recordValue(metadata.reusables) ?? {});
  const remaining = importedTrainedModelRefs(graph).filter(
    (candidate) => candidate !== modelRef,
  );
  if (remaining.length > 0) reusables.trainedModels = remaining;
  else delete reusables.trainedModels;
  if (Object.keys(reusables).length > 0) metadata.reusables = reusables;
  else delete metadata.reusables;
  return { ...graph, metadata };
}

export function selectCastColumnsOf(
  node: PipelineCanvasNode,
): SelectCastColumn[] {
  const value = nodeDataOf(node).columns;
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (typeof item !== "object" || item === null) return [];
    const column = item as Record<string, unknown>;
    return [
      {
        source: asText(column.source) ?? asText(column.name) ?? "",
        name: asText(column.name) ?? asText(column.source) ?? "",
        type: (asText(column.type) ?? "VARCHAR").toUpperCase(),
      },
    ];
  });
}

export function normalizeJoinType(value: unknown): string {
  if (typeof value !== "string") return "inner";
  const normalized = value.trim().toLowerCase().replaceAll("_", " ");
  return normalized === "full" ? "full outer" : normalized || "inner";
}

export function nodeDatasetRef(node: PipelineCanvasNode): string | null {
  const data = nodeDataOf(node);
  const ref =
    data.datasetRef ??
    data.outputDatasetRef ??
    data.mediaSetRef ??
    data.indexRef ??
    data.virtualTableRef ??
    data.mappingRef;
  return typeof ref === "string" && ref.length > 0 ? ref : null;
}

export function withNodeConfigurationPatch(
  node: PipelineCanvasNode,
  patch: Record<string, unknown>,
): PipelineCanvasNode {
  if (node.isReadOnly) return node;
  const config = { ...(node.config ?? {}) };
  for (const [key, rawValue] of Object.entries(patch)) {
    const value = key === "joinType" ? normalizeJoinType(rawValue) : rawValue;
    if (value === undefined) delete config[key];
    else config[key] = value;
  }
  return {
    ...node,
    config,
    data: undefined,
    schema:
      Array.isArray(config.schema) && config.schema.length > 0
        ? configuredSchema(config.schema)
        : node.schema,
  };
}

export function parseValidation(
  payload: Record<string, unknown> | null | undefined,
): PipelineValidationResult | null {
  if (!payload || typeof payload.valid !== "boolean") return null;
  return {
    valid: payload.valid,
    errors: issueList(payload.errors),
    warnings: issueList(payload.warnings),
    fingerprint:
      typeof payload.fingerprint === "string" ? payload.fingerprint : "",
  };
}

export function issuesByNodeId(
  issues: readonly PipelineValidationIssue[],
): Record<string, PipelineValidationIssue[]> {
  const grouped: Record<string, PipelineValidationIssue[]> = {};
  for (const issue of issues) {
    const nodeId = issue.nodeId;
    if (typeof nodeId !== "string") continue;
    grouped[nodeId] = [...(grouped[nodeId] ?? []), issue];
  }
  return grouped;
}

export function graphPositions(
  graph: PipelineCanvasGraph,
): PositionsByNodeId {
  const raw = recordValue(graph.layout)?.positions;
  if (!raw || typeof raw !== "object") return {};
  const positions: PositionsByNodeId = {};
  for (const [nodeId, value] of Object.entries(
    raw as Record<string, unknown>,
  )) {
    if (
      typeof value === "object" &&
      value !== null &&
      typeof (value as { x?: unknown }).x === "number" &&
      typeof (value as { y?: unknown }).y === "number"
    ) {
      positions[nodeId] = {
        x: (value as { x: number }).x,
        y: (value as { y: number }).y,
      };
    }
  }
  return positions;
}

export function withLayoutPositions(
  graph: PipelineCanvasGraph,
  positions: PositionsByNodeId,
): PipelineCanvasGraph {
  const previousPositions =
    recordValue(recordValue(graph.layout)?.positions) ?? {};
  const activeNodeIds = new Set(graph.nodes.map((node) => node.id));
  const overlaidPositions = Object.fromEntries(
    Object.entries(positions)
      .filter(([nodeId]) => activeNodeIds.has(nodeId))
      .map(([nodeId, position]) => [
        nodeId,
        {
          ...cloneRecord(recordValue(previousPositions[nodeId]) ?? {}),
          x: position.x,
          y: position.y,
        },
      ]),
  );
  return {
    ...graph,
    layout: { ...(graph.layout ?? {}), positions: overlaidPositions },
  };
}

const COLUMN_GAP = 280;
const ROW_GAP = 130;

export function autoLayoutPositions(
  graph: PipelineCanvasGraph,
): PositionsByNodeId {
  const depthByNodeId = nodeDepths(graph);
  const rowsUsedByDepth: Record<number, number> = {};
  const positions: PositionsByNodeId = {};
  for (const node of graph.nodes) {
    const depth = depthByNodeId[node.id] ?? 0;
    const row = rowsUsedByDepth[depth] ?? 0;
    rowsUsedByDepth[depth] = row + 1;
    positions[node.id] = {
      x: 40 + depth * COLUMN_GAP,
      y: 40 + row * ROW_GAP + (depth % 2) * 32,
    };
  }
  return positions;
}

export function topologicalNodeIds(graph: PipelineCanvasGraph): string[] {
  const ids = graph.nodes.map((node) => node.id);
  const incomingCount: Record<string, number> = Object.fromEntries(
    ids.map((id) => [id, 0]),
  );
  const outgoing: Record<string, string[]> = Object.fromEntries(
    ids.map((id) => [id, []]),
  );
  for (const edge of graph.edges) {
    if (!(edge.source in outgoing) || !(edge.target in incomingCount)) continue;
    incomingCount[edge.target] += 1;
    outgoing[edge.source] = [...outgoing[edge.source], edge.target];
  }
  const queue = ids.filter((id) => incomingCount[id] === 0).sort();
  const ordered: string[] = [];
  while (queue.length > 0) {
    const current = queue.shift();
    if (current === undefined) break;
    ordered.push(current);
    for (const target of outgoing[current]) {
      incomingCount[target] -= 1;
      if (incomingCount[target] === 0) queue.push(target);
    }
  }
  return ordered.length === ids.length ? ordered : ids;
}

export function deriveNodeSchemas(
  graph: PipelineCanvasGraph,
): PipelineCanvasGraph {
  const schemaByNodeId: Record<string, PipelineSchemaColumn[]> = {};
  for (const nodeId of topologicalNodeIds(graph)) {
    const node = graph.nodes.find((candidate) => candidate.id === nodeId);
    if (!node) continue;
    if (node.isReadOnly) {
      schemaByNodeId[node.id] =
        node.schema ?? configuredSchema(nodeDataOf(node).schema);
      continue;
    }
    const inputSchemas = graph.edges
      .filter((edge) => edge.target === node.id)
      .sort((left, right) => joinHandleOrder(left) - joinHandleOrder(right))
      .map((edge) => schemaByNodeId[edge.source] ?? []);
    schemaByNodeId[node.id] = derivedSchemaFor(node, inputSchemas);
  }
  return {
    ...graph,
    nodes: graph.nodes.map((node) => {
      if (node.isReadOnly) return node;
      const schema = schemaByNodeId[node.id] ?? node.schema ?? [];
      return {
        ...node,
        schema,
        config: { ...(node.config ?? {}), schema },
      };
    }),
  };
}

export function nextNodeId(
  value: PipelineCanvasNodeType | string,
  existingIds: readonly string[],
): string {
  const stem = value
    .replace(/^(source|transform|bridge|output)\./, "")
    .replaceAll(".", "_")
    .replace(/[^A-Za-z0-9_]/g, "_");
  const taken = new Set(existingIds);
  for (let index = 1; index <= 999; index += 1) {
    const candidate = `${stem}_${index}`;
    if (!taken.has(candidate)) return candidate;
  }
  return `${stem}_${Date.now()}`;
}

export function createDatasetNode(
  id: string,
  datasetRef: string,
  schema: PipelineSchemaColumn[],
  descriptor?: PipelineNodeDescriptorPayload,
): PipelineCanvasNode {
  const label = datasetRef.split(/[/.]/).pop() ?? datasetRef;
  return {
    id,
    type: "dataset",
    kind: "source",
    descriptorId: "source.dataset",
    specVersion: descriptor?.specVersion ?? 1,
    descriptor,
    config: { datasetRef, label, schema },
    schema,
  };
}

export function createDescriptorNode(
  id: string,
  descriptor: PipelineNodeDescriptorPayload,
  pipelineId: string,
): PipelineCanvasNode | null {
  const type = canvasTypeForDescriptor(descriptor.descriptorId);
  if (!type) return null;
  const config = defaultDescriptorConfig(descriptor.descriptorId, id, pipelineId);
  return {
    id,
    type,
    kind: descriptor.kind,
    descriptorId: descriptor.descriptorId,
    specVersion: descriptor.specVersion,
    descriptor,
    config,
    schema: configuredSchema(config.schema),
  };
}

export function createGraphEdge(
  connection: PipelineConnection,
  existing: readonly PipelineCanvasEdge[],
): PipelineCanvasEdge {
  const sourcePort = connection.sourceHandle ?? "output";
  const targetPort = connection.targetHandle ?? "input";
  const prefix = `edge_${connection.source}_${sourcePort}__${connection.target}_${targetPort}`;
  const existingIds = new Set(existing.map((edge) => edge.id));
  let sequence = 1;
  while (existingIds.has(`${prefix}_${sequence}`)) sequence += 1;
  return {
    id: `${prefix}_${sequence}`,
    source: connection.source,
    target: connection.target,
    sourceHandle: connection.sourceHandle ?? null,
    targetHandle: connection.targetHandle ?? null,
  };
}

export type ConnectionValidation = {
  isValid: boolean;
  reason: string | null;
  connection: PipelineConnection;
};

export function validatePipelineConnection(
  graph: PipelineCanvasGraph,
  requested: PipelineConnection,
): ConnectionValidation {
  const source = graph.nodes.find((node) => node.id === requested.source);
  const target = graph.nodes.find((node) => node.id === requested.target);
  if (!source || !target) {
    return invalidConnection(requested, "연결할 노드를 찾을 수 없습니다.");
  }
  if (source.isReadOnly || target.isReadOnly) {
    return invalidConnection(
      requested,
      "읽기 전용 Graph v2 노드의 기존 연결은 보존되지만 새 연결은 만들 수 없습니다.",
    );
  }
  const sourcePort =
    requested.sourceHandle ?? firstOutputPortId(source) ?? "output";
  const targetPort =
    requested.targetHandle ?? firstInputPortId(target) ?? "input";
  const connection = {
    ...requested,
    sourceHandle: sourcePort,
    targetHandle: targetPort,
  };
  if (source.id === target.id) {
    return invalidConnection(connection, "노드는 자기 자신과 연결할 수 없습니다.");
  }
  const outputKind = outputArtifactKind(source, sourcePort);
  const acceptedKinds = acceptedArtifactKinds(target, targetPort);
  if (
    outputKind &&
    acceptedKinds.length > 0 &&
    !acceptedKinds.includes(outputKind)
  ) {
    return invalidConnection(
      connection,
      `${outputKind} artifact는 ${target.descriptorId}.${targetPort} 포트에 직접 연결할 수 없습니다.`,
    );
  }
  const targetCardinality = inputCardinality(target, targetPort);
  if (
    targetCardinality !== "many" &&
    graph.edges.some(
      (edge) =>
        edge.target === target.id &&
        (edge.targetHandle ?? firstInputPortId(target)) === targetPort,
    )
  ) {
    return invalidConnection(
      connection,
      `${targetPort} 포트에는 입력을 하나만 연결할 수 있습니다.`,
    );
  }
  const isDuplicate = graph.edges.some(
    (edge) =>
      edge.source === connection.source &&
      edge.target === connection.target &&
      (edge.sourceHandle ?? null) === connection.sourceHandle &&
      (edge.targetHandle ?? null) === connection.targetHandle,
  );
  if (isDuplicate) {
    return invalidConnection(connection, "이미 같은 named-port 연결이 있습니다.");
  }
  return { isValid: true, reason: null, connection };
}

export function nodeInputPorts(node: PipelineCanvasNode): string[] {
  const configured = descriptorPorts(node.descriptor, "inputPorts").map(
    (port) => port.portId,
  );
  return configured.length > 0
    ? configured
    : [
        ...(node.preservedInputPortIds ??
          LEGACY_PORTS[node.descriptorId]?.inputs ??
          []),
      ];
}

export function nodeOutputPorts(node: PipelineCanvasNode): string[] {
  const configured = descriptorPorts(node.descriptor, "outputPorts").map(
    (port) => port.portId,
  );
  return configured.length > 0
    ? configured
    : [
        ...(node.preservedOutputPortIds ??
          LEGACY_PORTS[node.descriptorId]?.outputs ??
          []),
      ];
}

export function canvasTypeForDescriptor(
  descriptorId: string,
): PipelineCanvasNodeType | null {
  return CANVAS_TYPE_BY_DESCRIPTOR[descriptorId] ?? null;
}

export function isOutputNode(node: PipelineCanvasNode): boolean {
  return node.kind === "output";
}

export function isReadOnlyPipelineNode(
  node: PipelineCanvasNode | null | undefined,
): boolean {
  return Boolean(node?.isReadOnly);
}

export function isDedicatedConfigurationNode(
  node: PipelineCanvasNode | null,
): boolean {
  return Boolean(
    node &&
      [
        "transform.document_extract",
        "transform.media",
        "transform.embedding.vision",
        "transform.use_llm",
        "transform.trained_model",
        "source.stream",
        "source.geospatial",
        "output.geospatial",
      ].includes(
        node.descriptorId,
      ),
  );
}

export const SQL_INPUT_PLACEHOLDER = "{{ inputs[0] }}";
export const DEFAULT_SQL_TEMPLATE = `SELECT * FROM ${SQL_INPUT_PLACEHOLDER}`;
export const DEFAULT_PYTHON_SOURCE =
  "def transform(**inputs):\n    first_input = next(iter(inputs.values()))\n    return first_input.read_rows()\n";

export function withSqlInputTemplate(
  graph: PipelineCanvasGraph,
  sourceId: string,
  targetId: string,
): PipelineCanvasGraph {
  const target = graph.nodes.find((node) => node.id === targetId);
  if (!target || target.type !== "sql") return graph;
  const sql = nodeDataOf(target).sql;
  if (typeof sql !== "string" || !sql.includes(SQL_INPUT_PLACEHOLDER))
    return graph;
  const source = graph.nodes.find((node) => node.id === sourceId);
  const sourceRef = source ? nodeDatasetRef(source) : null;
  if (!sourceRef) return graph;
  const inputMacro = `{{ input('${sourceRef}') }}`;
  const columnNames = (source?.schema ?? []).map((column) => column.name);
  const nextSql =
    sql === DEFAULT_SQL_TEMPLATE && columnNames.length > 0
      ? `SELECT ${columnNames.join(", ")} FROM ${inputMacro}`
      : sql.replace(SQL_INPUT_PLACEHOLDER, inputMacro);
  return {
    ...graph,
    nodes: graph.nodes.map((node) =>
      node.id === targetId
        ? { ...node, config: { ...(node.config ?? {}), sql: nextSql } }
        : node,
    ),
  };
}

export function shortFingerprint(
  fingerprint: string | null | undefined,
): string {
  return fingerprint ? fingerprint.slice(0, 10) : "-";
}

export function asGraph(value: unknown): PersistedPipelineGraph | null {
  if (typeof value !== "object" || value === null) return null;
  const graph = value as { nodes?: unknown; edges?: unknown };
  return Array.isArray(graph.nodes) && Array.isArray(graph.edges)
    ? (value as PersistedPipelineGraph)
    : null;
}

export type GraphNodeChangeKind = "added" | "removed" | "modified";

export type GraphNodeChange = {
  kind: GraphNodeChangeKind;
  nodeId: string;
  nodeType: string;
  label: string;
  schemaBefore: PipelineSchemaColumn[];
  schemaAfter: PipelineSchemaColumn[];
};

export function diffGraphNodes(
  base: PipelineCanvasGraph,
  next: PipelineCanvasGraph,
): GraphNodeChange[] {
  const baseById = new Map(base.nodes.map((node) => [node.id, node]));
  const nextIds = new Set(next.nodes.map((node) => node.id));
  const changes: GraphNodeChange[] = [];
  for (const node of next.nodes) {
    const before = baseById.get(node.id);
    if (!before) changes.push(nodeChange("added", null, node));
    else if (stableStringify(before) !== stableStringify(node)) {
      changes.push(nodeChange("modified", before, node));
    }
  }
  for (const node of base.nodes) {
    if (!nextIds.has(node.id)) changes.push(nodeChange("removed", node, null));
  }
  return changes;
}

export type SchemaColumnDiffRow = {
  name: string;
  before: string | null;
  after: string | null;
};

export function diffSchemaColumns(
  before: readonly PipelineSchemaColumn[],
  after: readonly PipelineSchemaColumn[],
): SchemaColumnDiffRow[] {
  const beforeByName = new Map(
    before.map((column) => [column.name, column.type]),
  );
  const afterByName = new Map(
    after.map((column) => [column.name, column.type]),
  );
  const names = [
    ...before.map((column) => column.name),
    ...after
      .filter((column) => !beforeByName.has(column.name))
      .map((column) => column.name),
  ];
  return names.map((name) => ({
    name,
    before: beforeByName.get(name) ?? null,
    after: afterByName.get(name) ?? null,
  }));
}

export type BranchDiffSection = {
  changed: boolean;
  baseFingerprint: string;
  graphFingerprint: string;
};

export type BranchDiffSummary = {
  baseVersionId: string | null;
  latestVersionId: string | null;
  baseStale: boolean;
  graph: BranchDiffSection | null;
};

export function parseBranchDiff(
  payload: Record<string, unknown> | null | undefined,
): BranchDiffSummary | null {
  if (!payload) return null;
  return {
    baseVersionId: asText(payload.baseVersionId),
    latestVersionId: asText(payload.latestVersionId),
    baseStale: payload.baseStale === true,
    graph: diffSection(payload.graph),
  };
}

export function scheduleLabel(value: unknown): string {
  if (typeof value !== "object" || value === null) return "수동 실행";
  const schedule = value as Record<string, unknown>;
  const kind =
    asText(schedule.triggerType) ?? asText(schedule.type) ?? asText(schedule.kind);
  const timezone = asText(schedule.timezone) ?? "UTC";
  if (kind === "cron") {
    const expression =
      asText(schedule.cronExpression) ??
      asText(schedule.cron) ??
      asText(schedule.expression) ??
      "-";
    return `Cron · ${expression} · ${timezone}`;
  }
  if (kind === "interval") {
    const seconds =
      schedule.intervalSeconds ??
      schedule.everySeconds ??
      schedule.seconds ??
      (typeof schedule.intervalMinutes === "number"
        ? schedule.intervalMinutes * 60
        : "-");
    return `간격 · ${String(seconds)}초 · ${timezone}`;
  }
  return "수동 실행";
}

export function formatTimestamp(value: unknown): string {
  if (typeof value !== "string" || !value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("ko-KR");
}

export function asText(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function normalizeV1Graph(
  graph: PipelineGraph,
  descriptorById: ReadonlyMap<string, PipelineNodeDescriptorPayload>,
): PipelineCanvasGraph {
  const nodes = graph.nodes.map((node) => {
    const descriptorId = DESCRIPTOR_BY_LEGACY_TYPE[node.type];
    const descriptor = descriptorById.get(descriptorId);
    const kind =
      descriptor?.kind ??
      (node.type === "dataset"
        ? "source"
        : node.type === "output_dataset"
          ? "output"
          : "transform");
    const config = canonicalLegacyNodeConfig(node);
    const schema = node.schema ?? configuredSchema(config.schema);
    return {
      ...node,
      type: node.type,
      kind,
      descriptorId,
      specVersion: descriptor?.specVersion ?? 1,
      descriptor,
      config: { ...config, schema },
      data: undefined,
      schema,
    } satisfies PipelineCanvasNode;
  });
  const normalized = normalizeJoinEdges({
    schemaVersion: 2,
    nodes,
    edges: graph.edges.map((edge) => ({ ...edge })),
    layout: graph.layout,
    outputContract: graph.outputContract,
    tests: graph.tests,
    schedule: graph.schedule,
  });
  return attachDefaultEdgePorts(normalized);
}

function normalizeV2Graph(
  raw: Record<string, unknown>,
  descriptorByKey: ReadonlyMap<string, PipelineNodeDescriptorPayload>,
): PipelineCanvasGraph {
  const edges = objectRows(raw.edges).flatMap((edge) => {
    const source = asText(edge.sourceNodeId);
    const target = asText(edge.targetNodeId);
    if (!source || !target) return [];
    return [
      {
        id: asText(edge.id) ?? undefined,
        source,
        target,
        sourceHandle: asText(edge.sourcePortId),
        targetHandle: asText(edge.targetPortId),
        rawV2Edge: cloneRecord(edge),
      },
    ];
  });
  const nodes = objectRows(raw.nodes).flatMap((node) => {
    const descriptorId = asText(node.descriptorId);
    const id = asText(node.id);
    const kind = node.kind;
    if (
      !descriptorId ||
      !id ||
      !["source", "transform", "output"].includes(String(kind))
    ) {
      return [];
    }
    const specVersion =
      typeof node.specVersion === "number" ? node.specVersion : 1;
    const descriptor = descriptorByKey.get(
      descriptorKey(descriptorId, specVersion),
    );
    const compatibility = editorCompatibility(
      descriptorId,
      specVersion,
      String(kind),
      descriptor,
    );
    const config = recordValue(node.config) ?? {};
    return [
      {
        id,
        type: compatibility.canvasType ?? "unknown_v2",
        kind: kind as PipelineCanvasNode["kind"],
        descriptorId,
        specVersion,
        descriptor,
        config: cloneRecord(config),
        schema: configuredSchema(config.schema),
        isReadOnly: compatibility.canvasType === null,
        readOnlyReason: compatibility.readOnlyReason ?? undefined,
        preservedInputPortIds: connectedPortIds(edges, id, "input"),
        preservedOutputPortIds: connectedPortIds(edges, id, "output"),
        rawV2Node: cloneRecord(node),
      },
    ];
  });
  return {
    schemaVersion: 2,
    nodes,
    edges,
    layout: cloneRecord(recordValue(raw.layout) ?? {}),
    outputContract:
      (cloneRecord(
        recordValue(raw.outputContract) ?? { columns: [] },
      ) as PipelineGraph["outputContract"]) ?? { columns: [] },
    tests: Array.isArray(raw.tests)
      ? (cloneJsonValue(raw.tests) as PipelineGraph["tests"])
      : [],
    schedule: recordValue(raw.schedule)
      ? cloneRecord(recordValue(raw.schedule) ?? {})
      : null,
    metadata: recordValue(raw.metadata)
      ? cloneRecord(recordValue(raw.metadata) ?? {})
      : undefined,
    rawV2Graph: cloneRecord(raw),
  };
}

function canonicalLegacyNodeConfig(node: PipelineNode): Record<string, unknown> {
  const merged = { ...(node.config ?? {}), ...(node.data ?? {}) };
  for (const field of EXECUTION_FIELDS_BY_NODE_TYPE[node.type]) {
    if (merged[field] !== undefined) continue;
    const value = nodeDataOf(node as PipelineCanvasNode)[field];
    if (value !== undefined) merged[field] = value;
  }
  if (node.schema) merged.schema = node.schema;
  if (node.type === "join") merged.joinType = normalizeJoinType(merged.joinType);
  return merged;
}

function canonicalizeNodeForSave(
  node: PipelineCanvasNode,
  edges: readonly PipelineCanvasEdge[],
): PipelineGraphV2Node {
  const config = { ...(node.config ?? {}) };
  if (node.isReadOnly && node.rawV2Node) {
    return cloneRecord(node.rawV2Node) as PipelineGraphV2Node;
  }
  if (node.schema) config.schema = node.schema;
  if (node.type === "join") {
    config.joinType = normalizeJoinType(config.joinType);
    const incoming = edges.filter((edge) => edge.target === node.id);
    const left = incoming.find((edge) => edge.targetHandle === "left");
    const right = incoming.find((edge) => edge.targetHandle === "right");
    if (left) config.leftNodeId = left.source;
    if (right) config.rightNodeId = right.source;
  }
  return {
    ...cloneRecord(node.rawV2Node ?? {}),
    id: node.id,
    kind: node.kind,
    descriptorId: node.descriptorId,
    specVersion: node.specVersion,
    config: cloneRecord(config),
  };
}

function canonicalizeEdgeForSave(
  edge: PipelineCanvasEdge,
  nodes: readonly PipelineCanvasNode[],
): PipelineGraphV2Edge {
  const source = nodes.find((node) => node.id === edge.source);
  const target = nodes.find((node) => node.id === edge.target);
  if (
    edge.rawV2Edge &&
    (isReadOnlyPipelineNode(source) || isReadOnlyPipelineNode(target))
  ) {
    return cloneRecord(edge.rawV2Edge) as PipelineGraphV2Edge;
  }
  const sourcePortId =
    edge.sourceHandle ?? (source ? firstOutputPortId(source) : null) ?? "output";
  const targetPortId =
    edge.targetHandle ?? (target ? firstInputPortId(target) : null) ?? "input";
  return {
    ...cloneRecord(edge.rawV2Edge ?? {}),
    id:
      edge.id ??
      `edge_${edge.source}_${sourcePortId}__${edge.target}_${targetPortId}`,
    sourceNodeId: edge.source,
    sourcePortId,
    targetNodeId: edge.target,
    targetPortId,
  };
}

function attachDefaultEdgePorts(
  graph: PipelineCanvasGraph,
): PipelineCanvasGraph {
  const nodes = new Map(graph.nodes.map((node) => [node.id, node]));
  return {
    ...graph,
    edges: graph.edges.map((edge) => {
      const source = nodes.get(edge.source);
      const target = nodes.get(edge.target);
      return {
        ...edge,
        sourceHandle:
          edge.sourceHandle ?? (source ? firstOutputPortId(source) : null),
        targetHandle:
          edge.targetHandle ?? (target ? firstInputPortId(target) : null),
      };
    }),
  };
}

function normalizeJoinEdges(
  graph: PipelineCanvasGraph,
): PipelineCanvasGraph {
  const edges = [...graph.edges];
  for (const node of graph.nodes.filter((item) => item.type === "join")) {
    normalizeJoinNodeEdges(node, edges);
  }
  return { ...graph, edges };
}

function normalizeJoinNodeEdges(
  node: PipelineCanvasNode,
  edges: PipelineCanvasEdge[],
): void {
  const indexes = edges.flatMap((edge, index) =>
    edge.target === node.id ? [index] : [],
  );
  if (indexes.length === 0) return;
  const data = nodeDataOf(node);
  const leftIndex = joinRoleEdgeIndex(edges, indexes, "left", data.leftNodeId);
  const rightIndex = joinRoleEdgeIndex(
    edges,
    indexes.filter((index) => index !== leftIndex),
    "right",
    data.rightNodeId,
  );
  const remaining = indexes.filter(
    (index) => index !== leftIndex && index !== rightIndex,
  );
  const fallbackLeft =
    leftIndex ?? (rightIndex === indexes[0] ? remaining[0] : indexes[0]);
  const fallbackRight =
    rightIndex ?? remaining.find((index) => index !== fallbackLeft);
  if (fallbackLeft !== undefined) {
    edges[fallbackLeft] = { ...edges[fallbackLeft], targetHandle: "left" };
  }
  if (fallbackRight !== undefined) {
    edges[fallbackRight] = { ...edges[fallbackRight], targetHandle: "right" };
  }
}

function joinRoleEdgeIndex(
  edges: readonly PipelineCanvasEdge[],
  indexes: readonly number[],
  role: "left" | "right",
  configuredSource: unknown,
): number | undefined {
  const explicit = indexes.find((index) => edges[index].targetHandle === role);
  if (explicit !== undefined) return explicit;
  if (typeof configuredSource !== "string") return undefined;
  return indexes.find((index) => edges[index].source === configuredSource);
}

function orderNamedJoinInputEdges(
  nodes: readonly PipelineGraphV2Node[],
  edges: readonly PipelineGraphV2Edge[],
): PipelineGraphV2Edge[] {
  const joinIds = new Set(
    nodes
      .filter((node) => node.descriptorId === "transform.join")
      .map((node) => node.id),
  );
  return [...edges].sort((left, right) => {
    if (
      left.targetNodeId !== right.targetNodeId ||
      !joinIds.has(left.targetNodeId)
    ) {
      return 0;
    }
    return namedJoinPortOrder(left.targetPortId) -
      namedJoinPortOrder(right.targetPortId);
  });
}

function joinHandleOrder(edge: PipelineCanvasEdge): number {
  return namedJoinPortOrder(edge.targetHandle);
}

function namedJoinPortOrder(port: string | null | undefined): number {
  if (port === "left") return 0;
  if (port === "right") return 1;
  return 2;
}

function nodeDepths(graph: PipelineCanvasGraph): Record<string, number> {
  const depths: Record<string, number> = {};
  for (const nodeId of topologicalNodeIds(graph)) {
    const incoming = graph.edges.filter((edge) => edge.target === nodeId);
    depths[nodeId] = incoming.reduce(
      (max, edge) => Math.max(max, (depths[edge.source] ?? 0) + 1),
      0,
    );
  }
  return depths;
}

function derivedSchemaFor(
  node: PipelineCanvasNode,
  inputSchemas: readonly PipelineSchemaColumn[][],
): PipelineSchemaColumn[] {
  const configured = configuredSchema(nodeDataOf(node).schema);
  if (node.type === "dataset") return configured;
  if (node.type === "join") return mergeColumns(inputSchemas);
  if (node.type === "select_cast") {
    const columns = selectCastColumnsOf(node);
    return columns.length > 0
      ? columns.map((column) => ({
          name: column.name,
          type: column.type,
          nullable: sourceColumnNullable(inputSchemas[0] ?? [], column.source),
        }))
      : (inputSchemas[0] ?? []);
  }
  if (node.type === "media_to_table_rows") return MEDIA_REFERENCE_SCHEMA;
  if (node.type === "content_units_to_dataset")
    return CONTENT_UNIT_DATASET_SCHEMA;
  if (node.type === "use_llm") {
    const outputColumn =
      asText(nodeDataOf(node).outputColumn) ?? "interpretation";
    const outputType =
      asText(recordValue(nodeDataOf(node).outputSchema)?.type) ?? "object";
    return mergeColumns([
      inputSchemas[0] ?? [],
      [{ name: outputColumn, type: outputType, nullable: true }],
    ]);
  }
  if (["sql", "python"].includes(node.type) && configured.length > 0) {
    return configured;
  }
  return inputSchemas[0] ?? configured;
}

function sourceColumnNullable(
  schema: readonly PipelineSchemaColumn[],
  source: string,
): boolean | undefined {
  return schema.find((column) => column.name === source)?.nullable;
}

function mergeColumns(
  inputSchemas: readonly PipelineSchemaColumn[][],
): PipelineSchemaColumn[] {
  const merged: PipelineSchemaColumn[] = [];
  const seen = new Set<string>();
  for (const schema of inputSchemas) {
    for (const column of schema) {
      if (seen.has(column.name)) continue;
      seen.add(column.name);
      merged.push(column);
    }
  }
  return merged;
}

function defaultDescriptorConfig(
  descriptorId: string,
  nodeId: string,
  pipelineId: string,
): Record<string, unknown> {
  const slug = pipelineId.replace(/[^A-Za-z0-9_]/g, "_");
  const outputRef = `pipelines.${slug}_${nodeId}`;
  const shared = { label: descriptorLabel(descriptorId) };
  const defaults: Record<string, Record<string, unknown>> = {
    "transform.sql": { outputDatasetRef: outputRef, sql: DEFAULT_SQL_TEMPLATE },
    "transform.python": {
      outputDatasetRef: outputRef,
      sourceCode: DEFAULT_PYTHON_SOURCE,
      functionName: "transform",
    },
    "transform.join": {
      outputDatasetRef: outputRef,
      leftKey: "",
      rightKey: "",
      joinType: "inner",
    },
    "transform.union": { outputDatasetRef: outputRef },
    "transform.select_cast": { outputDatasetRef: outputRef, columns: [] },
    "source.media_set": {
      mediaSetRef: "media.pipeline_input",
      mediaItemVersionIds: [],
    },
    "source.stream": { sourceRef: "streaming_sync_name" },
    "source.geospatial": {
      resourceRef: "geo.source_dataset",
      geometryField: "geometry",
      timeField: "event_time",
    },
    "transform.media": {
      processorId: "image_v1@1",
      parameters: {},
    },
    "transform.document_extract": {
      processorId: "pdf_text_v1@1",
      profileName: "document-default@1",
      extractionStrategy: "raw",
      outputFormat: "markdown",
      promptMode: "none",
      parameters: { pageSelection: { start: 1, limit: 3 } },
    },
    "transform.chunk": { chunkSize: 500, overlap: 50 },
    "transform.embedding.text": { modelRef: "bge-small-en-v1.5" },
    "transform.embedding.vision": { modelRef: "clip-ViT-B-32" },
    "transform.use_llm": defaultUseLlmConfig(),
    "transform.trained_model": {
      modelRef: "demo.transaction-risk",
      modelBranch: "master",
      fallbackBranches: ["master"],
      inputMappings: {
        amount: "$amount",
        country: "$country",
      },
      outputMappings: {
        riskScore: "risk_score",
        decision: "risk_decision",
      },
    },
    "output.dataset": { outputDatasetRef: outputRef },
    "output.media_set": { mediaSetRef: `media.${slug}_${nodeId}` },
    "output.virtual_table": {
      virtualTableRef: `virtual.${slug}_${nodeId}`,
    },
    "output.semantic_index": { indexRef: `search.${slug}_${nodeId}` },
    "output.ontology": { mappingRef: `ontology.${slug}_${nodeId}` },
    "output.geospatial": {
      resourceRef: `geo.${slug}_${nodeId}`,
      geometryField: "geometry",
      timeField: "event_time",
    },
  };
  return { ...shared, ...(defaults[descriptorId] ?? {}) };
}

function defaultUseLlmConfig(): Record<string, unknown> {
  return {
    templateId: "empty_prompt",
    modelAlias: "default-completion",
    promptVersionId: "draft@1",
    promptMode: "text",
    promptTemplate: "Interpret {{text}}.",
    systemPrompt: "",
    inputFields: ["text"],
    outputColumn: "interpretation",
    outputSchema: {
      type: "object",
      properties: { result: { type: "string" } },
      required: ["result"],
    },
    dataClassification: "public",
    outputMode: "simple",
    skipRecomputingRows: true,
    cacheGeneration: 1,
    modelParameters: {
      temperature: 0,
      maxOutputTokens: 500,
      thinkingMode: "disabled",
    },
    environment: "prod",
    trialCount: 3,
    cachePolicy: "referenced_fields",
  };
}

function firstInputPortId(node: PipelineCanvasNode): string | null {
  return nodeInputPorts(node)[0] ?? null;
}

function firstOutputPortId(node: PipelineCanvasNode): string | null {
  return nodeOutputPorts(node)[0] ?? null;
}

export function outputArtifactKind(
  node: PipelineCanvasNode,
  portId: string,
): string | null {
  const port = descriptorPorts(node.descriptor, "outputPorts").find(
    (candidate) => candidate.portId === portId,
  );
  if (port?.artifactKind) return port.artifactKind;
  const fallback: Record<string, string> = {
    "source.dataset": "dataset_version",
    "source.media_set": "media_set_selection",
    "transform.media": "media_derivative_set",
    "transform.document_extract": "content_unit_set",
    "transform.chunk": "content_unit_set",
    "transform.embedding.text": "vector_index_generation",
    "transform.embedding.vision": "vector_index_generation",
    "bridge.media_to_table_rows": "dataset_version",
    "bridge.content_units_to_dataset": "dataset_version",
    "transform.use_llm": "dataset_version",
    "transform.trained_model": "dataset_version",
    "output.dataset": "dataset_version",
    "output.media_set": "media_set_selection",
    "output.virtual_table": "virtual_table",
    "output.semantic_index": "vector_index_generation",
    "output.ontology": "ontology_mapping",
  };
  return fallback[node.descriptorId] ?? "dataset_version";
}

function acceptedArtifactKinds(
  node: PipelineCanvasNode,
  portId: string,
): string[] {
  const port = descriptorPorts(node.descriptor, "inputPorts").find(
    (candidate) => candidate.portId === portId,
  );
  if (port?.acceptedArtifactKinds?.length) return port.acceptedArtifactKinds;
  const fallback: Record<string, string[]> = {
    "transform.document_extract": [
      "media_set_selection",
      "media_derivative_set",
    ],
    "transform.media": [
      "media_set_selection",
      "media_derivative_set",
    ],
    "transform.chunk": ["content_unit_set"],
    "transform.embedding.text": ["content_unit_set"],
    "transform.embedding.vision": [
      "media_set_selection",
      "media_derivative_set",
    ],
    "bridge.media_to_table_rows": [
      "media_set_selection",
      "media_derivative_set",
    ],
    "bridge.content_units_to_dataset": ["content_unit_set"],
    "transform.use_llm": ["dataset_version"],
    "transform.trained_model": ["dataset_version"],
    "output.dataset": ["dataset_version"],
    "output.media_set": [
      "media_set_selection",
      "media_derivative_set",
    ],
    "output.virtual_table": ["dataset_version", "virtual_table"],
    "output.semantic_index": ["vector_index_generation"],
    "output.ontology": [
      "dataset_version",
      "content_unit_set",
      "media_set_selection",
    ],
  };
  return fallback[node.descriptorId] ?? ["dataset_version"];
}

function inputCardinality(
  node: PipelineCanvasNode,
  portId: string,
): string {
  const port = descriptorPorts(node.descriptor, "inputPorts").find(
    (candidate) => candidate.portId === portId,
  );
  return port?.cardinality ?? "one";
}

type DescriptorPort = {
  portId: string;
  artifactKind: string | null;
  acceptedArtifactKinds: string[];
  cardinality: string;
};

function descriptorPorts(
  descriptor: PipelineNodeDescriptorPayload | undefined,
  key: "inputPorts" | "outputPorts",
): DescriptorPort[] {
  if (!descriptor) return [];
  const value = (descriptor as Record<string, unknown>)[key];
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    const record = recordValue(item);
    const portId = asText(record?.portId);
    if (!record || !portId) return [];
    return [
      {
        portId,
        artifactKind: asText(record.artifactKind),
        acceptedArtifactKinds: Array.isArray(record.acceptedArtifactKinds)
          ? record.acceptedArtifactKinds.filter(
              (kind): kind is string => typeof kind === "string",
            )
          : [],
        cardinality: asText(record.cardinality) ?? "one",
      },
    ];
  });
}

function invalidConnection(
  connection: PipelineConnection,
  reason: string,
): ConnectionValidation {
  return { isValid: false, reason, connection };
}

function connectedPortIds(
  edges: readonly PipelineCanvasEdge[],
  nodeId: string,
  direction: "input" | "output",
): string[] {
  const values = edges.flatMap((edge) => {
    if (direction === "input" && edge.target === nodeId) {
      return edge.targetHandle ? [edge.targetHandle] : [];
    }
    if (direction === "output" && edge.source === nodeId) {
      return edge.sourceHandle ? [edge.sourceHandle] : [];
    }
    return [];
  });
  return [...new Set(values)];
}

function issueList(value: unknown): PipelineValidationIssue[] {
  if (!Array.isArray(value)) return [];
  return value.filter(
    (item): item is PipelineValidationIssue =>
      typeof item === "object" &&
      item !== null &&
      typeof (item as { code?: unknown }).code === "string",
  );
}

function objectRows(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    const record = recordValue(item);
    return record ? [record] : [];
  });
}

function recordValue(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function currentDescriptorsById(
  descriptors: readonly PipelineNodeDescriptorPayload[],
): Map<string, PipelineNodeDescriptorPayload> {
  const result = new Map<string, PipelineNodeDescriptorPayload>();
  for (const descriptor of descriptors) {
    const current = result.get(descriptor.descriptorId);
    if (!current || descriptor.specVersion > current.specVersion) {
      result.set(descriptor.descriptorId, descriptor);
    }
  }
  return result;
}

function descriptorKey(descriptorId: string, specVersion: number): string {
  return `${descriptorId}@${specVersion}`;
}

function editorCompatibility(
  descriptorId: string,
  specVersion: number,
  kind: string,
  descriptor: PipelineNodeDescriptorPayload | undefined,
): {
  canvasType: PipelineCanvasNodeType | null;
  readOnlyReason: PipelineReadOnlyReason | null;
} {
  const canvasType = canvasTypeForDescriptor(descriptorId);
  if (!canvasType) {
    return { canvasType: null, readOnlyReason: "unknown_descriptor" };
  }
  const supportedVersions =
    SUPPORTED_SPEC_VERSIONS_BY_DESCRIPTOR[descriptorId] ?? [];
  if (!supportedVersions.includes(specVersion)) {
    return {
      canvasType: null,
      readOnlyReason: "unsupported_spec_version",
    };
  }
  if (!descriptor) {
    return {
      canvasType: null,
      readOnlyReason: "descriptor_contract_missing",
    };
  }
  if (descriptor.kind !== kind) {
    return {
      canvasType: null,
      readOnlyReason: "descriptor_kind_mismatch",
    };
  }
  return { canvasType, readOnlyReason: null };
}

function cloneRecord(value: Record<string, unknown>): Record<string, unknown> {
  return cloneJsonValue(value) as Record<string, unknown>;
}

function cloneJsonValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(cloneJsonValue);
  if (typeof value !== "object" || value === null) return value;
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>).map(([key, item]) => [
      key,
      cloneJsonValue(item),
    ]),
  );
}

function configuredSchema(value: unknown): PipelineSchemaColumn[] {
  if (!Array.isArray(value)) return [];
  return value.filter(
    (column): column is PipelineSchemaColumn =>
      typeof column === "object" &&
      column !== null &&
      typeof (column as { name?: unknown }).name === "string" &&
      typeof (column as { type?: unknown }).type === "string",
  );
}

function nodeChange(
  kind: GraphNodeChangeKind,
  before: PipelineCanvasNode | null,
  after: PipelineCanvasNode | null,
): GraphNodeChange {
  const node = after ?? before;
  return {
    kind,
    nodeId: node?.id ?? "",
    nodeType: node?.descriptorId ?? "",
    label: node ? nodeLabel(node) : "",
    schemaBefore: before?.schema ?? [],
    schemaAfter: after?.schema ?? [],
  };
}

function stableStringify(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map(stableStringify).join(",")}]`;
  }
  if (typeof value === "object" && value !== null) {
    const entries = Object.entries(value as Record<string, unknown>)
      .filter(
        ([key]) =>
          ![
            "descriptor",
            "rawV2Node",
            "rawV2Edge",
            "rawV2Graph",
          ].includes(key),
      )
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => `${JSON.stringify(key)}:${stableStringify(item)}`);
    return `{${entries.join(",")}}`;
  }
  return JSON.stringify(value) ?? "null";
}

function diffSection(value: unknown): BranchDiffSection | null {
  const section = recordValue(value);
  if (!section || typeof section.changed !== "boolean") return null;
  return {
    changed: section.changed,
    baseFingerprint: asText(section.baseFingerprint) ?? "",
    graphFingerprint: asText(section.graphFingerprint) ?? "",
  };
}

function descriptorLabel(descriptorId: string): string {
  const labels: Record<string, string> = {
    "source.dataset": "Dataset source",
    "source.media_set": "Media Set source",
    "transform.media": "Transform media",
    "transform.sql": "SQL",
    "transform.python": "Python",
    "transform.join": "Join",
    "transform.union": "Union",
    "transform.select_cast": "Select / Cast",
    "transform.document_extract": "Document extract",
    "transform.chunk": "Chunk content",
    "transform.embedding.text": "Text embedding",
    "transform.embedding.vision": "Vision embedding",
    "transform.use_llm": "Use LLM",
    "transform.trained_model": "Trained Model",
    "bridge.media_to_table_rows": "Media → Table rows",
    "bridge.content_units_to_dataset": "Content Units → Dataset",
    "output.dataset": "Dataset output",
    "output.media_set": "Media Set output",
    "output.virtual_table": "Virtual Table output",
    "output.semantic_index": "Semantic index output",
    "output.ontology": "Ontology mapping output",
  };
  return labels[descriptorId] ?? descriptorId;
}

function nodeMeta(
  label: string,
  shortLabel: string,
  headerClassName: string,
  iconCellClassName: string,
  iconClassName: string,
  isSolidHeader: boolean,
): NodeTypeMeta {
  return {
    label,
    shortLabel,
    headerClassName,
    iconCellClassName,
    iconClassName,
    isSolidHeader,
  };
}

function transformMeta(label: string, shortLabel: string): NodeTypeMeta {
  return nodeMeta(
    label,
    shortLabel,
    "bg-[#83C485] text-white",
    "bg-[#EDF1F7]",
    "text-[#4C90F0]",
    true,
  );
}

function bridgeMeta(label: string, shortLabel: string): NodeTypeMeta {
  return nodeMeta(
    label,
    shortLabel,
    "bg-[#3F9F96] text-white",
    "bg-[#E7F4F2]",
    "text-[#147D75]",
    true,
  );
}

function outputMeta(label: string, shortLabel: string): NodeTypeMeta {
  return nodeMeta(
    label,
    shortLabel,
    "bg-[#147DB3] text-white",
    "bg-white",
    "text-[#147DB3]",
    true,
  );
}

const MEDIA_REFERENCE_SCHEMA: PipelineSchemaColumn[] = [
  { name: "mediaReference", type: "object", nullable: false },
  { name: "mediaItemVersionId", type: "string", nullable: false },
  { name: "securityEnvelope", type: "object", nullable: false },
];

const CONTENT_UNIT_DATASET_SCHEMA: PipelineSchemaColumn[] = [
  { name: "sourceMediaItemVersionId", type: "string", nullable: false },
  { name: "unitKind", type: "string", nullable: false },
  { name: "text", type: "string", nullable: true },
  { name: "structure", type: "object", nullable: true },
  { name: "sourceLocator", type: "object", nullable: false },
  { name: "securityEnvelope", type: "object", nullable: false },
];
