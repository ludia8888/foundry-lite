import type { PipelineNodeDescriptorPayload } from "@foundry-lite/sdk";

export type DescriptorState = {
  isAddable: boolean;
  reason: string;
};

export type DescriptorRuntimeState =
  PipelineNodeDescriptorPayload["availability"];

export const AUTHORABLE_DESCRIPTOR_IDS = new Set([
  "transform.sql",
  "transform.python",
  "transform.join",
  "transform.union",
  "transform.select_cast",
  "output.dataset",
  "source.media_set",
  "transform.media",
  "transform.document_extract",
  "transform.chunk",
  "transform.embedding.text",
  "transform.embedding.vision",
  "bridge.media_to_table_rows",
  "bridge.content_units_to_dataset",
  "transform.use_llm",
  "transform.trained_model",
  "output.media_set",
  "output.virtual_table",
  "output.semantic_index",
  "output.ontology",
  "source.stream",
  "source.geospatial",
  "output.geospatial",
  "bridge.stream_to_dataset",
]);

const RUNTIME_STATES = new Set<DescriptorRuntimeState>([
  "legacy_executable",
  "graph_v2_executable",
  "governed_candidate",
  "validation_only",
]);

export function descriptorRuntimeState(
  descriptor: PipelineNodeDescriptorPayload,
): DescriptorRuntimeState {
  const runtimeCapability = descriptor.runtimeCapability;
  if (
    typeof runtimeCapability === "string" &&
    RUNTIME_STATES.has(runtimeCapability as DescriptorRuntimeState)
  ) {
    return runtimeCapability as DescriptorRuntimeState;
  }
  return descriptor.availability;
}

export function descriptorState(
  descriptor: PipelineNodeDescriptorPayload,
  _hasOutputNode: boolean,
  hasImportedTrainedModel = true,
): DescriptorState {
  if (
    descriptor.descriptorId === "transform.trained_model" &&
    !hasImportedTrainedModel
  ) {
    return {
      isAddable: false,
      reason:
        "Reusables에 가져온 Trained Model이 없습니다. 모델을 먼저 가져온 뒤 API 입력·출력 컬럼을 매핑해야 합니다.",
    };
  }
  if (descriptor.descriptorId === "source.dataset") {
    return {
      isAddable: false,
      reason:
        "실행 가능한 source입니다. 위 툴바의 데이터셋 추가에서 committed Dataset을 선택해야 합니다.",
    };
  }

  const runtimeState = descriptorRuntimeState(descriptor);
  if (!AUTHORABLE_DESCRIPTOR_IDS.has(descriptor.descriptorId)) {
    if (runtimeState === "governed_candidate") {
      return {
        isAddable: false,
        reason: `${descriptor.descriptorId} governed candidate는 현재 전용 config board와 canonical Graph v2 serializer가 없어 안전하게 추가할 수 없습니다.`,
      };
    }
    return {
      isAddable: false,
      reason:
        "서버 descriptor는 존재하지만 이 화면의 config board와 canonical Graph v2 serializer가 아직 연결되지 않았습니다.",
    };
  }

  if (runtimeState === "graph_v2_executable") {
    return {
      isAddable: true,
      reason:
        "서버가 Graph v2 실행 capability를 선언했고 현재 config board와 canonical serializer가 연결되어 있어 named port와 config를 보존한 채 추가·검증·실행할 수 있습니다.",
    };
  }
  if (runtimeState === "legacy_executable") {
    return {
      isAddable: true,
      reason:
        "현재 tabular v1 compiler와 연결되어 있어 이 캔버스에서 추가하고 검증·실행할 수 있습니다.",
    };
  }
  if (runtimeState === "governed_candidate") {
    return {
      isAddable: true,
      reason:
        "현재 캔버스가 named port와 config를 canonical Graph v2로 직렬화합니다. 다만 governed candidate이므로 배포·build는 해당 output runtime과 거버넌스 승인이 활성화된 경우에만 가능합니다.",
    };
  }
  return {
    isAddable: true,
    reason:
      "Graph v2 authoring과 no-commit preview를 지원합니다. 현재 descriptor는 validation-only이므로 배포·build runtime은 별도 활성화가 필요합니다.",
  };
}
