import type {
  PipelineNodeDescriptorPayload,
  PipelineNodeType,
} from "@foundry-lite/sdk";

import {
  compactJson,
  recordList,
  textValue,
  type PreviewRecord,
} from "./pipeline-preview-model";

export {
  AUTHORABLE_DESCRIPTOR_IDS,
  descriptorRuntimeState,
  descriptorState,
  type DescriptorRuntimeState,
  type DescriptorState,
} from "./pipeline-catalog-capability-model";

export type CatalogCategory =
  | "all"
  | "source"
  | "table"
  | "media"
  | "content"
  | "bridge"
  | "output";

const LEGACY_TYPE_BY_DESCRIPTOR: Partial<
  Record<string, PipelineNodeType>
> = {
  "transform.sql": "sql",
  "transform.python": "python",
  "transform.join": "join",
  "transform.union": "union",
  "transform.select_cast": "select_cast",
  "output.dataset": "output_dataset",
};

export function legacyTypeForDescriptor(
  descriptorId: string,
): PipelineNodeType | null {
  return LEGACY_TYPE_BY_DESCRIPTOR[descriptorId] ?? null;
}

export function filterDescriptors(
  descriptors: readonly PipelineNodeDescriptorPayload[],
  category: CatalogCategory,
  query: string,
): PipelineNodeDescriptorPayload[] {
  const normalizedQuery = query.trim().toLowerCase();
  return descriptors.filter((descriptor) => {
    if (
      category !== "all" &&
      descriptorCategory(descriptor.descriptorId) !== category
    ) {
      return false;
    }
    if (!normalizedQuery) return true;
    const searchable = JSON.stringify(descriptor).toLowerCase();
    return (
      searchable.includes(normalizedQuery) ||
      descriptorLabel(descriptor.descriptorId)
        .toLowerCase()
        .includes(normalizedQuery)
    );
  });
}

export function descriptorCategory(
  descriptorId: string,
): CatalogCategory {
  if (descriptorId.startsWith("source.")) return "source";
  if (descriptorId.startsWith("output.")) return "output";
  if (descriptorId.startsWith("bridge.")) return "bridge";
  if (
    descriptorId.includes("media") ||
    descriptorId.includes("document_extract")
  ) {
    return "media";
  }
  if (
    descriptorId.includes("chunk") ||
    descriptorId.includes("embedding") ||
    descriptorId.includes("llm") ||
    descriptorId.includes("trained_model")
  ) {
    return "content";
  }
  return "table";
}

export function descriptorConfigFields(
  descriptor: PipelineNodeDescriptorPayload,
): PreviewRecord[] {
  return recordList((descriptor as PreviewRecord).configFields);
}

export function portLabel(port: PreviewRecord): string {
  const id = textValue(port.portId) ?? "port";
  const kinds = Array.isArray(port.acceptedArtifactKinds)
    ? port.acceptedArtifactKinds.join("|")
    : textValue(port.artifactKind);
  return `${id}:${kinds ?? "unknown"}`;
}

export function descriptorKey(
  descriptor: PipelineNodeDescriptorPayload,
): string {
  return `${descriptor.descriptorId}@${descriptor.specVersion}`;
}

export function descriptorLabel(descriptorId: string): string {
  const labels: Record<string, string> = {
    "source.dataset": "Dataset source",
    "source.virtual_table": "Virtual table source",
    "source.media_set": "Media Set source",
    "source.stream": "Stream / CDC source",
    "source.geospatial": "Geospatial source",
    "transform.sql": "SQL",
    "transform.python": "Python",
    "transform.join": "Join",
    "transform.union": "Union",
    "transform.select_cast": "Select / Cast",
    "transform.media": "Transform media",
    "transform.document_extract": "Document extract",
    "transform.chunk": "Chunk content",
    "transform.embedding.text": "Text embedding",
    "transform.embedding.vision": "Vision embedding",
    "transform.use_llm": "Use LLM",
    "transform.trained_model": "Trained Model",
    "bridge.media_to_table_rows": "Media → Table rows",
    "bridge.content_units_to_dataset": "Content Units → Dataset",
    "bridge.stream_to_dataset": "Stream checkpoint → Dataset rows",
    "output.dataset": "Dataset output",
    "output.media_set": "Media Set output",
    "output.semantic_index": "Semantic index output",
    "output.virtual_table": "Virtual table output",
    "output.ontology": "Ontology output",
    "output.geospatial": "Geospatial output",
  };
  return labels[descriptorId] ?? descriptorId;
}

export function configFieldKey(field: PreviewRecord): string {
  return textValue(field.fieldName) ?? compactJson(field);
}
