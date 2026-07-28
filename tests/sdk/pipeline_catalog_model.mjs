import assert from "node:assert/strict";

import {
  descriptorRuntimeState,
  descriptorState,
} from "../../apps/foundry/src/features/pipelines/pipeline-catalog-capability-model.ts";
import {
  canonicalizeGraphForSave,
  createDescriptorNode,
  createGraphEdge,
  emptyGraphDoc,
  importedTrainedModelRefs,
  nodeInputPorts,
  nodeOutputPorts,
  validatePipelineConnection,
  withImportedTrainedModel,
  withNodeConfigurationPatch,
} from "../../apps/foundry/src/features/pipelines/pipeline-model.ts";

const graphWithoutReusable = emptyGraphDoc();
assert.deepEqual(importedTrainedModelRefs(graphWithoutReusable), []);
const graphWithReusable = withImportedTrainedModel(
  graphWithoutReusable,
  "demo.transaction-risk",
);
assert.deepEqual(importedTrainedModelRefs(graphWithReusable), [
  "demo.transaction-risk",
]);
assert.deepEqual(
  withImportedTrainedModel(graphWithReusable, "demo.transaction-risk")
    .metadata,
  graphWithReusable.metadata,
  "repeated reusable import must be idempotent",
);

function descriptor({
  descriptorId,
  kind = "transform",
  availability = "graph_v2_executable",
  runtimeCapability = "graph_v2_executable",
  inputPorts = [],
  outputPorts = [],
  configFields = [],
}) {
  return {
    descriptorId,
    specVersion: 1,
    kind,
    availability,
    runtimeCapability,
    inputPorts,
    outputPorts,
    configFields,
  };
}

function input(portId, ...acceptedArtifactKinds) {
  return {
    portId,
    acceptedArtifactKinds,
    cardinality: "one",
    required: true,
  };
}

function output(portId, artifactKind) {
  return { portId, artifactKind };
}

const mediaSource = descriptor({
  descriptorId: "source.media_set",
  kind: "source",
  runtimeCapability: "media_pipeline_runtime",
  outputPorts: [output("media", "media_set_selection")],
});
const documentExtract = descriptor({
  descriptorId: "transform.document_extract",
  runtimeCapability: "media_processor_registry",
  inputPorts: [
    input("media", "media_set_selection", "media_derivative_set"),
  ],
  outputPorts: [output("content", "content_unit_set")],
});
const chunk = descriptor({
  descriptorId: "transform.chunk",
  runtimeCapability: "content_pipeline_runtime",
  inputPorts: [input("content", "content_unit_set")],
  outputPorts: [output("content", "content_unit_set")],
});
const contentBridge = descriptor({
  descriptorId: "bridge.content_units_to_dataset",
  runtimeCapability: "multimodal_bridge_runtime",
  inputPorts: [input("content", "content_unit_set")],
  outputPorts: [output("dataset", "dataset_version")],
});
const mediaBridge = descriptor({
  descriptorId: "bridge.media_to_table_rows",
  runtimeCapability: "multimodal_bridge_runtime",
  inputPorts: [
    input("media", "media_set_selection", "media_derivative_set"),
  ],
  outputPorts: [output("dataset", "dataset_version")],
});
const useLlm = descriptor({
  descriptorId: "transform.use_llm",
  runtimeCapability: "governed_model_gateway_runtime",
  inputPorts: [input("input", "dataset_version")],
  outputPorts: [output("dataset", "dataset_version")],
});
const datasetOutput = descriptor({
  descriptorId: "output.dataset",
  kind: "output",
  availability: "legacy_executable",
  runtimeCapability: "tabular_v1_compiler",
  inputPorts: [input("input", "dataset_version")],
  outputPorts: [output("dataset", "dataset_version")],
});

const authorableDescriptors = [
  mediaSource,
  documentExtract,
  chunk,
  contentBridge,
  mediaBridge,
  useLlm,
  datasetOutput,
];
for (const item of authorableDescriptors) {
  assert.equal(
    descriptorState(item, false).isAddable,
    true,
    `${item.descriptorId} should be authorable`,
  );
}

const runtimeAlias = {
  ...mediaSource,
  availability: "validation_only",
  runtimeCapability: "graph_v2_executable",
};
assert.equal(descriptorRuntimeState(runtimeAlias), "graph_v2_executable");
assert.match(descriptorState(runtimeAlias, false).reason, /named port/);

const semanticIndex = descriptor({
  descriptorId: "output.semantic_index",
  kind: "output",
  availability: "governed_candidate",
  runtimeCapability: "semantic_index_candidate_runtime",
  inputPorts: [input("index", "vector_index_generation")],
  outputPorts: [output("index", "vector_index_generation")],
});
assert.equal(descriptorState(semanticIndex, true).isAddable, true);
assert.match(descriptorState(semanticIndex, true).reason, /governed candidate/);

const mediaOutput = descriptor({
  descriptorId: "output.media_set",
  kind: "output",
  availability: "graph_v2_executable",
  runtimeCapability: "media_output_runtime",
  inputPorts: [
    input("media", "media_set_selection", "media_derivative_set"),
  ],
  outputPorts: [output("media", "media_set_selection")],
});
assert.equal(descriptorState(mediaOutput, false).isAddable, true);
assert.match(descriptorState(mediaOutput, false).reason, /named port/);

const virtualTableOutput = descriptor({
  descriptorId: "output.virtual_table",
  kind: "output",
  availability: "validation_only",
  runtimeCapability: "virtual_table_runtime",
  inputPorts: [input("input", "dataset_version", "virtual_table")],
  outputPorts: [output("table", "virtual_table")],
});
assert.equal(descriptorState(virtualTableOutput, false).isAddable, true);
assert.match(
  descriptorState(virtualTableOutput, false).reason,
  /no-commit preview/,
);

const ontologyOutput = descriptor({
  descriptorId: "output.ontology",
  kind: "output",
  availability: "governed_candidate",
  runtimeCapability: "ontology_mapping_candidate_runtime",
  inputPorts: [input("input", "dataset_version")],
  outputPorts: [output("mapping", "ontology_mapping")],
});
assert.equal(descriptorState(ontologyOutput, false).isAddable, true);
assert.match(
  descriptorState(ontologyOutput, false).reason,
  /governed candidate/,
);

function createNode(id, item, patch) {
  const created = createDescriptorNode(id, item, "catalog_contract");
  assert.ok(created, `${item.descriptorId} should have a canvas authoring path`);
  return withNodeConfigurationPatch(created, patch);
}

const nodes = [
  createNode("media", mediaSource, {
    label: "Committed media",
    mediaSetRef: "media.contract_documents",
    mediaItemVersionIds: ["miv-contract-pdf"],
  }),
  createNode("media_output", mediaOutput, {
    label: "Processed media output",
    mediaSetRef: "media.contract_processed",
  }),
  createNode("extract", documentExtract, {
    label: "Layout extraction",
    processorId: "pdf_layout_v1@1",
    promptMode: "layout_aware_vision",
    systemPrompt: "Preserve headings, body blocks, and tables.",
  }),
  createNode("chunk", chunk, {
    label: "Token chunks",
    chunkSize: 500,
    overlap: 50,
  }),
  createNode("content_rows", contentBridge, { label: "Content rows" }),
  createNode("structured_output", datasetOutput, {
    label: "Structured output",
    outputDatasetRef: "pipelines.contract_structured",
  }),
  createNode("media_rows", mediaBridge, { label: "Media rows" }),
  createNode("interpret", useLlm, {
    label: "Prompt interpretation",
    promptMode: "basic_vision",
    promptTemplate: "Interpret {{mediaReference}}.",
    inputFields: ["mediaReference"],
    mediaReferenceField: "mediaReference",
    outputColumn: "interpretation",
  }),
  createNode("interpreted_output", datasetOutput, {
    label: "Interpreted output",
    outputDatasetRef: "pipelines.contract_interpreted",
  }),
  createNode("ontology_candidate", ontologyOutput, {
    label: "Document ontology candidate",
    mappingRef: "ontology.document_contract",
  }),
  createNode("virtual_output", virtualTableOutput, {
    label: "Virtual table output",
    virtualTableRef: "virtual.document_contract",
  }),
];

const expectedPorts = new Map([
  ["media", [[], ["media"]]],
  ["media_output", [["media"], ["media"]]],
  ["extract", [["media"], ["content"]]],
  ["chunk", [["content"], ["content"]]],
  ["content_rows", [["content"], ["dataset"]]],
  ["structured_output", [["input"], ["dataset"]]],
  ["media_rows", [["media"], ["dataset"]]],
  ["interpret", [["input"], ["dataset"]]],
  ["interpreted_output", [["input"], ["dataset"]]],
  ["ontology_candidate", [["input"], ["mapping"]]],
  ["virtual_output", [["input"], ["table"]]],
]);
for (const node of nodes) {
  const [inputs, outputs] = expectedPorts.get(node.id);
  assert.deepEqual(nodeInputPorts(node), inputs);
  assert.deepEqual(nodeOutputPorts(node), outputs);
}

let graph = { ...emptyGraphDoc(), nodes };
for (const requested of [
  {
    source: "media",
    target: "extract",
    sourceHandle: "media",
    targetHandle: "media",
  },
  {
    source: "media",
    target: "media_output",
    sourceHandle: "media",
    targetHandle: "media",
  },
  {
    source: "extract",
    target: "chunk",
    sourceHandle: "content",
    targetHandle: "content",
  },
  {
    source: "chunk",
    target: "content_rows",
    sourceHandle: "content",
    targetHandle: "content",
  },
  {
    source: "content_rows",
    target: "structured_output",
    sourceHandle: "dataset",
    targetHandle: "input",
  },
  {
    source: "media",
    target: "media_rows",
    sourceHandle: "media",
    targetHandle: "media",
  },
  {
    source: "media_rows",
    target: "interpret",
    sourceHandle: "dataset",
    targetHandle: "input",
  },
  {
    source: "interpret",
    target: "interpreted_output",
    sourceHandle: "dataset",
    targetHandle: "input",
  },
  {
    source: "interpret",
    target: "ontology_candidate",
    sourceHandle: "dataset",
    targetHandle: "input",
  },
  {
    source: "interpret",
    target: "virtual_output",
    sourceHandle: "dataset",
    targetHandle: "input",
  },
]) {
  const validation = validatePipelineConnection(graph, requested);
  assert.equal(validation.isValid, true, validation.reason);
  graph = {
    ...graph,
    edges: [
      ...graph.edges,
      createGraphEdge(validation.connection, graph.edges),
    ],
  };
}

const saved = canonicalizeGraphForSave(graph);
assert.equal(saved.schemaVersion, 2);
assert.deepEqual(
  saved.edges.map((edge) => [
    edge.sourceNodeId,
    edge.sourcePortId,
    edge.targetNodeId,
    edge.targetPortId,
  ]),
  [
    ["media", "media", "extract", "media"],
    ["media", "media", "media_output", "media"],
    ["extract", "content", "chunk", "content"],
    ["chunk", "content", "content_rows", "content"],
    ["content_rows", "dataset", "structured_output", "input"],
    ["media", "media", "media_rows", "media"],
    ["media_rows", "dataset", "interpret", "input"],
    ["interpret", "dataset", "interpreted_output", "input"],
    ["interpret", "dataset", "ontology_candidate", "input"],
    ["interpret", "dataset", "virtual_output", "input"],
  ],
);

const savedNodes = new Map(saved.nodes.map((node) => [node.id, node]));
assert.deepEqual(savedNodes.get("media")?.config.mediaItemVersionIds, [
  "miv-contract-pdf",
]);
assert.equal(
  savedNodes.get("media_output")?.config.mediaSetRef,
  "media.contract_processed",
);
assert.equal(
  savedNodes.get("extract")?.config.systemPrompt,
  "Preserve headings, body blocks, and tables.",
);
assert.equal(savedNodes.get("chunk")?.config.overlap, 50);
assert.deepEqual(savedNodes.get("interpret")?.config.inputFields, [
  "mediaReference",
]);
assert.equal(
  savedNodes.get("structured_output")?.config.outputDatasetRef,
  "pipelines.contract_structured",
);
assert.equal(
  savedNodes.get("interpreted_output")?.config.outputDatasetRef,
  "pipelines.contract_interpreted",
);
assert.equal(
  savedNodes.get("ontology_candidate")?.config.mappingRef,
  "ontology.document_contract",
);
assert.equal(
  savedNodes.get("virtual_output")?.config.virtualTableRef,
  "virtual.document_contract",
);

console.log("pipeline catalog capability and Graph v2 authoring contract ok");
